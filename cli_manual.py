#!/usr/bin/env python3
"""
cli_manual.py · v39.6
Manual page spec → Imagen → PDF (character-lock consistency)

Changes vs v39.4:
1. parse_spec() robust to line-break after “A4 Image Prompt:”
2. build_pdf() extracts a character lock from the Cover-Page image prompt
   (text up to first period) and prepends it to every Imagen prompt.
3. Progress logs to stdout unchanged.

Usage
─────
▸ interactive paste:
      python cli_manual.py
      (paste) … END ↵
▸ from file:
      python cli_manual.py -f story.txt
▸ pipe:
      cat story.txt | python cli_manual.py
"""

# ── stdlib
import argparse, io, os, re, sys, tempfile, textwrap, unicodedata, time, random
from pathlib import Path
from uuid import uuid4
from datetime import datetime

# ── third-party
import google.genai as genai
from google.genai import types
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from fpdf import FPDF

# ── constants ------------------------------------------------------------
PAGE_SIZE, RAW_SIZE = (595, 842), (768, 1024)
IMG_MODEL           = "imagen-3.0-generate-002"
GUIDANCE_SCALE      = 9.0
MAX_RETRY           = 2
STYLE_TAG           = "##" + uuid4().hex[:8].upper() + "##"

NO_TEXT = "No text, no letters, no words, no subtitles, no watermark."
NEG     = ("extra limbs, mutated anatomy, wrong outfit, outfit change, watermark, blurry, ugly, "
           "any change of colours, clothes, props")

safe = lambda s: unicodedata.normalize("NFKD", s).encode("latin-1", "ignore").decode()
log  = lambda m: print(m, flush=True)

# ── keys -----------------------------------------------------------------
load_dotenv()
gkey = os.getenv("GOOGLE_API_KEY") or sys.exit("❌  GOOGLE_API_KEY missing")
gen_client = genai.Client(api_key=gkey)

# ── prompt log -----------------------------------------------------------
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
log_dir = Path("outputs/generated_prompts"); log_dir.mkdir(parents=True, exist_ok=True)
log_file = log_dir / f"manual_prompts_{ts}.txt"
def dump(tag: str, txt: str):
    with log_file.open("a", encoding="utf-8") as f:
        f.write(f"--- {tag} ---\n{txt}\n\n")

