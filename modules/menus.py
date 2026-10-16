# -*- coding: utf-8 -*-
"""
Menu keyboards and UI templates according to the FigJam revamp + RegaSpotify specifications.
Supports multi-language: Persian (fa), English (en), Russian (ru), Spanish (es).
"""

from balethon.objects import InlineKeyboard, InlineKeyboardButton

def InlineKeyboardMarkup(rows):
    return InlineKeyboard(*rows)

MENU_STRINGS = {
    "main_menu_welcome": {
        "fa": "👋 سلام **{name}** عزیز! به ربات همه‌کاره **TMulti** خوش آمدید.\n\n"
              "⚡️ شما می‌توانید هر لینکی (اینستاگرام، تیک‌تاک، یوتیوب، اسپاتیفای، ساندکلاد، ردیت، پینترست و...) "
              "را مستقیماً ارسال کنید تا بلافاصله دانلود شود.\n\n"
              "🎛 یا از منوی زیر ابزار یا پلتفرم مورد نظر خود را انتخاب کنید:",
        "en": "👋 Hello **{name}**! Welcome to **TMulti** Bot.\n\n"
              "⚡️ Send any link directly (Instagram, TikTok, YouTube, Spotify, SoundCloud, Reddit, Pinterest...) "
              "and I will download it immediately.\n\n"
              "🎛 Or choose one of the options below to explore features:",
        "ru": "👋 Привет, **{name}**! Добро пожаловать в бота **TMulti**.\n\n"
              "⚡️ Отправьте любую ссылку (Instagram, TikTok, YouTube, Spotify, SoundCloud, Reddit, Pinterest...), "
              "и я мгновенно скачаю её.\n\n"
              "🎛 Или выберите категорию ниже:",
        "es": "👋 ¡Hola **{name}**! Bienvenido al bot **TMulti**.\n\n"
              "⚡️ Envía cualquier enlace (Instagram, TikTok, YouTube, Spotify, SoundCloud, Reddit, Pinterest...) "
              "y lo descargaré de inmediato.\n\n"
              "🎛 O elige una de las siguientes opciones:"
    },
    "btn_youtube": {"fa": "▶️ یوتیوب", "en": "▶️ YouTube", "ru": "▶️ YouTube", "es": "▶️ YouTube"},
    "btn_ig_tiktok": {"fa": "📸 اینستاگرام / تیک‌تاک", "en": "📸 Instagram / TikTok", "ru": "📸 Instagram / TikTok", "es": "📸 Instagram / TikTok"},
    "btn_reddit_twitter": {"fa": "🐦 ردیت / توییتر (X)", "en": "🐦 Reddit / Twitter (X)", "ru": "🐦 Reddit / Twitter (X)", "es": "🐦 Reddit / Twitter (X)"},
    "btn_video_tools": {"fa": "🎬 ابزارهای ویدیو", "en": "🎬 Video Tools", "ru": "🎬 Видео инструменты", "es": "🎬 Herramientas de Video"},
    "btn_spotify": {"fa": "🟢 اسپاتیفای", "en": "🟢 Spotify", "ru": "🟢 Spotify", "es": "🟢 Spotify"},
    "btn_soundcloud": {"fa": "☁️ ساندکلاد", "en": "☁️ SoundCloud", "ru": "☁️ SoundCloud", "es": "☁️ SoundCloud"},
    "btn_yt_music": {"fa": "🔴 یوتیوب موزیک", "en": "🔴 YouTube Music", "ru": "🔴 YouTube Music", "es": "🔴 YouTube Music"},
    "btn_music_tools": {"fa": "🎵 ابزارهای صدا و موزیک", "en": "🎵 Music Tools", "ru": "🎵 Музыкальные инструменты", "es": "🎵 Herramientas de Música"},
    "btn_pinterest": {"fa": "📌 پینترست", "en": "📌 Pinterest", "ru": "📌 Pinterest", "es": "📌 Pinterest"},
    "btn_pexels": {"fa": "🖼 پکسلز (Pexels)", "en": "🖼 Pexels", "ru": "🖼 Pexels", "es": "🖼 Pexels"},
    "btn_google_images": {"fa": "🔍 تصاویر گوگل", "en": "🔍 Google Images", "ru": "🔍 Google Картинки", "es": "🔍 Google Imágenes"},
    "btn_image_tools": {"fa": "🎨 ابزارهای تصویر", "en": "🎨 Image Tools", "ru": "🎨 Инструменты фото", "es": "🎨 Herramientas de Imagen"},
    "btn_tmulti_ai": {"fa": "🤖 دستیار هوش مصنوعی TMulti", "en": "🤖 TMulti AI Assistant", "ru": "🤖 ИИ-ассистент TMulti", "es": "🤖 Asistente de IA TMulti"},
    "btn_message_dev": {"fa": "📩 پیام به سازنده", "en": "📩 Message Developer", "ru": "📩 Сообщение разработчику", "es": "📩 Mensaje al desarrollador"},
    "btn_profile": {"fa": "👤 پروفایل من", "en": "👤 My Profile", "ru": "👤 Мой профиль", "es": "👤 Mi Perfil"},
    "btn_help": {"fa": "❓ راهنمای کامل", "en": "❓ Full Help Guide", "ru": "❓ Полная помощь", "es": "❓ Guía de Ayuda"},
    "btn_back": {"fa": "🔙 بازگشت", "en": "🔙 Back", "ru": "🔙 Назад", "es": "🔙 Volver"},
    "btn_back_main": {"fa": "🔙 منوی اصلی", "en": "🔙 Main Menu", "ru": "🔙 Главное меню", "es": "🔙 Menú Principal"},
}


