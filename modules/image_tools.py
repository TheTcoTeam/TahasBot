# -*- coding: utf-8 -*-
"""
Section 3: Image Tools Engine for TMulti Bot.
Handles image operations via Pillow (PIL):
- Smart Compression (reduce file size by 50-80%)
- Format Conversion (WebP, PNG, JPG, PDF, ICO)
- Crop & Resize (1:1 Avatar, 16:9 Landscape, 9:16 Story)
- Background Removal (Alpha cutout)
- Aesthetic Filters (Sharpen, HDR Boost, B&W, Vintage Sepia)
- Watermark & Text Overlay
"""

import os
import time
from pathlib import Path
from PIL import Image, ImageOps, ImageEnhance, ImageFilter, ImageDraw, ImageFont

IMAGE_TOOLS_DIR = "media_downloads"
os.makedirs(IMAGE_TOOLS_DIR, exist_ok=True)


def smart_compress_image(input_path: str, quality: int = 70) -> tuple:
    """Compress image and return (output_path, original_size, new_size, percent_saved)."""
    orig_size = os.path.getsize(input_path)
    out_path = os.path.join(IMAGE_TOOLS_DIR, f"comp_{int(time.time())}_{Path(input_path).name}")

    with Image.open(input_path) as img:
        # Preserve orientation
        img = ImageOps.exif_transpose(img)
        fmt = img.format or "JPEG"
        if img.mode in ("RGBA", "LA", "P") and fmt.upper() in ("JPEG", "JPG"):
            # Fill transparency with white for JPEG
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
            bg.save(out_path, format="JPEG", quality=quality, optimize=True)
        elif img.mode == "RGBA":
            img.save(out_path, format="PNG", optimize=True)
        else:
            img.save(out_path, format=fmt, quality=quality, optimize=True)

    new_size = os.path.getsize(out_path)
    saved_pct = max(0, round((1.0 - (new_size / max(1, orig_size))) * 100, 1))
    return out_path, orig_size, new_size, saved_pct


def convert_image_format(input_path: str, target_format: str) -> str:
    """Convert image between WEBP, PNG, JPEG, PDF, ICO."""
    target_format = target_format.upper().replace(".", "")
    if target_format == "JPG":
        target_format = "JPEG"

    ext_map = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "PDF": ".pdf", "ICO": ".ico"}
    out_ext = ext_map.get(target_format, f".{target_format.lower()}")
    out_path = os.path.join(IMAGE_TOOLS_DIR, f"conv_{int(time.time())}{out_ext}")

    with Image.open(input_path) as img:
        img = ImageOps.exif_transpose(img)
        if target_format in ("JPEG", "PDF") and img.mode in ("RGBA", "LA", "P"):
            rgb = Image.new("RGB", img.size, (255, 255, 255))
            rgb.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
            rgb.save(out_path, format=target_format, quality=95)
        elif target_format == "ICO":
            img.resize((256, 256)).save(out_path, format="ICO")
        else:
            img.save(out_path, format=target_format, quality=95)

    return out_path


def crop_resize_preset(input_path: str, preset: str) -> str:
    """Crop image to standard aspect ratios: 1:1, 16:9, 9:16."""
    out_path = os.path.join(IMAGE_TOOLS_DIR, f"crop_{preset}_{int(time.time())}.jpg")

    with Image.open(input_path) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        w, h = img.size

        if preset == "1:1":
            # Square crop centered
            side = min(w, h)
            left = (w - side) // 2
            top = (h - side) // 2
            cropped = img.crop((left, top, left + side, top + side))
        elif preset == "16:9":
            # Landscape
            target_h = int(w * 9 / 16)
            if target_h <= h:
                top = (h - target_h) // 2
                cropped = img.crop((0, top, w, top + target_h))
            else:
                target_w = int(h * 16 / 9)
                left = (w - target_w) // 2
                cropped = img.crop((left, 0, left + target_w, h))
        elif preset == "9:16":
            # Portrait (Story / Reel)
            target_w = int(h * 9 / 16)
            if target_w <= w:
                left = (w - target_w) // 2
                cropped = img.crop((left, 0, left + target_w, h))
            else:
                target_h = int(w * 16 / 9)
                top = (h - target_h) // 2
                cropped = img.crop((0, top, w, top + target_h))
        else:
            cropped = img

        cropped.save(out_path, format="JPEG", quality=95)

    return out_path


