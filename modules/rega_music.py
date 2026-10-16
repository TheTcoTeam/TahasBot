# -*- coding: utf-8 -*-
"""
RegaSpotify-Style Music Engine for TMulti Bot.
Provides high-speed, high-resolution music, album, artist, and playlist search
and downloading matching the exact RegaSpotify workflow.
"""

import os
import re
import sys
import time
import requests
import subprocess
import traceback
from pathlib import Path
import concurrent.futures

DEEZER_BASE = "https://api.deezer.com"
MUSIC_DIR = "music_downloads"
os.makedirs(MUSIC_DIR, exist_ok=True)

# In-memory search cache with 10-minute TTL
_API_CACHE = {}

def _get_cache(key: str):
    item = _API_CACHE.get(key)
    if item and (time.time() - item[0] < 600):
        return item[1]
    return None

def _set_cache(key: str, val):
    if len(_API_CACHE) > 1000:
        _API_CACHE.clear()
    _API_CACHE[key] = (time.time(), val)


def format_duration(seconds: int) -> str:
    """Format seconds into MM:SS."""
    try:
        sec = int(seconds or 0)
        m = sec // 60
        s = sec % 60
        return f"{m:02d}:{s:02d}"
    except Exception:
        return "00:00"


def search_tracks(query: str, limit: int = 25) -> list:
    """Search tracks with clean metadata, duration, high-res covers, and instant MP3 previews."""
    ckey = f"trk_{query}_{limit}"
    cached = _get_cache(ckey)
    if cached is not None:
        return cached

    try:
        r = requests.get(f"{DEEZER_BASE}/search", params={"q": query, "limit": limit}, timeout=6)
        if r.status_code != 200:
            return []
        items = r.json().get("data", [])
        results = []
        for t in items:
            results.append({
                "id": str(t.get("id")),
                "title": t.get("title", "Unknown Title"),
                "artist": t.get("artist", {}).get("name", "Unknown Artist"),
                "album": t.get("album", {}).get("title", ""),
                "duration": int(t.get("duration", 0)),
                "thumb": t.get("album", {}).get("cover_big") or t.get("album", {}).get("cover_medium") or "",
                "preview": t.get("preview") or "",
                "isrc": t.get("isrc") or "",
                "link": f"https://www.deezer.com/track/{t.get('id')}"
            })
        _set_cache(ckey, results)
        return results
    except Exception as e:
        print(f"search_tracks error: {e}", flush=True)
        return []


def resolve_preview_url(title: str, artist: str = "") -> str:
    """Quickly find a valid 100% playable MP3 preview URL for any song."""
    query = f"{artist} {title}".strip()
    ckey = f"prev_{query}"
    cached = _get_cache(ckey)
    if cached is not None:
        return cached

    try:
        res = search_tracks(query, limit=1)
        if res and res[0].get("preview"):
            prev = res[0]["preview"]
            _set_cache(ckey, prev)
            return prev
    except Exception:
        pass
    _set_cache(ckey, "")
    return ""


def search_albums(query: str, limit: int = 20) -> list:
    """Search albums with title, artist, track count, and cover."""
    try:
        r = requests.get(f"{DEEZER_BASE}/search/album", params={"q": query, "limit": limit}, timeout=8)
        if r.status_code != 200:
            return []
        items = r.json().get("data", [])
        results = []
        for a in items:
            results.append({
                "id": str(a.get("id")),
                "title": a.get("title", "Unknown Album"),
                "artist": a.get("artist", {}).get("name", "Unknown Artist"),
                "nb_tracks": int(a.get("nb_tracks", 0)),
                "thumb": a.get("cover_big") or a.get("cover_medium") or "",
                "link": f"https://www.deezer.com/album/{a.get('id')}"
            })
        return results
    except Exception as e:
        print(f"search_albums error: {e}", flush=True)
        return []


def search_artists(query: str, limit: int = 20) -> list:
    """Search artists with name, fan count, and picture."""
    try:
        r = requests.get(f"{DEEZER_BASE}/search/artist", params={"q": query, "limit": limit}, timeout=8)
        if r.status_code != 200:
            return []
        items = r.json().get("data", [])
        results = []
        for art in items:
            results.append({
                "id": str(art.get("id")),
                "name": art.get("name", "Unknown Artist"),
                "fans": int(art.get("nb_fan", 0)),
                "thumb": art.get("picture_big") or art.get("picture_medium") or "",
                "link": f"https://www.deezer.com/artist/{art.get('id')}"
            })
        return results
    except Exception as e:
        print(f"search_artists error: {e}", flush=True)
        return []