def get_menu_text(key: str, lang: str = "fa", **kwargs) -> str:
    item = MENU_STRINGS.get(key, {})
    txt = item.get(lang) or item.get("fa") or key
    if "{name}" in txt:
        if kwargs.get("name"):
            txt = txt.replace("{name}", str(kwargs["name"]))
        else:
            txt = txt.replace("**{name}** عزیز", "عزیز").replace("{name}", "کاربر" if lang == "fa" else "there")
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
            InlineKeyboardButton(b("btn_youtube"), callback_data="menu_youtube"),
            InlineKeyboardButton(b("btn_ig_tiktok"), callback_data="menu_ig_tiktok")
        ],
        # Row 2: Reddit / Twitter | Video Tools
        [
            InlineKeyboardButton(b("btn_reddit_twitter"), callback_data="menu_reddit_twitter"),
            InlineKeyboardButton(b("btn_video_tools"), callback_data="menu_vedit")
        ],
        # Row 3: Spotify | SoundCloud
        [
            InlineKeyboardButton(b("btn_spotify"), callback_data="menu_music_spotify"),
            InlineKeyboardButton(b("btn_soundcloud"), callback_data="menu_music_sc")
        ],
        # Row 4: YouTube Music | Music Tools
        [
            InlineKeyboardButton(b("btn_yt_music"), callback_data="menu_music_ytm"),
            InlineKeyboardButton(b("btn_music_tools"), callback_data="menu_music_tools")
        ],
        # Row 5: Pinterest | Pexels
        [
            InlineKeyboardButton(b("btn_pinterest"), callback_data="menu_pinterest"),
            InlineKeyboardButton(b("btn_pexels"), callback_data="menu_pexels")
        ],
        # Row 6: Google Images | Image Tools
        [
            InlineKeyboardButton(b("btn_google_images"), callback_data="menu_google_images"),
            InlineKeyboardButton(b("btn_image_tools"), callback_data="menu_image_tools")
        ],
        # Row 7: TMulti AI (Full Width)
        [
            InlineKeyboardButton(b("btn_tmulti_ai"), callback_data="menu_ai")
        ],
        # Row 8: Message Developer | My Profile
        [
            InlineKeyboardButton(b("btn_message_dev"), callback_data="contact_admin"),
            InlineKeyboardButton(b("btn_profile"), callback_data="menu_profile")
        ],
        # Row 9: Help (Full Width)
        [
            InlineKeyboardButton(b("btn_help"), callback_data="help_home")
        ]
    ]

    if is_admin_fn and is_admin_fn(chat_id):
        rows.append([InlineKeyboardButton("👑 پنل مدیریت (Admin Panel)", callback_data="adm_home")])

    return InlineKeyboardMarkup(rows)


def get_youtube_keyboard(chat_id, get_lang_fn=None) -> InlineKeyboardMarkup:
    """YouTube submenu per FigJam."""
    lang = get_lang_fn(chat_id) if get_lang_fn else "fa"
    if lang == "fa":
        labels = ["🔍 جستجو در یوتیوب", "🔔 اشتراک در کانال", "🎬 ویرایشگر ویدیو", "🔙 بازگشت"]
    else:
        labels = ["🔍 Search YouTube", "🔔 Channel Subscription", "🎬 Video Editor", "🔙 Back"]

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(labels[0], callback_data="yt_search_menu")],
        [InlineKeyboardButton(labels[1], callback_data="sub_add_yt")],
        [InlineKeyboardButton(labels[2], callback_data="menu_vedit")],
        [InlineKeyboardButton(labels[3], callback_data="menu_main")]
    ])


