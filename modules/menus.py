# -*- coding: utf-8 -*-
"""
Menu keyboards and UI templates for TahasBot on Bale Messenger.
Optimized 100% for Bale: all buttons use callback_data (no unsupported inline query switches).
"""

from balethon.objects import InlineKeyboard, InlineKeyboardButton

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

InlineKeyboardMarkup = InlineKeyboard


MENU_STRINGS = {
    "main_menu_welcome": {
        "fa": "👋 سلام **{name}** عزیز! به ربات همه‌کاره **TahasBot** خوش آمدید.\n\n"
              "⚡️ شما می‌توانید هر لینکی (اینستاگرام، تیک‌تاک، یوتیوب، اسپاتیفای، ساندکلاد، ردیت، توییتر، پینترست و...) "
              "را مستقیماً ارسال کنید تا بلافاصله با بالاترین کیفیت دانلود شود.\n\n"
              "🎛 یا از منوی زیر پلتفرم یا ابزار مورد نظر خود را انتخاب کنید:",
        "en": "👋 Hello **{name}**! Welcome to **TahasBot**.\n\n"
              "⚡️ Send any link directly (Instagram, TikTok, YouTube, Spotify, SoundCloud, Reddit, Twitter, Pinterest...) "
              "to download instantly.\n\n"
              "🎛 Or choose an option below to explore features:"
    },
    "btn_youtube": {"fa": "▶️ یوتیوب", "en": "▶️ YouTube"},
    "btn_ig_tiktok": {"fa": "📸 اینستاگرام / تیک‌تاک", "en": "📸 Instagram / TikTok"},
    "btn_reddit_twitter": {"fa": "🐦 ردیت / توییتر (X)", "en": "🐦 Reddit / Twitter (X)"},
    "btn_video_tools": {"fa": "🎬 ابزارهای ویدیو", "en": "🎬 Video Tools"},
    "btn_spotify": {"fa": "🟢 اسپاتیفای", "en": "🟢 Spotify"},
    "btn_soundcloud": {"fa": "☁️ ساندکلاد", "en": "☁️ SoundCloud"},
    "btn_yt_music": {"fa": "🔴 یوتیوب موزیک", "en": "🔴 YouTube Music"},
    "btn_music_tools": {"fa": "🎵 ابزارهای صدا و موزیک", "en": "🎵 Music Tools"},
    "btn_pinterest": {"fa": "📌 پینترست", "en": "📌 Pinterest"},
    "btn_pexels": {"fa": "🖼 پکسلز (Pexels)", "en": "🖼 Pexels"},
    "btn_google_images": {"fa": "🔍 تصاویر گوگل", "en": "🔍 Google Images"},
    "btn_image_tools": {"fa": "🎨 ابزارهای تصویر", "en": "🎨 Image Tools"},
    "btn_my_playlists": {"fa": "📃 پلی‌لیست‌های من", "en": "📃 My Playlists"},
    "btn_tmulti_ai": {"fa": "🤖 دستیار هوش مصنوعی TMulti", "en": "🤖 TMulti AI Assistant"},
    "btn_message_dev": {"fa": "📩 پیام به سازنده", "en": "📩 Message Developer"},
    "btn_profile": {"fa": "👤 پروفایل من", "en": "👤 My Profile"},
    "btn_help": {"fa": "❓ راهنمای کامل", "en": "❓ Full Help Guide"},
    "btn_back": {"fa": "🔙 بازگشت", "en": "🔙 Back"},
    "btn_back_main": {"fa": "🔙 منوی اصلی", "en": "🔙 Main Menu"},
}


def get_menu_text(key: str, lang: str = "fa", **kwargs) -> str:
    item = MENU_STRINGS.get(key, {})
    txt = item.get(lang) or item.get("fa") or key
    if "{name}" in txt:
        if kwargs.get("name"):
            txt = txt.replace("{name}", str(kwargs["name"]))
        else:
            txt = txt.replace("**{name}** عزیز", "عزیز").replace("{name}", "کاربر")
    if kwargs:
        try:
            return txt.format(**kwargs)
        except Exception:
            return txt
    return txt


