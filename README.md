# YT → MP3

Download music from YouTube and YouTube Music as high-quality MP3 files — straight to your device.

---

## What it does

Paste a YouTube or YouTube Music link and download the audio as an MP3. Works with single songs and full playlists.

**Single video:**
You get one `.mp3` file saved directly to your device.

**Playlist:**
You get a `.zip` file. Unzip it to find a folder named after the playlist, containing all the songs as MP3s plus a `cover.jpg`.

All files download directly to your device — nothing is kept on the server.

---

## What you get with each MP3

- **320 kbps** audio quality
- **Album art** embedded in the file (shows up in your music player)
- **Metadata** — title, artist, and other info embedded automatically

---

## Supported links

| What you want | Example link format |
|---|---|
| A single YouTube video | `youtube.com/watch?v=...` |
| A YouTube playlist | `youtube.com/playlist?list=...` |
| A YouTube Music song | `music.youtube.com/watch?v=...` |
| A YouTube Music playlist | `music.youtube.com/playlist?list=...` |
| A YouTube Music song from a playlist | `music.youtube.com/watch?v=...&list=...` |

---

## How to use it

1. Copy a YouTube or YouTube Music link
2. Paste it into the box and press **Enter** or click **Fetch**
3. The app loads the song or playlist with titles, thumbnails, and durations
4. Select the tracks you want (or select all)
5. Click **Download MP3**
6. Your browser will prompt you to save the file(s)

For playlists — your browser may ask "allow this site to download multiple files?" — click **Allow**.

---

## Works on

- **Desktop** — Chrome, Firefox, Edge, Safari (Mac)
- **Android** — Chrome, Firefox (files save to your Downloads folder)
- **iPhone / iPad** — Safari (files save to Files app → Downloads)

---

## Tips

**Playlist folder on your device:**
After downloading, unzip the file. You'll get a folder named after the playlist with all the MP3s inside, ready to import into any music app.

**iPhone / iPad:**
After the zip downloads, tap it in Safari's download list → tap the share icon → **Save to Files** or **Import to Music**.

**Android:**
The zip saves to your Downloads folder. Use your file manager to unzip, then import the folder into your music app.

**If a download fails:**
Some videos are region-locked or age-restricted and cannot be downloaded. The app will show an error for those tracks and continue with the rest.

---

## Privacy

- No account required
- No login, no tracking
- Files are deleted from the server the moment your download starts
- Nothing about your downloads is stored or logged




## Quick start (Docker)

```bash
./setup.sh
```

Creates `.env` from `.env.example`, then starts all services. Edit `.env` to set:
- `SECRET_KEY` — random string for session security
- `MAIL_USERNAME`, `MAIL_PASSWORD` — for email verification (Gmail: use an [App Password](https://support.google.com/accounts/answer/185833))
- `APP_BASE_URL` — your app URL (e.g. `http://localhost:5000`)

**URLs:**
- App: http://localhost:5000
- MinIO UI: http://localhost:9001  
  - **Username:** `minioadmin`  
  - **Password:** `minioadmin`

If port 9000 or 9001 is already in use, add to `.env`:
```
MINIO_API_PORT=19000
MINIO_CONSOLE_PORT=19001
```
Then use http://localhost:19001 for the MinIO UI.

Data persists in Docker volumes `postgres_data` and `minio_data`.

---

## Manual Docker start

```bash
cp .env.example .env   # edit .env first
docker compose up --build
```
