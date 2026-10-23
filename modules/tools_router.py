# -*- coding: utf-8 -*-
"""
Tools router for TMulti Bot Revamp.
Handles user requests for Video, Music, and Image editing tools,
as well as the /tool command dispatcher.
"""

import os
import re
import time
import asyncio
import traceback
from pyrogram import Client
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import modules.music_tools as mtools
from modules import audio_meta
import modules.image_tools as itools
from modules.menus import (
    get_music_tools_keyboard, get_image_tools_keyboard,
    get_tools_picker_keyboard, get_main_keyboard
)


async def handle_tool_command(client: Client, message: Message, user_data: dict, user_states: dict, get_lang_fn=None):
    """Dispatcher for /tool, /tools, or /edit commands."""
    chat_id = message.chat.id
    reply = message.reply_to_message
    lang = get_lang_fn(chat_id) if get_lang_fn else "fa"

    if not reply:
        text = (
            "🎛 **جعبه ابزار TMulti**\n\n"
            "شما می‌توانید روی هر **ویدیو**، **آهنگ/وویس** یا **عکس/استیکر** ریپلای بزنید و دستور `/tool` را ارسال کنید تا ویرایشگر باز شود.\n\n"
            "یا یکی از بخش‌های زیر را انتخاب کنید:"
            if lang == "fa" else
            "🎛 **TMulti Toolbox**\n\n"
            "Reply to any **video**, **audio/voice**, or **photo/sticker** with `/tool` to open the editor directly.\n\n"
            "Or select a tool category below:"
        )
        return await message.reply_text(text, reply_markup=get_tools_picker_keyboard(chat_id, get_lang_fn))

    # Check replied media type
    if reply.video or (reply.document and (reply.document.mime_type or "").startswith("video/")):
        v = reply.video or reply.document
        user_data.setdefault(chat_id, {})["last_video_file_id"] = v.file_id
        text = "🎬 **ابزارهای ویرایش ویدیو آماده است:**" if lang == "fa" else "🎬 **Video Editor is ready:**"
        # Download video to vedit_path
        status = await message.reply_text("⏳ در حال دریافت ویدیو..." if lang == "fa" else "⏳ Downloading video...")
        try:
            os.makedirs("jobs", exist_ok=True)
            vpath = await client.download_media(v.file_id, file_name=f"jobs/src_{chat_id}_{int(time.time())}.mp4")
            user_data[chat_id]["vedit_path"] = vpath
            # bot.py runs as __main__: "from bot import" would load a second
            # full copy of the bot (new client, DB connection, handlers).
            import sys as _sys
            _main = _sys.modules.get("__main__")
            video_editor_kb = getattr(_main, "video_editor_kb", None) or _sys.modules["bot"].video_editor_kb
            await status.edit_text(text, reply_markup=video_editor_kb(chat_id))
        except Exception as e:
            await status.edit_text(f"❌ خطا در دریافت ویدیو: {e}")
        return

    if reply.audio or reply.voice or (reply.document and (reply.document.mime_type or "").startswith("audio/")):
        a = reply.audio or reply.voice or reply.document
        user_data.setdefault(chat_id, {})["last_audio_file_id"] = a.file_id
        status = await message.reply_text("⏳ در حال دریافت فایل صوتی..." if lang == "fa" else "⏳ Downloading audio...")
        try:
            os.makedirs("music_downloads", exist_ok=True)
            ext = ".mp3"
            if reply.audio and reply.audio.file_name:
                ext = os.path.splitext(reply.audio.file_name)[1] or ".mp3"
            apath = await client.download_media(a.file_id, file_name=f"music_downloads/audio_{chat_id}_{int(time.time())}{ext}")
            user_data[chat_id]["music_tools_path"] = apath
            text = "🎵 **ابزارهای ویرایش موزیک و افکت صوتی آماده است:**" if lang == "fa" else "🎵 **Music Tools & Effects ready:**"
            await status.edit_text(text, reply_markup=get_music_tools_keyboard(chat_id, get_lang_fn))
        except Exception as e:
            await status.edit_text(f"❌ خطا در دریافت فایل صوتی: {e}")
        return

    if reply.photo or reply.sticker or (reply.document and (reply.document.mime_type or "").startswith("image/")):
        p = reply.photo or reply.sticker or reply.document
        user_data.setdefault(chat_id, {})["last_image_file_id"] = p.file_id
        status = await message.reply_text("⏳ در حال دریافت تصویر..." if lang == "fa" else "⏳ Downloading image...")
        try:
            os.makedirs("media_downloads", exist_ok=True)
            ipath = await client.download_media(p.file_id, file_name=f"media_downloads/img_{chat_id}_{int(time.time())}.png")
            user_data[chat_id]["image_tools_path"] = ipath
            text = "🎨 **ابزارهای ویرایش تصویر آماده است:**" if lang == "fa" else "🎨 **Image Tools ready:**"
            await status.edit_text(text, reply_markup=get_image_tools_keyboard(chat_id, get_lang_fn))
        except Exception as e:
            await status.edit_text(f"❌ خطا در دریافت تصویر: {e}")
        return

    # Fallback
    await message.reply_text("⚠️ لطفاً روی یک ویدیو، موزیک یا عکس ریپلای بزنید.")


