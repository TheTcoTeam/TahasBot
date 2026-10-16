# -*- coding: utf-8 -*-
"""
GitHub Actions Worker for TahasBot (Bale Messenger)
Runs on GitHub Actions 7GB RAM runner to download heavy YouTube videos & audios,
send description/comments/subtitles/thumbnail first, dynamically slice large files
according to admin limits, and upload them sequentially to Bale Messenger.
"""

import os
import re
import sys
import time
import math
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
SLICE_MB = float(os.environ.get("SLICE_MB", "25") or 25)
SEND_SUBTITLES = os.environ.get("SEND_SUBTITLES", "false").strip().lower() == "true"
SEND_THUMBNAIL = os.environ.get("SEND_THUMBNAIL", "false").strip().lower() == "true"
SEND_DESCRIPTION = os.environ.get("SEND_DESCRIPTION", "false").strip().lower() == "true"
SEND_COMMENTS = os.environ.get("SEND_COMMENTS", "false").strip().lower() == "true"

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
    if not (BOT_TOKEN and CHAT_ID) or CHAT_ID == "0":
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


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]', '', name).strip()
    return cleaned[:50] if cleaned else "Video"


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


def upload_photo_to_bale(photo_path: str, caption: str):
    if CHAT_ID == "0":
        return
    url = f"{BALE_API}/bot{BOT_TOKEN}/sendPhoto"
    with open(photo_path, "rb") as pf:
        files = {"photo": (Path(photo_path).name, pf, "image/jpeg")}
        data = {"chat_id": CHAT_ID, "caption": caption[:1024]}
        res = _SESSION.post(url, data=data, files=files, timeout=60, verify=False)
        print("Bale sendPhoto:", res.status_code, flush=True)


def upload_document_to_bale(doc_path: str, caption: str):
    if CHAT_ID == "0":
        return
    url = f"{BALE_API}/bot{BOT_TOKEN}/sendDocument"
    with open(doc_path, "rb") as df:
        files = {"document": (Path(doc_path).name, df, "application/octet-stream")}
        data = {"chat_id": CHAT_ID, "caption": caption[:1024]}
        res = _SESSION.post(url, data=data, files=files, timeout=60, verify=False)
        print("Bale sendDocument:", res.status_code, flush=True)


def upload_video_to_bale(video_path: str, caption: str, probe: dict = None):
    if CHAT_ID == "0":
        return
    url = f"{BALE_API}/bot{BOT_TOKEN}/sendVideo"
    if not probe:
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
            print("sendVideo failed, attempting sendDocument fallback...", flush=True)
            vf.seek(0)
            url_doc = f"{BALE_API}/bot{BOT_TOKEN}/sendDocument"
            files_doc = {"document": (Path(video_path).name, vf, "video/mp4")}
            res_doc = _SESSION.post(url_doc, data={"chat_id": CHAT_ID, "caption": caption[:1024]}, files=files_doc, timeout=300, verify=False)
            print("Bale fallback sendDocument:", res_doc.status_code, flush=True)


def upload_audio_to_bale(audio_path: str, caption: str, title: str, duration: int):
    if CHAT_ID == "0":
        return
    url = f"{BALE_API}/bot{BOT_TOKEN}/sendAudio"

    with open(audio_path, "rb") as af:
        files = {"audio": (Path(audio_path).name, af, "audio/mpeg")}
        data = {
            "chat_id": CHAT_ID,
            "caption": caption[:1024],
            "title": title[:100],
            "duration": duration
        }
        res = _SESSION.post(url, data=data, files=files, timeout=300, verify=False)
        print("Bale sendAudio response:", res.status_code, res.text[:200], flush=True)
        if res.status_code != 200:
            print("sendAudio failed, attempting sendDocument fallback...", flush=True)
            af.seek(0)
            url_doc = f"{BALE_API}/bot{BOT_TOKEN}/sendDocument"
            files_doc = {"document": (Path(audio_path).name, af, "audio/mpeg")}
            res_doc = _SESSION.post(url_doc, data={"chat_id": CHAT_ID, "caption": caption[:1024]}, files=files_doc, timeout=300, verify=False)
            print("Bale fallback sendDocument:", res_doc.status_code, flush=True)


