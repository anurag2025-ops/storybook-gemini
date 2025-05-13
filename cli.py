#!/usr/bin/env python3
"""
cli.py — GPT-4o text + Imagen-3 images
Kids’ moral-lesson storybook generator (A4 PDF, progressive image fallback)
"""

import io, json, os, sys, textwrap, time, base64, tempfile, unicodedata
from pathlib import Path
from dotenv import load_dotenv

import openai
import google.genai as genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFont, ImageFilter, UnidentifiedImageError
from fpdf import FPDF

# ─── configuration ─────────────────────────────────────────────────────────
PAGE_SIZE   = (595, 842)          # A4 portrait
RAW_SIZE    = (768, 1024)         # Imagen output 3:4
TEXT_MODEL  = "gpt-4o-mini"       # or "gpt-4o"
IMG_MODEL   = "imagen-3.0-generate-002"
MAX_RETRY   = 2                   # per prompt variant
TEXT_COLOR  = (30, 30, 150)       # navy blue

STYLE = (
    "Soft-textured storybook illustration, gentle rounded characters; "
    "digital water-colour gradients, grainy pencil textures; flat 2D shading; "
    "pastel colours, no hard outlines"
)

# ─── helpers (unchanged) ───────────────────────────────────────────────────
log  = lambda m: print(m, file=sys.stderr)
safe = lambda t: unicodedata.normalize("NFKD", t).encode("latin-1","ignore").decode()

def load_font(paths, size):
    for p in paths:
        try: return ImageFont.truetype(p, size)
        except (OSError, IOError): pass
    return ImageFont.load_default()

FONT_TITLE = load_font(["DejaVuSans-Bold.ttf",
                        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                        "Arial Bold.ttf"], 26)
FONT_BODY  = load_font(["DejaVuSans.ttf",
                        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                        "Arial.ttf"], 20)

def text_bbox(draw, txt, font):
    if hasattr(draw, "textbbox"):
        b = draw.textbbox((0,0), txt, font=font); return b[2]-b[0], b[3]-b[1]
    if hasattr(font, "getbbox"):
        b = font.getbbox(txt); return b[2]-b[0], b[3]-b[1]
    return draw.textsize(txt, font=font)

# ─── SDK init ──────────────────────────────────────────────────────────────
load_dotenv()
openai.api_key  = os.getenv("OPENAI_API_KEY")  or sys.exit("❌  Set OPENAI_API_KEY")
google_key      = os.getenv("GOOGLE_API_KEY") or sys.exit("❌  Set GOOGLE_API_KEY")
gen_client      = genai.Client(api_key=google_key)

# ─── 1 · character descriptor (unchanged) ──────────────────────────────────
def character_descriptor(theme):
    msgs=[
        {"role":"system","content":"You are a creative children’s author."},
        {"role":"user","content":
         f"Describe the main character for a children’s story themed “{theme}” "
         "in ONE vivid sentence (colours, clothing, species). No actions."}
    ]
    desc=openai.chat.completions.create(model=TEXT_MODEL,messages=msgs)\
         .choices[0].message.content.strip()
    return desc.split("\n")[0][:120]

# ─── 2 · story pages with moral lesson (unchanged) ─────────────────────────
def story_pages(theme, n, char_desc):
    msgs=[
        {"role":"system","content":
         "You are a warm, playful children’s author (ages 4-8)."},
        {"role":"user","content":
         f"Main character: {char_desc}\nTheme: “{theme}”\nPages: {n}\n"
         "Return ONLY JSON {\"pages\":[{\"title\":\"…\",\"text\":\"…\"}]}\n\n"
         "Each page: 3–5-word title + TWO sentences (10-15 words) featuring the character.\n"
         "Build toward a clear moral. On the FINAL page, make the second sentence state that lesson explicitly."}
    ]
    raw=openai.chat.completions.create(model=TEXT_MODEL,messages=msgs)\
        .choices[0].message.content.strip()
    for cut in (raw, raw[raw.find('{'):raw.rfind('}')+1]):
        try: return json.loads(cut)["pages"][:n]
        except Exception: pass
    return [{"title":"Untitled","text":"…"}]*n

