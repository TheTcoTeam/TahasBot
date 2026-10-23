"""Local stand-in for the Bale Bot API, for end-to-end tests of bot.py.

Point the bot at it with BALE_API_BASE=http://127.0.0.1:8899. The test driver
pushes user updates with POST /_inject and reads everything the bot did with
GET /_calls?since=N. Uploaded files are kept so getFile/download work and the
driver can inspect what users would have received.
"""
import asyncio
import itertools
import json
import os
import sys
import time

from aiohttp import web

PORT = int(os.environ.get("MOCK_PORT", "8899"))
STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "files")
os.makedirs(STORE, exist_ok=True)

BOT = {"id": 746005524, "is_bot": True, "first_name": "tahasbot", "username": "tahasbot"}
UPLOAD_LIMIT = 50 * 1024 * 1024

calls = []
updates = []
update_waiters = []
msg_ids = itertools.count(1000)
upd_ids = itertools.count(1)
files = {}          # file_id -> path
messages = {}       # (chat, id) -> message dict
file_counter = itertools.count(1)


def chat_obj(chat_id):
    chat_id = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
    if isinstance(chat_id, int) and chat_id < 0:
        return {"id": chat_id, "type": "group", "title": "Test Group"}
    if isinstance(chat_id, str):
        return {"id": -1001, "type": "channel", "title": chat_id, "username": chat_id.lstrip("@")}
    return {"id": chat_id, "type": "private", "first_name": "Tester"}


def media_obj(kind, fid, size, params):
    if kind == "photo":
        return [{"file_id": fid, "file_unique_id": fid, "width": 800, "height": 800, "file_size": size}]
    d = {"file_id": fid, "file_unique_id": fid, "file_size": size}
    for k in ("duration", "title", "performer", "width", "height"):
        if params.get(k) not in (None, ""):
            v = params[k]
            d[k] = int(v) if k in ("duration", "width", "height") and str(v).isdigit() else v
    if params.get("_filename"):
        d["file_name"] = params["_filename"]
    return d


async def read_params(request):
    params, uploads = {}, {}
    ctype = request.headers.get("Content-Type", "")
    if "multipart" in ctype:
        reader = await request.multipart()
        async for part in reader:
            if part.filename is not None:
                fid = f"F{next(file_counter):05d}"
                safe = "".join(c for c in part.filename if c.isalnum() or c in "._-")[-60:] or "bin"
                path = os.path.join(STORE, f"{fid}_{safe}")
                size = 0
                with open(path, "wb") as fh:
                    while True:
                        chunk = await part.read_chunk(1 << 20)
                        if not chunk:
                            break
                        size += len(chunk)
                        fh.write(chunk)
                files[fid] = path
                uploads[part.name] = (fid, size, part.filename)
            else:
                params[part.name] = (await part.read()).decode("utf-8", "replace")
    elif "json" in ctype:
        try:
            params = await request.json()
        except Exception:
            params = {}
    else:
        params = dict(request.query)
        if request.can_read_body:
            params.update(dict(await request.post()))
    for k in ("reply_markup", "media"):
        if isinstance(params.get(k), str):
            try:
                params[k] = json.loads(params[k])
            except ValueError:
                pass
    return params, uploads


def ok(result):
    return web.json_response({"ok": True, "result": result})


def err(code, desc, **extra):
    return web.json_response({"ok": False, "error_code": code, "description": desc, **extra}, status=200)


def new_message(chat_id, **fields):
    m = {"message_id": next(msg_ids), "date": int(time.time()), "chat": chat_obj(chat_id),
         "from": BOT}
    m.update({k: v for k, v in fields.items() if v is not None})
    messages[(str(m["chat"]["id"]), m["message_id"])] = m
    return m


def resolve_media(field, params, uploads):
    if field in uploads:
        fid, size, name = uploads[field]
        if size > (10 * 1024 * 1024 if field == "photo" else UPLOAD_LIMIT):
            return None, size, "Request Entity Too Large"
        params["_filename"] = name
        return fid, size, None
    v = params.get(field)
    if not v:
        return None, 0, f"Bad Request: {field} is empty"
    if str(v).startswith("http"):
        return f"URL:{v}", 0, None
    if v not in files and not str(v).startswith("URL:") and not str(v).startswith("EXT"):
        return None, 0, "Bad Request: wrong file identifier"
    return v, os.path.getsize(files[v]) if v in files else 0, None


