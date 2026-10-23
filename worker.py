import os
import sys
import time
import glob
import subprocess
import traceback
import asyncio
from pathlib import Path
import urllib3
import re
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pyrogram import Client
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import pyrogram.utils as _pyro_utils

# Pyrogram hardcodes MIN_CHANNEL_ID = -1002147483647, but Telegram now issues
# channel ids beyond it. Uploading to such a chat died with "Peer id invalid"
# AFTER the download had finished, wasting the whole job.
if getattr(_pyro_utils, "MIN_CHANNEL_ID", 0) > -1009999999999:
    _pyro_utils.MIN_CHANNEL_ID = -1009999999999

# Every local-file send_audio() writes tags/cover into the file and uploads it
# as "Artist - Title.mp3" instead of "TCO_DL_001.mp3" (see modules/audio_meta.py).
try:
    from modules import audio_meta
    audio_meta.install_send_audio_patch(Client)
except Exception as _e:  # never let tagging break a download job
    audio_meta = None
    print(f"audio_meta unavailable: {_e}", flush=True)


def _audio_meta_from_info(info_files):
    """title/artist/album for an audio upload, from yt-dlp's .info.json."""
    out = {}
    for path in info_files:
        try:
            import json as _j
            with open(path, 'r', encoding='utf-8') as fh:
                m = _j.load(fh)
        except Exception:
            continue
        title = (m.get('track') or m.get('title') or '').strip()
        artist = (m.get('artist') or m.get('creator') or m.get('uploader') or m.get('channel') or '').strip()
        artist = re.sub(r'(?i)\s*-\s*topic$|\s*vevo$', '', artist).strip()
        if not m.get('track') and ' - ' in title:
            head, tail = title.split(' - ', 1)
            if head.strip():
                artist, title = head.strip(), tail.strip()
        title = re.sub(r'(?i)\s*[\(\[](official\s*(music\s*)?(video|audio|lyric\s*video|visualizer)|lyrics?|audio|hd|4k)[\)\]]', '', title).strip()
        out = {'title': title, 'artist': artist, 'album': (m.get('album') or '').strip(),
               'duration': int(m.get('duration') or 0)}
        break
    return out


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
os.environ['PYTHONHTTPSVERIFY'] = '0'

# High-performance persistent HTTP session with connection pooling
_SESSION = requests.Session()
_retries = Retry(total=3, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
_adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=_retries)
_SESSION.mount("https://", _adapter)
_SESSION.mount("http://", _adapter)

URL = os.environ.get('YT_URL')
PLATFORM = os.environ.get('PLATFORM', 'youtube').lower()
FORMAT = os.environ.get('YT_FORMAT', 'mp4').lower()
TARGET_QUALITY = os.environ.get('YT_QUALITY', '1080p') 
BOT_TOKEN = os.environ.get('BOT_TOKEN') 
CHAT_ID = int(os.environ.get('CHAT_ID', '0'))
MESSAGE_ID = int(os.environ.get('MESSAGE_ID', '0'))

GET_COMMENTS = os.environ.get('GET_COMMENTS', 'false').lower() == 'true'
GET_DESC = os.environ.get('GET_DESC', 'false').lower() == 'true'
GET_THUMBNAIL = os.environ.get('GET_THUMBNAIL', 'false').lower() == 'true'

TRIM_START = os.environ.get('TRIM_START')
TRIM_END = os.environ.get('TRIM_END')

MUSIC_CACHE_CHANNEL = os.environ.get('MUSIC_CACHE_CHANNEL')
BOT_USERNAME = os.environ.get('BOT_USERNAME', 'tahasbot')
POT_URL = os.environ.get('POT_PROVIDER_URL', 'http://127.0.0.1:4416')
VID_ID = os.environ.get('VID_ID') or os.environ.get('TARGET_VID', '')
if not VID_ID and URL:
    m = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", URL)
    if m:
        VID_ID = m.group(1)

if URL:
    URL = URL.replace(" ", "")
    if "instagram.com" in URL:
        URL = URL.split("?")[0]

