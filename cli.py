#!/usr/bin/env python3
"""
cli.py · v38-consistency
Keeps v37b structure but:
• Stronger lock format
• Lock echoed + concise reminder inside every prompt
• GUIDANCE_SCALE 9
"""

# — stdlib
import io, json, os, sys, tempfile, textwrap, unicodedata, time, random
from pathlib import Path
from uuid import uuid4
from datetime import datetime

# — 3rd-party
import openai, google.genai as genai
from google.genai import types
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from fpdf import FPDF

# — constants -------------------------------------------------------------
PAGE_SIZE, RAW_SIZE   = (595, 842), (768, 1024)
TEXT_MODEL, IMG_MODEL = "gpt-4o-mini", "imagen-3.0-generate-002"
GUIDANCE_SCALE        = 9.0   # ← stronger obedience
MAX_RETRY             = 2
STYLE_TAG             = "##" + uuid4().hex[:8].upper() + "##"

STYLE = ("Vibrant storybook illustration, richly saturated colours, soft ambient light, "
         "gentle depth, digital-painting brush strokes, child-friendly, no hard outlines")
NO_TEXT = "No text, no letters, no words, no subtitles, no watermark."
NEG = ("extra limbs, mutated anatomy, wrong outfit, outfit change, watermark, blurry, ugly, "
       "any change of colours, clothes, props")

safe = lambda s: unicodedata.normalize("NFKD", s).encode("latin-1","ignore").decode()
log  = lambda m: print(m, file=sys.stderr, flush=True)

# — keys ------------------------------------------------------------------
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY") or sys.exit("OPENAI_API_KEY missing")
gkey           = os.getenv("GOOGLE_API_KEY") or sys.exit("GOOGLE_API_KEY missing")
gen_client     = genai.Client(api_key=gkey)

# — prompt log ------------------------------------------------------------
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
log_dir = Path("outputs/generated_prompts"); log_dir.mkdir(parents=True, exist_ok=True)
log_file = log_dir / f"prompts_{ts}.txt"
def dump(tag, txt):
    with log_file.open("a", encoding="utf-8") as f:
        f.write(f"--- {tag} ---\n{txt}\n\n")

