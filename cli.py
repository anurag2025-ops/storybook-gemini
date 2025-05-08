#!/usr/bin/env python3
"""
cli.py — Consistent-character kids’ story-book generator
          (google-genai 1.13.0, 640×960 portrait PDF)

Text  : gemini-2.0-flash
Images: imagen-3.0-generate-002   (3:4 portrait)
Style : warm digital-gouache, Little-Golden-Book look
Layout: bottom 10 % white band for centred title + body
"""

import io, json, os, sys, textwrap, time, tempfile, unicodedata
from pathlib import Path
from dotenv import load_dotenv
import google.genai as genai
from PIL import Image, ImageDraw, ImageFont
from fpdf import FPDF

# ─── config ────────────────────────────────────────────────────────────────
PAGE_SIZE  = (640, 960)
TEXT_MODEL = "gemini-2.0-flash"
IMG_MODEL  = "imagen-3.0-generate-002"
MAX_RETRY  = 2

STYLE = (
    "warm digital gouache illustration, soft brush strokes, subtle textured paper, "
    "muted pastel and earth-tone palette, gentle golden lighting, 1970s Little Golden Book style, "
    "no sharp outlines, dreamy atmosphere"
)

# ─── helpers ────────────────────────────────────────────────────────────────
log  = lambda m: print(m, file=sys.stderr)
safe = lambda t: unicodedata.normalize("NFKD", t).encode("latin-1","ignore").decode()
wrap = lambda t,w=38: "\n".join(textwrap.wrap(t,w))

def load_font(paths,size):
    for p in paths:
        try: return ImageFont.truetype(p,size)
        except (OSError,IOError): pass
    return ImageFont.load_default()

FONT_TITLE = load_font(
    ["DejaVuSans-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "Arial Bold.ttf"], 26)
FONT_BODY  = load_font(
    ["DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "Arial.ttf"], 20)

def placeholder(title):
    img=Image.new("RGB",PAGE_SIZE,(220,220,220))
    d=ImageDraw.Draw(img)
    msg=f"[Image unavailable]\n{title}"
    box=d.multiline_textbbox((0,0), msg, font=FONT_BODY, spacing=4)
    w,h=box[2]-box[0], box[3]-box[1]
    d.multiline_text(((PAGE_SIZE[0]-w)//2,(PAGE_SIZE[1]-h)//2),
                     msg,font=FONT_BODY,fill="black",spacing=4,align="center")
    return img

# ─── client ────────────────────────────────────────────────────────────────
load_dotenv()
API_KEY=os.getenv("GOOGLE_API_KEY")
if not API_KEY: sys.exit("❌  Set GOOGLE_API_KEY")
client=genai.Client(api_key=API_KEY)

# ─── 1 · story pages ───────────────────────────────────────────────────────
def story_pages(theme,n):
    prompt=(f'Return ONLY JSON {{"pages":[{{"title":"","text":""}}]}}\n'
            f'Theme:{theme}\nPages:{n}\n'
            'Each page: short title + TWO short sentences.')
    raw=client.models.generate_content(model=TEXT_MODEL,
                                       contents=prompt).text.strip()
    try:    return json.loads(raw)["pages"][:n]
    except: cut=raw[raw.find("{"):raw.rfind("}")+1]; return json.loads(cut)["pages"][:n]

# ─── 2 · consistent character descriptor ───────────────────────────────────
def character_descriptor(theme):
    q=("Based on the theme “{t}”, describe the main character’s appearance in ONE "
       "short sentence (colours, clothing, species). No actions."
       ).format(t=theme)
    d=client.models.generate_content(model=TEXT_MODEL,
                                      contents=q).text.strip()
    return d.split("\n")[0][:120]

# ─── 3 · image gen (no text, blank band) ───────────────────────────────────
def make_image(pg,char_desc):
    base_prompt=(f"{STYLE}. {char_desc}. "
                 "Do not include any text, letters, words, captions or speech bubbles. "
                 "Leave a blank white margin at the bottom (about 10% height) for text. "
                 f"Scene: {pg['text']}")
    prompt=base_prompt
    for att in range(MAX_RETRY+1):
        try:
            rsp=client.models.generate_images(model=IMG_MODEL, prompt=prompt)
            if rsp.generated_images:
                img=(Image.open(io.BytesIO(rsp.generated_images[0].image.image_bytes))
                     .convert("RGB")
                     .resize(PAGE_SIZE, Image.LANCZOS))
                return img
            log(f"⚠️  Imagen block ({att+1}/{MAX_RETRY+1})")
        except Exception as e:
            log(f"⚠️  Imagen error ({att+1}/{MAX_RETRY+1}): {e}")
            time.sleep(1)
        prompt=(f"{STYLE}. {char_desc}. no text, no words. "
                "Pastel children’s illustration with blank bottom margin.")
    return placeholder(pg['title'])

# ─── 4 · overlay text (centre align) ───────────────────────────────────────
def text_bbox(d, txt, font):
    if hasattr(d, "textbbox"):
        b=d.textbbox((0,0), txt, font=font); return b[2]-b[0], b[3]-b[1]
    if hasattr(font, "getbbox"):
        b=font.getbbox(txt); return b[2]-b[0], b[3]-b[1]
    return d.textsize(txt, font=font)

def overlay(img, pg):
    W,H=img.size
    band=int(H*0.10)
    pad =18
    d=ImageDraw.Draw(img)
    d.rectangle([0,H-band,W,H], fill="white")

    title=safe(pg["title"])
    tw,th=text_bbox(d,title,FONT_TITLE)
    d.text(((W-tw)//2, H-band+pad), title, font=FONT_TITLE, fill="black")

    body=wrap(safe(pg["text"]), 42)
    bw,bh=text_bbox(d, body, FONT_BODY)
    # multiline_text doesn’t auto-centre; compute left offset
    d.multiline_text(((W-bw)//2, H-band+pad+th+4),
                     body, font=FONT_BODY, fill="black",
                     spacing=4, align="center")
    return img

# ─── 5 · PDF build ─────────────────────────────────────────────────────────
def build_pdf(pages,theme,char_desc):
    pdf=FPDF(unit="pt", format=PAGE_SIZE)
    for i,pg in enumerate(pages,1):
        log(f"🖼️  image {i}/{len(pages)} …")
        img=overlay(make_image(pg,char_desc), pg)
        with tempfile.NamedTemporaryFile(delete=False,suffix=".png") as tmp:
            img.save(tmp.name,"PNG")
            pdf.add_page(); pdf.image(tmp.name,x=0,y=0,
                                      w=PAGE_SIZE[0], h=PAGE_SIZE[1])
    out_dir=Path("outputs/pdf"); out_dir.mkdir(parents=True, exist_ok=True)
    fname="".join(c if c.isalnum() else "_" for c in theme)[:40] or "book"
    out=out_dir/f"storybook_{fname}.pdf"
    pdf.output(out.as_posix())
    print(f"\n✅  Saved → {out.resolve()}")

# ─── CLI ───────────────────────────────────────────────────────────────────
if __name__=="__main__":
    theme=input("Story theme: ").strip() or "A shy penguin who wants to fly"
    try: pages=int(input("Pages? (default 6): ").strip() or 6)
    except ValueError: pages=6
    log("✍️  Generating story & character …")
    pg_list=story_pages(theme,pages)
    char_desc=character_descriptor(theme)
    log(f"🎨  Character descriptor → {char_desc}")
    build_pdf(pg_list, theme, char_desc)
