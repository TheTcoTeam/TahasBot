"""Bale-backed drop-in for the parts of Pyrogram this bot uses.

The real Pyrogram speaks MTProto, which Bale does not offer. This package
shadows it so bot.py and modules/ keep their Pyrogram code unchanged while
every call goes to Bale's HTTP Bot API (see client.py).
"""
from . import enums, errors, filters, raw, types, utils
from .client import Client, ContinuePropagation, StopPropagation, idle

__version__ = "2.0.106-bale"
__all__ = ["Client", "filters", "idle", "StopPropagation", "ContinuePropagation",
           "raw", "utils", "enums", "errors", "types"]