# ─── 3 · Imagen-3 with progressive fallback (NEW) ──────────────────────────
image_prompts=[]
def make_image(pg, char_desc):
    prompts = [
        f"{STYLE}. {char_desc}. No text/letters. Blank bottom margin. Scene: {pg['text']}",
        f"{STYLE}. No text/letters. Blank bottom margin. Scene: {pg['text']}",
        "Cute pastel children’s illustration, no text, blank bottom margin."
    ]

    cfg = types.GenerateImagesConfig(number_of_images=1, aspect_ratio="3:4")

    for p_index, prompt in enumerate(prompts, 1):
        prompt = prompt[:800]
        image_prompts.append(prompt)
        for attempt in range(1, MAX_RETRY + 1):
            try:
                rsp = gen_client.models.generate_images(
                    model=IMG_MODEL,
                    prompt=prompt,
                    config=cfg
                )
                if rsp.generated_images and rsp.generated_images[0].image.image_bytes:
                    try:
                        b = rsp.generated_images[0].image.image_bytes
                        img = Image.open(io.BytesIO(b)).convert("RGB")\
                              .resize(RAW_SIZE, Image.LANCZOS)\
                              .resize(PAGE_SIZE, Image.LANCZOS)
                        return img
                    except UnidentifiedImageError:
                        log("⚠️  Imagen bytes unreadable")

                log(f"⚠️  Imagen block (prompt {p_index}/{len(prompts)}, attempt {attempt}/{MAX_RETRY})")
            except Exception as e:
                log(f"⚠️  Imagen error (prompt {p_index}/{len(prompts)}, attempt {attempt}/{MAX_RETRY}): {e}")
                time.sleep(1)

    log("⚠️  All Imagen attempts failed – grey placeholder used.")
    return Image.new("RGB", PAGE_SIZE, (220, 220, 220))

# ─── 4 · overlay card (unchanged) ─────────────────────────────────────────
def overlay(img, pg):
    img=img.convert("RGBA"); W,H=img.size
    side_pad, vert_pad, gap = 36, 20, 6
    d=ImageDraw.Draw(img)
    avg=FONT_BODY.getlength("ABCDEFGHIJKLMNOPQRSTUVWXYZ")/26
    max_chars=int((W-2*side_pad)/avg)
    body=textwrap.fill(safe(pg["text"]), max_chars)
    title_w,title_h=text_bbox(d,safe(pg["title"]),FONT_TITLE)
    body_w, body_h =text_bbox(d,body,FONT_BODY)
    card_h=vert_pad+title_h+gap+body_h+vert_pad
    top=H-card_h-12
    rect=(side_pad//2, top, W-side_pad//2, top+card_h)
    sh=Image.new("RGBA", img.size,(0,0,0,0))
    ImageDraw.Draw(sh).rounded_rectangle(rect,18,fill=(0,0,0,120))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(6)))
    card=Image.new("RGBA", img.size,(0,0,0,0))
    ImageDraw.Draw(card).rounded_rectangle(rect,18,
        fill=(255,255,255,210), outline=(220,220,220,210))
    img.alpha_composite(card)
    d=ImageDraw.Draw(img)
    d.text(((W-title_w)//2, top+vert_pad), safe(pg["title"]),
           font=FONT_TITLE, fill=TEXT_COLOR)
    d.multiline_text(((W-body_w)//2, top+vert_pad+title_h+gap),
                     body, font=FONT_BODY, fill=TEXT_COLOR,
                     spacing=4, align="center")
    return img.convert("RGB")

# ─── 5 · build PDF & log (unchanged) ───────────────────────────────────────
def build_assets(pages, theme, char_desc):
    pdf_dir=Path("outputs/pdf"); pdf_dir.mkdir(parents=True,exist_ok=True)
    txt_dir=Path("outputs");     txt_dir.mkdir(exist_ok=True)
    safe_name="".join(c if c.isalnum() else "_" for c in theme)[:40] or "book"
    pdf_path=pdf_dir/f"storybook_{safe_name}.pdf"
    txt_path=txt_dir/f"storybook_{safe_name}_log.txt"
    pdf=FPDF(unit="pt", format=PAGE_SIZE)
    for i,pg in enumerate(pages,1):
        log(f"🖼️  image {i}/{len(pages)} …")
        img=overlay(make_image(pg,char_desc), pg)
        with tempfile.NamedTemporaryFile(delete=False,suffix=".png") as tmp:
            img.save(tmp.name,"PNG")
            pdf.add_page(); pdf.image(tmp.name,x=0,y=0,w=PAGE_SIZE[0],h=PAGE_SIZE[1])
    pdf.output(pdf_path.as_posix()); print(f"✅  PDF → {pdf_path.resolve()}")
    with open(txt_path,"w",encoding="utf-8") as f:
        f.write(f"Theme: {theme}\nCharacter: {char_desc}\n\nPages:\n")
        for p in pages: f.write(f"- {p['title']}: {p['text']}\n")
        f.write("\nImage prompts:\n")
        for i,prompt in enumerate(image_prompts,1): f.write(f"[{i}] {prompt}\n")
    print(f"📝  Log → {txt_path.resolve()}")

# ─── CLI (unchanged) ───────────────────────────────────────────────────────
if __name__=="__main__":
    theme=input("Story theme: ").strip() or "A shy penguin who wants to fly"
    try: pages=int(input("Pages? (default 6): ").strip() or 6)
    except ValueError: pages=6
    char_desc=character_descriptor(theme); log(f"🧸 Character → {char_desc}")
    story=story_pages(theme,pages,char_desc)
    build_assets(story, theme, char_desc)
