# YT → MP3 Downloader

A local web app to download MP3s from YouTube and YouTube Music — videos, single tracks, or full playlists.

## Quick Start

### With Docker

```bash
# Build and start
docker compose up --build

# Then open in browser:
# http://localhost:5000
```

MP3 files are saved to `./downloads/` on your machine.

---

### Without Docker (Python 3.10+)

**Prerequisites:** Python 3.10+, `ffmpeg` installed on your system.

```bash
# Install ffmpeg (if not already installed)
# macOS:
brew install ffmpeg
# Ubuntu/Debian:
sudo apt install ffmpeg
# Windows: https://ffmpeg.org/download.html

# Install Python deps
pip install -r requirements.txt

# Run the app
DOWNLOAD_DIR=./downloads python app.py
```

Then open `http://localhost:5000`

---

## Supported URL formats

| Type | Example |
|------|---------|
| YouTube video | `https://www.youtube.com/watch?v=WMG2EEregSk` |
| YouTube playlist | `https://youtube.com/playlist?list=PL0tcET7ZkzP...` |
| YT Music track | `https://music.youtube.com/watch?v=MCZbjClHc18` |
| YT Music playlist | `https://music.youtube.com/playlist?list=PL0tcET7...` |
| YT Music track+list | `https://music.youtube.com/watch?v=...&list=PL0tcET7...` |

## Features

- Paste any YouTube or YouTube Music URL
- Playlists load all tracks with thumbnails, duration, uploader
- Select individual tracks or select all
- Real-time download progress per track
- MP3 at 320kbps with embedded metadata
- Files listed after download

## Notes

- Downloads are saved to `./downloads/` (Docker) or `DOWNLOAD_DIR` (local)
- yt-dlp is used under the hood — keep it updated with `pip install -U yt-dlp`
- To update yt-dlp inside Docker: `docker compose build --no-cache`


- On app.py, # DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/sdcard/Download") #uncomment to download on android /sdcard/ is misleading — it's not the SD card. On Android, /sdcard/ is a symlink that points to the phone's internal storage.

Future changes include playlists downloads in folders for each playlist and addition for photos to albums, playlists and standalone songs.