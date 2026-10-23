# -*- coding: utf-8 -*-
"""
Audio file metadata: tags, cover art, file names.

Everything the bot sends as audio goes through here, so a track saved from
Telegram to a phone/PC keeps a real name ("Artist - Title.mp3"), real ID3/MP4
tags and the album cover -- not "S9bCLPwzSC0.m4a" with nothing inside it.

Telegram shows title/performer/thumb from the send_audio() parameters, but
those are NOT written into the file. Players outside Telegram only see what is
inside the file, which is why both have to be done.

No bot globals in here: worker.py imports this too.
"""

import os
import re
import shutil
import subprocess
import tempfile

try:
    from mutagen import File as MutagenFile
    from mutagen.id3 import (ID3, ID3NoHeaderError, TIT2, TPE1, TPE2, TALB,
                             TRCK, TDRC, APIC)
    from mutagen.mp4 import MP4, MP4Cover
    from mutagen.flac import FLAC, Picture
    import base64
    HAVE_MUTAGEN = True
except ImportError:  # pragma: no cover
    HAVE_MUTAGEN = False

FFMPEG = shutil.which("ffmpeg")
AUDIO_EXTS = (".mp3", ".m4a", ".mp4", ".aac", ".ogg", ".opus", ".flac", ".wav", ".webm")


