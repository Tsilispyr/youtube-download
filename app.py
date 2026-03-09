import os
import json
import queue
import threading
import time
import re
import uuid
import sys
import platform
from flask import Flask, render_template, request, jsonify, Response
import yt_dlp
import yt_dlp.utils


app = Flask(__name__)

def get_default_download_dir() -> str:
    # 1. Android check
    if "ANDROID_STORAGE" in os.environ or os.path.isdir("/sdcard/Download"):
        return "/sdcard/Download"
        
    # 2. iOS / iCloud check
    is_ios = sys.platform == "ios" or (
        sys.platform == "darwin" and 
        platform.machine().lower().startswith(("iphone", "ipad", "ipod")))
    
    icloud_docs = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs")
    if is_ios or os.path.isdir(icloud_docs):
        if os.path.isdir(icloud_docs):
            return os.path.join(icloud_docs, "Downloads")
        return os.path.expanduser("~/Documents/Downloads")
        
    # 3. Default for PC/Docker
    return "/downloads"

DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", get_default_download_dir())
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Optional: path to a cookies.txt file exported from your browser.
# Set via environment variable, e.g.:  COOKIES_FILE=/app/cookies.txt
# Or place a file named "cookies.txt" next to app.py and it will be auto-detected.
_COOKIES_FILE_ENV = os.environ.get("COOKIES_FILE", "")
_COOKIES_FILE_AUTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")

def get_cookies_file() -> str | None:
    """Return path to a valid cookies.txt, or None if not found."""
    if _COOKIES_FILE_ENV and os.path.isfile(_COOKIES_FILE_ENV):
        return _COOKIES_FILE_ENV
    if os.path.isfile(_COOKIES_FILE_AUTO):
        return _COOKIES_FILE_AUTO
    return None

def cookies_opts() -> dict:
    """
    Return yt-dlp cookie options.
    Priority:
      1. cookies.txt file (most reliable for servers/Docker)
      2. Browser cookie extraction (works on desktop when a browser is logged in)
    """
    f = get_cookies_file()
    if f:
        return {"cookiefile": f}
    
    # Try browsers in order; yt-dlp will silently skip unavailable ones.
    # Using a list of tuples so we return the first successful one at runtime.
    # We just pass all browsers and let yt-dlp handle it, but yt-dlp only
    # accepts one browser at a time, so we try each and return on first success.
    for browser in ("chrome", "firefox", "edge", "brave", "chromium", "safari"):
        return {"cookiesfrombrowser": (browser,)}
    return {}


# Per-session download queues
download_queues: dict[str, queue.Queue] = {}


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def normalise_url(url: str) -> str:
    """Strip whitespace; yt-dlp handles music.youtube.com natively."""
    return url.strip()

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

@app.route("/")
def index():
    templates_dir = os.path.join(app.root_path, app.template_folder)
    for f in os.listdir(templates_dir):
        if f.endswith(".html") and os.path.isfile(os.path.join(templates_dir, f)):
            return render_template(f)
    return "No HTML template found in the templates directory.", 404


@app.route("/api/cookies-status")
def cookies_status():
    """Let the frontend know whether cookies are configured."""
    f = get_cookies_file()
    if f:
        return jsonify({"configured": True, "source": "file", "path": f})
    return jsonify({"configured": False, "source": None})


