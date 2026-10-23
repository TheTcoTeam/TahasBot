"""End-to-end feature flows against the mock Bale API: real downloads from
real platforms, checking that the user actually receives media."""
import os
import sys
import time

import driver as D
from driver import buttons, click, msg_from_call, ncalls, summarize, wait

UID = int(os.environ.get("FLOW_UID", "222"))
USER = {"id": UID, "is_bot": False, "first_name": "Flow", "username": "flow"}
CHAT = {"id": UID, "type": "private", "first_name": "Flow"}
RESULTS = []


def send(text=None, **kw):
    return D.send(text, chat=CHAT, user=USER, **kw)


def press(call, data):
    return click(msg_from_call(call), data, user=USER)


def media_calls(cs):
    return [c for c in cs if c["method"] in ("sendAudio", "sendVideo", "sendPhoto", "sendDocument",
                                             "sendMediaGroup", "sendAnimation", "sendVoice")
            and not c.get("error")]


def has_media(kinds=None):
    def f(cs):
        m = media_calls(cs)
        return bool(m) and (not kinds or any(c["method"] in kinds for c in m))
    return f


def record(name, ok, cs, note=""):
    RESULTS.append((name, ok, note))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {note}", flush=True)
    if not ok:
        print(summarize(cs[-12:]), flush=True)


def first_button(cs, pred):
    for c in reversed(cs):
        for b in buttons(c):
            d = b.get("callback_data") or ""
            if pred(d, b["text"]):
                return c, d
    return None, None


def flow_link(name, url, kinds=None, timeout=180):
    n = ncalls()
    send(url)
    cs = wait(n, until=has_media(kinds), timeout=timeout)
    m = media_calls(cs)
    up = sum(v["size"] for c in m for v in c.get("uploads", {}).values())
    record(name, bool(m) and (not kinds or any(c["method"] in kinds for c in m)), cs,
           f"{[c['method'] for c in m][:5]} {up // 1024}KB")
    return cs


def flow_search_pick(name, setup_cb, query, pick_pred, kinds=("sendAudio",), timeout=240):
    """menu button -> type query -> press a result -> expect media."""
    n = ncalls()
    if setup_cb:
        send("/start")
        cs = wait(n, timeout=20)
        c, _ = first_button(cs, lambda d, t: True)
        press(c, setup_cb)
        wait(n, timeout=15)
    n = ncalls()
    send(query)
    cs = wait(n, timeout=90, quiet=4)
    c, d = first_button(cs, pick_pred)
    if not c:
        record(name, False, cs, "no result button")
        return
    n2 = ncalls()
    press(c, d)
    cs2 = wait(n2, until=has_media(kinds), timeout=timeout)
    m = media_calls(cs2)
    record(name, bool(m), cs2, f"picked {d}; {[x['method'] for x in m][:4]}")


def main(which):
    t0 = time.time()
    send("/start")
    cs = wait(ncalls() - 1, timeout=20)
    c, d = first_button(cs, lambda d, t: d == "setlang_fa")
    if c:
        press(c, d)
        time.sleep(3)

    if "ig" in which:
        flow_link("instagram reel", "https://www.instagram.com/reel/DPfDZ9yjB0k/")
        flow_link("instagram post", "https://www.instagram.com/p/DPiGNumjCK5/")
    if "tt" in which:
        flow_link("tiktok video", "https://www.tiktok.com/@khaby.lame/video/7491624485342071062")
    if "sp" in which:
        flow_link("spotify track link", "https://open.spotify.com/track/7qiZfU4dY1lWllzX7mPBI3", ("sendAudio",))
    if "sc" in which:
        flow_link("soundcloud link", "https://soundcloud.com/octobersveryown/drake-gods-plan", ("sendAudio",))
    if "ytm" in which:
        flow_link("youtube music link", "https://music.youtube.com/watch?v=JGwWNGJdvx8", ("sendAudio",))
    if "pin" in which:
        flow_link("pinterest pin", "https://www.pinterest.com/pin/99360735500167749/", ("sendPhoto", "sendVideo", "sendDocument"))
    if "rd" in which:
        flow_link("reddit post", "https://www.reddit.com/r/aww/comments/1nzgq6j/", None)
    if "tw" in which:
        flow_link("twitter post", "https://x.com/NASA/status/1846626209939988739", None)
    if "yt" in which:
        n = ncalls()
        send("https://www.youtube.com/watch?v=jNQXAC9IVRw")
        cs = wait(n, timeout=60, quiet=4)
        record("youtube link -> options card", any(buttons(c) for c in cs), cs)
    if "search" in which:
        flow_search_pick("music text search (spotify)", None, "Shape of You Ed Sheeran",
                         lambda d, t: d.startswith(("sp_", "dl_", "offvid_", "regatrk_", "ytmdl_", "_ir:", "spt_")) or "🎵" in t)
    print(f"\n== {sum(ok for _, ok, _ in RESULTS)}/{len(RESULTS)} passed in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main(set(sys.argv[1:]) or {"ig", "tt", "sp", "sc", "ytm", "pin", "rd", "tw", "yt", "search"})
