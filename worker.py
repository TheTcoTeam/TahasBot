# -*- coding: utf-8 -*-
"""
GitHub Actions Worker for TahasBot (Bale Messenger)
Runs on GitHub Actions 7GB RAM runner to download heavy YouTube videos & audios
and upload them directly to Bale Messenger.
"""

import os
import sys
import time
import glob
import json
import subprocess
import traceback
from pathlib import Path
import urllib3
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# HTTP Session with connection pool & retries
_SESSION = requests.Session()
_retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
_adapter = HTTPAdapter(pool_connections=5, pool_maxsize=10, max_retries=_retries)
_SESSION.mount("https://", _adapter)
_SESSION.mount("http://", _adapter)

BALE_API = "https://tapi.bale.ai"
BOT_TOKEN = os.environ.get("BALE_BOT_TOKEN", "").strip()
URL = os.environ.get("YT_URL", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()
MESSAGE_ID = os.environ.get("MESSAGE_ID", "0").strip()
FORMAT = os.environ.get("YT_FORMAT", "mp4").strip().lower()
QUALITY = os.environ.get("YT_QUALITY", "720p").strip().lower()

WORK_DIR = "downloads"
os.makedirs(WORK_DIR, exist_ok=True)


def update_progress(text: str):
    print(f"[WORKER STATE] {text}", flush=True)
    if not (BOT_TOKEN and CHAT_ID and MESSAGE_ID and MESSAGE_ID != "0"):
        return
    try:
        url = f"{BALE_API}/bot{BOT_TOKEN}/editMessageText"
        _SESSION.post(
            url,
            json={"chat_id": CHAT_ID, "message_id": int(MESSAGE_ID), "text": text},
            timeout=15,
            verify=False
        )
    except Exception as e:
        print(f"[PROGRESS ERROR] {e}", flush=True)


def send_message(text: str):
    if not (BOT_TOKEN and CHAT_ID):
        return
    try:
        url = f"{BALE_API}/bot{BOT_TOKEN}/sendMessage"
        _SESSION.post(
            url,
            json={"chat_id": CHAT_ID, "text": text},
            timeout=15,
            verify=False
        )
    except Exception as e:
        print(f"[SEND ERROR] {e}", flush=True)


def delete_progress():
    if not (BOT_TOKEN and CHAT_ID and MESSAGE_ID and MESSAGE_ID != "0"):
        return
    try:
        url = f"{BALE_API}/bot{BOT_TOKEN}/deleteMessage"
        _SESSION.post(
            url,
            json={"chat_id": CHAT_ID, "message_id": int(MESSAGE_ID)},
            timeout=10,
            verify=False
        )
    except Exception:
        pass


def probe_media_file(file_path: str) -> dict:
    try:
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", file_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        data = json.loads(res.stdout or "{}")
        fmt = data.get("format", {})
        duration = int(float(fmt.get("duration", 0)))
        width, height = 1280, 720
        for s in data.get("streams", []):
            if s.get("codec_type") == "video":
                width = int(s.get("width") or 1280)
                height = int(s.get("height") or 720)
                break
        return {"duration": duration, "width": width, "height": height}
    except Exception as e:
        print(f"[PROBE ERROR] {e}", flush=True)
        return {"duration": 0, "width": 1280, "height": 720}


def compress_video_if_needed(video_path: str, max_mb: float = 48.0) -> str:
    size_mb = os.path.getsize(video_path) / (1024 * 1024)
    if size_mb <= max_mb:
        return video_path

    print(f"File size {size_mb:.2f}MB exceeds {max_mb}MB limit. Compressing...", flush=True)
    update_progress(f"🗜 حجم ویدیو ({size_mb:.1f} مگابایت) بیش از حد مجاز بله است. در حال بهینه‌سازی...")

    out_compressed = os.path.join(WORK_DIR, f"comp_{Path(video_path).name}")
    # Two-pass or CRF 28 encoding to safely hit under limit
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", "scale=-2:480",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-c:a", "aac", "-b:a", "128k",
        out_compressed
    ]
    res = subprocess.run(cmd, capture_output=True, timeout=180)
    if res.returncode == 0 and os.path.exists(out_compressed):
        return out_compressed
    return video_path


def upload_video_to_bale(video_path: str, caption: str, info: dict):
    update_progress("⬆️ در حال ارسال ویدیو به بله...")
    url = f"{BALE_API}/bot{BOT_TOKEN}/sendVideo"
    probe = probe_media_file(video_path)

    with open(video_path, "rb") as vf:
        files = {"video": (Path(video_path).name, vf, "video/mp4")}
        data = {
            "chat_id": CHAT_ID,
            "caption": caption[:1024],
            "duration": probe["duration"],
            "width": probe["width"],
            "height": probe["height"]
        }
        res = _SESSION.post(url, data=data, files=files, timeout=300, verify=False)
        print("Bale sendVideo response:", res.status_code, res.text[:200], flush=True)
        if res.status_code != 200:
            raise RuntimeError(f"Bale sendVideo failed: {res.status_code} {res.text}")