def search_playlists(query: str, limit: int = 20) -> list:
    """Search playlists with title, user, track count, and picture."""
    try:
        r = requests.get(f"{DEEZER_BASE}/search/playlist", params={"q": query, "limit": limit}, timeout=8)
        if r.status_code != 200:
            return []
        items = r.json().get("data", [])
        results = []
        for pl in items:
            results.append({
                "id": str(pl.get("id")),
                "title": pl.get("title", "Unknown Playlist"),
                "user": pl.get("user", {}).get("name", "Unknown"),
                "nb_tracks": int(pl.get("nb_tracks", 0)),
                "thumb": pl.get("picture_big") or pl.get("picture_medium") or "",
                "link": f"https://www.deezer.com/playlist/{pl.get('id')}"
            })
        return results
    except Exception as e:
        print(f"search_playlists error: {e}", flush=True)
        return []


def get_chart_tracks(limit: int = 25) -> list:
    """Fetch top chart tracks (top:week / top:month)."""
    try:
        r = requests.get(f"{DEEZER_BASE}/chart/0/tracks", params={"limit": limit}, timeout=8)
        if r.status_code != 200:
            return []
        items = r.json().get("data", [])
        results = []
        for t in items:
            results.append({
                "id": str(t.get("id")),
                "title": t.get("title", "Unknown Title"),
                "artist": t.get("artist", {}).get("name", "Unknown Artist"),
                "album": t.get("album", {}).get("title", ""),
                "duration": int(t.get("duration", 0)),
                "thumb": t.get("album", {}).get("cover_big") or t.get("album", {}).get("cover_medium") or "",
                "preview": t.get("preview") or "",
                "link": f"https://www.deezer.com/track/{t.get('id')}"
            })
        return results
    except Exception as e:
        print(f"get_chart_tracks error: {e}", flush=True)
        return []


def get_artist_top_tracks(artist_id: str, limit: int = 15) -> list:
    """Fetch top tracks for an artist."""
    try:
        r = requests.get(f"{DEEZER_BASE}/artist/{artist_id}/top", params={"limit": limit}, timeout=8)
        if r.status_code != 200:
            return []
        items = r.json().get("data", [])
        results = []
        for t in items:
            results.append({
                "id": str(t.get("id")),
                "title": t.get("title", "Unknown Title"),
                "artist": t.get("artist", {}).get("name", "Unknown Artist"),
                "album": t.get("album", {}).get("title", ""),
                "duration": int(t.get("duration", 0)),
                "thumb": t.get("album", {}).get("cover_big") or t.get("album", {}).get("cover_medium") or "",
                "preview": t.get("preview") or "",
                "link": f"https://www.deezer.com/track/{t.get('id')}"
            })
        return results
    except Exception as e:
        print(f"get_artist_top_tracks error: {e}", flush=True)
        return []


def get_artist_albums(artist_id: str, limit: int = 15) -> list:
    """Fetch albums for an artist."""
    try:
        r = requests.get(f"{DEEZER_BASE}/artist/{artist_id}/albums", params={"limit": limit}, timeout=8)
        if r.status_code != 200:
            return []
        items = r.json().get("data", [])
        results = []
        for a in items:
            results.append({
                "id": str(a.get("id")),
                "title": a.get("title", "Unknown Album"),
                "artist": a.get("artist", {}).get("name", ""),
                "nb_tracks": int(a.get("nb_tracks", 0)),
                "thumb": a.get("cover_big") or a.get("cover_medium") or "",
                "link": f"https://www.deezer.com/album/{a.get('id')}"
            })
        return results
    except Exception as e:
        print(f"get_artist_albums error: {e}", flush=True)
        return []