# ── font util ------------------------------------------------------------
def font_default(sz):
    for p in ("DejaVuSans.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "Arial.ttf"):
        try:
            return ImageFont.truetype(p, sz)
        except Exception:
            pass
    return ImageFont.load_default()
FONT_BODY  = font_default(20)
FONT_TITLE = font_default(48)
def txt_wh(d, t, f):
    b = d.textbbox((0, 0), t, font=f)
    return b[2] - b[0], b[3] - b[1]

# ── Imagen wrapper -------------------------------------------------------
def imagen(prompt: str):
    cfg = types.GenerateImagesConfig(number_of_images=1,
                                     aspect_ratio="3:4",
                                     guidance_scale=GUIDANCE_SCALE)
    last = None
    for i in range(MAX_RETRY + 1):
        try:
            r = gen_client.models.generate_images(model=IMG_MODEL,
                                                  prompt=prompt,
                                                  config=cfg)
            if r.generated_images and r.generated_images[0].image.image_bytes:
                return Image.open(io.BytesIO(r.generated_images[0].image.image_bytes))
            last = RuntimeError("Empty image bytes")
        except Exception as e:
            last = e
        log(f"Imagen error (try {i+1}/{MAX_RETRY+1}): {last}")
        if i < MAX_RETRY:
            time.sleep(1 + i)
    return Image.new("RGB", RAW_SIZE, (220, 220, 220))

def prep(im):
    return im.convert("RGB").resize(RAW_SIZE, Image.LANCZOS).resize(PAGE_SIZE, Image.LANCZOS)

# ── adaptive overlay -----------------------------------------------------
def overlay(img, caption: str, top_banner=False):
    img = img.convert("RGBA")
    W, H = img.size
    d = ImageDraw.Draw(img)
    avg = FONT_BODY.getlength("M") if hasattr(FONT_BODY, "getlength") else FONT_BODY.size * 0.6
    wrap = textwrap.fill(safe(caption), max(1, int((W - 72) / avg)))
    bw, bh = txt_wh(d, wrap, FONT_BODY)

    pad = 20
    left = max(36, (W - bw) // 2 - pad)
    right = min(W - 36, left + bw + 2 * pad)
    top = 36 if top_banner else random.choice([20, H - bh - 2 * pad - 20])
    bottom = top + bh + 2 * pad

    cloud = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(cloud).rounded_rectangle((left, top, right, bottom), 26,
                                            fill=(255, 255, 255, 235))
    img.alpha_composite(cloud.filter(ImageFilter.GaussianBlur(12)))

    d.multiline_text((left + pad, top + pad), wrap,
                     font=FONT_BODY, fill=(20, 20, 120), spacing=4)
    return img.convert("RGB")

# ── parse spec -----------------------------------------------------------
PAGE_HDR = re.compile(r'^(Cover Page|End Page|Page\s+\d+)\s+–\s+(.*)$', re.I)

def parse_spec(text: str):
    pages = []
    cur = None
    mode = None  # None / img / cap
    for ln in text.splitlines():
        if m := PAGE_HDR.match(ln.strip()):
            if cur:
                pages.append(cur)
            cur = {"hdr": m.group(1).strip().lower(),
                   "title": m.group(2).strip(),
                   "img": "", "cap": ""}
            mode = None
            continue

        if "A4 Image Prompt" in ln:
            cur["img"] = ln.split("A4 Image Prompt")[-1].split(":", 1)[-1].strip()
            mode = "img"
            continue

        if "Embedded Text" in ln or re.search(r'\bText\b', ln):
            cur["cap"] = ln.split(":", 1)[1].strip()
            mode = "cap"
            continue

        if cur and mode == "img":
            cur["img"] += " " + ln.strip()
        elif cur and mode == "cap":
            cur["cap"] += " " + ln.strip()

    if cur:
        pages.append(cur)
    return pages

# ── cover maker ----------------------------------------------------------
def make_cover(img_prompt: str, caption: str):
    title, *rest = caption.split("\n")
    subtitle = " ".join(rest).strip()
    prompt = f"{img_prompt}. {STYLE_TAG}. A4 portrait illustration. {NO_TEXT} --negative {NEG}"
    dump("cover_prompt", prompt)

    img = prep(imagen(prompt)).convert("RGBA")
    W, H = img.size
    d = ImageDraw.Draw(img)

    cloud = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(cloud).rectangle((0, 0, W, 240), fill=(255, 255, 255, 230))
    img.alpha_composite(cloud.filter(ImageFilter.GaussianBlur(8)))

    wrap = textwrap.fill(safe(title), width=18)
    y = 40
    for line in wrap.split("\n"):
        lw, lh = txt_wh(d, line, FONT_TITLE)
        d.text(((W - lw) // 2, y), line, font=FONT_TITLE, fill=(20, 20, 120))
        y += lh + 8
    if subtitle:
        y += 10
        sub_wrap = textwrap.fill(safe(subtitle), width=30)
        d.multiline_text((W // 2, y), sub_wrap,
                         font=FONT_BODY, fill=(20, 20, 120),
                         spacing=4, anchor="mm", align="center")
    return img.convert("RGB")

# ── PDF builder ----------------------------------------------------------
def build_pdf(pages):
    if not pages:
        log("No pages parsed."); return

    # Derive a lock from the Cover Page image prompt (text before the first period)
    lock = pages[0]["img"].split(".", 1)[0].strip()

    pdf_dir = Path("outputs/pdf"); pdf_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = pdf_dir / f"storybook_manual_{STYLE_TAG[2:-2]}_{uuid4().hex[:4]}.pdf"
    pdf = FPDF(unit="pt", format=PAGE_SIZE)

    for i, p in enumerate(pages, 1):
        log(f"🖼️  Rendering {p['hdr']} ({i}/{len(pages)}) …")

        if p["hdr"].startswith("cover"):
            img = make_cover(p["img"], p["cap"])
        else:
            prompt = (f"{lock}. {p['img']}. {STYLE_TAG}. "
                      f"A4 portrait illustration. {NO_TEXT} --negative {NEG}")
            dump(f"page_{i}", prompt)
            img = overlay(prep(imagen(prompt)), p["cap"],
                          top_banner=p["hdr"].startswith("end"))

        pdf.add_page()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp.name, "PNG")
            pdf.image(tmp.name, 0, 0, w=PAGE_SIZE[0], h=PAGE_SIZE[1])
        os.unlink(tmp.name)

    pdf.output(pdf_path.as_posix())
    log(f"✅ PDF saved → {pdf_path.resolve()}")
    log(f"📝 Prompt log → {log_file.resolve()}")

# ── main -----------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Generate PDF storybook from manual spec")
    ap.add_argument("-f", "--file", help="Text file with the specification")
    args = ap.parse_args()

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    else:
        if sys.stdin.isatty():
            log("Paste specification, finish with END on its own line:")
        lines = []
        for ln in sys.stdin:
            if ln.strip().upper() == "END":
                break
            lines.append(ln)
        text = "".join(lines)

    # Normalize exotic Unicode separators & NBSP
    text = (text.replace("\u2028", "\n")
                .replace("\u2029", "\n")
                .replace("\u00A0", " "))

    if not text.strip():
        log("No input provided."); return

    pages = parse_spec(text)
    if not pages:
        log("Could not parse specification format."); return

    build_pdf(pages)

if __name__ == "__main__":
    main()
