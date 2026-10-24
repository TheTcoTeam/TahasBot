"""Pyrogram-compatible Client that talks to the Bale Bot API over HTTPS.

bot.py was written for Pyrogram (MTProto). Bale only offers a Telegram-style
HTTP Bot API, so this class keeps Pyrogram's method names, decorators and
handler-group semantics and translates each call to Bale.

Bale differences handled here:
  * no parse_mode: Pyrogram markdown/HTML is rewritten to Bale's markdown
    (*bold*, _italic_, [text](url), ```code```);
  * no inline mode: switch-inline buttons open an in-chat search that renders
    the bot's own inline results as buttons (see _render_inline_answer);
  * no editMessageMedia: media edits send the new media and delete the old;
  * uploads capped at 50 MB (10 MB photos), downloads at 20 MB;
  * callback_data capped at 64 bytes: longer payloads are stored server side;
  * t.me links are rewritten to ble.ir.
"""
import asyncio
import hashlib
import html
import io
import json
import logging
import mimetypes
import os
import random
import re
import signal
import string
import threading
import time
import traceback

import requests
from requests.adapters import HTTPAdapter

from . import errors
from .enums import ChatType, ParseMode
from .types import (CallbackQuery, Chat, ChatMember, ChatMemberUpdated, ChosenInlineResult,
                    InlineKeyboardButton, InlineKeyboardMarkup, InlineQuery, InlineQueryResultArticle,
                    InlineQueryResultCachedAudio, InlineQueryResultCachedPhoto, InlineQueryResultPhoto,
                    InlineQueryResultAudio, InputMedia, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove,
                    ForceReply, KeyboardButton, User)

log = logging.getLogger("bale")

UPLOAD_LIMIT = int(os.environ.get("BALE_UPLOAD_LIMIT", str(50 * 1024 * 1024)))
PHOTO_LIMIT = 10 * 1024 * 1024
MAX_TEXT = 4096
MAX_CAPTION = 1024
INLINE_PENDING_TTL = 600
INLINE_RESULTS_SHOWN = 25


class StopPropagation(Exception):
    pass


class ContinuePropagation(Exception):
    pass


# ---------------------------------------------------------------- text format
_HTML_TAG = re.compile(r"</?(b|strong|i|em|u|ins|s|strike|del|code|pre|a|tg-spoiler|span|blockquote)(\s[^>]*)?>",
                       re.IGNORECASE)


