# -*- coding: utf-8 -*-
"""
TahasBot - Multi-Feature Media & Music Bot for Bale Messenger
Powered by Balethon & Render Web Service with GitHub Actions Heavy Worker Offloading.
"""

import os
import re
import sys
import time
import glob
import json
import asyncio
import traceback
import subprocess
import urllib.parse
import math
from pathlib import Path
from typing import Optional, Dict, Any

import aiohttp
from aiohttp import web
import requests
import pymongo
import yt_dlp

from balethon import Client
from balethon.objects import (
    InlineKeyboard, InlineKeyboardButton,
    Message, CallbackQuery, Update
)

# Monkey-patch Balethon InlineKeyboard so both list-of-lists and unpacked arguments serialize to proper 2D array
_orig_inline_kb_init = InlineKeyboard.__init__
_orig_add_row = InlineKeyboard.add_row

def _safe_inline_kb_init(self, *rows, **kwargs):
    if len(rows) == 1 and isinstance(rows[0], (list, tuple)):
        first_item = rows[0]
        if first_item and isinstance(first_item[0], (list, tuple)):
            rows = tuple(first_item)
        elif not first_item:
            rows = ()
    _orig_inline_kb_init(self, *rows, **kwargs)

def _safe_add_row(self, *row, row_index: int = -1):
    if len(row) == 1 and isinstance(row[0], (list, tuple)):
        row = tuple(row[0])
    _orig_add_row(self, *row, row_index=row_index)

InlineKeyboard.__init__ = _safe_inline_kb_init
InlineKeyboard.add_row = _safe_add_row


import modules.menus as menus
import modules.rega_music as rega_music
import modules.music_tools as mtools
import modules.image_tools as itools
import modules.video_tools as vtools
import modules.social_media as soc
import modules.audio_recognition as audd
import modules.audio_meta as audio_meta
import ai_chat

# ----------------- CONFIGURATION & CREDENTIALS -----------------
BALE_BOT_TOKEN = os.environ.get("BALE_BOT_TOKEN", "").strip()
MONGO_URI = os.environ.get("MONGO_URI", "").strip()
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GITHUB_REPO = os.environ.get("GITHUB_REPO", "TheTcoTeam/TahasBot").strip()
PORT = int(os.environ.get("PORT", 10000))


# Directories
MUSIC_DIR = "music_downloads"
MEDIA_DIR = "media_downloads"
WORK_DIR = "jobs"
for d in [MUSIC_DIR, MEDIA_DIR, WORK_DIR]:
    os.makedirs(d, exist_ok=True)

# ----------------- DATABASE SETUP -----------------
mongo_client = None
db = None
try:
    mongo_client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    mongo_client.admin.command("ping")
    db = mongo_client["telebot_db"]
    print("Connected to MongoDB Atlas successfully!", flush=True)
except Exception as e:
    print(f"MongoDB connection warning: {e}. Falling back to in-memory storage.", flush=True)
    mongo_client = None
    db = None

# In-memory session tracking
USER_STATES: Dict[int, str] = {}
USER_DATA: Dict[int, Dict[str, Any]] = {}
USER_LANGS: Dict[int, str] = {}

# Admin & Owner Configuration
ADMIN_IDS = [700708432, 1433545605]
ADMIN_USERNAMES = ["tahathegoat", "mthemolester"]
OWNER_ID = 700708432

AI_SYSTEM_PROMPT = (
    "You are the intelligent assistant for TahasBot on Bale Messenger. "
    "Respond in friendly Persian (Farsi), helpfully, accurately, and concisely. "
    "You can answer any user questions, chat warmly, and help with music, media, and download recommendations."
)


def is_admin(user_id, username="") -> bool:
    try:
        if int(user_id) in ADMIN_IDS:
            return True
    except Exception:
        pass
    if username and username.lower().lstrip("@") in ADMIN_USERNAMES:
        return True
    return False


def is_maintenance_mode() -> bool:
    if db is not None:
        try:
            doc = db.settings.find_one({"key": "maintenance"})
            if doc:
                return bool(doc.get("enabled", False))
        except Exception:
            pass
    return False


def set_maintenance_mode(enabled: bool):
    if db is not None:
        try:
            db.settings.update_one({"key": "maintenance"}, {"$set": {"enabled": enabled}}, upsert=True)
        except Exception:
            pass


_in_memory_settings = {}


def get_video_slice_limit_mb() -> int:
    """Returns the maximum chunk size in MB for video slicing (default 25 MB)."""
    if db is not None:
        try:
            doc = db.settings.find_one({"key": "video_slice_mb"})
            if doc and "value" in doc:
                return int(doc["value"])
        except Exception as e:
            print(f"Error fetching video_slice_mb: {e}", flush=True)
    return _in_memory_settings.get("video_slice_mb", 25)


def set_video_slice_limit_mb(mb: int):
    """Sets the maximum chunk size in MB for video slicing."""
    _in_memory_settings["video_slice_mb"] = int(mb)
    if db is not None:
        try:
            db.settings.update_one(
                {"key": "video_slice_mb"},
                {"$set": {"value": int(mb), "updated_at": int(time.time())}},
                upsert=True
            )
            print(f"Updated video_slice_mb to {mb} MB", flush=True)
        except Exception as e:
            print(f"Error setting video_slice_mb: {e}", flush=True)


def is_playlist_locked(platform: str) -> bool:
    """Check if playlists are locked for spotify, soundcloud, or ytmusic (default True = locked)."""
    if db is not None:
        try:
            doc = db.settings.find_one({"key": f"lock_{platform}_playlists"})
            if doc and "locked" in doc:
                return bool(doc["locked"])
        except Exception:
            pass
    return _in_memory_settings.get(f"lock_{platform}_playlists", True)


def set_playlist_lock(platform: str, locked: bool):
    """Set playlist lock state for a given platform."""
    _in_memory_settings[f"lock_{platform}_playlists"] = bool(locked)
    if db is not None:
        try:
            db.settings.update_one(
                {"key": f"lock_{platform}_playlists"},
                {"$set": {"key": f"lock_{platform}_playlists", "locked": bool(locked), "updated_at": int(time.time())}},
                upsert=True
            )
        except Exception as e:
            print(f"Error setting {platform} playlist lock: {e}", flush=True)


def toggle_playlist_lock(platform: str) -> bool:
    new_val = not is_playlist_locked(platform)
    set_playlist_lock(platform, new_val)
    return new_val


def is_user_banned(chat_id: int) -> bool:
    if db is not None:
        try:
            doc = db.users.find_one({"chat_id": int(chat_id)}, {"banned": 1})
            if doc and doc.get("banned"):
                return True
        except Exception:
            pass
    return False


def get_user_lang(chat_id: int) -> str:
    return "fa"


def save_user(chat_id: int, user_info: dict):
    if db is not None:
        try:
            now = int(time.time())
            user_info["chat_id"] = int(chat_id)
            user_info["last_seen"] = now
            db.users.update_one(
                {"chat_id": int(chat_id)},
                {
                    "$set": user_info,
                    "$setOnInsert": {
                        "first_seen": now,
                        "events": 0,
                        "downloads": 0,
                        "banned": False
                    }
                },
                upsert=True
            )
        except Exception:
            pass


def log_activity(chat_id: int, action: str, details: str = ""):
    if db is not None:
        try:
            db.activity.insert_one({
                "chat_id": int(chat_id),
                "action": action,
                "details": str(details)[:200],
                "timestamp": int(time.time())
            })
            inc_dict = {"events": 1}
            if "download" in action.lower():
                inc_dict["downloads"] = 1
            db.users.update_one({"chat_id": int(chat_id)}, {"$inc": inc_dict}, upsert=True)
        except Exception:
            pass


async def safe_edit_or_send(client: Client, chat_id: int, msg: Message, text: str, reply_markup=None):
    if msg:
        try:
            return await msg.edit(text, reply_markup=reply_markup)
        except Exception:
            try:
                return await msg.edit_text(text, reply_markup=reply_markup)
            except Exception:
                pass
    return await client.send_message(chat_id, text, reply_markup=reply_markup)


# ----------------- BALETHON CLIENT INITIALIZATION -----------------
bot = Client(BALE_BOT_TOKEN, time_out=60)


