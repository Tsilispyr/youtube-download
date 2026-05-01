"""
Spotify Download blueprint: Spotify → MP3 downloader.
Mounted at /AudioWeb/spotify-download

No Spotify API credentials required.

Strategy:
  /api/info  — scrapes the public Spotify web page and parses the embedded
               __NEXT_DATA__ JSON block (no OAuth, no client credentials).
               Falls back to og: meta tags for single tracks if needed.

  /api/download — for each Spotify track builds a YouTube Music search query
                  ("ytmsearch1:{artist} - {title}") and downloads via yt-dlp,
                  running the same FFmpeg/MP3/tag/thumbnail pipeline as
                  yt_download.py.
"""
import os, re, json, queue, threading, uuid, zipfile, urllib.request, urllib.error, urllib.parse, tempfile, shutil

import yt_dlp
import yt_dlp.utils

from flask import Blueprint, render_template, request, jsonify, Response, send_file, after_this_request
from flask_login import current_user

spotify_download_bp = Blueprint("spotify_download", __name__, url_prefix="/AudioWeb/spotify-download")

BASE_TEMP_DIR = os.path.join(tempfile.gettempdir(), "spotify-mp3-sessions")
os.makedirs(BASE_TEMP_DIR, exist_ok=True)
sessions: dict = {}

# ── Helpers ───────────────────────────────────────────────────────────────────

def fmt_duration(ms) -> str:
    if not ms: return ""
    sec = int(ms) // 1000 if int(ms) > 10_000 else int(ms)
    m, s = divmod(sec, 60); h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def fmt_duration_sec(ms):
    if not ms: return None
    v = int(ms)
    return v // 1000 if v > 10_000 else v

def fetch_cover(thumbnail_url: str, dest_path: str) -> bool:
    if not thumbnail_url: return False
    try:
        req = urllib.request.Request(thumbnail_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
        with open(dest_path, "wb") as f: f.write(data)
        return True
    except Exception: return False

def cleanup_session(session_id: str):
    session = sessions.get(session_id)
    if not session: return
    sd = session.get("session_dir")
    if sd and os.path.isdir(sd): shutil.rmtree(sd, ignore_errors=True)
    sessions.pop(session_id, None)

import logging as _logging
_log = _logging.getLogger(__name__)

def _ytmsearch(title: str, artist: str) -> str:
    q = f"{artist} - {title}".strip(" -") if artist else title
    return f"ytmsearch1:{q}"

# ── Spotify anonymous-token API (no developer credentials needed) ─────────────
#
# Spotify's web player obtains a short-lived anonymous Bearer token from a
# public endpoint every time someone visits open.spotify.com.  We do the same
# here — fetch the token, then call the standard Spotify Web API.  No OAuth,
# no client_id/client_secret, no developer account.

import time as _time

_token_cache: dict = {"token": "", "expires": 0}

_WP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://open.spotify.com",
    "Referer": "https://open.spotify.com/",
}


def _get_anon_token() -> str:
    """Return a cached anonymous Spotify Bearer token, refreshing when needed."""
    now = _time.time()
    if _token_cache["token"] and now < _token_cache["expires"] - 30:
        return _token_cache["token"]

    url = "https://open.spotify.com/get_access_token?reason=transport&productType=web_player"
    req = urllib.request.Request(url, headers=_WP_HEADERS)
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())

    token = data.get("accessToken", "")
    exp_ms = data.get("accessTokenExpirationTimestampMs", 0)
    if not token:
        raise RuntimeError(f"Spotify token endpoint returned no token: {data}")

    _token_cache["token"]   = token
    _token_cache["expires"] = exp_ms / 1000 if exp_ms else now + 3600
    _log.info("Spotify anon token refreshed, expires %s", _token_cache["expires"])
    return token


