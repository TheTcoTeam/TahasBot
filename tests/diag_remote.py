"""Runs the bot's own downloaders on whatever host executes it (GitHub
runner, Render shell...) and prints what each produced. No Bale traffic."""
import json, os, sys, time, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", "0:diag")
import bot as B  # noqa: E402

CASES = [
    ("instagram reel", B.download_instagram_native_sync, ["https://www.instagram.com/reel/C-X-0s-M8iI/"]),
    ("tiktok", B.download_tiktok_native_sync, ["https://www.tiktok.com/@tiktok/video/7106594312292453678"]),
    ("reddit search", B.reddit_search_sync, ["aww", 3]),
    ("reddit post", B.reddit_resolve_post_sync, ["1nzgq6j"]),
    ("youtube audio", B.download_source_audio_sync, [B.make_meta("yt", yt_id="JGwWNGJdvx8")]),
]
for name, fn, args in CASES:
    t0 = time.time()
    try:
        res = fn(*args)
        print(f"OK   {name} {time.time()-t0:.1f}s {repr(res)[:300]}", flush=True)
    except Exception as e:
        print(f"FAIL {name} {time.time()-t0:.1f}s {type(e).__name__}: {str(e)[:300]}", flush=True)
