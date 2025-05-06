#!/usr/bin/env python3
"""
cli.py — Kids’ story-book generator (google-genai 1.13.0)

Text  : gemini-2.0-flash
Images: imagen-3.0-generate-002   (portrait 3:4 — implied)
Pages : 640 × 960 px PDF, retries + placeholder
"""

import io, json, os, sys, textwrap, time, random, tempfile, unicodedata
from pathlib import Path
from dotenv import load_dotenv
import google.genai as genai                # 1.13 API uses Client.models.*
from PIL import Image, ImageDraw, ImageFont
from fpdf import FPDF

# ─── constants ──────────────────────────────────────────────────────────────
PAGE_SIZE  = (640, 960)       # px & PDF pt
TEXT_MODEL = "gemini-2.0-flash"
IMG_MODEL  = "imagen-3.0-generate-002"
MAX_RETRY  = 2

# ─── helpers ────────────────────────────────────────────────────────────────
log  = lambda m: print(m, file=sys.stderr)
safe = lambda t: unicodedata.normalize("NFKD", t).encode("latin-1","ignore").decode()
wrap = lambda t,w=38: "\n".join(textwrap.wrap(t,w))

def font(paths,size):
    for p in paths:
        try: return ImageFont.truetype(p,size)
        except (OSError,IOError): pass
    return ImageFont.load_default()

FONT_TITLE = font(["DejaVuSans-Bold.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                   "Arial Bold.ttf"], 26)
FONT_BODY  = font(["DejaVuSans.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                   "Arial.ttf"], 20)

def placeholder(title):
    img=Image.new("RGB",PAGE_SIZE,(220,220,220))
    d=ImageDraw.Draw(img)
    msg=f"[Image unavailable]\n{title}"
    box=d.multiline_textbbox((0,0),msg,font=FONT_BODY,spacing=4)
    w,h=box[2]-box[0],box[3]-box[1]
    d.multiline_text(((PAGE_SIZE[0]-w)//2,(PAGE_SIZE[1]-h)//2),
                     msg,font=FONT_BODY,fill="black",spacing=4,align="center")
    return img

# ─── client (fixed 1.13) ────────────────────────────────────────────────────
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

    for cut in (raw, raw[raw.find("{"):raw.rfind("}")+1]):
        try: return json.loads(cut)["pages"][:n]
        except Exception: pass

    pages,title,body=[], "", ""
    for l in raw.splitlines():
        l=l.strip()
        if l.lower().startswith("page") and "title" in l.lower():
            if title and body: pages.append({"title":title,"text":body.strip()})
            title,body=l.split(":",1)[1].strip(),""
        elif l.lower().startswith("text:"):
            body+=" "+l.split(":",1)[1].strip()
    if title and body: pages.append({"title":title,"text":body.strip()})
    return pages[:n] or [{"title":"Untitled","text":"…"}]*n

# ─── 2 · whimsical image ────────────────────────────────────────────────────
STYLE = ("whimsical, pastel colours, soft water-colour texture, dreamy, "
         "simple shapes, children’s book illustration, 3:4 portrait. ")

def make_image(pg):
    prompt = STYLE + f"Scene: {pg['text']}"
    for att in range(MAX_RETRY+1):
        try:
            rsp=client.models.generate_images(model=IMG_MODEL,
                                              prompt=prompt)   # no extra kwargs
            if rsp.generated_images:
                img=(Image.open(io.BytesIO(rsp.generated_images[0].image.image_bytes))
                     .convert("RGB").resize(PAGE_SIZE, Image.LANCZOS))
                return img
            log(f"⚠️  Imagen block ({att+1}/{MAX_RETRY+1})")
        except Exception as e:
            log(f"⚠️  Imagen error ({att+1}/{MAX_RETRY+1}): {e}")
            time.sleep(1)
        prompt="whimsical pastel children’s illustration, bright colours, 3:4"
    return placeholder(pg['title'])

def overlay(img,pg):
    W,H=img.size; box=int(H*0.28); pad=24
    mask=Image.new("RGBA",(W,box),(255,255,255,230))
    img.paste(mask,(0,H-box),mask)
    d=ImageDraw.Draw(img)
    d.text((pad,H-box+12), safe(pg['title']), font=FONT_TITLE, fill="black")
    d.multiline_text((pad,H-box+46), wrap(safe(pg['text'])), font=FONT_BODY,
                     fill="black", spacing=4)
    return img

# ─── 3 · PDF build ──────────────────────────────────────────────────────────
def build_pdf(pages,theme):
    pdf=FPDF(unit="pt",format=PAGE_SIZE)
    for i,pg in enumerate(pages,1):
        log(f"🖼️  image {i}/{len(pages)} …")
        img=overlay(make_image(pg),pg)
        with tempfile.NamedTemporaryFile(delete=False,suffix=".png") as tmp:
            img.save(tmp.name,"PNG")
            pdf.add_page(); pdf.image(tmp.name,x=0,y=0,
                                      w=PAGE_SIZE[0],h=PAGE_SIZE[1])
    out_dir=Path("outputs/pdf"); out_dir.mkdir(parents=True,exist_ok=True)
    fname="".join(c if c.isalnum() else "_" for c in theme)[:40] or "book"
    out=out_dir/f"storybook_{fname}.pdf"
    pdf.output(out.as_posix())
    print(f"\n✅  Saved → {out.resolve()}")

# ─── CLI ────────────────────────────────────────────────────────────────────
if __name__=="__main__":
    theme=input("Story theme: ").strip() or "A shy penguin who wants to fly"
    try: pages=int(input("Pages? (default 6): ").strip() or 6)
    except ValueError: pages=6
    log("✍️  Generating story …")
    build_pdf(story_pages(theme,pages), theme)
