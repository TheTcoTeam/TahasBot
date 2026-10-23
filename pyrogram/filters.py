"""Composable update filters with the same names and semantics as pyrogram.filters."""
import re

from .enums import ChatType


class Filter:
    def __init__(self, func, name: str = "filter"):
        self.func = func
        self.name = name

    def __call__(self, client, update) -> bool:
        return bool(self.func(client, update))

    def __and__(self, other):
        return Filter(lambda c, u: self(c, u) and other(c, u), f"({self.name} & {other.name})")

    def __or__(self, other):
        return Filter(lambda c, u: self(c, u) or other(c, u), f"({self.name} | {other.name})")

    def __invert__(self):
        return Filter(lambda c, u: not self(c, u), f"~{self.name}")

    def __repr__(self):
        return f"<Filter {self.name}>"


def create(func, name: str = None, **kwargs):
    f = Filter(lambda c, u: func(f, c, u), name or "custom")
    for k, v in kwargs.items():
        setattr(f, k, v)
    return f


def _msg(update):
    # Message filters applied to a CallbackQuery look at its message.
    return getattr(update, "message", None) if not hasattr(update, "chat") else update


text = Filter(lambda c, m: bool(getattr(m, "text", None)), "text")
caption = Filter(lambda c, m: bool(getattr(m, "caption", None)), "caption")
audio = Filter(lambda c, m: getattr(m, "audio", None) is not None, "audio")
voice = Filter(lambda c, m: getattr(m, "voice", None) is not None, "voice")
video = Filter(lambda c, m: getattr(m, "video", None) is not None, "video")
document = Filter(lambda c, m: getattr(m, "document", None) is not None, "document")
photo = Filter(lambda c, m: getattr(m, "photo", None) is not None, "photo")
animation = Filter(lambda c, m: getattr(m, "animation", None) is not None, "animation")
sticker = Filter(lambda c, m: getattr(m, "sticker", None) is not None, "sticker")
media = Filter(lambda c, m: any(getattr(m, a, None) is not None for a in
                                ("audio", "voice", "video", "document", "photo", "animation", "sticker")), "media")
via_bot = Filter(lambda c, m: getattr(m, "via_bot", None) is not None, "via_bot")
reply = Filter(lambda c, m: getattr(m, "reply_to_message", None) is not None, "reply")
bot = Filter(lambda c, m: bool(getattr(getattr(m, "from_user", None), "is_bot", False)), "bot")
all = Filter(lambda c, m: True, "all")  # noqa: A001


def _chat_type_is(*types):
    def f(c, u):
        m = _msg(u)
        ch = getattr(m, "chat", None)
        return ch is not None and ch.type in types
    return f


private = Filter(_chat_type_is(ChatType.PRIVATE, ChatType.BOT), "private")
group = Filter(_chat_type_is(ChatType.GROUP, ChatType.SUPERGROUP), "group")
channel = Filter(_chat_type_is(ChatType.CHANNEL), "channel")


def command(commands, prefixes="/", case_sensitive: bool = False):
    if isinstance(commands, str):
        commands = [commands]
    cmds = {c if case_sensitive else c.lower() for c in commands}
    if isinstance(prefixes, str):
        prefixes = [prefixes]
    prefixes = list(prefixes or [""])

    def func(c, m):
        t = getattr(m, "text", None) or getattr(m, "caption", None)
        if not t:
            return False
        me = (getattr(getattr(c, "me", None), "username", None) or "").lower()
        for p in prefixes:
            if not t.startswith(p):
                continue
            body = t[len(p):]
            parts = body.split()
            if not parts:
                continue
            head = parts[0]
            name, _, target = head.partition("@")
            if target and me and target.lower() != me:
                continue
            key = name if case_sensitive else name.lower()
            if key in cmds:
                m.command = [name] + parts[1:]
                return True
        return False

    return Filter(func, f"command({sorted(cmds)})")


def regex(pattern, flags: int = 0):
    rx = re.compile(pattern, flags) if isinstance(pattern, str) else pattern

    def func(c, u):
        value = getattr(u, "data", None)
        if value is None:
            value = getattr(u, "text", None) or getattr(u, "caption", None)
        if value is None:
            value = getattr(u, "query", None)
        if not isinstance(value, str):
            return False
        found = list(rx.finditer(value))
        if found:
            u.matches = found
            return True
        return False

    return Filter(func, f"regex({rx.pattern!r})")


def user(users):
    if not isinstance(users, (list, set, tuple)):
        users = [users]
    ids = {int(x) for x in users if str(x).lstrip("-").isdigit()}
    return Filter(lambda c, m: getattr(getattr(m, "from_user", None), "id", None) in ids, "user")


def chat(chats):
    if not isinstance(chats, (list, set, tuple)):
        chats = [chats]
    ids = {int(x) for x in chats if str(x).lstrip("-").isdigit()}
    return Filter(lambda c, m: getattr(getattr(_msg(m), "chat", None), "id", None) in ids, "chat")
