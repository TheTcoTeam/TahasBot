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
def dispatch_github_worker(chat_id: int, message_id: int, url: str, fmt: str = "mp4", quality: str = "720p") -> bool:
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
                "quality": quality
            }
        }
        res = requests.post(api_url, headers=headers, json=payload, timeout=10)
        print(f"GitHub workflow dispatch response: {res.status_code}", flush=True)
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

    text = (
        "🛠 **پنل مدیریت پیشرفته طه (Admin Panel)**\n\n"
        f"🚦 **وضعیت ربات:** {maint_status}\n"
        f"👥 کل کاربران: **{total_users}** نفر\n"
        f"🟢 کاربران ۲۴ ساعت گذشته: **{active_24h}** نفر\n"
        f"⬇️ کل دانلودها: **{total_dls}** فایل\n\n"
        "بخش مورد نظر خود را انتخاب کنید:"
    )

    kb = InlineKeyboard([
        [InlineKeyboardButton("📊 آمار و ارقام جامع", "adm_stats"), InlineKeyboardButton("👥 مدیریت کاربران", "adm_users_0_seen")],
        [InlineKeyboardButton("🔍 جستجوی کاربر", "adm_search"), InlineKeyboardButton("📜 جریان زنده فعالیت‌ها", "adm_act_0")],
        [InlineKeyboardButton("📢 ارسال همگانی (Broadcast)", "adm_bc")],
        [InlineKeyboardButton(maint_toggle_btn, "adm_maint")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]
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

        if data == "adm_noop":
            return

    # --- Submenu Navigation ---
    if data == "menu_youtube":
        text = (
            "▶️ **دانلود از یوتیوب:**\n\n"
            "شما می‌توانید لینک هر ویدیو یوتیوب را مستقیماً بفرستید، یا روی دکمه زیر کلیک کرده و نام ویدیو را برای جستجو تایپ کنید."
        )
        kb = InlineKeyboard([
            [InlineKeyboardButton("🔍 جستجوی ویدیو در یوتیوب", "yt_search_menu")],
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]
        ])
        await safe_edit_or_send(client, chat_id, msg, text, kb)
        return

    if data == "yt_search_menu":
        USER_STATES[chat_id] = "AWAIT_YT_SEARCH"
        await safe_edit_or_send(
            client, chat_id, msg,
            "🔍 **نام ویدیو یا کلمه کلیدی مورد نظر را ارسال کنید:**",
            InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_youtube")]])
        )
        return

    if data == "menu_ig_tiktok":
        text = (
            "📸 **اینستاگرام و تیک‌تاک:**\n\n"
            "کافیست لینک ریلز، پست یا ویدیوی تیک‌تاک را مستقیماً برای ربات ارسال کنید تا بلافاصله دانلود و برای شما ارسال شود."
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

    if state == "AWAIT_YT_SEARCH":
        USER_STATES.pop(chat_id, None)
        await handle_youtube_search(client, message, text)
        return

    # 4. Check for URL patterns
    # --- YouTube URL ---
    if "youtube.com" in text or "youtu.be" in text:
        USER_DATA.setdefault(chat_id, {})["last_yt_url"] = text
        kb = InlineKeyboard(
            [InlineKeyboardButton("🎬 دانلود ویدیو (720p)", "dl_yt_mp4_720p"),
             InlineKeyboardButton("🎬 کیفیت متوسط (480p)", "dl_yt_mp4_480p")],
            [InlineKeyboardButton("🎵 استخراج صوت (MP3)", "dl_yt_mp3_audio")],
            [InlineKeyboardButton("🔙 انصراف", "menu_main")]
        )
        await message.reply("🎬 **لینک یوتیوب شناسایی شد.**\nکیفیت و فرمت دلخواه را انتخاب کنید:", reply_markup=kb)
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


async def handle_youtube_search(client: Client, message: Message, query: str):
    chat_id = message.chat.id
    status_msg = await message.reply(f"🔍 در حال جستجو در یوتیوب: «{query}»...")

    try:
        # Search YouTube via yt-dlp flat-playlist extraction
        cmd = [
            sys.executable, "-m", "yt_dlp",
            f"ytsearch5:{query}",
            "--flat-playlist",
            "--dump-json"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        lines = [line for line in res.stdout.splitlines() if line.strip()]

        if not lines:
            await status_msg.edit_text("❌ نتیجه‌ای در یوتیوب یافت نشد.")
            return

        kb = InlineKeyboard()
        for idx, line in enumerate(lines[:5], 1):
            item = json.loads(line)
            vid = item.get("id")
            title = item.get("title", f"Video {idx}")[:35]
            # Store in USER_DATA
            USER_DATA.setdefault(chat_id, {})[f"yt_{vid}"] = f"https://www.youtube.com/watch?v={vid}"
            kb.add_row((f"▶️ {idx}. {title}", f"dl_yt_mp4_720p_{vid}"))

        kb.add_row(("🔙 بازگشت", "menu_youtube"))
        await status_msg.edit_text("🎬 **نتایج جستجوی یوتیوب:**", reply_markup=kb)

    except Exception as e:
        traceback.print_exc()
        await status_msg.edit_text(f"❌ خطا در جستجوی یوتیوب: {e}")


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
