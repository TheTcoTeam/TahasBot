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
    Message, CallbackQuery
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


def get_user_lang(chat_id: int) -> str:
    if chat_id in USER_LANGS:
        return USER_LANGS[chat_id]
    if db is not None:
        try:
            pref = db.prefs.find_one({"chat_id": chat_id})
            if pref and "lang" in pref:
                USER_LANGS[chat_id] = pref["lang"]
                return pref["lang"]
        except Exception:
            pass
    return "fa"


def set_user_lang(chat_id: int, lang: str):
    USER_LANGS[chat_id] = lang
    if db is not None:
        try:
            db.prefs.update_one({"chat_id": chat_id}, {"$set": {"lang": lang}}, upsert=True)
        except Exception:
            pass


def save_user(chat_id: int, user_info: dict):
    if db is not None:
        try:
            user_info["chat_id"] = chat_id
            user_info["last_seen"] = int(time.time())
            db.users.update_one({"chat_id": chat_id}, {"$set": user_info}, upsert=True)
        except Exception:
            pass


def log_activity(chat_id: int, action: str, details: str = ""):
    if db is not None:
        try:
            db.activity.insert_one({
                "chat_id": chat_id,
                "action": action,
                "details": details,
                "timestamp": int(time.time())
            })
        except Exception:
            pass


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
@bot.on_command("start")
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
    kb = menus.get_main_keyboard(chat_id, get_user_lang)

    await message.reply(welcome_text, reply_markup=kb)


@bot.on_command("help")
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
    await message.reply(help_text, reply_markup=kb)


@bot.on_command("tool")
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
    await message.reply(text, reply_markup=kb)


@bot.on_command("tools")
async def cmd_tools(client: Client, message: Message):
    await cmd_tool(client, message)



# ----------------- CALLBACK QUERY HANDLER -----------------
@bot.on_callback_query()
async def on_callback(client: Client, query: CallbackQuery):
    chat_id = query.message.chat.id
    data = query.data
    lang = get_user_lang(chat_id)
    msg = query.message

    try:
        await query.answer()
    except Exception:
        pass

    # --- Main Navigation ---
    if data == "menu_main":
        USER_STATES.pop(chat_id, None)
        name = query.author.first_name or "کاربر"
        text = menus.get_menu_text("main_menu_welcome", lang=lang, name=name)
        kb = menus.get_main_keyboard(chat_id, get_user_lang)
        try:
            await msg.edit_text(text, reply_markup=kb)
        except Exception:
            await client.send_message(chat_id, text, reply_markup=kb)
        return

    if data == "help_home":
        await cmd_help(client, msg)
        return

    if data == "menu_youtube":
        text = (
            "▶️ **دانلود از یوتیوب:**\n\n"
            "شما می‌توانید لینک هر ویدیو یوتیوب را مستقیماً بفرستید، یا روی دکمه زیر کلیک کرده و نام ویدیو را برای جستجو تایپ کنید."
            if lang == "fa" else
            "▶️ **YouTube Downloader:**\n\n"
            "Send any YouTube link directly, or type keywords to search."
        )
        kb = InlineKeyboard([
            [InlineKeyboardButton("🔍 جستجوی ویدیو در یوتیوب", "yt_search_menu")],
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]
        ])
        await msg.edit_text(text, reply_markup=kb)
        return

    if data == "yt_search_menu":
        USER_STATES[chat_id] = "AWAIT_YT_SEARCH"
        await msg.edit_text(
            "🔍 **نام ویدیو یا کلمه کلیدی مورد نظر را ارسال کنید:**",
            reply_markup=InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_youtube")]])
        )
        return

    if data == "menu_ig_tiktok":
        text = (
            "📸 **اینستاگرام و تیک‌تاک:**\n\n"
            "کافیست لینک ریلز، پست یا ویدیوی تیک‌تاک را مستقیماً برای ربات ارسال کنید تا بلافاصله دانلود و برای شما ارسال شود."
        )
        kb = InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]])
        await msg.edit_text(text, reply_markup=kb)
        return

    if data == "menu_reddit_twitter":
        text = "🐦 **ردیت و توییتر (X):**\n\nلینک پست مورد نظر را ارسال کنید."
        kb = InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]])
        await msg.edit_text(text, reply_markup=kb)
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
        await msg.edit_text(text, reply_markup=kb)
        return

    if data == "msearch_prompt":
        USER_STATES[chat_id] = "AWAIT_MUSIC_SEARCH"
        await msg.edit_text(
            "🎵 **لطفاً نام آهنگ یا خواننده را ارسال کنید:**",
            reply_markup=InlineKeyboard([[InlineKeyboardButton("🔙 انصراف", "menu_main")]])
        )
        return

    if data == "menu_music_tools":
        kb = menus.get_music_tools_keyboard(chat_id, get_user_lang)
        await msg.edit_text("🎵 **ابزارهای ویرایش صدا و موزیک:**", reply_markup=kb)
        return

    if data == "menu_image_tools":
        kb = menus.get_image_tools_keyboard(chat_id, get_user_lang)
        await msg.edit_text("🎨 **ابزارهای ویرایش تصویر:**", reply_markup=kb)
        return

    if data == "menu_settings":
        kb = InlineKeyboard([
            [InlineKeyboardButton("🇮🇷 فارسی", "set_lang_fa"), InlineKeyboardButton("🇬🇧 English", "set_lang_en")],
            [InlineKeyboardButton("🔙 بازگشت", "menu_main")]
        ])
        await msg.edit_text("⚙️ **تنظیمات زبان ربات:**", reply_markup=kb)
        return

    if data.startswith("set_lang_"):
        new_lang = data.split("_")[2]
        set_user_lang(chat_id, new_lang)
        confirm = "✅ زبان با موفقیت به فارسی تغییر کرد." if new_lang == "fa" else "✅ Language changed to English."
        await msg.edit_text(confirm, reply_markup=InlineKeyboard([[InlineKeyboardButton("🔙 منوی اصلی", "menu_main")]]))
        return

    if data == "contact_admin":
        await msg.edit_text(
            "📩 **ارتباط با توسعه‌دهنده:**\n\nبرای پشتیبانی و ارسال پیشنهادات می‌توانید با تیم توسعه در تماس باشید.",
            reply_markup=InlineKeyboard([[InlineKeyboardButton("🔙 بازگشت", "menu_main")]])
        )
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
                    title=track_meta["title"],
                    performer=track_meta["artist"],
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
                    performer=probe.get("artist", "TahasBot Audio"),
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

    # 1. Check if user sent an Audio / Voice file
    if getattr(message, "audio", None) or getattr(message, "voice", None):
        status_msg = await message.reply("⏳ در حال دریافت فایل صوتی...")
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
        status_msg = await message.reply("⏳ در حال دریافت تصویر...")
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

    # Ignore slash commands already routed
    if text.startswith("/"):
        return

    state = USER_STATES.get(chat_id)

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
                title=top_track["title"],
                performer=top_track["artist"],
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

    # 1. Start web server for Render health checks
    await start_web_server()

    # 2. Connect and run Balethon client
    print("Connecting to Bale API...", flush=True)
    await bot.connect()
    print("Connected successfully! Starting long polling...", flush=True)
    await bot.start_polling()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot stopped.", flush=True)
    except Exception as e:
        traceback.print_exc()