def update_progress(text):
    print(f"\n[BOT STATE] {text}", flush=True)
    if MESSAGE_ID and BOT_TOKEN and CHAT_ID:
        try:
            _SESSION.post(
                f"https://tapi.bale.ai/bot{BOT_TOKEN}/editMessageText", 
                json={'chat_id': CHAT_ID, 'message_id': MESSAGE_ID, 'text': text}, 
                timeout=10, 
                verify=False
            )
        except Exception:
            pass

def send_text_to_telegram(text):
    if CHAT_ID and BOT_TOKEN:
        try:
            _SESSION.post(
                f"https://tapi.bale.ai/bot{BOT_TOKEN}/sendMessage",
                json={'chat_id': CHAT_ID, 'text': text},
                timeout=10,
                verify=False
            )
        except Exception:
            pass

async def upload_music(audio_path, thumb_path, chat_id, msg_id, cache_channel=None, vid_id=""):
    app = Client("music_worker", api_id=2040, api_hash="b18441a1ff607e10a989891a5462e627", bot_token=BOT_TOKEN, in_memory=True, max_concurrent_transmissions=8, sleep_threshold=180)
    await app.start()
    try:
        # Group vs Private Chat keyboard
        kb = None
        if int(chat_id) < 0:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("⚡️ رفتن به تی‌مولتی", url=f"https://ble.ir/{BOT_USERNAME}?start=opt_{vid_id}")]])
        elif vid_id:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🎤 متن ترانه", callback_data=f"lyrics_{vid_id}"),
                 InlineKeyboardButton("📄 فایل LRC", callback_data=f"lrc_{vid_id}")],
                [InlineKeyboardButton("🎛 افکت‌ها", callback_data=f"fx_{vid_id}"),
                 InlineKeyboardButton("🎙 کارائوکه", callback_data=f"kar_{vid_id}")],
                [InlineKeyboardButton("✂️ برش موزیک", callback_data=f"trim_{vid_id}"),
                 InlineKeyboardButton("⏩ تغییر سرعت", callback_data=f"speed_{vid_id}")],
                [InlineKeyboardButton("🏷 ویرایش تگ‌ها", callback_data=f"tag_{vid_id}"),
                 InlineKeyboardButton("📀 آلبوم کامل", callback_data=f"alb_{vid_id}")],
                [InlineKeyboardButton("➕ افزودن به پلی‌لیست", callback_data=f"addpl_{vid_id}")],
            ])
        am = _audio_meta_from_info(glob.glob("TCO_DL_*.info.json"))
        sent_msg = await app.send_audio(chat_id, audio=audio_path, thumb=_small_thumb(thumb_path), caption="", reply_markup=kb,
                                        title=am.get('title') or None, performer=am.get('artist') or None,
                                        album=am.get('album') or None, duration=am.get('duration') or 0)
        
        try:
            await app.delete_messages(chat_id, msg_id)
        except Exception:
            pass
        
        if cache_channel and vid_id:
            try:
                _SESSION.post(
                    f"https://tapi.bale.ai/bot{BOT_TOKEN}/sendAudio",
                    json={
                        "chat_id": cache_channel,
                        "audio": sent_msg.audio.file_id,
                        "caption": f"CACHE_VID:{vid_id}"
                    },
                    timeout=10
                )
            except Exception:
                pass
            
    except Exception as e:
        print(f"[MUSIC UPLOAD ERROR] {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        send_text_to_telegram(f"❌ خطا در آپلود:\n{str(e)[:300]}")
        try:
            await app.edit_message_text(chat_id, msg_id, f"❌ خطا در آپلود:\n`{str(e)[:300]}`")
        except Exception:
            pass
    finally:
        await app.stop()

def _small_thumb(path):
    """Telegram audio thumbs: square JPEG <=320px. Full-size covers get ignored."""
    if not path or not os.path.exists(path) or audio_meta is None:
        return path
    try:
        with open(path, 'rb') as fh:
            out = audio_meta.make_thumb(fh.read(), path + '.sq.jpg')
        return out or path
    except Exception:
        return path


async def upload_progress(current, total, app, active_msg_id, last_update_ref, part_info=""):
    try:
        now = time.time()
        if now - last_update_ref[0] > 8.0 and total:
            last_update_ref[0] = now
            percent = (current / total) * 100
            try:
                await app.edit_message_text(CHAT_ID, active_msg_id, f"⬆️ **در حال آپلود فایل نهایی...** {part_info}\n`{percent:.1f}%`")
            except Exception:
                pass
    except Exception:
        traceback.print_exc()

async def upload_massive_file(file_paths, active_msg_id, video_meta=None, thumb_path=None):
    app = Client("upload_worker", api_id=2040, api_hash="b18441a1ff607e10a989891a5462e627", bot_token=BOT_TOKEN, in_memory=True, max_concurrent_transmissions=8, sleep_threshold=180)
    await app.start()
    try:
        total_files = len(file_paths)
        for idx, file_path in enumerate(file_paths):
            filename = Path(file_path).name
            part_info = f"(بخش {idx+1} از {total_files})" if total_files > 1 else ""
            ext = Path(file_path).suffix.lower()
            title = (video_meta or {}).get('title')
            vmeta_part = dict(video_meta or {})
            if total_files > 1:
                vmeta_part['duration'] = int(_probe_seconds(file_path))
            if ext not in ['.jpg', '.jpeg', '.png', '.webp'] and title:
                caption = f"🎬 {title} {part_info}"
            else:
                caption = f"📂 {filename} {part_info}"
            last_update = [time.time()]
            if ext in ['.mp3', '.m4a', '.flac', '.wav', '.ogg']: 
                if CHAT_ID < 0:
                    audio_kb = InlineKeyboardMarkup([[InlineKeyboardButton("⚡️ رفتن به تی‌مولتی", url=f"https://ble.ir/{BOT_USERNAME}")]])
                else:
                    audio_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎛 ابزارهای صدا و افکت", callback_data="menu_music_tools")]])
                am = _audio_meta_from_info(glob.glob("TCO_DL_*.info.json"))
                await app.send_audio(CHAT_ID, file_path, caption="", reply_markup=audio_kb,
                                     title=am.get('title') or (video_meta or {}).get('title') or None,
                                     performer=am.get('artist') or None, album=am.get('album') or None,
                                     duration=am.get('duration') or 0, thumb=_small_thumb(thumb_path),
                                     progress=upload_progress, progress_args=(app, active_msg_id, last_update, part_info))
            elif ext in ['.jpg', '.jpeg', '.png', '.webp']: 
                photo_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎨 ابزارهای تصویر", callback_data="menu_image_tools")]])
                await app.send_photo(CHAT_ID, file_path, caption=caption, reply_markup=photo_kb, progress=upload_progress, progress_args=(app, active_msg_id, last_update, part_info))
            else:
                vmeta = vmeta_part
                video_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎬 ویرایش این ویدیو", callback_data="vedit_from_last")]])
                await app.send_video(
                    CHAT_ID, file_path, caption=caption,
                    duration=vmeta.get('duration') or 0,
                    width=vmeta.get('width'), height=vmeta.get('height'),
                    thumb=thumb_path,
                    reply_markup=video_kb,
                    progress=upload_progress, progress_args=(app, active_msg_id, last_update, part_info))
        await app.edit_message_text(CHAT_ID, active_msg_id, "🎉 **فایل‌ها با موفقیت ارسال شدند!**")
    except Exception as e:
        print(f"[UPLOAD ERROR] {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        send_text_to_telegram(f"❌ خطا در آپلود فایل:\n{str(e)[:300]}")
        try:
            await app.edit_message_text(CHAT_ID, active_msg_id, f"❌ خطا در آپلود فایل:\n`{str(e)[:300]}`")
        except Exception:
            pass
    finally:
        await app.stop()

# Bale rejects bot uploads over 50 MB, so bigger files go out as several parts.
BALE_PART_BYTES = int(os.environ.get('BALE_PART_BYTES', str(47 * 1024 * 1024)))


def _probe_seconds(path):
    try:
        out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                              '-of', 'default=noprint_wrappers=1:nokey=1', path],
                             capture_output=True, text=True, timeout=30)
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def split_for_bale(paths, video_meta=None):
    out = []
    for p in paths:
        size = os.path.getsize(p)
        if size <= BALE_PART_BYTES:
            out.append(p)
            continue
        dur = _probe_seconds(p) or float((video_meta or {}).get('duration') or 0)
        if not dur:
            out.append(p)
            continue
        ext = Path(p).suffix.lower()
        parts = int(size // BALE_PART_BYTES) + 1
        seg = max(5, int(dur / parts * 0.92))
        stem = Path(p).stem
        pattern = f"{stem}_part%03d{ext}"
        update_progress(f"✂️ فایل {size // (1024*1024)} مگابایت است؛ به {parts} بخش تقسیم می‌شود (محدودیت ۵۰ مگابایتی بله)...")
        r = subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', p, '-map', '0', '-c', 'copy',
                            '-f', 'segment', '-segment_time', str(seg), '-reset_timestamps', '1', pattern],
                           capture_output=True, text=True)
        pieces = sorted(glob.glob(f"{stem}_part*{ext}"))
        if r.returncode != 0 or not pieces:
            print(f"split failed: {r.stderr[-400:]}", flush=True)
            out.append(p)
            continue
        # A copy-split cuts on keyframes; re-split any piece that is still too big.
        for piece in pieces:
            if os.path.getsize(piece) > BALE_PART_BYTES:
                out.extend(split_for_bale([piece], None))
            else:
                out.append(piece)
    return out


