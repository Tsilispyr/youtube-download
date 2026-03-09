import os
import json
import queue
import threading
import uuid
import sys
import platform
import tempfile
import shutil
import zipfile
import urllib.request
from flask import Flask, render_template, request, jsonify, Response, send_file, after_this_request
import yt_dlp
import yt_dlp.utils


app = Flask(__name__, static_folder="static")


# ──────────────────────────────────────────────
# Temp storage — files are served to devices then deleted, nothing persists
# ──────────────────────────────────────────────

BASE_TEMP_DIR = os.path.join(tempfile.gettempdir(), "yt-mp3-sessions")
os.makedirs(BASE_TEMP_DIR, exist_ok=True)

# {session_id: {"queue": Queue, "files": {url: filepath}, "session_dir": str}}
sessions: dict[str, dict] = {}


# ──────────────────────────────────────────────
# yt-dlp options
# ──────────────────────────────────────────────

def base_ydl_opts() -> dict:
    """
    Shared yt-dlp options for both info and download.
    Bot detection is handled automatically by bgutil-ytdlp-pot-provider plugin,
    which reads BGU_POT_SERVER_HOST from the environment (set in Dockerfile/render.yaml).
    """
    return {
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "cookies": False,
        "extractor_args": {
            "youtube": {
                "player_client": ["web", "android"],
            }
        },
    }


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def normalise_url(url: str) -> str:
    return url.strip()


def fmt_duration(seconds) -> str:
    if not seconds:
        return ""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def fetch_cover(thumbnail_url: str, dest_path: str) -> bool:
    """Download a thumbnail image to dest_path. Returns True on success."""
    if not thumbnail_url:
        return False
    try:
        req = urllib.request.Request(
            thumbnail_url,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
        with open(dest_path, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False


def cleanup_session(session_id: str):
    """Delete the session temp dir and remove from sessions dict."""
    session = sessions.get(session_id)
    if not session:
        return
    session_dir = session.get("session_dir")
    if session_dir and os.path.isdir(session_dir):
        shutil.rmtree(session_dir, ignore_errors=True)
    sessions.pop(session_id, None)


# ──────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────

@app.route("/favicon.ico")
def favicon():
    # Avoid noisy 404s in the browser devtools.
    return app.send_static_file("favicon.ico")


@app.route("/")
def index():
    """
    Auto-serves whichever .html file exists in templates/.
    Drop in a new file to swap the UI — no code change needed.
    """
    templates_dir = os.path.join(app.root_path, app.template_folder)
    for f in sorted(os.listdir(templates_dir)):
        if f.endswith(".html") and os.path.isfile(os.path.join(templates_dir, f)):
            return render_template(f)
    return "No HTML template found in the templates/ directory.", 404


@app.route("/api/info", methods=["POST"])
def get_info():
    data = request.get_json(force=True)
    url = normalise_url(data.get("url", ""))
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    ydl_opts = {
        **base_ydl_opts(),
        "extract_flat": "in_playlist",
        "skip_download": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if info is None:
            return jsonify({
                "error": "Could not fetch info. The video may be unavailable, private, or age-restricted."
            }), 400

        tracks = []

        if "entries" in info:
            # Playlist — get cover from playlist thumbnail, fall back to first track
            playlist_thumbnail = info.get("thumbnail") or ""
            first_entry_thumb = ""
            for entry in (info["entries"] or []):
                if entry and entry.get("thumbnail"):
                    first_entry_thumb = entry["thumbnail"]
                    break

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
                "thumbnail": playlist_thumbnail or first_entry_thumb,
                "count": len(tracks),
                "tracks": tracks,
            })

        else:
            # Single video
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
                "thumbnail": info.get("thumbnail") or "",
                "count": 1,
                "tracks": tracks,
            })

    except yt_dlp.utils.DownloadError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        # Environment/config issues like cookiefile configured but missing should be a 400,
        # not a 500 (it's not a server crash, it's a request that can't be fulfilled here).
        msg = str(e)
        if "failed to load cookies" in msg.lower():
            return jsonify({"error": msg}), 400
        return jsonify({"error": f"Unexpected error: {e}"}), 500