# ----------------- GITHUB ACTIONS WORKER DISPATCHER -----------------
def dispatch_github_worker(
    chat_id: int,
    message_id: int,
    url: str,
    fmt: str = "mp4",
    quality: str = "720p",
    slice_mb: int = 25,
    subtitles: bool = False,
    thumbnail: bool = False,
    description: bool = False,
    comments: bool = False
) -> bool:
    """Trigger GitHub Actions workflow to execute heavy download on 7GB runner."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return False
    try:
        api_url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/worker.yml/dispatches"
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json"
        }
        payload = {
            "ref": "main",
            "inputs": {
                "url": url,
                "chat_id": str(chat_id),
                "message_id": str(message_id),
                "format": fmt,
                "quality": quality,
                "slice_mb": str(slice_mb),
                "subtitles": "true" if subtitles else "false",
                "thumbnail": "true" if thumbnail else "false",
                "description": "true" if description else "false",
                "comments": "true" if comments else "false"
            }
        }
        res = requests.post(api_url, headers=headers, json=payload, timeout=10)
        print(f"GitHub workflow dispatch response: {res.status_code} {res.text}", flush=True)
        return res.status_code in [200, 204]
    except Exception as e:
        print(f"Error dispatching GitHub workflow: {e}", flush=True)
        return False


# ----------------- NATIVE MEDIA DOWNLOADERS (RUN ON RENDER) -----------------
def download_social_media_sync(url: str, prefix: str = "media") -> dict:
    """Lightweight downloader for Instagram, TikTok, and SoundCloud via yt-dlp."""
    ts = int(time.time() * 1000)
    out_tmpl = os.path.join(MEDIA_DIR, f"{prefix}_{ts}_%(id)s.%(ext)s")

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist",
        "--no-check-certificates",
        "-N", "4",
        "--max-filesize", "48M",
        "-o", out_tmpl
    ]

    # Use cookies if available
    if os.path.exists("cookies.txt") and os.path.getsize("cookies.txt") > 0:
        cmd.extend(["--cookies", "cookies.txt"])

    cmd.append(url)
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    # Find created files
    downloaded = glob.glob(os.path.join(MEDIA_DIR, f"{prefix}_{ts}_*.*"))
    media_files = [f for f in downloaded if f.lower().endswith((".mp4", ".mp3", ".jpg", ".jpeg", ".png", ".webp"))]

    if not media_files:
        raise RuntimeError(f"Download failed: {res.stderr[-200:] if res.stderr else 'No media found'}")

    return {
        "file": media_files[0],
        "all_files": media_files,
        "is_audio": media_files[0].lower().endswith((".mp3", ".m4a", ".aac"))
    }


# ----------------- BOT COMMAND HANDLERS -----------------
async def cmd_start(client: Client, message: Message):
    chat_id = message.chat.id
    user_info = {
        "first_name": message.author.first_name if message.author else "",
        "username": message.author.username if message.author else ""
    }
    save_user(chat_id, user_info)
    USER_STATES.pop(chat_id, None)

    lang = get_user_lang(chat_id)
    name = (message.author.first_name if message.author else "") or "کاربر"
    welcome_text = menus.get_menu_text("main_menu_welcome", lang=lang, name=name)
    kb = menus.get_main_keyboard(chat_id, get_user_lang, is_admin)

    await client.send_message(chat_id, welcome_text, reply_markup=kb)


async def cmd_help(client: Client, message: Message):
    chat_id = message.chat.id
    lang = get_user_lang(chat_id)
    if lang == "fa":
        help_text = (
            "📖 **راهنمای جامع ربات طه (TahasBot):**\n\n"
            "⚡️ **دانلود سریع با ارسال لینک:**\n"
            "فقط کافیست لینک مدیا را برای ربات بفرستید:\n"
            "• **اسپاتیفای و دیزر:** دانلود با کیفیت ۳۲۰ و کاور اورجینال\n"
            "• **اینستاگرام:** دانلود ریلز و پست‌های ویدیویی/تصویری\n"
            "• **تیک‌تاک:** دانلود بدون واترمارک\n"
            "• **یوتیوب:** دانلود ویدیو و فایل صوتی با سرور قدرتمند\n"
            "• **ساندکلاد و پینترست:** دانلود مستقیم و فوری\n\n"
            "🎵 **جستجوی آهنگ:**\n"
            "کافیست نام آهنگ یا خواننده را تایپ کنید تا لیست پخش آماده شود.\n\n"
            "🎛 **ابزارهای موزیک و تصویر:**\n"
            "یک آهنگ، وویس یا عکس به ربات بفرستید تا منوی افکت‌ها (اسلو، نایتکور، ۸ بعدی، فشرده‌سازی، حذف پس‌زمینه و...) نمایش داده شود."
        )
    else:
        help_text = (
            "📖 **TahasBot User Guide:**\n\n"
            "⚡️ **Fast Media Downloads:**\n"
            "Simply send any supported link:\n"
            "• **Spotify & Deezer:** 320kbps MP3 with official cover art\n"
            "• **Instagram:** Reels, video posts, and photos\n"
            "• **TikTok:** High quality no-watermark downloads\n"
            "• **YouTube:** Video & audio downloads via dedicated worker\n"
            "• **SoundCloud & Pinterest:** Direct audio & media downloads\n\n"
            "🎵 **Song Search:**\n"
            "Type any track or artist name to search and download directly.\n\n"
            "🎛 **Audio & Image Tools:**\n"
            "Send any song or photo to apply effects (slowed, nightcore, 8D, compression, etc.)."
        )

    kb = InlineKeyboard([
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]
    ])
    await client.send_message(chat_id, help_text, reply_markup=kb)


async def cmd_tool(client: Client, message: Message):
    chat_id = message.chat.id
    lang = get_user_lang(chat_id)
    reply = getattr(message, "reply_to_message", None)
    if reply:
        if getattr(reply, "video", None) or (getattr(reply, "document", None) and (getattr(reply.document, "mime_type", "") or "").startswith("video/")):
            status_msg = await client.send_message(chat_id, "⏳ در حال دریافت ویدیو...")
            try:
                v_obj = getattr(reply, "video", None) or getattr(reply, "document", None)
                file_id = getattr(v_obj, "id", None) or getattr(v_obj, "file_id", None)
                if file_id:
                    raw_bytes = await client.download(file_id)
                    saved_path = os.path.join(VIDEO_EDIT_DIR, f"vid_{chat_id}_{int(time.time())}.mp4")
                    with open(saved_path, "wb") as f:
                        f.write(raw_bytes)
                    USER_DATA.setdefault(chat_id, {})["vedit_path"] = saved_path
                    kb = vtools.video_editor_kb()
                    await status_msg.edit_text("🎬 **ویدیو دریافت شد! ابزار ویرایش مورد نظر را انتخاب کنید:**", reply_markup=kb)
                    return
            except Exception as e:
                await status_msg.edit_text(f"❌ خطا در دریافت ویدیو: {e}")
                return

        if getattr(reply, "audio", None) or getattr(reply, "voice", None) or (getattr(reply, "document", None) and (getattr(reply.document, "mime_type", "") or "").startswith("audio/")):
            status_msg = await client.send_message(chat_id, "⏳ در حال دریافت فایل صوتی...")
            try:
                file_obj = getattr(reply, "audio", None) or getattr(reply, "voice", None) or getattr(reply, "document", None)
                file_id = getattr(file_obj, "id", None) or getattr(file_obj, "file_id", None)
                if file_id:
                    raw_bytes = await client.download(file_id)
                    saved_path = os.path.join(MUSIC_DIR, f"audio_{chat_id}_{int(time.time())}.mp3")
                    with open(saved_path, "wb") as f:
                        f.write(raw_bytes)
                    USER_DATA.setdefault(chat_id, {})["audio_path"] = saved_path
                    kb = menus.get_music_tools_keyboard(chat_id, get_user_lang)
                    await status_msg.edit_text("🎵 **فایل صوتی دریافت شد! ابزار مورد نظر را انتخاب کنید:**", reply_markup=kb)
                    return
            except Exception as e:
                await status_msg.edit_text(f"❌ خطا در دریافت فایل صوتی: {e}")
                return

        if getattr(reply, "photo", None) or (getattr(reply, "document", None) and (getattr(reply.document, "mime_type", "") or "").startswith("image/")):
            status_msg = await client.send_message(chat_id, "⏳ در حال دریافت تصویر...")
            try:
                photo_obj = getattr(reply, "photo", None) or getattr(reply, "document", None)
                file_id = getattr(photo_obj, "id", None) or getattr(photo_obj, "file_id", None)
                if file_id:
                    raw_bytes = await client.download(file_id)
                    saved_path = os.path.join(MEDIA_DIR, f"img_{chat_id}_{int(time.time())}.jpg")
                    with open(saved_path, "wb") as f:
                        f.write(raw_bytes)
                    USER_DATA.setdefault(chat_id, {})["image_path"] = saved_path
                    kb = menus.get_image_tools_keyboard(chat_id, get_user_lang)
                    await status_msg.edit_text("🎨 **تصویر دریافت شد! ابزار مورد نظر را انتخاب کنید:**", reply_markup=kb)
                    return
            except Exception as e:
                await status_msg.edit_text(f"❌ خطا در دریافت تصویر: {e}")
                return

    kb = menus.get_tools_picker_keyboard(chat_id, get_user_lang)
    text = (
        "🎛 **جعبه ابزار چندرسانه‌ای:**\n"
        "بخش مورد نظر خود را انتخاب کنید یا روی یک فایل صوتی/عکس/ویدیو ریپلای بزنید:"
        if lang == "fa" else
        "🎛 **Multimedia Toolbox:**\n"
        "Select a tool category or reply to an audio/image/video file with /tool:"
    )
    await client.send_message(chat_id, text, reply_markup=kb)




# ----------------- USER PROFILE & ADMIN PANEL HANDLERS -----------------
async def render_user_profile(client: Client, chat_id: int, msg: Message = None, user_obj: Any = None):
    first_name = (user_obj.first_name if user_obj else "") or "کاربر"
    username = (user_obj.username if user_obj else "") or ""
    uname_str = f"@{username}" if username else "ثبت‌نشده"

    admin_badge = "👑 مالک و مدیر اصلی ربات" if is_admin(chat_id, username) else "⭐️ عضو ویژه"

    downloads_count = 0
    events_count = 0
    first_seen_str = "همین لحظه"
    last_seen_str = "اکنون"
    recent_acts = []

    if db is not None:
        try:
            udoc = db.users.find_one({"chat_id": int(chat_id)})
            if udoc:
                downloads_count = udoc.get("downloads", 0)
                events_count = udoc.get("events", 0)
                fs = udoc.get("first_seen")
                if fs:
                    first_seen_str = time.strftime("%Y/%m/%d %H:%M", time.localtime(fs))
                ls = udoc.get("last_seen")
                if ls:
                    last_seen_str = time.strftime("%Y/%m/%d %H:%M", time.localtime(ls))

            cursor = db.activity.find({"chat_id": int(chat_id)}).sort("timestamp", -1).limit(4)
            recent_acts = list(cursor)
        except Exception as e:
            print(f"profile error: {e}", flush=True)

    act_lines = []
    for a in recent_acts:
        act_time = time.strftime("%H:%M", time.localtime(a.get("timestamp", time.time())))
        act_name = a.get("action", "فعالیت").replace("_", " ")
        act_detail = str(a.get("details", ""))[:30]
        act_lines.append(f"• `[{act_time}]` {act_name} - {act_detail}")

    if not act_lines:
        act_lines.append("_هنوز فعالیتی ثبت نشده است_")

    profile_text = (
        "👤 **پروفایل و آمار کاربری شما:**\n\n"
        f"🏷 **نام:** {first_name}\n"
        f"🔗 **یوزرنیم:** {uname_str}\n"
        f"🆔 **شناسه عددی:** `{chat_id}`\n"
        f"🔰 **سطح دسترسی:** {admin_badge}\n\n"
        f"📊 **آمار استفاده از ربات:**\n"
        f"⬇️ کل دانلودهای موفق: **{downloads_count}** فایل\n"
        f"⚡️ مجموع درخواست‌ها: **{events_count}** بار\n"
        f"🕒 تاریخ اولین بازدید: **{first_seen_str}**\n"
        f"🕒 آخرین فعالیت: **{last_seen_str}**\n\n"
        f"📜 **آخرین فعالیت‌های ثبت‌شده:**\n" + "\n".join(act_lines)
    )

    kb = InlineKeyboard([
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]
    ])
    await safe_edit_or_send(client, chat_id, msg, profile_text, kb)


async def cmd_admin(client: Client, message: Message):
    chat_id = message.chat.id
    username = (message.author.username if message.author else "") or ""
    if not is_admin(chat_id, username):
        return await client.send_message(chat_id, "⛔️ این بخش مختص مدیریت و مالک ربات است.")
    await render_admin_home(client, chat_id)


async def render_admin_home(client: Client, chat_id: int, msg: Message = None):
    if not is_admin(chat_id):
        return

    maint = is_maintenance_mode()
    maint_status = "🔴 حالت تعمیرات فعال است" if maint else "🟢 ربات آنلاین و فعال است"
    maint_toggle_btn = "🟢 خاموش کردن تعمیرات (فعال‌سازی)" if maint else "🔴 فعال‌سازی حالت تعمیرات (تعلیق)"

    total_users = 0
    active_24h = 0
    total_dls = 0
    now = time.time()
    if db is not None:
        try:
            total_users = db.users.count_documents({})
            active_24h = db.users.count_documents({"last_seen": {"$gt": now - 86400}})
            total_dls = db.activity.count_documents({"action": {"$regex": "download"}})
        except Exception:
            pass

    slice_limit = get_video_slice_limit_mb()

    text = (
        "🛠 **پنل مدیریت پیشرفته طه (Admin Panel)**\n\n"
        f"🚦 **وضعیت ربات:** {maint_status}\n"
        f"✂️ سقف برش ویدیوی یوتیوب: **{slice_limit} مگابایت**\n"
        f"👥 کل کاربران: **{total_users}** نفر\n"
        f"🟢 کاربران ۲۴ ساعت گذشته: **{active_24h}** نفر\n"
        f"⬇️ کل دانلودها: **{total_dls}** فایل\n\n"
        "بخش مورد نظر خود را انتخاب کنید:"
    )

    kb = InlineKeyboard([
        [InlineKeyboardButton("📊 آمار و ارقام جامع", "adm_stats"), InlineKeyboardButton("👥 مدیریت کاربران", "adm_users_0_seen")],
        [InlineKeyboardButton("🔍 جستجوی کاربر", "adm_search"), InlineKeyboardButton("📜 جریان زنده فعالیت‌ها", "adm_act_0")],
        [InlineKeyboardButton(f"✂️ سقف تکه‌کردن ({slice_limit} MB)", "adm_slice"), InlineKeyboardButton("🎛 قفل پلی‌لیست‌ها", "adm_locks")],
        [InlineKeyboardButton("✉️ تیکت‌های پشتیبانی", "adm_sup_0"), InlineKeyboardButton("📢 ارسال همگانی (Broadcast)", "adm_bc")],
        [InlineKeyboardButton(maint_toggle_btn, "adm_maint")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]
    ])
    await safe_edit_or_send(client, chat_id, msg, text, kb)


async def render_admin_locks(client: Client, chat_id: int, msg: Message = None):
    if not is_admin(chat_id):
        return
    sp_l = is_playlist_locked("spotify")
    sc_l = is_playlist_locked("soundcloud")
    ytm_l = is_playlist_locked("ytmusic")

    def stat(l): return "🔒 قفل (دانلود و جستجو مسدود)" if l else "🔓 باز (دانلود و جستجو مجاز)"
    def btn_txt(plat, l): return f"{'🔓 باز کردن' if l else '🔒 قفل کردن'} پلی‌لیست {plat}"

    text = (
        "🎛 **تنظیمات قفل پلی‌لیست‌ها (Playlist Locks Control):**\n\n"
        "جهت مدیریت منابع سرور، می‌توانید دانلود پلی‌لیست‌ها را برای هر پلتفرم فعال یا قفل فرمایید:\n\n"
        f"🟢 **اسپاتیفای (Spotify):** {stat(sp_l)}\n"
        f"☁️ **ساندکلاد (SoundCloud):** {stat(sc_l)}\n"
        f"🔴 **یوتیوب موزیک (YouTube Music):** {stat(ytm_l)}\n\n"
        "روی دکمه پلتفرم مورد نظر جهت تغییر وضعیت کلیک کنید:"
    )
    kb = InlineKeyboard([
        [InlineKeyboardButton(btn_txt("اسپاتیفای", sp_l), "adm_tog_lock_sp")],
        [InlineKeyboardButton(btn_txt("ساندکلاد", sc_l), "adm_tog_lock_sc")],
        [InlineKeyboardButton(btn_txt("یوتیوب موزیک", ytm_l), "adm_tog_lock_ytm")],
        [InlineKeyboardButton("🔙 بازگشت به پنل مدیریت", "adm_home")]
    ])
    await safe_edit_or_send(client, chat_id, msg, text, kb)


async def render_admin_slice(client: Client, chat_id: int, msg: Message = None):
    if not is_admin(chat_id):
        return
    current_limit = get_video_slice_limit_mb()
    text = (
        "✂️ **تنظیم هوشمند محدودیت برش و تکه‌کردن ویدیو (Video Slicing Limit):**\n\n"
        f"📏 سقف حجم فعلی ارسال در بله: **{current_limit} مگابایت**\n\n"
        "ویدیوهای یوتیوب بزرگتر از این حجم به صورت خودکار به پارت‌های منظم (`[عنوان] Part 001`, `Part 002`, ...) "
        "بدون افت کیفیت تقسیم و ارسال خواهند شد.\n\n"
        "یکی از مقادیر پیش‌فرض را انتخاب کنید یا مقدار سفارشی وارد نمایید:"
    )
    kb = InlineKeyboard([
        [
            InlineKeyboardButton("15 MB", "adm_set_slice_15"),
            InlineKeyboardButton("20 MB", "adm_set_slice_20"),
            InlineKeyboardButton("25 MB (پیش‌فرض)", "adm_set_slice_25")
        ],
        [
            InlineKeyboardButton("30 MB", "adm_set_slice_30"),
            InlineKeyboardButton("40 MB", "adm_set_slice_40"),
            InlineKeyboardButton("48 MB", "adm_set_slice_48")
        ],
        [InlineKeyboardButton("✏️ وارد کردن حجم دلخواه (مگابایت)", "adm_slice_custom")],
        [InlineKeyboardButton("🔙 بازگشت به پنل مدیریت", "adm_home")]
    ])
    await safe_edit_or_send(client, chat_id, msg, text, kb)


async def render_admin_stats(client: Client, chat_id: int, msg: Message = None):
    if not is_admin(chat_id):
        return

    now = time.time()
    total_users = 0
    banned_users = 0
    active_24h = 0
    active_7d = 0
    total_dls = 0
    dls_24h = 0
    total_events = 0

    if db is not None:
        try:
            total_users = db.users.count_documents({})
            banned_users = db.users.count_documents({"banned": True})
            active_24h = db.users.count_documents({"last_seen": {"$gt": now - 86400}})
            active_7d = db.users.count_documents({"last_seen": {"$gt": now - 604800}})
            total_dls = db.activity.count_documents({"action": {"$regex": "download"}})
            dls_24h = db.activity.count_documents({"action": {"$regex": "download"}, "timestamp": {"$gt": now - 86400}})
            total_events = db.activity.count_documents({})
        except Exception as e:
            print(f"stats error: {e}", flush=True)

    stats_text = (
        "📊 **آمار و تحلیل جامع عملکرد ربات:**\n\n"
        f"👥 **کاربران:**\n"
        f"• کل اعضا: **{total_users}** نفر\n"
        f"• فعال در ۲۴ ساعت: **{active_24h}** نفر\n"
        f"• فعال در ۷ روز اخیر: **{active_7d}** نفر\n"
        f"• کاربران مسدود شده: **{banned_users}** نفر\n\n"
        f"⬇️ **دانلودها و رسانه‌ها:**\n"
        f"• کل دانلودهای ثبت‌شده: **{total_dls}** فایل\n"
        f"• دانلودهای ۲۴ ساعت گذشته: **{dls_24h}** فایل\n"
        f"• کل درخواست‌ها و لاگ‌ها: **{total_events}** رویداد\n\n"
        f"⚙️ **زیرساخت ابری:**\n"
        f"• هاست پیام‌رسان: Render Web Service (پایداری ۲۴ ساعته)\n"
        f"• سرور اختصاصی یوتیوب: GitHub Actions 7GB Runner\n"
        f"• پایگاه داده: MongoDB Atlas Dedicated Cluster"
    )

    kb = InlineKeyboard([
        [InlineKeyboardButton("🔄 به‌روزرسانی آمار", "adm_stats")],
        [InlineKeyboardButton("🔙 بازگشت به پنل مدیریت", "adm_home")]
    ])
    await safe_edit_or_send(client, chat_id, msg, stats_text, kb)


async def render_admin_users(client: Client, chat_id: int, page: int = 0, sort: str = "seen", msg: Message = None):
    if not is_admin(chat_id):
        return

    PAGE_SIZE = 8
    sort_field = "events" if sort == "events" else "last_seen"
    sort_label = "🔥 پرمصرف‌ترین" if sort == "events" else "🕒 آخرین بازدید"

    total = 0
    docs = []
    if db is not None:
        try:
            total = db.users.count_documents({})
            total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
            page = max(0, min(page, total_pages - 1))
            docs = list(db.users.find({}).sort(sort_field, -1).skip(page * PAGE_SIZE).limit(PAGE_SIZE))
        except Exception as e:
            print(f"users list error: {e}", flush=True)

    if not docs:
        text = "👥 کاربری در دیتابیس یافت نشد."
        kb = InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به پنل", "adm_home")]])
    else:
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        text = f"👥 **لیست کاربران ربات** (صفحه {page + 1} از {total_pages})\nمرتب‌سازی: **{sort_label}**\n\nبرای مشاهده پروفایل و اعمال تغییرات، روی کاربر کلیک کنید:\n"

        btn_rows = []
        for d in docs:
            u_id = d.get("chat_id")
            u_name = (d.get("first_name") or "کاربر")[:16]
            u_banned = "⛔️ " if d.get("banned") else ""
            btn_rows.append([InlineKeyboardButton(f"{u_banned}👤 {u_name} ({u_id})", f"adm_u_{u_id}")])

        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️ قبلی", f"adm_users_{page - 1}_{sort}"))
        nav_row.append(InlineKeyboardButton(f"· {page + 1} / {total_pages} ·", "adm_noop"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("بعدی ▶️", f"adm_users_{page + 1}_{sort}"))
        btn_rows.append(nav_row)

        other_sort = "seen" if sort == "events" else "events"
        other_label = "🕒 مرتب‌سازی بر اساس آخرین بازدید" if sort == "events" else "🔥 مرتب‌سازی بر اساس بیشترین مصرف"
        btn_rows.append([InlineKeyboardButton(other_label, f"adm_users_0_{other_sort}")])
        btn_rows.append([InlineKeyboardButton("🔙 بازگشت به پنل مدیریت", "adm_home")])
        kb = InlineKeyboard(btn_rows)

    await safe_edit_or_send(client, chat_id, msg, text, kb)


async def render_admin_user_detail(client: Client, chat_id: int, target_id: int, msg: Message = None):
    if not is_admin(chat_id):
        return

    doc = None
    recent_acts = []
    if db is not None:
        try:
            doc = db.users.find_one({"chat_id": int(target_id)})
            cursor = db.activity.find({"chat_id": int(target_id)}).sort("timestamp", -1).limit(5)
            recent_acts = list(cursor)
        except Exception:
            pass

    if not doc:
        text = f"❌ کاربری با شناسه `{target_id}` در دیتابیس یافت نشد."
        kb = InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به لیست کاربران", "adm_users_0_seen")]])
    else:
        name = doc.get("first_name") or "بی‌نام"
        username = doc.get("username") or ""
        uname_str = f"@{username}" if username else "ندارد"
        banned = doc.get("banned", False)
        status_str = "⛔️ مسدود شده" if banned else "✅ فعال و عادی"
        dls = doc.get("downloads", 0)
        evts = doc.get("events", 0)
        fs = doc.get("first_seen")
        fs_str = time.strftime("%Y/%m/%d %H:%M", time.localtime(fs)) if fs else "نامشخص"
        ls = doc.get("last_seen")
        ls_str = time.strftime("%Y/%m/%d %H:%M", time.localtime(ls)) if ls else "نامشخص"

        act_lines = []
        for a in recent_acts:
            t_str = time.strftime("%m/%d %H:%M", time.localtime(a.get("timestamp", time.time())))
            act_lines.append(f"• `[{t_str}]` {a.get('action')}: {str(a.get('details',''))[:25]}")
        if not act_lines:
            act_lines.append("_فعالیتی ثبت نشده_")

        text = (
            f"👤 **پروفایل و مدیریت کاربر:**\n\n"
            f"🏷 نام: **{name}**\n"
            f"🔗 یوزرنیم: **{uname_str}**\n"
            f"🆔 شناسه عددی: `{target_id}`\n"
            f"🚦 وضعیت حساب: **{status_str}**\n"
            f"⬇️ کل دانلودها: **{dls}** فایل\n"
            f"⚡️ کل رویدادها: **{evts}** درخواست\n"
            f"🕒 تاریخ عضویت: **{fs_str}**\n"
            f"🕒 آخرین حضور: **{ls_str}**\n\n"
            f"📜 **آخرین فعالیت‌ها:**\n" + "\n".join(act_lines)
        )

        ban_btn = (
            InlineKeyboardButton("✅ رفع مسدودی کاربر", f"adm_unban_{target_id}")
            if banned else
            InlineKeyboardButton("⛔️ مسدود کردن کاربر", f"adm_ban_{target_id}")
        )

        kb = InlineKeyboard([
            [ban_btn],
            [InlineKeyboardButton("✉️ ارسال پیام اختصاصی به کاربر", f"adm_pm_{target_id}")],
            [InlineKeyboardButton("🔙 بازگشت به لیست کاربران", "adm_users_0_seen")]
        ])

    await safe_edit_or_send(client, chat_id, msg, text, kb)


async def render_admin_activities(client: Client, chat_id: int, page: int = 0, msg: Message = None):
    if not is_admin(chat_id):
        return

    PAGE_SIZE = 10
    total = 0
    acts = []
    if db is not None:
        try:
            total = db.activity.count_documents({})
            total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
            page = max(0, min(page, total_pages - 1))
            acts = list(db.activity.find({}).sort("timestamp", -1).skip(page * PAGE_SIZE).limit(PAGE_SIZE))
        except Exception:
            pass

    if not acts:
        text = "📜 هنوز فعالیتی در سامانه ثبت نشده است."
        kb = InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به پنل", "adm_home")]])
    else:
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        lines = [f"📜 **جریان زنده فعالیت‌های ربات** (صفحه {page + 1} از {total_pages}):\n"]
        for a in acts:
            t_str = time.strftime("%H:%M:%S", time.localtime(a.get("timestamp", time.time())))
            u_id = a.get("chat_id")
            act = a.get("action", "activity")
            detail = str(a.get("details", ""))[:30]
            lines.append(f"• `[{t_str}]` `{u_id}` ➔ **{act}** ({detail})")

        text = "\n".join(lines)
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️ قبلی", f"adm_act_{page - 1}"))
        nav_row.append(InlineKeyboardButton(f"· {page + 1} / {total_pages} ·", "adm_noop"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("بعدی ▶️", f"adm_act_{page + 1}"))

        kb = InlineKeyboard([
            nav_row,
            [InlineKeyboardButton("🔄 به‌روزرسانی زنده", f"adm_act_{page}")],
            [InlineKeyboardButton("🔙 بازگشت به پنل مدیریت", "adm_home")]
        ])

    await safe_edit_or_send(client, chat_id, msg, text, kb)


# ----------------- ADMIN SUPPORT TICKETS -----------------
async def render_admin_support(client: Client, chat_id: int, page: int = 0, msg: Message = None):
    if not is_admin(chat_id):
        return
    PAGE_SIZE = 6
    total = 0
    docs = []
    if db is not None:
        try:
            total = db.support.count_documents({})
            total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
            page = max(0, min(page, total_pages - 1))
            docs = list(db.support.find({}).sort("created_at", -1).skip(page * PAGE_SIZE).limit(PAGE_SIZE))
        except Exception as e:
            print(f"support error: {e}", flush=True)

    if not docs:
        text = "✉️ هیچ تیکت یا پیام پشتیبانی ثبت نشده است."
        kb = InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به پنل", "adm_home")]])
    else:
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        text = f"✉️ **لیست تیکت‌های پشتیبانی** (صفحه {page + 1} از {total_pages}):\n\nبرای مشاهده متن پیام و ارسال پاسخ، روی تیکت کلیک کنید:\n"
        btn_rows = []
        for d in docs:
            t_id = str(d.get("_id", d.get("created_at", "")))
            u_id = d.get("chat_id", "")
            uname = d.get("username", "")
            ans = "✅ " if d.get("answered") else "📬 "
            name_label = f"@{uname}" if uname else f"ID: {u_id}"
            btn_rows.append([InlineKeyboardButton(f"{ans}{name_label}: {str(d.get('text', ''))[:20]}...", f"adm_view_sup_{t_id}")])

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️ قبلی", f"adm_sup_{page - 1}"))
        nav.append(InlineKeyboardButton(f"· {page + 1} / {total_pages} ·", "adm_noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("بعدی ▶️", f"adm_sup_{page + 1}"))
        btn_rows.append(nav)
        btn_rows.append([InlineKeyboardButton("🔙 بازگشت به پنل مدیریت", "adm_home")])
        kb = InlineKeyboard(btn_rows)

    await safe_edit_or_send(client, chat_id, msg, text, kb)


async def render_admin_support_detail(client: Client, chat_id: int, ticket_id: str, msg: Message = None):
    if not is_admin(chat_id):
        return
    doc = None
    if db is not None:
        try:
            from bson import ObjectId
            doc = db.support.find_one({"_id": ObjectId(ticket_id)})
        except Exception:
            pass
    if not doc:
        return await safe_edit_or_send(client, chat_id, msg, "❌ تیکت یافت نشد.", InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت", "adm_sup_0")]]))

    u_id = doc.get("chat_id")
    uname = doc.get("username")
    uname_str = f"@{uname}" if uname else "ثبت‌نشده"
    ans = "✅ پاسخ داده شده" if doc.get("answered") else "📬 در انتظار پاسخ"
    t_str = time.strftime("%Y/%m/%d %H:%M", time.localtime(doc.get("created_at", time.time())))

    text = (
        f"✉️ **مشخصات تیکت پشتیبانی:**\n\n"
        f"👤 فرستنده: `{u_id}` ({uname_str})\n"
        f"🕒 تاریخ ارسال: **{t_str}**\n"
        f"🚦 وضعیت: **{ans}**\n\n"
        f"📝 **متن پیام کاربر:**\n{doc.get('text', '')}"
    )

    kb = InlineKeyboard([
        [InlineKeyboardButton("✍️ ارسال پاسخ به این کاربر", f"adm_rep_{ticket_id}")],
        [InlineKeyboardButton("🔙 بازگشت به لیست تیکت‌ها", "adm_sup_0")]
    ])
    await safe_edit_or_send(client, chat_id, msg, text, kb)


# ----------------- USER PLAYLISTS SYSTEM -----------------
async def render_user_playlists(client: Client, chat_id: int, page: int = 0, msg: Message = None):
    PAGE_SIZE = 5
    playlists = []
    if db is not None:
        try:
            playlists = list(db.playlists.find({"chat_id": int(chat_id)}).sort("created_at", -1))
        except Exception as e:
            print(f"playlists read error: {e}", flush=True)
    if not playlists:
        playlists = USER_DATA.get(chat_id, {}).get("playlists", [])

    total = len(playlists)
    if total == 0:
        text = (
            "📃 **پلی‌لیست‌های شخصی شما:**\n\n"
            "شما هنوز هیچ پلی‌لیستی ایجاد نکرده‌اید!\n"
            "می‌توانید همین حالا اولین پلی‌لیست اختصاصی خود را بسازید:"
        )
        kb = InlineKeyboard([
            [InlineKeyboardButton("➕ ایجاد پلی‌لیست جدید", "pl_create")],
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]
        ])
    else:
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))
        chunk = playlists[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

        text = f"📃 **پلی‌لیست‌های اختصاصی شما** (صفحه {page + 1} از {total_pages}):\n\nبرای مشاهده یا پخش، روی پلی‌لیست کلیک کنید:"
        btn_rows = [
            [InlineKeyboardButton("➕ ایجاد پلی‌لیست جدید", "pl_create")]
        ]
        for p in chunk:
            p_id = str(p.get("_id", p.get("created_at", "")))
            name = p.get("name", "پلی‌لیست")
            cnt = len(p.get("tracks", []))
            btn_rows.append([InlineKeyboardButton(f"🎵 {name} ({cnt} آهنگ)", f"pl_view_{p_id}")])

        if total_pages > 1:
            nav = []
            if page > 0:
                nav.append(InlineKeyboardButton("◀️ قبلی", f"pl_pg_{page - 1}"))
            nav.append(InlineKeyboardButton(f"· {page + 1} / {total_pages} ·", "pl_noop"))
            if page < total_pages - 1:
                nav.append(InlineKeyboardButton("بعدی ▶️", f"pl_pg_{page + 1}"))
            btn_rows.append(nav)

        btn_rows.append([InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")])
        kb = InlineKeyboard(btn_rows)

    await safe_edit_or_send(client, chat_id, msg, text, kb)


async def render_playlist_detail(client: Client, chat_id: int, playlist_id: str, msg: Message = None):
    doc = None
    if db is not None:
        try:
            from bson import ObjectId
            doc = db.playlists.find_one({"_id": ObjectId(playlist_id)})
        except Exception:
            pass
    if not doc:
        for p in USER_DATA.get(chat_id, {}).get("playlists", []):
            if str(p.get("created_at")) == str(playlist_id):
                doc = p
                break

    if not doc:
        return await safe_edit_or_send(client, chat_id, msg, "❌ پلی‌لیست یافت نشد.", InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت", "menu_playlists")]]))

    name = doc.get("name", "پلی‌لیست")
    tracks = doc.get("tracks", [])
    cnt = len(tracks)

    text = (
        f"🎵 **پلی‌لیست:** {name}\n"
        f"🎶 تعداد کل آهنگ‌ها: **{cnt}**\n\n"
    )
    if tracks:
        text += "📜 **لیست ترک‌ها:**\n"
        for i, t in enumerate(tracks[:10], 1):
            text += f"{i}. {t.get('artist', '')} - {t.get('title', '')}\n"
        if cnt > 10:
            text += f"_... و {cnt - 10} آهنگ دیگر_\n"
    else:
        text += "_این پلی‌لیست در حال حاضر خالی است. هنگام جستجوی آهنگ، می‌توانید آن را به این لیست اضافه فرمایید._\n"

    btn_rows = []
    if tracks:
        btn_rows.append([
            InlineKeyboardButton("▶️ پخش و ارسال همه", f"pl_play_{playlist_id}"),
            InlineKeyboardButton("🔀 پخش تصادفی", f"pl_shuffle_{playlist_id}")
        ])
    btn_rows.append([
        InlineKeyboardButton("🗑 حذف این پلی‌لیست", f"pl_del_{playlist_id}"),
        InlineKeyboardButton("🔙 بازگشت به پلی‌لیست‌ها", "menu_playlists")
    ])
    kb = InlineKeyboard(btn_rows)
    await safe_edit_or_send(client, chat_id, msg, text, kb)


# ----------------- VIDEO EDITOR WIZARD -----------------
async def render_video_editor_home(client: Client, chat_id: int, msg: Message = None):
    src = USER_DATA.get(chat_id, {}).get("vedit_path")
    if not src or not os.path.exists(src):
        text = (
            "🎬 **ویرایشگر پیشرفته ویدیو (Video Editor):**\n\n"
            "هنوز ویدیویی برای ویرایش در سیستم بارگذاری نشده است.\n\n"
            "👇 **برای شروع:**\n"
            "• یک ویدیو به ربات ارسال کنید،\n"
            "• یا روی هر ویدیوی ارسال‌شده در ربات دکمه `[🎬 ویرایش]` را بزنید."
        )
        kb = InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]])
        return await safe_edit_or_send(client, chat_id, msg, text, kb)

    dur = await asyncio.to_thread(vtools.probe_duration_sync, src)
    dur_str = format_duration(int(dur))
    sz_mb = round(os.path.getsize(src) / (1024 * 1024), 1)

    text = (
        "🎬 **جعبه ابزار ویرایش ویدیو:**\n\n"
        f"⏱ مدت زمان: **{dur_str}** | حجم: **{sz_mb} MB**\n\n"
        "عملیات مورد نظر خود را انتخاب فرمایید:"
    )
    kb = vtools.video_editor_kb()
    await safe_edit_or_send(client, chat_id, msg, text, kb)


# ----------------- PEXELS IMAGE SEARCH -----------------
async def render_pexels_page(client: Client, chat_id: int, page: int = 1, msg: Message = None):
    query = USER_DATA.get(chat_id, {}).get("px_query", "wallpaper")
    photos = await asyncio.to_thread(soc.pexels_search_sync, query, page, 6)
    if not photos:
        text = f"❌ تصویری برای «{query}» در پکسلز یافت نشد."
        kb = InlineKeyboard([[InlineKeyboardButton("🔙 منوی اصلی", "menu_main")]])
        return await safe_edit_or_send(client, chat_id, msg, text, kb)

    USER_DATA.setdefault(chat_id, {})["px_results"] = photos
    text = f"🖼 **تصاویر با کیفیت پکسلز (Pexels): «{query}»**\nصفحه {page}\n\nبرای دریافت فایل با کیفیت اصلی، روی تصویر مورد نظر کلیک کنید:"

    btn_rows = []
    for i, p in enumerate(photos, 1):
        btn_rows.append([InlineKeyboardButton(f"📸 عکس شماره {i} (عکاس: {p['photographer'][:15]})", f"px_dl_{i - 1}")])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("◀️ قبلی", f"px_pg_{page - 1}"))
    nav.append(InlineKeyboardButton(f"صفحه {page}", "px_noop"))
    nav.append(InlineKeyboardButton("بعدی ▶️", f"px_pg_{page + 1}"))
    btn_rows.append(nav)
    btn_rows.append([InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")])

    kb = InlineKeyboard(btn_rows)
    await safe_edit_or_send(client, chat_id, msg, text, kb)


# ----------------- PINTEREST SEARCH -----------------
async def render_pinterest_page(client: Client, chat_id: int, page: int = 0, msg: Message = None):
    query = USER_DATA.get(chat_id, {}).get("pt_query", "aesthetic")
    pins = USER_DATA.get(chat_id, {}).get("pt_pins", [])
    if not pins:
        pins = await asyncio.to_thread(soc.pinterest_search_sync, query, 15)
        USER_DATA.setdefault(chat_id, {})["pt_pins"] = pins

    if not pins:
        text = f"❌ موردی برای «{query}» در پینترست یافت نشد."
        kb = InlineKeyboard([[InlineKeyboardButton("🔙 منوی اصلی", "menu_main")]])
        return await safe_edit_or_send(client, chat_id, msg, text, kb)

    PAGE_SIZE = 5
    total_pages = max(1, (len(pins) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = pins[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    text = f"📌 **نتایج پینترست: «{query}»** (صفحه {page + 1} از {total_pages})\nبرای دریافت، روی پین مورد نظر کلیک کنید:"
    btn_rows = []
    for idx_in_c, p in enumerate(chunk):
        real_idx = page * PAGE_SIZE + idx_in_c
        t_type = "🎬 ویدیو" if p.get("type") == "video" else "🖼 تصویر"
        t_name = p.get("title") or f"پین {real_idx + 1}"
        btn_rows.append([InlineKeyboardButton(f"{t_type}: {t_name[:25]}", f"pt_dl_{real_idx}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ قبلی", f"pt_pg_{page - 1}"))
    nav.append(InlineKeyboardButton(f"· {page + 1} / {total_pages} ·", "pt_noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("بعدی ▶️", f"pt_pg_{page + 1}"))
    btn_rows.append(nav)
    btn_rows.append([InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")])

    kb = InlineKeyboard(btn_rows)
    await safe_edit_or_send(client, chat_id, msg, text, kb)


# ----------------- REDDIT SEARCH -----------------
async def render_reddit_page(client: Client, chat_id: int, page: int = 0, msg: Message = None):
    query = USER_DATA.get(chat_id, {}).get("rd_query", "")
    posts = USER_DATA.get(chat_id, {}).get("rd_posts", [])
    if not posts:
        posts = await asyncio.to_thread(soc.reddit_search_sync, query, 15)
        USER_DATA.setdefault(chat_id, {})["rd_posts"] = posts

    if not posts:
        text = f"❌ پستی برای «{query}» در ردیت یافت نشد."
        kb = InlineKeyboard([[InlineKeyboardButton("🔙 منوی اصلی", "menu_main")]])
        return await safe_edit_or_send(client, chat_id, msg, text, kb)

    PAGE_SIZE = 5
    total_pages = max(1, (len(posts) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = posts[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    text = f"🐦 **نتایج ردیت: «{query}»** (صفحه {page + 1} از {total_pages}):"
    btn_rows = []
    for idx_in_c, p in enumerate(chunk):
        real_idx = page * PAGE_SIZE + idx_in_c
        p_id = p.get("id")
        t_title = p.get("title", "پست ردیت")[:28]
        sub = p.get("subreddit", "reddit")
        btn_rows.append([InlineKeyboardButton(f"r/{sub}: {t_title}", f"rd_dl_{p_id}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ قبلی", f"rd_pg_{page - 1}"))
    nav.append(InlineKeyboardButton(f"· {page + 1} / {total_pages} ·", "rd_noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("بعدی ▶️", f"rd_pg_{page + 1}"))
    btn_rows.append(nav)
    btn_rows.append([InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")])

    kb = InlineKeyboard(btn_rows)
    await safe_edit_or_send(client, chat_id, msg, text, kb)


# ----------------- GOOGLE IMAGES SEARCH -----------------
async def render_google_images_page(client: Client, chat_id: int, page: int = 0, msg: Message = None):
    query = USER_DATA.get(chat_id, {}).get("gi_query", "wallpaper")
    images = USER_DATA.get(chat_id, {}).get("gi_results", [])
    if not images:
        images = await asyncio.to_thread(soc.google_images_search_sync, query, 15)
        USER_DATA.setdefault(chat_id, {})["gi_results"] = images

    if not images:
        text = f"❌ تصویری برای «{query}» در گوگل یافت نشد."
        kb = InlineKeyboard([[InlineKeyboardButton("🔙 منوی اصلی", "menu_main")]])
        return await safe_edit_or_send(client, chat_id, msg, text, kb)

    PAGE_SIZE = 5
    total_pages = max(1, (len(images) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = images[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    text = f"🌐 **نتایج گوگل ایمیج: «{query}»** (صفحه {page + 1} از {total_pages})\nبرای دریافت فایل اصلی روی تصویر کلیک کنید:"
    btn_rows = []
    for idx_in_c, img_url in enumerate(chunk):
        real_idx = page * PAGE_SIZE + idx_in_c
        btn_rows.append([InlineKeyboardButton(f"🖼 دریافت تصویر شماره {real_idx + 1}", f"gi_dl_{real_idx}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ قبلی", f"gi_pg_{page - 1}"))
    nav.append(InlineKeyboardButton(f"· {page + 1} / {total_pages} ·", "gi_noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("بعدی ▶️", f"gi_pg_{page + 1}"))
    btn_rows.append(nav)
    btn_rows.append([InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")])

    kb = InlineKeyboard(btn_rows)
    await safe_edit_or_send(client, chat_id, msg, text, kb)


# ----------------- YOUTUBE HELPERS & INTERACTIVE WIZARD -----------------
def format_duration(seconds: int) -> str:
    if not seconds:
        return "00:00"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def calculate_yt_sizes(info: dict) -> dict:
    """Calculates estimated sizes for standard video and audio qualities in MB."""
    duration = info.get("duration") or 0
    formats = info.get("formats", [])

    audio_formats = [f for f in formats if f.get("vcodec") == "none" and f.get("acodec") != "none"]
    best_audio = max(audio_formats, key=lambda f: f.get("abr") or 0) if audio_formats else None
    audio_size = 0
    if best_audio:
        audio_size = best_audio.get("filesize") or best_audio.get("filesize_approx") or 0
    if not audio_size and duration:
        audio_size = int((128 * 1000 / 8) * duration)

    sizes = {}
    for h in [1080, 720, 480, 360, 240]:
        v_fmts = [f for f in formats if f.get("vcodec") != "none" and f.get("height") and f.get("height") <= h]
        best_v = max(v_fmts, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0)) if v_fmts else None
        if best_v:
            v_size = best_v.get("filesize") or best_v.get("filesize_approx") or 0
            if not v_size and best_v.get("tbr") and duration:
                v_size = int((best_v["tbr"] * 1000 / 8) * duration)
            tot_size = v_size + (audio_size if best_v.get("acodec") == "none" else 0)
            sizes[f"{h}p"] = round(tot_size / (1024 * 1024), 1)
        else:
            bitrates = {1080: 3500, 720: 1800, 480: 900, 360: 500, 240: 300}
            if duration:
                tot_bytes = ((bitrates[h] + 128) * 1000 / 8) * duration
                sizes[f"{h}p"] = round(tot_bytes / (1024 * 1024), 1)
            else:
                sizes[f"{h}p"] = 15.0

    if duration:
        sizes["320k"] = round(((320 * 1000 / 8) * duration) / (1024 * 1024), 1)
        sizes["128k"] = round(((128 * 1000 / 8) * duration) / (1024 * 1024), 1)
    else:
        sizes["320k"] = 8.5
        sizes["128k"] = 3.4

    return sizes


def search_youtube_channels(query: str, max_results: int = 15) -> list:
    """Search YouTube channels using YouTube search with sp=EgIQAg%3D%3D."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9"
    }
    url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}&sp=EgIQAg%3D%3D"
    channels = []
    try:
        r = requests.get(url, headers=headers, timeout=10)
        m = re.search(r'var ytInitialData = ({.*?});</script>', r.text)
        if not m:
            m = re.search(r'ytInitialData = ({.*?});</script>', r.text)
        if m:
            data = json.loads(m.group(1))
            contents = data.get("contents", {}).get("twoColumnSearchResultsRenderer", {}).get("primaryContents", {}).get("sectionListRenderer", {}).get("contents", [])
            for sec in contents:
                for it in sec.get("itemSectionRenderer", {}).get("contents", []):
                    ch = it.get("channelRenderer")
                    if ch:
                        ch_id = ch.get("channelId")
                        title = ch.get("title", {}).get("simpleText")
                        sub_text = ch.get("subscriberCountText", {}).get("simpleText")
                        if not sub_text and ch.get("videoCountText"):
                            sub_text = ch.get("videoCountText", {}).get("simpleText")
                        thumbs = ch.get("thumbnail", {}).get("thumbnails", [])
                        avatar = thumbs[-1].get("url") if thumbs else ""
                        if avatar.startswith("//"):
                            avatar = "https:" + avatar
                        if ch_id and title:
                            channels.append({
                                "id": ch_id,
                                "title": title,
                                "subs": sub_text or "تعداد دنبال‌کنندگان نامشخص",
                                "avatar": avatar
                            })
                            if len(channels) >= max_results:
                                break
    except Exception as e:
        print(f"Channel search error: {e}", flush=True)
    return channels


def search_youtube_videos(query: str, max_results: int = 25) -> list:
    """Search YouTube videos using yt-dlp flat extraction."""
    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": True,
        "extractor_args": {"youtube": {"player_client": ["android"]}}
    }
    videos = []
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            res = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
            for e in res.get("entries", []):
                v_id = e.get("id")
                title = e.get("title")
                dur = e.get("duration") or 0
                uploader = e.get("uploader") or e.get("channel") or "YouTube"
                views = e.get("view_count") or 0
                if v_id and title:
                    videos.append({
                        "id": v_id,
                        "title": title,
                        "duration": dur,
                        "duration_str": format_duration(dur),
                        "uploader": uploader,
                        "views": views
                    })
    except Exception as e:
        print(f"Video search error: {e}", flush=True)
    return videos


def fetch_channel_latest_videos(channel_id: str, max_results: int = 8) -> list:
    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": True,
        "playlist_items": f"1-{max_results}",
        "extractor_args": {"youtube": {"player_client": ["android"]}}
    }
    videos = []
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            res = ydl.extract_info(f"https://www.youtube.com/channel/{channel_id}/videos", download=False)
            for e in res.get("entries", []):
                v_id = e.get("id")
                title = e.get("title")
                dur = e.get("duration") or 0
                if v_id and title:
                    videos.append({
                        "id": v_id,
                        "title": title,
                        "duration": dur,
                        "duration_str": format_duration(dur)
                    })
    except Exception as e:
        print(f"Channel videos error: {e}", flush=True)
    return videos


