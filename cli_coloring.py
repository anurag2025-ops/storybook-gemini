#!/usr/bin/env python3
"""
cli_coloring_simple_bw.py
---------------------------------------------------------
Generates a black-and-white coloring book for young children (ages 3–7).

  • Each page contains one unique, bold-outlined shape.
  • Pages are black-and-white only, with no color, no text, and no shading.
  • Page numbers are added in the bottom-right corner.

Page format : US-Letter (8.5 × 11 inch → 612 × 792 pt)
Text model  : GPT-4o-mini   (subject generator)
Image model : Imagen-3 (imagen-3.0-generate-002) via Google GenAI
Output      : PDF → outputs/pdf/, prompt log → outputs/

Requires env vars:
  OPENAI_API_KEY   – for GPT-4o text
  GOOGLE_API_KEY   – for Imagen-3 images
---------------------------------------------------------
"""

import io, os, sys, time, base64, tempfile, random
from pathlib import Path
from dotenv import load_dotenv

import openai
import google.genai as genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFont
from fpdf import FPDF

# ─── Configuration ────────────────────────────────────────────────────────
PAGE_SIZE  = (612, 792)
RAW_SIZE   = (768, 1024)
TEXT_MODEL = "gpt-4o-mini"
IMG_MODEL  = "imagen-3.0-generate-002"
MAX_RETRY  = 2

STYLE_OUTLINE = (
    "Black and white line art only, super simple bold outline coloring-book page, "
    "no colors, no shading, no textures, no text, minimal detail, large shapes, thick lines, "
    "full page composition, designed for preschoolers aged 3 to 7"
)

FONT_NUM = ImageFont.load_default()
LOG_PROMPTS = []

log = lambda m: print(m, file=sys.stderr)

# ─── Initialize API Keys ──────────────────────────────────────────────────
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY") or sys.exit("❌  Set OPENAI_API_KEY")
google_key     = os.getenv("GOOGLE_API_KEY") or sys.exit("❌  Set GOOGLE_API_KEY")
gclient        = genai.Client(api_key=google_key)

# ─── Text Measurement ─────────────────────────────────────────────────────
def measure(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont):
    if hasattr(draw, "textbbox"):
        b = draw.textbbox((0, 0), text, font=font)
        return b[2]-b[0], b[3]-b[1]
    return draw.textsize(text, font=font)

# ─── GPT: Generate Unique Simple Prompt ───────────────────────────────────
def gpt_subject(theme: str, idx: int) -> str:
    variation = random.choice([
        "animal", "vehicle", "toy", "fruit", "tool", "building", "kitchen item",
        "cartoon-style object", "funny face", "pretend object", "clothing", "nature item"
    ])
    
    resp = openai.chat.completions.create(
        model=TEXT_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    f"You create unique, simple subjects for preschool coloring books. "
                    f"Subjects should be cute, bold-outline-friendly, and suitable for ages 3–7. "
                    f"Avoid repeating subjects. For each new page, give a different idea. "
                    f"This is page {idx} of a themed coloring book."
                )
            },
            {
                "role": "user",
                "content": (
                    f"Give ONE description of a cute and simple {variation} for a children’s coloring book "
                    f"with the theme “{theme}”. Keep it very simple. Example: 'A smiling hot air balloon with a tiny flag'. "
                    f"No text, no background, just one centered object."
                )
            },
        ],
    )
    return resp.choices[0].message.content.strip().split("\n")[0][:120]

# ─── Imagen Generator ─────────────────────────────────────────────────────
def imagen(prompt: str, aspect="3:4"):
    cfg = types.GenerateImagesConfig(number_of_images=1, aspect_ratio=aspect)
    for attempt in range(1, MAX_RETRY + 1):
        try:
            rsp = gclient.models.generate_images(
                model=IMG_MODEL, prompt=prompt[:800], config=cfg
            )
            if rsp.generated_images and rsp.generated_images[0].image.image_bytes:
                return Image.open(io.BytesIO(rsp.generated_images[0].image.image_bytes)).convert("RGB")
            log(f"⚠️  Imagen failed (attempt {attempt}/{MAX_RETRY})")
        except Exception as e:
            log(f"⚠️  Imagen error (attempt {attempt}/{MAX_RETRY}): {e}")
            time.sleep(1)
    return Image.new("RGB", RAW_SIZE, (230, 230, 230))

# ─── Add Page Number ──────────────────────────────────────────────────────
def add_pageno(img: Image.Image, n: int) -> None:
    d = ImageDraw.Draw(img)
    txt = str(n)
    tw, th = measure(d, txt, FONT_NUM)
    d.text((img.width - tw - 10, img.height - th - 8), txt, font=FONT_NUM, fill=(40, 40, 40))

# ─── Build PDF ────────────────────────────────────────────────────────────
def build_pdf(theme: str, pages: int):
    pdf_dir = Path("outputs/pdf"); pdf_dir.mkdir(parents=True, exist_ok=True)
    txt_dir = Path("outputs");     txt_dir.mkdir(exist_ok=True)
    safe = "".join(c if c.isalnum() else "_" for c in theme)[:40] or "bwbook"
    pdf_path = pdf_dir / f"bw_coloring_{safe}.pdf"
    txt_path = txt_dir / f"bw_coloring_{safe}_log.txt"

    pdf = FPDF(unit="pt", format=PAGE_SIZE)

    for page_no in range(1, pages + 1):
        desc = gpt_subject(theme, page_no); log(f"🖼️  {desc}")
        prompt = f"{STYLE_OUTLINE}. {desc}. Centered, full-page."
        LOG_PROMPTS.append(prompt)

        img = imagen(prompt).resize(PAGE_SIZE, Image.LANCZOS)
        add_pageno(img, page_no)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
            img.save(tmp.name, "PNG")
            pdf.add_page()
            pdf.image(tmp.name, x=0, y=0, w=PAGE_SIZE[0], h=PAGE_SIZE[1])

    pdf.output(pdf_path.as_posix())
    print(f"✅  PDF → {pdf_path.resolve()}")

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"Theme: {theme}\n\nPrompts:\n")
        for p in LOG_PROMPTS:
            f.write(f"- {p}\n")
    print(f"📝  Log → {txt_path.resolve()}")

# ─── CLI ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    theme = input("Coloring-book theme: ").strip() or "Everyday fun things"
    try:
        pages = int(input("How many pages? (default 6): ").strip() or 6)
    except ValueError:
        pages = 6
    build_pdf(theme, pages)
