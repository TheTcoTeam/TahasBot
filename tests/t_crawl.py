"""Press every reachable menu button once (per button shape) and report what
broke: handler tracebacks in the bot log, API calls the mock rejected, and
buttons the bot never answered."""
import collections
import json
import os
import re
import sys
import time

from driver import (buttons, calls_since, click, msg_from_call, ncalls, send, summarize, wait)

LOG = os.path.join(os.path.dirname(__file__), "out", "bot.log")
SKIP = re.compile(r"^(adm_maint$|adm_harvester$|adm_ban_|adm_unban_|adm_leavechat_|adm_chdel_|adm_smallzdel_|"
                  r"adm_maintdel_|adm_fjdel_|adm_bc$|unsub_|start_download$|kar_|aisub_|cola_|dl_all_alb_|albdl_all$|"
                  r"pl_ai_|pl_radio_|radio_start_ai$|ai_confirm_songs$|setlang_(?!fa)|pl_delete_|mixtape_now$|"
                  r"pl_playall_|pl_shuffle_|regatrk_|ytt_|sp_pick_|sp_rand$|offvid_|offrand$|scvid_|"
                  r"vid_\d|dlyt_|fmt_mp4$|q_\d|_ir:|_iq:|_iqx|adm_|pl_del|vedit_)")
LIMIT_PER_SIG = int(os.environ.get("LIMIT_PER_SIG", "1"))
MAX_PRESSES = int(os.environ.get("MAX_PRESSES", "400"))


def sig(data):
    d = re.sub(r"\d+", "N", data)
    return re.sub(r"_(?=[^_]*N)[^_]{6,}$", "_<id>", d)


def log_size():
    return os.path.getsize(LOG)


def new_log(pos):
    with open(LOG, encoding="utf-8", errors="replace") as fh:
        fh.seek(pos)
        return fh.read()


def main(entries):
    pressed, seen = set(), collections.Counter()
    queue = collections.deque()
    problems = []
    n_press = 0

    def harvest(cs):
        for c in cs:
            for b in buttons(c):
                d = b.get("callback_data")
                if not d or d in pressed or SKIP.search(d) or d == "_noop":
                    continue
                if seen[sig(d)] >= LIMIT_PER_SIG:
                    continue
                seen[sig(d)] += 1
                pressed.add(d)
                queue.append((d, c, b["text"]))

    def check(label, cs, pos):
        lg = new_log(pos)
        errs = [c for c in cs if c.get("error") and "not modified" not in c["error"]]
        tb = "Traceback" in lg or "failed:\n" in lg
        if errs or tb:
            problems.append((label, [f"{c['method']}: {c['error']}" for c in errs], lg[-1500:] if tb else ""))
            print(f"!! {label}: {[c['error'] for c in errs]} {'TRACEBACK' if tb else ''}", flush=True)

    for text in entries:
        pos, n = log_size(), ncalls()
        send(text)
        cs = wait(n, timeout=40, quiet=2.5)
        print(f"== {text}: {len(cs)} calls", flush=True)
        check(text, cs, pos)
        harvest(cs)

    while queue and n_press < MAX_PRESSES:
        d, c, label = queue.popleft()
        n_press += 1
        pos, n = log_size(), ncalls()
        click(msg_from_call(c), d)
        cs = wait(n, timeout=45, quiet=3.0)
        answered = [x for x in cs if x["method"] != "answerCallbackQuery"] or cs
        status = "ok" if cs else "NO RESPONSE"
        print(f"[{n_press}] {label[:30]!r} ({d}) -> {len(cs)} calls {status}", flush=True)
        if not cs:
            problems.append((f"{label} ({d})", ["no response"], ""))
        check(f"{label} ({d})", cs, pos)
        harvest(answered)

    print("\n================ SUMMARY ================")
    print(f"presses: {n_press}, problems: {len(problems)}")
    for label, errs, tb in problems:
        print(f"- {label}: {errs}")
        if tb:
            print("   " + tb.strip().replace("\n", "\n   ")[-1200:])
    with open(os.path.join(os.path.dirname(__file__), "out", "crawl_problems.json"), "w", encoding="utf-8") as fh:
        json.dump(problems, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:] or ["/start"])
