# 🔥 BULLETPROOF HIGH-PERFORMANCE EVENT LOOP FIX: MUST BE LINES 1-8. DO NOT MOVE THIS.
import asyncio
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import re
import html
import json
import base64
import logging
import threading
import concurrent.futures
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import glob
import shutil
import time
import random
import hashlib
import subprocess
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask
from pyrogram import Client, filters, idle, StopPropagation, raw, utils
from pyrogram.file_id import FileId
from pyrogram.enums import ParseMode, ChatMemberStatus, ChatType
from pyrogram.errors import UserNotParticipant
import pyrogram.utils as _pyro_utils
from modules.menus import (
    get_main_keyboard as get_revamp_main_keyboard,
    get_youtube_keyboard, get_youtube_search_keyboard,
    get_ig_tiktok_keyboard, get_reddit_twitter_keyboard,
    get_music_hub_keyboard, get_image_platform_keyboard,
    get_music_tools_keyboard, get_image_tools_keyboard,
    get_tools_picker_keyboard, get_rega_search_selector
)
from modules.tools_router import (
    handle_tool_command, handle_music_tool_callback, handle_image_tool_callback
)
import modules.music_tools as mtools
import modules.image_tools as itools
import modules.rega_music as rega_music


# Pyrogram 2.0.106 hardcodes MIN_CHANNEL_ID = -1002147483647, but Telegram now
# issues channel ids beyond it (e.g. -1004378906662). Anything outside the range
# fails with "Peer id invalid", which silently broke uploads to channels AND the
# music cache channel copy. Widen the bound to cover modern ids.
if getattr(_pyro_utils, "MIN_CHANNEL_ID", 0) > -1009999999999:
    _pyro_utils.MIN_CHANNEL_ID = -1009999999999


# Pyrogram 2.0.106 only builds inline_message_id from the old 32-bit
# InputBotInlineMessageID. Telegram now sends InputBotInlineMessageID64 for
# almost every chosen inline result, so cir.inline_message_id was always None
# and the "downloading..." inline message could never be replaced with the
# audio. Pack the 64-bit variant the same way utils.unpack_inline_message_id
# (which already understands it) expects.
def _patch_chosen_inline_result():
    import struct
    from base64 import b64encode
    from pyrogram.types import ChosenInlineResult as _CIR
    _orig_parse = _CIR._parse

    @staticmethod
    def _parse(client, chosen, users):
        res = _orig_parse(client, chosen, users)
        mid = getattr(chosen, "msg_id", None)
        if res.inline_message_id is None and isinstance(mid, raw.types.InputBotInlineMessageID64):
            res.inline_message_id = b64encode(
                struct.pack("<iqiq", mid.dc_id, mid.owner_id, mid.id, mid.access_hash), b"-_"
            ).decode().rstrip("=")
        return res

    _CIR._parse = _parse


_patch_chosen_inline_result()

# Every local-file send_audio() now writes tags/cover into the file and names
# it "Artist - Title.ext" (see modules/audio_meta.py).
from modules import audio_meta
audio_meta.install_send_audio_patch(Client)
from pyrogram.types import (InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message,
                            InputMediaPhoto, InputMediaVideo, InputMediaAudio, InlineQuery,
                            InlineQueryResultCachedAudio, InlineQueryResultArticle,
                            InlineQueryResultPhoto, InlineQueryResultAudio, InputTextMessageContent,
                            InlineQueryResultCachedPhoto,
                            ChosenInlineResult)
from pymongo import MongoClient
from bson import ObjectId
import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget
try:
    import mutagen
    from mutagen.id3 import ID3, TIT2, TPE1, TALB, TRCK, TDRC, APIC, ID3NoHeaderError
    from mutagen.mp4 import MP4, MP4Cover
    from mutagen.flac import Picture
except ImportError:
    mutagen = None

# --- COOKIE FILE SETUP ---
# Single shared "cookies.txt", written once at boot from env vars. Used by
# bot.py's own in-process yt-dlp calls (music/IG/TikTok) AND by worker.py /
# ai_worker.py, which now run as local subprocesses of this same process and
# share its working directory (they used to get their own copy of this file
# from GitHub Actions secrets in a separate "Create cookies file" step).
YOUTUBE_COOKIES_FILE = "cookies.txt"
INSTAGRAM_COOKIES_FILE = "ig_cookies.txt"
_yt_cookies_raw = os.environ.get("YOUTUBE_COOKIES", "")
_ig_cookies_raw = os.environ.get("IG_COOKIES", "")
if _yt_cookies_raw:
    with open(YOUTUBE_COOKIES_FILE, "w", encoding="utf-8") as f:
        f.write(_yt_cookies_raw.replace('\\n', '\n').replace('\r', '') + "\n")
if _ig_cookies_raw:
    with open(INSTAGRAM_COOKIES_FILE, "w", encoding="utf-8") as f:
        f.write(_ig_cookies_raw.replace('\\n', '\n').replace('\r', '') + "\n")

# Without valid cookies YouTube answers "Sign in to confirm you're not a bot" on
# datacenter IPs, so make the file's real state visible instead of guessing.
try:
    _ck_size = os.path.getsize(YOUTUBE_COOKIES_FILE) if os.path.exists(YOUTUBE_COOKIES_FILE) else 0
    _ck_lines = _ck_cookies = 0
    if _ck_size:
        with open(YOUTUBE_COOKIES_FILE, encoding="utf-8", errors="replace") as _f:
            for _line in _f:
                _ck_lines += 1
                if _line.strip() and not _line.startswith("#"):
                    _ck_cookies += 1
    print(f"🍪 YouTube cookies: {_ck_size} bytes / {_ck_lines} lines / {_ck_cookies} cookie rows"
          + ("" if _ck_cookies else "  ← UNUSABLE: YouTube will reject as a bot"), flush=True)
except Exception as _e:
    print(f"🍪 cookie check failed: {_e}", flush=True)

# Reddit gates its .json/.rss endpoints (and even oauth.reddit.com) behind a
# bot-check for anonymous/datacenter traffic -- confirmed live: a plain
# request 403s even on robots.txt, but the exact same request with a real
# logged-in account's cookies goes straight through. Separate file/jar from
# YouTube's since this is consumed via requests/http.cookiejar, not yt-dlp.
REDDIT_COOKIES_FILE = "reddit_cookies.txt"
_reddit_cookies_raw = os.environ.get("REDDIT_COOKIES", "")
if _reddit_cookies_raw:
    with open(REDDIT_COOKIES_FILE, "w", encoding="utf-8") as f:
        f.write(_reddit_cookies_raw.replace('\\n', '\n').replace('\r', '') + "\n")
REDDIT_COOKIE_JAR = None
if os.path.exists(REDDIT_COOKIES_FILE) and os.path.getsize(REDDIT_COOKIES_FILE) > 0:
    try:
        import http.cookiejar
        REDDIT_COOKIE_JAR = http.cookiejar.MozillaCookieJar(REDDIT_COOKIES_FILE)
        REDDIT_COOKIE_JAR.load(ignore_discard=True, ignore_expires=True)
        print(f"🍪 Reddit cookies loaded: {len(list(REDDIT_COOKIE_JAR))} cookies", flush=True)
    except Exception as _e:
        print(f"🍪 Reddit cookie load failed: {_e}", flush=True)
REDDIT_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# ---------------- 1. FLASK WEB SERVER ----------------
# Silence noisy HTTP access logs from 30s Docker health checks
logging.getLogger('werkzeug').setLevel(logging.ERROR)
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is alive and running!", 200

@app.route('/_selftest')
def selftest():
    """Runs one downloader/search in place on this host and reports what it
    produced, without sending anything to Bale. Locked behind SELFTEST_KEY;
    disabled when that is unset."""
    from flask import request, jsonify
    key = os.environ.get("SELFTEST_KEY", "")
    if not key or request.args.get("key") != key:
        return "not found", 404
    fn_name = request.args.get("fn", "")
    allowed = {"download_instagram_native_sync", "download_tiktok_native_sync", "download_source_audio_sync",
               "spotify_search_tracks_sync", "ytm_search_sync", "sc_search_sync", "pinterest_pin_media_sync",
               "pinterest_search_sync", "reddit_resolve_post_sync", "reddit_search_sync", "twitter_fetch_sync",
               "pexels_search_sync", "lrclib_lookup", "enrich_meta_sync", "resolve_youtube_id_sync",
               "selftest_music_timing", "download_url_to_file_sync", "instagram_embed_sync", "download_generic_sync",
               "wikimedia_search_sync", "fetch_full_image_sync", "instagram_embed_probe", "yt_client_timing"}
    if fn_name not in allowed:
        return jsonify({"error": "fn not allowed", "allowed": sorted(allowed)}), 400
    args = json.loads(request.args.get("args", "[]"))
    if fn_name in ("download_source_audio_sync", "enrich_meta_sync", "resolve_youtube_id_sync"):
        args = [make_meta(**args[0])]
    t0 = time.time()
    try:
        res = globals()[fn_name](*args)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"[:1500],
                        "trace": traceback.format_exc()[-1500:], "sec": round(time.time() - t0, 1)})
    found = []

    def walk(v):
        if isinstance(v, str) and os.path.isfile(v):
            found.append({"path": v, "bytes": os.path.getsize(v)})
            try:
                os.remove(v)
            except OSError:
                pass
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
    walk(res)
    return jsonify({"ok": True, "sec": round(time.time() - t0, 1), "files": found,
                    "result": repr(res)[:1500]})


def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), use_reloader=False)

# Reusable persistent HTTP session with connection pooling for Telegram Bot API calls
_TG_SESSION = requests.Session()
_tg_retries = Retry(total=3, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
_tg_adapter = HTTPAdapter(pool_connections=15, pool_maxsize=30, max_retries=_tg_retries)
_TG_SESSION.mount("https://", _tg_adapter)
_TG_SESSION.mount("http://", _tg_adapter)

# Dedicated thread pool executor for non-blocking database logging and user touches
_DB_BG_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="db_bg_worker")

# ---------------- 2. PYROGRAM & API CONFIGURATION ----------------
API_ID = int(os.environ.get("API_ID", 2040))
API_HASH = os.environ.get("API_HASH", "b18441a1ff607e10a989891a5462e627")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")

# Numeric id of this bot (the part before ':' in the token). Cached file_ids are
# scoped to it, because Telegram will not accept another bot's file_id.
BOT_ID = str(BOT_TOKEN or "").split(":")[0] or "unknown"

# Defined here (not with the subscription code) because the help pages below
# quote it, and they are built at import time.
MAX_SUBS_PER_USER = 15
MAX_PLAYLISTS_PER_USER = 20
MAX_TRACKS_PER_PLAYLIST = 300
PLAYLIST_PAGE_SIZE = 8

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "YOUR_YT_API_KEY")
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")
AUDD_API_KEY = os.environ.get("AUDD_API_KEY", "")

# Heavy jobs (full YouTube video downloads, Whisper subtitles, Demucs karaoke)
# used to be dispatched to GitHub Actions runners. On this VPS they run as
# local subprocesses of worker.py / ai_worker.py instead — same scripts,
# same env-var interface, just launched here instead of over the GitHub API.
# The semaphore caps how many run at once so a 2-core/2GB box doesn't get
# multiple yt-dlp/Whisper/Demucs jobs fighting for RAM simultaneously; extra
# jobs simply queue and start as soon as a slot frees up.
APP_DIR = os.path.dirname(os.path.abspath(__file__))
WORKER_SCRIPT = os.path.join(APP_DIR, "worker.py")
AI_WORKER_SCRIPT = os.path.join(APP_DIR, "ai_worker.py")
try:
    MAX_CONCURRENT_HEAVY_JOBS = max(1, int(os.environ.get("MAX_CONCURRENT_HEAVY_JOBS", "1")))
except ValueError:
    MAX_CONCURRENT_HEAVY_JOBS = 1
_HEAVY_JOB_SLOTS = threading.Semaphore(MAX_CONCURRENT_HEAVY_JOBS)

MONGO_URI = os.environ.get("MONGO_URI", "")
POT_PROVIDER_URL = os.environ.get("POT_PROVIDER_URL", "http://127.0.0.1:4416")
MUSIC_CACHE_CHANNEL = os.environ.get("MUSIC_CACHE_CHANNEL", "")

# Telegram user id of the owner. Everything under /admin is gated on this, so
# with it unset the panel is simply unreachable (fail closed). Users can find
# their own id with /id.
try:
    ADMIN_ID = int(os.environ.get("ADMIN_ID", "0") or 0)
except ValueError:
    ADMIN_ID = 0

# Seeds the forced-join list on first boot; after that it is managed from the
# panel and stored in MongoDB. These two env vars existed but were never read.
REQUIRED_CHANNEL = os.environ.get("REQUIRED_CHANNEL", "").strip()
CHANNEL_INVITE_LINK = os.environ.get("CHANNEL_INVITE_LINK", "").strip()
# Comma/space separated seed list; accepts @names or private t.me/+HASH links.
FORCE_CHANNELS_ENV = os.environ.get("FORCE_CHANNELS", "").strip()


def is_admin(user_id) -> bool:
    return bool(ADMIN_ID) and int(user_id or 0) == ADMIN_ID


def is_maintenance_allowed(chat_id) -> bool:
    """VIP bypass for maintenance mode -- managed from the admin panel."""
    try:
        allowlist = get_settings().get("maintenance_allowlist") or []
        return int(chat_id) in allowlist
    except Exception:
        return False


def is_force_join_exempt(chat_id) -> bool:
    """VIP bypass for the force-join gate -- these users never see the 'join
    these channels' prompt at all, managed from the admin panel."""
    try:
        allowlist = get_settings().get("force_join_allowlist") or []
        return int(chat_id) in allowlist
    except Exception:
        return False


def is_smallz(chat_id) -> bool:
    if smallz_col is None:
        return False
    try:
        return smallz_col.find_one({"chat_id": int(chat_id)}, {"_id": 1}) is not None
    except Exception:
        return False

# workers defaults to 6 on a 2-core VPS, bottlenecking concurrent traffic.
# Setting workers=32 eliminates queuing delays for concurrent user messages.
# max_concurrent_transmissions=8 enables high-throughput streaming.
bot = Client("main_listener_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN,
             workers=32, max_concurrent_transmissions=8)

# In-memory fast cache to guarantee instant responses
LOCAL_CACHE = {}
# lyrics_token -> {title, artist}; lets the Lyrics button work without MongoDB
LOCAL_META = {}

try:
    if MONGO_URI:
        # Render cold starts sometimes race DNS/Atlas; a single failure here
        # used to leave the bot without a database until the next restart.
        for _attempt in range(5):
            try:
                db_client = MongoClient(
                    MONGO_URI,
                    maxPoolSize=50,
                    minPoolSize=5,
                    maxIdleTimeMS=45000,
                    serverSelectionTimeoutMS=5000,
                    connectTimeoutMS=5000,
                    socketTimeoutMS=10000
                )
                db_client.admin.command('ping')
                break
            except Exception as _pe:
                if _attempt == 4:
                    raise
                print(f"MongoDB not reachable yet ({str(_pe)[:80]}); retrying...", flush=True)
                time.sleep(3 * (_attempt + 1))
        _db = db_client[os.environ.get("MONGO_DB_NAME", "telebot_db")]
        music_cache = _db.music_cache
        history_col = _db.history
        subs_col = _db.subscriptions
        prefs_col = _db.prefs
        users_col = _db.users
        activity_col = _db.activity
        settings_col = _db.settings
        support_col = _db.support
        bot_chats_col = _db.bot_chats
        smallz_col = _db.smallz
        playlists_col = _db.playlists
        try:
            music_cache.create_index("vid_id")
            music_cache.create_index("lyrics_key")
            music_cache.create_index("search_text")
            for _alias in ("spotify_id", "deezer_id", "sc_id", "isrc", "tag_v"):
                music_cache.create_index(_alias)
            history_col.create_index([("chat_id", 1), ("ts", -1)])
            subs_col.create_index([("chat_id", 1), ("platform", 1), ("target", 1)])
            users_col.create_index("chat_id", unique=True)
            activity_col.create_index([("ts", -1)])
            activity_col.create_index([("chat_id", 1), ("ts", -1)])
            # Keep the activity log from growing without bound on a free cluster.
            activity_col.create_index("at", expireAfterSeconds=90 * 24 * 3600)
            settings_col.create_index("key", unique=True)
            support_col.create_index([("ts", -1)])
            bot_chats_col.create_index("chat_id", unique=True)
            smallz_col.create_index("chat_id", unique=True)
            playlists_col.create_index([("chat_id", 1), ("created_at", 1)])
        except Exception as ie:
            print(f"Index warning: {ie}", flush=True)
        print("✅ MongoDB connected successfully!", flush=True)
    else:
        music_cache = history_col = subs_col = prefs_col = None
        users_col = activity_col = settings_col = support_col = None
        bot_chats_col = smallz_col = playlists_col = None
except Exception as e:
    print(f"❌ MongoDB Error: {e}", flush=True)
    music_cache = history_col = subs_col = prefs_col = None
    users_col = activity_col = settings_col = support_col = None
    bot_chats_col = smallz_col = playlists_col = None

# ---------------- 0.5 I18N ----------------
# Every user-facing string (admin panel excluded -- single Farsi-speaking
# owner, no need) is keyed here rather than hardcoded inline, so the bot can
# speak Farsi/English/Russian/Spanish per-user. STRINGS grows through the
# file; this block only holds the lookup machinery + the per-user language
# store, since users_col must already exist above for get_lang to work.
SUPPORTED_LANGS = {"fa": "🇮🇷 فارسی", "en": "🇬🇧 English", "ru": "🇷🇺 Русский", "es": "🇪🇸 Español"}
DEFAULT_LANG = "fa"
LANG_CACHE = {}  # chat_id -> lang; avoids a Mongo read on every single message


def get_lang(chat_id) -> str:
    key = int(chat_id)
    if key in LANG_CACHE:
        return LANG_CACHE[key]
    lang = DEFAULT_LANG
    if users_col is not None:
        try:
            doc = users_col.find_one({"chat_id": key}, {"lang": 1})
            if doc and doc.get("lang") in SUPPORTED_LANGS:
                lang = doc["lang"]
        except Exception as e:
            print(f"get_lang error: {e}", flush=True)
    LANG_CACHE[key] = lang
    return lang


def set_lang(chat_id, lang: str):
    if lang not in SUPPORTED_LANGS:
        return
    LANG_CACHE[int(chat_id)] = lang
    if users_col is not None:
        try:
            users_col.update_one({"chat_id": int(chat_id)}, {"$set": {"lang": lang}}, upsert=True)
        except Exception as e:
            print(f"set_lang error: {e}", flush=True)


STRINGS = {}  # populated throughout the file: {key: {"fa": ..., "en": ..., "ru": ..., "es": ...}}


def t(chat_id, key: str, **kwargs) -> str:
    """Look up one string in the caller's language, falling back to Farsi,
    then to any language that has it, then to the bare key (so a missing
    translation is visibly wrong rather than silently crashing a handler)."""
    entry = STRINGS.get(key)
    if not entry:
        return key
    lang = get_lang(chat_id)
    text = entry.get(lang) or entry.get(DEFAULT_LANG) or next(iter(entry.values()), key)
    if "{name}" in text and "name" not in kwargs:
        u = user_data.get(chat_id) or {} if "user_data" in globals() else {}
        first_name = u.get("first_name") or ("کاربر" if lang == "fa" else "friend")
        kwargs["name"] = first_name
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            try:
                formatted = text
                for k, v in kwargs.items():
                    formatted = formatted.replace(f"{{{k}}}", str(v))
                return re.sub(r'\{[a-zA-Z0-9_]+\}', '', formatted)
            except Exception:
                return text
    elif "{name}" in text:
        u = user_data.get(chat_id) or {} if "user_data" in globals() else {}
        first_name = u.get("first_name") or ("کاربر" if lang == "fa" else "friend")
        return text.replace("{name}", first_name)
    return text

# The Render instance has ~0.1 CPU, so unbounded parallel downloads would thrash
# it and starve every other user. This env var was already configured but unused.
try:
    MAX_CONCURRENT_MUSIC = max(1, int(os.environ.get("MAX_CONCURRENT_MUSIC_DOWNLOADS", "2")))
except ValueError:
    MAX_CONCURRENT_MUSIC = 2
MUSIC_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_MUSIC)

os.makedirs("music_downloads", exist_ok=True)
os.makedirs("media_downloads", exist_ok=True)

# --- BULLETPROOF CACHE & CHANNEL ARCHIVE HELPERS ---
def send_to_cache_channel_http(file_id: str, caption_text: str):
    """Archive to the cache channel and return the archived message_id.

    The message_id matters more than the file_id: file_ids are bound to the bot
    that created them, so another bot sharing this database gets MEDIA_EMPTY
    when it tries to reuse one. A message_id can be copy_message'd by any bot
    that can read the channel.
    """
    if not MUSIC_CACHE_CHANNEL or not BOT_TOKEN:
        return None
    try:
        cid = str(MUSIC_CACHE_CHANNEL).strip()
        r = _TG_SESSION.post(
            f"https://tapi.bale.ai/bot{BOT_TOKEN}/sendAudio",
            json={
                "chat_id": cid,
                "audio": file_id,
                "caption": caption_text
            },
            timeout=15
        )
        data = r.json()
        if data.get("ok"):
            return data["result"]["message_id"]
        print(f"Channel Archive rejected: {str(data)[:160]}", flush=True)
    except Exception as e:
        print(f"Channel Archive Exception: {e}", flush=True)
    return None

def _norm(s: str) -> str:
    # Unicode-aware: the old [^a-z0-9] stripped every Persian/Arabic/CJK
    # letter, so non-Latin titles normalised to "" and could never match.
    return re.sub(r'[\W_]+', '', (s or '').lower())

DIRTY_ARTIST_RE = re.compile(r'(Music|World|VEVO|Topic|Records|Channel|Official)$', re.IGNORECASE)
DIRTY_TITLE_RE = re.compile(r'(Explicit HD|Official Video|Official Music Video|Live at|Wembley|MTV Music Awards|Clean Version)', re.IGNORECASE)

# Cache documents written by the unified track pipeline carry tag_v. Anything
# older was uploaded without tags/cover/file name inside the file (and often
# with YouTube-channel metadata like "EminemMusic"), so it is treated as a
# miss and rebuilt once, in place, the next time somebody asks for it.
TAG_VERSION = 2


def is_clean_cache_doc(doc: dict, expected_artist: str = None, expected_album: str = None) -> bool:
    """True when a cache document is a current-format, fully tagged file."""
    if not doc or int(doc.get("tag_v") or 0) < TAG_VERSION:
        return False
    if expected_artist:
        ea, da = _norm(expected_artist), _norm(doc.get("artist") or "")
        if ea and da and ea not in da and da not in ea:
            return False
    if expected_album:
        eal, dal = _norm(expected_album), _norm(doc.get("album") or "")
        if eal and dal and eal != dal:
            return False
    return True


def cache_doc(cache_key: str):
    """Raw cache document for a key, or None."""
    if music_cache is None:
        return None
    try:
        return music_cache.find_one({"vid_id": str(cache_key)})
    except Exception as e:
        print(f"Cache Lookup Error: {e}", flush=True)
        return None


def doc_file_id(doc: dict):
    """file_id from a cache doc that THIS bot can send (file_ids are per-bot)."""
    if not doc:
        return None
    own = (doc.get("file_ids") or {}).get(BOT_ID)
    if own:
        return own
    if doc.get("file_id") and str(doc.get("owner_bot") or BOT_ID) == BOT_ID:
        return doc["file_id"]
    return None


def get_cached_file(cache_key: str, expected_artist: str = None, expected_album: str = None):
    """file_id usable by THIS bot for a current-format cache entry, or None."""
    doc = cache_doc(cache_key)
    if not is_clean_cache_doc(doc, expected_artist=expected_artist, expected_album=expected_album):
        LOCAL_CACHE.pop(cache_key, None)
        return None
    fid = doc_file_id(doc)
    if fid:
        LOCAL_CACHE[cache_key] = fid
    return fid


async def send_cached_doc(client: Client, chat_id: int, doc: dict, reply_markup=None, caption: str = "") -> bool:
    """Send a cache document's audio: own file_id first, archive-channel copy second.

    A successful copy teaches this bot its own file_id for next time.
    """
    if not doc:
        return False
    fid = doc_file_id(doc)
    msg_id = doc.get("cache_msg_id")
    # Bale drops the cover when a file is re-sent by file_id ("thumbnails
    # cannot be reused"), but a copy of the archived message keeps it.
    if msg_id and MUSIC_CACHE_CHANNEL:
        try:
            await client.copy_message(chat_id, int(str(MUSIC_CACHE_CHANNEL).strip()), int(msg_id),
                                      caption=caption, reply_markup=reply_markup)
            return True
        except Exception as e:
            print(f"archive copy failed for {doc.get('vid_id')}: {e}; trying file_id", flush=True)
    if fid:
        try:
            await client.send_audio(chat_id, audio=fid, caption=caption, reply_markup=reply_markup)
            return True
        except Exception as e:
            print(f"cached file_id failed ({e}); trying archive channel", flush=True)
            LOCAL_CACHE.pop(doc.get("vid_id"), None)
    if msg_id and MUSIC_CACHE_CHANNEL:
        try:
            sent = await client.copy_message(chat_id, int(str(MUSIC_CACHE_CHANNEL).strip()), int(msg_id),
                                             caption=caption, reply_markup=reply_markup)
            media = sent.audio or sent.document
            if media is not None and music_cache is not None:
                music_cache.update_one({"_id": doc["_id"]}, {"$set": {f"file_ids.{BOT_ID}": media.file_id}})
            return True
        except Exception as e:
            print(f"archive copy failed for {doc.get('vid_id')}: {e}", flush=True)
    return False


async def deliver_cached_track(client: Client, chat_id: int, cache_key: str,
                              caption: str, reply_markup=None,
                              expected_artist: str = None) -> bool:
    """Send a current-format cached track by key. False = caller must (re)build it."""
    doc = cache_doc(cache_key)
    if not is_clean_cache_doc(doc, expected_artist=expected_artist):
        return False
    return await send_cached_doc(client, chat_id, doc, reply_markup, caption)


def lyrics_token(cache_key: str) -> str:
    """Stable short token for callback_data (Telegram caps callback_data at 64 bytes).

    The old code sliced the key to 10 chars, but YouTube IDs are 11 — so the
    Lyrics lookup could never match the stored vid_id and always reported
    "song info not found". SoundCloud keys can be full URLs, hence the hash.
    """
    key = str(cache_key)
    # Also used in t.me/<bot>?start=opt_<token>, which only allows [A-Za-z0-9_-].
    if len(key) <= 40 and re.fullmatch(r"[A-Za-z0-9_-]+", key):
        return key
    return "h" + hashlib.md5(key.encode()).hexdigest()[:16]


def save_cached_file(cache_key: str, file_id: str, title: str, artist: str = "",
                     duration: int = 0, chat_id: int = 0, source_url: str = "",
                     album: str = "", clean_tagged: bool = False, archive: bool = False):
    """Register a derived file (effect / speed variant...) in the cache.

    Originals go through store_track_doc() instead. Derived files are only
    kept as this bot's file_id -- they are NOT posted to the archive channel
    any more, which used to fill it with "(🔊 بیس بوست)" copies.
    """
    LOCAL_CACHE[cache_key] = file_id
    token = lyrics_token(cache_key)
    LOCAL_META[token] = {"title": title, "artist": artist, "file_id": file_id,
                         "vid_id": str(cache_key), "duration": int(duration or 0),
                         "source_url": source_url, "album": album}
    if music_cache is not None:
        try:
            fields = {"file_id": file_id, "title": title, "artist": artist, "lyrics_key": token,
                      "duration": int(duration or 0), "source_url": source_url, "owner_bot": BOT_ID,
                      f"file_ids.{BOT_ID}": file_id, "search_text": f"{title} {artist}".lower(),
                      "tag_v": TAG_VERSION, "derived": True}
            if album:
                fields["album"] = album
            if archive:
                mid = send_to_cache_channel_http(file_id, f"🎵 {title}\n👤 {artist}")
                if mid:
                    fields["cache_msg_id"] = mid
            music_cache.update_one({"vid_id": str(cache_key)}, {"$set": fields, "$inc": {"hits": 1}}, upsert=True)
        except Exception as e:
            print(f"Cache Save Error: {e}", flush=True)
    if chat_id:
        log_history(chat_id, cache_key, title, artist)


def log_history(chat_id: int, vid_id: str, title: str, artist: str):
    """Record a download so /library, /stats and the weekly mixtape have data."""
    if history_col is None:
        return
    try:
        history_col.insert_one({
            "chat_id": int(chat_id), "vid_id": str(vid_id),
            "title": title, "artist": artist, "ts": time.time()
        })
    except Exception as e:
        print(f"History Error: {e}", flush=True)

# ---------------- 2.5 SHARED AUDIO ENGINE ----------------
# Telegram renders a track's title/artist/cover from the send_audio() parameters,
# so those display correctly even with no ffmpeg present. ffmpeg is only needed to
# (a) guarantee the file is audio-only and (b) embed tags/cover *inside* the file.
FFMPEG_BIN = shutil.which("ffmpeg")
FFMPEG_AVAILABLE = FFMPEG_BIN is not None

# 'best' stream-copies the audio: no re-encode, no quality loss, near-instant.
# Set AUDIO_FORMAT=mp3 on Render for a real MP3 re-encode (slower, slightly lossier).
AUDIO_FORMAT = os.environ.get("AUDIO_FORMAT", "best").strip().lower()

# Writing tags/cover INTO the file costs 3 extra ffmpeg passes per song, which
# is expensive on 0.1 CPU. Telegram shows the title/artist/cover from the
# send_audio parameters regardless, so this is off by default for speed.
EMBED_TAGS = os.environ.get("EMBED_TAGS", "false").strip().lower() in ("1", "true", "yes")

print(
    f"🎧 Audio engine: format={AUDIO_FORMAT} | "
    f"ffmpeg={'FOUND at ' + str(FFMPEG_BIN) if FFMPEG_AVAILABLE else 'NOT FOUND — tags/cover will not be embedded in the file'} | "
    f"node={shutil.which('node') or 'NOT FOUND (yt-dlp falls back to its slow pure-Python JS interpreter)'}",
    flush=True
)
print(
    f"🛡 Admin: {'ADMIN_ID=' + str(ADMIN_ID) if ADMIN_ID else 'NOT SET — /admin disabled and force-join stays off'} | "
    f"force-join seed: {len([t for t in FORCE_CHANNELS_ENV.replace(',', ' ').split() if t])} channel(s)",
    flush=True
)

# Per-deploy YouTube extractor overrides, e.g. to route around a host the
# platform blocks. Semicolon-separated key=value, values comma-separated:
#   innertube_host=youtubei.googleapis.com;player_client=android;player_skip=webpage,configs
YT_EXTRACTOR_ARGS = os.environ.get("YT_EXTRACTOR_ARGS", "").strip()

# Egress on some hosts (notably HF Spaces) is flaky rather than blocked: the
# same host times out once and connects fine moments later. Retry hard instead
# of failing a user's request on a single dropped handshake.
NET_RETRY_OPTS = {
    'retries': 10,
    'extractor_retries': 5,
    'fragment_retries': 10,
    'socket_timeout': 30,
}


def youtube_extractor_args():
    """Parse YT_EXTRACTOR_ARGS into yt-dlp's extractor_args shape, or None."""
    if not YT_EXTRACTOR_ARGS:
        return None
    parsed = {}
    for part in YT_EXTRACTOR_ARGS.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        vals = [v.strip() for v in val.split(",") if v.strip()]
        if key.strip() and vals:
            parsed[key.strip()] = vals
    return {"youtube": parsed} if parsed else None


def get_quality_size_labels(video_id: str) -> dict:
    """Best-effort {height: 'XXX MB'} for the quality-picker buttons.

    Sums the best video-only stream at-or-under each target height with the
    best audio-only stream, mirroring worker.py's actual
    bestvideo[height<=X]+bestaudio selector, so the estimate matches what the
    button really downloads. Returns {} on any failure -- callers fall back
    to plain quality labels with no size.
    """
    try:
        opts = {
            'quiet': True, 'no_warnings': True, 'skip_download': True,
            'js_runtimes': {'deno': {}},
            'extractor_args': {'youtube': {'getpot_bgutil_baseurl': [POT_PROVIDER_URL]}},
        }
        if os.path.exists(YOUTUBE_COOKIES_FILE) and os.path.getsize(YOUTUBE_COOKIES_FILE) > 0:
            opts['cookiefile'] = YOUTUBE_COOKIES_FILE
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        formats = info.get('formats') or []

        audio_only = [f for f in formats if f.get('vcodec') == 'none' and f.get('acodec') != 'none']
        audio_size = max(
            (f.get('filesize') or f.get('filesize_approx') or 0 for f in audio_only),
            default=0
        )

        labels = {}
        for target in (1080, 720, 480, 360):
            candidates = [f for f in formats if f.get('vcodec') != 'none' and (f.get('height') or 0) <= target]
            if not candidates:
                continue
            best = max(candidates, key=lambda f: f.get('height') or 0)
            vsize = best.get('filesize') or best.get('filesize_approx') or 0
            total = vsize + audio_size
            if total:
                labels[target] = f"{total / (1024 * 1024):.0f}MB"
        return labels
    except Exception:
        return {}


AUDIO_EXTS = ('.m4a', '.mp3', '.opus', '.ogg', '.aac', '.flac', '.wav', '.webm')
VIDEO_EXTS = ('.mp4', '.mkv', '.webm', '.mov')
IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.webp')


def build_audio_opts(outtmpl: str) -> dict:
    """yt-dlp options for music downloads.

    Never lets a muxed video satisfy the format selector. The previous
    '.../bestaudio/best' tail matched format 18 (a 360p MP4) whenever YouTube
    withheld audio-only formats, and Telegram then delivered it as a video with
    no title, artist or cover.
    """
    opts = {
        'outtmpl': outtmpl,
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'concurrent_fragment_downloads': 8,
        'http_chunk_size': 10485760,
        **NET_RETRY_OPTS,
    }
    if FFMPEG_AVAILABLE:
        # ffmpeg can strip a video stream, so a muxed fallback is recoverable here.
        opts['format'] = 'bestaudio[ext=m4a]/bestaudio/best'
        opts['ffmpeg_location'] = os.path.dirname(FFMPEG_BIN)
        # ExtractAudio alone guarantees an audio-only file and, with
        # AUDIO_FORMAT='best', is a stream copy rather than a re-encode.
        opts['postprocessors'] = [
            {'key': 'FFmpegExtractAudio', 'preferredcodec': AUDIO_FORMAT, 'preferredquality': '192'},
        ]
        if EMBED_TAGS:
            # Each of these is another ffmpeg process. Telegram renders the
            # title/artist/cover from the send_audio parameters, so embedding
            # them INSIDE the file only matters if users re-open the download
            # elsewhere — costly on a 0.1-CPU box for something usually unseen.
            opts['writethumbnail'] = True
            opts['postprocessors'] += [
                {'key': 'FFmpegThumbnailsConvertor', 'format': 'jpg'},
                {'key': 'FFmpegMetadata', 'add_metadata': True},
                {'key': 'EmbedThumbnail', 'already_have_thumbnail': False},
            ]
    else:
        # No ffmpeg: audio-only or nothing. Failing loudly beats shipping a video.
        opts['format'] = 'bestaudio[ext=m4a]/bestaudio'
    return opts


def collect_audio_file(base: str):
    """Pick the real audio file for `base`; the extension varies by codec/postprocessor.

    Deliberately excludes .mp4 so a muxed video can never be sent as a track.
    """
    for f in sorted(glob.glob(f"{base}.*")):
        if Path(f).suffix.lower() in AUDIO_EXTS:
            return f
    return None


def square_crop_jpg(path: str):
    """Centre-crop a cover to a square so Telegram shows real album art.

    YouTube thumbnails are 16:9/4:3, which is why covers rendered as rectangles.
    """
    if not FFMPEG_AVAILABLE or not path:
        return path
    out = f"{os.path.splitext(path)[0]}_sq.jpg"
    try:
        r = subprocess.run(
            [FFMPEG_BIN, '-y', '-loglevel', 'error', '-i', path,
             '-vf', 'crop=min(iw\\,ih):min(iw\\,ih),scale=320:320',
             '-frames:v', '1', out],
            capture_output=True, timeout=20
        )
        if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
            try:
                os.remove(path)
            except OSError:
                pass
            return out
    except Exception as e:
        print(f"Cover crop failed ({e}); using uncropped cover", flush=True)
    return path


def _norm(s: str) -> str:
    # Unicode-aware: the old [^a-z0-9] stripped every Persian/Arabic/CJK
    # letter, so non-Latin titles normalised to "" and could never match.
    return re.sub(r'[\W_]+', '', (s or '').lower())


def is_live_marker(s: str) -> bool:
    """Detects live concert, festival, tour, acoustic or awards markers in song titles."""
    return bool(re.search(r'\b(live|concert|tour|awards|festival|session|acoustic|wembley|tribute)\b', (s or '').lower()))


def strip_features(title: str) -> str:
    """Strips (feat. ...), (with ...), etc. to get the core track title."""
    t = re.sub(r'(?i)\s*[\(\[](feat\.|ft\.|featuring|with)[^\)\]]+[\)\]]', '', title or '')
    t = re.sub(r'(?i)\s*-\s*(feat\.|ft\.|featuring|with).*$', '', t)
    return t.strip()


def is_title_match(want_title: str, got_title: str, artist: str = "") -> bool:
    """Fuzzy yet strict matching between requested title and YouTube/catalog title."""
    w_norm = _norm(want_title)
    g_norm = _norm(got_title)
    if not w_norm or not g_norm:
        return False
    if w_norm in g_norm or g_norm in w_norm:
        return True

    # Strip features / extra info and compare core title
    w_core = _norm(strip_features(want_title))
    g_core = _norm(strip_features(got_title))
    if w_core and g_core and (w_core in g_core or g_core in w_core):
        return True

    # Check if got_title includes artist e.g. "Eminem - Title"
    if artist:
        a_norm = _norm(artist)
        g_no_artist = g_norm.replace(a_norm, "")
        if w_norm in g_no_artist or g_no_artist in w_norm:
            return True
        if w_core and (w_core in g_no_artist or g_no_artist in w_core):
            return True

    return False


def _pick_matching_result(results: list, title_hint: str, artist_hint: str = None):
    """Smart matching using clean title normalization and token comparison."""
    if not results:
        return None
    if not title_hint:
        return results[0]
    for r in results:
        got_title = r.get('title') or ''
        if is_title_match(title_hint, got_title, artist_hint or ""):
            return r
    return None


def embed_metadata(audio_path: str, title: str, artist: str, album: str = None,
                   track_num: int = None, total_tracks: int = None,
                   year: str = None, cover_path: str = None):
    """Embeds ID3/MP4 metadata and high-res cover art into audio files via mutagen."""
    if not mutagen or not audio_path or not os.path.exists(audio_path):
        return False
    ext = os.path.splitext(audio_path)[1].lower()
    try:
        if ext == '.mp3':
            try:
                tags = ID3(audio_path)
            except ID3NoHeaderError:
                tags = ID3()
            if title:
                tags['TIT2'] = TIT2(encoding=3, text=title)
            if artist:
                tags['TPE1'] = TPE1(encoding=3, text=artist)
            if album:
                tags['TALB'] = TALB(encoding=3, text=album)
            if track_num:
                trck_val = f"{track_num}/{total_tracks}" if total_tracks else str(track_num)
                tags['TRCK'] = TRCK(encoding=3, text=trck_val)
            if year:
                tags['TDRC'] = TDRC(encoding=3, text=str(year))
            if cover_path and os.path.exists(cover_path):
                try:
                    with open(cover_path, 'rb') as cf:
                        tags['APIC'] = APIC(
                            encoding=3,
                            mime='image/jpeg',
                            type=3,
                            desc='Cover',
                            data=cf.read()
                        )
                except Exception as ce:
                    print(f"Failed to embed APIC cover: {ce}", flush=True)
            tags.save(audio_path, v2_version=3)
            return True

        elif ext in ('.m4a', '.mp4'):
            mp4 = MP4(audio_path)
            if title:
                mp4['\xa9nam'] = [title]
            if artist:
                mp4['\xa9ART'] = [artist]
            if album:
                mp4['\xa9alb'] = [album]
            if track_num:
                mp4['trkn'] = [(track_num, total_tracks or 0)]
            if year:
                mp4['\xa9day'] = [str(year)]
            if cover_path and os.path.exists(cover_path):
                try:
                    with open(cover_path, 'rb') as cf:
                        mp4['covr'] = [MP4Cover(cf.read(), imageformat=MP4Cover.FORMAT_JPEG)]
                except Exception as ce:
                    print(f"Failed to embed MP4 covr: {ce}", flush=True)
            mp4.save()
            return True

        elif ext in ('.opus', '.ogg'):
            from mutagen.oggopus import OggOpus
            opus = OggOpus(audio_path)
            if title:
                opus['TITLE'] = [title]
            if artist:
                opus['ARTIST'] = [artist]
            if album:
                opus['ALBUM'] = [album]
            if track_num:
                opus['TRACKNUMBER'] = [str(track_num)]
            if year:
                opus['DATE'] = [str(year)]
            if cover_path and os.path.exists(cover_path):
                try:
                    pic = Picture()
                    with open(cover_path, 'rb') as cf:
                        pic.data = cf.read()
                    pic.type = 3
                    pic.mime = 'image/jpeg'
                    pic.desc = 'Cover'
                    opus['METADATA_BLOCK_PICTURE'] = [base64.b64encode(pic.write()).decode('ascii')]
                except Exception as ce:
                    print(f"Failed to embed Opus picture: {ce}", flush=True)
            opus.save()
            return True

    except Exception as e:
        print(f"Metadata tag error for {audio_path}: {e}", flush=True)
        return False
    return False


def fetch_album_art_url(artist: str, title: str):
    """Find real album artwork (Spotify 640x640, then iTunes, then Deezer)."""
    a_norm, t_norm = _norm(artist), _norm(title)
    if not t_norm:
        return None

    # 1. Spotify Web API (640x640 official album art)
    try:
        sp_func = globals().get('spotify_api_sync')
        if sp_func:
            j = sp_func("search", params={"q": f"track:{title} artist:{artist}", "type": "track", "limit": 1})
            if j:
                items = (j.get("tracks", {}).get("items") or [])
                if items:
                    imgs = (items[0].get("album") or {}).get("images") or []
                    if imgs and imgs[0].get("url"):
                        return imgs[0]["url"]
    except Exception:
        pass

    def acceptable(cand_artist, cand_title):
        ia, it = _norm(cand_artist), _norm(cand_title)
        if not ia or not it:
            return False
        artist_ok = (not a_norm) or ia in a_norm or a_norm in ia
        title_ok = it in t_norm or t_norm in it
        return artist_ok and title_ok

    # 2. iTunes API (600x600)
    try:
        r = requests.get("https://itunes.apple.com/search",
                         params={"term": f"{artist} {title}", "entity": "song", "limit": 5},
                         timeout=6)
        for item in (r.json().get("results") or []):
            if acceptable(item.get("artistName"), item.get("trackName")) and item.get("artworkUrl100"):
                return item["artworkUrl100"].replace("100x100bb", "600x600bb")
    except Exception:
        pass

    # 3. Deezer API
    try:
        r = requests.get("https://api.deezer.com/search",
                         params={"q": f"{artist} {title}", "limit": 5}, timeout=6)
        for item in (r.json().get("data") or []):
            if acceptable((item.get("artist") or {}).get("name"), item.get("title")):
                cover = (item.get("album") or {}).get("cover_big")
                if cover:
                    return cover
    except Exception:
        pass
    return None



def fetch_cover_jpg(info: dict, base: str, artist: str = "", title: str = ""):
    """Download a JPEG cover. Telegram ignores webp/png thumbnails, so force JPEG.

    Real album art is tried first and is already square, so it also skips the
    ffmpeg crop. maxresdefault beats hqdefault as a fallback because hqdefault
    pads a 16:9 frame into a 4:3 canvas with black bars that survive cropping.
    """
    urls = []
    art = fetch_album_art_url(artist, title) if (artist or title) else None
    if art:
        urls.append(('album', art))
    vid = info.get('id')
    if vid and 'youtube' in (info.get('extractor_key') or '').lower():
        urls.append(('still', f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg"))
        urls.append(('still', f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"))
    for t in reversed(info.get('thumbnails') or []):
        u = t.get('url') or ''
        if '.jpg' in u.lower():
            urls.append(('still', u))
    if info.get('thumbnail'):
        urls.append(('still', info['thumbnail']))

    for kind, url in urls:
        try:
            r = requests.get(url, timeout=8)
            if r.ok and r.content[:2] == b'\xff\xd8':  # JPEG magic bytes
                path = f"{base}_cover.jpg"
                with open(path, 'wb') as fh:
                    fh.write(r.content)
                if kind == 'album':
                    print(f"🎨 cover: real album art for {artist} — {title}", flush=True)
                    return path
                return square_crop_jpg(path)
        except Exception:
            continue
    return None


def normalize_media_ext(path: str) -> str:
    """Give a file a real extension based on its magic bytes.

    yt-dlp writes some thumbnails with a generic '.image' extension (TikTok does
    this). Telegram needs a recognisable one, and the extension is also how the
    photo/video split below is decided.
    """
    if Path(path).suffix.lower() in VIDEO_EXTS + IMAGE_EXTS:
        return path
    try:
        with open(path, 'rb') as fh:
            head = fh.read(12)
    except OSError:
        return path
    if head[:2] == b'\xff\xd8':
        ext = '.jpg'
    elif head[:8] == b'\x89PNG\r\n\x1a\n':
        ext = '.png'
    elif head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        ext = '.webp'
    else:
        return path
    new_path = f"{os.path.splitext(path)[0]}{ext}"
    try:
        os.replace(path, new_path)
        return new_path
    except OSError:
        return path


def best_image_url(entry: dict):
    """Pick the highest-quality still for a photo entry.

    Instagram reports no width/height on its thumbnails, and yt-dlp orders
    thumbnails worst-to-best, so fall back to trusting that ordering.
    """
    thumbs = [t for t in (entry.get('thumbnails') or []) if t.get('url')]
    if thumbs:
        sized = [t for t in thumbs if t.get('width')]
        if sized:
            return max(sized, key=lambda t: t.get('width') or 0)['url']
        return thumbs[-1]['url']
    return entry.get('thumbnail')


def fetch_entry_image(entry: dict, base: str):
    """Download a photo entry's picture directly via HTTP."""
    url = best_image_url(entry)
    if not url:
        return None
    try:
        r = requests.get(url, timeout=15)
        if not r.ok or not r.content:
            return None
        raw = f"{base}.img"
        with open(raw, 'wb') as fh:
            fh.write(r.content)
        path = normalize_media_ext(raw)
        if Path(path).suffix.lower() == '.webp':
            # Telegram's sendPhoto/InputMediaPhoto rejects WEBP outright with
            # 400 PHOTO_EXT_INVALID -- Instagram/TikTok both serve slideshow
            # stills as webp, so this hit every multi-photo post that had one.
            jpg_path = str(Path(path).with_suffix('.jpg'))
            try:
                subprocess.run([FFMPEG_BIN, '-y', '-loglevel', 'error', '-i', path, jpg_path],
                              check=True, capture_output=True, timeout=20)
                os.remove(path)
                path = jpg_path
            except Exception as e:
                print(f"webp->jpg conversion failed: {e}", flush=True)
                os.remove(path)
                return None
        if Path(path).suffix.lower() in IMAGE_EXTS:
            return path
        os.remove(path)
    except Exception as e:
        print(f"Image fetch failed: {e}", flush=True)
    return None


def assemble_post_media(info: dict, prefix: str):
    """Build the ordered file list for a post, mixing videos and photos.

    Video entries are already on disk from yt-dlp. Photo entries expose ZERO
    formats — yt-dlp's downloader raises "No video formats found!" on them — but
    they do carry the picture as thumbnail URLs, so those are fetched over plain
    HTTP instead. Matching per entry id also stops a video post's thumbnail
    being sent as a bonus photo, and ignores stale files from an earlier run.
    """
    entries = info.get('entries') or [info]
    files = []
    for idx, e in enumerate(entries):
        if not e:
            continue
        eid = str(e.get('id') or idx)
        cands = [normalize_media_ext(f) for f in sorted(glob.glob(f"{prefix}{eid}.*"))
                 if not f.endswith(('.json', '.part', '.tmp', '.ytdl'))]
        chosen = next((f for f in cands if Path(f).suffix.lower() in VIDEO_EXTS), None)
        if chosen is None:
            chosen = fetch_entry_image(e, f"{prefix}{eid}")
        if chosen is None:
            chosen = next((f for f in cands if Path(f).suffix.lower() in IMAGE_EXTS), None)
        if chosen and chosen not in files:
            files.append(chosen)
        for f in cands:
            if f != chosen and os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass
    return files


def extract_post_info(opts: dict, url: str, label: str):
    """Extract a post, tolerating photo posts that have no downloadable formats."""
    try:
        return timed_extract(opts, url, label)
    except Exception as e:
        # Photo posts fail at the DOWNLOAD stage with "No video formats found!",
        # so re-extract without downloading and let the caller fetch the images.
        print(f"{label}: no video formats ({str(e)[:80]}); retrying as a photo post", flush=True)
        with yt_dlp.YoutubeDL({**opts, 'skip_download': True}) as ydl:
            return ydl.extract_info(url, download=False)


STRINGS.update({
    "btn_edit_this_video": {"fa": "🎬 ویرایش این ویدیو", "en": "🎬 Edit This Video", "ru": "🎬 Редактировать это видео", "es": "🎬 Editar este video"},
})

async def send_post_media(client: Client, chat_id: int, files: list, caption: str = "", prefix: str = ""):
    """Send post files with NO caption attached, then the description separately.

    Keeping the text off the media is deliberate. It costs one extra small API
    call and zero extra upload time, so the media still arrives just as fast,
    and the description is no longer capped by Telegram's 1024-char caption
    limit. parse_mode is disabled because post descriptions are arbitrary user
    text and would otherwise break on stray markdown characters.
    """
    files = [f for f in files if f and os.path.isfile(f)]
    if not files:
        raise RuntimeError("no media files on disk")
    if len(files) == 1:
        path = files[0]
        if Path(path).suffix.lower() in VIDEO_EXTS:
            edit_kb = InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_edit_this_video"), callback_data="vedit_from_last")]])
            sent = await client.send_video(chat_id, video=path, reply_markup=edit_kb)
            v = sent.video or sent.document
            if v:
                user_data.setdefault(chat_id, {})["last_video_file_id"] = v.file_id
        else:
            await client.send_photo(chat_id, photo=path)
    else:
        group = []
        for path in files[:10]:
            if Path(path).suffix.lower() in VIDEO_EXTS:
                group.append(InputMediaVideo(path))
            else:
                group.append(InputMediaPhoto(path))
        await client.send_media_group(chat_id, media=group)

    text = (caption or "").strip()
    if text:
        await client.send_message(
            chat_id, f"{prefix}{text}".strip(),
            parse_mode=ParseMode.DISABLED,
            disable_web_page_preview=True
        )


def timed_extract(opts: dict, url: str, label: str):
    """Run yt-dlp and log an extract/download/postprocess breakdown.

    yt-dlp runs with quiet=True, so without this the Render logs show nothing at
    all for a download and there is no way to tell which stage is slow.
    """
    marks = {'t0': time.time(), 'dl_start': None, 'dl_end': None}

    def hook(d):
        if d.get('status') == 'downloading' and marks['dl_start'] is None:
            marks['dl_start'] = time.time()
        elif d.get('status') == 'finished':
            marks['dl_end'] = time.time()

    opts = dict(opts)
    opts['progress_hooks'] = [hook]
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    end = time.time()
    ds = marks['dl_start'] or end
    de = marks['dl_end'] or end
    print(
        f"⏱ {label}: extract={ds - marks['t0']:.1f}s "
        f"download={de - ds:.1f}s postprocess={end - de:.1f}s "
        f"TOTAL={end - marks['t0']:.1f}s",
        flush=True
    )
    return info


# ---------------- 3. SOUNDCLOUD ENGINE (RENDER NATIVE) ----------------
def download_sc_sync(url):
    opts = build_audio_opts('music_downloads/%(id)s.%(ext)s')
    info = timed_extract(opts, url, "soundcloud")
    base = f"music_downloads/{info['id']}"
    title = info.get('title') or 'Unknown Title'
    artist = info.get('uploader') or 'Unknown Artist'
    audio_path = collect_audio_file(base)
    claimed_duration = int(info.get('duration') or 0)

    # SoundCloud Go+ (DRM) tracks don't raise an error -- yt-dlp silently
    # succeeds with a ~30s preview clip while the metadata still reports the
    # song's real full length. Compare the actual downloaded audio against
    # what was claimed; a big mismatch means this was a preview, not the
    # real track, and should be treated as a failure so the caller's
    # DRM-fallback (fetch the same song from the YouTube-music engine) kicks in.
    if audio_path and claimed_duration > 50:
        try:
            probe = subprocess.run(
                ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                 '-of', 'default=noprint_wrappers=1:nokey=1', audio_path],
                capture_output=True, text=True, timeout=15
            )
            real_duration = float(probe.stdout.strip() or 0)
        except Exception:
            real_duration = claimed_duration  # probe failed -- don't false-positive
        if real_duration and real_duration < claimed_duration * 0.6 and real_duration < 45:
            raise RuntimeError(
                f"SoundCloud Go+ preview detected: got {real_duration:.0f}s of a {claimed_duration}s track"
            )

    return {
        'audio_path': audio_path,
        'thumb_path': fetch_cover_jpg(info, base, artist, title),
        'title': title,
        'artist': artist,
        'duration': claimed_duration
    }

STRINGS.update({
    "sc_page_header": {"fa": "☁️ **ساندکلود:** `{query}`", "en": "☁️ **SoundCloud:** `{query}`", "ru": "☁️ **SoundCloud:** `{query}`", "es": "☁️ **SoundCloud:** `{query}`"},
    "sc_page_meta": {"fa": "_{count} نتیجه • صفحه {page} از {pages}_", "en": "_{count} results • page {page} of {pages}_", "ru": "_{count} результатов • стр. {page} из {pages}_", "es": "_{count} resultados • página {page} de {pages}_"},
})

async def render_sc_page(msg: Message, chat_id: int, page: int = 0):
    """Paginated SoundCloud results."""
    store = user_data.setdefault(chat_id, {})
    entries = store.get("sc_all") or []
    back = InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_music")]])
    if not entries:
        return await msg.edit_text(t(chat_id, "official_results_expired"), reply_markup=back)

    pages = max(1, (len(entries) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    chunk = entries[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    query = store.get("last_music_query", "")
    lines = [t(chat_id, "sc_page_header", query=query[:48]),
             t(chat_id, "sc_page_meta", count=len(entries), page=page+1, pages=pages) + "\n"]
    num_btns = []
    for i, item in enumerate(chunk):
        gi = page * PAGE_SIZE + i
        dur = int(item.get('duration') or 0)
        views = item.get('view_count') or 0
        lines.append(f"`{gi+1}` **{str(item.get('title','Unknown'))[:40]}**")
        lines.append(f"       👤 {str(item.get('uploader','Unknown'))[:24]}▫️  ⏱ {dur//60}:{dur%60:02d}"
                     + (f"▫️  👁 {format_number(views)}" if views else "") + "\n")
        num_btns.append(InlineKeyboardButton(str(gi + 1), callback_data=f"scvid_{gi}"))

    rows = [num_btns]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(t(chat_id, "btn_prev"), callback_data=f"scp_{page-1}"))
    nav.append(InlineKeyboardButton(f"· {page+1}/{pages} ·", callback_data="noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(t(chat_id, "btn_next"), callback_data=f"scp_{page+1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_music")])
    await msg.edit_text("\n".join(lines)[:4000], reply_markup=InlineKeyboardMarkup(rows))


STRINGS.update({
    "generic_expired": {"fa": "❌ منقضی شده.", "en": "❌ Expired.", "ru": "❌ Истекло.", "es": "❌ Caducado."},
    "lib_result_expired": {"fa": "❌ این نتیجه منقضی شده است.", "en": "❌ This result has expired.", "ru": "❌ Этот результат истёк.", "es": "❌ Este resultado ha caducado."},
    "lib_caption": {"fa": "📚 از کتابخانه شما", "en": "📚 From your library", "ru": "📚 Из вашей библиотеки", "es": "📚 De tu biblioteca"},
    "lib_file_unavailable": {"fa": "❌ فایل این آهنگ دیگر در دسترس نیست.", "en": "❌ This song's file is no longer available.", "ru": "❌ Файл этой песни больше не доступен.", "es": "❌ El archivo de esta canción ya no está disponible."},
    "album_track_prompt": {
        "fa": "🎵 **{title}**\nچه کاری میخواید انجام بدید؟",
        "en": "🎵 **{title}**\nWhat would you like to do?",
        "ru": "🎵 **{title}**\nЧто вы хотите сделать?",
        "es": "🎵 **{title}**\n¿Qué quieres hacer?",
    },
    "sc_no_results": {"fa": "❌ هیچ آهنگی پیدا نشد.", "en": "❌ No songs found.", "ru": "❌ Песни не найдены.", "es": "❌ No se encontraron canciones."},
    "sc_search_error": {"fa": "❌ خطا در جستجو.", "en": "❌ Search error.", "ru": "❌ Ошибка поиска.", "es": "❌ Error de búsqueda."},
    "sc_downloading": {"fa": "⏳ **در حال دانلود آهنگ...**", "en": "⏳ **Downloading song...**", "ru": "⏳ **Загрузка песни...**", "es": "⏳ **Descargando canción...**"},
    "sc_downloaded_caption": {"fa": "🎵 دانلود شده از ساندکلود", "en": "🎵 Downloaded from SoundCloud", "ru": "🎵 Загружено из SoundCloud", "es": "🎵 Descargado de SoundCloud"},
    "sc_drm_locked": {
        "fa": "⚠️ **این آهنگ در ساندکلود قفل (DRM) است.**\nدر حال دریافت از منبع رسمی...",
        "en": "⚠️ **This track is DRM-locked on SoundCloud.**\nFetching from the official source...",
        "ru": "⚠️ **Этот трек защищён DRM на SoundCloud.**\nПолучение из официального источника...",
        "es": "⚠️ **Esta canción está bloqueada por DRM en SoundCloud.**\nObteniendo de la fuente oficial...",
    },
    "sc_download_failed": {"fa": "❌ متاسفانه دریافت این آهنگ با مشکل مواجه شد.", "en": "❌ Sorry, downloading this track failed.", "ru": "❌ К сожалению, загрузить этот трек не удалось.", "es": "❌ Lo sentimos, no se pudo descargar esta canción."},
})

async def render_sc_search(msg: Message, query: str, chat_id: int):
    try:
        def fetch_sc():
            with yt_dlp.YoutubeDL({'extract_flat': True, 'quiet': True, 'no_warnings': True}) as ydl:
                info = ydl.extract_info(f"scsearch25:{query}", download=False)
                entries = [e for e in (info.get('entries') or []) if e]
                for e in entries:
                    e['view_count'] = e.get('view_count') or 0
                entries.sort(key=lambda x: x.get('view_count', 0), reverse=True)
                valid = [e for e in entries if (e.get('duration') or 0) > 30]
                return valid or entries

        data = await asyncio.to_thread(fetch_sc)
        if not data:
            return await msg.edit_text(t(chat_id, "sc_no_results"), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_music")]]))

        store = user_data.setdefault(chat_id, {})
        store["sc_all"] = data
        store["last_music_query"] = query
        store["mus_results"] = [(str(item.get('id') or item.get('url')), item.get('webpage_url') or item.get('url'))
                                for item in data]
        items = []
        for i, e in enumerate(data):
            m = meta_from_sc(e)
            views = int(e.get("view_count") or 0)
            items.append({"id": m["sc_id"], "title": m["title"] or "Unknown Title",
                          "artist": m["artist"] or "Unknown Artist", "album": "",
                          "duration": m["duration"], "dur_str": fmt_dur(m["duration"]),
                          "views_str": format_number(views) if views else "", "views": views,
                          "cover": m["cover"], "score": 1000 - i, "meta": m})
        store["official_all"] = items
        await render_official_page(msg, chat_id, page=0)
    except Exception as e:
        print(f"SC Search Error: {e}", flush=True)
        await msg.edit_text(t(chat_id, "sc_search_error"), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_music")]]))

async def render_ytm_search(msg: Message, query: str, chat_id: int):
    """In-chat YouTube Music search: real song results (title, artists,
    album, exact duration, square art) from YouTube Music itself."""
    back = InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_music_ytm")]])
    try:
        data = await asyncio.to_thread(ytm_search_sync, query, "songs", 25)
        data = [r for r in data if r.get("videoId")]
        if not data:
            return await msg.edit_text("❌ هیچ نتیجه‌ای در یوتیوب موزیک یافت نشد.", reply_markup=back)
        store = user_data.setdefault(chat_id, {})
        items = []
        for i, r in enumerate(data):
            m = meta_from_ytm(r)
            items.append({"id": m["yt_id"], "title": m["title"] or "Unknown Title",
                          "artist": m["artist"] or "Unknown Artist", "album": m["album"],
                          "duration": m["duration"], "dur_str": fmt_dur(m["duration"]),
                          "views_str": "", "views": 0, "cover": m["cover"], "score": 1000 - i, "meta": m})
        store["official_all"] = items
        store["last_music_query"] = query
        await render_official_page(msg, chat_id, page=0)
    except Exception as e:
        print(f"YTM Search Error: {e}", flush=True)
        await msg.edit_text("❌ خطا در جستجوی یوتیوب موزیک.", reply_markup=back)


def _sc_link_meta_sync(sc_url: str):
    """Track metadata for a SoundCloud link without downloading.

    Returns (meta, drm_locked). Go+ (DRM) tracks raise inside yt-dlp's
    extractor even for metadata, so for those the title/artist are recovered
    from a search for the URL's slug instead, and the song is then fetched
    from YouTube by the caller.
    """
    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, 'skip_download': True}) as ydl:
            info = ydl.extract_info(sc_url, download=False)
        if info.get("_type") == "playlist":
            info = next((e for e in (info.get("entries") or []) if e), info)
        return meta_from_sc(info), False
    except Exception as e:
        print(f"[SC] metadata extract failed ({str(e)[:120]}); trying slug search", flush=True)
    real_url = sc_url
    try:
        real_url = requests.head(sc_url, allow_redirects=True, timeout=8).url or sc_url
    except Exception:
        pass
    slug = real_url.rstrip('/').split('/')[-1].split('?')[0].replace('-', ' ').replace('_', ' ')
    entries = sc_search_sync(slug, 5)
    hit = next((e for e in entries if (e.get('webpage_url') or '').rstrip('/') == real_url.rstrip('/')),
               entries[0] if entries else None)
    return (meta_from_sc(hit) if hit else None), True


async def process_sc_download(client: Client, chat_id: int, msg: Message, sc_url: str, cache_key: str = ""):
    """SoundCloud link / search pick -> tagged MP3. Go+ (DRM) tracks fall back
    to the same song's official audio on YouTube."""
    print(f"[SC] request for: {sc_url}", flush=True)
    sc_id = str(cache_key) if str(cache_key or "").isdigit() else ""
    m = re.search(r"tracks(?:/|%3A)(\d+)", sc_url or "")
    if m:
        sc_id = m.group(1)
    meta, drm = None, False
    if sc_id:
        doc = await asyncio.to_thread(cache_find, {"sc_id": sc_id})
        if doc and await send_cached_doc(client, chat_id, doc, track_kb_for(doc, chat_id)):
            try:
                await msg.delete()
            except Exception:
                pass
            return log_activity(chat_id, "music_soundcloud", f"{doc.get('title')} (cache)")
    meta, drm = await asyncio.to_thread(_sc_link_meta_sync, sc_url)
    if meta is None:
        return await _show_status(client, chat_id, msg, t(chat_id, "sc_download_failed"))
    if not drm:
        if not meta.get("sc_url"):
            meta["sc_url"] = sc_url
        if await deliver_track(client, chat_id, meta, status=msg, activity="music_soundcloud",
                               report_failure=False):
            return
        msg = meta.get("_status")
    # DRM-locked (or the SoundCloud download itself failed): same song via YouTube.
    yt_meta = make_meta("yt", title=meta.get("title"), artist=meta.get("artist"),
                        duration=meta.get("duration"), cover=meta.get("cover"))
    msg = await _show_status(client, chat_id, msg, t(chat_id, "sc_drm_locked"))
    await deliver_track(client, chat_id, yt_meta, status=msg, activity="music_soundcloud")


async def _spotify_track_scrape_fallback(url: str):
    """Old method, kept only for when the API call fails (bad/expired
    credentials, deleted app, etc) -- scrapes the track page's <title> tag."""
    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        m = re.search(r'<title>(.*?) - song(?: and lyrics)? by (.*?) \| Spotify</title>', r.text)
        if not m:
            return None
        title, artist_full = m.group(1).strip(), m.group(2).strip()
        return {"title": title, "artist": artist_full.split(',')[0].strip(), "artists_all": artist_full}
    except Exception:
        return None


STRINGS.update({
    "spotify_invalid_link": {"fa": "❌ لینک اسپاتیفای نامعتبر است.", "en": "❌ Invalid Spotify link.", "ru": "❌ Недействительная ссылка Spotify.", "es": "❌ Enlace de Spotify no válido."},
    "spotify_track_info_failed": {"fa": "❌ اطلاعات این آهنگ از اسپاتیفای دریافت نشد.", "en": "❌ Couldn't get this track's info from Spotify.", "ru": "❌ Не удалось получить данные трека из Spotify.", "es": "❌ No se pudo obtener la información de la canción desde Spotify."},
    "spotify_searching_yt": {
        "fa": "⏳ **{title} — {artist}**\nدر حال دانلود آهنگ...",
        "en": "⏳ **{title} — {artist}**\nDownloading song...",
        "ru": "⏳ **{title} — {artist}**\nЗагрузка песни...",
        "es": "⏳ **{title} — {artist}**\nDescargando canción...",
    },
    "spotify_exact_not_found": {
        "fa": "❌ نسخه دقیق «{title}» در یوتیوب پیدا نشد.",
        "en": "❌ Couldn't find an exact match for \"{title}\" on YouTube.",
        "ru": "❌ Точное совпадение «{title}» на YouTube не найдено.",
        "es": "❌ No se encontró una coincidencia exacta de «{title}» en YouTube.",
    },
    "spotify_fetching_album": {"fa": "📀 **در حال دریافت لیست آلبوم از اسپاتیفای...**", "en": "📀 **Fetching album list from Spotify...**", "ru": "📀 **Получение списка альбома из Spotify...**", "es": "📀 **Obteniendo la lista del álbum desde Spotify...**"},
    "spotify_album_not_found": {"fa": "❌ این آلبوم از اسپاتیفای یافت نشد.", "en": "❌ This album wasn't found on Spotify.", "ru": "❌ Этот альбом не найден в Spotify.", "es": "❌ No se encontró este álbum en Spotify."},
    "spotify_fetching_playlist": {"fa": "📋 **در حال دریافت لیست پلی‌لیست از اسپاتیفای...**", "en": "📋 **Fetching playlist list from Spotify...**", "ru": "📋 **Получение списка плейлиста из Spotify...**", "es": "📋 **Obteniendo la lista de la playlist desde Spotify...**"},
    "spotify_playlist_not_found": {"fa": "❌ این پلی‌لیست از اسپاتیفای یافت نشد یا خصوصی است.", "en": "❌ This playlist wasn't found on Spotify or is private.", "ru": "❌ Этот плейлист не найден в Spotify или является приватным.", "es": "❌ No se encontró esta playlist en Spotify o es privada."},
    "spotify_link_process_error": {"fa": "❌ خطا در پردازش لینک اسپاتیفای.", "en": "❌ Error processing the Spotify link.", "ru": "❌ Ошибка при обработке ссылки Spotify.", "es": "❌ Error al procesar el enlace de Spotify."},
})

async def process_spotify_link(client: Client, chat_id: int, msg: Message, url: str):
    """Spotify serves no audio (DRM), so this is metadata-only: exact
    title/artist/album/cover/ISRC from Spotify's catalog, audio from the
    official YouTube Music upload, all through the unified pipeline."""
    m = re.search(r"spotify\.com/(?:intl-\w+/)?(track|album|playlist)/([a-zA-Z0-9]+)", url)
    if not m:
        return await msg.edit_text(t(chat_id, "spotify_invalid_link"))
    kind, sid = m.group(1), m.group(2)

    try:
        if kind == "track":
            doc = await asyncio.to_thread(cache_find, {"spotify_id": sid})
            if doc and await send_cached_doc(client, chat_id, doc, track_kb_for(doc, chat_id)):
                try:
                    await msg.delete()
                except Exception:
                    pass
                return
            info = await asyncio.to_thread(spotify_track_info_sync, sid)
            if not info or not info.get("title"):
                info = await _spotify_track_scrape_fallback(url)
            if not info or not info.get("title"):
                return await msg.edit_text(t(chat_id, "spotify_track_info_failed"))
            meta = make_meta("spotify", title=info["title"], artist=info.get("artists_all") or info.get("artist"),
                             album=info.get("album"), year=info.get("year"), duration=info.get("duration"),
                             cover=info.get("cover"), isrc=info.get("isrc"), spotify_id=sid)
            return await deliver_track(client, chat_id, meta, status=msg, activity="music_spotify")

        if kind == "album":
            await msg.edit_text(t(chat_id, "spotify_fetching_album"))
            album = await asyncio.to_thread(spotify_album_tracks_sync, sid)
            if not album or not album.get("tracks"):
                try:
                    sp_page = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
                    m_alb = re.search(r'<title>(.*?) - album by (.*?) \| Spotify</title>', sp_page.text, re.IGNORECASE)
                    if m_alb:
                        dz_results = await asyncio.to_thread(rega_music.search_albums,
                                                             f"{m_alb.group(2).strip()} {m_alb.group(1).strip()}", 5)
                        if dz_results:
                            return await process_deezer_album(client, chat_id, msg, dz_results[0]["id"])
                except Exception:
                    pass
                return await msg.edit_text(t(chat_id, "spotify_album_not_found"))
            return await download_album(client, chat_id, msg, album)

        if kind == "playlist":
            await msg.edit_text(t(chat_id, "spotify_fetching_playlist"))
            pl = await asyncio.to_thread(spotify_playlist_tracks_sync, sid, 30)
            if not pl or not pl.get("tracks"):
                return await msg.edit_text(t(chat_id, "spotify_playlist_not_found"))
            return await download_album(client, chat_id, msg, pl)
    except Exception:
        traceback.print_exc()
        try:
            await msg.edit_text(t(chat_id, "spotify_link_process_error"))
        except Exception:
            pass


# ---------------- REGA-SPOTIFY MUSIC ENGINE & ALBUM PIPELINE ----------------
async def process_deezer_track(client: Client, chat_id: int, status_msg: Message, track_id: str, spotify_id: str = None):
    """Deezer track id -> tagged MP3 through the unified pipeline."""
    doc = await asyncio.to_thread(cache_find, {"deezer_id": str(track_id), "spotify_id": spotify_id or ""})
    if doc and await send_cached_doc(client, chat_id, doc, track_kb_for(doc, chat_id)):
        try:
            await status_msg.delete()
        except Exception:
            pass
        return
    info = await asyncio.to_thread(rega_music.get_track_info, track_id)
    if not info:
        return await _show_status(client, chat_id, status_msg, "❌ آهنگ مورد نظر یافت نشد.")
    meta = meta_from_deezer(info)
    if spotify_id:
        meta["spotify_id"] = str(spotify_id)
    await deliver_track(client, chat_id, meta, status=status_msg, activity="music_deezer")


# ---- Track collections: Deezer albums/playlists, YouTube Music albums/playlists ----
_COLLECTION_CACHE = {}   # (kind, id) -> (ts, collection)
COLLECTION_PAGE = 8


def _load_collection_sync(kind: str, cid: str):
    hit = _COLLECTION_CACHE.get((kind, cid))
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    col = None
    if kind in ("dza", "dzp"):
        raw_c = rega_music.get_album_details(cid) if kind == "dza" else rega_music.get_playlist_details(cid)
        if raw_c and raw_c.get("tracks"):
            is_alb = kind == "dza"
            year = (raw_c.get("release_date") or "")[:4]
            col = {"title": raw_c.get("title"), "artist": raw_c.get("artist"), "thumb": raw_c.get("thumb"),
                   "year": year, "is_playlist": not is_alb, "tracks": []}
            for tr in raw_c["tracks"]:
                m = make_meta("deezer", title=tr.get("title"), artist=tr.get("artist"),
                              album=tr.get("album") or (raw_c.get("title") if is_alb else ""),
                              duration=tr.get("duration"), deezer_id=tr.get("id"),
                              cover=tr.get("thumb") or (raw_c.get("thumb") if is_alb else ""),
                              year=year if is_alb else "",
                              track_no=tr.get("track_number") if is_alb else 0)
                col["tracks"].append({"key": f"dz:{tr['id']}", "cb": f"regatrk_{tr['id']}", "meta": m})
    elif kind in ("yta", "ytp"):
        try:
            if kind == "yta":
                raw_c = _ytm().get_album(cid)
                artist = ", ".join(a.get("name", "") for a in (raw_c.get("artists") or []))
            else:
                raw_c = _ytm().get_playlist(cid, limit=100)
                au = raw_c.get("author")
                artist = au.get("name", "") if isinstance(au, dict) else _s(au)
            alb_thumb = _ytm_thumb(raw_c.get("thumbnails"))
            tracks = []
            for i, tr in enumerate(raw_c.get("tracks") or [], 1):
                if not tr.get("videoId"):
                    continue
                m = meta_from_ytm(tr)
                if kind == "yta":
                    m.update(album=_s(raw_c.get("title")), cover=alb_thumb, year=_s(raw_c.get("year")),
                             track_no=int(tr.get("trackNumber") or i))
                tracks.append({"key": m["yt_id"], "cb": f"ytt_{m['yt_id']}", "meta": m})
            if tracks:
                col = {"title": raw_c.get("title"), "artist": artist, "thumb": alb_thumb,
                       "year": raw_c.get("year") or "", "is_playlist": kind == "ytp", "tracks": tracks}
        except Exception as e:
            print(f"ytm collection {kind}:{cid} error: {e}", flush=True)
    if col:
        if len(_COLLECTION_CACHE) > 300:
            _COLLECTION_CACHE.clear()
        _COLLECTION_CACHE[(kind, cid)] = (time.time(), col)
    return col


def _remember_track_metas(chat_id: int, col: dict):
    store = user_data.setdefault(chat_id, {}).setdefault("col_meta", {})
    if len(store) > 800:
        store.clear()
    for tr in col["tracks"]:
        store[tr["cb"]] = tr["meta"]


async def render_collection(client: Client, chat_id: int, status_msg, kind: str, cid: str,
                            page: int = 0, edit_msg: Message = None):
    col = await asyncio.to_thread(_load_collection_sync, kind, cid)
    if not col:
        return await _show_status(client, chat_id, status_msg, "❌ اطلاعات یافت نشد.")
    _remember_track_metas(chat_id, col)
    tracks = col["tracks"]
    pages = max(1, (len(tracks) + COLLECTION_PAGE - 1) // COLLECTION_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = tracks[page * COLLECTION_PAGE:(page + 1) * COLLECTION_PAGE]
    rows = [[InlineKeyboardButton(f"⬇️ دانلود همه ({len(tracks)} قطعه)", callback_data=f"cola_{kind}_{cid}"[:64])]]
    for i in range(0, len(chunk), 2):
        row = []
        for j, tr in enumerate(chunk[i:i + 2]):
            n = page * COLLECTION_PAGE + i + j + 1
            row.append(InlineKeyboardButton(f"{n}. {tr['meta']['title'][:22]}", callback_data=tr["cb"][:64]))
        rows.append(row)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"colp_{kind}_{page-1}_{cid}"[:64]))
    nav.append(InlineKeyboardButton(f"· {page+1}/{pages} ·", callback_data="noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"colp_{kind}_{page+1}_{cid}"[:64]))
    rows.append(nav)
    back = "menu_music_ytm" if kind.startswith("yt") else "menu_music_spotify"
    rows.append([InlineKeyboardButton("🔙 بازگشت به منوی موزیک", callback_data=back)])
    icon = "📁" if col.get("is_playlist") else "📀"
    caption = (f"{icon} **{col['title']}**\n\n👤 {col.get('artist') or '-'}\n🔢 {len(tracks)} قطعه"
               + (f"\n📅 {col['year']}" if col.get("year") else "")
               + "\n\nبرای دریافت هر قطعه روی دکمه آن بزنید:")
    kb = InlineKeyboardMarkup(rows)
    if edit_msg is not None:
        try:
            if edit_msg.photo:
                return await edit_msg.edit_caption(caption, reply_markup=kb)
            return await edit_msg.edit_text(caption, reply_markup=kb)
        except Exception:
            pass
    if status_msg is not None:
        try:
            await status_msg.delete()
        except Exception:
            pass
    if col.get("thumb"):
        try:
            return await client.send_photo(chat_id, photo=col["thumb"], caption=caption, reply_markup=kb)
        except Exception:
            pass
    await client.send_message(chat_id, caption, reply_markup=kb)


async def download_collection(client: Client, chat_id: int, kind: str, cid: str):
    col = await asyncio.to_thread(_load_collection_sync, kind, cid)
    if not col:
        return await client.send_message(chat_id, "❌ اطلاعات یافت نشد.")
    status = await client.send_message(chat_id, t(chat_id, "album_queue_started"))
    album = {"album": col["title"], "artist": col.get("artist"), "cover": col.get("thumb"),
             "year": col.get("year"), "is_playlist": col.get("is_playlist"),
             "total_tracks": None if col.get("is_playlist") else len(col["tracks"]),
             "tracks": [{"name": tr["meta"]["title"], "artist": tr["meta"]["artist"],
                         "album": tr["meta"].get("album"), "cover": tr["meta"].get("cover"),
                         "duration": tr["meta"].get("duration"), "track_number": tr["meta"].get("track_no"),
                         "deezer_id": tr["meta"].get("deezer_id"), "yt_id": tr["meta"].get("yt_id")}
                        for tr in col["tracks"]]}
    await download_album(client, chat_id, status, album)


async def render_ytm_artist(client: Client, chat_id: int, status_msg, channel_id: str):
    try:
        art = await asyncio.to_thread(lambda: _ytm().get_artist(channel_id))
    except Exception as e:
        print(f"ytm artist error: {e}", flush=True)
        art = None
    if not art:
        return await _show_status(client, chat_id, status_msg, "❌ اطلاعات هنرمند یافت نشد.")
    songs = ((art.get("songs") or {}).get("results") or [])[:10]
    albums = ((art.get("albums") or {}).get("results") or [])[:8]
    store = user_data.setdefault(chat_id, {}).setdefault("col_meta", {})
    rows = []
    for i in range(0, len(songs), 2):
        row = []
        for s_ in songs[i:i + 2]:
            m = meta_from_ytm(s_)
            if not m["yt_id"]:
                continue
            store[f"ytt_{m['yt_id']}"] = m
            row.append(InlineKeyboardButton(f"🎵 {m['title'][:22]}", callback_data=f"ytt_{m['yt_id']}"))
        if row:
            rows.append(row)
    for i in range(0, len(albums), 2):
        row = [InlineKeyboardButton(f"📀 {(a.get('title') or '')[:22]}", callback_data=f"colp_yta_0_{a['browseId']}"[:64])
               for a in albums[i:i + 2] if a.get("browseId")]
        if row:
            rows.append(row)
    rows.append([InlineKeyboardButton("🔙 بازگشت به منوی موزیک", callback_data="menu_music_ytm")])
    caption = f"👤 **{art.get('name')}**\n\nمحبوب‌ترین قطعات و آلبوم‌ها:"
    if status_msg is not None:
        try:
            await status_msg.delete()
        except Exception:
            pass
    thumb = _ytm_thumb(art.get("thumbnails"))
    if thumb:
        try:
            return await client.send_photo(chat_id, thumb, caption=caption, reply_markup=InlineKeyboardMarkup(rows))
        except Exception:
            pass
    await client.send_message(chat_id, caption, reply_markup=InlineKeyboardMarkup(rows))


async def process_deezer_album(client: Client, chat_id: int, status_msg: Message, album_id: str, page: int = 0):
    await render_collection(client, chat_id, status_msg, "dza", str(album_id), page)


async def process_download_all_album(client: Client, chat_id: int, album_id: str):
    album = await asyncio.to_thread(rega_music.get_album_details, album_id)
    if not album or not album.get("tracks"):
        await client.send_message(chat_id, "❌ خطایی در بارگذاری قطعات آلبوم رخ داد.")
        return
    status = await client.send_message(chat_id, t(chat_id, "album_queue_started"))
    await download_album(client, chat_id, status, {
        "album": album.get("title", ""), "artist": album.get("artist", ""),
        "cover": album.get("thumb", ""), "year": (album.get("release_date") or "")[:4],
        "tracks": [{"name": tr.get("title"), "artist": tr.get("artist") or album.get("artist", ""),
                    "duration": tr.get("duration"), "track_number": tr.get("track_number"),
                    "deezer_id": tr.get("id")} for tr in album["tracks"]],
    })


async def process_deezer_artist(client: Client, chat_id: int, status_msg: Message, artist_id: str):
    try:
        await status_msg.edit_text("⏳ در حال دریافت آثار هنرمند...")
        top_tracks = await asyncio.to_thread(rega_music.get_artist_top_tracks, artist_id, 10)
        albums = await asyncio.to_thread(rega_music.get_artist_albums, artist_id, 8)
        
        art_name = top_tracks[0].get("artist") if top_tracks else "Artist"
        thumb = top_tracks[0].get("thumb") if top_tracks else ""

        buttons = []
        if top_tracks:
            for i in range(0, len(top_tracks), 2):
                row = []
                t1 = top_tracks[i]
                row.append(InlineKeyboardButton(f"🎵 {t1.get('title')[:22]}", callback_data=f"regatrk_{t1['id']}"))
                if i + 1 < len(top_tracks):
                    t2 = top_tracks[i+1]
                    row.append(InlineKeyboardButton(f"🎵 {t2.get('title')[:22]}", callback_data=f"regatrk_{t2['id']}"))
                buttons.append(row)

        if albums:
            for i in range(0, len(albums), 2):
                row = []
                a1 = albums[i]
                row.append(InlineKeyboardButton(f"📀 {a1.get('title')[:22]}", callback_data=f"regaalb_{a1['id']}"))
                if i + 1 < len(albums):
                    a2 = albums[i+1]
                    row.append(InlineKeyboardButton(f"📀 {a2.get('title')[:22]}", callback_data=f"regaalb_{a2['id']}"))
                buttons.append(row)

        buttons.append([InlineKeyboardButton("🔙 بازگشت به منوی موزیک", callback_data="menu_music_spotify")])
        kb = InlineKeyboardMarkup(buttons)

        caption = f"👤 **{art_name}**\n\nمحبوب‌ترین قطعات و آلبوم‌ها:"
        try:
            await status_msg.delete()
        except Exception:
            pass

        if thumb:
            await client.send_photo(chat_id, photo=thumb, caption=caption, reply_markup=kb)
        else:
            await client.send_message(chat_id, text=caption, reply_markup=kb)
    except Exception as e:
        print(f"process_deezer_artist error: {e}", flush=True)
        try:
            await status_msg.edit_text(f"❌ خطا در پردازش آثار خواننده: {e}")
        except Exception:
            pass

# ---------------- 4. ADVANCED OFFICIAL MUSIC ENGINE (SMART RANKING) ----------------
def clean_song_title(title: str) -> str:
    """Strips unnecessary video noise for clean Telegram player metadata."""
    title = re.sub(r'(?i)\s*[\(\[](official\s*(music\s*)?(video|audio|lyric\s*video|hd|4k|visualizer)?|audio|lyrics|hq)[\)\]]', '', title or '')
    title = re.sub(r'(?i)\s*[\(\[](explicit(\s*version|\s*hd)?|clean(\s*version)?)[\)\]]', '', title)
    title = title.replace('&quot;', '"').replace('&amp;', '&').replace('&#39;', "'").strip()
    return title

# Some artists' official YouTube uploads use a different name than their
# best-known name (e.g. Kanye West titles everything under "YE" now) --
# a text search for the well-known name then never matches the title at
# all, so the real official upload doesn't even appear in results.
ARTIST_ALIASES = {
    "kanye west": "ye",
    "ye": "kanye west",
}


def search_official_smart_sync(query: str, limit: int = 20, artist_hint: str = None, title_hint: str = None, expected_duration: int = None):
    """Smart music search with topic/verified-artist priority ranking and studio duration matching.

    Uses yt-dlp's own search (ytsearch:) instead of the YouTube Data API --
    a Data API text search costs 100 quota units against a 10,000/day free
    cap, so at ~100-200 units per music search the bot could only handle
    roughly 50-100 total searches a day across every user combined. yt-dlp's
    search costs nothing against that quota and, as a bonus, returns
    view_count/duration/channel_is_verified directly with no second lookup.
    """
    want_artist = _norm(artist_hint) if artist_hint else None
    want_title = _norm(title_hint) if title_hint else None
    user_wants_live = is_live_marker(title_hint or "") or is_live_marker(query)

    def _search(q, n=25):
        try:
            with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, 'extract_flat': True}) as ydl:
                info = ydl.extract_info(f"ytsearch{n}:{q}", download=False)
            return info.get('entries') or []
        except Exception as e:
            print(f"ytsearch error for {q!r}: {e}", flush=True)
            return []

    entries = _search(query)
    seen = {e.get('id') for e in entries}

    alias = ARTIST_ALIASES.get((artist_hint or "").strip().lower())
    if alias:
        alias_query = re.sub(re.escape(artist_hint), alias, query, flags=re.IGNORECASE) if artist_hint in query else f"{alias} {query}"
        for e in _search(alias_query):
            if e.get('id') not in seen:
                entries.append(e)
                seen.add(e.get('id'))

    for e in _search(f"{query} official audio", n=10):
        if e.get('id') not in seen:
            entries.append(e)
            seen.add(e.get('id'))

    if artist_hint and title_hint:
        for e in _search(f"{artist_hint} {title_hint} topic", n=6):
            if e.get('id') not in seen:
                entries.append(e)
                seen.add(e.get('id'))
        for e in _search(f"{artist_hint} {title_hint} audio", n=6):
            if e.get('id') not in seen:
                entries.append(e)
                seen.add(e.get('id'))

    if not entries:
        return []

    ranked_results = []
    for e in entries:
        vid = e.get('id')
        title_raw = e.get('title') or ''
        channel = e.get('channel') or e.get('uploader') or ''
        duration_sec = int(e.get('duration') or 0)
        views = int(e.get('view_count') or 0)
        verified = bool(e.get('channel_is_verified'))

        # Skits and interludes can be 10-25 seconds! Do NOT reject them with < 45!
        min_allowed_dur = 5 if (expected_duration and expected_duration < 45) else 10
        if not vid or duration_sec < min_allowed_dur or duration_sec > 1800:
            continue

        title_lower = title_raw.lower()
        artist_clean = channel.replace(" - Topic", "").replace("VEVO", "").strip()
        score = 0

        if channel.endswith(" - Topic"):
            score += 150
        if "official" in title_lower:
            score += 60
        if "audio" in title_lower:
            score += 50
        if "vevo" in channel.lower():
            score += 50
        if verified:
            score += 60

        # LIVE PENALTY: Never pick live concert/awards if user didn't ask for live!
        if is_live_marker(title_lower) and not user_wants_live:
            score -= 600

        # Clean marker
        is_clean_marker = re.search(r'\b(clean version|clean edit|radio edit|censored|edited version|clean)\b', title_lower)
        if is_clean_marker and not (want_title and _norm(want_title) == "clean"):
            score -= 300

        # Clickbait / unofficial video markers
        if re.search(r'\b(explicit hd|fan video|bootleg|unofficial|re-upload|remastered 2025)\b', title_lower):
            score -= 250

        is_video = bool(re.search(r'\b(music video|official video)\b', title_lower))
        if is_video:
            score -= 160

        if want_artist:
            channel_norm = _norm(artist_clean)
            if channel_norm and (want_artist in channel_norm or channel_norm in want_artist):
                score += 200  # verified: uploader actually is the requested artist
            else:
                score -= 60  # unverified uploader -- deprioritize even if popular
        if want_title:
            norm_raw = _norm(title_raw)
            norm_clean = _norm(clean_song_title(title_raw))
            if norm_raw == want_title or norm_clean == want_title:
                score += 260
            elif is_title_match(title_hint, title_raw, artist_hint or ""):
                score += 150
            else:
                score -= 150

        # DURATION MATCHING:
        if expected_duration and expected_duration > 0:
            diff = abs(duration_sec - expected_duration)
            if diff <= 3:
                score += 180
            elif diff <= 8:
                score += 100
            elif diff <= 15:
                score += 30
            elif diff > 45:
                score -= 400

        score += min(views // 1000000, 50)
        title_clean = clean_song_title(title_raw)

        ranked_results.append({
            "id": vid,
            "title": title_clean,
            "artist": artist_clean,
            "duration": duration_sec,
            "dur_str": f"{duration_sec//60}:{duration_sec%60:02d}",
            "views_str": format_number(views),
            "views": views,
            "published": "",
            "score": score,
            "is_video": is_video,
        })

    # Hard rule, not just a score weight: every non-video candidate sorts
    # ahead of every video candidate, regardless of how the rest of the
    # score compares. A video is only ever returned first when literally
    # nothing audio-only matched at all.
    ranked_results.sort(key=lambda x: (x["is_video"], -x["score"]))
    return ranked_results[:limit]



def download_official_native_sync(video_id: str):
    """Downloads the official audio track on Render (audio-only, never a muxed video)."""
    opts = build_audio_opts('music_downloads/%(id)s.%(ext)s')
    # No player_client override here on purpose. Forcing 'android' made YouTube
    # return storyboards + a muxed 360p MP4 and no audio-only formats at all; and
    # when cookies are present yt-dlp skips 'android' anyway ("does not support
    # cookies"), leaving a lone 'web' client. Both paths ended up shipping a video.
    # yt-dlp's default client list negotiates audio-only formats correctly.
    opts['js_runtimes'] = {'deno': {}}
    ex_args = youtube_extractor_args()
    if ex_args:
        opts['extractor_args'] = ex_args
    if os.path.exists(YOUTUBE_COOKIES_FILE) and os.path.getsize(YOUTUBE_COOKIES_FILE) > 0:
        opts['cookiefile'] = YOUTUBE_COOKIES_FILE

    info = timed_extract(opts, f"https://www.youtube.com/watch?v={video_id}", f"youtube:{video_id}")

    base = f"music_downloads/{info['id']}"
    artist = (info.get('uploader') or info.get('channel') or 'Unknown Artist').replace(" - Topic", "").strip()
    title = clean_song_title(info.get('title') or 'Unknown Title')

    return {
        'audio_path': collect_audio_file(base),
        'thumb_path': fetch_cover_jpg(info, base, artist, title),
        'title': title,
        'artist': artist,
        'duration': int(info.get('duration') or 0)
    }

STRINGS.update({
    "sort_relevant": {"fa": "🔥 مرتبط", "en": "🔥 Relevant", "ru": "🔥 По теме", "es": "🔥 Relevante"},
    "sort_views": {"fa": "👁 پربازدید", "en": "👁 Most Viewed", "ru": "👁 Популярные", "es": "👁 Más vistos"},
    "sort_duration": {"fa": "⏱ کوتاه", "en": "⏱ Shortest", "ru": "⏱ Короткие", "es": "⏱ Más cortos"},
    "sort_new": {"fa": "🆕 جدید", "en": "🆕 Newest", "ru": "🆕 Новые", "es": "🆕 Más nuevos"},
})
# Sort modes for the music search. Keys stay single-char so callback_data
# ("osp_<page>_<key>") stays far inside Telegram's 64-byte limit.
SORT_MODES = {"r": "🔥 مرتبط", "v": "👁 پربازدید", "d": "⏱ کوتاه", "n": "🆕 جدید"}
SORT_MODE_KEYS = {"r": "sort_relevant", "v": "sort_views", "d": "sort_duration", "n": "sort_new"}


def sort_modes(chat_id) -> dict:
    return {k: t(chat_id, key) for k, key in SORT_MODE_KEYS.items()}


PAGE_SIZE = 5
MEDALS = ["🥇", "🥈", "🥉"]


def sort_tracks(items: list, mode: str) -> list:
    if mode == "v":
        return sorted(items, key=lambda x: x.get("views", 0), reverse=True)
    if mode == "d":
        return sorted(items, key=lambda x: x.get("duration", 0))
    if mode == "n":
        return sorted(items, key=lambda x: x.get("published", ""), reverse=True)
    return sorted(items, key=lambda x: x.get("score", 0), reverse=True)


STRINGS.update({
    "official_results_expired": {"fa": "❌ نتایج منقضی شده است. دوباره جستجو کنید.", "en": "❌ Results have expired. Please search again.", "ru": "❌ Результаты устарели. Выполните поиск снова.", "es": "❌ Los resultados han caducado. Busca de nuevo."},
    "official_results_header": {"fa": "💽 **نتایج برای:** `{query}`", "en": "💽 **Results for:** `{query}`", "ru": "💽 **Результаты для:** `{query}`", "es": "💽 **Resultados para:** `{query}`"},
    "official_results_meta": {
        "fa": "_{sort} • {count} نتیجه • صفحه {page} از {pages}_",
        "en": "_{sort} • {count} results • page {page} of {pages}_",
        "ru": "_{sort} • {count} результатов • стр. {page} из {pages}_",
        "es": "_{sort} • {count} resultados • página {page} de {pages}_",
    },
    "btn_prev": {"fa": "◀️ قبلی", "en": "◀️ Prev", "ru": "◀️ Пред.", "es": "◀️ Anterior"},
    "btn_next": {"fa": "بعدی ▶️", "en": "Next ▶️", "ru": "След. ▶️", "es": "Siguiente ▶️"},
    "btn_random": {"fa": "🎲 شانسی", "en": "🎲 Random", "ru": "🎲 Случайно", "es": "🎲 Aleatorio"},
    "official_downloading": {"fa": "⏳ **در حال دانلود موزیک...**", "en": "⏳ **Downloading music...**", "ru": "⏳ **Загрузка музыки...**", "es": "⏳ **Descargando música...**"},
    "official_downloaded_caption": {"fa": "🎵 دانلود شده از نسخه اورجینال", "en": "🎵 Downloaded from the original version", "ru": "🎵 Загружено из оригинальной версии", "es": "🎵 Descargado de la versión original"},
})

def _official_item_meta(item: dict) -> dict:
    """Meta for a row of the shared results view (YouTube Music, SoundCloud,
    smart YouTube search), so every pick goes through deliver_track."""
    if item.get("meta"):
        return dict(item["meta"])
    return make_meta("yt", yt_id=item.get("id"), title=item.get("title"), artist=item.get("artist"),
                     album=item.get("album"), duration=item.get("duration"), cover=item.get("cover"))


async def render_official_page(msg: Message, chat_id: int, page: int = 0, sort: str = "r"):
    """Paginated + sortable results view.

    The sorted list is written back to official_results so the existing
    offvid_<index> callbacks keep pointing at the row the user actually sees.
    """
    store = user_data.setdefault(chat_id, {})
    all_items = store.get("official_all") or []
    back = InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_music")]])
    if not all_items:
        return await msg.edit_text(t(chat_id, "official_results_expired"), reply_markup=back)

    sort = sort if sort in SORT_MODES else "r"
    items = sort_tracks(all_items, sort)
    store["official_results"] = items
    store["off_sort"] = sort
    modes = sort_modes(chat_id)

    pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    store["off_page"] = page
    chunk = items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    query = store.get("last_music_query", "")
    lines = [t(chat_id, "official_results_header", query=query[:48]),
             t(chat_id, "official_results_meta", sort=modes[sort], count=len(items), page=page+1, pages=pages) + "\n"]
    num_btns = []
    for i, song in enumerate(chunk):
        gi = page * PAGE_SIZE + i
        badge = MEDALS[gi] if (sort == "r" and gi < 3) else f"`{gi+1}`"
        lines.append(f"{badge} **{song['title'][:40]}**")
        extra = (f"▫️  👁 {song['views_str']}" if song.get('views_str')
                 else (f"▫️  💿 {song['album'][:20]}" if song.get('album') else ""))
        lines.append(f"       👤 {song['artist'][:24]}▫️  ⏱ {song['dur_str']}{extra}\n")
        num_btns.append(InlineKeyboardButton(str(gi + 1), callback_data=f"offvid_{gi}"))

    rows = [num_btns]
    rows.append([InlineKeyboardButton(f"‹{lbl}›" if k == sort else lbl,
                                      callback_data=f"osp_0_{k}")
                 for k, lbl in modes.items()])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(t(chat_id, "btn_prev"), callback_data=f"osp_{page-1}_{sort}"))
    nav.append(InlineKeyboardButton(f"· {page+1}/{pages} ·", callback_data="noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(t(chat_id, "btn_next"), callback_data=f"osp_{page+1}_{sort}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(t(chat_id, "btn_random"), callback_data="offrand"),
                 InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_music")])
    await msg.edit_text("\n".join(lines)[:4000], reply_markup=InlineKeyboardMarkup(rows))


async def process_official_download(client: Client, chat_id: int, msg: Message, song_info: dict, extra_rows=None):
    """YouTube-id based delivery (YT Music search picks, music.youtube.com
    links, radio, AudD...). extra_rows: optional extra button rows (Radio
    Mode's Next/Stop) appended after the normal track actions."""
    source = "spotify" if song_info.get("spotify_id") else ("ytm" if song_info.get("album") else "yt")
    meta = make_meta(source, yt_id=song_info.get("id"), title=song_info.get("title"),
                     artist=song_info.get("artist"), album=song_info.get("album"),
                     year=song_info.get("year"), duration=song_info.get("duration"),
                     cover=song_info.get("cover"), spotify_id=song_info.get("spotify_id"),
                     isrc=song_info.get("isrc"))
    return await deliver_track(client, chat_id, meta, status=msg, extra_rows=extra_rows,
                               activity="music_official")


# ---------------- 4.5 TRACK SUPERPOWERS (effects / lyrics files / albums) ----------------
# ffmpeg filter chains. Each variant is cached under "<key>:<effect>", so the
# re-encode cost is paid once globally and every later request is instant.
EFFECTS = {
    "slow": ("🌙 اسلو + ریورب", "asetrate=44100*0.86,aresample=44100,aecho=0.8:0.88:60:0.4"),
    "night": ("⚡ نایتکور", "asetrate=44100*1.25,aresample=44100,atempo=1.02"),
    "eight": ("🎧 هشت‌بعدی (8D)", "apulsator=hz=0.09,aecho=0.8:0.9:200:0.2"),
    "bass": ("🔊 بیس بوست", "bass=g=13,volume=0.85"),
    "vocal": ("🎙 کارائوکه (حذف صدا)", "pan=stereo|c0=c0-c1|c1=c1-c0"),
}
STRINGS.update({
    "effect_slow": {"fa": "🌙 اسلو + ریورب", "en": "🌙 Slowed + Reverb", "ru": "🌙 Замедление + реверб", "es": "🌙 Ralentizado + reverb"},
    "effect_night": {"fa": "⚡ نایتکور", "en": "⚡ Nightcore", "ru": "⚡ Найткор", "es": "⚡ Nightcore"},
    "effect_eight": {"fa": "🎧 هشت‌بعدی (8D)", "en": "🎧 8D Audio", "ru": "🎧 8D-звук", "es": "🎧 Audio 8D"},
    "effect_bass": {"fa": "🔊 بیس بوست", "en": "🔊 Bass Boost", "ru": "🔊 Усиление басов", "es": "🔊 Realce de graves"},
    "effect_vocal": {"fa": "🎙 کارائوکه (حذف صدا)", "en": "🎙 Karaoke (Remove Vocal)", "ru": "🎙 Караоке (удалить вокал)", "es": "🎙 Karaoke (quitar voz)"},
})
EFFECT_LABEL_KEYS = {"slow": "effect_slow", "night": "effect_night", "eight": "effect_eight", "bass": "effect_bass", "vocal": "effect_vocal"}


def effect_label(chat_id, code: str) -> str:
    key = EFFECT_LABEL_KEYS.get(code)
    if not key:
        return EFFECTS.get(code, ("",))[0]
    return t(chat_id, key)


STRINGS.update({
    "track_btn_lyrics": {"fa": "🎤 متن آهنگ", "en": "🎤 Lyrics", "ru": "🎤 Текст песни", "es": "🎤 Letra"},
    "track_btn_lrc": {"fa": "📄 فایل LRC", "en": "📄 LRC File", "ru": "📄 Файл LRC", "es": "📄 Archivo LRC"},
    "track_btn_effects": {"fa": "🎛 افکت‌ها", "en": "🎛 Effects", "ru": "🎛 Эффекты", "es": "🎛 Efectos"},
    "track_btn_album": {"fa": "📀 آلبوم کامل", "en": "📀 Full Album", "ru": "📀 Весь альбом", "es": "📀 Álbum completo"},
    "track_btn_karaoke": {"fa": "🎙 کارائوکه (حذف صدا)", "en": "🎙 Karaoke (Remove Vocal)", "ru": "🎙 Караоке (удалить вокал)", "es": "🎙 Karaoke (quitar voz)"},
    "track_btn_trim": {"fa": "✂️ برش موزیک", "en": "✂️ Trim / Ringtone", "ru": "✂️ Обрезать موزیک", "es": "✂️ Recortar audio"},
    "track_btn_speed": {"fa": "⏩ سرعت و زیروبم", "en": "⏩ Speed & Pitch", "ru": "⏩ Изменить скорость", "es": "⏩ Cambiar velocidad"},
    "track_btn_tags": {"fa": "🏷 ویرایش تگ و نام", "en": "🏷 Edit Tags & Name", "ru": "🏷 Изменить теги", "es": "🏷 Editar etiquetas"},
    "track_btn_add_playlist": {"fa": "➕ افزودن به پلی‌لیست", "en": "➕ Add to Playlist", "ru": "➕ Добавить в плейлист", "es": "➕ Añadir a la lista"},
})

def track_actions_kb(cache_key: str, chat_id=None) -> InlineKeyboardMarkup:
    """The action row attached to every delivered track."""
    tok = lyrics_token(cache_key)
    tt = (lambda key: t(chat_id, key)) if chat_id is not None else (lambda key: STRINGS[key]["fa"])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(tt("track_btn_lyrics"), callback_data=f"lyrics_{tok}"),
         InlineKeyboardButton(tt("track_btn_lrc"), callback_data=f"lrc_{tok}")],
        [InlineKeyboardButton(tt("track_btn_effects"), callback_data=f"fx_{tok}"),
         InlineKeyboardButton(tt("track_btn_karaoke"), callback_data=f"kar_{tok}")],
        [InlineKeyboardButton(tt("track_btn_trim"), callback_data=f"trim_{tok}"),
         InlineKeyboardButton(tt("track_btn_speed"), callback_data=f"speed_{tok}")],
        [InlineKeyboardButton(tt("track_btn_tags"), callback_data=f"tag_{tok}"),
         InlineKeyboardButton(tt("track_btn_album"), callback_data=f"alb_{tok}")],
        [InlineKeyboardButton(tt("track_btn_add_playlist"), callback_data=f"addpl_{tok}")],
    ])


def get_track_delivery_kb(cache_key: str, chat_id=None) -> InlineKeyboardMarkup:
    """If delivered in a group/channel (chat_id < 0), return only the 'Go to Taha Bot' button.
    If in private chat (chat_id > 0), return the full superpower action keyboard."""
    if chat_id and int(chat_id) < 0:
        bot_uname = os.environ.get("BOT_USERNAME", "tahasbot")
        tok = lyrics_token(str(cache_key))
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("⚡️ رفتن به بات طاها", url=f"https://ble.ir/{bot_uname}?start=opt_{tok}")
        ]])
    return track_actions_kb(str(cache_key), chat_id)


def find_track(token: str):
    """Resolve a lyrics_token back to its cached track document."""
    doc = LOCAL_META.get(token)
    if doc and doc.get("file_id"):
        return doc
    if music_cache is not None:
        try:
            found = (music_cache.find_one({"lyrics_key": token})
                     or music_cache.find_one({"vid_id": token})
                     or music_cache.find_one({"spotify_id": token})
                     or music_cache.find_one({"deezer_id": token}))
            if found:
                return found
        except Exception as e:
            print(f"find_track error: {e}", flush=True)
    return doc


def clean_track_title(title: str) -> str:
    """Strip video noise so a YouTube title can be matched against a lyrics DB."""
    t = re.sub(r'\s*[\(\[][^)\]]*[\)\]]', '', title or '')
    t = re.sub(r'(?i)\s*\b(official\s*(music\s*)?video|lyrics?\s*video|lyrics?|audio|hd|4k|remaster(ed)?|visualizer)\b', '', t)
    return t.strip(" -–—\t")


def clean_artist_name(artist: str) -> str:
    """Turn a YouTube channel name into something closer to a real artist name."""
    a = re.sub(r'(?i)\s*-\s*topic$', '', artist or '')
    a = re.sub(r'(?i)\s*(vevo|official)\s*$', '', a).strip()
    a = re.sub(r'(?i)music$', '', a).strip()  # "EminemMusic" -> "Eminem"
    return a.strip()


def lrclib_lookup(title: str, artist: str):
    """Fetch lyrics for a track from lrclib.

    The old query was q="<title> <artist>", which failed for anything uploaded
    by a music-video channel: the channel name ("EminemMusic") poisons the
    search. YouTube titles are usually "Artist - Track (Official Video)", so
    split that apart and use lrclib's structured track_name/artist_name fields,
    then validate the artist — a track_name-only search happily returns a
    different act's song of the same name.
    """
    ct = clean_track_title(title)
    ca = clean_artist_name(artist)
    split_artist = split_track = None
    if " - " in ct:
        a_part, t_part = ct.split(" - ", 1)
        split_artist, split_track = a_part.strip(), t_part.strip()

    attempts = []
    if split_track:
        attempts.append({"track_name": split_track, "artist_name": split_artist})
        if ca:
            attempts.append({"track_name": split_track, "artist_name": ca})
        attempts.append({"track_name": split_track})
    attempts.append({"track_name": ct, "artist_name": ca} if ca else {"track_name": ct})
    attempts.append({"q": ct})
    if ca:
        attempts.append({"q": f"{ct} {ca}"})

    want_artist = _norm(split_artist or ca)
    want_track = _norm(split_track or ct)
    fallback = None

    for params in attempts:
        try:
            data = requests.get("https://lrclib.net/api/search",
                                params=params, timeout=8).json()
        except Exception as e:
            print(f"lrclib error: {e}", flush=True)
            continue
        if not isinstance(data, list):
            continue
        for item in data[:8]:
            if not (item.get("syncedLyrics") or item.get("plainLyrics")):
                continue
            ia, it = _norm(item.get("artistName")), _norm(item.get("trackName"))
            track_ok = bool(it) and (it in want_track or want_track in it)
            artist_ok = (not want_artist) or (bool(ia) and (ia in want_artist or want_artist in ia))
            if track_ok and artist_ok:
                return item
            if fallback is None and track_ok and not want_artist:
                fallback = item
    return fallback


def apply_effect_sync(src_path: str, out_path: str, filter_chain: str):
    """Re-encode with an ffmpeg filter chain. Blocking; call via to_thread."""
    subprocess.run(
        [FFMPEG_BIN, "-y", "-loglevel", "error", "-i", src_path,
         "-af", filter_chain, "-c:a", "libmp3lame", "-b:a", "192k", out_path],
        check=True, capture_output=True, timeout=600
    )


STRINGS.update({
    "effect_source_not_found": {"fa": "❌ فایل اصلی این آهنگ یافت نشد.", "en": "❌ This song's original file wasn't found.", "ru": "❌ Оригинальный файл этой песни не найден.", "es": "❌ No se encontró el archivo original de esta canción."},
    "effect_feature_disabled": {"fa": "❌ این قابلیت روی سرور فعلی در دسترس نیست.", "en": "❌ This feature isn't available on the current server.", "ru": "❌ Эта функция недоступна на текущем сервере.", "es": "❌ Esta función no está disponible en el servidor actual."},
    "effect_building": {"fa": "🎛 **در حال ساخت نسخه {label}...**\nاین نسخه فقط یک‌بار ساخته می‌شود.", "en": "🎛 **Building the {label} version...**\nThis version is built only once.", "ru": "🎛 **Создание версии {label}...**\nЭта версия создаётся только один раз.", "es": "🎛 **Generando la versión {label}...**\nEsta versión se genera solo una vez."},
    "effect_build_failed": {"fa": "❌ ساخت این افکت با خطا مواجه شد.", "en": "❌ Failed to build this effect.", "ru": "❌ Не удалось создать этот эффект.", "es": "❌ No se pudo generar este efecto."},
})

async def process_effect(client: Client, chat_id: int, msg: Message, token: str, code: str):
    """Deliver an effect variant, reusing the cached original instead of YouTube."""
    _, chain = EFFECTS[code]
    label = effect_label(chat_id, code)
    doc = find_track(token)
    if not doc or not doc.get("file_id"):
        return await msg.edit_text(t(chat_id, "effect_source_not_found"))

    base_key = str(doc.get("vid_id") or token)
    variant_key = f"{base_key}:{code}"
    title, artist = doc.get("title", "Unknown"), doc.get("artist", "")

    cached = get_cached_file(variant_key)
    if cached:
        await client.send_audio(chat_id, audio=cached, title=f"{title} ({label})",
                                performer=artist, caption=f"{label}",
                                reply_markup=track_actions_kb(variant_key, chat_id))
        return await msg.delete()

    if not FFMPEG_AVAILABLE:
        return await msg.edit_text(t(chat_id, "effect_feature_disabled"))

    await msg.edit_text(t(chat_id, "effect_building", label=label))
    src = out = None
    try:
        async with MUSIC_SEMAPHORE:
            # Pulling the original back from Telegram avoids re-hitting YouTube,
            # which is the slowest part of the whole pipeline.
            src = await client.download_media(doc["file_id"],
                                              file_name=f"music_downloads/fx_{code}_src")
            out = f"music_downloads/fx_{code}_{abs(hash(base_key)) % 10**8}.mp3"
            t0 = time.time()
            await asyncio.to_thread(apply_effect_sync, src, out, chain)
            print(f"⏱ effect {code}: {time.time() - t0:.1f}s", flush=True)

        sent = await client.send_audio(
            chat_id, audio=out, title=f"{title} ({label})", performer=artist,
            caption=f"{label}", tag_source=src, album=doc.get("album") or None,
            reply_markup=track_actions_kb(variant_key, chat_id)
        )
        await msg.delete()
        media = sent.audio or sent.document
        if media is not None:
            save_cached_file(variant_key, media.file_id, f"{title} ({label})", artist,
                             doc.get("duration", 0), chat_id)
    except Exception as e:
        print(f"Effect error:\n{traceback.format_exc()}", flush=True)
        try:
            await msg.edit_text(t(chat_id, "effect_build_failed"))
        except Exception:
            await client.send_message(chat_id, t(chat_id, "effect_build_failed"))
    finally:
        for p in (src, out):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


def itunes_album_of(artist: str, title: str):
    """Find the album a track belongs to, then its full tracklist and cover."""
    try:
        r = requests.get("https://itunes.apple.com/search",
                         params={"term": f"{artist} {title}", "entity": "song", "limit": 5},
                         timeout=8)
        a_norm, t_norm = _norm(artist), _norm(title)
        coll_id = album_name = None
        for item in (r.json().get("results") or []):
            ia, it = _norm(item.get("artistName")), _norm(item.get("trackName"))
            if not ia or not it:
                continue
            if ((not a_norm or ia in a_norm or a_norm in ia)
                    and (it in t_norm or t_norm in it)):
                coll_id = item.get("collectionId")
                album_name = item.get("collectionName")
                break
        if not coll_id:
            return None
        lk = requests.get("https://itunes.apple.com/lookup",
                          params={"id": coll_id, "entity": "song", "limit": 100},
                          timeout=8).json()
        tracks_raw = [x for x in (lk.get("results") or []) if x.get("wrapperType") == "track"]
        tracks_raw.sort(key=lambda x: x.get("trackNumber") or 0)
        cover = ""
        year = ""
        if tracks_raw:
            cover = (tracks_raw[0].get("artworkUrl100") or "").replace("100x100bb", "600x600bb")
            year = (tracks_raw[0].get("releaseDate") or "")[:4]
        tracks = []
        for t in tracks_raw:
            if t.get("trackName"):
                tracks.append({
                    "name": t.get("trackName"),
                    "artist": t.get("artistName") or artist,
                    "duration": int((t.get("trackTimeMillis") or 0) // 1000),
                    "track_number": t.get("trackNumber") or 0
                })
        return {
            "name": album_name,
            "album": album_name,
            "artist": tracks_raw[0].get("artistName") if tracks_raw else artist,
            "cover": cover,
            "year": year,
            "tracks": tracks
        }
    except Exception as e:
        print(f"itunes album error: {e}", flush=True)
    return None


# ---------------- 4.7 SPOTIFY METADATA ENGINE ----------------
# Spotify's API never serves audio (DRM), so this is metadata-only: accurate
# title/artist/album/cover from Spotify's own catalog, used either to feed
# the existing YouTube-download pipeline (track links, search) or to build a
# real tracklist for the album/playlist bulk-download path (download_album
# already accepts exactly this {"album": name, "tracks": [{"name","artist"}]}
# shape, so no changes were needed there). Auth is client-credentials (app
# access, not a user's account), token cached in memory until near expiry.
_SPOTIFY_TOKEN_CACHE = {"token": None, "expires": 0.0}


def get_spotify_token_sync():
    if not (SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET):
        return None
    if _SPOTIFY_TOKEN_CACHE["token"] and time.time() < _SPOTIFY_TOKEN_CACHE["expires"] - 30:
        return _SPOTIFY_TOKEN_CACHE["token"]
    try:
        auth = base64.b64encode(f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()).decode()
        r = requests.post(
            "https://accounts.spotify.com/api/token",
            headers={"Authorization": f"Basic {auth}"},
            data={"grant_type": "client_credentials"}, timeout=15)
        r.raise_for_status()
        j = r.json()
        _SPOTIFY_TOKEN_CACHE["token"] = j["access_token"]
        _SPOTIFY_TOKEN_CACHE["expires"] = time.time() + int(j.get("expires_in") or 3600)
        return _SPOTIFY_TOKEN_CACHE["token"]
    except Exception as e:
        print(f"spotify auth error: {e}", flush=True)
        return None


def spotify_api_sync(path: str, params: dict = None):
    tok = get_spotify_token_sync()
    if not tok:
        return None
    try:
        r = requests.get(f"https://api.spotify.com/v1/{path}",
                         headers={"Authorization": f"Bearer {tok}"}, params=params, timeout=15)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception as e:
        print(f"spotify api error ({path}): {e}", flush=True)
        return None


def spotify_track_info_sync(track_id: str):
    j = spotify_api_sync(f"tracks/{track_id}")
    if not j:
        return None
    artists = [a["name"] for a in (j.get("artists") or [])]
    album = j.get("album") or {}
    images = album.get("images") or []
    return {
        "title": j.get("name") or "",
        "artist": artists[0] if artists else "",
        "artists_all": ", ".join(artists),
        "album": album.get("name"),
        "cover": images[0].get("url") if images else None,
        "duration": int((j.get("duration_ms") or 0) // 1000),
        "year": (album.get("release_date") or "")[:4],
        "isrc": (j.get("external_ids") or {}).get("isrc"),
    }


def spotify_album_tracks_sync(album_id: str):
    j = spotify_api_sync(f"albums/{album_id}")
    if not j:
        return None
    tracks = []
    items = (j.get("tracks", {}).get("items") or [])
    for idx, t in enumerate(items, 1):
        artists = [a["name"] for a in (t.get("artists") or [])]
        if t.get("name"):
            tracks.append({
                "name": t["name"],
                "id": t.get("id"),
                "artist": artists[0] if artists else "",
                "artists_all": ", ".join(artists),
                "duration": int((t.get("duration_ms") or 0) // 1000),
                "track_number": t.get("track_number") or idx,
            })
    cover = ((j.get("images") or [{}])[0]).get("url")
    alb_artists = [a["name"] for a in (j.get("artists") or [])]
    return {
        "name": j.get("name") or "",
        "album": j.get("name") or "",
        "artist": alb_artists[0] if alb_artists else "",
        "cover": cover,
        "year": (j.get("release_date") or "")[:4],
        "total_tracks": j.get("total_tracks") or len(tracks),
        "tracks": tracks
    }


def spotify_playlist_tracks_sync(playlist_id: str, limit: int = 30):
    j = spotify_api_sync(f"playlists/{playlist_id}",
                         params={"fields": "name,images,tracks.items(track(id,name,artists(name),duration_ms,track_number,external_ids,album(name,images,release_date)))"})
    if not j:
        return None
    tracks = []
    items = (j.get("tracks", {}).get("items") or [])[:limit]
    for idx, it in enumerate(items, 1):
        t = it.get("track") or {}
        if not t.get("name"):
            continue
        artists = [a["name"] for a in (t.get("artists") or [])]
        album = t.get("album") or {}
        cover = ((album.get("images") or [{}])[0]).get("url")
        tracks.append({
            "name": t["name"],
            "id": t.get("id"),
            "isrc": (t.get("external_ids") or {}).get("isrc"),
            "artist": artists[0] if artists else "",
            "artists_all": ", ".join(artists),
            "album": album.get("name"),
            "cover": cover,
            "duration": int((t.get("duration_ms") or 0) // 1000),
            "year": (album.get("release_date") or "")[:4],
            "track_number": t.get("track_number") or idx,
        })
    pl_cover = ((j.get("images") or [{}])[0]).get("url")
    return {
        "name": j.get("name") or "",
        "album": j.get("name") or "",
        "cover": pl_cover,
        "is_playlist": True,
        "tracks": tracks
    }


def spotify_search_tracks_sync(query: str, limit: int = 30):
    """Fetches multiple pages and filters out Spotify recommendation slop.
    Guarantees that results match the search query words and ranks official,
    popular, exact title hits first.
    """
    pages = 1 if limit <= 10 else 2
    seen = {}  # (norm_title, norm_artist) -> index in out
    out = []
    q_clean = _norm(query)
    q_tokens = [tok for tok in re.split(r'\s+', q_clean) if len(tok) >= 2]

    for page in range(pages):
        j = spotify_api_sync("search", params={"q": query, "type": "track", "limit": 10, "offset": page * 10})
        if not j:
            break
        items = (j.get("tracks", {}).get("items") or [])
        if not items:
            break
        for t in items:
            if not t.get("name"):
                continue
            artists = [a["name"] for a in (t.get("artists") or [])]
            artist = artists[0] if artists else ""
            t_name = t["name"]

            # Relevance filter: Ensure at least one significant token from query appears in title or artist
            if q_tokens:
                target_str = _norm(f"{t_name} {artist} {' '.join(artists)}")
                if not any(tok in target_str for tok in q_tokens):
                    continue

            key = (_norm(t_name), _norm(artist))
            album = t.get("album") or {}
            images = album.get("images") or []
            duration_sec = int((t.get("duration_ms") or 0) // 1000)
            release_year = (album.get("release_date") or "")[:4]
            cover_img = images[1].get("url") if len(images) > 1 else (images[0].get("url") if images else None)
            entry = {
                "cover_big": images[0].get("url") if images else None,
                "isrc": (t.get("external_ids") or {}).get("isrc") or "",
                "id": t.get("id"),
                "title": t_name, "artist": artist,
                "artists_all": ", ".join(artists),
                "album": album.get("name"),
                "explicit": bool(t.get("explicit")),
                "popularity": int(t.get("popularity") or 0),
                "duration": duration_sec,
                "dur_str": f"{duration_sec//60}:{duration_sec%60:02d}" if duration_sec else "",
                "year": release_year,
                "cover": cover_img,
                "thumb": cover_img,
            }
            if key in seen:
                prev = out[seen[key]]
                better = (entry["explicit"] and not prev["explicit"]) or \
                         (entry["explicit"] == prev["explicit"] and entry["popularity"] > prev["popularity"])
                if better:
                    out[seen[key]] = entry
                continue
            seen[key] = len(out)
            out.append(entry)
        if len(items) < 10:
            break

    def _rank_score(e):
        score = e.get("popularity", 0)
        norm_t = _norm(e.get("title", ""))
        norm_a = _norm(e.get("artist", ""))
        if norm_t == q_clean:
            score += 1000
        elif norm_t.startswith(q_clean):
            score += 500
        elif norm_a == q_clean:
            score += 300
        return score

    out.sort(key=_rank_score, reverse=True)
    return out[:limit]


def spotify_album_of(artist: str, title: str):
    """Same job as itunes_album_of (find the album a track belongs to and
    its full tracklist) but against Spotify's much more complete catalog --
    called first, with itunes_album_of kept as the fallback for whatever
    Spotify doesn't have."""
    j = spotify_api_sync("search", params={"q": f"track:{title} artist:{artist}", "type": "track", "limit": 5})
    if not j:
        return None
    items = (j.get("tracks") or {}).get("items") or []
    a_norm, t_norm = _norm(artist), _norm(title)
    album_id = None
    for it in items:
        artists = [a["name"] for a in (it.get("artists") or [])]
        ia, it_norm = _norm(artists[0] if artists else ""), _norm(it.get("name"))
        if not ia or not it_norm:
            continue
        if (ia in a_norm or a_norm in ia) and (it_norm in t_norm or t_norm in it_norm):
            album_id = (it.get("album") or {}).get("id")
            break
    if not album_id:
        return None
    alb = spotify_album_tracks_sync(album_id)
    if not alb or not alb.get("tracks"):
        return None
    return {
        "name": alb["name"],
        "album": alb["name"],
        "artist": alb.get("artist") or artist,
        "cover": alb.get("cover"),
        "year": alb.get("year"),
        "tracks": alb["tracks"]
    }


STRINGS.update({
    "spotify_no_results": {"fa": "❌ نتیجه‌ای در اسپاتیفای پیدا نشد.", "en": "❌ No results found on Spotify.", "ru": "❌ Результаты в Spotify не найдены.", "es": "❌ No se encontraron resultados en Spotify."},
    "spotify_results_header": {"fa": "🎧 **نتایج اسپاتیفای برای:** `{query}`", "en": "🎧 **Spotify results for:** `{query}`", "ru": "🎧 **Результаты Spotify для:** `{query}`", "es": "🎧 **Resultados de Spotify para:** `{query}`"},
    "spotify_results_meta": {
        "fa": "_{sort} • صفحه {page} از {pages}_",
        "en": "_{sort} • page {page} of {pages}_",
        "ru": "_{sort} • стр. {page} из {pages}_",
        "es": "_{sort} • página {page} de {pages}_",
    },
    "sort_popular": {"fa": "⭐ محبوب", "en": "⭐ Popular", "ru": "⭐ Популярные", "es": "⭐ Populares"},
    "spotify_search_error": {"fa": "❌ خطا در جستجوی اسپاتیفای.", "en": "❌ Spotify search error.", "ru": "❌ Ошибка поиска в Spotify.", "es": "❌ Error en la búsqueda de Spotify."},
})

SPOTIFY_SORT_MODE_KEYS = {"r": "sort_relevant", "pop": "sort_popular", "new": "sort_new", "dur": "sort_duration"}



# ---- Spotify without API credentials ----
# The Spotify developer app behind SPOTIFY_CLIENT_ID started answering
# "invalid_client" (deleted/revoked app), which silently broke Spotify links,
# Spotify search and tag enrichment. Every Spotify helper now falls back to
# the public embed page (no auth, has titles/artists/durations/tracklists)
# plus Deezer for covers/albums/ISRCs, so the bot keeps working until new
# credentials are configured -- and keeps working if they break again.
_SP_EMBED_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
_SP_STATUS = {"api_ok": None, "checked": 0.0}


def spotify_api_available() -> bool:
    if time.time() - _SP_STATUS["checked"] > 600:
        _SP_STATUS["api_ok"] = bool(get_spotify_token_sync())
        _SP_STATUS["checked"] = time.time()
    return bool(_SP_STATUS["api_ok"])


def _spotify_embed_entity(kind: str, sid: str):
    try:
        r = requests.get(f"https://open.spotify.com/embed/{kind}/{sid}", headers=_SP_EMBED_UA, timeout=12)
        m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text)
        if r.status_code != 200 or not m:
            return None
        return json.loads(m.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    except Exception as e:
        print(f"spotify embed error ({kind}/{sid}): {e}", flush=True)
        return None


def _spotify_oembed_cover(kind: str, sid: str) -> str:
    try:
        j = requests.get("https://open.spotify.com/oembed",
                         params={"url": f"https://open.spotify.com/{kind}/{sid}"}, headers=_SP_EMBED_UA, timeout=10).json()
        return j.get("thumbnail_url") or ""
    except Exception:
        return ""


def _deezer_match(title: str, artist: str, duration: int = 0):
    """Best Deezer track for title/artist -> full Deezer track info, or None."""
    try:
        for t_ in rega_music.search_tracks(f"{_first_artist(artist)} {title}", 8):
            if not is_title_match(title, t_.get("title") or "", artist):
                continue
            a0 = _norm(t_.get("artist"))
            if artist and a0 and not (a0 in _norm(artist) or _norm(_first_artist(artist)) in a0):
                continue
            if duration and t_.get("duration") and abs(int(t_["duration"]) - duration) > 15:
                continue
            return rega_music.get_track_info(t_["id"]) or t_
    except Exception as e:
        print(f"deezer match error: {e}", flush=True)
    return None


def _embed_artists(e: dict) -> str:
    names = [a.get("name") for a in (e.get("artists") or []) if a.get("name")]
    return ", ".join(names) or (e.get("subtitle") or "").replace("\xa0", " ").strip()


def _spotify_track_info_embed(track_id: str):
    e = _spotify_embed_entity("track", track_id)
    if not e or not (e.get("name") or e.get("title")):
        return None
    title = e.get("name") or e.get("title")
    artists = _embed_artists(e)
    dur = int((e.get("duration") or 0) // 1000)
    info = {"title": title, "artist": _first_artist(artists), "artists_all": artists, "duration": dur,
            "year": ((e.get("releaseDate") or {}).get("isoString") or "")[:4],
            "cover": _spotify_oembed_cover("track", track_id), "album": None, "isrc": None}
    dz = _deezer_match(title, artists, dur)
    if dz:
        info["album"] = dz.get("album") or None
        info["isrc"] = dz.get("isrc") or None
        info["cover"] = info["cover"] or dz.get("thumb")
    return info


def _spotify_collection_embed(kind: str, sid: str, limit: int = 100):
    e = _spotify_embed_entity(kind, sid)
    if not e or not e.get("trackList"):
        return None
    cover = _spotify_oembed_cover(kind, sid)
    tracks = []
    for i, tr in enumerate(e["trackList"][:limit], 1):
        uri = tr.get("uri") or ""
        artist = (tr.get("subtitle") or "").replace("\xa0", " ").strip()
        tracks.append({"name": tr.get("title") or "", "id": uri.split(":")[-1] if "track:" in uri else None,
                       "artist": _first_artist(artist), "artists_all": artist,
                       "duration": int((tr.get("duration") or 0) // 1000), "track_number": i})
    tracks = [x for x in tracks if x["name"]]
    out = {"name": e.get("name") or e.get("title") or "", "album": e.get("name") or e.get("title") or "",
           "artist": (e.get("subtitle") or "").replace("\xa0", " ").strip(), "cover": cover, "tracks": tracks}
    if kind == "album":
        out["total_tracks"] = len(tracks)
        out["year"] = ((e.get("releaseDate") or {}).get("isoString") or "")[:4]
    else:
        out["is_playlist"] = True
    return out


def _spotify_search_via_deezer(query: str, limit: int = 30):
    out = []
    for t_ in rega_music.search_tracks(query, limit):
        dur = int(t_.get("duration") or 0)
        out.append({"id": None, "deezer_id": t_.get("id"), "title": t_.get("title"), "artist": t_.get("artist"),
                    "artists_all": t_.get("artist"), "album": t_.get("album"), "explicit": False, "popularity": 0,
                    "duration": dur, "dur_str": f"{dur//60}:{dur%60:02d}" if dur else "", "year": "",
                    "cover": t_.get("thumb"), "cover_big": t_.get("thumb"), "thumb": t_.get("thumb"),
                    "isrc": t_.get("isrc") or ""})
    if len(out) < min(8, limit):
        # Deezer is thin for Persian/regional music; YouTube Music isn't.
        seen = {(_norm(o["title"]), _norm(_first_artist(o["artist"] or ""))) for o in out}
        for r in ytm_search_sync(query, "songs", limit):
            m = meta_from_ytm(r)
            k = (_norm(m["title"]), _norm(_first_artist(m["artist"])))
            if not m["yt_id"] or k in seen:
                continue
            seen.add(k)
            d = m["duration"]
            out.append({"id": None, "yt_id": m["yt_id"], "title": m["title"], "artist": _first_artist(m["artist"]),
                        "artists_all": m["artist"], "album": m["album"], "explicit": False, "popularity": 0,
                        "duration": d, "dur_str": f"{d//60}:{d%60:02d}" if d else "", "year": m["year"],
                        "cover": m["cover"], "cover_big": m["cover"], "thumb": m["cover"], "isrc": ""})
            if len(out) >= limit:
                break
    return out


_spotify_track_info_api = spotify_track_info_sync
_spotify_album_tracks_api = spotify_album_tracks_sync
_spotify_playlist_tracks_api = spotify_playlist_tracks_sync
_spotify_search_tracks_api = spotify_search_tracks_sync


def spotify_track_info_sync(track_id: str):
    return (_spotify_track_info_api(track_id) if spotify_api_available() else None) or _spotify_track_info_embed(track_id)


def spotify_album_tracks_sync(album_id: str):
    return (_spotify_album_tracks_api(album_id) if spotify_api_available() else None) or _spotify_collection_embed("album", album_id)


def spotify_playlist_tracks_sync(playlist_id: str, limit: int = 30):
    return ((_spotify_playlist_tracks_api(playlist_id, limit) if spotify_api_available() else None)
            or _spotify_collection_embed("playlist", playlist_id, limit))


def spotify_search_tracks_sync(query: str, limit: int = 30):
    res = _spotify_search_tracks_api(query, limit) if spotify_api_available() else []
    return res or _spotify_search_via_deezer(query, limit)


def spotify_sort_modes(chat_id) -> dict:
    return {k: t(chat_id, key) for k, key in SPOTIFY_SORT_MODE_KEYS.items()}


def sort_spotify_tracks(items: list, mode: str) -> list:
    if mode == "pop":
        return sorted(items, key=lambda x: x.get("popularity", 0), reverse=True)
    if mode == "new":
        return sorted(items, key=lambda x: x.get("year") or "0", reverse=True)
    if mode == "dur":
        return sorted(items, key=lambda x: x.get("duration") or 999999)
    return items  # 'r' -- Spotify's own relevance order


async def render_spotify_search(msg: Message, query: str, chat_id: int, page: int = 1, sort: str = "r"):
    try:
        store = user_data.setdefault(chat_id, {})
        if store.get("sp_query") != query or not store.get("sp_all"):
            results = await asyncio.to_thread(spotify_search_tracks_sync, query, 30)
            store["sp_all"] = results
            store["sp_query"] = query

        sp_all = store.get("sp_all") or []
        back = InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_music")]])
        if not sp_all:
            return await msg.edit_text(t(chat_id, "spotify_no_results"), reply_markup=back)

        sort = sort if sort in SPOTIFY_SORT_MODE_KEYS else "r"
        items = sort_spotify_tracks(sp_all, sort)
        modes = spotify_sort_modes(chat_id)

        page_size = 5
        pages = max(1, (len(items) + page_size - 1) // page_size)
        page = max(1, min(page, pages))
        chunk = items[(page - 1) * page_size: page * page_size]
        store["sp_page_items"] = chunk

        lines = [t(chat_id, "spotify_results_header", query=query),
                 t(chat_id, "spotify_results_meta", sort=modes[sort], page=page, pages=pages) + "\n"]
        btns = []
        for i, trk in enumerate(chunk):
            gi = (page - 1) * page_size + i
            badge = MEDALS[gi] if (sort == "r" and gi < 3) else f"`{gi+1}`"
            mark = " 🅴" if trk.get("explicit") else ""
            pop_bar = "🔥" * max(1, round((trk.get("popularity", 0) / 100) * 4)) if trk.get("popularity") else ""
            lines.append(f"{badge} **{trk['title']}**{mark}")
            meta_bits = [f"👤 {trk['artists_all'][:30]}"]
            if trk.get("dur_str"): meta_bits.append(f"⏱ {trk['dur_str']}")
            if trk.get("year"): meta_bits.append(f"🗓 {trk['year']}")
            if trk.get("album"): meta_bits.append(f"💿 {trk['album'][:20]}")
            lines.append("      " + " ▫️ ".join(meta_bits) + (f" ▫️ {pop_bar}" if pop_bar else "") + "\n")
            btns.append(InlineKeyboardButton(str(i + 1), callback_data=f"sp_pick_{i}"))

        rows = [btns]
        rows.append([InlineKeyboardButton(f"‹{lbl}›" if k == sort else lbl,
                                          callback_data=f"sppage_1_{k}")
                     for k, lbl in modes.items()])
        nav = []
        if page > 1: nav.append(InlineKeyboardButton(t(chat_id, "btn_prev"), callback_data=f"sppage_{page-1}_{sort}"))
        nav.append(InlineKeyboardButton(f"· {page}/{pages} ·", callback_data="noop"))
        if page < pages: nav.append(InlineKeyboardButton(t(chat_id, "btn_next"), callback_data=f"sppage_{page+1}_{sort}"))
        rows.append(nav)
        rows.append([InlineKeyboardButton(t(chat_id, "btn_random"), callback_data="sp_rand"),
                     InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_music")])
        await msg.edit_text("\n".join(lines)[:4000], reply_markup=InlineKeyboardMarkup(rows))
    except Exception:
        traceback.print_exc()
        try:
            await msg.edit_text(t(chat_id, "spotify_search_error"), reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_music")]]))
        except Exception:
            pass


# ---------------- 4.8 UNIFIED TRACK PIPELINE ----------------
# One path for every song the bot delivers, whatever the source (Spotify,
# YouTube Music, SoundCloud, Deezer, AudD, AI suggestions, albums, radio,
# inline mode):
#
#   meta -> cache lookup (any alias: YouTube id / SoundCloud id / Spotify id /
#   Deezer id / ISRC) -> resolve the actual audio source -> download ->
#   MP3 + ID3 tags + square cover + "Artist - Title.mp3" -> ONE upload, to the
#   cache channel -> cache doc -> delivered by file_id (instant, keeps the tags)
#
# Before this there were six different download paths, each writing its own
# cache doc shape (vid_id docs, deezer_id docs, title+artist docs), none of
# them putting tags inside the file, and inline mode pre-downloaded the top 3
# results of every keystroke into the cache channel.
from modules.audio_meta import (safe_filename, write_tags, fetch_image, to_square_jpeg,
                                make_thumb, to_mp3, probe_duration)

_TRACK_LOCKS = {}
_ytm_local = threading.local()


def _ytm():
    """Per-thread YTMusic client (it holds a requests session)."""
    cli = getattr(_ytm_local, "cli", None)
    if cli is None:
        from ytmusicapi import YTMusic
        cli = _ytm_local.cli = YTMusic()
    return cli


def _s(v) -> str:
    return audio_meta._clean(v)


def make_meta(source: str, **kw) -> dict:
    m = {"source": source, "title": "", "artist": "", "album": "", "year": "", "duration": 0,
         "cover": "", "isrc": "", "spotify_id": "", "deezer_id": "", "yt_id": "", "sc_id": "",
         "sc_url": "", "track_no": 0, "total": 0}
    for k, v in kw.items():
        if v is None:
            continue
        m[k] = v if k in ("duration", "track_no", "total") else _s(v)
    m["duration"] = int(float(m.get("duration") or 0))
    return m


def _ytm_thumb(thumbs, size=544) -> str:
    if not thumbs:
        return ""
    url = (thumbs[-1] or {}).get("url") or ""
    if "googleusercontent.com" in url:
        url = re.sub(r"=w\d+-h\d+.*$", f"=w{size}-h{size}-l90-rj", url)
    return url


def _sc_thumb(entry) -> str:
    thumbs = entry.get("thumbnails") or []
    for t in thumbs:
        if t.get("id") == "t500x500" and t.get("url"):
            return t["url"]
    url = entry.get("thumbnail") or (thumbs[-1].get("url") if thumbs else "") or ""
    return re.sub(r"-(large|mini|small|t\d+x\d+|crop|original)\.(jpg|png)$", r"-t500x500.\2", url)


def meta_from_spotify(t: dict) -> dict:
    sp_id = t.get("id") or t.get("spotify_id")
    # Fallback search results carry a deezer_id / yt_id instead.
    return make_meta("spotify" if sp_id else ("ytm" if t.get("yt_id") else "deezer"), title=t.get("title"),
                     artist=t.get("artists_all") or t.get("artist"),
                     album=t.get("album"), year=t.get("year"), duration=t.get("duration"),
                     cover=t.get("cover_big") or t.get("cover"), isrc=t.get("isrc"),
                     spotify_id=sp_id, deezer_id=t.get("deezer_id"), yt_id=t.get("yt_id"))


def meta_from_deezer(t: dict) -> dict:
    return make_meta("deezer", title=t.get("title"), artist=t.get("artist"), album=t.get("album"),
                     year=(t.get("release_date") or "")[:4], duration=t.get("duration"),
                     cover=t.get("thumb"), isrc=t.get("isrc"), deezer_id=t.get("id"))


def _mmss_to_sec(v) -> int:
    try:
        parts = [int(x) for x in str(v or "").split(":")]
        out = 0
        for x in parts:
            out = out * 60 + x
        return out
    except ValueError:
        return 0


def meta_from_ytm(r: dict) -> dict:
    artists = ", ".join(a.get("name", "") for a in (r.get("artists") or []) if a.get("name"))
    album = (r.get("album") or {}).get("name") if isinstance(r.get("album"), dict) else r.get("album")
    dur = r.get("duration_seconds") or _mmss_to_sec(r.get("duration"))
    return make_meta("ytm", title=clean_song_title(r.get("title") or ""), artist=artists,
                     album=album, year=r.get("year"), duration=dur,
                     cover=_ytm_thumb(r.get("thumbnails")), yt_id=r.get("videoId"))


def meta_from_sc(e: dict) -> dict:
    title, artist = e.get("title") or "", e.get("uploader") or ""
    if " - " in title and _norm(title.split(" - ", 1)[0]) == _norm(artist):
        title = title.split(" - ", 1)[1]
    return make_meta("sc", title=title, artist=artist, duration=e.get("duration"),
                     cover=_sc_thumb(e), sc_id=str(e.get("id") or ""),
                     sc_url=e.get("webpage_url") or e.get("url") or "")


def meta_from_doc(doc: dict) -> dict:
    """Rebuild a meta from a (possibly legacy) cache document."""
    key = str(doc.get("vid_id") or "")
    yt = key if re.fullmatch(r"[0-9A-Za-z_-]{11}", key) else ""
    sc_id = key[3:] if key.startswith("sc:") else (doc.get("sc_id") or "")
    sc_url = doc.get("source_url") if "soundcloud" in str(doc.get("source_url") or "") else ""
    if not sc_url and key.startswith("http") and "soundcloud" in key:
        sc_url = key
    src = "sc" if (sc_id or sc_url) else ("spotify" if doc.get("spotify_id") else ("deezer" if doc.get("deezer_id") else "yt"))
    artist = clean_artist_name(doc.get("artist") or "")
    title = clean_song_title(doc.get("title") or "")
    if " - " in title and _norm(title.split(" - ", 1)[0]) in (_norm(artist), ""):
        title = title.split(" - ", 1)[1]
    return make_meta(src, title=title, artist=artist, album=doc.get("album"), duration=doc.get("duration"),
                     yt_id=yt, sc_id=sc_id, sc_url=sc_url, spotify_id=doc.get("spotify_id"),
                     deezer_id=doc.get("deezer_id"), isrc=doc.get("isrc"), cover=doc.get("cover"))


def _alias_conditions(meta: dict) -> list:
    c = []
    if meta.get("yt_id"):
        c.append({"vid_id": meta["yt_id"]})
    if meta.get("sc_id"):
        c += [{"vid_id": f"sc:{meta['sc_id']}"}, {"sc_id": meta["sc_id"]}]
    if meta.get("spotify_id"):
        c.append({"spotify_id": meta["spotify_id"]})
    if meta.get("deezer_id"):
        c.append({"deezer_id": meta["deezer_id"]})
    if meta.get("isrc"):
        c.append({"isrc": meta["isrc"].upper()})
    return c


def cache_find(meta: dict):
    """Current-format cache doc for any of the track's aliases, or None."""
    if music_cache is None:
        return None
    conds = _alias_conditions(meta)
    if not conds:
        return None
    try:
        for doc in music_cache.find({"$or": conds, "tag_v": {"$gte": TAG_VERSION}, "derived": {"$ne": True}}).limit(3):
            if doc_file_id(doc) or doc.get("cache_msg_id"):
                return doc
    except Exception as e:
        print(f"cache_find error: {e}", flush=True)
    return None


def cache_find_many(metas: list) -> dict:
    """{index: doc} for a page of search results, in ONE Mongo query."""
    if music_cache is None or not metas:
        return {}
    conds = [c for m in metas for c in _alias_conditions(m)]
    if not conds:
        return {}
    try:
        docs = list(music_cache.find({"$or": conds, "tag_v": {"$gte": TAG_VERSION}, "derived": {"$ne": True}}))
    except Exception as e:
        print(f"cache_find_many error: {e}", flush=True)
        return {}
    out = {}
    for i, m in enumerate(metas):
        for d in docs:
            if not doc_file_id(d):
                continue
            if ((m.get("yt_id") and d.get("vid_id") == m["yt_id"])
                    or (m.get("sc_id") and (d.get("vid_id") == f"sc:{m['sc_id']}" or d.get("sc_id") == m["sc_id"]))
                    or (m.get("spotify_id") and d.get("spotify_id") == m["spotify_id"])
                    or (m.get("deezer_id") and d.get("deezer_id") == m["deezer_id"])
                    or (m.get("isrc") and d.get("isrc") == m["isrc"].upper())):
                out[i] = d
                break
    return out


def _first_artist(artist: str) -> str:
    return re.split(r",|&| feat\.| ft\.| x ", artist or "", maxsplit=1, flags=re.I)[0].strip()


def _pick_ytm_song(results: list, title: str, artist: str, duration: int):
    best, best_score = None, -10**9
    want_a = _norm(_first_artist(artist))
    for r in results or []:
        vid = r.get("videoId")
        if not vid or r.get("resultType") not in (None, "song", "video"):
            continue
        rt = r.get("title") or ""
        if not is_title_match(title, rt, artist):
            continue
        ra = _norm(" ".join(a.get("name", "") for a in (r.get("artists") or [])))
        d = int(r.get("duration_seconds") or 0)
        score = 0
        if _norm(rt) == _norm(title) or _norm(strip_features(rt)) == _norm(strip_features(title)):
            score += 100
        if want_a:
            score += 80 if (want_a in ra or (ra and ra in want_a)) else -60
        if duration and d:
            diff = abs(d - duration)
            score += 70 if diff <= 3 else 35 if diff <= 8 else 0 if diff <= 20 else -150
        if r.get("resultType") == "song":
            score += 40
        if is_live_marker(rt) and not is_live_marker(title):
            score -= 200
        if score > best_score:
            best, best_score = r, score
    return best if best_score >= 40 else None


def resolve_youtube_id_sync(meta: dict):
    """Find the official audio for a catalog (Spotify/Deezer/AudD/AI) track.

    YouTube Music's own song search first -- it returns the label "Topic"
    uploads with exact durations, so title+artist+duration pins the studio
    version. The ranked general YouTube search is the fallback.
    """
    if meta.get("yt_id"):
        return meta["yt_id"]
    title, artist, dur = meta.get("title", ""), meta.get("artist", ""), int(meta.get("duration") or 0)
    if not title:
        return None
    q = f"{_first_artist(artist)} {title}".strip()
    try:
        res = _ytm().search(q, filter="songs", limit=10)
        hit = _pick_ytm_song(res, title, artist, dur)
        if not hit:
            hit = _pick_ytm_song(_ytm().search(q, filter="videos", limit=10), title, artist, dur)
        if hit:
            if not meta.get("album") and isinstance(hit.get("album"), dict):
                meta["album"] = _s(hit["album"].get("name"))
            if not meta.get("cover"):
                meta["cover"] = _ytm_thumb(hit.get("thumbnails"))
            return hit["videoId"]
    except Exception as e:
        print(f"ytm resolve error for {q!r}: {e}", flush=True)
    try:
        results = search_official_smart_sync(q, 15, _first_artist(artist), title, dur or None)
        match = _pick_matching_result(results, title, artist)
        if match:
            return match["id"]
    except Exception as e:
        print(f"yt resolve fallback error for {q!r}: {e}", flush=True)
    return None


def enrich_meta_sync(meta: dict) -> dict:
    """Give YouTube/SoundCloud-sourced tracks catalog-quality tags + art.

    Only fills fields when Spotify's match is unambiguous (same title, same
    artist) -- a wrong album cover is worse than the uploader's artwork.
    """
    if meta.get("source") in ("spotify", "deezer") and meta.get("cover") and meta.get("album"):
        return meta
    title, artist = meta.get("title", ""), meta.get("artist", "")
    if not title:
        return meta
    try:
        j = spotify_api_sync("search", params={"q": f"track:{strip_features(title)} artist:{_first_artist(artist)}",
                                               "type": "track", "limit": 5})
        for it in ((j or {}).get("tracks") or {}).get("items") or []:
            names = [a["name"] for a in (it.get("artists") or [])]
            if not names or not is_title_match(title, it.get("name") or ""):
                continue
            a0 = _norm(names[0])
            if not (a0 and (a0 in _norm(artist) or _norm(_first_artist(artist)) in a0)):
                continue
            dur = int((it.get("duration_ms") or 0) // 1000)
            if meta.get("duration") and dur and abs(dur - meta["duration"]) > 15:
                continue
            alb = it.get("album") or {}
            imgs = alb.get("images") or []
            if meta.get("source") in ("yt", "ytm", "sc"):
                meta["title"] = _s(it.get("name")) or title
                meta["artist"] = ", ".join(names)
            meta["album"] = meta.get("album") or _s(alb.get("name"))
            meta["year"] = meta.get("year") or (alb.get("release_date") or "")[:4]
            if imgs and (not meta.get("cover") or meta.get("source") in ("yt", "sc")):
                meta["cover"] = imgs[0].get("url")
            meta["isrc"] = meta.get("isrc") or (it.get("external_ids") or {}).get("isrc") or ""
            meta["spotify_id"] = meta.get("spotify_id") or it.get("id") or ""
            break
        else:
            # No Spotify match (or Spotify API unavailable): Deezer has album,
            # 1000px cover and ISRC for most catalog music.
            dz = _deezer_match(strip_features(title), artist, int(meta.get("duration") or 0))
            if dz:
                if meta.get("source") in ("yt", "ytm", "sc"):
                    meta["title"] = _s(dz.get("title")) or title
                    meta["artist"] = _s(dz.get("artist")) or artist
                meta["album"] = meta.get("album") or _s(dz.get("album"))
                meta["year"] = meta.get("year") or (dz.get("release_date") or "")[:4]
                if dz.get("thumb") and (not meta.get("cover") or meta.get("source") in ("yt", "sc")):
                    meta["cover"] = dz["thumb"]
                meta["isrc"] = meta.get("isrc") or dz.get("isrc") or ""
                meta["deezer_id"] = meta.get("deezer_id") or str(dz.get("id") or "")
    except Exception as e:
        print(f"enrich error: {e}", flush=True)
    return meta


def _yt_dl_opts(outtmpl: str) -> dict:
    opts = {
        'outtmpl': outtmpl, 'quiet': True, 'no_warnings': True, 'noplaylist': True,
        'format': 'bestaudio[ext=m4a]/bestaudio/best[height<=480]',
        'concurrent_fragment_downloads': 8, 'http_chunk_size': 10485760,
        'js_runtimes': {'deno': {}},
        **NET_RETRY_OPTS,
    }
    ex = youtube_extractor_args() or {"youtube": {}}
    ex.setdefault("youtube", {}).setdefault("getpot_bgutil_baseurl", [POT_PROVIDER_URL])
    opts['extractor_args'] = ex
    if os.path.exists(YOUTUBE_COOKIES_FILE) and os.path.getsize(YOUTUBE_COOKIES_FILE) > 0:
        opts['cookiefile'] = YOUTUBE_COOKIES_FILE
    return opts


def download_source_audio_sync(meta: dict):
    """Download the raw audio for meta. Returns (path, info). Fills meta in place."""
    os.makedirs("music_downloads", exist_ok=True)
    stamp = f"{int(time.time() * 1000)}_{random.randint(100, 999)}"
    if meta.get("source") == "sc" and (meta.get("sc_url") or meta.get("sc_id")):
        url = meta.get("sc_url") or f"https://api.soundcloud.com/tracks/{meta['sc_id']}"
        opts = {'outtmpl': f'music_downloads/sc_{stamp}.%(ext)s', 'quiet': True, 'no_warnings': True,
                'format': 'bestaudio/best', **NET_RETRY_OPTS}
        info = timed_extract(opts, url, "soundcloud")
        path = collect_audio_file(f"music_downloads/sc_{stamp}")
        claimed = int(info.get("duration") or 0)
        real = probe_duration(path) if path else 0
        if path and claimed > 50 and real and real < claimed * 0.6 and real < 45:
            os.remove(path)
            raise RuntimeError(f"SoundCloud Go+ preview ({real}s of {claimed}s)")
        meta["sc_id"] = meta.get("sc_id") or str(info.get("id") or "")
        meta["sc_url"] = info.get("webpage_url") or meta.get("sc_url")
        meta["title"] = meta.get("title") or _s(info.get("title"))
        meta["artist"] = meta.get("artist") or _s(info.get("uploader"))
        meta["duration"] = meta.get("duration") or claimed
        if not meta.get("cover"):
            meta["cover"] = _sc_thumb(info)
        if not path:
            raise RuntimeError("SoundCloud returned no audio")
        return path, info

    vid = resolve_youtube_id_sync(meta)
    if not vid:
        raise RuntimeError(f"no YouTube match for {meta.get('artist')} - {meta.get('title')}")
    meta["yt_id"] = vid
    info = timed_extract(_yt_dl_opts(f'music_downloads/yt_{stamp}.%(ext)s'),
                         f"https://www.youtube.com/watch?v={vid}", f"youtube:{vid}")
    path = None
    for f in sorted(glob.glob(f"music_downloads/yt_{stamp}.*")):
        if Path(f).suffix.lower() in AUDIO_EXTS + ('.mp4',):
            path = f
            break
    if not path:
        raise RuntimeError("YouTube returned no audio")
    if meta.get("source") == "yt" and info.get("track"):
        # Official/"Topic" uploads carry real music metadata -- better than
        # the video title ("Eminem - Mockingbird") / channel ("EminemMusic").
        meta["title"] = _s(info.get("track"))
        meta["artist"] = _s(info.get("artist") or info.get("creator") or meta.get("artist"))
    if not meta.get("title"):
        meta["title"] = clean_song_title(info.get("title") or "")
    if not meta.get("artist"):
        meta["artist"] = _s(clean_artist_name(info.get("uploader") or info.get("channel") or ""))
    if meta.get("source") == "yt":
        meta["artist"] = clean_artist_name(meta["artist"])
        if " - " in meta["title"]:
            head, tail = meta["title"].split(" - ", 1)
            if _norm(head) and (_norm(head) in _norm(meta["artist"]) or _norm(meta["artist"]) in _norm(head)):
                meta["title"], meta["artist"] = tail.strip(), head.strip()
    meta["album"] = meta.get("album") or _s(info.get("album"))
    meta["duration"] = meta.get("duration") or int(info.get("duration") or 0)
    if not meta.get("cover"):
        meta["cover"] = f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg"
    return path, info


def finalize_track_sync(raw_path: str, meta: dict):
    """raw audio -> tagged MP3 with embedded square cover. Returns (mp3, thumb, duration)."""
    stamp = f"{int(time.time() * 1000)}_{random.randint(100, 999)}"
    mp3 = to_mp3(raw_path, f"music_downloads/final_{stamp}.mp3")
    cover = fetch_image(meta.get("cover"))
    if not cover and meta.get("yt_id"):
        cover = (fetch_image(f"https://i.ytimg.com/vi/{meta['yt_id']}/maxresdefault.jpg")
                 or fetch_image(f"https://i.ytimg.com/vi/{meta['yt_id']}/hqdefault.jpg"))
    cover = to_square_jpeg(cover) if cover else b""
    write_tags(mp3, title=meta.get("title"), artist=meta.get("artist"), album=meta.get("album"),
               year=meta.get("year"), track=meta.get("track_no") or None, total=meta.get("total") or None,
               cover=cover)
    thumb = make_thumb(cover, f"music_downloads/thumb_{stamp}.jpg") if cover else None
    return mp3, thumb, probe_duration(mp3) or int(meta.get("duration") or 0)


def selftest_music_timing(yt_id: str = "JGwWNGJdvx8") -> dict:
    """Stage timings of the song pipeline on this host (for /_selftest)."""
    meta = make_meta("yt", yt_id=yt_id)
    out = {}
    t0 = time.time()
    raw, _ = download_source_audio_sync(meta)
    out["download"] = round(time.time() - t0, 1)
    t1 = time.time()
    mp3 = to_mp3(raw, f"music_downloads/timing_{int(t1)}.mp3") if not raw.endswith(".mp3") else raw
    out["mp3_encode"] = round(time.time() - t1, 1)
    for p in (raw, mp3):
        try:
            os.remove(p)
        except OSError:
            pass
    return out


def yt_client_timing(yt_id: str = "JGwWNGJdvx8") -> dict:
    """/_selftest: extraction time per YouTube client on this host."""
    url = f"https://www.youtube.com/watch?v={yt_id}"
    out = {}
    variants = {
        "current": {},
        "current_again": {},
        "no_pot": {"fetch_pot": ["never"], "getpot_bgutil_baseurl": ["http://127.0.0.1:1"]},
        "no_pot_again": {"fetch_pot": ["never"], "getpot_bgutil_baseurl": ["http://127.0.0.1:1"]},
        "web_music": {"player_client": ["web_music"]},
    }
    for name, extra in variants.items():
        opts = _yt_dl_opts("music_downloads/probe.%(ext)s")
        if extra:
            opts["extractor_args"] = {"youtube": {**opts["extractor_args"].get("youtube", {}), **extra}}
        t0 = time.time()
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            out[name] = f"ok {time.time() - t0:.1f}s fmt={info.get('format_id')} cachedir={ydl.params.get('cachedir')}"
        except Exception as e:
            out[name] = f"fail {time.time() - t0:.1f}s {str(e)[:60]}"
    return out


def track_cache_key(meta: dict) -> str:
    if meta.get("yt_id"):
        return meta["yt_id"]
    if meta.get("sc_id"):
        return f"sc:{meta['sc_id']}"
    return f"sp:{meta.get('spotify_id')}" if meta.get("spotify_id") else f"dz:{meta.get('deezer_id')}"


def store_track_doc(meta: dict, file_id: str, msg_id, duration: int) -> dict:
    key = track_cache_key(meta)
    token = lyrics_token(key)
    src_url = (f"https://www.youtube.com/watch?v={meta['yt_id']}" if meta.get("yt_id")
               else meta.get("sc_url") or "")
    fields = {
        "vid_id": key, "file_id": file_id, f"file_ids.{BOT_ID}": file_id, "owner_bot": BOT_ID,
        "title": meta.get("title"), "artist": meta.get("artist"), "album": meta.get("album") or "",
        "year": meta.get("year") or "", "duration": int(duration or 0), "cover": meta.get("cover") or "",
        "lyrics_key": token, "source": meta.get("source"), "source_url": src_url,
        "search_text": f"{meta.get('title')} {meta.get('artist')} {meta.get('album') or ''}".lower(),
        "tag_v": TAG_VERSION, "clean_tagged": True, "derived": False, "updated_at": time.time(),
    }
    for f in ("spotify_id", "deezer_id", "sc_id"):
        if meta.get(f):
            fields[f] = str(meta[f])
    if meta.get("isrc"):
        fields["isrc"] = meta["isrc"].upper()
    if msg_id:
        fields["cache_msg_id"] = msg_id
    LOCAL_META[token] = {**fields, "vid_id": key}
    if music_cache is not None:
        try:
            music_cache.update_one({"vid_id": key}, {"$set": fields, "$inc": {"hits": 1},
                                                     "$setOnInsert": {"created_at": time.time()}}, upsert=True)
            return music_cache.find_one({"vid_id": key}) or fields
        except Exception as e:
            print(f"store_track_doc error: {e}", flush=True)
    return fields


def _add_aliases(doc: dict, meta: dict):
    """A different catalog id resolved to an already-cached file: remember it."""
    if music_cache is None or not doc:
        return
    upd = {f: str(meta[f]) for f in ("spotify_id", "deezer_id") if meta.get(f) and not doc.get(f)}
    if meta.get("isrc") and not doc.get("isrc"):
        upd["isrc"] = meta["isrc"].upper()
    if upd:
        try:
            music_cache.update_one({"_id": doc["_id"]}, {"$set": upd})
        except Exception:
            pass


def _cache_caption(meta: dict) -> str:
    lines = [f"🎵 {meta.get('title')}", f"👤 {meta.get('artist')}"]
    if meta.get("album"):
        lines.append(f"💿 {meta['album']}" + (f" ({meta['year']})" if meta.get("year") else ""))
    lines.append(f"🔑 {track_cache_key(meta)}")
    return "\n".join(lines)[:1000]


async def obtain_track(meta: dict, upload_chat=None) -> dict:
    """Return a current-format cache doc for meta, downloading it if needed.

    Concurrent requests for the same song share one download. upload_chat is
    only used when no cache channel is configured.
    """
    doc = await asyncio.to_thread(cache_find, meta)
    if doc:
        return doc
    lock_key = (meta.get("yt_id") or meta.get("sc_id") or meta.get("spotify_id") or meta.get("deezer_id")
                or f"{_norm(meta.get('title'))}:{_norm(meta.get('artist'))}")
    lock = _TRACK_LOCKS.setdefault(lock_key, asyncio.Lock())
    try:
        async with lock:
            doc = await asyncio.to_thread(cache_find, meta)
            if doc:
                return doc
            raw = mp3 = thumb = None
            try:
                async with MUSIC_SEMAPHORE:
                    if meta.get("source") != "sc" and not meta.get("yt_id"):
                        meta["yt_id"] = await asyncio.to_thread(resolve_youtube_id_sync, meta) or ""
                        if meta["yt_id"]:
                            hit = await asyncio.to_thread(cache_find, {"yt_id": meta["yt_id"]})
                            if hit:
                                await asyncio.to_thread(_add_aliases, hit, meta)
                                return hit
                    raw, _info = await asyncio.to_thread(download_source_audio_sync, meta)
                    await asyncio.to_thread(enrich_meta_sync, meta)
                    mp3, thumb, duration = await asyncio.to_thread(finalize_track_sync, raw, meta)
                    raw = None  # consumed by to_mp3
                target = int(str(MUSIC_CACHE_CHANNEL).strip()) if MUSIC_CACHE_CHANNEL else upload_chat
                if not target:
                    raise RuntimeError("no cache channel and no upload chat")
                sent = await bot.send_audio(
                    target, audio=mp3, thumb=thumb, title=meta.get("title")[:200],
                    performer=(meta.get("artist") or "")[:200], duration=duration,
                    file_name=safe_filename(meta.get("artist"), meta.get("title"), ".mp3"),
                    caption=_cache_caption(meta) if target != upload_chat else "",
                    disable_notification=True)
                media = sent.audio or sent.document
                if media is None:
                    raise RuntimeError("upload returned no audio")
                doc = await asyncio.to_thread(store_track_doc, meta, media.file_id,
                                              sent.id if target != upload_chat else None, duration)
                if target == upload_chat:
                    # No cache channel: the user already has this upload, so the
                    # caller should decorate it rather than send the song again.
                    doc = dict(doc or {})
                    doc["_sent_msg"] = sent
                return doc
            finally:
                for p in (raw, mp3, thumb):
                    if p and os.path.exists(p):
                        try:
                            os.remove(p)
                        except OSError:
                            pass
    finally:
        if not lock.locked():
            _TRACK_LOCKS.pop(lock_key, None)


STRINGS.update({
    "track_downloading": {
        "fa": "⏳ در حال دانلود «{track}» ...",
        "en": "⏳ Downloading “{track}” ...",
        "ru": "⏳ Загрузка «{track}» ...",
        "es": "⏳ Descargando «{track}» ...",
    },
    "track_download_failed": {
        "fa": "❌ دانلود «{track}» ناموفق بود. لطفاً دوباره امتحان کنید.",
        "en": "❌ Couldn't download “{track}”. Please try again.",
        "ru": "❌ Не удалось скачать «{track}». Попробуйте ещё раз.",
        "es": "❌ No se pudo descargar «{track}». Inténtalo de nuevo.",
    },
})


def _track_label(meta: dict) -> str:
    t_, a_ = meta.get("title") or "", meta.get("artist") or ""
    return (f"{t_} — {a_}" if a_ else t_)[:90]


async def _show_status(client: Client, chat_id: int, status, text: str):
    """Edit the status message in place, or replace it when it can't be edited
    (photo cards, deleted messages)."""
    if status is not None:
        try:
            await status.edit_text(text)
            return status
        except Exception:
            try:
                await status.delete()
            except Exception:
                pass
    try:
        return await client.send_message(chat_id, text)
    except Exception:
        return None


def track_kb_for(doc_or_key, chat_id, extra_rows=None):
    key = doc_or_key.get("vid_id") if isinstance(doc_or_key, dict) else str(doc_or_key)
    kb = get_track_delivery_kb(key, chat_id)
    if extra_rows and chat_id and int(chat_id) > 0:
        kb.inline_keyboard.extend(extra_rows)
    return kb


def _cover_url(meta: dict, doc: dict = None) -> str:
    url = (meta or {}).get("cover") or (doc or {}).get("cover") or ""
    yt = (meta or {}).get("yt_id") or ""
    if not url and re.fullmatch(r"[0-9A-Za-z_-]{11}", yt):
        url = f"https://i.ytimg.com/vi/{yt}/hqdefault.jpg"
    return url if str(url).startswith("http") else ""


async def send_cover_card(client: Client, chat_id: int, meta: dict, doc: dict = None):
    """Bale's bot API cannot attach artwork to an audio message (it drops the
    thumbnail field), so the cover is shown as a photo card right above the
    song. The file itself still has the cover embedded for local players."""
    if not get_settings().get("cover_card", True):
        return
    url = _cover_url(meta, doc)
    if not url:
        return
    title = (meta or {}).get("title") or (doc or {}).get("title") or ""
    artist = (meta or {}).get("artist") or (doc or {}).get("artist") or ""
    album = (meta or {}).get("album") or (doc or {}).get("album") or ""
    cap = f"🎵 **{title}**" + (f"\n👤 {artist}" if artist else "") + (f"\n💿 {album}" if album else "")
    try:
        await client.send_photo(chat_id, url, caption=cap)
    except Exception as e:
        print(f"cover card failed: {e}", flush=True)


async def deliver_track(client: Client, chat_id: int, meta: dict, status=None,
                        extra_rows=None, activity: str = "music", report_failure: bool = True) -> bool:
    """Cached -> sent instantly. Otherwise a "⏳ downloading «song»" message is
    shown, the song is built, the message is deleted and the audio is sent."""
    doc = await asyncio.to_thread(cache_find, meta)
    if doc:
        await send_cover_card(client, chat_id, meta, doc)
    if doc and await send_cached_doc(client, chat_id, doc, track_kb_for(doc, chat_id, extra_rows)):
        if status is not None:
            try:
                await status.delete()
            except Exception:
                pass
        log_activity(chat_id, activity, f"{_track_label(meta)} (cache)")
        log_history(chat_id, doc.get("vid_id"), doc.get("title"), doc.get("artist"))
        return True

    label = _track_label(meta) or "..."
    status = await _show_status(client, chat_id, status, t(chat_id, "track_downloading", track=label))
    try:
        doc = await obtain_track(meta, upload_chat=chat_id)
        if doc and not doc.get("_sent_msg"):
            await send_cover_card(client, chat_id, meta, doc)
        sent_here = doc.pop("_sent_msg", None) if isinstance(doc, dict) else None
        if sent_here is not None:
            try:
                await sent_here.edit_reply_markup(track_kb_for(doc, chat_id, extra_rows))
            except Exception as e:
                print(f"could not attach track keyboard: {e}", flush=True)
            ok = True
        else:
            ok = await send_cached_doc(client, chat_id, doc, track_kb_for(doc, chat_id, extra_rows))
        if not ok:
            raise RuntimeError("delivery of fresh upload failed")
    except Exception as e:
        print(f"deliver_track failed for {label!r}: {e}\n{traceback.format_exc()[-1200:]}", flush=True)
        meta["_status"] = status
        if report_failure:
            await _show_status(client, chat_id, status, t(chat_id, "track_download_failed", track=label))
        return False
    if status is not None:
        try:
            await status.delete()
        except Exception:
            pass
    log_activity(chat_id, activity, label)
    log_history(chat_id, doc.get("vid_id"), doc.get("title"), doc.get("artist"))
    return True


async def deliver_by_key(client: Client, chat_id: int, key: str, reply_markup=None, status=None) -> bool:
    """Library / playlist / mixtape replays: send the cached file, rebuilding
    legacy (untagged) entries through the pipeline instead of failing."""
    doc = cache_doc(key)
    if not doc:
        return False
    if is_clean_cache_doc(doc) and await send_cached_doc(client, chat_id, doc, reply_markup):
        return True
    if doc.get("derived"):
        return await send_cached_doc(client, chat_id, doc, reply_markup)
    return await deliver_track(client, chat_id, meta_from_doc(doc), status=status)


# ---- search wrappers used by inline mode and the in-chat search pages ----
def ytm_search_sync(query: str, kind: str = "songs", limit: int = 20) -> list:
    try:
        return _ytm().search(query, filter=kind, limit=limit) or []
    except Exception as e:
        print(f"ytm search error ({kind}, {query!r}): {e}", flush=True)
        return []


def sc_search_sync(query: str, limit: int = 20) -> list:
    try:
        with yt_dlp.YoutubeDL({'extract_flat': True, 'quiet': True, 'no_warnings': True}) as ydl:
            info = ydl.extract_info(f"scsearch{limit}:{query}", download=False)
        return [e for e in (info.get('entries') or []) if e and e.get("id")]
    except Exception as e:
        print(f"sc search error ({query!r}): {e}", flush=True)
        return []


def fmt_dur(sec) -> str:
    sec = int(sec or 0)
    if sec <= 0:
        return ""
    h, rem = divmod(sec, 3600)
    return f"{h}:{rem // 60:02d}:{rem % 60:02d}" if h else f"{rem // 60}:{rem % 60:02d}"


# ---------------- 5. INSTAGRAM & TIKTOK NATIVE RENDER ENGINES ----------------
_IG_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")


def _ig_unescape(text: str) -> str:
    for _ in range(3):
        text = (text.replace('\\"', '"').replace("\\/", "/").replace("\\u0026", "&")
                .replace("\\\\", "\\"))
    return text


def instagram_embed_sync(url: str) -> dict:
    """Logged-out fallback: Instagram's public embed page carries the media
    URLs of a post/reel and is served to datacenter IPs that the API blocks."""
    m = re.search(r"instagram\.com/(?:[\w.]+/)?(?:p|reel|reels|tv)/([\w-]+)", url)
    if not m:
        raise RuntimeError("not a post/reel link")
    sc = m.group(1)
    r = requests.get(f"https://www.instagram.com/p/{sc}/embed/captioned/",
                     headers={"User-Agent": _IG_UA, "Accept-Language": "en-US,en;q=0.9"}, timeout=25)
    r.raise_for_status()
    h = _ig_unescape(r.text)
    videos = list(dict.fromkeys(v for v in re.findall(r'"video_url":"(https?://[^"]+)"', h)))
    images = list(dict.fromkeys(v for v in re.findall(r'"display_url":"(https?://[^"]+)"', h)))
    if not images:
        images = [html.unescape(x) for x in re.findall(r'class="EmbeddedMediaImage"[^>]*src="([^"]+)"', r.text)]
    caption = ""
    cm = re.search(r'"edge_media_to_caption":\{"edges":\[\{"node":\{"text":"(.*?)"\}', h)
    if cm:
        try:
            caption = json.loads('"' + cm.group(1) + '"')
        except Exception:
            caption = cm.group(1)
    os.makedirs("media_downloads", exist_ok=True)
    stamp = f"{int(time.time() * 1000)}_{random.randint(100, 999)}"
    files = []
    # a carousel lists one display_url per child plus the cover; videos win
    picks = [(u, ".mp4") for u in videos] or [(u, ".jpg") for u in images[:10]]
    for i, (u, ext) in enumerate(picks[:10]):
        path = f"media_downloads/ig_{sc}_{stamp}_{i}{ext}"
        try:
            download_url_to_file_sync(u, path)
            files.append(path)
        except Exception as e:
            print(f"instagram embed media fetch failed: {e}", flush=True)
    return {"files": files, "caption": caption[:3900]}


def instagram_embed_probe(url: str) -> dict:
    """Diagnostics for /_selftest: what the embed page looks like from here."""
    m = re.search(r"instagram\.com/(?:[\w.]+/)?(?:p|reel|reels|tv)/([\w-]+)", url)
    r = requests.get(f"https://www.instagram.com/p/{m.group(1)}/embed/captioned/",
                     headers={"User-Agent": _IG_UA, "Accept-Language": "en-US,en;q=0.9"}, timeout=25)
    h = _ig_unescape(r.text)
    return {"status": r.status_code, "len": len(r.text), "final_url": r.url[:120],
            "video_url": h.count('"video_url"'), "display_url": h.count('"display_url"'),
            "EmbeddedMediaImage": r.text.count("EmbeddedMediaImage"), "login": "login" in r.url,
            "snippet": re.sub(r"\s+", " ", r.text[:300])}


def download_instagram_native_sync(url: str):
    """Instagram on Render, no cookies needed: yt-dlp first (fast, works for
    most public posts), then the public embed page. Cookies are only a last
    resort if an IG_COOKIES value happens to be configured."""
    clean_url = url.split("?")[0]
    base = {
        'format': 'best',
        'outtmpl': 'media_downloads/ig_%(id)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'http_chunk_size': 10485760,
        'ignore_no_formats_error': True,
        'impersonate': ImpersonateTarget.from_str('chrome'),
        'retries': 3, 'extractor_retries': 1, 'socket_timeout': 20,
    }
    last = None
    try:
        info = extract_post_info(base, clean_url, "instagram")
        files = assemble_post_media(info, "media_downloads/ig_")
        if files:
            caption = info.get('description') or info.get('title') or ""
            return {'files': files, 'caption': caption[:3900]}
    except Exception as e:
        last = e
        print(f"instagram yt-dlp (no cookies) failed: {str(e)[:120]}", flush=True)
    try:
        data = instagram_embed_sync(clean_url)
        if data["files"]:
            return data
    except Exception as e:
        last = e
        print(f"instagram embed failed: {str(e)[:120]}", flush=True)
    if os.path.exists(INSTAGRAM_COOKIES_FILE) and os.path.getsize(INSTAGRAM_COOKIES_FILE) > 0:
        info = extract_post_info({**base, 'cookiefile': INSTAGRAM_COOKIES_FILE}, clean_url, "instagram")
        files = assemble_post_media(info, "media_downloads/ig_")
        return {'files': files, 'caption': (info.get('description') or info.get('title') or "")[:3900]}
    if last:
        raise last
    return {'files': [], 'caption': ""}


def download_generic_sync(url: str):
    """Any other site yt-dlp supports (Facebook, Vimeo, Twitch clips, ...)."""
    limit = int(getattr(bot, "upload_limit", 50 * 1024 * 1024))
    opts = {
        'format': f'best[filesize<{limit}]/best[filesize_approx<{limit}]/best[height<=720]/best',
        'outtmpl': 'media_downloads/gen_%(id)s.%(ext)s',
        'quiet': True, 'no_warnings': True, 'noplaylist': True,
        'impersonate': ImpersonateTarget.from_str('chrome'),
        'ignore_no_formats_error': True,
        **NET_RETRY_OPTS,
    }
    info = extract_post_info(opts, url, "generic")
    files = assemble_post_media(info, "media_downloads/gen_")
    caption = info.get('title') or ""
    return {'files': files, 'caption': caption[:3900]}


async def process_generic_link(client: Client, chat_id: int, msg: Message, url: str):
    try:
        await msg.edit_text("⏳ در حال بررسی و دانلود لینک...")
        data = await asyncio.to_thread(download_generic_sync, url)
        files = data.get("files") or []
        if not files:
            return await msg.edit_text("❌ از این لینک فایلی قابل دانلود پیدا نشد.")
        await msg.delete()
        await send_post_media(client, chat_id, files, data.get("caption") or "", "🌐 ")
        log_activity(chat_id, "download", url)
        for f in files:
            if os.path.exists(f):
                os.remove(f)
    except Exception as e:
        print(f"generic link failed: {str(e)[:200]}", flush=True)
        try:
            await msg.edit_text("❌ این لینک پشتیبانی نمی‌شود یا دانلود آن ممکن نبود.")
        except Exception:
            pass


STRINGS.update({
    "ig_fetching_media": {"fa": "⏳ **در حال دریافت مدیا از اینستاگرام...**", "en": "⏳ **Fetching media from Instagram...**", "ru": "⏳ **Получение медиа из Instagram...**", "es": "⏳ **Obteniendo contenido de Instagram...**"},
    "ig_no_files_extracted": {"fa": "❌ هیچ فایلی از این پست اینستاگرام استخراج نشد.", "en": "❌ No files could be extracted from this Instagram post.", "ru": "❌ Не удалось извлечь файлы из этого поста Instagram.", "es": "❌ No se pudo extraer ningún archivo de esta publicación de Instagram."},
    "ig_post_fetch_failed": {"fa": "❌ متاسفانه دریافت این پست با خطا مواجه شد.", "en": "❌ Sorry, fetching this post failed.", "ru": "❌ К сожалению, не удалось получить этот пост.", "es": "❌ Lo sentimos, no se pudo obtener esta publicación."},
})

async def process_instagram_render(client: Client, chat_id: int, msg: Message, url: str):
    await msg.edit_text(t(chat_id, "ig_fetching_media"))
    try:
        data = await asyncio.to_thread(download_instagram_native_sync, url)
        files = data.get('files', [])
        if not files:
            return await msg.edit_text(t(chat_id, "ig_no_files_extracted"))

        await msg.delete()
        await send_post_media(client, chat_id, files, data['caption'], "📸 ")
        log_activity(chat_id, "instagram", url)

        for f in files:
            if os.path.exists(f): os.remove(f)
    except Exception as e:
        error_trace = traceback.format_exc()
        print(f"Instagram Render Error:\n{error_trace}", flush=True)
        try:
            await msg.edit_text(t(chat_id, "ig_post_fetch_failed"))
        except Exception:
            await client.send_message(chat_id, t(chat_id, "ig_post_fetch_failed"))

def _tiktok_via_tikwm(url: str):
    r = requests.post("https://www.tikwm.com/api/", data={"url": url, "hd": 1}, timeout=30,
                      headers={"User-Agent": "Mozilla/5.0"})
    j = r.json()
    d = j.get("data") or {}
    if j.get("code") != 0 or not d:
        raise RuntimeError(f"tikwm: {j.get('msg')}")

    def absu(u):
        return u if not u or u.startswith("http") else "https://www.tikwm.com" + u
    os.makedirs("media_downloads", exist_ok=True)
    stamp = f"{int(time.time() * 1000)}_{random.randint(100, 999)}"
    files = []
    images = d.get("images") or []
    if images:
        for i, u in enumerate(images[:20]):
            p = f"media_downloads/tt_{stamp}_{i}.jpg"
            download_url_to_file_sync(absu(u), p)
            files.append(p)
    else:
        # hdplay can exceed Bale's 50 MB cap; fall back to the normal stream.
        for key in ("hdplay", "play", "wmplay"):
            u = absu(d.get(key) or "")
            if not u:
                continue
            p = f"media_downloads/tt_{stamp}.mp4"
            download_url_to_file_sync(u, p)
            if os.path.getsize(p) <= 49 * 1024 * 1024 or key == "wmplay":
                files.append(p)
                break
            os.remove(p)
    caption = d.get("title") or ""
    return {"files": files, "caption": caption[:3900]}


def download_tiktok_native_sync(target: str):
    """Downloads TikTok videos directly on Render."""
    if not target.startswith("http"):
        url = f"https://www.tiktok.com/@{target.replace('@', '').strip()}"
        extra = {'playlist_items': '1'}
    else:
        url = target.split("?")[0]
        extra = {}

    opts = {
        'format': 'best',
        'outtmpl': 'media_downloads/tt_%(id)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'impersonate': ImpersonateTarget.from_str('chrome'),
        # TikTok photo/slideshow posts carry no video formats — same fix as
        # Instagram, otherwise they fail instead of returning the images.
        'ignore_no_formats_error': True,
        **NET_RETRY_OPTS,
        **extra
    }

    try:
        info = extract_post_info(opts, url, "tiktok")
    except Exception as e:
        # TikTok blocks many datacenter IPs (Render included); tikwm.com fetches
        # the post for us. Only direct video/photo links can go this way.
        if not target.startswith("http"):
            raise
        print(f"tiktok yt-dlp failed ({str(e)[:120]}); trying tikwm", flush=True)
        return _tiktok_via_tikwm(url)

    media_files = assemble_post_media(info, "media_downloads/tt_")
    caption = info.get('description') or info.get('title') or ""
    if len(caption) > 3900:
        caption = caption[:3900] + "..."

    return {'files': media_files, 'caption': caption}

STRINGS.update({
    "tiktok_no_media_found": {"fa": "❌ هیچ مدیایی یافت نشد.", "en": "❌ No media found.", "ru": "❌ Медиа не найдено.", "es": "❌ No se encontró contenido."},
    "tiktok_video_fetch_failed": {"fa": "❌ متاسفانه دریافت این ویدیو تیک‌تاک با خطا مواجه شد.", "en": "❌ Sorry, fetching this TikTok video failed.", "ru": "❌ К сожалению, не удалось получить это видео TikTok.", "es": "❌ Lo sentimos, no se pudo obtener este video de TikTok."},
})

async def process_tiktok_render(client: Client, chat_id: int, msg: Message, target: str):
    await msg.edit_text(t(chat_id, "tiktok_fetching"))
    try:
        data = await asyncio.to_thread(download_tiktok_native_sync, target)
        files = data.get('files', [])
        if not files:
            return await msg.edit_text(t(chat_id, "tiktok_no_media_found"))

        await msg.delete()
        # Slideshow posts have several images, so send them all, not just files[0].
        await send_post_media(client, chat_id, files, data['caption'], "📱 ")
        log_activity(chat_id, "tiktok", target)

        for f in files:
            if os.path.exists(f): os.remove(f)
    except Exception as e:
        error_trace = traceback.format_exc()
        print(f"TikTok Render Error:\n{error_trace}", flush=True)
        try:
            await msg.edit_text(t(chat_id, "tiktok_video_fetch_failed"))
        except Exception:
            await client.send_message(chat_id, t(chat_id, "tiktok_video_fetch_failed"))

# ---------------- 5.5 PINTEREST ENGINE ----------------
# Unauthenticated calls to Pinterest's own frontend resource API -- the same
# JSON endpoints pinterest.com's web app uses, and the same ones yt-dlp's
# Pinterest extractor calls for single-video pins. yt-dlp only ever extracts
# video -- it hard-errors "No video formats found" on a plain image pin --
# so pins/boards/search are all handled directly against these endpoints
# instead, uniformly covering both images and videos.
PINTEREST_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
PINTEREST_DL_DIR = "pinterest_downloads"
os.makedirs(PINTEREST_DL_DIR, exist_ok=True)


def pinterest_api_call_sync(resource: str, options: dict, handler: str = "www/[username].js") -> dict:
    r = requests.get(
        f"https://www.pinterest.com/resource/{resource}Resource/get/",
        params={"data": json.dumps({"options": options})},
        headers={"X-Pinterest-PWS-Handler": handler, "User-Agent": PINTEREST_UA, "Accept": "application/json"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["resource_response"]


def _pin_to_media(d: dict):
    """Normalize one pin object -- Pin/BoardFeed/search results all return
    the same shape -- into a downloadable media reference."""
    if not d:
        return None
    raw_title = d.get("title") or d.get("grid_title") or ""
    if isinstance(raw_title, dict):
        raw_title = raw_title.get("text") or raw_title.get("value") or ""
    title = str(raw_title).strip()
    images = d.get("images") or {}
    thumb = (images.get("474x") or images.get("236x") or {}).get("url")
    video_list = (d.get("videos") or {}).get("video_list") or {}
    best = None
    for f in video_list.values():
        if not isinstance(f, dict):
            continue
        u = f.get("url")
        if not u or u.endswith(".m3u8"):
            continue
        if best is None or (f.get("height") or 0) > (best.get("height") or 0):
            best = f
    if best:
        return {"type": "video", "url": best["url"], "title": title, "id": str(d.get("id") or ""), "thumb": thumb}
    orig = (images.get("orig") or {}).get("url")
    if orig:
        return {"type": "photo", "url": orig, "title": title, "id": str(d.get("id") or ""), "thumb": thumb}
    return None


def pinterest_pin_media_sync(pin_id: str):
    resp = pinterest_api_call_sync("Pin", {"field_set_key": "unauth_react_main_pin", "id": pin_id})
    return _pin_to_media(resp.get("data") or {})


def pinterest_search_sync(query: str, page_size: int = 25):
    resp = pinterest_api_call_sync(
        "BaseSearch", {"query": query, "scope": "pins", "page_size": page_size},
        handler="www/search/[scope].js")
    return (resp.get("data") or {}).get("results") or []


def pinterest_board_feed_sync(username: str, slug: str, limit: int = 20):
    board = pinterest_api_call_sync("Board", {"slug": slug, "username": username}).get("data") or {}
    board_id = board.get("id")
    if not board_id:
        return None, []
    items, bookmark = [], None
    while len(items) < limit:
        opts = {"board_id": board_id, "page_size": min(50, limit - len(items))}
        if bookmark:
            opts["bookmarks"] = [bookmark]
        feed = pinterest_api_call_sync("BoardFeed", opts)
        batch = feed.get("data") or []
        for it in batch:
            if isinstance(it, dict) and it.get("type") == "pin":
                items.append(it)
        bookmark = feed.get("bookmark")
        if not bookmark or not batch:
            break
    return board.get("name") or slug, items[:limit]


def pinterest_user_pins_sync(username: str, limit: int = 20):
    items, bookmark = [], None
    while len(items) < limit:
        opts = {"username": username, "page_size": min(50, limit - len(items))}
        if bookmark:
            opts["bookmarks"] = [bookmark]
        resp = pinterest_api_call_sync("UserPins", opts)
        batch = resp.get("data") or []
        for it in batch:
            if isinstance(it, dict) and it.get("type") == "pin":
                items.append(it)
        bookmark = resp.get("bookmark")
        if not bookmark or not batch:
            break
    return items[:limit]


def download_url_to_file_sync(url: str, path: str, timeout: int = 90):
    with requests.get(url, stream=True, timeout=timeout, headers={"User-Agent": PINTEREST_UA}) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)


STRINGS.update({
    "pt_deliver_failed": {"fa": "❌ دریافت این پین با خطا مواجه شد.", "en": "❌ Failed to fetch this pin.", "ru": "❌ Не удалось получить этот пин.", "es": "❌ No se pudo obtener este pin."},
    "pt_bulk_empty": {"fa": "❌ {empty_msg}", "en": "❌ {empty_msg}", "ru": "❌ {empty_msg}", "es": "❌ {empty_msg}"},
    "pt_no_downloadable_media": {"fa": "❌ هیچ مدیای قابل دانلودی یافت نشد.", "en": "❌ No downloadable media found.", "ru": "❌ Медиа для загрузки не найдено.", "es": "❌ No se encontró contenido descargable."},
    "pt_bulk_fetching": {
        "fa": "📋 **{name}**\nدر حال دریافت {count} پین...",
        "en": "📋 **{name}**\nFetching {count} pins...",
        "ru": "📋 **{name}**\nПолучение {count} пинов...",
        "es": "📋 **{name}**\nObteniendo {count} pins...",
    },
    "pt_bulk_all_failed": {"fa": "❌ دانلود هیچ پینی موفق نبود.", "en": "❌ Failed to download any pin.", "ru": "❌ Не удалось загрузить ни один пин.", "es": "❌ No se pudo descargar ningún pin."},
    "pt_board_not_found": {"fa": "❌ این بورد یافت نشد یا خصوصی است.", "en": "❌ This board wasn't found or is private.", "ru": "❌ Эта доска не найдена или является приватной.", "es": "❌ No se encontró este tablero o es privado."},
    "pt_board_empty": {"fa": "این بورد خالی است یا یافت نشد.", "en": "This board is empty or wasn't found.", "ru": "Эта доска пуста или не найдена.", "es": "Este tablero está vacío o no se encontró."},
    "pt_profile_not_found": {"fa": "❌ این پروفایل یافت نشد یا خصوصی است.", "en": "❌ This profile wasn't found or is private.", "ru": "❌ Этот профиль не найден или является приватным.", "es": "❌ No se encontró este perfil o es privado."},
    "pt_profile_pins_title": {"fa": "پین‌های {username}", "en": "{username}'s pins", "ru": "Пины {username}", "es": "Pines de {username}"},
    "pt_profile_empty": {"fa": "این پروفایل خالی است یا یافت نشد.", "en": "This profile is empty or wasn't found.", "ru": "Этот профиль пуст или не найден.", "es": "Este perfil está vacío o no se encontró."},
    "pt_fetching_pin": {"fa": "📌 **در حال دریافت پین...**", "en": "📌 **Fetching pin...**", "ru": "📌 **Получение пина...**", "es": "📌 **Obteniendo pin...**"},
    "pt_pin_not_found": {"fa": "❌ این پین یافت نشد یا خصوصی است.", "en": "❌ This pin wasn't found or is private.", "ru": "❌ Этот пин не найден или является приватным.", "es": "❌ No se encontró este pin o es privado."},
    "pt_invalid_link": {"fa": "❌ لینک پینترست نامعتبر است.", "en": "❌ Invalid Pinterest link.", "ru": "❌ Недействительная ссылка Pinterest.", "es": "❌ Enlace de Pinterest no válido."},
    "pt_fetching_board": {"fa": "📋 **در حال دریافت بورد...**", "en": "📋 **Fetching board...**", "ru": "📋 **Получение доски...**", "es": "📋 **Obteniendo tablero...**"},
    "pt_fetching_user_pins": {"fa": "👤 **در حال دریافت پین‌های این کاربر...**", "en": "👤 **Fetching this user's pins...**", "ru": "👤 **Получение пинов этого пользователя...**", "es": "👤 **Obteniendo los pines de este usuario...**"},
    "pt_expired_research": {"fa": "❌ منقضی شده، دوباره جستجو کنید.", "en": "❌ Expired, please search again.", "ru": "❌ Истекло, выполните поиск снова.", "es": "❌ Caducado, busca de nuevo."},
    "pt_not_downloadable": {"fa": "❌ این پین قابل دانلود نیست.", "en": "❌ This pin isn't downloadable.", "ru": "❌ Этот пин недоступен для загрузки.", "es": "❌ Este pin no se puede descargar."},
    "pt_fetching_generic": {"fa": "⬇️ **در حال دریافت...**", "en": "⬇️ **Fetching...**", "ru": "⬇️ **Получение...**", "es": "⬇️ **Obteniendo...**"},
})

IMG_SOURCE_LABELS = {"pinterest": "📌 Pinterest", "pexels": "🖼 Pexels", "google": "🔍 Web"}


def record_image_download(chat_id: int, source: str, url: str, title: str = "", mtype: str = "photo"):
    if settings_col is None or not url:
        return
    try:
        settings_col.database["image_history"].insert_one({
            "chat_id": int(chat_id), "source": source, "url": url, "title": (title or "")[:120],
            "type": mtype, "ts": time.time()})
    except Exception as e:
        print(f"image history write error: {e}", flush=True)


STRINGS.update({
    "img_history_header": {"fa": "📜 **تاریخچه دانلود تصاویر {src}**\n", "en": "📜 **Image download history {src}**\n",
                           "ru": "📜 **История загрузок изображений {src}**\n", "es": "📜 **Historial de imágenes {src}**\n"},
    "img_history_empty": {"fa": "هنوز تصویری از این بخش دانلود نکرده‌اید.", "en": "You haven't downloaded any images here yet.",
                          "ru": "Вы ещё не скачивали здесь изображения.", "es": "Aún no has descargado imágenes aquí."},
    "img_history_hint": {"fa": "\n_برای دانلود دوباره، شماره را بزنید._", "en": "\n_Tap a number to download it again._",
                         "ru": "\n_Нажмите номер, чтобы скачать снова._", "es": "\n_Toca un número para descargarla de nuevo._"},
})


async def render_image_history(msg: Message, chat_id: int, plat: str):
    back_cb = {"google": "menu_google_images", "pexels": "menu_pexels"}.get(plat, "menu_pinterest")
    docs = []
    if settings_col is not None:
        try:
            docs = list(settings_col.database["image_history"].find({"chat_id": int(chat_id), "source": plat})
                        .sort("ts", -1).limit(10))
        except Exception as e:
            print(f"image history read error: {e}", flush=True)
    user_data.setdefault(chat_id, {})["img_history_items"] = docs
    lines = [t(chat_id, "img_history_header", src=IMG_SOURCE_LABELS.get(plat, ""))]
    if not docs:
        lines.append(t(chat_id, "img_history_empty"))
    for i, d in enumerate(docs, 1):
        when = datetime.fromtimestamp(d.get("ts") or 0).strftime("%m-%d %H:%M")
        lines.append(f"`{i}` {'🎬' if d.get('type') == 'video' else '🖼'} {d.get('title') or '—'}  _{when}_")
    rows = []
    btns = [InlineKeyboardButton(str(i), callback_data=f"imgh_{plat}_{i-1}") for i in range(1, len(docs) + 1)]
    rows += [btns[j:j + 5] for j in range(0, len(btns), 5)]
    if docs:
        lines.append(t(chat_id, "img_history_hint"))
    rows.append([InlineKeyboardButton(t(chat_id, "btn_back"), callback_data=back_cb)])
    await msg.edit_text("\n".join(lines)[:4000], reply_markup=InlineKeyboardMarkup(rows))


async def deliver_pinterest_media(client: Client, chat_id: int, msg: Message, media: dict, source: str = "pinterest"):
    ext = "mp4" if media["type"] == "video" else "jpg"
    path = os.path.join(PINTEREST_DL_DIR, f"pin_{media['id'] or int(time.time())}.{ext}")
    try:
        await asyncio.to_thread(download_url_to_file_sync, media["url"], path)
        cap = media["title"][:1000] if media["title"] else None
        if media["type"] == "video":
            video_kb = InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_edit_this_video"), callback_data="vedit_from_last")]])
            sent = await client.send_video(chat_id, video=path, caption=cap, reply_markup=video_kb)
            if sent and (sent.video or sent.document):
                user_data.setdefault(chat_id, {})["last_video_file_id"] = (sent.video or sent.document).file_id
        else:
            # send_photo re-compresses through Telegram's own photo pipeline
            # (downscales, re-encodes as JPEG) regardless of the source
            # file's real resolution -- exactly why "high quality" looked
            # identical to the preview. send_document is untouched by that.
            photo_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎨 ابزارهای تصویر", callback_data="menu_image_tools")]])
            sent = await client.send_document(chat_id, document=path, caption=cap, reply_markup=photo_kb)
            if sent and (sent.document or sent.photo):
                user_data.setdefault(chat_id, {})["last_image_file_id"] = (sent.document or sent.photo).file_id
        log_activity(chat_id, "pinterest" if source == "pinterest" else "image_search",
                     f"{media['type']}: {media.get('title') or media.get('id')}")
        record_image_download(chat_id, source or "pinterest", media["url"], media.get("title"), media["type"])
        await msg.delete()
    except Exception:
        print(f"Pinterest deliver error:\n{traceback.format_exc()}", flush=True)
        try:
            await msg.edit_text(t(chat_id, "pt_deliver_failed"))
        except Exception:
            await client.send_message(chat_id, t(chat_id, "pt_deliver_failed"))
    finally:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


async def _deliver_pinterest_items(client: Client, chat_id: int, msg: Message, name: str, items: list, empty_msg: str):
    if not items:
        return await msg.edit_text(t(chat_id, "pt_bulk_empty", empty_msg=empty_msg))

    medias = [m for m in (_pin_to_media(it) for it in items) if m]
    if not medias:
        return await msg.edit_text(t(chat_id, "pt_no_downloadable_media"))

    try:
        await msg.edit_text(t(chat_id, "pt_bulk_fetching", name=name, count=len(medias)))
    except Exception:
        pass

    downloaded = []  # (path, type)
    for i, m in enumerate(medias):
        ext = "mp4" if m["type"] == "video" else "jpg"
        path = os.path.join(PINTEREST_DL_DIR, f"pt_{m['id'] or i}.{ext}")
        try:
            await asyncio.to_thread(download_url_to_file_sync, m["url"], path)
            downloaded.append((path, m["type"]))
        except Exception as e:
            print(f"Pinterest bulk item failed: {e}", flush=True)

    if not downloaded:
        return await msg.edit_text(t(chat_id, "pt_bulk_all_failed"))

    log_activity(chat_id, "pinterest_bulk", f"{name} ({len(downloaded)} مورد)")

    for batch_start in range(0, len(downloaded), 10):
        batch = downloaded[batch_start:batch_start + 10]
        group = [InputMediaVideo(p) if mtype == "video" else InputMediaPhoto(p) for p, mtype in batch]
        try:
            await client.send_media_group(chat_id, group)
        except Exception as e:
            print(f"Pinterest bulk group send failed: {e}", flush=True)
            for p, mtype in batch:
                try:
                    if mtype == "video":
                        await client.send_video(chat_id, video=p)
                    else:
                        await client.send_photo(chat_id, photo=p)
                except Exception as e2:
                    print(f"Pinterest bulk item send failed: {e2}", flush=True)

    for p, _ in downloaded:
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass

    try:
        await msg.delete()
    except Exception:
        pass


async def deliver_pinterest_board(client: Client, chat_id: int, msg: Message, username: str, slug: str):
    try:
        name, items = await asyncio.to_thread(pinterest_board_feed_sync, username, slug, 20)
    except Exception:
        print(f"Pinterest board fetch error:\n{traceback.format_exc()}", flush=True)
        return await msg.edit_text(t(chat_id, "pt_board_not_found"))
    await _deliver_pinterest_items(client, chat_id, msg, name or slug, items, t(chat_id, "pt_board_empty"))


async def deliver_pinterest_profile(client: Client, chat_id: int, msg: Message, username: str):
    try:
        items = await asyncio.to_thread(pinterest_user_pins_sync, username, 20)
    except Exception:
        print(f"Pinterest profile fetch error:\n{traceback.format_exc()}", flush=True)
        return await msg.edit_text(t(chat_id, "pt_profile_not_found"))
    await _deliver_pinterest_items(client, chat_id, msg, t(chat_id, "pt_profile_pins_title", username=username), items, t(chat_id, "pt_profile_empty"))


async def process_pinterest_link(client: Client, chat_id: int, msg: Message, url: str):
    pin_match = re.search(r"/pin/(?:[\w-]+--)?(\d+)", url)
    if not pin_match and "pin.it" in url:
        try:
            resolved = await asyncio.to_thread(
                lambda: requests.head(url, allow_redirects=True, timeout=10,
                                      headers={"User-Agent": PINTEREST_UA}).url)
            url = resolved
            pin_match = re.search(r"/pin/(?:[\w-]+--)?(\d+)", url)
        except Exception:
            pass

    if pin_match:
        await msg.edit_text(t(chat_id, "pt_fetching_pin"))
        try:
            media = await asyncio.to_thread(pinterest_pin_media_sync, pin_match.group(1))
        except Exception:
            print(f"Pinterest pin fetch error:\n{traceback.format_exc()}", flush=True)
            media = None
        if not media:
            return await msg.edit_text(t(chat_id, "pt_pin_not_found"))
        return await deliver_pinterest_media(client, chat_id, msg, media)

    path_match = re.search(r"pinterest\.[a-z.]+/([^/?#]+)(?:/([^/?#]+))?/?", url)
    PT_RESERVED = ("pin", "search", "today", "ideas", "about", "business", "developers", "help", "login")
    if not path_match or path_match.group(1) in PT_RESERVED:
        return await msg.edit_text(t(chat_id, "pt_invalid_link"))
    username, slug = path_match.group(1), path_match.group(2)

    if slug and slug not in PT_RESERVED:
        await msg.edit_text(t(chat_id, "pt_fetching_board"))
        return await deliver_pinterest_board(client, chat_id, msg, username, slug)

    await msg.edit_text(t(chat_id, "pt_fetching_user_pins"))
    await deliver_pinterest_profile(client, chat_id, msg, username)


STRINGS.update({
    "pt_no_results": {"fa": "❌ نتیجه‌ای پیدا نشد.", "en": "❌ No results found.", "ru": "❌ Результаты не найдены.", "es": "❌ No se encontraron resultados."},
    "pt_results_status": {
        "fa": "🔎 **نتایج «{query}»** — صفحه {page} از {pages}\nدر حال ارسال پیش‌نمایش...",
        "en": "🔎 **Results for \"{query}\"** — page {page} of {pages}\nSending previews...",
        "ru": "🔎 **Результаты «{query}»** — стр. {page} из {pages}\nОтправка предпросмотров...",
        "es": "🔎 **Resultados de «{query}»** — página {page} de {pages}\nEnviando vistas previas...",
    },
    "pt_btn_download_hq": {"fa": "⬇️ دانلود کیفیت کامل", "en": "⬇️ Download Full Quality", "ru": "⬇️ Скачать в полном качестве", "es": "⬇️ Descargar calidad completa"},
    "pt_btn_prev_page": {"fa": "➡️ صفحه قبلی", "en": "➡️ Previous page", "ru": "➡️ Пред. страница", "es": "➡️ Página anterior"},
    "pt_btn_next_page": {"fa": "⬅️ صفحه بعدی", "en": "⬅️ Next page", "ru": "⬅️ След. страница", "es": "⬅️ Página siguiente"},
    "pt_previews_failed": {"fa": "❌ نمایش پیش‌نمایش‌ها با خطا مواجه شد.", "en": "❌ Failed to show previews.", "ru": "❌ Не удалось показать предпросмотры.", "es": "❌ No se pudieron mostrar las vistas previas."},
    "pt_search_error": {"fa": "❌ خطا در جستجوی پینترست.", "en": "❌ Pinterest search error.", "ru": "❌ Ошибка поиска в Pinterest.", "es": "❌ Error en la búsqueda de Pinterest."},
})

STRINGS.update({
    "img_nav_text": {"fa": "🔎 «{query}» — صفحه {page}{of}", "en": "🔎 “{query}” — page {page}{of}",
                     "ru": "🔎 «{query}» — стр. {page}{of}", "es": "🔎 «{query}» — página {page}{of}"},
})


async def send_page_navigator(client: Client, chat_id: int, query: str, page: int, pages,
                              cb_prefix: str, back_cb: str):
    """Previous/next sent as its own message AFTER the page's previews, so it
    is always the last message. Pressing it turns it into the loading status,
    which is deleted once the next page arrives, followed by a fresh navigator."""
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(t(chat_id, "pt_btn_prev_page"), callback_data=f"{cb_prefix}{page-1}"))
    if pages is None or page < pages:
        nav.append(InlineKeyboardButton(t(chat_id, "pt_btn_next_page"), callback_data=f"{cb_prefix}{page+1}"))
    rows = [nav] if nav else []
    rows.append([InlineKeyboardButton(t(chat_id, "btn_back"), callback_data=back_cb)])
    of = f" / {pages}" if pages else ""
    await client.send_message(chat_id, t(chat_id, "img_nav_text", query=query[:60], page=page, of=of),
                              reply_markup=InlineKeyboardMarkup(rows))


async def render_pinterest_search(msg: Message, query: str, chat_id: int, page: int = 1):
    try:
        store = user_data.setdefault(chat_id, {})
        if store.get("pt_query") != query or not store.get("pt_all"):
            results = await asyncio.to_thread(pinterest_search_sync, query, 25)
            store["pt_all"] = results
            store["pt_query"] = query

        pt_all = store.get("pt_all") or []
        if not pt_all:
            return await msg.edit_text(t(chat_id, "pt_no_results"), reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_pinterest")]]))

        page_size = 5
        pages = max(1, (len(pt_all) + page_size - 1) // page_size)
        page = max(1, min(page, pages))
        chunk = pt_all[(page - 1) * page_size: page * page_size]
        store["pt_page_items"] = chunk

        client = msg._client
        try:
            await msg.edit_text(t(chat_id, "pt_results_status", query=query, page=page, pages=pages))
        except Exception:
            pass

        # Each result is its own message with its own download button --
        # a shared media group can't carry per-item buttons, which was the
        # whole problem (users had to scroll back up to a separate picker
        # message to act on any given image).
        sent_any = False
        for i, it in enumerate(chunk):
            thumb = ((it.get("images") or {}).get("474x") or {}).get("url")
            if not thumb:
                continue
            title = (it.get("title") or it.get("grid_title") or "")[:200] or None
            rows = [[InlineKeyboardButton(t(chat_id, "pt_btn_download_hq"), callback_data=f"pt_pick_{i}")]]
            try:
                await client.send_photo(chat_id, photo=thumb, caption=title,
                                        reply_markup=InlineKeyboardMarkup(rows))
                sent_any = True
            except Exception as e:
                print(f"Pinterest search photo send failed: {e}", flush=True)

        if not sent_any:
            return await msg.edit_text(t(chat_id, "pt_previews_failed"), reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_pinterest")]]))
        try:
            await msg.delete()
        except Exception:
            pass
        await send_page_navigator(client, chat_id, query, page, pages, "ptpage_", "menu_pinterest")
    except Exception:
        traceback.print_exc()
        try:
            await msg.edit_text(t(chat_id, "pt_search_error"), reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_pinterest")]]))
        except Exception:
            pass

# ---------------- 5.5 REDDIT ENGINE ----------------
REDDIT_DL_DIR = "reddit_downloads"
os.makedirs(REDDIT_DL_DIR, exist_ok=True)


def reddit_get_json_sync(url: str, params: dict = None) -> dict:
    r = requests.get(url, params=params, cookies=REDDIT_COOKIE_JAR, headers=REDDIT_HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def _reddit_audio_url_sync(fallback_url: str, dash_url: str):
    """Reddit videos with sound split video/audio into separate DASH tracks --
    fallback_url alone is silent. Parse the manifest for the real audio file
    name instead of guessing it (naming differs across Reddit's video-encoding
    eras: CMAF_AUDIO_128.mp4 vs older DASH_audio.mp4)."""
    try:
        base = fallback_url.split("?")[0].rsplit("/", 1)[0]
        manifest = requests.get(dash_url, cookies=REDDIT_COOKIE_JAR, headers=REDDIT_HEADERS, timeout=10).text
        cands = re.findall(r"<BaseURL>([^<]+)</BaseURL>", manifest)
        audio_file = next((c for c in cands if "audio" in c.lower()), None)
        if audio_file:
            return f"{base}/{audio_file}"
    except Exception as e:
        print(f"reddit audio url error: {e}", flush=True)
    return None


def _reddit_post_media(d: dict) -> list:
    """A post's 'data' dict -> list of {'type': 'photo'|'video', 'url', 'audio_url'?}."""
    out = []
    # Crossposts carry no media of their own -- it lives on the parent post.
    if d.get("crosspost_parent_list") and not (d.get("is_video") or d.get("is_gallery")):
        parent = _reddit_post_media(d["crosspost_parent_list"][0])
        if parent:
            return parent
    if not d.get("is_video"):
        # GIF/image link posts often only expose a reddit_video_preview.
        rvp = (d.get("preview") or {}).get("reddit_video_preview") or (d.get("secure_media") or {}).get("reddit_video")
        if rvp and rvp.get("fallback_url"):
            return [{"type": "video", "url": rvp["fallback_url"], "audio_url": None}]
    if d.get("is_video"):
        rv = (d.get("media") or d.get("secure_media") or {}).get("reddit_video") or {}
        vurl = rv.get("fallback_url")
        if vurl:
            audio_url = None
            if rv.get("has_audio") and rv.get("dash_url"):
                audio_url = _reddit_audio_url_sync(vurl, rv["dash_url"])
            out.append({"type": "video", "url": vurl, "audio_url": audio_url})
        return out

    if d.get("is_gallery") and d.get("media_metadata"):
        items = (d.get("gallery_data") or {}).get("items") or []
        meta = d.get("media_metadata") or {}
        for item in items:
            m = meta.get(item.get("media_id")) or {}
            src = m.get("s") or {}
            u = src.get("u") or src.get("gif") or src.get("mp4")
            if u:
                out.append({"type": "video" if src.get("mp4") else "photo", "url": u.replace("&amp;", "&")})
        return out

    url = d.get("url_overridden_by_dest") or d.get("url") or ""
    if re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", url, re.I):
        out.append({"type": "photo", "url": url})
    elif re.search(r"\.gifv(\?|$)", url, re.I):
        out.append({"type": "video", "url": re.sub(r"\.gifv", ".mp4", url, flags=re.I)})
    elif re.search(r"\.(mp4|gif)(\?|$)", url, re.I):
        out.append({"type": "video" if url.lower().endswith(".mp4") else "photo", "url": url})
    elif re.search(r"(youtube\.com|youtu\.be|streamable\.com|imgur\.com|redgifs\.com|v\.redd\.it|twitter\.com|x\.com)", url, re.I):
        out.append({"type": "video", "url": url, "external": True})
    if not out:
        # Link posts: fall back to the preview image Reddit generated.
        imgs = (d.get("preview") or {}).get("images") or []
        src = (imgs[0].get("source") or {}).get("url") if imgs else None
        if src:
            out.append({"type": "photo", "url": src.replace("&amp;", "&")})
    return out


def download_reddit_video_sync(media: dict, out_path: str):
    if media.get("external"):
        # YouTube / Streamable / Imgur / RedGifs links: let yt-dlp fetch them.
        base = os.path.splitext(out_path)[0]
        opts = {'format': 'best[ext=mp4][height<=720]/best[height<=720]/best', 'outtmpl': base + '.%(ext)s',
                'quiet': True, 'no_warnings': True, 'noplaylist': True, 'max_filesize': 1900 * 1024 * 1024,
                'merge_output_format': 'mp4', **NET_RETRY_OPTS}
        if "youtu" in media["url"]:
            opts.update(_yt_dl_opts(base + '.%(ext)s'))
            opts['format'] = 'best[ext=mp4][height<=720]/best[height<=720]'
        timed_extract(opts, media["url"], "reddit-external")
        got = next((f for f in sorted(glob.glob(base + ".*")) if Path(f).suffix.lower() in VIDEO_EXTS), None)
        if not got:
            raise RuntimeError("external video download failed")
        if got != out_path:
            os.replace(got, out_path)
        return
    vtmp = out_path + ".v.mp4"
    download_url_to_file_sync(media["url"], vtmp)
    if media.get("audio_url"):
        atmp = out_path + ".a.mp4"
        try:
            download_url_to_file_sync(media["audio_url"], atmp)
            subprocess.run(
                [FFMPEG_BIN, "-y", "-loglevel", "error", "-i", vtmp, "-i", atmp,
                 "-c", "copy", "-map", "0:v:0", "-map", "1:a:0", out_path],
                check=True, capture_output=True, timeout=120)
            os.remove(vtmp)
            os.remove(atmp)
            return
        except Exception as e:
            print(f"reddit mux error: {e}", flush=True)
            if os.path.exists(atmp):
                os.remove(atmp)
    os.rename(vtmp, out_path)


def reddit_resolve_post_sync(post_id: str):
    data = reddit_get_json_sync(f"https://www.reddit.com/comments/{post_id}.json", params={"limit": 1})
    return data[0]["data"]["children"][0]["data"]


def reddit_subreddit_posts_sync(subreddit: str, limit: int = 15, sort: str = "hot"):
    data = reddit_get_json_sync(f"https://www.reddit.com/r/{subreddit}/{sort}.json", params={"limit": limit})
    return [c["data"] for c in (data.get("data", {}).get("children") or [])]


def reddit_search_sync(query: str, limit: int = 25):
    data = reddit_get_json_sync("https://www.reddit.com/search.json",
                                params={"q": query, "limit": limit, "sort": "relevance"})
    return [c["data"] for c in (data.get("data", {}).get("children") or [])]


async def deliver_reddit_media_item(client: Client, chat_id: int, media: dict, caption: str = None, reply_markup=None):
    ext = "mp4" if media["type"] == "video" else "jpg"
    path = os.path.join(REDDIT_DL_DIR, f"rd_{int(time.time()*1000)}_{random.randint(1000,9999)}.{ext}")
    if reply_markup is None:
        if media["type"] == "video":
            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_edit_this_video"), callback_data="vedit_from_last")]])
        else:
            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🎨 ابزارهای تصویر", callback_data="menu_image_tools")]])
    try:
        if media["type"] == "video":
            await asyncio.to_thread(download_reddit_video_sync, media, path)
            sent = await client.send_video(chat_id, video=path, caption=caption, supports_streaming=True, reply_markup=reply_markup)
            if sent and (sent.video or sent.document):
                user_data.setdefault(chat_id, {})["last_video_file_id"] = (sent.video or sent.document).file_id
        else:
            await asyncio.to_thread(download_url_to_file_sync, media["url"], path)
            sent = await client.send_photo(chat_id, photo=path, caption=caption, reply_markup=reply_markup)
            if sent and (sent.photo or sent.document):
                user_data.setdefault(chat_id, {})["last_image_file_id"] = (sent.photo or sent.document).file_id
        return True
    except Exception as e:
        print(f"reddit item deliver error: {e}", flush=True)
        return False
    finally:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


STRINGS.update({
    "rd_post_not_found": {"fa": "❌ این پست پیدا نشد یا خصوصی است.", "en": "❌ This post wasn't found or is private.", "ru": "❌ Этот пост не найден или является приватным.", "es": "❌ No se encontró esta publicación o es privada."},
    "rd_post_no_media": {"fa": "❌ این پست هیچ عکس یا ویدیویی ندارد.", "en": "❌ This post has no photo or video.", "ru": "❌ У этого поста нет фото или видео.", "es": "❌ Esta publicación no tiene foto ni video."},
    "rd_fetching_generic": {"fa": "⬇️ **در حال دریافت...**\n{title}", "en": "⬇️ **Fetching...**\n{title}", "ru": "⬇️ **Получение...**\n{title}", "es": "⬇️ **Obteniendo...**\n{title}"},
    "rd_media_fetch_failed": {"fa": "❌ دریافت مدیای این پست با خطا مواجه شد.", "en": "❌ Failed to fetch this post's media.", "ru": "❌ Не удалось получить медиа этого поста.", "es": "❌ No se pudo obtener el contenido de esta publicación."},
    "rd_subreddit_not_found": {"fa": "❌ این ساب‌ردیت پیدا نشد یا خصوصی است.", "en": "❌ This subreddit wasn't found or is private.", "ru": "❌ Этот сабреддит не найден или является приватным.", "es": "❌ No se encontró este subreddit o es privado."},
    "rd_subreddit_empty": {"fa": "❌ پستی در این ساب‌ردیت یافت نشد.", "en": "❌ No posts found in this subreddit.", "ru": "❌ В этом сабреддите не найдено постов.", "es": "❌ No se encontraron publicaciones en este subreddit."},
    "rd_subreddit_fetching": {"fa": "📋 **r/{subreddit}**\nدر حال دریافت پست‌های برتر...", "en": "📋 **r/{subreddit}**\nFetching top posts...", "ru": "📋 **r/{subreddit}**\nПолучение лучших постов...", "es": "📋 **r/{subreddit}**\nObteniendo las mejores publicaciones..."},
    "rd_subreddit_all_failed": {"fa": "❌ دانلود هیچ پستی موفق نبود.", "en": "❌ Failed to download any post.", "ru": "❌ Не удалось загрузить ни один пост.", "es": "❌ No se pudo descargar ninguna publicación."},
})

async def deliver_reddit_post(client: Client, chat_id: int, msg: Message, post_id: str):
    try:
        post = await asyncio.to_thread(reddit_resolve_post_sync, post_id)
    except Exception:
        print(f"Reddit post fetch error:\n{traceback.format_exc()}", flush=True)
        return await msg.edit_text(t(chat_id, "rd_post_not_found"))

    medias = _reddit_post_media(post)
    if not medias:
        return await msg.edit_text(t(chat_id, "rd_post_no_media"))

    title = (post.get("title") or "")[:200]
    try:
        await msg.edit_text(t(chat_id, "rd_fetching_generic", title=title))
    except Exception:
        pass

    ok_count = 0
    if len(medias) == 1:
        ok = await deliver_reddit_media_item(client, chat_id, medias[0], caption=title)
        ok_count = 1 if ok else 0
    else:
        # Gallery: batch as a media group, same pattern as albums/boards.
        paths = []
        for i, m in enumerate(medias[:10]):
            ext = "mp4" if m["type"] == "video" else "jpg"
            p = os.path.join(REDDIT_DL_DIR, f"rdg_{int(time.time()*1000)}_{i}.{ext}")
            try:
                if m["type"] == "video":
                    await asyncio.to_thread(download_reddit_video_sync, m, p)
                else:
                    await asyncio.to_thread(download_url_to_file_sync, m["url"], p)
                paths.append((p, m["type"]))
            except Exception as e:
                print(f"reddit gallery item failed: {e}", flush=True)
        if paths:
            group = [(InputMediaVideo(p, caption=title) if mtype == "video" else InputMediaPhoto(p, caption=title))
                     if i == 0 else (InputMediaVideo(p) if mtype == "video" else InputMediaPhoto(p))
                     for i, (p, mtype) in enumerate(paths)]
            try:
                await client.send_media_group(chat_id, group)
                ok_count = len(paths)
            except Exception as e:
                print(f"reddit gallery group send failed: {e}", flush=True)
        for p, _ in paths:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    if ok_count == 0:
        return await msg.edit_text(t(chat_id, "rd_media_fetch_failed"))
    log_activity(chat_id, "reddit", f"{title} ({ok_count} مورد)")
    try:
        await msg.delete()
    except Exception:
        pass


async def deliver_reddit_subreddit(client: Client, chat_id: int, msg: Message, subreddit: str):
    try:
        posts = await asyncio.to_thread(reddit_subreddit_posts_sync, subreddit, 15, "hot")
    except Exception:
        print(f"Reddit subreddit fetch error:\n{traceback.format_exc()}", flush=True)
        return await msg.edit_text(t(chat_id, "rd_subreddit_not_found"))
    if not posts:
        return await msg.edit_text(t(chat_id, "rd_subreddit_empty"))

    try:
        await msg.edit_text(t(chat_id, "rd_subreddit_fetching", subreddit=subreddit))
    except Exception:
        pass

    downloaded = []  # (path, type)
    for post in posts:
        medias = _reddit_post_media(post)
        if not medias:
            continue
        m = medias[0]  # one item per post here -- galleries open fully via their own post link
        ext = "mp4" if m["type"] == "video" else "jpg"
        p = os.path.join(REDDIT_DL_DIR, f"rds_{post.get('id')}.{ext}")
        try:
            if m["type"] == "video":
                await asyncio.to_thread(download_reddit_video_sync, m, p)
            else:
                await asyncio.to_thread(download_url_to_file_sync, m["url"], p)
            downloaded.append((p, m["type"]))
        except Exception as e:
            print(f"reddit subreddit item failed: {e}", flush=True)
        if len(downloaded) >= 15:
            break

    if not downloaded:
        return await msg.edit_text(t(chat_id, "rd_subreddit_all_failed"))

    log_activity(chat_id, "reddit_bulk", f"r/{subreddit} ({len(downloaded)} پست)")

    for batch_start in range(0, len(downloaded), 10):
        batch = downloaded[batch_start:batch_start + 10]
        group = [InputMediaVideo(p) if mtype == "video" else InputMediaPhoto(p) for p, mtype in batch]
        try:
            await client.send_media_group(chat_id, group)
        except Exception as e:
            print(f"reddit subreddit group send failed: {e}", flush=True)
            for p, mtype in batch:
                try:
                    if mtype == "video":
                        await client.send_video(chat_id, video=p)
                    else:
                        await client.send_photo(chat_id, photo=p)
                except Exception as e2:
                    print(f"reddit subreddit item send failed: {e2}", flush=True)

    for p, _ in downloaded:
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    try:
        await msg.delete()
    except Exception:
        pass


STRINGS.update({
    "rd_no_results": {"fa": "❌ نتیجه‌ای پیدا نشد.", "en": "❌ No results found.", "ru": "❌ Результаты не найдены.", "es": "❌ No se encontraron resultados."},
    "rd_results_status": {
        "fa": "🔎 **نتایج «{query}»** — صفحه {page} از {pages}\nدر حال ارسال پیش‌نمایش...",
        "en": "🔎 **Results for \"{query}\"** — page {page} of {pages}\nSending previews...",
        "ru": "🔎 **Результаты «{query}»** — стр. {page} из {pages}\nОтправка предпросмотров...",
        "es": "🔎 **Resultados de «{query}»** — página {page} de {pages}\nEnviando vistas previas...",
    },
    "rd_btn_download": {"fa": "⬇️ دانلود", "en": "⬇️ Download", "ru": "⬇️ Скачать", "es": "⬇️ Descargar"},
    "rd_previews_failed": {"fa": "❌ نمایش پیش‌نمایش‌ها با خطا مواجه شد.", "en": "❌ Failed to show previews.", "ru": "❌ Не удалось показать предпросмотры.", "es": "❌ No se pudieron mostrar las vistas previas."},
    "rd_search_error": {"fa": "❌ خطا در جستجوی ردیت.", "en": "❌ Reddit search error.", "ru": "❌ Ошибка поиска в Reddit.", "es": "❌ Error en la búsqueda de Reddit."},
    "rd_fetching_post": {"fa": "⬇️ **در حال دریافت پست...**", "en": "⬇️ **Fetching post...**", "ru": "⬇️ **Получение поста...**", "es": "⬇️ **Obteniendo publicación...**"},
    "rd_fetching_subreddit": {"fa": "📋 **در حال دریافت ساب‌ردیت...**", "en": "📋 **Fetching subreddit...**", "ru": "📋 **Получение сабреддита...**", "es": "📋 **Obteniendo subreddit...**"},
    "rd_invalid_link": {"fa": "❌ لینک ردیت نامعتبر است.", "en": "❌ Invalid Reddit link.", "ru": "❌ Недействительная ссылка Reddit.", "es": "❌ Enlace de Reddit no válido."},
})

async def render_reddit_search(msg: Message, query: str, chat_id: int, page: int = 1):
    try:
        store = user_data.setdefault(chat_id, {})
        if store.get("rd_query") != query or not store.get("rd_all"):
            results = await asyncio.to_thread(reddit_search_sync, query, 25)
            store["rd_all"] = results
            store["rd_query"] = query

        rd_all = store.get("rd_all") or []
        if not rd_all:
            return await msg.edit_text(t(chat_id, "rd_no_results"), reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_reddit")]]))

        page_size = 5
        pages = max(1, (len(rd_all) + page_size - 1) // page_size)
        page = max(1, min(page, pages))
        chunk = rd_all[(page - 1) * page_size: page * page_size]
        store["rd_page_items"] = chunk

        client = msg._client
        try:
            await msg.edit_text(t(chat_id, "rd_results_status", query=query, page=page, pages=pages))
        except Exception:
            pass

        sent_any = False
        for i, post in enumerate(chunk):
            medias = _reddit_post_media(post)
            thumb = post.get("thumbnail")
            preview_url = thumb if (thumb and thumb.startswith("http")) else (
                medias[0]["url"] if medias and medias[0]["type"] == "photo" else None)
            title = f"{post.get('title','')[:150]}\nr/{post.get('subreddit','')}"
            rows = [[InlineKeyboardButton(t(chat_id, "rd_btn_download"), callback_data=f"rd_pick_{i}")]]
            if i == len(chunk) - 1:
                nav = []
                if page > 1: nav.append(InlineKeyboardButton(t(chat_id, "pt_btn_prev_page"), callback_data=f"rdpage_{page-1}"))
                if page < pages: nav.append(InlineKeyboardButton(t(chat_id, "pt_btn_next_page"), callback_data=f"rdpage_{page+1}"))
                if nav: rows.append(nav)
                rows.append([InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_reddit")])
            try:
                if preview_url:
                    await client.send_photo(chat_id, photo=preview_url, caption=title,
                                            reply_markup=InlineKeyboardMarkup(rows))
                else:
                    await client.send_message(chat_id, f"🎬 {title}", reply_markup=InlineKeyboardMarkup(rows))
                sent_any = True
            except Exception as e:
                print(f"Reddit search preview send failed: {e}", flush=True)

        if not sent_any:
            return await msg.edit_text(t(chat_id, "rd_previews_failed"), reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_reddit")]]))
        try:
            await msg.delete()
        except Exception:
            pass
    except Exception:
        traceback.print_exc()
        try:
            await msg.edit_text(t(chat_id, "rd_search_error"), reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_reddit")]]))
        except Exception:
            pass


async def process_reddit_link(client: Client, chat_id: int, msg: Message, url: str):
    if "redd.it" in url or "/s/" in url:
        try:
            url = await asyncio.to_thread(
                lambda: requests.head(url, allow_redirects=True, headers=REDDIT_HEADERS, timeout=10).url)
        except Exception:
            pass

    m = re.search(r"/comments/([a-z0-9]+)", url)
    if m:
        await msg.edit_text(t(chat_id, "rd_fetching_post"))
        return await deliver_reddit_post(client, chat_id, msg, m.group(1))

    m2 = re.search(r"reddit\.com/r/([A-Za-z0-9_]+)", url)
    if m2:
        await msg.edit_text(t(chat_id, "rd_fetching_subreddit"))
        return await deliver_reddit_subreddit(client, chat_id, msg, m2.group(1))

    await msg.edit_text(t(chat_id, "rd_invalid_link"))

# ---------------- 5.6 TWITTER / X ENGINE ----------------
# The menu has advertised "Reddit / Twitter (X)" all along, but no X link was
# ever handled -- they fell through the text handler silently. FxTwitter's
# public API returns every photo/video/GIF of a tweet (yt-dlp only sees
# videos and needs a guest token that X hands out less and less); yt-dlp
# stays as the fallback for video tweets.
TWITTER_DL_DIR = "twitter_downloads"
os.makedirs(TWITTER_DL_DIR, exist_ok=True)
TWITTER_URL_RE = re.compile(
    r"https?://(?:www\.|mobile\.)?(?:twitter|x|fxtwitter|vxtwitter|fixupx|fixvx)\.com/([A-Za-z0-9_]+|i(?:/web)?)/status(?:es)?/(\d+)",
    re.IGNORECASE)

STRINGS.update({
    "tw_fetching": {"fa": "🐦 **در حال دریافت پست توییتر (X)...**", "en": "🐦 **Fetching the X (Twitter) post...**",
                    "ru": "🐦 **Получение поста X (Twitter)...**", "es": "🐦 **Obteniendo la publicación de X (Twitter)...**"},
    "tw_no_media": {"fa": "❌ این توییت عکس یا ویدیویی ندارد.", "en": "❌ This tweet has no photo or video.",
                    "ru": "❌ В этом твите нет фото или видео.", "es": "❌ Este tweet no tiene foto ni video."},
    "tw_failed": {"fa": "❌ دریافت این توییت با خطا مواجه شد (ممکن است حذف یا خصوصی شده باشد).",
                  "en": "❌ Couldn't fetch this tweet (it may be deleted or protected).",
                  "ru": "❌ Не удалось получить твит (возможно, он удалён или защищён).",
                  "es": "❌ No se pudo obtener este tweet (puede estar eliminado o protegido)."},
})


def twitter_fetch_sync(user: str, tweet_id: str):
    """Download a tweet's media. Returns {'files': [...], 'caption': str}."""
    files, caption = [], ""
    try:
        r = requests.get(f"https://api.fxtwitter.com/{user}/status/{tweet_id}",
                         headers={"User-Agent": PINTEREST_UA}, timeout=15)
        tw = (r.json() or {}).get("tweet") if r.ok else None
    except Exception as e:
        print(f"fxtwitter error: {e}", flush=True)
        tw = None
    if tw:
        author = (tw.get("author") or {}).get("screen_name") or user
        caption = f"{tw.get('text') or ''}\n\n— @{author}".strip()
        for i, m in enumerate(((tw.get("media") or {}).get("all") or [])[:10]):
            url = m.get("url") or ""
            if not url:
                continue
            is_video = m.get("type") in ("video", "gif")
            if not is_video and "pbs.twimg.com" in url and "name=" not in url:
                url += ("&" if "?" in url else "?") + "name=orig"
            path = os.path.join(TWITTER_DL_DIR, f"tw_{tweet_id}_{i}.{'mp4' if is_video else 'jpg'}")
            try:
                download_url_to_file_sync(url, path)
                files.append(path)
            except Exception as e:
                print(f"tweet media {i} failed: {e}", flush=True)
    if not files:
        # Fallback: yt-dlp (video tweets only).
        prefix = os.path.join(TWITTER_DL_DIR, f"twy_{tweet_id}_")
        try:
            opts = {'format': 'best[ext=mp4]/best', 'outtmpl': prefix + '%(autonumber)s.%(ext)s',
                    'quiet': True, 'no_warnings': True, **NET_RETRY_OPTS}
            info = timed_extract(opts, f"https://x.com/{user}/status/{tweet_id}", "twitter")
            caption = caption or (info.get("description") or info.get("title") or "")
            files = [f for f in sorted(glob.glob(prefix + "*")) if Path(f).suffix.lower() in VIDEO_EXTS + IMAGE_EXTS]
        except Exception as e:
            print(f"twitter yt-dlp fallback failed: {str(e)[:160]}", flush=True)
    return {"files": files, "caption": caption[:3900], "found": bool(tw)}


async def process_twitter_link(client: Client, chat_id: int, msg: Message, url: str):
    m = TWITTER_URL_RE.search(url)
    if not m:
        return await msg.edit_text(t(chat_id, "tw_failed"))
    user, tid = m.group(1), m.group(2)
    if user.lower() in ("i", "i/web"):
        user = "i"
    try:
        await msg.edit_text(t(chat_id, "tw_fetching"))
    except Exception:
        pass  # already showing this text (MESSAGE_NOT_MODIFIED)
    files = []
    try:
        data = await asyncio.to_thread(twitter_fetch_sync, user, tid)
        files = data["files"]
        if not files:
            return await msg.edit_text(t(chat_id, "tw_no_media" if data.get("found") else "tw_failed"))
        await msg.delete()
        await send_post_media(client, chat_id, files, data["caption"], "🐦 ")
        log_activity(chat_id, "twitter", url)
    except Exception:
        print(f"Twitter error:\n{traceback.format_exc()}", flush=True)
        try:
            await msg.edit_text(t(chat_id, "tw_failed"))
        except Exception:
            await client.send_message(chat_id, t(chat_id, "tw_failed"))
    finally:
        for f in files:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass


# ---------------- 5.6 IMAGE SEARCH (PEXELS) ----------------
IMAGE_DL_DIR = "image_downloads"
os.makedirs(IMAGE_DL_DIR, exist_ok=True)


def pexels_search_sync(query: str, page: int = 1, per_page: int = 15):
    r = requests.get("https://api.pexels.com/v1/search",
                     headers={"Authorization": PEXELS_API_KEY},
                     params={"query": query, "page": page, "per_page": per_page}, timeout=15)
    r.raise_for_status()
    j = r.json()
    return [{"id": p["id"], "photographer": p.get("photographer", ""),
             "src": {"medium": p["src"]["medium"], "large": p["src"].get("large"), "original": p["src"]["original"]}}
            for p in (j.get("photos") or [])]


STRINGS.update({
    "img_fetching_full": {"fa": "⬇️ **در حال دریافت کیفیت کامل...**", "en": "⬇️ **Fetching full quality...**", "ru": "⬇️ **Получение полного качества...**", "es": "⬇️ **Obteniendo calidad completa...**"},
    "img_fetch_failed": {"fa": "❌ دریافت تصویر با خطا مواجه شد.", "en": "❌ Failed to fetch the image.", "ru": "❌ Не удалось получить изображение.", "es": "❌ No se pudo obtener la imagen."},
    "img_no_results": {"fa": "❌ نتیجه‌ای پیدا نشد.", "en": "❌ No results found.", "ru": "❌ Результаты не найдены.", "es": "❌ No se encontraron resultados."},
    "img_feature_disabled": {"fa": "❌ این قابلیت روی سرور فعلی فعال نیست.", "en": "❌ This feature isn't enabled on the current server.", "ru": "❌ Эта функция не включена на текущем сервере.", "es": "❌ Esta función no está habilitada en el servidor actual."},
    "img_results_status": {
        "fa": "🖼 **نتایج «{query}»** — صفحه {page}\nدر حال ارسال پیش‌نمایش...",
        "en": "🖼 **Results for \"{query}\"** — page {page}\nSending previews...",
        "ru": "🖼 **Результаты «{query}»** — стр. {page}\nОтправка предпросмотров...",
        "es": "🖼 **Resultados de «{query}»** — página {page}\nEnviando vistas previas...",
    },
    "img_btn_full_quality": {"fa": "⬇️ کیفیت کامل", "en": "⬇️ Full Quality", "ru": "⬇️ Полное качество", "es": "⬇️ Calidad completa"},
    "img_search_error": {"fa": "❌ خطا در جستجوی تصویر.", "en": "❌ Image search error.", "ru": "❌ Ошибка поиска изображений.", "es": "❌ Error en la búsqueda de imágenes."},
})

# "Google Images": real Google only through the Custom Search JSON API (set
# GOOGLE_CSE_KEY + GOOGLE_CSE_CX; free for 100 searches/day). Google's normal
# result pages, Bing and Yandex return nothing useful from this server's IP,
# so without keys the button searches Wikimedia Commons -- a real web image
# search with full-resolution originals and no key required.
GOOGLE_CSE_KEY = os.environ.get("GOOGLE_CSE_KEY", "").strip()
GOOGLE_CSE_CX = os.environ.get("GOOGLE_CSE_CX", "").strip()
_WIKI_UA = {"User-Agent": "TahasBot/1.0 (Bale messenger bot; image search; https://ble.ir/tahasbot)"}


def google_cse_search_sync(query: str, page: int = 1, per_page: int = 8):
    r = requests.get("https://www.googleapis.com/customsearch/v1",
                     params={"key": GOOGLE_CSE_KEY, "cx": GOOGLE_CSE_CX, "q": query, "searchType": "image",
                             "num": min(per_page, 10), "start": (page - 1) * min(per_page, 10) + 1, "safe": "active"},
                     timeout=15)
    r.raise_for_status()
    out = []
    for it in r.json().get("items") or []:
        img = it.get("image") or {}
        out.append({"id": hashlib.md5(it["link"].encode()).hexdigest()[:10],
                    "photographer": (it.get("displayLink") or "Google")[:60],
                    "src": {"medium": img.get("thumbnailLink") or it["link"], "large": it["link"], "original": it["link"]}})
    return out


def wikimedia_search_sync(query: str, page: int = 1, per_page: int = 8):
    r = requests.get("https://commons.wikimedia.org/w/api.php", params={
        "action": "query", "format": "json", "generator": "search", "gsrsearch": f"{query} filetype:bitmap",
        "gsrnamespace": 6, "gsrlimit": per_page, "gsroffset": (page - 1) * per_page,
        "prop": "imageinfo", "iiprop": "url|mime|size", "iiurlwidth": 800}, headers=_WIKI_UA, timeout=15)
    r.raise_for_status()
    pages = sorted(((r.json().get("query") or {}).get("pages") or {}).values(), key=lambda x: x.get("index", 0))
    out = []
    for pg in pages:
        ii = (pg.get("imageinfo") or [{}])[0]
        if not ii.get("url") or not str(ii.get("mime", "")).startswith("image/") or ii.get("mime") == "image/svg+xml":
            continue
        out.append({"id": str(pg.get("pageid")),
                    "photographer": re.sub(r"^File:|\.[a-zA-Z]{3,4}$", "", pg.get("title") or "")[:60],
                    "src": {"medium": ii.get("thumburl") or ii["url"], "large": ii.get("thumburl") or ii["url"],
                            "original": ii["url"]}})
    return out


def fetch_full_image_sync(photo: dict) -> str:
    """Best available original that Bale will accept: the real original when
    it is a normal image under the upload cap, else the large rendition.
    Wikimedia refuses generic browser user agents, and many Google hits
    block hotlinking, hence the fallbacks."""
    limit = int(getattr(bot, "upload_limit", 50 * 1024 * 1024))
    os.makedirs(IMAGE_DL_DIR, exist_ok=True)
    src = photo.get("src") or {}
    last = None
    for url in [src.get("original"), src.get("large"), src.get("medium")]:
        if not url:
            continue
        ua = _WIKI_UA if "wikimedia.org" in url else {"User-Agent": PINTEREST_UA}
        try:
            r = requests.get(url, headers=ua, timeout=60, stream=True)
            r.raise_for_status()
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype and not ctype.startswith("image/"):
                raise RuntimeError(f"not an image ({ctype})")
            ext = {"image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}.get(ctype, ".jpg")
            if ctype in ("image/tiff", "image/svg+xml"):
                raise RuntimeError(f"unsupported format {ctype}")
            path = os.path.join(IMAGE_DL_DIR, f"img_{hashlib.md5(url.encode()).hexdigest()[:10]}{ext}")
            size = 0
            with open(path, "wb") as fh:
                for chunk in r.iter_content(1 << 16):
                    size += len(chunk)
                    if size > limit:
                        raise RuntimeError("larger than the upload limit")
                    fh.write(chunk)
            return path
        except Exception as e:
            last = e
            print(f"full image fetch failed for {url[:80]}: {e}", flush=True)
    raise RuntimeError(f"no downloadable rendition: {last}")


def image_provider_search_sync(provider: str, query: str, page: int, per_page: int = 8):
    if provider == "google":
        if GOOGLE_CSE_KEY and GOOGLE_CSE_CX:
            try:
                return google_cse_search_sync(query, page, per_page)
            except Exception as e:
                print(f"google cse error: {e}", flush=True)
        return wikimedia_search_sync(query, page, per_page)
    return pexels_search_sync(query, page, per_page)


async def render_image_search(msg: Message, query: str, chat_id: int, page: int = 1, provider: str = None):
    store = user_data.setdefault(chat_id, {})
    provider = provider or store.get("img_provider") or "pexels"
    store["img_provider"] = provider
    if provider == "pexels" and not PEXELS_API_KEY:
        return await msg.edit_text(t(chat_id, "img_feature_disabled"))
    back_cb = "menu_google_images" if provider == "google" else "menu_pexels"
    try:
        cache_key = f"{provider}:{query}:{page}"
        if store.get("img_cache_key") != cache_key:
            results = await asyncio.to_thread(image_provider_search_sync, provider, query, page, 8)
            store["img_all_page"] = results
            store["img_cache_key"] = cache_key
            store["img_query"] = query

        results = store.get("img_all_page") or []
        if not results:
            return await msg.edit_text(t(chat_id, "img_no_results"), reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data=back_cb)]]))

        store["img_page_items"] = results
        client = msg._client
        try:
            await msg.edit_text(t(chat_id, "img_results_status", query=query, page=page))
        except Exception:
            pass

        sent_any = False
        by_id = store.setdefault("img_by_id", {})
        if len(by_id) > 400:
            by_id.clear()
        for i, photo in enumerate(results):
            # Keyed by photo id, so buttons from earlier pages/searches still work.
            pid = hashlib.md5(f"{provider}:{photo['id']}".encode()).hexdigest()[:12]
            by_id[pid] = dict(photo, _provider=provider)
            rows = [[InlineKeyboardButton(t(chat_id, "img_btn_full_quality"), callback_data=f"img_pick_{pid}")]]
            try:
                await client.send_photo(chat_id, photo=photo["src"]["medium"],
                                        caption=f"📸 {photo['photographer']}",
                                        reply_markup=InlineKeyboardMarkup(rows))
                sent_any = True
            except Exception as e:
                print(f"image search preview send failed: {e}", flush=True)

        if not sent_any:
            return await msg.edit_text(t(chat_id, "rd_previews_failed"))
        try:
            await msg.delete()
        except Exception:
            pass
        await send_page_navigator(client, chat_id, query, page, None, "imgpage_", back_cb)
    except Exception:
        traceback.print_exc()
        try:
            await msg.edit_text(t(chat_id, "img_search_error"))
        except Exception:
            pass

# ---------------- 5.7 REVERSE AUDIO SEARCH (AUDD) ----------------
AUDD_DL_DIR = "audd_downloads"
os.makedirs(AUDD_DL_DIR, exist_ok=True)


def extract_audio_clip_sync(src_path: str, out_path: str, duration: int = 20):
    """AudD's standard endpoint is built around short clips, not whole files
    -- a ~20s slice recognizes just as reliably as the full track and is
    far less to upload. Start a few seconds in to skip a silent/quiet intro
    some voice notes and video openings have."""
    subprocess.run(
        [FFMPEG_BIN, "-y", "-loglevel", "error", "-i", src_path, "-ss", "3", "-t", str(duration),
         "-vn", "-ar", "44100", "-ac", "2", out_path],
        check=True, capture_output=True, timeout=60)


class RecognitionUnavailable(Exception):
    """Every recognition backend is down / unauthorised (not the same as 'no match')."""


def shazam_recognize_sync(clip_path: str):
    """Shazam via shazamio: free, no API key. Returns a normalised dict or None."""
    from shazamio import Shazam
    out = asyncio.run(Shazam().recognize(clip_path))
    tr = (out or {}).get("track") or {}
    if not tr.get("title"):
        return None
    meta = {}
    for sec in tr.get("sections") or []:
        if sec.get("type") == "SONG":
            meta = {m.get("title"): m.get("text") for m in sec.get("metadata") or []}
    imgs = tr.get("images") or {}
    cover = (imgs.get("coverarthq") or imgs.get("coverart") or "").replace("400x400cc", "1000x1000cc")
    return {"title": tr.get("title"), "artist": tr.get("subtitle"), "album": meta.get("Album") or "",
            "release_date": meta.get("Released") or "", "cover": cover, "isrc": tr.get("isrc") or "",
            "spotify_id": "", "duration": 0}


def recognize_song_sync(clip_path: str):
    """Shazam first (free), AudD second. None = nothing matched; raises
    RecognitionUnavailable when no backend could even run."""
    ran = False
    try:
        res = shazam_recognize_sync(clip_path)
        ran = True
        if res:
            return res
    except Exception as e:
        print(f"shazam recognition error: {e}", flush=True)
    if AUDD_API_KEY:
        try:
            r = audd_recognize_sync(clip_path)
            ran = True
            if r:
                sp = r.get("spotify") or {}
                imgs = ((sp.get("album") or {}).get("images") or [])
                return {"title": r.get("title", ""), "artist": r.get("artist", ""), "album": r.get("album", ""),
                        "release_date": r.get("release_date") or (sp.get("album") or {}).get("release_date") or "",
                        "cover": imgs[0].get("url") if imgs else "", "spotify_id": sp.get("id") or "",
                        "isrc": (sp.get("external_ids") or {}).get("isrc") or "",
                        "duration": int((sp.get("duration_ms") or 0) // 1000)}
        except RecognitionUnavailable:
            pass
        except Exception as e:
            print(f"audd recognition error: {e}", flush=True)
    if not ran:
        raise RecognitionUnavailable()
    return None


def audd_recognize_sync(clip_path: str):
    with open(clip_path, "rb") as f:
        r = requests.post("https://api.audd.io/",
                          data={"api_token": AUDD_API_KEY, "return": "apple_music,spotify"},
                          files={"file": f}, timeout=30)
    r.raise_for_status()
    j = r.json()
    if j.get("status") == "success" and j.get("result"):
        return j["result"]
    if j.get("status") == "error":
        # e.g. 900 = token inactive / trial over. Not a "no match".
        print(f"AudD error: {str(j.get('error'))[:200]}", flush=True)
        raise RecognitionUnavailable()
    return None


STRINGS.update({
    "vedit_audio_title": {"fa": "صدای ویدیو", "en": "Video Audio", "ru": "Звук из видео", "es": "Audio del video"},
    "btn_audio_tools": {"fa": "🎛 ویرایش این فایل (ابزار صدا)", "en": "🎛 Edit this file (Audio Tools)",
                        "ru": "🎛 Редактировать файл (аудио)", "es": "🎛 Editar este archivo (audio)"},
    "audd_feature_disabled": {"fa": "❌ این قابلیت روی سرور فعلی فعال نیست.", "en": "❌ This feature isn't enabled on the current server.", "ru": "❌ Эта функция не включена на текущем сервере.", "es": "❌ Esta función no está habilitada en el servidor actual."},
    "audd_recognizing": {"fa": "🎧 **در حال شناسایی آهنگ...**", "en": "🎧 **Recognizing song...**", "ru": "🎧 **Распознавание песни...**", "es": "🎧 **Reconociendo canción...**"},
    "audd_no_match": {"fa": "❌ آهنگی شناسایی نشد.", "en": "❌ No song was recognized.", "ru": "❌ Песня не была распознана.", "es": "❌ No se reconoció ninguna canción."},
    "audd_recognized_text": {
        "fa": "🎧 **آهنگ شناسایی شد:**\n\n🎵 {title}\n👤 {artist}",
        "en": "🎧 **Song recognized:**\n\n🎵 {title}\n👤 {artist}",
        "ru": "🎧 **Песня распознана:**\n\n🎵 {title}\n👤 {artist}",
        "es": "🎧 **Canción reconocida:**\n\n🎵 {title}\n👤 {artist}",
    },
    "audd_btn_download_full": {"fa": "⬇️ دانلود آهنگ کامل", "en": "⬇️ Download Full Song", "ru": "⬇️ Скачать полную песню", "es": "⬇️ Descargar canción completa"},
    "audd_no_result_to_download": {"fa": "❌ نتیجه‌ای برای دانلود یافت نشد.", "en": "❌ No result found to download.", "ru": "❌ Результат для скачивания не найден.", "es": "❌ No se encontró ningún resultado para descargar."},
    "audd_searching_yt": {"fa": "⏳ در حال دانلود آهنگ...", "en": "⏳ Downloading song...", "ru": "⏳ Загрузка песни...", "es": "⏳ Descargando canción..."},
    "audd_receiving_audio": {"fa": "⬇️ **در حال دریافت فایل صوتی...**", "en": "⬇️ **Receiving audio file...**", "ru": "⬇️ **Получение аудиофайла...**", "es": "⬇️ **Recibiendo archivo de audio...**"},
    "audd_receive_failed": {"fa": "❌ دریافت فایل صوتی با خطا مواجه شد.", "en": "❌ Failed to receive the audio file.", "ru": "❌ Не удалось получить аудиофайл.", "es": "❌ No se pudo recibir el archivo de audio."},
    "audd_recognition_error": {"fa": "❌ شناسایی آهنگ با خطا مواجه شد.", "en": "❌ Song recognition failed.", "ru": "❌ Не удалось распознать песню.", "es": "❌ No se pudo reconocer la canción."},
})

async def process_audio_recognition(client: Client, chat_id: int, msg: Message, src_path: str):
    await msg.edit_text(t(chat_id, "audd_recognizing"))
    clip_path = src_path + "_clip.mp3"
    try:
        await asyncio.to_thread(extract_audio_clip_sync, src_path, clip_path)
        try:
            result = await asyncio.to_thread(recognize_song_sync, clip_path)
        except RecognitionUnavailable:
            return await msg.edit_text(t(chat_id, "audd_feature_disabled"))
        if not result:
            return await msg.edit_text(t(chat_id, "audd_no_match"), reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(t(chat_id, "btn_audio_tools"), callback_data="menu_music_tools")]]))

        title, artist = result.get("title", ""), result.get("artist", "")
        album = result.get("album", "")
        text = t(chat_id, "audd_recognized_text", title=title, artist=artist) + (f"\n💿 {album}" if album else "")
        user_data.setdefault(chat_id, {})["audd_result"] = dict(result)
        log_activity(chat_id, "audio_recognition", f"{title} — {artist}")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "audd_btn_download_full"), callback_data="audd_download")],
                                   [InlineKeyboardButton(t(chat_id, "btn_audio_tools"), callback_data="menu_music_tools")]])

        cover = result.get("cover")
        if cover:
            try:
                await client.send_photo(chat_id, photo=cover, caption=text, reply_markup=kb)
                return await msg.delete()
            except Exception:
                pass
        await msg.edit_text(text, reply_markup=kb)
    except Exception:
        print(f"AudD error:\n{traceback.format_exc()}", flush=True)
        try:
            await msg.edit_text(t(chat_id, "audd_recognition_error"))
        except Exception:
            pass
    finally:
        if os.path.exists(clip_path):
            try:
                os.remove(clip_path)
            except OSError:
                pass


@bot.on_message(filters.audio | filters.voice)
async def audio_recognition_handler(client: Client, message: Message):
    chat_id = message.chat.id
    a_obj = message.audio or message.voice
    if a_obj:
        user_data.setdefault(chat_id, {})["last_audio_file_id"] = a_obj.file_id
    if user_states.get(chat_id) == "AWAIT_AUDIO_UPLOAD":
        user_states.pop(chat_id, None)
        status_dl = await message.reply_text("⏳ در حال دریافت فایل صوتی...")
        try:
            os.makedirs("music_downloads", exist_ok=True)
            ext = ".mp3"
            if message.audio and message.audio.file_name:
                ext = os.path.splitext(message.audio.file_name)[1] or ".mp3"
            apath = await client.download_media(message, file_name=f"music_downloads/audio_{chat_id}_{int(time.time())}{ext}")
            user_data[chat_id]["music_tools_path"] = apath
            return await status_dl.edit_text("🎵 **فایل صوتی دریافت شد. ابزار یا افکت مورد نظر را انتخاب کنید:**", reply_markup=get_music_tools_keyboard(chat_id, get_lang))
        except Exception as e:
            return await status_dl.edit_text(f"❌ خطا: {e}")
    # In a group, only react after an explicit /tahashazam -- otherwise every
    # voice note or music file anyone shares would auto-trigger this. DMs keep
    # the instant, no-command-needed behavior.
    if message.chat.type != ChatType.PRIVATE:
        if user_states.get(chat_id) != "AWAIT_SHAZAM_UPLOAD":
            return
        user_states.pop(chat_id, None)
    status = await message.reply_text(t(chat_id, "audd_receiving_audio"))
    path = None
    try:
        path = await client.download_media(
            message, file_name=f"{AUDD_DL_DIR}/src_{message.chat.id}_{int(time.time())}")
    except Exception:
        return await status.edit_text(t(chat_id, "audd_receive_failed"))
    try:
        await process_audio_recognition(client, message.chat.id, status, path)
    finally:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass

# ---------------- 6. HELPERS & GENERAL SEARCH RENDERERS ----------------
def yt_api(endpoint, params):
    params["key"] = YOUTUBE_API_KEY
    try:
        r = requests.get(f"https://www.googleapis.com/youtube/v3/{endpoint}", params=params, timeout=10)
        return r.json()
    except: return {}

JOBS_DIR = os.path.join(APP_DIR, "jobs")
os.makedirs(JOBS_DIR, exist_ok=True)
# Clean up any leftover temporary job directories from previous crashed sessions
try:
    for item in os.listdir(JOBS_DIR):
        p = os.path.join(JOBS_DIR, item)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
except Exception:
    pass


GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "TheTcoTeam/TahasBot")
GITHUB_REF = os.environ.get("GITHUB_REF_NAME", "main")
WORKFLOW_FILE = os.environ.get("WORKFLOW_FILE", "downloader.yml")
AI_WORKFLOW_FILE = os.environ.get("AI_WORKFLOW_FILE", "ai_worker.yml")
MEDIA_WORKFLOW_FILE = os.environ.get("MEDIA_WORKFLOW_FILE", "media_tools.yml")


def _dispatch_workflow(workflow: str, payload: dict) -> bool:
    """Start a GitHub Actions job. Render's free instance only has 0.1 CPU and
    512 MB, so YouTube downloads, Whisper/Demucs and ffmpeg video edits run on
    GitHub's runners; they report back to the user over the Bale HTTP API."""
    if not GITHUB_TOKEN:
        print("GitHub dispatch skipped: GITHUB_TOKEN is not set", flush=True)
        return False
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{workflow}/dispatches"
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    inputs = {k: str(v) for k, v in payload.items() if v is not None}
    # admin-panel values the runners must honour too
    inputs.setdefault("UPLOAD_LIMIT_MB", str(upload_limit_mb()))
    inputs.setdefault("SIGNATURE", getattr(bot, "caption_signature", "") or "")
    try:
        r = requests.post(url, headers=headers, json={"ref": GITHUB_REF, "inputs": inputs}, timeout=15)
        if r.status_code != 204:
            print(f"dispatch {workflow} failed {r.status_code}: {r.text[:300]}", flush=True)
        return r.status_code == 204
    except Exception as e:
        print(f"dispatch {workflow} error: {e}", flush=True)
        return False


def trigger_ai_worker(payload: dict) -> bool:
    return _dispatch_workflow(AI_WORKFLOW_FILE, payload)


def track_source_url(doc: dict):
    """Recover a downloadable URL for a cached track."""
    if not doc:
        return None
    src = str(doc.get("source_url") or "")
    if src.startswith("http"):
        return src
    vid = str(doc.get("vid_id") or "")
    if vid.startswith("http"):
        return vid
    if re.fullmatch(r"[0-9A-Za-z_-]{11}", vid):
        return f"https://www.youtube.com/watch?v={vid}"
    return None


def trigger_github_worker(payload: dict) -> bool:
    return _dispatch_workflow(WORKFLOW_FILE, payload)


def trigger_media_worker(payload: dict) -> bool:
    return _dispatch_workflow(MEDIA_WORKFLOW_FILE, payload)

def parse_pt_duration(duration_str):
    m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', str(duration_str))
    if not m: return "0:00"
    h, m, s = [int(x) if x else 0 for x in m.groups()]
    return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"

def parse_duration_to_seconds(duration_str):
    m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', str(duration_str))
    if not m: return 0
    h, m, s = [int(x) if x else 0 for x in m.groups()]
    return h * 3600 + m * 60 + s

def format_number(num):
    try:
        n = int(num)
        if n >= 1000000: return f"{n/1000000:.1f}M"
        if n >= 1000: return f"{n/1000:.1f}K"
        return str(n)
    except: return str(num)

user_states = {}
user_data = {}

# Persian is RTL: inline "•" separators and long ━ rules scramble the layout
# visually. One idea per line, Persian commas, emoji as line markers.
STRINGS.update({
    "main_menu_text": {
        "fa": "👋 سلام **{name}** عزیز! به ربات همه‌کاره **بات طاها** خوش آمدید.\n\n⚡️ شما می‌توانید هر لینکی (اینستاگرام، تیک‌تاک، یوتیوب، اسپاتیفای، ساندکلاد، ردیت، پینترست و...) را مستقیماً ارسال کنید تا بلافاصله دانلود شود.\n\n🎛 یا از منوی زیر بخش مورد نظر را انتخاب کنید:",
        "en": "✨ **Taha Bot**\nYour all-in-one download and music bot\n\n🎬  YouTube, Instagram, TikTok\n🎵  Original music and SoundCloud\n🎛  Audio effects, lyrics, full albums\n\n👇  Pick a section below",
        "ru": "✨ **Taha Bot**\nБот для скачивания и музыки на все случаи\n\n🎬  YouTube, Instagram, TikTok\n🎵  Оригинальная музыка и SoundCloud\n🎛  Аудиоэффекты, текст песни, целые альбомы\n\n👇  Выберите раздел ниже",
        "es": "✨ **Taha Bot**\nTu bot todo-en-uno de descargas y música\n\n🎬  YouTube, Instagram, TikTok\n🎵  Música original y SoundCloud\n🎛  Efectos de audio, letras, álbumes completos\n\n👇  Elige una sección abajo",
    },
    "btn_downloader": {"fa": "📥 دانلودر چندرسانه‌ای", "en": "📥 Media Downloader", "ru": "📥 Загрузчик медиа", "es": "📥 Descargador de medios"},
    "btn_music": {"fa": "🎵 موزیک", "en": "🎵 Music", "ru": "🎵 Музыка", "es": "🎵 Música"},
    "btn_vedit": {"fa": "🎬 ویرایش ویدیو", "en": "🎬 Video Editor", "ru": "🎬 Видеоредактор", "es": "🎬 Editor de video"},
    "btn_pinterest": {"fa": "📌 پینترست", "en": "📌 Pinterest", "ru": "📌 Pinterest", "es": "📌 Pinterest"},
    "btn_reddit": {"fa": "🔴 ردیت", "en": "🔴 Reddit", "ru": "🔴 Reddit", "es": "🔴 Reddit"},
    "btn_images": {"fa": "🖼 جستجوی تصویر", "en": "🖼 Image Search", "ru": "🖼 Поиск изображений", "es": "🖼 Buscar imágenes"},
    "btn_shazam": {"fa": "🎧 شناسایی آهنگ", "en": "🎧 Song Recognition", "ru": "🎧 Распознавание песни", "es": "🎧 Reconocer canción"},
    "btn_library": {"fa": "📚 کتابخانه من", "en": "📚 My Library", "ru": "📚 Моя библиотека", "es": "📚 Mi biblioteca"},
    "btn_stats": {"fa": "📊 آمار من", "en": "📊 My Stats", "ru": "📊 Моя статистика", "es": "📊 Mis estadísticas"},
    "btn_playlists": {"fa": "📃 پلی‌لیست‌های من", "en": "📃 My Playlists", "ru": "📃 Мои плейлисты", "es": "📃 Mis listas"},
    "btn_subs": {"fa": "🔔 اشتراک‌ها", "en": "🔔 Subscriptions", "ru": "🔔 Подписки", "es": "🔔 Suscripciones"},
    "btn_mixtape": {"fa": "🎁 میکس‌تیپ", "en": "🎁 Mixtape", "ru": "🎁 Микстейп", "es": "🎁 Mixtape"},
    "btn_ai": {"fa": "🧠 هوش مصنوعی", "en": "🧠 AI Assistant", "ru": "🧠 ИИ-помощник", "es": "🧠 Asistente IA"},
    "btn_help": {"fa": "❓ راهنما", "en": "❓ Help", "ru": "❓ Помощь", "es": "❓ Ayuda"},
    "btn_settings": {"fa": "⚙️ تنظیمات", "en": "⚙️ Settings", "ru": "⚙️ Настройки", "es": "⚙️ Ajustes"},
    "btn_contact": {"fa": "✉️ پیام به سازنده", "en": "✉️ Message the Creator", "ru": "✉️ Написать создателю", "es": "✉️ Mensaje al creador"},
    "btn_back": {"fa": "🔙 بازگشت", "en": "🔙 Back", "ru": "🔙 Назад", "es": "🔙 Volver"},
    "main_menu_btn": {"fa": "🔙 منوی اصلی", "en": "🔙 Main Menu", "ru": "🔙 Главное меню", "es": "🔙 Menú principal"},
    "inline_deny_banned": {"fa": "⛔️ دسترسی شما مسدود شده است", "en": "⛔️ Your access is blocked", "ru": "⛔️ Ваш доступ заблокирован", "es": "⛔️ Tu acceso está bloqueado"},
    "inline_deny_maintenance": {"fa": "🛠 ربات در حال به‌روزرسانی است", "en": "🛠 The bot is under maintenance", "ru": "🛠 Бот на техобслуживании", "es": "🛠 El bot está en mantenimiento"},
    "inline_deny_join": {"fa": "🔒 برای استفاده ابتدا در کانال عضو شوید", "en": "🔒 Join the channel first to use this", "ru": "🔒 Сначала подпишитесь на канал", "es": "🔒 Únete al canal primero para usar esto"},
    "inline_deny_force_join": {"fa": "🔒 ابتدا در کانال‌های اجباری عضو شوید.", "en": "🔒 Join the required channels first.", "ru": "🔒 Сначала подпишитесь на обязательные каналы.", "es": "🔒 Únete primero a los canales obligatorios."},
    "banned_msg": {
        "fa": "⛔️ دسترسی شما به این ربات مسدود شده است.", "en": "⛔️ Your access to this bot has been blocked.",
        "ru": "⛔️ Ваш доступ к этому боту заблокирован.", "es": "⛔️ Tu acceso a este bot ha sido bloqueado.",
    },
    "maintenance_msg": {
        "fa": "🛠 **ربات موقتاً در حال به‌روزرسانی است.**\n\nلطفاً کمی بعد دوباره تلاش کنید. از صبر شما ممنونیم 🙏",
        "en": "🛠 **The bot is temporarily under maintenance.**\n\nPlease try again shortly. Thanks for your patience 🙏",
        "ru": "🛠 **Бот временно на техобслуживании.**\n\nПопробуйте позже. Спасибо за терпение 🙏",
        "es": "🛠 **El bot está en mantenimiento temporal.**\n\nInténtalo de nuevo en un momento. Gracias por tu paciencia 🙏",
    },
    "force_join_msg": {
        "fa": "🔒 **برای استفاده از ربات، ابتدا در کانال‌های زیر عضو شوید:**",
        "en": "🔒 **To use the bot, first join the channels below:**",
        "ru": "🔒 **Чтобы использовать бота, сначала подпишитесь на каналы ниже:**",
        "es": "🔒 **Para usar el bot, primero únete a los canales de abajo:**",
    },
    "join_btn_prefix": {"fa": "📢 عضویت در", "en": "📢 Join", "ru": "📢 Подписаться на", "es": "📢 Unirse a"},
    "joined_btn": {"fa": "✅ عضو شدم", "en": "✅ I've Joined", "ru": "✅ Я подписался", "es": "✅ Ya me uní"},
    "join_still_missing": {
        "fa": "❌ هنوز در همه کانال‌ها عضو نشده‌اید.", "en": "❌ You haven't joined all the channels yet.",
        "ru": "❌ Вы ещё не подписались на все каналы.", "es": "❌ Todavía no te has unido a todos los canales.",
    },
    "join_confirmed": {
        "fa": "✅ عضویت تایید شد!\n\nحالا می‌توانید از ربات استفاده کنید.",
        "en": "✅ Membership confirmed!\n\nYou can now use the bot.",
        "ru": "✅ Подписка подтверждена!\n\nТеперь вы можете использовать бота.",
        "es": "✅ ¡Membresía confirmada!\n\nYa puedes usar el bot.",
    },
    "settings_text": {
        "fa": "⚙️ **تنظیمات**\n\nزبان ربات را انتخاب کنید:",
        "en": "⚙️ **Settings**\n\nChoose the bot's language:",
        "ru": "⚙️ **Настройки**\n\nВыберите язык бота:",
        "es": "⚙️ **Ajustes**\n\nElige el idioma del bot:",
    },
})


def get_main_keyboard(chat_id):
    return get_revamp_main_keyboard(chat_id, get_lang)


# Help pages: key -> (button label, body). Keeps every feature documented in
# one place so the menu and the docs cannot drift apart.
HELP_PAGES = {
    "dl": {
        "label": {"fa": "📥 دانلودر", "en": "📥 Downloader", "ru": "📥 Загрузчик", "es": "📥 Descargador"},
        "body": {
            "fa": "📥 **دانلودر چندرسانه‌ای**\n\nکافی است لینک را مستقیم در چت (یا گروه) بفرستید:\n\n🎬 **یوتیوب** — لینک ویدیو یا جستجو با نام\n▫️ انتخاب کیفیت: 1080p تا 240p\n▫️ خروجی MP4 یا فقط صدا (MP3)\n▫️ زیرنویس، کاور، کامنت‌ها و توضیحات\n▫️ ✂️ برش بازه دلخواه از ویدیو\n\n📸 **اینستاگرام** — پست، ریلز و آلبوم چندتایی\n📱 **تیک‌تاک** — ویدیو و اسلایدشو عکس\n\n_توضیحات پست جداگانه ارسال می‌شود تا فایل تمیز بماند._\n\n_دستور سریع:_ `/tahayoutube کلیدواژه` یا `/tahatiktok لینک`",
            "en": "📥 **Media Downloader**\n\nJust send a link directly in the chat (or group):\n\n🎬 **YouTube** — video link or search by name\n▫️ Quality picker: 1080p down to 240p\n▫️ MP4 output or audio-only (MP3)\n▫️ Cover, comments and description\n▫️ ✂️ Trim a custom range from the video\n\n📸 **Instagram** — posts, reels and multi-photo albums\n📱 **TikTok** — videos and photo slideshows\n\n_Post descriptions are sent separately so the media file stays clean._\n\n_Quick command:_ `/tahayoutube keyword` or `/tahatiktok link`",
            "ru": "📥 **Загрузчик медиа**\n\nПросто отправьте ссылку прямо в чат (или группу):\n\n🎬 **YouTube** — ссылка на видео или поиск по названию\n▫️ Выбор качества: от 1080p до 240p\n▫️ Вывод MP4 или только звук (MP3)\n▫️ Обложка, комментарии и описание\n▫️ ✂️ Обрезка нужного отрезка видео\n\n📸 **Instagram** — посты, рилсы и альбомы с несколькими фото\n📱 **TikTok** — видео и слайд-шоу из фото\n\n_Описание поста отправляется отдельным сообщением, чтобы файл оставался чистым._\n\n_Быстрая команда:_ `/tahayoutube ключевое_слово` или `/tahatiktok ссылка`",
            "es": "📥 **Descargador multimedia**\n\nSolo envía un enlace directamente en el chat (o grupo):\n\n🎬 **YouTube** — enlace de video o búsqueda por nombre\n▫️ Selector de calidad: de 1080p a 240p\n▫️ Salida en MP4 o solo audio (MP3)\n▫️ Portada, comentarios y descripción\n▫️ ✂️ Recorta un rango personalizado del video\n\n📸 **Instagram** — publicaciones, reels y álbumes de varias fotos\n📱 **TikTok** — videos y presentaciones de fotos\n\n_La descripción de la publicación se envía por separado para que el archivo quede limpio._\n\n_Comando rápido:_ `/tahayoutube palabra clave` o `/tahatiktok enlace`",
        },
    },
    "music": {
        "label": {"fa": "🎵 موزیک", "en": "🎵 Music", "ru": "🎵 Музыка", "es": "🎵 Música"},
        "body": {
            "fa": "🎵 **بخش موزیک**\n\n**دو موتور جستجو:**\n💽 **موزیک اورجینال (اسپاتیفای)** — جستجو در کاتالوگ رسمی اسپاتیفای برای دقیق‌ترین نام آهنگ/هنرمند، سپس دانلود نسخه اورجینال. لینک آهنگ، آلبوم یا پلی‌لیست اسپاتیفای هم مستقیم پشتیبانی می‌شود (آلبوم/پلی‌لیست به‌صورت دسته‌ای دانلود می‌شود)\n☁️ **ساندکلود** — موتور جستجوی دوم برای موزیک\n\n**امکانات جستجو:**\n▫️ نتایج با صفحه‌بندی\n▫️ مرتب‌سازی: 🔥 مرتبط‌ترین، 👁 پربازدید، ⏱ کوتاه‌ترین، 🆕 جدیدترین\n▫️ 🎲 انتخاب شانسی\n▫️ 🔎 پیدا کردن آهنگ از روی یک تکه از متن آن\n\n_آهنگ‌هایی که قبلاً دانلود شده‌اند فوری ارسال می‌شوند._\n\n_دستور سریع:_ `/tahamusic`، `/tahasoundcloud`، `/tahaspotify` یا `/tahalyrics` + نام آهنگ",
            "en": "🎵 **Music Section**\n\n**Two search engines:**\n💽 **Original Music (Spotify)** — searches Spotify's official catalog for the most accurate title/artist match, then downloads the original version. Spotify track/album/playlist links are also supported directly (album/playlist downloads as a batch)\n☁️ **SoundCloud** — second search engine for music\n\n**Search features:**\n▫️ Paginated results\n▫️ Sort by: 🔥 most relevant, 👁 most viewed, ⏱ shortest, 🆕 newest\n▫️ 🎲 random pick\n▫️ 🔎 find a song from a remembered line of lyrics\n\n_Songs already downloaded before are sent instantly._\n\n_Quick command:_ `/tahamusic`, `/tahasoundcloud`, `/tahaspotify` or `/tahalyrics` + song name",
            "ru": "🎵 **Раздел музыки**\n\n**Два поисковых движка:**\n💽 **Оригинальная музыка (Spotify)** — поиск в официальном каталоге Spotify для самого точного названия/исполнителя, затем загрузка оригинальной версии. Ссылки на трек/альбом/плейлист Spotify также поддерживаются напрямую (альбом/плейлист скачивается пакетом)\n☁️ **SoundCloud** — второй поисковый движок для музыки\n\n**Возможности поиска:**\n▫️ Результаты с пагинацией\n▫️ Сортировка: 🔥 самые релевантные, 👁 популярные, ⏱ короткие, 🆕 новые\n▫️ 🎲 случайный выбор\n▫️ 🔎 найти песню по запомнившейся строчке текста\n\n_Уже скачанные ранее песни отправляются мгновенно._\n\n_Быстрая команда:_ `/tahamusic`, `/tahasoundcloud`, `/tahaspotify` или `/tahalyrics` + название песни",
            "es": "🎵 **Sección de música**\n\n**Dos motores de búsqueda:**\n💽 **Música original (Spotify)** — busca en el catálogo oficial de Spotify para obtener el título/artista más preciso, luego descarga la versión original. Enlaces de canción/álbum/playlist de Spotify también son compatibles directamente (álbum/playlist se descarga en lote)\n☁️ **SoundCloud** — segundo motor de búsqueda para música\n\n**Funciones de búsqueda:**\n▫️ Resultados paginados\n▫️ Ordenar por: 🔥 más relevante, 👁 más vistos, ⏱ más cortos, 🆕 más nuevos\n▫️ 🎲 selección aleatoria\n▫️ 🔎 encontrar una canción a partir de un fragmento de la letra\n\n_Las canciones ya descargadas antes se envían al instante._\n\n_Comando rápido:_ `/tahamusic`, `/tahasoundcloud`, `/tahaspotify` o `/tahalyrics` + nombre de la canción",
        },
    },
    "track": {
        "label": {"fa": "🎛 ابزار آهنگ", "en": "🎛 Track Tools", "ru": "🎛 Инструменты трека", "es": "🎛 Herramientas de pista"},
        "body": {
            "fa": "🎛 **ابزارهای هر آهنگ**\n\nزیر هر آهنگ این دکمه‌ها را می‌بینید:\n\n🎤 **متن آهنگ** — دریافت متن هماهنگ با زمان\n📄 **فایل LRC** — فایل متن برای پخش‌کننده‌های موزیک\n📀 **آلبوم کامل** — دریافت تمام آهنگ‌های آلبوم با یک لمس\n🎙 **کارائوکه** — جداسازی صدای خواننده از موسیقی\n\n🎛 **افکت‌ها:**\n  🌙 اسلو + ریورب — نسخه آرام و عمیق\n  ⚡ نایتکور — سریع‌تر و تیزتر\n  🎧 هشت‌بعدی — چرخش صدا بین گوش‌ها\n  🔊 بیس بوست — تقویت باس\n\n_هر نسخه فقط یک‌بار ساخته می‌شود و بعد فوری تحویل داده می‌شود._",
            "en": "🎛 **Per-Track Tools**\n\nYou'll see these buttons under every song:\n\n🎤 **Lyrics** — get time-synced lyrics\n📄 **LRC file** — a lyrics file for music players\n📀 **Full album** — get every track of the album in one tap\n🎙 **Karaoke** — remove the vocals from the track\n\n🎛 **Effects:**\n  🌙 Slowed + reverb — a calm, deep version\n  ⚡ Nightcore — faster and sharper\n  🎧 8D — audio panned between your ears\n  🔊 Bass boost — punchier bass\n\n_Each version is only ever rendered once, then delivered instantly after that._",
            "ru": "🎛 **Инструменты трека**\n\nПод каждой песней вы увидите эти кнопки:\n\n🎤 **Текст песни** — текст с синхронизацией по времени\n📄 **Файл LRC** — файл текста для music-плееров\n📀 **Весь альбом** — все треки альбома одним нажатием\n🎙 **Караоке** — удаление вокала из трека\n\n🎛 **Эффекты:**\n  🌙 Slowed + reverb — спокойная, глубокая версия\n  ⚡ Nightcore — быстрее и резче\n  🎧 8D — звук вращается между ушами\n  🔊 Усиление баса — более мощный бас\n\n_Каждая версия рендерится лишь раз, а затем выдаётся мгновенно._",
            "es": "🎛 **Herramientas por pista**\n\nVerás estos botones debajo de cada canción:\n\n🎤 **Letra** — letra sincronizada con el tiempo\n📄 **Archivo LRC** — un archivo de letras para reproductores de música\n📀 **Álbum completo** — todas las pistas del álbum con un toque\n🎙 **Karaoke** — elimina la voz de la pista\n\n🎛 **Efectos:**\n  🌙 Slowed + reverb — versión tranquila y profunda\n  ⚡ Nightcore — más rápido y agudo\n  🎧 8D — audio que rota entre tus oídos\n  🔊 Refuerzo de bajos — bajo más potente\n\n_Cada versión se genera una sola vez y luego se entrega al instante._",
        },
    },
    "vedit": {
        "label": {"fa": "🎬 ویرایش ویدیو", "en": "🎬 Video Editor", "ru": "🎬 Видеоредактор", "es": "🎬 Editor de video"},
        "body": {
            "fa": "🎬 **ویرایشگر ویدیو**\n\nیک فایل ویدیویی مستقیم بفرستید تا این ابزارها زیرش نمایش داده شود:\n\n✂️ برش بازه دلخواه • 📝 واترمارک متنی (فارسی و انگلیسی)\n🔲📱🖥 برش مربعی / استوری / عریض\n🔄 چرخش ۹۰° و ۱۸۰° • ↔️↕️ آینه افقی و عمودی\n🐢⏩ تغییر سرعت (۰.۵x تا ۲x)\n🔇 حذف صدا • 🎵 استخراج صدا (MP3)\n🗜 فشرده‌سازی حجم • ⏮ معکوس‌سازی (حداکثر ۳۰ ثانیه)\n🖼 ساخت GIF • 🎧 شناسایی آهنگ داخل ویدیو\n\nهر نتیجه با همان ابزارها برمی‌گردد تا بتوانید چند ویرایش را پشت‌سرهم روی یک ویدیو انجام دهید.\n\n_دستور سریع:_ `/tahaedit` (در گروه‌ها اول این را بزنید، بعد ویدیو را بفرستید)",
            "en": "🎬 **Video Editor**\n\nSend a video file directly and these tools appear underneath it:\n\n✂️ Trim a custom range • 📝 Text watermark (Persian & English)\n🔲📱🖥 Square / story / wide crop\n🔄 Rotate 90° and 180° • ↔️↕️ Horizontal & vertical mirror\n🐢⏩ Change speed (0.5x to 2x)\n🔇 Mute audio • 🎵 Extract audio (MP3)\n🗜 Compress file size • ⏮ Reverse (up to 30 seconds)\n🖼 Make a GIF • 🎧 Identify the song playing in the video\n\nEvery result comes back with the same toolbar so you can chain several edits on one video.\n\n_Quick command:_ `/tahaedit` (in groups, use this first, then send the video)",
            "ru": "🎬 **Видеоредактор**\n\nОтправьте видеофайл, и под ним появятся эти инструменты:\n\n✂️ Обрезка нужного отрезка • 📝 Текстовый водяной знак (персидский и английский)\n🔲📱🖥 Обрезка квадрат / история / широкий\n🔄 Поворот на 90° и 180° • ↔️↕️ Зеркало по горизонтали и вертикали\n🐢⏩ Изменение скорости (0.5x–2x)\n🔇 Отключить звук • 🎵 Извлечь звук (MP3)\n🗜 Сжатие размера файла • ⏮ Реверс (до 30 секунд)\n🖼 Создать GIF • 🎧 Распознать песню в видео\n\nКаждый результат возвращается с той же панелью инструментов, чтобы можно было применить несколько правок подряд к одному видео.\n\n_Быстрая команда:_ `/tahaedit` (в группах сначала используйте эту команду, затем отправьте видео)",
            "es": "🎬 **Editor de video**\n\nEnvía un archivo de video directamente y estas herramientas aparecerán debajo:\n\n✂️ Recorta un rango personalizado • 📝 Marca de agua de texto (persa e inglés)\n🔲📱🖥 Recorte cuadrado / historia / ancho\n🔄 Girar 90° y 180° • ↔️↕️ Espejo horizontal y vertical\n🐢⏩ Cambiar velocidad (0.5x a 2x)\n🔇 Silenciar audio • 🎵 Extraer audio (MP3)\n🗜 Comprimir tamaño de archivo • ⏮ Invertir (hasta 30 segundos)\n🖼 Crear GIF • 🎧 Identificar la canción del video\n\nCada resultado vuelve con la misma barra de herramientas para que puedas encadenar varias ediciones en un mismo video.\n\n_Comando rápido:_ `/tahaedit` (en grupos, usa esto primero y luego envía el video)",
        },
    },
    "pinterest": {
        "label": {"fa": "📌 پینترست", "en": "📌 Pinterest", "ru": "📌 Pinterest", "es": "📌 Pinterest"},
        "body": {
            "fa": "📌 **بخش پینترست**\n\nلینک یک پین، بورد یا پروفایل کاربر پینترست را مستقیم بفرستید:\n\n📌 **پین** — دانلود تک عکس یا ویدیو با کیفیت کامل\n📋 **بورد** — دانلود دسته‌ای پین‌های یک بورد\n👤 **پروفایل** — دانلود دسته‌ای پین‌های اخیر یک کاربر\n🔎 **جستجو** — جستجوی کلیدواژه‌ای، با دکمه دانلود مستقیم زیر هر تصویر\n\n_دستور سریع:_ `/tahapinterest کلیدواژه`",
            "en": "📌 **Pinterest Section**\n\nSend a Pinterest pin, board, or user profile link directly:\n\n📌 **Pin** — download a single photo or video in full quality\n📋 **Board** — batch-download a board's pins\n👤 **Profile** — batch-download a user's recent pins\n🔎 **Search** — keyword search, with a download button right under every image\n\n_Quick command:_ `/tahapinterest keyword`",
            "ru": "📌 **Раздел Pinterest**\n\nОтправьте ссылку на пин, борд или профиль пользователя Pinterest напрямую:\n\n📌 **Пин** — скачать одно фото или видео в полном качестве\n📋 **Борд** — пакетная загрузка пинов борда\n👤 **Профиль** — пакетная загрузка последних пинов пользователя\n🔎 **Поиск** — поиск по ключевому слову, с кнопкой загрузки под каждым изображением\n\n_Быстрая команда:_ `/tahapinterest ключевое_слово`",
            "es": "📌 **Sección de Pinterest**\n\nEnvía un enlace de pin, tablero o perfil de usuario de Pinterest directamente:\n\n📌 **Pin** — descarga una sola foto o video en calidad completa\n📋 **Tablero** — descarga en lote los pines de un tablero\n👤 **Perfil** — descarga en lote los pines recientes de un usuario\n🔎 **Buscar** — búsqueda por palabra clave, con botón de descarga bajo cada imagen\n\n_Comando rápido:_ `/tahapinterest palabra clave`",
        },
    },
    "reddit": {
        "label": {"fa": "🔴 ردیت", "en": "🔴 Reddit", "ru": "🔴 Reddit", "es": "🔴 Reddit"},
        "body": {
            "fa": "🔴 **بخش ردیت**\n\nلینک یک پست یا ساب‌ردیت (reddit.com/r/...) را مستقیم بفرستید:\n\n📝 **پست** — دانلود عکس، گالری چندتایی یا ویدیو (صدا و تصویر ویدیو خودکار با هم ترکیب می‌شود)\n📋 **ساب‌ردیت** — دانلود دسته‌ای پست‌های برتر\n🔎 **جستجو** — جستجوی کلیدواژه‌ای در کل ردیت، با دکمه دانلود زیر هر نتیجه\n\n_دستور سریع:_ `/tahareddit کلیدواژه`",
            "en": "🔴 **Reddit Section**\n\nSend a post or subreddit link (reddit.com/r/...) directly:\n\n📝 **Post** — download a photo, multi-photo gallery, or video (video/audio tracks are merged automatically)\n📋 **Subreddit** — batch-download the top posts\n🔎 **Search** — keyword search across all of Reddit, with a download button under every result\n\n_Quick command:_ `/tahareddit keyword`",
            "ru": "🔴 **Раздел Reddit**\n\nОтправьте ссылку на пост или сабреддит (reddit.com/r/...) напрямую:\n\n📝 **Пост** — скачать фото, галерею из нескольких фото или видео (видео и аудио дорожки объединяются автоматически)\n📋 **Сабреддит** — пакетная загрузка топовых постов\n🔎 **Поиск** — поиск по ключевому слову по всему Reddit, с кнопкой загрузки под каждым результатом\n\n_Быстрая команда:_ `/tahareddit ключевое_слово`",
            "es": "🔴 **Sección de Reddit**\n\nEnvía un enlace de publicación o subreddit (reddit.com/r/...) directamente:\n\n📝 **Publicación** — descarga una foto, galería de varias fotos o video (las pistas de video/audio se combinan automáticamente)\n📋 **Subreddit** — descarga en lote las publicaciones destacadas\n🔎 **Buscar** — búsqueda por palabra clave en todo Reddit, con botón de descarga bajo cada resultado\n\n_Comando rápido:_ `/tahareddit palabra clave`",
        },
    },
    "photo": {
        "label": {"fa": "🖼 جستجوی تصویر", "en": "🖼 Image Search", "ru": "🖼 Поиск изображений", "es": "🖼 Buscar imágenes"},
        "body": {
            "fa": "🖼 **جستجوی تصویر**\n\nکلیدواژه‌ای بفرستید تا عکس‌های باکیفیت از آرشیو Pexels نمایش داده شود؛ زیر هر عکس دکمه «⬇️ کیفیت کامل» برای دریافت نسخه اصلی (بدون فشرده‌سازی تلگرام) هست.\n\n_نکته: کلیدواژه انگلیسی نتیجه بهتری می‌دهد._\n\n_دستور سریع:_ `/tahaphoto کلیدواژه`",
            "en": "🖼 **Image Search**\n\nSend a keyword to browse high-quality photos from the Pexels archive; every photo has a \"⬇️ Full quality\" button underneath to get the original (no Telegram compression).\n\n_Tip: an English keyword gives better results._\n\n_Quick command:_ `/tahaphoto keyword`",
            "ru": "🖼 **Поиск изображений**\n\nОтправьте ключевое слово, чтобы просмотреть качественные фото из архива Pexels; под каждым фото есть кнопка «⬇️ Полное качество» для получения оригинала (без сжатия Telegram).\n\n_Совет: ключевое слово на английском даёт лучшие результаты._\n\n_Быстрая команда:_ `/tahaphoto ключевое_слово`",
            "es": "🖼 **Búsqueda de imágenes**\n\nEnvía una palabra clave para ver fotos de alta calidad del archivo de Pexels; cada foto tiene un botón «⬇️ Calidad completa» debajo para obtener el original (sin compresión de Telegram).\n\n_Consejo: una palabra clave en inglés da mejores resultados._\n\n_Comando rápido:_ `/tahaphoto palabra clave`",
        },
    },
    "shazam": {
        "label": {"fa": "🎧 شناسایی آهنگ", "en": "🎧 Song Recognition", "ru": "🎧 Распознавание песни", "es": "🎧 Reconocer canción"},
        "body": {
            "fa": "🎧 **شناسایی آهنگ**\n\nیک ویس یا فایل صوتی از آهنگ مورد نظر بفرستید؛ نام و خواننده‌اش فوری شناسایی می‌شود و دکمه‌ای برای دانلود نسخه کامل نمایش داده می‌شود.\n\n🎬 برای شناسایی موزیک داخل یک **ویدیو**: ویدیو را در ویرایشگر ویدیو بفرستید و دکمه «🎧 شناسایی آهنگ» را بزنید.\n\n_دستور سریع:_ `/tahashazam` (در گروه‌ها اول این را بزنید، بعد فایل صوتی را بفرستید)",
            "en": "🎧 **Song Recognition**\n\nSend a voice note or audio file of the song you're after; its title and artist are recognized instantly, with a button to download the full track.\n\n🎬 To identify music inside a **video**: send the video into the video editor and tap the \"🎧 Identify song\" button.\n\n_Quick command:_ `/tahashazam` (in groups, use this first, then send the audio file)",
            "ru": "🎧 **Распознавание песни**\n\nОтправьте голосовое сообщение или аудиофайл нужной песни; название и исполнитель определяются мгновенно, с кнопкой для загрузки полного трека.\n\n🎬 Чтобы распознать музыку внутри **видео**: отправьте видео в видеоредактор и нажмите кнопку «🎧 Распознать песню».\n\n_Быстрая команда:_ `/tahashazam` (в группах сначала используйте эту команду, затем отправьте аудиофайл)",
            "es": "🎧 **Reconocimiento de canciones**\n\nEnvía una nota de voz o archivo de audio de la canción que buscas; su título y artista se reconocen al instante, con un botón para descargar la pista completa.\n\n🎬 Para identificar música dentro de un **video**: envía el video al editor de video y toca el botón «🎧 Identificar canción».\n\n_Comando rápido:_ `/tahashazam` (en grupos, usa esto primero y luego envía el archivo de audio)",
        },
    },
    "ai": {
        "label": {"fa": "🧠 هوش مصنوعی", "en": "🧠 AI Assistant", "ru": "🧠 ИИ-помощник", "es": "🧠 Asistente IA"},
        "body": {
            "fa": "🧠 **دستیار هوشمند**\n\nبا زبان طبیعی صحبت کنید، نه فقط دستور:\n\n🎵 پیشنهاد و دانلود موزیک بر اساس حس یا موقعیت («آهنگ برای باشگاه»)\n📺 جستجوی یوتیوب با توصیف موضوع\n🎤 دریافت متن آهنگ با گفتن اسم و خواننده\n📊 مرور تاریخچه دانلودهای خودتان\n\n_دستور سریع:_ `/tahaai سوال شما`\n_در گروه‌ها هر بار یک سوال/جواب است؛ در چت خصوصی مکالمه پیوسته باز می‌شود._",
            "en": "🧠 **AI Assistant**\n\nTalk in plain language, not just commands:\n\n🎵 Get music suggestions and downloads based on a mood or moment (\"a song for the gym\")\n📺 Search YouTube by describing a topic\n🎤 Get lyrics just by naming the song and artist\n📊 Review your own download history\n\n_Quick command:_ `/tahaai your question`\n_In groups it's one question/answer at a time; in DMs it opens an ongoing conversation._",
            "ru": "🧠 **ИИ-помощник**\n\nОбщайтесь обычным языком, а не только командами:\n\n🎵 Получайте музыкальные рекомендации и загрузки по настроению или ситуации («песня для тренажёрного зала»)\n📺 Ищите на YouTube, описав тему\n🎤 Получайте текст песни, просто назвав исполнителя и трек\n📊 Просматривайте свою историю загрузок\n\n_Быстрая команда:_ `/tahaai ваш вопрос`\n_В группах — один вопрос/ответ за раз; в личных сообщениях открывается непрерывный диалог._",
            "es": "🧠 **Asistente de IA**\n\nHabla en lenguaje natural, no solo comandos:\n\n🎵 Recibe sugerencias y descargas de música según tu estado de ánimo o momento («una canción para el gimnasio»)\n📺 Busca en YouTube describiendo un tema\n🎤 Obtén letras solo con nombrar la canción y el artista\n📊 Revisa tu propio historial de descargas\n\n_Comando rápido:_ `/tahaai tu pregunta`\n_En grupos es una pregunta/respuesta a la vez; en mensajes privados abre una conversación continua._",
        },
    },
    "lib": {
        "label": {"fa": "📚 کتابخانه", "en": "📚 Library", "ru": "📚 Библиотека", "es": "📚 Biblioteca"},
        "body": {
            "fa": "📚 **کتابخانه و آمار**\n\n📚 **کتابخانه من** — همه آهنگ‌هایی که گرفته‌اید\n▫️ مرتب‌سازی: 🕒 اخیر، 🔁 پرتکرار، 🔤 الفبا\n▫️ ارسال مجدد فوری بدون دانلود دوباره\n\n📊 **آمار من** — هنرمندان برتر، پرتکرارترین آهنگ‌ها و مجموع دانلودها\n\n🎁 **میکس‌تیپ** — مجموعه‌ای بر اساس سلیقه شما، ساخته‌شده از آهنگ‌های موجود (بنابراین فوری ارسال می‌شود)\n\n_دستور سریع:_ `/tahalibrary` و `/tahastats`",
            "en": "📚 **Library & Stats**\n\n📚 **My library** — every song you've ever downloaded\n▫️ Sort by: 🕒 recent, 🔁 most played, 🔤 alphabetical\n▫️ Instant resend, no re-downloading\n\n📊 **My stats** — top artists, most-replayed songs and total downloads\n\n🎁 **Mixtape** — a set built from your taste, made from songs already in the archive (so it sends instantly)\n\n_Quick command:_ `/tahalibrary` and `/tahastats`",
            "ru": "📚 **Библиотека и статистика**\n\n📚 **Моя библиотека** — все скачанные вами песни\n▫️ Сортировка: 🕒 недавние, 🔁 самые проигрываемые, 🔤 по алфавиту\n▫️ Мгновенная повторная отправка без повторной загрузки\n\n📊 **Моя статистика** — топ артистов, самые переигрываемые песни и общее число загрузок\n\n🎁 **Микстейп** — подборка по вашему вкусу из уже имеющихся в архиве песен (поэтому отправляется мгновенно)\n\n_Быстрая команда:_ `/tahalibrary` и `/tahastats`",
            "es": "📚 **Biblioteca y estadísticas**\n\n📚 **Mi biblioteca** — todas las canciones que has descargado\n▫️ Ordenar por: 🕒 recientes, 🔁 más reproducidas, 🔤 alfabético\n▫️ Reenvío instantáneo, sin volver a descargar\n\n📊 **Mis estadísticas** — artistas principales, canciones más repetidas y total de descargas\n\n🎁 **Mixtape** — una selección según tu gusto, hecha de canciones ya en el archivo (por eso se envía al instante)\n\n_Comando rápido:_ `/tahalibrary` y `/tahastats`",
        },
    },
    "playlists": {
        "label": {"fa": "📃 پلی‌لیست‌ها", "en": "📃 Playlists", "ru": "📃 Плейлисты", "es": "📃 Listas"},
        "body": {
            "fa": "📃 **پلی‌لیست‌های شما**\n\nهر آهنگی که ربات برایتان می‌فرستد یک دکمه «➕ افزودن به پلی‌لیست» دارد؛ با آن می‌توانید آهنگ را به یکی از پلی‌لیست‌های خودتان اضافه کنید یا یکی جدید بسازید.\n\nاز منوی اصلی وارد «📃 پلی‌لیست‌های من» شوید تا:\n▫️ آهنگ‌های هر پلی‌لیست را ببینید و مدیریت کنید\n▫️ نامش را عوض کنید یا کاملاً حذفش کنید\n▫️ هر آهنگ را با شماره‌اش باز کنید (متن آهنگ، افکت، آلبوم کامل، کارائوکه)\n\n🤖 **دکمه آهنگ‌های مشابه:** با یک لمس، هوش مصنوعی کل پلی‌لیست را تحلیل می‌کند و ۱۰ آهنگ مشابه واقعی پیشنهاد می‌دهد که می‌توانید همه را یکجا دانلود کنید.\n\n_حداکثر {max_playlists} پلی‌لیست، هرکدام تا {max_tracks} آهنگ._",
            "en": "📃 **Your Playlists**\n\nEvery song the bot sends you has an \"➕ Add to Playlist\" button; use it to add the song to one of your playlists or create a new one on the spot.\n\nOpen \"📃 My Playlists\" from the main menu to:\n▫️ View and manage each playlist's songs\n▫️ Rename it or delete it entirely\n▫️ Open any song by its number (lyrics, effects, full album, karaoke)\n\n🤖 **Similar Songs button:** with one tap, the AI analyzes the whole playlist and suggests 10 real similar songs you can download all at once.\n\n_Max {max_playlists} playlists, up to {max_tracks} songs each._",
            "ru": "📃 **Ваши плейлисты**\n\nУ каждой песни, которую отправляет бот, есть кнопка «➕ Добавить в плейлист»; с её помощью можно добавить песню в один из ваших плейлистов или сразу создать новый.\n\nОткройте «📃 Мои плейлисты» в главном меню, чтобы:\n▫️ Просматривать и управлять песнями каждого плейлиста\n▫️ Переименовать его или полностью удалить\n▫️ Открывать любую песню по номеру (текст, эффекты, весь альбом, караоке)\n\n🤖 **Кнопка похожих песен:** одним касанием ИИ анализирует весь плейлист и предлагает 10 реальных похожих песен, которые можно скачать все сразу.\n\n_Максимум {max_playlists} плейлистов, до {max_tracks} песен в каждом._",
            "es": "📃 **Tus listas de reproducción**\n\nCada canción que te envía el bot tiene un botón «➕ Añadir a la lista»;úsalo para añadir la canción a una de tus listas o crear una nueva al instante.\n\nAbre «📃 Mis listas» en el menú principal para:\n▫️ Ver y gestionar las canciones de cada lista\n▫️ Renombrarla o eliminarla por completo\n▫️ Abrir cualquier canción por su número (letra, efectos, álbum completo, karaoke)\n\n🤖 **Botón de canciones similares:** con un toque, la IA analiza toda la lista y sugiere 10 canciones reales similares que puedes descargar todas a la vez.\n\n_Máximo {max_playlists} listas, hasta {max_tracks} canciones cada una._",
        },
    },
    "subs": {
        "label": {"fa": "🔔 اشتراک‌ها", "en": "🔔 Subscriptions", "ru": "🔔 Подписки", "es": "🔔 Suscripciones"},
        "body": {
            "fa": "🔔 **اشتراک کانال‌ها**\n\nکانال یوتیوب، پیج اینستاگرام یا تیک‌تاک را دنبال کنید تا پست‌های جدیدشان خودکار برایتان بیاید.\n\n**نحوه افزودن:** لینک یا آیدی پیج را در بخش اشتراک‌ها بفرستید\n\n📸 اینستاگرام و 📱 تیک‌تاک: مستقیم ارسال می‌شود\n🎬 یوتیوب: اطلاع‌رسانی با دکمه دانلود (چون حجم بالاست)\n\n_حداکثر {max_subs} اشتراک برای هر کاربر._",
            "en": "🔔 **Channel Subscriptions**\n\nFollow a YouTube channel, Instagram page or TikTok account to get their new posts automatically.\n\n**How to add:** send the link or page ID in the subscriptions section\n\n📸 Instagram and 📱 TikTok: sent directly\n🎬 YouTube: a notification with a download button (since it's larger)\n\n_Max {max_subs} subscriptions per user._",
            "ru": "🔔 **Подписки на каналы**\n\nПодпишитесь на канал YouTube, страницу Instagram или аккаунт TikTok, чтобы получать их новые публикации автоматически.\n\n**Как добавить:** отправьте ссылку или ID страницы в разделе подписок\n\n📸 Instagram и 📱 TikTok: отправляется напрямую\n🎬 YouTube: уведомление с кнопкой загрузки (так как файл больше)\n\n_Максимум {max_subs} подписок на пользователя._",
            "es": "🔔 **Suscripciones a canales**\n\nSigue un canal de YouTube, página de Instagram o cuenta de TikTok para recibir sus nuevas publicaciones automáticamente.\n\n**Cómo agregar:** envía el enlace o ID de la página en la sección de suscripciones\n\n📸 Instagram y 📱 TikTok: se envía directamente\n🎬 YouTube: una notificación con botón de descarga (ya que es más pesado)\n\n_Máximo {max_subs} suscripciones por usuario._",
        },
    },
    "inline": {
        "label": {"fa": "⚡️ حالت اینلاین", "en": "⚡️ Inline Mode", "ru": "⚡️ Инлайн-режим", "es": "⚡️ Modo en línea"},
        "body": {
            "fa": "⚡️ **استفاده در هر چتی**\n\nلازم نیست وارد ربات شوید! در هر چت تایپ کنید:\n\n`@{bot_username} نام آهنگ`\n\nو آهنگ را مستقیم همان‌جا بفرستید.\n\n⚡️ این روش **فوری** است چون از آرشیو آماده استفاده می‌کند.\nهرچه افراد بیشتری از ربات استفاده کنند، آرشیو بزرگ‌تر و ربات سریع‌تر می‌شود.",
            "en": "⚡️ **Use It In Any Chat**\n\nNo need to open the bot! In any chat, type:\n\n`@{bot_username} song name`\n\nand send the song right there.\n\n⚡️ This is **instant** because it pulls from the ready-made archive.\nThe more people use the bot, the bigger the archive gets, and the faster it becomes.",
            "ru": "⚡️ **Использование в любом чате**\n\nНе нужно открывать бота! В любом чате наберите:\n\n`@{bot_username} название песни`\n\nи отправьте песню прямо там.\n\n⚡️ Это происходит **мгновенно**, так как берётся из готового архива.\nЧем больше людей пользуются ботом, тем больше архив и тем быстрее бот.",
            "es": "⚡️ **Úsalo en cualquier chat**\n\n¡No necesitas abrir el bot! En cualquier chat, escribe:\n\n`@{bot_username} nombre de la canción`\n\ny envía la canción justo ahí.\n\n⚡️ Esto es **instantáneo** porque usa el archivo ya preparado.\nCuantas más personas usen el bot, más grande será el archivo y más rápido será.",
        },
    },
    "cmds": {
        "label": {"fa": "⌨️ همه دستورها", "en": "⌨️ All Commands", "ru": "⌨️ Все команды", "es": "⌨️ Todos los comandos"},
        "body": {
            "fa": "⌨️ **فهرست کامل دستورها**\n\nهمه این دستورها هم در چت خصوصی و هم در گروه کار می‌کنند. بیشترشان با یک کلیدواژه بعد از دستور، بلافاصله نتیجه را نشان می‌دهند؛ بدون کلیدواژه، یک راهنمای کوتاه می‌آید:\n\n`/tahamenu` — منوی اصلی\n`/tahayoutube کلیدواژه` — جستجوی یوتیوب\n`/tahatiktok لینک یا آیدی` — دانلود از تیک‌تاک\n`/tahamusic نام آهنگ` — موزیک اورجینال\n`/tahasoundcloud نام آهنگ` — ساندکلود\n`/tahaspotify نام آهنگ` — اسپاتیفای\n`/tahalyrics تکه‌ای از متن` — پیدا کردن آهنگ از روی متن\n`/tahaedit` — ویرایشگر ویدیو (بعد ویدیو بفرستید)\n`/tahapinterest کلیدواژه` — جستجوی پینترست\n`/tahareddit کلیدواژه` — جستجوی ردیت\n`/tahaphoto کلیدواژه` — جستجوی تصویر\n`/tahashazam` — شناسایی آهنگ (بعد ویس/فایل صوتی بفرستید)\n`/tahalibrary` — کتابخانه من\n`/tahastats` — آمار من\n`/tahaai سوال` — دستیار هوش مصنوعی\n`/tahahelp` — همین راهنما\n\n_در گروه‌ها، لینک‌های یوتیوب/اینستاگرام/تیک‌تاک/ساندکلود/اسپاتیفای/پینترست/ردیت هم به‌صورت خودکار دانلود می‌شوند._",
            "en": "⌨️ **Full Command List**\n\nAll of these work in both DMs and groups. Most show a result instantly if you add a keyword after the command; without one, a short prompt appears:\n\n`/tahamenu` — main menu\n`/tahayoutube keyword` — YouTube search\n`/tahatiktok link or id` — download from TikTok\n`/tahamusic song name` — original music\n`/tahasoundcloud song name` — SoundCloud\n`/tahaspotify song name` — Spotify\n`/tahalyrics a bit of the lyrics` — find a song from its lyrics\n`/tahaedit` — video editor (then send a video)\n`/tahapinterest keyword` — Pinterest search\n`/tahareddit keyword` — Reddit search\n`/tahaphoto keyword` — image search\n`/tahashazam` — song recognition (then send a voice/audio file)\n`/tahalibrary` — my library\n`/tahastats` — my stats\n`/tahaai question` — AI assistant\n`/tahahelp` — this help\n\n_In groups, YouTube/Instagram/TikTok/SoundCloud/Spotify/Pinterest/Reddit links also auto-download._",
            "ru": "⌨️ **Полный список команд**\n\nВсе они работают как в личных сообщениях, так и в группах. Большинство сразу показывают результат, если добавить ключевое слово после команды; без него появляется короткая подсказка:\n\n`/tahamenu` — главное меню\n`/tahayoutube ключевое_слово` — поиск на YouTube\n`/tahatiktok ссылка_или_id` — загрузка с TikTok\n`/tahamusic название_песни` — оригинальная музыка\n`/tahasoundcloud название_песни` — SoundCloud\n`/tahaspotify название_песни` — Spotify\n`/tahalyrics часть_текста` — найти песню по тексту\n`/tahaedit` — видеоредактор (затем отправьте видео)\n`/tahapinterest ключевое_слово` — поиск в Pinterest\n`/tahareddit ключевое_слово` — поиск в Reddit\n`/tahaphoto ключевое_слово` — поиск изображений\n`/tahashazam` — распознавание песни (затем отправьте голосовое/аудио)\n`/tahalibrary` — моя библиотека\n`/tahastats` — моя статистика\n`/tahaai вопрос` — ИИ-помощник\n`/tahahelp` — эта справка\n\n_В группах ссылки YouTube/Instagram/TikTok/SoundCloud/Spotify/Pinterest/Reddit также скачиваются автоматически._",
            "es": "⌨️ **Lista completa de comandos**\n\nTodos funcionan tanto en mensajes privados como en grupos. La mayoría muestra un resultado al instante si añades una palabra clave después del comando; sin ella, aparece una breve indicación:\n\n`/tahamenu` — menú principal\n`/tahayoutube palabra clave` — búsqueda en YouTube\n`/tahatiktok enlace o id` — descargar de TikTok\n`/tahamusic nombre de canción` — música original\n`/tahasoundcloud nombre de canción` — SoundCloud\n`/tahaspotify nombre de canción` — Spotify\n`/tahalyrics parte de la letra` — encontrar canción por su letra\n`/tahaedit` — editor de video (luego envía un video)\n`/tahapinterest palabra clave` — búsqueda en Pinterest\n`/tahareddit palabra clave` — búsqueda en Reddit\n`/tahaphoto palabra clave` — búsqueda de imágenes\n`/tahashazam` — reconocimiento de canciones (luego envía audio/voz)\n`/tahalibrary` — mi biblioteca\n`/tahastats` — mis estadísticas\n`/tahaai pregunta` — asistente de IA\n`/tahahelp` — esta ayuda\n\n_En grupos, los enlaces de YouTube/Instagram/TikTok/SoundCloud/Spotify/Pinterest/Reddit también se descargan automáticamente._",
        },
    },
}


STRINGS.update({
    "help_home_text": {
        "fa": "❓ **راهنمای کامل ربات**\n\nاین ربات یک جعبه‌ابزار کامل برای دانلود، موزیک، ویرایش ویدیو و هوش مصنوعی است.\nبرای دیدن توضیح کامل هر بخش، روی دکمه‌اش بزنید:\n\n📥 **دانلودر** — یوتیوب، اینستاگرام، تیک‌تاک\n🎵 **موزیک** — اورجینال، ساندکلود و اسپاتیفای\n🎛 **ابزار آهنگ** — افکت، متن، آلبوم، کارائوکه\n🎬 **ویرایش ویدیو** — برش، واترمارک، سرعت، GIF و بیشتر\n📌 **پینترست** — پین، بورد، پروفایل و جستجو\n🔴 **ردیت** — پست، ساب‌ردیت و جستجو\n🖼 **جستجوی تصویر** — آرشیو باکیفیت Pexels\n🎧 **شناسایی آهنگ** — با فرستادن صدا یا ویدیو\n🧠 **هوش مصنوعی** — دستیار شخصی با زبان طبیعی\n📚 **کتابخانه** — تاریخچه، آمار و میکس‌تیپ\n📃 **پلی‌لیست‌ها** — بسازید، مدیریت کنید و آهنگ مشابه با هوش مصنوعی بگیرید\n🔔 **اشتراک‌ها** — دریافت خودکار پست‌های جدید\n⚡️ **حالت اینلاین** — استفاده در هر چتی\n⌨️ **همه دستورها** — فهرست کامل دستورهای گروه و خصوصی\n\n💡 _نکته: آهنگ‌هایی که قبلاً دانلود شده‌اند فوری ارسال می‌شوند._",
        "en": "❓ **Full Bot Guide**\n\nThis bot is a complete toolkit for downloads, music, video editing and AI.\nTap any button to see the full explanation of that section:\n\n📥 **Downloader** — YouTube, Instagram, TikTok\n🎵 **Music** — Original, SoundCloud and Spotify\n🎛 **Track Tools** — effects, lyrics, albums, karaoke\n🎬 **Video Editor** — trim, watermark, speed, GIF and more\n📌 **Pinterest** — pin, board, profile and search\n🔴 **Reddit** — post, subreddit and search\n🖼 **Image Search** — the high-quality Pexels archive\n🧠 **AI Assistant** — a personal assistant in plain language\n📚 **Library** — history, stats and mixtape\n📃 **Playlists** — create, manage, and get AI-suggested similar songs\n🔔 **Subscriptions** — automatic new-post notifications\n⚡️ **Inline Mode** — usable in any chat\n⌨️ **All Commands** — the full list of group/DM commands\n\n💡 _Tip: songs already downloaded before are sent instantly._",
        "ru": "❓ **Полное руководство по боту**\n\nЭтот бот — полный набор инструментов для загрузок, музыки, редактирования видео и ИИ.\nНажмите любую кнопку, чтобы увидеть подробное описание раздела:\n\n📥 **Загрузчик** — YouTube, Instagram, TikTok\n🎵 **Музыка** — Оригинал, SoundCloud и Spotify\n🎛 **Инструменты трека** — эффекты, текст, альбомы, караоке\n🎬 **Видеоредактор** — обрезка, водяной знак, скорость, GIF и многое другое\n📌 **Pinterest** — пин, борд, профиль и поиск\n🔴 **Reddit** — пост, сабреддит и поиск\n🖼 **Поиск изображений** — качественный архив Pexels\n🧠 **ИИ-помощник** — персональный помощник на обычном языке\n📚 **Библиотека** — история, статистика и микстейп\n📃 **Плейлисты** — создавайте, управляйте и получайте похожие песни от ИИ\n🔔 **Подписки** — автоматические уведомления о новых постах\n⚡️ **Инлайн-режим** — можно использовать в любом чате\n⌨️ **Все команды** — полный список команд для групп и личных чатов\n\n💡 _Совет: уже скачанные ранее песни отправляются мгновенно._",
        "es": "❓ **Guía completa del bot**\n\nEste bot es un kit de herramientas completo para descargas, música, edición de video e IA.\nToca cualquier botón para ver la explicación completa de esa sección:\n\n📥 **Descargador** — YouTube, Instagram, TikTok\n🎵 **Música** — Original, SoundCloud y Spotify\n🎛 **Herramientas de pista** — efectos, letras, álbumes, karaoke\n🎬 **Editor de video** — recorte, marca de agua, velocidad, GIF y más\n📌 **Pinterest** — pin, tablero, perfil y búsqueda\n🔴 **Reddit** — publicación, subreddit y búsqueda\n🖼 **Búsqueda de imágenes** — el archivo de alta calidad de Pexels\n🧠 **Asistente de IA** — un asistente personal en lenguaje natural\n📚 **Biblioteca** — historial, estadísticas y mixtape\n📃 **Listas de reproducción** — crea, gestiona y obtén canciones similares con IA\n🔔 **Suscripciones** — notificaciones automáticas de publicaciones nuevas\n⚡️ **Modo en línea** — usable en cualquier chat\n⌨️ **Todos los comandos** — la lista completa de comandos de grupo/privado\n\n💡 _Consejo: las canciones ya descargadas antes se envían al instante._",
    },
})


HELP_PAGES["music"]["body"] = {
    "fa": "🎵 **بخش موزیک**\n\nاز منوی اصلی یکی از سه پلتفرم را انتخاب کنید:\n🟢 **اسپاتیفای** — جستجوی آهنگ، آلبوم، خواننده، پلی‌لیست و پرطرفدارترین‌های هفته\n🔴 **یوتیوب موزیک** — آهنگ، آلبوم، خواننده و پلی‌لیست از خود یوتیوب موزیک\n☁️ **ساندکلاد** — ریمیکس‌ها و قطعات مستقل\n\n**نحوه کار:** با زدن دکمه جستجو، منوی اینلاین با کاور، نام و مدت هر آهنگ باز می‌شود. آهنگ آماده فوری ارسال می‌شود؛ در غیر این صورت پیام «⏳ در حال دانلود» نمایش داده شده و بلافاصله با آهنگ کامل جایگزین می‌شود. داخل خود ربات، آهنگ همراه با همه دکمه‌ها (متن، افکت، پلی‌لیست و...) ارسال می‌شود.\n\nهر فایل MP3 با نام «خواننده - آهنگ»، کاور اصلی و تگ‌های کامل (آلبوم، سال، شماره ترک) است.\n\n🔗 لینک اسپاتیفای، یوتیوب موزیک، ساندکلاد و دیزر هم مستقیم پشتیبانی می‌شود.\n\n_دستور سریع:_ `/tahamusic`، `/tahaspotify`، `/tahasoundcloud`، `/tahalyrics` + نام آهنگ",
    "en": "🎵 **Music Section**\n\nPick one of three platforms from the main menu:\n🟢 **Spotify** — search tracks, albums, artists, playlists and the weekly top charts\n🔴 **YouTube Music** — tracks, albums, artists and playlists from YouTube Music itself\n☁️ **SoundCloud** — remixes and independent tracks\n\n**How it works:** the search button opens the inline menu with each song's cover, name and duration. Ready songs are sent instantly; otherwise a \"⏳ downloading\" message appears and is swapped for the full song. Inside the bot itself the song arrives with all its buttons (lyrics, effects, playlist...).\n\nEvery file is an MP3 named \"Artist - Title\" with the original cover and full tags (album, year, track number).\n\n🔗 Spotify, YouTube Music, SoundCloud and Deezer links work directly too.\n\n_Quick command:_ `/tahamusic`, `/tahaspotify`, `/tahasoundcloud`, `/tahalyrics` + song name",
    "ru": "🎵 **Раздел музыки**\n\nВыберите одну из трёх платформ в главном меню:\n🟢 **Spotify** — поиск треков, альбомов, артистов, плейлистов и чарты недели\n🔴 **YouTube Music** — треки, альбомы, артисты и плейлисты из YouTube Music\n☁️ **SoundCloud** — ремиксы и независимые треки\n\n**Как это работает:** кнопка поиска открывает инлайн-меню с обложкой, названием и длительностью песен. Готовые песни приходят мгновенно; иначе появляется «⏳ загрузка», которое заменяется полной песней. Внутри бота песня приходит со всеми кнопками (текст, эффекты, плейлист...).\n\nКаждый файл — MP3 «Артист - Название» с оригинальной обложкой и полными тегами.\n\n🔗 Ссылки Spotify, YouTube Music, SoundCloud и Deezer тоже работают.\n\n_Быстрая команда:_ `/tahamusic`, `/tahaspotify`, `/tahasoundcloud`, `/tahalyrics` + название",
    "es": "🎵 **Sección de música**\n\nElige una de tres plataformas en el menú principal:\n🟢 **Spotify** — canciones, álbumes, artistas, listas y el top semanal\n🔴 **YouTube Music** — canciones, álbumes, artistas y listas de YouTube Music\n☁️ **SoundCloud** — remixes y pistas independientes\n\n**Cómo funciona:** el botón de búsqueda abre el menú en línea con portada, nombre y duración. Las canciones listas se envían al instante; si no, aparece «⏳ descargando» y se reemplaza por la canción completa. Dentro del bot la canción llega con todos sus botones (letra, efectos, lista...).\n\nCada archivo es un MP3 «Artista - Título» con portada original y etiquetas completas.\n\n🔗 También funcionan enlaces de Spotify, YouTube Music, SoundCloud y Deezer.\n\n_Comando rápido:_ `/tahamusic`, `/tahaspotify`, `/tahasoundcloud`, `/tahalyrics` + nombre",
}
HELP_PAGES["reddit"]["label"] = {"fa": "🐦 ردیت و X", "en": "🐦 Reddit & X", "ru": "🐦 Reddit и X", "es": "🐦 Reddit y X"}
for _l, _extra in {
    "fa": "\n\n🐦 **توییتر (X)** — لینک توییت (x.com یا twitter.com) را بفرستید تا عکس‌ها، ویدیوها و GIFهای آن به‌همراه متن توییت دانلود شود.\n🔔 **اشتراک ساب‌ردیت** — پست‌های جدید یک ساب‌ردیت خودکار برایتان ارسال می‌شود.",
    "en": "\n\n🐦 **Twitter (X)** — send a tweet link (x.com or twitter.com) to download its photos, videos and GIFs with the tweet text.\n🔔 **Subreddit subscription** — new posts from a subreddit are sent to you automatically.",
    "ru": "\n\n🐦 **Twitter (X)** — отправьте ссылку на твит (x.com или twitter.com), чтобы скачать фото, видео и GIF вместе с текстом.\n🔔 **Подписка на сабреддит** — новые посты присылаются автоматически.",
    "es": "\n\n🐦 **Twitter (X)** — envía un enlace de tweet (x.com o twitter.com) para descargar sus fotos, videos y GIFs con el texto.\n🔔 **Suscripción a subreddit** — las publicaciones nuevas se te envían automáticamente.",
}.items():
    HELP_PAGES["reddit"]["body"][_l] = HELP_PAGES["reddit"]["body"][_l] + _extra
for _l, _extra in {
    "fa": "\n\n▶️ **پخش همه** و 🔀 **پخش تصادفی** — ارسال همه آهنگ‌های پلی‌لیست با یک لمس.\n_دکمه «📃 پلی‌لیست‌های من» در منوی اصلی است._",
    "en": "\n\n▶️ **Play All** and 🔀 **Shuffle** — send every song in the playlist with one tap.\n_The \"📃 My Playlists\" button is in the main menu._",
    "ru": "\n\n▶️ **Воспроизвести все** и 🔀 **Перемешать** — все песни плейлиста одним нажатием.\n_Кнопка «📃 Мои плейлисты» — в главном меню._",
    "es": "\n\n▶️ **Reproducir todo** y 🔀 **Aleatorio** — todas las canciones de la lista con un toque.\n_El botón «📃 Mis listas» está en el menú principal._",
}.items():
    HELP_PAGES["playlists"]["body"][_l] = HELP_PAGES["playlists"]["body"][_l] + _extra
HELP_PAGES["profile"] = {
    "label": {"fa": "👤 پروفایل من", "en": "👤 My Profile", "ru": "👤 Мой профиль", "es": "👤 Mi perfil"},
    "body": {
        "fa": "👤 **پروفایل من**\n\nاز منوی اصلی وارد «👤 پروفایل من» شوید تا ببینید:\n▫️ تعداد آهنگ‌ها، ویدیوها، عکس‌ها، ویرایش‌ها و شناسایی‌های شما\n▫️ هنرمندان محبوب و آخرین آهنگ‌ها\n▫️ 🕒 تاریخچه کامل فعالیت‌ها\n▫️ دسترسی سریع به کتابخانه، آمار، اشتراک‌ها و پلی‌لیست‌ها\n\n🤖 **خلاصه هوشمند من** — هوش مصنوعی ربات از روی فعالیت‌هایتان یک معرفی کوتاه از سلیقه موزیک، ویدیوها و عکس‌هایتان می‌نویسد و چند آهنگ پیشنهاد می‌دهد.",
        "en": "👤 **My Profile**\n\nOpen \"👤 My Profile\" from the main menu to see:\n▫️ How many songs, videos, photos, edits and recognitions you have\n▫️ Your top artists and latest songs\n▫️ 🕒 Your full activity history\n▫️ Quick access to library, stats, subscriptions and playlists\n\n🤖 **My AI Summary** — the bot's AI reads your activity and writes a short portrait of your music taste, videos and photos, plus a few recommendations.",
        "ru": "👤 **Мой профиль**\n\nОткройте «👤 Мой профиль» в главном меню:\n▫️ Сколько у вас песен, видео, фото, правок и распознаваний\n▫️ Любимые артисты и последние песни\n▫️ 🕒 Полная история активности\n▫️ Быстрый доступ к библиотеке, статистике, подпискам и плейлистам\n\n🤖 **Мой ИИ-портрет** — ИИ бота описывает ваш музыкальный вкус, видео и фото и даёт рекомендации.",
        "es": "👤 **Mi perfil**\n\nAbre «👤 Mi perfil» en el menú principal para ver:\n▫️ Cuántas canciones, videos, fotos, ediciones y reconocimientos tienes\n▫️ Tus artistas favoritos y últimas canciones\n▫️ 🕒 Tu historial completo de actividad\n▫️ Acceso rápido a biblioteca, estadísticas, suscripciones y listas\n\n🤖 **Mi resumen IA** — la IA del bot describe tu gusto musical, videos y fotos, con algunas recomendaciones.",
    },
}
HELP_PAGES["inline"]["body"] = {
    "fa": "⚡️ **استفاده در هر چتی**\n\nدر هر چت تایپ کنید:\n`@{bot_username} sp نام آهنگ` (اسپاتیفای) • `ytm` (یوتیوب موزیک) • `sc` (ساندکلاد)\n`alb` آلبوم • `art` خواننده • `pl` پلی‌لیست • `top` چارت هفته • `vid` ویدیو • `pt` پینترست • `px` پکسلز • `ai` سوال\n\nآهنگ‌های آماده فوری ارسال می‌شوند؛ بقیه اول «⏳ در حال دانلود» را نشان می‌دهند و بعد با آهنگ کامل جایگزین می‌شوند. دکمه «⚡️ رفتن به بات طاها» زیر آهنگ، همه ابزارهای آن را در ربات باز می‌کند.",
    "en": "⚡️ **Use It In Any Chat**\n\nIn any chat type:\n`@{bot_username} sp song` (Spotify) • `ytm` (YouTube Music) • `sc` (SoundCloud)\n`alb` album • `art` artist • `pl` playlist • `top` weekly chart • `vid` video • `pt` Pinterest • `px` Pexels • `ai` question\n\nReady songs are sent instantly; others first show \"⏳ downloading\" and are then replaced by the full song. The \"⚡️ Go to Taha Bot\" button under a song opens all its tools in the bot.",
    "ru": "⚡️ **В любом чате**\n\nНаберите в любом чате:\n`@{bot_username} sp песня` (Spotify) • `ytm` (YouTube Music) • `sc` (SoundCloud)\n`alb` альбом • `art` артист • `pl` плейлист • `top` чарт • `vid` видео • `pt` Pinterest • `px` Pexels • `ai` вопрос\n\nГотовые песни приходят мгновенно; остальные сначала показывают «⏳ загрузка», затем заменяются полной песней. Кнопка «⚡️ Перейти в Taha Bot» открывает все инструменты песни в боте.",
    "es": "⚡️ **Úsalo en cualquier chat**\n\nEn cualquier chat escribe:\n`@{bot_username} sp canción` (Spotify) • `ytm` (YouTube Music) • `sc` (SoundCloud)\n`alb` álbum • `art` artista • `pl` lista • `top` top semanal • `vid` video • `pt` Pinterest • `px` Pexels • `ai` pregunta\n\nLas canciones listas se envían al instante; las demás muestran «⏳ descargando» y luego se reemplazan por la canción completa. El botón «⚡️ Ir a Taha Bot» abre todas sus herramientas en el bot.",
}
for _l, _line in {
    "fa": ("🔴 **ردیت** — پست، ساب‌ردیت و جستجو", "🐦 **ردیت و X** — پست، ساب‌ردیت، جستجو و توییت‌ها"),
    "en": ("🔴 **Reddit** — post, subreddit and search", "🐦 **Reddit & X** — posts, subreddits, search and tweets"),
    "ru": ("🔴 **Reddit** — пост, сабреддит и поиск", "🐦 **Reddit и X** — посты, сабреддиты, поиск и твиты"),
    "es": ("🔴 **Reddit** — publicación, subreddit y búsqueda", "🐦 **Reddit y X** — publicaciones, subreddits, búsqueda y tweets"),
}.items():
    _txt = STRINGS["help_home_text"][_l].replace(_line[0], _line[1])
    _prof = {"fa": "👤 **پروفایل من** — فعالیت‌ها، تاریخچه و خلاصه هوشمند", "en": "👤 **My Profile** — activity, history and AI summary",
             "ru": "👤 **Мой профиль** — активность, история и ИИ-портрет", "es": "👤 **Mi perfil** — actividad, historial y resumen IA"}[_l]
    _txt = _txt.replace("\n🔔", f"\n{_prof}\n🔔", 1)
    _txt = _txt.replace("📥 **دانلودر** — یوتیوب، اینستاگرام، تیک‌تاک", "📥 **دانلودر** — یوتیوب، اینستاگرام، تیک‌تاک، توییتر")
    _txt = _txt.replace("— Original, SoundCloud and Spotify", "— Spotify, YouTube Music and SoundCloud").replace("— اورجینال، ساندکلود و اسپاتیفای", "— اسپاتیفای، یوتیوب موزیک و ساندکلاد")
    STRINGS["help_home_text"][_l] = _txt


for _l, _extra in {
    "fa": "\n\n🔍 **جستجوی تصویر وب** — از منوی «تصاویر گوگل» در کل وب جستجو کنید و نسخه اصلی هر عکس را بگیرید.\n📜 **تاریخچه تصاویر** — هر عکسی که از پینترست، پکسلز یا وب گرفته‌اید با یک لمس دوباره دانلود می‌شود.\n🎨 **ابزار تصویر** — کافی است یک عکس برای ربات بفرستید تا ابزارها (حذف پس‌زمینه، فشرده‌سازی، تبدیل فرمت، برش، فیلتر، واترمارک) باز شوند.",
    "en": "\n\n🔍 **Web image search** — search the whole web from the \"Google Images\" menu and get each picture's original file.\n📜 **Image history** — every image you got from Pinterest, Pexels or the web downloads again with one tap.\n🎨 **Image tools** — just send the bot a photo and the tools open (remove background, compress, convert, crop, filters, watermark).",
    "ru": "\n\n🔍 **Поиск картинок в вебе** — ищите по всему интернету в меню «Google Картинки» и получайте оригиналы.\n📜 **История изображений** — любое скачанное изображение можно получить снова одним нажатием.\n🎨 **Инструменты фото** — просто отправьте боту фото, и инструменты откроются.",
    "es": "\n\n🔍 **Búsqueda de imágenes web** — busca en toda la web desde el menú «Google Imágenes» y obtén el original.\n📜 **Historial de imágenes** — vuelve a descargar cualquier imagen con un toque.\n🎨 **Herramientas de imagen** — envía una foto al bot y se abren las herramientas.",
}.items():
    HELP_PAGES["photo"]["body"][_l] = HELP_PAGES["photo"]["body"][_l] + _extra
for _l, _extra in {
    "fa": "\n\n⚡️ شناسایی با موتور Shazam انجام می‌شود و بعد از نتیجه، دکمه «🎛 ویرایش این فایل» هم برای ابزارهای صدا وجود دارد.",
    "en": "\n\n⚡️ Recognition runs on Shazam, and after the result an \"🎛 Edit this file\" button opens the audio tools.",
    "ru": "\n\n⚡️ Распознавание работает на Shazam, а после результата кнопка «🎛 Редактировать файл» открывает аудиоинструменты.",
    "es": "\n\n⚡️ El reconocimiento usa Shazam y, tras el resultado, el botón «🎛 Editar este archivo» abre las herramientas de audio.",
}.items():
    HELP_PAGES["shazam"]["body"][_l] = HELP_PAGES["shazam"]["body"][_l] + _extra

# Bale build: the help was rewritten for what this bot actually does on Bale
# (no inline mode, upload limits, admin approval...). ru/es fall back to English.
from modules import bale_help as _bale_help
HELP_PAGES.clear()
for _k, _page in _bale_help.PAGES.items():
    for _part in ("label", "body"):
        for _l in ("ru", "es"):
            _page[_part].setdefault(_l, _page[_part]["en"])
    HELP_PAGES[_k] = _page
STRINGS["help_home_text"] = {**_bale_help.HOME, "ru": _bale_help.HOME["en"], "es": _bale_help.HOME["en"]}


def help_keyboard(chat_id, active=None):
    lang = get_lang(chat_id)
    keys = list(HELP_PAGES.keys())
    rows, row = [], []
    for k in keys:
        label = HELP_PAGES[k]["label"].get(lang) or HELP_PAGES[k]["label"][DEFAULT_LANG]
        row.append(InlineKeyboardButton(("• " + label + " •") if k == active else label,
                                        callback_data=f"help_{k}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(t(chat_id, "main_menu_btn"), callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)

STRINGS.update({
    "btn_yt_search": {"fa": "🔴 جستجو در یوتیوب", "en": "🔴 Search YouTube", "ru": "🔴 Поиск на YouTube", "es": "🔴 Buscar en YouTube"},
    "btn_instagram": {"fa": "📸 اینستاگرام", "en": "📸 Instagram", "ru": "📸 Instagram", "es": "📸 Instagram"},
    "btn_tiktok": {"fa": "📱 تیک‌تاک", "en": "📱 TikTok", "ru": "📱 TikTok", "es": "📱 TikTok"},
    "btn_subs_full": {"fa": "🔔 اشتراک‌ها (دریافت خودکار پست جدید)", "en": "🔔 Subscriptions (auto-get new posts)", "ru": "🔔 Подписки (авто-получение новых постов)", "es": "🔔 Suscripciones (recibir publicaciones nuevas automáticamente)"},
    "btn_vedit_full": {"fa": "🎬 ویرایش ویدیو (ارسال فایل ویدیو)", "en": "🎬 Video Editor (send a video file)", "ru": "🎬 Видеоредактор (отправьте видеофайл)", "es": "🎬 Editor de video (envía un archivo de video)"},
    "btn_back_main": {"fa": "🔙 بازگشت به منوی اصلی", "en": "🔙 Back to Main Menu", "ru": "🔙 Назад в главное меню", "es": "🔙 Volver al menú principal"},
    "btn_yt_keyword": {"fa": "🔍 جستجو با کلمه کلیدی (Keyword)", "en": "🔍 Search by Keyword", "ru": "🔍 Поиск по ключевому слову", "es": "🔍 Buscar por palabra clave"},
    "btn_yt_channel": {"fa": "📺 جستجوی کانال (Channel)", "en": "📺 Search by Channel", "ru": "📺 Поиск по каналу", "es": "📺 Buscar por canal"},
    "yt_search_choose_text": {
        "fa": "🔴 **جستجوی یوتیوب:** نحوه جستجو را انتخاب کنید:",
        "en": "🔴 **YouTube Search:** choose how to search:",
        "ru": "🔴 **Поиск YouTube:** выберите способ поиска:",
        "es": "🔴 **Búsqueda en YouTube:** elige cómo buscar:",
    },
    "dl_menu_text": {
        "fa": "🔗 **بخش دانلودر چندرسانه‌ای**\n\nلینک مدیا را مستقیماً در چت بفرستید، یا پلتفرم مورد نظر را برای جستجو انتخاب کنید:",
        "en": "🔗 **Media Downloader Section**\n\nSend a media link directly in the chat, or pick a platform to search:",
        "ru": "🔗 **Раздел загрузчика медиа**\n\nОтправьте ссылку на медиа прямо в чат или выберите платформу для поиска:",
        "es": "🔗 **Sección de descargador multimedia**\n\nEnvía un enlace multimedia directamente en el chat, o elige una plataforma para buscar:",
    },
    "btn_engine_sc": {"fa": "☁️ ساندکلود", "en": "☁️ SoundCloud", "ru": "☁️ SoundCloud", "es": "☁️ SoundCloud"},
    "btn_engine_spotify": {"fa": "💽 موزیک اورجینال", "en": "💽 Original Music", "ru": "💽 Оригинальная музыка", "es": "💽 Música original"},
    "btn_engine_lyric": {"fa": "🔎 پیدا کردن آهنگ از روی متن", "en": "🔎 Find a Song by Its Lyrics", "ru": "🔎 Найти песню по тексту", "es": "🔎 Encontrar canción por su letra"},
    "music_menu_text": {
        "fa": "🎵 **بخش جستجو و پخش موزیک**\n\nلطفاً موتور جستجوی خود را انتخاب کنید:",
        "en": "🎵 **Music Search & Playback**\n\nPlease choose your search engine:",
        "ru": "🎵 **Поиск и воспроизведение музыки**\n\nВыберите поисковый движок:",
        "es": "🎵 **Búsqueda y reproducción de música**\n\nPor favor elige tu motor de búsqueda:",
    },
    "sc_search_prompt": {
        "fa": "☁️ **جستجو در ساندکلود:**\nنام آهنگ یا هنرمند را بفرستید:",
        "en": "☁️ **SoundCloud Search:**\nSend the song name or artist:",
        "ru": "☁️ **Поиск в SoundCloud:**\nОтправьте название песни или исполнителя:",
        "es": "☁️ **Búsqueda en SoundCloud:**\nEnvía el nombre de la canción o artista:",
    },
    "spotify_search_prompt": {
        "fa": "🎧 **جستجوی اسپاتیفای:**\nنام آهنگ یا هنرمند را بفرستید:",
        "en": "🎧 **Spotify Search:**\nSend the song name or artist:",
        "ru": "🎧 **Поиск в Spotify:**\nОтправьте название песни или исполнителя:",
        "es": "🎧 **Búsqueda en Spotify:**\nEnvía el nombre de la canción o artista:",
    },
    "ai_intro_text": {
        "fa": "🧠✨ **دستیار هوشمند بات طاها**\n_دستیار شخصی شما برای موزیک، ویدیو و بیشتر_\n\nبا من مثل یک دوست حرف بزن — نه دستور! چیزی که میتونم برات انجام بدم:\n\n🎵 **کشف و دانلود موزیک**\nبگو چه حسی داری یا چیکار میخوای بکنی («آهنگ برای باشگاه»، «آهنگ‌های غمگین») و چند تا پیشنهاد عالی می‌گیری — با یک کلیک همه رو دانلود کن.\n\n📺 **جستجوی یوتیوب**\nاسم ویدیو یا موضوعش رو بگو، نتایج واقعی رو نشونت میدم تا انتخاب کنی.\n\n🎤 **متن آهنگ**\nاسم و خواننده آهنگ رو بگو، متن کاملشو برات میارم.\n\n📊 **سابقه دانلودها**\nبپرس چی دانلود کردی و کِی — از دیتابیس خودت برات میگم.\n\n_فقط بنویس چی میخوای، بقیه‌شو من انجام میدم._ 🚀",
        "en": "🧠✨ **Taha Bot AI Assistant**\n_Your personal assistant for music, video and more_\n\nTalk to me like a friend, not a command! Here's what I can do for you:\n\n🎵 **Discover & download music**\nTell me your mood or what you're up to (\"a song for the gym\", \"sad songs\") and get great suggestions — download them all with one tap.\n\n📺 **YouTube search**\nName a video or its topic, and I'll show you real results to pick from.\n\n🎤 **Lyrics**\nName the song and artist, and I'll bring you the full lyrics.\n\n📊 **Download history**\nAsk what you downloaded and when — I'll pull it from your own data.\n\n_Just type what you want, I'll handle the rest._ 🚀",
        "ru": "🧠✨ **ИИ-помощник Taha Bot**\n_Ваш персональный помощник для музыки, видео и многого другого_\n\nГоворите со мной как с другом, а не командами! Вот что я умею:\n\n🎵 **Находить и скачивать музыку**\nСкажите своё настроение или что вы делаете («песня для тренажёрного зала», «грустные песни») и получите отличные предложения — скачайте всё одним нажатием.\n\n📺 **Поиск на YouTube**\nНазовите видео или его тему, и я покажу настоящие результаты для выбора.\n\n🎤 **Текст песни**\nНазовите песню и исполнителя, и я принесу полный текст.\n\n📊 **История загрузок**\nСпросите, что и когда вы скачивали — я возьму это из ваших данных.\n\n_Просто напишите, что хотите, остальное сделаю я._ 🚀",
        "es": "🧠✨ **Asistente de IA de Taha Bot**\n_Tu asistente personal para música, video y más_\n\n¡Habla conmigo como con un amigo, no con comandos! Esto es lo que puedo hacer por ti:\n\n🎵 **Descubrir y descargar música**\nDime tu estado de ánimo o qué estás haciendo («una canción para el gimnasio», «canciones tristes») y recibe excelentes sugerencias — descárgalas todas con un toque.\n\n📺 **Búsqueda en YouTube**\nNombra un video o su tema, y te mostraré resultados reales para elegir.\n\n🎤 **Letras**\nNombra la canción y el artista, y te traeré la letra completa.\n\n📊 **Historial de descargas**\nPregunta qué descargaste y cuándo — lo sacaré de tus propios datos.\n\n_Solo escribe lo que quieres, yo me encargo del resto._ 🚀",
    },
    "btn_ai_exit": {"fa": "🔙 خروج از چت", "en": "🔙 Exit Chat", "ru": "🔙 Выйти из чата", "es": "🔙 Salir del chat"},
    "ai_group_hint": {
        "fa": "🧠 **هوش مصنوعی**\n\nدر گروه‌ها باید سوال را همراه دستور بنویسید، مثلاً:\n`/tahaai آهنگ شاد برای مهمونی پیشنهاد بده`",
        "en": "🧠 **AI Assistant**\n\nIn groups, write your question along with the command, e.g.:\n`/tahaai suggest an upbeat song for a party`",
        "ru": "🧠 **ИИ-помощник**\n\nВ группах пишите вопрос вместе с командой, например:\n`/tahaai предложи энергичную песню для вечеринки`",
        "es": "🧠 **Asistente de IA**\n\nEn grupos, escribe tu pregunta junto con el comando, por ejemplo:\n`/tahaai sugiere una canción animada para una fiesta`",
    },
    "ai_thinking": {"fa": "🧠 در حال فکر کردن...", "en": "🧠 Thinking...", "ru": "🧠 Думаю...", "es": "🧠 Pensando..."},
    "ai_didnt_understand": {"fa": "متوجه نشدم، دوباره بپرسید.", "en": "I didn't understand, please ask again.", "ru": "Я не понял, спросите ещё раз.", "es": "No entendí, pregunta de nuevo."},
    "ai_unavailable": {"fa": "❌ سرویس هوش مصنوعی موقتاً در دسترس نیست.", "en": "❌ The AI service is temporarily unavailable.", "ru": "❌ Сервис ИИ временно недоступен.", "es": "❌ El servicio de IA no está disponible temporalmente."},
    "contact_admin_prompt": {
        "fa": "✉️ **پیام به سازنده**\n\nپیام خود را بنویسید و ارسال کنید.\nپاسخ سازنده مستقیماً برای شما ارسال خواهد شد.",
        "en": "✉️ **Message the Creator**\n\nWrite your message and send it.\nThe creator's reply will be sent to you directly.",
        "ru": "✉️ **Написать создателю**\n\nНапишите своё сообщение и отправьте его.\nОтвет создателя придёт вам напрямую.",
        "es": "✉️ **Mensaje al creador**\n\nEscribe tu mensaje y envíalo.\nLa respuesta del creador se te enviará directamente.",
    },
    "btn_cancel": {"fa": "🔙 انصراف", "en": "🔙 Cancel", "ru": "🔙 Отмена", "es": "🔙 Cancelar"},
    "support_unavailable": {"fa": "❌ ارسال پیام در حال حاضر ممکن نیست.", "en": "❌ Sending messages isn't possible right now.", "ru": "❌ Отправка сообщений сейчас невозможна.", "es": "❌ No es posible enviar mensajes ahora."},
    "support_send_failed": {"fa": "❌ ارسال پیام با خطا مواجه شد.", "en": "❌ Sending the message failed.", "ru": "❌ Не удалось отправить сообщение.", "es": "❌ Error al enviar el mensaje."},
    "support_sent_confirm": {
        "fa": "✅ پیام شما برای سازنده ارسال شد.\nپاسخ به‌زودی همین‌جا برایتان می‌آید.",
        "en": "✅ Your message has been sent to the creator.\nThe reply will arrive here soon.",
        "ru": "✅ Ваше сообщение отправлено создателю.\nОтвет придёт сюда в ближайшее время.",
        "es": "✅ Tu mensaje ha sido enviado al creador.\nLa respuesta llegará aquí pronto.",
    },
    "ai_closed_text": {
        "fa": "👋 چت هوش مصنوعی بسته شد.", "en": "👋 AI chat closed.", "ru": "👋 ИИ-чат закрыт.", "es": "👋 Chat de IA cerrado.",
    },
    "sub_add_prompt": {
        "fa": "➕ **افزودن اشتراک**\n\nلینک کانال یوتیوب، پیج اینستاگرام یا تیک‌تاک را بفرستید.\nمثال: `https://instagram.com/username` یا `@username`",
        "en": "➕ **Add Subscription**\n\nSend a YouTube channel, Instagram page, or TikTok link.\nExample: `https://instagram.com/username` or `@username`",
        "ru": "➕ **Добавить подписку**\n\nОтправьте ссылку на канал YouTube, страницу Instagram или TikTok.\nПример: `https://instagram.com/username` или `@username`",
        "es": "➕ **Agregar suscripción**\n\nEnvía un enlace de canal de YouTube, página de Instagram o TikTok.\nEjemplo: `https://instagram.com/username` o `@username`",
    },
    "ig_menu_prompt": {
        "fa": "📸 **دانلود مستقیم از اینستاگرام:**\n\nلطفاً لینک ریلز، پست یا استوری را بفرستید:",
        "en": "📸 **Direct Instagram Download:**\n\nSend a reel, post, or story link:",
        "ru": "📸 **Прямая загрузка из Instagram:**\n\nОтправьте ссылку на рилс, пост или историю:",
        "es": "📸 **Descarga directa de Instagram:**\n\nEnvía un enlace de reel, publicación o historia:",
    },
    "tiktok_menu_prompt": {
        "fa": "📱 **دانلود مستقیم از تیک‌تاک:**\nآیدی پیج یا لینک مستقیم ویدیو را بفرستید:",
        "en": "📱 **Direct TikTok Download:**\nSend a page ID or a direct video link:",
        "ru": "📱 **Прямая загрузка из TikTok:**\nОтправьте ID страницы или прямую ссылку на видео:",
        "es": "📱 **Descarga directa de TikTok:**\nEnvía un ID de página o un enlace directo del video:",
    },
    "btn_pt_search": {"fa": "🔎 جستجوی پین", "en": "🔎 Search Pins", "ru": "🔎 Поиск пинов", "es": "🔎 Buscar pines"},
    "pinterest_menu_text": {
        "fa": "📌 **بخش پینترست**\n\nلینک یک پین، بورد یا پروفایل کاربر پینترست را مستقیم در چت بفرستید تا دانلود شود (بورد و پروفایل به‌صورت دسته‌ای ارسال می‌شوند)، یا با کلیدواژه جستجو کنید:",
        "en": "📌 **Pinterest Section**\n\nSend a Pinterest pin, board, or user profile link directly in the chat to download it (boards and profiles are sent as a batch), or search by keyword:",
        "ru": "📌 **Раздел Pinterest**\n\nОтправьте ссылку на пин, борд или профиль пользователя Pinterest прямо в чат для загрузки (борды и профили отправляются пакетом), или выполните поиск по ключевому слову:",
        "es": "📌 **Sección de Pinterest**\n\nEnvía un enlace de pin, tablero o perfil de usuario de Pinterest directamente en el chat para descargarlo (tableros y perfiles se envían en lote), o busca por palabra clave:",
    },
    "pt_search_prompt": {
        "fa": "🔎 **جستجوی پینترست**\n\nکلیدواژه مورد نظر را ارسال کنید:",
        "en": "🔎 **Pinterest Search**\n\nSend the keyword you want:",
        "ru": "🔎 **Поиск в Pinterest**\n\nОтправьте нужное ключевое слово:",
        "es": "🔎 **Búsqueda en Pinterest**\n\nEnvía la palabra clave que quieres:",
    },
    "btn_rd_search": {"fa": "🔎 جستجوی ردیت", "en": "🔎 Search Reddit", "ru": "🔎 Поиск на Reddit", "es": "🔎 Buscar en Reddit"},
    "reddit_menu_text": {
        "fa": "🔴 **بخش ردیت**\n\nلینک یک پست یا ساب‌ردیت (مثلاً t.me/r/aww) را مستقیم در چت بفرستید (ساب‌ردیت پست‌های برتر را به‌صورت دسته‌ای می‌فرستد)، یا با کلیدواژه جستجو کنید:",
        "en": "🔴 **Reddit Section**\n\nSend a post or subreddit link (e.g. reddit.com/r/aww) directly in the chat (subreddits send top posts as a batch), or search by keyword:",
        "ru": "🔴 **Раздел Reddit**\n\nОтправьте ссылку на пост или сабреддит (например, reddit.com/r/aww) прямо в чат (сабреддиты отправляют топовые посты пакетом), или выполните поиск по ключевому слову:",
        "es": "🔴 **Sección de Reddit**\n\nEnvía un enlace de publicación o subreddit (por ejemplo, reddit.com/r/aww) directamente en el chat (los subreddits envían las publicaciones destacadas en lote), o busca por palabra clave:",
    },
    "rd_search_prompt": {
        "fa": "🔎 **جستجوی ردیت**\n\nکلیدواژه مورد نظر را ارسال کنید:",
        "en": "🔎 **Reddit Search**\n\nSend the keyword you want:",
        "ru": "🔎 **Поиск на Reddit**\n\nОтправьте нужное ключевое слово:",
        "es": "🔎 **Búsqueda en Reddit**\n\nEnvía la palabra clave que quieres:",
    },
    "image_search_prompt": {
        "fa": "🖼 **جستجوی تصویر**\n\nکلیدواژه مورد نظر را ارسال کنید (انگلیسی نتیجه بهتری می‌دهد):",
        "en": "🖼 **Image Search**\n\nSend the keyword you want (English gives better results):",
        "ru": "🖼 **Поиск изображений**\n\nОтправьте нужное ключевое слово (на английском результаты лучше):",
        "es": "🖼 **Búsqueda de imágenes**\n\nEnvía la palabra clave que quieres (el inglés da mejores resultados):",
    },
    "audd_menu_text": {
        "fa": "🎧 **شناسایی آهنگ**\n\nیک ویس یا فایل صوتی از آهنگ مورد نظر برایم بفرستید؛ بلافاصله نام و خواننده‌اش را شناسایی می‌کنم و می‌توانید نسخه کامل را دانلود کنید.\n\n_برای شناسایی موزیک داخل یک ویدیو هم می‌توانید ویدیو را بفرستید و از ابزارهای ویرایش، «🎧 شناسایی آهنگ» را بزنید._",
        "en": "🎧 **Song Recognition**\n\nSend me a voice note or audio file of the song; I'll instantly identify its title and artist, and you can download the full version.\n\n_To identify music inside a video, send the video and use the \"🎧 Identify Song\" tool in the editor._",
        "ru": "🎧 **Распознавание песни**\n\nОтправьте мне голосовое сообщение или аудиофайл песни; я мгновенно определю название и исполнителя, и вы сможете скачать полную версию.\n\n_Чтобы распознать музыку внутри видео, отправьте видео и используйте инструмент «🎧 Распознать песню» в редакторе._",
        "es": "🎧 **Reconocimiento de canciones**\n\nEnvíame una nota de voz o archivo de audio de la canción; identificaré al instante su título y artista, y podrás descargar la versión completa.\n\n_Para identificar música dentro de un video, envía el video y usa la herramienta «🎧 Identificar canción» en el editor._",
    },
    "lyric_search_prompt": {
        "fa": "🔎 **جستجو از روی متن آهنگ**\n\nیک تکه از متن آهنگی که یادتان است را بفرستید:",
        "en": "🔎 **Find a Song by Lyrics**\n\nSend a bit of the lyrics you remember:",
        "ru": "🔎 **Найти песню по тексту**\n\nОтправьте фрагмент текста, который вы помните:",
        "es": "🔎 **Encontrar canción por su letra**\n\nEnvía un fragmento de la letra que recuerdes:",
    },
})


def get_dl_keyboard(chat_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(chat_id, "btn_yt_search"), callback_data="yt_menu")],
        [InlineKeyboardButton(t(chat_id, "btn_instagram"), callback_data="ig_menu"), InlineKeyboardButton(t(chat_id, "btn_tiktok"), callback_data="tiktok_menu")],
        [InlineKeyboardButton(t(chat_id, "btn_subs_full"), callback_data="subs_menu")],
        [InlineKeyboardButton(t(chat_id, "btn_vedit_full"), callback_data="menu_vedit")],
        [InlineKeyboardButton(t(chat_id, "btn_back_main"), callback_data="menu_main")]
    ])

def get_yt_search_menu(chat_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(chat_id, "btn_yt_keyword"), callback_data="yt_keyword")],
        [InlineKeyboardButton(t(chat_id, "btn_yt_channel"), callback_data="yt_channel")],
        [InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_downloader")]
    ])

STRINGS.update({
    "yt_extras_trim_range": {"fa": "✂️ برش ({start} تا {end})", "en": "✂️ Trim ({start} to {end})", "ru": "✂️ Обрезка ({start} - {end})", "es": "✂️ Recorte ({start} a {end})"},
    "yt_extras_trim_default": {"fa": "✂️ برش ویدیو", "en": "✂️ Trim Video", "ru": "✂️ Обрезка видео", "es": "✂️ Recortar video"},
    "yt_extras_thumb": {"fa": "کاور {check}", "en": "Thumbnail {check}", "ru": "Обложка {check}", "es": "Miniatura {check}"},
    "yt_extras_comments": {"fa": "کامنت‌ها {check}", "en": "Comments {check}", "ru": "Комментарии {check}", "es": "Comentarios {check}"},
    "yt_extras_desc": {"fa": "توضیحات {check}", "en": "Description {check}", "ru": "Описание {check}", "es": "Descripción {check}"},
    "yt_extras_start_download": {"fa": "🚀 شروع دانلود", "en": "🚀 Start Download", "ru": "🚀 Начать загрузку", "es": "🚀 Iniciar descarga"},
})

def get_extras_kb(chat_id):
    e = user_data.get(chat_id, {})
    trim_text = t(chat_id, "yt_extras_trim_range", start=e.get('trim_start'), end=e.get('trim_end')) if e.get('trim_start') else t(chat_id, "yt_extras_trim_default")
    def chk(key): return "✅" if e.get(key) else "❌"

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(trim_text, callback_data="tog_trim")],
        [InlineKeyboardButton(t(chat_id, "yt_extras_thumb", check=chk('thumb')), callback_data="tog_thumb")],
        [InlineKeyboardButton(t(chat_id, "yt_extras_comments", check=chk('comments')), callback_data="tog_comments"), InlineKeyboardButton(t(chat_id, "yt_extras_desc", check=chk('desc')), callback_data="tog_desc")],
        [InlineKeyboardButton(t(chat_id, "yt_extras_start_download"), callback_data="start_download")]
    ])

# ---------------- 6.5 ALBUMS, LIBRARY, STATS, LYRIC SEARCH ----------------
STRINGS.update({
    "album_none_found": {"fa": "❌ هیچ آهنگی از این آلبوم پیدا/دانلود نشد.", "en": "❌ No songs from this album could be found/downloaded.", "ru": "❌ Не удалось найти/скачать ни одной песни из этого альбома.", "es": "❌ No se pudo encontrar/descargar ninguna canción de este álbum."},
    "album_sending": {"fa": "📤 در حال ارسال {count} آهنگ...", "en": "📤 Sending {count} songs...", "ru": "📤 Отправка {count} песен...", "es": "📤 Enviando {count} canciones..."},
    "album_not_found_note": {"fa": "\n\n⚠️ **پیدا نشد:** {names}", "en": "\n\n⚠️ **Not found:** {names}", "ru": "\n\n⚠️ **Не найдено:** {names}", "es": "\n\n⚠️ **No encontrado:** {names}"},
    "track_info_not_found": {"fa": "❌ اطلاعات این آهنگ یافت نشد.", "en": "❌ This song's info wasn't found.", "ru": "❌ Информация об этой песне не найдена.", "es": "❌ No se encontró información de esta canción."},
    "lrc_not_available": {"fa": "❌ نسخه هماهنگ‌شده (LRC) برای این آهنگ موجود نیست.", "en": "❌ A synced (LRC) version isn't available for this song.", "ru": "❌ Синхронизированная (LRC) версия для этой песни недоступна.", "es": "❌ No hay una versión sincronizada (LRC) disponible para esta canción."},
    "lrc_file_caption": {"fa": "📄 فایل متن هماهنگ‌شده\n🎵 {title}", "en": "📄 Synced lyrics file\n🎵 {title}", "ru": "📄 Файл синхронизированного текста\n🎵 {title}", "es": "📄 Archivo de letra sincronizada\n🎵 {title}"},
    "fx_choose_prompt": {"fa": "🎛 **یک افکت انتخاب کنید:**\n\nهر نسخه فقط یک‌بار ساخته می‌شود و بعد از آن فوری تحویل داده می‌شود.", "en": "🎛 **Choose an effect:**\n\nEach version is built only once, then delivered instantly after that.", "ru": "🎛 **Выберите эффект:**\n\nКаждая версия создаётся только один раз, а затем доставляется мгновенно.", "es": "🎛 **Elige un efecto:**\n\nCada versión se genera solo una vez, y luego se entrega al instante."},
    "fx_invalid": {"fa": "❌ این افکت معتبر نیست.", "en": "❌ This effect isn't valid.", "ru": "❌ Этот эффект недействителен.", "es": "❌ Este efecto no es válido."},
    "fx_preparing": {"fa": "🎛 در حال آماده‌سازی...", "en": "🎛 Preparing...", "ru": "🎛 Подготовка...", "es": "🎛 Preparando..."},
    "album_finding": {"fa": "📀 در حال یافتن آلبوم...", "en": "📀 Finding the album...", "ru": "📀 Поиск альбома...", "es": "📀 Buscando el álbum..."},
    "album_not_found_for_track": {"fa": "❌ آلبومی برای این آهنگ پیدا نشد.", "en": "❌ No album found for this song.", "ru": "❌ Альбом для этой песни не найден.", "es": "❌ No se encontró un álbum para esta canción."},
    "album_track_count": {"fa": "🎵 {count} آهنگ", "en": "🎵 {count} songs", "ru": "🎵 {count} песен", "es": "🎵 {count} canciones"},
    "album_btn_download_all": {"fa": "⬇️ دریافت کل آلبوم ({count} آهنگ)", "en": "⬇️ Download Full Album ({count} songs)", "ru": "⬇️ Скачать весь альбом ({count} песен)", "es": "⬇️ Descargar álbum completo ({count} canciones)"},
    "album_info_expired": {"fa": "❌ اطلاعات آلبوم منقضی شده است.", "en": "❌ Album info has expired.", "ru": "❌ Информация об альбоме истекла.", "es": "❌ La información del álbum ha caducado."},
    "lyrics_fetching_toast": {"fa": "در حال دریافت متن ترانه...", "en": "Fetching lyrics...", "ru": "Получение текста песни...", "es": "Obteniendo la letra..."},
    "lyrics_song_info_not_found": {"fa": "❌ اطلاعات آهنگ جهت دریافت متن یافت نشد.", "en": "❌ Song info needed to fetch lyrics wasn't found.", "ru": "❌ Информация о песне для получения текста не найдена.", "es": "❌ No se encontró la información de la canción para obtener la letra."},
    "lyrics_result_header": {"fa": "🎤 **متن آهنگ:** {shown}", "en": "🎤 **Lyrics:** {shown}", "ru": "🎤 **Текст песни:** {shown}", "es": "🎤 **Letra:** {shown}"},
    "lyrics_not_in_db": {"fa": "❌ متن این ترانه در دیتابیس پیدا نشد.", "en": "❌ This song's lyrics weren't found in the database.", "ru": "❌ Текст этой песни не найден в базе данных.", "es": "❌ No se encontró la letra de esta canción en la base de datos."},
    "karaoke_source_unavailable": {"fa": "❌ منبع اصلی این آهنگ برای پردازش در دسترس نیست.", "en": "❌ The original source of this song isn't available for processing.", "ru": "❌ Исходный источник этой песни недоступен для обработки.", "es": "❌ La fuente original de esta canción no está disponible para procesar."},
    "processing_sending_to_server": {"fa": "🎙 در حال ارسال به سرور پردازش...", "en": "🎙 Sending to the processing server...", "ru": "🎙 Отправка на сервер обработки...", "es": "🎙 Enviando al servidor de procesamiento..."},
    "karaoke_request_queued": {
        "fa": "🎙 **درخواست کارائوکه ثبت شد.**\n\nجداسازی صدای خواننده چند دقیقه طول می‌کشد؛ نتیجه به‌صورت خودکار ارسال می‌شود.",
        "en": "🎙 **Karaoke request queued.**\n\nSeparating the vocals takes a few minutes; the result will be sent automatically.",
        "ru": "🎙 **Запрос на караоке принят.**\n\nОтделение вокала займёт несколько минут; результат будет отправлен автоматически.",
        "es": "🎙 **Solicitud de karaoke en cola.**\n\nSeparar la voz toma unos minutos; el resultado se enviará automáticamente.",
    },
    "processing_server_unreachable": {"fa": "❌ ارتباط با سرور پردازش برقرار نشد.", "en": "❌ Couldn't connect to the processing server.", "ru": "❌ Не удалось подключиться к серверу обработки.", "es": "❌ No se pudo conectar con el servidor de procesamiento."},
    "aisub_request_queued": {
        "fa": "📝 **درخواست زیرنویس فارسی ثبت شد.**\n\nتبدیل گفتار به متن و ترجمه چند دقیقه طول می‌کشد؛ ویدیو با زیرنویس چسبیده ارسال خواهد شد.",
        "en": "📝 **Persian subtitle request queued.**\n\nSpeech-to-text and translation take a few minutes; the video with burned-in subtitles will be sent.",
        "ru": "📝 **Запрос на персидские субтитры принят.**\n\nРаспознавание речи и перевод займут несколько минут; видео со встроенными субтитрами будет отправлено.",
        "es": "📝 **Solicitud de subtítulos en persa en cola.**\n\nLa conversión de voz a texto y la traducción toman unos minutos; se enviará el video con subtítulos incrustados.",
    },
    "lyrics_server_error": {"fa": "❌ خطا در اتصال به سرور متن ترانه.", "en": "❌ Error connecting to the lyrics server.", "ru": "❌ Ошибка соединения с сервером текстов песен.", "es": "❌ Error al conectar con el servidor de letras."},
    "album_queue_started": {"fa": "📀 صف دانلود آلبوم آغاز شد...", "en": "📀 Album download queue started...", "ru": "📀 Очередь загрузки альбома запущена...", "es": "📀 Se inició la cola de descarga del álbum..."},
    "album_sent_summary": {
        "fa": "✅ **{album}** ({sent} از {total} آهنگ) ارسال شد.{skip_note}\n\n{names}\n\nبرای متن آهنگ، افکت‌ها یا کارائوکه هر ترک، شماره‌اش رو بزن:",
        "en": "✅ **{album}** ({sent} of {total} songs) sent.{skip_note}\n\n{names}\n\nTap a track's number for lyrics, effects, or karaoke:",
        "ru": "✅ **{album}** ({sent} из {total} песен) отправлено.{skip_note}\n\n{names}\n\nНажмите номер трека для текста песни, эффектов или караоке:",
        "es": "✅ **{album}** ({sent} de {total} canciones) enviado.{skip_note}\n\n{names}\n\nToca el número de una pista para letra, efectos o karaoke:",
    },
})

async def download_album(client: Client, chat_id: int, status: Message, album: dict, extra_rows=None):
    """Albums, Spotify playlists, AI mood playlists.

    Every track goes through the unified pipeline (cache hit -> instant,
    otherwise download + tag + cover + archive), then the whole set is sent as
    grouped audio messages by file_id -- so cached tracks cost nothing and
    every file carries its own tags and cover. A numbered picker afterwards
    gives each track's actions (Telegram media-group items can't carry
    their own keyboards). extra_rows: extra buttons under that picker.
    """
    tracks = (album.get('tracks') or [])[:35]
    total = len(tracks)
    alb_name = album.get('album') or album.get('name') or ''
    is_real_album = bool(album.get('total_tracks') or album.get('year')) and not album.get('is_playlist')
    collected, skipped = [], []
    results = [None] * total
    done = [0]

    async def _progress():
        try:
            await status.edit_text(f"📀 **{alb_name}**\n⏳ در حال آماده‌سازی {done[0]} از {total} آهنگ...\n"
                                   "_همه آهنگ‌ها با هم ارسال می‌شوند._")
        except Exception:
            pass

    async def _one(i, trk):
        name = trk.get('name') or trk.get('title') or ''
        meta = make_meta(
            "spotify" if not trk.get("deezer_id") else "deezer",
            title=name, artist=trk.get('artists_all') or trk.get('artist') or album.get('artist'),
            album=trk.get('album') or (alb_name if is_real_album else ""),
            year=trk.get('year') or album.get('year'), duration=trk.get('duration'),
            cover=trk.get('cover') or (album.get('cover') if is_real_album else ""),
            isrc=trk.get('isrc'), spotify_id=trk.get('spotify_id') or trk.get('id') if not trk.get("deezer_id") else "",
            deezer_id=trk.get('deezer_id'), yt_id=trk.get('yt_id'),
            track_no=(trk.get('track_number') or i) if is_real_album else 0,
            total=total if is_real_album else 0)
        try:
            doc = await obtain_track(meta, upload_chat=chat_id)
            if doc_file_id(doc):
                if doc.pop("_sent_msg", None) is not None:
                    doc["_delivered"] = True  # no cache channel: already in the chat
                results[i - 1] = doc
            else:
                results[i - 1] = name
        except Exception as e:
            print(f"Album track failed ({name}): {e}", flush=True)
            results[i - 1] = name
        done[0] += 1
        if done[0] % 2 == 0 or done[0] == total:
            await _progress()

    # Tracks are prepared in parallel (MUSIC_SEMAPHORE caps real downloads;
    # cached ones resolve instantly) and delivered together afterwards.
    await _progress()
    await asyncio.gather(*(_one(i, trk) for i, trk in enumerate(tracks, 1)))
    for r in results:
        if isinstance(r, dict):
            collected.append(r)
        elif r:
            skipped.append(r)

    if not collected:
        try:
            return await status.edit_text(t(chat_id, "album_none_found"))
        except Exception:
            return

    try:
        await status.edit_text(t(chat_id, "album_sending", count=len(collected)))
    except Exception:
        pass

    if album.get("cover") or (collected and collected[0].get("cover")):
        await send_cover_card(client, chat_id, {
            "title": alb_name or album.get("name") or "", "artist": album.get("artist") or "",
            "cover": album.get("cover") or collected[0].get("cover")})
    pending = [d for d in collected if not d.get("_delivered")]
    for batch_start in range(0, len(pending), 10):
        batch = pending[batch_start:batch_start + 10]
        try:
            await client.send_media_group(chat_id, [InputMediaAudio(doc_file_id(d)) for d in batch])
        except Exception as e:
            print(f"Album media-group send failed, sending one by one: {e}", flush=True)
            for d in batch:
                await send_cached_doc(client, chat_id, d)
        await asyncio.sleep(0.5)
        for d in batch:
            log_history(chat_id, d.get("vid_id"), d.get("title"), d.get("artist"))

    user_data.setdefault(chat_id, {})["album_tracks"] = [(d.get("vid_id"), d.get("title")) for d in collected]
    rows, row = [], []
    for i in range(len(collected)):
        row.append(InlineKeyboardButton(str(i + 1), callback_data=f"album_track_{i}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if extra_rows:
        rows.extend(extra_rows)
    names = "\n".join(f"{i+1}. {d.get('title')}" for i, d in enumerate(collected))
    skip_note = t(chat_id, "album_not_found_note", names=', '.join(skipped)) if skipped else ""
    log_activity(chat_id, "album", f"{alb_name} ({len(collected)}/{total} تراک)")
    try:
        await status.delete()
    except Exception:
        pass
    await client.send_message(
        chat_id,
        t(chat_id, "album_sent_summary", album=alb_name, sent=len(collected), total=total,
          skip_note=skip_note, names=names[:3000]),
        reply_markup=InlineKeyboardMarkup(rows) if rows else None)


STRINGS.update({
    "lib_sort_recent": {"fa": "🕒 اخیر", "en": "🕒 Recent", "ru": "🕒 Недавние", "es": "🕒 Recientes"},
    "lib_sort_repeat": {"fa": "🔁 پرتکرار", "en": "🔁 Most Played", "ru": "🔁 Часто слушаемые", "es": "🔁 Más reproducidas"},
    "lib_sort_alpha": {"fa": "🔤 الفبا", "en": "🔤 A-Z", "ru": "🔤 По алфавиту", "es": "🔤 Alfabético"},
    "lib_unavailable": {"fa": "❌ کتابخانه در دسترس نیست.", "en": "❌ Library isn't available.", "ru": "❌ Библиотека недоступна.", "es": "❌ La biblioteca no está disponible."},
    "lib_read_error": {"fa": "❌ خطا در خواندن کتابخانه.", "en": "❌ Error reading the library.", "ru": "❌ Ошибка чтения библиотеки.", "es": "❌ Error al leer la biblioteca."},
    "lib_empty": {"fa": "📚 هنوز آهنگی دانلود نکرده‌اید.", "en": "📚 You haven't downloaded any songs yet.", "ru": "📚 Вы ещё не скачали ни одной песни.", "es": "📚 Aún no has descargado ninguna canción."},
    "lib_header": {"fa": "📚 **کتابخانه شما**", "en": "📚 **Your Library**", "ru": "📚 **Ваша библиотека**", "es": "📚 **Tu biblioteca**"},
    "lib_meta": {
        "fa": "_{count} آهنگ • {plays} دریافت • {sort} • صفحه {page} از {pages}_",
        "en": "_{count} songs • {plays} downloads • {sort} • page {page} of {pages}_",
        "ru": "_{count} песен • {plays} загрузок • {sort} • стр. {page} из {pages}_",
        "es": "_{count} canciones • {plays} descargas • {sort} • página {page} de {pages}_",
    },
    "lib_btn_mixtape": {"fa": "🎁 میکس‌تیپ من", "en": "🎁 My Mixtape", "ru": "🎁 Мой микстейп", "es": "🎁 Mi mixtape"},
    "stats_unavailable": {"fa": "❌ آمار در دسترس نیست.", "en": "❌ Stats aren't available.", "ru": "❌ Статистика недоступна.", "es": "❌ Las estadísticas no están disponibles."},
    "stats_read_error": {"fa": "❌ خطا در خواندن آمار.", "en": "❌ Error reading stats.", "ru": "❌ Ошибка чтения статистики.", "es": "❌ Error al leer las estadísticas."},
    "stats_empty": {"fa": "📊 هنوز آماری ثبت نشده است.", "en": "📊 No stats recorded yet.", "ru": "📊 Статистика пока не записана.", "es": "📊 Aún no se han registrado estadísticas."},
    "stats_unknown": {"fa": "نامشخص", "en": "Unknown", "ru": "Неизвестно", "es": "Desconocido"},
    "stats_header": {"fa": "📊 **آمار شما**\n", "en": "📊 **Your Stats**\n", "ru": "📊 **Ваша статистика**\n", "es": "📊 **Tus estadísticas**\n"},
    "stats_total_downloads": {"fa": "🎵 مجموع دانلودها: **{count}**", "en": "🎵 Total downloads: **{count}**", "ru": "🎵 Всего загрузок: **{count}**", "es": "🎵 Total de descargas: **{count}**"},
    "stats_last_7_days": {"fa": "🗓 در ۷ روز گذشته: **{count}**", "en": "🗓 In the last 7 days: **{count}**", "ru": "🗓 За последние 7 дней: **{count}**", "es": "🗓 En los últimos 7 días: **{count}**"},
    "stats_unique_artists": {"fa": "👤 هنرمندان یکتا: **{count}**\n", "en": "👤 Unique artists: **{count}**\n", "ru": "👤 Уникальных артистов: **{count}**\n", "es": "👤 Artistas únicos: **{count}**\n"},
    "stats_top_artists": {"fa": "**🏆 هنرمندان برتر شما:**", "en": "**🏆 Your Top Artists:**", "ru": "**🏆 Ваши топ-артисты:**", "es": "**🏆 Tus artistas favoritos:**"},
    "stats_times_suffix": {"fa": "بار", "en": "times", "ru": "раз", "es": "veces"},
    "stats_top_songs": {"fa": "\n**🔁 پرتکرارترین آهنگ‌ها:**", "en": "\n**🔁 Most Played Songs:**", "ru": "\n**🔁 Самые прослушиваемые песни:**", "es": "\n**🔁 Canciones más reproducidas:**"},
    "lyric_no_match": {"fa": "❌ آهنگی با این متن پیدا نشد.", "en": "❌ No song found with this lyric.", "ru": "❌ Песня с этим текстом не найдена.", "es": "❌ No se encontró ninguna canción con esta letra."},
})

LIB_PAGE_SIZE = 8
LIB_SORTS = {"r": "🕒 اخیر", "c": "🔁 پرتکرار", "a": "🔤 الفبا"}
LIB_SORT_KEYS = {"r": "lib_sort_recent", "c": "lib_sort_repeat", "a": "lib_sort_alpha"}


def lib_sorts(chat_id) -> dict:
    return {k: t(chat_id, key) for k, key in LIB_SORT_KEYS.items()}


async def render_library(msg: Message, chat_id: int, page: int = 0, sort: str = "r"):
    """Paginated personal library. Every entry resends instantly from cache."""
    back = InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_music")]])
    if history_col is None:
        return await msg.edit_text(t(chat_id, "lib_unavailable"), reply_markup=back)
    try:
        docs = list(history_col.find({"chat_id": int(chat_id)}).sort("ts", -1).limit(1000))
    except Exception as e:
        print(f"Library error: {e}", flush=True)
        return await msg.edit_text(t(chat_id, "lib_read_error"), reply_markup=back)

    # Collapse repeats into one row, keeping a play count and the latest time.
    merged = {}
    for d in docs:
        vid = d.get("vid_id")
        if not vid:
            continue
        cur = merged.get(vid)
        if cur:
            cur["count"] += 1
            cur["ts"] = max(cur["ts"], float(d.get("ts") or 0))
        else:
            merged[vid] = {"vid_id": vid, "title": str(d.get("title") or ""),
                           "artist": str(d.get("artist") or ""),
                           "ts": float(d.get("ts") or 0), "count": 1}
    items = list(merged.values())
    if not items:
        return await msg.edit_text(t(chat_id, "lib_empty"), reply_markup=back)

    sort = sort if sort in LIB_SORTS else "r"
    if sort == "c":
        items.sort(key=lambda x: (x["count"], x["ts"]), reverse=True)
    elif sort == "a":
        items.sort(key=lambda x: x["title"].lower())
    else:
        items.sort(key=lambda x: x["ts"], reverse=True)

    pages = max(1, (len(items) + LIB_PAGE_SIZE - 1) // LIB_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    chunk = items[page * LIB_PAGE_SIZE:(page + 1) * LIB_PAGE_SIZE]
    # libget_<index> indexes this ordering, so store it as displayed.
    user_data.setdefault(chat_id, {})["library"] = [d["vid_id"] for d in items]

    total_plays = sum(d["count"] for d in items)
    modes = lib_sorts(chat_id)
    lines = [t(chat_id, "lib_header"),
             t(chat_id, "lib_meta", count=len(items), plays=total_plays, sort=modes[sort], page=page+1, pages=pages) + "\n"]
    btns = []
    for i, d in enumerate(chunk):
        gi = page * LIB_PAGE_SIZE + i
        rep = f"  ×{d['count']}" if d["count"] > 1 else ""
        lines.append(f"`{gi+1}` **{d['title'][:38]}**{rep}")
        lines.append(f"       👤 {d['artist'][:26]}\n")
        btns.append(InlineKeyboardButton(str(gi + 1), callback_data=f"libget_{gi}"))

    rows = [btns[j:j + 4] for j in range(0, len(btns), 4)]
    rows.append([InlineKeyboardButton(f"‹{lbl}›" if k == sort else lbl,
                                      callback_data=f"libp_0_{k}")
                 for k, lbl in modes.items()])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(t(chat_id, "btn_prev"), callback_data=f"libp_{page-1}_{sort}"))
    nav.append(InlineKeyboardButton(f"· {page+1}/{pages} ·", callback_data="noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(t(chat_id, "btn_next"), callback_data=f"libp_{page+1}_{sort}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(t(chat_id, "lib_btn_mixtape"), callback_data="mixtape_now"),
                 InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_music")])
    await msg.edit_text("\n".join(lines)[:4000], reply_markup=InlineKeyboardMarkup(rows))


async def render_stats(msg: Message, chat_id: int):
    """Personal listening stats — the shareable 'Wrapped' view."""
    back = InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_music")]])
    if history_col is None:
        return await msg.edit_text(t(chat_id, "stats_unavailable"), reply_markup=back)
    try:
        docs = list(history_col.find({"chat_id": int(chat_id)}).limit(2000))
    except Exception as e:
        print(f"Stats error: {e}", flush=True)
        return await msg.edit_text(t(chat_id, "stats_read_error"), reply_markup=back)
    if not docs:
        return await msg.edit_text(t(chat_id, "stats_empty"), reply_markup=back)
    unknown = t(chat_id, "stats_unknown")
    artists = Counter(str(d.get("artist") or unknown) for d in docs).most_common(5)
    titles = Counter(str(d.get("title") or unknown) for d in docs).most_common(5)
    week = sum(1 for d in docs if (time.time() - float(d.get("ts") or 0)) < 604800)
    times_word = t(chat_id, "stats_times_suffix")
    lines = [t(chat_id, "stats_header"),
             t(chat_id, "stats_total_downloads", count=len(docs)),
             t(chat_id, "stats_last_7_days", count=week),
             t(chat_id, "stats_unique_artists", count=len(set(d.get('artist') for d in docs))),
             t(chat_id, "stats_top_artists")]
    for i, (a, c) in enumerate(artists, 1):
        lines.append(f"{i}. {a[:32]} — {c} {times_word}")
    lines.append(t(chat_id, "stats_top_songs"))
    for i, (song, c) in enumerate(titles, 1):
        lines.append(f"{i}. {song[:38]} — {c} {times_word}")
    await msg.edit_text("\n".join(lines)[:4000], reply_markup=back)


async def render_lyric_search(msg: Message, fragment: str, chat_id: int):
    """Find a song from a remembered line.

    Uses the YouTube index rather than a lyrics database: lrclib's search only
    matches title/artist metadata, whereas YouTube reliably surfaces songs from
    a quoted line. Two stages, merged rather than replaced: first IDENTIFY the
    song from the raw lyric query (unhinted YouTube search is already good at
    this), then RE-RESOLVE with that title/artist as hints so the official-
    audio/explicit ranking and the video-avoidance rule actually apply.
    Merging instead of discarding the seed matters -- the identification step
    itself can occasionally lock onto a cover/tribute upload rather than the
    real original artist, and merging keeps the real one visible as another
    numbered option instead of silently vanishing.
    """
    back = InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_music")]])
    try:
        seed = await asyncio.to_thread(search_official_smart_sync, f"{fragment} lyrics")
        if not seed:
            return await msg.edit_text(t(chat_id, "lyric_no_match"), reply_markup=back)

        top = seed[0]
        artist_hint, title_hint = top.get("artist"), top.get("title")
        results = seed
        if artist_hint and title_hint:
            resolved = await asyncio.to_thread(
                search_official_smart_sync, f"{artist_hint} {title_hint}", 20, artist_hint, title_hint)
            if resolved:
                # Only the best couple of re-resolved candidates get
                # prepended -- taking the whole hinted list risked flooding
                # the results with one artist's entire unrelated catalog
                # whenever stage 1 misidentified a cover/tribute act (their
                # other songs still score decently on artist-match alone),
                # burying the real original further down than before.
                top_resolved = resolved[:2]
                seen_ids = {r["id"] for r in top_resolved}
                merged = top_resolved + [e for e in seed if e["id"] not in seen_ids]
                merged.sort(key=lambda x: (x["is_video"], -x["score"]))
                results = merged

        store = user_data.setdefault(chat_id, {})
        store["official_all"] = results
        store["last_music_query"] = f"🔎 {fragment[:40]}"
        await render_official_page(msg, chat_id, page=0, sort="r")
    except Exception as e:
        print(f"Lyric search error: {e}", flush=True)
        await msg.edit_text(t(chat_id, "sc_search_error"), reply_markup=back)


# ---------------- 6.55 ADMIN CORE: settings, users, activity, gatekeeper ----------------
SETTINGS_CACHE = {"data": {}, "ts": 0.0}
SETTINGS_TTL = 20  # seconds; keeps the gatekeeper off Mongo on every update

# --- hot-path caches ---------------------------------------------------------
# The gatekeeper runs on EVERY update. Uncached it cost a Telegram round-trip
# per forced-join channel plus several Mongo queries for every single tap,
# which is what made the bot feel sluggish after the admin panel landed.
MEMBER_OK = {}        # user_id -> timestamp until which membership is trusted
# Deliberately tiny. Telegram does NOT reliably push member updates for
# broadcast channels, so the chat_member listener cannot be relied on and this
# TTL is the real enforcement window. It only exists to absorb bursts (a user
# tapping several buttons in a row), not to grant lasting access.
MEMBER_TTL = 30
BAN_CACHE = {}        # chat_id -> (banned, expiry)
BAN_TTL = 120
TOUCH_LAST = {}       # chat_id -> last profile write
TOUCH_INTERVAL = 180
PEER_WARMED = {}      # channel key -> resolved chat id (peer met this process)


def get_settings() -> dict:
    """Bot settings with a short TTL cache (the gatekeeper reads these constantly)."""
    now = time.time()
    if now - SETTINGS_CACHE["ts"] < SETTINGS_TTL:
        return SETTINGS_CACHE["data"]
    data = {}
    if settings_col is not None:
        try:
            for doc in settings_col.find({}):
                data[doc.get("key")] = doc.get("value")
        except Exception as e:
            print(f"settings read error: {e}", flush=True)
            return SETTINGS_CACHE["data"]
    if "force_channels" not in data:
        # Seed once from env; after that the panel's DB copy is authoritative.
        seeded = []
        for tok in re.split(r"[,\s]+", FORCE_CHANNELS_ENV):
            tok = tok.strip()
            if not tok:
                continue
            # A private t.me/+HASH link has no @username, so `chat` stays empty
            # and gets resolved to a numeric id on first use.
            seeded.append({"chat": tok if not tok.startswith("http") else "",
                           "link": tok if tok.startswith("http") else ""})
        if not seeded and REQUIRED_CHANNEL:
            seeded = [{"chat": REQUIRED_CHANNEL, "link": CHANNEL_INVITE_LINK}]
        if seeded:
            data["force_channels"] = seeded
    SETTINGS_CACHE["data"], SETTINGS_CACHE["ts"] = data, now
    return data


def set_setting(key: str, value):
    if settings_col is None:
        return False
    try:
        settings_col.update_one({"key": key}, {"$set": {"value": value}}, upsert=True)
        SETTINGS_CACHE["ts"] = 0.0  # force refresh
        return True
    except Exception as e:
        print(f"settings write error: {e}", flush=True)
        return False


def _sync_touch_user(user_data: dict, chat_id: int):
    if users_col is None:
        return
    try:
        users_col.update_one(
            {"chat_id": int(chat_id)},
            {"$set": {"username": user_data.get("username"),
                      "first_name": user_data.get("first_name"),
                      "last_seen": time.time()},
             "$setOnInsert": {"joined_at": time.time(), "banned": False},
             "$inc": {"events": 1}},
            upsert=True)
    except Exception as e:
        print(f"touch_user error: {e}", flush=True)


def touch_user(user, chat_id: int):
    """Upsert the user record so the admin panel has someone to show.

    Throttled and offloaded to background pool so no Mongo round-trip stalls the hot path.
    """
    if users_col is None:
        return
    now = time.time()
    if now - TOUCH_LAST.get(int(chat_id), 0) < TOUCH_INTERVAL:
        return
    TOUCH_LAST[int(chat_id)] = now
    u_data = {
        "username": getattr(user, "username", None),
        "first_name": getattr(user, "first_name", None)
    }
    _DB_BG_POOL.submit(_sync_touch_user, u_data, int(chat_id))


def _sync_log_activity(chat_id: int, kind: str, detail: str):
    if activity_col is None:
        return
    try:
        activity_col.insert_one({
            "chat_id": int(chat_id), "kind": kind,
            "detail": str(detail)[:300], "ts": time.time(),
            "at": datetime.now(timezone.utc),
        })
    except Exception as e:
        print(f"activity log error: {e}", flush=True)


def log_activity(chat_id: int, kind: str, detail: str):
    """Full activity trail: searches, links, button presses, commands.
    Offloaded to background thread pool to keep the event loop non-blocking.
    """
    if activity_col is None:
        return
    _DB_BG_POOL.submit(_sync_log_activity, int(chat_id), str(kind), str(detail))


def is_banned(chat_id: int) -> bool:
    """Cached ban lookup; the panel clears the entry on ban/unban so the
    decision still takes effect immediately."""
    if users_col is None:
        return False
    key = int(chat_id)
    now = time.time()
    hit = BAN_CACHE.get(key)
    if hit and hit[1] > now:
        return hit[0]
    try:
        doc = users_col.find_one({"chat_id": key}, {"banned": 1})
        val = bool(doc and doc.get("banned"))
    except Exception:
        val = False
    BAN_CACHE[key] = (val, now + BAN_TTL)
    return val


def channel_refs(ch: dict) -> list:
    """Candidate references Pyrogram can actually resolve, best first.

    A public link like https://ble.ir/name is NOT usable as-is: Pyrogram only
    special-cases invite links (t.me/+hash, /joinchat/), and otherwise tries to
    resolve the whole URL as a username, which fails. The @name has to be
    extracted. A bare numeric -100... id only works once the peer has been met,
    so it goes last.
    """
    refs = []
    chat = str(ch.get("chat") or "").strip()
    link = str(ch.get("link") or "").strip()

    m = re.match(r"^(?:https?://)?(?:www\.)?t\.me/([A-Za-z][A-Za-z0-9_]{3,31})/?$", link)
    if m:
        refs.append("@" + m.group(1))
    if chat.startswith("@"):
        refs.append(chat)
    if re.search(r"t\.me/(\+|joinchat/)", link):
        refs.append(link)
    if chat and not chat.startswith("@"):
        refs.append(chat)

    seen, out = set(), []
    for r in refs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


async def resolve_channel(client: Client, ch: dict):
    """Resolve a channel to a reference usable with get_chat_member.

    Tries each candidate until get_chat() succeeds, which both validates access
    AND makes Pyrogram 'meet' the peer — required before the numeric id works.
    The resolved id is cached per process and persisted with the title.
    Returns None when the bot genuinely cannot reach the channel.
    """
    key = str(ch.get("chat") or ch.get("link") or "")
    if not key:
        return None
    cached = PEER_WARMED.get(key)
    if cached:
        return cached

    tried, last_err = [], None
    for cand in channel_refs(ch):
        tried.append(cand)
        try:
            chat = await client.get_chat(cand)
        except Exception as e:
            last_err = e
            continue
        PEER_WARMED[key] = chat.id
        chans = list(get_settings().get("force_channels") or [])
        changed = False
        for c in chans:
            if str(c.get("chat") or "") == str(ch.get("chat") or "") and c.get("link") == ch.get("link"):
                if str(c.get("chat")) != str(chat.id) or not c.get("title"):
                    c["chat"] = str(chat.id)
                    c["title"] = getattr(chat, "title", None) or c.get("title") or ""
                    changed = True
        if changed:
            set_setting("force_channels", chans)
        print(f"✅ force-join resolved {key} via {cand} -> {chat.id}", flush=True)
        return chat.id

    print(f"⚠️ force-join: cannot access {key}; tried {tried}; last error: {last_err}", flush=True)
    return None


async def missing_force_channels(client: Client, user_id: int):
    """Return the forced-join channels this user has NOT joined.

    Fails open: if membership cannot be checked (bot not admin there, bad id),
    the user is let through rather than locked out of the whole bot.
    """
    # Safety interlock: with no ADMIN_ID configured nobody can bypass the gate,
    # so a misconfigured channel would lock the owner out of their own bot.
    if not ADMIN_ID:
        return []
    if is_force_join_exempt(user_id):
        return []
    channels = (get_settings().get("force_channels") or [])
    if not channels:
        return []

    # Once a user is confirmed joined, trust it for a while instead of hitting
    # Telegram on every message and button press.
    now = time.time()
    cached_until = MEMBER_OK.get(int(user_id), 0)
    if cached_until > now:
        print(f"🔎 gate uid={user_id}: CACHED pass ({int(cached_until - now)}s left)", flush=True)
        return []

    missing, seen = [], []
    for ch in channels:
        target = await resolve_channel(client, ch)
        if not target:
            seen.append("unresolved")
            continue
        try:
            member = await client.get_chat_member(target, user_id)
            status = getattr(member.status, "name", str(member.status))
            seen.append(f"{target}={status}")
            if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
                missing.append(ch)
        except UserNotParticipant:
            seen.append(f"{target}=NOT_PARTICIPANT")
            missing.append(ch)
        except Exception as e:
            # Previously this failed open silently, which let everyone through
            # whenever a check errored. Count it as missing instead: a wrong
            # prompt is recoverable, a bypassed gate is not.
            seen.append(f"{target}=ERR({type(e).__name__})")
            print(f"force-join check failed for {target}: {e}", flush=True)
            missing.append(ch)
    print(f"🔎 gate uid={user_id}: {' | '.join(seen)} -> "
          f"{'BLOCKED (' + str(len(missing)) + ' missing)' if missing else 'ALLOWED'}", flush=True)
    if not missing:
        MEMBER_OK[int(user_id)] = now + MEMBER_TTL
    return missing


def force_join_kb(missing: list, chat_id=None) -> InlineKeyboardMarkup:
    rows = []
    join_word = t(chat_id, "join_btn_prefix") if chat_id is not None else "📢 عضویت در"
    for i, ch in enumerate(missing, 1):
        link = ch.get("link") or ""
        target = str(ch.get("chat") or "")
        if not link and target.startswith("@"):
            link = f"https://ble.ir/{target[1:]}"
        # Never label a button with a raw -100... id.
        label = ch.get("title") or (target if target.startswith("@") else f"#{i}")
        if link:
            rows.append([InlineKeyboardButton(f"{join_word} {label}", url=link)])
    rows.append([InlineKeyboardButton(t(chat_id, "joined_btn") if chat_id is not None else "✅ عضو شدم", callback_data="check_join")])
    return InlineKeyboardMarkup(rows)


# Inline picks made INSIDE the bot's own chat: Telegram also delivers the sent
# inline message to us as a private message "via @bot". There the user should
# get the real track with the full actions keyboard (lyrics, effects, playlist
# ...), not the bare inline audio. chosen_inline_handler records which result
# the user picked; this handler deletes the inline card and delivers properly.
_LAST_INLINE_PICK = {}   # user id -> (result id, ts)


async def _wait_inline_pick(uid: int, since: float, timeout: float = 8.0):
    end = time.time() + timeout
    while time.time() < end:
        hit = _LAST_INLINE_PICK.get(uid)
        if hit and hit[1] >= since - 30:
            _LAST_INLINE_PICK.pop(uid, None)
            return hit[0]
        await asyncio.sleep(0.25)
    return None


@bot.on_message(filters.private & filters.via_bot, group=-3)
async def cleanup_inline_pm_message(client: Client, message: Message):
    via = getattr(message, "via_bot", None)
    me_id = getattr(client.me, "id", None)
    if not via or (me_id and via.id != me_id):
        return
    chat_id = message.chat.id
    try:
        await message.delete()
    except Exception:
        pass
    rid = await _wait_inline_pick(chat_id, time.time())
    if rid:
        is_cached = rid.startswith("c.")
        meta = await _meta_for_rid(rid[2:] if is_cached else rid)
        if meta:
            await deliver_track(client, chat_id, meta, activity="music_inline_pm")
    raise StopPropagation


@bot.on_message(filters.private, group=-2)
async def gatekeeper_message(client: Client, message: Message):
    """Runs before every other message handler: logs, bans, maintenance, join gate."""
    chat_id = message.chat.id
    user = message.from_user
    uid = user.id if user else chat_id
    text = (message.text or message.caption or "")[:300]

    # Referral credit: only checked for an actual "/start ref_<code>" payload
    # (rare path), never on regular traffic, so this costs nothing on the hot
    # path. Must run before touch_user, which is what creates the user record
    # -- crediting after that would always see an "existing" user.
    if text.startswith("/start ref_") and users_col is not None and smallz_col is not None:
        code = text.split("ref_", 1)[1].strip().split()[0]
        try:
            is_new_user = users_col.find_one({"chat_id": chat_id}, {"_id": 1}) is None
        except Exception:
            is_new_user = False
        if is_new_user and code.lstrip("-").isdigit() and int(code) != chat_id:
            try:
                smallz_col.update_one({"chat_id": int(code)}, {"$inc": {"referrals": 1}})
            except Exception as e:
                print(f"referral credit error: {e}", flush=True)

    touch_user(user, chat_id)
    # Log what people actually search for and the links they send — not every
    # command or tap. Button presses were pure noise and cost a Mongo write per
    # click, which is what slowed the bot down.
    if text and not text.startswith("/"):
        log_activity(chat_id, "link" if "http" in text.lower() else "search", text)

    if is_admin(uid):
        return  # owner bypasses every gate

    if is_banned(chat_id):
        await message.reply_text(t(chat_id, "banned_msg"))
        raise StopPropagation

    if get_settings().get("maintenance") and not is_maintenance_allowed(chat_id):
        await message.reply_text(t(chat_id, "maintenance_msg"))
        raise StopPropagation

    if not is_approved(uid):
        await deny_unapproved_message(client, message, uid)
        raise StopPropagation

    missing = await missing_force_channels(client, uid)
    if missing:
        await message.reply_text(t(chat_id, "force_join_msg"), reply_markup=force_join_kb(missing, chat_id))
        raise StopPropagation


@bot.on_chat_member_updated()
async def on_membership_change(client: Client, update):
    """Revoke a cached membership pass the instant someone leaves a channel.

    Without this the gate only re-checks when the cache expires, so a user who
    joined, passed, then left kept working until the TTL ran out. Telegram
    pushes this update for any chat where the bot is an admin.
    """
    try:
        member = getattr(update, "new_chat_member", None)
        user = getattr(member, "user", None)
        if not member or not user:
            return

        # The bot's own membership changing (added/removed/promoted somewhere)
        # -- tracked separately so the admin panel can show every channel/group
        # the bot has ever been added to, independent of the force-join list.
        if getattr(user, "is_self", False):
            if bot_chats_col is not None:
                chat = update.chat
                active = member.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
                try:
                    bot_chats_col.update_one(
                        {"chat_id": chat.id},
                        {"$set": {"title": chat.title or chat.first_name or str(chat.id),
                                  "type": str(chat.type), "active": active,
                                  "status": str(member.status), "ts": time.time()},
                         "$setOnInsert": {"first_seen": time.time()}},
                        upsert=True)
                except Exception as e:
                    print(f"bot_chats log error: {e}", flush=True)
            return

        if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
            uid = int(user.id)
            # Invalidate regardless of which chat it was: re-checking costs one
            # API call, whereas wrongly keeping access defeats the whole gate.
            if MEMBER_OK.pop(uid, None) is not None:
                print(f"🚪 user {uid} left chat {update.chat.id}; access revoked", flush=True)
    except Exception as e:
        print(f"chat_member update error: {e}", flush=True)


@bot.on_inline_query(group=-2)
async def gatekeeper_inline(client: Client, iq: InlineQuery):
    """Inline mode bypassed every gate, so a non-member could still use the bot
    from any chat. Apply the same ban / maintenance / join checks here."""
    uid = iq.from_user.id if iq.from_user else 0
    if is_admin(uid):
        return

    async def deny(text):
        try:
            await iq.answer([], cache_time=3, is_personal=True,
                            switch_pm_text=text, switch_pm_parameter="start")
        except Exception:
            pass
        raise StopPropagation

    if uid and is_banned(uid):
        await deny(t(uid, "inline_deny_banned"))
    if get_settings().get("maintenance") and not is_maintenance_allowed(uid):
        await deny(t(uid, "inline_deny_maintenance"))
    if not is_approved(uid):
        await deny("🔐 نیاز به تأیید مدیر")
    if uid and await missing_force_channels(client, uid):
        await deny(t(uid, "inline_deny_join"))


@bot.on_callback_query(group=-2)
async def gatekeeper_callback(client: Client, query: CallbackQuery):
    chat_id = query.message.chat.id if query.message else None
    user = query.from_user
    uid = user.id if user else chat_id
    if chat_id:
        touch_user(user, chat_id)
        # Deliberately NOT logging button presses: they were noise in the feed
        # and a Mongo write on every tap.

    if is_admin(uid) or query.data == "check_join":
        return

    if chat_id and is_banned(chat_id):
        await query.answer(t(chat_id or uid, "banned_msg"), show_alert=True)
        raise StopPropagation

    if get_settings().get("maintenance") and not is_maintenance_allowed(chat_id or uid):
        await query.answer(t(chat_id or uid, "maintenance_msg"), show_alert=True)
        raise StopPropagation

    if not is_approved(uid):
        await query.answer(f"🔐 برای استفاده از ربات باید مدیر دسترسی شما را تأیید کند.\nآیدی شما: {uid}\nبه @{ADMIN_CONTACT} پیام بدهید.",
                           show_alert=True)
        raise StopPropagation

    missing = await missing_force_channels(client, uid)
    if missing:
        await query.answer(t(chat_id or uid, "inline_deny_force_join"), show_alert=True)
        raise StopPropagation


# ---------------- 6.6 SUBSCRIPTIONS & WEEKLY MIXTAPE ----------------
try:
    SUBS_POLL_MINUTES = max(5, int(os.environ.get("SUBS_POLL_MINUTES", "60")))
except ValueError:
    SUBS_POLL_MINUTES = 30
# Set false on a secondary deploy that shares the same database, so only one
# instance pushes subscriptions and mixtapes.
ENABLE_BACKGROUND_WORKER = os.environ.get(
    "ENABLE_BACKGROUND_WORKER", "true").strip().lower() not in ("false", "0", "no")


def parse_subscription_target(text: str):
    """Turn a pasted link or @handle into (platform, target, label)."""
    t = text.strip()
    low = t.lower()
    rd = re.search(r"(?:reddit\.com/)?\br/([A-Za-z0-9_]{2,21})\b", t)
    if rd and ("reddit" in low or low.startswith("r/") or low.startswith("/r/")):
        return ("rd", rd.group(1), f"Reddit r/{rd.group(1)}")
    if "twitter.com" in low or "x.com/" in low:
        return None  # X has no free timeline API any more; links still download
    if "instagram.com" in low:
        user = low.split("instagram.com/")[-1].split("/")[0].split("?")[0]
        if user in ("p", "reel", "reels", "tv", "stories", "explore", "accounts", ""):
            return None  # a post link, not a profile
        return ("ig", user, f"Instagram @{user}")
    if "tiktok.com" in low:
        seg = low.split("tiktok.com/")[-1].split("/")[0].split("?")[0]
        user = seg.lstrip("@")
        return ("tt", user, f"TikTok @{user}") if user else None
    if "youtube.com" in low or "youtu.be" in low:
        m = re.search(r"/channel/([A-Za-z0-9_-]{10,})", t)
        if m:
            return ("yt", m.group(1), "YouTube channel")
        handle = re.search(r"youtube\.com/@([A-Za-z0-9._-]+)", t)
        if handle:
            found = yt_api("search", {"part": "snippet", "type": "channel",
                                      "maxResults": 1, "q": handle.group(1)})
            items = found.get("items") or []
            if items:
                return ("yt", items[0]["id"]["channelId"],
                        f"YouTube: {items[0]['snippet']['title']}")
        return None
    if t.startswith("@"):
        return ("tt", t[1:], f"TikTok @{t[1:]}")
    # Bare text: treat as a YouTube channel search.
    found = yt_api("search", {"part": "snippet", "type": "channel", "maxResults": 1, "q": t})
    items = found.get("items") or []
    if items:
        return ("yt", items[0]["id"]["channelId"], f"YouTube: {items[0]['snippet']['title']}")
    return None


def latest_post_sync(platform: str, target: str):
    """Return (id, title) of the newest item for a subscription, or None."""
    try:
        if platform == "yt":
            res = yt_api("search", {"channelId": target, "part": "snippet",
                                    "type": "video", "order": "date", "maxResults": 1})
            items = res.get("items") or []
            if items:
                return items[0]["id"]["videoId"], items[0]["snippet"]["title"]
            return None
        if platform == "rd":
            posts = reddit_subreddit_posts_sync(target, 3, "new")
            post = next((p_ for p_ in posts if not p_.get("stickied")), None)
            return (post["id"], (post.get("title") or "")[:80]) if post else None
        if platform == "ig" and target in ("p", "reel", "reels", "tv", "stories", "explore"):
            return None  # legacy bad subscription saved from a post link
        url = (f"https://www.instagram.com/{target}/" if platform == "ig"
               else f"https://www.tiktok.com/@{target}")
        opts = {'quiet': True, 'no_warnings': True, 'extract_flat': True,
                'playlist_items': '1', 'ignore_no_formats_error': True}
        if platform == "tt":
            opts['impersonate'] = ImpersonateTarget.from_str('chrome')
        elif os.path.exists(INSTAGRAM_COOKIES_FILE) and os.path.getsize(INSTAGRAM_COOKIES_FILE) > 0:
            opts['cookiefile'] = INSTAGRAM_COOKIES_FILE
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        entries = info.get('entries') or []
        if entries and entries[0]:
            e = entries[0]
            return str(e.get('id')), str(e.get('title') or e.get('description') or '')[:80]
    except Exception as e:
        print(f"latest_post({platform}/{target}) failed: {e}", flush=True)
    return None


STRINGS.update({
    "sub_new_video_caption": {"fa": "🔔 **ویدیوی جدید**\n{label}\n\n🎬 {title}", "en": "🔔 **New Video**\n{label}\n\n🎬 {title}", "ru": "🔔 **Новое видео**\n{label}\n\n🎬 {title}", "es": "🔔 **Nuevo video**\n{label}\n\n🎬 {title}"},
    "sub_btn_download_video": {"fa": "⬇️ دانلود این ویدیو", "en": "⬇️ Download This Video", "ru": "⬇️ Скачать это видео", "es": "⬇️ Descargar este video"},
    "sub_new_post": {"fa": "🔔 پست جدید از {label}", "en": "🔔 New post from {label}", "ru": "🔔 Новый пост от {label}", "es": "🔔 Nueva publicación de {label}"},
    "generic_cancelled": {"fa": "❌ لغو شد.", "en": "❌ Cancelled.", "ru": "❌ Отменено.", "es": "❌ Cancelado."},
    "ai_downloading_songs": {"fa": "⏳ در حال دانلود {count} آهنگ...", "en": "⏳ Downloading {count} songs...", "ru": "⏳ Загрузка {count} песен...", "es": "⏳ Descargando {count} canciones..."},
    "yt_keyword_prompt": {"fa": "🔍 لطفاً نام ویدیو یا کلمه کلیدی خود را تایپ کرده و ارسال کنید:", "en": "🔍 Please type and send the video name or keyword:", "ru": "🔍 Введите и отправьте название видео или ключевое слово:", "es": "🔍 Escribe y envía el nombre del video o la palabra clave:"},
    "yt_fetching_channel_videos": {"fa": "⏳ در حال دریافت ویدیوهای کانال...", "en": "⏳ Fetching channel videos...", "ru": "⏳ Получение видео канала...", "es": "⏳ Obteniendo videos del canal..."},
    "yt_channel_latest_videos": {"fa": "📺 **جدیدترین ویدیوهای این کانال:**\n\n", "en": "📺 **Latest videos from this channel:**\n\n", "ru": "📺 **Последние видео этого канала:**\n\n", "es": "📺 **Últimos videos de este canal:**\n\n"},
    "yt_connection_error": {"fa": "❌ خطا در ارتباط با یوتیوب.", "en": "❌ Error connecting to YouTube.", "ru": "❌ Ошибка соединения с YouTube.", "es": "❌ Error al conectar con YouTube."},
    "mixtape_private_only": {"fa": "🎁 میکس‌تیپ فقط در چت خصوصی با ربات در دسترس است.", "en": "🎁 The mixtape is only available in a private chat with the bot.", "ru": "🎁 Микстейп доступен только в личном чате с ботом.", "es": "🎁 El mixtape solo está disponible en un chat privado con el bot."},
    "mixtape_building": {"fa": "🎁 در حال ساخت میکس‌تیپ...", "en": "🎁 Building your mixtape...", "ru": "🎁 Создание микстейпа...", "es": "🎁 Generando tu mixtape..."},
    "mixtape_not_enough_songs": {
        "fa": "🎁 هنوز آهنگ کافی برای ساخت میکس‌تیپ نداریم.\nکمی بیشتر از ربات استفاده کنید تا سلیقه‌تان را یاد بگیرد.",
        "en": "🎁 We don't have enough songs yet to build a mixtape.\nUse the bot a bit more so it can learn your taste.",
        "ru": "🎁 У нас пока недостаточно песен для создания микстейпа.\nПользуйтесь ботом немного больше, чтобы он изучил ваши вкусы.",
        "es": "🎁 Aún no tenemos suficientes canciones para crear un mixtape.\nUsa el bot un poco más para que aprenda tus gustos.",
    },
    "yt_channel_prompt": {"fa": "📺 لطفاً نام کانال یوتیوب را بفرستید:", "en": "📺 Please send the YouTube channel name:", "ru": "📺 Отправьте название канала YouTube:", "es": "📺 Envía el nombre del canal de YouTube:"},
    "sub_receiving": {"fa": "⏳ در حال دریافت...", "en": "⏳ Fetching...", "ru": "⏳ Получение...", "es": "⏳ Obteniendo..."},
})

async def poll_subscriptions(client: Client):
    """Check every subscription once and push anything new."""
    if subs_col is None:
        return
    try:
        subs = list(subs_col.find({}).limit(300))
    except Exception as e:
        print(f"subs read error: {e}", flush=True)
        return
    for s in subs:
        platform, target, chat_id = s.get("platform"), s.get("target"), s.get("chat_id")
        if not platform or not target or not chat_id:
            continue
        try:
            latest = await asyncio.to_thread(latest_post_sync, platform, target)
            if not latest:
                continue
            new_id, title = latest
            if not new_id or new_id == s.get("last_id"):
                continue
            subs_col.update_one({"_id": s["_id"]}, {"$set": {"last_id": new_id}})
            if s.get("last_id") is None:
                continue  # First sight: record a baseline, don't spam history.

            if platform == "yt":
                # YouTube downloads are the expensive path, so notify with a
                # one-tap button instead of auto-downloading every upload.
                await client.send_photo(
                    chat_id, f"https://i.ytimg.com/vi/{new_id}/hqdefault.jpg",
                    caption=t(chat_id, "sub_new_video_caption", label=s.get('label',''), title=title[:120]),
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton(t(chat_id, "sub_btn_download_video"),
                                               callback_data=f"dlyt_{new_id}")]]))
            else:
                await client.send_message(chat_id, t(chat_id, "sub_new_post", label=s.get('label','')))
                status = await client.send_message(chat_id, t(chat_id, "sub_receiving"))
                if platform == "rd":
                    await deliver_reddit_post(client, chat_id, status, new_id)
                elif platform == "ig":
                    await process_instagram_render(
                        client, chat_id, status, f"https://www.instagram.com/p/{new_id}/")
                else:
                    await process_tiktok_render(
                        client, chat_id, status,
                        f"https://www.tiktok.com/@{target}/video/{new_id}")
        except Exception as e:
            print(f"sub push failed ({platform}/{target}): {e}", flush=True)
        await asyncio.sleep(2)  # be gentle on a 0.1-CPU instance


async def send_weekly_mixtape(client: Client, chat_id: int):
    """A personal mixtape built ONLY from already-cached tracks.

    Cached-only keeps it instant and free of extraction cost, and it gets
    richer as the shared library grows.
    """
    if history_col is None or music_cache is None:
        return False
    try:
        docs = list(history_col.find({"chat_id": int(chat_id)}).limit(500))
        if len(docs) < 3:
            return False
        owned = {d.get("vid_id") for d in docs}
        top_artists = [a for a, _ in Counter(
            str(d.get("artist") or "") for d in docs if d.get("artist")).most_common(3)]
        picks = []
        for artist in top_artists:
            for doc in music_cache.find(
                    {"search_text": {"$regex": re.escape(artist.lower()), "$options": "i"}}).limit(12):
                if doc.get("vid_id") in owned or not doc.get("file_id"):
                    continue
                picks.append(doc)
                if len(picks) >= 6:
                    break
            if len(picks) >= 6:
                break
        if not picks:
            return False
        await client.send_message(
            chat_id, f"🎁 **میکس‌تیپ هفتگی شما**\nبر اساس سلیقه‌تان، {len(picks)} آهنگ:")
        for doc in picks:
            await deliver_by_key(client, chat_id, doc.get("vid_id", ""),
                                 get_track_delivery_kb(doc.get("vid_id", ""), chat_id))
            await asyncio.sleep(1)
        return True
    except Exception as e:
        print(f"mixtape error: {e}", flush=True)
        return False


async def background_worker(client: Client):
    """Single background loop: subscription polling.

    Weekly mixtapes used to be auto-pushed here to every chat_id that ever
    appeared in history_col -- which, once groups started using the bot,
    included group chats with no opt-in at all (the reported "spams random
    users" bug). Mixtape is now strictly on-demand: the mixtape_now callback
    only, private chats only. No automatic push exists anymore.
    """
    await asyncio.sleep(90)  # let the bot finish booting
    print(f"🔔 background worker started (every {SUBS_POLL_MINUTES}m)", flush=True)
    while True:
        try:
            await poll_subscriptions(client)
            await asyncio.to_thread(cleanup_stale_video_edits)
        except Exception:
            print(f"background worker error:\n{traceback.format_exc()}", flush=True)
        await asyncio.sleep(SUBS_POLL_MINUTES * 60)


STRINGS.update({
    "subs_unavailable": {"fa": "❌ اشتراک‌ها در دسترس نیست.", "en": "❌ Subscriptions aren't available.", "ru": "❌ Подписки недоступны.", "es": "❌ Las suscripciones no están disponibles."},
    "subs_read_error": {"fa": "❌ خطا در خواندن اشتراک‌ها.", "en": "❌ Error reading subscriptions.", "ru": "❌ Ошибка чтения подписок.", "es": "❌ Error al leer las suscripciones."},
    "subs_header": {
        "fa": "🔔 **اشتراک‌های شما**\n\nپست‌های جدید به‌صورت خودکار برایتان ارسال می‌شود.\n\n",
        "en": "🔔 **Your Subscriptions**\n\nNew posts are automatically sent to you.\n\n",
        "ru": "🔔 **Ваши подписки**\n\nНовые посты автоматически отправляются вам.\n\n",
        "es": "🔔 **Tus suscripciones**\n\nLas publicaciones nuevas se te envían automáticamente.\n\n",
    },
    "subs_none_yet": {"fa": "_هنوز اشتراکی ندارید._\n", "en": "_You have no subscriptions yet._\n", "ru": "_У вас пока нет подписок._\n", "es": "_Aún no tienes suscripciones._\n"},
    "subs_btn_remove": {"fa": "🗑 حذف {n}", "en": "🗑 Remove {n}", "ru": "🗑 Удалить {n}", "es": "🗑 Eliminar {n}"},
    "creator_message_header": {"fa": "📩 **پیامی از سازنده ربات:**", "en": "📩 **A message from the bot's creator:**", "ru": "📩 **Сообщение от создателя бота:**", "es": "📩 **Un mensaje del creador del bot:**"},
    "creator_reply_header": {"fa": "📩 **پاسخ سازنده:**", "en": "📩 **The creator's reply:**", "ru": "📩 **Ответ создателя:**", "es": "📩 **Respuesta del creador:**"},
    "maintenance_on_announce": {
        "fa": "🛠 **ربات موقتاً برای به‌روزرسانی خاموش شد.**\n\nبه‌زودی با امکانات بهتر برمی‌گردیم. ممنون از صبر شما 🙏",
        "en": "🛠 **The bot is temporarily down for an update.**\n\nWe'll be back soon with better features. Thanks for your patience 🙏",
        "ru": "🛠 **Бот временно отключён для обновления.**\n\nМы скоро вернёмся с улучшениями. Спасибо за терпение 🙏",
        "es": "🛠 **El bot está temporalmente fuera de servicio por una actualización.**\n\nVolveremos pronto con mejores funciones. Gracias por tu paciencia 🙏",
    },
    "maintenance_off_announce": {
        "fa": "✅ **ربات دوباره فعال شد!**\n\nممنون که منتظر ماندید 🎉",
        "en": "✅ **The bot is back online!**\n\nThanks for waiting 🎉",
        "ru": "✅ **Бот снова в сети!**\n\nСпасибо за ожидание 🎉",
        "es": "✅ **¡El bot está de vuelta!**\n\nGracias por esperar 🎉",
    },
    "sub_expired": {"fa": "❌ این اشتراک منقضی شده است.", "en": "❌ This subscription has expired.", "ru": "❌ Эта подписка истекла.", "es": "❌ Esta suscripción ha caducado."},
    "subs_btn_add": {"fa": "➕ افزودن اشتراک", "en": "➕ Add Subscription", "ru": "➕ Добавить подписку", "es": "➕ Añadir suscripción"},
})

async def render_subs(msg: Message, chat_id: int):
    back = InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_downloader")]])
    if subs_col is None:
        return await msg.edit_text(t(chat_id, "subs_unavailable"), reply_markup=back)
    try:
        subs = list(subs_col.find({"chat_id": int(chat_id)}).limit(MAX_SUBS_PER_USER))
    except Exception:
        return await msg.edit_text(t(chat_id, "subs_read_error"), reply_markup=back)
    text = t(chat_id, "subs_header")
    rows = []
    if not subs:
        text += t(chat_id, "subs_none_yet")
    else:
        user_data.setdefault(chat_id, {})["subs"] = [str(s["_id"]) for s in subs]
        for i, s in enumerate(subs):
            text += f"{i+1}. {s.get('label','')}\n"
            rows.append([InlineKeyboardButton(t(chat_id, "subs_btn_remove", n=i+1), callback_data=f"unsub_{i}")])
    rows.append([InlineKeyboardButton(t(chat_id, "subs_btn_add"), callback_data="sub_add")])
    rows.append([InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_downloader")])
    await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(rows))


# ---------------- 6.65 PLAYLISTS ----------------
STRINGS.update({
    "playlists_unavailable": {"fa": "❌ پلی‌لیست‌ها در دسترس نیست.", "en": "❌ Playlists aren't available.", "ru": "❌ Плейлисты недоступны.", "es": "❌ Las listas de reproducción no están disponibles."},
    "playlists_header": {"fa": "📃 **پلی‌لیست‌های شما**", "en": "📃 **Your Playlists**", "ru": "📃 **Ваши плейлисты**", "es": "📃 **Tus listas de reproducción**"},
    "playlists_empty": {"fa": "_هنوز پلی‌لیستی نساخته‌اید._", "en": "_You haven't made any playlists yet._", "ru": "_У вас пока нет плейлистов._", "es": "_Aún no has creado ninguna lista._"},
    "playlists_btn_create": {"fa": "➕ ساخت پلی‌لیست جدید", "en": "➕ Create New Playlist", "ru": "➕ Создать новый плейлист", "es": "➕ Crear nueva lista"},
    "playlist_name_prompt": {"fa": "📝 نام پلی‌لیست را بفرستید:", "en": "📝 Send a name for the playlist:", "ru": "📝 Отправьте название плейлиста:", "es": "📝 Envía un nombre para la lista:"},
    "playlist_name_invalid": {"fa": "❌ نام نامعتبر است. یک نام متنی بفرستید.", "en": "❌ Invalid name. Send a text name.", "ru": "❌ Недействительное название. Отправьте текстовое название.", "es": "❌ Nombre no válido. Envía un nombre de texto."},
    "playlist_limit_reached": {"fa": "❌ حداکثر {max} پلی‌لیست مجاز است.", "en": "❌ Maximum of {max} playlists allowed.", "ru": "❌ Разрешено максимум {max} плейлистов.", "es": "❌ Se permite un máximo de {max} listas."},
    "playlist_created_confirm": {"fa": "✅ پلی‌لیست **{name}** ساخته شد.", "en": "✅ Playlist **{name}** created.", "ru": "✅ Плейлист **{name}** создан.", "es": "✅ Lista **{name}** creada."},
    "playlist_not_found": {"fa": "❌ پلی‌لیست یافت نشد.", "en": "❌ Playlist not found.", "ru": "❌ Плейлист не найден.", "es": "❌ No se encontró la lista."},
    "playlist_header": {"fa": "📃 **{name}**\n_{count} آهنگ_", "en": "📃 **{name}**\n_{count} songs_", "ru": "📃 **{name}**\n_{count} песен_", "es": "📃 **{name}**\n_{count} canciones_"},
    "playlist_empty_body": {"fa": "\n_هنوز آهنگی اضافه نشده. از دکمه «➕ افزودن به پلی‌لیست» زیر هر آهنگ استفاده کنید._", "en": "\n_No songs added yet. Use the \"➕ Add to Playlist\" button under any song._", "ru": "\n_Песни ещё не добавлены. Используйте кнопку «➕ Добавить в плейлист» под любой песней._", "es": "\n_Aún no se han añadido canciones. Usa el botón «➕ Añadir a la lista» debajo de cualquier canción._"},
    "playlist_page_meta": {"fa": "_صفحه {page} از {pages}_", "en": "_Page {page} of {pages}_", "ru": "_Стр. {page} из {pages}_", "es": "_Página {page} de {pages}_"},
    "playlist_btn_ai": {"fa": "🤖 آهنگ‌های مشابه", "en": "🤖 Similar Songs", "ru": "🤖 Похожие песни", "es": "🤖 Canciones similares"},
    "playlist_btn_radio": {"fa": "📻 حالت رادیو", "en": "📻 Radio Mode", "ru": "📻 Режим радио", "es": "📻 Modo radio"},
    "playlist_btn_rename": {"fa": "✏️ تغییر نام", "en": "✏️ Rename", "ru": "✏️ Переименовать", "es": "✏️ Renombrar"},
    "playlist_btn_delete": {"fa": "🗑 حذف پلی‌لیست", "en": "🗑 Delete Playlist", "ru": "🗑 Удалить плейлист", "es": "🗑 Eliminar lista"},
    "playlist_rename_prompt": {"fa": "📝 نام جدید پلی‌لیست را بفرستید:", "en": "📝 Send the playlist's new name:", "ru": "📝 Отправьте новое название плейлиста:", "es": "📝 Envía el nuevo nombre de la lista:"},
    "playlist_renamed_confirm": {"fa": "✅ نام پلی‌لیست به **{name}** تغییر کرد.", "en": "✅ Playlist renamed to **{name}**.", "ru": "✅ Плейлист переименован в **{name}**.", "es": "✅ Lista renombrada a **{name}**."},
    "playlist_delete_confirm_prompt": {"fa": "🗑 آیا از حذف پلی‌لیست **{name}** مطمئن هستید؟ این عمل قابل بازگشت نیست.", "en": "🗑 Are you sure you want to delete the playlist **{name}**? This can't be undone.", "ru": "🗑 Вы уверены, что хотите удалить плейлист **{name}**? Это действие нельзя отменить.", "es": "🗑 ¿Seguro que quieres eliminar la lista **{name}**? Esto no se puede deshacer."},
    "playlist_btn_confirm_delete": {"fa": "🗑 بله، حذف کن", "en": "🗑 Yes, delete it", "ru": "🗑 Да, удалить", "es": "🗑 Sí, eliminarla"},
    "playlist_deleted_confirm": {"fa": "✅ پلی‌لیست حذف شد.", "en": "✅ Playlist deleted.", "ru": "✅ Плейлист удалён.", "es": "✅ Lista eliminada."},
    "playlist_btn_remove_track": {"fa": "🗑 حذف از این پلی‌لیست", "en": "🗑 Remove from This Playlist", "ru": "🗑 Удалить из этого плейлиста", "es": "🗑 Quitar de esta lista"},
    "playlist_track_removed_confirm": {"fa": "✅ از پلی‌لیست حذف شد.", "en": "✅ Removed from the playlist.", "ru": "✅ Удалено из плейлиста.", "es": "✅ Quitado de la lista."},
    "playlist_ai_no_tracks": {"fa": "❌ ابتدا حداقل یک آهنگ به این پلی‌لیست اضافه کنید تا هوش مصنوعی بتواند سلیقه‌تان را بفهمد.", "en": "❌ Add at least one song to this playlist first so the AI can learn your taste.", "ru": "❌ Сначала добавьте хотя бы одну песню в этот плейлист, чтобы ИИ понял ваши вкусы.", "es": "❌ Primero añade al menos una canción a esta lista para que la IA entienda tus gustos."},
    "playlist_ai_analyzing": {"fa": "🤖 در حال تحلیل پلی‌لیست و پیدا کردن آهنگ‌های مشابه...", "en": "🤖 Analyzing the playlist and finding similar songs...", "ru": "🤖 Анализ плейлиста и поиск похожих песен...", "es": "🤖 Analizando la lista y buscando canciones similares..."},
    "playlist_ai_failed": {"fa": "❌ هوش مصنوعی نتوانست پیشنهادی پیدا کند. بعداً دوباره امتحان کنید.", "en": "❌ The AI couldn't come up with suggestions. Try again later.", "ru": "❌ ИИ не смог найти предложения. Попробуйте позже.", "es": "❌ La IA no pudo encontrar sugerencias. Inténtalo de nuevo más tarde."},
    "playlist_ai_album_name": {"fa": "🤖 مشابه «{name}»", "en": "🤖 Similar to \"{name}\"", "ru": "🤖 Похоже на «{name}»", "es": "🤖 Similar a «{name}»"},
    "addpl_choose_header": {"fa": "➕ **افزودن به کدام پلی‌لیست؟**", "en": "➕ **Add to which playlist?**", "ru": "➕ **В какой плейлист добавить?**", "es": "➕ **¿A qué lista añadir?**"},
    "addpl_btn_new": {"fa": "➕ پلی‌لیست جدید", "en": "➕ New Playlist", "ru": "➕ Новый плейлист", "es": "➕ Nueva lista"},
    "addpl_track_not_found": {"fa": "❌ اطلاعات این آهنگ یافت نشد.", "en": "❌ This song's info wasn't found.", "ru": "❌ Информация об этой песне не найдена.", "es": "❌ No se encontró información de esta canción."},
    "addpl_added_confirm": {"fa": "✅ به پلی‌لیست **{name}** اضافه شد.", "en": "✅ Added to playlist **{name}**.", "ru": "✅ Добавлено в плейлист **{name}**.", "es": "✅ Añadido a la lista **{name}**."},
    "addpl_duplicate": {"fa": "ℹ️ این آهنگ از قبل در این پلی‌لیست بود.", "en": "ℹ️ This song was already in this playlist.", "ru": "ℹ️ Эта песня уже была в этом плейлисте.", "es": "ℹ️ Esta canción ya estaba en esta lista."},
    "addpl_full": {"fa": "❌ این پلی‌لیست به سقف {max} آهنگ رسیده است.", "en": "❌ This playlist has reached its {max}-song limit.", "ru": "❌ Этот плейлист достиг лимита в {max} песен.", "es": "❌ Esta lista alcanzó su límite de {max} canciones."},
    "addpl_error": {"fa": "❌ خطا در افزودن به پلی‌لیست.", "en": "❌ Error adding to the playlist.", "ru": "❌ Ошибка при добавлении в плейлист.", "es": "❌ Error al añadir a la lista."},
})


def get_user_playlists(chat_id):
    if playlists_col is None:
        return []
    try:
        return list(playlists_col.find({"chat_id": int(chat_id)}).sort("created_at", 1))
    except Exception as e:
        print(f"playlists read error: {e}", flush=True)
        return []


def find_playlist(pid):
    if playlists_col is None:
        return None
    try:
        return playlists_col.find_one({"_id": ObjectId(pid)})
    except Exception:
        return None


def create_playlist(chat_id, name):
    if playlists_col is None:
        return None
    doc = {"chat_id": int(chat_id), "name": name[:60], "created_at": time.time(), "tracks": []}
    res = playlists_col.insert_one(doc)
    doc["_id"] = res.inserted_id
    return doc


def add_track_to_playlist(pid, key, title, artist, duration=0):
    """Returns 'added' | 'duplicate' | 'full' | 'error'."""
    if playlists_col is None:
        return "error"
    try:
        doc = playlists_col.find_one({"_id": ObjectId(pid)})
        if not doc:
            return "error"
        tracks = doc.get("tracks") or []
        if any(str(tr.get("key")) == str(key) for tr in tracks):
            return "duplicate"
        if len(tracks) >= MAX_TRACKS_PER_PLAYLIST:
            return "full"
        playlists_col.update_one(
            {"_id": ObjectId(pid)},
            {"$push": {"tracks": {"key": str(key), "title": title, "artist": artist,
                                  "duration": int(duration or 0), "added_at": time.time()}}})
        return "added"
    except Exception as e:
        print(f"add_track_to_playlist error: {e}", flush=True)
        return "error"


def remove_track_from_playlist(pid, track_idx):
    if playlists_col is None:
        return False
    try:
        doc = playlists_col.find_one({"_id": ObjectId(pid)})
        if not doc:
            return False
        tracks = doc.get("tracks") or []
        if track_idx < 0 or track_idx >= len(tracks):
            return False
        tracks.pop(track_idx)
        playlists_col.update_one({"_id": ObjectId(pid)}, {"$set": {"tracks": tracks}})
        return True
    except Exception as e:
        print(f"remove_track_from_playlist error: {e}", flush=True)
        return False


def rename_playlist(pid, new_name):
    if playlists_col is None:
        return False
    try:
        playlists_col.update_one({"_id": ObjectId(pid)}, {"$set": {"name": new_name[:60]}})
        return True
    except Exception:
        return False


def delete_playlist(pid):
    if playlists_col is None:
        return False
    try:
        playlists_col.delete_one({"_id": ObjectId(pid)})
        return True
    except Exception:
        return False


async def render_playlists(msg: Message, chat_id: int):
    back = InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_main")]])
    if playlists_col is None:
        return await msg.edit_text(t(chat_id, "playlists_unavailable"), reply_markup=back)
    playlists = get_user_playlists(chat_id)
    user_data.setdefault(chat_id, {})["my_playlists"] = [str(p["_id"]) for p in playlists]

    rows = []
    if not playlists:
        text = t(chat_id, "playlists_header") + "\n\n" + t(chat_id, "playlists_empty")
    else:
        lines = [t(chat_id, "playlists_header"), ""]
        for i, p in enumerate(playlists):
            count = len(p.get("tracks") or [])
            rows.append([InlineKeyboardButton(f"📃 {p.get('name','?')[:30]} ({count})", callback_data=f"pl_open_{i}")])
        text = "\n".join(lines)
    rows.append([InlineKeyboardButton(t(chat_id, "playlists_btn_create"), callback_data="pl_create")])
    rows.append([InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_main")])
    await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(rows))


async def render_playlist(msg: Message, chat_id: int, idx: int, page: int = 0):
    back_list = InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_playlists")]])
    pids = user_data.get(chat_id, {}).get("my_playlists", [])
    if idx >= len(pids):
        return await msg.edit_text(t(chat_id, "generic_expired"), reply_markup=back_list)
    doc = find_playlist(pids[idx])
    if not doc:
        return await msg.edit_text(t(chat_id, "playlist_not_found"), reply_markup=back_list)

    tracks = doc.get("tracks") or []
    pages = max(1, (len(tracks) + PLAYLIST_PAGE_SIZE - 1) // PLAYLIST_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    chunk = tracks[page * PLAYLIST_PAGE_SIZE:(page + 1) * PLAYLIST_PAGE_SIZE]

    lines = [t(chat_id, "playlist_header", name=doc.get("name", "?"), count=len(tracks))]
    if not tracks:
        lines.append(t(chat_id, "playlist_empty_body"))
    else:
        lines.append(t(chat_id, "playlist_page_meta", page=page + 1, pages=pages))
        lines.append("")

    btns = []
    for i, tr in enumerate(chunk):
        gi = page * PLAYLIST_PAGE_SIZE + i
        lines.append(f"`{gi+1}` **{str(tr.get('title','?'))[:40]}** — {str(tr.get('artist',''))[:24]}")
        btns.append(InlineKeyboardButton(str(gi + 1), callback_data=f"pl_track_{idx}_{gi}"))

    rows = [btns[j:j + 4] for j in range(0, len(btns), 4)]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(t(chat_id, "btn_prev"), callback_data=f"pl_page_{idx}_{page-1}"))
    if pages > 1:
        nav.append(InlineKeyboardButton(f"· {page+1}/{pages} ·", callback_data="noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(t(chat_id, "btn_next"), callback_data=f"pl_page_{idx}_{page+1}"))
    if nav:
        rows.append(nav)

    if tracks:
        rows.append([InlineKeyboardButton(t(chat_id, "playlist_btn_play_all"), callback_data=f"pl_playall_{idx}"),
                     InlineKeyboardButton(t(chat_id, "playlist_btn_shuffle"), callback_data=f"pl_shuffle_{idx}")])
    rows.append([InlineKeyboardButton(t(chat_id, "playlist_btn_ai"), callback_data=f"pl_ai_{idx}"),
                 InlineKeyboardButton(t(chat_id, "playlist_btn_radio"), callback_data=f"pl_radio_{idx}")])
    rows.append([InlineKeyboardButton(t(chat_id, "playlist_btn_rename"), callback_data=f"pl_rename_{idx}"),
                 InlineKeyboardButton(t(chat_id, "playlist_btn_delete"), callback_data=f"pl_delconfirm_{idx}")])
    rows.append([InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_playlists")])
    await msg.edit_text("\n".join(lines)[:4000], reply_markup=InlineKeyboardMarkup(rows))


async def show_playlist_track_actions(client: Client, msg: Message, chat_id: int, idx: int, track_idx: int):
    """Resends the actual cached track (fast, no re-download) with its full
    actions keyboard attached -- this is how a user actually retrieves a
    song they organized into a playlist, not just a menu with no audio."""
    pids = user_data.get(chat_id, {}).get("my_playlists", [])
    if idx >= len(pids):
        return await msg.reply_text(t(chat_id, "generic_expired"))
    doc = find_playlist(pids[idx])
    if not doc:
        return await msg.reply_text(t(chat_id, "playlist_not_found"))
    tracks = doc.get("tracks") or []
    if track_idx >= len(tracks):
        return await msg.reply_text(t(chat_id, "generic_expired"))
    tr = tracks[track_idx]
    key = tr.get("key")
    kb = track_actions_kb(key, chat_id)
    kb.inline_keyboard.append(
        [InlineKeyboardButton(t(chat_id, "playlist_btn_remove_track"), callback_data=f"pl_remove_{idx}_{track_idx}")])
    delivered = await deliver_by_key(client, chat_id, key, kb)
    if not delivered:
        await msg.reply_text(t(chat_id, "addpl_track_not_found"))


# The AI similar-songs feature reuses the propose_songs tool schema and is
# a one-shot, non-conversational call -- deliberately separate from the
# stateful AI_CHAT history so it doesn't pollute or get affected by it.
PROPOSE_SIMILAR_SONGS_TOOL = [{
    "name": "propose_songs",
    "description": "Propose exactly 10 real, well-known songs similar to the given playlist.",
    "parameters": {
        "type": "object",
        "properties": {
            "songs": {
                "type": "array",
                "minItems": 10,
                "maxItems": 10,
                "items": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}, "artist": {"type": "string"}},
                    "required": ["title", "artist"],
                },
            }
        },
        "required": ["songs"],
    },
}]


async def _ai_propose_similar_once(playlist_name: str, tracks: list) -> list:
    track_lines = "\n".join(f"- {tr.get('title','?')} by {tr.get('artist','?')}" for tr in tracks[:50])
    system_prompt = (
        "You are a music recommendation engine embedded in a Telegram bot. "
        "Given a user's playlist, propose real, well-known songs that musically "
        "fit the playlist (similar genre, mood, era, or artists), using the "
        "propose_songs tool. The songs array MUST contain EXACTLY 10 items -- "
        "never fewer, never more. Do not propose any song already in the "
        "playlist. Always call the tool -- never reply with plain text."
    )
    user_msg = f"Playlist \"{playlist_name}\":\n{track_lines}\n\nSuggest exactly 10 similar songs."
    result, _ = await asyncio.to_thread(
        ai_chat.ask_ai, system_prompt, [{"role": "user", "content": user_msg}], PROPOSE_SIMILAR_SONGS_TOOL)
    if result.get("type") != "tool_call" or result.get("name") != "propose_songs":
        return []
    return result.get("args", {}).get("songs") or []


async def ai_suggest_similar_songs(playlist_name: str, tracks: list) -> list:
    """Ask the AI for 10 real songs similar to this playlist. Returns a list
    of {"title","artist"} dicts (deduplicated against the playlist's own
    tracks), or [] on failure.

    Neither provider's function-calling strictly guarantees the array-length
    schema hint, so a short result triggers one retry before giving up --
    a single low-thinking call occasionally returns just 1-2 items instead
    of the requested 10.
    """
    have = {_norm(tr.get("title", "")) for tr in tracks}

    def _clean(songs):
        out = []
        for s in songs:
            title, artist = (s.get("title") or "").strip(), (s.get("artist") or "").strip()
            if not title or not artist or _norm(title) in have:
                continue
            out.append({"title": title, "artist": artist})
        return out

    for attempt in range(2):
        try:
            songs = await _ai_propose_similar_once(playlist_name, tracks)
        except Exception:
            traceback.print_exc()
            songs = []
        cleaned = _clean(songs)
        if len(cleaned) >= 8:
            return cleaned[:10]
        print(f"[playlist AI] attempt {attempt+1} returned only {len(cleaned)} usable songs", flush=True)
    return cleaned[:10]  # last attempt's result, even if short, beats nothing


# ---------------- 6.66 AI RADIO MODE ----------------
# An endless, personalized station built on the same ai_suggest_similar_songs
# engine as the playlist "Similar Songs" feature -- the difference is it
# plays one song at a time and feeds the running play history back into the
# next AI call, so the vibe can drift instead of being a fixed one-shot list.
STRINGS.update({
    "radio_not_active": {"fa": "❌ رادیویی در حال پخش نیست.", "en": "❌ No radio is currently playing.", "ru": "❌ Радио сейчас не воспроизводится.", "es": "❌ No hay ninguna radio reproduciéndose."},
    "radio_ai_failed": {"fa": "❌ هوش مصنوعی نتوانست آهنگ بعدی را پیدا کند. بعداً دوباره امتحان کنید.", "en": "❌ The AI couldn't find a next song. Try again later.", "ru": "❌ ИИ не смог найти следующую песню. Попробуйте позже.", "es": "❌ La IA no pudo encontrar la siguiente canción. Inténtalo más tarde."},
    "radio_starting": {"fa": "📻 در حال روشن کردن رادیو...", "en": "📻 Starting the radio...", "ru": "📻 Запуск радио...", "es": "📻 Iniciando la radio..."},
    "radio_now_playing": {"fa": "📻 **در حال پخش:** {title} — {artist}", "en": "📻 **Now playing:** {title} — {artist}", "ru": "📻 **Сейчас играет:** {title} — {artist}", "es": "📻 **Sonando ahora:** {title} — {artist}"},
    "btn_radio_next": {"fa": "⏭ بعدی", "en": "⏭ Next", "ru": "⏭ Далее", "es": "⏭ Siguiente"},
    "btn_radio_stop": {"fa": "⏹ توقف رادیو", "en": "⏹ Stop Radio", "ru": "⏹ Остановить радио", "es": "⏹ Detener radio"},
    "radio_stopped_confirm": {"fa": "⏹ رادیو متوقف شد.", "en": "⏹ Radio stopped.", "ru": "⏹ Радио остановлено.", "es": "⏹ Radio detenida."},
    "ai_btn_continue_radio": {"fa": "📻 ادامه با رادیو", "en": "📻 Continue with Radio", "ru": "📻 Продолжить с радио", "es": "📻 Continuar con radio"},
    "radio_seed_ai_name": {"fa": "پیشنهادهای هوش مصنوعی", "en": "AI Suggestions", "ru": "Предложения ИИ", "es": "Sugerencias de la IA"},
})

RADIO_HISTORY_CAP = 40


def radio_controls_row(chat_id) -> list:
    return [[InlineKeyboardButton(t(chat_id, "btn_radio_next"), callback_data="radio_next"),
             InlineKeyboardButton(t(chat_id, "btn_radio_stop"), callback_data="radio_stop")]]


async def deliver_radio_track(client: Client, chat_id: int, msg: Message):
    """Pops the next queued suggestion, resolves+delivers it with real audio
    (not just a menu), and refills the queue via the AI once it runs dry."""
    radio = user_data.get(chat_id, {}).get("radio")
    if not radio:
        return await msg.reply_text(t(chat_id, "radio_not_active"))

    if not radio["queue"]:
        suggestions = await ai_suggest_similar_songs(radio["seed_name"], radio["history"][-30:])
        if not suggestions:
            return await msg.reply_text(t(chat_id, "radio_ai_failed"))
        radio["queue"] = suggestions

    while radio["queue"]:
        nxt = radio["queue"].pop(0)
        title, artist = nxt["title"], nxt["artist"]
        status = await client.send_message(chat_id, t(chat_id, "radio_now_playing", title=title, artist=artist))
        rmeta = make_meta("catalog", title=title, artist=artist)
        ok = await deliver_track(client, chat_id, rmeta, status=status, extra_rows=radio_controls_row(chat_id),
                                 activity="music_radio", report_failure=False)
        if not ok:
            # Couldn't resolve this suggestion to real audio -- quietly try the next one.
            try:
                await rmeta["_status"].delete()
            except Exception:
                pass
            continue
        radio["history"].append({"title": title, "artist": artist})
        radio["history"] = radio["history"][-RADIO_HISTORY_CAP:]
        return

    # Every queued suggestion failed to resolve to real audio -- one retry
    # with a fresh AI batch rather than silently doing nothing.
    radio["queue"] = []
    return await msg.reply_text(t(chat_id, "radio_ai_failed"))


async def start_radio(client: Client, chat_id: int, status: Message, seed_name: str, seed_tracks: list):
    """status: a message already showing a 'starting radio' placeholder --
    the caller sends it fresh so the triggering view (playlist, AI reply)
    stays intact rather than being overwritten in place."""
    user_data.setdefault(chat_id, {})["radio"] = {
        "seed_name": seed_name,
        "history": [{"title": tr.get("title", ""), "artist": tr.get("artist", "")} for tr in seed_tracks][-RADIO_HISTORY_CAP:],
        "queue": [],
    }
    await deliver_radio_track(client, chat_id, status)


# ---------------- 6.65 MY PROFILE ----------------
# Everything the bot knows about one user, in one place: what they listen to,
# watch, save and edit, plus an AI-written "who you are" summary.
ACTIVITY_GROUPS = {
    "music": ("🎵", {"fa": "موزیک", "en": "Music", "ru": "Музыка", "es": "Música"}),
    "video": ("🎬", {"fa": "ویدیو یوتیوب", "en": "YouTube videos", "ru": "Видео YouTube", "es": "Videos de YouTube"}),
    "social": ("📱", {"fa": "اینستاگرام/تیک‌تاک/ردیت/X", "en": "Instagram/TikTok/Reddit/X", "ru": "Instagram/TikTok/Reddit/X", "es": "Instagram/TikTok/Reddit/X"}),
    "photo": ("🖼", {"fa": "عکس (پینترست/پکسلز)", "en": "Photos (Pinterest/Pexels)", "ru": "Фото (Pinterest/Pexels)", "es": "Fotos (Pinterest/Pexels)"}),
    "edit": ("✂️", {"fa": "ویرایش ویدیو و صدا", "en": "Video & audio edits", "ru": "Правки видео и аудио", "es": "Ediciones de video y audio"}),
    "shazam": ("🎧", {"fa": "شناسایی آهنگ", "en": "Song recognition", "ru": "Распознавание песен", "es": "Reconocimiento de canciones"}),
}

STRINGS.update({
    "profile_header": {"fa": "👤 **پروفایل {name}**", "en": "👤 **{name}'s profile**", "ru": "👤 **Профиль {name}**", "es": "👤 **Perfil de {name}**"},
    "profile_member_since": {"fa": "📅 عضو از: {date}", "en": "📅 Member since: {date}", "ru": "📅 С нами с: {date}", "es": "📅 Miembro desde: {date}"},
    "profile_activity_title": {"fa": "**📊 فعالیت‌های شما:**", "en": "**📊 Your activity:**", "ru": "**📊 Ваша активность:**", "es": "**📊 Tu actividad:**"},
    "profile_top_artists": {"fa": "**🏆 هنرمندان محبوب شما:**", "en": "**🏆 Your top artists:**", "ru": "**🏆 Ваши любимые артисты:**", "es": "**🏆 Tus artistas favoritos:**"},
    "profile_recent_songs": {"fa": "**🕒 آخرین آهنگ‌ها:**", "en": "**🕒 Recent songs:**", "ru": "**🕒 Последние песни:**", "es": "**🕒 Canciones recientes:**"},
    "profile_counts": {"fa": "📃 پلی‌لیست: {pl} • 🔔 اشتراک: {subs}", "en": "📃 Playlists: {pl} • 🔔 Subscriptions: {subs}", "ru": "📃 Плейлисты: {pl} • 🔔 Подписки: {subs}", "es": "📃 Listas: {pl} • 🔔 Suscripciones: {subs}"},
    "profile_empty": {"fa": "_هنوز فعالیتی ثبت نشده — از ربات استفاده کنید تا پروفایلتان ساخته شود._", "en": "_No activity yet — use the bot and your profile will fill up._", "ru": "_Пока нет активности — пользуйтесь ботом, и профиль заполнится._", "es": "_Aún no hay actividad — usa el bot y tu perfil se irá llenando._"},
    "profile_btn_ai": {"fa": "🤖 خلاصه هوشمند من", "en": "🤖 My AI Summary", "ru": "🤖 Мой ИИ-портрет", "es": "🤖 Mi resumen IA"},
    "profile_btn_activity": {"fa": "🕒 تاریخچه فعالیت", "en": "🕒 Activity History", "ru": "🕒 История активности", "es": "🕒 Historial de actividad"},
    "profile_btn_library": {"fa": "📚 کتابخانه من", "en": "📚 My Library", "ru": "📚 Моя библиотека", "es": "📚 Mi biblioteca"},
    "profile_btn_stats": {"fa": "📊 آمار موزیک", "en": "📊 Music Stats", "ru": "📊 Музыкальная статистика", "es": "📊 Estadísticas musicales"},
    "profile_btn_subs": {"fa": "🔔 اشتراک‌های من", "en": "🔔 My Subscriptions", "ru": "🔔 Мои подписки", "es": "🔔 Mis suscripciones"},
    "profile_btn_back": {"fa": "🔙 بازگشت به پروفایل", "en": "🔙 Back to profile", "ru": "🔙 Назад к профилю", "es": "🔙 Volver al perfil"},
    "profile_activity_header": {"fa": "🕒 **۲۰ فعالیت اخیر شما:**\n", "en": "🕒 **Your last 20 actions:**\n", "ru": "🕒 **Ваши последние 20 действий:**\n", "es": "🕒 **Tus últimas 20 acciones:**\n"},
    "profile_ai_thinking": {"fa": "🤖 در حال تحلیل سلیقه و فعالیت‌های شما...", "en": "🤖 Analyzing your taste and activity...", "ru": "🤖 Анализирую ваши вкусы и активность...", "es": "🤖 Analizando tus gustos y actividad..."},
    "profile_ai_failed": {"fa": "❌ ساخت خلاصه با خطا مواجه شد، کمی بعد دوباره امتحان کنید.", "en": "❌ Couldn't build the summary, try again in a bit.", "ru": "❌ Не удалось составить портрет, попробуйте позже.", "es": "❌ No se pudo generar el resumen, inténtalo más tarde."},
    "profile_ai_need_more": {"fa": "🤖 هنوز داده کافی برای خلاصه ندارم — کمی بیشتر از ربات استفاده کنید.", "en": "🤖 Not enough data for a summary yet — use the bot a bit more.", "ru": "🤖 Пока мало данных для портрета — пользуйтесь ботом чуть больше.", "es": "🤖 Aún no hay suficientes datos — usa el bot un poco más."},
    "profile_ai_title": {"fa": "🤖 **خلاصه هوشمند شما**\n\n", "en": "🤖 **Your AI summary**\n\n", "ru": "🤖 **Ваш ИИ-портрет**\n\n", "es": "🤖 **Tu resumen IA**\n\n"},
    "playlist_btn_play_all": {"fa": "▶️ پخش همه", "en": "▶️ Play All", "ru": "▶️ Воспроизвести все", "es": "▶️ Reproducir todo"},
    "playlist_btn_shuffle": {"fa": "🔀 پخش تصادفی", "en": "🔀 Shuffle", "ru": "🔀 Перемешать", "es": "🔀 Aleatorio"},
    "playlist_sending_all": {"fa": "▶️ در حال ارسال {count} آهنگ...", "en": "▶️ Sending {count} songs...", "ru": "▶️ Отправка {count} песен...", "es": "▶️ Enviando {count} canciones..."},
})


def _activity_group(kind: str):
    k = (kind or "").lower()
    if k.startswith("music") or k in ("album", "lyrics", "radio"):
        return "music"
    if k in ("download", "youtube", "video"):
        return "video"
    if k.startswith(("instagram", "tiktok", "reddit", "twitter")):
        return "social"
    if k.startswith(("pinterest", "image")):
        return "photo"
    if k in ("video_edit", "audio_tool", "image_tool"):
        return "edit"
    if k == "audio_recognition":
        return "shazam"
    return None


def collect_profile_sync(chat_id: int) -> dict:
    out = {"groups": Counter(), "artists": [], "recent_songs": [], "songs_total": 0, "joined": None,
           "playlists": 0, "subs": 0, "recent_activity": [], "details": {}}
    try:
        if users_col is not None:
            u = users_col.find_one({"chat_id": int(chat_id)}, {"joined_at": 1}) or {}
            out["joined"] = u.get("joined_at")
        if history_col is not None:
            hist = list(history_col.find({"chat_id": int(chat_id)}, {"title": 1, "artist": 1, "ts": 1})
                        .sort("ts", -1).limit(1000))
            out["songs_total"] = len(hist)
            out["artists"] = Counter(clean_artist_name(_first_artist(h.get("artist") or ""))
                                     for h in hist if h.get("artist")).most_common(8)
            seen, rec = set(), []
            for h in hist:
                k = (h.get("title"), h.get("artist"))
                if k in seen or not h.get("title"):
                    continue
                seen.add(k)
                rec.append(k)
                if len(rec) >= 30:
                    break
            out["recent_songs"] = rec
        if activity_col is not None:
            acts = list(activity_col.find({"chat_id": int(chat_id)}, {"kind": 1, "detail": 1, "ts": 1})
                        .sort("ts", -1).limit(1500))
            for a in acts:
                g = _activity_group(a.get("kind"))
                if g:
                    out["groups"][g] += 1
                    lst = out["details"].setdefault(g, [])
                    if len(lst) < 25:
                        lst.append(str(a.get("detail") or "")[:120])
            out["recent_activity"] = acts[:20]
        if playlists_col is not None:
            out["playlists"] = playlists_col.count_documents({"chat_id": int(chat_id)})
        if subs_col is not None:
            out["subs"] = subs_col.count_documents({"chat_id": int(chat_id)})
    except Exception as e:
        print(f"profile collect error: {e}", flush=True)
    return out


def profile_kb(chat_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(chat_id, "profile_btn_ai"), callback_data="prof_ai")],
        [InlineKeyboardButton(t(chat_id, "profile_btn_activity"), callback_data="prof_act"),
         InlineKeyboardButton(t(chat_id, "profile_btn_library"), callback_data="my_library")],
        [InlineKeyboardButton(t(chat_id, "profile_btn_stats"), callback_data="my_stats"),
         InlineKeyboardButton(t(chat_id, "profile_btn_subs"), callback_data="subs_menu")],
        [InlineKeyboardButton(t(chat_id, "btn_my_playlists_short"), callback_data="menu_playlists")],
        [InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_main")],
    ])


STRINGS.update({
    "btn_my_playlists_short": {"fa": "📃 پلی‌لیست‌های من", "en": "📃 My Playlists", "ru": "📃 Мои плейлисты", "es": "📃 Mis listas"},
})


async def render_profile(msg: Message, chat_id: int, name: str):
    p = await asyncio.to_thread(collect_profile_sync, chat_id)
    lang = get_lang(chat_id)
    lines = [t(chat_id, "profile_header", name=name or "—")]
    if p["joined"]:
        lines.append(t(chat_id, "profile_member_since", date=datetime.fromtimestamp(p["joined"]).strftime("%Y-%m-%d")))
    lines.append(t(chat_id, "profile_counts", pl=p["playlists"], subs=p["subs"]))
    lines.append("")
    if not p["groups"] and not p["songs_total"]:
        lines.append(t(chat_id, "profile_empty"))
    else:
        lines.append(t(chat_id, "profile_activity_title"))
        for g, (icon, labels) in ACTIVITY_GROUPS.items():
            n = p["songs_total"] if g == "music" else p["groups"].get(g, 0)
            if n:
                lines.append(f"{icon} {labels.get(lang) or labels['en']}: **{n}**")
        if p["artists"]:
            lines.append("")
            lines.append(t(chat_id, "profile_top_artists"))
            lines.append(" • ".join(f"{a} ({c})" for a, c in p["artists"][:5]))
        if p["recent_songs"]:
            lines.append("")
            lines.append(t(chat_id, "profile_recent_songs"))
            for ti, ar in p["recent_songs"][:5]:
                lines.append(f"▫️ {ti} — {ar}")
    text = "\n".join(lines)[:4000]
    try:
        await msg.edit_text(text, reply_markup=profile_kb(chat_id))
    except Exception:
        await msg.reply_text(text, reply_markup=profile_kb(chat_id))


async def render_profile_activity(msg: Message, chat_id: int):
    p = await asyncio.to_thread(collect_profile_sync, chat_id)
    lines = [t(chat_id, "profile_activity_header")]
    for a in p["recent_activity"]:
        g = _activity_group(a.get("kind"))
        icon = ACTIVITY_GROUPS[g][0] if g else "▫️"
        when = datetime.fromtimestamp(a.get("ts") or 0).strftime("%m-%d %H:%M")
        lines.append(f"{icon} `{when}` {str(a.get('detail') or a.get('kind'))[:70]}")
    if len(lines) == 1:
        lines.append(t(chat_id, "profile_empty"))
    await msg.edit_text("\n".join(lines)[:4000], reply_markup=InlineKeyboardMarkup(
        [[InlineKeyboardButton(t(chat_id, "profile_btn_back"), callback_data="menu_profile")]]))


_PROFILE_AI_CACHE = {}  # chat_id -> (ts, text)
LANG_NAMES = {"fa": "Persian (Farsi)", "en": "English", "ru": "Russian", "es": "Spanish"}


async def render_profile_ai(client: Client, msg: Message, chat_id: int, name: str):
    back = InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "profile_btn_back"), callback_data="menu_profile")]])
    hit = _PROFILE_AI_CACHE.get(chat_id)
    if hit and time.time() - hit[0] < 3600:
        return await msg.edit_text(t(chat_id, "profile_ai_title") + hit[1], reply_markup=back)
    p = await asyncio.to_thread(collect_profile_sync, chat_id)
    if p["songs_total"] + sum(p["groups"].values()) < 3:
        return await msg.edit_text(t(chat_id, "profile_ai_need_more"), reply_markup=back)
    await msg.edit_text(t(chat_id, "profile_ai_thinking"))
    data = {
        "top_artists": [f"{a} x{c}" for a, c in p["artists"]],
        "recent_songs": [f"{ti} - {ar}" for ti, ar in p["recent_songs"][:30]],
        "activity_counts": {g: (p["songs_total"] if g == "music" else p["groups"].get(g, 0)) for g in ACTIVITY_GROUPS},
        "youtube_videos": p["details"].get("video", [])[:15],
        "social_downloads": p["details"].get("social", [])[:15],
        "photo_searches_downloads": p["details"].get("photo", [])[:15],
        "edits": p["details"].get("edit", [])[:10],
        "playlists": p["playlists"], "subscriptions": p["subs"],
    }
    lang = LANG_NAMES.get(get_lang(chat_id), "Persian (Farsi)")
    system = (
        "You write a short, warm, fun personal profile of a Telegram bot user from their activity data "
        f"(music, videos, photos, edits). Write ONLY in {lang} (if Persian, never Arabic). Address the user "
        "as 'you'. Structure: 1) one-line headline describing their vibe, 2) their music taste (genres, moods, "
        "eras you can infer from the artists/songs), 3) what they watch / download / save, 4) 2-3 playful "
        "observations, 5) three song or artist recommendations they would probably like. Use a few emojis, "
        "short paragraphs, no markdown tables, max ~1200 characters. Never invent activity that isn't in the data."
    )
    try:
        res, _ = await asyncio.wait_for(asyncio.to_thread(
            ai_chat.ask_ai, system,
            [{"role": "user", "content": f"Name: {name}\nData: {json.dumps(data, ensure_ascii=False)[:6000]}"}], []),
            timeout=60)
        text = _clean_str((res or {}).get("content") or "")
        if not text:
            raise RuntimeError("empty AI reply")
    except Exception as e:
        print(f"profile AI error: {e}", flush=True)
        return await msg.edit_text(t(chat_id, "profile_ai_failed"), reply_markup=back)
    _PROFILE_AI_CACHE[chat_id] = (time.time(), text[:3500])
    try:
        await msg.edit_text(t(chat_id, "profile_ai_title") + text[:3500], reply_markup=back)
    except Exception:
        # AI text may contain characters that break markdown parsing
        await msg.edit_text(t(chat_id, "profile_ai_title") + text[:3500], reply_markup=back,
                            parse_mode=ParseMode.DISABLED)


async def play_playlist(client: Client, chat_id: int, idx: int, shuffle: bool = False):
    pids = user_data.get(chat_id, {}).get("my_playlists", [])
    if idx >= len(pids):
        return await client.send_message(chat_id, t(chat_id, "generic_expired"))
    doc = find_playlist(pids[idx])
    tracks = list((doc or {}).get("tracks") or [])
    if not tracks:
        return await client.send_message(chat_id, t(chat_id, "playlist_ai_no_tracks"))
    if shuffle:
        random.shuffle(tracks)
    tracks = tracks[:35]
    status = await client.send_message(chat_id, t(chat_id, "playlist_sending_all", count=len(tracks)))
    # Same grouped delivery as albums: everything is prepared first, then sent
    # together with one numbered picker for each song's tools.
    items = []
    for tr in tracks:
        key = str(tr.get("key") or "")
        items.append({"name": tr.get("title") or "", "artist": tr.get("artist") or "",
                      "duration": tr.get("duration") or 0,
                      "yt_id": key if re.fullmatch(r"[0-9A-Za-z_-]{11}", key) else ""})
    await download_album(client, chat_id, status,
                         {"album": (doc or {}).get("name") or "Playlist", "is_playlist": True, "tracks": items})


# ---------------- 6.7 ADMIN PANEL ----------------
ADM_PAGE = 8


def fmt_ts(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%m-%d %H:%M")
    except Exception:
        return "?"


# Raw activity "kind" strings are internal/English -- fine for grep-ing logs,
# unreadable at a glance in the admin panel. This is display-only; log_activity
# call sites are untouched.
KIND_LABELS = {
    "link": "🔗 لینک", "search": "🔎 جستجو", "download": "⬇️ دانلود یوتیوب",
    "music_official": "💽 موزیک (اورجینال)", "music_soundcloud": "☁️ موزیک (ساندکلود)",
    "instagram": "📸 اینستاگرام", "tiktok": "📱 تیک‌تاک", "album": "📀 آلبوم",
    "pinterest": "📌 پینترست", "pinterest_bulk": "📌 پینترست (دسته‌ای)",
    "reddit": "🔴 ردیت", "reddit_bulk": "🔴 ردیت (دسته‌ای)",
    "image_search": "🖼 جستجوی تصویر", "audio_recognition": "🎧 شناسایی آهنگ",
    "video_edit": "🎬 ویرایش ویدیو",
}


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, f"▫️ {kind}")


def admin_home_kb() -> InlineKeyboardMarkup:
    maint = bool(get_settings().get("maintenance"))
    harv_on = bool(get_settings().get("harvester_enabled", True))
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 آمار کلی", callback_data="adm_stats"),
         InlineKeyboardButton("👥 کاربران", callback_data="adm_users_0")],
        [InlineKeyboardButton("🔍 جستجوی کاربر", callback_data="adm_search"),
         InlineKeyboardButton("📜 فعالیت‌ها", callback_data="adm_act_0")],
        [InlineKeyboardButton("✉️ پیام‌های کاربران", callback_data="adm_sup_0"),
         InlineKeyboardButton("📢 ارسال همگانی", callback_data="adm_bc")],
        [InlineKeyboardButton("🔗 کانال‌های اجباری", callback_data="adm_ch"),
         InlineKeyboardButton("📡 چت‌های ربات", callback_data="adm_chats_0")],
        [InlineKeyboardButton("🎯 مدیریت Smallz", callback_data="adm_smallz_0"),
         InlineKeyboardButton("🛡 دسترسی در تعمیرات", callback_data="adm_maintallow")],
        [InlineKeyboardButton("🚪 معافیت از عضویت اجباری", callback_data="adm_fjallow")],
        [InlineKeyboardButton("🔐 دسترسی کاربران", callback_data="adm_access"),
         InlineKeyboardButton("📦 حجم آپلود و امضا", callback_data="adm_limits")],
        [InlineKeyboardButton("⚙️ پنل پیشرفته (روشن/خاموش قابلیت‌ها)", callback_data="adm_feat")],
        [InlineKeyboardButton(
            "🎵 کش خودکار ۲۴/۷: 🟢 روشن" if harv_on else "🎵 کش خودکار ۲۴/۷: 🔴 خاموش",
            callback_data="adm_harvester")],
        [InlineKeyboardButton(
            "🟢 ربات فعال است — خاموش کن" if not maint else "🔴 حالت تعمیرات — روشن کن",
            callback_data="adm_maint")],
    ])


async def render_admin_home(msg: Message, edit: bool = True):
    harv_state = "🟢 فعال" if get_settings().get("harvester_enabled", True) else "🔴 غیرفعال"
    text = ("🛠 **پنل مدیریت**\n\n"
            f"وضعیت ربات: {'🔴 حالت تعمیرات' if get_settings().get('maintenance') else '🟢 فعال'}\n"
            f"کش خودکار ۲۴/۷: {harv_state}\n"
            f"تأیید دستی کاربران: {'🟢 روشن' if approval_required() else '🔴 خاموش'}\n"
            f"حداکثر حجم آپلود: {upload_limit_mb()} مگابایت\n"
            "یکی از بخش‌ها را انتخاب کنید:")
    if edit:
        await msg.edit_text(text, reply_markup=admin_home_kb())
    else:
        await msg.reply_text(text, reply_markup=admin_home_kb())


async def render_admin_stats(msg: Message):
    back = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="adm_home")]])
    if users_col is None:
        return await msg.edit_text("❌ دیتابیس در دسترس نیست.", reply_markup=back)
    try:
        now = time.time()
        total_users = users_col.count_documents({})
        banned = users_col.count_documents({"banned": True})
        active_24h = users_col.count_documents({"last_seen": {"$gt": now - 86400}})
        active_7d = users_col.count_documents({"last_seen": {"$gt": now - 604800}})
        downloads = history_col.count_documents({}) if history_col is not None else 0
        dl_24h = history_col.count_documents({"ts": {"$gt": now - 86400}}) if history_col is not None else 0
        cached = music_cache.count_documents({}) if music_cache is not None else 0
        events = activity_col.count_documents({}) if activity_col is not None else 0
        pending = support_col.count_documents({"replied": {"$ne": True}}) if support_col is not None else 0
        subs = subs_col.count_documents({}) if subs_col is not None else 0
    except Exception as e:
        print(f"admin stats error: {e}", flush=True)
        return await msg.edit_text("❌ خطا در خواندن آمار.", reply_markup=back)

    await msg.edit_text(
        "📊 **آمار کلی ربات**\n\n"
        f"👥 کاربران: **{total_users}**  (⛔️ مسدود: {banned})\n"
        f"🟢 فعال ۲۴ ساعت: **{active_24h}**▫️  ۷ روز: **{active_7d}**\n\n"
        f"⬇️ کل دانلودها: **{downloads}**  (۲۴ ساعت: {dl_24h})\n"
        f"💾 آهنگ‌های کش‌شده: **{cached}**\n"
        f"🔔 اشتراک‌ها: **{subs}**\n"
        f"📜 رویدادهای ثبت‌شده: **{events}**\n"
        f"✉️ پیام‌های بی‌پاسخ: **{pending}**",
        reply_markup=back)


USER_SORTS = {"seen": "🕒 اخیر", "events": "🔥 پرمصرف"}


async def render_admin_users(msg: Message, page: int = 0, sort: str = "seen"):
    back = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="adm_home")]])
    if users_col is None:
        return await msg.edit_text("❌ دیتابیس در دسترس نیست.", reply_markup=back)
    sort = sort if sort in USER_SORTS else "seen"
    sort_field = "events" if sort == "events" else "last_seen"
    try:
        total = users_col.count_documents({})
        pages = max(1, (total + ADM_PAGE - 1) // ADM_PAGE)
        page = max(0, min(page, pages - 1))
        docs = list(users_col.find({}).sort(sort_field, -1)
                    .skip(page * ADM_PAGE).limit(ADM_PAGE))
    except Exception as e:
        print(f"admin users error: {e}", flush=True)
        return await msg.edit_text("❌ خطا در خواندن کاربران.", reply_markup=back)
    if not docs:
        return await msg.edit_text("👥 هنوز کاربری ثبت نشده است.", reply_markup=back)

    # Stashed so render_admin_user's "back" button returns to this exact
    # page/sort instead of always resetting to page 1 -- keyed by the admin's
    # own chat_id (msg.chat.id here, since only the admin ever sees this panel).
    back_target = f"adm_users_{page}" if sort == "seen" else f"adm_userssort_{page}_{sort}"
    user_data.setdefault(msg.chat.id, {})["adm_users_back"] = back_target

    lines = [f"👥 **کاربران** — {total} نفر • {USER_SORTS[sort]} • صفحه {page+1} از {pages}\n"]
    rows = []
    for d in docs:
        cid = d.get("chat_id")
        name = (d.get("first_name") or "بی‌نام")[:18]
        uname = f"@{d['username']}" if d.get("username") else "—"
        flag = "⛔️" if d.get("banned") else ("✅" if d.get("approved") or is_admin(cid) else "⏳")
        lines.append(f"{flag} `{cid}` • {name} • {uname}")
        lines.append(f"      آخرین فعالیت: {fmt_ts(d.get('last_seen'))} • رویداد: {d.get('events', 0)}\n")
        rows.append([InlineKeyboardButton(f"{flag} {name}", callback_data=f"adm_u_{cid}")])

    def _nav_cb(p, s):
        return f"adm_users_{p}" if s == "seen" else f"adm_userssort_{p}_{s}"

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=_nav_cb(page - 1, sort)))
    nav.append(InlineKeyboardButton(f"· {page+1}/{pages} ·", callback_data="noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=_nav_cb(page + 1, sort)))
    rows.append(nav)
    rows.append([
        InlineKeyboardButton(("• 🕒 اخیر •" if sort == "seen" else "🕒 اخیر"), callback_data="adm_users_0"),
        InlineKeyboardButton(("• 🔥 پرمصرف •" if sort == "events" else "🔥 پرمصرف"), callback_data="adm_userssort_0_events"),
    ])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="adm_home")])
    await msg.edit_text("\n".join(lines)[:4000], reply_markup=InlineKeyboardMarkup(rows))


async def render_admin_user(msg: Message, target: int):
    back_target = user_data.get(msg.chat.id, {}).get("adm_users_back", "adm_users_0")
    back = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 کاربران", callback_data=back_target)]])
    if users_col is None:
        return await msg.edit_text("❌ دیتابیس در دسترس نیست.", reply_markup=back)
    doc = users_col.find_one({"chat_id": int(target)})
    if not doc:
        return await msg.edit_text("❌ کاربر یافت نشد.", reply_markup=back)
    banned = bool(doc.get("banned"))
    dls = history_col.count_documents({"chat_id": int(target)}) if history_col is not None else 0
    recent = []
    if activity_col is not None:
        recent = list(activity_col.find({"chat_id": int(target)}).sort("ts", -1).limit(8))

    lines = [f"👤 **{(doc.get('first_name') or 'بی‌نام')}**",
             f"آیدی: `{target}`",
             f"یوزرنیم: {'@' + doc['username'] if doc.get('username') else '—'}",
             f"وضعیت: {'⛔️ مسدود' if banned else '✅ فعال'} • دسترسی: {'✅ تأیید شده' if doc.get('approved') or is_admin(target) else '⏳ تأیید نشده'}",
             f"عضویت: {fmt_ts(doc.get('joined_at'))} • آخرین فعالیت: {fmt_ts(doc.get('last_seen'))}",
             f"دانلودها: {dls} • رویدادها: {doc.get('events', 0)}\n",
             "**آخرین فعالیت‌ها:**"]
    for a in recent:
        lines.append(f"• {fmt_ts(a.get('ts'))} — {kind_label(a.get('kind',''))}\n   {str(a.get('detail'))[:60]}")
    if not recent:
        lines.append("_موردی ثبت نشده_")

    rows = [
        [InlineKeyboardButton("✅ رفع مسدودیت" if banned else "⛔️ مسدود کردن",
                              callback_data=f"adm_{'unban' if banned else 'ban'}_{target}"),
         InlineKeyboardButton("🚫 لغو دسترسی" if doc.get("approved") else "✅ تأیید دسترسی",
                              callback_data=f"adm_{'revk' if doc.get('approved') else 'appr'}_{target}")],
        [InlineKeyboardButton("✉️ ارسال پیام", callback_data=f"adm_msg_{target}"),
         InlineKeyboardButton("📜 فعالیت کامل", callback_data=f"adm_uact_{target}_0")],
        [InlineKeyboardButton("🔙 کاربران", callback_data=back_target)],
    ]
    await msg.edit_text("\n".join(lines)[:4000], reply_markup=InlineKeyboardMarkup(rows))


async def render_admin_activity(msg: Message, page: int = 0, target: int = 0):
    """Global feed, or one user's full trail when target is set."""
    home = "adm_home" if not target else f"adm_u_{target}"
    back = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=home)]])
    if activity_col is None:
        return await msg.edit_text("❌ دیتابیس در دسترس نیست.", reply_markup=back)
    q = {"chat_id": int(target)} if target else {}
    try:
        total = activity_col.count_documents(q)
        pages = max(1, (total + ADM_PAGE - 1) // ADM_PAGE)
        page = max(0, min(page, pages - 1))
        docs = list(activity_col.find(q).sort("ts", -1).skip(page * ADM_PAGE).limit(ADM_PAGE))
    except Exception as e:
        print(f"admin activity error: {e}", flush=True)
        return await msg.edit_text("❌ خطا در خواندن فعالیت‌ها.", reply_markup=back)
    if not docs:
        return await msg.edit_text("📜 فعالیتی ثبت نشده است.", reply_markup=back)

    # One batched name lookup instead of a query per row -- and the actual
    # fix for "can't tell which user did what": most users have no username,
    # so falling back to first_name (and showing it at all, unlike before,
    # which only ever printed the bare numeric id) is what makes the feed
    # readable instead of a wall of anonymous ids.
    names = {}
    if not target and users_col is not None:
        ids = list({int(a["chat_id"]) for a in docs if a.get("chat_id") is not None})
        if ids:
            for u in users_col.find({"chat_id": {"$in": ids}}, {"chat_id": 1, "first_name": 1, "username": 1}):
                label = u.get("first_name") or "بی‌نام"
                if u.get("username"):
                    label += f" (@{u['username']})"
                names[u["chat_id"]] = label

    title = f"📜 **فعالیت کاربر `{target}`**" if target else "📜 **فعالیت‌های اخیر (همه)**"
    lines = [f"{title} — {total} رویداد • صفحه {page+1} از {pages}\n"]
    seen_users, jump_rows = set(), []
    for a in docs:
        cid = a.get("chat_id")
        lines.append(f"🕓 {fmt_ts(a.get('ts'))} — {kind_label(a.get('kind',''))}")
        if not target:
            lines.append(f"   👤 {names.get(cid, 'بی‌نام')} • `{cid}`")
        lines.append(f"   {str(a.get('detail'))[:80]}\n")
        if not target and cid is not None and cid not in seen_users:
            seen_users.add(cid)
            jump_rows.append([InlineKeyboardButton(f"👤 {names.get(cid, str(cid))[:24]}", callback_data=f"adm_u_{cid}")])

    prefix = f"adm_uact_{target}" if target else "adm_act"
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"{prefix}_{page-1}"))
    nav.append(InlineKeyboardButton(f"· {page+1}/{pages} ·", callback_data="noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"{prefix}_{page+1}"))
    rows = jump_rows + [nav, [InlineKeyboardButton("🔙 بازگشت", callback_data=home)]]
    await msg.edit_text("\n".join(lines)[:4000], reply_markup=InlineKeyboardMarkup(rows))


async def render_admin_support(msg: Message, page: int = 0):
    back = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="adm_home")]])
    if support_col is None:
        return await msg.edit_text("❌ دیتابیس در دسترس نیست.", reply_markup=back)
    try:
        total = support_col.count_documents({})
        pages = max(1, (total + ADM_PAGE - 1) // ADM_PAGE)
        page = max(0, min(page, pages - 1))
        docs = list(support_col.find({}).sort("ts", -1).skip(page * ADM_PAGE).limit(ADM_PAGE))
    except Exception as e:
        print(f"admin support error: {e}", flush=True)
        return await msg.edit_text("❌ خطا در خواندن پیام‌ها.", reply_markup=back)
    if not docs:
        return await msg.edit_text("✉️ پیامی دریافت نشده است.", reply_markup=back)

    lines = [f"✉️ **پیام‌های کاربران** — {total} • صفحه {page+1} از {pages}\n"]
    rows = []
    for d in docs:
        mark = "✅" if d.get("replied") else "🆕"
        lines.append(f"{mark} `{d.get('chat_id')}` • {d.get('name','')[:16]} • {fmt_ts(d.get('ts'))}")
        lines.append(f"    {str(d.get('text',''))[:90]}\n")
        rows.append([InlineKeyboardButton(f"{mark} پاسخ به {d.get('name','')[:14]}",
                                          callback_data=f"adm_rep_{d['_id']}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"adm_sup_{page-1}"))
    nav.append(InlineKeyboardButton(f"· {page+1}/{pages} ·", callback_data="noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"adm_sup_{page+1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="adm_home")])
    await msg.edit_text("\n".join(lines)[:4000], reply_markup=InlineKeyboardMarkup(rows))


async def render_admin_channels(msg: Message):
    channels = get_settings().get("force_channels") or []
    lines = ["🔗 **کانال‌های عضویت اجباری**\n"]
    rows = []
    if not channels:
        lines.append("_هیچ کانالی تنظیم نشده — ربات برای همه آزاد است._")
    for i, ch in enumerate(channels):
        lines.append(f"{i+1}. `{ch.get('chat')}`\n     {ch.get('link') or '—'}")
        rows.append([InlineKeyboardButton(f"🗑 حذف {ch.get('chat')}", callback_data=f"adm_chdel_{i}")])
    lines.append("\n_توجه: ربات باید در کانال ادمین باشد تا بتواند عضویت را بررسی کند._")
    rows.append([InlineKeyboardButton("➕ افزودن کانال", callback_data="adm_chadd")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="adm_home")])
    await msg.edit_text("\n".join(lines)[:4000], reply_markup=InlineKeyboardMarkup(rows))


async def render_admin_search_results(msg: Message, query: str):
    back = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="adm_home")]])
    if users_col is None:
        return await msg.edit_text("❌ دیتابیس در دسترس نیست.", reply_markup=back)
    q = query.strip().lstrip("@")
    try:
        if q.isdigit() or (q.startswith("-") and q[1:].isdigit()):
            docs = list(users_col.find({"chat_id": int(q)}).limit(ADM_PAGE))
        else:
            docs = list(users_col.find({
                "$or": [{"username": {"$regex": re.escape(q), "$options": "i"}},
                        {"first_name": {"$regex": re.escape(q), "$options": "i"}}]
            }).limit(ADM_PAGE))
    except Exception as e:
        print(f"admin search error: {e}", flush=True)
        return await msg.edit_text("❌ خطا در جستجو.", reply_markup=back)
    if not docs:
        return await msg.edit_text(f"🔍 نتیجه‌ای برای «{query}» یافت نشد.", reply_markup=back)

    lines = [f"🔍 **نتایج جستجو برای:** «{query}» — {len(docs)} نتیجه\n"]
    rows = []
    for d in docs:
        cid = d.get("chat_id")
        name = (d.get("first_name") or "بی‌نام")[:18]
        uname = f"@{d['username']}" if d.get("username") else "—"
        flag = "⛔️" if d.get("banned") else "✅"
        lines.append(f"{flag} `{cid}` • {name} • {uname}")
        rows.append([InlineKeyboardButton(f"{flag} {name}", callback_data=f"adm_u_{cid}")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="adm_home")])
    await msg.edit_text("\n".join(lines)[:4000], reply_markup=InlineKeyboardMarkup(rows))


async def render_admin_bot_chats(msg: Message, page: int = 0):
    back = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="adm_home")]])
    if bot_chats_col is None:
        return await msg.edit_text("❌ دیتابیس در دسترس نیست.", reply_markup=back)
    try:
        q = {"active": True}
        total = bot_chats_col.count_documents(q)
        pages = max(1, (total + ADM_PAGE - 1) // ADM_PAGE)
        page = max(0, min(page, pages - 1))
        docs = list(bot_chats_col.find(q).sort("ts", -1).skip(page * ADM_PAGE).limit(ADM_PAGE))
    except Exception as e:
        print(f"admin bot_chats error: {e}", flush=True)
        return await msg.edit_text("❌ خطا در خواندن چت‌ها.", reply_markup=back)
    if not docs:
        return await msg.edit_text("📡 ربات هنوز به هیچ کانال یا گروهی اضافه نشده.", reply_markup=back)

    lines = [f"📡 **چت‌هایی که ربات در آن‌هاست** — {total} مورد • صفحه {page+1} از {pages}\n"]
    rows = []
    for d in docs:
        cid = d.get("chat_id")
        lines.append(f"• **{str(d.get('title'))[:40]}**\n     `{cid}` • {d.get('type')} • {d.get('status')}")
        rows.append([InlineKeyboardButton(f"🚪 ترک {str(d.get('title'))[:20]}", callback_data=f"adm_leavechat_{cid}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"adm_chats_{page-1}"))
    nav.append(InlineKeyboardButton(f"· {page+1}/{pages} ·", callback_data="noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"adm_chats_{page+1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="adm_home")])
    await msg.edit_text("\n".join(lines)[:4000], reply_markup=InlineKeyboardMarkup(rows))


async def render_admin_smallz(msg: Message):
    back = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="adm_home")]])
    if smallz_col is None:
        return await msg.edit_text("❌ دیتابیس در دسترس نیست.", reply_markup=back)
    try:
        docs = list(smallz_col.find({}).sort("added_at", -1))
    except Exception as e:
        print(f"admin smallz error: {e}", flush=True)
        return await msg.edit_text("❌ خطا در خواندن Smallz.", reply_markup=back)

    lines = ["🎯 **مدیریت Smallz**\n",
             "_افراد Smallz با دستور /smallz به پنل کوچک خودشان دسترسی دارند: "
             "تعداد کل کاربران ربات (بدون جزئیات) و لینک رفرال شخصی خودشان._\n"]
    rows = []
    if not docs:
        lines.append("_هیچ Smallz ثبت نشده._")
    for d in docs:
        cid = d.get("chat_id")
        u = users_col.find_one({"chat_id": cid}) if users_col is not None else None
        name = (u.get("first_name") if u else None) or str(cid)
        lines.append(f"• **{name}** — `{cid}` — 🔗 {d.get('referrals', 0)} رفرال")
        rows.append([InlineKeyboardButton(f"🗑 حذف {name[:18]}", callback_data=f"adm_smallzdel_{cid}")])
    rows.append([InlineKeyboardButton("➕ افزودن Smallz", callback_data="adm_smallzadd")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="adm_home")])
    await msg.edit_text("\n".join(lines)[:4000], reply_markup=InlineKeyboardMarkup(rows))


async def render_admin_maint_allowlist(msg: Message):
    back = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="adm_home")]])
    allowlist = get_settings().get("maintenance_allowlist") or []

    lines = ["🛡 **دسترسی در حالت تعمیرات**\n",
             "_کاربرانی که حتی وقتی ربات خاموش است می‌توانند از آن استفاده کنند._\n"]
    rows = []
    if not allowlist:
        lines.append("_هیچ کاربری اضافه نشده._")
    for cid in allowlist:
        u = users_col.find_one({"chat_id": cid}) if users_col is not None else None
        name = (u.get("first_name") if u else None) or str(cid)
        lines.append(f"• **{name}** — `{cid}`")
        rows.append([InlineKeyboardButton(f"🗑 حذف {name[:18]}", callback_data=f"adm_maintdel_{cid}")])
    rows.append([InlineKeyboardButton("➕ افزودن کاربر", callback_data="adm_maintadd")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="adm_home")])
    await msg.edit_text("\n".join(lines)[:4000], reply_markup=InlineKeyboardMarkup(rows))


async def render_admin_fj_allowlist(msg: Message):
    back = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="adm_home")]])
    allowlist = get_settings().get("force_join_allowlist") or []

    lines = ["🚪 **معافیت از عضویت اجباری**\n",
             "_این کاربران هرگز پیام «ابتدا در کانال‌ها عضو شوید» را نمی‌بینند و بدون اطلاع از "
             "وجود عضویت اجباری، مستقیم از ربات استفاده می‌کنند._\n"]
    rows = []
    if not allowlist:
        lines.append("_هیچ کاربری اضافه نشده._")
    for cid in allowlist:
        u = users_col.find_one({"chat_id": cid}) if users_col is not None else None
        name = (u.get("first_name") if u else None) or str(cid)
        lines.append(f"• **{name}** — `{cid}`")
        rows.append([InlineKeyboardButton(f"🗑 حذف {name[:18]}", callback_data=f"adm_fjdel_{cid}")])
    rows.append([InlineKeyboardButton("➕ افزودن کاربر", callback_data="adm_fjadd")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="adm_home")])
    await msg.edit_text("\n".join(lines)[:4000], reply_markup=InlineKeyboardMarkup(rows))


async def broadcast(client: Client, text: str = None, status: Message = None, text_fn=None):
    """Send to every known user, paced so Telegram doesn't rate-limit us.

    text_fn, when given, is called per-recipient chat_id to produce that
    user's message -- used for bot-authored announcements (e.g. maintenance
    on/off) so each user gets it in their own language, unlike an admin's
    free-typed broadcast text which is sent as-is to everyone.
    """
    if users_col is None:
        return 0, 0
    try:
        targets = [d["chat_id"] for d in users_col.find({"banned": {"$ne": True}}, {"chat_id": 1})]
    except Exception as e:
        print(f"broadcast read error: {e}", flush=True)
        return 0, 0
    sent = failed = 0
    for i, cid in enumerate(targets):
        if is_admin(cid):
            continue
        try:
            await client.send_message(cid, text_fn(cid) if text_fn else text, parse_mode=ParseMode.DISABLED)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.06)  # ~16 msg/s, well under Telegram's limit
        if status is not None and i % 25 == 24:
            try:
                await status.edit_text(f"📢 در حال ارسال... {sent}/{len(targets)}")
            except Exception:
                pass
    return sent, failed


# ---------------- 7. BOT ROUTERS ----------------
def lang_picker_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇮🇷 فارسی", callback_data="setlang_fa"),
         InlineKeyboardButton("🇬🇧 English", callback_data="setlang_en")],
        [InlineKeyboardButton("🇷🇺 Русский", callback_data="setlang_ru"),
         InlineKeyboardButton("🇪🇸 Español", callback_data="setlang_es")],
    ])


LANG_PICKER_TEXT = "🌐 لطفاً زبان خود را انتخاب کنید / Please choose your language:"


@bot.on_message(filters.regex(r"^/dl_(?P<type>ytm|sc|sp|tr|al|ar|pl)_(?P<id>[\w-]+)"))
async def inline_selection_download_handler(client: Client, message: Message):
    match = message.matches[0]
    dl_type = match.group("type")
    item_id = match.group("id")
    chat_id = message.chat.id

    try:
        await message.delete()
    except Exception:
        pass

    if dl_type == "ytm":
        status = await client.send_message(chat_id, "⏳ در حال دانلود آهنگ از یوتیوب موزیک...")
        await process_official_download(client, chat_id, status, {"id": item_id})
    elif dl_type == "sc":
        status = await client.send_message(chat_id, "⏳ در حال دانلود قطعه از ساندکلاد...")
        sc_url = f"https://api.soundcloud.com/tracks/{item_id}"
        await process_sc_download(client, chat_id, status, sc_url, item_id)
    elif dl_type == "sp":
        status = await client.send_message(chat_id, "⏳ در حال دریافت موزیک اسپاتیفای...")
        await process_spotify_link(client, chat_id, status, f"https://open.spotify.com/track/{item_id}")
    elif dl_type == "tr":
        status = await client.send_message(chat_id, "⏳ در حال دریافت موزیک اورجینال...")
        await process_deezer_track(client, chat_id, status, item_id)
    elif dl_type == "al":
        status = await client.send_message(chat_id, "⏳ در حال دریافت مشخصات آلبوم...")
        await process_deezer_album(client, chat_id, status, item_id)
    elif dl_type == "ar":
        status = await client.send_message(chat_id, "⏳ در حال دریافت آثار هنرمند...")
        await process_deezer_artist(client, chat_id, status, item_id)
    elif dl_type == "pl":
        status = await client.send_message(chat_id, "⏳ در حال پردازش پلی‌لیست...")
        await render_collection(client, chat_id, status, "dzp", item_id)


@bot.on_message(filters.command("start") | filters.regex("(?i)^/menu$"))
async def start_handler(client: Client, message: Message):
    chat_id = message.chat.id
    user_states.pop(chat_id, None)
    has_lang = users_col is not None and bool((users_col.find_one({"chat_id": chat_id}, {"lang": 1}) or {}).get("lang"))
    if not has_lang:
        return await message.reply_text(LANG_PICKER_TEXT, reply_markup=lang_picker_kb())

    # Deep linking support for inline queries and shared links
    if len(message.command) > 1:
        param = message.command[1].strip()
        try:
            await message.delete()
        except Exception:
            pass
        if param.startswith("tr_"):
            track_id = param[3:]
            status = await client.send_message(chat_id, "⏳ این آهنگ در حال دانلود است...")
            return await process_deezer_track(client, chat_id, status, track_id)
        elif param.startswith("yta_"):
            vid_id = param[4:]
            status = await client.send_message(chat_id, "⏳ این آهنگ در حال دانلود است...")
            return await process_official_download(client, chat_id, status, {"id": vid_id})
        elif param.startswith("sp_"):
            sp_id = param[3:]
            status = await client.send_message(chat_id, "⏳ این آهنگ در حال دانلود است...")
            return await process_spotify_link(client, chat_id, status, f"https://open.spotify.com/track/{sp_id}")
        elif param.startswith("sc_"):
            sc_id = param[3:]
            status = await client.send_message(chat_id, "⏳ این آهنگ در حال دانلود است...")
            sc_url = f"https://api.soundcloud.com/tracks/{sc_id}"
            return await process_sc_download(client, chat_id, status, sc_url, sc_id)
        elif param.startswith("al_"):
            album_id = param[3:]
            status = await message.reply_text("⏳ در حال دریافت مشخصات آلبوم...")
            return await process_deezer_album(client, chat_id, status, album_id)
        elif param.startswith("ar_"):
            artist_id = param[3:]
            status = await message.reply_text("⏳ در حال دریافت آثار هنرمند...")
            return await process_deezer_artist(client, chat_id, status, artist_id)
        elif param.startswith("pl_") or param.startswith("dzpl_"):
            playlist_id = param.split("_", 1)[1]
            status = await message.reply_text("⏳ در حال پردازش پلی‌لیست...")
            return await render_collection(client, chat_id, status, "dzp", playlist_id)
        elif param.startswith("ytal_"):
            status = await message.reply_text("⏳ در حال دریافت مشخصات آلبوم...")
            return await render_collection(client, chat_id, status, "yta", param[5:])
        elif param.startswith("ytpl_"):
            status = await message.reply_text("⏳ در حال پردازش پلی‌لیست...")
            return await render_collection(client, chat_id, status, "ytp", param[5:])
        elif param.startswith("ytar_"):
            status = await message.reply_text("⏳ در حال دریافت آثار هنرمند...")
            return await render_ytm_artist(client, chat_id, status, param[5:])
        elif param.startswith("opt_"):
            # "Go to Taha Bot" button under a song shared in a group/inline:
            # resend it here with the full actions keyboard.
            token = param[4:]
            status = await message.reply_text("⏳ در حال دریافت موزیک و گزینه‌ها...")
            doc = find_track(token)
            if doc:
                key = doc.get("vid_id") or token
                if await deliver_by_key(client, chat_id, key, track_actions_kb(key, chat_id), status=status):
                    try:
                        await status.delete()
                    except Exception:
                        pass
                return
            if re.fullmatch(r"[0-9A-Za-z]{22}", token):
                return await process_spotify_link(client, chat_id, status, f"https://open.spotify.com/track/{token}")
            return await process_official_download(client, chat_id, status, {"id": token})
        elif param.startswith("ytv_"):
            vid_id = param[4:]
            status = await message.reply_text("⏳ در حال دریافت گزینه‌های ویدیو...")
            return await fetch_and_verify_video(status, chat_id, vid_id)
        elif param.startswith("ptq_") or param.startswith("pxq_"):
            q = load_search_link(param[4:])
            if not q:
                return await message.reply_text(t(chat_id, "pt_expired_research"))
            status = await message.reply_text(t(chat_id, "searching_images"))
            user_data.setdefault(chat_id, {}).pop("pt_all" if param.startswith("ptq_") else "img_cache_key", None)
            if param.startswith("ptq_"):
                return await render_pinterest_search(status, q, chat_id, 1)
            return await render_image_search(status, q, chat_id, 1, provider="pexels")
        elif param.startswith("pti_"):
            media = load_media_link(param[4:])
            status = await message.reply_text(t(chat_id, "pt_fetching_pin"))
            if not media:
                return await status.edit_text(t(chat_id, "pt_pin_not_found"))
            return await deliver_pinterest_media(client, chat_id, status, media)
        elif param.startswith("px_"):
            status = await message.reply_text(t(chat_id, "img_fetching_full"))
            try:
                r = await asyncio.to_thread(lambda: requests.get(
                    f"https://api.pexels.com/v1/photos/{param[3:]}", headers={"Authorization": PEXELS_API_KEY}, timeout=15).json())
                path = os.path.join(IMAGE_DL_DIR, f"img_{param[3:]}.jpg")
                await asyncio.to_thread(download_url_to_file_sync, r["src"]["original"], path)
                await client.send_document(chat_id, document=path, caption=f"📸 {r.get('photographer', '')}",
                                           reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                                               "🎨 ابزارهای تصویر", callback_data="menu_image_tools")]]))
                os.remove(path)
                return await status.delete()
            except Exception as e:
                print(f"pexels deep link error: {e}", flush=True)
                return await status.edit_text(t(chat_id, "img_fetch_failed"))
        elif param.startswith("pt_"):
            pin_id = param[3:]
            status = await message.reply_text("⏳ در حال دریافت تصویر پینترست...")
            return await process_pinterest_link(client, chat_id, status, f"https://www.pinterest.com/pin/{pin_id}/")
        elif param == "ai":
            user_states[chat_id] = "AI_CHAT"
            AI_HISTORY.pop(chat_id, None)
            return await message.reply_text(
                t(chat_id, "ai_intro_text"),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_ai_exit"), callback_data="ai_exit")]])
            )

    user = message.from_user
    name = (user.first_name if user else "") or "کاربر"
    await message.reply_text(t(chat_id, "main_menu_text", name=name), reply_markup=get_main_keyboard(chat_id))


def smallz_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 کاربران کل ربات", callback_data="smz_users")],
        [InlineKeyboardButton("🔗 لینک رفرال من", callback_data="smz_link")],
    ])


@bot.on_message(filters.command("smallz"))
async def smallz_handler(client: Client, message: Message):
    if not is_smallz(message.chat.id):
        return await message.reply_text("❌ شما به این پنل دسترسی ندارید.")
    await message.reply_text("🎯 **پنل Smallz**\n\nیکی از گزینه‌ها را انتخاب کنید:", reply_markup=smallz_kb())


async def _run_search_command(message: Message, renderer, prompt: str, state: str):
    """Shared body for the /taha* search commands.

    With an argument it searches immediately; without one it sets the normal
    conversational state so the next message is treated as the query.
    """
    chat_id = message.chat.id
    parts = (message.text or "").split(maxsplit=1)
    query = parts[1].strip() if len(parts) > 1 else ""
    if not query:
        user_states[chat_id] = state
        return await message.reply_text(prompt)
    user_states.pop(chat_id, None)
    status = await message.reply_text(t(chat_id, "searching_text"))
    await renderer(status, query, chat_id)


@bot.on_message(filters.command("tahamusic"))
async def cmd_tahamusic(client: Client, message: Message):
    await _run_search_command(message, render_spotify_search, t(message.chat.id, "cmd_music_prompt"), "MUSIC_SEARCH_OFFICIAL")


@bot.on_message(filters.command("tahasoundcloud"))
async def cmd_tahasoundcloud(client: Client, message: Message):
    await _run_search_command(message, render_sc_search, t(message.chat.id, "cmd_sc_prompt"), "MUSIC_SEARCH_SC")


@bot.on_message(filters.command("tahayoutube"))
async def cmd_tahayoutube(client: Client, message: Message):
    await _run_search_command(message, render_video_search, t(message.chat.id, "cmd_yt_prompt"), "YT_KEYWORD")


@bot.on_message(filters.command("tahaspotify"))
async def cmd_tahaspotify(client: Client, message: Message):
    await _run_search_command(message, render_spotify_search, t(message.chat.id, "cmd_spotify_prompt"), "MUSIC_SEARCH_SPOTIFY")


@bot.on_message(filters.command("tahalyrics"))
async def cmd_tahalyrics(client: Client, message: Message):
    await _run_search_command(message, render_lyric_search, t(message.chat.id, "cmd_lyrics_prompt"), "MUSIC_SEARCH_LYRIC")


@bot.on_message(filters.command("tahapinterest"))
async def cmd_tahapinterest(client: Client, message: Message):
    await _run_search_command(message, render_pinterest_search, t(message.chat.id, "cmd_pinterest_prompt"), "PT_SEARCH")


@bot.on_message(filters.command("tahareddit"))
async def cmd_tahareddit(client: Client, message: Message):
    await _run_search_command(message, render_reddit_search, t(message.chat.id, "cmd_reddit_prompt"), "REDDIT_SEARCH")


@bot.on_message(filters.command("tahaphoto"))
async def cmd_tahaphoto(client: Client, message: Message):
    await _run_search_command(message, lambda m, q, c: render_image_search(m, q, c, 1, provider="pexels"),
                              t(message.chat.id, "cmd_photo_prompt"), "IMAGE_SEARCH")


@bot.on_message(filters.command("tahatiktok"))
async def cmd_tahatiktok(client: Client, message: Message):
    chat_id = message.chat.id
    parts = (message.text or "").split(maxsplit=1)
    target = parts[1].strip() if len(parts) > 1 else ""
    if not target:
        user_states[chat_id] = "TIKTOK_TARGET"
        return await message.reply_text(t(chat_id, "cmd_tiktok_prompt"))
    user_states.pop(chat_id, None)
    status = await message.reply_text(t(chat_id, "tiktok_fetching"))
    await process_tiktok_render(client, chat_id, status, target)


@bot.on_message(filters.command("tahamenu"))
async def cmd_tahamenu(client: Client, message: Message):
    chat_id = message.chat.id
    user_states.pop(chat_id, None)
    user = message.from_user
    name = (user.first_name if user else "") or "کاربر"
    await message.reply_text(t(chat_id, "main_menu_text", name=name), reply_markup=get_main_keyboard(chat_id))


@bot.on_message(filters.command("tahaedit"))
async def cmd_tahaedit(client: Client, message: Message):
    chat_id = message.chat.id
    if message.chat.type != ChatType.PRIVATE:
        user_states[chat_id] = "AWAIT_VEDIT_UPLOAD"
    await message.reply_text(t(chat_id, "vedit_menu_intro"))


@bot.on_message(filters.command("tahashazam"))
async def cmd_tahashazam(client: Client, message: Message):
    chat_id = message.chat.id
    if message.chat.type != ChatType.PRIVATE:
        user_states[chat_id] = "AWAIT_SHAZAM_UPLOAD"
    await message.reply_text(t(chat_id, "cmd_shazam_prompt"))


@bot.on_message(filters.command("tahalibrary"))
async def cmd_tahalibrary(client: Client, message: Message):
    status = await message.reply_text(t(message.chat.id, "library_loading"))
    await render_library(status, message.chat.id)


@bot.on_message(filters.command("tahastats"))
async def cmd_tahastats(client: Client, message: Message):
    status = await message.reply_text(t(message.chat.id, "stats_loading"))
    await render_stats(status, message.chat.id)


@bot.on_message(filters.command("tahaai"))
async def cmd_tahaai(client: Client, message: Message):
    chat_id = message.chat.id
    parts = (message.text or "").split(maxsplit=1)
    question = parts[1].strip() if len(parts) > 1 else ""

    if message.chat.type != ChatType.PRIVATE:
        # A persistent AI_CHAT state would swallow every message from every
        # member of the group afterwards -- harmless in a DM, a real problem
        # in a group. Groups get a single one-shot question/answer instead.
        if not question:
            return await message.reply_text(t(chat_id, "ai_group_hint"))
        status = await message.reply_text(t(chat_id, "ai_thinking"))
        try:
            result, _ = await asyncio.to_thread(
                ai_chat.ask_ai, AI_SYSTEM_PROMPT, [{"role": "user", "content": question}], [])
            await status.edit_text(result.get("content") or t(chat_id, "ai_didnt_understand"))
        except Exception:
            await status.edit_text(t(chat_id, "ai_unavailable"))
        return

    user_states[chat_id] = "AI_CHAT"
    AI_HISTORY.pop(chat_id, None)
    if question:
        return await handle_ai_message(client, chat_id, message, question)
    await message.reply_text(
        t(chat_id, "ai_intro_text"),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_ai_exit"), callback_data="ai_exit")]]))


@bot.on_message(filters.command("tahathegoat"))
async def cmd_tahathegoat(client: Client, message: Message):
    status = await message.reply_text("⏳ در حال آماده‌سازی...")
    prompt = (
        "Write ONE warm, funny, over-the-top complimentary paragraph in Persian (Farsi) "
        "praising 'طاها' (Taha), the genius creator of this Telegram bot. Be creative and "
        "enthusiastic -- vary your metaphors, humor, and Persian expressions of praise so it "
        "reads differently each time. 3-5 sentences, Persian only, no English, no markdown."
    )
    try:
        result, _ = await asyncio.to_thread(
            ai_chat.ask_ai, prompt, [{"role": "user", "content": "یک متن تعریف بنویس"}], [])
        text = result.get("content") or "طاها فوق‌العاده است و این ربات ثابتش می‌کنه! 🐐"
    except Exception:
        text = "طاها فوق‌العاده است و این ربات ثابتش می‌کنه! 🐐"
    await status.edit_text(f"🐐 **THE GOAT** 🐐\n\n{text}")


@bot.on_message(filters.command("tahahelp"))
async def cmd_tahahelp(client: Client, message: Message):
    chat_id = message.chat.id
    await message.reply_text(t(chat_id, "help_home_text"), reply_markup=help_keyboard(chat_id))


def register_bot_commands():
    """Publish the command list so Telegram shows it in the '/' menu."""
    if not BOT_TOKEN:
        return
    cmds = [
        {"command": "start", "description": "منوی اصلی ربات"},
        {"command": "tahamenu", "description": "منوی اصلی ربات"},
        {"command": "tahayoutube", "description": "جستجوی ویدیو در یوتیوب"},
        {"command": "tahatiktok", "description": "دانلود از تیک‌تاک"},
        {"command": "tahamusic", "description": "جستجوی موزیک اورجینال"},
        {"command": "tahasoundcloud", "description": "جستجو در ساندکلود"},
        {"command": "tahaspotify", "description": "جستجوی اسپاتیفای"},
        {"command": "tahalyrics", "description": "پیدا کردن آهنگ از روی متن"},
        {"command": "tahaedit", "description": "ویرایشگر ویدیو"},
        {"command": "tahapinterest", "description": "جستجوی پینترست"},
        {"command": "tahareddit", "description": "جستجوی ردیت"},
        {"command": "tahaphoto", "description": "جستجوی تصویر"},
        {"command": "tahashazam", "description": "شناسایی آهنگ"},
        {"command": "tahalibrary", "description": "کتابخانه من"},
        {"command": "tahastats", "description": "آمار من"},
        {"command": "tahaai", "description": "دستیار هوش مصنوعی"},
        {"command": "tahahelp", "description": "راهنمای کامل ربات"},
    ]
    try:
        r = requests.post(f"https://tapi.bale.ai/bot{BOT_TOKEN}/setMyCommands",
                          json={"commands": cmds}, timeout=15)
        print(f"⌨️ command menu registered: {r.json().get('ok')}", flush=True)
    except Exception as e:
        print(f"setMyCommands failed: {e}", flush=True)


@bot.on_message(filters.command("id"))
async def id_handler(client: Client, message: Message):
    """Lets the owner discover the numeric id to put in ADMIN_ID."""
    uid = message.from_user.id if message.from_user else message.chat.id
    await message.reply_text(
        f"🆔 آیدی عددی شما:\n`{uid}`\n\n"
        + ("✅ شما ادمین این ربات هستید." if is_admin(uid)
           else "برای فعال‌سازی پنل مدیریت، این عدد را در متغیر ADMIN_ID قرار دهید."))


@bot.on_message(filters.command("admin"))
async def admin_handler(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else 0
    if not is_admin(uid):
        return  # stay silent: don't reveal the panel exists
    await render_admin_home(message, edit=False)


def parse_inline_query_prefix(q: str):
    """Parses inline query string into (mode, sub_query).
    Supports space or colon separators, case-insensitive.
    """
    q = q.strip()
    if not q:
        return ("guide", "")

    pattern = r"^(ytm_alb|ytm_art|ytm_pl|ytmalb|ytmart|ytmpl|ytmusic|ytm|ytv|video|vid|yt|spotify|track|album|artist|playlist|soundcloud|pinterest|pin|pt|pexels|px|top:week|top:month|charts|top|sp|sc|alb|art|pl|trk|ai|gpt|gemini)(?:[:\s]+(.*)|[:\s]*$)"
    m = re.match(pattern, q, re.IGNORECASE)
    if m:
        p = m.group(1).lower()
        sub = (m.group(2) or "").strip()
        if p in ("sp", "spotify", "trk", "track"):
            return ("sp", sub)
        elif p in ("sc", "soundcloud"):
            return ("sc", sub)
        elif p in ("ytm_alb", "ytmalb"):
            return ("ytm_alb", sub)
        elif p in ("ytm_art", "ytmart"):
            return ("ytm_art", sub)
        elif p in ("ytm_pl", "ytmpl"):
            return ("ytm_pl", sub)
        elif p in ("ytm", "ytmusic"):
            return ("ytm", sub)
        elif p in ("vid", "video", "ytv", "yt"):
            return ("vid", sub)
        elif p in ("px", "pexels"):
            return ("px", sub)
        elif p in ("pt", "pin", "pinterest"):
            return ("pt", sub)
        elif p in ("alb", "album"):
            return ("alb", sub)
        elif p in ("art", "artist"):
            return ("art", sub)
        elif p in ("pl", "playlist"):
            return ("pl", sub)
        elif p in ("top", "top:week", "top:month", "charts"):
            return ("top", sub)
        elif p in ("ai", "gpt", "gemini"):
            return ("ai", sub)

    return ("general", q)


def _clean_str(s) -> str:
    if not s:
        return ""
    return re.sub(r'[\ud800-\udfff]', '', str(s)).strip()


def _lead_thumb(url: str) -> str:
    if url and isinstance(url, str) and url.startswith("http"):
        return f"[\u200b]({url})"
    return ""


def _norm_track_str(s: str) -> str:
    """Normalize song title or artist by removing parens, remasters, and special chars."""
    if not s:
        return ""
    s = re.sub(r"[\(\[\{].*?[\)\]\}]", "", str(s))
    s = re.sub(r"\s*-\s*.*?(?:remaster|live|deluxe|bonus|edit|version|mono|stereo|anniversary).*$", "", s, flags=re.I)
    s = re.sub(r"[^\w\s]", "", s).lower().strip()
    return re.sub(r"\s+", " ", s)


SP_ICON = "https://upload.wikimedia.org/wikipedia/commons/thumb/1/19/Spotify_logo_without_text.svg/240px-Spotify_logo_without_text.svg.png"
YTM_ICON = "https://upload.wikimedia.org/wikipedia/commons/thumb/6/6a/Youtube_Music_icon.svg/240px-Youtube_Music_icon.svg.png"
SC_ICON = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a2/Antu_soundcloud.svg/240px-Antu_soundcloud.svg.png"
VID_ICON = "https://cdn-icons-png.flaticon.com/512/1384/1384060.png"
PIN_ICON = "https://cdn-icons-png.flaticon.com/512/3536/3536559.png"
AI_ICON = "https://cdn-icons-png.flaticon.com/512/4712/4712035.png"
ALB_ICON = "https://cdn-icons-png.flaticon.com/512/3844/3844724.png"
ART_ICON = "https://cdn-icons-png.flaticon.com/512/847/847969.png"
PL_ICON = "https://cdn-icons-png.flaticon.com/512/2995/2995101.png"
TOP_ICON = "https://cdn-icons-png.flaticon.com/512/1828/1828884.png"

# ---------------- INLINE MUSIC SEARCH ----------------
# Result ids encode the track ("sp.<spotify id>", "yt.<video id>",
# "sc.<soundcloud id>", "dz.<deezer id>"), so a chosen result can be resolved
# even after a restart. Cached tracks come back as the real audio
# ("c." prefix, sent instantly by Telegram); everything else is a photo card
# (cover + title + "artist • duration" in the list) whose message reads
# "⏳ downloading..." and is swapped for the audio by chosen_inline_handler.
_INLINE_META = {}            # result id -> (ts, meta)
_INLINE_SEARCH_CACHE = {}    # (mode, query) -> (ts, [meta])
_INLINE_SEARCH_TTL = 180
_DEFAULT_COVER = {"fid": None}
INLINE_LIMIT = 20

_RID_PREFIX = {"spotify": "sp", "ytm": "yt", "yt": "yt", "sc": "sc", "deezer": "dz", "catalog": "sp"}


def _rid(meta: dict):
    src = meta.get("source")
    if src == "sc" and meta.get("sc_id"):
        return f"sc.{meta['sc_id']}"
    if src in ("ytm", "yt") and meta.get("yt_id"):
        return f"yt.{meta['yt_id']}"
    if meta.get("spotify_id"):
        return f"sp.{meta['spotify_id']}"
    if meta.get("deezer_id"):
        return f"dz.{meta['deezer_id']}"
    if meta.get("yt_id"):
        return f"yt.{meta['yt_id']}"
    return None


def _remember_meta(rid: str, meta: dict):
    if len(_INLINE_META) > 5000:
        cutoff = time.time() - 3600
        for k in [k for k, (ts, _) in _INLINE_META.items() if ts < cutoff]:
            _INLINE_META.pop(k, None)
    _INLINE_META[rid] = (time.time(), meta)


async def _meta_for_rid(rid: str):
    hit = _INLINE_META.get(rid)
    if hit:
        return dict(hit[1])
    kind, _, ident = rid.partition(".")
    try:
        if kind == "sp":
            info = await asyncio.to_thread(spotify_track_info_sync, ident)
            if info:
                return make_meta("spotify", title=info["title"], artist=info.get("artists_all") or info.get("artist"),
                                 album=info.get("album"), year=info.get("year"), duration=info.get("duration"),
                                 cover=info.get("cover"), isrc=info.get("isrc"), spotify_id=ident)
        elif kind == "dz":
            info = await asyncio.to_thread(rega_music.get_track_info, ident)
            if info:
                return meta_from_deezer(info)
        elif kind == "yt":
            return make_meta("yt", yt_id=ident)
        elif kind == "sc":
            return make_meta("sc", sc_id=ident)
    except Exception as e:
        print(f"meta refetch failed for {rid}: {e}", flush=True)
    return None


async def _default_cover_fid():
    """A neutral JPEG cover for tracks that have no artwork at all
    (InlineQueryResultPhoto needs a JPEG; the platform icons are PNG/SVG)."""
    if _DEFAULT_COVER["fid"]:
        return _DEFAULT_COVER["fid"]
    stored = get_settings().get("default_cover_fid")
    if stored:
        _DEFAULT_COVER["fid"] = stored
        return stored
    if not MUSIC_CACHE_CHANNEL:
        return None
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (512, 512), (24, 24, 32))
        d = ImageDraw.Draw(img)
        d.ellipse((136, 136, 376, 376), outline=(120, 220, 160), width=14)
        d.ellipse((226, 226, 286, 286), fill=(120, 220, 160))
        path = "music_downloads/default_cover.jpg"
        img.save(path, "JPEG", quality=90)
        sent = await bot.send_photo(int(str(MUSIC_CACHE_CHANNEL).strip()), path, caption="default inline cover",
                                    disable_notification=True)
        fid = sent.photo.file_id
        set_setting("default_cover_fid", fid)
        _DEFAULT_COVER["fid"] = fid
        return fid
    except Exception as e:
        print(f"default cover setup failed: {e}", flush=True)
        return None


def _inline_cover_url(meta: dict):
    url = meta.get("cover") or ""
    if not url.startswith("http"):
        return None
    if "sndcdn.com" in url:
        url = re.sub(r"-(large|t\d+x\d+)\.(jpg|png)$", r"-t500x500.\2", url)
        return url if url.endswith(".jpg") else None
    if url.lower().split("?")[0].endswith((".png", ".webp", ".svg", ".gif")):
        return None
    return url


def _go_to_bot_kb(doc_or_key, bot_username: str):
    key = doc_or_key.get("vid_id") if isinstance(doc_or_key, dict) else str(doc_or_key)
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        "⚡️ رفتن به بات طاها", url=f"https://ble.ir/{bot_username}?start=opt_{lyrics_token(key)}")]])


async def build_track_inline_result(meta: dict, doc, bot_username: str):
    import html as _html
    rid = _rid(meta)
    if not rid:
        return None
    fid = doc_file_id(doc) if doc else None
    if fid:
        return InlineQueryResultCachedAudio(id=f"c.{rid}"[:64], audio_file_id=fid,
                                            reply_markup=_go_to_bot_kb(doc, bot_username),
                                            label=_track_label(meta), description=fmt_dur(meta.get("duration")))
    _remember_meta(rid, meta)
    title = _clean_str(meta.get("title")) or "Track"
    artist = _clean_str(meta.get("artist")) or ""
    dur = fmt_dur(meta.get("duration"))
    desc = " • ".join(x for x in (f"👤 {artist}" if artist else "", f"⏱ {dur}" if dur else "",
                                  f"💿 {meta['album'][:30]}" if meta.get("album") else "") if x)
    caption = (f"🎵 <b>{_html.escape(title)}</b>" + (f" — {_html.escape(artist)}" if artist else "")
               + "\n\n⏳ در حال دانلود... چند لحظه صبر کنید")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏳ در حال دانلود...", callback_data="dl_wait")]])
    cover = _inline_cover_url(meta)
    if cover:
        return InlineQueryResultPhoto(photo_url=cover, thumb_url=cover, id=rid[:64], title=title[:100],
                                      description=desc[:200], caption=caption, parse_mode=ParseMode.HTML,
                                      reply_markup=kb)
    dfid = await _default_cover_fid()
    if dfid:
        return InlineQueryResultCachedPhoto(photo_file_id=dfid, id=rid[:64], title=title[:100],
                                            description=desc[:200], caption=caption,
                                            parse_mode=ParseMode.HTML, reply_markup=kb)
    return None


def _inline_search_metas_sync(mode: str, q: str) -> list:
    if mode == "ytm":
        return [meta_from_ytm(r) for r in ytm_search_sync(q, "songs", INLINE_LIMIT) if r.get("videoId")]
    if mode == "sc":
        return [meta_from_sc(e) for e in sc_search_sync(q, INLINE_LIMIT)]
    if mode == "top":
        return [meta_from_deezer(t_) for t_ in rega_music.get_chart_tracks(INLINE_LIMIT)]
    # "sp" and plain queries: Spotify catalog, Deezer when Spotify is unavailable.
    sp = spotify_search_tracks_sync(q, INLINE_LIMIT)
    if sp:
        return [meta_from_spotify(t_) for t_ in sp]
    return [meta_from_deezer(t_) for t_ in rega_music.search_tracks(q, INLINE_LIMIT)]


async def inline_track_results(mode: str, q: str, bot_username: str) -> list:
    ck = (mode, q.lower())
    hit = _INLINE_SEARCH_CACHE.get(ck)
    if hit and time.time() - hit[0] < _INLINE_SEARCH_TTL:
        metas = hit[1]
    else:
        metas = await asyncio.to_thread(_inline_search_metas_sync, mode, q)
        if len(_INLINE_SEARCH_CACHE) > 500:
            _INLINE_SEARCH_CACHE.clear()
        _INLINE_SEARCH_CACHE[ck] = (time.time(), metas)
    docs = await asyncio.to_thread(cache_find_many, metas)
    out = []
    for i, m in enumerate(metas):
        try:
            r = await build_track_inline_result(dict(m), docs.get(i), bot_username)
            if r is not None:
                out.append(r)
        except Exception as e:
            print(f"inline result build failed: {e}", flush=True)
    return out


def _prompt_article(rid, title, desc, icon, prefix, bot_username, example):
    return InlineQueryResultArticle(
        id=rid, title=title, description=desc, thumb_url=icon,
        input_message_content=InputTextMessageContent(
            f"💡 {example}\n@{bot_username} {prefix} ...", parse_mode=ParseMode.DISABLED),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔎 جستجو", switch_inline_query_current_chat=f"{prefix} ")]]))


def _link_article(rid, title, desc, thumb, text, btn_text, url):
    lead = _lead_thumb(thumb)
    return InlineQueryResultArticle(
        id=rid[:64], title=_clean_str(title)[:100] or "—", description=_clean_str(desc)[:200],
        thumb_url=thumb if (thumb and str(thumb).startswith("http")) else None,
        input_message_content=InputTextMessageContent(f"{lead}{_clean_str(text)}", parse_mode=ParseMode.MARKDOWN),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(btn_text, url=url)]]))


def save_search_link(query: str) -> str:
    """Short, restart-safe token for a search query (start params allow only 64 [A-Za-z0-9_-])."""
    tok = hashlib.md5(query.strip().lower().encode()).hexdigest()[:12]
    if settings_col is not None:
        try:
            settings_col.database["search_links"].update_one(
                {"_id": tok}, {"$set": {"q": query.strip()[:200], "ts": time.time()}}, upsert=True)
        except Exception as e:
            print(f"search link save error: {e}", flush=True)
    return tok


def save_media_link(media: dict) -> str:
    """Pinterest search now returns encrypted pin ids ("e1_Afq...") that the
    Pin API can't resolve, so the full-quality button stores the media itself."""
    tok = hashlib.md5((media.get("url") or "").encode()).hexdigest()[:14]
    if settings_col is not None:
        try:
            settings_col.database["media_links"].update_one(
                {"_id": tok}, {"$set": {"media": media, "ts": time.time()}}, upsert=True)
        except Exception as e:
            print(f"media link save error: {e}", flush=True)
    return tok


def load_media_link(tok: str):
    if settings_col is None:
        return None
    try:
        d = settings_col.database["media_links"].find_one({"_id": tok})
        return d.get("media") if d else None
    except Exception:
        return None


def load_search_link(tok: str):
    if settings_col is None:
        return None
    try:
        d = settings_col.database["search_links"].find_one({"_id": tok})
        return d.get("q") if d else None
    except Exception:
        return None


GUIDE_CARDS = [
    ("sp", "🟢 جستجوی آهنگ در اسپاتیفای", "sp <نام آهنگ>", SP_ICON),
    ("ytm", "🔴 جستجوی آهنگ در یوتیوب موزیک", "ytm <نام آهنگ>", YTM_ICON),
    ("sc", "☁️ جستجوی قطعه در ساندکلاد", "sc <نام قطعه>", SC_ICON),
    ("alb", "📀 جستجوی آلبوم", "alb <نام آلبوم>", ALB_ICON),
    ("art", "👤 جستجوی خواننده", "art <نام خواننده>", ART_ICON),
    ("pl", "📁 جستجوی پلی‌لیست", "pl <نام پلی‌لیست>", PL_ICON),
    ("vid", "🎬 جستجوی ویدیوی یوتیوب", "vid <نام ویدیو>", VID_ICON),
    ("pt", "📌 جستجوی تصاویر پینترست", "pt <موضوع>", PIN_ICON),
    ("px", "🖼 جستجوی تصاویر پکسلز", "px <موضوع>", PIN_ICON),
    ("ai", "🤖 هوش مصنوعی بات طاها", "ai <سوال شما>", AI_ICON),
]


@bot.on_inline_query()
async def inline_handler(client: Client, iq: InlineQuery):
    """Inline search from any chat: sp / ytm / sc tracks (plus plain text =
    Spotify), ytm_alb / ytm_art / ytm_pl, alb / art / pl (Deezer), top, vid,
    pt and ai."""
    raw_query = (iq.query or "").strip()
    bot_username = getattr(client.me, "username", None) or os.environ.get("BOT_USERNAME", "tahasbot")
    mode, sub_q = parse_inline_query_prefix(raw_query)
    results = []
    try:
        if mode == "guide":
            for prefix, title, usage, icon in GUIDE_CARDS:
                results.append(_prompt_article(f"guide_{prefix}", title, f"دستور: {usage}", icon, prefix,
                                               bot_username, f"برای {title[2:]} بنویسید:"))
            results += (await inline_track_results("top", "", bot_username))[:10]

        elif mode in ("sp", "ytm", "sc") and not sub_q:
            icon = {"sp": SP_ICON, "ytm": YTM_ICON, "sc": SC_ICON}[mode]
            results.append(_prompt_article(f"{mode}_prompt", "🔎 نام آهنگ را بنویسید...",
                                           f"مثال: {mode} Shape of You", icon, mode, bot_username,
                                           f"نام آهنگ را بعد از {mode} بنویسید:"))
            if mode == "sp":
                results += (await inline_track_results("top", "", bot_username))[:15]

        elif mode in ("sp", "ytm", "sc", "general") or mode == "top":
            results += await inline_track_results("sp" if mode == "general" else mode, sub_q, bot_username)

        elif mode in ("ytm_alb", "ytm_art", "ytm_pl"):
            if not sub_q:
                results.append(_prompt_article(f"{mode}_prompt", "🔎 نام را بنویسید...", f"{mode} <نام>",
                                               YTM_ICON, mode, bot_username, f"نام را بعد از {mode} بنویسید:"))
            else:
                kind = {"ytm_alb": "albums", "ytm_art": "artists", "ytm_pl": "playlists"}[mode]
                for r in await asyncio.to_thread(ytm_search_sync, sub_q, kind, INLINE_LIMIT):
                    bid = r.get("browseId") or ""
                    thumb = _ytm_thumb(r.get("thumbnails"), 300)
                    if kind == "albums" and bid:
                        artists = ", ".join(a.get("name", "") for a in (r.get("artists") or []))
                        results.append(_link_article(
                            f"ya_{bid}", r.get("title"), f"💿 {artists} • {r.get('year') or ''}", thumb,
                            f"📀 **{r.get('title')}**\n👤 {artists}", "📀 مشاهده و دانلود آلبوم",
                            f"https://ble.ir/{bot_username}?start=ytal_{bid}"))
                    elif kind == "artists" and bid:
                        results.append(_link_article(
                            f"yr_{bid}", r.get("artist"), "👤 Artist", thumb, f"👤 **{r.get('artist')}**",
                            "👤 مشاهده آثار هنرمند", f"https://ble.ir/{bot_username}?start=ytar_{bid}"))
                    elif kind == "playlists" and bid:
                        pid = bid[2:] if bid.startswith("VL") else bid
                        if len(pid) > 58:
                            continue
                        results.append(_link_article(
                            f"yp_{pid}", r.get("title"), f"📁 {r.get('author') or ''}", thumb,
                            f"📁 **{r.get('title')}**\n👤 {r.get('author') or ''}", "📁 مشاهده پلی‌لیست",
                            f"https://ble.ir/{bot_username}?start=ytpl_{pid}"))

        elif mode in ("alb", "art", "pl"):
            if not sub_q:
                icon = {"alb": ALB_ICON, "art": ART_ICON, "pl": PL_ICON}[mode]
                results.append(_prompt_article(f"{mode}_prompt", "🔎 نام را بنویسید...", f"{mode} <نام>",
                                               icon, mode, bot_username, f"نام را بعد از {mode} بنویسید:"))
            elif mode == "alb":
                for a in await asyncio.to_thread(rega_music.search_albums, sub_q, INLINE_LIMIT):
                    results.append(_link_article(
                        f"al_{a['id']}", a.get("title"), f"🎵 {a.get('nb_tracks', 0)} • 👤 {a.get('artist', '')}",
                        a.get("thumb"), f"📀 **{a.get('title')}**\n👤 {a.get('artist', '')}\n🎵 {a.get('nb_tracks', 0)}",
                        "📀 مشاهده و دانلود آلبوم", f"https://ble.ir/{bot_username}?start=al_{a['id']}"))
            elif mode == "art":
                for a in await asyncio.to_thread(rega_music.search_artists, sub_q, INLINE_LIMIT):
                    results.append(_link_article(
                        f"ar_{a['id']}", a.get("name"), f"👥 {a.get('fans', 0):,} fans", a.get("thumb"),
                        f"👤 **{a.get('name')}**", "👤 مشاهده آثار هنرمند",
                        f"https://ble.ir/{bot_username}?start=ar_{a['id']}"))
            else:
                for p_ in await asyncio.to_thread(rega_music.search_playlists, sub_q, INLINE_LIMIT):
                    results.append(_link_article(
                        f"pl_{p_['id']}", p_.get("title"), f"📁 {p_.get('user', '')} • {p_.get('nb_tracks', 0)} tracks",
                        p_.get("thumb"), f"📁 **{p_.get('title')}**\n👤 {p_.get('user', '')}",
                        "📁 مشاهده پلی‌لیست", f"https://ble.ir/{bot_username}?start=dzpl_{p_['id']}"))

        elif mode == "vid":
            if not sub_q:
                results.append(_prompt_article("vid_prompt", "🎬 نام ویدیو را بنویسید...", "vid <نام ویدیو>",
                                               VID_ICON, "vid", bot_username, "نام ویدیو را بعد از vid بنویسید:"))
            else:
                def _yt_vid_search():
                    with yt_dlp.YoutubeDL({'extract_flat': True, 'quiet': True, 'no_warnings': True}) as ydl:
                        info = ydl.extract_info(f"ytsearch15:{sub_q}", download=False)
                        return [e for e in (info.get('entries') or []) if e]
                for v in await asyncio.to_thread(_yt_vid_search):
                    vid_id = str(v.get("id"))
                    title = _clean_str(v.get("title", "YouTube Video"))
                    channel = _clean_str(v.get("uploader") or v.get("channel") or "YouTube")
                    thumb = f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"
                    results.append(_link_article(
                        f"vid_{vid_id}", title, f"🎬 {channel} • {fmt_dur(v.get('duration'))}", thumb,
                        f"🎬 **{title}**\n👤 {channel}\n⏱ {fmt_dur(v.get('duration'))}",
                        "🎬 دانلود ویدیو و ابزارها", f"https://ble.ir/{bot_username}?start=ytv_{vid_id}"))

        elif mode == "pt":
            if not sub_q:
                results.append(_prompt_article("pt_prompt", "📌 موضوع تصویر را بنویسید...", "pt <موضوع>",
                                               PIN_ICON, "pt", bot_username, "موضوع را بعد از pt بنویسید:"))
            else:
                qtok = await asyncio.to_thread(save_search_link, sub_q)
                for i, p_ in enumerate((await asyncio.to_thread(pinterest_search_sync, sub_q, 15))[:15]):
                    m = _pin_to_media(p_)
                    if not (m and m.get("url")):
                        continue
                    photo_url = m["url"] if m["type"] == "photo" else (m.get("thumb") or "")
                    if not photo_url.lower().split("?")[0].endswith((".jpg", ".jpeg")):
                        continue
                    pin_id = await asyncio.to_thread(save_media_link, m)
                    pin_title = _clean_str(m.get("title")) or "Pinterest"
                    results.append(InlineQueryResultPhoto(
                        id=f"pt_{i}_{pin_id}"[:64], photo_url=photo_url, thumb_url=m.get("thumb") or photo_url,
                        title=pin_title[:100], caption=f"📌 {pin_title[:200]}\n⚡️ @{bot_username}",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("⬇️ کیفیت اصلی", url=f"https://ble.ir/{bot_username}?start=pti_{pin_id}"),
                            InlineKeyboardButton("🔎 ادامه در بات طاها", url=f"https://ble.ir/{bot_username}?start=ptq_{qtok}")]])))

        elif mode == "px":
            if not sub_q:
                results.append(_prompt_article("px_prompt", "🖼 موضوع تصویر را بنویسید...", "px <موضوع>",
                                               PIN_ICON, "px", bot_username, "موضوع را بعد از px بنویسید:"))
            elif PEXELS_API_KEY:
                qtok = await asyncio.to_thread(save_search_link, sub_q)
                for ph in await asyncio.to_thread(pexels_search_sync, sub_q, 1, 15):
                    med = ph["src"]["medium"]
                    results.append(InlineQueryResultPhoto(
                        id=f"px_{ph['id']}"[:64], photo_url=ph["src"].get("large") or med, thumb_url=med,
                        title=f"📸 {ph.get('photographer', '')}"[:100],
                        caption=f"📸 {ph.get('photographer', '')}\n⚡️ @{bot_username}",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("⬇️ کیفیت اصلی", url=f"https://ble.ir/{bot_username}?start=px_{ph['id']}"),
                            InlineKeyboardButton("🔎 ادامه در بات طاها", url=f"https://ble.ir/{bot_username}?start=pxq_{qtok}")]])))

        elif mode == "ai":
            if not sub_q:
                results.append(_prompt_article("ai_prompt", "🤖 سوال خود را بنویسید...", "ai <سوال شما>",
                                               AI_ICON, "ai", bot_username, "سوال را بعد از ai بنویسید:"))
            else:
                try:
                    res, _ = await asyncio.wait_for(asyncio.to_thread(
                        ai_chat.ask_ai,
                        "You are Taha Bot AI assistant. Answer concisely and helpfully in the user's language.",
                        [{"role": "user", "content": sub_q}], []), timeout=8)
                    content = _clean_str(res.get("content") or "پاسخی دریافت نشد.")[:3000]
                    results.append(InlineQueryResultArticle(
                        id=f"ai_{hashlib.md5(sub_q.encode()).hexdigest()[:12]}",
                        title=f"🤖 {sub_q[:50]}", description=content[:100], thumb_url=AI_ICON,
                        input_message_content=InputTextMessageContent(
                            f"❓ {sub_q}\n\n🤖 {content}\n\n⚡️ @{bot_username}", parse_mode=ParseMode.DISABLED),
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                            "💬 ادامه گفتگو در ربات", url=f"https://ble.ir/{bot_username}?start=ai")]])))
                except Exception as e:
                    print(f"inline AI error: {e}", flush=True)
    except Exception as e:
        print(f"inline_handler error: {e}\n{traceback.format_exc()[-800:]}", flush=True)

    seen, final = set(), []
    for r in results:
        if r is None or r.id in seen:
            continue
        seen.add(r.id)
        final.append(r)
        if len(final) >= INLINE_LIMIT + 10:
            break
    try:
        await iq.answer(final, cache_time=5, is_personal=False, is_gallery=False,
                        switch_pm_text="🎵 منوی اصلی ربات بات طاها", switch_pm_parameter="start")
    except Exception as e:
        print(f"Inline answer error: {e}", flush=True)
        # One bad result must not blank the whole menu: retry with plain cards.
        try:
            safe = [r for r in final if isinstance(r, (InlineQueryResultCachedAudio, InlineQueryResultArticle))]
            await iq.answer(safe, cache_time=5, is_personal=False,
                            switch_pm_text="🎵 منوی اصلی ربات بات طاها", switch_pm_parameter="start")
        except Exception:
            pass


@bot.on_chosen_inline_result()
async def chosen_inline_handler(client: Client, cir: ChosenInlineResult):
    """The user picked an uncached track: the message they sent says
    "⏳ downloading"; build the song and replace that message with the audio."""
    rid = cir.result_id or ""
    uid = cir.from_user.id if cir.from_user else 0
    if uid:
        _LAST_INLINE_PICK[uid] = (rid, time.time())  # for picks made inside the bot's own chat
    if rid.startswith("c.") or rid.split(".", 1)[0] not in ("sp", "yt", "sc", "dz"):
        return
    imid = cir.inline_message_id
    print(f"⚡️ inline pick {rid} by {uid} (inline_msg={'yes' if imid else 'NO'})", flush=True)
    meta = await _meta_for_rid(rid)
    bot_username = getattr(client.me, "username", None) or "tahasbot"
    label = _track_label(meta or {}) or "..."
    try:
        if not meta:
            raise RuntimeError("track metadata unavailable")
        # Bale build: inline picks are emulated in a chat ("bale:<chat>:<msg>"),
        # which doubles as the upload target when there is no cache channel.
        pick_chat = int(imid.split(":")[1]) if imid and str(imid).startswith("bale:") else uid
        doc = await obtain_track(meta, upload_chat=pick_chat)
        sent_here = doc.pop("_sent_msg", None) if isinstance(doc, dict) else None
        fid = doc_file_id(doc)
        if not fid:
            raise RuntimeError("no file id after upload")
        if uid:
            log_activity(uid, "music_inline", label)
            log_history(uid, doc.get("vid_id"), doc.get("title"), doc.get("artist"))
        if sent_here is not None:
            try:
                await sent_here.edit_reply_markup(_go_to_bot_kb(doc, bot_username))
            except Exception:
                pass
            if imid and str(imid).startswith("bale:"):
                _c, _m = imid.split(":")[1:3]
                await client.delete_messages(int(_c), int(_m))
        elif imid:
            await client.edit_inline_media(imid, InputMediaAudio(fid), reply_markup=_go_to_bot_kb(doc, bot_username))
    except Exception as e:
        print(f"inline delivery failed for {rid}: {e}\n{traceback.format_exc()[-800:]}", flush=True)
        if imid:
            try:
                await client.edit_inline_caption(imid, f"❌ دانلود «{label}» ناموفق بود. دوباره امتحان کنید.",
                                                 reply_markup=None)
            except Exception:
                pass


@bot.on_callback_query()
async def callback_router(client: Client, query: CallbackQuery):
    data = query.data or ""
    if data in ("dl_wait", "downloading_placeholder"):
        return await query.answer("⏳ این قطعه در حال دانلود و آماده‌سازی است...", show_alert=False)

    await query.answer()
    chat_id = query.message.chat.id if query.message else None
    msg = query.message
    if chat_id is None:
        return  # buttons on inline messages carry no chat; nothing below applies
    user_data.setdefault(chat_id, {})  # several branches index user_data[chat_id] directly

    if data == "menu_main":
        user_states.pop(chat_id, None)
        name = (query.from_user.first_name if query.from_user else "") or "کاربر"
        await msg.edit_text(t(chat_id, "main_menu_text", name=name), reply_markup=get_main_keyboard(chat_id))

    elif data.startswith("setlang_"):
        set_lang(chat_id, data.split("_", 1)[1])
        await msg.edit_text(t(chat_id, "main_menu_text"), reply_markup=get_main_keyboard(chat_id))

    
    elif data == "menu_youtube":
        user_states.pop(chat_id, None)
        await msg.edit_text(
            "▶️ **منوی یوتیوب**\n\nبرای دانلود، کافیست لینک ویدیوی یوتیوب را بفرستید.\nیا یکی از گزینه‌های زیر را انتخاب کنید:",
            reply_markup=get_youtube_keyboard(chat_id, get_lang)
        )

    elif data == "yt_search_menu":
        await msg.edit_text(
            "🔍 **جستجو در یوتیوب**\n\nنحوه جستجو را انتخاب کنید:",
            reply_markup=get_youtube_search_keyboard(chat_id, get_lang)
        )

    elif data == "sub_add_yt":
        user_states[chat_id] = "SUB_ADD"
        await msg.edit_text(
            "🔔 **اشتراک کانال یوتیوب**\n\nلطفاً آیدی کانال را با @ یا لینک کامل آن را ارسال کنید:\nمثال: `@MrBeast` یا `https://youtube.com/@MrBeast`",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_youtube")]])
        )

    elif data == "menu_ig_tiktok":
        user_states.pop(chat_id, None)
        await msg.edit_text(
            "📸 **اینستاگرام و تیک‌تاک**\n\nلطفاً لینک پست، ریلز یا استوری اینستاگرام یا ویدیوی تیک‌تاک را مستقیماً ارسال کنید تا فوری دانلود شود.",
            reply_markup=get_ig_tiktok_keyboard(chat_id, get_lang)
        )

    elif data == "sub_add_ig_tiktok":
        user_states[chat_id] = "SUB_ADD"
        await msg.edit_text(
            "🔔 **اشتراک صفحه اینستاگرام یا تیک‌تاک**\n\nلطفاً لینک یا آیدی صفحه را ارسال کنید:\nمثال: `https://instagram.com/username` یا `@username`",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_ig_tiktok")]])
        )

    elif data in ("menu_reddit_twitter", "menu_reddit"):
        user_states.pop(chat_id, None)
        await msg.edit_text(
            "🐦 **ردیت و توییتر (X)**\n\nلینک پست ردیت یا توییت (x.com / twitter.com) را بفرستید تا عکس‌ها و ویدیوهای آن دانلود شود، یا در ردیت جستجو کنید:",
            reply_markup=get_reddit_twitter_keyboard(chat_id, get_lang)
        )

    elif data == "sub_add_reddit_twitter":
        user_states[chat_id] = "SUB_ADD"
        await msg.edit_text(
            "🔔 **اشتراک ساب‌ردیت**\n\nنام ساب‌ردیت را بفرستید تا پست‌های جدیدش برایتان ارسال شود:\nمثال: `r/memes`\n\n_اشتراک توییتر به دلیل محدودیت‌های X پشتیبانی نمی‌شود، اما لینک توییت‌ها مستقیم دانلود می‌شوند._",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_reddit_twitter")]])
        )

    elif data == "menu_music_spotify" or data == "menu_music":
        user_states.pop(chat_id, None)
        await msg.edit_text(
            "🟢 **اسپاتیفای (Spotify) و جستجوی موزیک**\n\nبرای جستجوی آسان قطعات، آلبوم‌ها یا خواننده‌ها یکی از گزینه‌های زیر را انتخاب کنید یا نام موزیک را مستقیماً ارسال کنید:",
            reply_markup=get_music_hub_keyboard(chat_id, "spotify", get_lang)
        )

    elif data.startswith("regatrk_"):
        track_id = data.split("_", 1)[1]
        status = await msg.reply_text("⏳ در حال دریافت قطعه...")
        await process_deezer_track(client, chat_id, status, track_id)

    elif data.startswith("ytt_"):
        meta = dict(user_data.get(chat_id, {}).get("col_meta", {}).get(data) or make_meta("yt", yt_id=data[4:]))
        status = await client.send_message(chat_id, t(chat_id, "track_downloading", track=_track_label(meta) or "..."))
        await deliver_track(client, chat_id, meta, status=status, activity="music_ytm")

    elif data.startswith("colp_"):
        _, kind, page_s, cid = data.split("_", 3)
        await render_collection(client, chat_id, None, kind, cid, int(page_s), edit_msg=msg)

    elif data.startswith("cola_"):
        _, kind, cid = data.split("_", 2)
        await query.answer("⏳ دانلود همه قطعات آغاز شد.", show_alert=False)
        asyncio.create_task(download_collection(client, chat_id, kind, cid))

    elif data.startswith("regaalb_"):
        album_id = data.split("_", 1)[1]
        status = await msg.reply_text("⏳ در حال بارگذاری آلبوم...")
        await process_deezer_album(client, chat_id, status, album_id)

    elif data.startswith("albpage_"):
        parts = data.split("_")
        album_id = parts[1]
        page = int(parts[2]) if len(parts) > 2 else 0
        status = await msg.reply_text("⏳ در حال بارگذاری صفحه...")
        await process_deezer_album(client, chat_id, status, album_id, page=page)

    elif data.startswith("dl_all_alb_"):
        album_id = data.split("dl_all_alb_", 1)[1]
        await query.answer("⏳ دانلود کل آلبوم آغاز شد.", show_alert=False)
        asyncio.create_task(process_download_all_album(client, chat_id, album_id))

    elif data.startswith("regaart_"):
        artist_id = data.split("_", 1)[1]
        status = await msg.reply_text("⏳ در حال دریافت آثار هنرمند...")
        await process_deezer_artist(client, chat_id, status, artist_id)

    elif data == "menu_music_sc":
        user_states.pop(chat_id, None)
        await msg.edit_text(
            "☁️ **ساندکلاد (SoundCloud)**\n\nلینک ساندکلاد را بفرستید یا یکی از بخش‌های زیر را برای جستجو انتخاب کنید:",
            reply_markup=get_music_hub_keyboard(chat_id, "sc", get_lang)
        )

    elif data == "menu_music_ytm":
        user_states.pop(chat_id, None)
        await msg.edit_text(
            "🔴 **یوتیوب موزیک (YouTube Music)**\n\nلینک یوتیوب موزیک را بفرستید یا یکی از بخش‌های زیر را برای جستجو انتخاب کنید:",
            reply_markup=get_music_hub_keyboard(chat_id, "ytm", get_lang)
        )

    elif data.startswith("msearch_"):
        parts = data.split("_")
        mtype = parts[1]
        plat = parts[2] if len(parts) > 2 else "spotify"
        user_states[chat_id] = f"MSEARCH_{mtype.upper()}:{plat}"
        labels = {"track": "آهنگ", "album": "آلبوم", "artist": "خواننده / آرتیست", "playlist": "پلی‌لیست"}
        lbl = labels.get(mtype, mtype)
        await msg.edit_text(
            f"🔍 لطفاً نام **{lbl}** مورد نظر را تایپ کرده و ارسال کنید:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data=f"menu_music_{plat}")]])
        )

    elif data == "menu_pinterest":
        user_states.pop(chat_id, None)
        await msg.edit_text(
            "📌 **پینترست (Pinterest)**\n\nلینک پین، بورد یا کلمه کلیدی را بفرستید، یا از دکمه‌های زیر استفاده کنید:",
            reply_markup=get_image_platform_keyboard(chat_id, "pinterest", get_lang)
        )

    elif data == "menu_pexels":
        user_states.pop(chat_id, None)
        await msg.edit_text(
            "🖼 **پکسلز (Pexels)**\n\nبرای جستجو در آرشیو تصاویر باکیفیت پکسلز، دکمه جستجو را لمس کنید:",
            reply_markup=get_image_platform_keyboard(chat_id, "pexels", get_lang)
        )

    elif data == "menu_google_images":
        user_states.pop(chat_id, None)
        await msg.edit_text(
            "🔍 **تصاویر گوگل (Google Images)**\n\nبرای جستجوی تصاویر با کیفیت از گوگل، دکمه جستجو را لمس کنید:",
            reply_markup=get_image_platform_keyboard(chat_id, "google", get_lang)
        )

    elif data.startswith("img_search_"):
        plat = data.split("_")[2]
        user_states[chat_id] = f"IMG_SEARCH:{plat}"
        await msg.edit_text(
            "🔍 کلمه کلیدی یا عبارت مورد نظر برای جستجوی تصویر را بفرستید:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data=f"menu_{plat if plat != 'google' else 'google_images'}")]])
        )

    elif data.startswith("img_history_"):
        await render_image_history(msg, chat_id, data.split("_", 2)[2])

    elif data.startswith("imgh_"):
        _, plat, idx = data.split("_", 2)
        items = user_data.get(chat_id, {}).get("img_history_items") or []
        if int(idx) >= len(items):
            return await client.send_message(chat_id, t(chat_id, "pt_expired_research"))
        it = items[int(idx)]
        status = await client.send_message(chat_id, t(chat_id, "img_fetching_full"))
        await deliver_pinterest_media(client, chat_id, status,
                                      {"type": it.get("type") or "photo", "url": it["url"],
                                       "title": it.get("title") or "", "id": hashlib.md5(it["url"].encode()).hexdigest()[:10]},
                                      source=it.get("source"))

    elif data == "menu_music_tools" or data.startswith("mtool_") or data.startswith("mtoolspeed_") or data.startswith("mtoolconv_"):
        await handle_music_tool_callback(client, query, user_data, user_states, get_lang)

    elif data == "menu_image_tools" or data.startswith("itool_") or data.startswith("itoolconv_") or data.startswith("itoolcrop_") or data.startswith("itoolfltr_"):
        await handle_image_tool_callback(client, query, user_data, user_states, get_lang)

    elif data == "tool_select_video":
        await msg.edit_text(t(chat_id, "vedit_menu_intro"), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_main")]]))

    elif data == "tool_select_music":
        await handle_music_tool_callback(client, query, user_data, user_states, get_lang)

    elif data == "tool_select_image":
        await handle_image_tool_callback(client, query, user_data, user_states, get_lang)

    elif data == "menu_settings":
        await msg.edit_text(t(chat_id, "settings_text"), reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🇮🇷 فارسی", callback_data="setlang_fa"),
             InlineKeyboardButton("🇬🇧 English", callback_data="setlang_en")],
            [InlineKeyboardButton("🇷🇺 Русский", callback_data="setlang_ru"),
             InlineKeyboardButton("🇪🇸 Español", callback_data="setlang_es")],
            [InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_main")],
        ]))

    elif data == "menu_downloader":
        await msg.edit_text(t(chat_id, "dl_menu_text"), reply_markup=get_dl_keyboard(chat_id))

    elif data == "menu_music":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t(chat_id, "btn_engine_sc"), callback_data="engine_sc")],
            [InlineKeyboardButton(t(chat_id, "btn_engine_spotify"), callback_data="engine_spotify")],
            [InlineKeyboardButton(t(chat_id, "btn_engine_lyric"), callback_data="engine_lyric")],
            [InlineKeyboardButton(t(chat_id, "btn_library"), callback_data="my_library"),
             InlineKeyboardButton(t(chat_id, "btn_stats"), callback_data="my_stats")],
            [InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_main")]
        ])
        await msg.edit_text(t(chat_id, "music_menu_text"), reply_markup=kb)

    elif data == "engine_lyric":
        user_states[chat_id] = "MUSIC_SEARCH_LYRIC"
        await msg.edit_text(
            t(chat_id, "lyric_search_prompt"),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_music")]]))

    elif data.startswith("lrc_"):
        token = data.split("_", 1)[1]
        doc = find_track(token)
        if not doc or not doc.get("title"):
            return await msg.reply_text(t(chat_id, "track_info_not_found"))
        rec = await asyncio.to_thread(lrclib_lookup, doc.get("title", ""), doc.get("artist", ""))
        synced = (rec or {}).get("syncedLyrics")
        if not synced:
            return await msg.reply_text(t(chat_id, "lrc_not_available"))
        safe = re.sub(r'[^\w\s.-]', '', f"{doc.get('title','track')}").strip()[:50] or "lyrics"
        path = f"music_downloads/{safe}.lrc"
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(synced)
            await client.send_document(
                chat_id, path,
                caption=t(chat_id, "lrc_file_caption", title=doc.get('title','')))
        finally:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    elif data.startswith("fx_"):
        token = data.split("_", 1)[1]
        rows = [[InlineKeyboardButton(effect_label(chat_id, code), callback_data=f"fxgo_{code}_{token}")]
                for code in EFFECTS]
        await msg.reply_text(t(chat_id, "fx_choose_prompt"),
                             reply_markup=InlineKeyboardMarkup(rows))

    elif data.startswith("fxgo_"):
        _, code, token = data.split("_", 2)
        if code not in EFFECTS:
            return await msg.reply_text(t(chat_id, "fx_invalid"))
        status = await msg.reply_text(t(chat_id, "fx_preparing"))
        await process_effect(client, chat_id, status, token, code)

    elif data.startswith("speed_"):
        token = data.split("_", 1)[1]
        rows = [
            [
                InlineKeyboardButton("🐌 0.75x", callback_data=f"spdgo_0.75_{token}"),
                InlineKeyboardButton("🐢 0.85x", callback_data=f"spdgo_0.85_{token}"),
                InlineKeyboardButton("▶️ 1.0x", callback_data=f"spdgo_1.0_{token}")
            ],
            [
                InlineKeyboardButton("🐇 1.15x", callback_data=f"spdgo_1.15_{token}"),
                InlineKeyboardButton("⚡ 1.25x", callback_data=f"spdgo_1.25_{token}"),
                InlineKeyboardButton("🚀 1.5x", callback_data=f"spdgo_1.5_{token}")
            ],
            [InlineKeyboardButton(t(chat_id, "btn_cancel"), callback_data="addplcancel")]
        ]
        await msg.reply_text("⏩ **تنظیم سرعت پخش موزیک:**\nیک سرعت را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(rows))

    elif data.startswith("spdgo_"):
        _, factor, token = data.split("_", 2)
        doc = find_track(token)
        if not doc or not doc.get("file_id"):
            return await msg.reply_text(t(chat_id, "effect_source_not_found"))
        status = await msg.reply_text(f"⏩ در حال اعمال سرعت {factor}x...")
        src = None
        out = None
        try:
            from modules.music_tools import change_audio_speed
            src = await client.download_media(doc["file_id"], file_name=f"music_downloads/spd_{token}_src.mp3")
            out = await asyncio.to_thread(change_audio_speed, src, float(factor))
            title = doc.get("title", "Track")
            artist = doc.get("artist", "")
            await client.send_audio(
                chat_id, audio=out,
                title=f"{title} ({factor}x)",
                performer=artist, tag_source=src,
                caption=f"⏩ **سرعت {factor}x**\n🎵 {title}",
                reply_markup=track_actions_kb(token, chat_id)
            )
            await status.delete()
        except Exception as e:
            print(f"speed error: {e}", flush=True)
            await status.edit_text("❌ خطا در تغییر سرعت موزیک.")
        finally:
            for p in (src, out):
                if p and os.path.exists(p):
                    try: os.remove(p)
                    except OSError: pass

    elif data.startswith("trim_"):
        token = data.split("_", 1)[1]
        doc = find_track(token)
        if not doc or not doc.get("file_id"):
            return await msg.reply_text(t(chat_id, "effect_source_not_found"))
        user_states[chat_id] = f"MTOOL_TRIM:{token}"
        dur = doc.get("duration", 0)
        dur_str = f"{dur//60}:{dur%60:02d}" if dur else ""
        dur_info = f" (مدت کل: {dur_str})" if dur_str else ""
        await msg.reply_text(
            f"✂️ **برش موزیک (ساخت رینگتون)**{dur_info}\n\n"
            f"لطفاً بازه زمانی مورد نظر را ارسال کنید.\n"
            f"مثال:\n"
            f"`0:30-1:15` (از ثانیه ۳۰ تا ۱ دقیقه و ۱۵ ثانیه)\n"
            f"`30-75`",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_cancel"), callback_data="addplcancel")]])
        )

    elif data.startswith("tag_"):
        token = data.split("_", 1)[1]
        doc = find_track(token)
        if not doc or not doc.get("file_id"):
            return await msg.reply_text(t(chat_id, "effect_source_not_found"))
        user_states[chat_id] = f"MTOOL_TAGS:{token}"
        await msg.reply_text(
            f"🏷 **ویرایش عنوان و خواننده موزیک**\n\n"
            f"عنوان فعلی: **{doc.get('title','')}**\n"
            f"خواننده فعلی: **{doc.get('artist','')}**\n\n"
            f"لطفاً عنوان و خواننده جدید را به صورت زیر ارسال کنید:\n"
            f"`نام آهنگ | نام خواننده`",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_cancel"), callback_data="addplcancel")]])
        )

    elif data.startswith("alb_"):
        token = data.split("_", 1)[1]
        doc = find_track(token)
        if not doc or not doc.get("title"):
            return await msg.reply_text(t(chat_id, "track_info_not_found"))
        status = await msg.reply_text(t(chat_id, "album_finding"))
        album = await asyncio.to_thread(spotify_album_of, doc.get("artist", ""), doc.get("title", ""))
        if not album or not album.get("tracks"):
            album = await asyncio.to_thread(itunes_album_of, doc.get("artist", ""), doc.get("title", ""))
        if not album or not album.get("tracks"):
            return await status.edit_text(t(chat_id, "album_not_found_for_track"))
        user_data.setdefault(chat_id, {})["album"] = album
        lines = [f"📀 **{album['album']}**", f"👤 {album['artist']}",
                 t(chat_id, "album_track_count", count=len(album['tracks'])) + "\n"]
        for i, trk in enumerate(album['tracks'][:25], 1):
            lines.append(f"{i}. {trk['name']}")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t(chat_id, "album_btn_download_all", count=len(album['tracks'])),
                                  callback_data="albdl_all")]])
        await status.edit_text("\n".join(lines)[:4000], reply_markup=kb)

    elif data == "albdl_all":
        album = user_data.get(chat_id, {}).get("album")
        if not album:
            return await msg.reply_text(t(chat_id, "album_info_expired"))
        status = await msg.reply_text(t(chat_id, "album_queue_started"))
        asyncio.create_task(download_album(client, chat_id, status, album))

    elif data == "my_library":
        await render_library(msg, chat_id)

    elif data == "my_stats":
        await render_stats(msg, chat_id)

    elif data == "menu_playlists":
        await render_playlists(msg, chat_id)

    elif data == "menu_profile":
        user_states.pop(chat_id, None)
        await render_profile(msg, chat_id, (query.from_user.first_name if query.from_user else "") or "")

    elif data == "prof_act":
        await render_profile_activity(msg, chat_id)

    elif data == "prof_ai":
        await render_profile_ai(client, msg, chat_id, (query.from_user.first_name if query.from_user else "") or "")

    elif data.startswith("pl_playall_") or data.startswith("pl_shuffle_"):
        idx = int(data.rsplit("_", 1)[1])
        asyncio.create_task(play_playlist(client, chat_id, idx, shuffle=data.startswith("pl_shuffle_")))

    elif data == "pl_create":
        user_states[chat_id] = "PLAYLIST_NAME_NEW"
        await msg.edit_text(t(chat_id, "playlist_name_prompt"),
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_cancel"), callback_data="menu_playlists")]]))

    elif data.startswith("pl_open_"):
        idx = int(data.rsplit("_", 1)[1])
        await render_playlist(msg, chat_id, idx, 0)

    elif data.startswith("pl_page_"):
        _, _, idx_s, page_s = data.split("_", 3)
        await render_playlist(msg, chat_id, int(idx_s), int(page_s))

    elif data.startswith("pl_track_"):
        _, _, idx_s, tidx_s = data.split("_", 3)
        await show_playlist_track_actions(client, msg, chat_id, int(idx_s), int(tidx_s))

    elif data.startswith("pl_remove_"):
        _, _, idx_s, tidx_s = data.split("_", 3)
        idx, tidx = int(idx_s), int(tidx_s)
        pids = user_data.get(chat_id, {}).get("my_playlists", [])
        if idx >= len(pids):
            return await msg.edit_text(t(chat_id, "generic_expired"))
        remove_track_from_playlist(pids[idx], tidx)
        await msg.edit_text(t(chat_id, "playlist_track_removed_confirm"))

    elif data.startswith("pl_rename_"):
        idx = int(data.rsplit("_", 1)[1])
        pids = user_data.get(chat_id, {}).get("my_playlists", [])
        if idx >= len(pids):
            return await msg.edit_text(t(chat_id, "generic_expired"))
        user_states[chat_id] = f"PLAYLIST_RENAME:{pids[idx]}"
        await msg.edit_text(t(chat_id, "playlist_rename_prompt"),
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_cancel"), callback_data=f"pl_open_{idx}")]]))

    elif data.startswith("pl_delconfirm_"):
        idx = int(data.rsplit("_", 1)[1])
        pids = user_data.get(chat_id, {}).get("my_playlists", [])
        if idx >= len(pids):
            return await msg.edit_text(t(chat_id, "generic_expired"))
        doc = find_playlist(pids[idx])
        if not doc:
            return await msg.edit_text(t(chat_id, "playlist_not_found"))
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t(chat_id, "playlist_btn_confirm_delete"), callback_data=f"pl_delete_{idx}"),
             InlineKeyboardButton(t(chat_id, "btn_cancel"), callback_data=f"pl_open_{idx}")]])
        await msg.edit_text(t(chat_id, "playlist_delete_confirm_prompt", name=doc.get("name", "?")), reply_markup=kb)

    elif data.startswith("pl_delete_"):
        idx = int(data.rsplit("_", 1)[1])
        pids = user_data.get(chat_id, {}).get("my_playlists", [])
        if idx < len(pids):
            delete_playlist(pids[idx])
        await render_playlists(msg, chat_id)

    elif data.startswith("pl_ai_"):
        idx = int(data.rsplit("_", 1)[1])
        pids = user_data.get(chat_id, {}).get("my_playlists", [])
        if idx >= len(pids):
            return await msg.edit_text(t(chat_id, "generic_expired"))
        doc = find_playlist(pids[idx])
        if not doc:
            return await msg.edit_text(t(chat_id, "playlist_not_found"))
        tracks = doc.get("tracks") or []
        if not tracks:
            return await query.answer(t(chat_id, "playlist_ai_no_tracks"), show_alert=True)
        status = await msg.reply_text(t(chat_id, "playlist_ai_analyzing"))
        suggestions = await ai_suggest_similar_songs(doc.get("name", "?"), tracks)
        if not suggestions:
            return await status.edit_text(t(chat_id, "playlist_ai_failed"))
        album_name = t(chat_id, "playlist_ai_album_name", name=doc.get("name", "?"))
        await download_album(client, chat_id, status,
                             {"album": album_name, "tracks": [{"name": s["title"], "artist": s["artist"]} for s in suggestions]},
                             extra_rows=[[InlineKeyboardButton(t(chat_id, "ai_btn_continue_radio"), callback_data=f"pl_radio_{idx}")]])

    elif data.startswith("pl_radio_"):
        idx = int(data.rsplit("_", 1)[1])
        pids = user_data.get(chat_id, {}).get("my_playlists", [])
        if idx >= len(pids):
            return await msg.edit_text(t(chat_id, "generic_expired"))
        doc = find_playlist(pids[idx])
        if not doc:
            return await msg.edit_text(t(chat_id, "playlist_not_found"))
        tracks = doc.get("tracks") or []
        if not tracks:
            return await query.answer(t(chat_id, "playlist_ai_no_tracks"), show_alert=True)
        status = await msg.reply_text(t(chat_id, "radio_starting"))
        await start_radio(client, chat_id, status, doc.get("name", "?"), tracks)

    elif data == "radio_next":
        await deliver_radio_track(client, chat_id, msg)

    elif data == "radio_stop":
        user_data.get(chat_id, {}).pop("radio", None)
        await msg.edit_text(t(chat_id, "radio_stopped_confirm"))

    elif data == "radio_start_ai":
        songs = user_data.get(chat_id, {}).get("last_ai_songs") or []
        if not songs:
            return await msg.reply_text(t(chat_id, "radio_not_active"))
        status = await msg.reply_text(t(chat_id, "radio_starting"))
        await start_radio(client, chat_id, status, t(chat_id, "radio_seed_ai_name"), songs)

    elif data.startswith("addpl_"):
        tok = data.split("_", 1)[1]
        doc = find_track(tok)
        if not doc:
            return await query.answer(t(chat_id, "addpl_track_not_found"), show_alert=True)
        playlists = get_user_playlists(chat_id)
        user_data.setdefault(chat_id, {})["addpl_playlists"] = [str(p["_id"]) for p in playlists]
        rows = [[InlineKeyboardButton(f"📃 {p.get('name','?')[:28]} ({len(p.get('tracks') or [])})",
                                      callback_data=f"addplto_{i}_{tok}")]
                for i, p in enumerate(playlists)]
        rows.append([InlineKeyboardButton(t(chat_id, "addpl_btn_new"), callback_data=f"addplnew_{tok}")])
        rows.append([InlineKeyboardButton(t(chat_id, "btn_cancel"), callback_data="addplcancel")])
        await msg.reply_text(t(chat_id, "addpl_choose_header"), reply_markup=InlineKeyboardMarkup(rows))

    elif data.startswith("addplto_"):
        _, idx_s, tok = data.split("_", 2)
        idx = int(idx_s)
        pids = user_data.get(chat_id, {}).get("addpl_playlists", [])
        if idx >= len(pids):
            return await msg.edit_text(t(chat_id, "generic_expired"))
        doc = find_track(tok)
        if not doc:
            return await msg.edit_text(t(chat_id, "addpl_track_not_found"))
        key = doc.get("vid_id") or tok
        pdoc = find_playlist(pids[idx])
        result = add_track_to_playlist(pids[idx], key, doc.get("title", "?"), doc.get("artist", ""), doc.get("duration", 0))
        name = pdoc.get("name", "?") if pdoc else "?"
        if result == "added":
            await msg.edit_text(t(chat_id, "addpl_added_confirm", name=name))
        elif result == "duplicate":
            await msg.edit_text(t(chat_id, "addpl_duplicate"))
        elif result == "full":
            await msg.edit_text(t(chat_id, "addpl_full", max=MAX_TRACKS_PER_PLAYLIST))
        else:
            await msg.edit_text(t(chat_id, "addpl_error"))

    elif data.startswith("addplnew_"):
        tok = data.split("_", 1)[1]
        user_states[chat_id] = f"PLAYLIST_NEW_FOR_ADD:{tok}"
        await msg.edit_text(t(chat_id, "playlist_name_prompt"))

    elif data == "addplcancel":
        await msg.delete()

    elif data == "subs_menu":
        await render_subs(msg, chat_id)

    elif data == "sub_add":
        user_states[chat_id] = "SUB_ADD"
        await msg.edit_text(
            t(chat_id, "sub_add_prompt"),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="subs_menu")]]))

    elif data.startswith("unsub_"):
        idx = int(data.split("_", 1)[1])
        ids = user_data.get(chat_id, {}).get("subs", [])
        if idx >= len(ids) or subs_col is None:
            return await msg.reply_text(t(chat_id, "sub_expired"))
        try:
            from bson import ObjectId
            subs_col.delete_one({"_id": ObjectId(ids[idx])})
        except Exception as e:
            print(f"unsub error: {e}", flush=True)
        await render_subs(msg, chat_id)

    elif data.startswith("dlyt_"):
        vid = data.split("_", 1)[1]
        status = await msg.reply_text(t(chat_id, "yt_extracting_info"))
        await fetch_and_verify_video(status, chat_id, vid)

    elif data.startswith("libget_"):
        idx = int(data.split("_", 1)[1])
        lib = user_data.get(chat_id, {}).get("library", [])
        if idx >= len(lib):
            return await msg.reply_text(t(chat_id, "lib_result_expired"))
        key = lib[idx]
        # Resend from cache: own file_id if we have one, else copy from the
        # archive channel. No download, no CPU.
        if not await deliver_by_key(client, chat_id, key, track_actions_kb(key, chat_id)):
            return await msg.reply_text(t(chat_id, "lib_file_unavailable"))

    elif data == "engine_sc":
        user_states[chat_id] = "MUSIC_SEARCH_SC"
        await msg.edit_text(t(chat_id, "sc_search_prompt"), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_music")]]))

    elif data == "engine_spotify":
        user_states[chat_id] = "MUSIC_SEARCH_SPOTIFY"
        await msg.edit_text(t(chat_id, "spotify_search_prompt"), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_music")]]))

    elif data.startswith("sppage_"):
        _, page_s, sort_key = data.split("_", 2)
        query = user_data.get(chat_id, {}).get("sp_query", "")
        await render_spotify_search(msg, query, chat_id, int(page_s), sort_key)

    elif data == "sp_rand" or data.startswith("sp_pick_"):
        if data == "sp_rand":
            items = user_data.get(chat_id, {}).get("sp_all", [])
            if not items:
                return await msg.reply_text(t(chat_id, "official_results_expired"))
            trk = random.choice(items)
        else:
            idx = int(data.split("_", 2)[2])
            items = user_data.get(chat_id, {}).get("sp_page_items", [])
            if idx >= len(items):
                return await msg.edit_text(t(chat_id, "generic_expired"))
            trk = items[idx]
        # A fresh status message keeps the result list usable for more picks.
        status = await client.send_message(chat_id, t(chat_id, "track_downloading", track=_track_label(meta_from_spotify(trk))))
        await deliver_track(client, chat_id, meta_from_spotify(trk), status=status, activity="music_spotify")

    elif data.startswith("lyrics_"):
        # split("_", 1): YouTube IDs may themselves contain "_", so splitting on
        # every underscore truncated the token and broke the lookup.
        track_id = data.split("_", 1)[1]
        await query.answer(t(chat_id, "lyrics_fetching_toast"), show_alert=False)
        cached_song = LOCAL_META.get(track_id)
        # `if music_cache` raises NotImplementedError: pymongo Collections forbid
        # truth-value testing. Must compare against None explicitly.
        if cached_song is None and music_cache is not None:
            cached_song = (music_cache.find_one({"lyrics_key": track_id})
                           or music_cache.find_one({"vid_id": track_id}))
        title = cached_song.get("title") if cached_song else None
        artist = cached_song.get("artist") if cached_song else ""

        if not title:
            return await msg.reply_text(t(chat_id, "lyrics_song_info_not_found"))

        try:
            # Shared resolver: splits "Artist - Track", strips video noise and
            # validates the artist, instead of the old single q= query that
            # failed on anything from a music-video channel.
            rec = await asyncio.to_thread(lrclib_lookup, title, artist)
            lyrics = (rec or {}).get("syncedLyrics") or (rec or {}).get("plainLyrics")
            if lyrics:
                shown = f"{rec.get('trackName') or title} — {rec.get('artistName') or artist}"
                safe_lyrics = lyrics[:3800] + "..." if len(lyrics) > 3800 else lyrics
                await msg.reply_text(t(chat_id, "lyrics_result_header", shown=shown) + f"\n\n`{safe_lyrics}`")
            else:
                await msg.reply_text(t(chat_id, "lyrics_not_in_db"))
        except Exception as e:
            print(f"Lyrics handler error: {e}", flush=True)
            await msg.reply_text(t(chat_id, "lyrics_server_error"))

    elif data == "ig_menu":
        user_states[chat_id] = "IG_LINK"
        await msg.edit_text(t(chat_id, "ig_menu_prompt"), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_downloader")]]))

    elif data == "tiktok_menu":
        user_states[chat_id] = "TIKTOK_TARGET"
        await msg.edit_text(t(chat_id, "tiktok_menu_prompt"), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_downloader")]]))

    elif data == "menu_pinterest":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t(chat_id, "btn_pt_search"), callback_data="pt_search")],
            [InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_main")]
        ])
        await msg.edit_text(t(chat_id, "pinterest_menu_text"), reply_markup=kb)

    elif data == "pt_search":
        user_states[chat_id] = "PT_SEARCH"
        await msg.edit_text(t(chat_id, "pt_search_prompt"),
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_pinterest")]]))

    elif data.startswith("ptpage_"):
        page = int(data.split("_", 1)[1])
        query = user_data.get(chat_id, {}).get("pt_query", "")
        await render_pinterest_search(msg, query, chat_id, page)

    elif data.startswith("pt_pick_"):
        idx = int(data.split("_")[2])
        items = user_data.get(chat_id, {}).get("pt_page_items", [])
        if idx >= len(items):
            return await client.send_message(chat_id, t(chat_id, "pt_expired_research"))
        media = _pin_to_media(items[idx])
        if not media:
            return await client.send_message(chat_id, t(chat_id, "pt_not_downloadable"))
        status = await client.send_message(chat_id, t(chat_id, "pt_fetching_generic"))
        await deliver_pinterest_media(client, chat_id, status, media)

    elif data == "rd_search":
        user_states[chat_id] = "REDDIT_SEARCH"
        await msg.edit_text(t(chat_id, "rd_search_prompt"),
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_reddit")]]))

    elif data.startswith("rdpage_"):
        page = int(data.split("_", 1)[1])
        query = user_data.get(chat_id, {}).get("rd_query", "")
        await render_reddit_search(msg, query, chat_id, page)

    elif data.startswith("rd_pick_"):
        idx = int(data.split("_")[2])
        items = user_data.get(chat_id, {}).get("rd_page_items", [])
        if idx >= len(items):
            return await client.send_message(chat_id, t(chat_id, "pt_expired_research"))
        status = await client.send_message(chat_id, t(chat_id, "pt_fetching_generic"))
        await deliver_reddit_post(client, chat_id, status, items[idx].get("id"))

    elif data == "menu_images":
        user_states[chat_id] = "IMAGE_SEARCH"
        await msg.edit_text(t(chat_id, "image_search_prompt"),
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_main")]]))

    elif data == "menu_audd":
        await msg.edit_text(
            t(chat_id, "audd_menu_text"),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_main")]]))

    elif data.startswith("imgpage_"):
        page = int(data.split("_", 1)[1])
        query = user_data.get(chat_id, {}).get("img_query", "")
        await render_image_search(msg, query, chat_id, page)

    elif data.startswith("img_pick_"):
        key = data.split("_", 2)[2]
        store = user_data.get(chat_id, {})
        photo = (store.get("img_by_id") or {}).get(key)
        if photo is None and key.isdigit():
            items = store.get("img_page_items", [])
            photo = items[int(key)] if int(key) < len(items) else None
        if photo is None:
            return await client.send_message(chat_id, t(chat_id, "pt_expired_research"))
        status = await client.send_message(chat_id, t(chat_id, "img_fetching_full"))
        try:
            path = await asyncio.to_thread(fetch_full_image_sync, photo)
            await client.send_document(chat_id, document=path, caption=f"📸 {photo.get('photographer','')}",
                                       reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                                           "🎨 ابزارهای تصویر", callback_data="menu_image_tools")]]))
            os.remove(path)
            await status.delete()
            prov = photo.get("_provider") or user_data.get(chat_id, {}).get("img_provider") or "pexels"
            log_activity(chat_id, "image_search", f"{prov}: {photo.get('photographer','')} #{photo['id']}")
            record_image_download(chat_id, prov, photo["src"]["original"], photo.get("photographer", ""))
        except Exception as e:
            print(f"image download error: {e}", flush=True)
            await status.edit_text(t(chat_id, "img_fetch_failed"))

    elif data == "audd_download":
        res = user_data.get(chat_id, {}).get("audd_result")
        if not res:
            return await client.send_message(chat_id, t(chat_id, "audd_no_result_to_download"))
        meta = make_meta("spotify" if res.get("spotify_id") else "catalog",
                         title=res.get("title"), artist=res.get("artist"), album=res.get("album"),
                         year=(res.get("release_date") or "")[:4], cover=res.get("cover"),
                         isrc=res.get("isrc"), spotify_id=res.get("spotify_id"), duration=res.get("duration"))
        await deliver_track(client, chat_id, meta, activity="music_audd")

    elif data == "menu_ai":
        user_states[chat_id] = "AI_CHAT"
        AI_HISTORY.pop(chat_id, None)
        await msg.edit_text(
            t(chat_id, "ai_intro_text"),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_ai_exit"), callback_data="ai_exit")]]))


    elif data == "ai_exit":
        user_states.pop(chat_id, None)
        AI_HISTORY.pop(chat_id, None)
        await msg.edit_text(t(chat_id, "ai_closed_text"), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_main")]]))

    elif data == "ai_cancel_songs":
        AI_PENDING_SONGS.pop(chat_id, None)
        await msg.edit_text(t(chat_id, "generic_cancelled"))

    elif data == "ai_confirm_songs":
        songs = AI_PENDING_SONGS.pop(chat_id, None) or []
        if not songs:
            return await msg.edit_text(t(chat_id, "generic_expired"))
        user_data.setdefault(chat_id, {})["last_ai_songs"] = songs
        await msg.edit_text(t(chat_id, "ai_downloading_songs", count=len(songs)))
        # Grouped delivery (same pipeline as albums and the playlist AI
        # suggestions) instead of one message per song -- gives every track
        # its numbered picker with the full actions menu, and a one-tap way
        # to keep the vibe going with Radio Mode afterward.
        await download_album(client, chat_id, msg,
                             {"album": t(chat_id, "radio_seed_ai_name"),
                              "tracks": [{"name": s.get("title", ""), "artist": s.get("artist", "")} for s in songs]},
                             extra_rows=[[InlineKeyboardButton(t(chat_id, "ai_btn_continue_radio"), callback_data="radio_start_ai")]])


    elif data == "yt_menu":
        await msg.edit_text(t(chat_id, "yt_search_choose_text"), reply_markup=get_yt_search_menu(chat_id))

    elif data == "yt_keyword":
        user_states[chat_id] = "YT_KEYWORD"
        await msg.edit_text(t(chat_id, "yt_keyword_prompt"))

    elif data == "yt_channel":
        user_states[chat_id] = "YT_CHANNEL"
        await msg.edit_text(t(chat_id, "yt_channel_prompt"))

    # --- MUSIC SELECTIONS ---
    elif data.startswith("scvid_"):
        idx = int(data.split("_")[1])
        mus_results = user_data.get(chat_id, {}).get("mus_results", [])
        if idx >= len(mus_results): return await msg.edit_text(t(chat_id, "lib_result_expired"), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_music")]]))
        sc_id, sc_url = mus_results[idx]
        meta = make_meta("sc", sc_id=sc_id, sc_url=sc_url)
        status = await client.send_message(chat_id, t(chat_id, "track_downloading", track="SoundCloud"))
        await deliver_track(client, chat_id, meta, status=status, activity="music_sc")

    elif data == "noop":
        pass  # page indicator button; query.answer() above already ack'd it

    elif data == "check_join":
        missing = await missing_force_channels(client, query.from_user.id if query.from_user else chat_id)
        if missing:
            await query.answer(t(chat_id, "join_still_missing"), show_alert=True)
        else:
            await msg.edit_text(t(chat_id, "join_confirmed"), reply_markup=get_main_keyboard(chat_id))

    elif data == "help_home" or data.startswith("help_"):
        key = data.split("_", 1)[1]
        lang = get_lang(chat_id)
        if key in HELP_PAGES:
            body = HELP_PAGES[key]["body"].get(lang) or HELP_PAGES[key]["body"][DEFAULT_LANG]
            if key == "subs":
                body = body.format(max_subs=MAX_SUBS_PER_USER)
            elif key == "inline":
                body = body.format(bot_username=os.environ.get("BOT_USERNAME", "tahasbot"))
            elif key in ("playlists", "lib"):
                body = body.format(max_playlists=MAX_PLAYLISTS_PER_USER, max_tracks=MAX_TRACKS_PER_PLAYLIST)
            await msg.edit_text(body[:4000], reply_markup=help_keyboard(chat_id, key))
        else:
            await msg.edit_text(t(chat_id, "help_home_text"), reply_markup=help_keyboard(chat_id))

    elif data == "contact_admin":
        user_states[chat_id] = "SUPPORT_MSG"
        await msg.edit_text(
            t(chat_id, "contact_admin_prompt"),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_cancel"), callback_data="menu_main")]]))

    # ---- admin panel (every branch re-checks ownership) ----
    elif data.startswith("adm_"):
        if not is_admin(query.from_user.id if query.from_user else 0):
            return await query.answer("⛔️", show_alert=True)
        if await handle_bale_admin_callback(client, query, data):
            return

        if data == "adm_home":
            await render_admin_home(msg)
        elif data == "adm_stats":
            await render_admin_stats(msg)
        elif data.startswith("adm_users_"):
            await render_admin_users(msg, int(data.rsplit("_", 1)[1]))
        elif data.startswith("adm_userssort_"):
            _, _, pg, srt = data.split("_", 3)
            await render_admin_users(msg, int(pg), srt)
        elif data.startswith("adm_uact_"):
            _, _, rest = data.split("_", 2)
            tgt, pg = rest.rsplit("_", 1)
            await render_admin_activity(msg, page=int(pg), target=int(tgt))
        elif data.startswith("adm_u_"):
            await render_admin_user(msg, int(data.rsplit("_", 1)[1]))
        elif data.startswith("adm_act_"):
            await render_admin_activity(msg, page=int(data.rsplit("_", 1)[1]))
        elif data.startswith("adm_sup_"):
            await render_admin_support(msg, int(data.rsplit("_", 1)[1]))
        elif data.startswith("adm_ban_") or data.startswith("adm_unban_"):
            banning = data.startswith("adm_ban_")
            target = int(data.rsplit("_", 1)[1])
            if users_col is not None:
                users_col.update_one({"chat_id": target}, {"$set": {"banned": banning}}, upsert=True)
            BAN_CACHE.pop(target, None)  # take effect immediately despite the cache
            await query.answer("⛔️ مسدود شد" if banning else "✅ رفع مسدودیت شد", show_alert=True)
            await render_admin_user(msg, target)
        elif data.startswith("adm_msg_"):
            target = int(data.rsplit("_", 1)[1])
            user_states[chat_id] = f"ADMIN_MSG:{target}"
            await msg.edit_text(f"✉️ پیام خود را برای کاربر `{target}` بنویسید:",
                                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 انصراف", callback_data=f"adm_u_{target}")]]))
        elif data.startswith("adm_rep_"):
            sup_id = data.split("_", 2)[2]
            user_states[chat_id] = f"ADMIN_REPLY:{sup_id}"
            await msg.edit_text("✍️ پاسخ خود را بنویسید:",
                                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 انصراف", callback_data="adm_sup_0")]]))
        elif data == "adm_bc":
            user_states[chat_id] = "ADMIN_BROADCAST"
            await msg.edit_text("📢 متن پیام همگانی را بنویسید:",
                                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 انصراف", callback_data="adm_home")]]))
        elif data == "adm_ch":
            await render_admin_channels(msg)
        elif data == "adm_chadd":
            user_states[chat_id] = "ADMIN_ADDCH"
            await msg.edit_text(
                "➕ **افزودن کانال اجباری**\n\nآیدی کانال و لینک دعوت را در یک خط بفرستید:\n"
                "`@channelname https://ble.ir/channelname`\n\n"
                "⚠️ ربات باید در آن کانال ادمین باشد.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 انصراف", callback_data="adm_ch")]]))
        elif data.startswith("adm_chdel_"):
            idx = int(data.rsplit("_", 1)[1])
            chans = list(get_settings().get("force_channels") or [])
            if 0 <= idx < len(chans):
                removed = chans.pop(idx)
                set_setting("force_channels", chans)
                await query.answer(f"حذف شد: {removed.get('chat')}", show_alert=True)
            await render_admin_channels(msg)
        elif data == "adm_harvester":
            cur = bool(get_settings().get("harvester_enabled", True))
            new_state = not cur
            set_setting("harvester_enabled", new_state)
            await query.answer(f"کش خودکار ۲۴/۷: {'روشن شد 🟢' if new_state else 'خاموش شد 🔴'}", show_alert=False)
            await render_admin_home(msg)
        elif data == "adm_maint":
            new_state = not bool(get_settings().get("maintenance"))
            set_setting("maintenance", new_state)
            await render_admin_home(msg)
            note_fn = (lambda cid: t(cid, "maintenance_on_announce")) if new_state else (lambda cid: t(cid, "maintenance_off_announce"))
            status = await client.send_message(chat_id, "📢 در حال اطلاع‌رسانی به کاربران...")
            sent, failed = await broadcast(client, status=status, text_fn=note_fn)
            await status.edit_text(
                f"{'🔴 حالت تعمیرات فعال شد' if new_state else '🟢 ربات فعال شد'}\n"
                f"اطلاع‌رسانی: ✅ {sent} • ❌ {failed}")
        elif data == "adm_search":
            user_states[chat_id] = "ADMIN_SEARCH_USER"
            await msg.edit_text(
                "🔍 **جستجوی کاربر**\n\nآیدی عددی، یوزرنیم یا نام را بفرستید:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 انصراف", callback_data="adm_home")]]))
        elif data.startswith("adm_chats_"):
            await render_admin_bot_chats(msg, int(data.rsplit("_", 1)[1]))
        elif data.startswith("adm_leavechat_"):
            target_chat = int(data.rsplit("_", 1)[1])
            try:
                await client.leave_chat(target_chat)
                if bot_chats_col is not None:
                    bot_chats_col.update_one({"chat_id": target_chat}, {"$set": {"active": False}})
                await query.answer("🚪 چت ترک شد", show_alert=True)
            except Exception as e:
                await query.answer(f"❌ خطا: {str(e)[:100]}", show_alert=True)
            await render_admin_bot_chats(msg, 0)
        elif data == "adm_smallz_0":
            await render_admin_smallz(msg)
        elif data == "adm_smallzadd":
            user_states[chat_id] = "ADMIN_SMALLZ_ADD"
            await msg.edit_text(
                "➕ **افزودن Smallz**\n\nآیدی عددی کاربر مورد نظر را بفرستید "
                "(کاربر باید قبلاً حداقل یک‌بار ربات را استارت کرده باشد):",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 انصراف", callback_data="adm_smallz_0")]]))
        elif data.startswith("adm_smallzdel_"):
            target_sz = int(data.rsplit("_", 1)[1])
            if smallz_col is not None:
                smallz_col.delete_one({"chat_id": target_sz})
            await query.answer("🗑 حذف شد", show_alert=True)
            await render_admin_smallz(msg)
        elif data == "adm_maintallow":
            await render_admin_maint_allowlist(msg)
        elif data == "adm_maintadd":
            user_states[chat_id] = "ADMIN_MAINT_ADD"
            await msg.edit_text(
                "➕ **افزودن دسترسی در تعمیرات**\n\nآیدی عددی کاربر مورد نظر را بفرستید:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 انصراف", callback_data="adm_maintallow")]]))
        elif data.startswith("adm_maintdel_"):
            target_ma = int(data.rsplit("_", 1)[1])
            allowlist = list(get_settings().get("maintenance_allowlist") or [])
            if target_ma in allowlist:
                allowlist.remove(target_ma)
                set_setting("maintenance_allowlist", allowlist)
            await query.answer("🗑 حذف شد", show_alert=True)
            await render_admin_maint_allowlist(msg)
        elif data == "adm_fjallow":
            await render_admin_fj_allowlist(msg)
        elif data == "adm_fjadd":
            user_states[chat_id] = "ADMIN_FJ_ADD"
            await msg.edit_text(
                "➕ **افزودن معافیت از عضویت اجباری**\n\nآیدی عددی کاربر مورد نظر را بفرستید:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 انصراف", callback_data="adm_fjallow")]]))
        elif data.startswith("adm_fjdel_"):
            target_fj = int(data.rsplit("_", 1)[1])
            allowlist = list(get_settings().get("force_join_allowlist") or [])
            if target_fj in allowlist:
                allowlist.remove(target_fj)
                set_setting("force_join_allowlist", allowlist)
            MEMBER_OK.pop(target_fj, None)
            await query.answer("🗑 حذف شد", show_alert=True)
            await render_admin_fj_allowlist(msg)

    elif data in ("smz_users", "smz_link", "smz_home"):
        if not is_smallz(chat_id):
            return await query.answer("⛔️", show_alert=True)
        if data == "smz_home":
            await msg.edit_text("🎯 **پنل Smallz**\n\nیکی از گزینه‌ها را انتخاب کنید:", reply_markup=smallz_kb())
        elif data == "smz_users":
            total = users_col.count_documents({}) if users_col is not None else 0
            await msg.edit_text(f"👥 **تعداد کل کاربران ربات:** {total}",
                                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="smz_home")]]))
        elif data == "smz_link":
            doc = smallz_col.find_one({"chat_id": chat_id}) if smallz_col is not None else None
            refs = doc.get("referrals", 0) if doc else 0
            bot_username = os.environ.get("BOT_USERNAME", "tahasbot")
            link = f"https://ble.ir/{bot_username}?start=ref_{chat_id}"
            await msg.edit_text(
                f"🔗 **لینک رفرال شما:**\n`{link}`\n\n👥 تعداد افرادی که با این لینک آمده‌اند: **{refs}**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="smz_home")]]))

    elif data.startswith("kar_"):
        token = data.split("_", 1)[1]
        doc = find_track(token)
        if not doc or not doc.get("file_id"):
            return await msg.reply_text(t(chat_id, "effect_source_not_found"))
        src = track_source_url(doc)
        status = await msg.reply_text(t(chat_id, "processing_sending_to_server"))
        if src:
            ok = await asyncio.to_thread(trigger_ai_worker, {
                "TASK": "karaoke", "SOURCE_URL": src,
                "CHAT_ID": str(chat_id), "MESSAGE_ID": str(status.id), "DRY_RUN": "false"})
            if ok:
                return await status.edit_text(t(chat_id, "karaoke_request_queued"))
        # Fallback to instant server-side vocal cancellation via ffmpeg
        await process_effect(client, chat_id, status, token, "vocal")

    elif data.startswith("aisub_"):
        vid = data.split("_", 1)[1]
        status = await msg.reply_text(t(chat_id, "processing_sending_to_server"))
        ok = await asyncio.to_thread(trigger_ai_worker, {
            "TASK": "subs", "SOURCE_URL": f"https://www.youtube.com/watch?v={vid}",
            "CHAT_ID": str(chat_id), "MESSAGE_ID": str(status.id),
            "TARGET_LANG": "fa", "DRY_RUN": "false"})
        await status.edit_text(
            t(chat_id, "aisub_request_queued")
            if ok else t(chat_id, "processing_server_unreachable"))

    elif data.startswith("osp_"):
        _, page_s, sort_key = data.split("_", 2)
        await render_official_page(msg, chat_id, page=int(page_s), sort=sort_key)

    elif data == "offrand":
        items = user_data.get(chat_id, {}).get("official_results", [])
        if not items:
            return await msg.reply_text(t(chat_id, "official_results_expired"))
        song = random.choice(items)
        meta = _official_item_meta(song)
        status = await client.send_message(chat_id, t(chat_id, "track_downloading", track=_track_label(meta)))
        await deliver_track(client, chat_id, meta, status=status, activity="music_" + meta.get("source", "yt"))

    elif data.startswith("scp_"):
        await render_sc_page(msg, chat_id, page=int(data.split("_", 1)[1]))

    elif data.startswith("libp_"):
        _, page_s, sort_key = data.split("_", 2)
        await render_library(msg, chat_id, page=int(page_s), sort=sort_key)

    elif data == "mixtape_now":
        if msg.chat.type != ChatType.PRIVATE:
            return await query.answer(t(chat_id, "mixtape_private_only"), show_alert=True)
        status = await msg.reply_text(t(chat_id, "mixtape_building"))
        ok = await send_weekly_mixtape(client, chat_id)
        if not ok:
            await status.edit_text(t(chat_id, "mixtape_not_enough_songs"))
        else:
            await status.delete()

    elif data.startswith("album_track_"):
        idx = int(data.split("_")[2])
        album_tracks = user_data.get(chat_id, {}).get("album_tracks", [])
        if idx >= len(album_tracks):
            return await msg.edit_text(t(chat_id, "generic_expired"))
        sid, title = album_tracks[idx]
        await client.send_message(chat_id, t(chat_id, "album_track_prompt", title=title), reply_markup=track_actions_kb(sid, chat_id))

    elif data.startswith("offvid_"):
        idx = int(data.split("_")[1])
        official_results = user_data.get(chat_id, {}).get("official_results", [])
        if idx >= len(official_results):
            return await msg.edit_text(t(chat_id, "lib_result_expired"), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_music")]]))

        meta = _official_item_meta(official_results[idx])
        status = await client.send_message(chat_id, t(chat_id, "track_downloading", track=_track_label(meta)))
        await deliver_track(client, chat_id, meta, status=status, activity="music_" + meta.get("source", "yt"))

    # --- VIDEO SELECTIONS ---
    elif data.startswith("page_"):
        _, nav, p_num = data.split("_")
        query_text = user_data[chat_id].get("search_query")
        is_chan = user_data[chat_id].get("is_channel_search", False)
        if is_chan:
            token = user_data[chat_id].get("next_token") if nav == "next" else user_data[chat_id].get("prev_token")
            await render_channel_search(msg, query_text, chat_id, token, int(p_num))
        else:
            await render_video_search(msg, query_text, chat_id, int(p_num))

    elif data.startswith("chan_"):
        idx = int(data.split("_")[1])
        chan_results = user_data.get(chat_id, {}).get("chan_results", [])
        if idx >= len(chan_results):
            return await msg.edit_text(t(chat_id, "generic_expired"), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="yt_menu")]]))
        chan_id = chan_results[idx]
        await msg.edit_text(t(chat_id, "yt_fetching_channel_videos"))
        try:
            res = yt_api("search", {"channelId": chan_id, "part": "snippet", "type": "video", "order": "date", "maxResults": 5})
            items = res.get('items', [])
            if not items: return await msg.edit_text(t(chat_id, "yt_no_videos"), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="yt_menu")]]))
            user_data.setdefault(chat_id, {})["vid_results"] = [i['id']['videoId'] for i in items]
            kb_buttons = []
            text = t(chat_id, "yt_channel_latest_videos")
            for i, item in enumerate(items):
                title = item['snippet']['title'][:45] + "..."
                text += f"{i+1}️⃣ **{title}**\n\n"
                kb_buttons.append(InlineKeyboardButton(str(i+1), callback_data=f"vid_{i}"))
            await msg.edit_text(text, reply_markup=InlineKeyboardMarkup([kb_buttons, [InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="yt_menu")]]))
        except: await msg.edit_text(t(chat_id, "yt_connection_error"))

    elif data.startswith("vid_"):
        idx = int(data.split("_")[1])
        vid_results = user_data.get(chat_id, {}).get("vid_results", [])
        if idx >= len(vid_results): return await msg.edit_text(t(chat_id, "generic_expired"), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="yt_menu")]]))
        await fetch_and_verify_video(msg, chat_id, vid_results[idx])

    elif data == "menu_vedit":
        await msg.edit_text(
            t(chat_id, "vedit_menu_intro"),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_main")]]))

    elif data == "vedit_trim":
        user_states[chat_id] = "VEDIT_TRIM"
        await msg.edit_text(
            t(chat_id, "vedit_trim_prompt"),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_cancel"), callback_data="vedit_back")]]))

    elif data == "vedit_watermark":
        user_states[chat_id] = "VEDIT_WATERMARK"
        await msg.edit_text(
            t(chat_id, "vedit_watermark_prompt"),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_cancel"), callback_data="vedit_back")]]))

    elif data == "vedit_back":
        user_states.pop(chat_id, None)
        if user_data.get(chat_id, {}).get("vedit_path"):
            await msg.edit_text(t(chat_id, "vedit_ready_text"), reply_markup=video_editor_kb(chat_id))
        else:
            await msg.edit_text(t(chat_id, "vedit_no_file"),
                                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "main_menu_btn"), callback_data="menu_main")]]))

    elif data == "vedit_from_last":
        file_id = None
        if query.message:
            v = query.message.video or query.message.document
            if v and getattr(v, "file_id", None):
                if (getattr(v, "file_size", 0) or 0) > MAX_VIDEO_UPLOAD_BYTES:
                    await query.answer(t(chat_id, "vedit_too_large"), show_alert=True)
                    return
                file_id = v.file_id
        if not file_id:
            file_id = user_data.get(chat_id, {}).get("last_video_file_id")
        if not file_id:
            await query.answer(t(chat_id, "vedit_no_video_alert"), show_alert=True)
            return
        user_data.setdefault(chat_id, {})["last_video_file_id"] = file_id
        old = user_data.get(chat_id, {}).get("vedit_path")
        if old and os.path.exists(old):
            try: os.remove(old)
            except OSError: pass
        status = await client.send_message(chat_id, t(chat_id, "vedit_preparing"))
        try:
            path = await client.download_media(
                file_id, file_name=f"{VIDEO_EDIT_DIR}/src_{chat_id}_{int(time.time())}.mp4")
        except Exception:
            await status.edit_text(t(chat_id, "vedit_download_failed"))
            return
        user_data.setdefault(chat_id, {})["vedit_path"] = path
        await status.edit_text(t(chat_id, "vedit_ready_text"), reply_markup=video_editor_kb(chat_id))

    elif data.startswith("vedit_"):
        await process_video_edit(client, chat_id, msg, data[len("vedit_"):])

    elif data == "fmt_choose":
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "yt_btn_fmt_video"), callback_data="fmt_mp4")], [InlineKeyboardButton(t(chat_id, "yt_btn_fmt_audio"), callback_data="fmt_mp3")]])
        await msg.edit_caption(t(chat_id, "yt_choose_format"), reply_markup=kb)

    elif data.startswith("fmt_"):
        fmt = data.split("_")[1]
        user_data[chat_id]["format"] = fmt
        if fmt == "mp3":
            user_data[chat_id]["quality"] = "best"
            await msg.edit_caption(t(chat_id, "yt_mp3_selected"), reply_markup=get_extras_kb(chat_id))
        else:
            await msg.edit_caption(t(chat_id, "yt_calculating_size"))
            sizes = await asyncio.to_thread(get_quality_size_labels, user_data.get(chat_id, {}).get("target_vid", ""))
            def _label(name, height):
                size = sizes.get(height)
                return f"{name} ({size})" if size else name
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(_label("1080p", 1080), callback_data="q_1080p"), InlineKeyboardButton(_label("720p", 720), callback_data="q_720p")],
                [InlineKeyboardButton(_label("480p", 480), callback_data="q_480p"), InlineKeyboardButton(_label("360p", 360), callback_data="q_360p")]
            ])
            await msg.edit_caption(t(chat_id, "yt_choose_quality"), reply_markup=kb)

    elif data.startswith("q_"):
        user_data[chat_id]["quality"] = data.split("_")[1]
        await msg.edit_caption(t(chat_id, "yt_extras_optional"), reply_markup=get_extras_kb(chat_id))

    elif data.startswith("tog_"):
        key = data.split("_")[1]
        if key == "trim":
            user_states[chat_id] = "AWAITING_TRIM"
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "yt_btn_first_15s"), callback_data="settrim_00:00-00:15"), InlineKeyboardButton(t(chat_id, "yt_btn_first_30s"), callback_data="settrim_00:00-00:30")], [InlineKeyboardButton(t(chat_id, "yt_btn_cancel_trim"), callback_data="q_1080p")]])
            await msg.edit_caption(t(chat_id, "yt_trim_prompt"), reply_markup=kb)
        else:
            user_data[chat_id][key] = not user_data[chat_id].get(key, False)
            await msg.edit_caption(t(chat_id, "yt_extras_optional"), reply_markup=get_extras_kb(chat_id))

    elif data.startswith("settrim_"):
        times = data.split("_")[1]
        user_data[chat_id]["trim_start"], user_data[chat_id]["trim_end"] = times.split("-")
        user_states.pop(chat_id, None)
        await msg.edit_caption(t(chat_id, "yt_extras_optional"), reply_markup=get_extras_kb(chat_id))

    elif data == "start_download":
        e = user_data.get(chat_id, {})
        await msg.edit_caption(t(chat_id, "yt_download_confirmed"))
        prog_msg = await msg.reply_text(t(chat_id, "yt_connecting_server"))
        payload = {
            "YT_URL": f"https://www.youtube.com/watch?v={e.get('target_vid')}",
            "PLATFORM": "youtube", "CHAT_ID": str(chat_id), "MESSAGE_ID": str(prog_msg.id),
            "YT_QUALITY": e.get("quality", "720p"), "YT_FORMAT": e.get("format", "mp4"),
            "GET_SUBS": "false", "GET_COMMENTS": "true" if e.get("comments") else "false",
            "GET_DESC": "true" if e.get("desc") else "false", "GET_THUMBNAIL": "true" if e.get("thumb") else "false",
            "TRIM_START": e.get("trim_start", ""), "TRIM_END": e.get("trim_end", "")
        }
        log_activity(chat_id, "download",
                     f"YouTube {e.get('format','mp4')} {e.get('quality','')} — {payload['YT_URL']}")
        if trigger_github_worker(payload): await prog_msg.edit_text(t(chat_id, "yt_request_sent"))
        else: await prog_msg.edit_text(t(chat_id, "yt_github_connection_error"))

STRINGS.update({
    "yt_extracting_info": {"fa": "⏳ در حال استخراج اطلاعات ویدیو...", "en": "⏳ Extracting video info...", "ru": "⏳ Извлечение информации о видео...", "es": "⏳ Extrayendo información del video..."},
    "yt_no_videos": {"fa": "❌ هیچ ویدیویی پیدا نشد.", "en": "❌ No videos found.", "ru": "❌ Видео не найдены.", "es": "❌ No se encontraron videos."},
    "yt_search_results_header": {"fa": "🔍 **نتایج جستجو برای:** `{query}`\n📄 **صفحه:** {page} از {pages}\n\n", "en": "🔍 **Search results for:** `{query}`\n📄 **Page:** {page} of {pages}\n\n", "ru": "🔍 **Результаты поиска для:** `{query}`\n📄 **Страница:** {page} из {pages}\n\n", "es": "🔍 **Resultados de búsqueda para:** `{query}`\n📄 **Página:** {page} de {pages}\n\n"},
    "yt_search_process_error": {"fa": "❌ خطا در پردازش نتایج.", "en": "❌ Error processing results.", "ru": "❌ Ошибка обработки результатов.", "es": "❌ Error al procesar los resultados."},
    "yt_no_channels": {"fa": "❌ کانالی پیدا نشد.", "en": "❌ No channels found.", "ru": "❌ Каналы не найдены.", "es": "❌ No se encontraron canales."},
    "yt_channel_results_header": {"fa": "👤 **نتایج کانال برای:** `{query}`\n📄 **صفحه:** {page}\n\n", "en": "👤 **Channel results for:** `{query}`\n📄 **Page:** {page}\n\n", "ru": "👤 **Результаты каналов для:** `{query}`\n📄 **Страница:** {page}\n\n", "es": "👤 **Resultados de canales para:** `{query}`\n📄 **Página:** {page}\n\n"},
    "yt_channel_subs_line": {"fa": "👥 {subs} سابسکرایبر | 🎬 {videos} ویدیو\n\n", "en": "👥 {subs} subscribers | 🎬 {videos} videos\n\n", "ru": "👥 {subs} подписчиков | 🎬 {videos} видео\n\n", "es": "👥 {subs} suscriptores | 🎬 {videos} videos\n\n"},
    "yt_channel_search_error": {"fa": "❌ خطا در جستجوی کانال.", "en": "❌ Channel search error.", "ru": "❌ Ошибка поиска канала.", "es": "❌ Error en la búsqueda de canales."},
    "yt_video_info_error": {"fa": "❌ خطا در دریافت اطلاعات ویدیو.", "en": "❌ Error fetching video info.", "ru": "❌ Ошибка получения информации о видео.", "es": "❌ Error al obtener la información del video."},
    "yt_video_confirm_text": {
        "fa": "🎬 **{title}**\n👤 کانال: {channel}\n👁 بازدید: {views} | ⏱ زمان: {dur}\n🔗 لینک: https://youtu.be/{vid_id}\n\nآیا این ویدیوی مورد نظر شماست؟",
        "en": "🎬 **{title}**\n👤 Channel: {channel}\n👁 Views: {views} | ⏱ Duration: {dur}\n🔗 Link: https://youtu.be/{vid_id}\n\nIs this the video you meant?",
        "ru": "🎬 **{title}**\n👤 Канал: {channel}\n👁 Просмотры: {views} | ⏱ Длительность: {dur}\n🔗 Ссылка: https://youtu.be/{vid_id}\n\nЭто нужное вам видео?",
        "es": "🎬 **{title}**\n👤 Canal: {channel}\n👁 Vistas: {views} | ⏱ Duración: {dur}\n🔗 Enlace: https://youtu.be/{vid_id}\n\n¿Es este el video que buscabas?",
    },
    "yt_btn_confirm_video": {"fa": "✅ بله، این ویدیوی من است", "en": "✅ Yes, this is my video", "ru": "✅ Да, это моё видео", "es": "✅ Sí, este es mi video"},
    "yt_btn_ai_subs": {"fa": "📝 زیرنویس فارسی (هوش مصنوعی)", "en": "📝 AI Subtitles", "ru": "📝 Субтитры (ИИ)", "es": "📝 Subtítulos con IA"},
    "yt_btn_no_back": {"fa": "❌ خیر، بازگشت به منو", "en": "❌ No, back to menu", "ru": "❌ Нет, назад в меню", "es": "❌ No, volver al menú"},
    "yt_btn_fmt_video": {"fa": "🎬 ویدیو (MP4)", "en": "🎬 Video (MP4)", "ru": "🎬 Видео (MP4)", "es": "🎬 Video (MP4)"},
    "yt_btn_fmt_audio": {"fa": "🎵 صدا (MP3)", "en": "🎵 Audio (MP3)", "ru": "🎵 Аудио (MP3)", "es": "🎵 Audio (MP3)"},
    "yt_choose_format": {"fa": "فرمت فایل را انتخاب کنید:", "en": "Choose the file format:", "ru": "Выберите формат файла:", "es": "Elige el formato del archivo:"},
    "yt_mp3_selected": {"fa": "⚙️ فرمت MP3 انتخاب شد. تنظیمات جانبی:", "en": "⚙️ MP3 format selected. Extra settings:", "ru": "⚙️ Выбран формат MP3. Дополнительные настройки:", "es": "⚙️ Formato MP3 seleccionado. Configuración adicional:"},
    "yt_calculating_size": {"fa": "⏳ در حال محاسبه حجم فایل...", "en": "⏳ Calculating file size...", "ru": "⏳ Вычисление размера файла...", "es": "⏳ Calculando el tamaño del archivo..."},
    "yt_choose_quality": {"fa": "کیفیت ویدیو را انتخاب کنید:", "en": "Choose the video quality:", "ru": "Выберите качество видео:", "es": "Elige la calidad del video:"},
    "yt_extras_optional": {"fa": "⚙️ تنظیمات جانبی ویدیو (اختیاری):", "en": "⚙️ Extra video settings (optional):", "ru": "⚙️ Дополнительные настройки видео (необязательно):", "es": "⚙️ Configuración adicional del video (opcional):"},
    "yt_btn_first_15s": {"fa": "⏱ ۱۵ ثانیه اول", "en": "⏱ First 15 seconds", "ru": "⏱ Первые 15 секунд", "es": "⏱ Primeros 15 segundos"},
    "yt_btn_first_30s": {"fa": "⏱ ۳۰ ثانیه اول", "en": "⏱ First 30 seconds", "ru": "⏱ Первые 30 секунд", "es": "⏱ Primeros 30 segundos"},
    "yt_btn_cancel_trim": {"fa": "🔙 لغو برش", "en": "🔙 Cancel Trim", "ru": "🔙 Отменить обрезку", "es": "🔙 Cancelar recorte"},
    "yt_trim_prompt": {"fa": "✂️ **برش ویدیو**\n\nزمان شروع و پایان را به صورت `01:15 - 02:30` ارسال کنید:", "en": "✂️ **Trim Video**\n\nSend the start and end time like `01:15 - 02:30`:", "ru": "✂️ **Обрезка видео**\n\nОтправьте время начала и конца в формате `01:15 - 02:30`:", "es": "✂️ **Recortar video**\n\nEnvía la hora de inicio y fin como `01:15 - 02:30`:"},
    "yt_download_confirmed": {"fa": "✅ **ویدیوی شما جهت دانلود تایید شد.**\nدر حال پردازش...", "en": "✅ **Your video is confirmed for download.**\nProcessing...", "ru": "✅ **Ваше видео подтверждено для загрузки.**\nОбработка...", "es": "✅ **Tu video está confirmado para descargar.**\nProcesando..."},
    "yt_connecting_server": {"fa": "🚀 **در حال اتصال به سرور دانلود...**", "en": "🚀 **Connecting to the download server...**", "ru": "🚀 **Подключение к серверу загрузки...**", "es": "🚀 **Conectando con el servidor de descarga...**"},
    "yt_request_sent": {"fa": "🚀 **درخواست با موفقیت به سرور ارسال شد!**\nشروع عملیات دانلود...", "en": "🚀 **Request sent to the server successfully!**\nStarting the download...", "ru": "🚀 **Запрос успешно отправлен на сервер!**\nНачало загрузки...", "es": "🚀 **¡Solicitud enviada al servidor con éxito!**\nIniciando la descarga..."},
    "yt_github_connection_error": {"fa": "❌ خطا در برقراری ارتباط با سرور GitHub Actions.", "en": "❌ Error connecting to the GitHub Actions server.", "ru": "❌ Ошибка подключения к серверу GitHub Actions.", "es": "❌ Error al conectar con el servidor de GitHub Actions."},
    "searching_official_db": {"fa": "⏳ در حال جستجو در دیتابیس اورجینال...", "en": "⏳ Searching the original database...", "ru": "⏳ Поиск в оригинальной базе данных...", "es": "⏳ Buscando en la base de datos original..."},
    "searching_spotify": {"fa": "⏳ در حال جستجو در اسپاتیفای...", "en": "⏳ Searching Spotify...", "ru": "⏳ Поиск в Spotify...", "es": "⏳ Buscando en Spotify..."},
    "searching_by_lyrics": {"fa": "🔎 در حال جستجو بر اساس متن آهنگ...", "en": "🔎 Searching by lyrics...", "ru": "🔎 Поиск по тексту песни...", "es": "🔎 Buscando por letra..."},
    "checking_generic": {"fa": "⏳ در حال بررسی...", "en": "⏳ Checking...", "ru": "⏳ Проверка...", "es": "⏳ Verificando..."},
    "sub_invalid_target": {"fa": "❌ نشانی معتبر نبود. لینک کانال یوتیوب، پیج اینستاگرام یا تیک‌تاک را بفرستید.", "en": "❌ Invalid address. Send a YouTube channel, Instagram page, or TikTok link.", "ru": "❌ Недействительный адрес. Отправьте ссылку на канал YouTube, страницу Instagram или TikTok.", "es": "❌ Dirección no válida. Envía un enlace de canal de YouTube, página de Instagram o TikTok."},
    "sub_max_reached": {"fa": "❌ حداکثر {max} اشتراک مجاز است.", "en": "❌ Maximum of {max} subscriptions allowed.", "ru": "❌ Разрешено максимум {max} подписок.", "es": "❌ Se permite un máximo de {max} suscripciones."},
    "sub_add_error": {"fa": "❌ خطا در ثبت اشتراک.", "en": "❌ Error saving the subscription.", "ru": "❌ Ошибка при сохранении подписки.", "es": "❌ Error al guardar la suscripción."},
    "sub_added_confirm": {"fa": "✅ اشتراک ثبت شد:\n**{label}**\n\nپست‌های جدید به‌صورت خودکار ارسال می‌شود.", "en": "✅ Subscription added:\n**{label}**\n\nNew posts will be sent automatically.", "ru": "✅ Подписка добавлена:\n**{label}**\n\nНовые посты будут отправляться автоматически.", "es": "✅ Suscripción añadida:\n**{label}**\n\nLas publicaciones nuevas se enviarán automáticamente."},
    "btn_my_subs": {"fa": "🔔 اشتراک‌های من", "en": "🔔 My Subscriptions", "ru": "🔔 Мои подписки", "es": "🔔 Mis suscripciones"},
    "searching_youtube_db": {"fa": "⏳ در حال جستجو در دیتابیس یوتیوب...", "en": "⏳ Searching the YouTube database...", "ru": "⏳ Поиск в базе данных YouTube...", "es": "⏳ Buscando en la base de datos de YouTube..."},
    "searching_pinterest": {"fa": "⏳ در حال جستجو در پینترست...", "en": "⏳ Searching Pinterest...", "ru": "⏳ Поиск в Pinterest...", "es": "⏳ Buscando en Pinterest..."},
    "searching_reddit": {"fa": "⏳ در حال جستجو در ردیت...", "en": "⏳ Searching Reddit...", "ru": "⏳ Поиск в Reddit...", "es": "⏳ Buscando en Reddit..."},
    "searching_images": {"fa": "⏳ در حال جستجوی تصویر...", "en": "⏳ Searching images...", "ru": "⏳ Поиск изображений...", "es": "⏳ Buscando imágenes..."},
    "checking_youtube_channels": {"fa": "⏳ در حال بررسی کانال‌های یوتیوب...", "en": "⏳ Checking YouTube channels...", "ru": "⏳ Проверка каналов YouTube...", "es": "⏳ Verificando canales de YouTube..."},
    "vedit_trim_format_error": {"fa": "❌ فرمت اشتباه است. مثال: `00:05-00:15`", "en": "❌ Wrong format. Example: `00:05-00:15`", "ru": "❌ Неверный формат. Пример: `00:05-00:15`", "es": "❌ Formato incorrecto. Ejemplo: `00:05-00:15`"},
    "vedit_trimming": {"fa": "⚙️ در حال برش ویدیو...", "en": "⚙️ Trimming the video...", "ru": "⚙️ Обрезка видео...", "es": "⚙️ Recortando el video..."},
    "vedit_adding_watermark": {"fa": "⚙️ در حال افزودن واترمارک...", "en": "⚙️ Adding watermark...", "ru": "⚙️ Добавление водяного знака...", "es": "⚙️ Añadiendo marca de agua..."},
    "yt_trim_set_confirm": {"fa": "✅ **برش دقیق ثبت شد:** از {start} تا {end}", "en": "✅ **Exact trim set:** from {start} to {end}", "ru": "✅ **Точная обрезка установлена:** с {start} по {end}", "es": "✅ **Recorte exacto establecido:** de {start} a {end}"},
    "yt_btn_back_to_settings": {"fa": "⚙️ بازگشت به تنظیمات", "en": "⚙️ Back to Settings", "ru": "⚙️ Назад к настройкам", "es": "⚙️ Volver a la configuración"},
    "yt_trim_format_error": {"fa": "❌ فرمت اشتباه است. لطفاً دقیقاً مانند مثال بفرستید:\n`01:15 - 02:30`", "en": "❌ Wrong format. Please send it exactly like the example:\n`01:15 - 02:30`", "ru": "❌ Неверный формат. Отправьте точно как в примере:\n`01:15 - 02:30`", "es": "❌ Formato incorrecto. Envíalo exactamente como el ejemplo:\n`01:15 - 02:30`"},
    "ig_invalid_link": {"fa": "❌ لطفاً یک لینک معتبر اینستاگرام ارسال کنید.", "en": "❌ Please send a valid Instagram link.", "ru": "❌ Пожалуйста, отправьте действительную ссылку Instagram.", "es": "❌ Envía un enlace válido de Instagram."},
    "ig_identifying_link": {"fa": "⚡ **شناسایی لینک اینستاگرام...**", "en": "⚡ **Identifying Instagram link...**", "ru": "⚡ **Определение ссылки Instagram...**", "es": "⚡ **Identificando enlace de Instagram...**"},
    "tiktok_identifying_link": {"fa": "⚡ **شناسایی لینک تیک‌تاک...**", "en": "⚡ **Identifying TikTok link...**", "ru": "⚡ **Определение ссылки TikTok...**", "es": "⚡ **Identificando enlace de TikTok...**"},
    "sc_fetching_link": {"fa": "⚡ **در حال دریافت آهنگ از ساندکلود...**", "en": "⚡ **Fetching song from SoundCloud...**", "ru": "⚡ **Получение песни из SoundCloud...**", "es": "⚡ **Obteniendo canción de SoundCloud...**"},
    "spotify_identifying_link": {"fa": "⚡ **شناسایی لینک اسپاتیفای...**", "en": "⚡ **Identifying Spotify link...**", "ru": "⚡ **Определение ссылки Spotify...**", "es": "⚡ **Identificando enlace de Spotify...**"},
    "pt_identifying_link": {"fa": "📌 **شناسایی لینک پینترست...**", "en": "📌 **Identifying Pinterest link...**", "ru": "📌 **Определение ссылки Pinterest...**", "es": "📌 **Identificando enlace de Pinterest...**"},
    "rd_identifying_link": {"fa": "🔴 **شناسایی لینک ردیت...**", "en": "🔴 **Identifying Reddit link...**", "ru": "🔴 **Определение ссылки Reddit...**", "es": "🔴 **Identificando enlace de Reddit...**"},
    "yt_video_process_error": {"fa": "❌ خطا در پردازش اطلاعات.", "en": "❌ Error processing information.", "ru": "❌ Ошибка обработки информации.", "es": "❌ Error al procesar la información."},
})

async def render_video_search(msg: Message, query, chat_id, page=1):
    try:
        store = user_data.setdefault(chat_id, {})
        # Fetch the full candidate list once via yt-dlp's own search (no
        # YouTube Data API quota cost, unlike the old search.list+videos.list
        # pair this replaced); pages are then sliced in memory, since yt-dlp's
        # search doesn't support page tokens and re-searching per page would
        # just be wasteful.
        if store.get("search_query") != query or not store.get("vid_all"):
            def _search():
                with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, 'extract_flat': True}) as ydl:
                    info = ydl.extract_info(f"ytsearch25:{query}", download=False)
                return info.get('entries') or []
            entries = await asyncio.to_thread(_search)
            store["vid_all"] = entries
            store["search_query"] = query
            store["is_channel_search"] = False

        vid_all = store.get("vid_all") or []
        if not vid_all:
            return await msg.edit_text(t(chat_id, "yt_no_videos"), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="yt_menu")]]))

        page_size = 5
        pages = max(1, (len(vid_all) + page_size - 1) // page_size)
        page = max(1, min(page, pages))
        chunk = vid_all[(page - 1) * page_size: page * page_size]

        store["vid_results"] = [e.get("id") for e in chunk]

        text = t(chat_id, "yt_search_results_header", query=query, page=page, pages=pages)
        kb_buttons = []
        for i, e in enumerate(chunk):
            title = (e.get("title") or "")[:45]
            dur_sec = int(e.get("duration") or 0)
            dur = f"{dur_sec // 60}:{dur_sec % 60:02d}" if dur_sec else "?"
            views = format_number(e.get("view_count") or 0)
            channel = e.get("channel") or e.get("uploader") or "?"
            text += f"{i+1}️⃣ **{title}**\n👤 {channel} | 👁 {views} | ⏱ {dur}\n\n"
            kb_buttons.append(InlineKeyboardButton(str(i + 1), callback_data=f"vid_{i}"))

        nav = []
        if page > 1: nav.append(InlineKeyboardButton(t(chat_id, "btn_prev"), callback_data=f"page_prev_{page-1}"))
        if page < pages: nav.append(InlineKeyboardButton(t(chat_id, "btn_next"), callback_data=f"page_next_{page+1}"))
        await msg.edit_text(text, reply_markup=InlineKeyboardMarkup([kb_buttons, nav, [InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="yt_menu")]] if nav else [kb_buttons, [InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="yt_menu")]]))
    except Exception:
        traceback.print_exc()
        await msg.edit_text(t(chat_id, "yt_search_process_error"), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="yt_menu")]]))


async def render_channel_search(msg: Message, query, chat_id, token=None, page=1):
    try:
        params = {"part": "snippet", "type": "channel", "maxResults": 5, "q": query}
        if token: params["pageToken"] = token
        data = yt_api("search", params)
        if not data or "items" not in data or not data["items"]: return await msg.edit_text(t(chat_id, "yt_no_channels"), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="yt_menu")]]))

        items = data["items"]
        user_data.setdefault(chat_id, {})["chan_results"] = [i["id"]["channelId"] for i in items]
        user_data[chat_id]["search_query"] = query
        user_data[chat_id]["is_channel_search"] = True
        user_data[chat_id]["next_token"] = data.get("nextPageToken")
        user_data[chat_id]["prev_token"] = data.get("prevPageToken")

        stats = yt_api("channels", {"part": "statistics", "id": ",".join([i["id"]["channelId"] for i in items])})
        s_dict = {i["id"]: i for i in stats.get("items", [])}
        text = t(chat_id, "yt_channel_results_header", query=query, page=page)
        kb_buttons = []
        for i, item in enumerate(items):
            subs = format_number(s_dict.get(item["id"]["channelId"], {}).get("statistics", {}).get("subscriberCount", "0"))
            v_count = format_number(s_dict.get(item["id"]["channelId"], {}).get("statistics", {}).get("videoCount", "0"))
            text += f"{i+1}️⃣ **{item['snippet']['title'][:40]}**\n" + t(chat_id, "yt_channel_subs_line", subs=subs, videos=v_count)
            kb_buttons.append(InlineKeyboardButton(str(i+1), callback_data=f"chan_{i}"))

        nav = []
        if data.get("nextPageToken"): nav.append(InlineKeyboardButton(t(chat_id, "btn_next"), callback_data=f"page_next_{page+1}"))
        if data.get("prevPageToken"): nav.append(InlineKeyboardButton(t(chat_id, "btn_prev"), callback_data=f"page_prev_{page-1}"))
        await msg.edit_text(text, reply_markup=InlineKeyboardMarkup([kb_buttons, nav, [InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="yt_menu")]] if nav else [kb_buttons, [InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="yt_menu")]]))
    except Exception: await msg.edit_text(t(chat_id, "yt_channel_search_error"))

async def fetch_and_verify_video(msg: Message, chat_id, vid_id):
    try:
        data = yt_api("videos", {"part": "snippet,contentDetails,statistics", "id": vid_id})
        if not data or "items" not in data or not data["items"]: return await msg.edit_text(t(chat_id, "yt_video_info_error"), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="yt_menu")]]))

        item = data["items"][0]
        dur = parse_pt_duration(item["contentDetails"]["duration"])
        views = format_number(item["statistics"].get("viewCount", "0"))
        thumb_url = item["snippet"]["thumbnails"].get("maxres", item["snippet"]["thumbnails"].get("high", item["snippet"]["thumbnails"].get("default", {})))["url"]

        user_data.setdefault(chat_id, {})["target_vid"] = vid_id
        text = t(chat_id, "yt_video_confirm_text", title=item['snippet']['title'], channel=item['snippet']['channelTitle'], views=views, dur=dur, vid_id=vid_id)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t(chat_id, "yt_btn_confirm_video"), callback_data="fmt_choose")],
            [InlineKeyboardButton(t(chat_id, "yt_btn_ai_subs"), callback_data=f"aisub_{vid_id}")],
            [InlineKeyboardButton(t(chat_id, "yt_btn_no_back"), callback_data="yt_menu")]])
        await msg.delete()
        await msg._client.send_photo(chat_id, thumb_url, caption=text, reply_markup=kb)
    except Exception: await msg.edit_text(t(chat_id, "yt_video_process_error"))

# ---------------- 7.5 VIDEO EDITOR ENGINE ----------------
# Local ffmpeg toolkit for user-uploaded clips. Runs in-process (not the
# GitHub-Actions-style local job dispatch used for Whisper/Demucs/downloads)
# but shares that same _HEAVY_JOB_SLOTS semaphore so at most one heavy
# CPU/RAM job of any kind runs at once on this 1.9GB box.
VIDEO_EDIT_DIR = "video_edits"
os.makedirs(VIDEO_EDIT_DIR, exist_ok=True)
MAX_REVERSE_SECONDS = 30
MAX_VIDEO_UPLOAD_BYTES = 20 * 1024 * 1024  # Bale getFile limit
_ARABIC_RANGE = re.compile(r'[؀-ۿ]')
FONT_LATIN = "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"
FONT_ARABIC = "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf"

STD_VENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-c:a", "aac", "-b:a", "128k"]

VIDEO_EDIT_LABELS = {
    "trim": {"fa": "✂️ برش", "en": "✂️ Trim", "ru": "✂️ Обрезка", "es": "✂️ Recortar"},
    "mute": {"fa": "🔇 حذف صدا", "en": "🔇 Mute", "ru": "🔇 Без звука", "es": "🔇 Silenciar"},
    "extract_audio": {"fa": "🎵 استخراج صدا", "en": "🎵 Extract Audio", "ru": "🎵 Извлечь звук", "es": "🎵 Extraer audio"},
    "rotate_cw": {"fa": "🔄 چرخش ۹۰ درجه", "en": "🔄 Rotate 90°", "ru": "🔄 Поворот на 90°", "es": "🔄 Girar 90°"},
    "rotate_ccw": {"fa": "🔄 چرخش ۹۰- درجه", "en": "🔄 Rotate -90°", "ru": "🔄 Поворот на -90°", "es": "🔄 Girar -90°"},
    "rotate_180": {"fa": "🔄 چرخش ۱۸۰ درجه", "en": "🔄 Rotate 180°", "ru": "🔄 Поворот на 180°", "es": "🔄 Girar 180°"},
    "flip_h": {"fa": "↔️ آینه افقی", "en": "↔️ Horizontal Flip", "ru": "↔️ Отражение по горизонтали", "es": "↔️ Espejo horizontal"},
    "flip_v": {"fa": "↕️ آینه عمودی", "en": "↕️ Vertical Flip", "ru": "↕️ Отражение по вертикали", "es": "↕️ Espejo vertical"},
    "crop_square": {"fa": "🔲 برش مربعی", "en": "🔲 Square Crop", "ru": "🔲 Обрезка в квадрат", "es": "🔲 Recorte cuadrado"},
    "crop_9x16": {"fa": "📱 برش استوری", "en": "📱 Story Crop", "ru": "📱 Обрезка для истории", "es": "📱 Recorte para historia"},
    "crop_16x9": {"fa": "🖥 برش عریض", "en": "🖥 Wide Crop", "ru": "🖥 Широкая обрезка", "es": "🖥 Recorte ancho"},
    "speed_05": {"fa": "🐢 نصف سرعت", "en": "🐢 Half Speed", "ru": "🐢 Половина скорости", "es": "🐢 Media velocidad"},
    "speed_15": {"fa": "⏩ سرعت ۱.۵ برابر", "en": "⏩ 1.5x Speed", "ru": "⏩ Скорость x1.5", "es": "⏩ Velocidad 1.5x"},
    "speed_2": {"fa": "⏩ سرعت ۲ برابر", "en": "⏩ 2x Speed", "ru": "⏩ Скорость x2", "es": "⏩ Velocidad 2x"},
    "compress": {"fa": "🗜 فشرده‌سازی", "en": "🗜 Compress", "ru": "🗜 Сжатие", "es": "🗜 Comprimir"},
    "reverse": {"fa": "⏮ معکوس", "en": "⏮ Reverse", "ru": "⏮ Реверс", "es": "⏮ Invertir"},
    "gif": {"fa": "🖼 ساخت GIF", "en": "🖼 Make GIF", "ru": "🖼 Создать GIF", "es": "🖼 Crear GIF"},
    "watermark": {"fa": "📝 واترمارک متنی", "en": "📝 Text Watermark", "ru": "📝 Текстовый водяной знак", "es": "📝 Marca de agua de texto"},
}


def vedit_label(chat_id, action: str) -> str:
    entry = VIDEO_EDIT_LABELS.get(action)
    if not entry:
        return action
    lang = get_lang(chat_id)
    return entry.get(lang) or entry[DEFAULT_LANG]


STRINGS.update({
    "vedit_btn_trim": VIDEO_EDIT_LABELS["trim"],
    "vedit_btn_watermark": VIDEO_EDIT_LABELS["watermark"],
    "vedit_btn_square": {"fa": "🔲 مربعی", "en": "🔲 Square", "ru": "🔲 Квадрат", "es": "🔲 Cuadrado"},
    "vedit_btn_story": {"fa": "📱 استوری", "en": "📱 Story", "ru": "📱 История", "es": "📱 Historia"},
    "vedit_btn_wide": {"fa": "🖥 عریض", "en": "🖥 Wide", "ru": "🖥 Широкий", "es": "🖥 Ancho"},
    "vedit_btn_rot90": {"fa": "🔄 ۹۰°", "en": "🔄 90°", "ru": "🔄 90°", "es": "🔄 90°"},
    "vedit_btn_rotneg90": {"fa": "🔄 ۹۰-°", "en": "🔄 -90°", "ru": "🔄 -90°", "es": "🔄 -90°"},
    "vedit_btn_rot180": {"fa": "🔄 ۱۸۰°", "en": "🔄 180°", "ru": "🔄 180°", "es": "🔄 180°"},
    "vedit_btn_fliph": VIDEO_EDIT_LABELS["flip_h"],
    "vedit_btn_flipv": VIDEO_EDIT_LABELS["flip_v"],
    "vedit_btn_speed05": {"fa": "🐢 0.5x", "en": "🐢 0.5x", "ru": "🐢 0.5x", "es": "🐢 0.5x"},
    "vedit_btn_speed15": {"fa": "⏩ 1.5x", "en": "⏩ 1.5x", "ru": "⏩ 1.5x", "es": "⏩ 1.5x"},
    "vedit_btn_speed2": {"fa": "⏩ 2x", "en": "⏩ 2x", "ru": "⏩ 2x", "es": "⏩ 2x"},
    "vedit_btn_mute": VIDEO_EDIT_LABELS["mute"],
    "vedit_btn_extract_audio": VIDEO_EDIT_LABELS["extract_audio"],
    "vedit_btn_compress": VIDEO_EDIT_LABELS["compress"],
    "vedit_btn_reverse": VIDEO_EDIT_LABELS["reverse"],
    "vedit_btn_gif": VIDEO_EDIT_LABELS["gif"],
    "vedit_btn_identify": {"fa": "🎧 شناسایی آهنگ", "en": "🎧 Identify Song", "ru": "🎧 Распознать песню", "es": "🎧 Identificar canción"},
    "vedit_btn_end": {"fa": "🗑 پایان و حذف فایل", "en": "🗑 End & Delete File", "ru": "🗑 Завершить и удалить файл", "es": "🗑 Terminar y eliminar archivo"},
    "vedit_menu_intro": {
        "fa": "🎬 **ویرایشگر ویدیو**\n\nکافیست یک فایل ویدیویی مستقیم در همین چت بفرستید؛ ابزارهای برش، واترمارک، چرخش، آینه، تغییر سرعت، فشرده‌سازی، معکوس‌سازی و ساخت GIF نمایش داده می‌شود.",
        "en": "🎬 **Video Editor**\n\nJust send a video file directly in this chat; tools for trim, watermark, rotate, mirror, speed change, compress, reverse and GIF-making will appear.",
        "ru": "🎬 **Видеоредактор**\n\nПросто отправьте видеофайл прямо в этот чат; появятся инструменты для обрезки, водяного знака, поворота, зеркала, изменения скорости, сжатия, реверса и создания GIF.",
        "es": "🎬 **Editor de video**\n\nSolo envía un archivo de video directamente en este chat; aparecerán herramientas para recortar, marca de agua, girar, espejo, cambiar velocidad, comprimir, invertir y crear GIF.",
    },
    "vedit_trim_prompt": {
        "fa": "✂️ **برش ویدیو**\n\nزمان شروع و پایان را به‌صورت `00:05-00:15` ارسال کنید:",
        "en": "✂️ **Trim Video**\n\nSend the start and end time like `00:05-00:15`:",
        "ru": "✂️ **Обрезка видео**\n\nОтправьте время начала и конца в формате `00:05-00:15`:",
        "es": "✂️ **Recortar video**\n\nEnvía la hora de inicio y fin como `00:05-00:15`:",
    },
    "vedit_watermark_prompt": {
        "fa": "📝 **واترمارک متنی**\n\nمتنی که می‌خواهید گوشه ویدیو نمایش داده شود را ارسال کنید:",
        "en": "📝 **Text Watermark**\n\nSend the text you want shown in the corner of the video:",
        "ru": "📝 **Текстовый водяной знак**\n\nОтправьте текст, который должен отображаться в углу видео:",
        "es": "📝 **Marca de agua de texto**\n\nEnvía el texto que quieres que aparezca en la esquina del video:",
    },
    "vedit_ready_text": {
        "fa": "🎬 **ویرایشگر ویدیو**\n\nیکی از ابزارها را انتخاب کنید:",
        "en": "🎬 **Video Editor**\n\nChoose one of the tools:",
        "ru": "🎬 **Видеоредактор**\n\nВыберите один из инструментов:",
        "es": "🎬 **Editor de video**\n\nElige una de las herramientas:",
    },
    "vedit_no_file": {
        "fa": "❌ فایلی برای ویرایش پیدا نشد. یک ویدیو ارسال کنید.",
        "en": "❌ No file found to edit. Send a video.",
        "ru": "❌ Файл для редактирования не найден. Отправьте видео.",
        "es": "❌ No se encontró ningún archivo para editar. Envía un video.",
    },
    "vedit_no_video_alert": {
        "fa": "❌ ویدیویی برای ویرایش یافت نشد.", "en": "❌ No video found to edit.",
        "ru": "❌ Видео для редактирования не найдено.", "es": "❌ No se encontró video para editar.",
    },
    "vedit_preparing": {
        "fa": "⬇️ **در حال آماده‌سازی برای ویرایش...**", "en": "⬇️ **Preparing for editing...**",
        "ru": "⬇️ **Подготовка к редактированию...**", "es": "⬇️ **Preparando para editar...**",
    },
    "vedit_download_failed": {
        "fa": "❌ دریافت ویدیو با خطا مواجه شد.", "en": "❌ Downloading the video failed.",
        "ru": "❌ Не удалось скачать видео.", "es": "❌ Error al descargar el video.",
    },
    "vedit_not_available": {
        "fa": "❌ این قابلیت روی سرور فعلی در دسترس نیست.", "en": "❌ This feature isn't available on the current server.",
        "ru": "❌ Эта функция недоступна на текущем сервере.", "es": "❌ Esta función no está disponible en el servidor actual.",
    },
    "vedit_reverse_too_long": {
        "fa": "❌ برای معکوس‌کردن، ویدیو باید کوتاه‌تر از {max_sec} ثانیه باشد (این ویدیو {dur} ثانیه است). اول با ✂️ برش یک بخش کوتاه‌تر بگیرید.",
        "en": "❌ To reverse, the video must be shorter than {max_sec} seconds (this video is {dur} seconds). Use ✂️ Trim first to get a shorter clip.",
        "ru": "❌ Для реверса видео должно быть короче {max_sec} секунд (это видео длится {dur} секунд). Сначала используйте ✂️ Обрезку, чтобы получить более короткий фрагмент.",
        "es": "❌ Para invertir, el video debe durar menos de {max_sec} segundos (este video dura {dur} segundos). Usa ✂️ Recortar primero para obtener un clip más corto.",
    },
    "vedit_trim_not_set": {
        "fa": "❌ بازه برش ثبت نشده.", "en": "❌ The trim range wasn't set.",
        "ru": "❌ Диапазон обрезки не задан.", "es": "❌ El rango de recorte no se estableció.",
    },
    "vedit_watermark_not_set": {
        "fa": "❌ متن واترمارک ثبت نشده.", "en": "❌ The watermark text wasn't set.",
        "ru": "❌ Текст водяного знака не задан.", "es": "❌ El texto de la marca de agua no se estableció.",
    },
    "vedit_invalid_action": {
        "fa": "❌ عملیات نامعتبر.", "en": "❌ Invalid operation.", "ru": "❌ Недопустимая операция.", "es": "❌ Operación no válida.",
    },
    "vedit_applying": {
        "fa": "⚙️ **در حال اعمال: {label}**\nممکن است چند دقیقه طول بکشد...",
        "en": "⚙️ **Applying: {label}**\nThis may take a few minutes...",
        "ru": "⚙️ **Применяется: {label}**\nЭто может занять несколько минут...",
        "es": "⚙️ **Aplicando: {label}**\nEsto puede tardar unos minutos...",
    },
    "vedit_timeout": {
        "fa": "❌ پردازش بیش از حد طول کشید و لغو شد.", "en": "❌ Processing took too long and was canceled.",
        "ru": "❌ Обработка заняла слишком много времени и была отменена.", "es": "❌ El procesamiento tardó demasiado y se canceló.",
    },
    "vedit_failed": {
        "fa": "❌ ویرایش با خطا مواجه شد.", "en": "❌ The edit failed.", "ru": "❌ Редактирование не удалось.", "es": "❌ La edición falló.",
    },
    "searching_text": {"fa": "⏳ در حال جستجو...", "en": "⏳ Searching...", "ru": "⏳ Поиск...", "es": "⏳ Buscando..."},
    "cmd_music_prompt": {
        "fa": "💽 **جستجوی موزیک اورجینال**\nنام آهنگ یا هنرمند را بفرستید:\n\n_یا مستقیم بنویسید:_ `/tahamusic نام آهنگ`",
        "en": "💽 **Original Music Search**\nSend the song name or artist:\n\n_Or type directly:_ `/tahamusic song name`",
        "ru": "💽 **Оригинальный поиск музыки**\nОтправьте название песни или исполнителя:\n\n_Или напишите прямо:_ `/tahamusic название песни`",
        "es": "💽 **Búsqueda de música original**\nEnvía el nombre de la canción o artista:\n\n_O escribe directamente:_ `/tahamusic nombre de la canción`",
    },
    "cmd_sc_prompt": {
        "fa": "☁️ **جستجو در ساندکلود**\nنام آهنگ یا هنرمند را بفرستید:\n\n_یا مستقیم بنویسید:_ `/tahasoundcloud نام آهنگ`",
        "en": "☁️ **SoundCloud Search**\nSend the song name or artist:\n\n_Or type directly:_ `/tahasoundcloud song name`",
        "ru": "☁️ **Поиск в SoundCloud**\nОтправьте название песни или исполнителя:\n\n_Или напишите прямо:_ `/tahasoundcloud название песни`",
        "es": "☁️ **Búsqueda en SoundCloud**\nEnvía el nombre de la canción o artista:\n\n_O escribe directamente:_ `/tahasoundcloud nombre de la canción`",
    },
    "cmd_yt_prompt": {
        "fa": "🔴 **جستجوی ویدیو در یوتیوب**\nکلمه کلیدی را بفرستید:\n\n_یا مستقیم بنویسید:_ `/tahayoutube کلمه کلیدی`",
        "en": "🔴 **YouTube Video Search**\nSend a keyword:\n\n_Or type directly:_ `/tahayoutube keyword`",
        "ru": "🔴 **Поиск видео на YouTube**\nОтправьте ключевое слово:\n\n_Или напишите прямо:_ `/tahayoutube ключевое слово`",
        "es": "🔴 **Búsqueda de video en YouTube**\nEnvía una palabra clave:\n\n_O escribe directamente:_ `/tahayoutube palabra clave`",
    },
    "cmd_spotify_prompt": {
        "fa": "🎧 **جستجوی اسپاتیفای**\nنام آهنگ یا هنرمند را بفرستید:\n\n_یا مستقیم بنویسید:_ `/tahaspotify نام آهنگ`",
        "en": "🎧 **Spotify Search**\nSend the song name or artist:\n\n_Or type directly:_ `/tahaspotify song name`",
        "ru": "🎧 **Поиск в Spotify**\nОтправьте название песни или исполнителя:\n\n_Или напишите прямо:_ `/tahaspotify название песни`",
        "es": "🎧 **Búsqueda en Spotify**\nEnvía el nombre de la canción o artista:\n\n_O escribe directamente:_ `/tahaspotify nombre de la canción`",
    },
    "cmd_lyrics_prompt": {
        "fa": "🔎 **جستجو از روی متن آهنگ**\nیک تکه از متن آهنگ را بفرستید:\n\n_یا مستقیم بنویسید:_ `/tahalyrics تکه‌ای از متن`",
        "en": "🔎 **Find a Song by Lyrics**\nSend a bit of the lyrics:\n\n_Or type directly:_ `/tahalyrics a bit of the lyrics`",
        "ru": "🔎 **Найти песню по тексту**\nОтправьте фрагмент текста песни:\n\n_Или напишите прямо:_ `/tahalyrics фрагмент текста`",
        "es": "🔎 **Encontrar canción por su letra**\nEnvía un fragmento de la letra:\n\n_O escribe directamente:_ `/tahalyrics fragmento de la letra`",
    },
    "cmd_pinterest_prompt": {
        "fa": "📌 **جستجوی پینترست**\nکلیدواژه را بفرستید:\n\n_یا مستقیم بنویسید:_ `/tahapinterest کلیدواژه`",
        "en": "📌 **Pinterest Search**\nSend a keyword:\n\n_Or type directly:_ `/tahapinterest keyword`",
        "ru": "📌 **Поиск в Pinterest**\nОтправьте ключевое слово:\n\n_Или напишите прямо:_ `/tahapinterest ключевое слово`",
        "es": "📌 **Búsqueda en Pinterest**\nEnvía una palabra clave:\n\n_O escribe directamente:_ `/tahapinterest palabra clave`",
    },
    "cmd_reddit_prompt": {
        "fa": "🔴 **جستجوی ردیت**\nکلیدواژه را بفرستید:\n\n_یا مستقیم بنویسید:_ `/tahareddit کلیدواژه`",
        "en": "🔴 **Reddit Search**\nSend a keyword:\n\n_Or type directly:_ `/tahareddit keyword`",
        "ru": "🔴 **Поиск на Reddit**\nОтправьте ключевое слово:\n\n_Или напишите прямо:_ `/tahareddit ключевое слово`",
        "es": "🔴 **Búsqueda en Reddit**\nEnvía una palabra clave:\n\n_O escribe directamente:_ `/tahareddit palabra clave`",
    },
    "cmd_photo_prompt": {
        "fa": "🖼 **جستجوی تصویر**\nکلیدواژه را بفرستید (انگلیسی نتیجه بهتری می‌دهد):\n\n_یا مستقیم بنویسید:_ `/tahaphoto کلیدواژه`",
        "en": "🖼 **Image Search**\nSend a keyword (English gives better results):\n\n_Or type directly:_ `/tahaphoto keyword`",
        "ru": "🖼 **Поиск изображений**\nОтправьте ключевое слово (на английском результаты лучше):\n\n_Или напишите прямо:_ `/tahaphoto ключевое слово`",
        "es": "🖼 **Búsqueda de imágenes**\nEnvía una palabra clave (el inglés da mejores resultados):\n\n_O escribe directamente:_ `/tahaphoto palabra clave`",
    },
    "cmd_tiktok_prompt": {
        "fa": "📱 **دانلود مستقیم از تیک‌تاک**\nآیدی پیج یا لینک مستقیم ویدیو را بفرستید:\n\n_یا مستقیم بنویسید:_ `/tahatiktok لینک یا آیدی`",
        "en": "📱 **Direct TikTok Download**\nSend a page ID or a direct video link:\n\n_Or type directly:_ `/tahatiktok link or id`",
        "ru": "📱 **Прямая загрузка из TikTok**\nОтправьте ID страницы или прямую ссылку на видео:\n\n_Или напишите прямо:_ `/tahatiktok ссылка или id`",
        "es": "📱 **Descarga directa de TikTok**\nEnvía un ID de página o un enlace directo del video:\n\n_O escribe directamente:_ `/tahatiktok enlace o id`",
    },
    "tiktok_fetching": {
        "fa": "⏳ در حال دریافت ویدیو از تیک‌تاک...", "en": "⏳ Fetching video from TikTok...",
        "ru": "⏳ Получение видео из TikTok...", "es": "⏳ Obteniendo video de TikTok...",
    },
    "cmd_shazam_prompt": {
        "fa": "🎧 **شناسایی آهنگ**\n\nیک ویس یا فایل صوتی از آهنگ مورد نظر برایم بفرستید؛ بلافاصله نام و خواننده‌اش را شناسایی می‌کنم و می‌توانید نسخه کامل را دانلود کنید.",
        "en": "🎧 **Song Recognition**\n\nSend me a voice note or audio file of the song; I'll instantly identify its title and artist, and you can download the full version.",
        "ru": "🎧 **Распознавание песни**\n\nОтправьте мне голосовое сообщение или аудиофайл песни; я мгновенно определю название и исполнителя, и вы сможете скачать полную версию.",
        "es": "🎧 **Reconocimiento de canciones**\n\nEnvíame una nota de voz o archivo de audio de la canción; identificaré al instante su título y artista, y podrás descargar la versión completa.",
    },
    "library_loading": {"fa": "⏳ در حال بارگذاری کتابخانه...", "en": "⏳ Loading library...", "ru": "⏳ Загрузка библиотеки...", "es": "⏳ Cargando biblioteca..."},
    "stats_loading": {"fa": "⏳ در حال محاسبه آمار...", "en": "⏳ Calculating stats...", "ru": "⏳ Вычисление статистики...", "es": "⏳ Calculando estadísticas..."},
    "vedit_too_large": {
        "fa": "❌ حجم ویدیو بیش از حد مجاز (۲۰ مگابایت؛ محدودیت دریافت فایل در بله) است.", "en": "❌ The video exceeds the maximum size (20 MB, Bale download limit).",
        "ru": "❌ Видео превышает максимальный размер (20 МБ).", "es": "❌ El video excede el tamaño máximo (20 MB, Bale download limit).",
    },
    "vedit_receiving": {
        "fa": "⬇️ **در حال دریافت ویدیو...**", "en": "⬇️ **Receiving video...**",
        "ru": "⬇️ **Получение видео...**", "es": "⬇️ **Recibiendo video...**",
    },
    "vedit_ready_full_text": {
        "fa": "🎬 **ویرایشگر ویدیو**\n\nویدیوی شما آماده است. یکی از ابزارها را انتخاب کنید:",
        "en": "🎬 **Video Editor**\n\nYour video is ready. Choose one of the tools:",
        "ru": "🎬 **Видеоредактор**\n\nВаше видео готово. Выберите один из инструментов:",
        "es": "🎬 **Editor de video**\n\nTu video está listo. Elige una de las herramientas:",
    },
    "vedit_session_ended": {
        "fa": "✅ جلسه ویرایش پایان یافت.", "en": "✅ Editing session ended.", "ru": "✅ Сессия редактирования завершена.", "es": "✅ Sesión de edición finalizada.",
    },
})


def video_editor_kb(chat_id) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(chat_id, "vedit_btn_trim"), callback_data="vedit_trim"),
         InlineKeyboardButton(t(chat_id, "vedit_btn_watermark"), callback_data="vedit_watermark")],
        [InlineKeyboardButton(t(chat_id, "vedit_btn_square"), callback_data="vedit_crop_square"),
         InlineKeyboardButton(t(chat_id, "vedit_btn_story"), callback_data="vedit_crop_9x16"),
         InlineKeyboardButton(t(chat_id, "vedit_btn_wide"), callback_data="vedit_crop_16x9")],
        [InlineKeyboardButton(t(chat_id, "vedit_btn_rot90"), callback_data="vedit_rotate_cw"),
         InlineKeyboardButton(t(chat_id, "vedit_btn_rotneg90"), callback_data="vedit_rotate_ccw"),
         InlineKeyboardButton(t(chat_id, "vedit_btn_rot180"), callback_data="vedit_rotate_180")],
        [InlineKeyboardButton(t(chat_id, "vedit_btn_fliph"), callback_data="vedit_flip_h"),
         InlineKeyboardButton(t(chat_id, "vedit_btn_flipv"), callback_data="vedit_flip_v")],
        [InlineKeyboardButton(t(chat_id, "vedit_btn_speed05"), callback_data="vedit_speed_05"),
         InlineKeyboardButton(t(chat_id, "vedit_btn_speed15"), callback_data="vedit_speed_15"),
         InlineKeyboardButton(t(chat_id, "vedit_btn_speed2"), callback_data="vedit_speed_2")],
        [InlineKeyboardButton(t(chat_id, "vedit_btn_mute"), callback_data="vedit_mute"),
         InlineKeyboardButton(t(chat_id, "vedit_btn_extract_audio"), callback_data="vedit_extract_audio")],
        [InlineKeyboardButton(t(chat_id, "vedit_btn_compress"), callback_data="vedit_compress"),
         InlineKeyboardButton(t(chat_id, "vedit_btn_reverse"), callback_data="vedit_reverse")],
        [InlineKeyboardButton(t(chat_id, "vedit_btn_gif"), callback_data="vedit_gif"),
         InlineKeyboardButton(t(chat_id, "vedit_btn_identify"), callback_data="vedit_identify")],
        [InlineKeyboardButton(t(chat_id, "vedit_btn_end"), callback_data="vedit_cancel")],
    ])


def probe_duration_sync(path: str) -> float:
    """ffprobe's own duration read; blocking, call via to_thread."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=20
        )
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def apply_video_edit_sync(args: list, timeout: int = 900):
    """Run one ffmpeg edit. Blocking; call via to_thread."""
    with _HEAVY_JOB_SLOTS:
        subprocess.run(
            [FFMPEG_BIN, "-y", "-loglevel", "error", *args],
            check=True, capture_output=True, timeout=timeout
        )


def cleanup_stale_video_edits(max_age_sec: int = 3 * 3600):
    """Sweeps orphaned uploads/outputs left behind by abandoned edit sessions
    or a mid-flow restart -- nothing else ever deletes the source clip."""
    now = time.time()
    try:
        for fn in os.listdir(VIDEO_EDIT_DIR):
            fp = os.path.join(VIDEO_EDIT_DIR, fn)
            try:
                if now - os.path.getmtime(fp) > max_age_sec:
                    os.remove(fp)
            except OSError:
                pass
    except Exception:
        pass


def _escape_drawtext(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace('"', "'")


async def process_video_edit(client: Client, chat_id: int, msg: Message, action: str):
    e = user_data.get(chat_id, {})
    src = e.get("vedit_path")

    if action == "cancel":
        if src and os.path.exists(src):
            try: os.remove(src)
            except OSError: pass
        user_data.setdefault(chat_id, {}).pop("vedit_path", None)
        return await msg.edit_text(t(chat_id, "vedit_session_ended"), reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(t(chat_id, "main_menu_btn"), callback_data="menu_main")]]))

    if not src or not os.path.exists(src):
        return await msg.edit_text(t(chat_id, "vedit_no_file"))
    if not FFMPEG_AVAILABLE:
        return await msg.edit_text(t(chat_id, "vedit_not_available"))

    if action == "identify":
        return await process_audio_recognition(client, chat_id, msg, src)

    label = vedit_label(chat_id, action)
    await msg.edit_text(t(chat_id, "vedit_applying", label=label))

    ts = int(time.time() * 1000)
    out = os.path.join(VIDEO_EDIT_DIR, f"vedit_{ts}.mp4")
    send_as = "video"
    args = None

    try:
        dur = await asyncio.to_thread(probe_duration_sync, src)

        if action == "reverse" and dur > MAX_REVERSE_SECONDS:
            return await msg.edit_text(
                t(chat_id, "vedit_reverse_too_long", max_sec=MAX_REVERSE_SECONDS, dur=int(dur)),
                reply_markup=video_editor_kb(chat_id))

        if action == "trim":
            start, end = e.get("vedit_trim_start"), e.get("vedit_trim_end")
            if not start or not end:
                return await msg.edit_text(t(chat_id, "vedit_trim_not_set"), reply_markup=video_editor_kb(chat_id))
            args = ["-i", src, "-ss", start, "-to", end, *STD_VENC, out]
        elif action == "mute":
            args = ["-i", src, "-c:v", "copy", "-an", out]
        elif action == "extract_audio":
            out = os.path.join(VIDEO_EDIT_DIR, f"vedit_{ts}.mp3")
            args = ["-i", src, "-vn", "-c:a", "libmp3lame", "-b:a", "192k", out]
            send_as = "audio"
        elif action == "rotate_cw":
            args = ["-i", src, "-vf", "transpose=1", *STD_VENC, out]
        elif action == "rotate_ccw":
            args = ["-i", src, "-vf", "transpose=2", *STD_VENC, out]
        elif action == "rotate_180":
            args = ["-i", src, "-vf", "transpose=1,transpose=1", *STD_VENC, out]
        elif action == "flip_h":
            args = ["-i", src, "-vf", "hflip", *STD_VENC, out]
        elif action == "flip_v":
            args = ["-i", src, "-vf", "vflip", *STD_VENC, out]
        elif action == "crop_square":
            args = ["-i", src, "-vf", "crop='min(iw,ih)':'min(iw,ih)'", *STD_VENC, out]
        elif action == "crop_9x16":
            args = ["-i", src, "-vf", "crop=ih*9/16:ih", *STD_VENC, out]
        elif action == "crop_16x9":
            args = ["-i", src, "-vf", "crop=iw:iw*9/16", *STD_VENC, out]
        elif action == "speed_05":
            args = ["-i", src, "-vf", "setpts=2.0*PTS", "-af", "atempo=0.5", *STD_VENC, out]
        elif action == "speed_15":
            args = ["-i", src, "-vf", "setpts=PTS/1.5", "-af", "atempo=1.5", *STD_VENC, out]
        elif action == "speed_2":
            args = ["-i", src, "-vf", "setpts=0.5*PTS", "-af", "atempo=2.0", *STD_VENC, out]
        elif action == "compress":
            args = ["-i", src, "-vf", "scale='min(1280,iw)':-2", "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "28", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    "-c:a", "aac", "-b:a", "96k", out]
        elif action == "reverse":
            args = ["-i", src, "-vf", "reverse", "-af", "areverse", *STD_VENC, out]
        elif action == "gif":
            out = os.path.join(VIDEO_EDIT_DIR, f"vedit_{ts}.gif")
            clip_len = min(dur, 8) if dur else 8
            args = ["-i", src, "-t", str(clip_len), "-vf", "fps=12,scale=480:-1:flags=lanczos", out]
            send_as = "animation"
        elif action == "watermark":
            wm_text = e.get("vedit_watermark_text", "")
            if not wm_text:
                return await msg.edit_text(t(chat_id, "vedit_watermark_not_set"), reply_markup=video_editor_kb(chat_id))
            font = FONT_ARABIC if _ARABIC_RANGE.search(wm_text) else FONT_LATIN
            drawtext = (f"drawtext=fontfile={font}:text='{_escape_drawtext(wm_text)}':"
                       "fontcolor=white:fontsize=28:x=(w-text_w)-20:y=(h-text_h)-20:"
                       "box=1:boxcolor=black@0.4:boxborderw=8")
            args = ["-i", src, "-vf", drawtext, "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "copy", out]
        else:
            return await msg.edit_text(t(chat_id, "vedit_invalid_action"))

        # Heavy ffmpeg work runs on GitHub Actions (Render's free CPU is 0.1);
        # the runner fetches the clip from Bale by file_id and replies itself.
        src_fid = e.get("last_video_file_id")
        if src_fid and GITHUB_TOKEN and os.environ.get("VEDIT_ON_ACTIONS", "true").lower() == "true":
            def _ph(a):
                return (a.replace(src, "{SRC}").replace(out, "{OUT}")
                        .replace(FONT_ARABIC, "{FONT_ARABIC}").replace(FONT_LATIN, "{FONT_LATIN}"))
            payload = {
                "FILE_ID": src_fid, "ARGS": json.dumps([_ph(a) for a in args], ensure_ascii=False),
                "OUT_EXT": os.path.splitext(out)[1], "SEND_AS": send_as,
                "CHAT_ID": str(chat_id), "MESSAGE_ID": str(msg.id), "CAPTION": f"✅ {label}",
                "AUDIO_TITLE": t(chat_id, "vedit_audio_title") if send_as == "audio" else "",
                "REPLY_MARKUP": json.dumps(client._markup(video_editor_kb(chat_id)), ensure_ascii=False),
            }
            if await asyncio.to_thread(trigger_media_worker, payload):
                log_activity(chat_id, "video_edit", label)
                await msg.edit_text(
                    f"⏳ {label}\n\n" + ("ویرایش به سرور پردازش ارسال شد؛ نتیجه تا چند دقیقه دیگر همین‌جا ارسال می‌شود."
                                          if get_lang(chat_id) == "fa" else
                                          "Sent to the processing server; the result will arrive here in a few minutes."))
                return
        t0 = time.time()
        await asyncio.to_thread(apply_video_edit_sync, args)
        print(f"⏱ vedit {action}: {time.time() - t0:.1f}s", flush=True)

        if not os.path.exists(out) or os.path.getsize(out) == 0:
            raise RuntimeError("empty output")

        log_activity(chat_id, "video_edit", label)
        cap = f"✅ {label}"
        if send_as == "audio":
            await client.send_audio(chat_id, audio=out, caption=cap, title=t(chat_id, "vedit_audio_title"),
                                    performer="Taha Bot")
        elif send_as == "animation":
            await client.send_animation(chat_id, animation=out, caption=cap)
        else:
            await client.send_video(chat_id, video=out, caption=cap, supports_streaming=True,
                                    reply_markup=video_editor_kb(chat_id))
        await msg.delete()
    except subprocess.TimeoutExpired:
        await msg.edit_text(t(chat_id, "vedit_timeout"), reply_markup=video_editor_kb(chat_id))
    except Exception:
        print(f"Video edit error:\n{traceback.format_exc()}", flush=True)
        try:
            await msg.edit_text(t(chat_id, "vedit_failed"), reply_markup=video_editor_kb(chat_id))
        except Exception:
            await client.send_message(chat_id, t(chat_id, "vedit_failed"))
    finally:
        if out and os.path.exists(out):
            try:
                os.remove(out)
            except OSError:
                pass



@bot.on_message(filters.command(["tool", "tools", "edit"]))
async def cmd_tools_entry(client: Client, message: Message):
    await handle_tool_command(client, message, user_data, user_states, get_lang)


@bot.on_message(filters.photo | (filters.document & filters.private))
async def photo_upload_handler(client: Client, message: Message):
    doc = message.photo or (message.document if (message.document.mime_type or "").startswith("image/") else None)
    if not doc:
        return
    chat_id = message.chat.id
    user_data.setdefault(chat_id, {})["last_image_file_id"] = doc.file_id
    # A photo sent straight to the bot used to get no reply at all unless the
    # user had first opened Image Tools. In private chat it now opens them.
    if user_states.get(chat_id) == "AWAIT_IMAGE_UPLOAD" or message.chat.type == ChatType.PRIVATE:
        user_states.pop(chat_id, None)
        os.makedirs("media_downloads", exist_ok=True)
        status = await message.reply_text("⏳ در حال دریافت تصویر...")
        try:
            ipath = await client.download_media(doc.file_id, file_name=f"media_downloads/img_{chat_id}_{int(time.time())}.png")
            user_data[chat_id]["image_tools_path"] = ipath
            await status.edit_text("🎨 **تصویر شما دریافت شد. ابزار مورد نظر را انتخاب کنید:**", reply_markup=get_image_tools_keyboard(chat_id, get_lang))
        except Exception as e:
            await status.edit_text(f"❌ خطا در دریافت تصویر: {e}")

@bot.on_message(filters.video | filters.document)
async def video_upload_handler(client: Client, message: Message):
    doc = message.video or message.document
    if message.document and not (message.document.mime_type or "").startswith("video/"):
        return
    chat_id = message.chat.id
    # Same group-gating as audio recognition: require an explicit /tahaedit
    # first in groups so random video shares don't spin up an editor session.
    if message.chat.type != ChatType.PRIVATE:
        if user_states.get(chat_id) != "AWAIT_VEDIT_UPLOAD":
            return
        user_states.pop(chat_id, None)
    if doc.file_size and doc.file_size > MAX_VIDEO_UPLOAD_BYTES:
        return await message.reply_text(t(chat_id, "vedit_too_large"))

    old = user_data.get(chat_id, {}).get("vedit_path")
    if old and os.path.exists(old):
        try: os.remove(old)
        except OSError: pass

    status = await message.reply_text(t(chat_id, "vedit_receiving"))
    try:
        path = await client.download_media(
            message, file_name=f"{VIDEO_EDIT_DIR}/src_{chat_id}_{int(time.time())}.mp4")
    except Exception:
        return await status.edit_text(t(chat_id, "vedit_download_failed"))

    user_data.setdefault(chat_id, {})["vedit_path"] = path
    if doc and getattr(doc, "file_id", None):
        user_data[chat_id]["last_video_file_id"] = doc.file_id
    log_activity(chat_id, "video_edit", "ویدیو برای ویرایش دریافت شد")
    await status.edit_text(
        t(chat_id, "vedit_ready_full_text"),
        reply_markup=video_editor_kb(chat_id))

# ---------------- 8. TEXT HANDLERS ----------------
@bot.on_message(filters.text & ~filters.command([
    "start", "menu", "id", "admin", "smallz",
    "tahamenu", "tahamusic", "tahasoundcloud", "tahayoutube", "tahatiktok",
    "tahaspotify", "tahalyrics", "tahaedit", "tahapinterest", "tahareddit",
    "tahaphoto", "tahashazam", "tahalibrary", "tahastats", "tahaai",
    "tahathegoat", "tahahelp"]))
async def main_text_handler(client: Client, message: Message):
    chat_id = message.chat.id
    text = message.text.strip()
    state = user_states.get(chat_id)
    uid = message.from_user.id if message.from_user else chat_id

    if state == "AI_CHAT":
        return await handle_ai_message(client, chat_id, message, text)


    # ---- user -> creator ----
    if state == "SUPPORT_MSG":
        user_states.pop(chat_id, None)
        if support_col is None:
            return await message.reply_text(t(chat_id, "support_unavailable"))
        name = (message.from_user.first_name if message.from_user else "") or "بی‌نام"
        uname = message.from_user.username if message.from_user else None
        try:
            res = support_col.insert_one({
                "chat_id": chat_id, "name": name, "username": uname,
                "text": text[:3000], "ts": time.time(), "replied": False})
        except Exception as e:
            print(f"support save error: {e}", flush=True)
            return await message.reply_text(t(chat_id, "support_send_failed"))
        await message.reply_text(
            t(chat_id, "support_sent_confirm"),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "main_menu_btn"), callback_data="menu_main")]]))
        if ADMIN_ID:
            try:
                await client.send_message(
                    ADMIN_ID,
                    f"✉️ **پیام جدید از کاربر**\n👤 {name}"
                    f"{' (@' + uname + ')' if uname else ''}\n🆔 `{chat_id}`\n\n{text[:1500]}",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("✍️ پاسخ", callback_data=f"adm_rep_{res.inserted_id}")]]))
            except Exception as e:
                print(f"admin notify error: {e}", flush=True)
        return

    # ---- admin composing flows ----
    if state and is_admin(uid):
        if await handle_bale_admin_text(client, message, state, text):
            return
        if state == "ADMIN_BROADCAST":
            user_states.pop(chat_id, None)
            status = await message.reply_text("📢 در حال ارسال...")
            sent, failed = await broadcast(client, text, status)
            return await status.edit_text(f"📢 ارسال شد.\n✅ {sent} • ❌ {failed}",
                                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 پنل", callback_data="adm_home")]]))

        if state.startswith("ADMIN_MSG:"):
            user_states.pop(chat_id, None)
            target = int(state.split(":", 1)[1])
            try:
                await client.send_message(target, t(target, "creator_message_header") + f"\n\n{text}")
                out = f"✅ پیام برای `{target}` ارسال شد."
            except Exception as e:
                out = f"❌ ارسال نشد: {str(e)[:80]}"
            return await message.reply_text(out, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 پنل", callback_data="adm_home")]]))

        if state.startswith("ADMIN_REPLY:"):
            user_states.pop(chat_id, None)
            sup_id = state.split(":", 1)[1]
            try:
                from bson import ObjectId
                doc = support_col.find_one({"_id": ObjectId(sup_id)}) if support_col is not None else None
            except Exception:
                doc = None
            if not doc:
                return await message.reply_text("❌ پیام اصلی یافت نشد.")
            try:
                await client.send_message(doc["chat_id"], t(doc["chat_id"], "creator_reply_header") + f"\n\n{text}")
                support_col.update_one({"_id": doc["_id"]},
                                       {"$set": {"replied": True, "reply": text[:2000],
                                                 "replied_at": time.time()}})
                out = f"✅ پاسخ برای `{doc['chat_id']}` ارسال شد."
            except Exception as e:
                out = f"❌ ارسال نشد: {str(e)[:80]}"
            return await message.reply_text(out, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 پیام‌ها", callback_data="adm_sup_0")]]))

        if state == "ADMIN_ADDCH":
            user_states.pop(chat_id, None)
            back_ch = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 کانال‌ها", callback_data="adm_ch")]])
            parts = text.split()
            first = parts[0]
            # Accept "@name", "@name <link>", or a bare private t.me/+HASH link.
            if first.startswith("http"):
                chat_ref, link = first, first
            else:
                chat_ref = first
                link = parts[1] if len(parts) > 1 else (
                    f"https://ble.ir/{first[1:]}" if first.startswith("@") else "")
            try:
                # Verify the bot can actually see the channel before saving it,
                # otherwise every user gets gated behind an uncheckable channel.
                chat = await client.get_chat(chat_ref)
                resolved, title = str(chat.id), (chat.title or chat_ref)
            except Exception as e:
                return await message.reply_text(
                    f"❌ ربات به این کانال دسترسی ندارد:\n`{str(e)[:120]}`\n\n"
                    "ابتدا ربات را در کانال **ادمین** کنید، سپس دوباره تلاش کنید.",
                    reply_markup=back_ch)
            chans = list(get_settings().get("force_channels") or [])
            if not any(str(c.get("chat")) == resolved for c in chans):
                chans.append({"chat": resolved, "link": link, "title": title})
                set_setting("force_channels", chans)
            return await message.reply_text(
                f"✅ کانال **{title}** اضافه شد.\n`{resolved}`",
                reply_markup=back_ch)

        if state == "ADMIN_SEARCH_USER":
            user_states.pop(chat_id, None)
            status_msg = await message.reply_text("⏳ در حال جستجو...")
            return await render_admin_search_results(status_msg, text)

        if state == "ADMIN_SMALLZ_ADD":
            user_states.pop(chat_id, None)
            back_sz = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Smallz", callback_data="adm_smallz_0")]])
            if not text.strip().lstrip("-").isdigit():
                return await message.reply_text("❌ لطفاً فقط آیدی عددی کاربر را بفرستید.", reply_markup=back_sz)
            target_sz = int(text.strip())
            if users_col is None or not users_col.find_one({"chat_id": target_sz}):
                return await message.reply_text(
                    "❌ این کاربر قبلاً ربات را استارت نکرده — از او بخواهید ابتدا /start بزند.",
                    reply_markup=back_sz)
            if smallz_col is not None:
                smallz_col.update_one({"chat_id": target_sz},
                                      {"$setOnInsert": {"referrals": 0, "added_at": time.time()}},
                                      upsert=True)
            try:
                await client.send_message(
                    target_sz,
                    "🎯 **شما به پنل Smallz دسترسی پیدا کردید.**\n\nبرای مشاهده، دستور /smallz را بفرستید.")
            except Exception:
                pass
            return await message.reply_text(f"✅ کاربر `{target_sz}` به Smallz اضافه شد.", reply_markup=back_sz)

        if state == "ADMIN_MAINT_ADD":
            user_states.pop(chat_id, None)
            back_ma = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="adm_maintallow")]])
            if not text.strip().lstrip("-").isdigit():
                return await message.reply_text("❌ لطفاً فقط آیدی عددی کاربر را بفرستید.", reply_markup=back_ma)
            target_ma = int(text.strip())
            allowlist = list(get_settings().get("maintenance_allowlist") or [])
            if target_ma not in allowlist:
                allowlist.append(target_ma)
                set_setting("maintenance_allowlist", allowlist)
            try:
                await client.send_message(
                    target_ma,
                    "🛡 **شما حتی در حالت تعمیرات هم می‌توانید از ربات استفاده کنید.**")
            except Exception:
                pass
            return await message.reply_text(f"✅ کاربر `{target_ma}` به لیست اضافه شد.", reply_markup=back_ma)

        if state == "ADMIN_FJ_ADD":
            user_states.pop(chat_id, None)
            back_fj = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="adm_fjallow")]])
            if not text.strip().lstrip("-").isdigit():
                return await message.reply_text("❌ لطفاً فقط آیدی عددی کاربر را بفرستید.", reply_markup=back_fj)
            target_fj = int(text.strip())
            allowlist = list(get_settings().get("force_join_allowlist") or [])
            if target_fj not in allowlist:
                allowlist.append(target_fj)
                set_setting("force_join_allowlist", allowlist)
            MEMBER_OK.pop(target_fj, None)
            # Deliberately no DM to this user -- the whole point is they never
            # find out a force-join gate exists at all.
            return await message.reply_text(f"✅ کاربر `{target_fj}` معاف از عضویت اجباری شد.", reply_markup=back_fj)

    
    if state and state.startswith("MSEARCH_"):
        user_states.pop(chat_id, None)
        tag, plat = state.split(":", 1)
        status_msg = await message.reply_text(t(chat_id, "searching_text"))
        if plat == "sc":
            await render_sc_search(status_msg, text, chat_id)
        elif plat == "spotify":
            await render_spotify_search(status_msg, text, chat_id)
        elif plat == "ytm":
            await render_ytm_search(status_msg, text, chat_id)
        else:
            await render_video_search(status_msg, text, chat_id)
        return

    if state and state.startswith("IMG_SEARCH:"):
        user_states.pop(chat_id, None)
        plat = state.split(":", 1)[1]
        status_msg = await message.reply_text(t(chat_id, "searching_images"))
        if plat == "pinterest":
            await render_pinterest_search(status_msg, text, chat_id)
        else:
            await render_image_search(status_msg, text, chat_id, 1, provider="google" if plat == "google" else "pexels")
        return

    if state and (state == "MTOOL_TRIM" or state.startswith("MTOOL_TRIM:")):
        user_states.pop(chat_id, None)
        token = state.split(":", 1)[1] if ":" in state else None
        src = user_data.get(chat_id, {}).get("music_tools_path")
        needs_cleanup = False
        if token:
            doc = find_track(token)
            if doc and doc.get("file_id"):
                status_dl = await message.reply_text("⏳ در حال دریافت فایل صوتی...")
                src = await client.download_media(doc["file_id"], file_name=f"music_downloads/trim_{token}_src.mp3")
                await status_dl.delete()
                needs_cleanup = True
        if not src or not os.path.exists(src):
            return await message.reply_text("❌ فایل صوتی برای برش یافت نشد.")
        m = re.search(r"(\d{1,2}:\d{2}|\d+)\s*[-–—]\s*(\d{1,2}:\d{2}|\d+)", text)
        if not m:
            return await message.reply_text("❌ فرمت زمان اشتباه است. لطفاً به این شکل بفرستید: `0:15 - 0:45` یا `15-45`")
        t_start, t_end = m.group(1), m.group(2)
        status = await message.reply_text("⏳ در حال برش فایل صوتی...")
        try:
            from modules.music_tools import trim_audio_file, probe_audio_info
            out_file = await asyncio.to_thread(trim_audio_file, src, t_start, t_end)
            info = await asyncio.to_thread(probe_audio_info, out_file)
            await client.send_audio(
                chat_id, audio=out_file,
                title=f"{info.get('title','Track')} (Trimmed)",
                performer=info.get('artist', 'Taha Bot Audio'), tag_source=src,
                caption=f"✂️ **برش موزیک ({t_start} تا {t_end})**",
                reply_markup=track_actions_kb(token or abs(hash(out_file)), chat_id)
            )
            await status.delete()
        except Exception as e:
            await status.edit_text(f"❌ خطا: {e}")
        finally:
            if needs_cleanup and src and os.path.exists(src):
                try: os.remove(src)
                except OSError: pass
        return

    if state and (state == "MTOOL_TAGS" or state.startswith("MTOOL_TAGS:")):
        user_states.pop(chat_id, None)
        token = state.split(":", 1)[1] if ":" in state else None
        src = user_data.get(chat_id, {}).get("music_tools_path")
        needs_cleanup = False
        if token:
            doc = find_track(token)
            if doc and doc.get("file_id"):
                status_dl = await message.reply_text("⏳ در حال دریافت فایل صوتی...")
                src = await client.download_media(doc["file_id"], file_name=f"music_downloads/tag_{token}_src.mp3")
                await status_dl.delete()
                needs_cleanup = True
        if not src or not os.path.exists(src):
            return await message.reply_text("❌ فایل صوتی یافت نشد.")
        sep = "|" if "|" in text else "-"
        parts = text.split(sep, 1)
        title = parts[0].strip()
        artist = parts[1].strip() if len(parts) > 1 else ""
        status = await message.reply_text("⏳ در حال به‌روزرسانی تگ‌ها...")
        try:
            from modules.music_tools import update_audio_metadata
            await asyncio.to_thread(update_audio_metadata, src, title=title, artist=artist or None)
            await client.send_audio(
                chat_id, audio=src,
                title=title, performer=artist or "Taha Bot Artist", tag_source=src,
                caption="🏷 **مشخصات آهنگ به‌روزرسانی شد.**",
                reply_markup=track_actions_kb(token or abs(hash(src)), chat_id)
            )
            await status.delete()
        except Exception as e:
            await status.edit_text(f"❌ خطا: {e}")
        finally:
            if needs_cleanup and src and os.path.exists(src):
                try: os.remove(src)
                except OSError: pass
        return

    if state == "ITOOL_WATERMARK":
        user_states.pop(chat_id, None)
        src = user_data.get(chat_id, {}).get("image_tools_path")
        if not src or not os.path.exists(src):
            return await message.reply_text("❌ تصویر برای درج واترمارک یافت نشد.")
        status = await message.reply_text("⏳ در حال درج واترمارک...")
        try:
            out_file = await asyncio.to_thread(itools.add_text_watermark, src, text[:50])
            await client.send_photo(
                chat_id, photo=out_file,
                caption=f"🏷 **واترمارک درج شد:** `{text[:50]}`",
                reply_markup=get_image_tools_keyboard(chat_id, get_lang)
            )
            await status.delete()
        except Exception as e:
            await status.edit_text(f"❌ خطا: {e}")
        return

    if state == "MUSIC_SEARCH_SC":
        user_states.pop(chat_id, None)
        status_msg = await message.reply_text(t(chat_id, "searching_text"))
        if "soundcloud.com" in text or "http" in text:
            clean_url = text.split("?")[0]
            await process_sc_download(client, chat_id, status_msg, clean_url, cache_key=clean_url)
        else:
            await render_sc_search(status_msg, text, chat_id)
        return

    if state == "MUSIC_SEARCH_OFFICIAL" or state == "MUSIC_SEARCH_SPOTIFY":
        user_states.pop(chat_id, None)
        status_msg = await message.reply_text(t(chat_id, "searching_spotify"))
        await render_spotify_search(status_msg, text, chat_id)
        return

    if state == "MUSIC_SEARCH_LYRIC":
        user_states.pop(chat_id, None)
        status_msg = await message.reply_text(t(chat_id, "searching_by_lyrics"))
        await render_lyric_search(status_msg, text, chat_id)
        return

    if state == "SUB_ADD":
        user_states.pop(chat_id, None)
        status_msg = await message.reply_text(t(chat_id, "checking_generic"))
        if subs_col is None:
            return await status_msg.edit_text(t(chat_id, "subs_unavailable"))
        parsed = await asyncio.to_thread(parse_subscription_target, text)
        if not parsed:
            return await status_msg.edit_text(t(chat_id, "sub_invalid_target"))
        platform, target, label = parsed
        try:
            if subs_col.count_documents({"chat_id": int(chat_id)}) >= MAX_SUBS_PER_USER:
                return await status_msg.edit_text(t(chat_id, "sub_max_reached", max=MAX_SUBS_PER_USER))
            subs_col.update_one(
                {"chat_id": int(chat_id), "platform": platform, "target": target},
                {"$set": {"label": label}, "$setOnInsert": {"last_id": None}},
                upsert=True)
        except Exception as e:
            print(f"sub add error: {e}", flush=True)
            return await status_msg.edit_text(t(chat_id, "sub_add_error"))
        await status_msg.edit_text(
            t(chat_id, "sub_added_confirm", label=label),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_my_subs"), callback_data="subs_menu")]]))
        return

    if state == "PLAYLIST_NAME_NEW":
        user_states.pop(chat_id, None)
        name = text.strip()
        if not name:
            return await message.reply_text(t(chat_id, "playlist_name_invalid"))
        if playlists_col is not None and playlists_col.count_documents({"chat_id": int(chat_id)}) >= MAX_PLAYLISTS_PER_USER:
            return await message.reply_text(t(chat_id, "playlist_limit_reached", max=MAX_PLAYLISTS_PER_USER))
        doc = create_playlist(chat_id, name)
        if not doc:
            return await message.reply_text(t(chat_id, "playlists_unavailable"))
        await message.reply_text(
            t(chat_id, "playlist_created_confirm", name=doc["name"]),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "btn_back"), callback_data="menu_playlists")]]))
        return

    if state and state.startswith("PLAYLIST_RENAME:"):
        user_states.pop(chat_id, None)
        pid = state.split(":", 1)[1]
        name = text.strip()
        if not name:
            return await message.reply_text(t(chat_id, "playlist_name_invalid"))
        rename_playlist(pid, name)
        await message.reply_text(t(chat_id, "playlist_renamed_confirm", name=name[:60]))
        return

    if state and state.startswith("PLAYLIST_NEW_FOR_ADD:"):
        user_states.pop(chat_id, None)
        tok = state.split(":", 1)[1]
        name = text.strip()
        if not name:
            return await message.reply_text(t(chat_id, "playlist_name_invalid"))
        track_doc = find_track(tok)
        if not track_doc:
            return await message.reply_text(t(chat_id, "addpl_track_not_found"))
        if playlists_col is not None and playlists_col.count_documents({"chat_id": int(chat_id)}) >= MAX_PLAYLISTS_PER_USER:
            return await message.reply_text(t(chat_id, "playlist_limit_reached", max=MAX_PLAYLISTS_PER_USER))
        new_pl = create_playlist(chat_id, name)
        if not new_pl:
            return await message.reply_text(t(chat_id, "playlists_unavailable"))
        key = track_doc.get("vid_id") or tok
        add_track_to_playlist(new_pl["_id"], key, track_doc.get("title", "?"), track_doc.get("artist", ""), track_doc.get("duration", 0))
        await message.reply_text(t(chat_id, "addpl_added_confirm", name=new_pl["name"]))
        return

    if state == "YT_KEYWORD":
        user_states.pop(chat_id, None)
        status_msg = await message.reply_text(t(chat_id, "searching_youtube_db"))
        await render_video_search(status_msg, text, chat_id)
        return

    if state == "PT_SEARCH":
        user_states.pop(chat_id, None)
        status_msg = await message.reply_text(t(chat_id, "searching_pinterest"))
        await render_pinterest_search(status_msg, text, chat_id)
        return

    if state == "REDDIT_SEARCH":
        user_states.pop(chat_id, None)
        status_msg = await message.reply_text(t(chat_id, "searching_reddit"))
        await render_reddit_search(status_msg, text, chat_id)
        return

    if state == "IMAGE_SEARCH":
        user_states.pop(chat_id, None)
        status_msg = await message.reply_text(t(chat_id, "searching_images"))
        await render_image_search(status_msg, text, chat_id, 1, provider="pexels")
        return

    if state == "YT_CHANNEL":
        user_states.pop(chat_id, None)
        status_msg = await message.reply_text(t(chat_id, "checking_youtube_channels"))
        await render_channel_search(status_msg, text, chat_id)
        return

    if state == "VEDIT_TRIM":
        match = re.search(r"(\d+(?::\d+)?(?::\d+)?)\s*-\s*(\d+(?::\d+)?(?::\d+)?)", text)
        if not match:
            await message.reply_text(t(chat_id, "vedit_trim_format_error"))
            return
        start_raw, end_raw = match.group(1), match.group(2)
        if ":" not in start_raw:
            s_val = int(start_raw)
            start_raw = f"{s_val//60:02d}:{s_val%60:02d}"
        if ":" not in end_raw:
            e_val = int(end_raw)
            end_raw = f"{e_val//60:02d}:{e_val%60:02d}"
        user_data.setdefault(chat_id, {})["vedit_trim_start"] = start_raw
        user_data[chat_id]["vedit_trim_end"] = end_raw
        user_states.pop(chat_id, None)
        status_msg = await message.reply_text(t(chat_id, "vedit_trimming"))
        await process_video_edit(client, chat_id, status_msg, "trim")
        return

    if state == "VEDIT_WATERMARK":
        user_data.setdefault(chat_id, {})["vedit_watermark_text"] = text[:60]
        user_states.pop(chat_id, None)
        status_msg = await message.reply_text(t(chat_id, "vedit_adding_watermark"))
        await process_video_edit(client, chat_id, status_msg, "watermark")
        return

    if state == "AWAITING_TRIM":
        match = re.search(r"(\d+(?::\d+)?)\s*-\s*(\d+(?::\d+)?)", text)
        if match:
            start_raw, end_raw = match.group(1), match.group(2)
            if ":" not in start_raw:
                s_val = int(start_raw)
                start_raw = f"{s_val//60:02d}:{s_val%60:02d}"
            if ":" not in end_raw:
                e_val = int(end_raw)
                end_raw = f"{e_val//60:02d}:{e_val%60:02d}"
            user_data.setdefault(chat_id, {})["trim_start"], user_data[chat_id]["trim_end"] = start_raw, end_raw
            user_states.pop(chat_id, None)
            await message.reply_text(t(chat_id, "yt_trim_set_confirm", start=start_raw, end=end_raw), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(chat_id, "yt_btn_back_to_settings"), callback_data="q_1080p")]]))
        else: await message.reply_text(t(chat_id, "yt_trim_format_error"))
        return

    url_match = re.search(r"(https?://[^\s]+)", text)
    if not url_match and re.search(r"(?:^|\s)(?:www\.)?(?:instagram\.com|tiktok\.com|vm\.tiktok\.com|youtu\.be|youtube\.com|"
                                   r"open\.spotify\.com|soundcloud\.com|pinterest\.com|pin\.it|reddit\.com|x\.com|twitter\.com)/\S+", text):
        text = re.sub(r"(?:^|(?<=\s))((?:www\.)?[\w.-]+\.(?:com|be|it)/\S+)", r"https://\1", text, count=1)
        url_match = re.search(r"(https?://[^\s]+)", text)
    if not url_match:
        if state == "TIKTOK_TARGET":
            user_states.pop(chat_id, None)
            status_msg = await message.reply_text(t(chat_id, "tiktok_fetching"))
            await process_tiktok_render(client, chat_id, status_msg, text)
        elif state == "IG_LINK":
            await message.reply_text(t(chat_id, "ig_invalid_link"))
        elif not state and message.chat.type == ChatType.PRIVATE:
            # RegaSpotify instant search card ONLY for private chat!
            await message.reply_text(
                f"🎵 **{text}**\n\nبرای جستجو، یکی از بخش‌های زیر را انتخاب کنید:",
                reply_markup=get_rega_search_selector(text, chat_id, get_lang)
            )
        return

    url = url_match.group(0)
    user_states.pop(chat_id, None)
    if re.search(r"(spotify\.link|deezer\.page\.link|on\.soundcloud\.com|vm\.tiktok\.com|vt\.tiktok\.com|/t/|pin\.it|instagram\.com/share/)", url):
        try:
            url = await asyncio.to_thread(
                lambda: requests.get(url, allow_redirects=True, timeout=15,
                                     headers={"User-Agent": PINTEREST_UA}).url) or url
        except Exception:
            pass

    if TWITTER_URL_RE.search(url):
        status_msg = await message.reply_text(t(chat_id, "tw_fetching"))
        return await process_twitter_link(client, chat_id, status_msg, url)

    if "deezer.com" in url:
        m_tr = re.search(r"deezer\.com/(?:[a-zA-Z-]+/)?track/(\d+)", url)
        if m_tr:
            status_msg = await message.reply_text("⏳ در حال دریافت قطعه...")
            await process_deezer_track(client, chat_id, status_msg, m_tr.group(1))
            return
        m_al = re.search(r"deezer\.com/(?:[a-zA-Z-]+/)?album/(\d+)", url)
        if m_al:
            status_msg = await message.reply_text("⏳ در حال دریافت مشخصات آلبوم...")
            await process_deezer_album(client, chat_id, status_msg, m_al.group(1))
            return
        m_ar = re.search(r"deezer\.com/(?:[a-zA-Z-]+/)?artist/(\d+)", url)
        if m_ar:
            status_msg = await message.reply_text("⏳ در حال دریافت آثار هنرمند...")
            await process_deezer_artist(client, chat_id, status_msg, m_ar.group(1))
            return

    elif "music.youtube.com" in url:
        m = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", url)
        if m:
            status = await message.reply_text(t(chat_id, "track_downloading", track="YouTube Music"))
            await deliver_track(client, chat_id, make_meta("yt", yt_id=m.group(1)), status=status,
                                activity="music_ytm")
            return

    elif "youtube.com" in url or "youtu.be" in url:
        m = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", url)
        if m:
            status = await message.reply_text(t(chat_id, "yt_extracting_info"))
            await fetch_and_verify_video(status, chat_id, m.group(1))
        return

    elif "instagram.com" in url:
        status_msg = await message.reply_text(t(chat_id, "ig_identifying_link"))
        await process_instagram_render(client, chat_id, status_msg, url)
        return

    elif "tiktok.com" in url:
        status_msg = await message.reply_text(t(chat_id, "tiktok_identifying_link"))
        await process_tiktok_render(client, chat_id, status_msg, url)
        return

    elif "soundcloud.com" in url:
        status_msg = await message.reply_text(t(chat_id, "sc_fetching_link"))
        clean_url = url.split("?")[0]
        await process_sc_download(client, chat_id, status_msg, clean_url, cache_key=clean_url)
        return

    elif "spotify.com" in url:
        status_msg = await message.reply_text(t(chat_id, "spotify_identifying_link"))
        await process_spotify_link(client, chat_id, status_msg, url)
        return

    elif "pinterest.com" in url or "pin.it" in url:
        status_msg = await message.reply_text(t(chat_id, "pt_identifying_link"))
        await process_pinterest_link(client, chat_id, status_msg, url)
        return

    elif "reddit.com" in url or "redd.it" in url:
        status_msg = await message.reply_text(t(chat_id, "rd_identifying_link"))
        await process_reddit_link(client, chat_id, status_msg, url)
        return

    else:
        status_msg = await message.reply_text("🔎 در حال شناسایی لینک...")
        await process_generic_link(client, chat_id, status_msg, url)
        return


# ---------------- AI CHATBOT (Gemini primary, Groq fallback) ----------------
import ai_chat

AI_HISTORY = {}          # chat_id -> list of {role, content|tool_call}, capped
AI_PENDING_SONGS = {}     # chat_id -> list of {title, artist} awaiting confirm
AI_MAX_TURNS = 16         # trim history so token cost/context stays bounded

AI_SYSTEM_PROMPT = (
    "You are an AI assistant embedded inside a Telegram media-downloader bot "
    "(Taha Bot). You can search for and download music, search for and download "
    "YouTube videos, fetch song lyrics, and answer questions about the user's "
    "own download history on this bot. Always reply in the same language the "
    "user writes in -- if they write in Persian/Farsi, reply in Persian, "
    "NEVER Arabic (Persian and Arabic share a script but are different "
    "languages -- do not confuse them). Keep replies short and conversational "
    "-- one or two sentences of text; the bot itself renders the actual "
    "results/buttons/lyrics separately.\n\n"
    "Tools (pick exactly one per request, based on what the user actually wants):\n"
    "- get_lyrics: call this when the user asks for the LYRICS/TEXT of a "
    "SPECIFIC song they named. Never guess or write lyrics yourself -- always "
    "call this tool. If it's not found, tell the user honestly instead of "
    "inventing lyrics.\n"
    "- propose_songs: call this when the user wants to DISCOVER music -- a "
    "mood/activity playlist (\"gym music\", \"sad songs\"), or general "
    "recommendations. NOT for when they name one specific song and want its "
    "lyrics (use get_lyrics for that) or want it downloaded (use get_lyrics "
    "is wrong there too -- if they name one exact song to download, still use "
    "propose_songs with just that one song). Suggest real, well-known songs "
    "with correct title+artist, 5-10 unless a specific number is requested.\n"
    "- search_youtube: call this whenever the user wants a YouTube video "
    "found or downloaded.\n"
    "- get_user_activity: call this whenever the user asks about their own "
    "history, stats, or what they've downloaded before.\n"
    "Only call a tool when the request actually needs one -- for normal "
    "conversation, just reply with text."
)

AI_TOOLS = [
    {
        "name": "propose_songs",
        "description": "Propose a list of real songs (title + artist) for the user to optionally download.",
        "parameters": {
            "type": "object",
            "properties": {
                "songs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"title": {"type": "string"}, "artist": {"type": "string"}},
                        "required": ["title", "artist"],
                    },
                }
            },
            "required": ["songs"],
        },
    },
    {
        "name": "search_youtube",
        "description": "Search YouTube for a video the user wants to find or download.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query for YouTube"}},
            "required": ["query"],
        },
    },
    {
        "name": "get_user_activity",
        "description": "Look up the user's own download history/activity on this bot.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many recent items to fetch, default 10"}
            },
        },
    },
    {
        "name": "get_lyrics",
        "description": "Fetch the real lyrics for one specific named song.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Song title"},
                "artist": {"type": "string", "description": "Artist name"},
            },
            "required": ["title", "artist"],
        },
    },
]

AI_PENDING_LYRICS = {}   # chat_id -> lyrics text (or None) awaiting delivery
STRINGS.update({
    "ai_service_unavailable": {"fa": "❌ سرویس هوش مصنوعی موقتاً در دسترس نیست، بعداً دوباره امتحان کنید.", "en": "❌ The AI service is temporarily unavailable, please try again later.", "ru": "❌ Сервис ИИ временно недоступен, попробуйте позже.", "es": "❌ El servicio de IA no está disponible temporalmente, inténtalo de nuevo más tarde."},
    "ai_lyrics_found": {"fa": "🎤 پیدا شد، بفرمایید:", "en": "🎤 Found it, here you go:", "ru": "🎤 Найдено, вот текст:", "es": "🎤 Encontrado, aquí tienes:"},
    "ai_lyrics_not_found": {"fa": "❌ متاسفانه متن این آهنگ پیدا نشد. اسم دقیق‌تری امتحان کنید؟", "en": "❌ Sorry, this song's lyrics weren't found. Want to try a more exact name?", "ru": "❌ К сожалению, текст этой песни не найден. Попробуете более точное название?", "es": "❌ Lo sentimos, no se encontró la letra de esta canción. ¿Quieres probar con un nombre más exacto?"},
    "ai_songs_suggested": {"fa": "🎵 {n} پیشنهاد پیدا کردم:", "en": "🎵 I found {n} suggestions:", "ru": "🎵 Я нашёл {n} предложений:", "es": "🎵 Encontré {n} sugerencias:"},
    "ai_searching_now": {"fa": "🔍 الان جستجو میکنم...", "en": "🔍 Searching now...", "ru": "🔍 Ищу сейчас...", "es": "🔍 Buscando ahora..."},
    "ai_btn_yes_download": {"fa": "✅ بله، دانلود کن", "en": "✅ Yes, download it", "ru": "✅ Да, скачать", "es": "✅ Sí, descárgalo"},
    "ai_btn_no": {"fa": "❌ نه", "en": "❌ No", "ru": "❌ Нет", "es": "❌ No"},
    "ai_suggested_songs_header": {"fa": "🎵 **آهنگ‌های پیشنهادی:**\n\n", "en": "🎵 **Suggested Songs:**\n\n", "ru": "🎵 **Предложенные песни:**\n\n", "es": "🎵 **Canciones sugeridas:**\n\n"},
    "ai_searching_youtube": {"fa": "⏳ در حال جستجو در یوتیوب...", "en": "⏳ Searching on YouTube...", "ru": "⏳ Поиск на YouTube...", "es": "⏳ Buscando en YouTube..."},
    "ai_exit_chat_prompt": {"fa": "برای خروج از چت هوش مصنوعی:", "en": "To exit the AI chat:", "ru": "Чтобы выйти из чата с ИИ:", "es": "Para salir del chat de IA:"},
    "ai_lyrics_header": {"fa": "🎤 **{title} — {artist}**", "en": "🎤 **{title} — {artist}**", "ru": "🎤 **{title} — {artist}**", "es": "🎤 **{title} — {artist}**"},
    "ai_lyrics_truncated_note": {"fa": "\n\n[متن خلاصه شد...]", "en": "\n\n[Text truncated...]", "ru": "\n\n[Текст сокращён...]", "es": "\n\n[Texto truncado...]"},
})


def ai_exit_row(chat_id):
    return [InlineKeyboardButton(t(chat_id, "btn_ai_exit"), callback_data="ai_exit")]


def _ai_run_tool(chat_id: int, name: str, args: dict) -> str:
    """Executes a tool call and returns a short text result to feed back to
    the model. Any UI (buttons, result lists, lyrics) the tool itself already
    showed to the user gets handled by the caller, not here."""
    if name == "propose_songs":
        songs = args.get("songs") or []
        AI_PENDING_SONGS[chat_id] = songs
        return f"Proposed {len(songs)} songs to the user; awaiting their confirmation to download."
    if name == "search_youtube":
        return "Search results were shown to the user; they can pick one to download."
    if name == "get_user_activity":
        limit = int(args.get("limit") or 10)
        if history_col is None:
            return "No activity data available (database not connected)."
        docs = list(history_col.find({"chat_id": int(chat_id)}).sort("ts", -1).limit(limit))
        if not docs:
            return "The user has no download history yet."
        lines = []
        for d in docs:
            when = datetime.fromtimestamp(d.get("ts", 0), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            lines.append(f"- {d.get('title','?')} by {d.get('artist','?')} ({when})")
        return f"Recent download history ({len(docs)} items):\n" + "\n".join(lines)
    if name == "get_lyrics":
        title, artist = args.get("title", ""), args.get("artist", "")
        item = lrclib_lookup(title, artist)
        if not item:
            AI_PENDING_LYRICS[chat_id] = None
            return f"No lyrics found for '{title}' by '{artist}'. Tell the user honestly it wasn't found -- never invent lyrics."
        lyrics = item.get("plainLyrics") or re.sub(r"\[\d+:\d+\.\d+\]", "", item.get("syncedLyrics") or "").strip()
        if not lyrics:
            AI_PENDING_LYRICS[chat_id] = None
            return f"Lyrics entry found for '{title}' but the text was empty."
        AI_PENDING_LYRICS[chat_id] = {
            "title": item.get("trackName") or title, "artist": item.get("artistName") or artist,
            "text": lyrics,
        }
        return f"Lyrics for '{item.get('trackName', title)}' by '{item.get('artistName', artist)}' were found and shown to the user directly."
    return "Unknown tool."


async def handle_ai_message(client: Client, chat_id: int, msg: Message, user_text: str):
    history = AI_HISTORY.setdefault(chat_id, [])
    history.append({"role": "user", "content": user_text})
    exit_kb = InlineKeyboardMarkup([ai_exit_row(chat_id)])

    try:
        result, provider = await asyncio.to_thread(ai_chat.ask_ai, AI_SYSTEM_PROMPT, history, AI_TOOLS)
    except Exception as e:
        print(f"[ai_chat] both providers failed: {e}", flush=True)
        history.pop()
        return await msg.reply_text(t(chat_id, "ai_service_unavailable"), reply_markup=exit_kb)

    tool_name = None
    if result["type"] == "tool_call":
        tool_name = result["name"]
        history.append({"role": "assistant", "tool_call": result})
        tool_result = await asyncio.to_thread(_ai_run_tool, chat_id, result["name"], result.get("args") or {})
        history.append({
            "role": "tool", "name": result["name"],
            "content": tool_result, "tool_call_id": result.get("id"),
        })
        # Most tools have a deterministic, known outcome by the time we get
        # here -- a second free-form LLM call to phrase the reply added
        # 1-2s of pure latency per message for no real benefit, and for
        # get_lyrics it sometimes even contradicted the tool's real result.
        # Only get_user_activity actually needs the model to turn raw DB
        # rows into a natural sentence.
        if tool_name == "get_lyrics":
            found = AI_PENDING_LYRICS.get(chat_id)
            reply_text = t(chat_id, "ai_lyrics_found") if found else t(chat_id, "ai_lyrics_not_found")
        elif tool_name == "propose_songs":
            n = len(AI_PENDING_SONGS.get(chat_id) or [])
            reply_text = t(chat_id, "ai_songs_suggested", n=n)
        elif tool_name == "search_youtube":
            reply_text = t(chat_id, "ai_searching_now")
        else:
            try:
                result2, _ = await asyncio.to_thread(ai_chat.ask_ai, AI_SYSTEM_PROMPT, history, AI_TOOLS)
                reply_text = result2.get("content") or "..."
            except Exception:
                reply_text = "..."
        history.append({"role": "assistant", "content": reply_text})
    else:
        reply_text = result.get("content") or "..."
        history.append({"role": "assistant", "content": reply_text})

    del history[:-AI_MAX_TURNS]

    try:
        await msg.reply_text(reply_text[:4000], reply_markup=exit_kb)
    except Exception:
        # Model output can contain unbalanced markdown that Telegram rejects.
        await msg.reply_text(reply_text[:4000], reply_markup=exit_kb, parse_mode=ParseMode.DISABLED)

    if tool_name == "propose_songs":
        songs = AI_PENDING_SONGS.get(chat_id) or []
        lines = [f"{i+1}. {s.get('title','?')} — {s.get('artist','?')}" for i, s in enumerate(songs)]
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t(chat_id, "ai_btn_yes_download"), callback_data="ai_confirm_songs"),
             InlineKeyboardButton(t(chat_id, "ai_btn_no"), callback_data="ai_cancel_songs")],
            ai_exit_row(chat_id),
        ])
        await client.send_message(chat_id, t(chat_id, "ai_suggested_songs_header") + "\n".join(lines), reply_markup=kb)
    elif tool_name == "search_youtube":
        query = None
        for m in reversed(history):
            if m.get("role") == "assistant" and m.get("tool_call", {}).get("name") == "search_youtube":
                query = m["tool_call"]["args"].get("query")
                break
        if query:
            status = await client.send_message(chat_id, t(chat_id, "ai_searching_youtube"))
            await render_video_search(status, query, chat_id)
            await client.send_message(chat_id, t(chat_id, "ai_exit_chat_prompt"), reply_markup=InlineKeyboardMarkup([ai_exit_row(chat_id)]))
    elif tool_name == "get_lyrics":
        lyr = AI_PENDING_LYRICS.pop(chat_id, None)
        if lyr:
            text = t(chat_id, "ai_lyrics_header", title=lyr['title'], artist=lyr['artist']) + f"\n\n{lyr['text']}"
            if len(text) > 4000:
                text = text[:4000] + t(chat_id, "ai_lyrics_truncated_note")
            await client.send_message(chat_id, text, reply_markup=exit_kb)

# ---------------- 9. BALE EXTRAS: access approval, feature switches, limits ----------------
# Everything the Bale build adds on top of the Telegram bot's admin panel.
# All checks read in-memory state (refreshed in the background), so none of
# this adds a database round trip to normal traffic.

ADMIN_CONTACT = os.environ.get("ADMIN_CONTACT", "Tahathegoat").lstrip("@")
DEFAULT_SIGNATURE = os.environ.get("CAPTION_SIGNATURE", "🤖 @tahasbot")


# ---- access approval ----
_APPROVED = set()
_APPROVED_TS = [0.0]
_ACCESS_NOTIFIED = set()


def approval_required() -> bool:
    default = os.environ.get("APPROVAL_REQUIRED_DEFAULT", "true").lower() != "false"
    return bool(get_settings().get("approval_required", default))


def _refresh_approved(force: bool = False):
    if users_col is None:
        return
    if not force and time.time() - _APPROVED_TS[0] < 60:
        return
    try:
        ids = {int(d["chat_id"]) for d in users_col.find({"approved": True}, {"chat_id": 1})}
        _APPROVED.clear()
        _APPROVED.update(ids)
        _APPROVED_TS[0] = time.time()
    except Exception as e:
        print(f"approved refresh error: {e}", flush=True)


def is_approved(uid) -> bool:
    if not uid or is_admin(uid) or not approval_required():
        return True
    _refresh_approved()
    return int(uid) in _APPROVED


def set_approved(uid: int, approved: bool):
    uid = int(uid)
    if users_col is not None:
        users_col.update_one({"chat_id": uid},
                             {"$set": {"approved": approved, "approved_at" if approved else "revoked_at": time.time()},
                              "$setOnInsert": {"joined_at": time.time()}}, upsert=True)
    (_APPROVED.add if approved else _APPROVED.discard)(uid)


def access_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(f"📩 پیام به @{ADMIN_CONTACT}",
                                                       url=f"https://ble.ir/{ADMIN_CONTACT}")]])


def access_text(user, uid) -> str:
    name = (getattr(user, "first_name", "") or "").strip() or "کاربر"
    return (f"👋 سلام {name} عزیز، به **بات طاها** خوش آمدید!\n\n"
            f"🆔 آیدی حساب شما:\n`{uid}`\n\n"
            "🔐 استفاده از ربات نیاز به تأیید مدیر دارد.\n"
            f"لطفاً به @{ADMIN_CONTACT} پیام بدهید و آیدی بالا را برایش بفرستید تا دسترسی شما فعال شود.\n\n"
            "_بعد از تأیید، از طریق همین ربات به شما خبر داده می‌شود._")


async def notify_access_request(client: Client, user, uid: int):
    """One admin ping per user (restart-safe via the users doc)."""
    if not ADMIN_ID or uid in _ACCESS_NOTIFIED:
        return
    _ACCESS_NOTIFIED.add(uid)
    try:
        if users_col is not None:
            res = users_col.update_one({"chat_id": uid, "access_requested_at": {"$exists": False}},
                                       {"$set": {"access_requested_at": time.time()}})
            if res.matched_count == 0:
                return  # already requested before
        name = (getattr(user, "first_name", "") or "بی‌نام")
        uname = f"@{user.username}" if getattr(user, "username", None) else "—"
        await client.send_message(
            ADMIN_ID,
            f"🆕 **درخواست دسترسی**\n\n👤 {name}\n🔗 {uname}\n🆔 `{uid}`",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ تأیید", callback_data=f"adm_appr_{uid}"),
                 InlineKeyboardButton("❌ رد", callback_data=f"adm_rej_{uid}")],
                [InlineKeyboardButton("👤 جزئیات کاربر", callback_data=f"adm_u_{uid}")]]))
    except Exception as e:
        print(f"access notify error: {e}", flush=True)


async def deny_unapproved_message(client: Client, message: Message, uid: int):
    await message.reply_text(access_text(message.from_user, uid), reply_markup=access_kb())
    await notify_access_request(client, message.from_user, uid)


# ---- feature switches (enforced in pyrogram/client.py) ----
PROTECTED_FEATURES = ("adm", "menu_main", "setlang", "noop", "check_join", "help", "_", "contact_admin",
                      "cmd:/start", "cmd:/admin", "cmd:/id", "cmd:/menu", "cmd:/help", "cmd:/tahahelp")
FEATURE_CATS = {
    "link": ("🔗 لینک‌ها (دانلود از هر سایت)", "link:"),
    "upload": ("📤 فایل‌های ارسالی کاربر", "upload:"),
    "cmd": ("⌨️ دستورات", "cmd:"),
    "inline": ("🔎 جستجوی سریع (دکمه‌های جستجو)", "inline:"),
    "btn": ("🔘 همه دکمه‌ها", ""),
}
CURATED_FEATURES = {
    "link:youtube": "▶️ لینک یوتیوب", "link:ytmusic": "🎵 لینک یوتیوب موزیک",
    "link:instagram": "📸 لینک اینستاگرام", "link:tiktok": "📱 لینک تیک‌تاک",
    "link:spotify": "🟢 لینک اسپاتیفای", "link:soundcloud": "☁️ لینک ساندکلاد",
    "link:pinterest": "📌 لینک پینترست", "link:reddit": "🔴 لینک ردیت", "link:twitter": "🐦 لینک توییتر/X",
    "link:deezer": "💿 لینک دیزر", "link:other": "🌐 سایر لینک‌ها",
    "upload:photo": "🖼 ارسال عکس", "upload:video": "🎬 ارسال ویدیو", "upload:audio": "🎵 ارسال آهنگ",
    "upload:voice": "🎙 ارسال ویس", "upload:document": "📄 ارسال فایل",
    "inline:sp": "🔎 جستجوی اسپاتیفای", "inline:sc": "🔎 جستجوی ساندکلاد", "inline:ytm": "🔎 جستجوی یوتیوب موزیک",
    "inline:alb": "🔎 جستجوی آلبوم", "inline:art": "🔎 جستجوی خواننده", "inline:pl": "🔎 جستجوی پلی‌لیست",
    "inline:top": "🔥 پرطرفدارهای هفته",
    "q_1080p": "📺 کیفیت 1080p", "q_720p": "📺 کیفیت 720p", "q_480p": "📺 کیفیت 480p", "q_360p": "📺 کیفیت 360p",
}


class FeatureGate:
    def __init__(self):
        self.registry = dict(CURATED_FEATURES)
        self.disabled = set()
        self.dirty = False

    def load(self):
        st = get_settings()
        reg = st.get("feat_registry") or {}
        if isinstance(reg, dict):
            for k, v in reg.items():
                self.registry.setdefault(k, v)
        self.disabled = set(st.get("feat_disabled") or [])

    @staticmethod
    def protected(key: str) -> bool:
        return any(key == p or key.startswith(p) for p in PROTECTED_FEATURES)

    def is_exempt(self, uid) -> bool:
        return is_admin(uid)

    def is_disabled(self, key: str) -> bool:
        return key in self.disabled and not self.protected(key)

    def seen(self, key: str, label: str):
        if key in self.registry or self.protected(key) or len(self.registry) > 3000:
            return
        self.registry[key] = re.sub(r"\s+", " ", str(label or key))[:40]
        self.dirty = True

    def toggle(self, key: str) -> bool:
        if key in self.disabled:
            self.disabled.discard(key)
        else:
            self.disabled.add(key)
        set_setting("feat_disabled", sorted(self.disabled))
        return key not in self.disabled

    def flush(self):
        if self.dirty:
            self.dirty = False
            set_setting("feat_registry", self.registry)

    def keys_for(self, cat: str):
        prefix = FEATURE_CATS[cat][1]
        if cat == "btn":
            keys = [k for k in self.registry if ":" not in k]
        else:
            keys = [k for k in self.registry if k.startswith(prefix)]
        return sorted(keys, key=lambda k: (self.registry.get(k) or k))


FEATURES = FeatureGate()


def apply_runtime_settings():
    """Push admin-panel values into the Bale client (limit, signature, gate)."""
    global MUSIC_CACHE_CHANNEL
    st = get_settings()
    try:
        mb = int(st.get("upload_limit_mb") or 50)
    except (TypeError, ValueError):
        mb = 50
    bot.upload_limit = max(5, min(50, mb)) * 1024 * 1024
    bot.caption_signature = (st.get("signature_text") or DEFAULT_SIGNATURE) if st.get("signature_on", True) else ""
    if not os.environ.get("MUSIC_CACHE_CHANNEL") and st.get("cache_channel"):
        MUSIC_CACHE_CHANNEL = str(st["cache_channel"])
    FEATURES.load()
    bot.feature_gate = FEATURES


def upload_limit_mb() -> int:
    return int(getattr(bot, "upload_limit", 50 * 1024 * 1024) // (1024 * 1024))


async def on_channel_seen(chat):
    """A post (or our admin status) in a channel reveals its numeric id --
    the only way to learn it, since Bale cannot resolve private invite links.
    The first such channel becomes the music cache channel."""
    global MUSIC_CACHE_CHANNEL
    if bot_chats_col is not None:
        try:
            bot_chats_col.update_one({"chat_id": chat.id}, {"$set": {
                "chat_id": chat.id, "title": chat.title, "type": "channel", "active": True,
                "updated_at": time.time()}}, upsert=True)
        except Exception as e:
            print(f"bot_chats save error: {e}", flush=True)
    if not MUSIC_CACHE_CHANNEL:
        MUSIC_CACHE_CHANNEL = str(chat.id)
        set_setting("cache_channel", chat.id)
        print(f"🎵 music cache channel set to {chat.id} ({chat.title})", flush=True)
        if ADMIN_ID:
            try:
                await bot.send_message(ADMIN_ID, f"🎵 کانال کش موزیک شناسایی و فعال شد:\n**{chat.title}**\n`{chat.id}`")
            except Exception:
                pass


async def _bale_background():
    while True:
        await asyncio.sleep(120)
        try:
            FEATURES.flush()
            _refresh_approved()
        except Exception as e:
            print(f"bale background error: {e}", flush=True)


# ---- admin panel pages ----
async def render_admin_access(msg: Message, page: int = 0, mode: str = "pending"):
    req = approval_required()
    q = ({"approved": {"$ne": True}, "banned": {"$ne": True}} if mode == "pending"
         else {"approved": True})
    docs, total = [], 0
    if users_col is not None:
        try:
            total = users_col.count_documents(q)
            docs = list(users_col.find(q).sort("last_seen", -1).skip(page * ADM_PAGE).limit(ADM_PAGE))
        except Exception as e:
            print(f"admin access error: {e}", flush=True)
    pages = max(1, (total + ADM_PAGE - 1) // ADM_PAGE)
    title = "⏳ در انتظار تأیید" if mode == "pending" else "✅ تأیید شده"
    lines = ["🔐 **مدیریت دسترسی کاربران**\n",
             f"تأیید دستی کاربران جدید: {'🟢 روشن' if req else '🔴 خاموش (همه آزادند)'}",
             f"\n**{title}** — {total} نفر • صفحه {page + 1} از {pages}\n"]
    rows = [[InlineKeyboardButton("🔴 خاموش کردن تأیید دستی" if req else "🟢 روشن کردن تأیید دستی",
                                  callback_data="adm_acctoggle")],
            [InlineKeyboardButton(("• " if mode == "pending" else "") + "⏳ در انتظار", callback_data="adm_acc_pending_0"),
             InlineKeyboardButton(("• " if mode == "approved" else "") + "✅ تأیید شده", callback_data="adm_acc_approved_0")]]
    for d in docs:
        cid = d.get("chat_id")
        name = (d.get("first_name") or "بی‌نام")[:16]
        uname = f"@{d['username']}" if d.get("username") else ""
        lines.append(f"• `{cid}` {name} {uname} — {fmt_ts(d.get('last_seen'))}")
        if mode == "pending":
            rows.append([InlineKeyboardButton(f"✅ {name}", callback_data=f"adm_appr_{cid}"),
                         InlineKeyboardButton("👤", callback_data=f"adm_u_{cid}")])
        else:
            rows.append([InlineKeyboardButton(f"🚫 لغو دسترسی {name}", callback_data=f"adm_revk_{cid}"),
                         InlineKeyboardButton("👤", callback_data=f"adm_u_{cid}")])
    if not docs:
        lines.append("_موردی نیست_")
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"adm_acc_{mode}_{page - 1}"))
    nav.append(InlineKeyboardButton(f"· {page + 1}/{pages} ·", callback_data="noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"adm_acc_{mode}_{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("➕ تأیید با آیدی عددی", callback_data="adm_accadd")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="adm_home")])
    await msg.edit_text("\n".join(lines)[:4000], reply_markup=InlineKeyboardMarkup(rows))


async def render_admin_limits(msg: Message):
    mb = upload_limit_mb()
    st = get_settings()
    sig_on = st.get("signature_on", True)
    cache = MUSIC_CACHE_CHANNEL or "— (یک پست در کانال کش بگذارید تا شناسایی شود)"
    text = ("📦 **محدودیت‌ها و تنظیمات فایل**\n\n"
            f"حداکثر حجم آپلود: **{mb} مگابایت** (بین ۵ تا ۵۰)\n"
            "_فایل‌های بزرگ‌تر از این حد تقسیم یا فشرده می‌شوند، یا ارسال نمی‌شوند._\n\n"
            f"امضای زیر فایل‌ها: {'🟢 ' + (st.get('signature_text') or DEFAULT_SIGNATURE) if sig_on else '🔴 خاموش'}\n"
            f"کانال کش موزیک: `{cache}`")
    rows = [
        [InlineKeyboardButton("➖ ۵", callback_data="adm_lim_-5"), InlineKeyboardButton("➖ ۱", callback_data="adm_lim_-1"),
         InlineKeyboardButton(f"{mb} MB", callback_data="noop"),
         InlineKeyboardButton("➕ ۱", callback_data="adm_lim_1"), InlineKeyboardButton("➕ ۵", callback_data="adm_lim_5")],
        [InlineKeyboardButton("۵", callback_data="adm_limset_5"), InlineKeyboardButton("۱۰", callback_data="adm_limset_10"),
         InlineKeyboardButton("۲۰", callback_data="adm_limset_20"), InlineKeyboardButton("۵۰", callback_data="adm_limset_50")],
        [InlineKeyboardButton("✍️ امضا: خاموش کن" if sig_on else "✍️ امضا: روشن کن", callback_data="adm_sigtoggle"),
         InlineKeyboardButton("📝 متن امضا", callback_data="adm_sigtext")],
        [InlineKeyboardButton("🖼 کارت کاور آهنگ: خاموش کن" if st.get("cover_card", True) else "🖼 کارت کاور آهنگ: روشن کن",
                              callback_data="adm_covertoggle")],
        [InlineKeyboardButton("♻️ تشخیص دوباره کانال کش", callback_data="adm_cachereset")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="adm_home")],
    ]
    await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(rows))


FEAT_PAGE = 8


async def render_admin_features(msg: Message, cat: str = "", page: int = 0):
    if not cat:
        FEATURES.flush()
        off = len(FEATURES.disabled)
        text = ("⚙️ **پنل پیشرفته — روشن/خاموش کردن قابلیت‌ها**\n\n"
                "هر قابلیت، لینک، دستور، نوع فایل و حتی تک‌تک دکمه‌های ربات (مثل کیفیت 1080p) اینجا قابل خاموش شدن است.\n"
                "دکمه‌های خاموش برای کاربران نمایش داده نمی‌شوند و استفاده از آن‌ها مسدود می‌شود. "
                "شما به‌عنوان مدیر همه چیز را می‌بینید.\n\n"
                "_دکمه‌ها با اولین نمایش در ربات به این فهرست اضافه می‌شوند._\n\n"
                f"قابلیت‌های خاموش: **{off}**")
        rows = [[InlineKeyboardButton(f"{label} ({len(FEATURES.keys_for(c))})", callback_data=f"adm_feat_{c}_0")]
                for c, (label, _) in FEATURE_CATS.items()]
        if off:
            rows.append([InlineKeyboardButton("🟢 روشن کردن همه", callback_data="adm_featall")])
        rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="adm_home")])
        return await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(rows))
    keys = FEATURES.keys_for(cat)
    pages = max(1, (len(keys) + FEAT_PAGE - 1) // FEAT_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = keys[page * FEAT_PAGE:(page + 1) * FEAT_PAGE]
    # callback_data stays short: index into a per-admin list
    store = user_data.setdefault(msg.chat.id, {})
    store["feat_keys"] = chunk
    store["feat_view"] = (cat, page)
    rows = []
    for i, k in enumerate(chunk):
        on = k not in FEATURES.disabled
        label = FEATURES.registry.get(k) or k
        shown = label if cat != "btn" else f"{label} · {k}"
        rows.append([InlineKeyboardButton(f"{'🟢' if on else '🔴'} {shown}"[:60], callback_data=f"adm_ft_{i}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"adm_feat_{cat}_{page - 1}"))
    nav.append(InlineKeyboardButton(f"· {page + 1}/{pages} ·", callback_data="noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"adm_feat_{cat}_{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 دسته‌ها", callback_data="adm_feat")])
    text = (f"⚙️ **{FEATURE_CATS[cat][0]}**\n\nروی هر مورد بزنید تا روشن/خاموش شود.\n"
            f"🟢 روشن • 🔴 خاموش — صفحه {page + 1} از {pages}")
    await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(rows))


async def handle_bale_admin_callback(client: Client, query: CallbackQuery, data: str) -> bool:
    """Admin callbacks added by the Bale build. Returns True when handled."""
    msg = query.message
    chat_id = msg.chat.id
    if data == "adm_access" or data.startswith("adm_acc_"):
        mode, page = "pending", 0
        if data.startswith("adm_acc_"):
            _, _, mode, pg = data.split("_", 3)
            page = int(pg)
        await render_admin_access(msg, page, mode)
    elif data == "adm_acctoggle":
        set_setting("approval_required", not approval_required())
        await render_admin_access(msg)
    elif data == "adm_accadd":
        user_states[chat_id] = "ADMIN_APPROVE_ID"
        await msg.edit_text("➕ آیدی عددی کاربر (یا چند آیدی با فاصله) را بفرستید تا دسترسی‌اش فعال شود:",
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 انصراف", callback_data="adm_access")]]))
    elif data.startswith(("adm_appr_", "adm_rej_", "adm_revk_")):
        target = int(data.rsplit("_", 1)[1])
        approve = data.startswith("adm_appr_")
        set_approved(target, approve)
        await query.answer("✅ دسترسی فعال شد" if approve else "🚫 دسترسی غیرفعال شد", show_alert=False)
        try:
            await client.send_message(
                target, "✅ دسترسی شما به **بات طاها** فعال شد! برای شروع /start را بزنید." if approve
                else "⛔️ درخواست/دسترسی شما به ربات توسط مدیر غیرفعال شد.")
        except Exception:
            pass
        if msg.text and "درخواست دسترسی" in msg.text:
            await msg.edit_text(msg.text + ("\n\n✅ تأیید شد" if approve else "\n\n❌ رد شد"))
        elif data.startswith("adm_revk_"):
            await render_admin_access(msg, 0, "approved")
        else:
            await render_admin_user(msg, target)
    elif data == "adm_limits":
        await render_admin_limits(msg)
    elif data.startswith("adm_lim_") or data.startswith("adm_limset_"):
        val = int(data.rsplit("_", 1)[1])
        mb = val if data.startswith("adm_limset_") else upload_limit_mb() + val
        mb = max(5, min(50, mb))
        set_setting("upload_limit_mb", mb)
        apply_runtime_settings()
        await render_admin_limits(msg)
    elif data == "adm_sigtoggle":
        set_setting("signature_on", not get_settings().get("signature_on", True))
        apply_runtime_settings()
        await render_admin_limits(msg)
    elif data == "adm_covertoggle":
        set_setting("cover_card", not get_settings().get("cover_card", True))
        await render_admin_limits(msg)
    elif data == "adm_sigtext":
        user_states[chat_id] = "ADMIN_SIGTEXT"
        await msg.edit_text("📝 متن امضای زیر فایل‌ها را بفرستید (مثلاً `🤖 @tahasbot`):",
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 انصراف", callback_data="adm_limits")]]))
    elif data == "adm_cachereset":
        global MUSIC_CACHE_CHANNEL
        MUSIC_CACHE_CHANNEL = os.environ.get("MUSIC_CACHE_CHANNEL", "")
        set_setting("cache_channel", None)
        await query.answer("یک پست جدید در کانال کش بگذارید تا شناسایی شود.", show_alert=True)
        await render_admin_limits(msg)
    elif data == "adm_feat":
        await render_admin_features(msg)
    elif data.startswith("adm_feat_"):
        _, _, cat, pg = data.split("_", 3)
        await render_admin_features(msg, cat, int(pg))
    elif data.startswith("adm_ft_"):
        keys = user_data.get(chat_id, {}).get("feat_keys") or []
        i = int(data.rsplit("_", 1)[1])
        if i < len(keys):
            on = FEATURES.toggle(keys[i])
            await query.answer("🟢 روشن شد" if on else "🔴 خاموش شد", show_alert=False)
        cat, page = user_data.get(chat_id, {}).get("feat_view", ("btn", 0))
        await render_admin_features(msg, cat, page)
    elif data == "adm_featall":
        FEATURES.disabled.clear()
        set_setting("feat_disabled", [])
        await render_admin_features(msg)
    else:
        return False
    return True


async def handle_bale_admin_text(client: Client, message: Message, state: str, text: str) -> bool:
    chat_id = message.chat.id
    if state == "ADMIN_APPROVE_ID":
        user_states.pop(chat_id, None)
        ids = [int(x) for x in re.findall(r"\d{4,}", text)]
        for uid in ids:
            set_approved(uid, True)
            try:
                await client.send_message(uid, "✅ دسترسی شما به **بات طاها** فعال شد! برای شروع /start را بزنید.")
            except Exception:
                pass
        await message.reply_text(f"✅ دسترسی {len(ids)} کاربر فعال شد." if ids else "❌ آیدی معتبری پیدا نشد.",
                                 reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔐 دسترسی‌ها", callback_data="adm_access")]]))
        return True
    if state == "ADMIN_SIGTEXT":
        user_states.pop(chat_id, None)
        set_setting("signature_text", text.strip()[:60])
        set_setting("signature_on", True)
        apply_runtime_settings()
        await message.reply_text("✅ امضا ذخیره شد.",
                                 reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📦 تنظیمات فایل", callback_data="adm_limits")]]))
        return True
    return False


async def _keep_render_awake():
    """Render's free tier sleeps after 15 minutes without inbound HTTP traffic,
    and long-polling Bale is outbound only. Hitting our own public URL goes
    through Render's proxy, which counts as traffic."""
    url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("SELF_PING_URL")
    if not url:
        return
    while True:
        await asyncio.sleep(240)
        try:
            await asyncio.to_thread(requests.get, url, timeout=20)
        except Exception as e:
            print(f"self-ping failed: {e}", flush=True)


async def _run_bot():
    """start -> spawn background worker -> idle -> stop.

    Mirrors what Client.run() does with no argument, plus the subscription /
    mixtape loop, which needs to live on the same event loop as the client.
    """
    await bot.start()
    print("🤖 Bot started", flush=True)
    register_bot_commands()
    apply_runtime_settings()
    bot.on_channel_seen = on_channel_seen
    _refresh_approved(force=True)
    asyncio.create_task(_bale_background())
    asyncio.create_task(_keep_render_awake())
    # Two instances sharing one database would both poll subscriptions and send
    # weekly mixtapes, so users would get everything twice. Exactly one deploy
    # should own the background loop.
    if ENABLE_BACKGROUND_WORKER:
        asyncio.create_task(background_worker(bot))
    else:
        print("⏸ background worker disabled (ENABLE_BACKGROUND_WORKER=false)", flush=True)
    await idle()
    try:
        await bot.stop()
    except (TimeoutError, asyncio.TimeoutError, Exception) as e:
        print(f"Bot stopped: {e}", flush=True)


def notify_admin_crash(error_trace: str):
    """Best-effort crash alert straight over the raw Bot HTTP API -- deliberately
    NOT going through the Pyrogram client, since whatever crashed the process
    may have left that client in a dead/unusable state. Docker's
    restart:unless-stopped already handles the actual recovery; this just
    makes sure a crash doesn't go unnoticed until someone happens to check.
    """
    if not ADMIN_ID or not BOT_TOKEN:
        return
    try:
        text = f"🔴 Bot crashed and is restarting\n\n```\n{error_trace[-3500:]}\n```"
        _TG_SESSION.post(
            f"https://tapi.bale.ai/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10)
    except Exception as e:
        print(f"crash notify failed: {e}", flush=True)


if __name__ == "__main__":
    print("Cleaning up old webhooks...", flush=True)
    try: _TG_SESSION.get(f"https://tapi.bale.ai/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=True", timeout=5)
    except: pass
    threading.Thread(target=run_flask, daemon=True).start()
    try:
        bot.run(_run_bot())
    except (KeyboardInterrupt, SystemExit):
        print("Bot exiting gracefully...", flush=True)
    except (TimeoutError, asyncio.TimeoutError) as e:
        print(f"Bot shutdown timeout handled cleanly: {e}", flush=True)
    except Exception:
        tb = traceback.format_exc()
        print(f"FATAL: bot crashed\n{tb}", flush=True)
        notify_admin_crash(tb)
        raise