# — font util -------------------------------------------------------------
def font_default(sz):
    for p in ("DejaVuSans.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "Arial.ttf"):
        try: return ImageFont.truetype(p, sz)
        except Exception: pass
    return ImageFont.load_default()
FONT_BODY = font_default(20)
def txt_wh(d,t,f): box=d.textbbox((0,0),t,font=f); return box[2]-box[0], box[3]-box[1]

# — Imagen wrapper --------------------------------------------------------
def imagen(prompt):
    cfg = types.GenerateImagesConfig(number_of_images=1,
                                     aspect_ratio="3:4",
                                     guidance_scale=GUIDANCE_SCALE)
    last=None
    for i in range(MAX_RETRY+1):
        try:
            r = gen_client.models.generate_images(model=IMG_MODEL, prompt=prompt, config=cfg)
            if r.generated_images and r.generated_images[0].image.image_bytes:
                return Image.open(io.BytesIO(r.generated_images[0].image.image_bytes))
            last = RuntimeError("Empty image bytes")
        except Exception as e:
            last=e
        log(f"Imagen error (try {i+1}/{MAX_RETRY+1}): {last}")
        if i<MAX_RETRY: time.sleep(1+i)
    return Image.new("RGB", RAW_SIZE, (220,220,220))
def prep(i): return i.convert("RGB").resize(RAW_SIZE, Image.LANCZOS).resize(PAGE_SIZE, Image.LANCZOS)

# — GPT helper with retry -------------------------------------------------
def chat(msgs,temp,fmt=None):
    back=1
    for i in range(3):
        try:
            r=openai.chat.completions.create(model=TEXT_MODEL,temperature=temp,
                                             messages=msgs,
                                             response_format=fmt or {"type":"text"})
            return r.choices[0].message.content
        except openai.RateLimitError as e:
            log(f"GPT rate limit {e} (retry {i+1})"); time.sleep(back); back*=2
    raise RuntimeError("GPT failed thrice")

# — Step 1: lock & title --------------------------------------------------
def plan(theme, chars):
    joined=", ".join(chars) if chars else "None"
    sys = (
      "Return JSON {lock,title}. "
      "lock MUST be a single comma-separated list like: "
      "'Rani the Rhino (grey skin, rickshaw vest, carries small tool kit); "
      "Bholu the Bear Cub (brown fur, t-shirt & shorts, small backpack). "
      "Never alter these appearances.'")
    raw=chat([{"role":"system","content":sys},
              {"role":"user","content":json.dumps({"theme":theme,"characters":joined})}],
             0.7, fmt={"type":"json_object"})
    data=json.loads(raw)
    base_lock=str(data.get("lock","")).strip()
    if not base_lock: base_lock="Characters must stay consistent."
    lock=f"{base_lock} {STYLE_TAG}"
    title=str(data.get("title",f"{theme.title()} Adventure")).strip()[:60]
    # Build short reminder: "Rani stays grey in vest; Bholu stays brown in shorts."
    reminder = "; ".join([seg.split("(")[0].strip()+" stays "+seg.split("(")[1].split(",")[0].strip()
                          if "(" in seg else seg.split()[0]+" stays unchanged"
                          for seg in base_lock.split(";")])
    return lock,title,reminder

# — Step 2: story pages ---------------------------------------------------
def story(theme,n,moral,lock):
    sys=("Return JSON {pages:[{text,img_prompt,prev_syn}...]}. "
         "text=2 sentences + 3-5 word quote. "
         "img_prompt=8-14 words with characters. "
         "prev_syn=ONE visual summary sentence.")
    user=f"Theme:{theme}\nCharacters:{lock}\nMoral:{moral}\nPages:{n}"
    pages=json.loads(chat([{"role":"system","content":sys},
                           {"role":"user","content":user}],
                          0.7, fmt={"type":"json_object"}))["pages"][:n]
    for p in pages:
        for k in ("text","img_prompt","prev_syn"):
            p[k]=str(p.get(k,"")).strip()
    return pages

# — overlay ---------------------------------------------------------------
def overlay(img, caption):
    img=img.convert("RGBA"); W,H=img.size
    d=ImageDraw.Draw(img)
    avg=FONT_BODY.getlength("M") if hasattr(FONT_BODY,'getlength') else FONT_BODY.size*0.6
    wrap=textwrap.fill(safe(caption), max(1,int((W-72)/avg)))
    bw,bh=txt_wh(d,wrap,FONT_BODY)
    y=random.choice([20,H-bh-60])
    cloud=Image.new("RGBA",img.size,(0,0,0,0))
    ImageDraw.Draw(cloud).rounded_rectangle((36,y,W-36,y+bh+40),26,fill=(255,255,255,235))
    img.alpha_composite(cloud.filter(ImageFilter.GaussianBlur(12)))
    d.multiline_text((48,y+20),wrap,font=FONT_BODY,fill=(20,20,120),spacing=4)
    return img.convert("RGB")

# — cover -----------------------------------------------------------------
def cover(title,lock,theme):
    p=(f"{lock}. {STYLE}. {theme}. {STYLE_TAG}. Front cover illustration. "
       f"Blank top banner. {NO_TEXT} --negative {NEG}")
    dump("cover_prompt",p)
    img=prep(imagen(p)).convert("RGBA"); W,H=img.size
    d=ImageDraw.Draw(img); fs=48
    while fs>=20:
        f=font_default(fs)
        avg=f.getlength("M") if hasattr(f,'getlength') else fs*0.6
        wrap=textwrap.fill(safe(title), max(1,int((W-120)/avg)))
        tw,th=txt_wh(d,wrap,f)
        if th<=140 and tw<=W-120: break
        fs-=2
    cloud=Image.new("RGBA",img.size,(0,0,0,0))
    ImageDraw.Draw(cloud).rectangle((0,0,W,180),fill=(255,255,255,230))
    img.alpha_composite(cloud.filter(ImageFilter.GaussianBlur(8)))
    y=(180-th)//2
    for line in wrap.split("\n"):
        lw,lh=txt_wh(d,line,f)
        d.text(((W-lw)//2,y),line,font=f,fill=(20,20,120)); y+=lh+6
    return img.convert("RGB")

# — PDF builder -----------------------------------------------------------
def build_pdf(pages,title,lock,reminder,theme):
    out=Path("outputs/pdf"); out.mkdir(parents=True, exist_ok=True)
    pdf_path=out / f"storybook_{STYLE_TAG[2:-2]}_{uuid4().hex[:4]}.pdf"
    pdf=FPDF(unit="pt",format=PAGE_SIZE)

    pdf.add_page()
    with tempfile.NamedTemporaryFile(suffix=".png",delete=False) as tmp:
        cover(title,lock,theme).save(tmp.name,"PNG")
        pdf.image(tmp.name,0,0,w=PAGE_SIZE[0],h=PAGE_SIZE[1])
    os.unlink(tmp.name)

    prev=""
    for i,p in enumerate(pages,1):
        full=(f"{lock}. {STYLE}. {prev} {p['img_prompt']}. {reminder}. "
              f"{STYLE_TAG}. {NO_TEXT} --negative {NEG}")
        dump(f"page_{i}",full)
        img=overlay(prep(imagen(full)), p["text"])
        pdf.add_page()
        with tempfile.NamedTemporaryFile(suffix=".png",delete=False) as tmp:
            img.save(tmp.name,"PNG")
            pdf.image(tmp.name,0,0,w=PAGE_SIZE[0],h=PAGE_SIZE[1])
        os.unlink(tmp.name)
        prev=f"Previously: {p['prev_syn']}."

    pdf.output(pdf_path.as_posix())
    print("✅ PDF →", pdf_path.resolve())
    print("📝 Prompts →", log_file.resolve())

# — CLI -------------------------------------------------------------------
def main():
    theme=input("Theme: ").strip() or "Helping Others"
    chars=input("Characters (comma-sep): ").strip()
    moral=input("Moral: ").strip() or "Helping warms the heart."
    try: n=int(input("Pages (default 8): ").strip() or 8)
    except ValueError: n=8
    char_list=[c.strip() for c in chars.split(",") if c.strip()]

    lock,title,rem=plan(theme,char_list)
    pages=story(theme,n,moral,lock)
    build_pdf(pages,title,lock,rem,theme)

if __name__=="__main__":
    main()