def _clean(s) -> str:
    """Drop lone surrogates / control chars that break Telegram and mutagen."""
    if not s:
        return ""
    s = re.sub(r"[\ud800-\udfff]", "", str(s))
    s = re.sub(r"[\x00-\x1f\x7f]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def safe_filename(artist: str, title: str, ext: str = ".mp3") -> str:
    """'Artist - Title.mp3', safe on Windows/Android/iOS file systems."""
    artist, title = _clean(artist), _clean(title)
    base = f"{artist} - {title}" if artist and title else (title or artist or "track")
    base = re.sub(r'[\\/:*?"<>|]', "", base).strip(" .")
    if len(base) > 54 and artist and title:
        # Telegram rewrites long names to Underscore_Case: keep the main
        # artist only, drop "(feat. ...)" style tails, then cut at a word.
        main = re.split(r",|&| feat\.| ft\.", artist, maxsplit=1)[0].strip()
        base = re.sub(r'[\\/:*?"<>|]', "", f"{main} - {title}").strip(" .")
    if len(base) > 54:
        trimmed = base
        for _ in range(3):  # nested "(... (...))" tails
            trimmed = re.sub(r"\s*[\(\[][^\(\)\[\]]*[\)\]]", "", trimmed)
        trimmed = re.sub(r"[\(\)\[\]]", "", trimmed).strip()
        base = trimmed or base
        if len(base) > 54:
            base = base[:54].rsplit(" ", 1)[0]
    base = base.strip(" .-,") or "track"
    if not ext.startswith("."):
        ext = "." + ext
    return base + ext.lower()


# ---------------------------------------------------------------- cover art
def _is_jpeg(data: bytes) -> bool:
    return bool(data) and data[:2] == b"\xff\xd8"


def to_square_jpeg(data: bytes, size: int = 640, quality: int = 90) -> bytes:
    """Centre-crop any image (jpg/png/webp) to a square JPEG.

    YouTube thumbnails are 16:9 and webp covers are ignored by Telegram, so
    every cover is normalised here before it is embedded or used as thumb.
    """
    if not data:
        return b""
    try:
        from io import BytesIO
        from PIL import Image
        im = Image.open(BytesIO(data)).convert("RGB")
        w, h = im.size
        side = min(w, h)
        im = im.crop(((w - side) // 2, (h - side) // 2, (w - side) // 2 + side, (h - side) // 2 + side))
        if side > size:
            im = im.resize((size, size), Image.LANCZOS)
        out = BytesIO()
        im.save(out, "JPEG", quality=quality, optimize=True)
        return out.getvalue()
    except Exception:
        return data if _is_jpeg(data) else b""


def make_thumb(cover: bytes, path: str) -> str:
    """Telegram audio thumbs must be JPEG, <=320px and <200KB."""
    if not cover:
        return None
    small = to_square_jpeg(cover, size=320, quality=85)
    if not small:
        return None
    with open(path, "wb") as fh:
        fh.write(small)
    return path


def fetch_image(url: str, timeout: int = 10) -> bytes:
    if not url or not str(url).startswith("http"):
        return b""
    try:
        import requests
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        if r.ok and r.content:
            return r.content
    except Exception:
        pass
    return b""


# ---------------------------------------------------------------- tags
def read_tags(path: str) -> dict:
    """{'title','artist','album','year','cover'(bytes)} from any audio file."""
    out = {"title": "", "artist": "", "album": "", "year": "", "cover": b""}
    if not HAVE_MUTAGEN or not path or not os.path.exists(path):
        return out
    try:
        ext = os.path.splitext(path)[1].lower()
        if ext == ".mp3":
            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                return out
            out["title"] = str(tags.get("TIT2") or "")
            out["artist"] = str(tags.get("TPE1") or "")
            out["album"] = str(tags.get("TALB") or "")
            out["year"] = str(tags.get("TDRC") or "")
            pics = tags.getall("APIC")
            if pics:
                out["cover"] = pics[0].data
        elif ext in (".m4a", ".mp4", ".aac"):
            mp4 = MP4(path)
            g = lambda k: (mp4.tags or {}).get(k, [""])[0] if mp4.tags else ""
            out["title"], out["artist"], out["album"], out["year"] = (str(g("\xa9nam")), str(g("\xa9ART")),
                                                                     str(g("\xa9alb")), str(g("\xa9day")))
            covr = (mp4.tags or {}).get("covr") if mp4.tags else None
            if covr:
                out["cover"] = bytes(covr[0])
        else:
            f = MutagenFile(path)
            if f is not None and f.tags:
                g = lambda k: (f.tags.get(k) or [""])[0]
                out["title"], out["artist"], out["album"], out["year"] = g("title"), g("artist"), g("album"), g("date")
                if isinstance(f, FLAC) and f.pictures:
                    out["cover"] = f.pictures[0].data
                else:
                    b64 = (f.tags.get("metadata_block_picture") or [None])[0]
                    if b64:
                        out["cover"] = Picture(base64.b64decode(b64)).data
    except Exception:
        pass
    return out


def write_tags(path: str, title: str = "", artist: str = "", album: str = "", year: str = "",
               track: int = None, total: int = None, cover: bytes = b"") -> bool:
    """Write tags + embedded cover into mp3 / m4a / ogg / opus / flac."""
    if not HAVE_MUTAGEN or not path or not os.path.exists(path):
        return False
    title, artist, album, year = _clean(title), _clean(artist), _clean(album), _clean(year)[:4]
    cover = to_square_jpeg(cover) if cover else b""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".mp3":
            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                tags = ID3()
            if title:
                tags.setall("TIT2", [TIT2(encoding=3, text=title)])
            if artist:
                tags.setall("TPE1", [TPE1(encoding=3, text=artist)])
                tags.setall("TPE2", [TPE2(encoding=3, text=artist.split(",")[0].strip())])
            if album:
                tags.setall("TALB", [TALB(encoding=3, text=album)])
            if year:
                tags.setall("TDRC", [TDRC(encoding=3, text=year)])
            if track:
                tags.setall("TRCK", [TRCK(encoding=3, text=f"{track}/{total}" if total else str(track))])
            if cover:
                tags.delall("APIC")
                tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover))
            tags.save(path, v2_version=3)
            return True
        if ext in (".m4a", ".mp4", ".aac"):
            mp4 = MP4(path)
            if mp4.tags is None:
                mp4.add_tags()
            if title:
                mp4.tags["\xa9nam"] = [title]
            if artist:
                mp4.tags["\xa9ART"] = [artist]
                mp4.tags["aART"] = [artist.split(",")[0].strip()]
            if album:
                mp4.tags["\xa9alb"] = [album]
            if year:
                mp4.tags["\xa9day"] = [year]
            if track:
                mp4.tags["trkn"] = [(int(track), int(total or 0))]
            if cover:
                mp4.tags["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
            mp4.save()
            return True
        if ext in (".ogg", ".opus", ".flac"):
            f = MutagenFile(path)
            if f is None:
                return False
            if f.tags is None:
                f.add_tags()
            for k, v in (("title", title), ("artist", artist), ("album", album), ("date", year)):
                if v:
                    f.tags[k] = [v]
            if track:
                f.tags["tracknumber"] = [str(track)]
            if cover:
                pic = Picture()
                pic.data, pic.type, pic.mime, pic.desc = cover, 3, "image/jpeg", "Cover"
                if isinstance(f, FLAC):
                    f.clear_pictures()
                    f.add_picture(pic)
                else:
                    f.tags["metadata_block_picture"] = [base64.b64encode(pic.write()).decode("ascii")]
            f.save()
            return True
    except Exception as e:
        print(f"write_tags error ({path}): {e}", flush=True)
    return False


def to_mp3(src: str, dst: str, bitrate: str = "320k") -> str:
    """Re-encode to MP3 (ID3 is what every player understands). Returns dst."""
    if src.lower().endswith(".mp3"):
        if src != dst:
            shutil.move(src, dst)
        return dst
    subprocess.run([FFMPEG or "ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vn", "-map", "0:a:0",
                    "-c:a", "libmp3lame", "-b:a", bitrate, "-map_metadata", "-1", dst],
                   check=True, capture_output=True, timeout=600)
    try:
        os.remove(src)
    except OSError:
        pass
    return dst


def probe_duration(path: str) -> int:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1", path],
                           capture_output=True, text=True, timeout=20)
        return int(float(r.stdout.strip() or 0))
    except Exception:
        return 0


# ---------------------------------------------------------------- pyrogram patch
def install_send_audio_patch(client_cls):
    """Safety net for every send_audio() call that uploads a local file.

    - writes the title/performer that the caller passes into the file itself
      (ID3/MP4 tags), plus a cover (caller's thumb, or one copied from
      `tag_source=` -- the file an effect/trim/speed was made from);
    - uses the file's own embedded cover as the Telegram thumb if no thumb
      was passed;
    - names the upload "Artist - Title.ext" instead of the temp file name.

    Sends by file_id are passed through untouched.
    """
    if getattr(client_cls, "_tmulti_audio_patched", False):
        return
    original = client_cls.send_audio

    async def send_audio(self, chat_id, audio, *args, **kwargs):
        tag_source = kwargs.pop("tag_source", None)
        album = kwargs.pop("album", None)
        cleanup = []
        try:
            if isinstance(audio, str) and os.path.isfile(audio):
                title = kwargs.get("title")
                performer = kwargs.get("performer")
                thumb = kwargs.get("thumb")
                existing = read_tags(audio)
                src_tags = read_tags(tag_source) if tag_source else {}
                title = title or existing.get("title") or src_tags.get("title") or os.path.splitext(os.path.basename(audio))[0]
                performer = performer or existing.get("artist") or src_tags.get("artist") or ""
                cover = b""
                if thumb and isinstance(thumb, str) and os.path.isfile(thumb):
                    with open(thumb, "rb") as fh:
                        cover = fh.read()
                cover = cover or existing.get("cover") or src_tags.get("cover") or b""
                write_tags(audio, title=title, artist=performer,
                           album=album or existing.get("album") or src_tags.get("album") or "",
                           year=existing.get("year") or src_tags.get("year") or "",
                           # never replace a real embedded cover with the 320px thumb
                           cover=b"" if existing.get("cover") else cover)
                kwargs["title"] = _clean(title)[:200]
                kwargs["performer"] = _clean(performer)[:200]
                if not thumb and cover:
                    tp = os.path.join(tempfile.gettempdir(), f"tm_thumb_{os.getpid()}_{id(audio)}.jpg")
                    if make_thumb(cover, tp):
                        kwargs["thumb"] = tp
                        cleanup.append(tp)
                if not kwargs.get("file_name"):
                    kwargs["file_name"] = safe_filename(performer, title, os.path.splitext(audio)[1] or ".mp3")
                if not kwargs.get("duration"):
                    kwargs["duration"] = probe_duration(audio)
            return await original(self, chat_id, audio, *args, **kwargs)
        finally:
            for p in cleanup:
                try:
                    os.remove(p)
                except OSError:
                    pass

    client_cls.send_audio = send_audio
    client_cls._tmulti_audio_patched = True