def get_youtube_search_keyboard(chat_id, get_lang_fn=None) -> InlineKeyboardMarkup:
    """YouTube search options: Keyword or Channels."""
    lang = get_lang_fn(chat_id) if get_lang_fn else "fa"
    if lang == "fa":
        labels = ["🔤 جستجو با کلمه کلیدی", "👤 جستجوی کانال‌ها", "🔙 بازگشت"]
    else:
        labels = ["🔤 Search with Keyword", "👤 Search Channels", "🔙 Back"]

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(labels[0], callback_data="yt_keyword")],
        [InlineKeyboardButton(labels[1], callback_data="yt_channel")],
        [InlineKeyboardButton(labels[2], callback_data="menu_youtube")]
    ])


def get_ig_tiktok_keyboard(chat_id, get_lang_fn=None) -> InlineKeyboardMarkup:
    """Instagram / TikTok submenu per FigJam."""
    lang = get_lang_fn(chat_id) if get_lang_fn else "fa"
    if lang == "fa":
        labels = ["🔔 اشتراک در صفحه / پیج", "🔙 بازگشت"]
    else:
        labels = ["🔔 Page Subscription", "🔙 Back"]

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(labels[0], callback_data="sub_add_ig_tiktok")],
        [InlineKeyboardButton(labels[1], callback_data="menu_main")]
    ])


def get_reddit_twitter_keyboard(chat_id, get_lang_fn=None) -> InlineKeyboardMarkup:
    """Reddit / Twitter submenu per FigJam."""
    lang = get_lang_fn(chat_id) if get_lang_fn else "fa"
    if lang == "fa":
        labels = ["🔔 اشتراک در ساب‌ردیت / کاربر", "🔙 بازگشت"]
    else:
        labels = ["🔔 Subreddit / User Subscription", "🔙 Back"]

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(labels[0], callback_data="sub_add_reddit_twitter")],
        [InlineKeyboardButton(labels[1], callback_data="menu_main")]
    ])


def get_music_hub_keyboard(chat_id, platform="spotify", get_lang_fn=None) -> InlineKeyboardMarkup:
    """Distinct Music Hub keyboards for Spotify, SoundCloud, and YouTube Music."""
    lang = get_lang_fn(chat_id) if get_lang_fn else "fa"
    
    if platform == "sc":
        if lang == "fa":
            return InlineKeyboardMarkup([
                [InlineKeyboardButton("🔍 جستجو در ساندکلاد", switch_inline_query_current_chat="sc: ")],
                [InlineKeyboardButton("⌨️ تایپ نام آهنگ برای جستجو", callback_data="msearch_track_sc")],
                [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu_main")]
            ])
        else:
            return InlineKeyboardMarkup([
                [InlineKeyboardButton("🔍 Search SoundCloud", switch_inline_query_current_chat="sc: ")],
                [InlineKeyboardButton("⌨️ Type Song to Search", callback_data="msearch_track_sc")],
                [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="menu_main")]
            ])

    elif platform == "ytm":
        if lang == "fa":
            return InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🎵 جستجوی آهنگ", switch_inline_query_current_chat="ytm: "),
                    InlineKeyboardButton("📀 جستجوی آلبوم", switch_inline_query_current_chat="ytm_alb: ")
                ],
                [
                    InlineKeyboardButton("👤 جستجوی خواننده", switch_inline_query_current_chat="ytm_art: "),
                    InlineKeyboardButton("📁 جستجوی پلی‌لیست", switch_inline_query_current_chat="ytm_pl: ")
                ],
                [
                    InlineKeyboardButton("⌨️ تایپ نام آهنگ برای جستجو", callback_data="msearch_track_ytm")
                ],
                [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu_main")]
            ])
        else:
            return InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🎵 Search Tracks", switch_inline_query_current_chat="ytm: "),
                    InlineKeyboardButton("📀 Search Albums", switch_inline_query_current_chat="ytm_alb: ")
                ],
                [
                    InlineKeyboardButton("👤 Search Artists", switch_inline_query_current_chat="ytm_art: "),
                    InlineKeyboardButton("📁 Search Playlists", switch_inline_query_current_chat="ytm_pl: ")
                ],
                [
                    InlineKeyboardButton("⌨️ Type Song to Search", callback_data="msearch_track_ytm")
                ],
                [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="menu_main")]
            ])

    else:  # Spotify
        if lang == "fa":
            return InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🎵 جستجوی آهنگ", switch_inline_query_current_chat="sp: "),
                    InlineKeyboardButton("📀 جستجوی آلبوم", switch_inline_query_current_chat="alb: ")
                ],
                [
                    InlineKeyboardButton("👤 جستجوی خواننده", switch_inline_query_current_chat="art: "),
                    InlineKeyboardButton("📁 جستجوی پلی‌لیست", switch_inline_query_current_chat="pl: ")
                ],
                [
                    InlineKeyboardButton("⌨️ جستجوی متنی", callback_data="msearch_track_spotify"),
                    InlineKeyboardButton("🔥 پرطرفدارترین‌های هفته", switch_inline_query_current_chat="top:week")
                ],
                [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu_main")]
            ])
        else:
            return InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🎵 Search Tracks", switch_inline_query_current_chat="sp: "),
                    InlineKeyboardButton("📀 Search Albums", switch_inline_query_current_chat="alb: ")
                ],
                [
                    InlineKeyboardButton("👤 Search Artists", switch_inline_query_current_chat="art: "),
                    InlineKeyboardButton("📁 Search Playlists", switch_inline_query_current_chat="pl: ")
                ],
                [
                    InlineKeyboardButton("⌨️ Text Search", callback_data="msearch_track_spotify"),
                    InlineKeyboardButton("🔥 Top This Week", switch_inline_query_current_chat="top:week")
                ],
                [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="menu_main")]
            ])


