"""Stand-in for pyrogram.raw: MTProto types do not exist on Bale."""


class _Placeholder:
    pass


class types:  # noqa: N801 - mirrors pyrogram.raw.types
    InputBotInlineMessageID = _Placeholder
    InputBotInlineMessageID64 = _Placeholder


class functions:  # noqa: N801
    pass
