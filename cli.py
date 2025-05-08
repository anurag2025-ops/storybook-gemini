#!/usr/bin/env python3
"""
cli.py — Expert-prompted kids’ story-book generator
          (google-genai 1.13.0, A4 portrait PDF)

Changes in this version:
• PDF saved to outputs/pdf/
• .txt log saved to outputs/
• Bottom overlay card is 82 % opaque for subtle image bleed-through
"""

import io, json, os, sys, textwrap, time, tempfile, unicodedata
from pathlib import Path
from dotenv import load_dotenv
import google.genai as genai
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from fpdf import FPDF

# ─── configuration ─────────────────────────────────────────────────────────
PAGE_SIZE  = (595, 842)          # A4 portrait
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

def text_bbox(draw, txt, font):
    if hasattr(draw, "textbbox"):
        b=draw.textbbox((0,0), txt, font=font); return b[2]-b[0], b[3]-b[1]
    if hasattr(font, "getbbox"):
        b=font.getbbox(txt); return b[2]-b[0], b[3]-b[1]
    return draw.textsize(txt, font=font)

# ─── initialize GenAI client ───────────────────────────────────────────────
load_dotenv()
API_KEY=os.getenv("GOOGLE_API_KEY") or sys.exit("❌  Set GOOGLE_API_KEY")
client=genai.Client(api_key=API_KEY)

# ─── 1 · character descriptor ──────────────────────────────────────────────
def character_descriptor(theme):
    prompt=(f"Describe the main character for a children’s story themed “{theme}” "
            "in ONE vivid sentence (colours, clothing, species). No actions.")
    desc=client.models.generate_content(model=TEXT_MODEL, contents=prompt).text.strip()
    return desc.split("\n")[0][:120]

# ─── 2 · story pages ───────────────────────────────────────────────────────
def story_pages(theme, n, char_desc):
    prompt=(
        "You are a warm, playful children’s author.\n"
        f"Main character: {char_desc}\nTheme: “{theme}”\nPages: {n}\n"
        "Return ONLY JSON {\"pages\":[{\"title\":\"…\",\"text\":\"…\"}]}.\n"
        "Each page: 3-5-word title + TWO sentences (10–15 words) featuring the character."
    )
    raw=client.models.generate_content(model=TEXT_MODEL, contents=prompt).text.strip()
    for cut in (raw, raw[raw.find("{"):raw.rfind("}")+1]):
        try: return json.loads(cut)["pages"][:n]
        except Exception: pass
    return [{"title":"Untitled","text":"…"}]*n

# ─── 3 · illustration prompts & cache ──────────────────────────────────────
image_prompts=[]
def make_image(pg, char_desc):
    prompt=(f"You are a children’s book illustrator. {STYLE}. {char_desc}. "
            "No text/letters. Leave a blank margin (~10 %) at bottom. "
            f"Scene: {pg['text']}")
    image_prompts.append(prompt)
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
    return Image.new("RGB", PAGE_SIZE, (220,220,220))

# ─── 4 · overlay with translucent rounded card ────────────────────────────
def overlay(img, pg):
    img = img.convert("RGBA")         # ensure alpha channel
    W,H  = img.size
    side_pad, vert_pad, gap = 36, 20, 6
    d = ImageDraw.Draw(img)

    # wrap body to page width
    avg_char = FONT_BODY.getlength("ABCDEFGHIJKLMNOPQRSTUVWXYZ") / 26
    max_chars = int((W - 2*side_pad) / avg_char)
    body = textwrap.fill(safe(pg["text"]), max_chars)

    title_w,title_h=text_bbox(d, safe(pg["title"]), FONT_TITLE)
    body_w, body_h = text_bbox(d, body, FONT_BODY)

    card_h  = vert_pad + title_h + gap + body_h + vert_pad
    card_top= H - card_h - 12
    card_box= (side_pad//2, card_top, W-side_pad//2, card_top+card_h)

    # shadow
    shadow = Image.new("RGBA", img.size, (0,0,0,0))
    sdraw  = ImageDraw.Draw(shadow)
    sdraw.rounded_rectangle(card_box, radius=18, fill=(0,0,0,120))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(6)))

    # translucent card
    card_layer = Image.new("RGBA", img.size, (0,0,0,0))
    cl_draw    = ImageDraw.Draw(card_layer)
    cl_draw.rounded_rectangle(card_box, radius=18,
                              fill=(255,255,255,210), outline=(220,220,220,210))
    img.alpha_composite(card_layer)

    # text
    d = ImageDraw.Draw(img)
    d.text(((W-title_w)//2, card_top+vert_pad), safe(pg["title"]),
           font=FONT_TITLE, fill="black")
    d.multiline_text(((W-body_w)//2, card_top+vert_pad+title_h+gap),
                     body, font=FONT_BODY, fill="black",
                     spacing=4, align="center")
    return img.convert("RGB")         # back to RGB for PDF

# ─── 5 · build PDF + log file ──────────────────────────────────────────────
def build_pdf(pages, theme, char_desc):
    pdf_dir   = Path("outputs/pdf"); pdf_dir.mkdir(parents=True, exist_ok=True)
    out_dir   = Path("outputs");     out_dir.mkdir(exist_ok=True)

    safe_name="".join(c if c.isalnum() else "_" for c in theme)[:40] or "book"
    pdf_path = pdf_dir / f"storybook_{safe_name}.pdf"
    log_path = out_dir / f"storybook_{safe_name}_log.txt"

    pdf = FPDF(unit="pt", format=PAGE_SIZE)
    for i,pg in enumerate(pages,1):
        log(f"🖼️  image {i}/{len(pages)} …")
        img=overlay(make_image(pg,char_desc), pg)
        with tempfile.NamedTemporaryFile(delete=False,suffix=".png") as tmp:
            img.save(tmp.name,"PNG")
            pdf.add_page(); pdf.image(tmp.name,x=0,y=0,w=PAGE_SIZE[0],h=PAGE_SIZE[1])
    pdf.output(pdf_path.as_posix())
    print(f"✅  PDF  → {pdf_path.resolve()}")

    with open(log_path,"w",encoding="utf-8") as f:
        f.write(f"Theme: {theme}\nCharacter: {char_desc}\n\nPages:\n")
        for p in pages: f.write(f"- {p['title']}: {p['text']}\n")
        f.write("\nImage prompts:\n")
        for i,prompt in enumerate(image_prompts,1): f.write(f"[{i}] {prompt}\n")
    print(f"📝  Log → {log_path.resolve()}")

# ─── CLI ───────────────────────────────────────────────────────────────────
if __name__=="__main__":
    theme=input("Story theme: ").strip() or "A shy penguin who wants to fly"
    try: pages=int(input("Pages? (default 6): ").strip() or 6)
    except ValueError: pages=6

    char_desc=character_descriptor(theme); log(f"🧸 Character → {char_desc}")
    pages_data=story_pages(theme, pages, char_desc)
    build_pdf(pages_data, theme, char_desc)
