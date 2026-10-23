"""Checks for the second round of changes (search UX, photo tools, Google full
quality, access approval, admin panels, feature switches, signature)."""
import os
import sys
import time

import requests

import driver as D

RES = []
ADMIN = {"id": 111, "is_bot": False, "first_name": "Admin"}
ADMIN_C = {"id": 111, "type": "private", "first_name": "Admin"}


def mk(uid):
    return ({"id": uid, "is_bot": False, "first_name": f"U{uid}", "username": f"u{uid}"},
            {"id": uid, "type": "private", "first_name": f"U{uid}"})


def say(user, chat, text, **kw):
    n = D.ncalls()
    D.send(text, chat=chat, user=user)
    return D.wait(n, chat_id=chat["id"], **kw)


def press(user, chat, call, data, **kw):
    if "until" not in kw:
        kw["until"] = lambda cs: any(D.buttons(x) or x["method"].startswith("edit") and x["method"] != "editMessageReplyMarkup"
                                     for x in cs if x["method"] != "answerCallbackQuery")
    n = D.ncalls()
    D.click(D.msg_from_call(call), data, user=user)
    return D.wait(n, chat_id=chat["id"], **kw)


def texts(cs):
    return [str(c["params"].get("text") or c["params"].get("caption") or "") for c in cs]


def btn(cs, pred):
    for c in reversed(cs):
        for b in D.buttons(c):
            if pred(b.get("callback_data") or "", b["text"]):
                return c, b["callback_data"]
    return None, None


def check(name, ok, info=""):
    RES.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'} {name} {info}", flush=True)


def media(cs, kinds=("sendAudio",)):
    return [c for c in cs if c["method"] in kinds and not c.get("error")]


