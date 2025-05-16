#!/usr/bin/env python3
"""
cli.py — GPT-4o text + Imagen-3 story-book generator  ·  v24
• Removes unsupported random_seed param (fixes ValidationError)
• Still uses CHAR_LOCK + NEG_PROMPT for visual consistency
• Page captions body-only; multi-input CLI
"""

import io, json, os, sys, textwrap, time, tempfile, unicodedata, random, re
from pathlib import Path
from dotenv import load_dotenv
import openai, google.genai as genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from fpdf import FPDF

# ─── STATIC CONFIG ─────────────────────────────────────────────────────────
PAGE_SIZE        = (595, 842)
RAW_SIZE         = (768, 1024)
TEXT_MODEL       = "gpt-4o-mini"
IMG_MODEL        = "imagen-3.0-generate-002"
MAX_RETRY        = 2
GUIDANCE_SCALE   = 7.5

STYLE = ("Vibrant storybook illustration, richly saturated colours, soft ambient light, "
         "gentle depth, digital-painting brush strokes, child-friendly, no hard outlines")
NO_TEXT_CLAUSE   = ("No text, no letters, no words, no captions, "
                    "no subtitles, no watermark.")
NEG_PROMPT = ("extra limbs, extra legs, extra arms, mutated anatomy, wrong outfit, "
              "costume swap, outfit change")

COVER_MAX_PT, COVER_MIN_PT   = 48, 20
COVER_BANNER_H, COVER_SIDE_PAD = 180, 60
COVER_LINE_SPACING, COVER_CLOUD_ALPHA = 8, 230

# ─── HELPERS (fonts / utils) ───────────────────────────────────────────────
log  = lambda m: print(m, file=sys.stderr)
safe = lambda t: unicodedata.normalize("NFKD", t).encode("latin-1","ignore").decode()

def load_font(paths, size):
    for p in paths:
        try: return ImageFont.truetype(p, size)
        except (OSError, IOError): pass
    return ImageFont.load_default()

