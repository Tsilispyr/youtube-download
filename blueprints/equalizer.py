"""
Equalizer blueprint.
Mounted at /AudioWeb/equalizer
Supports: standalone EQ/visualizer + upload-to-library when logged in.
"""
import os
import io
import tempfile
from flask import Blueprint, render_template, request, jsonify
from flask_login import current_user, login_required

equalizer_bp = Blueprint("equalizer", __name__, url_prefix="/AudioWeb/equalizer")


@equalizer_bp.route("/")
def index():
    return render_template("equalizer.html")


@equalizer_bp.route("/api/upload-to-library", methods=["POST"])
@login_required
def upload_to_library():
    """Upload a local audio file to user's MinIO library (Downloads folder)."""
    from flask import current_app
    from extensions import db
    from models import Song
    from services import MinIOService

    if not current_user.minio_bucket:
        return jsonify({"error": "No storage bucket for user"}), 500

    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file provided"}), 400

    filename = file.filename or "track.mp3"
    # Sanitize filename
    safe_name = "".join(c for c in filename if c.isalnum() or c in "._- ").strip()
    if not safe_name:
        safe_name = "track.mp3"

    title = os.path.splitext(safe_name)[0]
    object_name = f"Downloads/{safe_name}"

    # Check if already in library (by object_name)
    existing = Song.query.filter_by(
        user_id=current_user.id,
        object_name=object_name
    ).first()
    if existing:
        return jsonify({"ok": True, "song": existing.to_dict(), "already_exists": True})

    c = current_app.config
    minio = MinIOService(
        c["MINIO_ENDPOINT"], c["MINIO_ACCESS_KEY"], c["MINIO_SECRET_KEY"], c["MINIO_SECURE"]
    )

    data = file.read()
    file_size = len(data)
    buf = io.BytesIO(data)

    ok = minio.put_object(
        current_user.minio_bucket,
        object_name,
        buf,
        content_type="audio/mpeg",
    )
    if not ok:
        return jsonify({"error": "Failed to upload to storage"}), 500

    song = Song(
        user_id=current_user.id,
        minio_path=object_name,
        object_name=object_name,
        title=title,
        playlist_name=None,
        file_size=file_size,
    )
    db.session.add(song)
    db.session.commit()

    # Also save to the shared vault (audioweb-library)
    try:
        from models import VaultSong
        vault_bucket = current_app.config.get("MINIO_WIDE_BUCKET", "audioweb-library")
        vault_obj = f"Vault/{safe_name}"
        if not VaultSong.query.filter_by(object_name=vault_obj).first():
            minio.ensure_wide_bucket(vault_bucket)
            buf2 = io.BytesIO(data)
            minio.put_object(vault_bucket, vault_obj, buf2, content_type="audio/mpeg")
            vault_song = VaultSong(
                object_name=vault_obj,
                title=title,
                file_size=file_size,
            )
            db.session.add(vault_song)
            db.session.commit()
    except Exception:
        pass  # vault save is best-effort

    return jsonify({"ok": True, "song": song.to_dict(), "already_exists": False})
