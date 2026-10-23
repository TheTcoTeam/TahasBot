# Render web service: the bot itself plus the light jobs (Instagram, TikTok,
# all music downloads, AI chat, image/audio tools). Heavy jobs run on GitHub
# Actions (see .github/workflows).

# Prebuilt YouTube PO-token server (same image the VPS ran as a sidecar).
FROM brainicism/bgutil-ytdlp-pot-provider:2.0.2 AS pot

FROM python:3.11-slim-bookworm

# ffmpeg          - audio/video conversion and effects
# fonts-noto-core - Persian/Arabic glyphs for image watermarks
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fonts-noto-core ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Node runtime + built PO-token server, started next to the bot by start.sh.
COPY --from=pot /usr/local/bin/node /usr/local/bin/node
COPY --from=pot /app /opt/pot

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -U "yt-dlp[default] @ https://github.com/yt-dlp/yt-dlp/archive/master.zip"

COPY . .
RUN mkdir -p music_downloads media_downloads jobs video_edits downloads && chmod +x start.sh

ENV PYTHONUNBUFFERED=1 PORT=10000
EXPOSE 10000

CMD ["./start.sh"]