@app.route("/api/info", methods=["POST"])
def get_info():
    data = request.get_json(force=True)
    url = normalise_url(data.get("url", ""))
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "ignoreerrors": True,
        # FIX: Use web_creator + mweb instead of android (android is heavily flagged by YouTube)
        "extractor_args": {
            "youtube": {
                "player_client": ["web_creator", "web", "mweb"],
                "player_skip": ["webpage"],
            }
        },
        # FIX: Add cookie support
        **cookies_opts(),
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if info is None:
            return jsonify({"error": "Could not fetch info. The video may be unavailable or age-restricted. Try adding a cookies.txt file next to app.py."}), 400

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
                tracks.append({
                    "id": vid_id,
                    "title": entry.get("title") or vid_id,
                    "url": vid_url,
                    "duration": fmt_duration(entry.get("duration")),
                    "thumbnail": entry.get("thumbnail") or "",
                    "uploader": entry.get("uploader") or entry.get("channel") or "",
                })
            return jsonify({
                "type": "playlist",
                "title": info.get("title", "Playlist"),
                "uploader": info.get("uploader") or info.get("channel") or "",
                "count": len(tracks),
                "tracks": tracks,
            })
        else:
            # Single video
            # FIX: For single videos, extract_flat may leave out full info.
            # Re-extract without extract_flat to get proper metadata.
            vid_id = info.get("id", "")
            tracks.append({
                "id": vid_id,
                "title": info.get("title") or vid_id,
                "url": url,
                "duration": fmt_duration(info.get("duration")),
                "thumbnail": info.get("thumbnail") or "",
                "uploader": info.get("uploader") or info.get("channel") or "",
            })
            return jsonify({
                "type": "single",
                "title": info.get("title") or vid_id,
                "count": 1,
                "tracks": tracks,
            })

    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        if "Sign in" in msg or "bot" in msg.lower():
            msg = ("YouTube requires authentication. "
                   "Export cookies.txt from your browser (while logged into YouTube) "
                   "and place it next to app.py, or set the COOKIES_FILE env variable.")
        return jsonify({"error": msg}), 400
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {e}"}), 500


@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.get_json(force=True)
    urls = data.get("urls", [])
    titles = data.get("titles", {})
    playlist_title = data.get("playlist_title", "").strip()
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
                        q.put({
                            "type": "progress",
                            "url": u,
                            "title": t,
                            "percent": pct,
                            "speed": speed,
                            "eta": eta,
                        })
                    elif status == "finished":
                        q.put({"type": "converting", "url": u, "title": t})
                    elif status == "error":
                        q.put({
                            "type": "track_error",
                            "url": u,
                            "title": t,
                            "message": str(d.get("error", "Unknown")),
                        })
                return hook

            out_dir = DOWNLOAD_DIR
            if playlist_title:
                safe_title = yt_dlp.utils.sanitize_filename(playlist_title, is_id=False)
                if safe_title:
                    out_dir = os.path.join(DOWNLOAD_DIR, safe_title)
                    os.makedirs(out_dir, exist_ok=True)

            ydl_opts = {
                "format": "bestaudio/best",
                "writethumbnail": True,
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
                    {
                        "key": "EmbedThumbnail",
                        "already_have_thumbnail": False,
                    },
                ],
                "outtmpl": os.path.join(out_dir, "%(title)s.%(ext)s"),
                "progress_hooks": [make_hook(url, title)],
                "quiet": True,
                "no_warnings": True,
                "ignoreerrors": True,
                # FIX: Use web_creator + mweb instead of android
                "extractor_args": {
                    "youtube": {
                        "player_client": ["web_creator", "web", "mweb"],
                        "player_skip": ["webpage"],
                    }
                },
                # FIX: Add cookie support
                **cookies_opts(),
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

        download_queues.pop(session_id, None)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/files")
def list_files():
    files = []
    for root, _, filenames in os.walk(DOWNLOAD_DIR):
        for f in filenames:
            if f.endswith(".mp3"):
                path = os.path.join(root, f)
                rel_path = os.path.relpath(path, DOWNLOAD_DIR)
                files.append({
                    "name": rel_path.replace(os.sep, "/"),
                    "size": os.path.getsize(path),
                    "mtime": os.path.getmtime(path),
                })
    files.sort(key=lambda x: x["name"].lower())
    return jsonify(files)


if __name__ == "__main__":
    # Render.com (and other PaaS) set PORT dynamically; default to 5000 locally
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
