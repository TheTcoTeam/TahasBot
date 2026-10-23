"""Help pages for the Bale build of بات طاها.

Rewritten for Bale: no inline mode (search runs inside the chat), Bale's
upload limit, admin approval, the music cache channel. Persian is the main
language; English is the fallback for every other language.
"""

HOME = {
    "fa": ("❓ **راهنمای بات طاها**\n\n"
           "بات طاها یک جعبه‌ابزار کامل برای دانلود، موزیک، ویرایش عکس و ویدیو و هوش مصنوعی در بله است.\n\n"
           "⚡️ **ساده‌ترین راه:** هر لینکی (اینستاگرام، تیک‌تاک، یوتیوب، اسپاتیفای، ساندکلاد، پینترست، توییتر و...) "
           "یا اسم یک آهنگ را مستقیم در چت بفرستید.\n\n"
           "برای توضیح کامل هر بخش، روی دکمه‌اش بزنید 👇"),
    "en": ("❓ **Taha Bot Guide**\n\n"
           "Taha Bot is a full toolbox for downloads, music, photo & video editing and AI on Bale.\n\n"
           "⚡️ **Easiest way:** just send any link (Instagram, TikTok, YouTube, Spotify, SoundCloud, Pinterest, X...) "
           "or a song name straight into the chat.\n\n"
           "Tap a section below for details 👇"),
}

