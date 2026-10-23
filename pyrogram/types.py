"""Pyrogram-compatible object model built from Bale Bot API JSON.

Only what the bot uses is implemented, but names, attributes and bound
methods match Pyrogram 2.x so bot.py and modules/ run unchanged.
"""
from datetime import datetime

from .enums import ChatType, ChatMemberStatus, ParseMode


class Object:
    def __repr__(self):
        fields = ", ".join(f"{k}={v!r}" for k, v in self.__dict__.items()
                           if not k.startswith("_") and v is not None)
        return f"{type(self).__name__}({fields})"

    def __getitem__(self, item):
        return getattr(self, item)


# ------------------------------------------------------------------ keyboards
class InlineKeyboardButton(Object):
    def __init__(self, text: str, callback_data=None, url: str = None,
                 switch_inline_query: str = None, switch_inline_query_current_chat: str = None,
                 web_app=None, user_id=None, callback_game=None, login_url=None, copy_text=None, **kw):
        self.text = str(text)
        if isinstance(callback_data, bytes):
            callback_data = callback_data.decode("utf-8", "ignore")
        self.callback_data = callback_data
        self.url = url
        self.switch_inline_query = switch_inline_query
        self.switch_inline_query_current_chat = switch_inline_query_current_chat
        self.web_app = web_app
        self.copy_text = copy_text

    @staticmethod
    def _parse(d: dict):
        return InlineKeyboardButton(d.get("text", ""), callback_data=d.get("callback_data"), url=d.get("url"))


class InlineKeyboardMarkup(Object):
    def __init__(self, inline_keyboard):
        self.inline_keyboard = [list(r) for r in (inline_keyboard or [])]

    @staticmethod
    def _parse(d):
        if not d or "inline_keyboard" not in d:
            return None
        return InlineKeyboardMarkup([[InlineKeyboardButton._parse(b) for b in row] for row in d["inline_keyboard"]])


class KeyboardButton(Object):
    def __init__(self, text: str, request_contact: bool = None, request_location: bool = None, **kw):
        self.text = text
        self.request_contact = request_contact
        self.request_location = request_location


class ReplyKeyboardMarkup(Object):
    def __init__(self, keyboard, resize_keyboard: bool = None, one_time_keyboard: bool = None,
                 selective: bool = None, placeholder: str = None, is_persistent: bool = None, **kw):
        self.keyboard = keyboard
        self.resize_keyboard = resize_keyboard
        self.one_time_keyboard = one_time_keyboard


class ReplyKeyboardRemove(Object):
    def __init__(self, selective: bool = None):
        self.selective = selective


class ForceReply(Object):
    def __init__(self, selective: bool = None, placeholder: str = None):
        self.selective = selective


class BotCommand(Object):
    def __init__(self, command: str, description: str):
        self.command = command
        self.description = description


# ------------------------------------------------------------------ input media
class InputMedia(Object):
    TYPE = "document"

    def __init__(self, media, caption: str = "", parse_mode=None, thumb=None, **kw):
        self.media = media
        self.caption = caption
        self.parse_mode = parse_mode
        self.thumb = thumb
        for k, v in kw.items():
            setattr(self, k, v)


class InputMediaPhoto(InputMedia):
    TYPE = "photo"


class InputMediaVideo(InputMedia):
    TYPE = "video"


class InputMediaAudio(InputMedia):
    TYPE = "audio"


class InputMediaDocument(InputMedia):
    TYPE = "document"


class InputMediaAnimation(InputMedia):
    TYPE = "animation"


# ------------------------------------------------------------------ inline results
class InputTextMessageContent(Object):
    def __init__(self, message_text: str, parse_mode=None, disable_web_page_preview=None, **kw):
        self.message_text = message_text
        self.parse_mode = parse_mode


class InlineQueryResult(Object):
    KIND = "article"

    def __init__(self, id: str = None, title: str = None, description: str = None,
                 reply_markup=None, input_message_content=None, caption: str = None,
                 parse_mode=None, thumb_url: str = None, **kw):
        self.id = str(id) if id is not None else None
        self.title = title
        self.description = description
        self.reply_markup = reply_markup
        self.input_message_content = input_message_content
        self.caption = caption
        self.parse_mode = parse_mode
        self.thumb_url = thumb_url
        for k, v in kw.items():
            setattr(self, k, v)


class InlineQueryResultArticle(InlineQueryResult):
    KIND = "article"

    def __init__(self, title: str = None, input_message_content=None, id: str = None, **kw):
        super().__init__(id=id, title=title, input_message_content=input_message_content, **kw)


class InlineQueryResultPhoto(InlineQueryResult):
    KIND = "photo"

    def __init__(self, photo_url: str = None, id: str = None, **kw):
        super().__init__(id=id, **kw)
        self.photo_url = photo_url


