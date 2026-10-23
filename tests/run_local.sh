#!/bin/sh
# Starts the bot against the mock Bale API. Secrets come from an env file
# passed as $1 (never committed); test-only overrides follow it.
set -a
[ -n "$1" ] && . "$1"
set +a
export BOT_TOKEN="746005524:test" BALE_API_BASE="http://127.0.0.1:8899" ADMIN_ID=111 \
       MONGO_DB_NAME=tahasbot_bale_test ENABLE_BACKGROUND_WORKER=false GITHUB_TOKEN="${TEST_GITHUB_TOKEN:-}" \
       PORT=8098 FORCE_CHANNELS= REQUIRED_CHANNEL= CHANNEL_INVITE_LINK= MUSIC_CACHE_CHANNEL= PYTHONIOENCODING=utf-8
exec python -u bot.py
