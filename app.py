import os
import json
import queue
import threading
import time
import re
import uuid
from flask import Flask, render_template, request, jsonify, Response

import yt_dlp

app = Flask(__name__)

DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Per-session download queues
download_queues: dict[str, queue.Queue] = {}


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────
# Note: yt-dlp can handle music.youtube.com URLs directly, so we don't need to rewrite them.

def normalise_url(url: str) -> str:
    """Normalise YouTube Music URLs to plain YouTube when needed."""
    url = url.strip()
    # yt-dlp handles music.youtube.com natively – just return as-is
    return url

# Format seconds as H:MM:SS or M:SS
def fmt_duration(seconds) -> str:
    if not seconds:
        return ""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ──────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────
# The frontend is very minimal and just serves the static HTML/JS/CSS. All the logic is in the API routes.
@app.route("/")
def index():
    return render_template("index.html")

# This route accepts a YouTube URL (video or playlist) and returns structured info about it, including track details for playlists.
@app.route("/api/info", methods=["POST"])
def get_info():
    data = request.get_json(force=True)
    url = normalise_url(data.get("url", ""))
    if not url:
        return jsonify({"error": "No URL provided"}), 400

# We use yt-dlp to extract info without downloading. For playlists, we get all entries; for single videos, we just get the one.
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "ignoreerrors": True,
    }
# We handle errors gracefully, returning JSON error messages for known issues and a generic message for unexpected exceptions.
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if info is None:
            return jsonify({"error": "Could not fetch info. Check the URL and try again."}), 400

        tracks = []

        if "entries" in info:
            # Playlist
            for entry in (info["entries"] or []):
                if not entry:
                    continue
                vid_id = entry.get("id") or entry.get("url", "")
                vid_url = (
                    entry.get("url")
                    or (f"https://www.youtube.com/watch?v={vid_id}" if vid_id else None)
                )
                if not vid_url:
                    continue
                tracks.append(
                    {
                        "id": vid_id,
                        "title": entry.get("title") or vid_id,
                        "url": vid_url,
                        "duration": fmt_duration(entry.get("duration")),
                        "thumbnail": entry.get("thumbnail") or "",
                        "uploader": entry.get("uploader") or entry.get("channel") or "",
                    }
                )
            return jsonify(
                {
                    "type": "playlist",
                    "title": info.get("title", "Playlist"),
                    "uploader": info.get("uploader") or info.get("channel") or "",
                    "count": len(tracks),
                    "tracks": tracks,
                }
            )
        else:
            # Single video
            vid_id = info.get("id", "")
            tracks.append(
                {
                    "id": vid_id,
                    "title": info.get("title") or vid_id,
                    "url": url,
                    "duration": fmt_duration(info.get("duration")),
                    "thumbnail": info.get("thumbnail") or "",
                    "uploader": info.get("uploader") or info.get("channel") or "",
                }
            )
            return jsonify(
                {
                    "type": "single",
                    "title": info.get("title") or vid_id,
                    "count": 1,
                    "tracks": tracks,
                }
            )
#
    except yt_dlp.utils.DownloadError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {e}"}), 500

# This route accepts a list of URLs and starts downloading them.
@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.get_json(force=True)
    urls = data.get("urls", [])
    titles = data.get("titles", {})   # {url: title}
    session_id = data.get("session_id") or str(uuid.uuid4())

    if not urls:
        return jsonify({"error": "No URLs provided"}), 400

    q: queue.Queue = queue.Queue()
    download_queues[session_id] = q

    def do_download():
        for url in urls:
            title = titles.get(url, url)

            def make_hook(u, t):
                def hook(d):
                    status = d.get("status")
                    if status == "downloading":
                        pct_str = d.get("_percent_str", "").strip().replace("%", "")
                        try:
                            pct = float(pct_str)
                        except ValueError:
                            pct = 0
                        speed = d.get("_speed_str", "").strip()
                        eta = d.get("_eta_str", "").strip()
                        q.put(
                            {
                                "type": "progress",
                                "url": u,
                                "title": t,
                                "percent": pct,
                                "speed": speed,
                                "eta": eta,
                            }
                        )
                    elif status == "finished":
                        q.put({"type": "converting", "url": u, "title": t})
                    elif status == "error":
                        q.put(
                            {
                                "type": "track_error",
                                "url": u,
                                "title": t,
                                "message": str(d.get("error", "Unknown")),
                            }
                        )

                return hook

            ydl_opts = {
                "format": "bestaudio/best",
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "320",
                    },
                    {
                        "key": "FFmpegMetadata",
                        "add_metadata": True,
                    },
                ],
                "outtmpl": os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s"),
                "progress_hooks": [make_hook(url, title)],
                "quiet": True,
                "no_warnings": True,
                "ignoreerrors": True,
            }

            try:
                q.put({"type": "started", "url": url, "title": title})
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                q.put({"type": "track_done", "url": url, "title": title})
            except Exception as e:
                q.put({"type": "track_error", "url": url, "title": title, "message": str(e)})

        q.put({"type": "all_done"})

    t = threading.Thread(target=do_download, daemon=True)
    t.start()

    return jsonify({"session_id": session_id})

# This route implements Server-Sent Events to stream download progress updates to the frontend in real-time.
@app.route("/api/progress/<session_id>")
def progress_stream(session_id):
    def generate():
        q = download_queues.get(session_id)
        if not q:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Session not found'})}\n\n"
            return

        while True:
            try:
                msg = q.get(timeout=25)
                yield f"data: {json.dumps(msg)}\n\n"
                if msg["type"] == "all_done":
                    break
            except queue.Empty:
                yield "data: {\"type\":\"keepalive\"}\n\n"

        # Clean up
        download_queues.pop(session_id, None)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

# This route lists all downloaded MP3 files with their name, size, and modification time.
@app.route("/api/files")
def list_files():
    files = []
    for f in sorted(os.listdir(DOWNLOAD_DIR)):
        if f.endswith(".mp3"):
            path = os.path.join(DOWNLOAD_DIR, f)
            files.append(
                {
                    "name": f,
                    "size": os.path.getsize(path),
                    "mtime": os.path.getmtime(path),
                }
            )
    return jsonify(files)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