def _html_to_bale(text: str) -> str:
    def a_repl(m):
        href = html.unescape(m.group(1))
        return f"[{m.group(2)}]({href})"
    text = re.sub(r'<a\s+href="([^"]*)"\s*>(.*?)</a>', a_repl, text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"</?(b|strong)>", "*", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(i|em)>", "_", text, flags=re.IGNORECASE)
    text = re.sub(r"<pre[^>]*>(.*?)</pre>", lambda m: f"```{m.group(1)}```", text, flags=re.IGNORECASE | re.DOTALL)
    text = _HTML_TAG.sub("", text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    return html.unescape(text)


def _md_to_bale(text: str) -> str:
    # Pyrogram markdown: **bold** __italic__ ~~strike~~ ||spoiler|| `code` [t](u)
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text, flags=re.DOTALL)
    text = re.sub(r"(?<![\w])__(.+?)__(?![\w])", r"_\1_", text, flags=re.DOTALL)
    text = re.sub(r"~~(.+?)~~", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"\|\|(.+?)\|\|", r"\1", text, flags=re.DOTALL)
    return text


def format_text(text, parse_mode=None) -> str:
    if text is None:
        return ""
    text = str(text)
    mode = parse_mode.value if isinstance(parse_mode, ParseMode) else (str(parse_mode).lower() if parse_mode else "default")
    if mode in ("disabled", "none", "text"):
        return text
    if mode in ("html", "default") and "<" in text:
        text = _html_to_bale(text)
    if mode in ("markdown", "md", "markdownv2", "default"):
        text = _md_to_bale(text)
    return text


def fix_url(url: str) -> str:
    if not url:
        return url
    return re.sub(r"^https?://(www\.)?(t|telegram)\.me/", "https://ble.ir/", str(url))


def _short(n=10):
    return "".join(random.choices(string.ascii_letters + string.digits, k=n))


_PLATFORMS = [
    ("ytmusic", ("music.youtube.com",)),
    ("youtube", ("youtube.com", "youtu.be")),
    ("instagram", ("instagram.com", "instagr.am")),
    ("tiktok", ("tiktok.com",)),
    ("spotify", ("spotify.com", "spotify.link")),
    ("soundcloud", ("soundcloud.com",)),
    ("pinterest", ("pinterest.", "pin.it")),
    ("reddit", ("reddit.com", "redd.it")),
    ("twitter", ("twitter.com", "x.com")),
    ("deezer", ("deezer.com", "deezer.page.link")),
]


def link_platform(text: str):
    m = re.search(r"https?://\S+|(?:www\.)?[\w.-]+\.(?:com|it|be|link)/\S*", text or "", re.I)
    if not m:
        return None
    host = m.group(0).lower()
    for name, hosts in _PLATFORMS:
        if any(h in host for h in hosts):
            return name
    return "other"


def feature_key(data: str) -> str:
    """Stable key for a callback_data shape: ids, page numbers and other
    variable parts are wildcarded so one toggle covers every instance of a
    button (e.g. every 'lyrics_<id>')."""
    if data.startswith("_iq:"):
        rest = data[4:].strip()
        return "inline:" + (rest.split(":")[0].split()[0] if rest else "menu")
    parts = data.split("_")
    out = [parts[0]]
    for part in parts[1:]:
        if part.isdigit():
            out.append("#")
        elif len(part) >= 6 and (any(c.isdigit() for c in part) or (part.lower() != part and part.upper() != part)):
            out.append("*")
        elif len(part) > 24:
            out.append("*")
        else:
            out.append(part)
    return re.sub(r"(_\*)+", "_*", "_".join(out))


class _Handler:
    def __init__(self, callback, filters, group):
        self.callback = callback
        self.filters = filters
        self.group = group


class Client:
    """Subset of pyrogram.Client backed by https://tapi.bale.ai."""

    def __init__(self, name: str = "bot", api_id=None, api_hash=None, bot_token: str = None,
                 workers: int = 32, api_base: str = None, **kwargs):
        self.name = name
        self.bot_token = bot_token or os.environ.get("BOT_TOKEN", "")
        self.api_base = (api_base or os.environ.get("BALE_API_BASE") or "https://tapi.bale.ai").rstrip("/")
        self.workers = max(4, int(workers or 32))
        self.me = None
        self.is_connected = False
        self._handlers = {k: {} for k in ("message", "callback_query", "inline_query",
                                           "chosen_inline_result", "chat_member_updated", "edited_message")}
        self._session = requests.Session()
        adapter = HTTPAdapter(pool_connections=20, pool_maxsize=64)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)
        self._poll_task = None
        self._sem = None
        self._offset = 0
        self._stop = None
        # callback_data longer than 64 bytes -> token
        self._long_cb = {}
        # inline-mode emulation state
        self._inline_pending = {}   # uid -> (prefix, chat_id, ts)
        self._inline_results = {}   # token -> dict
        self._inline_msgs = {}      # pseudo inline_message_id -> (chat_id, message_id)
        self._seen_updates = []
        # set by the bot: upload cap (admin-adjustable), caption signature,
        # and the feature gate (admin panel on/off switches)
        self.upload_limit = UPLOAD_LIMIT
        self.caption_signature = ""
        self.feature_gate = None

    # ------------------------------------------------------------ registration
    def _add(self, kind, func, filters, group):
        self._handlers[kind].setdefault(group, []).append(_Handler(func, filters, group))
        return func

    def on_message(self, filters=None, group: int = 0):
        return lambda f: self._add("message", f, filters, group)

    def on_edited_message(self, filters=None, group: int = 0):
        return lambda f: self._add("edited_message", f, filters, group)

    def on_callback_query(self, filters=None, group: int = 0):
        return lambda f: self._add("callback_query", f, filters, group)

    def on_inline_query(self, filters=None, group: int = 0):
        return lambda f: self._add("inline_query", f, filters, group)

    def on_chosen_inline_result(self, filters=None, group: int = 0):
        return lambda f: self._add("chosen_inline_result", f, filters, group)

    def on_chat_member_updated(self, filters=None, group: int = 0):
        return lambda f: self._add("chat_member_updated", f, filters, group)

    def add_handler(self, handler, group: int = 0):
        kind = getattr(handler, "KIND", "message")
        return self._add(kind, handler.callback, handler.filters, group)

    # ------------------------------------------------------------ HTTP core
    def _url(self, method):
        return f"{self.api_base}/bot{self.bot_token}/{method}"

    def _call_sync(self, method: str, data: dict = None, files: dict = None, timeout: float = 60):
        data = {k: v for k, v in (data or {}).items() if v is not None}
        last_exc = None
        for attempt in range(5):
            try:
                if files:
                    form = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else
                                ("true" if v is True else "false" if v is False else str(v)))
                            for k, v in data.items()}
                    for f in files.values():
                        try:
                            f[1].seek(0)
                        except Exception:
                            pass
                    r = self._session.post(self._url(method), data=form, files=files, timeout=max(timeout, 600))
                else:
                    r = self._session.post(self._url(method), json=data, timeout=timeout)
                try:
                    j = r.json()
                except ValueError:
                    if r.status_code == 413:
                        raise errors.FileTooLarge(None, "Request Entity Too Large", method)
                    raise errors.InternalServerError(None, f"HTTP {r.status_code}: {r.text[:200]}", method)
            except (requests.ConnectionError, requests.Timeout, errors.InternalServerError) as e:
                last_exc = e
                if method == "getUpdates" or attempt == 4:
                    break
                time.sleep(min(8, 0.6 * (2 ** attempt)))
                continue
            if j.get("ok"):
                return j.get("result")
            code = int(j.get("error_code") or r.status_code or 400)
            desc = str(j.get("description") or "")
            params = j.get("parameters") or {}
            if code == 429:
                wait = int(params.get("retry_after") or 3)
                if wait <= 30 and attempt < 4:
                    time.sleep(wait + 0.5)
                    continue
            if code >= 500 and attempt < 4:
                time.sleep(min(8, 0.6 * (2 ** attempt)))
                continue
            raise errors.from_response(method, code, desc, params)
        if isinstance(last_exc, errors.RPCError):
            raise last_exc
        raise errors.InternalServerError(None, f"network error: {last_exc}", method)

    async def invoke(self, method: str, data: dict = None, files: dict = None, timeout: float = 60):
        return await asyncio.to_thread(self._call_sync, method, data, files, timeout)

    # ------------------------------------------------------------ serialization
    def _cb_data(self, data: str) -> str:
        data = str(data)
        if len(data.encode("utf-8")) <= 64:
            return data
        tok = "_cb:" + hashlib.md5(data.encode()).hexdigest()[:16]
        self._long_cb[tok] = data
        return tok

    def _button(self, b):
        if isinstance(b, dict):
            return b
        if isinstance(b, KeyboardButton):
            d = {"text": b.text}
            if b.request_contact:
                d["request_contact"] = True
            if b.request_location:
                d["request_location"] = True
            return d
        if isinstance(b, str):
            return {"text": b}
        d = {"text": b.text}
        if b.url:
            d["url"] = fix_url(b.url)
        elif b.callback_data is not None:
            d["callback_data"] = self._cb_data(b.callback_data)
        elif b.switch_inline_query_current_chat is not None or b.switch_inline_query is not None:
            q = b.switch_inline_query_current_chat if b.switch_inline_query_current_chat is not None else b.switch_inline_query
            d["callback_data"] = self._cb_data("_iq:" + q)
        elif getattr(b, "copy_text", None):
            d["callback_data"] = "_noop"
        else:
            d["callback_data"] = "_noop"
        return d

    def _gate_allows(self, key: str, uid=None) -> bool:
        g = self.feature_gate
        if g is None or not key:
            return True
        try:
            if uid is not None and g.is_exempt(uid):
                return True
            return not g.is_disabled(key)
        except Exception:
            return True

    def _markup(self, rm, chat_id=None):
        if rm is None:
            return None
        if isinstance(rm, dict):
            return rm
        if isinstance(rm, InlineKeyboardMarkup):
            rows = []
            g = self.feature_gate
            for row in rm.inline_keyboard:
                out = []
                for b in row:
                    d = self._button(b)
                    data = getattr(b, "callback_data", None)
                    if data is None and getattr(b, "switch_inline_query_current_chat", None) is not None:
                        data = "_iq:" + str(b.switch_inline_query_current_chat)
                    if data and g is not None:
                        key = feature_key(str(data))
                        try:
                            g.seen(key, b.text)
                        except Exception:
                            pass
                        if not self._gate_allows(key, chat_id):
                            continue
                    out.append(d)
                if out:
                    rows.append(out)
            return {"inline_keyboard": rows}
        if isinstance(rm, ReplyKeyboardMarkup):
            d = {"keyboard": [[self._button(b) for b in row] for row in rm.keyboard]}
            if rm.resize_keyboard:
                d["resize_keyboard"] = True
            if rm.one_time_keyboard:
                d["one_time_keyboard"] = True
            return d
        if isinstance(rm, ReplyKeyboardRemove):
            return {"remove_keyboard": True}
        if isinstance(rm, ForceReply):
            return {"force_reply": True}
        return None

    @staticmethod
    def _chat(chat_id):
        if isinstance(chat_id, str):
            s = chat_id.strip()
            if s.lstrip("-").isdigit():
                return int(s)
            if s.startswith("https://") or s.startswith("http://"):
                s = "@" + s.rstrip("/").rsplit("/", 1)[-1]
            return s if s.startswith("@") else "@" + s
        return chat_id

    def _file_arg(self, value, field: str, files: dict, file_name: str = None):
        """Returns the JSON value for `field`, adding to `files` for uploads."""
        if value is None:
            return None
        if isinstance(value, (bytes, bytearray)):
            value = io.BytesIO(value)
        if hasattr(value, "read"):
            name = file_name or os.path.basename(getattr(value, "name", "") or f"{field}.bin")
            try:
                value.seek(0, os.SEEK_END)
                size = value.tell()
                value.seek(0)
            except Exception:
                size = 0
            self._check_size(size, field)
            files[field] = (name, value, mimetypes.guess_type(name)[0] or "application/octet-stream")
            return None
        s = str(value)
        if os.path.isfile(s):
            size = os.path.getsize(s)
            self._check_size(size, field)
            name = file_name or os.path.basename(s)
            fh = open(s, "rb")
            files[field] = (name, fh, mimetypes.guess_type(name)[0] or "application/octet-stream")
            return None
        return s

    def _check_size(self, size, field):
        limit = min(PHOTO_LIMIT, self.upload_limit) if field == "photo" else self.upload_limit
        if size and size > limit:
            raise errors.FileTooLarge(size, f"file is {size // (1024 * 1024)} MB; Bale allows {limit // (1024 * 1024)} MB",
                                      f"send_{field}")

    @staticmethod
    def _close(files):
        for f in (files or {}).values():
            try:
                f[1].close()
            except Exception:
                pass

    def _msg(self, result):
        if isinstance(result, dict):
            return Message._parse(self, result)
        return None

    # ------------------------------------------------------------ lifecycle
    async def start(self):
        me = await self.invoke("getMe")
        self.me = User._parse(me)
        self.is_connected = True
        try:
            await self.invoke("deleteWebhook", {})
        except Exception:
            pass
        self._sem = asyncio.Semaphore(self.workers)
        self._stop = asyncio.Event()
        self._poll_task = asyncio.create_task(self._poll())
        log.info("Bale client started as @%s", self.me.username)
        return self

    async def stop(self, block: bool = True):
        self.is_connected = False
        if self._stop:
            self._stop.set()
        if self._poll_task:
            self._poll_task.cancel()
        return self

    async def __aenter__(self):
        return await self.start()

    async def __aexit__(self, *a):
        await self.stop()

    def run(self, coroutine=None):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        if coroutine is not None:
            return loop.run_until_complete(coroutine)

        async def _main():
            await self.start()
            await idle()
            await self.stop()
        return loop.run_until_complete(_main())

    async def get_me(self):
        if not self.me:
            self.me = User._parse(await self.invoke("getMe"))
        return self.me

    # ------------------------------------------------------------ polling
    async def _poll(self):
        backoff = 1
        while self.is_connected:
            try:
                updates = await self.invoke("getUpdates", {"offset": self._offset or None, "timeout": 25,
                                                           "limit": 100}, timeout=40)
                backoff = 1
            except asyncio.CancelledError:
                return
            except Exception as e:
                print(f"getUpdates failed: {e}", flush=True)
                await asyncio.sleep(min(30, backoff))
                backoff *= 2
                continue
            for u in updates or []:
                uid = u.get("update_id", 0)
                self._offset = max(self._offset, uid + 1)
                asyncio.create_task(self._process(u))

    async def feed_update(self, u: dict):
        """Process one raw update (webhook mode / tests)."""
        await self._process(u)

    async def _process(self, u: dict):
        async with self._sem or asyncio.Semaphore(1):
            try:
                if "message" in u:
                    m = Message._parse(self, u["message"])
                    if await self._maybe_inline_text(m):
                        return
                    if not await self._gate_message(m):
                        return
                    await self._dispatch("message", m)
                elif "callback_query" in u:
                    cq = CallbackQuery._parse(self, u["callback_query"])
                    if cq.data in self._long_cb:
                        cq.data = self._long_cb[cq.data]
                    if cq.data == "_noop":
                        return await cq.answer()
                    uid = cq.from_user.id if cq.from_user else 0
                    if not self._gate_allows(feature_key(cq.data or ""), uid):
                        return await cq.answer("⛔️ این قابلیت فعلاً توسط مدیر غیرفعال شده است.", show_alert=True)
                    if await self._maybe_inline_callback(cq):
                        return
                    await self._dispatch("callback_query", cq)
                elif "edited_message" in u:
                    await self._dispatch("edited_message", Message._parse(self, u["edited_message"]))
                elif "channel_post" in u or "edited_channel_post" in u:
                    # Never routed to message handlers: the bot must not answer
                    # inside channels (e.g. the music cache channel).
                    d = u.get("channel_post") or u.get("edited_channel_post")
                    await self._channel_seen(Chat._parse(d.get("chat"), self))
                elif "my_chat_member" in u or "chat_member" in u:
                    d = u.get("chat_member") or u.get("my_chat_member")
                    upd = ChatMemberUpdated._parse(self, d)
                    if upd.chat and upd.chat.type == ChatType.CHANNEL:
                        await self._channel_seen(upd.chat)
                    await self._dispatch("chat_member_updated", upd)
            except Exception:
                print(f"update processing error:\n{traceback.format_exc()}", flush=True)

    @staticmethod
    def message_feature_key(m):
        if m.text and m.text.startswith("/"):
            return "cmd:/" + m.text[1:].split()[0].split("@")[0].lower() if m.text[1:].strip() else None
        if m.text:
            plat = link_platform(m.text)
            return f"link:{plat}" if plat else None
        kind = m.media
        return f"upload:{kind}" if kind else None

    async def _gate_message(self, m) -> bool:
        if self.feature_gate is None or m is None:
            return True
        key = self.message_feature_key(m)
        if not key:
            return True
        try:
            self.feature_gate.seen(key, key)
        except Exception:
            pass
        uid = m.from_user.id if m.from_user else (m.chat.id if m.chat else 0)
        if self._gate_allows(key, uid):
            return True
        if m.chat and m.chat.type in (ChatType.PRIVATE, ChatType.BOT):
            try:
                await self.send_message(m.chat.id, "⛔️ این قابلیت فعلاً توسط مدیر غیرفعال شده است.")
            except Exception:
                pass
        return False

    async def _channel_seen(self, chat):
        hook = getattr(self, "on_channel_seen", None)
        if hook and chat is not None:
            try:
                await hook(chat)
            except Exception:
                print(f"channel hook failed:\n{traceback.format_exc()}", flush=True)

    async def _dispatch(self, kind: str, obj):
        groups = self._handlers.get(kind) or {}
        for g in sorted(groups):
            for h in groups[g]:
                try:
                    if h.filters is not None and not h.filters(self, obj):
                        continue
                except Exception:
                    continue
                try:
                    await h.callback(self, obj)
                except StopPropagation:
                    return
                except ContinuePropagation:
                    continue
                except Exception:
                    print(f"handler {getattr(h.callback, '__name__', h.callback)} failed:\n"
                          f"{traceback.format_exc()}", flush=True)
                break

    # ------------------------------------------------------------ messages
    async def send_message(self, chat_id, text: str, parse_mode=None, reply_markup=None,
                           reply_to_message_id: int = None, disable_web_page_preview=None,
                           disable_notification=None, **kw):
        text = format_text(text, parse_mode)
        if not text.strip():
            text = "‌"
        chunks = [text[i:i + MAX_TEXT] for i in range(0, len(text), MAX_TEXT)] or [text]
        sent = None
        for i, chunk in enumerate(chunks):
            res = await self.invoke("sendMessage", {
                "chat_id": self._chat(chat_id), "text": chunk,
                "reply_markup": self._markup(reply_markup, chat_id) if i == len(chunks) - 1 else None,
                "reply_to_message_id": reply_to_message_id if i == 0 else None})
            sent = self._msg(res)
        return sent

    async def edit_message_text(self, chat_id, message_id: int, text: str, parse_mode=None,
                                reply_markup=None, disable_web_page_preview=None, **kw):
        if not message_id:
            return None
        text = format_text(text, parse_mode)[:MAX_TEXT]
        try:
            res = await self.invoke("editMessageText", {"chat_id": self._chat(chat_id), "message_id": message_id,
                                                        "text": text, "reply_markup": self._markup(reply_markup, chat_id)})
        except errors.MessageNotModified:
            return Message(message_id, Chat(chat_id), text=text, _client=self)
        except errors.BadRequest as e:
            d = e.description.lower()
            if "no text" in d or "caption" in d:
                return await self.edit_message_caption(chat_id, message_id, text, reply_markup=reply_markup)
            raise
        return self._msg(res) or Message(message_id, Chat(chat_id), text=text, _client=self)

    async def edit_message_caption(self, chat_id, message_id: int, caption: str, parse_mode=None,
                                   reply_markup=None, **kw):
        if not message_id:
            return None
        caption = format_text(caption, parse_mode)[:MAX_CAPTION]
        try:
            res = await self.invoke("editMessageCaption", {"chat_id": self._chat(chat_id), "message_id": message_id,
                                                           "caption": caption,
                                                           "reply_markup": self._markup(reply_markup, chat_id)})
        except errors.MessageNotModified:
            return Message(message_id, Chat(chat_id), caption=caption, _client=self)
        return self._msg(res) or Message(message_id, Chat(chat_id), caption=caption, _client=self)

    async def edit_message_reply_markup(self, chat_id, message_id: int, reply_markup=None, **kw):
        if not message_id:
            return None
        try:
            res = await self.invoke("editMessageReplyMarkup", {
                "chat_id": self._chat(chat_id), "message_id": message_id,
                "reply_markup": self._markup(reply_markup, chat_id) or {"inline_keyboard": []}})
        except errors.MessageNotModified:
            return None
        return self._msg(res)

    async def edit_message_media(self, chat_id, message_id: int, media: InputMedia, reply_markup=None, **kw):
        """Bale has no editMessageMedia: send the new media, then drop the old message."""
        sent = await self._send_input_media(chat_id, media, reply_markup)
        await self.delete_messages(chat_id, message_id)
        return sent

    async def delete_messages(self, chat_id, message_ids, revoke: bool = True):
        ids = message_ids if isinstance(message_ids, (list, tuple, set)) else [message_ids]
        n = 0
        for mid in ids:
            if not mid:
                continue
            try:
                await self.invoke("deleteMessage", {"chat_id": self._chat(chat_id), "message_id": int(mid)})
                n += 1
            except errors.RPCError:
                pass
        return n

    async def forward_messages(self, chat_id, from_chat_id, message_ids, **kw):
        ids = message_ids if isinstance(message_ids, (list, tuple)) else [message_ids]
        out = []
        for mid in ids:
            res = await self.invoke("forwardMessage", {"chat_id": self._chat(chat_id),
                                                       "from_chat_id": self._chat(from_chat_id), "message_id": mid})
            out.append(self._msg(res))
        return out if isinstance(message_ids, (list, tuple)) else out[0]

    async def copy_message(self, chat_id, from_chat_id, message_id: int, caption: str = None,
                           parse_mode=None, reply_markup=None, **kw):
        data = {"chat_id": self._chat(chat_id), "from_chat_id": self._chat(from_chat_id),
                "message_id": message_id, "reply_markup": self._markup(reply_markup, chat_id)}
        data["caption"] = format_text(self._signed(caption), parse_mode)[:MAX_CAPTION]
        try:
            res = await self.invoke("copyMessage", data)
            if isinstance(res, dict) and "chat" not in res:
                # copyMessage returns only {message_id}
                return Message(res.get("message_id", 0), Chat(chat_id), _client=self)
            return self._msg(res)
        except errors.RPCError:
            res = await self.invoke("forwardMessage", {"chat_id": self._chat(chat_id),
                                                       "from_chat_id": self._chat(from_chat_id),
                                                       "message_id": message_id})
            return self._msg(res)

    async def pin_chat_message(self, chat_id, message_id: int, **kw):
        try:
            return await self.invoke("pinChatMessage", {"chat_id": self._chat(chat_id), "message_id": message_id})
        except errors.RPCError:
            return False

    async def send_chat_action(self, chat_id, action=None):
        try:
            return await self.invoke("sendChatAction", {"chat_id": self._chat(chat_id), "action": "typing"})
        except errors.RPCError:
            return False

    # ------------------------------------------------------------ media
    def _signed(self, caption):
        """Every delivered file carries the bot's signature, so forwarded
        media always shows where it came from."""
        sig = self.caption_signature
        caption = caption or ""
        if not sig or sig in caption:
            return caption
        room = MAX_CAPTION - len(sig) - 2
        return (caption[:room] + "\n\n" + sig) if caption.strip() else sig

    async def _send_file(self, method: str, field: str, chat_id, value, caption: str = "", parse_mode=None,
                         reply_markup=None, reply_to_message_id=None, file_name: str = None, thumb=None,
                         extra: dict = None):
        files = {}
        data = {"chat_id": self._chat(chat_id), "reply_markup": self._markup(reply_markup, chat_id),
                "reply_to_message_id": reply_to_message_id}
        caption = self._signed(caption)
        if caption:
            data["caption"] = format_text(caption, parse_mode)[:MAX_CAPTION]
        data.update({k: v for k, v in (extra or {}).items() if v is not None})
        try:
            data[field] = self._file_arg(value, field, files, file_name)
            if thumb and isinstance(thumb, str) and os.path.isfile(thumb) and files:
                # Bale follows the older Bot API, where the field is "thumb";
                # send both names so either version picks the cover up.
                self._file_arg(thumb, "thumbnail", files)
                self._file_arg(thumb, "thumb", files)
            try:
                res = await self.invoke(method, data, files or None)
            except errors.BadRequest as e:
                if "thumbnail" in files or "thumb" in files:
                    for k in ("thumbnail", "thumb"):
                        if k in files:
                            files.pop(k)[1].close()
                    res = await self.invoke(method, data, files or None)
                elif field == "photo" and not files and "url" in e.description.lower():
                    raise
                else:
                    raise
            return self._msg(res)
        finally:
            self._close(files)

    async def send_photo(self, chat_id, photo, caption: str = "", parse_mode=None, reply_markup=None,
                         reply_to_message_id=None, has_spoiler=None, **kw):
        if isinstance(photo, str) and os.path.isfile(photo) and os.path.getsize(photo) > PHOTO_LIMIT:
            return await self.send_document(chat_id, photo, caption=caption, parse_mode=parse_mode,
                                            reply_markup=reply_markup)
        try:
            return await self._send_file("sendPhoto", "photo", chat_id, photo, caption, parse_mode, reply_markup,
                                         reply_to_message_id)
        except errors.BadRequest:
            # Bale often refuses remote photo URLs (5 MB cap, hotlink blocks): fetch and upload.
            if isinstance(photo, str) and photo.startswith("http"):
                blob = await asyncio.to_thread(self._fetch_bytes, photo)
                if blob:
                    bio = io.BytesIO(blob)
                    bio.name = "photo.jpg"
                    return await self._send_file("sendPhoto", "photo", chat_id, bio, caption, parse_mode,
                                                 reply_markup, reply_to_message_id)
            raise

    def _fetch_bytes(self, url: str):
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            if r.ok and len(r.content) <= UPLOAD_LIMIT:
                return r.content
        except Exception:
            pass
        return None

    async def _send_remote_fallback(self, method, field, chat_id, value, file_name, **kw):
        blob = await asyncio.to_thread(self._fetch_bytes, value)
        if not blob:
            raise errors.BadRequest(None, "could not fetch remote file", method)
        bio = io.BytesIO(blob)
        bio.name = file_name or os.path.basename(value.split("?")[0]) or f"{field}.bin"
        return await self._send_file(method, field, chat_id, bio, file_name=bio.name, **kw)

    async def send_audio(self, chat_id, audio, caption: str = "", parse_mode=None, duration: int = 0,
                         performer: str = None, title: str = None, thumb=None, file_name: str = None,
                         reply_markup=None, reply_to_message_id=None, **kw):
        extra = {"duration": int(duration) if duration else None, "performer": performer, "title": title}
        try:
            return await self._send_file("sendAudio", "audio", chat_id, audio, caption, parse_mode, reply_markup,
                                         reply_to_message_id, file_name, thumb or kw.get("thumbnail"), extra)
        except errors.BadRequest:
            if isinstance(audio, str) and audio.startswith("http"):
                return await self._send_remote_fallback("sendAudio", "audio", chat_id, audio, file_name,
                                                        caption=caption, parse_mode=parse_mode,
                                                        reply_markup=reply_markup, extra=extra)
            raise

    async def send_video(self, chat_id, video, caption: str = "", parse_mode=None, duration: int = 0,
                         width: int = 0, height: int = 0, thumb=None, file_name: str = None,
                         supports_streaming: bool = True, reply_markup=None, reply_to_message_id=None, **kw):
        extra = {"duration": int(duration) if duration else None, "width": width or None,
                 "height": height or None, "supports_streaming": True if supports_streaming else None}
        try:
            return await self._send_file("sendVideo", "video", chat_id, video, caption, parse_mode, reply_markup,
                                         reply_to_message_id, file_name, thumb or kw.get("thumbnail"), extra)
        except errors.BadRequest:
            if isinstance(video, str) and video.startswith("http"):
                return await self._send_remote_fallback("sendVideo", "video", chat_id, video, file_name,
                                                        caption=caption, parse_mode=parse_mode,
                                                        reply_markup=reply_markup, extra=extra)
            raise

    async def send_document(self, chat_id, document, caption: str = "", parse_mode=None, file_name: str = None,
                            thumb=None, reply_markup=None, reply_to_message_id=None, **kw):
        try:
            return await self._send_file("sendDocument", "document", chat_id, document, caption, parse_mode,
                                         reply_markup, reply_to_message_id, file_name, thumb)
        except errors.BadRequest:
            if isinstance(document, str) and document.startswith("http"):
                return await self._send_remote_fallback("sendDocument", "document", chat_id, document, file_name,
                                                        caption=caption, parse_mode=parse_mode,
                                                        reply_markup=reply_markup)
            raise

    async def send_animation(self, chat_id, animation, caption: str = "", parse_mode=None, reply_markup=None,
                             file_name: str = None, **kw):
        try:
            return await self._send_file("sendAnimation", "animation", chat_id, animation, caption, parse_mode,
                                         reply_markup, None, file_name)
        except errors.BadRequest:
            return await self.send_document(chat_id, animation, caption=caption, parse_mode=parse_mode,
                                            reply_markup=reply_markup, file_name=file_name)

    async def send_voice(self, chat_id, voice, caption: str = "", parse_mode=None, duration: int = 0,
                         reply_markup=None, **kw):
        return await self._send_file("sendVoice", "voice", chat_id, voice, caption, parse_mode, reply_markup,
                                     extra={"duration": duration or None})

    async def send_sticker(self, chat_id, sticker, reply_markup=None, **kw):
        return await self._send_file("sendSticker", "sticker", chat_id, sticker, reply_markup=reply_markup)

    async def _send_input_media(self, chat_id, media: InputMedia, reply_markup=None):
        fn = {"photo": self.send_photo, "video": self.send_video, "audio": self.send_audio,
              "animation": self.send_animation}.get(media.TYPE, self.send_document)
        kw = {k: v for k, v in media.__dict__.items() if k not in ("media", "caption", "parse_mode")}
        return await fn(chat_id, media.media, caption=media.caption or "", parse_mode=media.parse_mode,
                        reply_markup=reply_markup, **kw)

    async def send_media_group(self, chat_id, media: list, reply_to_message_id=None, **kw):
        media = [m for m in media if m is not None][:10]
        if not media:
            return []
        if len(media) == 1:
            m = await self._send_input_media(chat_id, media[0])
            return [m] if m else []
        files, items = {}, []
        try:
            for i, m in enumerate(media):
                field = f"file{i}"
                val = self._file_arg(m.media, field, files)
                item = {"type": m.TYPE if m.TYPE != "animation" else "video",
                        "media": f"attach://{field}" if val is None else val}
                if m.TYPE == "audio":
                    for k in ("title", "performer", "duration"):
                        v = getattr(m, k, None)
                        if v:
                            item[k] = v
                cap = self._signed(m.caption) if i == 0 else m.caption
                if cap:
                    item["caption"] = format_text(cap, m.parse_mode)[:MAX_CAPTION]
                items.append(item)
            res = await self.invoke("sendMediaGroup", {"chat_id": self._chat(chat_id), "media": items,
                                                       "reply_to_message_id": reply_to_message_id}, files or None)
            return [self._msg(r) for r in (res or [])]
        except errors.FileTooLarge:
            raise
        except errors.RPCError as e:
            print(f"sendMediaGroup failed ({e}); sending items one by one", flush=True)
            out = []
            for m in media:
                try:
                    out.append(await self._send_input_media(chat_id, m))
                except errors.RPCError as e2:
                    print(f"  item failed: {e2}", flush=True)
            if not any(out):
                raise
            return out
        finally:
            self._close(files)

    async def download_media(self, message, file_name: str = "", in_memory: bool = False, block: bool = True,
                             progress=None, progress_args=(), **kw):
        file_id, orig_name = None, None
        if isinstance(message, Message):
            media = None
            for a in ("audio", "video", "document", "voice", "animation", "photo", "sticker"):
                media = getattr(message, a, None)
                if media is not None:
                    break
            if media is None:
                raise ValueError("This message doesn't contain any downloadable media")
            file_id, orig_name = media.file_id, getattr(media, "file_name", None)
        elif hasattr(message, "file_id"):
            file_id, orig_name = message.file_id, getattr(message, "file_name", None)
        else:
            file_id = str(message)
        info = await self.invoke("getFile", {"file_id": file_id})
        path = (info or {}).get("file_path")
        if not path:
            raise errors.BadRequest(None, "file is not available for download (Bale allows up to 20 MB)", "getFile")
        url = path if path.startswith("http") else f"{self.api_base}/file/bot{self.bot_token}/{path}"
        blob = await asyncio.to_thread(self._download_bytes, url)
        if in_memory:
            bio = io.BytesIO(blob)
            bio.name = orig_name or os.path.basename(path)
            return bio
        target = file_name or "downloads/"
        if target.endswith("/") or target.endswith(os.sep) or os.path.isdir(target):
            target = os.path.join(target, orig_name or os.path.basename(path) or f"{file_id[:16]}.bin")
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
        with open(target, "wb") as fh:
            fh.write(blob)
        return os.path.abspath(target)

    def _download_bytes(self, url):
        last = None
        for attempt in range(3):
            try:
                r = self._session.get(url, timeout=300)
                if r.ok:
                    return r.content
                last = f"HTTP {r.status_code}"
            except requests.RequestException as e:
                last = str(e)
            time.sleep(1 + attempt)
        raise errors.InternalServerError(None, f"download failed: {last}", "download")

    # ------------------------------------------------------------ chats
    async def get_chat(self, chat_id):
        return Chat._parse(await self.invoke("getChat", {"chat_id": self._chat(chat_id)}), self)

    async def get_chat_member(self, chat_id, user_id):
        res = await self.invoke("getChatMember", {"chat_id": self._chat(chat_id), "user_id": int(user_id)})
        member = ChatMember._parse(res)
        if member is None:
            raise errors.UserNotParticipant(None, "user not found", "getChatMember")
        return member

    async def get_chat_members_count(self, chat_id):
        try:
            return int(await self.invoke("getChatMemberCount", {"chat_id": self._chat(chat_id)}) or 0)
        except errors.RPCError:
            return 0

    get_chat_member_count = get_chat_members_count

    async def get_users(self, user_ids):
        single = not isinstance(user_ids, (list, tuple))
        out = []
        for uid in ([user_ids] if single else user_ids):
            try:
                c = await self.get_chat(uid)
                out.append(User(c.id, c.first_name or c.title or "", c.last_name, c.username))
            except errors.RPCError:
                out.append(User(int(uid) if str(uid).lstrip("-").isdigit() else 0))
        return out[0] if single else out

    async def leave_chat(self, chat_id, delete: bool = False):
        return await self.invoke("leaveChat", {"chat_id": self._chat(chat_id)})

    async def export_chat_invite_link(self, chat_id):
        return await self.invoke("exportChatInviteLink", {"chat_id": self._chat(chat_id)})

    async def set_bot_commands(self, commands, **kw):
        try:
            return await self.invoke("setMyCommands", {"commands": [{"command": c.command,
                                                                      "description": c.description}
                                                                     for c in commands]})
        except errors.RPCError:
            return False

    async def answer_callback_query(self, callback_query_id: str, text: str = None, show_alert: bool = None,
                                    url: str = None, cache_time: int = 0):
        if not callback_query_id or callback_query_id.startswith("_local"):
            return True
        try:
            return await self.invoke("answerCallbackQuery", {
                "callback_query_id": callback_query_id, "text": (text or None) and str(text)[:200],
                "show_alert": bool(show_alert) if show_alert else None, "url": fix_url(url) if url else None})
        except errors.RPCError:
            return False

    async def answer_inline_query(self, inline_query_id, results, **kw):
        return True

    # ------------------------------------------------------------ inline emulation
    def _inline_target(self, inline_message_id):
        return self._inline_msgs.get(inline_message_id)

    async def edit_inline_text(self, inline_message_id, text, parse_mode=None, reply_markup=None, **kw):
        tgt = self._inline_target(inline_message_id)
        if not tgt:
            return False
        await self.edit_message_text(tgt[0], tgt[1], text, parse_mode=parse_mode, reply_markup=reply_markup)
        return True

    async def edit_inline_caption(self, inline_message_id, caption, parse_mode=None, reply_markup=None, **kw):
        tgt = self._inline_target(inline_message_id)
        if not tgt:
            return False
        await self.edit_message_caption(tgt[0], tgt[1], caption, parse_mode=parse_mode, reply_markup=reply_markup)
        return True

    async def edit_inline_media(self, inline_message_id, media, reply_markup=None, **kw):
        tgt = self._inline_target(inline_message_id)
        if not tgt:
            return False
        sent = await self.edit_message_media(tgt[0], tgt[1], media, reply_markup=reply_markup)
        if sent:
            self._inline_msgs[inline_message_id] = (tgt[0], sent.id)
        return True

    async def edit_inline_reply_markup(self, inline_message_id, reply_markup=None, **kw):
        tgt = self._inline_target(inline_message_id)
        if not tgt:
            return False
        await self.edit_message_reply_markup(tgt[0], tgt[1], reply_markup)
        return True

    @staticmethod
    def _inline_needs_text(q: str) -> bool:
        s = (q or "").strip()
        return not s or (" " not in s and s.endswith(":")) or s.lower() in ("sp", "ytm", "sc", "alb", "art", "pl",
                                                                              "ytm_alb", "ytm_art", "ytm_pl", "vid",
                                                                              "pt", "px", "ai")

    async def _maybe_inline_callback(self, cq: CallbackQuery) -> bool:
        data = cq.data or ""
        uid = cq.from_user.id if cq.from_user else 0
        chat = cq.message.chat if cq.message else Chat(uid)
        if data.startswith("_iq:"):
            await cq.answer()
            hook = getattr(self, "inline_button_hook", None)
            if hook is not None:
                await hook(self, cq.from_user, chat, data[4:])
            else:
                await self._start_inline(cq.from_user, chat, data[4:])
            return True
        if data == "_iqx":
            self._inline_pending.pop(uid, None)
            await cq.answer("لغو شد")
            if cq.message:
                await self.delete_messages(chat.id, cq.message.id)
            return True
        if data.startswith("_ir:"):
            await cq.answer()
            await self._inline_pick(cq.from_user, chat, data[4:])
            return True
        return False

    async def _start_inline(self, user, chat, q: str):
        if self._inline_needs_text(q):
            prefix = (q or "").strip()
            self._inline_pending[user.id] = (prefix, chat.id, time.time())
            label = f" ({prefix.rstrip(':')})" if prefix else ""
            await self.send_message(chat.id, f"🔎 عبارت جستجو{label} را بنویسید و ارسال کنید:",
                                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="_iqx")]]))
            return
        await self._run_inline(user, chat, q)

    async def _maybe_inline_text(self, m: Message) -> bool:
        if not m or not m.text or not m.from_user or m.text.startswith("/"):
            return False
        pend = self._inline_pending.get(m.from_user.id)
        if not pend or pend[1] != m.chat.id:
            return False
        self._inline_pending.pop(m.from_user.id, None)
        if time.time() - pend[2] > INLINE_PENDING_TTL:
            return False
        prefix = pend[0]
        q = f"{prefix} {m.text.strip()}".strip() if prefix else m.text.strip()
        await self._run_inline(m.from_user, m.chat, q)
        return True

    async def _run_inline(self, user, chat, q: str):
        status = await self.send_message(chat.id, "🔎 در حال جستجو...")
        iq = InlineQuery(f"_local{_short(6)}", user, q, chat_type=chat.type, _client=self, _chat_id=chat.id)
        iq._status = status
        iq._answered = False
        await self._dispatch("inline_query", iq)
        if not iq._answered:
            await self._edit_or_send(chat.id, status, "❌ نتیجه‌ای یافت نشد.")

    async def _edit_or_send(self, chat_id, status, text, reply_markup=None):
        try:
            if status:
                return await self.edit_message_text(chat_id, status.id, text, reply_markup=reply_markup)
        except errors.RPCError:
            pass
        return await self.send_message(chat_id, text, reply_markup=reply_markup)

    @staticmethod
    def _result_label(r) -> str:
        title = r.title or ""
        if not title and isinstance(r, InlineQueryResultCachedAudio):
            title = getattr(r, "label", "") or "🎵 Track"
        icon = "🎵" if isinstance(r, (InlineQueryResultCachedAudio, InlineQueryResultAudio)) else ""
        if isinstance(r, (InlineQueryResultPhoto, InlineQueryResultCachedPhoto)) and r.description:
            icon = "🎵" if "⏱" in (r.description or "") else "🖼"
        lab = f"{icon} {title}".strip()
        if r.description and len(lab) < 40:
            d = re.sub(r"\s+", " ", r.description).strip()
            lab = f"{lab} | {d}"
        return lab[:60] or "•"

    async def _render_inline_answer(self, iq: InlineQuery, results: list):
        iq._answered = True
        chat_id = iq._chat_id
        results = [r for r in results if r is not None][:INLINE_RESULTS_SHOWN]
        if not results:
            await self._edit_or_send(chat_id, getattr(iq, "_status", None), "❌ نتیجه‌ای یافت نشد.")
            return True
        if len(self._inline_results) > 3000:
            for k in list(self._inline_results)[:1500]:
                self._inline_results.pop(k, None)
        rows = []
        for r in results:
            tok = _short(10)
            self._inline_results[tok] = {"r": r, "q": iq.query, "ts": time.time()}
            rows.append([InlineKeyboardButton(self._result_label(r), callback_data=f"_ir:{tok}")])
        rows.append([InlineKeyboardButton("❌ بستن", callback_data="_iqx")])
        q = iq.query.strip()
        text = f"🔎 نتایج «{q}»:\n\nیکی از گزینه‌ها را انتخاب کنید 👇" if q else "👇 یکی از گزینه‌ها را انتخاب کنید:"
        await self._edit_or_send(chat_id, getattr(iq, "_status", None), text, InlineKeyboardMarkup(rows))
        return True

    def _synthetic_message(self, user, chat, text: str, via_bot=None) -> Message:
        return Message(0, chat, user, text=text, _client=self, via_bot=via_bot)

    async def _inline_pick(self, user, chat, tok: str):
        entry = self._inline_results.get(tok)
        if not entry:
            await self.send_message(chat.id, "⌛️ این نتیجه منقضی شده است؛ دوباره جستجو کنید.")
            return
        r, q = entry["r"], entry["q"]
        rid = r.id or ""
        is_track = (rid.split(".", 1)[0] in ("sp", "yt", "sc", "dz", "c")
                    and isinstance(r, (InlineQueryResultCachedAudio, InlineQueryResultPhoto,
                                       InlineQueryResultCachedPhoto, InlineQueryResultAudio)))
        start_param = None
        sw = None
        rows = r.reply_markup.inline_keyboard if isinstance(r.reply_markup, InlineKeyboardMarkup) else []
        for row in rows:
            for b in row:
                if b.url and not start_param:
                    m = re.search(r"(?:t\.me|ble\.ir)/([\w]+)\?start=([\w-]+)", b.url)
                    if m:
                        start_param = m.group(2)
                if b.switch_inline_query_current_chat is not None and sw is None:
                    sw = b.switch_inline_query_current_chat
        if is_track:
            if chat.type in (ChatType.PRIVATE, ChatType.BOT):
                await self._dispatch("chosen_inline_result",
                                     ChosenInlineResult(rid, user, q, None, _client=self))
                await self._dispatch("message", self._synthetic_message(user, chat, "", via_bot=self.me))
                return
            imid = None
            if isinstance(r, InlineQueryResultCachedAudio):
                await self.send_audio(chat.id, r.audio_file_id, caption=r.caption or "")
                return
            photo = getattr(r, "photo_url", None) or getattr(r, "photo_file_id", None)
            sent = await self.send_photo(chat.id, photo, caption=r.caption or "", parse_mode=r.parse_mode,
                                         reply_markup=r.reply_markup)
            if sent:
                imid = f"bale:{chat.id}:{sent.id}"
                self._inline_msgs[imid] = (chat.id, sent.id)
            await self._dispatch("chosen_inline_result", ChosenInlineResult(rid, user, q, imid, _client=self))
            return
        if start_param and not (rid.startswith("ai_")):
            await self._dispatch("message", self._synthetic_message(user, chat, f"/start {start_param}"))
            return
        if sw is not None:
            await self._start_inline(user, chat, sw)
            return
        content = r.input_message_content
        if isinstance(r, InlineQueryResultArticle) and content is not None:
            await self.send_message(chat.id, content.message_text, parse_mode=content.parse_mode,
                                    reply_markup=r.reply_markup)
        elif isinstance(r, InlineQueryResultCachedAudio):
            await self.send_audio(chat.id, r.audio_file_id, caption=r.caption or "", reply_markup=r.reply_markup)
        elif isinstance(r, (InlineQueryResultPhoto, InlineQueryResultCachedPhoto)):
            photo = getattr(r, "photo_url", None) or getattr(r, "photo_file_id", None)
            await self.send_photo(chat.id, photo, caption=r.caption or "", parse_mode=r.parse_mode,
                                  reply_markup=r.reply_markup)
        await self._dispatch("chosen_inline_result", ChosenInlineResult(rid, user, q, None, _client=self))


async def idle():
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError, ValueError):
            pass
    await stop.wait()
