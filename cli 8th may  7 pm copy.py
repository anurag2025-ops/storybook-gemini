#!/usr/bin/env python3
"""
cli.py — Consistent-character kids’ story-book generator  (google-genai 1.13.0)

Text   : gemini-2.0-flash
Images : imagen-3.0-generate-002   (3:4 portrait)
Style  : soft watercolor, pastel, hand-painted
Output : outputs/pdf/storybook_portrait.pdf   (640×960 px)
"""

import io, json, os, sys, textwrap, time, random, tempfile, unicodedata
from pathlib import Path
from dotenv import load_dotenv
import google.genai as genai   # 1.13 client API
from PIL import Image, ImageDraw, ImageFont
from fpdf import FPDF

# ─── constants ──────────────────────────────────────────────────────────────
PAGE_SIZE = (640, 960)
TEXT_MODEL = "gemini-2.0-flash"
IMG_MODEL  = "imagen-3.0-generate-002"
MAX_RETRY  = 2

STYLE = (
    "soft watercolor washes, pastel tones, hand-painted storybook style, "
    "dreamy atmosphere, textured paper"
)

# ─── helpers ────────────────────────────────────────────────────────────────
log  = lambda m: print(m, file=sys.stderr)
safe = lambda t: unicodedata.normalize("NFKD", t).encode("latin-1","ignore").decode()
wrap = lambda t,w=38: "\n".join(textwrap.wrap(t,w))

def font(paths, size):
    for p in paths:
        try: return ImageFont.truetype(p, size)
        except (OSError, IOError): pass
    return ImageFont.load_default()

FONT_TITLE = font(["DejaVuSans-Bold.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"], 26)
FONT_BODY  = font(["DejaVuSans.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"], 20)

def placeholder(title):
    img=Image.new("RGB",PAGE_SIZE,(220,220,220))
    d=ImageDraw.Draw(img)
    msg=f"[Image unavailable]\n{title}"
    box=d.multiline_textbbox((0,0),msg,font=FONT_BODY,spacing=4)
    w,h=box[2]-box[0],box[3]-box[1]
    d.multiline_text(((PAGE_SIZE[0]-w)//2,(PAGE_SIZE[1]-h)//2),
                     msg,font=FONT_BODY,fill="black",spacing=4,align="center")
    return img

# ─── init client ────────────────────────────────────────────────────────────
load_dotenv()
API_KEY=os.getenv("GOOGLE_API_KEY")
if not API_KEY: sys.exit("❌  Set GOOGLE_API_KEY")
client=genai.Client(api_key=API_KEY)

# ─── 1 · story pages ────────────────────────────────────────────────────────
def story_pages(theme,n):
    prompt=(f'Return ONLY JSON {{"pages":[{{"title":"","text":""}}]}}\n'
            f'Theme:{theme}\nPages:{n}\n'
            'Each page: short title + TWO short sentences.')
    raw=client.models.generate_content(model=TEXT_MODEL,
                                       contents=prompt).text.strip()
    try:
        return json.loads(raw)["pages"][:n]
    except Exception:
        # quick cut-&-parse fallback
        cut=raw[raw.find("{"):raw.rfind("}")+1]
        return json.loads(cut)["pages"][:n]

# ─── 2 · character descriptor (one sentence) ───────────────────────────────
def character_descriptor(theme):
    prompt=(f"Based on the story theme \"{theme}\", write ONE sentence that "
            f"visually describes the main character with stable traits "
            f"(e.g. colours, clothing). Do NOT mention scene actions.")
    desc=client.models.generate_content(model=TEXT_MODEL,
                                        contents=prompt).text.strip()
    # enforce brevity
    return desc.split("\n")[0][:120]

# ─── 3 · image generation ──────────────────────────────────────────────────
def make_image(pg, char_desc):
    prompt = f"{STYLE}. {char_desc}. Scene: {pg['text']}"
    for att in range(MAX_RETRY+1):
        try:
            rsp=client.models.generate_images(model=IMG_MODEL, prompt=prompt)
            if rsp.generated_images:
                img=(Image.open(io.BytesIO(rsp.generated_images[0].image.image_bytes))
                     .convert("RGB").resize(PAGE_SIZE, Image.LANCZOS))
                return img
            log(f"⚠️  Imagen block ({att+1}/{MAX_RETRY+1})")
        except Exception as e:
            log(f"⚠️  Imagen error ({att+1}/{MAX_RETRY+1}): {e}")
            time.sleep(1)
        prompt=f"{STYLE}. {char_desc}. whimsical pastel children’s illustration"
    return placeholder(pg['title'])

def overlay(img, pg):
    W,H=img.size; box=int(H*0.28); pad=24
    mask=Image.new("RGBA",(W,box),(255,255,255,230))
    img.paste(mask,(0,H-box),mask)
    d=ImageDraw.Draw(img)
    d.text((pad,H-box+12), safe(pg['title']), font=FONT_TITLE, fill="black")
    d.multiline_text((pad,H-box+46), wrap(safe(pg['text'])),
                     font=FONT_BODY, fill="black", spacing=4)
    return img

# ─── 4 · PDF build ─────────────────────────────────────────────────────────
def build_pdf(pages, theme, char_desc):
    pdf=FPDF(unit="pt",format=PAGE_SIZE)
    for i,pg in enumerate(pages,1):
        log(f"🖼️  image {i}/{len(pages)} …")
        img=overlay(make_image(pg,char_desc), pg)
        with tempfile.NamedTemporaryFile(delete=False,suffix=".png") as tmp:
            img.save(tmp.name,"PNG")
            pdf.add_page(); pdf.image(tmp.name,x=0,y=0,
                                      w=PAGE_SIZE[0],h=PAGE_SIZE[1])
    out_dir=Path("outputs/pdf"); out_dir.mkdir(parents=True,exist_ok=True)
    fname="".join(c if c.isalnum() else "_" for c in theme)[:40] or "book"
    out=out_dir/f"storybook_{fname}.pdf"
    pdf.output(out.as_posix())
    print(f"\n✅  Saved → {out.resolve()}")

# ─── CLI ───────────────────────────────────────────────────────────────────
if __name__=="__main__":
    theme=input("Story theme: ").strip() or "A shy penguin who wants to fly"
    try: pages=int(input("Pages? (default 6): ").strip() or 6)
    except ValueError: pages=6

    log("✍️  Generating story & character design …")
    story=story_pages(theme,pages)
    char_desc=character_descriptor(theme)
    log(f"🎨  Character descriptor → {char_desc}")
    build_pdf(story, theme, char_desc)
