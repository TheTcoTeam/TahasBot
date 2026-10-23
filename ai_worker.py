"""Heavy AI jobs that Render's 0.1-CPU instance cannot run.

Dispatched from bot.py via workflow_dispatch. Two tasks:
  subs    - transcribe a video with Whisper, translate the text, burn subtitles in
  karaoke - split a track into vocals / instrumental with Demucs

Set DRY_RUN=true to run the full pipeline but skip Telegram delivery; useful for
validating the workflow without a real chat.
"""
import os
import sys
import glob
import time
import shutil
import subprocess
import asyncio
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pyrogram import Client
import pyrogram.utils as _pyro_utils

# Support modern 64-bit Telegram channel IDs
if getattr(_pyro_utils, "MIN_CHANNEL_ID", 0) > -1009999999999:
    _pyro_utils.MIN_CHANNEL_ID = -1009999999999

# Persistent HTTP session with connection pooling
_SESSION = requests.Session()
_retries = Retry(total=3, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
_adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=_retries)
_SESSION.mount("https://", _adapter)
_SESSION.mount("http://", _adapter)

TASK = (os.environ.get("TASK") or "subs").strip().lower()
SOURCE_URL = (os.environ.get("SOURCE_URL") or "").strip()
CHAT_ID = int(os.environ.get("CHAT_ID") or 0)
MESSAGE_ID = int(os.environ.get("MESSAGE_ID") or 0)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
TARGET_LANG = (os.environ.get("TARGET_LANG") or "fa").strip().lower()
DRY_RUN = (os.environ.get("DRY_RUN") or "false").strip().lower() == "true"

WORK = "ai_out"
API_ID, API_HASH = 2040, "b18441a1ff607e10a989891a5462e627"


def log(msg):
    print(f"[AI] {msg}", flush=True)


def progress(text):
    """Mirror status into the user's Telegram message."""
    log(text)
    if DRY_RUN or not (MESSAGE_ID and BOT_TOKEN and CHAT_ID):
        return
    try:
        _SESSION.post(
            f"https://tapi.bale.ai/bot{BOT_TOKEN}/editMessageText",
            json={"chat_id": CHAT_ID, "message_id": MESSAGE_ID, "text": text},
            timeout=10,
        )
    except Exception:
        pass


