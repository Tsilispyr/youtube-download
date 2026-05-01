"""
YT Download blueprint: YouTube/YT Music → MP3 downloader.
Mounted at /AudioWeb/yt-download
"""
import os
import json
import queue
import threading
import uuid
import zipfile
import urllib.request
from flask import Blueprint, render_template, request, jsonify, Response, send_file, after_this_request
from flask_login import current_user
import yt_dlp
import yt_dlp.utils

yt_download_bp = Blueprint("yt_download", __name__, url_prefix="/AudioWeb/yt-download")

# Temp storage for anonymous downloads
import tempfile
import shutil
BASE_TEMP_DIR = os.path.join(tempfile.gettempdir(), "yt-mp3-sessions")
os.makedirs(BASE_TEMP_DIR, exist_ok=True)
sessions: dict = {}


def base_ydl_opts():
    return {
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "cookies": False,
        "extractor_args": {"youtube": {"player_client": ["web", "android"]}},
    }


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
    if not thumbnail_url:
        return False
    try:
        req = urllib.request.Request(thumbnail_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
        with open(dest_path, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False


def cleanup_session(session_id: str):
    session = sessions.get(session_id)
    if not session:
        return
    session_dir = session.get("session_dir")
    if session_dir and os.path.isdir(session_dir):
        shutil.rmtree(session_dir, ignore_errors=True)
    sessions.pop(session_id, None)


def _save_to_minio_and_db(app, user_id: int, bucket: str, local_path: str, object_name: str,
                          title: str, artist: str = "", duration_sec: int = None, playlist_name: str = None,
                          source_url: str = "", thumbnail_local_path: str = None):
    """Save to MinIO and insert Song record. Uploads MP3 + thumbnail. Called from download thread."""
    import logging
    log = logging.getLogger(__name__)
    from services import MinIOService
    from models import Song
    from extensions import db
    try:
        minio = MinIOService(
            app.config["MINIO_ENDPOINT"],
            app.config["MINIO_ACCESS_KEY"],
            app.config["MINIO_SECRET_KEY"],
            app.config["MINIO_SECURE"],
        )
        # Ensure bucket exists
        minio.ensure_user_bucket(bucket)

        if not os.path.isfile(local_path):
            log.error(f"MinIO upload: local file not found: {local_path}")
            return False

        file_size = os.path.getsize(local_path)
        ok = minio.put_file(bucket, object_name, local_path)
        if not ok:
            log.error(f"MinIO upload failed: bucket={bucket} object={object_name} path={local_path}")
            return False

        log.info(f"MinIO upload OK: {bucket}/{object_name} ({file_size} bytes)")

        thumb_obj = None
        if thumbnail_local_path and os.path.isfile(thumbnail_local_path):
            base, _ = os.path.splitext(object_name)
            thumb_ext = "jpg" if thumbnail_local_path.lower().endswith(".jpg") else (
                "png" if thumbnail_local_path.lower().endswith(".png") else "webp")
            thumb_obj = f"{base}.{thumb_ext}"
            minio.put_file(bucket, thumb_obj, thumbnail_local_path)

        # Avoid duplicate DB entries
        existing = Song.query.filter_by(user_id=user_id, object_name=object_name).first()
        if existing:
            log.info(f"Song already in DB: {object_name}")
            return True

        song = Song(
            user_id=user_id,
            minio_path=object_name,
            object_name=object_name,
            title=title,
            artist=artist,
            duration_sec=duration_sec,
            thumbnail_path=thumb_obj,
            playlist_name=playlist_name,
            source_url=source_url or None,
            file_size=file_size,
        )
        db.session.add(song)
        db.session.commit()
        log.info(f"Song saved to DB: id={song.id} title={title}")
        return True
    except Exception as e:
        log.error(f"_save_to_minio_and_db error: {e}", exc_info=True)
        try:
            db.session.rollback()
        except Exception:
            pass
        return False


def _save_to_vault(app, local_path: str, object_name: str, title: str, artist: str = "",
                   duration_sec: int = None, source_url: str = "", thumbnail_local_path: str = None):
    """Save to the shared audioweb-library vault bucket (all users, even anonymous).
    Idempotent — skips if object_name already in VaultSong table."""
    import logging
    log = logging.getLogger(__name__)
    from services import MinIOService
    from models import VaultSong
    from extensions import db
    try:
        vault_bucket = app.config.get("MINIO_WIDE_BUCKET", "audioweb-library")
        minio = MinIOService(
            app.config["MINIO_ENDPOINT"],
            app.config["MINIO_ACCESS_KEY"],
            app.config["MINIO_SECRET_KEY"],
            app.config["MINIO_SECURE"],
        )
        minio.ensure_wide_bucket(vault_bucket)
        existing = VaultSong.query.filter_by(object_name=object_name).first()
        if existing:
            log.info(f"Vault: already exists {object_name}")
            return True
        if not os.path.isfile(local_path):
            log.warning(f"Vault: file not found {local_path}")
            return False
        file_size = os.path.getsize(local_path)
        ok = minio.put_file(vault_bucket, object_name, local_path)
        if not ok:
            log.error(f"Vault: MinIO upload failed {object_name}")
            return False
        thumb_obj = None
        if thumbnail_local_path and os.path.isfile(thumbnail_local_path):
            base, _ = os.path.splitext(object_name)
            ext = os.path.splitext(thumbnail_local_path)[1].lstrip(".") or "jpg"
            thumb_obj = f"{base}.{ext}"
            minio.put_file(vault_bucket, thumb_obj, thumbnail_local_path)
        vault_song = VaultSong(
            object_name=object_name,
            title=title,
            artist=artist,
            duration_sec=int(duration_sec) if duration_sec else None,
            source_url=source_url or None,
            file_size=file_size,
            thumbnail_path=thumb_obj,
        )
        db.session.add(vault_song)
        db.session.commit()
        log.info(f"Vault: saved {object_name}")
        return True
    except Exception as e:
        log.error(f"_save_to_vault error: {e}", exc_info=True)
        try:
            db.session.rollback()
        except Exception:
            pass
        return False


@yt_download_bp.route("/")
def index():
    return render_template("yt_download.html")


@yt_download_bp.route("/api/info", methods=["POST"])
def get_info():
    data = request.get_json(force=True)
    url = normalise_url(data.get("url", ""))
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    ydl_opts = {**base_ydl_opts(), "extract_flat": "in_playlist", "skip_download": True}

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if info is None:
            return jsonify({
                "error": "Could not fetch info. The video may be unavailable, private, or age-restricted."
            }), 400

        tracks = []
        if "entries" in info:
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
                vid_url = entry.get("url") or (f"https://www.youtube.com/watch?v={vid_id}" if vid_id else None)
                if not vid_url:
                    continue
                dur = entry.get("duration")
                tracks.append({
                    "id": vid_id,
                    "title": entry.get("title") or vid_id,
                    "url": vid_url,
                    "duration": fmt_duration(dur),
                    "duration_sec": int(dur) if dur else None,
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
            vid_id = info.get("id", "")
            dur = info.get("duration")
            tracks.append({
                "id": vid_id,
                "title": info.get("title") or vid_id,
                "url": url,
                "duration": fmt_duration(dur),
                "duration_sec": int(dur) if dur else None,
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
        msg = str(e)
        if "failed to load cookies" in msg.lower():
            return jsonify({"error": msg}), 400
        return jsonify({"error": f"Unexpected error: {e}"}), 500


@yt_download_bp.route("/api/download", methods=["POST"])
def start_download():
    from flask import current_app
    data = request.get_json(force=True)
    urls = data.get("urls", [])
    titles = data.get("titles", {})
    track_meta = data.get("track_meta", {})  # {url: {artist, duration_sec}}
    playlist_title = data.get("playlist_title", "").strip()
    playlist_thumbnail = data.get("playlist_thumbnail", "").strip()
    is_playlist = bool(playlist_title)
    session_id = data.get("session_id") or str(uuid.uuid4())
    save_to_library = data.get("save_to_library", False)  # only if logged in

    if not urls:
        return jsonify({"error": "No URLs provided"}), 400

    user = current_user if current_user.is_authenticated else None
    if save_to_library and not user:
        return jsonify({"error": "Login required to save to library"}), 401

    app = current_app._get_current_object()
    session_dir = os.path.join(BASE_TEMP_DIR, session_id)
    if is_playlist:
        safe_name = yt_dlp.utils.sanitize_filename(playlist_title, is_id=False) or "playlist"
        music_dir = os.path.join(session_dir, safe_name)
    else:
        music_dir = session_dir

    os.makedirs(music_dir, exist_ok=True)

    q = queue.Queue()
    sessions[session_id] = {
        "queue": q,
        "files": {},
        "session_dir": session_dir,
    }

    def do_download():
        user_bucket = user.minio_bucket if user else None
        playlist_folder = yt_dlp.utils.sanitize_filename(playlist_title, is_id=False) if is_playlist else None

        for url in urls:
            meta = track_meta.get(url, {})
            title = titles.get(url, url)
            captured_path = []

            def make_hooks(u, t, cap):
                def progress_hook(d):
                    status = d.get("status")
                    if status == "downloading":
                        pct_str = d.get("_percent_str", "").strip().replace("%", "")
                        try:
                            pct = float(pct_str)
                        except ValueError:
                            pct = 0
                        q.put({"type": "progress", "url": u, "title": t, "percent": pct,
                               "speed": d.get("_speed_str", "").strip(), "eta": d.get("_eta_str", "").strip()})
                    elif status == "finished":
                        q.put({"type": "converting", "url": u, "title": t})
                    elif status == "error":
                        q.put({"type": "track_error", "url": u, "title": t, "message": str(d.get("error", "Unknown"))})

                def pp_hook(d):
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
                    {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320"},
                    {"key": "FFmpegMetadata", "add_metadata": True},
                    {"key": "EmbedThumbnail", "already_have_thumbnail": False},
                ],
                "outtmpl": os.path.join(music_dir, "%(title)s.%(ext)s"),
                "progress_hooks": [progress_hook],
                "postprocessor_hooks": [pp_hook],
            }

            try:
                q.put({"type": "started", "url": url, "title": title})
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

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

                    # Save thumbnail image alongside MP3 (local zip + MinIO)
                    thumb_path = None
                    thumb_url = meta.get("thumbnail", "")
                    if thumb_url:
                        base_name = os.path.splitext(os.path.basename(final_path))[0]
                        ext = "png" if ".png" in thumb_url else ("webp" if ".webp" in thumb_url else "jpg")
                        thumb_path = os.path.join(music_dir, f"{base_name}.{ext}")
                        fetch_cover(thumb_url, thumb_path)
                        if not os.path.isfile(thumb_path):
                            thumb_path = None

                    # Always save to the shared vault (logged-in or anonymous)
                    vault_obj = f"Vault/{os.path.basename(final_path)}"
                    with app.app_context():
                        _save_to_vault(
                            app, final_path, vault_obj,
                            title=title,
                            artist=meta.get("artist", ""),
                            duration_sec=int(meta["duration_sec"]) if meta.get("duration_sec") else None,
                            source_url=url,
                            thumbnail_local_path=thumb_path,
                        )

                    # Save to MinIO + DB for logged-in users who opted in
                    if save_to_library and user_bucket and user:
                        if playlist_folder:
                            object_name = f"{playlist_folder}/{os.path.basename(final_path)}"
                        else:
                            object_name = f"Downloads/{os.path.basename(final_path)}"
                        dur = meta.get("duration_sec")
                        with app.app_context():
                            saved = _save_to_minio_and_db(
                                app, user.id, user_bucket, final_path, object_name,
                                title=title,
                                artist=meta.get("artist", ""),
                                duration_sec=int(dur) if dur else None,
                                playlist_name=playlist_folder if is_playlist else None,
                                source_url=url,
                                thumbnail_local_path=thumb_path,
                            )
                        if saved:
                            q.put({"type": "saved_to_library", "url": url, "title": title})
                        else:
                            q.put({"type": "library_error", "url": url, "title": title,
                                   "message": "Upload to storage failed — check MinIO connection and logs."})

                    if is_playlist:
                        q.put({"type": "track_done", "url": url, "title": title})
                    else:
                        q.put({
                            "type": "track_done",
                            "url": url,
                            "title": title,
                            "filename": os.path.basename(final_path),
                            "session_id": session_id,
                        })
                else:
                    q.put({"type": "track_error", "url": url, "title": title, "message": "Output file not found after conversion."})

            except Exception as e:
                q.put({"type": "track_error", "url": url, "title": title, "message": str(e)})

        if is_playlist:
            q.put({"type": "zipping", "message": "Creating archive…"})
            if playlist_thumbnail:
                ext = "png" if ".png" in playlist_thumbnail else ("webp" if ".webp" in playlist_thumbnail else "jpg")
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
            q.put({"type": "zip_ready", "session_id": session_id, "filename": f"{safe_name}.zip"})

        q.put({"type": "all_done", "session_id": session_id})

    threading.Thread(target=do_download, daemon=True).start()
    return jsonify({"session_id": session_id, "is_playlist": is_playlist})


@yt_download_bp.route("/api/session-status/<session_id>")
def session_status(session_id):
    """Returns whether a download session is still alive on the server."""
    session = sessions.get(session_id)
    if not session:
        return jsonify({"exists": False})
    zip_ready = bool(session.get("zip_path") and os.path.isfile(session.get("zip_path", "")))
    return jsonify({
        "exists": True,
        "zip_ready": zip_ready,
        "zip_name": session.get("zip_name", ""),
        "file_count": len(session.get("files", {})),
    })


@yt_download_bp.route("/api/serve/<session_id>/<path:filename>")
def serve_file(session_id, filename):
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
        session_dir = session.get("session_dir")
        try:
            if session_dir and os.path.isdir(session_dir):
                if not any(f.endswith(".mp3") for f in os.listdir(session_dir)):
                    cleanup_session(session_id)
        except Exception:
            pass
        return response

    return send_file(filepath, mimetype="audio/mpeg", as_attachment=True, download_name=filename)


@yt_download_bp.route("/api/serve-zip/<session_id>")
def serve_zip(session_id):
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

    return send_file(zip_path, mimetype="application/zip", as_attachment=True, download_name=zip_name)


@yt_download_bp.route("/api/progress/<session_id>")
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

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
