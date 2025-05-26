#!/usr/bin/env python3
"""
cli.py · v38.3.2-stable
• Characters used exactly as typed → colour/wardrobe stay fixed
• Bottom 12 % kept totally blank (flat pastel ground) for cloud caption
• Page n/total progress logs
• Random cloud-shaped caption bubble
"""

# ── stdlib
import io, json, os, sys, tempfile, textwrap, unicodedata, time, random, signal
from pathlib import Path
from uuid import uuid4
from datetime import datetime
from contextlib import contextmanager

# ── third-party
import openai, google.genai as genai
from google.genai import types
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from fpdf import FPDF

# ── constants ───────────────────────────────────────────────────────────
PAGE_SIZE, RAW_SIZE   = (595, 842), (768, 1024)
TEXT_MODEL, IMG_MODEL = "gpt-4o-mini", "imagen-3.0-generate-002"
GUIDANCE_SCALE        = 9.0
MAX_RETRY             = 2
TIMEOUT_SEC           = 60
CAPTION_PCT           = 10
STYLE_TAG             = "##" + uuid4().hex[:8].upper() + "##"

RESERVE = (f"Compose the illustration in the upper {100-CAPTION_PCT}% of the frame, "
           f"leaving the entire bottom {CAPTION_PCT}% EMPTY (flat pastel ground) for text.")
STYLE = ("Whimsical storybook illustration, soft watercolor cartoon, hand-painted textures, "
         "warm pastel palette, dreamy sunset lighting, paper-grain texture, subtle blurry outlines, "
         "cheerful sentimental tone, 300 dpi, no text")
NO_TEXT = "No text, no title, no words, no letters, no subtitles, no watermark."
NEG = ("extra limbs, mutated anatomy, wrong proportions, watermark, blurry, harsh lighting, "
       "any change of colours, modern digital style, realistic rendering")

safe = lambda s: unicodedata.normalize("NFKD", s).encode("latin-1","ignore").decode()
log  = lambda m: print(m, file=sys.stderr, flush=True)

# ── timeout helper ──────────────────────────────────────────────────────
@contextmanager
def soft_timeout(sec):
    if sec is None: yield; return
    def _h(*_): raise TimeoutError
    old = signal.signal(signal.SIGALRM, _h); signal.alarm(sec)
    try: yield
    finally: signal.alarm(0); signal.signal(signal.SIGALRM, old)

# ── keys ────────────────────────────────────────────────────────────────
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY") or sys.exit("OPENAI_API_KEY missing")
gen_client     = genai.Client(api_key=os.getenv("GOOGLE_API_KEY") or sys.exit("GOOGLE_API_KEY missing"))

# ── prompt log ──────────────────────────────────────────────────────────
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
log_dir = Path("outputs/generated_prompts"); log_dir.mkdir(parents=True, exist_ok=True)
log_file = log_dir / f"prompts_{ts}.txt"
def dump(tag, txt):
    with log_file.open("a", encoding="utf-8") as f:
        f.write(f"--- {tag} ---\n{txt}\n\n")