class InlineQueryResultCachedPhoto(InlineQueryResult):
    KIND = "cached_photo"

    def __init__(self, photo_file_id: str = None, id: str = None, **kw):
        super().__init__(id=id, **kw)
        self.photo_file_id = photo_file_id


class InlineQueryResultAudio(InlineQueryResult):
    KIND = "audio"

    def __init__(self, audio_url: str = None, title: str = None, id: str = None, **kw):
        super().__init__(id=id, title=title, **kw)
        self.audio_url = audio_url


class InlineQueryResultCachedAudio(InlineQueryResult):
    KIND = "cached_audio"

    def __init__(self, audio_file_id: str = None, id: str = None, **kw):
        super().__init__(id=id, **kw)
        self.audio_file_id = audio_file_id


class InlineQueryResultVideo(InlineQueryResult):
    KIND = "video"

    def __init__(self, video_url: str = None, id: str = None, **kw):
        super().__init__(id=id, **kw)
        self.video_url = video_url


class InlineQueryResultCachedDocument(InlineQueryResult):
    KIND = "cached_document"

    def __init__(self, document_file_id: str = None, title: str = None, id: str = None, **kw):
        super().__init__(id=id, title=title, **kw)
        self.document_file_id = document_file_id


# ------------------------------------------------------------------ users/chats
class User(Object):
    def __init__(self, id: int = 0, first_name: str = "", last_name: str = None, username: str = None,
                 is_bot: bool = False, language_code: str = None, **kw):
        self.id = int(id or 0)
        self.first_name = first_name or ""
        self.last_name = last_name or None
        self.username = username or None
        self.is_bot = bool(is_bot)
        self.language_code = language_code

    @property
    def mention(self):
        return self.first_name or (f"@{self.username}" if self.username else str(self.id))

    @staticmethod
    def _parse(d):
        if not d:
            return None
        return User(d.get("id"), d.get("first_name", ""), d.get("last_name"), d.get("username"),
                    d.get("is_bot", False), d.get("language_code"))


class Chat(Object):
    def __init__(self, id: int = 0, type=ChatType.PRIVATE, title: str = None, username: str = None,
                 first_name: str = None, last_name: str = None, invite_link: str = None,
                 members_count: int = None, _client=None, **kw):
        self.id = int(id or 0)
        self.type = type
        self.title = title
        self.username = username or None
        self.first_name = first_name
        self.last_name = last_name
        self.invite_link = invite_link
        self.members_count = members_count
        self._client = _client

    @staticmethod
    def _parse(d, client=None):
        if not d:
            return None
        return Chat(d.get("id"), ChatType.parse(d.get("type")), d.get("title"), d.get("username"),
                    d.get("first_name"), d.get("last_name"), d.get("invite_link"),
                    d.get("members_count") or d.get("member_count"), _client=client)

    async def leave(self):
        return await self._client.leave_chat(self.id)


class ChatMember(Object):
    def __init__(self, user=None, status=ChatMemberStatus.LEFT, **kw):
        self.user = user
        self.status = status

    @staticmethod
    def _parse(d):
        if not d:
            return None
        return ChatMember(User._parse(d.get("user")), ChatMemberStatus.parse(d.get("status")))


class ChatMemberUpdated(Object):
    def __init__(self, chat=None, from_user=None, date=None, old_chat_member=None,
                 new_chat_member=None, _client=None, **kw):
        self.chat = chat
        self.from_user = from_user
        self.date = date
        self.old_chat_member = old_chat_member
        self.new_chat_member = new_chat_member
        self._client = _client

    @staticmethod
    def _parse(client, d):
        return ChatMemberUpdated(Chat._parse(d.get("chat"), client), User._parse(d.get("from")),
                                 _ts(d.get("date")), ChatMember._parse(d.get("old_chat_member")),
                                 ChatMember._parse(d.get("new_chat_member")), _client=client)


# ------------------------------------------------------------------ media
class _Media(Object):
    def __init__(self, **d):
        self.file_id = d.get("file_id")
        self.file_unique_id = d.get("file_unique_id")
        self.file_size = d.get("file_size")
        self.file_name = d.get("file_name")
        self.mime_type = d.get("mime_type")
        self.duration = d.get("duration")
        self.width = d.get("width")
        self.height = d.get("height")
        self.title = d.get("title")
        self.performer = d.get("performer")
        thumb = d.get("thumbnail") or d.get("thumb")
        self.thumbs = [Thumbnail(**thumb)] if isinstance(thumb, dict) else None
        self.date = None


class Thumbnail(_Media):
    pass