def fetch_video_details(video_url_or_id: str, cached_entry: dict = None) -> dict:
    if not video_url_or_id.startswith("http"):
        video_url = f"https://www.youtube.com/watch?v={video_url_or_id}"
        v_id = video_url_or_id
    else:
        video_url = video_url_or_id
        m = re.search(r'(?:v=|\/|youtu\.be\/)([a-zA-Z0-9_-]{11})', video_url_or_id)
        v_id = m.group(1) if m else ""

    title = (cached_entry or {}).get("title")
    uploader = (cached_entry or {}).get("uploader")
    duration = (cached_entry or {}).get("duration") or 0
    views = (cached_entry or {}).get("views") or 0
    thumbnail = (cached_entry or {}).get("thumbnail") or (f"https://i.ytimg.com/vi/{v_id}/hqdefault.jpg" if v_id else "")
    description = (cached_entry or {}).get("description") or ""

    # If title or uploader missing, query YouTube oEmbed (instant, never blocked)
    if not title or not uploader:
        try:
            r = requests.get(f"https://www.youtube.com/oembed?url={video_url}&format=json", timeout=5)
            if r.status_code == 200:
                data = r.json()
                title = title or data.get("title")
                uploader = uploader or data.get("author_name")
                thumbnail = thumbnail or data.get("thumbnail_url")
        except Exception:
            pass

    # Optional yt-dlp probe without cookies if duration is still 0
    if not duration:
        try:
            ydl_opts = {
                "quiet": True,
                "skip_download": True,
                "extract_flat": True,
                "extractor_args": {"youtube": {"player_client": ["android"]}}
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
                if info:
                    duration = info.get("duration") or duration
                    views = info.get("view_count") or views
                    if not title:
                        title = info.get("title")
                    if not uploader:
                        uploader = info.get("uploader") or info.get("channel")
        except Exception:
            pass

    title = title or "YouTube Video"
    uploader = uploader or "YouTube"
    duration = duration or 300
    duration_str = format_duration(duration)
    sizes = calculate_yt_sizes({"duration": duration, "formats": []})

    return {
        "url": f"https://www.youtube.com/watch?v={v_id}" if v_id else video_url,
        "video_id": v_id,
        "title": title,
        "uploader": uploader,
        "duration": duration,
        "duration_str": duration_str,
        "views": views,
        "thumbnail": thumbnail,
        "description": description[:500],
        "sizes": sizes
    }


async def render_yt_channels_page(client: Client, chat_id: int, page: int = 0, msg: Message = None):
    data = USER_DATA.get(chat_id, {})
    channels = data.get("yt_channels", [])
    query = data.get("yt_ch_query", "")

    if not channels:
        text = f"❌ کانالی برای «{query}» در یوتیوب یافت نشد."
        kb = InlineKeyboard([[InlineKeyboardButton("🔙 منوی یوتیوب", "menu_youtube")]])
        await safe_edit_or_send(client, chat_id, msg, text, kb)
        return

    PAGE_SIZE = 5
    total_pages = max(1, (len(channels) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start_idx = page * PAGE_SIZE
    chunk = channels[start_idx : start_idx + PAGE_SIZE]

    text = (
        f"👤 **کانال‌های یوتیوب برای «{query}»** (صفحه {page + 1} از {total_pages}):\n\n"
        "کانال مورد نظر خود را انتخاب کنید:"
    )

    btn_rows = []
    for idx_in_chunk, ch in enumerate(chunk):
        real_idx = start_idx + idx_in_chunk
        t = ch.get("title", "کانال")[:25]
        s = ch.get("subs", "")
        btn_rows.append([InlineKeyboardButton(f"👤 {t} ({s})", f"yt_ch_sel_{real_idx}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ قبلی", f"yt_ch_pg_{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"· {page + 1} / {total_pages} ·", "adm_noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("بعدی ▶️", f"yt_ch_pg_{page + 1}"))
    btn_rows.append(nav_row)
    btn_rows.append([InlineKeyboardButton("🔙 بازگشت به منوی یوتیوب", "menu_youtube")])

    kb = InlineKeyboard(btn_rows)
    await safe_edit_or_send(client, chat_id, msg, text, kb)


async def render_yt_channel_detail(client: Client, chat_id: int, channel_idx: int, msg: Message = None):
    data = USER_DATA.get(chat_id, {})
    channels = data.get("yt_channels", [])
    if channel_idx < 0 or channel_idx >= len(channels):
        await render_yt_channels_page(client, chat_id, 0, msg)
        return

    ch = channels[channel_idx]
    ch_id = ch["id"]
    ch_title = ch["title"]
    ch_subs = ch["subs"]
    avatar_url = ch.get("avatar")

    status_msg = await safe_edit_or_send(client, chat_id, msg, f"⏳ در حال بارگذاری ویدیوهای کانال **{ch_title}**...")

    vids = await asyncio.to_thread(fetch_channel_latest_videos, ch_id, 8)
    USER_DATA.setdefault(chat_id, {})["yt_ch_videos"] = vids

    caption = (
        f"👤 **کانال یوتیوب: {ch_title}**\n"
        f"👥 مشترکین: **{ch_subs}**\n\n"
        "🎬 **جدیدترین ویدیوهای کانال:**\n"
        "برای انتخاب و دریافت، روی ویدیوی مورد نظر کلیک کنید:"
    )

    btn_rows = []
    for v in vids:
        v_title = v.get("title", "")[:32]
        d_str = v.get("duration_str", "")
        btn_rows.append([InlineKeyboardButton(f"🎬 {v_title}... ({d_str})", f"yt_vid_{v['id']}")])

    btn_rows.append([InlineKeyboardButton("🔙 بازگشت به لیست کانال‌ها", "yt_ch_back")])
    btn_rows.append([InlineKeyboardButton("🔙 منوی یوتیوب", "menu_youtube")])
    kb = InlineKeyboard(btn_rows)

    sent_photo = False
    if avatar_url:
        try:
            r = await asyncio.to_thread(requests.get, avatar_url, timeout=6)
            if r.status_code == 200:
                tmp_p = os.path.join(MEDIA_DIR, f"ch_{chat_id}_{int(time.time())}.jpg")
                with open(tmp_p, "wb") as f:
                    f.write(r.content)
                with open(tmp_p, "rb") as f:
                    await client.send_photo(chat_id, photo=f, caption=caption, reply_markup=kb)
                sent_photo = True
                if status_msg:
                    await status_msg.delete()
                if os.path.exists(tmp_p):
                    os.remove(tmp_p)
        except Exception as e:
            print(f"Channel photo error: {e}", flush=True)

    if not sent_photo:
        await safe_edit_or_send(client, chat_id, status_msg, caption, kb)


async def render_yt_keyword_page(client: Client, chat_id: int, page: int = 0, msg: Message = None):
    data = USER_DATA.get(chat_id, {})
    raw_results = data.get("yt_kw_results", [])
    query = data.get("yt_kw_query", "")
    filters = data.get("yt_kw_filters", {"dur": "all", "sort": "rel"})

    if not raw_results:
        text = f"❌ ویدیویی برای «{query}» در یوتیوب یافت نشد."
        kb = InlineKeyboard([[InlineKeyboardButton("🔙 منوی یوتیوب", "menu_youtube")]])
        await safe_edit_or_send(client, chat_id, msg, text, kb)
        return

    dur_f = filters.get("dur", "all")
    filtered = []
    for v in raw_results:
        dur = v.get("duration", 0)
        if dur_f == "short" and dur >= 240:
            continue
        elif dur_f == "med" and not (240 <= dur <= 1200):
            continue
        elif dur_f == "long" and dur <= 1200:
            continue
        filtered.append(v)

    sort_f = filters.get("sort", "rel")
    if sort_f == "views":
        filtered.sort(key=lambda x: x.get("views", 0), reverse=True)
    elif sort_f == "dur":
        filtered.sort(key=lambda x: x.get("duration", 0), reverse=True)

    if not filtered:
        text = "❌ نتیجه‌ای با فیلترهای انتخابی یافت نشد.\nمی‌توانید فیلترها را ریست کنید:"
        kb = InlineKeyboard([
            [InlineKeyboardButton("🔄 ریست فیلترها", "yt_filt_dur_all")],
            [InlineKeyboardButton("🔙 منوی یوتیوب", "menu_youtube")]
        ])
        await safe_edit_or_send(client, chat_id, msg, text, kb)
        return

    PAGE_SIZE = 5
    total_pages = max(1, (len(filtered) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start_idx = page * PAGE_SIZE
    chunk = filtered[start_idx : start_idx + PAGE_SIZE]

    dur_labels = {"all": "همه", "short": "کوتاه (<4m)", "med": "متوسط (4-20m)", "long": "طولانی (>20m)"}
    sort_labels = {"rel": "مرتبط‌ترین", "views": "پربازدیدترین", "dur": "طولانی‌ترین"}

    text = (
        f"🔍 **نتایج جستجوی یوتیوب: «{query}»**\n"
        f"📄 صفحه {page + 1} از {total_pages} (کل: {len(filtered)} ویدیو)\n"
        f"⏱ فیلتر زمان: **{dur_labels.get(dur_f, dur_f)}** | مرتب‌سازی: **{sort_labels.get(sort_f, sort_f)}**\n\n"
        "ویدیوی مورد نظر خود را برای دانلود انتخاب کنید:"
    )

    btn_rows = []
    for idx_in_chunk, v in enumerate(chunk):
        real_num = start_idx + idx_in_chunk + 1
        t = v.get("title", "")[:30]
        d = v.get("duration_str", "")
        btn_rows.append([InlineKeyboardButton(f"{real_num}️⃣ {t}... ({d})", f"yt_vid_{v['id']}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ قبلی", f"yt_kw_pg_{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"· {page + 1} / {total_pages} ·", "adm_noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("بعدی ▶️", f"yt_kw_pg_{page + 1}"))
    btn_rows.append(nav_row)

    btn_rows.append([InlineKeyboardButton("⚙️ فیلترها (مدت زمان و مرتب‌سازی)", "yt_kw_filters_menu")])
    btn_rows.append([InlineKeyboardButton("🔙 منوی یوتیوب", "menu_youtube")])

    kb = InlineKeyboard(btn_rows)
    await safe_edit_or_send(client, chat_id, msg, text, kb)


async def render_yt_video_card(client: Client, chat_id: int, video_url_or_id: str, msg: Message = None):
    data = USER_DATA.get(chat_id, {})
    cached = None
    v_clean = video_url_or_id
    if "youtube.com" in video_url_or_id or "youtu.be" in video_url_or_id:
        m = re.search(r'(?:v=|\/|youtu\.be\/)([a-zA-Z0-9_-]{11})', video_url_or_id)
        if m:
            v_clean = m.group(1)

    for item in data.get("yt_kw_results", []):
        if item.get("id") == v_clean:
            cached = item
            break
    if not cached:
        for item in data.get("yt_ch_videos", []):
            if item.get("id") == v_clean:
                cached = item
                break

    status_msg = await safe_edit_or_send(client, chat_id, msg, "⏳ در حال دریافت مشخصات ویدیو و محاسبه حجم کیفیت‌ها...")

    try:
        info = await asyncio.to_thread(fetch_video_details, video_url_or_id, cached)
    except Exception as e:
        traceback.print_exc()
        await safe_edit_or_send(client, chat_id, status_msg, f"❌ خطا در استخراج اطلاعات ویدیو: {e}")
        return

    USER_DATA.setdefault(chat_id, {})["yt_pending"] = {
        "url": info["url"],
        "video_id": info["video_id"],
        "title": info["title"],
        "uploader": info["uploader"],
        "duration": info["duration"],
        "duration_str": info["duration_str"],
        "views": info["views"],
        "thumbnail": info["thumbnail"],
        "description": info["description"],
        "sizes": info["sizes"],
        "format": "mp4",
        "quality": "720p",
        "extras": {
            "subtitles": False,
            "thumbnail": False,
            "description": False,
            "comments": False
        }
    }

    views_str = f"{info['views']:,}" if info['views'] else "نامشخص"
    caption = (
        f"🎬 **{info['title']}**\n\n"
        f"👤 کانال: **{info['uploader']}**\n"
        f"⏱ مدت زمان: **{info['duration_str']}**\n"
        f"👁 تعداد بازدید: **{views_str}**\n\n"
        "لطفاً نوع فرمت مورد نظر خود را انتخاب کنید:"
    )

    kb = InlineKeyboard([
        [
            InlineKeyboardButton("🎬 دانلود ویدیو (MP4)", "yt_choose_mp4"),
            InlineKeyboardButton("🎵 استخراج صوت (MP3)", "yt_choose_mp3")
        ],
        [InlineKeyboardButton("❌ انصراف", "menu_youtube")]
    ])

    sent_photo = False
    thumb_url = info.get("thumbnail")
    if thumb_url:
        try:
            r = await asyncio.to_thread(requests.get, thumb_url, timeout=6)
            if r.status_code == 200:
                tmp_p = os.path.join(MEDIA_DIR, f"yt_thumb_{chat_id}_{int(time.time())}.jpg")
                with open(tmp_p, "wb") as f:
                    f.write(r.content)
                with open(tmp_p, "rb") as f:
                    await client.send_photo(chat_id, photo=f, caption=caption, reply_markup=kb)
                sent_photo = True
                if status_msg:
                    await status_msg.delete()
                if os.path.exists(tmp_p):
                    os.remove(tmp_p)
        except Exception as e:
            print(f"Thumbnail card error: {e}", flush=True)

    if not sent_photo:
        await safe_edit_or_send(client, chat_id, status_msg, caption, kb)


async def render_yt_quality_selector(client: Client, chat_id: int, msg: Message = None):
    pending = USER_DATA.get(chat_id, {}).get("yt_pending", {})
    if not pending:
        await safe_edit_or_send(client, chat_id, msg, "❌ درخواست منقضی شده است.", InlineKeyboard([[InlineKeyboardButton("🔙 منوی یوتیوب", "menu_youtube")]]))
        return

    fmt = pending.get("format", "mp4")
    title = pending.get("title", "ویدیو")
    sizes = pending.get("sizes", {})

    if fmt == "mp4":
        text = (
            f"🎬 **انتخاب کیفیت ویدیوی MP4:**\n\n"
            f"📌 عنوان: **{title}**\n\n"
            "کیفیت مورد نظر را بر اساس حجم تخمینی انتخاب کنید:"
        )
        s_1080 = sizes.get("1080p", "70")
        s_720 = sizes.get("720p", "45")
        s_480 = sizes.get("480p", "25")
        s_360 = sizes.get("360p", "15")
        s_240 = sizes.get("240p", "8")

        kb = InlineKeyboard([
            [InlineKeyboardButton(f"1080p FHD (~{s_1080} MB)", "yt_set_qual_1080p")],
            [InlineKeyboardButton(f"720p HD (~{s_720} MB)", "yt_set_qual_720p")],
            [InlineKeyboardButton(f"480p SD (~{s_480} MB)", "yt_set_qual_480p")],
            [InlineKeyboardButton(f"360p (~{s_360} MB)", "yt_set_qual_360p"), InlineKeyboardButton(f"240p (~{s_240} MB)", "yt_set_qual_240p")],
            [InlineKeyboardButton("🔙 بازگشت به انتخاب فرمت", "yt_card_back")]
        ])
    else:
        text = (
            f"🎵 **انتخاب کیفیت فایل صوتی MP3:**\n\n"
            f"📌 عنوان: **{title}**\n\n"
            "کیفیت صوتی مورد نظر را انتخاب کنید:"
        )
        s_320 = sizes.get("320k", "12")
        s_128 = sizes.get("128k", "5")

        kb = InlineKeyboard([
            [InlineKeyboardButton(f"320kbps بالاترین کیفیت (~{s_320} MB)", "yt_set_qual_320k")],
            [InlineKeyboardButton(f"128kbps کیفیت استاندارد (~{s_128} MB)", "yt_set_qual_128k")],
            [InlineKeyboardButton("🔙 بازگشت به انتخاب فرمت", "yt_card_back")]
        ])

    await safe_edit_or_send(client, chat_id, msg, text, kb)


async def render_yt_extras_screen(client: Client, chat_id: int, msg: Message = None):
    pending = USER_DATA.get(chat_id, {}).get("yt_pending", {})
    if not pending:
        await safe_edit_or_send(client, chat_id, msg, "❌ درخواست منقضی شده است.", InlineKeyboard([[InlineKeyboardButton("🔙 منوی یوتیوب", "menu_youtube")]]))
        return

    fmt = pending.get("format", "mp4")
    qual = pending.get("quality", "720p")
    title = pending.get("title", "رسانه")
    extras = pending.get("extras", {})
    sizes = pending.get("sizes", {})
    est_sz = sizes.get(qual, sizes.get("720p" if fmt == "mp4" else "320k", "نامشخص"))
    slice_mb = get_video_slice_limit_mb()

    text = (
        f"⚙️ **تنظیمات و پیوست‌های دانلودی:**\n\n"
        f"🎬 اثر: **{title}**\n"
        f"🎯 فرمت: **{fmt.upper()}** | کیفیت: **{qual}** (~{est_sz} MB)\n"
        f"✂️ سقف هر پارت ارسال در بله: **{slice_mb} مگابایت**\n\n"
        "موارد دلخواه را با کلیک روی آنها فعال یا غیرفعال کنید:\n"
        "*(پیوست‌های انتخاب‌شده قبل از فایل اصلی برای شما ارسال خواهند شد)*"
    )

    sub_chk = "✅" if extras.get("subtitles") else "❌"
    thumb_chk = "✅" if extras.get("thumbnail") else "❌"
    desc_chk = "✅" if extras.get("description") else "❌"
    comm_chk = "✅" if extras.get("comments") else "❌"

    kb = InlineKeyboard([
        [InlineKeyboardButton(f"{sub_chk} 📝 دریافت زیرنویس (Subtitle)", "yt_tog_sub")],
        [InlineKeyboardButton(f"{thumb_chk} 🖼 تصویر کاور اصلی با کیفیت بالا", "yt_tog_thumb")],
        [InlineKeyboardButton(f"{desc_chk} 📄 توضیحات کامل ویدیو (Description)", "yt_tog_desc")],
        [InlineKeyboardButton(f"{comm_chk} 💬 برترین نظرات کاربران (Comments)", "yt_tog_comm")],
        [InlineKeyboardButton("🚀 تأیید نهایی و شروع دانلود", "yt_confirm_dl")],
        [InlineKeyboardButton("🔙 تغییر کیفیت", f"yt_choose_{fmt}")],
        [InlineKeyboardButton("❌ انصراف", "menu_youtube")]
    ])

    await safe_edit_or_send(client, chat_id, msg, text, kb)


# ----------------- SPOTIFY MUSIC RENDERERS -----------------
async def render_spotify_home(client: Client, chat_id: int, msg: Message = None):
    USER_STATES[chat_id] = "AWAIT_SP_LINK_OR_QUERY"
    sp_locked = is_playlist_locked("spotify")
    pl_label = "🔒 پلی‌لیست‌ها (غیرفعال)" if sp_locked else "📑 پلی‌لیست‌ها (Playlists)"
    text = (
        "🟢 **اسپاتیفای (Spotify Music):**\n\n"
        "شما می‌توانید لینک هر آهنگ، آلبوم یا پلی‌لیست اسپاتیفای را ارسال کنید، "
        "یا از طریق دکمه‌های زیر جستجو فرمایید:"
    )
    kb = InlineKeyboard([
        [
            InlineKeyboardButton("🎵 جستجوی آهنگ (Tracks)", "sp_search_track"),
            InlineKeyboardButton("👤 جستجوی خواننده (Artists)", "sp_search_artist")
        ],
        [
            InlineKeyboardButton("💿 جستجوی آلبوم (Albums)", "sp_search_album"),
            InlineKeyboardButton("🔥 داغ‌ترین‌های هفته (Hottest)", "sp_hottest")
        ],
        [InlineKeyboardButton(pl_label, "sp_search_playlist")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]
    ])
    await safe_edit_or_send(client, chat_id, msg, text, kb)


async def render_sp_tracks_page(client: Client, chat_id: int, page: int = 0, msg: Message = None):
    tracks = USER_DATA.get(chat_id, {}).get("sp_track_results", [])
    query = USER_DATA.get(chat_id, {}).get("sp_track_query", "موزیک")
    if not tracks:
        await safe_edit_or_send(
            client, chat_id, msg,
            f"❌ نتیجه‌ای برای «{query}» در اسپاتیفای یافت نشد.",
            InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به اسپاتیفای", "menu_music_spotify")]])
        )
        return

    per_page = 5
    total_pages = max(1, math.ceil(len(tracks) / per_page))
    page = max(0, min(page, total_pages - 1))
    start_idx = page * per_page
    page_tracks = tracks[start_idx:start_idx + per_page]

    text = (
        f"🟢 **نتایج جستجوی آهنگ در اسپاتیفای:**\n"
        f"🔍 عبارت: **{query}** | صفحه {page + 1} از {total_pages}\n\n"
        "برای مشاهده جزییات و کیفیت دانلود، روی آهنگ مورد نظر کلیک کنید:"
    )

    rows = []
    for idx, t in enumerate(page_tracks, start=start_idx + 1):
        dur = rega_music.format_duration(t.get("duration", 0))
        btn_title = f"▶️ {idx}. {t['title'][:25]} - {t['artist'][:15]} ({dur})"
        rows.append([InlineKeyboardButton(btn_title, f"sp_show_trk_{t['id']}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ قبلی", f"sp_trk_pg_{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", "sp_noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("بعدی ▶️", f"sp_trk_pg_{page + 1}"))
    rows.append(nav_row)

    rows.append([InlineKeyboardButton("🔙 بازگشت به منوی اسپاتیفای", "menu_music_spotify")])
    await safe_edit_or_send(client, chat_id, msg, text, InlineKeyboard(rows))


async def render_sp_track_card(client: Client, chat_id: int, track_id: str, msg: Message = None):
    t = rega_music.get_track_info(track_id)
    if not t:
        tracks = USER_DATA.get(chat_id, {}).get("sp_track_results", [])
        for item in tracks:
            if str(item.get("id")) == str(track_id):
                t = item
                break
    if not t:
        await safe_edit_or_send(
            client, chat_id, msg, "❌ اطلاعات آهنگ یافت نشد.",
            InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت", "sp_trk_pg_0")]])
        )
        return

    USER_DATA.setdefault(chat_id, {})["sp_selected_track"] = t
    dur = rega_music.format_duration(t.get("duration", 0))
    rel_date = t.get("release_date") or "ثبت‌نشده"
    album_name = t.get("album") or "تک‌آهنگ (Single)"

    text = (
        "🎵 **مشخصات ترک صوتی اسپاتیفای:**\n\n"
        f"🎧 عنوان: **{t['title']}**\n"
        f"👤 خواننده: **{t['artist']}**\n"
        f"💿 آلبوم: **{album_name}**\n"
        f"⏱ مدت زمان: **{dur}**\n"
        f"📅 تاریخ انتشار: **{rel_date}**\n\n"
        "کیفیت مورد نظر خود را برای دانلود انتخاب فرمایید:"
    )

    kb = InlineKeyboard([
        [
            InlineKeyboardButton("📥 کیفیت عالی (320 kbps)", f"sp_dl_320_{track_id}"),
            InlineKeyboardButton("📥 کیفیت خوب (128 kbps)", f"sp_dl_128_{track_id}")
        ],
        [InlineKeyboardButton("🔙 بازگشت به نتایج آهنگ‌ها", "sp_trk_pg_curr")]
    ])
    await safe_edit_or_send(client, chat_id, msg, text, kb)


async def render_sp_albums_page(client: Client, chat_id: int, page: int = 0, msg: Message = None):
    albums = USER_DATA.get(chat_id, {}).get("sp_album_results", [])
    query = USER_DATA.get(chat_id, {}).get("sp_album_query", "آلبوم")
    if not albums:
        await safe_edit_or_send(
            client, chat_id, msg,
            f"❌ آلبومی برای «{query}» در اسپاتیفای یافت نشد.",
            InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به اسپاتیفای", "menu_music_spotify")]])
        )
        return

    per_page = 5
    total_pages = max(1, math.ceil(len(albums) / per_page))
    page = max(0, min(page, total_pages - 1))
    start_idx = page * per_page
    page_albs = albums[start_idx:start_idx + per_page]

    text = (
        f"💿 **نتایج جستجوی آلبوم در اسپاتیفای:**\n"
        f"🔍 عبارت: **{query}** | صفحه {page + 1} از {total_pages}\n\n"
        "آلبوم مورد نظر را انتخاب کنید:"
    )

    rows = []
    for idx, a in enumerate(page_albs, start=start_idx + 1):
        nb = a.get("nb_tracks", 0)
        btn_title = f"💿 {idx}. {a['title'][:25]} - {a['artist'][:15]} ({nb} ترک)"
        rows.append([InlineKeyboardButton(btn_title, f"sp_show_alb_{a['id']}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ قبلی", f"sp_alb_pg_{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", "sp_noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("بعدی ▶️", f"sp_alb_pg_{page + 1}"))
    rows.append(nav_row)

    rows.append([InlineKeyboardButton("🔙 بازگشت به منوی اسپاتیفای", "menu_music_spotify")])
    await safe_edit_or_send(client, chat_id, msg, text, InlineKeyboard(rows))


async def render_sp_album_detail(client: Client, chat_id: int, album_id: str, page: int = 0, msg: Message = None):
    alb = rega_music.get_album_details(album_id)
    if not alb:
        await safe_edit_or_send(
            client, chat_id, msg, "❌ اطلاعات آلبوم دریافت نشد.",
            InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت", "sp_alb_pg_0")]])
        )
        return

    USER_DATA.setdefault(chat_id, {})["sp_selected_album"] = alb
    tracks = alb.get("tracks", [])
    per_page = 6
    total_pages = max(1, math.ceil(len(tracks) / per_page))
    page = max(0, min(page, total_pages - 1))
    start_idx = page * per_page
    page_trks = tracks[start_idx:start_idx + per_page]

    text = (
        f"💿 **آلبوم:** {alb['title']}\n"
        f"👤 **خواننده:** {alb['artist']}\n"
        f"🎶 **تعداد ترک‌ها:** {alb['nb_tracks']} آهنگ\n"
        f"📅 **تاریخ انتشار:** {alb.get('release_date', 'ثبت‌نشده')}\n\n"
        "می‌توانید کل آلبوم را به صورت یکجا دریافت کنید یا هر آهنگ را جداگانه انتخاب نمایید:"
    )

    rows = [
        [InlineKeyboardButton(f"📥 دانلود کل آلبوم ({alb['nb_tracks']} ترک - 320k)", f"sp_dl_alb_all_{album_id}")]
    ]

    for t in page_trks:
        dur = rega_music.format_duration(t.get("duration", 0))
        rows.append([InlineKeyboardButton(f"▶️ {t['track_number']}. {t['title']} ({dur})", f"sp_show_trk_{t['id']}")])

    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️ قبلی", f"sp_alb_trks_{album_id}_{page - 1}"))
        nav.append(InlineKeyboardButton(f"صفحه {page + 1}/{total_pages}", "sp_noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("بعدی ▶️", f"sp_alb_trks_{album_id}_{page + 1}"))
        rows.append(nav)

    rows.append([InlineKeyboardButton("🔙 بازگشت به لیست آلبوم‌ها", "sp_alb_pg_curr")])
    await safe_edit_or_send(client, chat_id, msg, text, InlineKeyboard(rows))


async def render_sp_artists_page(client: Client, chat_id: int, page: int = 0, msg: Message = None):
    artists = USER_DATA.get(chat_id, {}).get("sp_artist_results", [])
    query = USER_DATA.get(chat_id, {}).get("sp_artist_query", "هنرمند")
    if not artists:
        await safe_edit_or_send(
            client, chat_id, msg,
            f"❌ هنرمندی برای «{query}» در اسپاتیفای یافت نشد.",
            InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به اسپاتیفای", "menu_music_spotify")]])
        )
        return

    per_page = 5
    total_pages = max(1, math.ceil(len(artists) / per_page))
    page = max(0, min(page, total_pages - 1))
    start_idx = page * per_page
    page_arts = artists[start_idx:start_idx + per_page]

    text = (
        f"👤 **نتایج جستجوی خواننده در اسپاتیفای:**\n"
        f"🔍 عبارت: **{query}** | صفحه {page + 1} از {total_pages}\n\n"
        "خواننده مورد نظر را انتخاب کنید:"
    )

    rows = []
    for idx, art in enumerate(page_arts, start=start_idx + 1):
        fans = art.get("fans", 0)
        btn_title = f"👤 {idx}. {art['name'][:25]} ({fans:,} طرفدار)"
        rows.append([InlineKeyboardButton(btn_title, f"sp_show_art_{art['id']}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ قبلی", f"sp_art_pg_{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", "sp_noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("بعدی ▶️", f"sp_art_pg_{page + 1}"))
    rows.append(nav_row)

    rows.append([InlineKeyboardButton("🔙 بازگشت به منوی اسپاتیفای", "menu_music_spotify")])
    await safe_edit_or_send(client, chat_id, msg, text, InlineKeyboard(rows))


async def render_sp_artist_detail(client: Client, chat_id: int, artist_id: str, msg: Message = None):
    art = None
    for a in USER_DATA.get(chat_id, {}).get("sp_artist_results", []):
        if str(a.get("id")) == str(artist_id):
            art = a
            break
    art_name = art.get("name") if art else "خواننده"

    text = (
        f"👤 **هنرمند:** {art_name}\n"
        f"👥 تعداد هواداران: **{art.get('fans', 0):,} نفر**\n\n"
        "بخش مورد نظر را انتخاب نمایید:"
    )

    kb = InlineKeyboard([
        [InlineKeyboardButton("🎵 برترین آهنگ‌های این خواننده", f"sp_art_trks_{artist_id}")],
        [InlineKeyboardButton("💿 آلبوم‌های رسمی این خواننده", f"sp_art_albs_{artist_id}")],
        [InlineKeyboardButton("🔙 بازگشت به لیست خوانندگان", "sp_art_pg_curr")]
    ])
    await safe_edit_or_send(client, chat_id, msg, text, kb)


async def render_sp_playlists_page(client: Client, chat_id: int, page: int = 0, msg: Message = None):
    pls = USER_DATA.get(chat_id, {}).get("sp_playlist_results", [])
    query = USER_DATA.get(chat_id, {}).get("sp_playlist_query", "پلی‌لیست")
    if not pls:
        await safe_edit_or_send(
            client, chat_id, msg,
            f"❌ پلی‌لیستی برای «{query}» یافت نشد.",
            InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به اسپاتیفای", "menu_music_spotify")]])
        )
        return

    per_page = 5
    total_pages = max(1, math.ceil(len(pls) / per_page))
    page = max(0, min(page, total_pages - 1))
    start_idx = page * per_page
    page_pls = pls[start_idx:start_idx + per_page]

    text = (
        f"📑 **نتایج جستجوی پلی‌لیست اسپاتیفای:**\n"
        f"🔍 عبارت: **{query}** | صفحه {page + 1} از {total_pages}\n\n"
        "پلی‌لیست مورد نظر را انتخاب کنید:"
    )
    rows = []
    for idx, pl in enumerate(page_pls, start=start_idx + 1):
        nb = pl.get("nb_tracks", 0)
        btn_title = f"📑 {idx}. {pl['title'][:25]} ({nb} ترک)"
        rows.append([InlineKeyboardButton(btn_title, f"sp_show_pl_{pl['id']}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ قبلی", f"sp_pl_pg_{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", "sp_noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("بعدی ▶️", f"sp_pl_pg_{page + 1}"))
    rows.append(nav_row)

    rows.append([InlineKeyboardButton("🔙 بازگشت به منوی اسپاتیفای", "menu_music_spotify")])
    await safe_edit_or_send(client, chat_id, msg, text, InlineKeyboard(rows))


async def render_sp_playlist_detail(client: Client, chat_id: int, playlist_id: str, page: int = 0, msg: Message = None):
    pl = rega_music.get_playlist_details(playlist_id)
    if not pl:
        await safe_edit_or_send(
            client, chat_id, msg, "❌ اطلاعات پلی‌لیست دریافت نشد.",
            InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت", "sp_pl_pg_0")]])
        )
        return

    USER_DATA.setdefault(chat_id, {})["sp_selected_playlist"] = pl
    tracks = pl.get("tracks", [])
    per_page = 6
    total_pages = max(1, math.ceil(len(tracks) / per_page))
    page = max(0, min(page, total_pages - 1))
    start_idx = page * per_page
    page_trks = tracks[start_idx:start_idx + per_page]

    text = (
        f"📑 **پلی‌لیست:** {pl['title']}\n"
        f"👤 **سازنده:** {pl['creator']}\n"
        f"🎶 **تعداد ترک‌ها:** {pl['nb_tracks']} آهنگ\n\n"
        "می‌توانید کل پلی‌لیست را یکجا دریافت کنید یا هر آهنگ را جداگانه انتخاب نمایید:"
    )

    rows = [
        [InlineKeyboardButton(f"📥 دانلود کامل پلی‌لیست ({pl['nb_tracks']} آهنگ - 320k)", f"sp_dl_pl_all_{playlist_id}")]
    ]
    for t in page_trks:
        dur = rega_music.format_duration(t.get("duration", 0))
        rows.append([InlineKeyboardButton(f"▶️ {t['track_number']}. {t['title']} ({dur})", f"sp_show_trk_{t['id']}")])

    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️ قبلی", f"sp_pl_trks_{playlist_id}_{page - 1}"))
        nav.append(InlineKeyboardButton(f"صفحه {page + 1}/{total_pages}", "sp_noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("بعدی ▶️", f"sp_pl_trks_{playlist_id}_{page + 1}"))
        rows.append(nav)

    rows.append([InlineKeyboardButton("🔙 بازگشت به لیست پلی‌لیست‌ها", "sp_pl_pg_curr")])
    await safe_edit_or_send(client, chat_id, msg, text, InlineKeyboard(rows))


# ----------------- SOUNDCLOUD RENDERERS -----------------
async def render_sc_home(client: Client, chat_id: int, msg: Message = None):
    USER_STATES[chat_id] = "AWAIT_SC_LINK_OR_QUERY"
    sc_locked = is_playlist_locked("soundcloud")
    pl_label = "🔒 پلی‌لیست‌ها (غیرفعال)" if sc_locked else "📑 پلی‌لیست‌ها (Playlists)"
    text = (
        "☁️ **ساندکلاد (SoundCloud):**\n\n"
        "شما می‌توانید لینک هر آهنگ، ست یا پلی‌لیست از ساندکلاد را ارسال کنید، "
        "یا از طریق دکمه‌های زیر جستجو فرمایید:"
    )
    kb = InlineKeyboard([
        [
            InlineKeyboardButton("🎵 جستجوی آهنگ (Tracks)", "sc_search_track"),
            InlineKeyboardButton("👤 جستجوی هنرمند (Artists)", "sc_search_artist")
        ],
        [
            InlineKeyboardButton("💿 جستجوی آلبوم / ست (Albums)", "sc_search_album"),
            InlineKeyboardButton("🔥 برترین‌های ساندکلاد (Trending)", "sc_trending")
        ],
        [InlineKeyboardButton(pl_label, "sc_search_playlist")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]
    ])
    await safe_edit_or_send(client, chat_id, msg, text, kb)


async def render_sc_tracks_page(client: Client, chat_id: int, page: int = 0, msg: Message = None):
    tracks = USER_DATA.get(chat_id, {}).get("sc_track_results", [])
    query = USER_DATA.get(chat_id, {}).get("sc_track_query", "موزیک")
    if not tracks:
        await safe_edit_or_send(
            client, chat_id, msg,
            f"❌ نتیجه‌ای برای «{query}» در ساندکلاد یافت نشد.",
            InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به ساندکلاد", "menu_music_sc")]])
        )
        return

    per_page = 5
    total_pages = max(1, math.ceil(len(tracks) / per_page))
    page = max(0, min(page, total_pages - 1))
    start_idx = page * per_page
    page_trks = tracks[start_idx:start_idx + per_page]

    text = (
        f"☁️ **نتایج جستجوی ساندکلاد:**\n"
        f"🔍 عبارت: **{query}** | صفحه {page + 1} از {total_pages}\n\n"
        "برای انتخاب کیفیت و دانلود، روی آهنگ مورد نظر کلیک کنید:"
    )

    rows = []
    for idx, t in enumerate(page_trks, start=start_idx):
        dur = rega_music.format_duration(t.get("duration", 0))
        t_title = t.get("title") or "آهنگ"
        t_art = t.get("artist") or t.get("uploader") or "SoundCloud"
        btn_title = f"▶️ {idx + 1}. {t_title[:25]} - {t_art[:15]} ({dur})"
        rows.append([InlineKeyboardButton(btn_title, f"sc_show_trk_{idx}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ قبلی", f"sc_trk_pg_{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", "sc_noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("بعدی ▶️", f"sc_trk_pg_{page + 1}"))
    rows.append(nav_row)

    rows.append([InlineKeyboardButton("🔙 بازگشت به منوی ساندکلاد", "menu_music_sc")])
    await safe_edit_or_send(client, chat_id, msg, text, InlineKeyboard(rows))


async def render_sc_track_card(client: Client, chat_id: int, track_idx: int, msg: Message = None):
    tracks = USER_DATA.get(chat_id, {}).get("sc_track_results", [])
    if track_idx < 0 or track_idx >= len(tracks):
        await safe_edit_or_send(
            client, chat_id, msg, "❌ آهنگ یافت نشد.",
            InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت", "sc_trk_pg_0")]])
        )
        return

    t = tracks[track_idx]
    USER_DATA.setdefault(chat_id, {})["sc_selected_track"] = t
    t_title = t.get("title") or "آهنگ ساندکلاد"
    t_art = t.get("artist") or t.get("uploader") or "هنرمند ساندکلاد"
    dur = rega_music.format_duration(t.get("duration", 0))
    views = f"{t.get('views', 0):,} بار" if t.get("views") else "مشخص‌نشده"

    text = (
        "☁️ **مشخصات ترک ساندکلاد:**\n\n"
        f"🎧 عنوان: **{t_title}**\n"
        f"👤 ناشر / هنرمند: **{t_art}**\n"
        f"⏱ مدت زمان: **{dur}**\n"
        f"▶️ پخش و بازدید: **{views}**\n\n"
        "کیفیت مورد نظر خود را برای دانلود انتخاب فرمایید:"
    )

    kb = InlineKeyboard([
        [
            InlineKeyboardButton("📥 کیفیت عالی (320 kbps)", f"sc_dl_320_{track_idx}"),
            InlineKeyboardButton("📥 کیفیت خوب (128 kbps)", f"sc_dl_128_{track_idx}")
        ],
        [InlineKeyboardButton("🔙 بازگشت به نتایج ساندکلاد", "sc_trk_pg_curr")]
    ])
    await safe_edit_or_send(client, chat_id, msg, text, kb)


async def render_sc_albums_page(client: Client, chat_id: int, page: int = 0, msg: Message = None):
    albums = USER_DATA.get(chat_id, {}).get("sc_album_results", [])
    query = USER_DATA.get(chat_id, {}).get("sc_album_query", "آلبوم")
    if not albums:
        await safe_edit_or_send(
            client, chat_id, msg,
            f"❌ آلبوم یا ستی برای «{query}» در ساندکلاد یافت نشد.",
            InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به ساندکلاد", "menu_music_sc")]])
        )
        return

    per_page = 5
    total_pages = max(1, math.ceil(len(albums) / per_page))
    page = max(0, min(page, total_pages - 1))
    start_idx = page * per_page
    page_albs = albums[start_idx:start_idx + per_page]

    text = (
        f"💿 **نتایج جستجوی آلبوم / ست در ساندکلاد:**\n"
        f"🔍 عبارت: **{query}** | صفحه {page + 1} از {total_pages}\n\n"
        "آلبوم مورد نظر را انتخاب فرمایید:"
    )
    rows = []
    for idx, a in enumerate(page_albs, start=start_idx):
        btn_title = f"💿 {idx + 1}. {a['title'][:25]} - {a['artist'][:15]}"
        rows.append([InlineKeyboardButton(btn_title, f"sc_show_alb_{idx}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ قبلی", f"sc_alb_pg_{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", "sc_noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("بعدی ▶️", f"sc_alb_pg_{page + 1}"))
    rows.append(nav_row)

    rows.append([InlineKeyboardButton("🔙 بازگشت به منوی ساندکلاد", "menu_music_sc")])
    await safe_edit_or_send(client, chat_id, msg, text, InlineKeyboard(rows))


async def render_sc_album_detail(client: Client, chat_id: int, album_idx: int, msg: Message = None):
    albums = USER_DATA.get(chat_id, {}).get("sc_album_results", [])
    if album_idx < 0 or album_idx >= len(albums):
        await safe_edit_or_send(client, chat_id, msg, "❌ آلبوم یافت نشد.", InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت", "sc_alb_pg_0")]]))
        return

    a = albums[album_idx]
    USER_DATA.setdefault(chat_id, {})["sc_selected_album"] = a
    text = (
        f"💿 **آلبوم / ست ساندکلاد:**\n\n"
        f"🎧 عنوان: **{a['title']}**\n"
        f"👤 ناشر: **{a['artist']}**\n\n"
        "گزینه دانلود مورد نظر را انتخاب فرمایید:"
    )
    kb = InlineKeyboard([
        [
            InlineKeyboardButton("📥 دانلود (320 kbps)", f"sc_dl_320_{album_idx}"),
            InlineKeyboardButton("📥 دانلود (128 kbps)", f"sc_dl_128_{album_idx}")
        ],
        [InlineKeyboardButton("🔙 بازگشت به لیست آلبوم‌ها", "sc_alb_pg_curr")]
    ])
    await safe_edit_or_send(client, chat_id, msg, text, kb)


async def render_sc_artists_page(client: Client, chat_id: int, page: int = 0, msg: Message = None):
    artists = USER_DATA.get(chat_id, {}).get("sc_artist_results", [])
    query = USER_DATA.get(chat_id, {}).get("sc_artist_query", "هنرمند")
    if not artists:
        await safe_edit_or_send(
            client, chat_id, msg,
            f"❌ هنرمندی برای «{query}» در ساندکلاد یافت نشد.",
            InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به ساندکلاد", "menu_music_sc")]])
        )
        return

    per_page = 5
    total_pages = max(1, math.ceil(len(artists) / per_page))
    page = max(0, min(page, total_pages - 1))
    start_idx = page * per_page
    page_arts = artists[start_idx:start_idx + per_page]

    text = (
        f"👤 **هنرمندان ساندکلاد:**\n"
        f"🔍 عبارت: **{query}** | صفحه {page + 1} از {total_pages}\n\n"
        "هنرمند مورد نظر را جهت مشاهده ترک‌ها انتخاب کنید:"
    )
    rows = []
    for idx, art in enumerate(page_arts, start=start_idx):
        rows.append([InlineKeyboardButton(f"👤 {idx + 1}. {art['name']}", f"sc_art_trks_{idx}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ قبلی", f"sc_art_pg_{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", "sc_noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("بعدی ▶️", f"sc_art_pg_{page + 1}"))
    rows.append(nav_row)

    rows.append([InlineKeyboardButton("🔙 بازگشت به منوی ساندکلاد", "menu_music_sc")])
    await safe_edit_or_send(client, chat_id, msg, text, InlineKeyboard(rows))


# ----------------- YOUTUBE MUSIC RENDERERS -----------------
async def render_ytm_home(client: Client, chat_id: int, msg: Message = None):
    USER_STATES[chat_id] = "AWAIT_YTM_LINK_OR_QUERY"
    ytm_locked = is_playlist_locked("ytmusic")
    pl_label = "🔒 پلی‌لیست‌ها (غیرفعال)" if ytm_locked else "📑 پلی‌لیست‌ها (Playlists)"
    text = (
        "🔴 **یوتیوب موزیک (YouTube Music):**\n\n"
        "شما می‌توانید لینک هر آهنگ، آلبوم یا پلی‌لیست یوتیوب موزیک را ارسال کنید، "
        "یا از طریق دکمه‌های زیر جستجو فرمایید:"
    )
    kb = InlineKeyboard([
        [
            InlineKeyboardButton("🎵 جستجوی آهنگ (Tracks)", "ytm_search_track"),
            InlineKeyboardButton("👤 جستجوی خواننده (Artists)", "ytm_search_artist")
        ],
        [
            InlineKeyboardButton("💿 جستجوی آلبوم (Albums)", "ytm_search_album"),
            InlineKeyboardButton("🔥 داغ‌ترین آهنگ‌ها (Top Music)", "ytm_trending")
        ],
        [InlineKeyboardButton(pl_label, "ytm_search_playlist")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]
    ])
    await safe_edit_or_send(client, chat_id, msg, text, kb)


async def render_ytm_tracks_page(client: Client, chat_id: int, page: int = 0, msg: Message = None):
    tracks = USER_DATA.get(chat_id, {}).get("ytm_track_results", [])
    query = USER_DATA.get(chat_id, {}).get("ytm_track_query", "موزیک")
    if not tracks:
        await safe_edit_or_send(
            client, chat_id, msg,
            f"❌ نتیجه‌ای برای «{query}» در یوتیوب موزیک یافت نشد.",
            InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به یوتیوب موزیک", "menu_music_ytm")]])
        )
        return

    per_page = 5
    total_pages = max(1, math.ceil(len(tracks) / per_page))
    page = max(0, min(page, total_pages - 1))
    start_idx = page * per_page
    page_trks = tracks[start_idx:start_idx + per_page]

    text = (
        f"🔴 **نتایج جستجوی یوتیوب موزیک:**\n"
        f"🔍 عبارت: **{query}** | صفحه {page + 1} از {total_pages}\n\n"
        "برای انتخاب کیفیت و دانلود، روی آهنگ مورد نظر کلیک کنید:"
    )

    rows = []
    for idx, t in enumerate(page_trks, start=start_idx + 1):
        dur = rega_music.format_duration(t.get("duration", 0))
        t_title = t.get("title") or "YouTube Music Track"
        t_art = t.get("artist") or t.get("channel") or "YouTube Music"
        btn_title = f"▶️ {idx}. {t_title[:25]} - {t_art[:15]} ({dur})"
        rows.append([InlineKeyboardButton(btn_title, f"ytm_show_trk_{t.get('id', idx)}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ قبلی", f"ytm_trk_pg_{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", "ytm_noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("بعدی ▶️", f"ytm_trk_pg_{page + 1}"))
    rows.append(nav_row)

    rows.append([InlineKeyboardButton("🔙 بازگشت به منوی یوتیوب موزیک", "menu_music_ytm")])
    await safe_edit_or_send(client, chat_id, msg, text, InlineKeyboard(rows))


async def render_ytm_track_card(client: Client, chat_id: int, vid_id: str, msg: Message = None):
    t = None
    for item in USER_DATA.get(chat_id, {}).get("ytm_track_results", []):
        if str(item.get("id")) == str(vid_id):
            t = item
            break
    if not t:
        t = {"id": vid_id, "title": "YouTube Music Track", "artist": "YouTube Music", "duration": 0, "thumb": f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"}

    USER_DATA.setdefault(chat_id, {})["ytm_selected_track"] = t
    t_title = t.get("title") or "YouTube Music Track"
    t_art = t.get("artist") or t.get("channel") or "YouTube Music"
    dur = rega_music.format_duration(t.get("duration", 0))

    text = (
        "🔴 **مشخصات ترک صوتی یوتیوب موزیک:**\n\n"
        f"🎧 عنوان: **{t_title}**\n"
        f"👤 کانال / هنرمند: **{t_art}**\n"
        f"⏱ مدت زمان: **{dur}**\n\n"
        "کیفیت مورد نظر خود را برای دانلود انتخاب فرمایید:"
    )

    kb = InlineKeyboard([
        [
            InlineKeyboardButton("📥 کیفیت عالی (320 kbps)", f"ytm_dl_320_{vid_id}"),
            InlineKeyboardButton("📥 کیفیت خوب (128 kbps)", f"ytm_dl_128_{vid_id}")
        ],
        [InlineKeyboardButton("🔙 بازگشت به نتایج", "ytm_trk_pg_curr")]
    ])
    await safe_edit_or_send(client, chat_id, msg, text, kb)


async def render_ytm_albums_page(client: Client, chat_id: int, page: int = 0, msg: Message = None):
    albums = USER_DATA.get(chat_id, {}).get("ytm_album_results", [])
    query = USER_DATA.get(chat_id, {}).get("ytm_album_query", "آلبوم")
    if not albums:
        await safe_edit_or_send(
            client, chat_id, msg,
            f"❌ آلبومی برای «{query}» در یوتیوب موزیک یافت نشد.",
            InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به یوتیوب موزیک", "menu_music_ytm")]])
        )
        return

    per_page = 5
    total_pages = max(1, math.ceil(len(albums) / per_page))
    page = max(0, min(page, total_pages - 1))
    start_idx = page * per_page
    page_albs = albums[start_idx:start_idx + per_page]

    text = (
        f"💿 **نتایج جستجوی آلبوم در یوتیوب موزیک:**\n"
        f"🔍 عبارت: **{query}** | صفحه {page + 1} از {total_pages}\n\n"
        "آلبوم مورد نظر را انتخاب فرمایید:"
    )
    rows = []
    for idx, a in enumerate(page_albs, start=start_idx + 1):
        btn_title = f"💿 {idx}. {a['title'][:25]} - {a['artist'][:15]}"
        rows.append([InlineKeyboardButton(btn_title, f"ytm_show_alb_{a['id']}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ قبلی", f"ytm_alb_pg_{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", "ytm_noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("بعدی ▶️", f"ytm_alb_pg_{page + 1}"))
    rows.append(nav_row)

    rows.append([InlineKeyboardButton("🔙 بازگشت به منوی یوتیوب موزیک", "menu_music_ytm")])
    await safe_edit_or_send(client, chat_id, msg, text, InlineKeyboard(rows))


async def render_ytm_album_detail(client: Client, chat_id: int, vid_id: str, msg: Message = None):
    a = None
    for item in USER_DATA.get(chat_id, {}).get("ytm_album_results", []):
        if str(item.get("id")) == str(vid_id):
            a = item
            break
    if not a:
        a = {"id": vid_id, "title": "YouTube Music Album", "artist": "Artist", "thumb": f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"}

    USER_DATA.setdefault(chat_id, {})["ytm_selected_album"] = a
    text = (
        f"💿 **آلبوم یوتیوب موزیک:**\n\n"
        f"🎧 عنوان: **{a['title']}**\n"
        f"👤 کانال / ناشر: **{a['artist']}**\n\n"
        "گزینه دانلود مورد نظر را انتخاب فرمایید:"
    )
    kb = InlineKeyboard([
        [
            InlineKeyboardButton("📥 دانلود کامل آلبوم (320 kbps)", f"ytm_dl_320_{vid_id}"),
            InlineKeyboardButton("📥 دانلود (128 kbps)", f"ytm_dl_128_{vid_id}")
        ],
        [InlineKeyboardButton("🔙 بازگشت به لیست آلبوم‌ها", "ytm_alb_pg_curr")]
    ])
    await safe_edit_or_send(client, chat_id, msg, text, kb)


async def render_ytm_artists_page(client: Client, chat_id: int, page: int = 0, msg: Message = None):
    artists = USER_DATA.get(chat_id, {}).get("ytm_artist_results", [])
    query = USER_DATA.get(chat_id, {}).get("ytm_artist_query", "هنرمند")
    if not artists:
        await safe_edit_or_send(
            client, chat_id, msg,
            f"❌ هنرمندی برای «{query}» در یوتیوب موزیک یافت نشد.",
            InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به یوتیوب موزیک", "menu_music_ytm")]])
        )
        return

    per_page = 5
    total_pages = max(1, math.ceil(len(artists) / per_page))
    page = max(0, min(page, total_pages - 1))
    start_idx = page * per_page
    page_arts = artists[start_idx:start_idx + per_page]

    text = (
        f"👤 **هنرمندان یوتیوب موزیک:**\n"
        f"🔍 عبارت: **{query}** | صفحه {page + 1} از {total_pages}\n\n"
        "هنرمند مورد نظر را جهت مشاهده ترک‌ها انتخاب فرمایید:"
    )
    rows = []
    for idx, art in enumerate(page_arts, start=start_idx):
        rows.append([InlineKeyboardButton(f"👤 {idx + 1}. {art['name']}", f"ytm_art_trks_{idx}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ قبلی", f"ytm_art_pg_{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", "ytm_noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("بعدی ▶️", f"ytm_art_pg_{page + 1}"))
    rows.append(nav_row)

    rows.append([InlineKeyboardButton("🔙 بازگشت به منوی یوتیوب موزیک", "menu_music_ytm")])
    await safe_edit_or_send(client, chat_id, msg, text, InlineKeyboard(rows))


# ----------------- CALLBACK QUERY HANDLER -----------------
@bot.on_callback_query()
async def on_callback(client: Client, callback_query: CallbackQuery):
    query = callback_query
    chat_id = query.message.chat.id if (query.message and query.message.chat) else (query.author.id if query.author else 0)
    data = query.data
    lang = get_user_lang(chat_id)
    msg = query.message
    username = (query.author.username if query.author else "") or ""

    try:
        await query.answer()
    except Exception:
        pass

    # Ban check
    if is_user_banned(chat_id):
        await client.send_message(chat_id, "⛔️ حساب کاربری شما مسدود شده است.")
        return

    # Maintenance check
    if is_maintenance_mode() and not is_admin(chat_id, username) and data not in ["menu_main", "help_home"]:
        await safe_edit_or_send(client, chat_id, msg, "🛠 **ربات در حال به‌روزرسانی و ارتقای سرور است.**\nلطفاً دقایقی دیگر مراجعه فرمایید.")
        return

    # --- Main Navigation ---
    if data == "menu_main":
        USER_STATES.pop(chat_id, None)
        name = (query.author.first_name if query.author else "") or "کاربر"
        text = menus.get_menu_text("main_menu_welcome", lang=lang, name=name)
        kb = menus.get_main_keyboard(chat_id, get_user_lang, is_admin)
        await safe_edit_or_send(client, chat_id, msg, text, kb)
        return

    if data == "help_home":
        await cmd_help(client, msg)
        return

    if data == "menu_profile":
        await render_user_profile(client, chat_id, msg, user_obj=query.author)
        return

    # --- Admin Panel Callbacks (adm_*) ---
    if data.startswith("adm_"):
        if not is_admin(chat_id, username):
            await safe_edit_or_send(client, chat_id, msg, "⛔️ این بخش مختص مدیریت ربات است.")
            return

        if data == "adm_home":
            USER_STATES.pop(chat_id, None)
            await render_admin_home(client, chat_id, msg)
            return

        if data == "adm_stats":
            await render_admin_stats(client, chat_id, msg)
            return

        if data.startswith("adm_users_"):
            parts = data.replace("adm_users_", "").split("_")
            page = int(parts[0]) if parts[0].isdigit() else 0
            sort = parts[1] if len(parts) > 1 else "seen"
            await render_admin_users(client, chat_id, page=page, sort=sort, msg=msg)
            return

        if data.startswith("adm_u_"):
            target_id = int(data.replace("adm_u_", ""))
            await render_admin_user_detail(client, chat_id, target_id=target_id, msg=msg)
            return

        if data.startswith("adm_ban_"):
            target_id = int(data.replace("adm_ban_", ""))
            if db is not None:
                db.users.update_one({"chat_id": target_id}, {"$set": {"banned": True}}, upsert=True)
            await client.send_message(chat_id, f"⛔️ کاربر `{target_id}` با موفقیت مسدود شد.")
            await render_admin_user_detail(client, chat_id, target_id=target_id, msg=msg)
            return

        if data.startswith("adm_unban_"):
            target_id = int(data.replace("adm_unban_", ""))
            if db is not None:
                db.users.update_one({"chat_id": target_id}, {"$set": {"banned": False}}, upsert=True)
            await client.send_message(chat_id, f"✅ کاربر `{target_id}` با موفقیت رفع مسدودی شد.")
            await render_admin_user_detail(client, chat_id, target_id=target_id, msg=msg)
            return

        if data.startswith("adm_pm_"):
            target_id = int(data.replace("adm_pm_", ""))
            USER_STATES[chat_id] = f"AWAIT_ADMIN_PM_{target_id}"
            await safe_edit_or_send(
                client, chat_id, msg,
                f"✉️ **متن پیام ارسالی به کاربر `{target_id}` را بنویسید:**",
                InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", f"adm_u_{target_id}")]])
            )
            return

        if data == "adm_search":
            USER_STATES[chat_id] = "AWAIT_ADMIN_SEARCH"
            await safe_edit_or_send(
                client, chat_id, msg,
                "🔍 **شناسه کاربری (عددی) یا یوزرنیم کاربر مورد نظر را بفرستید:**",
                InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "adm_home")]])
            )
            return

        if data == "adm_bc":
            USER_STATES[chat_id] = "AWAIT_BROADCAST"
            await safe_edit_or_send(
                client, chat_id, msg,
                "📢 **متن پیام همگانی را بنویسید:**\n\nاین پیام برای تمام کاربران ثبت‌شده در ربات ارسال خواهد شد.",
                InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "adm_home")]])
            )
            return

        if data == "adm_maint":
            new_val = not is_maintenance_mode()
            set_maintenance_mode(new_val)
            await render_admin_home(client, chat_id, msg)
            return

        if data.startswith("adm_act_"):
            page_str = data.replace("adm_act_", "")
            page = int(page_str) if page_str.isdigit() else 0
            await render_admin_activities(client, chat_id, page=page, msg=msg)
            return

        if data == "adm_slice":
            await render_admin_slice(client, chat_id, msg)
            return

        if data.startswith("adm_set_slice_"):
            val_str = data.replace("adm_set_slice_", "")
            if val_str.isdigit():
                set_video_slice_limit_mb(int(val_str))
                await client.send_message(chat_id, f"✅ سقف تکه‌کردن ویدیوها با موفقیت روی {val_str} مگابایت تنظیم شد.")
            await render_admin_slice(client, chat_id, msg)
            return

        if data == "adm_slice_custom":
            USER_STATES[chat_id] = "AWAIT_ADMIN_SLICE_VAL"
            await safe_edit_or_send(
                client, chat_id, msg,
                "✂️ **لطفاً سقف حجم هر پارت را به مگابایت (فقط عدد، مثلاً 35) ارسال کنید:**",
                InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "adm_slice")]])
            )
            return

        if data == "adm_locks":
            await render_admin_locks(client, chat_id, msg)
            return

        if data == "adm_tog_lock_sp":
            new_v = toggle_playlist_lock("spotify")
            stat = "قفل 🔒" if new_v else "باز 🔓"
            await client.send_message(chat_id, f"✅ پلی‌لیست‌های اسپاتیفای اکنون {stat} شدند.")
            await render_admin_locks(client, chat_id, msg)
            return

        if data == "adm_tog_lock_sc":
            new_v = toggle_playlist_lock("soundcloud")
            stat = "قفل 🔒" if new_v else "باز 🔓"
            await client.send_message(chat_id, f"✅ پلی‌لیست‌های ساندکلاد اکنون {stat} شدند.")
            await render_admin_locks(client, chat_id, msg)
            return

        if data == "adm_tog_lock_ytm":
            new_v = toggle_playlist_lock("ytmusic")
            stat = "قفل 🔒" if new_v else "باز 🔓"
            await client.send_message(chat_id, f"✅ پلی‌لیست‌های یوتیوب موزیک اکنون {stat} شدند.")
            await render_admin_locks(client, chat_id, msg)
            return

        if data.startswith("adm_sup_"):
            page_str = data.replace("adm_sup_", "")
            page = int(page_str) if page_str.isdigit() else 0
            await render_admin_support(client, chat_id, page=page, msg=msg)
            return

        if data.startswith("adm_view_sup_"):
            t_id = data.replace("adm_view_sup_", "")
            await render_admin_support_detail(client, chat_id, ticket_id=t_id, msg=msg)
            return

        if data.startswith("adm_rep_"):
            t_id = data.replace("adm_rep_", "")
            USER_STATES[chat_id] = f"AWAIT_ADMIN_REPLY_{t_id}"
            await safe_edit_or_send(
                client, chat_id, msg,
                f"✍️ **پاسخ خود برای تیکت را ارسال فرمایید:**\n(این پیام مستقیماً به کاربر تحویل داده خواهد شد)",
                InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", f"adm_view_sup_{t_id}")]])
            )
            return

        if data == "adm_noop":
            return

    # --- Submenu Navigation ---
    if data == "menu_youtube":
        USER_STATES[chat_id] = "AWAIT_YT_QUERY"
        text = (
            "▶️ **یوتیوب (YouTube):**\n\n"
            "شما می‌توانید لینک هر ویدیوی یوتیوب را مستقیماً ارسال کنید، یا از طریق دکمه‌های زیر جستجو فرمایید:"
        )
        kb = InlineKeyboard([
            [InlineKeyboardButton("🔍 جستجو با کلمه کلیدی (Keyword)", "yt_search_kw")],
            [InlineKeyboardButton("👤 جستجو در کانال‌ها (Channel)", "yt_search_ch")],
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]
        ])
        await safe_edit_or_send(client, chat_id, msg, text, kb)
        return

    if data == "yt_search_kw":
        USER_STATES[chat_id] = "AWAIT_YT_KEYWORD_SEARCH"
        await safe_edit_or_send(
            client, chat_id, msg,
            "🔍 **جستجوی ویدیو در یوتیوب:**\n\nلطفاً کلمه کلیدی یا عنوان ویدیو را ارسال کنید:",
            InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_youtube")]])
        )
        return

    if data == "yt_search_ch":
        USER_STATES[chat_id] = "AWAIT_YT_CHANNEL_SEARCH"
        await safe_edit_or_send(
            client, chat_id, msg,
            "👤 **جستجو در کانال‌های یوتیوب:**\n\nلطفاً نام یا شناسه کانال یوتیوب را ارسال کنید:",
            InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_youtube")]])
        )
        return

    if data.startswith("yt_ch_pg_"):
        pg_str = data.replace("yt_ch_pg_", "")
        pg = int(pg_str) if pg_str.isdigit() else 0
        await render_yt_channels_page(client, chat_id, page=pg, msg=msg)
        return

    if data.startswith("yt_ch_sel_"):
        idx_str = data.replace("yt_ch_sel_", "")
        idx = int(idx_str) if idx_str.isdigit() else 0
        await render_yt_channel_detail(client, chat_id, channel_idx=idx, msg=msg)
        return

    if data == "yt_ch_back":
        await render_yt_channels_page(client, chat_id, page=0, msg=msg)
        return

    if data.startswith("yt_kw_pg_"):
        pg_str = data.replace("yt_kw_pg_", "")
        pg = int(pg_str) if pg_str.isdigit() else 0
        await render_yt_keyword_page(client, chat_id, page=pg, msg=msg)
        return

    if data == "yt_kw_filters_menu":
        filters = USER_DATA.get(chat_id, {}).get("yt_kw_filters", {"dur": "all", "sort": "rel"})
        dur_f = filters.get("dur", "all")
        sort_f = filters.get("sort", "rel")
        text = (
            "⚙️ **فیلترها و مرتب‌سازی نتایج جستجو:**\n\n"
            "فیلتر مدت زمان یا نوع چینش نتایج را انتخاب کنید:"
        )
        def d_mark(k): return "🔘 " if dur_f == k else "⚪️ "
        def s_mark(k): return "🔘 " if sort_f == k else "⚪️ "

        kb = InlineKeyboard([
            [
                InlineKeyboardButton(f"{d_mark('all')}همه زمان‌ها", "yt_filt_dur_all"),
                InlineKeyboardButton(f"{d_mark('short')}کوتاه (<4m)", "yt_filt_dur_short")
            ],
            [
                InlineKeyboardButton(f"{d_mark('med')}متوسط (4-20m)", "yt_filt_dur_med"),
                InlineKeyboardButton(f"{d_mark('long')}طولانی (>20m)", "yt_filt_dur_long")
            ],
            [
                InlineKeyboardButton(f"{s_mark('rel')}مرتبط‌ترین", "yt_filt_sort_rel"),
                InlineKeyboardButton(f"{s_mark('views')}پربازدیدترین", "yt_filt_sort_views"),
                InlineKeyboardButton(f"{s_mark('dur')}طولانی‌ترین", "yt_filt_sort_dur")
            ],
            [InlineKeyboardButton("✅ اعمال و بازگشت به نتایج", "yt_kw_pg_0")]
        ])
        await safe_edit_or_send(client, chat_id, msg, text, kb)
        return

    if data.startswith("yt_filt_dur_"):
        val = data.replace("yt_filt_dur_", "")
        USER_DATA.setdefault(chat_id, {}).setdefault("yt_kw_filters", {})["dur"] = val
        fake_q = CallbackQuery(id="0", message=msg, data="yt_kw_filters_menu", author=query.author)
        await on_callback(client, fake_q)
        return

    if data.startswith("yt_filt_sort_"):
        val = data.replace("yt_filt_sort_", "")
        USER_DATA.setdefault(chat_id, {}).setdefault("yt_kw_filters", {})["sort"] = val
        fake_q = CallbackQuery(id="0", message=msg, data="yt_kw_filters_menu", author=query.author)
        await on_callback(client, fake_q)
        return

    if data.startswith("yt_vid_"):
        vid = data.replace("yt_vid_", "")
        await render_yt_video_card(client, chat_id, vid, msg=msg)
        return

    if data == "yt_choose_mp4":
        USER_DATA.setdefault(chat_id, {}).setdefault("yt_pending", {})["format"] = "mp4"
        await render_yt_quality_selector(client, chat_id, msg=msg)
        return

    if data == "yt_choose_mp3":
        USER_DATA.setdefault(chat_id, {}).setdefault("yt_pending", {})["format"] = "mp3"
        await render_yt_quality_selector(client, chat_id, msg=msg)
        return

    if data.startswith("yt_set_qual_"):
        qual = data.replace("yt_set_qual_", "")
        USER_DATA.setdefault(chat_id, {}).setdefault("yt_pending", {})["quality"] = qual
        await render_yt_extras_screen(client, chat_id, msg=msg)
        return

    if data == "yt_card_back":
        pending = USER_DATA.get(chat_id, {}).get("yt_pending", {})
        vid = pending.get("video_id") or pending.get("url")
        if vid:
            await render_yt_video_card(client, chat_id, vid, msg=msg)
        else:
            await on_callback(client, CallbackQuery(id="0", message=msg, data="menu_youtube", author=query.author))
        return

    if data.startswith("yt_tog_"):
        key_map = {"yt_tog_sub": "subtitles", "yt_tog_thumb": "thumbnail", "yt_tog_desc": "description", "yt_tog_comm": "comments"}
        field = key_map.get(data)
        if field:
            pending = USER_DATA.setdefault(chat_id, {}).setdefault("yt_pending", {})
            extras = pending.setdefault("extras", {"subtitles": False, "thumbnail": False, "description": False, "comments": False})
            extras[field] = not extras.get(field, False)
            await render_yt_extras_screen(client, chat_id, msg=msg)
        return

    if data == "yt_confirm_dl":
        pending = USER_DATA.get(chat_id, {}).get("yt_pending", {})
        if not pending:
            await safe_edit_or_send(client, chat_id, msg, "❌ درخواست منقضی شده است.", InlineKeyboard([[InlineKeyboardButton("🔙 منوی یوتیوب", "menu_youtube")]]))
            return

        slice_mb = get_video_slice_limit_mb()
        title = pending.get("title", "یوتیوب")
        fmt = pending.get("format", "mp4")
        qual = pending.get("quality", "720p")
        url = pending.get("url")
        extras = pending.get("extras", {})

        progress_msg = await client.send_message(
            chat_id,
            f"🚀 **درخواست دانلود شما با موفقیت ثبت شد!**\n\n"
            f"🎬 عنوان: **{title}**\n"
            f"🎯 کیفیت: **{qual}** ({fmt.upper()})\n"
            f"✂️ سقف هر پارت: **{slice_mb} مگابایت**\n\n"
            f"⏳ در حال راه‌اندازی سرور اختصاصی و دانلود در پس‌زمینه..."
        )

        ok = dispatch_github_worker(
            chat_id=chat_id,
            message_id=progress_msg.id,
            url=url,
            fmt=fmt,
            quality=qual,
            slice_mb=slice_mb,
            subtitles=extras.get("subtitles", False),
            thumbnail=extras.get("thumbnail", False),
            description=extras.get("description", False),
            comments=extras.get("comments", False)
        )

        if ok:
            log_activity(chat_id, "youtube_download", url)
        else:
            await progress_msg.edit_text("❌ خطا در ارسال درخواست به سرور پردازش. لطفاً لحظاتی دیگر تلاش فرمایید.")
        return

    if data == "menu_ig_tiktok":
        USER_STATES[chat_id] = "AWAIT_IG_TIKTOK_LINK"
        text = (
            "📸 **دانلود از اینستاگرام و تیک‌تاک:**\n\n"
            "لطفاً لینک ریلز، پست یا ویدیوی اینستاگرام یا تیک‌تاک را ارسال کنید تا بلافاصله دانلود و برای شما ارسال شود."
        )
        kb = InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]])
        await safe_edit_or_send(client, chat_id, msg, text, kb)
        return

    if data == "menu_reddit_twitter":
        text = (
            "🐦 **ردیت و توییتر (X):**\n\n"
            "شما می‌توانید لینک هر پست از **توییتر (X)** یا **ردیت (Reddit)** را ارسال کنید، "
            "یا در ردیت با کلمه کلیدی جستجو نمایید:"
        )
        kb = menus.get_reddit_twitter_keyboard(chat_id)
        await safe_edit_or_send(client, chat_id, msg, text, kb)
        return

    if data == "rd_search":
        USER_STATES[chat_id] = "AWAIT_RD_SEARCH"
        await safe_edit_or_send(
            client, chat_id, msg,
            "🔍 **جستجو در ردیت (Reddit Search):**\n\nلطفاً کلمه کلیدی یا موضوع مورد نظر را بنویسید:",
            InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_reddit_twitter")]])
        )
        return

    if data == "rd_link_help":
        text = (
            "📥 **راهنمای ارسال لینک:**\n\n"
            "• **توییتر (X):** لینک هر توییت، عکس، گیف یا ویدیو را بفرستید.\n"
            "• **ردیت (Reddit):** لینک پست یا ویدیوی ردیت را بفرستید تا با صوت و تصویر اصلی دانلود شود."
        )
        kb = InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت", "menu_reddit_twitter")]])
        await safe_edit_or_send(client, chat_id, msg, text, kb)
        return

    if data.startswith("rd_pg_"):
        pg_str = data.replace("rd_pg_", "")
        pg = int(pg_str) if pg_str.isdigit() else 0
        await render_reddit_page(client, chat_id, page=pg, msg=msg)
        return

    if data.startswith("rd_dl_"):
        post_id = data.replace("rd_dl_", "")
        status = await client.send_message(chat_id, "⏳ در حال دانلود پست از ردیت...")
        try:
            res = await asyncio.to_thread(soc.download_reddit_post_sync, post_id)
            files = res.get("files", [])
            title = res.get("title", "")
            if not files:
                await status.edit_text("❌ فایلی در این پست ردیت یافت نشد یا دسترسی مسدود است.")
                return
            await status.edit_text("⬆️ در حال ارسال به بله...")
            for f in files:
                caption = f"🐦 **{title}**\n⚡️ دانلود شده توسط @tahasbot"
                if f.lower().endswith((".mp4", ".mov", ".mkv")):
                    USER_DATA.setdefault(chat_id, {})["vedit_path"] = f
                    kb_edit = InlineKeyboard([[InlineKeyboardButton("🎬 ویرایش این ویدیو", "vedit_from_last")]])
                    with open(f, "rb") as vf:
                        await client.send_video(chat_id, video=vf, caption=caption, reply_markup=kb_edit)
                else:
                    with open(f, "rb") as pf:
                        await client.send_photo(chat_id, photo=pf, caption=caption)
            await status.delete()
            log_activity(chat_id, "download_reddit", title)
            for f in files:
                if os.path.exists(f):
                    try: os.remove(f)
                    except OSError: pass
        except Exception as e:
            traceback.print_exc()
            await status.edit_text(f"❌ خطا در دانلود ردیت: {e}")
        return

    if data == "rd_noop":
        return

    if data == "menu_vedit":
        await render_video_editor_home(client, chat_id, msg)
        return

    if data == "vedit_from_last":
        await render_video_editor_home(client, chat_id, msg)
        return

    if data == "vedit_trim":
        USER_STATES[chat_id] = "AWAIT_VEDIT_TRIM"
        await safe_edit_or_send(
            client, chat_id, msg,
            "✂️ **برش ویدیو:**\n\nلطفاً زمان شروع و پایان را به این صورت ارسال کنید:\n`00:00:10 - 00:00:30`",
            InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_vedit")]])
        )
        return

    if data == "vedit_watermark":
        USER_STATES[chat_id] = "AWAIT_VEDIT_WM"
        await safe_edit_or_send(
            client, chat_id, msg,
            "🏷 **درج واترمارک روی ویدیو:**\n\nمتن مورد نظر برای درج روی ویدیو را ارسال فرمایید (مثلاً آیدی کانال شما):",
            InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_vedit")]])
        )
        return

    if data.startswith("vedit_"):
        action = data.replace("vedit_", "")
        if action == "cancel":
            USER_STATES.pop(chat_id, None)
            USER_DATA.setdefault(chat_id, {}).pop("vedit_path", None)
            await safe_edit_or_send(client, chat_id, msg, "✅ ویرایشگر ویدیو با موفقیت بسته شد.", InlineKeyboard([[InlineKeyboardButton("🔙 منوی اصلی", "menu_main")]]))
            return

        src = USER_DATA.get(chat_id, {}).get("vedit_path")
        if not src or not os.path.exists(src):
            await safe_edit_or_send(client, chat_id, msg, "❌ ویدیویی برای ویرایش یافت نشد. لطفاً ابتدا یک ویدیو ارسال کنید.")
            return

        if action == "identify":
            status = await client.send_message(chat_id, "🎙 در حال استخراج و شناسایی موزیک داخل ویدیو با Shazam...")
            try:
                clip_p = os.path.join(WORK_DIR, f"clip_{chat_id}_{int(time.time())}.mp3")
                await asyncio.to_thread(audd.extract_audio_clip_sync, src, clip_p, 15)
                res = await asyncio.to_thread(audd.audd_recognize_sync, clip_p)
                if os.path.exists(clip_p):
                    try: os.remove(clip_p)
                    except OSError: pass

                if not res:
                    await status.edit_text("❌ آهنگی شناسایی نشد.")
                    return

                USER_DATA.setdefault(chat_id, {})["audd_found"] = res
                title = res["title"]
                artist = res["artist"]
                caption = (
                    "🎵 **موزیک شناسایی شد!**\n\n"
                    f"🎧 عنوان: **{title}**\n"
                    f"👤 خواننده: **{artist}**\n"
                    f"💿 آلبوم: {res.get('album', '')}\n\n"
                    "می‌توانید نسخه با کیفیت اورجینال این آهنگ را دریافت کنید:"
                )
                kb_dl = InlineKeyboard([
                    [InlineKeyboardButton("📥 دانلود نسخه باکیفیت ۳۲۰", f"audd_dl_{title} - {artist}")],
                    [InlineKeyboardButton("🔙 بازگشت به ویرایشگر", "menu_vedit")]
                ])
                if res.get("cover"):
                    try:
                        r = requests.get(res["cover"], timeout=5)
                        if r.status_code == 200:
                            cov_p = os.path.join(MEDIA_DIR, f"cov_{chat_id}.jpg")
                            with open(cov_p, "wb") as cf:
                                cf.write(r.content)
                            with open(cov_p, "rb") as cf:
                                await client.send_photo(chat_id, photo=cf, caption=caption, reply_markup=kb_dl)
                            await status.delete()
                            if os.path.exists(cov_p): os.remove(cov_p)
                            return
                    except Exception:
                        pass
                await status.edit_text(caption, reply_markup=kb_dl)
            except Exception as e:
                traceback.print_exc()
                await status.edit_text(f"❌ خطا در شناسایی موزیک: {e}")
            return

        label = vtools.VIDEO_EDIT_LABELS.get(action, action)
        status = await client.send_message(chat_id, f"⏳ در حال پردازش ویدیو ({label})... لطفاً صبور باشید...")
        try:
            res = await vtools.execute_video_action(src, action)
            out_p = res["output_path"]
            send_as = res["send_as"]

            await status.edit_text("⬆️ در حال ارسال به بله...")
            cap = f"🎬 **{label}**\n⚡️ پردازش شده توسط @tahasbot"
            kb_v = vtools.video_editor_kb()

            if send_as == "audio":
                with open(out_p, "rb") as af:
                    await client.send_audio(chat_id, audio=af, caption=cap, reply_markup=kb_v)
            elif send_as == "document":
                with open(out_p, "rb") as df:
                    await client.send_document(chat_id, document=df, caption=cap, reply_markup=kb_v)
            else:
                USER_DATA.setdefault(chat_id, {})["vedit_path"] = out_p
                with open(out_p, "rb") as vf:
                    await client.send_video(chat_id, video=vf, caption=cap, reply_markup=kb_v)

            await status.delete()
            log_activity(chat_id, "video_edit", label)
            if send_as in ["audio", "document"] and os.path.exists(out_p):
                try: os.remove(out_p)
                except OSError: pass
        except Exception as e:
            traceback.print_exc()
            await status.edit_text(f"❌ خطا در پردازش ویدیو:\n`{str(e)[:250]}`", reply_markup=InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به ویرایشگر", "menu_vedit")]]))
        return

    # --- Spotify Callbacks ---
    if data == "menu_music_spotify":
        await render_spotify_home(client, chat_id, msg)
        return

    if data == "sp_search_track":
        USER_STATES[chat_id] = "AWAIT_SP_TRACK_QUERY"
        await safe_edit_or_send(
            client, chat_id, msg,
            "🎵 **جستجوی آهنگ در اسپاتیفای:**\n\nلطفاً نام آهنگ یا خواننده را بنویسید:",
            InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_music_spotify")]])
        )
        return

    if data == "sp_search_artist":
        USER_STATES[chat_id] = "AWAIT_SP_ARTIST_QUERY"
        await safe_edit_or_send(
            client, chat_id, msg,
            "👤 **جستجوی خواننده در اسپاتیفای:**\n\nلطفاً نام خواننده یا گروه موسیقی را بنویسید:",
            InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_music_spotify")]])
        )
        return

    if data == "sp_search_album":
        USER_STATES[chat_id] = "AWAIT_SP_ALBUM_QUERY"
        await safe_edit_or_send(
            client, chat_id, msg,
            "💿 **جستجوی آلبوم در اسپاتیفای:**\n\nلطفاً نام آلبوم یا خواننده را بنویسید:",
            InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_music_spotify")]])
        )
        return

    if data == "sp_hottest":
        status_msg = await client.send_message(chat_id, "🔥 در حال دریافت داغ‌ترین‌های این هفته...")
        tracks = await asyncio.to_thread(rega_music.get_chart_tracks, 25)
        USER_DATA.setdefault(chat_id, {})["sp_track_results"] = tracks
        USER_DATA.setdefault(chat_id, {})["sp_track_query"] = "داغ‌ترین‌های هفته (Hottest)"
        await render_sp_tracks_page(client, chat_id, page=0, msg=status_msg)
        return

    if data == "sp_search_playlist":
        if is_playlist_locked("spotify"):
            await safe_edit_or_send(
                client, chat_id, msg,
                "⚠️ **بخش پلی‌لیست در حال حاضر غیرفعال است.**\n\n"
                "امکان جستجو و دانلود پلی‌لیست‌های اسپاتیفای موقتاً توسط مدیریت قفل شده است.",
                InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به منوی اسپاتیفای", "menu_music_spotify")]])
            )
            return
        USER_STATES[chat_id] = "AWAIT_SP_PLAYLIST_QUERY"
        await safe_edit_or_send(
            client, chat_id, msg,
            "📑 **جستجوی پلی‌لیست در اسپاتیفای:**\n\nلطفاً عنوان پلی‌لیست را بنویسید:",
            InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_music_spotify")]])
        )
        return

    if data.startswith("sp_trk_pg_"):
        pg_str = data.replace("sp_trk_pg_", "")
        pg = int(pg_str) if pg_str.isdigit() else 0
        await render_sp_tracks_page(client, chat_id, page=pg, msg=msg)
        return

    if data == "sp_trk_pg_curr":
        await render_sp_tracks_page(client, chat_id, page=0, msg=msg)
        return

    if data.startswith("sp_show_trk_"):
        trk_id = data.replace("sp_show_trk_", "")
        await render_sp_track_card(client, chat_id, trk_id, msg=msg)
        return

    if data.startswith("sp_dl_"):
        # Format: sp_dl_{128|320}_{track_id}
        parts = data.split("_")
        if len(parts) >= 4:
            bitrate_val = parts[2]
            track_id = parts[3]
            t = rega_music.get_track_info(track_id)
            if not t:
                for item in USER_DATA.get(chat_id, {}).get("sp_track_results", []):
                    if str(item.get("id")) == str(track_id):
                        t = item
                        break
            if not t:
                await safe_edit_or_send(client, chat_id, msg, "❌ اطلاعات آهنگ یافت نشد.")
                return

            status = await client.send_message(chat_id, f"⏳ در حال دانلود ترک با کیفیت {bitrate_val}kbps:\n🎵 **{t['title']}**...")
            try:
                audio_f, cover_f = await asyncio.to_thread(rega_music.download_track_sync, t, f"{bitrate_val}k")
                await status.edit_text("⬆️ در حال ارسال به بله...")
                dur = int(t.get("duration", 0))
                with open(audio_f, "rb") as af:
                    await client.send_audio(
                        chat_id,
                        audio=af,
                        caption=f"🎵 **{t['artist']} - {t['title']}** ({bitrate_val}kbps)\n⚡️ دانلود شده توسط @tahasbot",
                        title=f"{t['artist']} - {t['title']}",
                        duration=dur
                    )
                await status.delete()
                log_activity(chat_id, "download_spotify", t["title"])
                if os.path.exists(audio_f):
                    os.remove(audio_f)
                if cover_f and os.path.exists(cover_f):
                    os.remove(cover_f)
            except Exception as e:
                traceback.print_exc()
                await status.edit_text(f"❌ خطا در دانلود ترک:\n{e}")
        return

    if data.startswith("sp_alb_pg_"):
        pg_str = data.replace("sp_alb_pg_", "")
        pg = int(pg_str) if pg_str.isdigit() else 0
        await render_sp_albums_page(client, chat_id, page=pg, msg=msg)
        return

    if data == "sp_alb_pg_curr":
        await render_sp_albums_page(client, chat_id, page=0, msg=msg)
        return

    if data.startswith("sp_show_alb_"):
        alb_id = data.replace("sp_show_alb_", "")
        await render_sp_album_detail(client, chat_id, alb_id, page=0, msg=msg)
        return

    if data.startswith("sp_alb_trks_"):
        parts = data.replace("sp_alb_trks_", "").split("_")
        alb_id = parts[0]
        pg = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        await render_sp_album_detail(client, chat_id, alb_id, page=pg, msg=msg)
        return

    if data.startswith("sp_dl_alb_all_"):
        alb_id = data.replace("sp_dl_alb_all_", "")
        alb = rega_music.get_album_details(alb_id)
        if not alb or not alb.get("tracks"):
            await safe_edit_or_send(client, chat_id, msg, "❌ ترک‌های این آلبوم یافت نشد.")
            return

        tracks = alb["tracks"]
        status = await client.send_message(chat_id, f"📥 دانلود کامل آلبوم **{alb['title']}** آغاز شد ({len(tracks)} آهنگ)...")
        success_cnt = 0
        for idx, t in enumerate(tracks, 1):
            try:
                await status.edit_text(f"⏳ در حال دانلود و ارسال ترک {idx} از {len(tracks)}:\n🎵 **{t['title']}**...")
                audio_f, cover_f = await asyncio.to_thread(rega_music.download_track_sync, t, "320k")
                dur = int(t.get("duration", 0))
                with open(audio_f, "rb") as af:
                    await client.send_audio(
                        chat_id,
                        audio=af,
                        caption=f"💿 **آلبوم:** {alb['title']}\n🎵 ترک {idx} از {len(tracks)}: **{t['title']}**\n⚡️ دانلود شده توسط @tahasbot",
                        title=f"{t.get('artist', alb['artist'])} - {t['title']}",
                        duration=dur
                    )
                success_cnt += 1
                if os.path.exists(audio_f):
                    os.remove(audio_f)
                if cover_f and os.path.exists(cover_f):
                    os.remove(cover_f)
                await asyncio.sleep(1.5)
            except Exception as e:
                print(f"Error downloading album track {idx}: {e}", flush=True)

        await status.edit_text(f"✅ دانلود کامل آلبوم **{alb['title']}** پایان یافت!\nتعداد کل آهنگ‌های ارسال‌شده: {success_cnt}")
        log_activity(chat_id, "download_album", alb["title"])
        return

    if data.startswith("sp_art_pg_"):
        pg_str = data.replace("sp_art_pg_", "")
        pg = int(pg_str) if pg_str.isdigit() else 0
        await render_sp_artists_page(client, chat_id, page=pg, msg=msg)
        return

    if data == "sp_art_pg_curr":
        await render_sp_artists_page(client, chat_id, page=0, msg=msg)
        return

    if data.startswith("sp_show_art_"):
        art_id = data.replace("sp_show_art_", "")
        await render_sp_artist_detail(client, chat_id, art_id, msg=msg)
        return

    if data.startswith("sp_art_trks_"):
        art_id = data.replace("sp_art_trks_", "")
        status = await client.send_message(chat_id, "⏳ در حال استخراج برترین آهنگ‌های خواننده...")
        trks = await asyncio.to_thread(rega_music.get_artist_top_tracks, art_id, 25)
        USER_DATA.setdefault(chat_id, {})["sp_track_results"] = trks
        USER_DATA.setdefault(chat_id, {})["sp_track_query"] = "برترین‌های خواننده"
        await render_sp_tracks_page(client, chat_id, page=0, msg=status)
        return

    if data.startswith("sp_art_albs_"):
        art_id = data.replace("sp_art_albs_", "")
        status = await client.send_message(chat_id, "⏳ در حال استخراج آلبوم‌های خواننده...")
        albs = await asyncio.to_thread(rega_music.get_artist_albums, art_id, 20)
        USER_DATA.setdefault(chat_id, {})["sp_album_results"] = albs
        USER_DATA.setdefault(chat_id, {})["sp_album_query"] = "آلبوم‌های خواننده"
        await render_sp_albums_page(client, chat_id, page=0, msg=status)
        return

    if data.startswith("sp_pl_pg_"):
        pg_str = data.replace("sp_pl_pg_", "")
        pg = int(pg_str) if pg_str.isdigit() else 0
        await render_sp_playlists_page(client, chat_id, page=pg, msg=msg)
        return

    if data == "sp_pl_pg_curr":
        await render_sp_playlists_page(client, chat_id, page=0, msg=msg)
        return

    if data.startswith("sp_show_pl_"):
        pl_id = data.replace("sp_show_pl_", "")
        await render_sp_playlist_detail(client, chat_id, pl_id, page=0, msg=msg)
        return

    if data.startswith("sp_pl_trks_"):
        parts = data.replace("sp_pl_trks_", "").split("_")
        pl_id = parts[0]
        pg = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        await render_sp_playlist_detail(client, chat_id, pl_id, page=pg, msg=msg)
        return

    if data.startswith("sp_dl_pl_all_"):
        pl_id = data.replace("sp_dl_pl_all_", "")
        if is_playlist_locked("spotify"):
            await safe_edit_or_send(client, chat_id, msg, "⚠️ دانلود پلی‌لیست توسط مدیریت قفل شده است.")
            return
        pl = rega_music.get_playlist_details(pl_id)
        if not pl or not pl.get("tracks"):
            await safe_edit_or_send(client, chat_id, msg, "❌ ترک‌های این پلی‌لیست یافت نشد.")
            return

        tracks = pl["tracks"]
        status = await client.send_message(chat_id, f"📥 دانلود کامل پلی‌لیست **{pl['title']}** آغاز شد ({len(tracks)} آهنگ)...")
        success_cnt = 0
        for idx, t in enumerate(tracks, 1):
            try:
                await status.edit_text(f"⏳ در حال دانلود ترک {idx} از {len(tracks)}:\n🎵 **{t['title']}**...")
                audio_f, cover_f = await asyncio.to_thread(rega_music.download_track_sync, t, "320k")
                dur = int(t.get("duration", 0))
                with open(audio_f, "rb") as af:
                    await client.send_audio(
                        chat_id,
                        audio=af,
                        caption=f"📑 **پلی‌لیست:** {pl['title']}\n🎵 ترک {idx} از {len(tracks)}: **{t['title']}**\n⚡️ دانلود شده توسط @tahasbot",
                        title=f"{t.get('artist', 'Artist')} - {t['title']}",
                        duration=dur
                    )
                success_cnt += 1
                if os.path.exists(audio_f):
                    os.remove(audio_f)
                if cover_f and os.path.exists(cover_f):
                    os.remove(cover_f)
                await asyncio.sleep(1.5)
            except Exception as e:
                print(f"Error downloading playlist track {idx}: {e}", flush=True)

        await status.edit_text(f"✅ دانلود کامل پلی‌لیست **{pl['title']}** پایان یافت!\nتعداد کل آهنگ‌های ارسال‌شده: {success_cnt}")
        log_activity(chat_id, "download_playlist", pl["title"])
        return

    # --- SoundCloud Callbacks ---
    if data == "menu_music_sc":
        await render_sc_home(client, chat_id, msg)
        return

    if data == "sc_search_track":
        USER_STATES[chat_id] = "AWAIT_SC_TRACK_QUERY"
        await safe_edit_or_send(
            client, chat_id, msg,
            "🎵 **جستجوی آهنگ در ساندکلاد:**\n\nلطفاً نام آهنگ یا خواننده را بنویسید:",
            InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_music_sc")]])
        )
        return

    if data == "sc_search_artist":
        USER_STATES[chat_id] = "AWAIT_SC_ARTIST_QUERY"
        await safe_edit_or_send(
            client, chat_id, msg,
            "👤 **جستجوی هنرمند در ساندکلاد:**\n\nلطفاً نام هنرمند یا کانال را بنویسید:",
            InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_music_sc")]])
        )
        return

    if data == "sc_search_album":
        USER_STATES[chat_id] = "AWAIT_SC_ALBUM_QUERY"
        await safe_edit_or_send(
            client, chat_id, msg,
            "💿 **جستجوی آلبوم / ست در ساندکلاد:**\n\nلطفاً نام آلبوم یا ست را بنویسید:",
            InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_music_sc")]])
        )
        return

    if data == "sc_trending":
        status = await client.send_message(chat_id, "🔥 در حال دریافت برترین‌های ساندکلاد...")
        tracks = await asyncio.to_thread(rega_music.search_soundcloud_tracks, "trending", 20)
        USER_DATA.setdefault(chat_id, {})["sc_track_results"] = tracks
        USER_DATA.setdefault(chat_id, {})["sc_track_query"] = "برترین‌های ساندکلاد (Trending)"
        await render_sc_tracks_page(client, chat_id, page=0, msg=status)
        return

    if data == "sc_search_playlist":
        if is_playlist_locked("soundcloud"):
            await safe_edit_or_send(
                client, chat_id, msg,
                "⚠️ **بخش پلی‌لیست در حال حاضر غیرفعال است.**\n\n"
                "امکان جستجو و دانلود پلی‌لیست‌های ساندکلاد موقتاً توسط مدیریت قفل شده است.",
                InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به منوی ساندکلاد", "menu_music_sc")]])
            )
            return
        USER_STATES[chat_id] = "AWAIT_SC_PLAYLIST_QUERY"
        await safe_edit_or_send(
            client, chat_id, msg,
            "📑 **جستجوی پلی‌لیست در ساندکلاد:**\n\nلطفاً عنوان پلی‌لیست را بنویسید:",
            InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_music_sc")]])
        )
        return

    if data.startswith("sc_trk_pg_"):
        pg_str = data.replace("sc_trk_pg_", "")
        pg = int(pg_str) if pg_str.isdigit() else 0
        await render_sc_tracks_page(client, chat_id, page=pg, msg=msg)
        return

    if data == "sc_trk_pg_curr":
        await render_sc_tracks_page(client, chat_id, page=0, msg=msg)
        return

    if data.startswith("sc_show_trk_"):
        idx_str = data.replace("sc_show_trk_", "")
        idx = int(idx_str) if idx_str.isdigit() else 0
        await render_sc_track_card(client, chat_id, idx, msg=msg)
        return

    if data.startswith("sc_dl_"):
        # Format: sc_dl_{128|320}_{idx}
        parts = data.split("_")
        if len(parts) >= 4:
            bitrate_val = parts[2]
            idx = int(parts[3]) if parts[3].isdigit() else 0
            tracks = USER_DATA.get(chat_id, {}).get("sc_track_results", [])
            if idx < 0 or idx >= len(tracks):
                await safe_edit_or_send(client, chat_id, msg, "❌ اطلاعات آهنگ یافت نشد.")
                return
            t = tracks[idx]
            status = await client.send_message(chat_id, f"⏳ در حال دانلود از ساندکلاد با کیفیت {bitrate_val}kbps:\n🎵 **{t['title']}**...")
            try:
                audio_f, cover_f = await asyncio.to_thread(
                    rega_music.download_soundcloud_track,
                    t["url"], t["title"], t["artist"], f"{bitrate_val}k", t.get("thumb")
                )
                await status.edit_text("⬆️ در حال ارسال به بله...")
                dur = int(t.get("duration", 0))
                with open(audio_f, "rb") as af:
                    await client.send_audio(
                        chat_id,
                        audio=af,
                        caption=f"☁️ **{t['artist']} - {t['title']}** ({bitrate_val}kbps)\n⚡️ دانلود شده توسط @tahasbot",
                        title=f"{t['artist']} - {t['title']}",
                        duration=dur
                    )
                await status.delete()
                log_activity(chat_id, "download_soundcloud", t["title"])
                if os.path.exists(audio_f):
                    os.remove(audio_f)
                if cover_f and os.path.exists(cover_f):
                    os.remove(cover_f)
            except Exception as e:
                traceback.print_exc()
                await status.edit_text(f"❌ خطا در دانلود ساندکلاد:\n{e}")
        return

    if data.startswith("sc_alb_pg_"):
        pg_str = data.replace("sc_alb_pg_", "")
        pg = int(pg_str) if pg_str.isdigit() else 0
        await render_sc_albums_page(client, chat_id, page=pg, msg=msg)
        return

    if data == "sc_alb_pg_curr":
        await render_sc_albums_page(client, chat_id, page=0, msg=msg)
        return

    if data.startswith("sc_show_alb_"):
        idx_str = data.replace("sc_show_alb_", "")
        idx = int(idx_str) if idx_str.isdigit() else 0
        await render_sc_album_detail(client, chat_id, idx, msg=msg)
        return

    if data.startswith("sc_art_pg_"):
        pg_str = data.replace("sc_art_pg_", "")
        pg = int(pg_str) if pg_str.isdigit() else 0
        await render_sc_artists_page(client, chat_id, page=pg, msg=msg)
        return

    if data.startswith("sc_art_trks_"):
        idx_str = data.replace("sc_art_trks_", "")
        idx = int(idx_str) if idx_str.isdigit() else 0
        artists = USER_DATA.get(chat_id, {}).get("sc_artist_results", [])
        if 0 <= idx < len(artists):
            art = artists[idx]
            status = await client.send_message(chat_id, f"⏳ در حال جستجوی آثار «{art['name']}» در ساندکلاد...")
            trks = await asyncio.to_thread(rega_music.search_soundcloud_tracks, art["name"], 20)
            USER_DATA.setdefault(chat_id, {})["sc_track_results"] = trks
            USER_DATA.setdefault(chat_id, {})["sc_track_query"] = art["name"]
            await render_sc_tracks_page(client, chat_id, page=0, msg=status)
        return

    # --- YouTube Music Callbacks ---
    if data == "menu_music_ytm":
        await render_ytm_home(client, chat_id, msg)
        return

    if data == "ytm_search_track":
        USER_STATES[chat_id] = "AWAIT_YTM_TRACK_QUERY"
        await safe_edit_or_send(
            client, chat_id, msg,
            "🎵 **جستجوی آهنگ در یوتیوب موزیک:**\n\nلطفاً نام آهنگ یا خواننده را بنویسید:",
            InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_music_ytm")]])
        )
        return

    if data == "ytm_search_artist":
        USER_STATES[chat_id] = "AWAIT_YTM_ARTIST_QUERY"
        await safe_edit_or_send(
            client, chat_id, msg,
            "👤 **جستجوی خواننده در یوتیوب موزیک:**\n\nلطفاً نام خواننده را بنویسید:",
            InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_music_ytm")]])
        )
        return

    if data == "ytm_search_album":
        USER_STATES[chat_id] = "AWAIT_YTM_ALBUM_QUERY"
        await safe_edit_or_send(
            client, chat_id, msg,
            "💿 **جستجوی آلبوم در یوتیوب موزیک:**\n\nلطفاً نام آلبوم را بنویسید:",
            InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_music_ytm")]])
        )
        return

    if data == "ytm_trending":
        status = await client.send_message(chat_id, "🔥 در حال دریافت داغ‌ترین‌های یوتیوب موزیک...")
        tracks = await asyncio.to_thread(rega_music.search_ytm_tracks, "top hits 2026", 20)
        USER_DATA.setdefault(chat_id, {})["ytm_track_results"] = tracks
        USER_DATA.setdefault(chat_id, {})["ytm_track_query"] = "داغ‌ترین‌های یوتیوب موزیک (Top Music)"
        await render_ytm_tracks_page(client, chat_id, page=0, msg=status)
        return

    if data == "ytm_search_playlist":
        if is_playlist_locked("ytmusic"):
            await safe_edit_or_send(
                client, chat_id, msg,
                "⚠️ **بخش پلی‌لیست در حال حاضر غیرفعال است.**\n\n"
                "امکان جستجو و دانلود پلی‌لیست‌های یوتیوب موزیک موقتاً توسط مدیریت قفل شده است.",
                InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به منوی یوتیوب موزیک", "menu_music_ytm")]])
            )
            return
        USER_STATES[chat_id] = "AWAIT_YTM_PLAYLIST_QUERY"
        await safe_edit_or_send(
            client, chat_id, msg,
            "📑 **جستجوی پلی‌لیست در یوتیوب موزیک:**\n\nلطفاً عنوان پلی‌لیست را بنویسید:",
            InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_music_ytm")]])
        )
        return

    if data.startswith("ytm_trk_pg_"):
        pg_str = data.replace("ytm_trk_pg_", "")
        pg = int(pg_str) if pg_str.isdigit() else 0
        await render_ytm_tracks_page(client, chat_id, page=pg, msg=msg)
        return

    if data == "ytm_trk_pg_curr":
        await render_ytm_tracks_page(client, chat_id, page=0, msg=msg)
        return

    if data.startswith("ytm_show_trk_"):
        vid_id = data.replace("ytm_show_trk_", "")
        await render_ytm_track_card(client, chat_id, vid_id, msg=msg)
        return

    if data.startswith("ytm_dl_"):
        # Format: ytm_dl_{128|320}_{vid_id}
        parts = data.split("_")
        if len(parts) >= 4:
            bitrate_val = parts[2]
            vid_id = parts[3]
            t = None
            for item in USER_DATA.get(chat_id, {}).get("ytm_track_results", []):
                if str(item.get("id")) == str(vid_id):
                    t = item
                    break
            if not t:
                t = {"id": vid_id, "title": "Track", "artist": "YouTube Music", "duration": 0, "thumb": f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"}

            status = await client.send_message(chat_id, f"⏳ در حال دانلود ترک با کیفیت {bitrate_val}kbps:\n🎵 **{t['title']}**...")
            try:
                audio_f, cover_f = await asyncio.to_thread(
                    rega_music.download_ytm_track,
                    vid_id, t["title"], t["artist"], f"{bitrate_val}k", t.get("thumb")
                )
                await status.edit_text("⬆️ در حال ارسال به بله...")
                dur = int(t.get("duration", 0))
                with open(audio_f, "rb") as af:
                    await client.send_audio(
                        chat_id,
                        audio=af,
                        caption=f"🔴 **{t['artist']} - {t['title']}** ({bitrate_val}kbps)\n⚡️ دانلود شده توسط @tahasbot",
                        title=f"{t['artist']} - {t['title']}",
                        duration=dur
                    )
                await status.delete()
                log_activity(chat_id, "download_ytmusic", t["title"])
                if os.path.exists(audio_f):
                    os.remove(audio_f)
                if cover_f and os.path.exists(cover_f):
                    os.remove(cover_f)
            except Exception as e:
                traceback.print_exc()
                await status.edit_text(f"❌ خطا در دانلود یوتیوب موزیک:\n{e}")
        return

    if data.startswith("ytm_alb_pg_"):
        pg_str = data.replace("ytm_alb_pg_", "")
        pg = int(pg_str) if pg_str.isdigit() else 0
        await render_ytm_albums_page(client, chat_id, page=pg, msg=msg)
        return

    if data == "ytm_alb_pg_curr":
        await render_ytm_albums_page(client, chat_id, page=0, msg=msg)
        return

    if data.startswith("ytm_show_alb_"):
        vid_id = data.replace("ytm_show_alb_", "")
        await render_ytm_album_detail(client, chat_id, vid_id, msg=msg)
        return

    if data.startswith("ytm_art_pg_"):
        pg_str = data.replace("ytm_art_pg_", "")
        pg = int(pg_str) if pg_str.isdigit() else 0
        await render_ytm_artists_page(client, chat_id, page=pg, msg=msg)
        return

    if data.startswith("ytm_art_trks_"):
        idx_str = data.replace("ytm_art_trks_", "")
        idx = int(idx_str) if idx_str.isdigit() else 0
        artists = USER_DATA.get(chat_id, {}).get("ytm_artist_results", [])
        if 0 <= idx < len(artists):
            art = artists[idx]
            status = await client.send_message(chat_id, f"⏳ در حال جستجوی آثار «{art['name']}» در یوتیوب موزیک...")
            trks = await asyncio.to_thread(rega_music.search_ytm_tracks, art["name"], 20)
            USER_DATA.setdefault(chat_id, {})["ytm_track_results"] = trks
            USER_DATA.setdefault(chat_id, {})["ytm_track_query"] = art["name"]
            await render_ytm_tracks_page(client, chat_id, page=0, msg=status)
        return

    if data in ["sp_noop", "sc_noop", "ytm_noop"]:
        return

    # --- Playlists Navigation ---
    if data == "menu_playlists":
        USER_STATES.pop(chat_id, None)
        await render_user_playlists(client, chat_id, page=0, msg=msg)
        return

    if data == "pl_create":
        USER_STATES[chat_id] = "AWAIT_PLAYLIST_NAME"
        await safe_edit_or_send(
            client, chat_id, msg,
            "➕ **ایجاد پلی‌لیست اختصاصی جدید:**\n\nلطفاً یک نام برای پلی‌لیست خود ارسال کنید (مثلاً: ریمیکس‌های شاد، آهنگ‌های جاده، ...):",
            InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_playlists")]])
        )
        return

    if data.startswith("pl_view_"):
        pl_id = data.replace("pl_view_", "")
        await render_playlist_detail(client, chat_id, pl_id, msg=msg)
        return

    if data.startswith("pl_pg_"):
        pg_str = data.replace("pl_pg_", "")
        pg = int(pg_str) if pg_str.isdigit() else 0
        await render_user_playlists(client, chat_id, page=pg, msg=msg)
        return

    if data.startswith("pl_del_"):
        pl_id = data.replace("pl_del_", "")
        if db is not None:
            try:
                from bson import ObjectId
                db.playlists.delete_one({"_id": ObjectId(pl_id)})
            except Exception:
                pass
        await client.send_message(chat_id, "✅ پلی‌لیست با موفقیت حذف شد.")
        await render_user_playlists(client, chat_id, page=0, msg=msg)
        return

    if data.startswith("pl_play_") or data.startswith("pl_shuffle_"):
        is_shuf = "shuffle" in data
        pl_id = data.replace("pl_shuffle_", "").replace("pl_play_", "")
        doc = None
        if db is not None:
            try:
                from bson import ObjectId
                doc = db.playlists.find_one({"_id": ObjectId(pl_id)})
            except Exception:
                pass
        if not doc or not doc.get("tracks"):
            await safe_edit_or_send(client, chat_id, msg, "❌ هیچ آهنگی در این پلی‌لیست وجود ندارد.")
            return

        tracks = list(doc["tracks"])
        if is_shuf:
            random.shuffle(tracks)

        status = await client.send_message(chat_id, f"🎵 در حال آماده‌سازی و ارسال {len(tracks)} آهنگ از پلی‌لیست «{doc.get('name')}»...")
        for i, t in enumerate(tracks, 1):
            try:
                audio_file, cover_file = await asyncio.to_thread(rega_music.download_track_sync, t, "320k")
                dur = int(t.get("duration", 0))
                with open(audio_file, "rb") as af:
                    await client.send_audio(
                        chat_id, audio=af,
                        caption=f"🎵 ترک {i} از {len(tracks)}: **{t.get('artist')} - {t.get('title')}**\n⚡️ @tahasbot",
                        title=f"{t.get('artist')} - {t.get('title')}", duration=dur
                    )
                if os.path.exists(audio_file): os.remove(audio_file)
                if cover_file and os.path.exists(cover_file): os.remove(cover_file)
                await asyncio.sleep(1)
            except Exception as e:
                print(f"Playlist track {i} play error: {e}", flush=True)
        await status.edit_text("✅ تمام آهنگ‌های پلی‌لیست ارسال شدند!")
        return

    if data == "pl_noop":
        return

    # --- AI Chat Navigation ---
    if data == "menu_ai":
        USER_STATES[chat_id] = "AI_CHAT"
        USER_DATA.setdefault(chat_id, {})["ai_history"] = []
        text = (
            "🤖 **دستیار هوش مصنوعی TMulti فعال شد!**\n\n"
            "اکنون می‌توانید هر سوالی دارید بپرسید، یا درخواست پیشنهاد موسیقی و اطلاعات بدهید.\n\n"
            "برای پایان گفتگو در هر لحظه دکمه زیر را لمس کنید:"
        )
        kb = InlineKeyboard([[InlineKeyboardButton("❌ خروج از گفتگوی هوش مصنوعی", "ai_exit")]])
        await safe_edit_or_send(client, chat_id, msg, text, kb)
        return

    if data == "ai_exit":
        USER_STATES.pop(chat_id, None)
        name = (query.author.first_name if query.author else "") or "کاربر"
        text = menus.get_menu_text("main_menu_welcome", lang=lang, name=name)
        kb = menus.get_main_keyboard(chat_id, get_user_lang, is_admin)
        await safe_edit_or_send(client, chat_id, msg, "✅ گفتگوی هوش مصنوعی به پایان رسید.\n\n" + text, kb)
        return

    # --- Support / Contact Developer ---
    if data == "contact_admin":
        USER_STATES[chat_id] = "AWAIT_SUPPORT_MSG"
        text = (
            "📩 **ارتباط با مدیریت و سازنده ربات:**\n\n"
            "پیام، نظر، پیشنهاد یا گزارش باگ خود را در قالب یک پیام ارسال فرمایید.\n"
            "پیام شما به صورت تیکت مستقیماً برای مدیر اصلی (@TahatheGoat) ارسال و پاسخ داده خواهد شد:"
        )
        kb = InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_main")]])
        await safe_edit_or_send(client, chat_id, msg, text, kb)
        return

    # --- Pinterest Navigation ---
    if data == "menu_pinterest":
        kb = menus.get_image_platform_keyboard(chat_id, "pinterest")
        text = (
            "📌 **پینترست (Pinterest):**\n\n"
            "شما می‌توانید لینک هر پین یا ویدیوی پینترست را ارسال کنید تا دانلود شود، "
            "یا با کلمه کلیدی در پینترست جستجو فرمایید:"
        )
        await safe_edit_or_send(client, chat_id, msg, text, kb)
        return

    if data == "pt_search":
        USER_STATES[chat_id] = "AWAIT_PT_SEARCH"
        await safe_edit_or_send(
            client, chat_id, msg,
            "🔍 **جستجو در پینترست:**\n\nکلمه کلیدی یا موضوع دلخواه را بنویسید (فارسی یا انگلیسی):",
            InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_pinterest")]])
        )
        return

    if data.startswith("pt_pg_"):
        pg_str = data.replace("pt_pg_", "")
        pg = int(pg_str) if pg_str.isdigit() else 0
        await render_pinterest_page(client, chat_id, page=pg, msg=msg)
        return

    if data.startswith("pt_dl_"):
        idx_str = data.replace("pt_dl_", "")
        idx = int(idx_str) if idx_str.isdigit() else 0
        pins = USER_DATA.get(chat_id, {}).get("pt_pins", [])
        if 0 <= idx < len(pins):
            p = pins[idx]
            status = await client.send_message(chat_id, "⏳ در حال دریافت پین با کیفیت اصلی...")
            try:
                res = await asyncio.to_thread(soc.pinterest_pin_download_sync, p.get("id"))
                if not res or not res.get("path"):
                    await status.edit_text("❌ خطا در دانلود این پین.")
                    return
                fpath = res["path"]
                caption = f"📌 **{p.get('title', 'Pinterest')}**\n⚡️ @tahasbot"
                if res["type"] == "video":
                    with open(fpath, "rb") as vf:
                        await client.send_video(chat_id, video=vf, caption=caption)
                else:
                    with open(fpath, "rb") as pf:
                        await client.send_photo(chat_id, photo=pf, caption=caption)
                await status.delete()
                log_activity(chat_id, "download_pinterest", p.get("title", ""))
                if os.path.exists(fpath): os.remove(fpath)
            except Exception as e:
                traceback.print_exc()
                await status.edit_text(f"❌ خطا در دانلود: {e}")
        return

    if data == "pt_noop":
        return

    # --- Pexels Navigation ---
    if data == "menu_pexels":
        kb = menus.get_image_platform_keyboard(chat_id, "pexels")
        text = (
            "🖼 **پکسلز (Pexels Images):**\n\n"
            "دسترسی به میلیون‌ها تصویر با کیفیت فوق‌العاده بالا و رایگان.\n"
            "کلمه کلیدی مورد نظر خود را برای جستجو وارد کنید:"
        )
        await safe_edit_or_send(client, chat_id, msg, text, kb)
        return

    if data == "px_search":
        USER_STATES[chat_id] = "AWAIT_PX_SEARCH"
        await safe_edit_or_send(
            client, chat_id, msg,
            "🔍 **جستجو در پکسلز (Pexels):**\n\nکلمه کلیدی را بنویسید (مثلاً: nature, cars, dark aesthetic, ...):",
            InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_pexels")]])
        )
        return

    if data.startswith("px_pg_"):
        pg_str = data.replace("px_pg_", "")
        pg = int(pg_str) if pg_str.isdigit() else 1
        await render_pexels_page(client, chat_id, page=pg, msg=msg)
        return

    if data.startswith("px_dl_"):
        idx_str = data.replace("px_dl_", "")
        idx = int(idx_str) if idx_str.isdigit() else 0
        photos = USER_DATA.get(chat_id, {}).get("px_results", [])
        if 0 <= idx < len(photos):
            ph = photos[idx]
            status = await client.send_message(chat_id, "⏳ در حال دانلود تصویر اورجینال با بالاترین وضوح...")
            try:
                out_p = os.path.join(MEDIA_DIR, f"px_{ph['id']}_{int(time.time())}.jpg")
                await asyncio.to_thread(soc.download_url_to_file, ph["large"], out_p)
                caption = f"📸 عکاس: **{ph['photographer']}**\n⚡️ دانلود شده توسط @tahasbot"
                with open(out_p, "rb") as pf:
                    await client.send_photo(chat_id, photo=pf, caption=caption)
                await status.delete()
                log_activity(chat_id, "download_pexels", ph["id"])
                if os.path.exists(out_p): os.remove(out_p)
            except Exception as e:
                traceback.print_exc()
                await status.edit_text(f"❌ خطا: {e}")
        return

    if data == "px_noop":
        return

    # --- Google Images Navigation ---
    if data == "menu_google_images":
        kb = menus.get_image_platform_keyboard(chat_id, "google")
        text = "🔍 **جستجوی تصاویر گوگل (Google Images):**\n\nکلمه کلیدی دلخواه را برای دریافت تصاویر باکیفیت بنویسید:"
        await safe_edit_or_send(client, chat_id, msg, text, kb)
        return

    if data == "gi_search":
        USER_STATES[chat_id] = "AWAIT_GI_SEARCH"
        await safe_edit_or_send(
            client, chat_id, msg,
            "🔍 **جستجو در گوگل:**\n\nکلمه یا عبارت مورد نظر خود را ارسال فرمایید:",
            InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_google_images")]])
        )
        return

    # --- Shazam / AudD Navigation ---
    if data == "menu_audd":
        USER_STATES[chat_id] = "AWAIT_AUDD_UPLOAD"
        text = (
            "🎙 **شناسایی موزیک با صدا (Shazam / AudD):**\n\n"
            "یک فایل صوتی، ویس ضبط‌شده، یا یک ویدیو بفرستید تا نام آهنگ، خواننده، آلبوم و کاور اصلی بلافاصله برای شما استخراج شود."
        )
        kb = InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]])
        await safe_edit_or_send(client, chat_id, msg, text, kb)
        return

    if data.startswith("audd_dl_"):
        query_val = data.replace("audd_dl_", "").strip()
        status = await client.send_message(chat_id, f"🔍 در حال یافتن و دانلود فایل صوتی: **{query_val}**...")
        try:
            results = await asyncio.to_thread(rega_music.search_tracks, query_val, 1)
            if results:
                t = results[0]
                audio_f, cover_f = await asyncio.to_thread(rega_music.download_track_sync, t, "320k")
                dur = int(t.get("duration", 0))
                with open(audio_f, "rb") as af:
                    await client.send_audio(
                        chat_id, audio=af,
                        caption=f"🎵 **{t['artist']} - {t['title']}** (320kbps)\n⚡️ شناسایی شده توسط @tahasbot",
                        title=f"{t['artist']} - {t['title']}", duration=dur
                    )
                await status.delete()
                log_activity(chat_id, "download_audd", query_val)
                if os.path.exists(audio_f): os.remove(audio_f)
                if cover_f and os.path.exists(cover_f): os.remove(cover_f)
            else:
                await status.edit_text("❌ نسخه صوتی با کیفیت پیدا نشد.")
        except Exception as e:
            traceback.print_exc()
            await status.edit_text(f"❌ خطا: {e}")
        return

    if data == "menu_music_tools":
        kb = menus.get_music_tools_keyboard(chat_id, get_user_lang)
        await safe_edit_or_send(client, chat_id, msg, "🎵 **ابزارهای ویرایش صدا و موزیک:**", kb)
        return

    if data == "menu_image_tools":
        kb = menus.get_image_tools_keyboard(chat_id, get_user_lang)
        await safe_edit_or_send(client, chat_id, msg, "🎨 **ابزارهای ویرایش تصویر:**", kb)
        return

    # --- Music Track Download Callback (dl_track_<id>) ---
    if data.startswith("dl_track_"):
        track_id = data.replace("dl_track_", "").strip()
        status_msg = await client.send_message(chat_id, "⏳ در حال دانلود و آماده‌سازی فایل صوتی...")

        try:
            # Query track info from Deezer API
            r = requests.get(f"{rega_music.DEEZER_BASE}/track/{track_id}", timeout=10)
            if r.status_code != 200:
                await status_msg.edit_text("❌ آهنگ مورد نظر در دسترس نیست.")
                return

            t = r.json()
            track_meta = {
                "id": str(t.get("id")),
                "title": t.get("title", "Track"),
                "artist": t.get("artist", {}).get("name", "Artist"),
                "album": t.get("album", {}).get("title", ""),
                "duration": int(t.get("duration", 0)),
                "thumb": t.get("album", {}).get("cover_big") or t.get("album", {}).get("cover_medium") or "",
                "isrc": t.get("isrc") or ""
            }

            audio_file, cover_file = await asyncio.to_thread(rega_music.download_track_sync, track_meta)

            await status_msg.edit_text("⬆️ در حال ارسال به بله...")
            caption = f"🎵 **{track_meta['artist']} - {track_meta['title']}**\n⚡️ دانلود شده توسط @tahasbot"

            with open(audio_file, "rb") as af:
                await client.send_audio(
                    chat_id,
                    audio=af,
                    caption=caption,
                    title=f"{track_meta['artist']} - {track_meta['title']}",
                    duration=track_meta["duration"]
                )

            await status_msg.delete()
            log_activity(chat_id, "download_music", track_meta["title"])

            # Cleanup
            if os.path.exists(audio_file):
                os.remove(audio_file)
            if cover_file and os.path.exists(cover_file):
                os.remove(cover_file)

        except Exception as e:
            traceback.print_exc()
            await status_msg.edit_text(f"❌ خطا در دانلود آهنگ:\n`{str(e)[:200]}`")
        return

    # --- YouTube Quality Selection Callback (dl_yt_<fmt>_<quality>) ---
    if data.startswith("dl_yt_"):
        parts = data.split("_")
        fmt = parts[2]       # mp4 or mp3
        quality = parts[3]   # 720p, 480p, audio
        target_url = USER_DATA.get(chat_id, {}).get("last_yt_url", "")

        if not target_url:
            await msg.edit_text("❌ زمان انتخاب سپری شده است. لطفاً لینک را مجدداً ارسال کنید.")
            return

        status_msg = await client.send_message(chat_id, "⏳ اتصال به سرور دانلود اختصاصی...")

        slice_mb = get_video_slice_limit_mb()
        # Dispatch heavy job to GitHub Actions Runner
        dispatched = dispatch_github_worker(
            chat_id=chat_id,
            message_id=status_msg.id,
            url=target_url,
            fmt=fmt,
            quality=quality,
            slice_mb=slice_mb
        )

        if dispatched:
            await status_msg.edit_text("⚡️ **درخواست به سرور پرسرعت ارسال شد.**\nفایل به زودی برای شما ارسال خواهد شد...")
        else:
            await status_msg.edit_text("⚠️ سرور در دسترس نیست، دانلود مستقیم روی رندر آغاز شد...")
            # Fallback in-process download
            try:
                res = await asyncio.to_thread(download_social_media_sync, target_url, "yt")
                file_path = res["file"]
                if res["is_audio"]:
                    with open(file_path, "rb") as af:
                        await client.send_audio(chat_id, audio=af, caption="🎵 دانلود شده توسط @tahasbot")
                else:
                    with open(file_path, "rb") as vf:
                        await client.send_video(chat_id, video=vf, caption="🎬 دانلود شده توسط @tahasbot")
                await status_msg.delete()
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception as e:
                await status_msg.edit_text(f"❌ دانلود با خطا مواجه شد:\n{e}")
        return

    # --- Audio Tools Filters (mtool_*) ---
    effect_map = {
        "mtool_slow": "slow",
        "mtool_night": "night",
        "mtool_eight": "eight",
        "mtool_bass": "bass",
        "mtool_vocal": "vocal"
    }
    if data in effect_map:
        eff_code = effect_map[data]
        src = USER_DATA.get(chat_id, {}).get("audio_path")
        if not src or not os.path.exists(src):
            await msg.edit_text(
                "⚠️ فایلی برای اعمال افکت یافت نشد. لطفاً ابتدا یک فایل صوتی ارسال کنید.",
                reply_markup=InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت", "menu_music_tools")]])
            )
            return

        eff_label = mtools.AUDIO_FILTERS[eff_code]["label_fa"]
        status_msg = await client.send_message(chat_id, f"🎛 در حال اعمال افکت **{eff_label}**...")
        try:
            out_file = await asyncio.to_thread(mtools.process_audio_filter, src, eff_code)
            probe = await asyncio.to_thread(mtools.probe_audio_info, out_file)
            with open(out_file, "rb") as af:
                await client.send_audio(
                    chat_id,
                    audio=af,
                    title=f"{probe.get('title','Track')} ({eff_label})",
                    duration=int(probe.get("duration", 0)),
                    caption=f"🎧 **{eff_label}**\n⚡️ پردازش شده توسط @tahasbot"
                )
            await status_msg.delete()
            if os.path.exists(out_file):
                os.remove(out_file)
        except Exception as e:
            traceback.print_exc()
            await status_msg.edit_text(f"❌ خطا در پردازش صدا: {e}")
        return

    # 2. Speed / Pitch presets
    if data == "mtool_speed":
        kb = InlineKeyboard([
            [InlineKeyboardButton("🐌 0.75x", "mtoolspeed_0.75"), InlineKeyboardButton("⚡ 1.25x", "mtoolspeed_1.25")],
            [InlineKeyboardButton("🚀 1.5x", "mtoolspeed_1.5"), InlineKeyboardButton("🏎 2.0x", "mtoolspeed_2.0")],
            [InlineKeyboardButton("🔙 بازگشت", "menu_music_tools")]
        ])
        await safe_edit_or_send(client, chat_id, msg, "⏩ **سرعت مورد نظر را انتخاب کنید:**", kb)
        return

    if data.startswith("mtoolspeed_"):
        spd = float(data.split("_")[1])
        src = USER_DATA.get(chat_id, {}).get("audio_path")
        if not src or not os.path.exists(src):
            await safe_edit_or_send(client, chat_id, msg, "⚠️ فایلی برای تغییر سرعت یافت نشد. لطفاً ابتدا یک فایل صوتی ارسال کنید.")
            return
        status_msg = await client.send_message(chat_id, f"⏳ در حال تغییر سرعت به {spd}x...")
        try:
            out_file = await asyncio.to_thread(mtools.change_audio_speed, src, spd)
            probe = await asyncio.to_thread(mtools.probe_audio_info, out_file)
            with open(out_file, "rb") as af:
                await client.send_audio(
                    chat_id,
                    audio=af,
                    title=f"{probe.get('title','Track')} ({spd}x)",
                    duration=int(probe.get("duration", 0)),
                    caption=f"⏩ **سرعت {spd}x**\n⚡️ پردازش شده توسط @tahasbot",
                    reply_markup=menus.get_music_tools_keyboard(chat_id, get_user_lang)
                )
            await status_msg.delete()
            if os.path.exists(out_file): os.remove(out_file)
        except Exception as e:
            await status_msg.edit_text(f"❌ خطا: {e}")
        return

    # 3. Audio Trimming
    if data == "mtool_trim":
        USER_STATES[chat_id] = "AWAIT_MTOOL_TRIM"
        await safe_edit_or_send(
            client, chat_id, msg,
            "✂️ **برش موزیک (ساخت رینگتون)**\n\nلطفاً زمان شروع و پایان را به این صورت ارسال کنید:\n`00:15 - 00:45` یا `15 - 45`",
            InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_music_tools")]])
        )
        return

    # 4. Format Conversion
    if data == "mtool_convert":
        kb = InlineKeyboard([
            [InlineKeyboardButton("🎵 MP3 (320 kbps)", "mtoolconv_mp3"), InlineKeyboardButton("💎 FLAC (Lossless)", "mtoolconv_flac")],
            [InlineKeyboardButton("🍎 M4A (AAC 256k)", "mtoolconv_m4a"), InlineKeyboardButton("📻 WAV (Studio Uncompressed)", "mtoolconv_wav")],
            [InlineKeyboardButton("🔙 بازگشت", "menu_music_tools")]
        ])
        await safe_edit_or_send(client, chat_id, msg, "🔄 **فرمت صوتی مورد نظر را انتخاب کنید:**", kb)
        return

    if data.startswith("mtoolconv_"):
        fmt = data.split("_")[1]
        src = USER_DATA.get(chat_id, {}).get("audio_path")
        if not src or not os.path.exists(src):
            await safe_edit_or_send(client, chat_id, msg, "⚠️ فایلی برای تبدیل یافت نشد. لطفاً ابتدا یک فایل صوتی ارسال کنید.")
            return
        status_msg = await client.send_message(chat_id, f"⏳ در حال تبدیل به فرمت {fmt.upper()}...")
        try:
            out_file = await asyncio.to_thread(mtools.convert_audio_format, src, fmt)
            probe = await asyncio.to_thread(mtools.probe_audio_info, out_file)
            cap = f"🎵 **فرمت {fmt.upper()}**\n⚡️ پردازش شده توسط @tahasbot"
            kb = menus.get_music_tools_keyboard(chat_id, get_user_lang)
            if fmt in ["flac", "wav"]:
                with open(out_file, "rb") as df:
                    await client.send_document(chat_id, document=df, caption=cap, reply_markup=kb)
            else:
                with open(out_file, "rb") as af:
                    await client.send_audio(chat_id, audio=af, caption=cap, reply_markup=kb)
            await status_msg.delete()
            if os.path.exists(out_file): os.remove(out_file)
        except Exception as e:
            await status_msg.edit_text(f"❌ خطا: {e}")
        return

    # 5. Tag & Cover Editor
    if data == "mtool_tags":
        USER_STATES[chat_id] = "AWAIT_MTOOL_TAGS"
        await safe_edit_or_send(
            client, chat_id, msg,
            "🏷 **ویرایش مشخصات و تگ‌های آهنگ**\n\nعنوان آهنگ و نام خواننده را در یک خط با علامت `-` یا `|` بفرستید:\n`نام آهنگ - نام خواننده`",
            InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_music_tools")]])
        )
        return

    # --- Image Tools (itool_*) ---
    if data.startswith("itool_") or data.startswith("itoolconv_") or data.startswith("itoolcrop_") or data.startswith("itoolfltr_"):
        src = USER_DATA.get(chat_id, {}).get("image_path")
        if not src or not os.path.exists(src):
            await safe_edit_or_send(
                client, chat_id, msg,
                "⚠️ تصویری برای ویرایش یافت نشد. لطفاً ابتدا یک عکس ارسال کنید.",
                InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت", "menu_image_tools")]])
            )
            return

        if data == "itool_rembg":
            status_msg = await client.send_message(chat_id, "🪄 در حال حذف پس‌زمینه تصویر...")
            try:
                out_img = await asyncio.to_thread(itools.remove_image_background, src)
                with open(out_img, "rb") as df:
                    await client.send_document(chat_id, document=df, caption="🪄 **پس‌زمینه با موفقیت حذف شد (PNG شفاف)**", reply_markup=menus.get_image_tools_keyboard(chat_id, get_user_lang))
                await status_msg.delete()
                if os.path.exists(out_img): os.remove(out_img)
            except Exception as e:
                await status_msg.edit_text(f"❌ خطا در حذف پس‌زمینه: {e}")
            return

        if data == "itool_compress":
            status_msg = await client.send_message(chat_id, "🗜 در حال فشرده‌سازی هوشمند تصویر...")
            try:
                out_img, orig_s, new_s, saved = await asyncio.to_thread(itools.smart_compress_image, src)
                caption = f"🗜 حجم کاهش یافت: {saved}%\n({orig_s//1024}KB ➔ {new_s//1024}KB)"
                with open(out_img, "rb") as f:
                    await client.send_photo(chat_id, photo=f, caption=caption, reply_markup=menus.get_image_tools_keyboard(chat_id, get_user_lang))
                await status_msg.delete()
                if os.path.exists(out_img): os.remove(out_img)
            except Exception as e:
                await status_msg.edit_text(f"❌ خطا در فشرده‌سازی: {e}")
            return

        if data == "itool_convert":
            kb = InlineKeyboard([
                [InlineKeyboardButton("🖼 PNG (با شفافیت)", "itoolconv_png"), InlineKeyboardButton("📸 JPG / JPEG", "itoolconv_jpg")],
                [InlineKeyboardButton("🌐 WebP (وب و استیکر)", "itoolconv_webp"), InlineKeyboardButton("📄 PDF (سند)", "itoolconv_pdf")],
                [InlineKeyboardButton("🔙 بازگشت", "menu_image_tools")]
            ])
            await safe_edit_or_send(client, chat_id, msg, "🔄 **فرمت خروجی مورد نظر را انتخاب کنید:**", kb)
            return

        if data.startswith("itoolconv_"):
            fmt = data.split("_")[1]
            status_msg = await client.send_message(chat_id, f"⏳ در حال تبدیل تصویر به {fmt.upper()}...")
            try:
                out_img = await asyncio.to_thread(itools.convert_image_format, src, fmt)
                with open(out_img, "rb") as f:
                    if fmt in ["png", "jpg", "webp"]:
                        await client.send_photo(chat_id, photo=f, caption=f"🔄 **تبدیل به {fmt.upper()} انجام شد.**", reply_markup=menus.get_image_tools_keyboard(chat_id, get_user_lang))
                    else:
                        await client.send_document(chat_id, document=f, caption=f"🔄 **تبدیل به {fmt.upper()} انجام شد.**", reply_markup=menus.get_image_tools_keyboard(chat_id, get_user_lang))
                await status_msg.delete()
                if os.path.exists(out_img): os.remove(out_img)
            except Exception as e:
                await status_msg.edit_text(f"❌ خطا: {e}")
            return

        if data == "itool_crop":
            kb = InlineKeyboard([
                [InlineKeyboardButton("🔲 ۱:۱ مربع (پروفایل / آواتار)", "itoolcrop_1:1")],
                [InlineKeyboardButton("🖥 ۱۶:۹ افقی (یوتیوب / دسکتاپ)", "itoolcrop_16:9")],
                [InlineKeyboardButton("📱 ۹:۱۶ عمودی (استوری / ریلز)", "itoolcrop_9:16")],
                [InlineKeyboardButton("🔙 بازگشت", "menu_image_tools")]
            ])
            await safe_edit_or_send(client, chat_id, msg, "📐 **نسبت ابعاد مورد نظر را انتخاب کنید:**", kb)
            return

        if data.startswith("itoolcrop_"):
            ratio = data.split("_")[1]
            status_msg = await client.send_message(chat_id, f"⏳ در حال برش با نسبت {ratio}...")
            try:
                out_img = await asyncio.to_thread(itools.crop_resize_preset, src, ratio)
                with open(out_img, "rb") as f:
                    await client.send_photo(chat_id, photo=f, caption=f"📐 **برش با نسبت {ratio} انجام شد.**", reply_markup=menus.get_image_tools_keyboard(chat_id, get_user_lang))
                await status_msg.delete()
                if os.path.exists(out_img): os.remove(out_img)
            except Exception as e:
                await status_msg.edit_text(f"❌ خطا: {e}")
            return

        if data == "itool_filter":
            kb = InlineKeyboard([
                [InlineKeyboardButton("✨ افزایش شارپنس و وضوح", "itoolfltr_sharpen")],
                [InlineKeyboardButton("🌈 افکت HDR و تقویت رنگ", "itoolfltr_hdr")],
                [InlineKeyboardButton("🎞 سیاه و سفید سینمایی", "itoolfltr_bw")],
                [InlineKeyboardButton("☕ تم نوستالژی و وینتیج", "itoolfltr_vintage")],
                [InlineKeyboardButton("🔙 بازگشت", "menu_image_tools")]
            ])
            await safe_edit_or_send(client, chat_id, msg, "🎨 **فیلتر مورد نظر را انتخاب کنید:**", kb)
            return

        if data.startswith("itoolfltr_"):
            flt = data.split("_")[1]
            status_msg = await client.send_message(chat_id, "🎨 در حال اعمال فیلتر...")
            try:
                out_img = await asyncio.to_thread(itools.apply_image_filter, src, flt)
                with open(out_img, "rb") as f:
                    await client.send_photo(chat_id, photo=f, caption="🎨 **فیلتر با موفقیت اعمال شد.**", reply_markup=menus.get_image_tools_keyboard(chat_id, get_user_lang))
                await status_msg.delete()
                if os.path.exists(out_img): os.remove(out_img)
            except Exception as e:
                await status_msg.edit_text(f"❌ خطا در فیلتر: {e}")
            return

        if data == "itool_watermark":
            USER_STATES[chat_id] = "ITOOL_WATERMARK"
            await safe_edit_or_send(
                client, chat_id, msg,
                "🏷 **درج واترمارک و متن روی عکس**\n\nمتن مورد نظر برای درج روی تصویر را ارسال کنید (مثلاً آیدی کانال یا اسم شما):\n`@tahasbot`",
                InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_image_tools")]])
            )
            return

    # --- Google Images Callbacks ---
    if data.startswith("gi_pg_"):
        pg_str = data.replace("gi_pg_", "")
        pg = int(pg_str) if pg_str.isdigit() else 0
        await render_google_images_page(client, chat_id, page=pg, msg=msg)
        return

    if data.startswith("gi_dl_"):
        idx_str = data.replace("gi_dl_", "")
        idx = int(idx_str) if idx_str.isdigit() else 0
        images = USER_DATA.get(chat_id, {}).get("gi_results", [])
        if 0 <= idx < len(images):
            img_url = images[idx]
            status_msg = await client.send_message(chat_id, "⏳ در حال دانلود تصویر از گوگل...")
            try:
                out_p = os.path.join(MEDIA_DIR, f"gi_{int(time.time())}_{idx}.jpg")
                await asyncio.to_thread(soc.download_url_to_file, img_url, out_p)
                with open(out_p, "rb") as pf:
                    await client.send_photo(chat_id, photo=pf, caption="🖼 دانلود شده از Google Images توسط @tahasbot")
                await status_msg.delete()
                if os.path.exists(out_p): os.remove(out_p)
            except Exception as e:
                await status_msg.edit_text(f"❌ خطا در دانلود تصویر: {e}")
        return

    if data == "gi_noop":
        return


# ----------------- MESSAGE DISPATCHER (TEXT & MEDIA) -----------------
@bot.on_message()
async def on_message(client: Client, message: Message):
    if not message or not message.chat:
        return
    chat_id = message.chat.id
    text = (message.text or "").strip()
    username = (message.author.username if message.author else "") or ""

    # 0. Ban & Maintenance Checks
    if is_user_banned(chat_id):
        await client.send_message(chat_id, "⛔️ حساب کاربری شما توسط مدیریت مسدود شده است.")
        return

    if is_maintenance_mode() and not is_admin(chat_id, username):
        await client.send_message(chat_id, "🛠 **ربات در حال به‌روزرسانی و ارتقای سرور است.**\nلطفاً دقایقی دیگر مراجعه فرمایید.")
        return

    # 1. Check if user sent an Audio / Voice file
    if getattr(message, "audio", None) or getattr(message, "voice", None):
        status_msg = await client.send_message(chat_id, "⏳ در حال دریافت فایل صوتی...")
        try:
            file_obj = getattr(message, "audio", None) or getattr(message, "voice", None)
            file_id = getattr(file_obj, "id", None) or getattr(file_obj, "file_id", None)
            if file_id:
                raw_bytes = await client.download(file_id)
                saved_path = os.path.join(MUSIC_DIR, f"audio_{chat_id}_{int(time.time())}.mp3")
                with open(saved_path, "wb") as f:
                    f.write(raw_bytes)
                USER_DATA.setdefault(chat_id, {})["audio_path"] = saved_path

                # If user was in AWAIT_AUDD_UPLOAD, recognize song!
                if USER_STATES.get(chat_id) == "AWAIT_AUDD_UPLOAD":
                    USER_STATES.pop(chat_id, None)
                    await status_msg.edit_text("🎙 در حال شناسایی موزیک با Shazam...")
                    clip_p = os.path.join(WORK_DIR, f"clip_{chat_id}_{int(time.time())}.mp3")
                    await asyncio.to_thread(audd.extract_audio_clip_sync, saved_path, clip_p, 15)
                    res = await asyncio.to_thread(audd.audd_recognize_sync, clip_p)
                    if os.path.exists(clip_p):
                        try: os.remove(clip_p)
                        except OSError: pass

                    if not res:
                        await status_msg.edit_text("❌ متأسفانه آهنگی مطابق با این فایل شناسایی نشد.", reply_markup=InlineKeyboard([[InlineKeyboardButton("🔙 منوی اصلی", "menu_main")]]))
                        return

                    title = res["title"]
                    artist = res["artist"]
                    caption = (
                        "🎵 **موزیک با موفقیت شناسایی شد!**\n\n"
                        f"🎧 عنوان: **{title}**\n"
                        f"👤 خواننده: **{artist}**\n"
                        f"💿 آلبوم: {res.get('album', '')}\n\n"
                        "می‌توانید نسخه ۳۲۰ این آهنگ را مستقیماً دریافت کنید:"
                    )
                    kb_dl = InlineKeyboard([
                        [InlineKeyboardButton("📥 دانلود نسخه باکیفیت ۳۲۰", f"audd_dl_{title} - {artist}")],
                        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]
                    ])
                    if res.get("cover"):
                        try:
                            r = requests.get(res["cover"], timeout=5)
                            if r.status_code == 200:
                                cov_p = os.path.join(MEDIA_DIR, f"cov_{chat_id}.jpg")
                                with open(cov_p, "wb") as cf: cf.write(r.content)
                                with open(cov_p, "rb") as cf:
                                    await client.send_photo(chat_id, photo=cf, caption=caption, reply_markup=kb_dl)
                                await status_msg.delete()
                                if os.path.exists(cov_p): os.remove(cov_p)
                                return
                        except Exception: pass
                    await status_msg.edit_text(caption, reply_markup=kb_dl)
                    return

                kb = menus.get_music_tools_keyboard(chat_id, get_user_lang)
                await status_msg.edit_text("🎵 **فایل صوتی دریافت شد! ابزار مورد نظر را انتخاب کنید:**", reply_markup=kb)
                return
        except Exception as e:
            traceback.print_exc()
            await status_msg.edit_text(f"❌ خطا در دریافت فایل صوتی: {e}")
            return

    # 2. Check if user sent a Video or Video Document
    if getattr(message, "video", None) or (getattr(message, "document", None) and (getattr(message.document, "mime_type", "") or "").startswith("video/")):
        status_msg = await client.send_message(chat_id, "⏳ در حال دریافت ویدیو...")
        try:
            v_obj = getattr(message, "video", None) or getattr(message, "document", None)
            file_id = getattr(v_obj, "id", None) or getattr(v_obj, "file_id", None)
            if file_id:
                raw_bytes = await client.download(file_id)
                saved_path = os.path.join(VIDEO_EDIT_DIR, f"vid_{chat_id}_{int(time.time())}.mp4")
                with open(saved_path, "wb") as f:
                    f.write(raw_bytes)
                USER_DATA.setdefault(chat_id, {})["vedit_path"] = saved_path

                # If user was in AWAIT_AUDD_UPLOAD, recognize song inside video!
                if USER_STATES.get(chat_id) == "AWAIT_AUDD_UPLOAD":
                    USER_STATES.pop(chat_id, None)
                    await status_msg.edit_text("🎙 در حال استخراج و شناسایی موزیک داخل ویدیو با Shazam...")
                    clip_p = os.path.join(WORK_DIR, f"clip_{chat_id}_{int(time.time())}.mp3")
                    await asyncio.to_thread(audd.extract_audio_clip_sync, saved_path, clip_p, 15)
                    res = await asyncio.to_thread(audd.audd_recognize_sync, clip_p)
                    if os.path.exists(clip_p):
                        try: os.remove(clip_p)
                        except OSError: pass

                    if not res:
                        await status_msg.edit_text("❌ متأسفانه آهنگی داخل این ویدیو شناسایی نشد.", reply_markup=InlineKeyboard([[InlineKeyboardButton("🔙 منوی اصلی", "menu_main")]]))
                        return

                    title = res["title"]
                    artist = res["artist"]
                    caption = (
                        "🎵 **موزیک داخل ویدیو شناسایی شد!**\n\n"
                        f"🎧 عنوان: **{title}**\n"
                        f"👤 خواننده: **{artist}**\n"
                        f"💿 آلبوم: {res.get('album', '')}\n\n"
                        "می‌توانید نسخه ۳۲۰ این آهنگ را دریافت کنید:"
                    )
                    kb_dl = InlineKeyboard([
                        [InlineKeyboardButton("📥 دانلود نسخه باکیفیت ۳۲۰", f"audd_dl_{title} - {artist}")],
                        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]
                    ])
                    await status_msg.edit_text(caption, reply_markup=kb_dl)
                    return

                kb = vtools.video_editor_kb()
                await status_msg.edit_text("🎬 **ویدیو دریافت شد! ابزار ویرایش مورد نظر را انتخاب کنید:**", reply_markup=kb)
                return
        except Exception as e:
            traceback.print_exc()
            await status_msg.edit_text(f"❌ خطا در دریافت ویدیو: {e}")
            return

    # 2. Check if user sent a Photo
    if getattr(message, "photo", None):
        status_msg = await client.send_message(chat_id, "⏳ در حال دریافت تصویر...")
        try:
            photo = message.photo
            file_id = getattr(photo, "id", None) or getattr(photo, "file_id", None)
            if file_id:
                raw_bytes = await client.download(file_id)
                saved_path = os.path.join(MEDIA_DIR, f"img_{chat_id}_{int(time.time())}.jpg")
                with open(saved_path, "wb") as f:
                    f.write(raw_bytes)
                USER_DATA.setdefault(chat_id, {})["image_path"] = saved_path
                kb = menus.get_image_tools_keyboard(chat_id, get_user_lang)
                await status_msg.edit_text("🎨 **تصویر دریافت شد! ابزار مورد نظر را انتخاب کنید:**", reply_markup=kb)
                return
        except Exception as e:
            traceback.print_exc()
            await status_msg.edit_text(f"❌ خطا در دریافت تصویر: {e}")
            return

    if not text:
        return

    print(f"[BALE INCOMING] chat_id={chat_id} text={text[:60]}", flush=True)

    # Route slash commands directly
    if text.startswith("/"):
        cmd = text.split()[0].lower()
        if cmd.startswith("/start"):
            return await cmd_start(client, message)
        elif cmd.startswith("/help"):
            return await cmd_help(client, message)
        elif cmd.startswith("/tool"):
            return await cmd_tool(client, message)
        elif cmd.startswith("/profile"):
            return await render_user_profile(client, chat_id, user_obj=message.author)
        elif cmd.startswith("/admin") or cmd.startswith("/panel"):
            return await cmd_admin(client, message)
        return

    state = USER_STATES.get(chat_id)

    # Admin Text States
    if state == "AWAIT_ADMIN_SEARCH" and is_admin(chat_id, username):
        USER_STATES.pop(chat_id, None)
        query_val = text.strip().lstrip("@")
        doc = None
        if query_val.isdigit():
            doc = db.users.find_one({"chat_id": int(query_val)}) if db is not None else None
        if not doc and db is not None:
            doc = db.users.find_one({"username": {"$regex": f"^{re.escape(query_val)}$", "$options": "i"}})
        if doc:
            await render_admin_user_detail(client, chat_id, doc["chat_id"])
        else:
            await client.send_message(
                chat_id,
                f"❌ کاربر «{text}» در سامانه یافت نشد.",
                reply_markup=InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به پنل", "adm_home")]])
            )
        return

    if state == "AWAIT_BROADCAST" and is_admin(chat_id, username):
        USER_STATES.pop(chat_id, None)
        status_msg = await client.send_message(chat_id, "⏳ در حال ارسال پیام همگانی به تمام کاربران...")
        success = 0
        failed = 0
        if db is not None:
            for u in db.users.find({}, {"chat_id": 1}):
                uid = u.get("chat_id")
                if not uid or uid == chat_id:
                    continue
                try:
                    await client.send_message(uid, f"📢 **پیام مدیریت:**\n\n{text}")
                    success += 1
                    await asyncio.sleep(0.05)
                except Exception:
                    failed += 1
        await status_msg.edit_text(
            f"📢 **ارسال همگانی پایان یافت:**\n\n"
            f"✅ ارسال موفق: **{success}** کاربر\n"
            f"❌ ناموفق: **{failed}**",
            reply_markup=InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به پنل مدیریت", "adm_home")]])
        )
        return

    if state == "AWAIT_ADMIN_SLICE_VAL" and is_admin(chat_id, username):
        USER_STATES.pop(chat_id, None)
        if text.isdigit() and int(text) >= 5:
            new_val = int(text)
            set_video_slice_limit_mb(new_val)
            await client.send_message(
                chat_id,
                f"✅ سقف تکه‌کردن ویدیوها با موفقیت روی **{new_val} مگابایت** تنظیم شد.",
                reply_markup=InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به پنل مدیریت", "adm_home")]])
            )
        else:
            await client.send_message(
                chat_id,
                "❌ لطفاً یک عدد معتبر بزرگتر از 5 (مثلاً 25 یا 35) وارد کنید.",
                reply_markup=InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت", "adm_slice")]])
            )
        return

    if state and state.startswith("AWAIT_ADMIN_PM_") and is_admin(chat_id, username):
        target_id = int(state.replace("AWAIT_ADMIN_PM_", ""))
        USER_STATES.pop(chat_id, None)
        try:
            await client.send_message(target_id, f"✉️ **پیام مستقیم از طرف مدیریت:**\n\n{text}")
            await client.send_message(
                chat_id,
                f"✅ پیام شما برای کاربر `{target_id}` ارسال شد.",
                reply_markup=InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت", f"adm_u_{target_id}")]])
            )
        except Exception as e:
            await client.send_message(
                chat_id,
                f"❌ خطا در ارسال پیام: {e}",
                reply_markup=InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت", f"adm_u_{target_id}")]])
            )
        return

    # User Text States
    if state == "AWAIT_IG_TIKTOK_LINK":
        USER_STATES.pop(chat_id, None)
        if "instagram.com" in text:
            await handle_social_media_download(client, message, text, platform="Instagram")
            return
        elif "tiktok.com" in text:
            await handle_social_media_download(client, message, text, platform="TikTok")
            return
        elif text.startswith("http"):
            await handle_social_media_download(client, message, text, platform="Media")
            return
        else:
            await client.send_message(
                chat_id,
                "❌ لینک ارسالی معتبر نیست. لطفاً یک لینک معتبر از اینستاگرام یا تیک‌تاک ارسال کنید.",
                reply_markup=InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]])
            )
            return

    if state == "AWAIT_YT_CHANNEL_SEARCH":
        USER_STATES.pop(chat_id, None)
        await handle_youtube_channel_search(client, message, text)
        return

    if state in ["AWAIT_YT_QUERY", "AWAIT_YT_KEYWORD_SEARCH", "AWAIT_YT_SEARCH"]:
        USER_STATES.pop(chat_id, None)
        if "youtube.com" in text or "youtu.be" in text:
            await render_yt_video_card(client, chat_id, text)
            return
        await handle_youtube_keyword_search(client, message, text)
        return

    # Spotify States
    if state == "AWAIT_SP_TRACK_QUERY" or (state == "AWAIT_SP_LINK_OR_QUERY" and not text.startswith("http")):
        USER_STATES.pop(chat_id, None)
        status = await client.send_message(chat_id, f"🔍 در حال جستجوی «{text}» در اسپاتیفای...")
        tracks = await asyncio.to_thread(rega_music.search_tracks, text, 25)
        USER_DATA.setdefault(chat_id, {})["sp_track_results"] = tracks
        USER_DATA.setdefault(chat_id, {})["sp_track_query"] = text
        await render_sp_tracks_page(client, chat_id, page=0, msg=status)
        return

    if state == "AWAIT_SP_ARTIST_QUERY":
        USER_STATES.pop(chat_id, None)
        status = await client.send_message(chat_id, f"👤 در حال جستجوی خواننده «{text}» در اسپاتیفای...")
        artists = await asyncio.to_thread(rega_music.search_artists, text, 20)
        USER_DATA.setdefault(chat_id, {})["sp_artist_results"] = artists
        USER_DATA.setdefault(chat_id, {})["sp_artist_query"] = text
        await render_sp_artists_page(client, chat_id, page=0, msg=status)
        return

    if state == "AWAIT_SP_ALBUM_QUERY":
        USER_STATES.pop(chat_id, None)
        status = await client.send_message(chat_id, f"💿 در حال جستجوی آلبوم «{text}» در اسپاتیفای...")
        albums = await asyncio.to_thread(rega_music.search_albums, text, 20)
        USER_DATA.setdefault(chat_id, {})["sp_album_results"] = albums
        USER_DATA.setdefault(chat_id, {})["sp_album_query"] = text
        await render_sp_albums_page(client, chat_id, page=0, msg=status)
        return

    if state == "AWAIT_SP_PLAYLIST_QUERY":
        USER_STATES.pop(chat_id, None)
        if is_playlist_locked("spotify"):
            await client.send_message(chat_id, "⚠️ دانلود و جستجوی پلی‌لیست‌های اسپاتیفای توسط مدیریت قفل شده است.")
            return
        status = await client.send_message(chat_id, f"📑 در حال جستجوی پلی‌لیست «{text}» در اسپاتیفای...")
        pls = await asyncio.to_thread(rega_music.search_playlists, text, 20)
        USER_DATA.setdefault(chat_id, {})["sp_playlist_results"] = pls
        USER_DATA.setdefault(chat_id, {})["sp_playlist_query"] = text
        await render_sp_playlists_page(client, chat_id, page=0, msg=status)
        return

    # SoundCloud States
    if state == "AWAIT_SC_TRACK_QUERY" or (state == "AWAIT_SC_LINK_OR_QUERY" and not text.startswith("http")):
        USER_STATES.pop(chat_id, None)
        status = await client.send_message(chat_id, f"🔍 در حال جستجوی «{text}» در ساندکلاد...")
        tracks = await asyncio.to_thread(rega_music.search_soundcloud_tracks, text, 20)
        USER_DATA.setdefault(chat_id, {})["sc_track_results"] = tracks
        USER_DATA.setdefault(chat_id, {})["sc_track_query"] = text
        await render_sc_tracks_page(client, chat_id, page=0, msg=status)
        return

    if state == "AWAIT_SC_ARTIST_QUERY":
        USER_STATES.pop(chat_id, None)
        status = await client.send_message(chat_id, f"👤 در حال جستجوی هنرمند «{text}» در ساندکلاد...")
        artists = await asyncio.to_thread(rega_music.search_soundcloud_artists, text, 15)
        USER_DATA.setdefault(chat_id, {})["sc_artist_results"] = artists
        USER_DATA.setdefault(chat_id, {})["sc_artist_query"] = text
        await render_sc_artists_page(client, chat_id, page=0, msg=status)
        return

    if state == "AWAIT_SC_ALBUM_QUERY":
        USER_STATES.pop(chat_id, None)
        status = await client.send_message(chat_id, f"💿 در حال جستجوی آلبوم / ست «{text}» در ساندکلاد...")
        albums = await asyncio.to_thread(rega_music.search_soundcloud_albums, text, 15)
        USER_DATA.setdefault(chat_id, {})["sc_album_results"] = albums
        USER_DATA.setdefault(chat_id, {})["sc_album_query"] = text
        await render_sc_albums_page(client, chat_id, page=0, msg=status)
        return

    if state == "AWAIT_SC_PLAYLIST_QUERY":
        USER_STATES.pop(chat_id, None)
        if is_playlist_locked("soundcloud"):
            await client.send_message(chat_id, "⚠️ دانلود و جستجوی پلی‌لیست‌های ساندکلاد توسط مدیریت قفل شده است.")
            return
        status = await client.send_message(chat_id, f"📑 در حال جستجوی پلی‌لیست «{text}» در ساندکلاد...")
        albums = await asyncio.to_thread(rega_music.search_soundcloud_playlists, text, 15)
        USER_DATA.setdefault(chat_id, {})["sc_album_results"] = albums
        USER_DATA.setdefault(chat_id, {})["sc_album_query"] = text
        await render_sc_albums_page(client, chat_id, page=0, msg=status)
        return

    # YouTube Music States
    if state == "AWAIT_YTM_TRACK_QUERY" or (state == "AWAIT_YTM_LINK_OR_QUERY" and not text.startswith("http")):
        USER_STATES.pop(chat_id, None)
        status = await client.send_message(chat_id, f"🔍 در حال جستجوی «{text}» در یوتیوب موزیک...")
        tracks = await asyncio.to_thread(rega_music.search_ytm_tracks, text, 20)
        USER_DATA.setdefault(chat_id, {})["ytm_track_results"] = tracks
        USER_DATA.setdefault(chat_id, {})["ytm_track_query"] = text
        await render_ytm_tracks_page(client, chat_id, page=0, msg=status)
        return

    if state == "AWAIT_YTM_ARTIST_QUERY":
        USER_STATES.pop(chat_id, None)
        status = await client.send_message(chat_id, f"👤 در حال جستجوی خواننده «{text}» در یوتیوب موزیک...")
        artists = await asyncio.to_thread(rega_music.search_ytm_artists, text, 15)
        USER_DATA.setdefault(chat_id, {})["ytm_artist_results"] = artists
        USER_DATA.setdefault(chat_id, {})["ytm_artist_query"] = text
        await render_ytm_artists_page(client, chat_id, page=0, msg=status)
        return

    if state == "AWAIT_YTM_ALBUM_QUERY":
        USER_STATES.pop(chat_id, None)
        status = await client.send_message(chat_id, f"💿 در حال جستجوی آلبوم «{text}» در یوتیوب موزیک...")
        albums = await asyncio.to_thread(rega_music.search_ytm_albums, text, 15)
        USER_DATA.setdefault(chat_id, {})["ytm_album_results"] = albums
        USER_DATA.setdefault(chat_id, {})["ytm_album_query"] = text
        await render_ytm_albums_page(client, chat_id, page=0, msg=status)
        return

    if state == "AWAIT_YTM_PLAYLIST_QUERY":
        USER_STATES.pop(chat_id, None)
        if is_playlist_locked("ytmusic"):
            await client.send_message(chat_id, "⚠️ دانلود و جستجوی پلی‌لیست‌های یوتیوب موزیک توسط مدیریت قفل شده است.")
            return
        status = await client.send_message(chat_id, f"📑 در حال جستجوی پلی‌لیست «{text}» در یوتیوب موزیک...")
        albums = await asyncio.to_thread(rega_music.search_ytm_playlists, text, 15)
        USER_DATA.setdefault(chat_id, {})["ytm_album_results"] = albums
        USER_DATA.setdefault(chat_id, {})["ytm_album_query"] = text
        await render_ytm_albums_page(client, chat_id, page=0, msg=status)
        return

    # Video Editor States
    if state == "AWAIT_VEDIT_TRIM":
        USER_STATES.pop(chat_id, None)
        src = USER_DATA.get(chat_id, {}).get("vedit_path")
        if not src or not os.path.exists(src):
            await client.send_message(chat_id, "❌ ویدیویی برای برش یافت نشد. لطفاً ابتدا ویدیو ارسال کنید.")
            return
        raw_parts = [p.strip() for p in text.split("-") if p.strip()]
        if len(raw_parts) == 2:
            s_val, e_val = raw_parts[0], raw_parts[1]
        else:
            s_val, e_val = "00:00:00", text.strip()
        status_msg = await client.send_message(chat_id, f"✂️ در حال برش ویدیو از {s_val} تا {e_val}...")
        try:
            res = await vtools.execute_video_action(src, "trim", {"trim_start": s_val, "trim_end": e_val})
            out_p = res["output_path"]
            USER_DATA.setdefault(chat_id, {})["vedit_path"] = out_p
            with open(out_p, "rb") as vf:
                await client.send_video(chat_id, video=vf, caption="✂️ **برش ویدیو انجام شد.**", reply_markup=vtools.video_editor_kb())
            await status_msg.delete()
        except Exception as e:
            traceback.print_exc()
            await status_msg.edit_text(f"❌ خطا در برش ویدیو: {e}")
        return

    if state == "AWAIT_VEDIT_WM":
        USER_STATES.pop(chat_id, None)
        src = USER_DATA.get(chat_id, {}).get("vedit_path")
        if not src or not os.path.exists(src):
            await client.send_message(chat_id, "❌ ویدیویی برای افزودن واترمارک یافت نشد. لطفاً ابتدا ویدیو ارسال کنید.")
            return
        status_msg = await client.send_message(chat_id, f"🏷 در حال درج واترمارک «{text}» روی ویدیو...")
        try:
            res = await vtools.execute_video_action(src, "watermark", {"watermark_text": text})
            out_p = res["output_path"]
            USER_DATA.setdefault(chat_id, {})["vedit_path"] = out_p
            with open(out_p, "rb") as vf:
                await client.send_video(chat_id, video=vf, caption=f"🏷 **واترمارک «{text}» درج شد.**", reply_markup=vtools.video_editor_kb())
            await status_msg.delete()
        except Exception as e:
            traceback.print_exc()
            await status_msg.edit_text(f"❌ خطا در درج واترمارک: {e}")
        return

    # Music Tools States
    if state == "AWAIT_MTOOL_TRIM":
        USER_STATES.pop(chat_id, None)
        src = USER_DATA.get(chat_id, {}).get("audio_path")
        if not src or not os.path.exists(src):
            await client.send_message(chat_id, "⚠️ فایلی برای برش یافت نشد. لطفاً ابتدا یک فایل صوتی ارسال کنید.")
            return
        raw_parts = [p.strip() for p in text.split("-") if p.strip()]
        s_val = raw_parts[0] if len(raw_parts) >= 1 else "0"
        e_val = raw_parts[1] if len(raw_parts) >= 2 else "30"
        status_msg = await client.send_message(chat_id, f"✂️ در حال برش موزیک از {s_val} تا {e_val}...")
        try:
            out_file = await asyncio.to_thread(mtools.trim_audio_file, src, s_val, e_val)
            probe = await asyncio.to_thread(mtools.probe_audio_info, out_file)
            with open(out_file, "rb") as af:
                await client.send_audio(
                    chat_id,
                    audio=af,
                    title=f"{probe.get('title','Ringtone')} (Trimmed)",
                    duration=int(probe.get("duration", 0)),
                    caption=f"✂️ **برش موزیک ({s_val} تا {e_val})**\n⚡️ پردازش شده توسط @tahasbot",
                    reply_markup=menus.get_music_tools_keyboard(chat_id, get_user_lang)
                )
            await status_msg.delete()
            if os.path.exists(out_file): os.remove(out_file)
        except Exception as e:
            await status_msg.edit_text(f"❌ خطا در برش صدا: {e}")
        return

    if state == "AWAIT_MTOOL_TAGS":
        USER_STATES.pop(chat_id, None)
        src = USER_DATA.get(chat_id, {}).get("audio_path")
        if not src or not os.path.exists(src):
            await client.send_message(chat_id, "⚠️ فایلی برای ویرایش تگ یافت نشد. لطفاً ابتدا یک فایل صوتی ارسال کنید.")
            return
        parts = [p.strip() for p in re.split(r"[-–—|]+", text) if p.strip()]
        title_new = parts[0] if parts else text
        artist_new = parts[1] if len(parts) > 1 else ""
        status_msg = await client.send_message(chat_id, "🏷 در حال به‌روزرسانی مشخصات فایل صوتی...")
        try:
            await asyncio.to_thread(mtools.update_audio_metadata, src, title=title_new, artist=artist_new)
            probe = await asyncio.to_thread(mtools.probe_audio_info, src)
            with open(src, "rb") as af:
                await client.send_audio(
                    chat_id,
                    audio=af,
                    title=title_new,
                    performer=artist_new,
                    caption=f"🏷 **مشخصات آهنگ تغییر یافت:**\n🎵 {title_new}\n👤 {artist_new}",
                    reply_markup=menus.get_music_tools_keyboard(chat_id, get_user_lang)
                )
            await status_msg.delete()
        except Exception as e:
            await status_msg.edit_text(f"❌ خطا در ویرایش تگ: {e}")
        return

    # Image Tools States
    if state == "ITOOL_WATERMARK":
        USER_STATES.pop(chat_id, None)
        src = USER_DATA.get(chat_id, {}).get("image_path")
        if not src or not os.path.exists(src):
            await client.send_message(chat_id, "⚠️ تصویری برای درج واترمارک یافت نشد. لطفاً ابتدا عکس ارسال کنید.")
            return
        status_msg = await client.send_message(chat_id, f"🏷 در حال درج واترمارک «{text}»...")
        try:
            out_img = await asyncio.to_thread(itools.add_text_watermark, src, text)
            with open(out_img, "rb") as f:
                await client.send_photo(
                    chat_id, photo=f,
                    caption=f"🏷 **واترمارک درج شد:** `{text}`",
                    reply_markup=menus.get_image_tools_keyboard(chat_id, get_user_lang)
                )
            await status_msg.delete()
            if os.path.exists(out_img): os.remove(out_img)
        except Exception as e:
            await status_msg.edit_text(f"❌ خطا در درج واترمارک: {e}")
        return

    # User Playlists System
    if state == "AWAIT_PLAYLIST_NAME":
        USER_STATES.pop(chat_id, None)
        pl_name = text.strip()[:40]
        if not pl_name:
            await client.send_message(chat_id, "❌ نام پلی‌لیست نمی‌تواند خالی باشد.")
            return
        pl_id = f"pl_{chat_id}_{int(time.time())}"
        pl_doc = {
            "playlist_id": pl_id,
            "chat_id": chat_id,
            "name": pl_name,
            "tracks": [],
            "created_at": int(time.time())
        }
        if db is not None:
            try:
                db.user_playlists.insert_one(pl_doc)
            except Exception as e:
                print(f"Error saving playlist: {e}", flush=True)
        await client.send_message(
            chat_id,
            f"✅ پلی‌لیست «**{pl_name}**» با موفقیت ساخته شد!",
            reply_markup=InlineKeyboard([
                [InlineKeyboardButton("🎵 مشاهده پلی‌لیست‌های من", "menu_playlists")],
                [InlineKeyboardButton("🔙 منوی اصلی", "menu_main")]
            ])
        )
        return

    # Support & Contact Admin
    if state == "AWAIT_SUPPORT_MSG":
        USER_STATES.pop(chat_id, None)
        t_id = f"TICK_{int(time.time())}_{chat_id % 10000}"
        ticket_doc = {
            "ticket_id": t_id,
            "chat_id": chat_id,
            "username": username,
            "text": text,
            "created_at": int(time.time()),
            "status": "open"
        }
        if db is not None:
            try:
                db.support_tickets.insert_one(ticket_doc)
            except Exception as e:
                print(f"Error saving support ticket: {e}", flush=True)

        try:
            owner_kb = InlineKeyboard([[InlineKeyboardButton("✍️ پاسخ به پیام", f"adm_rep_{t_id}")]])
            await client.send_message(
                OWNER_ID,
                f"📩 **تیکت پشتیبانی جدید:**\n\n"
                f"👤 از طرف: `{chat_id}` (@{username or 'بدون نام کاربری'})\n"
                f"🆔 شناسه: `{t_id}`\n\n"
                f"💬 متن پیام:\n{text}",
                reply_markup=owner_kb
            )
        except Exception as e:
            print(f"Error notifying owner of support ticket: {e}", flush=True)

        await client.send_message(
            chat_id,
            "✅ **پیام شما با موفقیت برای مدیریت ارسال شد.**\nپاسخ به زودی از طریق همین ربات برای شما ارسال خواهد شد.",
            reply_markup=InlineKeyboard([[InlineKeyboardButton("🔙 منوی اصلی", "menu_main")]])
        )
        return

    if state and state.startswith("AWAIT_ADMIN_REPLY_") and is_admin(chat_id, username):
        t_id = state.replace("AWAIT_ADMIN_REPLY_", "")
        USER_STATES.pop(chat_id, None)
        ticket = db.support_tickets.find_one({"ticket_id": t_id}) if db is not None else None
        target_uid = ticket.get("chat_id") if ticket else None
        if target_uid:
            try:
                await client.send_message(
                    target_uid,
                    f"📬 **پاسخ پشتیبانی به پیام شما:**\n\n{text}"
                )
                if db is not None:
                    db.support_tickets.update_one({"ticket_id": t_id}, {"$set": {"status": "answered", "reply": text, "answered_at": int(time.time())}})
                await client.send_message(
                    chat_id,
                    f"✅ پاسخ شما برای کاربر `{target_uid}` ارسال شد.",
                    reply_markup=InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به پیام‌ها", "adm_sup_0")]])
                )
            except Exception as e:
                await client.send_message(chat_id, f"❌ خطا در ارسال پیام به کاربر: {e}")
        else:
            await client.send_message(chat_id, "❌ کاربر مرتبط با این تیکت پیدا نشد.")
        return

    # Image & Social Search States
    if state == "AWAIT_RD_SEARCH":
        USER_STATES.pop(chat_id, None)
        status = await client.send_message(chat_id, f"🤖 در حال جستجو در ردیت برای «{text}»...")
        USER_DATA.setdefault(chat_id, {})["rd_query"] = text
        USER_DATA.setdefault(chat_id, {})["rd_posts"] = []
        await render_reddit_page(client, chat_id, page=0, msg=status)
        return

    if state == "AWAIT_PT_SEARCH":
        USER_STATES.pop(chat_id, None)
        status = await client.send_message(chat_id, f"📌 در حال جستجوی پین‌ها برای «{text}» در پینترست...")
        USER_DATA.setdefault(chat_id, {})["pt_query"] = text
        USER_DATA.setdefault(chat_id, {})["pt_pins"] = []
        await render_pinterest_page(client, chat_id, page=0, msg=status)
        return

    if state == "AWAIT_PX_SEARCH":
        USER_STATES.pop(chat_id, None)
        status = await client.send_message(chat_id, f"🔍 در حال جستجوی تصاویر باکیفیت برای «{text}» در پکسلز...")
        USER_DATA.setdefault(chat_id, {})["px_query"] = text
        await render_pexels_page(client, chat_id, page=1, msg=status)
        return

    if state == "AWAIT_GI_SEARCH":
        USER_STATES.pop(chat_id, None)
        status = await client.send_message(chat_id, f"🔍 در حال جستجوی تصاویر برای «{text}» در گوگل...")
        USER_DATA.setdefault(chat_id, {})["gi_query"] = text
        USER_DATA.setdefault(chat_id, {})["gi_results"] = []
        await render_google_images_page(client, chat_id, page=0, msg=status)
        return

    if state == "AWAIT_AUDD_UPLOAD":
        USER_STATES.pop(chat_id, None)
        await handle_music_search(client, message, text)
        return

    # AI Chat Assistant
    if state == "AI_CHAT":
        if text in ["/exit", "خروج", "انصراف", "منوی اصلی", "/start", "/help"]:
            USER_STATES.pop(chat_id, None)
            name = (message.author.first_name if message.author else "") or "کاربر"
            w_text = menus.get_menu_text("main_menu_welcome", lang=lang, name=name)
            kb = menus.get_main_keyboard(chat_id, get_user_lang, is_admin)
            await client.send_message(chat_id, "✅ با موفقیت از گفتگوی هوش مصنوعی خارج شدید.\n\n" + w_text, reply_markup=kb)
            return

        history = USER_DATA.setdefault(chat_id, {}).setdefault("ai_history", [])
        history.append({"role": "user", "content": text})
        status_msg = await client.send_message(chat_id, "🧠 در حال تفکر...")

        try:
            res, provider = await asyncio.to_thread(ai_chat.ask_ai, AI_SYSTEM_PROMPT, history, [])
            ans = res.get("content") or "پاسخی دریافت نشد."
            history.append({"role": "assistant", "content": ans})
            if len(history) > 20:
                del history[:-20]

            kb = InlineKeyboard([[InlineKeyboardButton("🔙 خروج از هوش مصنوعی", "ai_exit")]])
            await status_msg.edit_text(ans, reply_markup=kb)
            log_activity(chat_id, "ai_chat", text[:30])
        except Exception as e:
            traceback.print_exc()
            await status_msg.edit_text(
                "❌ دستیار هوش مصنوعی موقتاً در دسترس نیست. لطفاً کمی بعد دوباره پیام دهید.",
                reply_markup=InlineKeyboard([[InlineKeyboardButton("🔙 خروج", "ai_exit")]])
            )
        return

    if state == "AWAIT_AI_QUERY":
        USER_STATES.pop(chat_id, None)
        status_msg = await client.send_message(chat_id, "🤖 در حال پردازش توسط هوش مصنوعی...")
        reply_txt = (
            f"🧠 **پاسخ دستیار هوش مصنوعی TahasBot:**\n\n"
            f"درخواست شما برای «**{text}**» دریافت شد.\n\n"
            f"ربات طه به صورت هوشمند برای دانلود و پردازش با کیفیت بالا از اینستاگرام، تیک‌تاک، یوتیوب، اسپاتیفای و ساندکلاد آماده است.\n"
            f"برای دریافت هر آهنگ یا فایل رسانه‌ای، کافیست لینک آن یا نام اثر را ارسال کنید!"
        )
        await status_msg.edit_text(reply_txt, reply_markup=InlineKeyboard([[InlineKeyboardButton("🔙 منوی اصلی", "menu_main")]]))
        log_activity(chat_id, "ai_query", text)
        return

    if state == "AWAIT_IMG_SEARCH":
        USER_STATES.pop(chat_id, None)
        await client.send_message(
            chat_id,
            f"🔍 در حال جستجوی تصاویر برای «{text}»...\nبرای دانلود دقیق‌تر می‌توانید لینک مستقیم پینترست یا اینستاگرام را بفرستید.",
            reply_markup=InlineKeyboard([[InlineKeyboardButton("🔙 منوی اصلی", "menu_main")]])
        )
        return

    # 3. Handle state waiting for search input
    if state == "AWAIT_MUSIC_SEARCH":
        USER_STATES.pop(chat_id, None)
        await handle_music_search(client, message, text)
        return

    # 4. Check for URL patterns
    # --- Twitter / X URL ---
    m_tw = soc.TWITTER_URL_RE.search(text)
    if m_tw or ("twitter.com" in text or "x.com" in text):
        user_tw = m_tw.group(1) if m_tw else "user"
        tweet_id = m_tw.group(2) if m_tw else ""
        if not tweet_id:
            m_tid = re.search(r"status(?:es)?/(\d+)", text)
            tweet_id = m_tid.group(1) if m_tid else ""
        if tweet_id:
            status_msg = await message.reply("⏳ در حال دریافت رسانه از توییتر (X)...")
            try:
                data = await asyncio.to_thread(soc.twitter_fetch_sync, user_tw, tweet_id)
                files = data.get("files", [])
                cap = (data.get("caption") or "")[:200]
                if not cap:
                    cap = "⚡️ دانلود شده از Twitter/X توسط @tahasbot"
                if not files:
                    res = await asyncio.to_thread(download_social_media_sync, text, "tw")
                    if res and res.get("file"):
                        files = [res["file"]]

                if files:
                    await status_msg.edit_text("⬆️ در حال ارسال به بله...")
                    for f in files:
                        if f.lower().endswith((".mp4", ".mov", ".mkv")):
                            with open(f, "rb") as vf:
                                await client.send_video(chat_id, video=vf, caption=cap)
                        else:
                            with open(f, "rb") as pf:
                                await client.send_photo(chat_id, photo=pf, caption=cap)
                        if os.path.exists(f):
                            try: os.remove(f)
                            except OSError: pass
                    await status_msg.delete()
                    log_activity(chat_id, "twitter_download", text)
                    return
                else:
                    await status_msg.edit_text("❌ رسانه‌ای در این توییت یافت نشد یا دسترسی به آن محدود است.")
                    return
            except Exception as e:
                traceback.print_exc()
                await status_msg.edit_text(f"❌ خطا در دانلود توییت: {e}")
                return

    # --- Reddit URL ---
    if "reddit.com" in text or "redd.it" in text:
        m_rd = re.search(r"comments/([a-zA-Z0-9]+)", text)
        post_id = m_rd.group(1) if m_rd else None
        if not post_id:
            m_short = re.search(r"redd\.it/([a-zA-Z0-9]+)", text)
            post_id = m_short.group(1) if m_short else None

        if post_id:
            status_msg = await message.reply("⏳ در حال دریافت پست از ردیت...")
            try:
                res = await asyncio.to_thread(soc.download_reddit_post_sync, post_id)
                files = res.get("files", [])
                title = res.get("title", "")
                cap = f"🤖 **{title}**\n⚡️ دانلود شده از Reddit توسط @tahasbot" if title else "⚡️ دانلود شده از Reddit توسط @tahasbot"
                if not files:
                    res2 = await asyncio.to_thread(download_social_media_sync, text, "rd")
                    if res2 and res2.get("file"):
                        files = [res2["file"]]

                if files:
                    await status_msg.edit_text("⬆️ در حال ارسال به بله...")
                    for f in files:
                        if f.lower().endswith((".mp4", ".mov", ".mkv")):
                            with open(f, "rb") as vf:
                                await client.send_video(chat_id, video=vf, caption=cap)
                        else:
                            with open(f, "rb") as pf:
                                await client.send_photo(chat_id, photo=pf, caption=cap)
                        if os.path.exists(f):
                            try: os.remove(f)
                            except OSError: pass
                    await status_msg.delete()
                    log_activity(chat_id, "reddit_download", text)
                    return
                else:
                    await status_msg.edit_text("❌ رسانه‌ای در این پست ردیت یافت نشد.")
                    return
            except Exception as e:
                traceback.print_exc()
                await status_msg.edit_text(f"❌ خطا در دانلود از ردیت: {e}")
                return

    # --- YouTube URL (Direct link triggers interactive Video Card with format & quality selection) ---
    if "youtube.com" in text or "youtu.be" in text:
        if "music.youtube.com" in text:
            if "list=" in text:
                if is_playlist_locked("ytmusic"):
                    await client.send_message(chat_id, "⚠️ دانلود پلی‌لیست‌های یوتیوب موزیک توسط مدیریت قفل شده است.")
                    return
            await render_yt_video_card(client, chat_id, text)
            return
        await render_yt_video_card(client, chat_id, text)
        return

    # --- Spotify URL ---
    if "open.spotify.com" in text:
        if "/album/" in text:
            m = re.search(r"album/([a-zA-Z0-9]+)", text)
            alb_id = m.group(1) if m else text
            await render_sp_album_detail(client, chat_id, alb_id)
            return
        elif "/playlist/" in text:
            if is_playlist_locked("spotify"):
                await client.send_message(chat_id, "⚠️ دانلود پلی‌لیست‌های اسپاتیفای توسط مدیریت قفل شده است.")
                return
            m = re.search(r"playlist/([a-zA-Z0-9]+)", text)
            pl_id = m.group(1) if m else text
            await render_sp_playlist_detail(client, chat_id, pl_id)
            return
        else:
            await handle_spotify_link(client, message, text)
            return

    # --- Deezer URL ---
    if "deezer.com" in text:
        m = re.search(r"track/(\d+)", text)
        if m:
            track_id = m.group(1)
            fake_query = CallbackQuery(id="0", message=message, data=f"dl_track_{track_id}", author=message.author)
            await on_callback(client, fake_query)
            return

    # --- Instagram URL ---
    if "instagram.com" in text:
        await handle_social_media_download(client, message, text, platform="Instagram")
        return

    # --- TikTok URL ---
    if "tiktok.com" in text:
        await handle_social_media_download(client, message, text, platform="TikTok")
        return

    # --- SoundCloud URL ---
    if "soundcloud.com" in text:
        if "/sets/" in text or "/playlists/" in text:
            if is_playlist_locked("soundcloud"):
                await client.send_message(chat_id, "⚠️ دانلود پلی‌لیست‌های ساندکلاد توسط مدیریت قفل شده است.")
                return
            await handle_social_media_download(client, message, text, platform="SoundCloud")
            return
        else:
            # Check if direct track can open quality card
            status = await client.send_message(chat_id, "⏳ در حال بررسی آهنگ از ساندکلاد...")
            t_list = await asyncio.to_thread(rega_music.search_soundcloud_tracks, text, 1)
            if t_list:
                USER_DATA.setdefault(chat_id, {})["sc_track_results"] = t_list
                await render_sc_track_card(client, chat_id, 0, msg=status)
            else:
                await handle_social_media_download(client, message, text, platform="SoundCloud")
            return

    # --- Pinterest URL ---
    if "pinterest.com" in text or "pin.it" in text:
        m_pin = re.search(r"pin/(\d+)", text)
        if m_pin:
            pin_id = m_pin.group(1)
            status_msg = await message.reply("⏳ در حال دانلود پین از پینترست...")
            try:
                res = await asyncio.to_thread(soc.pinterest_pin_download_sync, pin_id)
                if res and res.get("path"):
                    fpath = res["path"]
                    cap = f"📌 **{res.get('title') or 'Pinterest'}**\n⚡️ دانلود شده توسط @tahasbot"
                    if res["type"] == "video":
                        with open(fpath, "rb") as vf:
                            await client.send_video(chat_id, video=vf, caption=cap)
                    else:
                        with open(fpath, "rb") as pf:
                            await client.send_photo(chat_id, photo=pf, caption=cap)
                    await status_msg.delete()
                    log_activity(chat_id, "pinterest_download", text)
                    if os.path.exists(fpath):
                        try: os.remove(fpath)
                        except OSError: pass
                    return
            except Exception as e:
                print(f"Direct pin download error: {e}", flush=True)

        await handle_social_media_download(client, message, text, platform="Pinterest")
        return

    # 5. Default: Treat non-URL text as Music Track Search
    await handle_music_search(client, message, text)


# ----------------- SPECIFIC MEDIA HANDLERS -----------------
async def handle_spotify_link(client: Client, message: Message, url: str):
    chat_id = message.chat.id
    status_msg = await message.reply("⏳ در حال استخراج اطلاعات آهنگ از اسپاتیفای...")

    try:
        # Use Spotify oEmbed to fetch track name & artist with zero auth barrier
        r = requests.get("https://open.spotify.com/oembed", params={"url": url}, timeout=10)
        if r.status_code != 200:
            await status_msg.edit_text("❌ دریافت اطلاعات آهنگ از اسپاتیفای ناموفق بود.")
            return

        track_title = r.json().get("title", "")
        if not track_title:
            await status_msg.edit_text("❌ نام آهنگ یافت نشد.")
            return

        await status_msg.edit_text(f"🔍 در حال یافتن نسخه با کیفیت: **{track_title}**...")
        results = await asyncio.to_thread(rega_music.search_tracks, track_title, limit=1)

        if not results:
            await status_msg.edit_text(f"❌ نسخه صوتی برای «{track_title}» پیدا نشد.")
            return

        top_track = results[0]
        await status_msg.edit_text("📥 در حال دانلود ترک با کیفیت ۳۲۰...")
        audio_file, cover_file = await asyncio.to_thread(rega_music.download_track_sync, top_track)

        await status_msg.edit_text("⬆️ در حال ارسال به بله...")
        caption = f"🎵 **{top_track['artist']} - {top_track['title']}**\n⚡️ دانلود شده توسط @tahasbot"

        with open(audio_file, "rb") as af:
            await client.send_audio(
                chat_id,
                audio=af,
                caption=caption,
                title=f"{top_track['artist']} - {top_track['title']}",
                duration=top_track["duration"]
            )

        await status_msg.delete()
        log_activity(chat_id, "spotify_download", track_title)

        if os.path.exists(audio_file):
            os.remove(audio_file)
        if cover_file and os.path.exists(cover_file):
            os.remove(cover_file)

    except Exception as e:
        traceback.print_exc()
        await status_msg.edit_text(f"❌ خطا در پردازش اسپاتیفای:\n{e}")


async def handle_social_media_download(client: Client, message: Message, url: str, platform: str):
    chat_id = message.chat.id
    status_msg = await message.reply(f"⏳ در حال دانلود مدیا از {platform}...")

    try:
        data = await asyncio.to_thread(download_social_media_sync, url, platform.lower()[:3])
        file_path = data["file"]
        caption = f"⚡️ دانلود شده از **{platform}** توسط @tahasbot"

        await status_msg.edit_text("⬆️ در حال ارسال به بله...")

        if data["is_audio"]:
            with open(file_path, "rb") as af:
                await client.send_audio(chat_id, audio=af, caption=caption)
        elif file_path.lower().endswith((".mp4", ".mov", ".mkv")):
            with open(file_path, "rb") as vf:
                await client.send_video(chat_id, video=vf, caption=caption)
        else:
            with open(file_path, "rb") as pf:
                await client.send_photo(chat_id, photo=pf, caption=caption)

        await status_msg.delete()
        log_activity(chat_id, f"{platform.lower()}_download", url)

        # Cleanup all downloaded files
        for f in data.get("all_files", [file_path]):
            if os.path.exists(f):
                os.remove(f)

    except Exception as e:
        traceback.print_exc()
        await status_msg.edit_text(f"❌ خطا در دریافت مدیا:\n`{str(e)[:250]}`")


async def handle_music_search(client: Client, message: Message, query: str):
    chat_id = message.chat.id
    status_msg = await message.reply(f"🔍 در حال جستجوی «{query}»...")

    try:
        results = await asyncio.to_thread(rega_music.search_tracks, query, limit=5)
        if not results:
            await status_msg.edit_text("❌ هیچ آهنگی مطابق با جستجوی شما یافت نشد.")
            return

        kb = InlineKeyboard()
        for idx, t in enumerate(results, 1):
            dur = rega_music.format_duration(t["duration"])
            btn_text = f"▶️ {idx}. {t['title'][:25]} - {t['artist'][:15]} ({dur})"
            kb.add_row((btn_text, f"dl_track_{t['id']}"))

        kb.add_row(("🔙 منوی اصلی", "menu_main"))

        await status_msg.edit_text(
            f"🎵 **نتایج جستجو برای:** `{query}`\n\nبرای دانلود، روی آهنگ مورد نظر ضربه بزنید:",
            reply_markup=kb
        )
    except Exception as e:
        traceback.print_exc()
        await status_msg.edit_text(f"❌ خطا در جستجو: {e}")


async def handle_youtube_channel_search(client: Client, message: Message, query: str):
    chat_id = message.chat.id
    status_msg = await message.reply(f"⏳ در حال جستجوی کانال‌های یوتیوب برای «{query}»...")
    channels = await asyncio.to_thread(search_youtube_channels, query, 15)
    USER_DATA.setdefault(chat_id, {})["yt_channels"] = channels
    USER_DATA.setdefault(chat_id, {})["yt_ch_query"] = query
    await render_yt_channels_page(client, chat_id, page=0, msg=status_msg)


async def handle_youtube_keyword_search(client: Client, message: Message, query: str):
    chat_id = message.chat.id
    status_msg = await message.reply(f"⏳ در حال جستجوی ویدیوها برای «{query}»...")
    videos = await asyncio.to_thread(search_youtube_videos, query, 25)
    USER_DATA.setdefault(chat_id, {})["yt_kw_results"] = videos
    USER_DATA.setdefault(chat_id, {})["yt_kw_query"] = query
    USER_DATA.setdefault(chat_id, {})["yt_kw_filters"] = {"dur": "all", "sort": "rel"}
    await render_yt_keyword_page(client, chat_id, page=0, msg=status_msg)


# ----------------- WEB SERVER FOR RENDER HEALTH CHECKS -----------------
async def handle_web_root(request):
    return web.json_response({
        "status": "online",
        "bot": "TahasBot",
        "platform": "Bale Messenger",
        "timestamp": int(time.time())
    })


async def handle_web_health(request):
    return web.json_response({"status": "ok"})


async def poll_bale_updates():
    """Continuous resilient polling engine for Bale Messenger."""
    print("Starting continuous Bale polling engine...", flush=True)
    try:
        await bot.delete_webhook()
        print("Deleted any existing webhook on Bale.", flush=True)
    except Exception as e:
        print(f"delete_webhook error: {e}", flush=True)

    last_update_id = None
    session_timeout = aiohttp.ClientTimeout(total=50)

    async with aiohttp.ClientSession(timeout=session_timeout) as session:
        base_url = f"https://tapi.bale.ai/bot{BALE_BOT_TOKEN}"

        while True:
            try:
                params = {"timeout": 20}
                if last_update_id is not None:
                    params["offset"] = last_update_id + 1

                async with session.get(f"{base_url}/getUpdates", params=params) as response:
                    if response.status != 200:
                        await asyncio.sleep(2)
                        continue

                    data = await response.json()
                    if not data.get("ok"):
                        await asyncio.sleep(2)
                        continue

                    updates = data.get("result", [])
                    for item in updates:
                        up_id = item.get("update_id")
                        if up_id is not None:
                            last_update_id = up_id

                        try:
                            up_obj = Update.wrap(item)
                            effective = up_obj.get_effective_update()
                            if effective:
                                effective.bind(bot)
                                print(f"[BALE EVENT] id={up_id} type={type(effective).__name__}", flush=True)
                                await bot.dispatcher.dispatch_event(bot, effective)
                        except Exception as e:
                            print(f"[DISPATCH ERROR] {e}", flush=True)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[POLL LOOP ERROR] {e}", flush=True)
                await asyncio.sleep(1)


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_web_root)
    app.router.add_get("/health", handle_web_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Render HTTP Server listening on port {PORT}", flush=True)


# ----------------- MAIN ENTRY POINT -----------------
async def main():
    print("========================================", flush=True)
    print(" Starting TahasBot for Bale Messenger   ", flush=True)
    print("========================================", flush=True)

    # Bind Balethon dispatcher to active running loop
    loop = asyncio.get_running_loop()
    bot.dispatcher.event_loop = loop

    # 1. Start web server for Render health checks
    await start_web_server()

    # 2. Connect and initialize Balethon client
    print("Connecting to Bale API...", flush=True)
    await bot.connect()
    await bot.initialize()

    # 3. Start continuous polling engine
    await poll_bale_updates()





if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot stopped.", flush=True)
    except Exception as e:
        traceback.print_exc()