def _spotify_api(path: str, params: dict | None = None) -> dict:
    """GET https://api.spotify.com/v1/{path} with the anon token."""
    token = _get_anon_token()
    qs = ("?" + "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())) if params else ""
    url = f"https://api.spotify.com/v1/{path}{qs}"
    req = urllib.request.Request(url, headers={**_WP_HEADERS, "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _best_image(images: list) -> str:
    if not images: return ""
    return max(images, key=lambda i: i.get("width") or i.get("height") or 0).get("url", "")


def _artists_str(artists: list) -> str:
    return ", ".join(a.get("name", "") for a in (artists or []) if a.get("name"))


def _api_track_to_dict(t: dict, collection_thumb: str = "") -> dict:
    tid    = t.get("id") or ""
    title  = t.get("name") or "Unknown"
    artist = _artists_str(t.get("artists", []))
    album  = (t.get("album") or {}).get("name", "")
    dur_ms = t.get("duration_ms", 0)
    thumb  = _best_image((t.get("album") or {}).get("images", [])) or collection_thumb
    return {
        "id":           tid,
        "url":          f"https://open.spotify.com/track/{tid}",
        "title":        title,
        "uploader":     artist,
        "album":        album,
        "duration":     fmt_duration(dur_ms),
        "duration_sec": fmt_duration_sec(dur_ms),
        "thumbnail":    thumb,
    }


def _scrape_spotify(url: str) -> dict:
    """Resolve a public Spotify URL to a normalised track-list dict."""
    clean = re.sub(r"\?.*$", "", url).rstrip("/")
    if   "/track/"    in clean: kind = "track"
    elif "/album/"    in clean: kind = "album"
    elif "/playlist/" in clean: kind = "playlist"
    else:
        raise ValueError("Unrecognised Spotify URL. Paste a track, album, or playlist link.")

    spotify_id = clean.split(f"/{kind}/")[-1].split("/")[0]
    _log.info("Spotify fetch: kind=%s id=%s", kind, spotify_id)

    # ── Single track ──────────────────────────────────────────────────────
    if kind == "track":
        data = _spotify_api(f"tracks/{spotify_id}")
        t    = _api_track_to_dict(data)
        return {
            "type": "single", "title": t["title"], "uploader": t["uploader"],
            "thumbnail": t["thumbnail"], "count": 1, "tracks": [t],
            "playlist_title": "", "playlist_thumbnail": "",
        }

    # ── Album ─────────────────────────────────────────────────────────────
    if kind == "album":
        data    = _spotify_api(f"albums/{spotify_id}")
        p_title = data.get("name") or "Album"
        p_thumb = _best_image(data.get("images", []))
        tracks  = []
        # Paginate through all album tracks
        items = (data.get("tracks") or {}).get("items", [])
        next_url = (data.get("tracks") or {}).get("next")
        # Album track stubs don't include album info — inject it
        for item in items:
            item.setdefault("album", {"name": p_title, "images": data.get("images", [])})
            tracks.append(_api_track_to_dict(item, p_thumb))
        while next_url:
            token  = _get_anon_token()
            req    = urllib.request.Request(next_url, headers={**_WP_HEADERS, "Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                page = json.loads(resp.read())
            for item in (page.get("items") or []):
                item.setdefault("album", {"name": p_title, "images": data.get("images", [])})
                tracks.append(_api_track_to_dict(item, p_thumb))
            next_url = page.get("next")
        if not tracks:
            raise ValueError("Album has no tracks or is unavailable in your region.")
        return {
            "type": "playlist", "title": p_title, "uploader": "",
            "thumbnail": p_thumb, "count": len(tracks), "tracks": tracks,
            "playlist_title": p_title, "playlist_thumbnail": p_thumb,
        }

    # ── Playlist ──────────────────────────────────────────────────────────
    data    = _spotify_api(f"playlists/{spotify_id}", {"fields": "name,images,tracks(items(track),next)", "market": "US"})
    p_title = data.get("name") or "Playlist"
    p_thumb = _best_image(data.get("images", []))
    tracks  = []

    def _add_items(items):
        for item in (items or []):
            t = item.get("track")
            if not t or t.get("type") != "track": continue
            tracks.append(_api_track_to_dict(t, p_thumb))

    _add_items((data.get("tracks") or {}).get("items", []))
    next_url = (data.get("tracks") or {}).get("next")
    while next_url:
        token = _get_anon_token()
        req   = urllib.request.Request(next_url, headers={**_WP_HEADERS, "Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            page = json.loads(resp.read())
        _add_items(page.get("items", []))
        next_url = page.get("next")

    if not tracks:
        raise ValueError("Playlist is empty or private — only public playlists are supported.")
    return {
        "type": "playlist", "title": p_title, "uploader": "",
        "thumbnail": p_thumb, "count": len(tracks), "tracks": tracks,
        "playlist_title": p_title, "playlist_thumbnail": p_thumb,
    }

# ── MinIO / DB helpers (identical to yt_download) ────────────────────────────

def _save_to_minio_and_db(app, user_id, bucket, local_path, object_name, title,
                           artist="", duration_sec=None, playlist_name=None, source_url="", thumbnail_local_path=None):
    import logging; log = logging.getLogger(__name__)
    from services import MinIOService; from models import Song; from extensions import db
    try:
        minio = MinIOService(app.config["MINIO_ENDPOINT"], app.config["MINIO_ACCESS_KEY"],
                             app.config["MINIO_SECRET_KEY"], app.config["MINIO_SECURE"])
        minio.ensure_user_bucket(bucket)
        if not os.path.isfile(local_path): return False
        file_size = os.path.getsize(local_path)
        if not minio.put_file(bucket, object_name, local_path): return False
        thumb_obj = None
        if thumbnail_local_path and os.path.isfile(thumbnail_local_path):
            base, _ = os.path.splitext(object_name)
            thumb_obj = f"{base}.{os.path.splitext(thumbnail_local_path)[1].lstrip('.') or 'jpg'}"
            minio.put_file(bucket, thumb_obj, thumbnail_local_path)
        if Song.query.filter_by(user_id=user_id, object_name=object_name).first(): return True
        db.session.add(Song(user_id=user_id, minio_path=object_name, object_name=object_name,
            title=title, artist=artist, duration_sec=duration_sec, thumbnail_path=thumb_obj,
            playlist_name=playlist_name, source_url=source_url or None, file_size=file_size))
        db.session.commit(); return True
    except Exception as e:
        log.error(f"_save_to_minio_and_db error: {e}", exc_info=True)
        try: db.session.rollback()
        except Exception: pass
        return False

def _save_to_vault(app, local_path, object_name, title, artist="",
                   duration_sec=None, source_url="", thumbnail_local_path=None):
    import logging; log = logging.getLogger(__name__)
    from services import MinIOService; from models import VaultSong; from extensions import db
    try:
        vault_bucket = app.config.get("MINIO_WIDE_BUCKET", "audioweb-library")
        minio = MinIOService(app.config["MINIO_ENDPOINT"], app.config["MINIO_ACCESS_KEY"],
                             app.config["MINIO_SECRET_KEY"], app.config["MINIO_SECURE"])
        minio.ensure_wide_bucket(vault_bucket)
        if VaultSong.query.filter_by(object_name=object_name).first(): return True
        if not os.path.isfile(local_path): return False
        file_size = os.path.getsize(local_path)
        if not minio.put_file(vault_bucket, object_name, local_path): return False
        thumb_obj = None
        if thumbnail_local_path and os.path.isfile(thumbnail_local_path):
            base, _ = os.path.splitext(object_name)
            thumb_obj = f"{base}.{os.path.splitext(thumbnail_local_path)[1].lstrip('.') or 'jpg'}"
            minio.put_file(vault_bucket, thumb_obj, thumbnail_local_path)
        db.session.add(VaultSong(object_name=object_name, title=title, artist=artist,
            duration_sec=int(duration_sec) if duration_sec else None,
            source_url=source_url or None, file_size=file_size, thumbnail_path=thumb_obj))
        db.session.commit(); return True
    except Exception as e:
        log.error(f"_save_to_vault error: {e}", exc_info=True)
        try: db.session.rollback()
        except Exception: pass
        return False

# ── Routes ────────────────────────────────────────────────────────────────────

@spotify_download_bp.route("/")
def index():
    return render_template("spotify_download.html")

@spotify_download_bp.route("/api/info", methods=["POST"])
def get_info():
    data = request.get_json(force=True)
    url  = (data.get("url") or "").strip()
    if not url: return jsonify({"error": "No URL provided"}), 400
    try:
        result = _scrape_spotify(url)
    except ValueError as e:
        _log.warning("Spotify info 400: %s", e)
        return jsonify({"error": str(e)}), 400
    except urllib.error.HTTPError as e:
        _log.warning("Spotify HTTP error %s for %s", e.code, url)
        return jsonify({"error": f"Spotify returned HTTP {e.code} — the URL may be private or geo-blocked."}), 400
    except Exception as e:
        _log.error("Spotify info unexpected error for %s: %s", url, e, exc_info=True)
        return jsonify({"error": f"Failed to fetch Spotify info: {e}"}), 500
    return jsonify(result)

@spotify_download_bp.route("/api/download", methods=["POST"])
def start_download():
    from flask import current_app
    data               = request.get_json(force=True)
    urls               = data.get("urls", [])
    titles             = data.get("titles", {})
    track_meta         = data.get("track_meta", {})
    playlist_title     = data.get("playlist_title", "").strip()
    playlist_thumbnail = data.get("playlist_thumbnail", "").strip()
    is_playlist        = bool(playlist_title)
    session_id         = data.get("session_id") or str(uuid.uuid4())
    save_to_library    = data.get("save_to_library", False)

    if not urls: return jsonify({"error": "No URLs provided"}), 400
    user = current_user if current_user.is_authenticated else None
    if save_to_library and not user: return jsonify({"error": "Login required to save to library"}), 401

    app = current_app._get_current_object()
    session_dir = os.path.join(BASE_TEMP_DIR, session_id)
    if is_playlist:
        safe_name = yt_dlp.utils.sanitize_filename(playlist_title, is_id=False) or "playlist"
        music_dir = os.path.join(session_dir, safe_name)
    else:
        music_dir = session_dir
    os.makedirs(music_dir, exist_ok=True)

    q = queue.Queue()
    sessions[session_id] = {"queue": q, "files": {}, "session_dir": session_dir}

    def do_download():
        user_bucket     = user.minio_bucket if user else None
        playlist_folder = yt_dlp.utils.sanitize_filename(playlist_title, is_id=False) if is_playlist else None

        for spotify_url in urls:
            meta   = track_meta.get(spotify_url, {})
            title  = titles.get(spotify_url, spotify_url)
            artist = meta.get("artist", "")
            yt_search = _ytmsearch(title, artist)
            captured  = []

            def make_hooks(u, t, cap):
                def progress_hook(d):
                    status = d.get("status")
                    if status == "downloading":
                        pct_str = d.get("_percent_str","").strip().replace("%","")
                        try: pct = float(pct_str)
                        except ValueError: pct = 0
                        q.put({"type":"progress","url":u,"title":t,"percent":pct,
                               "speed":d.get("_speed_str","").strip(),"eta":d.get("_eta_str","").strip()})
                    elif status == "finished":
                        q.put({"type":"converting","url":u,"title":t})
                    elif status == "error":
                        q.put({"type":"track_error","url":u,"title":t,"message":str(d.get("error","Unknown"))})
                def pp_hook(d):
                    if d.get("status") == "finished":
                        fp = d.get("info_dict",{}).get("filepath") or d.get("filepath","")
                        if fp and fp.endswith(".mp3") and os.path.isfile(fp):
                            cap.clear(); cap.append(fp)
                return progress_hook, pp_hook

            progress_hook, pp_hook = make_hooks(spotify_url, title, captured)
            ydl_opts = {
                "quiet":True,"no_warnings":True,"ignoreerrors":True,"format":"bestaudio/best",
                "writethumbnail":True,
                "postprocessors":[
                    {"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":"320"},
                    {"key":"FFmpegMetadata","add_metadata":True},
                    {"key":"EmbedThumbnail","already_have_thumbnail":False},
                ],
                "outtmpl":os.path.join(music_dir,"%(title)s.%(ext)s"),
                "progress_hooks":[progress_hook],"postprocessor_hooks":[pp_hook],
            }

            try:
                q.put({"type":"started","url":spotify_url,"title":title})
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([yt_search])

                final_path = captured[0] if captured else None
                if not final_path:
                    known = set(sessions[session_id]["files"].values())
                    for fname in os.listdir(music_dir):
                        if fname.endswith(".mp3"):
                            candidate = os.path.join(music_dir, fname)
                            if candidate not in known: final_path = candidate; break

                if final_path and os.path.isfile(final_path):
                    sessions[session_id]["files"][spotify_url] = final_path
                    thumb_path = None
                    thumb_url  = meta.get("thumbnail","")
                    if thumb_url:
                        base_name = os.path.splitext(os.path.basename(final_path))[0]
                        ext = "png" if ".png" in thumb_url else ("webp" if ".webp" in thumb_url else "jpg")
                        thumb_path = os.path.join(music_dir, f"{base_name}.{ext}")
                        fetch_cover(thumb_url, thumb_path)
                        if not os.path.isfile(thumb_path): thumb_path = None

                    vault_obj = f"Vault/{os.path.basename(final_path)}"
                    with app.app_context():
                        _save_to_vault(app, final_path, vault_obj, title=title, artist=artist,
                            duration_sec=int(meta["duration_sec"]) if meta.get("duration_sec") else None,
                            source_url=spotify_url, thumbnail_local_path=thumb_path)

                    if save_to_library and user_bucket and user:
                        obj = (f"{playlist_folder}/{os.path.basename(final_path)}" if playlist_folder
                               else f"Downloads/{os.path.basename(final_path)}")
                        dur = meta.get("duration_sec")
                        with app.app_context():
                            saved = _save_to_minio_and_db(app, user.id, user_bucket, final_path, obj,
                                title=title, artist=artist, duration_sec=int(dur) if dur else None,
                                playlist_name=playlist_folder if is_playlist else None,
                                source_url=spotify_url, thumbnail_local_path=thumb_path)
                        q.put({"type":"saved_to_library" if saved else "library_error",
                               "url":spotify_url,"title":title,
                               **({"message":"Upload to storage failed."} if not saved else {})})

                    if is_playlist:
                        q.put({"type":"track_done","url":spotify_url,"title":title})
                    else:
                        q.put({"type":"track_done","url":spotify_url,"title":title,
                               "filename":os.path.basename(final_path),"session_id":session_id})
                else:
                    q.put({"type":"track_error","url":spotify_url,"title":title,
                           "message":"Output file not found after download."})
            except Exception as e:
                q.put({"type":"track_error","url":spotify_url,"title":title,"message":str(e)})

        if is_playlist:
            q.put({"type":"zipping","message":"Creating archive…"})
            if playlist_thumbnail:
                ext = "png" if ".png" in playlist_thumbnail else ("webp" if ".webp" in playlist_thumbnail else "jpg")
                fetch_cover(playlist_thumbnail, os.path.join(music_dir, f"cover.{ext}"))
            safe_name = os.path.basename(music_dir)
            zip_path  = os.path.join(session_dir, f"{safe_name}.zip")
            with zipfile.ZipFile(zip_path,"w",zipfile.ZIP_DEFLATED) as zf:
                for root,_,files in os.walk(music_dir):
                    for fname in sorted(files):
                        abs_path = os.path.join(root, fname)
                        zf.write(abs_path, os.path.relpath(abs_path, session_dir))
            sessions[session_id]["zip_path"] = zip_path
            sessions[session_id]["zip_name"]  = f"{safe_name}.zip"
            q.put({"type":"zip_ready","session_id":session_id,"filename":f"{safe_name}.zip"})

        q.put({"type":"all_done","session_id":session_id})

    threading.Thread(target=do_download, daemon=True).start()
    return jsonify({"session_id":session_id,"is_playlist":is_playlist})

@spotify_download_bp.route("/api/session-status/<session_id>")
def session_status(session_id):
    session = sessions.get(session_id)
    if not session: return jsonify({"exists":False})
    zip_ready = bool(session.get("zip_path") and os.path.isfile(session.get("zip_path","")))
    return jsonify({"exists":True,"zip_ready":zip_ready,"zip_name":session.get("zip_name",""),"file_count":len(session.get("files",{}))})

@spotify_download_bp.route("/api/serve/<session_id>/<path:filename>")
def serve_file(session_id, filename):
    session = sessions.get(session_id)
    if not session: return jsonify({"error":"Session not found or expired"}), 404
    filepath = None
    for path in session["files"].values():
        if os.path.basename(path) == filename: filepath = path; break
    if not filepath or not os.path.isfile(filepath): return jsonify({"error":"File not found"}), 404

    @after_this_request
    def cleanup(response):
        try: os.remove(filepath)
        except Exception: pass
        try:
            sd = session.get("session_dir")
            if sd and os.path.isdir(sd) and not any(f.endswith(".mp3") for f in os.listdir(sd)):
                cleanup_session(session_id)
        except Exception: pass
        return response

    return send_file(filepath, mimetype="audio/mpeg", as_attachment=True, download_name=filename)

@spotify_download_bp.route("/api/serve-zip/<session_id>")
def serve_zip(session_id):
    session = sessions.get(session_id)
    if not session: return jsonify({"error":"Session not found or expired"}), 404
    zip_path = session.get("zip_path"); zip_name = session.get("zip_name","playlist.zip")
    if not zip_path or not os.path.isfile(zip_path): return jsonify({"error":"Zip not ready"}), 404

    @after_this_request
    def cleanup(response): cleanup_session(session_id); return response

    return send_file(zip_path, mimetype="application/zip", as_attachment=True, download_name=zip_name)

@spotify_download_bp.route("/api/progress/<session_id>")
def progress_stream(session_id):
    def generate():
        session = sessions.get(session_id)
        if not session:
            yield f"data: {json.dumps({'type':'error','message':'Session not found'})}\n\n"; return
        q = session["queue"]
        while True:
            try:
                msg = q.get(timeout=25)
                yield f"data: {json.dumps(msg)}\n\n"
                if msg["type"] == "all_done": break
            except queue.Empty:
                yield "data: {\"type\":\"keepalive\"}\n\n"
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})