FONT_BODY = load_font(
    ["DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "Arial.ttf"], 20)

def text_size(draw, txt, font):
    if hasattr(draw, "textbbox"):
        b = draw.textbbox((0,0), txt, font=font); return b[2]-b[0], b[3]-b[1]
    return draw.textsize(txt, font=font)

# ─── SDK INIT ──────────────────────────────────────────────────────────────
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")  or sys.exit("❌  Set OPENAI_API_KEY")
google_key     = os.getenv("GOOGLE_API_KEY") or sys.exit("❌  Set GOOGLE_API_KEY")
gen_client     = genai.Client(api_key=google_key)

# ─── GPT UTILITIES ─────────────────────────────────────────────────────────
def character_descriptions(char_list):
    joined=", ".join(char_list)
    msgs=[{"role":"system",
           "content":"Describe each animal in ONE vivid sentence."},
          {"role":"user",
           "content":f"Characters: {joined}"}]
    raw=openai.chat.completions.create(model=TEXT_MODEL,temperature=0.7,
                                       messages=msgs).choices[0].message.content
    return " ".join(line.strip() for line in raw.split("\n") if line.strip())

def build_char_lock(desc):
    fixed=[]
    for s in re.split(r'[.!?]', desc):
        s=s.strip()
        if s:
            s=re.sub(r'\b(mongoose|deer|rabbit|fox|bear|bunny)\b',
                     r'\1 (exactly four legs)', s, flags=re.I)
            fixed.append(s)
    return " ".join(fixed)

def rewrite_title(theme, chars):
    msgs=[{"role":"system",
           "content":"Create a catchy kids’ title ≤7 words; include theme word + one character name."},
          {"role":"user",
           "content":f"Theme: {theme}\nCharacters: {', '.join(chars)}"}]
    raw=openai.chat.completions.create(model=TEXT_MODEL,temperature=0.7,
                                       messages=msgs).choices[0].message.content
    return re.sub(r"^[\d\W_]+\s*", "", raw.split("\n")[0].strip())[:60]

def scene_prompt(body, lock):
    msgs=[{"role":"system",
           "content":"Turn story text into ONE vivid illustration prompt ≤35 words. "
                     "Keep characters EXACTLY as described. No text in scene."},
          {"role":"user",
           "content":f"Characters: {lock}\nScene: {body}"}]
    return openai.chat.completions.create(model=TEXT_MODEL,temperature=0.3,
            messages=msgs).choices[0].message.content.strip()

def story_pages(theme,n,moral,lock):
    msgs=[{"role":"system",
           "content":"You are a playful children’s author (ages 4–8). Return JSON exactly."},
          {"role":"user","content":
f"""Theme: “{theme}”
Characters: {lock}
Moral: {moral}
Pages: {n}

Return ONLY JSON: {{"pages":[{{"text":"…"}}]}}.
Each page body = 2 sentences (10-15 words) + one 3-5-word quote.
Continuity must build toward moral; final page states it clearly."""}]
    rsp=openai.chat.completions.create(model=TEXT_MODEL,temperature=0.7,
                                       messages=msgs,
                                       response_format={"type":"json_object"})
    try: return json.loads(rsp.choices[0].message.content)["pages"][:n]
    except Exception: return [{"text":"…"}]*n

# ─── IMAGEN ────────────────────────────────────────────────────────────────
def _imagen(prompt):
    cfg=types.GenerateImagesConfig(
        number_of_images=1,
        aspect_ratio="3:4",
        guidance_scale=GUIDANCE_SCALE
    )
    for _ in range(MAX_RETRY):
        try:
            img_bytes=gen_client.models.generate_images(
                model=IMG_MODEL,prompt=prompt,config=cfg
            ).generated_images[0].image.image_bytes
            return Image.open(io.BytesIO(img_bytes))
        except Exception as e:
            log(f"⚠️  Imagen error: {e}"); time.sleep(1)
    return Image.new("RGB", RAW_SIZE, (220,220,220))

def _prep(img): return img.convert("RGB").resize(RAW_SIZE,Image.LANCZOS)\
                                   .resize(PAGE_SIZE,Image.LANCZOS)

# ─── COVER ─────────────────────────────────────────────────────────────────
def make_cover(title, lock):
    prompt=f"{STYLE}. {lock}. Front-cover illustration. Blank top banner. {NO_TEXT_CLAUSE} --negative {NEG_PROMPT}"
    img=_prep(_imagen(prompt)).convert("RGBA"); W,H=img.size
    draw=ImageDraw.Draw(img)
    size=COVER_MAX_PT
    while size>=COVER_MIN_PT:
        font=load_font(["DejaVuSans-Bold.ttf",
                        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                        "Arial Bold.ttf"], size)
        chars=int((W-2*COVER_SIDE_PAD)/font.getlength("M"))
        wrapped=textwrap.fill(safe(title), width=max(1,chars))
        w,h=text_size(draw, wrapped, font)
        if h<=COVER_BANNER_H-40 and w<=W-2*COVER_SIDE_PAD: break
        size-=2
    cloud=Image.new("RGBA", img.size, (0,0,0,0))
    ImageDraw.Draw(cloud).rectangle((0,0,W,COVER_BANNER_H),
                                    fill=(255,255,255,COVER_CLOUD_ALPHA))
    img.alpha_composite(cloud.filter(ImageFilter.GaussianBlur(8)))
    y=(COVER_BANNER_H-h)//2
    for line in wrapped.split("\n"):
        lw,lh=text_size(draw, line, font)
        draw.text(((W-lw)//2, y), line, font=font, fill=(20,20,120))
        y+=lh+COVER_LINE_SPACING
    return img.convert("RGB")

# ─── PAGE RENDER ───────────────────────────────────────────────────────────
def make_page_image(pg, lock):
    scene=scene_prompt(pg['text'], lock)
    prompt=f"{STYLE}. {lock}. {scene}. {NO_TEXT_CLAUSE} --negative {NEG_PROMPT}"
    return _prep(_imagen(prompt))

def overlay(img, pg):
    img=img.convert("RGBA"); W,H=img.size
    side_pad, vert_pad = 36, 20
    draw=ImageDraw.Draw(img)
    avg=FONT_BODY.getlength("ABCDEFGHIJKLMNOPQRSTUVWXYZ")/26
    body=textwrap.fill(safe(pg["text"]), int((W-2*side_pad)/avg))
    b_w,b_h=text_size(draw, body, FONT_BODY)
    panel_w=min(b_w+2*side_pad, W-2*side_pad)
    card_h=vert_pad+b_h+vert_pad
    top=12 if random.random()<0.5 else H-card_h-12
    rect=(side_pad,top,side_pad+panel_w,top+card_h)
    cloud=Image.new("RGBA",img.size,(255,255,255,0))
    ImageDraw.Draw(cloud).rounded_rectangle(rect,26,fill=(255,255,255,235))
    img.alpha_composite(cloud.filter(ImageFilter.GaussianBlur(12)))
    draw.multiline_text((side_pad*2, top+vert_pad),
                        body, font=FONT_BODY, fill=(20,20,120),
                        spacing=4, align="left")
    return img.convert("RGB")

# ─── PDF ───────────────────────────────────────────────────────────────────
def build_pdf(pages,title,lock):
    out=Path("outputs/pdf"); out.mkdir(parents=True,exist_ok=True)
    safe_name="".join(c if c.isalnum() else "_" for c in title)[:40] or "book"
    pdf_path=out/f"storybook_{safe_name}.pdf"
    pdf=FPDF(unit="pt", format=PAGE_SIZE)
    cover=make_cover(title,lock)
    with tempfile.NamedTemporaryFile(delete=False,suffix=".png") as tmp:
        cover.save(tmp.name,"PNG"); pdf.add_page()
        pdf.image(tmp.name, x=0, y=0, w=PAGE_SIZE[0], h=PAGE_SIZE[1])
    for i,pg in enumerate(pages,1):
        log(f"🖼️  image {i}/{len(pages)} …")
        img=overlay(make_page_image(pg,lock), pg)
        with tempfile.NamedTemporaryFile(delete=False,suffix=".png") as tmp:
            img.save(tmp.name,"PNG"); pdf.add_page()
            pdf.image(tmp.name, x=0, y=0, w=PAGE_SIZE[0], h=PAGE_SIZE[1])
    pdf.output(pdf_path.as_posix()); print(f"✅  PDF → {pdf_path.resolve()}")

# ─── CLI ───────────────────────────────────────────────────────────────────
if __name__=="__main__":
    theme=input("Story Theme: ").strip() or "Honesty: Telling the Truth"
    chars=input("Characters (comma-separated): ").strip() \
          or "Meeku the Mongoose, Chinu the Chital"
    moral=input("Moral / Focus: ").strip() \
          or "Meeku learns honesty after breaking Chinu’s singing shell."
    try: pages=int(input("Story pages (default 10): ").strip() or 10)
    except ValueError: pages=10

    char_list=[c.strip() for c in chars.split(",") if c.strip()]
    desc=character_descriptions(char_list)
    CHAR_LOCK=build_char_lock(desc)
    title=rewrite_title(theme,char_list)

    log(f"🧸 {desc}")
    log(f"🔒 {CHAR_LOCK}")
    log(f"📕 {title}")

    build_pdf(story_pages(theme,pages,moral,CHAR_LOCK), title, CHAR_LOCK)
