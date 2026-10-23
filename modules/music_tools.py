# -*- coding: utf-8 -*-
"""
Section 2: Music & Audio Tools Engine for TMulti Bot.
Handles audio transformations via ffmpeg and mutagen:
- Slowed + Reverb
- Nightcore
- 8D Audio
- Bass Boost
- Vocal Remover / Karaoke
- Speed & Pitch Shifting
- Audio Trimming
- Format Conversion (MP3 320k, FLAC, M4A, WAV)
- Tag & Cover Editor
"""

import os
import time
import subprocess
import traceback
from pathlib import Path

MUSIC_TOOLS_DIR = "music_downloads"
os.makedirs(MUSIC_TOOLS_DIR, exist_ok=True)

AUDIO_FILTERS = {
    "slow": {
        "label_fa": "🌙 اسلو + ریورب",
        "label_en": "🌙 Slowed + Reverb",
        "filter": "asetrate=44100*0.85,aresample=44100,aecho=0.8:0.88:60:0.4",
        "suffix": "_slowed"
    },
    "night": {
        "label_fa": "⚡ نایتکور",
        "label_en": "⚡ Nightcore",
        "filter": "asetrate=44100*1.25,aresample=44100,atempo=1.02",
        "suffix": "_nightcore"
    },
    "eight": {
        "label_fa": "🎧 هشت‌بعدی (8D)",
        "label_en": "🎧 8D Audio",
        "filter": "apulsator=hz=0.09,aecho=0.8:0.9:200:0.2",
        "suffix": "_8d"
    },
    "bass": {
        "label_fa": "🔊 بیس بوست",
        "label_en": "🔊 Bass Boost",
        "filter": "equalizer=f=60:width_type=h:width=50:g=10,bass=g=8",
        "suffix": "_bass"
    },
    "vocal": {
        "label_fa": "🎙 کارائوکه",
        "label_en": "🎙 Karaoke",
        "filter": "pan=stereo|c0=c0-c1|c1=c1-c0",
        "suffix": "_instrumental"
    }
}


def probe_audio_info(file_path: str) -> dict:
    """Read duration, bitrate, artist, title via ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", file_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        import json
        data = json.loads(res.stdout or "{}")
        fmt = data.get("format", {})
        tags = fmt.get("tags", {})
        return {
            "duration": float(fmt.get("duration", 0)),
            "bitrate": int(fmt.get("bit_rate", 0)),
            "title": tags.get("title") or tags.get("TITLE") or Path(file_path).stem,
            "artist": tags.get("artist") or tags.get("ARTIST") or "Unknown Artist",
            "album": tags.get("album") or tags.get("ALBUM") or ""
        }
    except Exception:
        return {"duration": 0, "bitrate": 0, "title": Path(file_path).stem, "artist": "Unknown Artist", "album": ""}


def process_audio_filter(input_path: str, effect_code: str) -> str:
    """Apply an ffmpeg filter and return path to the output audio."""
    conf = AUDIO_FILTERS.get(effect_code)
    if not conf:
        raise ValueError(f"Unknown effect: {effect_code}")

    ext = Path(input_path).suffix or ".mp3"
    out_filename = f"{Path(input_path).stem}{conf['suffix']}_{int(time.time())}.mp3"
    output_path = os.path.join(MUSIC_TOOLS_DIR, out_filename)

    cmd = [
        "ffmpeg", "-y", "-i", input_path, "-vn",
        "-af", conf["filter"],
        "-c:a", "libmp3lame", "-b:a", "320k",
        output_path
    ]
    res = subprocess.run(cmd, capture_output=True, timeout=180)
    if res.returncode != 0:
        err = res.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"FFmpeg error: {err[-300:]}")

    return output_path


def change_audio_speed(input_path: str, speed_factor: float) -> str:
    """Adjust audio speed (0.5x to 2.0x)."""
    out_filename = f"{Path(input_path).stem}_speed{speed_factor}x_{int(time.time())}.mp3"
    output_path = os.path.join(MUSIC_TOOLS_DIR, out_filename)

    cmd = [
        "ffmpeg", "-y", "-i", input_path, "-vn",
        "-filter:a", f"atempo={speed_factor}",
        "-c:a", "libmp3lame", "-b:a", "320k",
        output_path
    ]
    res = subprocess.run(cmd, capture_output=True, timeout=180)
    if res.returncode != 0:
        err = res.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"FFmpeg speed error: {err[-300:]}")

    return output_path


def trim_audio_file(input_path: str, start_sec: str, end_sec: str) -> str:
    """Trim audio between start_sec and end_sec."""
    out_filename = f"{Path(input_path).stem}_trimmed_{int(time.time())}.mp3"
    output_path = os.path.join(MUSIC_TOOLS_DIR, out_filename)

    cmd = [
        "ffmpeg", "-y", "-ss", str(start_sec), "-to", str(end_sec),
        "-i", input_path, "-vn",
        "-c:a", "libmp3lame", "-b:a", "320k",
        output_path
    ]
    res = subprocess.run(cmd, capture_output=True, timeout=120)
    if res.returncode != 0:
        err = res.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"FFmpeg trim error: {err[-300:]}")

    return output_path


def convert_audio_format(input_path: str, target_fmt: str) -> str:
    """Convert audio to mp3 (320k), flac, m4a, or wav."""
    target_fmt = target_fmt.lower().strip(".")
    out_filename = f"{Path(input_path).stem}_{int(time.time())}.{target_fmt}"
    output_path = os.path.join(MUSIC_TOOLS_DIR, out_filename)

    cmd = ["ffmpeg", "-y", "-i", input_path, "-vn"]
    if target_fmt == "mp3":
        cmd.extend(["-c:a", "libmp3lame", "-b:a", "320k"])
    elif target_fmt == "flac":
        cmd.extend(["-c:a", "flac"])
    elif target_fmt == "m4a":
        cmd.extend(["-c:a", "aac", "-b:a", "256k"])
    elif target_fmt == "wav":
        cmd.extend(["-c:a", "pcm_s16le"])
    else:
        cmd.extend(["-c:a", "libmp3lame", "-b:a", "320k"])

    cmd.append(output_path)
    res = subprocess.run(cmd, capture_output=True, timeout=180)
    if res.returncode != 0:
        err = res.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"FFmpeg convert error: {err[-300:]}")

    return output_path


def update_audio_metadata(input_path: str, title: str = None, artist: str = None, album: str = None, cover_path: str = None) -> bool:
    """Update ID3 tags and embed cover art using mutagen."""
    try:
        from mutagen.easyid3 import EasyID3
        from mutagen.mp3 import MP3
        from mutagen.id3 import ID3, APIC

        audio = MP3(input_path, ID3=EasyID3)
        if title:
            audio["title"] = title
        if artist:
            audio["artist"] = artist
        if album:
            audio["album"] = album
        audio.save()

        if cover_path and os.path.exists(cover_path):
            audio_full = MP3(input_path, ID3=ID3)
            with open(cover_path, "rb") as alb:
                audio_full.tags.add(
                    APIC(
                        encoding=3,
                        mime="image/jpeg",
                        type=3,
                        desc="Cover",
                        data=alb.read()
                    )
                )
            audio_full.save()
        return True
    except Exception as e:
        print(f"update_audio_metadata error: {e}", flush=True)
        return False
