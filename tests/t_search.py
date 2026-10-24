"""Every music-menu search button must open the paged, sortable in-chat
results view, and picking a song must give: status message -> cover card ->
song (with title/performer) carrying its full menu."""
import sys

import driver as D
from t_new import mk, say, press, texts

U, C = mk(int(sys.argv[1]) if len(sys.argv) > 1 else 611)
RES = []


def check(name, ok, info=""):
    RES.append(ok)
    print(f"{'PASS' if ok else 'FAIL'} {name} {info}", flush=True)


def results_view(cs):
    return [x for x in cs if x["method"] in ("editMessageText", "sendMessage") and len(D.buttons(x)) >= 5]


def song_ok(cs):
    st = any("دانلود" in t for t in texts(cs))
    cover = any(x["method"] == "sendPhoto" for x in cs)
    audio = [x for x in cs if x["method"] in ("sendAudio", "copyMessage") and not x.get("error")]
    kb = [b.get("callback_data") for x in cs for b in D.buttons(x)
          if x["method"] in ("sendAudio", "copyMessage", "editMessageReplyMarkup")]
    meta = any(x["params"].get("title") and x["params"].get("performer") for x in audio if x["method"] == "sendAudio")
    return st, cover, bool(audio), any(k and k.startswith("lyrics_") for k in kb), meta or any(x["method"] == "copyMessage" for x in audio)


def run(button_q, typed, label, pick_prefixes=("offvid_", "sp_pick_", "collpick_")):
    cs = say(U, C, "/start", timeout=15, until=lambda cs: any(D.buttons(x) for x in cs))
    n = D.ncalls()
    D.click(D.msg_from_call(D.last_message_with_buttons(cs)), "_iq:" + button_q, user=U)
    cs = D.wait(n, chat_id=C["id"], timeout=60, until=lambda cs: any(x["method"] == "sendMessage" for x in cs))
    if typed:
        cs = say(U, C, typed, timeout=150, until=lambda cs: bool(results_view(cs)))
    else:
        cs = D.wait(n, chat_id=C["id"], timeout=150, until=lambda cs: bool(results_view(cs)))
    rv = results_view(cs)
    btns = [b["callback_data"] for b in D.buttons(rv[-1])] if rv else []
    check(f"{label}: paged+sortable results", rv and any(b.startswith(("osp_", "sppage_", "collp_")) for b in btns),
          btns[:8] if rv else texts(cs)[-1:])
    if not rv:
        return
    pick = next((b for b in btns if b.startswith(pick_prefixes)), None)
    cs = press(U, C, rv[-1], pick, timeout=300, until=lambda cs: any(
        x["method"] == "editMessageReplyMarkup" or (x["method"] in ("sendAudio", "copyMessage") and D.buttons(x))
        or (len(D.buttons(x)) >= 5 and x["method"] == "editMessageText" and x is not rv[-1]) for x in cs))
    return cs


cs = run("sp: ", "Shape of You", "spotify button")
if cs:
    st, cover, audio, menu, meta = song_ok(cs)
    check("spotify pick: status, cover card, song, full menu, tags", st and cover and audio and menu and meta,
          dict(status=st, cover=cover, audio=audio, menu=menu, meta=meta))
cs = run("ytm: ", "Blinding Lights", "ytm button")
if cs:
    st, cover, audio, menu, meta = song_ok(cs)
    check("ytm pick: status, cover card, song, full menu, tags", st and cover and audio and menu and meta,
          dict(status=st, cover=cover, audio=audio, menu=menu, meta=meta))
cs = run("sc: ", "gods plan", "soundcloud button")
if cs:
    st, cover, audio, menu, meta = song_ok(cs)
    check("sc pick: status, cover card, song, full menu, tags", st and cover and audio and menu and meta,
          dict(status=st, cover=cover, audio=audio, menu=menu, meta=meta))
cs = run("alb: ", "Divide", "album button")
if cs:
    check("album pick opens album", any("📀" in t or "آلبوم" in t for t in texts(cs)), texts(cs)[-1:])
run("art: ", "Ed Sheeran", "artist button")
run("ytm_alb: ", "After Hours", "ytm album button")
run("top:week", None, "top week button")
print(f"\n== {sum(RES)}/{len(RES)} passed")
