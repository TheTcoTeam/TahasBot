#!/bin/sh
# PO-token server for yt-dlp (YouTube's bot check). Optional: the bot still
# runs if it fails to start, YouTube downloads just lose that fallback.
if [ "${ENABLE_POT_SERVER:-true}" = "true" ] && [ -f /opt/pot/build/main.js ]; then
    (cd /opt/pot && node --max-old-space-size=96 build/main.js --host 127.0.0.1 --port 4416 >/tmp/pot.log 2>&1 &)
fi
exec python bot.py