class Photo(_Media):
    @staticmethod
    def _parse(sizes):
        if not sizes:
            return None
        if isinstance(sizes, dict):
            return Photo(**sizes)
        best = max(sizes, key=lambda s: (s.get("width", 0) * s.get("height", 0), s.get("file_size") or 0))
        p = Photo(**best)
        p.thumbs = [Thumbnail(**s) for s in sizes if s is not best] or None
        return p


class Audio(_Media):
    pass


class Voice(_Media):
    pass


class Video(_Media):
    pass


class Document(_Media):
    pass


class Animation(_Media):
    pass


class Sticker(_Media):
    pass


def _media(cls, d):
    return cls(**d) if isinstance(d, dict) else None


def _ts(v):
    try:
        return datetime.fromtimestamp(int(v))
    except Exception:
        return None


# ------------------------------------------------------------------ message
class Message(Object):
    def __init__(self, id: int = 0, chat=None, from_user=None, text=None, caption=None, _client=None, **kw):
        self.id = int(id or 0)
        self.chat = chat
        self.from_user = from_user
        self.sender_chat = None
        self.date = kw.get("date")
        self.text = text
        self.caption = caption
        self.photo = kw.get("photo")
        self.audio = kw.get("audio")
        self.voice = kw.get("voice")
        self.video = kw.get("video")
        self.document = kw.get("document")
        self.animation = kw.get("animation")
        self.sticker = kw.get("sticker")
        self.video_note = None
        self.reply_to_message = kw.get("reply_to_message")
        self.reply_markup = kw.get("reply_markup")
        self.via_bot = kw.get("via_bot")
        self.media_group_id = kw.get("media_group_id")
        self.forward_from = kw.get("forward_from")
        self.forward_from_chat = kw.get("forward_from_chat")
        self.entities = kw.get("entities")
        self.new_chat_members = kw.get("new_chat_members")
        self.left_chat_member = kw.get("left_chat_member")
        self.inline_message_id = kw.get("inline_message_id")
        self.command = None
        self.matches = None
        self.empty = False
        self.outgoing = bool(kw.get("outgoing", False))
        self._client = _client

    @property
    def message_id(self):
        return self.id

    @property
    def chat_id(self):
        return self.chat.id if self.chat else None

    @property
    def media(self):
        for a in ("photo", "audio", "voice", "video", "document", "animation", "sticker"):
            if getattr(self, a) is not None:
                return a
        return None

    @property
    def link(self):
        return ""

    @staticmethod
    def _parse(client, d: dict):
        if not d:
            return None
        chat = Chat._parse(d.get("chat"), client) or Chat(0)
        return Message(
            d.get("message_id", 0), chat, User._parse(d.get("from")),
            d.get("text"), d.get("caption"), _client=client,
            date=_ts(d.get("date")),
            photo=Photo._parse(d.get("photo")),
            audio=_media(Audio, d.get("audio")),
            voice=_media(Voice, d.get("voice")),
            video=_media(Video, d.get("video")),
            document=_media(Document, d.get("document")),
            animation=_media(Animation, d.get("animation")),
            sticker=_media(Sticker, d.get("sticker")),
            reply_to_message=Message._parse(client, d.get("reply_to_message")) if d.get("reply_to_message") else None,
            reply_markup=InlineKeyboardMarkup._parse(d.get("reply_markup")),
            via_bot=User._parse(d.get("via_bot")),
            media_group_id=d.get("media_group_id"),
            forward_from=User._parse(d.get("forward_from")),
            forward_from_chat=Chat._parse(d.get("forward_from_chat"), client),
            entities=d.get("entities"),
            new_chat_members=[User._parse(u) for u in d.get("new_chat_members") or []] or None,
            left_chat_member=User._parse(d.get("left_chat_member")),
        )

    # --- bound methods (same signatures as pyrogram) ---
    async def reply_text(self, text: str, quote: bool = None, reply_to_message_id: int = None, **kw):
        if quote and reply_to_message_id is None:
            reply_to_message_id = self.id
        return await self._client.send_message(self.chat.id, text, reply_to_message_id=reply_to_message_id, **kw)

    reply = reply_text

    async def reply_photo(self, photo, quote: bool = None, **kw):
        return await self._client.send_photo(self.chat.id, photo, **kw)

    async def reply_audio(self, audio, quote: bool = None, **kw):
        return await self._client.send_audio(self.chat.id, audio, **kw)

    async def reply_video(self, video, quote: bool = None, **kw):
        return await self._client.send_video(self.chat.id, video, **kw)

    async def reply_document(self, document, quote: bool = None, **kw):
        return await self._client.send_document(self.chat.id, document, **kw)

    async def reply_animation(self, animation, quote: bool = None, **kw):
        return await self._client.send_animation(self.chat.id, animation, **kw)

    async def reply_voice(self, voice, quote: bool = None, **kw):
        return await self._client.send_voice(self.chat.id, voice, **kw)

    async def reply_media_group(self, media, quote: bool = None, **kw):
        return await self._client.send_media_group(self.chat.id, media, **kw)

    async def edit_text(self, text: str, **kw):
        if self.inline_message_id:
            return await self._client.edit_inline_text(self.inline_message_id, text, **kw)
        if self.media and not self.text:
            return await self._client.edit_message_caption(self.chat.id, self.id, text, **kw)
        return await self._client.edit_message_text(self.chat.id, self.id, text, **kw)

    edit = edit_text

    async def edit_caption(self, caption: str, **kw):
        if self.inline_message_id:
            return await self._client.edit_inline_caption(self.inline_message_id, caption, **kw)
        return await self._client.edit_message_caption(self.chat.id, self.id, caption, **kw)

    async def edit_reply_markup(self, reply_markup=None):
        return await self._client.edit_message_reply_markup(self.chat.id, self.id, reply_markup)

    async def edit_media(self, media, reply_markup=None, **kw):
        return await self._client.edit_message_media(self.chat.id, self.id, media, reply_markup=reply_markup)

    async def delete(self, revoke: bool = True):
        return await self._client.delete_messages(self.chat.id, self.id)

    async def download(self, file_name: str = "", in_memory: bool = False, **kw):
        return await self._client.download_media(self, file_name=file_name, in_memory=in_memory)

    async def copy(self, chat_id, caption: str = None, reply_markup=None, **kw):
        return await self._client.copy_message(chat_id, self.chat.id, self.id, caption=caption,
                                               reply_markup=reply_markup)

    async def forward(self, chat_id, **kw):
        return await self._client.forward_messages(chat_id, self.chat.id, self.id)

    async def pin(self, **kw):
        return await self._client.pin_chat_message(self.chat.id, self.id)


