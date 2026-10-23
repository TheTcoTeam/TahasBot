"""ffmpeg video edits, run on a GitHub Actions runner.

bot.py builds the exact ffmpeg arguments (with {SRC}/{OUT}/{FONT_*}
placeholders) and dispatches media_tools.yml; this script fetches the clip
from Bale by file_id, runs ffmpeg and sends the result back to the chat.
"""
import json
import os
import subprocess
import sys

import requests

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
API = os.environ.get("BALE_API_BASE", "https://tapi.bale.ai").rstrip("/")
FILE_ID = os.environ.get("FILE_ID", "")
ARGS = os.environ.get("ARGS", "[]")
OUT_EXT = os.environ.get("OUT_EXT", ".mp4") or ".mp4"
SEND_AS = (os.environ.get("SEND_AS") or "video").strip()
CHAT_ID = int(os.environ.get("CHAT_ID") or 0)
MESSAGE_ID = int(os.environ.get("MESSAGE_ID") or 0)
CAPTION = os.environ.get("CAPTION", "")
AUDIO_TITLE = os.environ.get("AUDIO_TITLE", "")
REPLY_MARKUP = os.environ.get("REPLY_MARKUP", "")
FONT_LATIN = os.environ.get("FONT_LATIN", "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf")
FONT_ARABIC = os.environ.get("FONT_ARABIC", "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf")
LIMIT = 47 * 1024 * 1024


def api(method, data=None, files=None, timeout=60):
    r = requests.post(f"{API}/bot{BOT_TOKEN}/{method}", data=data if files else None,
                      json=None if files else data, files=files, timeout=timeout)
    j = r.json()
    if not j.get("ok"):
        raise RuntimeError(f"{method}: {j.get('description')}")
    return j.get("result")


def notify_failure(text):
    print(text, flush=True)
    try:
        if MESSAGE_ID:
            api("editMessageText", {"chat_id": CHAT_ID, "message_id": MESSAGE_ID, "text": text,
                                    "reply_markup": json.loads(REPLY_MARKUP) if REPLY_MARKUP else None})
        else:
            api("sendMessage", {"chat_id": CHAT_ID, "text": text})
    except Exception as e:
        print(f"notify failed: {e}", flush=True)


def duration(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=noprint_wrappers=1:nokey=1", path],
                             capture_output=True, text=True, timeout=30)
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def fit(path):
    if os.path.getsize(path) <= LIMIT or SEND_AS != "video":
        return path
    dur = duration(path) or 1
    kbps = max(150, int(LIMIT * 8 / 1000 / dur * 0.9) - 96)
    out = "fit_" + os.path.basename(path)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-c:v", "libx264", "-preset", "veryfast",
                    "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{2 * kbps}k",
                    "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", out], check=True, timeout=1800)
    return out


def main():
    if not (BOT_TOKEN and CHAT_ID and FILE_ID):
        print("missing BOT_TOKEN/CHAT_ID/FILE_ID", flush=True)
        return 1
    try:
        info = api("getFile", {"file_id": FILE_ID})
        path = info["file_path"]
        url = path if path.startswith("http") else f"{API}/file/bot{BOT_TOKEN}/{path}"
        src = "source.mp4"
        with requests.get(url, stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(src, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
    except Exception as e:
        notify_failure(f"❌ دریافت ویدیو از بله ناموفق بود.\n{str(e)[:200]}")
        return 1

    out = "result" + OUT_EXT
    args = [a.replace("{SRC}", src).replace("{OUT}", out)
             .replace("{FONT_ARABIC}", FONT_ARABIC).replace("{FONT_LATIN}", FONT_LATIN)
            for a in json.loads(ARGS)]
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True, timeout=1800,
                       capture_output=True, text=True)
        if not os.path.exists(out) or os.path.getsize(out) == 0:
            raise RuntimeError("empty output")
        out = fit(out)
    except subprocess.CalledProcessError as e:
        notify_failure(f"❌ ویرایش ویدیو ناموفق بود.\n{(e.stderr or '')[-200:]}")
        return 1
    except Exception as e:
        notify_failure(f"❌ ویرایش ویدیو ناموفق بود.\n{str(e)[:200]}")
        return 1

    if os.path.getsize(out) > 50 * 1024 * 1024:
        notify_failure("❌ فایل نهایی بیشتر از ۵۰ مگابایت است و بله اجازه ارسال آن را نمی‌دهد.")
        return 1

    method, field = {"audio": ("sendAudio", "audio"), "animation": ("sendAnimation", "animation")}.get(
        SEND_AS, ("sendVideo", "video"))
    data = {"chat_id": str(CHAT_ID), "caption": CAPTION}
    if SEND_AS == "audio":
        data.update({"title": AUDIO_TITLE or "Audio", "performer": "TMulti"})
    if SEND_AS == "video":
        data["duration"] = str(int(duration(out)))
        data["supports_streaming"] = "true"
        if REPLY_MARKUP:
            data["reply_markup"] = REPLY_MARKUP
    try:
        with open(out, "rb") as fh:
            try:
                api(method, data, {field: (os.path.basename(out), fh)}, timeout=900)
            except RuntimeError:
                if SEND_AS != "animation":
                    raise
                fh.seek(0)
                api("sendDocument", data, {"document": (os.path.basename(out), fh)}, timeout=900)
    except Exception as e:
        notify_failure(f"❌ ارسال نتیجه ناموفق بود.\n{str(e)[:200]}")
        return 1
    if MESSAGE_ID:
        try:
            api("deleteMessage", {"chat_id": CHAT_ID, "message_id": MESSAGE_ID})
        except Exception:
            pass
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
