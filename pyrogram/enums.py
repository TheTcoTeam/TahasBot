"""Pyrogram-compatible enums, mapped onto Bale Bot API string values."""
from enum import Enum


class ParseMode(Enum):
    DEFAULT = "default"
    MARKDOWN = "markdown"
    HTML = "html"
    DISABLED = "disabled"


class ChatType(Enum):
    PRIVATE = "private"
    BOT = "bot"
    GROUP = "group"
    SUPERGROUP = "supergroup"
    CHANNEL = "channel"

    @classmethod
    def parse(cls, value):
        try:
            return cls(str(value or "private").lower())
        except ValueError:
            return cls.PRIVATE


class ChatMemberStatus(Enum):
    OWNER = "owner"
    ADMINISTRATOR = "administrator"
    MEMBER = "member"
    RESTRICTED = "restricted"
    LEFT = "left"
    BANNED = "banned"

    @classmethod
    def parse(cls, value):
        v = str(value or "").lower()
        return {
            "creator": cls.OWNER, "owner": cls.OWNER,
            "administrator": cls.ADMINISTRATOR, "admin": cls.ADMINISTRATOR,
            "member": cls.MEMBER, "restricted": cls.RESTRICTED,
            "left": cls.LEFT, "kicked": cls.BANNED, "banned": cls.BANNED,
        }.get(v, cls.LEFT)


class MessageMediaType(Enum):
    AUDIO = "audio"
    DOCUMENT = "document"
    PHOTO = "photo"
    VIDEO = "video"
    VOICE = "voice"
    ANIMATION = "animation"
    STICKER = "sticker"
