# -*- coding: utf-8 -*-
"""
Social Media Downloaders & Image Search for TahasBot:
- Twitter / X (via api.fxtwitter.com + yt-dlp fallback)
- Reddit (posts, galleries, video+audio merge via FFmpeg)
- Pinterest (pins, videos, search)
- Pexels (search, high-res photos)
- Google Images (search)
"""

import os
import re
import json
import time
import random
import glob
import shutil
import urllib.parse
import subprocess
import requests
from pathlib import Path

SOCIAL_DIR = "media_downloads"
os.makedirs(SOCIAL_DIR, exist_ok=True)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "MatgfHxNTX0YhK2Bs57afxmpfm4sQwXuYnDRteh5KblXgVn4VTjKL3fF").strip()

REDDIT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

# ----------------- TWITTER / X -----------------
TWITTER_URL_RE = re.compile(
    r"https?://(?:www\.|mobile\.)?(?:twitter|x|fxtwitter|vxtwitter|fixupx|fixvx)\.com/([A-Za-z0-9_]+|i(?:/web)?)/status(?:es)?/(\d+)",
    re.IGNORECASE
)


def download_url_to_file(url: str, out_path: str):
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, stream=True, timeout=30)
    r.raise_for_status()
    with open(out_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=65536):
            if chunk:
                f.write(chunk)


def twitter_fetch_sync(user: str, tweet_id: str) -> dict:
    """Download tweet media via fxtwitter API or yt-dlp."""
    files = []
    caption = ""
    try:
        r = requests.get(f"https://api.fxtwitter.com/{user}/status/{tweet_id}", headers={"User-Agent": USER_AGENT}, timeout=15)
        tw = (r.json() or {}).get("tweet") if r.ok else None
    except Exception as e:
        print(f"fxtwitter error: {e}", flush=True)
        tw = None

    if tw:
        author = (tw.get("author") or {}).get("screen_name") or user
        caption = f"{tw.get('text') or ''}\n\n👤 @{author}".strip()
        media_list = ((tw.get("media") or {}).get("all") or [])[:6]
        for i, m in enumerate(media_list):
            url = m.get("url") or ""
            if not url:
                continue
            is_video = m.get("type") in ("video", "gif")
            if not is_video and "pbs.twimg.com" in url and "name=" not in url:
                url += ("&" if "?" in url else "?") + "name=orig"
            path = os.path.join(SOCIAL_DIR, f"tw_{tweet_id}_{i}.{'mp4' if is_video else 'jpg'}")
            try:
                download_url_to_file(url, path)
                files.append(path)
            except Exception as e:
                print(f"Twitter media download failed: {e}", flush=True)

    if not files:
        # Fallback to yt-dlp
        prefix = os.path.join(SOCIAL_DIR, f"twy_{tweet_id}_%(id)s.%(ext)s")
        cmd = [
            "yt-dlp", "--no-playlist", "--no-check-certificates",
            "-o", prefix, f"https://x.com/{user}/status/{tweet_id}"
        ]
        res = subprocess.run(cmd, capture_output=True, timeout=60)
        found = glob.glob(os.path.join(SOCIAL_DIR, f"twy_{tweet_id}_*.*"))
        for f in found:
            if f.lower().endswith((".mp4", ".jpg", ".png", ".webp")):
                files.append(f)

    return {"files": files, "caption": caption}