def slice_video(video_path: str, slice_mb: float, title: str, probe: dict = None) -> list:
    """Slice a large video into sequential parts under slice_mb limit using FFmpeg."""
    if not probe:
        probe = probe_media_file(video_path)
    total_duration = probe.get("duration", 0)
    file_size_mb = os.path.getsize(video_path) / (1024 * 1024)

    target_part_mb = slice_mb * 0.88
    num_parts = max(2, math.ceil(file_size_mb / target_part_mb))
    part_duration = total_duration / num_parts if total_duration > 0 else 300

    clean_t = sanitize_filename(title)
    parts = []

    print(f"Slicing video ({file_size_mb:.1f}MB) into {num_parts} parts (target limit: {slice_mb}MB)...", flush=True)

    for i in range(num_parts):
        start_time = i * part_duration
        part_name = f"[{clean_t}] Part {i+1:03d} of {num_parts:03d}.mp4"
        part_path = os.path.join(WORK_DIR, part_name)

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(round(start_time, 2)),
            "-i", video_path,
        ]
        if i < num_parts - 1:
            cmd.extend(["-t", str(round(part_duration, 2))])

        cmd.extend([
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            "-movflags", "+faststart",
            part_path
        ])

        res = subprocess.run(cmd, capture_output=True)
        if res.returncode == 0 and os.path.exists(part_path) and os.path.getsize(part_path) > 1000:
            part_sz = os.path.getsize(part_path) / (1024 * 1024)
            print(f"Generated {part_name} ({part_sz:.1f}MB)", flush=True)
            parts.append(part_path)
        else:
            print(f"Error slicing part {i+1}: {res.stderr.decode('utf-8', errors='ignore')}", flush=True)

    return parts if parts else [video_path]


def slice_audio(audio_path: str, slice_mb: float, title: str, duration: int = 0) -> list:
    """Slice a large audio into sequential parts under slice_mb limit using FFmpeg."""
    file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
    target_part_mb = slice_mb * 0.88
    num_parts = max(2, math.ceil(file_size_mb / target_part_mb))
    part_duration = duration / num_parts if duration > 0 else 600

    clean_t = sanitize_filename(title)
    parts = []

    print(f"Slicing audio ({file_size_mb:.1f}MB) into {num_parts} parts (target limit: {slice_mb}MB)...", flush=True)

    for i in range(num_parts):
        start_time = i * part_duration
        part_name = f"[{clean_t}] Part {i+1:03d} of {num_parts:03d}.mp3"
        part_path = os.path.join(WORK_DIR, part_name)

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(round(start_time, 2)),
            "-i", audio_path,
        ]
        if i < num_parts - 1:
            cmd.extend(["-t", str(round(part_duration, 2))])

        cmd.extend([
            "-c", "copy",
            part_path
        ])

        res = subprocess.run(cmd, capture_output=True)
        if res.returncode == 0 and os.path.exists(part_path) and os.path.getsize(part_path) > 1000:
            part_sz = os.path.getsize(part_path) / (1024 * 1024)
            print(f"Generated {part_name} ({part_sz:.1f}MB)", flush=True)
            parts.append(part_path)

    return parts if parts else [audio_path]