def get_main_keyboard(chat_id, get_lang_fn=None, is_admin_fn=None) -> InlineKeyboardMarkup:
    """The 9-row FigJam layout with Profile and Admin Panel."""
    lang = get_lang_fn(chat_id) if get_lang_fn else "fa"
    def b(k): return get_menu_text(k, lang)

    rows = [
        # Row 1: YouTube | Instagram / TikTok
        [
            InlineKeyboardButton(b("btn_youtube"), "menu_youtube"),
            InlineKeyboardButton(b("btn_ig_tiktok"), "menu_ig_tiktok")
        ],
        # Row 2: Reddit / Twitter | Video Tools
        [
            InlineKeyboardButton(b("btn_reddit_twitter"), "menu_reddit_twitter"),
            InlineKeyboardButton(b("btn_video_tools"), "menu_vedit")
        ],
        # Row 3: Spotify | SoundCloud
        [
            InlineKeyboardButton(b("btn_spotify"), "menu_music_spotify"),
            InlineKeyboardButton(b("btn_soundcloud"), "menu_music_sc")
        ],
        # Row 4: YouTube Music | Music Tools
        [
            InlineKeyboardButton(b("btn_yt_music"), "menu_music_ytm"),
            InlineKeyboardButton(b("btn_music_tools"), "menu_music_tools")
        ],
        # Row 5: Pinterest | Pexels
        [
            InlineKeyboardButton(b("btn_pinterest"), "menu_pinterest"),
            InlineKeyboardButton(b("btn_pexels"), "menu_pexels")
        ],
        # Row 6: Google Images | Image Tools
        [
            InlineKeyboardButton(b("btn_google_images"), "menu_google_images"),
            InlineKeyboardButton(b("btn_image_tools"), "menu_image_tools")
        ],
        # Row 7: TMulti AI | My Playlists
        [
            InlineKeyboardButton(b("btn_tmulti_ai"), "menu_ai"),
            InlineKeyboardButton(b("btn_my_playlists"), "menu_playlists")
        ],
        # Row 8: Message Developer | My Profile
        [
            InlineKeyboardButton(b("btn_message_dev"), "contact_admin"),
            InlineKeyboardButton(b("btn_profile"), "menu_profile")
        ],
        # Row 9: Help
        [
            InlineKeyboardButton(b("btn_help"), "help_home")
        ]
    ]

    if is_admin_fn and is_admin_fn(chat_id):
        rows.append([InlineKeyboardButton("👑 پنل مدیریت پیشرفته (Admin Panel)", "adm_home")])

    return InlineKeyboardMarkup(rows)


def get_youtube_keyboard(chat_id, get_lang_fn=None) -> InlineKeyboardMarkup:
    """YouTube submenu with Keyword and Channel search."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 جستجو با کلمه کلیدی (Keyword)", "yt_search_kw")],
        [InlineKeyboardButton("👤 جستجو در کانال‌ها (Channel)", "yt_search_ch")],
        [InlineKeyboardButton("🎬 ابزارهای ویدیو (Video Editor)", "menu_vedit")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]
    ])


def get_ig_tiktok_keyboard(chat_id, get_lang_fn=None) -> InlineKeyboardMarkup:
    """Instagram / TikTok submenu."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 ارسال لینک اینستاگرام / تیک‌تاک", "menu_ig_tiktok")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]
    ])


def get_reddit_twitter_keyboard(chat_id, get_lang_fn=None) -> InlineKeyboardMarkup:
    """Reddit / Twitter submenu."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔍 جستجو در ردیت (Reddit Search)", "rd_search"),
            InlineKeyboardButton("📥 راهنمای ارسال لینک", "rd_link_help")
        ],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]
    ])


def get_music_hub_keyboard(chat_id, platform="spotify", get_lang_fn=None) -> InlineKeyboardMarkup:
    """Music Hub keyboards for Spotify, SoundCloud, and YouTube Music."""
    if platform == "sc":
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🎵 جستجوی آهنگ (Tracks)", "sc_search_track"),
                InlineKeyboardButton("👤 جستجوی هنرمند (Artists)", "sc_search_artist")
            ],
            [
                InlineKeyboardButton("💿 جستجوی آلبوم / ست (Albums)", "sc_search_album"),
                InlineKeyboardButton("🔥 برترین‌های ساندکلاد (Trending)", "sc_trending")
            ],
            [InlineKeyboardButton("📑 جستجوی پلی‌لیست", "sc_search_playlist")],
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]
        ])
    elif platform == "ytm":
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🎵 جستجوی آهنگ (Tracks)", "ytm_search_track"),
                InlineKeyboardButton("👤 جستجوی خواننده (Artists)", "ytm_search_artist")
            ],
            [
                InlineKeyboardButton("💿 جستجوی آلبوم (Albums)", "ytm_search_album"),
                InlineKeyboardButton("🔥 داغ‌ترین‌های یوتیوب موزیک", "ytm_trending")
            ],
            [InlineKeyboardButton("📑 جستجوی پلی‌لیست", "ytm_search_playlist")],
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]
        ])
    else:  # Spotify
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🎵 جستجوی آهنگ (Tracks)", "sp_search_track"),
                InlineKeyboardButton("👤 جستجوی خواننده (Artists)", "sp_search_artist")
            ],
            [
                InlineKeyboardButton("💿 جستجوی آلبوم (Albums)", "sp_search_album"),
                InlineKeyboardButton("🔥 داغ‌ترین‌های هفته (Hottest)", "sp_hottest")
            ],
            [InlineKeyboardButton("📑 جستجوی پلی‌لیست", "sp_search_playlist")],
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]
        ])


def get_rega_search_selector(query: str, chat_id=None, get_lang_fn=None) -> InlineKeyboardMarkup:
    """Multi-platform search selector card when a user types text in chat."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🟢 اسپاتیفای (Spotify)", f"sel_sp"),
            InlineKeyboardButton("☁️ ساندکلاد (SoundCloud)", f"sel_sc")
        ],
        [
            InlineKeyboardButton("🔴 یوتیوب موزیک (YT Music)", f"sel_ytm"),
            InlineKeyboardButton("▶️ ویدیوی یوتیوب (YouTube)", f"sel_yt")
        ],
        [
            InlineKeyboardButton("👤 خواننده در اسپاتیفای", f"sel_art"),
            InlineKeyboardButton("💿 آلبوم در اسپاتیفای", f"sel_alb")
        ],
        [
            InlineKeyboardButton("🎙 شناسایی با صدا (Shazam)", "menu_audd"),
            InlineKeyboardButton("🔥 داغ‌ترین‌های هفته", "sp_hottest")
        ],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]
    ])