def notify(text):
    if DRY_RUN or not (BOT_TOKEN and CHAT_ID):
        log(f"(notify skipped) {text}")
        return
    try:
        _SESSION.post(
            f"https://tapi.bale.ai/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception:
        pass


def run(cmd, timeout=5400):
    log("$ " + " ".join(str(c) for c in cmd[:8]) + (" ..." if len(cmd) > 8 else ""))
    return subprocess.run(cmd, check=True, timeout=timeout,
                          capture_output=True, text=True, errors="replace")


def ytdlp_base():
    cmd = [sys.executable, "-m", "yt_dlp", "--no-playlist", "--no-check-certificates",
           "--retries", "5", "--quiet", "--no-warnings"]
    # YouTube serves a JS challenge; without a JS runtime yt-dlp fails with
    # "The page needs to be reloaded." worker.py already does this — the runner
    # has Node preinstalled and the solver is fetched from the ejs release.
    if "youtube.com" in SOURCE_URL or "youtu.be" in SOURCE_URL:
        cmd += ["--js-runtimes", "deno"]
        cmd += ["--extractor-args", "youtube:getpot_bgutil_baseurl=" + os.environ.get("POT_PROVIDER_URL", "http://127.0.0.1:4416")]
    if os.path.exists("cookies.txt") and os.path.getsize("cookies.txt") > 0:
        cmd += ["--cookies", "cookies.txt"]
    return cmd


def download_video():
    """Fetch the video, capped at 720p to keep the subtitle burn-in affordable."""
    out = f"{WORK}/source.%(ext)s"
    run(ytdlp_base() + [
        "--format", "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
        "--merge-output-format", "mp4", "--impersonate", "chrome",
        "--output", out, SOURCE_URL])
    files = [f for f in glob.glob(f"{WORK}/source.*")
             if Path(f).suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")]
    if not files:
        raise RuntimeError("no video downloaded")
    return files[0]


def download_audio():
    out = f"{WORK}/audio.%(ext)s"
    run(ytdlp_base() + ["--format", "bestaudio[ext=m4a]/bestaudio",
                        "--impersonate", "chrome", "--output", out, SOURCE_URL])
    files = [f for f in glob.glob(f"{WORK}/audio.*")
             if Path(f).suffix.lower() in (".m4a", ".mp3", ".opus", ".webm", ".ogg", ".wav")]
    if not files:
        raise RuntimeError("no audio downloaded")
    return files[0]


def srt_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(cues, path):
    with open(path, "w", encoding="utf-8") as fh:
        for i, (start, end, text) in enumerate(cues, 1):
            fh.write(f"{i}\n{srt_timestamp(start)} --> {srt_timestamp(end)}\n{text}\n\n")
    return path


def media_duration(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", path],
                             capture_output=True, text=True, timeout=60).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


def _whisper_pass(model, audio_path, vad):
    segments, info = model.transcribe(audio_path, beam_size=1, vad_filter=vad)
    cues = [(s.start, s.end, (s.text or "").strip()) for s in segments if (s.text or "").strip()]
    log(f"whisper pass vad={vad}: language={info.language} cues={len(cues)}")
    return cues, info.language


def transcribe(audio_path):
    """Whisper transcription. 'small' + int8 is the accuracy/CPU sweet spot here."""
    from faster_whisper import WhisperModel
    log(f"audio: {os.path.getsize(audio_path)/1e6:.1f} MB, {media_duration(audio_path):.0f}s")
    progress("🧠 در حال تحلیل صدا با هوش مصنوعی...")
    model = WhisperModel("small", device="cpu", compute_type="int8")

    cues, lang = _whisper_pass(model, audio_path, vad=True)
    if not cues:
        # Silero VAD is tuned for speech and classifies singing/musical audio as
        # non-speech, which can strip an entire track and yield zero cues.
        # Retry without it rather than reporting "no speech found".
        log("VAD removed everything; retrying without VAD")
        progress("🧠 در حال تحلیل دقیق‌تر صدا...")
        cues, lang = _whisper_pass(model, audio_path, vad=False)
    return cues, lang


def translate_cues(cues, source_lang):
    """Translate cue text to TARGET_LANG, preserving timings.

    Falls back to the original text on failure — a subtitle in the source
    language beats no subtitle at all.
    """
    if not cues or source_lang == TARGET_LANG:
        return cues
    try:
        from deep_translator import GoogleTranslator
    except Exception as e:
        log(f"translator unavailable ({e}); keeping original language")
        return cues

    progress(f"🌐 در حال ترجمه {len(cues)} خط زیرنویس...")
    translator = GoogleTranslator(source="auto", target=TARGET_LANG)
    out, batch_size = [], 20
    for i in range(0, len(cues), batch_size):
        batch = cues[i:i + batch_size]
        texts = [c[2] for c in batch]
        translated = []
        # translate_batch fires one request per line back to back and trips the
        # free endpoint's 5 req/s limit; go line by line, backing off on errors.
        for text in texts:
            new = text
            for attempt in range(4):
                try:
                    new = translator.translate(text) or text
                    break
                except Exception as e:
                    if attempt == 3:
                        log(f"line translation failed ({str(e)[:80]}); keeping original")
                    time.sleep(1.5 * (attempt + 1))
            translated.append(new)
            time.sleep(0.25)
    return out


def burn_subtitles(video_path, srt_path):
    """Hardcode subtitles so they show in Telegram's player, not just external ones."""
    out = f"{WORK}/subtitled.mp4"
    srt_escaped = srt_path.replace("\\", "/").replace(":", r"\:")
    style = "FontName=Noto Sans,FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H90000000,BorderStyle=3"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", video_path,
         "-vf", f"subtitles='{srt_escaped}':force_style='{style}'",
         "-c:a", "copy", "-preset", "veryfast", out])
    return out


BALE_LIMIT = 47 * 1024 * 1024


def fit_for_bale(path):
    """Bale caps bot uploads at 50 MB: re-encode a bigger video to fit."""
    if os.path.getsize(path) <= BALE_LIMIT or Path(path).suffix.lower() not in (".mp4", ".mkv", ".webm"):
        return path
    dur = media_duration(path) or 1
    kbps = max(150, int(BALE_LIMIT * 8 / 1000 / dur * 0.9) - 96)
    out = f"{WORK}/fit_{Path(path).stem}.mp4"
    progress("🗜 در حال کم‌حجم کردن ویدیو برای ارسال در بله...")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-c:v", "libx264", "-preset", "veryfast",
         "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{2 * kbps}k",
         "-vf", "scale='min(1280,iw)':-2", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", out])
    return out


