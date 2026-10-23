"""Pyrogram-style exceptions raised by the Bale HTTP client."""


class RPCError(Exception):
    ID = None
    CODE = 400

    def __init__(self, value=None, description: str = "", method: str = ""):
        self.value = value
        self.description = description or ""
        self.method = method
        super().__init__(f"[{self.CODE}] {method}: {self.description}")


class BadRequest(RPCError):
    CODE = 400


class Forbidden(RPCError):
    CODE = 403


class NotFound(RPCError):
    CODE = 404


class FloodWait(RPCError):
    CODE = 429


class InternalServerError(RPCError):
    CODE = 500


class MessageNotModified(BadRequest):
    pass


class MessageIdInvalid(BadRequest):
    pass


class MessageDeleteForbidden(Forbidden):
    pass


class UserNotParticipant(BadRequest):
    pass


class UserIsBlocked(Forbidden):
    pass


class PeerIdInvalid(BadRequest):
    pass


class ChatAdminRequired(Forbidden):
    pass


class FileTooLarge(BadRequest):
    """Bale rejects uploads over 50 MB (10 MB for photos)."""


class QueryIdInvalid(BadRequest):
    pass


def from_response(method: str, code: int, description: str, params: dict = None):
    d = (description or "").lower()
    params = params or {}
    if code == 429:
        return FloodWait(int(params.get("retry_after") or 5), description, method)
    if "not modified" in d:
        return MessageNotModified(None, description, method)
    if "too large" in d or "too big" in d or code == 413:
        return FileTooLarge(None, description, method)
    if "query is too old" in d or "query id is invalid" in d:
        return QueryIdInvalid(None, description, method)
    if "blocked" in d:
        return UserIsBlocked(None, description, method)
    if method == "getChatMember" and ("not found" in d or "participant" in d or code in (400, 404)):
        return UserNotParticipant(None, description, method)
    if "message to edit not found" in d or "message to delete not found" in d or "message not found" in d:
        return MessageIdInvalid(None, description, method)
    if "chat not found" in d or "peer" in d:
        return PeerIdInvalid(None, description, method)
    if code == 403:
        return Forbidden(None, description, method)
    if code == 404:
        return NotFound(None, description, method)
    if code >= 500:
        return InternalServerError(None, description, method)
    return BadRequest(None, description, method)