def get_image_platform_keyboard(chat_id, platform="google", get_lang_fn=None) -> InlineKeyboardMarkup:
    """Image platforms submenu."""
    if platform == "pinterest":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔍 جستجو در پینترست", "pt_search")],
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]
        ])
    elif platform == "pexels":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔍 جستجو در پکسلز (Pexels)", "px_search")],
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]
        ])
    else:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔍 جستجو در تصاویر گوگل", "gi_search")],
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]
        ])


def get_music_tools_keyboard(chat_id, get_lang_fn=None) -> InlineKeyboardMarkup:
    """Section 2: Music Tools Hub."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌙 اسلو + ریورب", "mtool_slow"),
            InlineKeyboardButton("⚡ نایتکور", "mtool_night")
        ],
        [
            InlineKeyboardButton("🎧 هشت‌بعدی (8D)", "mtool_eight"),
            InlineKeyboardButton("🔊 بیس بوست", "mtool_bass")
        ],
        [
            InlineKeyboardButton("🎙 کارائوکه (حذف خواننده)", "mtool_vocal"),
            InlineKeyboardButton("⏩ تغییر سرعت و زیروبم", "mtool_speed")
        ],
        [
            InlineKeyboardButton("✂️ برش موزیک (رینگتون)", "mtool_trim"),
            InlineKeyboardButton("🔄 تبدیل فرمت (320k / FLAC)", "mtool_convert")
        ],
        [InlineKeyboardButton("🏷 ویرایش تگ و کاور", "mtool_tags")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]
    ])


def get_image_tools_keyboard(chat_id, get_lang_fn=None) -> InlineKeyboardMarkup:
    """Section 3: Image Tools Hub."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🪄 حذف پس‌زمینه (شفاف)", "itool_rembg"),
            InlineKeyboardButton("🗜 فشرده‌سازی هوشمند", "itool_compress")
        ],
        [
            InlineKeyboardButton("🔄 تبدیل فرمت (WebP/PNG/JPG/PDF)", "itool_convert"),
            InlineKeyboardButton("📐 تغییر اندازه و برش", "itool_crop")
        ],
        [
            InlineKeyboardButton("🎨 فیلترهای اختصاصی و شارپ", "itool_filter"),
            InlineKeyboardButton("🏷 درج واترمارک و متن", "itool_watermark")
        ],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", "menu_main")]
    ])


def get_tools_picker_keyboard(chat_id, get_lang_fn=None) -> InlineKeyboardMarkup:
    """Picker shown when /tool is invoked without a reply."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 ابزارهای ویدیو (Video Tools)", "menu_vedit")],
        [InlineKeyboardButton("🎵 ابزارهای صدا و موزیک (Music Tools)", "menu_music_tools")],
        [InlineKeyboardButton("🖼 ابزارهای تصویر (Image Tools)", "menu_image_tools")],
        [InlineKeyboardButton("🔙 منوی اصلی", "menu_main")]
    ])
