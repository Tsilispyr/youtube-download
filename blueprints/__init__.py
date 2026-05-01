from .auth import auth_bp
from .yt_download import yt_download_bp
from .spotify_download import spotify_download_bp
from .equalizer import equalizer_bp
from .player import player_bp
from .admin import admin_bp

__all__ = ["auth_bp", "yt_download_bp", "spotify_download_bp", "equalizer_bp", "player_bp", "admin_bp"]
