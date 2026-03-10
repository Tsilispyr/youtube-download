# AudioWeb / AudioWorld — Architecture

## Routes

| Path | Description |
|------|-------------|
| `/` | Redirects to `/AudioWeb/yt-download/` |
| `/AudioWeb/yt-download/` | YT → MP3 downloader |
| `/AudioWeb/equalizer/` | Standalone equalizer & visualizer |
| `/AudioWorld/Player/` | Web player with library, equalizer, visualizer |
| `/AudioWeb/register` | User registration |
| `/AudioWeb/login` | Login |
| `/AudioWeb/logout` | Logout |
| `/AudioWeb/verify?token=…` | Email verification |

## Storage (MinIO)

- **Per-user bucket**: `user-{user_id}` — created on registration
- **Playlists**: `{playlist_name}/song.mp3`
- **Single songs**: `Downloads/song.mp3`
- **Wide bucket**: `audioweb-library` — for future cross-user search

## Database (PostgreSQL / SQLite)

- **User**: email, password_hash, display_name, email_verified, minio_bucket
- **Song**: metadata (title, artist, album, duration, object_name, playlist_name)

## Download Flow

1. **Anonymous**: Files downloaded to temp, served to browser, deleted
2. **Logged-in + "Save to library"**: Same as above, plus file uploaded to user's MinIO bucket and Song record inserted

## Tech Stack

- **Backend**: Flask, SQLAlchemy, Flask-Login, Flask-Mail
- **Storage**: MinIO (S3-compatible)
- **Visualizer**: Butterchurn (WebGL Milkdrop-style)
- **Equalizer**: Web Audio API BiquadFilter nodes
