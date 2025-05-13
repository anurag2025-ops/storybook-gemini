#!/usr/bin/env python3
"""
cli.py — GPT-4o text + Imagen-3 story-book generator  ·  v16
• guidance_scale back to 7.5 (default detail level)
• Cover title is an AI-rewritten, catchy phrase (≤7 words)
"""

import io, json, os, sys, textwrap, time, tempfile, unicodedata, random
from pathlib import Path
from dotenv import load_dotenv
import openai, google.genai as genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFont, ImageFilter, UnidentifiedImageError
from fpdf import FPDF

# ─── static config ─────────────────────────────────────────────────────────
PAGE_SIZE   = (595, 842)
RAW_SIZE    = (768, 1024)
TEXT_MODEL  = "gpt-4o-mini"
IMG_MODEL   = "imagen-3.0-generate-002"
MAX_RETRY   = 2
GUIDANCE_SCALE = 7.5              # ← back to default

TEXT_COLOR  = (20, 20, 120)

STYLE = (
    "Vibrant high-definition storybook illustration — richly saturated colours, "
    "crisp line-work, soft ambient lighting, gentle depth of field, smooth digital-painting "
    "brush strokes, subtle grain for warmth, child-friendly, no hard outlines"
)
NO_TEXT_CLAUSE = "No text, no letters, no words, no watermark."

# cover layout
COVER_MAX_PT = 48
COVER_MIN_PT = 20
COVER_BANNER_H = 180
COVER_SIDE_PAD = 60
COVER_LINE_SPACING = 8
COVER_CLOUD_ALPHA = 230

# ─── helpers ───────────────────────────────────────────────────────────────
log  = lambda m: print(m, file=sys.stderr)
safe = lambda t: unicodedata.normalize("NFKD", t).encode("latin-1", "ignore").decode()

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

def text_size(draw, txt, font):
    if hasattr(draw, "textbbox"):
        b = draw.textbbox((0, 0), txt, font=font)
        return b[2] - b[0], b[3] - b[1]
    return draw.textsize(txt, font=font)

# ─── SDK init ──────────────────────────────────────────────────────────────
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")  or sys.exit("❌  Set OPENAI_API_KEY")
google_key     = os.getenv("GOOGLE_API_KEY") or sys.exit("❌  Set GOOGLE_API_KEY")
gen_client     = genai.Client(api_key=google_key)

# ─── GPT utilities ─────────────────────────────────────────────────────────
def character_descriptor(theme):
    msgs=[
        {"role":"system","content":"You are a creative children’s author."},
        {"role":"user",
         "content":f"Describe the main character for “{theme}” in ONE vivid sentence."}
    ]
    return openai.chat.completions.create(model=TEXT_MODEL,temperature=0.7,
                messages=msgs).choices[0].message.content.strip().split("\n")[0][:120]

def rewrite_title(theme):
    """Return a snappy ≤7-word title based on the theme."""
    msgs=[
        {"role":"system",
         "content":"You invent catchy story-book titles for kids (max 7 words)."},
        {"role":"user",
         "content":f"Write a fresh, fun title (≤7 words) for this story idea:\n“{theme}”"}
    ]
    raw=openai.chat.completions.create(model=TEXT_MODEL,temperature=0.7,
                                       messages=msgs)\
         .choices[0].message.content.strip()
    return raw.split("\n")[0][:60]

def story_pages(theme, n, char_desc):
    msgs=[
      {"role":"system",
       "content":"You are a warm, playful children’s author for ages 4-8. "
                 "ALWAYS return valid JSON exactly as specified."},
      {"role":"user","content":
f"""Main character: {char_desc}
Theme/title: “{theme}”
Total pages (not counting cover): {n}

Return ONLY JSON: {{"pages":[{{"title":"…","text":"…"}}]}}.

Continuity rules:
• Story must flow page-to-page like one adventure (no resets).
• Each page builds on the previous events.
• Use simple transitions (“Next, …”, “Then, …”).

Page format:
• Title: 3-5 words.
• Body: 2 sentences (10-15 words each) + one 3-5-word dialogue quote.
• Page 1 = setup, next pages adventure, final page moral.
"""}]
    rsp=openai.chat.completions.create(model=TEXT_MODEL,temperature=0.7,
                                       messages=msgs,
                                       response_format={"type":"json_object"})
    try:
        return json.loads(rsp.choices[0].message.content)["pages"][:n]
    except Exception:
        return [{"title":"Untitled","text":"…"}]*n

# ─── Imagen wrappers ───────────────────────────────────────────────────────
def _imagen(prompt):
    cfg = types.GenerateImagesConfig(number_of_images=1, aspect_ratio="3:4",
                                     guidance_scale=GUIDANCE_SCALE)
    for _ in range(MAX_RETRY):
        try:
            img_bytes = gen_client.models.generate_images(model=IMG_MODEL,
                        prompt=prompt, config=cfg).generated_images[0].image.image_bytes
            return Image.open(io.BytesIO(img_bytes))
        except Exception as e:
            log(f"⚠️  Imagen error: {e}"); time.sleep(1)
    return Image.new("RGB", RAW_SIZE, (220,220,220))

def _prep(img):
    return img.convert("RGB").resize(RAW_SIZE, Image.LANCZOS)\
                             .resize(PAGE_SIZE, Image.LANCZOS)

