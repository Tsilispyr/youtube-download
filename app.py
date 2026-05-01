"""
AudioWeb / AudioWorld — YT Downloader, Equalizer, Player.
"""
import os
from flask import Flask, redirect, url_for

from config import Config
from extensions import db, login_manager, mail
from models import User

from blueprints import auth_bp, yt_download_bp, spotify_download_bp, equalizer_bp, player_bp, admin_bp


def create_app(config_class=Config) -> Flask:
    app = Flask(__name__, static_folder="static")
    app.config.from_object(config_class)

    db.init_app(app)
    mail.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to access this page."

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    app.register_blueprint(auth_bp)
    app.register_blueprint(yt_download_bp)
    app.register_blueprint(spotify_download_bp)
    app.register_blueprint(equalizer_bp)
    app.register_blueprint(player_bp)
    app.register_blueprint(admin_bp)

    @app.route("/")
    def index():
        return redirect(url_for("yt_download.index"))

    @app.route("/favicon.ico")
    def favicon():
        p = os.path.join(app.static_folder or "", "favicon.ico")
        if os.path.isfile(p):
            return app.send_static_file("favicon.ico")
        return "", 404

    with app.app_context():
        db.create_all()

        # Auto-promote first user to admin if no admin exists (dev convenience)
        admin_email = os.environ.get("ADMIN_EMAIL", "")
        if admin_email and User.query.filter_by(is_admin=True).count() == 0:
            u = User.query.filter_by(email=admin_email.lower()).first()
            if u:
                u.is_admin = True
                db.session.commit()

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