# ----------------- REDDIT -----------------
def reddit_get_json_sync(url: str, params: dict = None) -> dict:
    r = requests.get(url, params=params, headers=REDDIT_HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def reddit_search_sync(query: str, limit: int = 15) -> list:
    try:
        data = reddit_get_json_sync("https://www.reddit.com/search.json", params={"q": query, "limit": limit, "sort": "relevance"})
        return [c["data"] for c in (data.get("data", {}).get("children") or [])]
    except Exception as e:
        print(f"reddit search error: {e}", flush=True)
        return []


def _reddit_post_media(d: dict) -> list:
    out = []
    if d.get("crosspost_parent_list") and not (d.get("is_video") or d.get("is_gallery")):
        return _reddit_post_media(d["crosspost_parent_list"][0])

    if d.get("is_video"):
        rv = (d.get("secure_media") or {}).get("reddit_video") or (d.get("media") or {}).get("reddit_video") or {}
        vurl = rv.get("fallback_url")
        if vurl:
            out.append({"type": "video", "url": vurl, "audio_url": vurl.split("?")[0].rsplit("/", 1)[0] + "/DASH_audio.mp4"})
            return out

    if d.get("is_gallery") and d.get("media_metadata"):
        items = (d.get("gallery_data") or {}).get("items") or []
        meta = d.get("media_metadata") or {}
        for item in items[:6]:
            media_id = item.get("media_id")
            m_info = meta.get(media_id) or {}
            s = m_info.get("s") or {}
            u = s.get("u") or s.get("gif")
            if u:
                out.append({"type": "photo", "url": u.replace("&amp;", "&")})
        if out:
            return out

    url = d.get("url") or ""
    if any(url.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp"]):
        out.append({"type": "photo", "url": url})
        return out

    return out


def download_reddit_post_sync(post_id: str) -> dict:
    try:
        data = reddit_get_json_sync(f"https://www.reddit.com/comments/{post_id}.json", params={"limit": 1})
        post = data[0]["data"]["children"][0]["data"]
    except Exception as e:
        print(f"Reddit fetch post error: {e}", flush=True)
        return {"files": [], "title": ""}

    title = (post.get("title") or "")[:200]
    medias = _reddit_post_media(post)
    files = []

    for i, m in enumerate(medias):
        ts = int(time.time() * 1000)
        ext = "mp4" if m["type"] == "video" else "jpg"
        out_p = os.path.join(SOCIAL_DIR, f"rd_{post_id}_{ts}_{i}.{ext}")
        try:
            if m["type"] == "video":
                vtmp = out_p + ".v.mp4"
                download_url_to_file(m["url"], vtmp)
                # Try audio
                atmp = out_p + ".a.mp4"
                has_audio = False
                if m.get("audio_url"):
                    try:
                        download_url_to_file(m["audio_url"], atmp)
                        has_audio = True
                    except Exception:
                        pass
                if has_audio and os.path.exists(atmp) and os.path.getsize(atmp) > 1000:
                    subprocess.run(
                        ["ffmpeg", "-y", "-loglevel", "error", "-i", vtmp, "-i", atmp, "-c", "copy", "-map", "0:v:0", "-map", "1:a:0", out_p],
                        check=True, timeout=60
                    )
                    try: os.remove(vtmp); os.remove(atmp)
                    except OSError: pass
                else:
                    os.replace(vtmp, out_p)
                files.append(out_p)
            else:
                download_url_to_file(m["url"], out_p)
                files.append(out_p)
        except Exception as e:
            print(f"Reddit item download error: {e}", flush=True)

    return {"files": files, "title": title}


# ----------------- PINTEREST -----------------
def pinterest_api_call_sync(resource: str, options: dict) -> dict:
    r = requests.get(
        f"https://www.pinterest.com/resource/{resource}Resource/get/",
        params={"data": json.dumps({"options": options})},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json().get("resource_response", {})


def _pin_to_media(d: dict):
    if not d:
        return None
    raw_title = d.get("title") or d.get("grid_title") or ""
    if isinstance(raw_title, dict):
        raw_title = raw_title.get("text") or ""
    title = str(raw_title).strip()
    images = d.get("images") or {}
    thumb = (images.get("474x") or images.get("236x") or {}).get("url")
    video_list = (d.get("videos") or {}).get("video_list") or {}
    best = None
    for f in video_list.values():
        if isinstance(f, dict) and f.get("url") and not f["url"].endswith(".m3u8"):
            if best is None or (f.get("height") or 0) > (best.get("height") or 0):
                best = f
    if best:
        return {"type": "video", "url": best["url"], "title": title, "thumb": thumb}
    orig = (images.get("orig") or {}).get("url")
    if orig:
        return {"type": "photo", "url": orig, "title": title, "thumb": thumb}
    return None


def pinterest_pin_download_sync(pin_id: str) -> dict:
    try:
        resp = pinterest_api_call_sync("Pin", {"field_set_key": "unauth_react_main_pin", "id": pin_id})
        media = _pin_to_media(resp.get("data") or {})
        if not media:
            return None
        ext = "mp4" if media["type"] == "video" else "jpg"
        out_p = os.path.join(SOCIAL_DIR, f"pt_{pin_id}_{int(time.time())}.{ext}")
        download_url_to_file(media["url"], out_p)
        return {"path": out_p, "type": media["type"], "title": media["title"]}
    except Exception as e:
        print(f"Pinterest pin download error: {e}", flush=True)
        return None


def pinterest_search_sync(query: str, page_size: int = 15) -> list:
    try:
        resp = pinterest_api_call_sync("BaseSearch", {"query": query, "scope": "pins", "page_size": page_size})
        results = (resp.get("data") or {}).get("results") or []
        out = []
        for r in results:
            med = _pin_to_media(r)
            if med:
                med["id"] = str(r.get("id"))
                out.append(med)
        return out
    except Exception as e:
        print(f"Pinterest search error: {e}", flush=True)
        return []


# ----------------- PEXELS -----------------
def pexels_search_sync(query: str, page: int = 1, per_page: int = 10) -> list:
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": query, "page": page, "per_page": per_page},
            timeout=15
        )
        if r.status_code == 200:
            data = r.json()
            return [
                {
                    "id": p["id"],
                    "photographer": p.get("photographer", "عکاس"),
                    "thumb": p["src"]["medium"],
                    "large": p["src"].get("large2x") or p["src"].get("large") or p["src"]["original"]
                }
                for p in (data.get("photos") or [])
            ]
    except Exception as e:
        print(f"Pexels search error: {e}", flush=True)
    return []


# ----------------- GOOGLE IMAGES -----------------
def google_images_search_sync(query: str, max_results: int = 10) -> list:
    """Scrape Google Image search results directly."""
    url = f"https://www.google.com/search?tbm=isch&q={urllib.parse.quote(query)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    try:
        r = requests.get(url, headers=headers, timeout=10)
        matches = re.findall(r'\["(https://[^"]+\.(?:jpg|jpeg|png|webp))",\d+,\d+\]', r.text)
        cleaned = []
        for m in matches:
            if "encrypted-tbn0" not in m and m not in cleaned:
                cleaned.append(m)
                if len(cleaned) >= max_results:
                    break
        return cleaned
    except Exception as e:
        print(f"Google images search error: {e}", flush=True)
        return []
