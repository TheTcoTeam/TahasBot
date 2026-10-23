"""Test driver: plays a Bale user against the mock API (tests/mock_bale.py)."""
import json
import os
import time

import requests

MOCK = os.environ.get("MOCK_URL", "http://127.0.0.1:8899")
USER = {"id": 111, "is_bot": False, "first_name": "Tester", "username": "tester"}
CHAT = {"id": 111, "type": "private", "first_name": "Tester"}
_cq = [0]


def ncalls():
    return len(requests.get(f"{MOCK}/_calls", params={"since": 0}).json())


def calls_since(n):
    return requests.get(f"{MOCK}/_calls", params={"since": n}).json()


def _inject(u):
    return requests.post(f"{MOCK}/_inject", json=u).json()


def send(text=None, chat=None, user=None, **media):
    msg = {"chat": chat or CHAT, "from": user or USER}
    if text is not None:
        msg["text"] = text
    msg.update(media)
    return _inject({"message": msg})


def upload(path, kind="document", caption=None, **extra):
    """Send a local file as a user upload (photo/audio/video/document/voice)."""
    r = requests.post(f"{MOCK}/_add_file", json={"path": os.path.abspath(path)}).json()
    fid, size = r["file_id"], r["size"]
    if kind == "photo":
        obj = [{"file_id": fid, "file_unique_id": fid, "width": 800, "height": 800, "file_size": size}]
    else:
        obj = {"file_id": fid, "file_unique_id": fid, "file_size": size,
               "file_name": os.path.basename(path), **extra}
    m = {kind: obj}
    if caption:
        m["caption"] = caption
    return send(**m)


def click(message: dict, data: str, user=None):
    _cq[0] += 1
    return _inject({"callback_query": {"id": f"cq{_cq[0]}", "from": user or USER, "data": data,
                                       "message": message, "chat_instance": "x"}})


def _for_chat(cs, chat_id):
    if chat_id is None:
        return cs
    return [c for c in cs if str(c["params"].get("chat_id")) == str(chat_id) or
            (c["method"] == "answerCallbackQuery")]


def wait(n0, until=None, timeout=60, quiet=2.0, chat_id=None):
    """Collect calls after index n0 until `until(calls)` is true, or the bot
    has been quiet for `quiet` seconds after its first call."""
    t0 = time.time()
    last_len, last_change = 0, time.time()
    while time.time() - t0 < timeout:
        cs = calls_since(n0)
        cs = _for_chat([c for c in cs if c["method"] != "getUpdates"], chat_id)
        if until and until(cs):
            return cs
        if len(cs) != last_len:
            last_len, last_change = len(cs), time.time()
        elif cs and not until and time.time() - last_change > quiet:
            return cs
        time.sleep(0.25)
    return _for_chat([c for c in calls_since(n0) if c["method"] != "getUpdates"], chat_id)


def message_of(call):
    """The message object (as the mock stored it) a send/edit call produced."""
    p = call["params"]
    return p


def last_message_with_buttons(cs):
    for c in reversed(cs):
        rm = c["params"].get("reply_markup")
        if isinstance(rm, dict) and rm.get("inline_keyboard"):
            return c
    return None


def buttons(call):
    rm = (call or {}).get("params", {}).get("reply_markup") or {}
    return [b for row in rm.get("inline_keyboard", []) for b in row]


def get_message(chat_id, mid):
    return requests.get(f"{MOCK}/_message", params={"chat": str(chat_id), "id": mid}).json()


def summarize(cs, width=110):
    out = []
    for c in cs:
        p = c["params"]
        body = p.get("text") or p.get("caption") or ""
        body = body.replace("\n", " ⏎ ")[:width]
        up = ",".join(f"{k}:{v['name']}({v['size'] // 1024}KB)" for k, v in c.get("uploads", {}).items())
        btns = len(buttons(c))
        out.append(f"  {c['method']:<22} {body} {('[' + up + ']') if up else ''}{f' <{btns} btns>' if btns else ''}")
    return "\n".join(out)


def result_messages(cs):
    """Messages the mock returned are not in the call log; refetch by scanning."""
    return cs


def msg_from_call(call):
    """Rebuild the message dict a call produced (for clicking its buttons)."""
    chat = call.get("chat") or call["params"].get("chat_id")
    mid = call.get("mid") or call["params"].get("message_id")
    m = get_message(chat, mid)
    return m or {"message_id": mid, "chat": CHAT, "from": {"id": 746005524, "is_bot": True}}