def get_album_details(album_id: str) -> dict:
    """Fetch full album info and tracklist."""
    try:
        r = requests.get(f"{DEEZER_BASE}/album/{album_id}", timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        tracks_raw = (data.get("tracks", {}).get("data") or [])
        tracks = []
        for i, t in enumerate(tracks_raw, 1):
            tracks.append({
                "id": str(t.get("id")),
                "title": t.get("title", "Track"),
                "artist": t.get("artist", {}).get("name", data.get("artist", {}).get("name", "")),
                "duration": int(t.get("duration", 0)),
                "track_number": i
            })
        return {
            "id": str(data.get("id")),
            "title": data.get("title", "Album"),
            "artist": data.get("artist", {}).get("name", "Artist"),
            "nb_tracks": len(tracks),
            "release_date": data.get("release_date", ""),
            "thumb": data.get("cover_xl") or data.get("cover_big") or data.get("cover_medium") or "",
            "tracks": tracks
        }
    except Exception as e:
        print(f"get_album_details error: {e}", flush=True)
        return None


def get_track_info(track_id: str) -> dict:
    """Fetch single track info from Deezer with caching."""
    ckey = f"info_{track_id}"
    cached = _get_cache(ckey)
    if cached is not None:
        return cached

    try:
        r = requests.get(f"{DEEZER_BASE}/track/{track_id}", timeout=6)
        if r.status_code != 200:
            return None
        t = r.json()
        info = {
            "id": str(t.get("id")),
            "title": t.get("title", "Title"),
            "artist": t.get("artist", {}).get("name", "Artist"),
            "album": t.get("album", {}).get("title", ""),
            "duration": int(t.get("duration", 0)),
            "thumb": t.get("album", {}).get("cover_xl") or t.get("album", {}).get("cover_big") or t.get("album", {}).get("cover_medium") or "",
            "release_date": t.get("release_date", ""),
            "preview": t.get("preview") or "",
            "isrc": t.get("isrc") or ""
        }
        _set_cache(ckey, info)
        return info
    except Exception as e:
        print(f"get_track_info error: {e}", flush=True)
        return None


def download_track_sync(track_meta: dict, bitrate: str = "320k") -> tuple:
    """
    High-Speed Music Downloader matching RegaBot pipeline:
    1. Concurrently fetches high-res cover art & audio stream via multi-threaded yt-dlp (-N 4).
    2. Uses exact ISRC or official audio query for instant matching.
    3. Single-pass FFmpeg transcode to 128k or 320k MP3 + ID3 tags + embedded square cover.
    Returns (final_audio_path, cover_file_path).
    """
    import glob
    title = track_meta.get("title", "Track")
    artist = track_meta.get("artist", "Artist")
    album = track_meta.get("album", "")
    thumb_url = track_meta.get("thumb", "")
    isrc = track_meta.get("isrc", "")

    safe_name = re.sub(r'[\\/*?:"<>|]', "", f"{artist} - {title}")[:60].strip() or "track"
    ts = int(time.time() * 1000)
    raw_prefix = os.path.join(MUSIC_DIR, f"{safe_name}_{ts}_raw")
    final_audio_path = os.path.join(MUSIC_DIR, f"{safe_name}_{ts}.mp3")
    cover_path = os.path.join(MUSIC_DIR, f"{safe_name}_{ts}.jpg")

    def _dl_cover():
        if not thumb_url:
            return None
        try:
            r = requests.get(thumb_url, timeout=8)
            if r.status_code == 200:
                with open(cover_path, "wb") as f:
                    f.write(r.content)
                return cover_path
        except Exception as e:
            print(f"cover download error: {e}", flush=True)
        return None

    def _dl_audio():
        # Prefer exact ISRC if present (matches official track in 1s)
        if isrc:
            query = f'ytsearch1:"{isrc}"'
        else:
            query = f"ytsearch1:{artist} - {title} audio"
        cmd = [
            sys.executable, "-m", "yt_dlp",
            "--no-playlist",
            "-f", "ba/b",
            "-N", "4",
            "--no-check-certificates",
            "--buffer-size", "1024K",
            "--extractor-args", "youtube:player_client=android",
            "--output", f"{raw_prefix}.%(ext)s",
            query
        ]
        res = subprocess.run(cmd, capture_output=True, timeout=120)
        return res

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f_cov = executor.submit(_dl_cover)
        f_aud = executor.submit(_dl_audio)
        concurrent.futures.wait([f_cov, f_aud])

    candidates = glob.glob(f"{raw_prefix}.*")
    if not candidates:
        # Fallback to general query with android client
        query_fallback = f"ytsearch1:{artist} - {title}"
        cmd = [
            sys.executable, "-m", "yt_dlp",
            "--no-playlist",
            "-f", "ba/b",
            "-N", "4",
            "--no-check-certificates",
            "--buffer-size", "1024K",
            "--extractor-args", "youtube:player_client=android",
            "--output", f"{raw_prefix}.%(ext)s",
            query_fallback
        ]
        subprocess.run(cmd, capture_output=True, timeout=120)
        candidates = glob.glob(f"{raw_prefix}.*")

    if not candidates and os.path.exists("cookies.txt") and os.path.getsize("cookies.txt") > 0:
        # Second fallback: cookies with web client
        cmd = [
            sys.executable, "-m", "yt_dlp",
            "--no-playlist",
            "-f", "ba/b",
            "--cookies", "cookies.txt",
            "--extractor-args", "youtube:player_client=web,mweb",
            "--output", f"{raw_prefix}.%(ext)s",
            f"ytsearch1:{artist} - {title}"
        ]
        subprocess.run(cmd, capture_output=True, timeout=120)
        candidates = glob.glob(f"{raw_prefix}.*")

    if not candidates:
        raise RuntimeError(f"Download failed for {artist} - {title}")

    cand = candidates[0]

    # Single-pass high-quality FFmpeg transcode into requested MP3 bitrate + cover embedding
    ff_cmd = ["ffmpeg", "-y", "-i", cand]
    if os.path.exists(cover_path):
        ff_cmd.extend([
            "-i", cover_path,
            "-map", "0:a", "-map", "1:0",
            "-c:a", "libmp3lame", "-b:a", bitrate,
            "-id3v2_version", "3",
            "-metadata:s:v", "title=Album cover",
            "-metadata:s:v", "comment=Cover (front)",
            "-metadata", f"title={title}",
            "-metadata", f"artist={artist}"
        ])
    else:
        ff_cmd.extend([
            "-c:a", "libmp3lame", "-b:a", bitrate,
            "-metadata", f"title={title}",
            "-metadata", f"artist={artist}"
        ])
    if album:
        ff_cmd.extend(["-metadata", f"album={album}"])
    ff_cmd.append(final_audio_path)

    sub_res = subprocess.run(ff_cmd, capture_output=True, timeout=60)
    if sub_res.returncode == 0 and os.path.exists(final_audio_path):
        try:
            os.remove(cand)
        except Exception:
            pass
        return final_audio_path, cover_path if os.path.exists(cover_path) else None

    # Fallback to mutagen if ffmpeg copy failed
    try:
        from mutagen.easyid3 import EasyID3
        from mutagen.mp3 import MP3
        from mutagen.id3 import ID3, APIC

        audio = MP3(cand, ID3=EasyID3)
        audio["title"] = title
        audio["artist"] = artist
        if album:
            audio["album"] = album
        audio.save()

        if os.path.exists(cover_path):
            audio_full = MP3(cand, ID3=ID3)
            with open(cover_path, "rb") as alb_f:
                audio_full.tags.add(
                    APIC(
                        encoding=3,
                        mime="image/jpeg",
                        type=3,
                        desc="Cover",
                        data=alb_f.read()
                    )
                )
            audio_full.save()
    except Exception as e:
        print(f"mutagen fallback error: {e}", flush=True)

    return cand, cover_path if os.path.exists(cover_path) else None


def get_playlist_details(playlist_id: str) -> dict:
    """Fetch full playlist info and tracklist from Deezer."""
    ckey = f"pl_{playlist_id}"
    cached = _get_cache(ckey)
    if cached is not None:
        return cached

    try:
        r = requests.get(f"{DEEZER_BASE}/playlist/{playlist_id}", timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        tracks_raw = (data.get("tracks", {}).get("data") or [])
        tracks = []
        for i, t in enumerate(tracks_raw, 1):
            tracks.append({
                "id": str(t.get("id")),
                "title": t.get("title", "Track"),
                "artist": t.get("artist", {}).get("name", data.get("creator", {}).get("name", "")),
                "duration": int(t.get("duration", 0)),
                "track_number": i
            })
        res = {
            "id": str(data.get("id")),
            "title": data.get("title", "Playlist"),
            "creator": data.get("creator", {}).get("name", "Creator"),
            "nb_tracks": len(tracks),
            "thumb": data.get("picture_xl") or data.get("picture_big") or data.get("picture_medium") or "",
            "tracks": tracks
        }
        _set_cache(ckey, res)
        return res
    except Exception as e:
        print(f"get_playlist_details error: {e}", flush=True)
        return None


# ----------------- SOUNDCLOUD INTEGRATION -----------------
def search_soundcloud_tracks(query: str, limit: int = 20) -> list:
    """Search tracks on SoundCloud using yt-dlp flat playlist."""
    ckey = f"sc_trk_{query}_{limit}"
    cached = _get_cache(ckey)
    if cached is not None:
        return cached

    import yt_dlp
    ydl_opts = {
        "quiet": True,
        "extract_flat": True,
        "skip_download": True
    }
    results = []
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            data = ydl.extract_info(f"scsearch{limit}:{query}", download=False)
            entries = data.get("entries", [])
            for idx, e in enumerate(entries):
                if not e:
                    continue
                url = e.get("url") or e.get("webpage_url") or ""
                results.append({
                    "id": str(e.get("id") or idx),
                    "title": e.get("title") or "SoundCloud Track",
                    "artist": e.get("uploader") or "SoundCloud Artist",
                    "duration": int(float(e.get("duration") or 0)),
                    "thumb": e.get("thumbnail") or "",
                    "url": url,
                    "views": e.get("view_count") or 0
                })
        _set_cache(ckey, results)
    except Exception as e:
        print(f"search_soundcloud_tracks error: {e}", flush=True)
    return results


def search_soundcloud_artists(query: str, limit: int = 15) -> list:
    """Search artists/users on SoundCloud."""
    ckey = f"sc_art_{query}_{limit}"
    cached = _get_cache(ckey)
    if cached is not None:
        return cached

    tracks = search_soundcloud_tracks(query, limit=limit * 2)
    seen = set()
    artists = []
    for t in tracks:
        art = t.get("artist")
        if art and art not in seen:
            seen.add(art)
            artists.append({
                "id": str(len(artists) + 1),
                "name": art,
                "thumb": t.get("thumb") or "",
                "sample_track": t.get("title")
            })
            if len(artists) >= limit:
                break
    _set_cache(ckey, artists)
    return artists


def search_soundcloud_albums(query: str, limit: int = 15) -> list:
    """Search albums/sets on SoundCloud."""
    ckey = f"sc_alb_{query}_{limit}"
    cached = _get_cache(ckey)
    if cached is not None:
        return cached

    results = []
    import yt_dlp
    ydl_opts = {
        "quiet": True,
        "extract_flat": True,
        "skip_download": True
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            data = ydl.extract_info(f"scsearch{limit}:{query} album", download=False)
            for idx, e in enumerate(data.get("entries", [])):
                if not e:
                    continue
                results.append({
                    "id": str(e.get("id") or idx),
                    "title": e.get("title") or "SoundCloud Album",
                    "artist": e.get("uploader") or "SoundCloud Artist",
                    "thumb": e.get("thumbnail") or "",
                    "url": e.get("url") or e.get("webpage_url") or "",
                    "duration": int(float(e.get("duration") or 0))
                })
        _set_cache(ckey, results)
    except Exception as e:
        print(f"search_soundcloud_albums error: {e}", flush=True)
    return results


def search_soundcloud_playlists(query: str, limit: int = 15) -> list:
    """Search playlists on SoundCloud."""
    return search_soundcloud_albums(f"{query} playlist", limit=limit)


def get_soundcloud_set_details(set_url: str) -> dict:
    """Extract tracks from a SoundCloud set or album."""
    import yt_dlp
    ydl_opts = {"quiet": True, "extract_flat": True, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            data = ydl.extract_info(set_url, download=False)
            entries = data.get("entries") or [data]
            tracks = []
            for idx, e in enumerate(entries, 1):
                tracks.append({
                    "id": str(e.get("id") or idx),
                    "title": e.get("title") or "Track",
                    "artist": e.get("uploader") or data.get("uploader") or "Artist",
                    "duration": int(float(e.get("duration") or 0)),
                    "url": e.get("url") or e.get("webpage_url") or set_url,
                    "thumb": e.get("thumbnail") or data.get("thumbnail") or ""
                })
            return {
                "title": data.get("title") or "SoundCloud Set",
                "artist": data.get("uploader") or "Artist",
                "thumb": data.get("thumbnail") or "",
                "nb_tracks": len(tracks),
                "tracks": tracks
            }
    except Exception as e:
        print(f"get_soundcloud_set_details error: {e}", flush=True)
        return None


def download_soundcloud_track(track_url: str, title: str = "", artist: str = "", bitrate: str = "320k", thumb_url: str = "") -> tuple:
    """Download a SoundCloud track and transcode to 128k/320k MP3 with embedded tags."""
    safe_name = re.sub(r'[\\/*?:"<>|]', '', f"{artist} - {title}")[:60].strip() or "sc_track"
    ts = int(time.time() * 1000)
    raw_prefix = os.path.join(MUSIC_DIR, f"sc_{safe_name}_{ts}_raw")
    final_audio = os.path.join(MUSIC_DIR, f"sc_{safe_name}_{ts}.mp3")
    cover_path = os.path.join(MUSIC_DIR, f"sc_{safe_name}_{ts}.jpg")

    if thumb_url:
        try:
            cr = requests.get(thumb_url, timeout=8)
            if cr.status_code == 200:
                with open(cover_path, "wb") as cf:
                    cf.write(cr.content)
        except Exception:
            pass

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist",
        "-f", "bestaudio/best",
        "-o", f"{raw_prefix}.%(ext)s"
    ]
    if os.path.exists("cookies.txt") and os.path.getsize("cookies.txt") > 0:
        cmd.extend(["--cookies", "cookies.txt"])
    cmd.append(track_url)

    res = subprocess.run(cmd, capture_output=True, timeout=120)
    import glob
    candidates = glob.glob(f"{raw_prefix}.*")
    if not candidates:
        raise RuntimeError("SoundCloud download failed: " + (res.stderr.decode("utf-8", errors="ignore")[-200:] if res.stderr else "Unknown error"))

    cand = candidates[0]

    ff_cmd = ["ffmpeg", "-y", "-i", cand]
    if os.path.exists(cover_path):
        ff_cmd.extend([
            "-i", cover_path,
            "-map", "0:a", "-map", "1:0",
            "-c:a", "libmp3lame", "-b:a", bitrate,
            "-id3v2_version", "3",
            "-metadata", f"title={title}",
            "-metadata", f"artist={artist}"
        ])
    else:
        ff_cmd.extend([
            "-c:a", "libmp3lame", "-b:a", bitrate,
            "-metadata", f"title={title}",
            "-metadata", f"artist={artist}"
        ])
    ff_cmd.append(final_audio)
    sub = subprocess.run(ff_cmd, capture_output=True, timeout=60)
    try:
        os.remove(cand)
    except Exception:
        pass

    if sub.returncode == 0 and os.path.exists(final_audio):
        return final_audio, cover_path if os.path.exists(cover_path) else None
    return cand, cover_path if os.path.exists(cover_path) else None


# ----------------- YOUTUBE MUSIC INTEGRATION -----------------
def search_ytm_tracks(query: str, limit: int = 20) -> list:
    """Search tracks on YouTube Music via yt-dlp."""
    ckey = f"ytm_trk_{query}_{limit}"
    cached = _get_cache(ckey)
    if cached is not None:
        return cached

    import yt_dlp
    ydl_opts = {
        "quiet": True,
        "extract_flat": True,
        "skip_download": True
    }
    results = []
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            data = ydl.extract_info(f"ytsearch{limit}:{query} audio", download=False)
            for idx, e in enumerate(data.get("entries", [])):
                if not e:
                    continue
                vid_id = e.get("id") or str(idx)
                results.append({
                    "id": vid_id,
                    "title": e.get("title") or "YouTube Music Track",
                    "artist": e.get("channel") or e.get("uploader") or "Artist",
                    "duration": int(float(e.get("duration") or 0)),
                    "thumb": f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg",
                    "url": f"https://www.youtube.com/watch?v={vid_id}"
                })
        _set_cache(ckey, results)
    except Exception as e:
        print(f"search_ytm_tracks error: {e}", flush=True)
    return results


def search_ytm_artists(query: str, limit: int = 15) -> list:
    """Search artists on YouTube Music."""
    tracks = search_ytm_tracks(query, limit=limit * 2)
    seen = set()
    artists = []
    for t in tracks:
        art = t.get("artist")
        if art and art not in seen:
            seen.add(art)
            artists.append({
                "id": str(len(artists) + 1),
                "name": art,
                "thumb": t.get("thumb") or "",
                "sample_track": t.get("title")
            })
            if len(artists) >= limit:
                break
    return artists


def search_ytm_albums(query: str, limit: int = 15) -> list:
    """Search full albums on YouTube Music."""
    ckey = f"ytm_alb_{query}_{limit}"
    cached = _get_cache(ckey)
    if cached is not None:
        return cached

    import yt_dlp
    ydl_opts = {
        "quiet": True,
        "extract_flat": True,
        "skip_download": True
    }
    results = []
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            data = ydl.extract_info(f"ytsearch{limit}:{query} full album", download=False)
            for idx, e in enumerate(data.get("entries", [])):
                if not e:
                    continue
                vid_id = e.get("id") or str(idx)
                results.append({
                    "id": vid_id,
                    "title": e.get("title") or "YouTube Music Album",
                    "artist": e.get("channel") or e.get("uploader") or "Artist",
                    "duration": int(float(e.get("duration") or 0)),
                    "thumb": f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg",
                    "url": f"https://www.youtube.com/watch?v={vid_id}"
                })
        _set_cache(ckey, results)
    except Exception as e:
        print(f"search_ytm_albums error: {e}", flush=True)
    return results


def search_ytm_playlists(query: str, limit: int = 15) -> list:
    """Search playlists on YouTube Music."""
    return search_ytm_albums(f"{query} playlist", limit=limit)


def download_ytm_track(video_id_or_url: str, title: str = "", artist: str = "", bitrate: str = "320k", thumb_url: str = "") -> tuple:
    """Download a YouTube Music track and transcode to 128k/320k MP3."""
    url = f"https://www.youtube.com/watch?v={video_id_or_url}" if not video_id_or_url.startswith("http") else video_id_or_url
    safe_name = re.sub(r'[\\/*?:"<>|]', '', f"{artist} - {title}")[:60].strip() or "ytm_track"
    ts = int(time.time() * 1000)
    raw_prefix = os.path.join(MUSIC_DIR, f"ytm_{safe_name}_{ts}_raw")
    final_audio = os.path.join(MUSIC_DIR, f"ytm_{safe_name}_{ts}.mp3")
    cover_path = os.path.join(MUSIC_DIR, f"ytm_{safe_name}_{ts}.jpg")

    if thumb_url:
        try:
            cr = requests.get(thumb_url, timeout=8)
            if cr.status_code == 200:
                with open(cover_path, "wb") as cf:
                    cf.write(cr.content)
        except Exception:
            pass

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist",
        "-f", "ba/b",
        "-N", "4",
        "--no-check-certificates",
        "--extractor-args", "youtube:player_client=android",
        "--output", f"{raw_prefix}.%(ext)s",
        url
    ]
    res = subprocess.run(cmd, capture_output=True, timeout=120)
    import glob
    candidates = glob.glob(f"{raw_prefix}.*")
    if not candidates and os.path.exists("cookies.txt") and os.path.getsize("cookies.txt") > 0:
        cmd_cookie = [
            sys.executable, "-m", "yt_dlp",
            "--no-playlist",
            "-f", "ba/b",
            "--cookies", "cookies.txt",
            "--extractor-args", "youtube:player_client=web,mweb",
            "--output", f"{raw_prefix}.%(ext)s",
            url
        ]
        res = subprocess.run(cmd_cookie, capture_output=True, timeout=120)
        candidates = glob.glob(f"{raw_prefix}.*")

    if not candidates:
        raise RuntimeError("YouTube Music download failed: " + (res.stderr.decode("utf-8", errors="ignore")[-200:] if res.stderr else "Unknown error"))

    cand = candidates[0]

    ff_cmd = ["ffmpeg", "-y", "-i", cand]
    if os.path.exists(cover_path):
        ff_cmd.extend([
            "-i", cover_path,
            "-map", "0:a", "-map", "1:0",
            "-c:a", "libmp3lame", "-b:a", bitrate,
            "-id3v2_version", "3",
            "-metadata", f"title={title}",
            "-metadata", f"artist={artist}"
        ])
    else:
        ff_cmd.extend([
            "-c:a", "libmp3lame", "-b:a", bitrate,
            "-metadata", f"title={title}",
            "-metadata", f"artist={artist}"
        ])
    ff_cmd.append(final_audio)
    sub = subprocess.run(ff_cmd, capture_output=True, timeout=60)
    try:
        os.remove(cand)
    except Exception:
        pass

    if sub.returncode == 0 and os.path.exists(final_audio):
        return final_audio, cover_path if os.path.exists(cover_path) else None
    return cand, cover_path if os.path.exists(cover_path) else None