async def _api(request, entry_box):
    method = request.match_info["method"]
    params, uploads = await read_params(request)
    entry = {"n": len(calls), "t": time.time(), "method": method,
             "params": {k: v for k, v in params.items() if not k.startswith("_")},
             "uploads": {k: {"file_id": v[0], "size": v[1], "name": v[2]} for k, v in uploads.items()}}
    calls.append(entry)
    entry_box.append(entry)

    if method == "getMe":
        return ok(BOT)
    if method in ("deleteWebhook", "setMyCommands", "answerCallbackQuery", "sendChatAction",
                  "pinChatMessage", "leaveChat", "setWebhook"):
        if method == "answerCallbackQuery" and len(str(params.get("text") or "")) > 200:
            return err(400, "Bad Request: text too long")
        return ok(True)
    if method == "getUpdates":
        timeout = float(params.get("timeout") or 0)
        offset = int(params.get("offset") or 0)
        end = time.time() + min(timeout, 2)
        while True:
            pending = [u for u in updates if u["update_id"] >= offset]
            if pending or time.time() >= end:
                return ok(pending[:100])
            await asyncio.sleep(0.05)
    if method == "sendMessage":
        text = str(params.get("text") or "")
        if not text:
            return err(400, "Bad Request: message text is empty")
        if len(text) > 4096:
            return err(400, "Bad Request: message is too long")
        return ok(new_message(params["chat_id"], text=text, reply_markup=params.get("reply_markup")))
    if method in ("editMessageText", "editMessageCaption", "editMessageReplyMarkup"):
        key = (str(params.get("chat_id")), int(params.get("message_id") or 0))
        m = messages.get(key)
        if not m:
            return err(400, "Bad Request: message to edit not found")
        if method == "editMessageText":
            if "text" not in m:
                return err(400, "Bad Request: there is no text in the message to edit")
            if m.get("text") == params.get("text") and m.get("reply_markup") == params.get("reply_markup"):
                return err(400, "Bad Request: message is not modified")
            m["text"] = params.get("text")
        elif method == "editMessageCaption":
            m["caption"] = params.get("caption")
        m["reply_markup"] = params.get("reply_markup")
        return ok(m)
    if method == "deleteMessage":
        key = (str(params.get("chat_id")), int(params.get("message_id") or 0))
        messages.pop(key, None)
        return ok(True)
    if method in ("sendPhoto", "sendAudio", "sendVideo", "sendDocument", "sendAnimation", "sendVoice", "sendSticker"):
        field = method[4:].lower()
        fid, size, e = resolve_media(field, params, uploads)
        if e:
            return err(413 if "Large" in e else 400, e)
        kind = "photo" if field == "photo" else field
        return ok(new_message(params["chat_id"], caption=params.get("caption"),
                              reply_markup=params.get("reply_markup"),
                              **{kind: media_obj(kind, fid, size, params)}))
    if method == "sendMediaGroup":
        out = []
        for item in params.get("media") or []:
            ref = item.get("media", "")
            if ref.startswith("attach://"):
                fid, size, _ = uploads.get(ref[9:], (None, 0, None))
            else:
                fid, size = ref, 0
            kind = item.get("type", "document")
            out.append(new_message(params["chat_id"], caption=item.get("caption"),
                                   **{kind: media_obj(kind, fid, size, {})}))
        return ok(out)
    if method in ("copyMessage", "forwardMessage"):
        src = messages.get((str(params.get("from_chat_id")), int(params.get("message_id") or 0)))
        if not src:
            return err(400, "Bad Request: message not found")
        clone = {k: v for k, v in src.items() if k not in ("message_id", "chat", "date")}
        if params.get("caption") is not None:
            clone["caption"] = params["caption"]
        if params.get("reply_markup"):
            clone["reply_markup"] = params["reply_markup"]
        m = new_message(params["chat_id"], **clone)
        return ok({"message_id": m["message_id"]} if method == "copyMessage" else m)
    if method == "getFile":
        fid = params.get("file_id")
        if fid not in files:
            return err(400, "Bad Request: wrong file_id")
        size = os.path.getsize(files[fid])
        if size > 20 * 1024 * 1024:
            return err(400, "Bad Request: file is too big")
        return ok({"file_id": fid, "file_unique_id": fid, "file_size": size, "file_path": fid})
    if method == "getChat":
        return ok(chat_obj(params.get("chat_id")))
    if method == "getChatMember":
        return ok({"user": {"id": int(params.get("user_id") or 0), "is_bot": False, "first_name": "T"},
                   "status": os.environ.get("MOCK_MEMBER_STATUS", "member")})
    if method == "getChatMemberCount":
        return ok(10)
    return err(404, f"Not Found: method {method} not mocked")


async def api(request):
    box = []
    resp = await _api(request, box)
    if box:
        try:
            body = json.loads(resp.body)
            res = body.get("result")
            if isinstance(res, dict) and res.get("message_id"):
                box[0]["mid"] = res["message_id"]
                box[0]["chat"] = res.get("chat", {}).get("id")
            elif isinstance(res, list):
                box[0]["mids"] = [r.get("message_id") for r in res if isinstance(r, dict)]
            if not body.get("ok"):
                box[0]["error"] = body.get("description")
        except Exception:
            pass
    return resp


async def download(request):
    fid = request.match_info["path"]
    if fid not in files:
        return web.Response(status=404)
    return web.FileResponse(files[fid])


async def inject(request):
    u = await request.json()
    u["update_id"] = next(upd_ids)
    if "message" in u:
        u["message"].setdefault("message_id", next(msg_ids))
        u["message"].setdefault("date", int(time.time()))
        m = u["message"]
        messages[(str(m["chat"]["id"]), m["message_id"])] = m
    updates.append(u)
    return web.json_response({"update_id": u["update_id"],
                              "message_id": u.get("message", {}).get("message_id")})


async def add_file(request):
    """Register a local file as if a user had uploaded it to Bale."""
    d = await request.json()
    fid = f"U{next(file_counter):05d}"
    files[fid] = d["path"]
    return web.json_response({"file_id": fid, "size": os.path.getsize(d["path"])})


async def get_calls(request):
    since = int(request.query.get("since", 0))
    return web.json_response(calls[since:])


async def get_message(request):
    m = messages.get((request.query["chat"], int(request.query["id"])))
    return web.json_response(m or {})


async def file_path(request):
    return web.json_response({"path": files.get(request.query["id"])})


def main():
    app = web.Application(client_max_size=200 * 1024 * 1024)
    app.router.add_post("/_inject", inject)
    app.router.add_post("/_add_file", add_file)
    app.router.add_get("/_calls", get_calls)
    app.router.add_get("/_message", get_message)
    app.router.add_get("/_file", file_path)
    app.router.add_route("*", "/bot{token}/{method}", api)
    app.router.add_get("/file/bot{token}/{path}", download)
    web.run_app(app, host="127.0.0.1", port=PORT, print=lambda *a: print(*a, file=sys.stderr))


if __name__ == "__main__":
    main()