# ── font util ───────────────────────────────────────────────────────────
def font_default(sz):
    for p in ("DejaVuSans.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "Arial.ttf"):
        try: return ImageFont.truetype(p, sz)
        except Exception: pass
    return ImageFont.load_default()
FONT_BODY = font_default(20)
def txt_wh(d,t,f): x0,y0,x1,y1 = d.textbbox((0,0),t,font=f); return x1-x0, y1-y0

# ── Imagen wrapper with page logging & timeout ──────────────────────────
def imagen(prompt, idx, total):
    cfg = types.GenerateImagesConfig(number_of_images=1,
                                     aspect_ratio="3:4",
                                     guidance_scale=GUIDANCE_SCALE)
    last=None
    for attempt in range(1, MAX_RETRY+2):
        log(f"⏳  Imagen rendering page {idx}/{total} (try {attempt}/{MAX_RETRY+1}) …")
        try:
            with soft_timeout(TIMEOUT_SEC):
                r = gen_client.models.generate_images(model=IMG_MODEL, prompt=prompt, config=cfg)
            if r.generated_images and r.generated_images[0].image.image_bytes:
                return Image.open(io.BytesIO(r.generated_images[0].image.image_bytes))
            last = RuntimeError("Empty image bytes")
        except TimeoutError: last = RuntimeError("Timed out")
        except Exception as e: last = e
        log(f"Imagen error: {last}")
        if attempt < MAX_RETRY+1: time.sleep(1+attempt)
    return Image.new("RGB", RAW_SIZE, (220,220,220))

def prep(img): return img.convert("RGB").resize(RAW_SIZE, Image.LANCZOS).resize(PAGE_SIZE, Image.LANCZOS)

# ── GPT helper (unchanged) ----------------------------------------------
def chat(msgs,t,fmt=None):
    back=1
    for _ in range(3):
        try: r=openai.chat.completions.create(model=TEXT_MODEL,temperature=t,messages=msgs,response_format=fmt or {"type":"text"})
        except openai.RateLimitError: time.sleep(back); back*=2; continue
        return r.choices[0].message.content
    raise RuntimeError("GPT failed thrice")

# ── plan: **no GPT rewrite** → use user text verbatim -------------------
def plan(theme, chars):
    joined = ", ".join(chars) if chars else "Characters"
    lock   = f"{joined} {STYLE_TAG}"
    title  = f"{theme.title()} Adventure"
    return lock, title, joined

# ── story (unchanged) ----------------------------------------------------
def story(theme,n,moral,lock):
    pages=json.loads(chat(
        [{"role":"system","content":"Return JSON {pages:[{text,img_prompt,prev_syn}...]}."},
         {"role":"user","content":f"Theme:{theme}\nCharacters:{lock}\nMoral:{moral}\nPages:{n}"}],
        0.7, fmt={"type":"json_object"}))["pages"][:n]
    for p in pages:
        for k in ("text","img_prompt","prev_syn"): p[k]=str(p.get(k,"")).strip()
    return pages

# ── cloud helper ---------------------------------------------------------
def draw_cloud(draw, left, top, right, bottom, alpha):
    w, h = right-left, bottom-top
    cx, cy = left+w/2, top+h/2
    base_r = h/2
    lobes = random.randint(4,6)
    for i in range(lobes):
        ang = i*(360/lobes) + random.uniform(-10,10)
        rad = base_r * random.uniform(0.9,1.1)
        ox = cx + (w/4)*math.cos(math.radians(ang))
        oy = cy + (h/8)*math.sin(math.radians(ang))
        draw.ellipse((ox-rad, oy-rad, ox+rad, oy+rad), fill=(255,255,255,alpha))

import math

# ── overlay (never cut off) ----------------------------------------------
def overlay(img, caption):
    img = img.convert("RGBA")
    W, H = img.size
    reserve_h   = int(H * CAPTION_PCT / 100)
    band_top    = H - reserve_h
    floor_gap   = 20          # keep at least 20 px above page edge
    left_margin = 40
    pad         = 24
    usable_w    = W - 2*left_margin - 2*pad

    # pixel-measured wrapping
    d = ImageDraw.Draw(img)
    words, lines, line = safe(caption).split(), [], ""
    for w in words:
        test = (line + " " + w).strip()
        if d.textlength(test, FONT_BODY) <= usable_w:
            line = test
        else:
            if line: lines.append(line)
            line = w
    if line: lines.append(line)
    wrap = "\n".join(lines)

    bw, bh = txt_wh(d, wrap, FONT_BODY)
    band_h = bh + 2*pad

    # preferred position: centred within reserved strip
    top = band_top + max(0, (reserve_h - band_h)//2)

    # if bubble doesn’t fit, slide it up so bottom stays visible
    if top + band_h + floor_gap > H:
        top = max(0, H - band_h - floor_gap)
    bottom = top + band_h

    # draw rounded rectangle bubble
    bubble = Image.new("RGBA", img.size, (0,0,0,0))
    ImageDraw.Draw(bubble).rounded_rectangle(
        (left_margin, top, W - left_margin, bottom),
        radius=30,
        fill=(255, 255, 255, 195)
    )
    img.alpha_composite(bubble.filter(ImageFilter.GaussianBlur(3)))

    # draw text
    d.multiline_text((left_margin + pad, top + pad),
                     wrap,
                     font=FONT_BODY,
                     fill=(45, 45, 45),
                     spacing=4)

    return img.convert("RGB")


# ── cover (unchanged) ----------------------------------------------------
def cover(title, lock, theme):
    p=(f"{lock}. {STYLE}. {theme}. {STYLE_TAG}. Front cover illustration. "
       f"{NO_TEXT} --negative {NEG}")
    dump("cover_prompt",p)
    return prep(imagen(p,0,0))

# ── PDF builder ----------------------------------------------------------
def build_pdf(pages,title,lock,rem,theme):
    out = Path("outputs/pdf"); out.mkdir(parents=True, exist_ok=True)
    pdf_path = out / f"storybook_{STYLE_TAG[2:-2]}_{uuid4().hex[:4]}.pdf"
    pdf = FPDF(unit="pt", format=PAGE_SIZE)

    pdf.add_page()
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        cover(title,lock,theme).save(tmp.name,"PNG")
        pdf.image(tmp.name,0,0,w=PAGE_SIZE[0],h=PAGE_SIZE[1]); os.unlink(tmp.name)

    prev=""; total=len(pages)
    for i,p in enumerate(pages,1):
        prompt=(f"{lock}. {STYLE}. {prev} {p['img_prompt']}. {rem}. "
                f"{RESERVE} {STYLE_TAG}. {NO_TEXT} --negative {NEG}")
        dump(f"page_{i}",prompt)
        img = overlay(prep(imagen(prompt,i,total)), p["text"])

        pdf.add_page()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp.name,"PNG")
            pdf.image(tmp.name,0,0,w=PAGE_SIZE[0],h=PAGE_SIZE[1]); os.unlink(tmp.name)
        prev=f"Previously: {p['prev_syn']}."
    pdf.output(pdf_path.as_posix())
    print("✅ PDF →", pdf_path.resolve())
    print("📝 Prompts →", log_file.resolve())

# ── CLI ------------------------------------------------------------------
def main():
    theme=input("Theme: ").strip() or "Helping Others"
    chars=input("Characters (comma-sep): ").strip()
    moral=input("Moral: ").strip() or "Helping warms the heart."
    try: n=int(input("Pages (default 8): ").strip() or 8)
    except ValueError: n=8
    char_list=[c.strip() for c in chars.split(",") if c.strip()]

    log("📑 Planning lock & title …")
    lock,title,rem = plan(theme,char_list)

    log("📚 Generating story pages …")
    pages = story(theme,n,moral,lock)

    build_pdf(pages,title,lock,rem,theme)

if __name__=="__main__":
    main()