def apply_image_filter(input_path: str, filter_type: str) -> str:
    """Apply aesthetic filter (sharpen, hdr, bw, vintage)."""
    out_path = os.path.join(IMAGE_TOOLS_DIR, f"fltr_{filter_type}_{int(time.time())}.jpg")

    with Image.open(input_path) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")

        if filter_type == "sharpen":
            enhanced = img.filter(ImageFilter.SHARPEN)
            enhanced = ImageEnhance.Sharpness(enhanced).enhance(2.0)
        elif filter_type == "hdr":
            contrast = ImageEnhance.Contrast(img).enhance(1.3)
            color = ImageEnhance.Color(contrast).enhance(1.4)
            enhanced = ImageEnhance.Sharpness(color).enhance(1.5)
        elif filter_type == "bw":
            gray = ImageOps.grayscale(img)
            enhanced = ImageEnhance.Contrast(gray).enhance(1.2).convert("RGB")
        elif filter_type == "vintage":
            # Sepia / warm aesthetic
            sepia = ImageOps.colorize(ImageOps.grayscale(img), "#2e1a05", "#ffebd4")
            enhanced = ImageEnhance.Contrast(sepia).enhance(1.1)
        else:
            enhanced = img

        enhanced.save(out_path, format="JPEG", quality=95)

    return out_path


def remove_image_background(input_path: str) -> str:
    """Remove background and create transparent PNG."""
    out_path = os.path.join(IMAGE_TOOLS_DIR, f"nobg_{int(time.time())}.png")

    try:
        import rembg
        with open(input_path, "rb") as inp_f:
            input_bytes = inp_f.read()
        output_bytes = rembg.remove(input_bytes)
        with open(out_path, "wb") as out_f:
            out_f.write(output_bytes)
        return out_path
    except Exception:
        # Fallback to high-contrast corner/edge alpha cutout
        with Image.open(input_path) as img:
            img = ImageOps.exif_transpose(img).convert("RGBA")
            datas = img.getdata()
            # Sample corner pixel color
            bg_color = datas[0]
            new_data = []
            tolerance = 38
            for item in datas:
                diff = abs(item[0] - bg_color[0]) + abs(item[1] - bg_color[1]) + abs(item[2] - bg_color[2])
                if diff < tolerance:
                    new_data.append((255, 255, 255, 0))
                else:
                    new_data.append(item)
            img.putdata(new_data)
            img.save(out_path, "PNG")
        return out_path


def add_text_watermark(input_path: str, text: str) -> str:
    """Add a semi-transparent text watermark to the image."""
    out_path = os.path.join(IMAGE_TOOLS_DIR, f"wm_{int(time.time())}.jpg")

    with Image.open(input_path) as img:
        img = ImageOps.exif_transpose(img).convert("RGBA")
        txt_layer = Image.new("RGBA", img.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(txt_layer)

        w, h = img.size
        font_size = max(18, int(h / 25))
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()

        # Position at bottom-right
        text_w = font_size * len(text) * 0.6
        pos = (max(20, int(w - text_w - 40)), max(20, int(h - font_size * 2)))

        # Draw shadow + text
        draw.text((pos[0] + 2, pos[1] + 2), text, fill=(0, 0, 0, 160), font=font)
        draw.text(pos, text, fill=(255, 255, 255, 220), font=font)

        watermarked = Image.alpha_composite(img, txt_layer).convert("RGB")
        watermarked.save(out_path, "JPEG", quality=95)

    return out_path