@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.get_json(force=True)
    urls = data.get("urls", [])
    titles = data.get("titles", {})
    playlist_title = data.get("playlist_title", "").strip()
    playlist_thumbnail = data.get("playlist_thumbnail", "").strip()
    is_playlist = bool(playlist_title)
    session_id = data.get("session_id") or str(uuid.uuid4())

    if not urls:
        return jsonify({"error": "No URLs provided"}), 400

    # Isolated temp dir for this session
    session_dir = os.path.join(BASE_TEMP_DIR, session_id)

    # Playlists go into a named subfolder that becomes the zip root
    if is_playlist:
        safe_name = yt_dlp.utils.sanitize_filename(playlist_title, is_id=False) or "playlist"
        music_dir = os.path.join(session_dir, safe_name)
    else:
        music_dir = session_dir

    os.makedirs(music_dir, exist_ok=True)

    q: queue.Queue = queue.Queue()
    sessions[session_id] = {
        "queue": q,
        "files": {},
        "session_dir": session_dir,
    }

    def do_download():
        for url in urls:
            title = titles.get(url, url)
            captured_path: list[str] = []

            def make_hooks(u, t, cap):
                def progress_hook(d):
                    status = d.get("status")
                    if status == "downloading":
                        pct_str = d.get("_percent_str", "").strip().replace("%", "")
                        try:
                            pct = float(pct_str)
                        except ValueError:
                            pct = 0
                        q.put({
                            "type": "progress",
                            "url": u,
                            "title": t,
                            "percent": pct,
                            "speed": d.get("_speed_str", "").strip(),
                            "eta": d.get("_eta_str", "").strip(),
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

                def pp_hook(d):
                    # Capture final .mp3 path after all postprocessors finish
                    if d.get("status") == "finished":
                        fp = d.get("info_dict", {}).get("filepath") or d.get("filepath", "")
                        if fp and fp.endswith(".mp3") and os.path.isfile(fp):
                            cap.clear()
                            cap.append(fp)

                return progress_hook, pp_hook

            progress_hook, pp_hook = make_hooks(url, title, captured_path)

            ydl_opts = {
                **base_ydl_opts(),
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
                "outtmpl": os.path.join(music_dir, "%(title)s.%(ext)s"),
                "progress_hooks": [progress_hook],
                "postprocessor_hooks": [pp_hook],
            }

            try:
                q.put({"type": "started", "url": url, "title": title})
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

                # Locate the final .mp3
                final_path = captured_path[0] if captured_path else None
                if not final_path:
                    known = set(sessions[session_id]["files"].values())
                    for fname in os.listdir(music_dir):
                        if fname.endswith(".mp3"):
                            candidate = os.path.join(music_dir, fname)
                            if candidate not in known:
                                final_path = candidate
                                break

                if final_path and os.path.isfile(final_path):
                    sessions[session_id]["files"][url] = final_path
                    if is_playlist:
                        # Playlist: just report progress, zip comes at the end
                        q.put({"type": "track_done", "url": url, "title": title})
                    else:
                        # Single: serve immediately
                        q.put({
                            "type": "track_done",
                            "url": url,
                            "title": title,
                            "filename": os.path.basename(final_path),
                            "session_id": session_id,
                        })
                else:
                    q.put({
                        "type": "track_error",
                        "url": url,
                        "title": title,
                        "message": "Output file not found after conversion.",
                    })

            except Exception as e:
                q.put({"type": "track_error", "url": url, "title": title, "message": str(e)})

        # ── Playlist: add cover then zip ────────────────────────────────────
        if is_playlist:
            q.put({"type": "zipping", "message": "Creating archive…"})

            if playlist_thumbnail:
                ext = "png" if ".png" in playlist_thumbnail else (
                      "webp" if ".webp" in playlist_thumbnail else "jpg")
                fetch_cover(playlist_thumbnail, os.path.join(music_dir, f"cover.{ext}"))

            safe_name = os.path.basename(music_dir)
            zip_path = os.path.join(session_dir, f"{safe_name}.zip")
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, _, files in os.walk(music_dir):
                    for fname in sorted(files):
                        abs_path = os.path.join(root, fname)
                        arc_name = os.path.relpath(abs_path, session_dir)
                        zf.write(abs_path, arc_name)

            sessions[session_id]["zip_path"] = zip_path
            sessions[session_id]["zip_name"] = f"{safe_name}.zip"

            q.put({
                "type": "zip_ready",
                "session_id": session_id,
                "filename": f"{safe_name}.zip",
            })

        q.put({"type": "all_done", "session_id": session_id})

    threading.Thread(target=do_download, daemon=True).start()
    return jsonify({"session_id": session_id, "is_playlist": is_playlist})


@app.route("/api/serve/<session_id>/<path:filename>")
def serve_file(session_id, filename):
    """Stream a single MP3 to the browser then delete it."""
    session = sessions.get(session_id)
    if not session:
        return jsonify({"error": "Session not found or expired"}), 404

    filepath = None
    for path in session["files"].values():
        if os.path.basename(path) == filename:
            filepath = path
            break

    if not filepath or not os.path.isfile(filepath):
        return jsonify({"error": "File not found"}), 404

    @after_this_request
    def cleanup(response):
        try:
            os.remove(filepath)
        except Exception:
            pass
        # Clean up session dir if empty
        session_dir = session.get("session_dir")
        try:
            if session_dir and os.path.isdir(session_dir):
                if not any(f.endswith(".mp3") for f in os.listdir(session_dir)):
                    cleanup_session(session_id)
        except Exception:
            pass
        return response

    return send_file(
        filepath,
        mimetype="audio/mpeg",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/api/serve-zip/<session_id>")
def serve_zip(session_id):
    """Stream the playlist zip to the browser then delete the entire session."""
    session = sessions.get(session_id)
    if not session:
        return jsonify({"error": "Session not found or expired"}), 404

    zip_path = session.get("zip_path")
    zip_name = session.get("zip_name", "playlist.zip")

    if not zip_path or not os.path.isfile(zip_path):
        return jsonify({"error": "Zip not ready"}), 404

    @after_this_request
    def cleanup(response):
        cleanup_session(session_id)
        return response

    return send_file(
        zip_path,
        mimetype="application/zip",
        as_attachment=True,
        download_name=zip_name,
    )


@app.route("/api/progress/<session_id>")
def progress_stream(session_id):
    def generate():
        session = sessions.get(session_id)
        if not session:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Session not found'})}\n\n"
            return

        q = session["queue"]
        while True:
            try:
                msg = q.get(timeout=25)
                yield f"data: {json.dumps(msg)}\n\n"
                if msg["type"] == "all_done":
                    break
            except queue.Empty:
                yield "data: {\"type\":\"keepalive\"}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    # Render.com injects PORT dynamically; default 5000 for local
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