def upload_audio_to_bale(audio_path: str, caption: str, title: str, artist: str, duration: int):
    update_progress("⬆️ در حال ارسال موزیک به بله...")
    url = f"{BALE_API}/bot{BOT_TOKEN}/sendAudio"

    with open(audio_path, "rb") as af:
        files = {"audio": (Path(audio_path).name, af, "audio/mpeg")}
        data = {
            "chat_id": CHAT_ID,
            "caption": caption[:1024],
            "title": title[:100],
            "performer": artist[:100],
            "duration": duration
        }
        res = _SESSION.post(url, data=data, files=files, timeout=300, verify=False)
        print("Bale sendAudio response:", res.status_code, res.text[:200], flush=True)
        if res.status_code != 200:
            raise RuntimeError(f"Bale sendAudio failed: {res.status_code} {res.text}")


def main():
    if not (URL and BOT_TOKEN and CHAT_ID):
        print("Missing required parameters (YT_URL, BALE_BOT_TOKEN, or CHAT_ID)", flush=True)
        sys.exit(1)

    print(f"Starting worker for URL: {URL} | Format: {FORMAT} | Quality: {QUALITY}", flush=True)
    update_progress("🚀 سرور اختصاصی شروع به کار کرد!\n⏳ در حال دریافت اطلاعات ویدیو...")

    ts = int(time.time())
    out_template = os.path.join(WORK_DIR, f"yt_{ts}_%(id)s.%(ext)s")

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist",
        "--no-check-certificates",
        "-N", "4",
        "--buffer-size", "1024K",
        "-o", out_template,
        "--extractor-args", "youtube:player_client=android,web"
    ]

    if os.path.exists("cookies.txt") and os.path.getsize("cookies.txt") > 0:
        cmd.extend(["--cookies", "cookies.txt"])

    if FORMAT == "mp3" or "audio" in QUALITY:
        # Download best audio and convert to 320k MP3
        update_progress("📥 در حال دانلود ترک صوتی با بالاترین کیفیت...")
        cmd.extend([
            "-x", "--audio-format", "mp3",
            "--audio-quality", "0",
            "--write-thumbnail",
            "--embed-thumbnail"
        ])
    else:
        # Download video matching quality
        max_h = 720
        if "1080" in QUALITY:
            max_h = 1080
        elif "480" in QUALITY:
            max_h = 480
        elif "360" in QUALITY:
            max_h = 360

        update_progress(f"📥 در حال دانلود ویدیو با کیفیت {max_h}p...")
        cmd.extend([
            "-f", f"bestvideo[height<={max_h}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={max_h}]+bestaudio/best[height<={max_h}]/best",
            "--merge-output-format", "mp4"
        ])

    cmd.append(URL)
    print("Executing command:", " ".join(cmd), flush=True)
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    print("yt-dlp stdout:", res.stdout[-400:], flush=True)

    if res.returncode != 0:
        print("yt-dlp error:", res.stderr[-500:], flush=True)
        update_progress(f"❌ خطا در دانلود ویدیو:\n{res.stderr[-200:]}")
        sys.exit(1)

    # Find the output file
    downloaded_files = glob.glob(os.path.join(WORK_DIR, f"yt_{ts}_*.*"))
    # Filter out thumbnails or temp files
    media_files = [f for f in downloaded_files if f.lower().endswith((".mp4", ".mp3", ".m4a", ".webm", ".mkv"))]

    if not media_files:
        update_progress("❌ فایلی برای ارسال یافت نشد.")
        sys.exit(1)

    target_file = media_files[0]
    print(f"Target downloaded file: {target_file} (Size: {os.path.getsize(target_file)} bytes)", flush=True)

    # Extract title from filename or yt-dlp output
    stem_title = Path(target_file).stem
    caption = f"🎬 **{stem_title}**\n\n⚡️ دانلود شده توسط @tahasbot"

    if target_file.lower().endswith(".mp3"):
        probe = probe_media_file(target_file)
        upload_audio_to_bale(
            audio_path=target_file,
            caption=f"🎵 **{stem_title}**\n⚡️ دانلود شده توسط @tahasbot",
            title=stem_title,
            artist="YouTube Audio",
            duration=probe["duration"]
        )
    else:
        # Check size and compress if necessary
        final_video = compress_video_if_needed(target_file, max_mb=48.0)
        upload_video_to_bale(final_video, caption, {})

    delete_progress()
    print("Worker task completed successfully!", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        traceback.print_exc()
        update_progress(f"❌ خطای پیش‌بینی نشده:\n{str(e)[:250]}")
        sys.exit(1)