async def upload(files, caption):
    files = [fit_for_bale(f) for f in files]
    if DRY_RUN:
        for f in files:
            log(f"(dry run) would upload {f} ({os.path.getsize(f)/1e6:.1f} MB)")
        return
    app = Client("ai_worker", api_id=API_ID, api_hash=API_HASH,
                 bot_token=BOT_TOKEN, in_memory=True, max_concurrent_transmissions=8)
    await app.start()
    try:
        for idx, path in enumerate(files):
            ext = Path(path).suffix.lower()
            cap = caption if idx == 0 else Path(path).name
            if ext in (".mp4", ".mkv", ".webm"):
                v_dur = int(media_duration(path))
                v_thumb = f"{path}_thumb.jpg"
                thumb_to_use = None
                try:
                    subprocess.run(
                        ['ffmpeg', '-y', '-ss', '00:00:01', '-i', path, '-vframes', '1',
                         '-vf', 'scale=320:-2', '-qscale:v', '5', v_thumb],
                        capture_output=True, timeout=15
                    )
                    if os.path.exists(v_thumb) and os.path.getsize(v_thumb) > 0:
                        thumb_to_use = v_thumb
                except Exception:
                    pass
                await app.send_video(CHAT_ID, path, caption=cap, duration=v_dur, thumb=thumb_to_use)
                if thumb_to_use and os.path.exists(thumb_to_use):
                    try:
                        os.remove(thumb_to_use)
                    except Exception:
                        pass
            elif ext in (".mp3", ".m4a", ".wav", ".flac"):
                await app.send_audio(CHAT_ID, path, caption=cap)
            else:
                await app.send_document(CHAT_ID, path, caption=cap)
        if MESSAGE_ID:
            try:
                await app.delete_messages(CHAT_ID, MESSAGE_ID)
            except Exception:
                pass
    finally:
        await app.stop()


def task_subs():
    progress("⬇️ در حال دریافت ویدیو...")
    video = download_video()
    audio = f"{WORK}/track.wav"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", video,
         "-vn", "-ac", "1", "-ar", "16000", audio])

    cues, lang = transcribe(audio)
    if not cues:
        notify("❌ گفتاری برای تبدیل به زیرنویس پیدا نشد.")
        return
    cues = translate_cues(cues, lang)
    srt = write_srt(cues, f"{WORK}/subtitles.srt")

    progress("🎬 در حال چسباندن زیرنویس روی ویدیو...")
    try:
        final = burn_subtitles(video, srt)
        files = [final, srt]
    except Exception as e:
        # Still deliver the .srt: it is the valuable part.
        log(f"burn-in failed ({e}); sending the video and subtitle file separately")
        files = [video, srt]

    asyncio.run(upload(files, f"📝 زیرنویس ({TARGET_LANG}) • {len(cues)} خط"))


def task_karaoke():
    progress("⬇️ در حال دریافت آهنگ...")
    audio = download_audio()
    progress("🎛 در حال جداسازی صدای خواننده (چند دقیقه طول می‌کشد)...")
    # --two-stems=vocals gives exactly the two files we want, and is much
    # faster than separating drums/bass/other individually.
    run([sys.executable, "-m", "demucs", "--two-stems=vocals",
         "-n", "htdemucs", "-o", f"{WORK}/stems", audio])

    stems = glob.glob(f"{WORK}/stems/**/*.wav", recursive=True)
    log(f"stems produced: {stems}")
    outputs = []
    for stem in stems:
        name = Path(stem).stem
        label = "instrumental" if name == "no_vocals" else name
        mp3 = f"{WORK}/{label}.mp3"
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", stem,
             "-c:a", "libmp3lame", "-b:a", "192k", mp3])
        outputs.append(mp3)
    if not outputs:
        notify("❌ جداسازی صدا انجام نشد.")
        return
    # Instrumental first: it is what most people asked for.
    outputs.sort(key=lambda p: 0 if "instrumental" in p else 1)
    asyncio.run(upload(outputs, "🎤 نسخه کارائوکه (بی‌کلام) و صدای خواننده"))


def main():
    if not SOURCE_URL:
        log("no SOURCE_URL; nothing to do")
        return
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    t0 = time.time()
    try:
        if TASK == "karaoke":
            task_karaoke()
        else:
            task_subs()
        log(f"done in {time.time() - t0:.0f}s")
    except subprocess.CalledProcessError as e:
        log(f"command failed: {(e.stderr or '')[-600:]}")
        notify("❌ پردازش این فایل با خطا مواجه شد.")
        sys.exit(1)
    except Exception as e:
        log(f"failed: {type(e).__name__}: {e}")
        notify("❌ پردازش این فایل با خطا مواجه شد.")
        sys.exit(1)


if __name__ == "__main__":
    main()
