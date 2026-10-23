"""Upload-driven tools: image tools, audio tools/recognition, video editor,
plus AI chat, lyrics, library/stats/playlists. Prints every step."""
import glob
import os
import sys
import time

import driver as D

UID = int(os.environ.get("TOOLS_UID", "444"))
U = {"id": UID, "is_bot": False, "first_name": "Tools", "username": "tools"}
C = {"id": UID, "type": "private", "first_name": "Tools"}
OUT = os.path.join(os.path.dirname(__file__), "out")
LOG = os.path.join(OUT, "bot.log")
RES = []


def w(n, **k):
    return D.wait(n, chat_id=UID, **k)


def logpos():
    return os.path.getsize(LOG)


def logtail(pos):
    with open(LOG, encoding="utf-8", errors="replace") as fh:
        fh.seek(pos)
        return fh.read()


def check(name, cs, pos, want=None):
    tb = [l for l in logtail(pos).splitlines() if "Traceback" in l or "failed:" in l and "handler" in l]
    errs = [c["error"] for c in cs if c.get("error") and "not modified" not in c["error"]]
    got = [c["method"] for c in cs if not c.get("error")]
    ok = bool(cs) and not errs and not tb and (not want or any(m in got for m in want))
    RES.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'} {name}: {got[-4:]} {errs[:2]} {'TRACEBACK' if tb else ''}", flush=True)
    if not ok:
        print(D.summarize(cs[-6:]))
        if tb:
            print(logtail(pos)[-1500:])
    return cs


def press(c, data, name, want=None, timeout=90, quiet=4):
    pos, n = logpos(), D.ncalls()
    D.click(D.msg_from_call(c), data, user=U)
    until = (lambda cs: any(x["method"] in want and not x.get("error") for x in cs)) if want else None
    return check(name, w(n, timeout=timeout, quiet=quiet, until=until), pos, want)


def say(text, name, want=None, timeout=90, quiet=4):
    pos, n = logpos(), D.ncalls()
    D.send(text, chat=C, user=U)
    until = (lambda cs: any(x["method"] in want and not x.get("error") for x in cs)) if want else None
    return check(name, w(n, timeout=timeout, quiet=quiet, until=until), pos, want)


def up(path, kind, name, timeout=60, **extra):
    pos, n = logpos(), D.ncalls()
    r = D.upload(path, kind, **extra) if False else None
    # driver.upload uses the default user; inline it for ours
    import requests
    fr = requests.post(f"{D.MOCK}/_add_file", json={"path": os.path.abspath(path)}).json()
    fid, size = fr["file_id"], fr["size"]
    obj = ([{"file_id": fid, "file_unique_id": fid, "width": 800, "height": 600, "file_size": size}]
           if kind == "photo" else {"file_id": fid, "file_unique_id": fid, "file_size": size,
                                    "file_name": os.path.basename(path), **extra})
    D.send(chat=C, user=U, **{kind: obj})
    return check(name, w(n, timeout=timeout, quiet=4), pos)


def kb(cs):
    c = D.last_message_with_buttons(cs)
    return c, [(b["text"], b.get("callback_data")) for b in D.buttons(c)]


def main(which):
    say("/start", "start")
    cs = say("/start", "start2")
    c, _ = kb(cs)
    if c:
        press(c, "setlang_fa", "lang fa")

    if "img" in which:
        cs = up(os.path.join(OUT, "img.png"), "photo", "photo upload -> image tools")
        c, btns = kb(cs)
        print("   image tools:", btns)
        for text, data in btns:
            if not data or data.startswith(("menu_main", "img_cancel")):
                continue
            cs2 = press(c, data, f"image tool {data}", timeout=120)
            c2, b2 = kb(cs2)
            # sub-menus (format / ratio / filter): press their first option
            for t2, d2 in b2[:1]:
                if d2 and d2.startswith(("imgfmt_", "imgratio_", "imgflt_", "img_")) and d2 != data:
                    press(c2, d2, f"   sub {d2}", want=("sendPhoto", "sendDocument"), timeout=180)

    if "audio" in which:
        mp3 = sorted(glob.glob(os.path.join(OUT, "files", "*.mp3")), key=os.path.getsize)
        mp3 = [p for p in mp3 if os.path.getsize(p) > 500_000]
        if mp3:
            cs = up(mp3[0], "audio", "audio upload -> tools", duration=200, title="Shape of You",
                    performer="Ed Sheeran", mime_type="audio/mpeg")
            c, btns = kb(cs)
            print("   audio tools:", btns)
            for text, data in btns[:8]:
                if data and not data.startswith("menu_main"):
                    press(c, data, f"audio tool {data}", timeout=150)

    if "video" in which:
        cs = up(os.path.join(OUT, "clip.mp4"), "video", "video upload -> editor", duration=6,
                width=640, height=360, mime_type="video/mp4")
        c, btns = kb(cs)
        print("   video editor:", btns)
        for data in ("vedit_mute", "vedit_rotate_cw", "vedit_extract_audio", "vedit_gif", "vedit_speed_2"):
            if any(d == data for _, d in btns):
                press(c, data, f"video {data}", want=("sendVideo", "sendAudio", "sendAnimation", "sendDocument"),
                      timeout=180)

    if "misc" in which:
        say("/tahaai", "ai mode")
        say("سلام، یک جمله درباره موسیقی بگو", "ai reply", want=("sendMessage", "editMessageText"), timeout=90)
        say("/tahalibrary", "library")
        say("/tahastats", "stats")
        say("/id", "id")
        say("/tahalyrics", "lyrics prompt")
        say("I'm in love with the shape of you", "lyrics search", timeout=90)
        say("/tahahelp", "help")
    print(f"\n== {sum(ok for _, ok in RES)}/{len(RES)} passed")


if __name__ == "__main__":
    main(set(sys.argv[1:]) or {"img", "audio", "video", "misc"})
