# -*- coding: utf-8 -*-
"""
Video Tools Engine for TahasBot on Bale Messenger.
FFmpeg-powered operations:
- Trim / Cut
- Watermark (text overlay)
- Aspect Crop (1:1 Square, 9:16 Story, 16:9 Wide)
- Rotate (90 CW, 90 CCW, 180)
- Flip (Horizontal, Vertical)
- Speed (0.5x, 1.5x, 2.0x)
- Mute (remove audio)
- Extract Audio (MP3 192k)
- Smart Compression
- Reverse Video
- Convert to GIF
"""

import os
import re
import time
import shutil
import asyncio
import subprocess
import traceback
from pathlib import Path
from balethon.objects import InlineKeyboard, InlineKeyboardButton

VIDEO_EDIT_DIR = "jobs"
os.makedirs(VIDEO_EDIT_DIR, exist_ok=True)

FFMPEG_BIN = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE_BIN = shutil.which("ffprobe") or "ffprobe"

STD_VENC = [
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
    "-pix_fmt", "yuv420p", "-movflags", "+faststart",
    "-c:a", "aac", "-b:a", "128k"
]

VIDEO_EDIT_LABELS = {
    "trim": "✂️ برش ویدیو",
    "mute": "🔇 بی‌صدا کردن",
    "extract_audio": "🎵 استخراج صدا (MP3)",
    "rotate_cw": "🔄 چرخش ۹۰ درجه",
    "rotate_ccw": "🔄 چرخش ۹۰- درجه",
    "rotate_180": "🔄 چرخش ۱۸۰ درجه",
    "flip_h": "↔️ آینه افقی",
    "flip_v": "↕️ آینه عمودی",
    "crop_square": "🔲 برش ۱:۱ مربع",
    "crop_9x16": "📱 برش ۹:۱۶ استوری",
    "crop_16x9": "🖥 برش ۱۶:۹ افقی",
    "speed_05": "🐌 سرعت ۰.۵ برابر",
    "speed_15": "⚡ سرعت ۱.۵ برابر",
    "speed_2": "🚀 سرعت ۲ برابر",
    "compress": "🗜 فشرده‌سازی هوشمند",
    "reverse": "⏪ معکوس کردن ویدیو",
    "gif": "🎞 تبدیل به گیف (GIF)",
    "watermark": "🏷 درج واترمارک",
    "identify": "🎙 شناسایی موزیک داخل ویدیو"
}


def video_editor_kb() -> InlineKeyboard:
    """Inline keyboard for video tools in Bale."""
    return InlineKeyboard([
        [
            InlineKeyboardButton("✂️ برش ویدیو", "vedit_trim"),
            InlineKeyboardButton("🏷 درج واترمارک", "vedit_watermark")
        ],
        [
            InlineKeyboardButton("🔲 ۱:۱ مربع", "vedit_crop_square"),
            InlineKeyboardButton("📱 ۹:۱۶ استوری", "vedit_crop_9x16"),
            InlineKeyboardButton("🖥 ۱۶:۹ افقی", "vedit_crop_16x9")
        ],
        [
            InlineKeyboardButton("🔄 چرخش ۹۰°", "vedit_rotate_cw"),
            InlineKeyboardButton("🔄 چرخش ۲۷۰°", "vedit_rotate_ccw"),
            InlineKeyboardButton("🔄 چرخش ۱۸۰°", "vedit_rotate_180")
        ],
        [
            InlineKeyboardButton("↔️ آینه افقی", "vedit_flip_h"),
            InlineKeyboardButton("↕️ آینه عمودی", "vedit_flip_v")
        ],
        [
            InlineKeyboardButton("🐌 سرعت ۰.۵x", "vedit_speed_05"),
            InlineKeyboardButton("⚡ سرعت ۱.۵x", "vedit_speed_15"),
            InlineKeyboardButton("🚀 سرعت ۲.۰x", "vedit_speed_2")
        ],
        [
            InlineKeyboardButton("🔇 بی‌صدا کردن", "vedit_mute"),
            InlineKeyboardButton("🎵 استخراج صدا (MP3)", "vedit_extract_audio")
        ],
        [
            InlineKeyboardButton("🗜 فشرده‌سازی", "vedit_compress"),
            InlineKeyboardButton("⏪ معکوس کردن", "vedit_reverse")
        ],
        [
            InlineKeyboardButton("🎞 تبدیل به GIF", "vedit_gif"),
            InlineKeyboardButton("🎙 شناسایی موزیک", "vedit_identify")
        ],
        [
            InlineKeyboardButton("❌ پایان و بستن ویرایشگر", "vedit_cancel")
        ]
    ])