def main():
    if not (URL and BOT_TOKEN and CHAT_ID):
        print("Missing required parameters (YT_URL, BALE_BOT_TOKEN, or CHAT_ID)", flush=True)
        sys.exit(1)

    print(f"Starting worker for URL: {URL} | Format: {FORMAT} | Quality: {QUALITY} | Slice limit: {SLICE_MB}MB", flush=True)
    update_progress("🚀 سرور اختصاصی فعال شد!\n⏳ در حال دریافت اطلاعات ویدیو...")

    # Step 1: Extract Video Metadata (and comments if requested)
    import yt_dlp
    ydl_meta_opts = {
        "quiet": True,
        "skip_download": True,
        "getcomments": SEND_COMMENTS
    }
    if "youtube.com" in URL or "youtu.be" in URL:
        ydl_meta_opts["js_runtimes"] = {"deno": {}}
        ydl_meta_opts["extractor_args"] = {
            "youtubepot-bgutilhttp": {
                "base_url": ["http://127.0.0.1:4416"],
                "disable_innertube": ["1"]
            },
            "youtube": {"player_client": ["web", "mweb", "android"]}
        }
    elif os.path.exists("cookies.txt") and os.path.getsize("cookies.txt") > 0:
        ydl_meta_opts["cookiefile"] = "cookies.txt"

    video_info = {}
    try:
        with yt_dlp.YoutubeDL(ydl_meta_opts) as ydl:
            video_info = ydl.extract_info(URL, download=False)
    except Exception as e:
        print(f"Metadata extraction warning: {e}", flush=True)

    title = video_info.get("title") or "YouTube Media"
    clean_title = sanitize_filename(title)

    # Step 2: Send extras first as requested (Description, Comments, Thumbnail, Subtitles)
    if SEND_DESCRIPTION and video_info.get("description"):
        desc = video_info["description"].strip()
        if desc:
            if len(desc) > 3800:
                desc = desc[:3800] + "\n\n... (ادامه در صفحه یوتیوب)"
            update_progress("📄 در حال ارسال توضیحات ویدیو...")
            send_message(f"📄 **توضیحات ویدیوی یوتیوب:**\n🎬 **{title}**\n\n{desc}")
            time.sleep(1)

    if SEND_COMMENTS and video_info.get("comments"):
        comments = video_info["comments"]
        if comments:
            update_progress("💬 در حال ارسال برترین نظرات کاربران...")
            comm_lines = [f"💬 **برترین نظرات کاربران یوتیوب:**\n🎬 **{title}**\n"]
            for idx, c in enumerate(comments[:5]):
                c_author = c.get("author", "کاربر")
                c_text = (c.get("text", "") or "").strip().replace("\n", " ")[:220]
                c_likes = c.get("like_count", 0)
                like_str = f" (👍 {c_likes:,})" if c_likes else ""
                comm_lines.append(f"{idx+1}️⃣ **{c_author}**{like_str}:\n{c_text}\n")
            send_message("\n".join(comm_lines))
            time.sleep(1)

    if SEND_THUMBNAIL and video_info.get("thumbnail"):
        thumb_url = video_info["thumbnail"]
        update_progress("🖼 در حال ارسال کاور اصلی ویدیو...")
        try:
            t_res = _SESSION.get(thumb_url, timeout=20)
            if t_res.status_code == 200:
                t_path = os.path.join(WORK_DIR, f"thumb_{int(time.time())}.jpg")
                with open(t_path, "wb") as tf:
                    tf.write(t_res.content)
                upload_photo_to_bale(t_path, caption=f"🖼 **کاور اصلی با بالاترین کیفیت**\n🎬 **{title}**")
                time.sleep(1)
        except Exception as e:
            print(f"Send thumbnail error: {e}", flush=True)

    if SEND_SUBTITLES:
        update_progress("📝 در حال دانلود و استخراج زیرنویس‌ها...")
        sub_opts = {
            "quiet": True,
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["fa", "en", "fa-.*", "en-.*"],
            "subtitlesformat": "srt",
            "outtmpl": os.path.join(WORK_DIR, f"sub_%(id)s.%(ext)s")
        }
        if "youtube.com" in URL or "youtu.be" in URL:
            sub_opts["js_runtimes"] = {"deno": {}}
            sub_opts["extractor_args"] = {
                "youtubepot-bgutilhttp": {
                    "base_url": ["http://127.0.0.1:4416"],
                    "disable_innertube": ["1"]
                },
                "youtube": {"player_client": ["web", "mweb", "android"]}
            }
        elif os.path.exists("cookies.txt") and os.path.getsize("cookies.txt") > 0:
            sub_opts["cookiefile"] = "cookies.txt"
        try:
            with yt_dlp.YoutubeDL(sub_opts) as ydl:
                ydl.download([URL])
            sub_files = glob.glob(os.path.join(WORK_DIR, "sub_*.srt"))
            for sf in sub_files[:3]:
                upload_document_to_bale(sf, caption=f"📝 **فایل زیرنویس ویدیو (SRT):**\n🎬 **{title}**")
                time.sleep(1)
        except Exception as e:
            print(f"Subtitle download warning: {e}", flush=True)

    # Step 3: Download media file
    ts = int(time.time())
    out_template = os.path.join(WORK_DIR, f"yt_{ts}_%(id)s.%(ext)s")

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist",
        "--no-check-certificates",
        "-N", "4",
        "--buffer-size", "1024K",
        "-o", out_template
    ]

    if "youtube.com" in URL or "youtu.be" in URL:
        cmd.extend([
            "--js-runtimes", "deno",
            "--extractor-args", "youtubepot-bgutilhttp:base_url=http://127.0.0.1:4416;disable_innertube=1",
            "--extractor-args", "youtube:player_client=web,mweb,android"
        ])
    elif os.path.exists("cookies.txt") and os.path.getsize("cookies.txt") > 0:
        cmd.extend(["--cookies", "cookies.txt"])

    if FORMAT == "mp3" or "audio" in QUALITY:
        update_progress("📥 در حال دانلود ترک صوتی با بالاترین کیفیت (320kbps)...")
        cmd.extend([
            "-f", "ba/b",
            "-x", "--audio-format", "mp3",
            "--audio-quality", "0",
            "--write-thumbnail",
            "--embed-thumbnail"
        ])
    else:
        max_h = 720
        if "1080" in QUALITY:
            max_h = 1080
        elif "480" in QUALITY:
            max_h = 480
        elif "360" in QUALITY:
            max_h = 360
        elif "240" in QUALITY:
            max_h = 240

        update_progress(f"📥 در حال دانلود ویدیو با کیفیت {max_h}p...")
        cmd.extend([
            "-f", f"bv*[height<={max_h}][ext=mp4]+ba[ext=m4a]/bv*[height<={max_h}]+ba/b[height<={max_h}]/b",
            "--merge-output-format", "mp4"
        ])

    cmd.append(URL)
    print("Executing download command:", " ".join(cmd), flush=True)
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    print("Primary returncode:", res.returncode, flush=True)
    if res.stdout:
        print("Primary stdout:", res.stdout[-600:], flush=True)
    if res.stderr:
        print("Primary stderr:", res.stderr[-600:], flush=True)

    # Automatic fallback if primary format failed
    if res.returncode != 0:
        print("Primary download failed. Attempting resilient fallback download...", flush=True)
        fallback_cmd = [
            sys.executable, "-m", "yt_dlp",
            "--no-playlist",
            "--no-check-certificates",
            "-N", "4",
            "-o", out_template
        ]
        if "youtube.com" in URL or "youtu.be" in URL:
            fallback_cmd.extend([
                "--js-runtimes", "deno",
                "--extractor-args", "youtubepot-bgutilhttp:base_url=http://127.0.0.1:4416;disable_innertube=1",
                "--extractor-args", "youtube:player_client=web,mweb,android"
            ])
        if FORMAT == "mp3" or "audio" in QUALITY:
            fallback_cmd.extend(["-f", "ba/b", "-x", "--audio-format", "mp3"])
        else:
            fallback_cmd.extend(["-f", "bestvideo+bestaudio/best", "--merge-output-format", "mp4"])
        fallback_cmd.append(URL)
        res = subprocess.run(fallback_cmd, capture_output=True, text=True, timeout=600)
        print("Fallback returncode:", res.returncode, flush=True)
        if res.stdout:
            print("Fallback stdout:", res.stdout[-600:], flush=True)
        if res.stderr:
            print("Fallback stderr:", res.stderr[-600:], flush=True)

    if res.returncode != 0:
        print("yt-dlp error:", res.stderr[-500:], flush=True)
        update_progress(f"❌ خطا در دانلود رسانه:\n{res.stderr[-200:]}")
        sys.exit(1)

    downloaded_files = glob.glob(os.path.join(WORK_DIR, f"yt_{ts}_*.*"))
    media_files = [f for f in downloaded_files if f.lower().endswith((".mp4", ".mp3", ".m4a", ".webm", ".mkv"))]

    if not media_files:
        update_progress("❌ فایلی برای ارسال یافت نشد.")
        sys.exit(1)

    target_file = media_files[0]
    total_size_mb = os.path.getsize(target_file) / (1024 * 1024)
    print(f"Target downloaded file: {target_file} (Size: {total_size_mb:.2f} MB)", flush=True)

    # Step 4: Slicing Check & Sequential Upload
    is_audio = target_file.lower().endswith(".mp3")

    if is_audio:
        probe = probe_media_file(target_file)
        duration = probe.get("duration", 0)

        if total_size_mb <= SLICE_MB:
            update_progress("⬆️ در حال ارسال موزیک به بله...")
            upload_audio_to_bale(
                audio_path=target_file,
                caption=f"🎵 **{title}**\n⚡️ دانلود شده توسط @tahasbot",
                title=clean_title,
                duration=duration
            )
        else:
            update_progress(f"✂️ حجم فایل ({total_size_mb:.1f}MB) بیش از سقف بله ({SLICE_MB}MB) است. در حال تکه‌کردن...")
            parts = slice_audio(target_file, SLICE_MB, title, duration=duration)
            for idx, part in enumerate(parts):
                part_probe = probe_media_file(part)
                update_progress(f"⬆️ در حال ارسال پارت {idx + 1} از {len(parts)} به بله...")
                upload_audio_to_bale(
                    audio_path=part,
                    caption=f"🎵 **{title}**\n📦 **Part {idx + 1:03d} of {len(parts):03d}**\n\n⚡️ دانلود شده توسط @tahasbot",
                    title=f"{clean_title} Part {idx + 1:03d}",
                    duration=part_probe.get("duration", 0)
                )
                time.sleep(2)
    else:
        probe = probe_media_file(target_file)
        if total_size_mb <= SLICE_MB:
            update_progress("⬆️ در حال ارسال ویدیو به بله...")
            upload_video_to_bale(
                video_path=target_file,
                caption=f"🎬 **{title}**\n\n⚡️ دانلود شده توسط @tahasbot",
                probe=probe
            )
        else:
            update_progress(f"✂️ حجم ویدیو ({total_size_mb:.1f}MB) بیش از سقف بله ({SLICE_MB}MB) است. در حال تکه‌کردن به پارت‌ها...")
            parts = slice_video(target_file, SLICE_MB, title, probe=probe)
            for idx, part in enumerate(parts):
                part_probe = probe_media_file(part)
                update_progress(f"⬆️ در حال ارسال پارت {idx + 1} از {len(parts)} به بله...")
                upload_video_to_bale(
                    video_path=part,
                    caption=f"🎬 **{title}**\n📦 **Part {idx + 1:03d} of {len(parts):03d}**\n\n⚡️ دانلود شده توسط @tahasbot",
                    probe=part_probe
                )
                time.sleep(2)

    delete_progress()
    print("Worker task completed successfully!", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        traceback.print_exc()
        update_progress(f"❌ خطای پیش‌بینی نشده در سرور پردازش:\n{str(e)[:250]}")
        sys.exit(1)