def get_rega_search_selector(query: str, chat_id=None, get_lang_fn=None) -> InlineKeyboardMarkup:
    """Multi-platform search selector card when a user types text in private chat."""
    lang = get_lang_fn(chat_id) if get_lang_fn else "fa"
    if lang == "fa":
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🟢 اسپاتیفای", switch_inline_query_current_chat=f"sp: {query}"),
                InlineKeyboardButton("☁️ ساندکلاد", switch_inline_query_current_chat=f"sc: {query}")
            ],
            [
                InlineKeyboardButton("🔴 یوتیوب موزیک", switch_inline_query_current_chat=f"ytm: {query}"),
                InlineKeyboardButton("📀 جستجوی آلبوم", switch_inline_query_current_chat=f"alb: {query}")
            ],
            [
                InlineKeyboardButton("👤 جستجوی خواننده", switch_inline_query_current_chat=f"art: {query}"),
                InlineKeyboardButton("📁 جستجوی پلی‌لیست", switch_inline_query_current_chat=f"pl: {query}")
            ],
            [
                InlineKeyboardButton("🎙 شناسایی با صدا (Shazam)", callback_data="menu_audd"),
                InlineKeyboardButton("🔥 پرطرفدارترین‌های هفته", switch_inline_query_current_chat="top:week")
            ]
        ])
    else:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🟢 Spotify", switch_inline_query_current_chat=f"sp: {query}"),
                InlineKeyboardButton("☁️ SoundCloud", switch_inline_query_current_chat=f"sc: {query}")
            ],
            [
                InlineKeyboardButton("🔴 YouTube Music", switch_inline_query_current_chat=f"ytm: {query}"),
                InlineKeyboardButton("📀 Albums", switch_inline_query_current_chat=f"alb: {query}")
            ],
            [
                InlineKeyboardButton("👤 Artists", switch_inline_query_current_chat=f"art: {query}"),
                InlineKeyboardButton("📁 Playlists", switch_inline_query_current_chat=f"pl: {query}")
            ],
            [
                InlineKeyboardButton("🎙 Shazam Voice", callback_data="menu_audd"),
                InlineKeyboardButton("🔥 Top This Week", switch_inline_query_current_chat="top:week")
            ]
        ])


def get_image_platform_keyboard(chat_id, platform="google", get_lang_fn=None) -> InlineKeyboardMarkup:
    """Image platforms submenu per FigJam."""
    lang = get_lang_fn(chat_id) if get_lang_fn else "fa"
    if lang == "fa":
        labels = ["🔍 جستجو با کلمه کلیدی", "📜 تاریخچه دانلود تصاویر", "🔙 بازگشت"]
    else:
        labels = ["🔍 Search with Keyword", "📜 Image Download History", "🔙 Back"]

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(labels[0], callback_data=f"img_search_{platform}")],
        [InlineKeyboardButton(labels[1], callback_data=f"img_history_{platform}")],
        [InlineKeyboardButton(labels[2], callback_data="menu_main")]
    ])