# ------------------------------------------------------------------ callback / inline
class CallbackQuery(Object):
    def __init__(self, id: str = "", from_user=None, message=None, data=None, chat_instance=None,
                 inline_message_id=None, _client=None, **kw):
        self.id = str(id)
        self.from_user = from_user
        self.message = message
        self.data = data
        self.chat_instance = chat_instance
        self.inline_message_id = inline_message_id
        self.matches = None
        self._client = _client

    @staticmethod
    def _parse(client, d):
        return CallbackQuery(d.get("id"), User._parse(d.get("from")), Message._parse(client, d.get("message")),
                             d.get("data"), d.get("chat_instance"), d.get("inline_message_id"), _client=client)

    async def answer(self, text: str = None, show_alert: bool = None, url: str = None, cache_time: int = 0):
        return await self._client.answer_callback_query(self.id, text=text, show_alert=show_alert,
                                                        url=url, cache_time=cache_time)

    async def edit_message_text(self, text: str, **kw):
        if self.message is None:
            return None
        return await self.message.edit_text(text, **kw)

    async def edit_message_caption(self, caption: str, **kw):
        if self.message is None:
            return None
        return await self.message.edit_caption(caption, **kw)

    async def edit_message_reply_markup(self, reply_markup=None):
        if self.message is None:
            return None
        return await self.message.edit_reply_markup(reply_markup)

    async def edit_message_media(self, media, reply_markup=None):
        if self.message is None:
            return None
        return await self.message.edit_media(media, reply_markup=reply_markup)


class InlineQuery(Object):
    """Emulated: Bale has no inline mode, so the client turns a
    switch-inline button press (or the user's typed follow-up) into one of
    these and renders .answer() results as a message with buttons."""

    def __init__(self, id: str = "", from_user=None, query: str = "", offset: str = "",
                 chat_type=None, _client=None, _chat_id=None, _reply_to=None, **kw):
        self.id = str(id)
        self.from_user = from_user
        self.query = query
        self.offset = offset
        self.chat_type = chat_type
        self.matches = None
        self._client = _client
        self._chat_id = _chat_id
        self._reply_to = _reply_to

    async def answer(self, results, cache_time: int = 300, is_gallery: bool = False, is_personal: bool = False,
                     next_offset: str = "", switch_pm_text: str = "", switch_pm_parameter: str = "", **kw):
        return await self._client._render_inline_answer(self, list(results or []))


class ChosenInlineResult(Object):
    def __init__(self, result_id: str = "", from_user=None, query: str = "", inline_message_id: str = None,
                 location=None, _client=None, **kw):
        self.result_id = result_id
        self.from_user = from_user
        self.query = query
        self.inline_message_id = inline_message_id
        self.location = location
        self._client = _client

    @staticmethod
    def _parse(client, chosen, users=None):
        return chosen