def probe_duration_sync(path: str) -> float:
    """Read media duration in seconds via ffprobe."""
    try:
        out = subprocess.run(
            [
                FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", path
            ],
            capture_output=True, text=True, timeout=15
        )
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def _escape_drawtext(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace('"', "'")


def apply_video_edit_sync(args: list, timeout: int = 600):
    """Run an FFmpeg command synchronously."""
    subprocess.run(
        [FFMPEG_BIN, "-y", "-loglevel", "error", *args],
        check=True, capture_output=True, timeout=timeout
    )


async def execute_video_action(src_path: str, action: str, extra_data: dict = None) -> dict:
    """
    Applies the video action and returns dict:
    {"output_path": str, "send_as": "video"|"audio"|"photo"|"document", "label": str}
    """
    if not os.path.exists(src_path):
        raise FileNotFoundError("Video source file not found")

    extra_data = extra_data or {}
    ts = int(time.time() * 1000)
    out = os.path.join(VIDEO_EDIT_DIR, f"vedit_{ts}.mp4")
    send_as = "video"
    label = VIDEO_EDIT_LABELS.get(action, action)

    dur = await asyncio.to_thread(probe_duration_sync, src_path)

    if action == "trim":
        start = extra_data.get("trim_start", "00:00:00")
        end = extra_data.get("trim_end", "00:00:10")
        args = ["-ss", start, "-to", end, "-i", src_path, *STD_VENC, out]

    elif action == "mute":
        args = ["-i", src_path, "-c:v", "copy", "-an", out]

    elif action == "extract_audio":
        out = os.path.join(VIDEO_EDIT_DIR, f"vedit_{ts}.mp3")
        args = ["-i", src_path, "-vn", "-c:a", "libmp3lame", "-b:a", "192k", out]
        send_as = "audio"

    elif action == "rotate_cw":
        args = ["-i", src_path, "-vf", "transpose=1", *STD_VENC, out]

    elif action == "rotate_ccw":
        args = ["-i", src_path, "-vf", "transpose=2", *STD_VENC, out]

    elif action == "rotate_180":
        args = ["-i", src_path, "-vf", "transpose=1,transpose=1", *STD_VENC, out]

    elif action == "flip_h":
        args = ["-i", src_path, "-vf", "hflip", *STD_VENC, out]

    elif action == "flip_v":
        args = ["-i", src_path, "-vf", "vflip", *STD_VENC, out]

    elif action == "crop_square":
        args = ["-i", src_path, "-vf", "crop='min(iw,ih)':'min(iw,ih)'", *STD_VENC, out]

    elif action == "crop_9x16":
        args = ["-i", src_path, "-vf", "crop=ih*9/16:ih", *STD_VENC, out]

    elif action == "crop_16x9":
        args = ["-i", src_path, "-vf", "crop=iw:iw*9/16", *STD_VENC, out]

    elif action == "speed_05":
        args = ["-i", src_path, "-vf", "setpts=2.0*PTS", "-af", "atempo=0.5", *STD_VENC, out]

    elif action == "speed_15":
        args = ["-i", src_path, "-vf", "setpts=PTS/1.5", "-af", "atempo=1.5", *STD_VENC, out]

    elif action == "speed_2":
        args = ["-i", src_path, "-vf", "setpts=0.5*PTS", "-af", "atempo=2.0", *STD_VENC, out]

    elif action == "compress":
        args = [
            "-i", src_path, "-vf", "scale='min(1280,iw)':-2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-c:a", "aac", "-b:a", "96k", out
        ]

    elif action == "reverse":
        if dur > 180:
            raise ValueError("طول ویدیو برای معکوس‌کردن باید کمتر از ۳ دقیقه باشد.")
        args = ["-i", src_path, "-vf", "reverse", "-af", "areverse", *STD_VENC, out]

    elif action == "gif":
        out = os.path.join(VIDEO_EDIT_DIR, f"vedit_{ts}.gif")
        clip_len = min(dur, 8) if dur > 0 else 8
        args = ["-i", src_path, "-t", str(clip_len), "-vf", "fps=12,scale=480:-1:flags=lanczos", out]
        send_as = "document"

    elif action == "watermark":
        wm_text = extra_data.get("watermark_text", "TMultiBot")
        escaped = _escape_drawtext(wm_text)
        drawtext = (
            f"drawtext=text='{escaped}':fontcolor=white:fontsize=28:"
            "x=(w-text_w)-20:y=(h-text_h)-20:box=1:boxcolor=black@0.4:boxborderw=8"
        )
        args = ["-i", src_path, "-vf", drawtext, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "copy", out]

    else:
        raise ValueError(f"عملیات ویدیویی ناشناخته: {action}")

    await asyncio.to_thread(apply_video_edit_sync, args)

    if not os.path.exists(out) or os.path.getsize(out) == 0:
        raise RuntimeError("فایل خروجی ایجاد نشد یا خالی است.")

    return {
        "output_path": out,
        "send_as": send_as,
        "label": label
    }