# ─── Cover builder ─────────────────────────────────────────────────────────
def make_cover(theme, char_desc):
    title = rewrite_title(theme)
    prompt=(f"{STYLE}. Front-cover illustration of {char_desc}. Dynamic composition, "
            "ample space above the hero for title banner. " + NO_TEXT_CLAUSE)
    img=_prep(_imagen(prompt)).convert("RGBA"); W,H=img.size
    draw=ImageDraw.Draw(img)
    # choose font size & wrap
    size=COVER_MAX_PT
    while size>=COVER_MIN_PT:
        font=load_font(["DejaVuSans-Bold.ttf",
                        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                        "Arial Bold.ttf"], size)
        chars=int((W-2*COVER_SIDE_PAD)/font.getlength("M"))
        wrapped=textwrap.fill(safe(title), width=max(1,chars))
        w,h=text_size(draw,wrapped,font)
        if h<=COVER_BANNER_H-40 and w<=W-2*COVER_SIDE_PAD:
            break
        size-=2
    # banner cloud
    cloud=Image.new("RGBA", img.size, (0,0,0,0))
    ImageDraw.Draw(cloud).rectangle((0,0,W,COVER_BANNER_H),
                                    fill=(255,255,255,COVER_CLOUD_ALPHA))
    img.alpha_composite(cloud.filter(ImageFilter.GaussianBlur(8)))
    # draw title centered
    y=(COVER_BANNER_H-h)//2
    for line in wrapped.split("\n"):
        lw,lh=text_size(draw,line,font)
        draw.text(((W-lw)//2,y),line,font=font,fill=TEXT_COLOR)
        y+=lh+COVER_LINE_SPACING
    return img.convert("RGB")

# ─── page image, overlay, PDF build (unchanged visuals) ────────────────────
def make_page_image(pg,char_desc):
    base=f"{STYLE}. {char_desc}. {NO_TEXT_CLAUSE}"
    for p in [f"{base} Blank caption margin. Scene: {pg['text']}",
              f"{base} Scene: {pg['text']}",
              f"{STYLE}. Child-friendly digital painting. {NO_TEXT_CLAUSE}"]:
        img=_imagen(p)
        if img.size!=(RAW_SIZE[0],RAW_SIZE[1]): return _prep(img)
    return _prep(img)

def overlay(img,pg):
    img=img.convert("RGBA"); W,H=img.size
    side_pad,vert_pad,gap=36,20,6
    draw=ImageDraw.Draw(img)
    avg=FONT_BODY.getlength("ABCDEFGHIJKLMNOPQRSTUVWXYZ")/26
    body=textwrap.fill(safe(pg["text"]),
                       int((W-2*side_pad)/avg))
    title_w,title_h=text_size(draw,safe(pg["title"]),FONT_TITLE)
    body_w,body_h=text_size(draw,body,FONT_BODY)
    panel_w=min(max(title_w,body_w)+2*side_pad,W-2*side_pad)
    card_h=vert_pad+title_h+gap+body_h+vert_pad
    top=12 if random.random()<0.5 else H-card_h-12
    rect=(side_pad,top,side_pad+panel_w,top+card_h)
    cloud=Image.new("RGBA",img.size,(255,255,255,0))
    ImageDraw.Draw(cloud).rounded_rectangle(rect,26,fill=(255,255,255,235))
    img.alpha_composite(cloud.filter(ImageFilter.GaussianBlur(12)))
    tx=side_pad*2
    draw.text((tx,top+vert_pad),safe(pg["title"]),font=FONT_TITLE,fill=TEXT_COLOR)
    draw.multiline_text((tx,top+vert_pad+title_h+gap),body,font=FONT_BODY,
                        fill=TEXT_COLOR,spacing=4,align="left")
    return img.convert("RGB")

def build_pdf(pages,theme,char_desc):
    pdf_dir=Path("outputs/pdf"); pdf_dir.mkdir(parents=True,exist_ok=True)
    safe_name="".join(c if c.isalnum() else "_" for c in theme)[:40] or "book"
    pdf_path=pdf_dir/f"storybook_{safe_name}.pdf"
    pdf=FPDF(unit="pt",format=PAGE_SIZE)
    cover=make_cover(theme,char_desc)
    with tempfile.NamedTemporaryFile(delete=False,suffix=".png") as tmp:
        cover.save(tmp.name,"PNG"); pdf.add_page()
        pdf.image(tmp.name,x=0,y=0,w=PAGE_SIZE[0],h=PAGE_SIZE[1])
    for i,pg in enumerate(pages,1):
        log(f"🖼️  image {i}/{len(pages)} …")
        img=overlay(make_page_image(pg,char_desc),pg)
        with tempfile.NamedTemporaryFile(delete=False,suffix=".png") as tmp:
            img.save(tmp.name,"PNG"); pdf.add_page()
            pdf.image(tmp.name,x=0,y=0,w=PAGE_SIZE[0],h=PAGE_SIZE[1])
    pdf.output(pdf_path.as_posix()); print(f"✅  PDF → {pdf_path.resolve()}")

# ─── CLI ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    theme=input("Story theme: ").strip() or "Finn the fox and his adventure in the city"
    try: pages=int(input("Story pages (default 10): ").strip() or 10)
    except ValueError: pages=10
    char_desc=character_descriptor(theme); log(f"🧸 Character → {char_desc}")
    build_pdf(story_pages(theme,pages,char_desc),theme,char_desc)