def main():
    if not URL: 
        return

    if PLATFORM == 'music':
        ydl_opts = [
            sys.executable, '-m', 'yt_dlp',
            '--output', 'TCO_DL_%(autonumber)03d.%(ext)s', 
            '--format', 'bestaudio[ext=m4a]/m4a/bestaudio/best', 
            '--write-thumbnail', '--convert-thumbnails', 'jpg', '--write-info-json',
            '--no-playlist', '--quiet',
            '--js-runtimes', 'deno',
            '--socket-timeout', '15',
            '--concurrent-fragments', '4',
            '--extractor-args', f'youtube:getpot_bgutil_baseurl={POT_URL}'
        ]
        if os.path.exists('cookies.txt') and os.path.getsize('cookies.txt') > 0:
            ydl_opts.extend(['--cookies', 'cookies.txt'])
        if TRIM_START and TRIM_END:
            ydl_opts.extend(['--download-sections', f"*{TRIM_START}-{TRIM_END}", '--force-keyframes-at-cuts'])
        ydl_opts.append(URL)
        
        subprocess.run(ydl_opts)
        
        all_files = glob.glob("TCO_DL_*.*")
        audio_files = [f for f in all_files if f.lower().endswith(('.mp3', '.m4a', '.webm', '.ogg', '.wav'))]
        thumb_files = glob.glob("TCO_DL_*.jpg")
        
        if not audio_files:
            return update_progress("❌ خطا در استخراج فایل صوتی.")
            
        asyncio.run(upload_music(
            audio_files[0], 
            thumb_files[0] if thumb_files else None, 
            CHAT_ID, MESSAGE_ID, MUSIC_CACHE_CHANNEL, VID_ID
        ))
        return

    update_progress("⬇️ در حال دانلود محتوا...")
    ydl_opts = [
        sys.executable, '-m', 'yt_dlp',
        '--output', 'TCO_DL_%(autonumber)03d.%(ext)s', 
        '--no-check-certificates', '--impersonate', 'chrome',
        '--retries', '10', '--fragment-retries', '10', '--concurrent-fragments', '4',
        '--socket-timeout', '15', '--buffer-size', '1024K'
    ]

    if PLATFORM == 'instagram':
        ydl_opts.append('--ignore-no-formats-error')
        if '/p/' not in URL and '/reel/' not in URL and '/reels/' not in URL: 
            ydl_opts.extend(['--playlist-items', '1'])
    elif PLATFORM == 'tiktok':
        if '/video/' not in URL: 
            ydl_opts.extend(['--playlist-items', '1'])
    else: 
        ydl_opts.append('--no-playlist')

    if GET_DESC: 
        ydl_opts.append('--write-description')
    # Always pulled (not just when GET_THUMBNAIL is checked): send_video needs
    # a real thumb + duration or Telegram shows a black cover and 00:00.
    ydl_opts.extend(['--write-thumbnail', '--convert-thumbnails', 'jpg', '--write-info-json'])

    if FORMAT == 'mp3': 
        ydl_opts.extend(['--format', 'bestaudio/best', '--extract-audio', '--audio-format', 'mp3'])
    elif PLATFORM in ['instagram', 'tiktok']: 
        ydl_opts.extend(['--format', 'best'])
    else:
        height_map = {'8K': 4320, '4K': 2160, '1080p': 1080, '720p': 720, '480p': 480, '360p': 360, '240p': 240}
        target_height = height_map.get(TARGET_QUALITY, 1080)
        DYNAMIC_QUALITY_STRING = f"bestvideo[height<={target_height}]+bestaudio/best[height<={target_height}]/best"
        ydl_opts.extend(['--format', DYNAMIC_QUALITY_STRING, '--merge-output-format', 'mp4'])

    if PLATFORM == 'youtube':
        ydl_opts.extend(['--js-runtimes', 'deno'])
        yt_extractor_args = f'getpot_bgutil_baseurl={POT_URL}'
        if GET_COMMENTS:
            ydl_opts.extend(['--write-info-json', '--write-comments'])
            yt_extractor_args += ';max-comments=100'
        ydl_opts.extend(['--extractor-args', f'youtube:{yt_extractor_args}'])

    if os.path.exists('cookies.txt') and os.path.getsize('cookies.txt') > 0:
        ydl_opts.extend(['--cookies', 'cookies.txt'])
        
    if TRIM_START and TRIM_END: 
        ydl_opts.extend(['--download-sections', f"*{TRIM_START}-{TRIM_END}", '--force-keyframes-at-cuts'])
        
    ydl_opts.append(URL)
    error_log = ""
    try:
        process = subprocess.Popen(ydl_opts, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, bufsize=1)
        last_update = time.time()
        for line in process.stdout:
            print(line, end="", flush=True)
            error_log += line + "\n"
        process.wait()
    except Exception as e:
        return update_progress(f"❌ خطا در اجرای سیستم دانلود:\n`{e}`")
    
    all_files = glob.glob("TCO_DL_*.*")
    valid_exts = ['.mp4', '.webm', '.mkv', '.mp3', '.m4a', '.jpg', '.jpeg', '.png', '.webp']
    media_files = [f for f in all_files if Path(f).suffix.lower() in valid_exts]
    
    if not media_files:
        error_lower = error_log.lower()
        if "this account does not have any videos" in error_lower: 
            return update_progress("❌ این پیج تیک‌تاک هیچ ویدیویی ندارد (یا پرایوت است).")
        elif "unable to extract secondary user id" in error_lower: 
            return update_progress("❌ تیک‌تاک اجازه دریافت اطلاعات این پیج را نداد.")
        elif "unsupported url" in error_lower: 
            return update_progress("❌ آیدی یا لینک وارد شده نامعتبر است.")
        elif "404" in error_lower or "not found" in error_lower: 
            return update_progress("❌ پیج یا پست مورد نظر یافت نشد (ممکن است حذف شده باشد).")
        elif "login" in error_lower or "sign in" in error_lower or "400" in error_lower: 
            return update_progress("❌ پلتفرم اجازه دانلود نداد (نیاز به لاگین یا تعویض کوکی).")
        elif "private" in error_lower: 
            return update_progress("❌ این پیج پرایوت (Private) است و بدون لاگین قابل دانلود نیست.")
        elif "finished downloading playlist" in error_lower: 
            return update_progress("❌ ربات پست را پیدا کرد، اما شبکه اجتماعی جلوی استخراج فایل را گرفت.")
        else: 
            return update_progress(f"❌ رسانه‌ای یافت نشد. خطای سیستم:\n`{error_log[-200:]}`")


    info_json_files_for_comments = glob.glob("TCO_DL_*.info.json")
    desc_files = glob.glob("TCO_DL_*.description")
    if GET_DESC and desc_files:
        with open(desc_files[0], 'r', encoding='utf-8') as f:
            description_text = f.read().strip()
        if description_text:
            if len(description_text) > 3000:
                description_text = description_text[:3000] + "\n\n[متن خلاصه شد...]"
            send_text_to_telegram(f"📝 **توضیحات:**\n\n{description_text}")

    # GET_COMMENTS was fetching comments into the info.json via yt-dlp but nothing
    # ever read them back out and sent them -- the checkbox did nothing before
    # this. Sent as a .txt file rather than a chat message: a message caps out
    # at 4096 chars (a handful of comments), a file can hold all of them.
    if GET_COMMENTS and info_json_files_for_comments:
        try:
            import json as _json_c
            with open(info_json_files_for_comments[0], 'r', encoding='utf-8') as f:
                _meta_c = _json_c.load(f)
            comments = [c for c in (_meta_c.get('comments') or []) if not c.get('parent') or c.get('parent') == 'root']
            comments.sort(key=lambda c: c.get('like_count') or 0, reverse=True)
            if comments:
                lines = [f"کامنت‌های ویدیو ({len(comments)} کامنت)\n" + "=" * 40 + "\n"]
                for c in comments:
                    author = (c.get('author') or 'کاربر').strip()
                    text = (c.get('text') or '').strip()
                    likes = c.get('like_count') or 0
                    lines.append(f"{author}  ({likes} لایک)\n{text}\n")
                comments_path = "TCO_DL_comments.txt"
                with open(comments_path, 'w', encoding='utf-8') as f:
                    f.write("\n".join(lines))
                with open(comments_path, 'rb') as f:
                    requests.post(
                        f"https://tapi.bale.ai/bot{BOT_TOKEN}/sendDocument",
                        data={'chat_id': CHAT_ID, 'caption': f"💬 {len(comments)} کامنت برتر"},
                        files={'document': ('comments.txt', f, 'text/plain')},
                        timeout=30, verify=False
                    )
        except Exception:
            traceback.print_exc()

    video_audio = [f for f in media_files if Path(f).suffix.lower() in ['.mp4', '.webm', '.mkv', '.mp3', '.m4a']]
    images = [f for f in media_files if Path(f).suffix.lower() in ['.jpg', '.jpeg', '.png', '.webp']]
    final_upload_list = video_audio if video_audio else images

    # Real title/duration for the Telegram video player (was showing the raw
    # TCO_DL_001 filename and a black 00:00 cover before this).
    video_meta = {}
    info_json_files = glob.glob("TCO_DL_*.info.json")
    if info_json_files:
        try:
            import json as _json
            with open(info_json_files[0], 'r', encoding='utf-8') as f:
                _meta = _json.load(f)
            video_meta['title'] = (_meta.get('title') or '').strip()
            video_meta['duration'] = int(_meta.get('duration') or 0)
            video_meta['width'] = _meta.get('width')
            video_meta['height'] = _meta.get('height')
        except Exception:
            pass
    thumb_path = images[0] if images else None
    # Telegram rejects/ignores thumbs that aren't a small JPEG (<=320px, <200KB);
    # yt-dlp writes the full-res source image, so shrink a copy for send_video.
    if thumb_path:
        try:
            small_thumb = 'TCO_DL_thumb_small.jpg'
            subprocess.run(
                ['ffmpeg', '-y', '-i', thumb_path, '-vf', 'scale=320:-2',
                 '-qscale:v', '5', small_thumb],
                capture_output=True, timeout=15
            )
            if os.path.exists(small_thumb) and os.path.getsize(small_thumb) > 0:
                thumb_path = small_thumb
        except Exception:
            pass

    if GET_THUMBNAIL and video_audio and images:
        try:
            with open(images[0], 'rb') as photo_f:
                _SESSION.post(
                    f"https://tapi.bale.ai/bot{BOT_TOKEN}/sendPhoto", 
                    data={'chat_id': CHAT_ID, 'caption': "🖼 کاور ویدیو"}, 
                    files={'photo': photo_f}, 
                    timeout=20,
                    verify=False
                )
        except Exception:
            pass

    if MESSAGE_ID: 
        try:
            _SESSION.post(f"https://tapi.bale.ai/bot{BOT_TOKEN}/deleteMessage", json={'chat_id': CHAT_ID, 'message_id': MESSAGE_ID}, timeout=10, verify=False)
        except Exception:
            pass
            
    try:
        resp = _SESSION.post(f"https://tapi.bale.ai/bot{BOT_TOKEN}/sendMessage", json={'chat_id': CHAT_ID, 'text': "⬆️ در حال آپلود..."}, timeout=10, verify=False)
        new_msg_id = resp.json()['result']['message_id']
    except Exception: 
        new_msg_id = MESSAGE_ID 
    
    final_upload_list.sort()
    final_upload_list = split_for_bale(final_upload_list, video_meta)
    asyncio.run(upload_massive_file(final_upload_list, new_msg_id, video_meta=video_meta, thumb_path=thumb_path))

if __name__ == "__main__":
    main()
