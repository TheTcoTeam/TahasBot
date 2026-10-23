# -*- coding: utf-8 -*-
"""
AudD / Shazam Audio Recognition Engine for TahasBot.
Identifies any song from voice, audio, or video clip and queries track info.
"""

import os
import subprocess
import requests
import traceback

AUDD_API_KEY = os.environ.get("AUDD_API_KEY", "e2b508a812938bdf4e53b39397224db0").strip()


def extract_audio_clip_sync(src_path: str, clip_path: str, duration: int = 15):
    """Extract first N seconds of audio for song recognition."""
    cmd = [
        "ffmpeg", "-y", "-i", src_path,
        "-t", str(duration),
        "-vn", "-c:a", "libmp3lame", "-b:a", "128k",
        clip_path
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=30)


def audd_recognize_sync(clip_path: str) -> dict:
    """Send audio clip to AudD API and return result dict or None."""
    if not os.path.exists(clip_path):
        return None
    try:
        with open(clip_path, "rb") as f:
            r = requests.post(
                "https://api.audd.io/",
                data={"api_token": AUDD_API_KEY, "return": "apple_music,spotify"},
                files={"file": f},
                timeout=30
            )
        if r.status_code == 200:
            j = r.json()
            if j.get("status") == "success" and j.get("result"):
                res = j["result"]
                sp = res.get("spotify") or {}
                sp_imgs = ((sp.get("album") or {}).get("images") or [])
                cover_url = sp_imgs[0].get("url") if sp_imgs else ""

                return {
                    "title": res.get("title", "آهنگ ناشناخته"),
                    "artist": res.get("artist", "خواننده نامشخص"),
                    "album": res.get("album", ""),
                    "release_date": res.get("release_date") or "",
                    "cover": cover_url,
                    "duration": int((sp.get("duration_ms") or 0) // 1000)
                }
    except Exception as e:
        print(f"AudD recognition error: {e}", flush=True)
    return None