async def handle_music_tool_callback(client: Client, query: CallbackQuery, user_data: dict, user_states: dict, get_lang_fn=None):
    """Handles mtool_* callbacks."""
    chat_id = query.message.chat.id
    data = query.data
    lang = get_lang_fn(chat_id) if get_lang_fn else "fa"
    msg = query.message

    src = user_data.get(chat_id, {}).get("music_tools_path")
    if not src or not os.path.exists(src):
        # Try downloading last_audio_file_id
        file_id = user_data.get(chat_id, {}).get("last_audio_file_id")
        if file_id:
            try:
                os.makedirs("music_downloads", exist_ok=True)
                src = await client.download_media(file_id, file_name=f"music_downloads/src_{chat_id}_{int(time.time())}.mp3")
                user_data.setdefault(chat_id, {})["music_tools_path"] = src
            except Exception:
                src = None

    if not src or not os.path.exists(src):
        user_states[chat_id] = "AWAIT_AUDIO_UPLOAD"
        return await msg.edit_text(
            "🎵 **ابزارهای صدا و موزیک**\n\n"
            "هنوز فایلی برای ویرایش انتخاب نشده است.\n"
            "لطفاً یک فایل صوتی، آهنگ یا ویس بفرستید، یا روی یک آهنگ ریپلای زده و `/tool` بفرستید.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu_main")]])
        )

    # 1. Preset Effects (Slowed, Nightcore, 8D, Bass Boost, Vocal Remover)
    effect_map = {
        "mtool_slow": "slow",
        "mtool_night": "night",
        "mtool_eight": "eight",
        "mtool_bass": "bass",
        "mtool_vocal": "vocal"
    }

    if data in effect_map:
        eff_code = effect_map[data]
        eff_label = mtools.AUDIO_FILTERS[eff_code]["label_fa"] if lang == "fa" else mtools.AUDIO_FILTERS[eff_code]["label_en"]
        status = await client.send_message(chat_id, f"🎛 در حال اعمال افکت **{eff_label}**...")
        try:
            out_file = await asyncio.to_thread(mtools.process_audio_filter, src, eff_code)
            info = await asyncio.to_thread(mtools.probe_audio_info, src)  # tags live on the source
            await client.send_audio(
                chat_id, audio=out_file,
                title=f"{info.get('title','Track')} ({eff_label})",
                performer=info.get('artist', 'TMulti Audio'), tag_source=src,
                caption=f"🎧 **{eff_label}**\n⚡️ پردازش شده توسط @TMultiBot",
                reply_markup=get_music_tools_keyboard(chat_id, get_lang_fn)
            )
            await status.delete()
        except Exception as e:
            traceback.print_exc()
            await status.edit_text(f"❌ خطا در پردازش صدا:\n`{str(e)[:200]}`")
        return

    # 2. Speed / Pitch presets
    if data == "mtool_speed":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🐌 0.75x", callback_data="mtoolspeed_0.75"),
             InlineKeyboardButton("⚡ 1.25x", callback_data="mtoolspeed_1.25")],
            [InlineKeyboardButton("🚀 1.5x", callback_data="mtoolspeed_1.5"),
             InlineKeyboardButton("🏎 2.0x", callback_data="mtoolspeed_2.0")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="menu_music_tools")]
        ])
        return await msg.edit_text("⏩ **سرعت مورد نظر را انتخاب کنید:**", reply_markup=kb)

    if data.startswith("mtoolspeed_"):
        spd = float(data.split("_")[1])
        status = await client.send_message(chat_id, f"⏳ در حال تغییر سرعت به {spd}x...")
        try:
            out_file = await asyncio.to_thread(mtools.change_audio_speed, src, spd)
            info = await asyncio.to_thread(mtools.probe_audio_info, src)  # tags live on the source
            await client.send_audio(
                chat_id, audio=out_file,
                title=f"{info.get('title','Track')} ({spd}x)",
                performer=info.get('artist', 'TMulti Audio'), tag_source=src,
                caption=f"⏩ **سرعت {spd}x**\n⚡️ پردازش شده توسط @TMultiBot",
                reply_markup=get_music_tools_keyboard(chat_id, get_lang_fn)
            )
            await status.delete()
        except Exception as e:
            await status.edit_text(f"❌ خطا: {e}")
        return

    # 3. Audio Trimming
    if data == "mtool_trim":
        user_states[chat_id] = "MTOOL_TRIM"
        return await msg.edit_text(
            "✂️ **برش موزیک (ساخت رینگتون)**\n\n"
            "لطفاً زمان شروع و پایان را به این صورت ارسال کنید:\n"
            "`00:15 - 00:45` یا `01:10 - 01:40`",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 انصراف", callback_data="menu_music_tools")]])
        )

    # 4. Format Conversion
    if data == "mtool_convert":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎵 MP3 (320 kbps)", callback_data="mtoolconv_mp3"),
             InlineKeyboardButton("💎 FLAC (Lossless)", callback_data="mtoolconv_flac")],
            [InlineKeyboardButton("🍎 M4A (AAC 256k)", callback_data="mtoolconv_m4a"),
             InlineKeyboardButton("📻 WAV (Studio Uncompressed)", callback_data="mtoolconv_wav")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="menu_music_tools")]
        ])
        return await msg.edit_text("🔄 **فرمت صوتی مورد نظر را انتخاب کنید:**", reply_markup=kb)

    if data.startswith("mtoolconv_"):
        fmt = data.split("_")[1]
        status = await client.send_message(chat_id, f"⏳ در حال تبدیل به فرمت {fmt.upper()}...")
        try:
            out_file = await asyncio.to_thread(mtools.convert_audio_format, src, fmt)
            info = await asyncio.to_thread(mtools.probe_audio_info, src)  # tags live on the source
            if fmt == "flac" or fmt == "wav":
                tags = audio_meta.read_tags(src)
                audio_meta.write_tags(out_file, title=info.get('title'), artist=info.get('artist'),
                                      album=tags.get('album'), year=tags.get('year'), cover=tags.get('cover'))
                await client.send_document(
                    chat_id, document=out_file,
                    file_name=audio_meta.safe_filename(info.get('artist'), info.get('title'), f".{fmt}"),
                    caption=f"💎 **فرمت {fmt.upper()}** با بالاترین کیفیت استودیویی",
                    reply_markup=get_music_tools_keyboard(chat_id, get_lang_fn)
                )
            else:
                await client.send_audio(
                    chat_id, audio=out_file,
                    title=info.get('title','Track'),
                    performer=info.get('artist', 'TMulti Audio'), tag_source=src,
                    caption=f"🎵 **فرمت {fmt.upper()}**",
                    reply_markup=get_music_tools_keyboard(chat_id, get_lang_fn)
                )
            await status.delete()
        except Exception as e:
            await status.edit_text(f"❌ خطا: {e}")
        return

    # 5. Tag & Cover Editor
    if data == "mtool_tags":
        user_states[chat_id] = "MTOOL_TAGS"
        return await msg.edit_text(
            "🏷 **ویرایش مشخصات و تگ‌های آهنگ**\n\n"
            "عنوان آهنگ و نام خواننده را در یک خط با علامت `-` بفرستید:\n"
            "`نام آهنگ - نام خواننده`\n\n"
            "_همچنین می‌توانید بعد از این مرحله، عکس کاور را بفرستید تا روی فایل قرار گیرد._",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 انصراف", callback_data="menu_music_tools")]])
        )