def get_music_tools_keyboard(chat_id, get_lang_fn=None) -> InlineKeyboardMarkup:
    """Section 2: Music Tools Hub."""
    lang = get_lang_fn(chat_id) if get_lang_fn else "fa"
    if lang == "fa":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🌙 اسلو + ریورب", callback_data="mtool_slow"),
             InlineKeyboardButton("⚡ نایتکور", callback_data="mtool_night")],
            [InlineKeyboardButton("🎧 هشت‌بعدی (8D)", callback_data="mtool_eight"),
             InlineKeyboardButton("🔊 بیس بوست", callback_data="mtool_bass")],
            [InlineKeyboardButton("🎙 کارائوکه (حذف خواننده)", callback_data="mtool_vocal"),
             InlineKeyboardButton("⏩ تغییر سرعت و زیروبم", callback_data="mtool_speed")],
            [InlineKeyboardButton("✂️ برش موزیک (رینگتون)", callback_data="mtool_trim"),
             InlineKeyboardButton("🔄 تبدیل فرمت (320k / FLAC)", callback_data="mtool_convert")],
            [InlineKeyboardButton("🏷 ویرایش تگ و کاور", callback_data="mtool_tags")],
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu_main")]
        ])
    else:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🌙 Slowed + Reverb", callback_data="mtool_slow"),
             InlineKeyboardButton("⚡ Nightcore", callback_data="mtool_night")],
            [InlineKeyboardButton("🎧 8D Audio", callback_data="mtool_eight"),
             InlineKeyboardButton("🔊 Bass Boost", callback_data="mtool_bass")],
            [InlineKeyboardButton("🎙 Vocal Remover / Karaoke", callback_data="mtool_vocal"),
             InlineKeyboardButton("⏩ Speed & Pitch", callback_data="mtool_speed")],
            [InlineKeyboardButton("✂️ Trim / Ringtone", callback_data="mtool_trim"),
             InlineKeyboardButton("🔄 Convert (320k / FLAC)", callback_data="mtool_convert")],
            [InlineKeyboardButton("🏷 Tag & Cover Editor", callback_data="mtool_tags")],
            [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="menu_main")]
        ])


def get_image_tools_keyboard(chat_id, get_lang_fn=None) -> InlineKeyboardMarkup:
    """Section 3: Image Tools Hub."""
    lang = get_lang_fn(chat_id) if get_lang_fn else "fa"
    if lang == "fa":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🪄 حذف پس‌زمینه (شفاف)", callback_data="itool_rembg"),
             InlineKeyboardButton("🗜 فشرده‌سازی هوشمند", callback_data="itool_compress")],
            [InlineKeyboardButton("🔄 تبدیل فرمت (WebP/PNG/JPG/PDF)", callback_data="itool_convert"),
             InlineKeyboardButton("📐 تغییر اندازه و برش", callback_data="itool_crop")],
            [InlineKeyboardButton("🎨 فیلترهای اختصاصی و شارپ", callback_data="itool_filter"),
             InlineKeyboardButton("🏷 درج واترمارک و متن", callback_data="itool_watermark")],
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu_main")]
        ])
    else:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🪄 Remove Background", callback_data="itool_rembg"),
             InlineKeyboardButton("🗜 Smart Compress", callback_data="itool_compress")],
            [InlineKeyboardButton("🔄 Convert (WebP/PNG/JPG/PDF)", callback_data="itool_convert"),
             InlineKeyboardButton("📐 Crop & Resize", callback_data="itool_crop")],
            [InlineKeyboardButton("🎨 Filters & Enhance", callback_data="itool_filter"),
             InlineKeyboardButton("🏷 Watermark & Text", callback_data="itool_watermark")],
            [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="menu_main")]
        ])


def get_tools_picker_keyboard(chat_id, get_lang_fn=None) -> InlineKeyboardMarkup:
    """Picker shown when /tool is invoked without a reply."""
    lang = get_lang_fn(chat_id) if get_lang_fn else "fa"
    if lang == "fa":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🎬 ابزارهای ویدیو (Video Tools)", callback_data="menu_vedit")],
            [InlineKeyboardButton("🎵 ابزارهای صدا و موزیک (Music Tools)", callback_data="menu_music_tools")],
            [InlineKeyboardButton("🖼 ابزارهای تصویر (Image Tools)", callback_data="menu_image_tools")],
            [InlineKeyboardButton("🔙 منوی اصلی", callback_data="menu_main")]
        ])
    else:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🎬 Video Tools", callback_data="menu_vedit")],
            [InlineKeyboardButton("🎵 Music Tools", callback_data="menu_music_tools")],
            [InlineKeyboardButton("🖼 Image Tools", callback_data="menu_image_tools")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="menu_main")]
        ])