PAGES = {
    "dl": {
        "label": {"fa": "📥 دانلودر", "en": "📥 Downloader"},
        "body": {
            "fa": ("📥 **دانلودر**\n\nلینک را مستقیم بفرستید؛ ربات خودش نوعش را تشخیص می‌دهد:\n\n"
                   "🎬 **یوتیوب** — انتخاب کیفیت (1080p تا 240p)، ویدیو یا فقط صدا، کاور، توضیحات، کامنت‌ها و برش بازه دلخواه\n"
                   "📸 **اینستاگرام** — پست، ریلز و آلبوم چندتایی (بدون نیاز به لاگین)\n"
                   "📱 **تیک‌تاک** — ویدیو بدون واترمارک و اسلایدشو عکس (لینک کوتاه vm.tiktok هم پشتیبانی می‌شود)\n"
                   "🐦 **توییتر / X**، 📌 **پینترست**، 🔴 **ردیت**\n"
                   "🌐 **سایر سایت‌ها** — هر لینکی که قابل دانلود باشد (فیسبوک، ویمئو و...)\n\n"
                   "📦 **محدودیت حجم بله:** ویدیوهای بزرگ‌تر از حد مجاز به چند بخش تقسیم یا کم‌حجم ارسال می‌شوند.\n"
                   "⏳ دانلودهای سنگین یوتیوب روی سرور پردازش جداگانه انجام می‌شوند و چند دقیقه طول می‌کشند.\n\n"
                   "_دستور سریع:_ `/tahayoutube کلیدواژه` • `/tahatiktok لینک`"),
            "en": ("📥 **Downloader**\n\nJust send a link; the bot detects what it is:\n\n"
                   "🎬 **YouTube** — quality picker (1080p–240p), video or audio only, cover, description, comments and trimming\n"
                   "📸 **Instagram** — posts, reels and carousels (no login needed)\n"
                   "📱 **TikTok** — no-watermark videos and photo slideshows (short vm.tiktok links work too)\n"
                   "🐦 **X / Twitter**, 📌 **Pinterest**, 🔴 **Reddit**\n"
                   "🌐 **Other sites** — anything downloadable (Facebook, Vimeo, ...)\n\n"
                   "📦 **Bale size limit:** videos above the limit are split into parts or compressed.\n"
                   "⏳ Heavy YouTube jobs run on a separate processing server and take a few minutes.\n\n"
                   "_Quick commands:_ `/tahayoutube keyword` • `/tahatiktok link`"),
        },
    },
    "music": {
        "label": {"fa": "🎵 موزیک", "en": "🎵 Music"},
        "body": {
            "fa": ("🎵 **موزیک**\n\nاسم آهنگ را بفرستید و منبع جستجو را انتخاب کنید:\n\n"
                   "🟢 **اسپاتیفای** — دقیق‌ترین نام آهنگ و خواننده، نسخه اورجینال\n"
                   "🔴 **یوتیوب موزیک** • ☁️ **ساندکلاد**\n"
                   "📀 جستجوی **آلبوم**، 👤 **خواننده** و 📁 **پلی‌لیست** • 🔥 پرطرفدارهای هفته\n\n"
                   "روی هر نتیجه بزنید: پیام «⏳ در حال دانلود» نمایش داده می‌شود و آهنگ با کاور، تگ کامل و منوی ابزارها ارسال می‌شود.\n\n"
                   "📀 **آلبوم‌ها و پلی‌لیست‌ها** اول کامل آماده می‌شوند و بعد یکجا ارسال می‌شوند، همراه با یک پیام برای انتخاب هر آهنگ و ابزارهایش.\n"
                   "🔗 لینک اسپاتیفای، یوتیوب موزیک، ساندکلاد و دیزر هم مستقیم پشتیبانی می‌شود.\n"
                   "⚡️ آهنگ‌هایی که قبلاً دانلود شده‌اند فوری ارسال می‌شوند.\n\n"
                   "_دستور سریع:_ `/tahamusic` • `/tahaspotify` • `/tahasoundcloud` • `/tahalyrics`"),
            "en": ("🎵 **Music**\n\nSend a song name and pick a source:\n\n"
                   "🟢 **Spotify** — most accurate title/artist, original version\n"
                   "🔴 **YouTube Music** • ☁️ **SoundCloud**\n"
                   "📀 **Album**, 👤 **artist** and 📁 **playlist** search • 🔥 weekly top songs\n\n"
                   "Tap a result: a “⏳ downloading” message appears, then the song arrives with cover, full tags and its tools menu.\n\n"
                   "📀 **Albums and playlists** are fully prepared first, then sent together with one message to pick each song's tools.\n"
                   "🔗 Spotify, YouTube Music, SoundCloud and Deezer links work directly.\n"
                   "⚡️ Songs downloaded before are sent instantly.\n\n"
                   "_Quick commands:_ `/tahamusic` • `/tahaspotify` • `/tahasoundcloud` • `/tahalyrics`"),
        },
    },
    "track": {
        "label": {"fa": "🎛 ابزار آهنگ", "en": "🎛 Track tools"},
        "body": {
            "fa": ("🎛 **منوی هر آهنگ**\n\n🎤 متن آهنگ • 📄 فایل LRC\n🎛 افکت‌ها: اسلو+ریورب، نایتکور، هشت‌بعدی، بیس بوست و...\n"
                   "🎙 کارائوکه (جداسازی صدای خواننده با هوش مصنوعی)\n✂️ برش • ⏩ تغییر سرعت • 🏷 ویرایش تگ‌ها\n"
                   "📀 آلبوم کامل • ➕ افزودن به پلی‌لیست\n\n"
                   "🎧 **فایل صوتی خودتان:** یک آهنگ یا ویس بفرستید تا شناسایی شود (Shazam)؛ با `/tool` روی آن ریپلای کنید تا افکت، "
                   "تغییر سرعت و تبدیل فرمت در دسترس باشد."),
            "en": ("🎛 **Each song's menu**\n\n🎤 Lyrics • 📄 LRC file\n🎛 Effects: slowed+reverb, nightcore, 8D, bass boost...\n"
                   "🎙 Karaoke (AI vocal removal)\n✂️ Trim • ⏩ Speed • 🏷 Edit tags\n📀 Full album • ➕ Add to playlist\n\n"
                   "🎧 **Your own audio:** send a song or voice note to identify it (Shazam); reply `/tool` to it for effects, "
                   "speed and format conversion."),
        },
    },
    "photo": {
        "label": {"fa": "🖼 تصویر", "en": "🖼 Images"},
        "body": {
            "fa": ("🖼 **جستجو و ابزار تصویر**\n\n🔎 جستجوی عکس در **Pexels** و **گوگل/ویکی‌مدیا**؛ زیر هر عکس دکمه «⬇️ کیفیت اصلی» هست.\n"
                   "📌 پینترست: پین، بورد، پروفایل و جستجو.\n\n"
                   "🎨 **ابزار تصویر** (برای هر عکسی که می‌فرستید یا ربات برایتان دانلود کرده، از دکمه «🎨 ابزارهای تصویر»):\n"
                   "🪄 حذف پس‌زمینه • 🗜 فشرده‌سازی • 🔄 تبدیل فرمت (PNG/JPG/WebP/PDF)\n📐 برش با نسبت دلخواه • 🎨 فیلترها • 🏷 واترمارک متنی\n\n"
                   "_دستور سریع:_ `/tahaphoto کلیدواژه` • `/tahapinterest کلیدواژه`"),
            "en": ("🖼 **Image search & tools**\n\n🔎 Photo search on **Pexels** and **Google/Wikimedia**; each photo has a “⬇️ Full quality” button.\n"
                   "📌 Pinterest: pins, boards, profiles and search.\n\n"
                   "🎨 **Image tools** (for any photo you send or the bot downloads, via “🎨 Image tools”):\n"
                   "🪄 Background removal • 🗜 Compress • 🔄 Convert (PNG/JPG/WebP/PDF)\n📐 Crop to ratio • 🎨 Filters • 🏷 Text watermark\n\n"
                   "_Quick commands:_ `/tahaphoto keyword` • `/tahapinterest keyword`"),
        },
    },
    "vedit": {
        "label": {"fa": "🎬 ویرایش ویدیو", "en": "🎬 Video editor"},
        "body": {
            "fa": ("🎬 **ویرایشگر ویدیو**\n\nیک ویدیو (تا ۲۰ مگابایت؛ محدودیت دریافت فایل در بله) بفرستید:\n\n"
                   "✂️ برش • 📝 واترمارک متنی • 🔲📱🖥 برش مربعی/استوری/عریض\n🔄 چرخش • ↔️↕️ آینه • 🐢⏩ سرعت ۰.۵x تا ۲x\n"
                   "🔇 حذف صدا • 🎵 استخراج صدا • 🗜 فشرده‌سازی • ⏮ معکوس • 🖼 ساخت GIF • 🎧 شناسایی آهنگ\n\n"
                   "📝 **زیرنویس هوشمند:** زیر ویدیوهای یوتیوب، زیرنویس فارسی خودکار (ترجمه و چسباندن روی ویدیو).\n"
                   "پردازش روی سرور جداگانه انجام می‌شود و نتیجه چند دقیقه بعد همین‌جا می‌آید.\n\n_دستور سریع:_ `/tahaedit`"),
            "en": ("🎬 **Video editor**\n\nSend a video (up to 20 MB, Bale's download limit):\n\n"
                   "✂️ Trim • 📝 Text watermark • 🔲📱🖥 Square/story/wide crop\n🔄 Rotate • ↔️↕️ Mirror • 🐢⏩ Speed 0.5x–2x\n"
                   "🔇 Mute • 🎵 Extract audio • 🗜 Compress • ⏮ Reverse • 🖼 GIF • 🎧 Identify song\n\n"
                   "📝 **AI subtitles:** automatic Persian subtitles burned into YouTube videos.\n"
                   "Processing runs on a separate server; the result arrives here a few minutes later.\n\n_Quick command:_ `/tahaedit`"),
        },
    },
    "ai": {
        "label": {"fa": "🤖 هوش مصنوعی", "en": "🤖 AI"},
        "body": {
            "fa": ("🤖 **دستیار هوش مصنوعی**\n\nبا `/tahaai` یا دکمه هوش مصنوعی وارد شوید و مثل یک دوست حرف بزنید:\n\n"
                   "🎵 پیشنهاد آهنگ بر اساس حال‌وهوا («آهنگ برای باشگاه») و دانلود همه با یک لمس\n"
                   "📺 جستجوی ویدیو در یوتیوب • 🎤 پیدا کردن متن آهنگ • 📊 سؤال درباره دانلودهای خودتان\n"
                   "📻 حالت رادیو: پخش پشت‌سرهم آهنگ‌های مشابه"),
            "en": ("🤖 **AI assistant**\n\nOpen it with `/tahaai` or the AI button and just talk:\n\n"
                   "🎵 Mood-based song suggestions (“gym music”), all downloadable in one tap\n"
                   "📺 YouTube video search • 🎤 Lyrics • 📊 Questions about your own downloads\n📻 Radio mode: similar songs back to back"),
        },
    },
    "lib": {
        "label": {"fa": "📚 کتابخانه و پلی‌لیست", "en": "📚 Library & playlists"},
        "body": {
            "fa": ("📚 **کتابخانه، پلی‌لیست و پروفایل**\n\n📚 کتابخانه: همه آهنگ‌هایی که دانلود کرده‌اید، با مرتب‌سازی و ارسال فوری\n"
                   "📁 پلی‌لیست‌های شخصی (حداکثر {max_playlists} پلی‌لیست، هر کدام {max_tracks} آهنگ): پخش همه، پخش تصادفی، پیشنهاد آهنگ مشابه با هوش مصنوعی\n"
                   "📊 آمار شخصی و پروفایل شما\n\n_دستور سریع:_ `/tahalibrary` • `/tahastats`"),
            "en": ("📚 **Library, playlists & profile**\n\n📚 Library: every song you've downloaded, sortable and instantly resendable\n"
                   "📁 Personal playlists (up to {max_playlists}, {max_tracks} songs each): play all, shuffle, AI similar-song suggestions\n"
                   "📊 Your stats and profile\n\n_Quick commands:_ `/tahalibrary` • `/tahastats`"),
        },
    },
    "subs": {
        "label": {"fa": "🔔 اشتراک‌ها", "en": "🔔 Subscriptions"},
        "body": {
            "fa": ("🔔 **اشتراک‌ها**\n\nصفحه اینستاگرام، تیک‌تاک یا کانال یوتیوب را دنبال کنید تا پست‌های جدیدش خودکار برایتان ارسال شود "
                   "(حداکثر {max_subs} اشتراک). هر هفته هم یک میکس‌تیپ شخصی از سلیقه‌تان دریافت می‌کنید."),
            "en": ("🔔 **Subscriptions**\n\nFollow an Instagram page, TikTok account or YouTube channel and get new posts automatically "
                   "(up to {max_subs}). You also get a weekly personal mixtape."),
        },
    },
    "access": {
        "label": {"fa": "🔐 دسترسی و پشتیبانی", "en": "🔐 Access & support"},
        "body": {
            "fa": ("🔐 **دسترسی و پشتیبانی**\n\nاستفاده از بات طاها با تأیید مدیر فعال می‌شود. آیدی عددی خود را با `/id` ببینید و برای "
                   "@Tahathegoat بفرستید.\n\n✉️ برای گزارش مشکل یا پیشنهاد، از دکمه «تماس با پشتیبانی» در منوی اصلی استفاده کنید.\n"
                   "🤖 هر فایلی که ربات می‌فرستد امضای @tahasbot دارد."),
            "en": ("🔐 **Access & support**\n\nTaha Bot is enabled per user by the admin. See your numeric id with `/id` and send it to "
                   "@Tahathegoat.\n\n✉️ For problems or ideas, use “Contact support” in the main menu.\n"
                   "🤖 Every file the bot sends carries the @tahasbot signature."),
        },
    },
    "cmds": {
        "label": {"fa": "⌨️ دستورات", "en": "⌨️ Commands"},
        "body": {
            "fa": ("⌨️ **دستورات**\n\n/start — منوی اصلی\n/tahamusic — جستجوی موزیک\n/tahaspotify — اسپاتیفای\n/tahasoundcloud — ساندکلاد\n"
                   "/tahalyrics — پیدا کردن آهنگ از روی متن\n/tahayoutube — جستجوی یوتیوب\n/tahatiktok — تیک‌تاک\n/tahapinterest — پینترست\n"
                   "/tahareddit — ردیت\n/tahaphoto — جستجوی تصویر\n/tahaedit — ویرایشگر ویدیو\n/tahashazam — شناسایی آهنگ\n/tool — ابزار روی فایل ریپلای‌شده\n"
                   "/tahalibrary — کتابخانه\n/tahastats — آمار\n/tahaai — هوش مصنوعی\n/id — آیدی عددی شما\n/tahahelp — همین راهنما"),
            "en": ("⌨️ **Commands**\n\n/start — main menu\n/tahamusic — music search\n/tahaspotify — Spotify\n/tahasoundcloud — SoundCloud\n"
                   "/tahalyrics — find a song by lyrics\n/tahayoutube — YouTube search\n/tahatiktok — TikTok\n/tahapinterest — Pinterest\n"
                   "/tahareddit — Reddit\n/tahaphoto — image search\n/tahaedit — video editor\n/tahashazam — identify a song\n/tool — tools for a replied file\n"
                   "/tahalibrary — library\n/tahastats — stats\n/tahaai — AI\n/id — your numeric id\n/tahahelp — this guide"),
        },
    },
}
