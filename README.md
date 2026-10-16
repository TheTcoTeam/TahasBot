# TahasBot - Bale Messenger Bot

High-performance multimedia downloader and assistant bot for **Bale Messenger** (`tapi.bale.ai`).

## Architecture & Zero-Cost Infrastructure
- **Render Web Service (Free Tier):**
  - Runs the main bot process, responds to Bale user commands and menus.
  - Handles lightweight in-process downloads: Spotify (320kbps MP3s), Deezer, Instagram Reels, TikTok videos, SoundCloud, Pinterest.
  - Hosts multimedia editor tools: Audio filters (Slowed+Reverb, Nightcore, 8D, Bass Boost, Vocal Remover) and Image manipulation (Pillow smart compression, HDR filters, format conversion).
  - Listens on `PORT` for 100% reliable Render uptime and health checks (`/`, `/health`).
- **GitHub Actions Runners (Free Tier):**
  - Provisioned automatically via `workflow_dispatch` for heavy YouTube downloads (720p/1080p video muxing, FFmpeg audio conversion).
  - 7 GB RAM / 2 vCPUs compute ensures zero Out-Of-Memory (OOM) risks on Render.
  - Uploads completed videos and audio directly to Bale Messenger.

## Environment Variables
| Variable | Description |
|---|---|
| `BALE_BOT_TOKEN` | Bale Bot API Token (`746005524:...`) |
| `MONGO_URI` | MongoDB Atlas Connection String (`telebot_db`) |
| `GITHUB_TOKEN` | GitHub Personal Access Token (for workflow dispatch) |
| `GITHUB_REPO` | GitHub Repository (`TheTcoTeam/TahasBot`) |
| `PORT` | Web service listening port (default: 10000) |
