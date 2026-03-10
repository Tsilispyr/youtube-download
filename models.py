"""
Database models for users, songs, playlists.
"""
from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(100))
    email_verified = db.Column(db.Boolean, default=False)
    verification_token = db.Column(db.String(64))
    verification_token_expires = db.Column(db.DateTime)
    minio_bucket = db.Column(db.String(255), unique=True, nullable=True)  # user-{user_id}
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Song(db.Model):
    """Metadata for each downloaded song (stored in MinIO)."""
    __tablename__ = "songs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    minio_path = db.Column(db.String(512), nullable=False)  # path in user bucket
    object_name = db.Column(db.String(512), nullable=False)  # MinIO object key
    title = db.Column(db.String(512))
    artist = db.Column(db.String(512))
    album = db.Column(db.String(512))
    duration_sec = db.Column(db.Integer)
    thumbnail_path = db.Column(db.String(512))  # path to cover in MinIO
    playlist_name = db.Column(db.String(512))  # null = single song (Downloads folder)
    source_url = db.Column(db.String(512))  # original YouTube URL
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("songs", lazy="dynamic"))

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title or "Unknown",
            "artist": self.artist or "",
            "album": self.album or "",
            "duration_sec": self.duration_sec,
            "playlist_name": self.playlist_name,
            "object_name": self.object_name,
        }