def main(which):
    U, C = mk(601)
    say(U, C, "/start", timeout=15)

    if "ytm" in which:
        cs = say(U, C, "Blinding Lights", timeout=20, quiet=3)
        c, d = btn(cs, lambda d, t: d.startswith("_iq:ytm"))
        cs = press(U, C, c, d, timeout=90, until=lambda cs: any(len(D.buttons(x)) > 3 for x in cs))
        c, d = btn(cs, lambda d, t: d.startswith("_ir:"))
        cs = press(U, C, c, d, timeout=240, until=lambda cs: any(x["method"] == "editMessageReplyMarkup" or
                                                                  (x["method"] == "sendAudio" and D.buttons(x)) for x in cs))
        status = any("در حال دانلود" in t for t in texts(cs))
        kb = [b.get("callback_data") for x in cs for b in D.buttons(x)
              if x["method"] in ("sendAudio", "editMessageReplyMarkup", "copyMessage")]
        check("ytm inline pick: status + song menu", status and any(k and k.startswith("lyrics_") for k in kb),
              f"status={status} kb={kb[:4]}")
        # in-chat YTM menu search (/tahamusic -> ytm)
    if "sc" in which:
        cs = say(U, C, "/tahasoundcloud", timeout=15)
        cs = say(U, C, "gods plan drake", timeout=150,
                 until=lambda cs: any(any((b.get("callback_data") or "").startswith("offvid_") for b in D.buttons(x)) for x in cs))
        c, d = btn(cs, lambda d, t: d.startswith("offvid_"))
        check("soundcloud results use shared view", c is not None, d or texts(cs)[-1:])
        if c:
            cs = press(U, C, c, d, timeout=240, until=lambda cs: any(
                x["method"] == "editMessageReplyMarkup" or (x["method"] in ("sendAudio", "copyMessage") and D.buttons(x)) for x in cs))
            status = any("در حال دانلود" in t for t in texts(cs))
            kb = [b.get("callback_data") for x in cs for b in D.buttons(x)]
            check("soundcloud pick: status + song menu", status and any(k and k.startswith("lyrics_") for k in kb),
                  f"status={status}")
    if "img" in which:
        U, C = mk(602)
        say(U, C, "/start", timeout=15)
        cs = say(U, C, "/tahaphoto mountain", timeout=120, quiet=8,
                 until=lambda cs: sum(x["method"] == "sendPhoto" for x in cs) >= 8)
        photos = [x for x in cs if x["method"] == "sendPhoto" and not x.get("error")]
        check("pexels search sends photos", photos, len(photos))
        if photos:
            c = photos[0]
            d = [b["callback_data"] for b in D.buttons(c)][0]
            cs2 = press(U, C, c, d, timeout=90, until=lambda cs: any(x["method"] == "sendDocument" for x in cs))
            docs = media(cs2, ("sendDocument",))
            check("full quality download", docs, [x.get("error") for x in cs2 if x.get("error")])
            if docs:
                cs3 = press(U, C, docs[-1], "menu_image_tools", timeout=90,
                            until=lambda cs: any(any(b.get("callback_data") == "itool_compress" for b in D.buttons(x)) for x in cs))
                c4, d4 = btn(cs3, lambda d, t: d == "itool_compress")
                check("photo tools button opens tools", c4 is not None, texts(cs3)[-1:])
                if c4:
                    cs5 = press(U, C, c4, "itool_compress", timeout=120,
                                until=lambda cs: any(x["method"] == "sendDocument" for x in cs))
                    check("tool works on that photo", media(cs5, ("sendDocument",)))
        # google provider
        cs = say(U, C, "/start", timeout=10)
    if "admin" in which:
        # approval on
        cs = say(ADMIN, ADMIN_C, "/admin", timeout=15)
        c, d = btn(cs, lambda d, t: d == "adm_access")
        check("admin home has access button", c)
        cs = press(ADMIN, ADMIN_C, c, "adm_access", timeout=20, quiet=2)
        c, d = btn(cs, lambda d, t: d == "adm_acctoggle")
        txt = texts(cs)[-1] if cs else ""
        if "خاموش (همه آزادند)" in txt:
            cs = press(ADMIN, ADMIN_C, c, "adm_acctoggle", timeout=20, quiet=2)
        X, XC = mk(700 + int(time.time()) % 100)
        cs = say(X, XC, "/start", timeout=15)
        check("new user gets access message with id", any(str(X["id"]) in t and "Tahathegoat" in t for t in texts(cs)),
              texts(cs)[-1:][:1])
        n = D.ncalls()
        time.sleep(3)
        adm = [c for c in D.calls_since(n - 30) if str(c["params"].get("chat_id")) == "111" and "درخواست دسترسی" in str(c["params"].get("text"))]
        check("admin notified of request", adm)
        if adm:
            cs = press(ADMIN, ADMIN_C, adm[-1], f"adm_appr_{X['id']}", timeout=15, quiet=2)
        cs = say(X, XC, "/start", timeout=15)
        check("approved user reaches menu", not any("Tahathegoat" in t for t in texts(cs)), texts(cs)[-1:][:1])
        # limits
        cs = say(ADMIN, ADMIN_C, "/admin", timeout=15)
        c, _ = btn(cs, lambda d, t: d == "adm_limits")
        cs = press(ADMIN, ADMIN_C, c, "adm_limits", timeout=15, quiet=2)
        c, _ = btn(cs, lambda d, t: d == "adm_lim_-5")
        cs = press(ADMIN, ADMIN_C, c, "adm_lim_-5", timeout=15, quiet=2)
        check("upload limit decreases", any("45 مگابایت" in t for t in texts(cs)), texts(cs)[-1:][:1])
        c, _ = btn(cs, lambda d, t: d == "adm_limset_50")
        press(ADMIN, ADMIN_C, c, "adm_limset_50", timeout=15, quiet=2)
        # feature switch: turn off q_1080p and the music menu for users
        cs = say(ADMIN, ADMIN_C, "/admin", timeout=15)
        c, _ = btn(cs, lambda d, t: d == "adm_feat")
        cs = press(ADMIN, ADMIN_C, c, "adm_feat", timeout=15, quiet=2)
        c, _ = btn(cs, lambda d, t: d == "adm_feat_btn_0")
        cs = press(ADMIN, ADMIN_C, c, "adm_feat_btn_0", timeout=15, quiet=2)
        labels = [b["text"] for x in cs for b in D.buttons(x)]
        check("feature list shows buttons", any("🟢" in l for l in labels), labels[:3])
        cs = press(ADMIN, ADMIN_C, D.last_message_with_buttons(cs), "adm_feat_link_0", timeout=15, quiet=2)
        c, d = btn(cs, lambda d, t: "اینستاگرام" in t)
        if c:
            press(ADMIN, ADMIN_C, c, d, timeout=15, quiet=2)
        cs = say(X, XC, "https://www.instagram.com/reel/C-X-0s-M8iI/", timeout=20, quiet=3)
        check("disabled feature blocks users", any("غیرفعال" in t for t in texts(cs)), texts(cs)[-1:][:1])
        cs = say(ADMIN, ADMIN_C, "/admin", timeout=15)
        c, _ = btn(cs, lambda d, t: d == "adm_feat")
        cs = press(ADMIN, ADMIN_C, c, "adm_feat", timeout=15, quiet=2)
        c, _ = btn(cs, lambda d, t: d == "adm_featall")
        if c:
            press(ADMIN, ADMIN_C, c, "adm_featall", timeout=15, quiet=2)
    if "sig" in which:
        sent = [c for c in D.calls_since(0) if c["method"] in ("sendAudio", "sendPhoto", "sendDocument", "sendVideo")
                and c.get("chat") and not c.get("error")][-5:]
        check("media captions carry @tahasbot", sent and all("@tahasbot" in str(c["params"].get("caption")) for c in sent),
              [str(c["params"].get("caption"))[-30:] for c in sent])
    print(f"\n== {sum(ok for _, ok in RES)}/{len(RES)} passed")


if __name__ == "__main__":
    main(set(sys.argv[1:]) or {"ytm", "sc", "img", "admin", "sig"})