async def handle_image_tool_callback(client: Client, query: CallbackQuery, user_data: dict, user_states: dict, get_lang_fn=None):
    """Handles itool_* callbacks."""
    chat_id = query.message.chat.id
    data = query.data
    lang = get_lang_fn(chat_id) if get_lang_fn else "fa"
    msg = query.message

    src = user_data.get(chat_id, {}).get("image_tools_path")
    if not src or not os.path.exists(src):
        # Try downloading last_image_file_id
        file_id = user_data.get(chat_id, {}).get("last_image_file_id")
        if not file_id and query.message:
            p = query.message.photo or (query.message.document if (getattr(query.message.document, "mime_type", "") or "").startswith("image/") else None)
            if p and getattr(p, "file_id", None):
                file_id = p.file_id
                user_data.setdefault(chat_id, {})["last_image_file_id"] = file_id
        if file_id:
            try:
                os.makedirs("media_downloads", exist_ok=True)
                src = await client.download_media(file_id, file_name=f"media_downloads/src_{chat_id}_{int(time.time())}.png")
                user_data.setdefault(chat_id, {})["image_tools_path"] = src
            except Exception:
                src = None

    if not src or not os.path.exists(src):
        user_states[chat_id] = "AWAIT_IMAGE_UPLOAD"
        return await msg.edit_text(
            "🎨 **ابزارهای ویرایش تصویر**\n\n"
            "هنوز تصویری برای ویرایش انتخاب نشده است.\n"
            "لطفاً یک عکس، استیکر یا فایل تصویر بفرستید، یا روی یک عکس ریپلای زده و `/tool` بفرستید.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu_main")]])
        )

    # 1. Remove Background
    if data == "itool_rembg":
        status = await client.send_message(chat_id, "🪄 در حال حذف پس‌زمینه و شفاف‌سازی...")
        try:
            out_file = await asyncio.to_thread(itools.remove_image_background, src)
            await client.send_document(
                chat_id, document=out_file,
                caption="🪄 **پس‌زمینه با موفقیت حذف شد (PNG شفاف)**\n⚡️ پردازش شده توسط @TMultiBot",
                reply_markup=get_image_tools_keyboard(chat_id, get_lang_fn)
            )
            await status.delete()
        except Exception as e:
            await status.edit_text(f"❌ خطا: {e}")
        return

    # 2. Smart Compress
    if data == "itool_compress":
        status = await client.send_message(chat_id, "🗜 در حال فشرده‌سازی هوشمند...")
        try:
            out_file, orig_sz, new_sz, saved_pct = await asyncio.to_thread(itools.smart_compress_image, src)
            orig_kb = round(orig_sz / 1024, 1)
            new_kb = round(new_sz / 1024, 1)
            caption = (
                f"🗜 **فشرده‌سازی هوشمند انجام شد:**\n"
                f"📊 حجم اولیه: `{orig_kb} KB` ⬅️ حجم جدید: `{new_kb} KB`\n"
                f"⚡️ کاهش حجم: **{saved_pct}%** بدون افت کیفیت محسوس!"
            )
            await client.send_document(
                chat_id, document=out_file,
                caption=caption,
                reply_markup=get_image_tools_keyboard(chat_id, get_lang_fn)
            )
            await status.delete()
        except Exception as e:
            await status.edit_text(f"❌ خطا: {e}")
        return

    # 3. Format Convert
    if data == "itool_convert":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🖼 PNG (با شفافیت)", callback_data="itoolconv_png"),
             InlineKeyboardButton("📸 JPG / JPEG", callback_data="itoolconv_jpg")],
            [InlineKeyboardButton("🌐 WebP (وب و استیکر)", callback_data="itoolconv_webp"),
             InlineKeyboardButton("📄 PDF (سند)", callback_data="itoolconv_pdf")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="menu_image_tools")]
        ])
        return await msg.edit_text("🔄 **فرمت خروجی مورد نظر را انتخاب کنید:**", reply_markup=kb)

    if data.startswith("itoolconv_"):
        fmt = data.split("_")[1]
        status = await client.send_message(chat_id, f"⏳ در حال تبدیل تصویر به {fmt.upper()}...")
        try:
            out_file = await asyncio.to_thread(itools.convert_image_format, src, fmt)
            await client.send_document(
                chat_id, document=out_file,
                caption=f"🔄 **تبدیل به {fmt.upper()} انجام شد.**",
                reply_markup=get_image_tools_keyboard(chat_id, get_lang_fn)
            )
            await status.delete()
        except Exception as e:
            await status.edit_text(f"❌ خطا: {e}")
        return

    # 4. Crop & Resize
    if data == "itool_crop":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔲 ۱:۱ مربع (پروفایل / آواتار)", callback_data="itoolcrop_1:1")],
            [InlineKeyboardButton("🖥 ۱۶:۹ افقی (یوتیوب / دسکتاپ)", callback_data="itoolcrop_16:9")],
            [InlineKeyboardButton("📱 ۹:۱۶ عمودی (استوری / ریلز)", callback_data="itoolcrop_9:16")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="menu_image_tools")]
        ])
        return await msg.edit_text("📐 **نسبت ابعاد مورد نظر را انتخاب کنید:**", reply_markup=kb)

    if data.startswith("itoolcrop_"):
        ratio = data.split("_")[1]
        status = await client.send_message(chat_id, f"⏳ در حال برش با نسبت {ratio}...")
        try:
            out_file = await asyncio.to_thread(itools.crop_resize_preset, src, ratio)
            await client.send_photo(
                chat_id, photo=out_file,
                caption=f"📐 **برش با نسبت {ratio} انجام شد.**",
                reply_markup=get_image_tools_keyboard(chat_id, get_lang_fn)
            )
            await status.delete()
        except Exception as e:
            await status.edit_text(f"❌ خطا: {e}")
        return

    # 5. Aesthetic Filters
    if data == "itool_filter":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✨ افزایش شارپنس و وضوح", callback_data="itoolfltr_sharpen")],
            [InlineKeyboardButton("🌈 افکت HDR و تقویت رنگ", callback_data="itoolfltr_hdr")],
            [InlineKeyboardButton("🎞 سیاه و سفید سینمایی", callback_data="itoolfltr_bw")],
            [InlineKeyboardButton("☕ تم نوستالژی و وینتیج", callback_data="itoolfltr_vintage")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="menu_image_tools")]
        ])
        return await msg.edit_text("🎨 **فیلتر مورد نظر را انتخاب کنید:**", reply_markup=kb)

    if data.startswith("itoolfltr_"):
        flt = data.split("_")[1]
        status = await client.send_message(chat_id, "⏳ در حال اعمال فیلتر...")
        try:
            out_file = await asyncio.to_thread(itools.apply_image_filter, src, flt)
            await client.send_photo(
                chat_id, photo=out_file,
                caption="🎨 **فیلتر با موفقیت اعمال شد.**",
                reply_markup=get_image_tools_keyboard(chat_id, get_lang_fn)
            )
            await status.delete()
        except Exception as e:
            await status.edit_text(f"❌ خطا: {e}")
        return

    # 6. Watermark
    if data == "itool_watermark":
        user_states[chat_id] = "ITOOL_WATERMARK"
        return await msg.edit_text(
            "🏷 **درج واترمارک و متن روی عکس**\n\n"
            "متن مورد نظر برای درج روی تصویر را ارسال کنید (مثلاً آیدی کانال یا اسم شما):\n"
            "`@MyChannel`",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 انصراف", callback_data="menu_image_tools")]])
        )
