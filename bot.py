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
from pathlib import Path
from typing import Optional, Dict, Any

import aiohttp
from aiohttp import web
import requests
import pymongo

from balethon import Client
from balethon.objects import (
    InlineKeyboard, InlineKeyboardButton,
    Message, CallbackQuery, Update
)


import modules.menus as menus
import modules.rega_music as rega_music
import modules.music_tools as mtools
import modules.image_tools as itools

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


def get_video_slice_limit_mb() -> int:
    """Returns the maximum chunk size in MB for video slicing (default 25 MB)."""
    if db is not None:
        try:
            doc = db.settings.find_one({"key": "video_slice_mb"})
            if doc and "value" in doc:
                return int(doc["value"])
        except Exception as e:
            print(f"Error fetching video_slice_mb: {e}", flush=True)
    return 25


def set_video_slice_limit_mb(mb: int):
    """Sets the maximum chunk size in MB for video slicing."""
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
    kb = menus.get_tools_picker_keyboard(chat_id, get_user_lang)
    text = (
        "🎛 **جعبه ابزار چندرسانه‌ای:**\n"
        "بخش مورد نظر خود را انتخاب کنید یا یک فایل صوتی/عکس بفرستید:"
        if lang == "fa" else
        "🎛 **Multimedia Toolbox:**\n"
        "Select a tool category or send an audio/image file:"
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
        [InlineKeyboardButton(f"✂️ تنظیم سقف تکه‌کردن ویدیو ({slice_limit} MB)", "adm_slice")],
        [InlineKeyboardButton("📢 ارسال همگانی (Broadcast)", "adm_bc")],
        [InlineKeyboardButton(maint_toggle_btn, "adm_maint")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]
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


def fetch_video_details(video_url_or_id: str) -> dict:
    if not video_url_or_id.startswith("http"):
        video_url = f"https://www.youtube.com/watch?v={video_url_or_id}"
    else:
        video_url = video_url_or_id

    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "extractor_args": {"youtube": {"player_client": ["android"]}}
    }
    if os.path.exists("cookies.txt") and os.path.getsize("cookies.txt") > 0:
        ydl_opts["cookiefile"] = "cookies.txt"

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        sizes = calculate_yt_sizes(info)
        v_id = info.get("id") or ""
        return {
            "url": f"https://www.youtube.com/watch?v={v_id}" if v_id else video_url,
            "video_id": v_id,
            "title": info.get("title") or "YouTube Video",
            "uploader": info.get("uploader") or info.get("channel") or "YouTube",
            "duration": info.get("duration") or 0,
            "duration_str": format_duration(info.get("duration") or 0),
            "views": info.get("view_count") or 0,
            "thumbnail": info.get("thumbnail") or "",
            "description": (info.get("description") or "").strip()[:500],
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
    status_msg = await safe_edit_or_send(client, chat_id, msg, "⏳ در حال دریافت مشخصات ویدیو و محاسبه حجم کیفیت‌ها...")

    try:
        info = await asyncio.to_thread(fetch_video_details, video_url_or_id)
    except Exception as e:
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

        if data == "adm_noop":
            return

    # --- Submenu Navigation ---
    if data == "menu_youtube":
        USER_STATES.pop(chat_id, None)
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
        text = "🐦 **ردیت و توییتر (X):**\n\nلینک پست مورد نظر را ارسال کنید."
        kb = InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]])
        await safe_edit_or_send(client, chat_id, msg, text, kb)
        return

    if data == "menu_vedit":
        text = (
            "🎬 **ابزارهای پیشرفته ویدیو:**\n\n"
            "برای استفاده از امکانات ویدیو:\n"
            "• استخراج صدای ویدیو به MP3\n"
            "• تبدیل ویدیو به گیف (GIF)\n"
            "• فشرده‌سازی هوشمند حجم بدون افت کیفیت\n"
            "• برش بخش دلخواه از ویدیو\n\n"
            "کافیست یک فایل ویدیویی به ربات ارسال فرمایید."
        )
        kb = InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]])
        await safe_edit_or_send(client, chat_id, msg, text, kb)
        return

    if data in ["menu_music_spotify", "menu_music_sc", "menu_music_ytm"]:
        text = (
            "🟢 **مرکز دانلود موزیک:**\n\n"
            "نام آهنگ یا خواننده را بنویسید، یا لینک آهنگ از اسپاتیفای، دیزر یا ساندکلاد را ارسال کنید."
        )
        kb = InlineKeyboard([
            [InlineKeyboardButton("🔍 جستجوی نام آهنگ", "msearch_prompt")],
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]
        ])
        await safe_edit_or_send(client, chat_id, msg, text, kb)
        return

    if data == "msearch_prompt":
        USER_STATES[chat_id] = "AWAIT_MUSIC_SEARCH"
        await safe_edit_or_send(
            client, chat_id, msg,
            "🎵 **لطفاً نام آهنگ یا خواننده را ارسال کنید:**",
            InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_main")]])
        )
        return

    if data in ["menu_pinterest", "menu_pexels", "menu_google_images"]:
        USER_STATES[chat_id] = "AWAIT_IMG_SEARCH"
        text = "🔍 **کلمه کلیدی مورد نظر برای جستجوی تصویر را بنویسید:**"
        kb = InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_main")]])
        await safe_edit_or_send(client, chat_id, msg, text, kb)
        return

    if data == "menu_ai":
        USER_STATES[chat_id] = "AWAIT_AI_QUERY"
        text = (
            "🤖 **دستیار هوش مصنوعی TMulti:**\n\n"
            "سوال، ایده یا درخواست متنی خود را ارسال کنید:"
        )
        kb = InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_main")]])
        await safe_edit_or_send(client, chat_id, msg, text, kb)
        return

    if data == "contact_admin":
        text = (
            "📩 **ارتباط با مدیریت و سازنده ربات:**\n\n"
            "برای هرگونه سوال، پیشنهاد یا گزارش خطا:\n"
            "👤 مدیر ربات: @TahatheGoat\n\n"
            "همچنین می‌توانید پیام خود را در همین گفتگو تایپ کنید."
        )
        kb = InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]])
        await safe_edit_or_send(client, chat_id, msg, text, kb)
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

        # Dispatch heavy job to GitHub Actions Runner
        dispatched = dispatch_github_worker(
            chat_id=chat_id,
            message_id=status_msg.id,
            url=target_url,
            fmt=fmt,
            quality=quality
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

    # --- Image Tools (itool_*) ---
    if data.startswith("itool_"):
        src = USER_DATA.get(chat_id, {}).get("image_path")
        if not src or not os.path.exists(src):
            await msg.edit_text(
                "⚠️ تصویری برای ویرایش یافت نشد. لطفاً ابتدا یک عکس ارسال کنید.",
                reply_markup=InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت", "menu_image_tools")]])
            )
            return

        if data == "itool_compress":
            status_msg = await client.send_message(chat_id, "🗜 در حال فشرده‌سازی هوشمند تصویر...")
            try:
                out_img, orig_s, new_s, saved = await asyncio.to_thread(itools.smart_compress_image, src)
                caption = f"🗜 حجم کاهش یافت: {saved}%\n({orig_s//1024}KB ➔ {new_s//1024}KB)"
                with open(out_img, "rb") as f:
                    await client.send_photo(chat_id, photo=f, caption=caption)
                await status_msg.delete()
                if os.path.exists(out_img):
                    os.remove(out_img)
            except Exception as e:
                await status_msg.edit_text(f"❌ خطا در فشرده‌سازی: {e}")
            return

        if data == "itool_filter":
            status_msg = await client.send_message(chat_id, "🎨 در حال اعمال فیلتر HDR و شارپ...")
            try:
                out_img = await asyncio.to_thread(itools.apply_aesthetic_filter, src, "hdr")
                with open(out_img, "rb") as f:
                    await client.send_photo(chat_id, photo=f, caption="✨ فیلتر HDR اعمال شد.")
                await status_msg.delete()
                if os.path.exists(out_img):
                    os.remove(out_img)
            except Exception as e:
                await status_msg.edit_text(f"❌ خطا در فیلتر: {e}")
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
                kb = menus.get_music_tools_keyboard(chat_id, get_user_lang)
                await status_msg.edit_text("🎵 **فایل صوتی دریافت شد! افکت مورد نظر را انتخاب کنید:**", reply_markup=kb)
                return
        except Exception as e:
            traceback.print_exc()
            await status_msg.edit_text(f"❌ خطا در دریافت فایل صوتی: {e}")
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

    if state in ["AWAIT_YT_KEYWORD_SEARCH", "AWAIT_YT_SEARCH"]:
        USER_STATES.pop(chat_id, None)
        await handle_youtube_keyword_search(client, message, text)
        return

    if state == "AWAIT_AI_QUERY":
        USER_STATES.pop(chat_id, None)
        status_msg = await client.send_message(chat_id, "🤖 در حال پردازش توسط هوش مصنوعی...")
        reply_txt = (
            f"🧠 **پاسخ دستیار هوش مصنوعی TMulti:**\n\n"
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
    # --- YouTube URL (Direct link triggers interactive Video Card with format & quality selection) ---
    if "youtube.com" in text or "youtu.be" in text:
        await render_yt_video_card(client, chat_id, text)
        return

    # --- Spotify URL ---
    if "open.spotify.com" in text:
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
        await handle_social_media_download(client, message, text, platform="SoundCloud")
        return

    # --- Pinterest URL ---
    if "pinterest.com" in text or "pin.it" in text:
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
