"""Stand-in for pyrogram.utils (only the names the bot touches)."""
MIN_CHANNEL_ID = -1009999999999
MAX_USER_ID = 999999999999


def unpack_inline_message_id(inline_message_id: str):
    return inline_message_id
