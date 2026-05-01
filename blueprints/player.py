"""
Player blueprint: Web player with EQ, visualiser, personal + vault library, queue.
Mounted at /AudioWorld/Player
"""
from flask import Blueprint, render_template, jsonify, request, Response
from flask_login import login_required, current_user

from extensions import db
from models import Song, VaultSong
from services import MinIOService

player_bp = Blueprint("player", __name__, url_prefix="/AudioWorld/Player")


def get_minio():
    from flask import current_app
    c = current_app.config
    return MinIOService(
        c["MINIO_ENDPOINT"], c["MINIO_ACCESS_KEY"],
        c["MINIO_SECRET_KEY"], c["MINIO_SECURE"],
    )


@player_bp.route("/")
def index():
    return render_template("player.html")


# ── Personal library ──────────────────────────────────────────────────────────

@player_bp.route("/api/library")
@login_required
def library():
    songs = (
        Song.query.filter_by(user_id=current_user.id)
        .order_by(Song.playlist_name.is_(None), Song.playlist_name, Song.title).all()
    )
    folders = {}
    for s in songs:
        folder = s.playlist_name or "Downloads"
        folders.setdefault(folder, [])
        folders[folder].append(s.to_dict())
    return jsonify({"folders": folders})


@player_bp.route("/api/stream/<int:song_id>")
@login_required
def stream(song_id):
    song = Song.query.filter_by(id=song_id, user_id=current_user.id).first()
    if not song:
        return jsonify({"error": "Not found"}), 404
    if not current_user.minio_bucket:
        return jsonify({"error": "No storage bucket"}), 500
    minio = get_minio()
    data = minio.get_object(current_user.minio_bucket, song.object_name)
    if not data:
        return jsonify({"error": "Could not load file from storage"}), 500
    safe_title = (song.title or "track").replace('"', '')
    return Response(data, mimetype="audio/mpeg",
                    headers={"Content-Disposition": f'inline; filename="{safe_title}.mp3"'})


@player_bp.route("/api/search")
@login_required
def search():
    q = request.args.get("q", "").strip()[:100]
    if not q:
        return jsonify({"songs": []})
    songs = Song.query.filter(
        Song.user_id == current_user.id,
        db.or_(Song.title.ilike(f"%{q}%"), Song.artist.ilike(f"%{q}%"),
               Song.album.ilike(f"%{q}%"), Song.playlist_name.ilike(f"%{q}%")),
    ).limit(50).all()
    return jsonify({"songs": [s.to_dict() for s in songs]})


# ── Vault (shared library, public) ───────────────────────────────────────────

@player_bp.route("/api/vault-library")
def vault_library():
    q = request.args.get("q", "").strip()[:100]
    base = VaultSong.query
    if q:
        base = base.filter(db.or_(
            VaultSong.title.ilike(f"%{q}%"),
            VaultSong.artist.ilike(f"%{q}%"),
        ))
    songs = base.order_by(VaultSong.created_at.desc()).limit(200).all()
    return jsonify({"songs": [s.to_dict() for s in songs]})


@player_bp.route("/api/vault-stream/<int:vault_id>")
def vault_stream(vault_id):
    from flask import current_app
    song = VaultSong.query.get(vault_id)
    if not song:
        return jsonify({"error": "Not found"}), 404
    vault_bucket = current_app.config.get("MINIO_WIDE_BUCKET", "audioweb-library")
    minio = get_minio()
    data = minio.get_object(vault_bucket, song.object_name)
    if not data:
        return jsonify({"error": "Could not load file"}), 500
    safe_title = (song.title or "track").replace('"', '')
    return Response(data, mimetype="audio/mpeg",
                    headers={"Content-Disposition": f'inline; filename="{safe_title}.mp3"'})
