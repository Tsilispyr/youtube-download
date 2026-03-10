"""
Player blueprint: Web player with equalizer, visualizer, library, search.
Mounted at /AudioWorld/Player
Requires login to access user's library.
"""
from flask import Blueprint, render_template, jsonify, request, Response
from flask_login import login_required, current_user

from extensions import db
from models import Song
from services import MinIOService

player_bp = Blueprint("player", __name__, url_prefix="/AudioWorld/Player")


def get_minio():
    from flask import current_app
    c = current_app.config
    return MinIOService(
        c["MINIO_ENDPOINT"],
        c["MINIO_ACCESS_KEY"],
        c["MINIO_SECRET_KEY"],
        c["MINIO_SECURE"],
    )


@player_bp.route("/")
def index():
    """Player page — shows login prompt if not authenticated."""
    return render_template("player.html")


@player_bp.route("/api/library")
@login_required
def library():
    """Return user's songs grouped by folder (playlist) or Downloads."""
    if not current_user.minio_bucket:
        return jsonify({"folders": {}})

    songs = Song.query.filter_by(user_id=current_user.id).order_by(Song.playlist_name, Song.title).all()

    folders = {}
    for s in songs:
        folder = s.playlist_name if s.playlist_name else "Downloads"
        if folder not in folders:
            folders[folder] = []
        folders[folder].append(s.to_dict())

    return jsonify({"folders": folders})


@player_bp.route("/api/stream/<int:song_id>")
@login_required
def stream(song_id):
    """Stream a song via proxy (avoids CORS/presigned URL hostname issues)."""
    song = Song.query.filter_by(id=song_id, user_id=current_user.id).first()
    if not song:
        return jsonify({"error": "Not found"}), 404
    if not current_user.minio_bucket:
        return jsonify({"error": "No storage"}), 500

    minio = get_minio()
    data = minio.get_object(current_user.minio_bucket, song.object_name)
    if not data:
        return jsonify({"error": "Could not load file"}), 500

    return Response(
        data,
        mimetype="audio/mpeg",
        headers={"Content-Disposition": f"inline; filename=\"{song.title or 'track'}.mp3\""},
    )


@player_bp.route("/api/search")
@login_required
def search():
    """Search user's library by title, artist, album."""
    q = request.args.get("q", "").strip()[:100]
    if not q:
        return jsonify({"songs": []})

    songs = Song.query.filter(
        Song.user_id == current_user.id,
        db.or_(
            Song.title.ilike(f"%{q}%"),
            Song.artist.ilike(f"%{q}%"),
            Song.album.ilike(f"%{q}%"),
            Song.playlist_name.ilike(f"%{q}%"),
        ),
    ).limit(50).all()

    return jsonify({"songs": [s.to_dict() for s in songs]})
