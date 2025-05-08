#!/usr/bin/env python3
"""
cli.py — Expert-prompted kids’ story-book generator
          (google-genai 1.13.0, A4 portrait PDF)

Text   : gemini-2.0-flash  (warm, playful children’s author)
Images : imagen-3.0-generate-002 (award-winning illustrator)
Style  : Soft-textured storybook illustration (flat 2D + watercolor vibes)
Layout : auto-height bottom band with centred title + body
Output : outputs/pdf/storybook_<theme>.pdf
"""

import io, json, os, sys, textwrap, time, tempfile, unicodedata
from pathlib import Path
from dotenv import load_dotenv
import google.genai as genai
from PIL import Image, ImageDraw, ImageFont
from fpdf import FPDF

# ─── configuration ─────────────────────────────────────────────────────────
PAGE_SIZE  = (595, 842)          # A4 portrait (pt @72 dpi)
TEXT_MODEL = "gemini-2.0-flash"
IMG_MODEL  = "imagen-3.0-generate-002"
MAX_RETRY  = 2

STYLE = (
    "Soft-textured storybook illustration: gentle rounded characters; "
    "watercolor-inspired digital art with soft gradients and grainy textures; "
    "flat 2D animation-ready shading; pastel colours, no hard outlines"
)

# ─── helpers ───────────────────────────────────────────────────────────────
log  = lambda m: print(m, file=sys.stderr)
safe = lambda t: unicodedata.normalize("NFKD", t).encode("latin-1","ignore").decode()

def load_font(paths, size):
    for p in paths:
        try: return ImageFont.truetype(p, size)
        except (OSError, IOError): pass
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
    img=Image.new("RGB", PAGE_SIZE, (220,220,220))
    d  = ImageDraw.Draw(img)
    msg=f"[Image unavailable]\n{title}"
    box=d.multiline_textbbox((0,0),msg,font=FONT_BODY,spacing=4)
    w,h=box[2]-box[0], box[3]-box[1]
    d.multiline_text(((PAGE_SIZE[0]-w)//2,(PAGE_SIZE[1]-h)//2),
                     msg,font=FONT_BODY,fill="black",spacing=4,align="center")
    return img

# ─── initialize Google GenAI client ────────────────────────────────────────
load_dotenv()
API_KEY=os.getenv("GOOGLE_API_KEY")
if not API_KEY: sys.exit("❌  Set GOOGLE_API_KEY")
client=genai.Client(api_key=API_KEY)

# ─── 1 · character descriptor (after theme input) ──────────────────────────
def character_descriptor(theme):
    prompt=("Describe the main character for a children’s story themed "
            f"“{theme}” in ONE vivid sentence (colours, clothing, species). "
            "No actions.")
    desc=client.models.generate_content(model=TEXT_MODEL, contents=prompt).text.strip()
    return desc.split("\n")[0][:120]

# ─── 2 · story pages (author persona, forced character) ────────────────────
def story_pages(theme, n, char_desc):
    prompt=(
        "You are a warm, playful children’s author.\n"
        f"Main character: {char_desc}\n"
        f"Theme: “{theme}”\nPages: {n}\n"
        "Return ONLY JSON {\"pages\":[{\"title\":\"…\",\"text\":\"…\"}]}\n"
        "Each page: 3–5-word title + TWO sentences (10-15 words) featuring that character."
    )
    raw=client.models.generate_content(model=TEXT_MODEL, contents=prompt).text.strip()
    for cut in (raw, raw[raw.find("{"):raw.rfind("}")+1]):
        try: return json.loads(cut)["pages"][:n]
        except Exception: pass
    return [{"title":"Untitled","text":"…"}]*n

# ─── 3 · illustration prompt (same character) ─────────────────────────────
def make_image(pg, char_desc):
    prompt=(
        f"You are an award-winning children’s illustrator. {STYLE}. {char_desc}. "
        "Do NOT include text, letters, captions. "
        "Leave a blank white margin at the bottom (~10 %) for overlay. "
        f"Scene: {pg['text']}"
    )
    for att in range(MAX_RETRY+1):
        try:
            rsp=client.models.generate_images(model=IMG_MODEL, prompt=prompt)
            if rsp.generated_images:
                return (Image.open(io.BytesIO(rsp.generated_images[0].image.image_bytes))
                        .convert("RGB").resize(PAGE_SIZE, Image.LANCZOS))
            log(f"⚠️  Imagen block ({att+1}/{MAX_RETRY+1})")
        except Exception as e:
            log(f"⚠️  Imagen error ({att+1}/{MAX_RETRY+1}): {e}")
            time.sleep(1)
    return placeholder(pg['title'])

# ─── 4 · overlay (auto-height, full-width wrap) ───────────────────────────
def text_bbox(d, txt, font):
    if hasattr(d, "textbbox"):
        b=d.textbbox((0,0),txt,font=font); return b[2]-b[0], b[3]-b[1]
    if hasattr(font, "getbbox"):
        b=font.getbbox(txt); return b[2]-b[0], b[3]-b[1]
    return d.textsize(txt,font=font)

def overlay(img, pg):
    W,H=img.size
    pad=28                               # larger side padding for A4
    d = ImageDraw.Draw(img)

    # Estimate wrap width in characters based on font’s average char width
    avg_char_w = FONT_BODY.getbbox("ABCDEFGHIJKLMNOPQRSTUVWXYZ")[2] / 26
    max_chars  = max(10, int((W - 2*pad) / avg_char_w))
    body_text  = textwrap.fill(safe(pg["text"]), max_chars)

    # Measure heights
    title_w,title_h=text_bbox(d, safe(pg["title"]), FONT_TITLE)
    body_w, body_h = text_bbox(d, body_text, FONT_BODY)

    total_h = pad + title_h + 4 + body_h + pad
    band_h  = total_h

    band_top = H - band_h
    d.rectangle([0, band_top, W, H], fill="white")

    d.text(((W-title_w)//2, band_top + pad),
           safe(pg["title"]), font=FONT_TITLE, fill="black")

    d.multiline_text(((W-body_w)//2, band_top + pad + title_h + 4),
                     body_text, font=FONT_BODY, fill="black",
                     spacing=4, align="center")
    return img

# ─── 5 · build PDF ─────────────────────────────────────────────────────────
def build_pdf(pages, theme, char_desc):
    pdf=FPDF(unit="pt", format=PAGE_SIZE)
    for i,pg in enumerate(pages,1):
        log(f"🖼️  image {i}/{len(pages)} …")
        img=overlay(make_image(pg,char_desc), pg)
        tmp=tempfile.NamedTemporaryFile(delete=False, suffix=".png")
        img.save(tmp.name,"PNG")
        pdf.add_page()
        pdf.image(tmp.name, x=0, y=0, w=PAGE_SIZE[0], h=PAGE_SIZE[1])
    out_dir=Path("outputs/pdf"); out_dir.mkdir(parents=True, exist_ok=True)
    safe_name="".join(c if c.isalnum() else "_" for c in theme)[:40] or "book"
    out=out_dir/f"storybook_{safe_name}.pdf"
    pdf.output(out.as_posix())
    print(f"\n✅  Saved → {out.resolve()}")

# ─── CLI ───────────────────────────────────────────────────────────────────
if __name__=="__main__":
    theme=input("Story theme: ").strip() or "A shy penguin who wants to fly"
    try: pages=int(input("Pages? (default 6): ").strip() or 6)
    except ValueError: pages=6

    char_desc = character_descriptor(theme)
    log(f"🧸 Main character → {char_desc}")

    pages_data = story_pages(theme, pages, char_desc)
    build_pdf(pages_data, theme, char_desc)
