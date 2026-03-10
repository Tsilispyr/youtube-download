"""
Auth blueprint: register, login, logout, email verification.
"""
import secrets
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user

from extensions import db
from models import User
from services import MinIOService, send_verification_email

auth_bp = Blueprint("auth", __name__)


def get_minio():
    from flask import current_app
    c = current_app.config
    return MinIOService(
        c["MINIO_ENDPOINT"],
        c["MINIO_ACCESS_KEY"],
        c["MINIO_SECRET_KEY"],
        c["MINIO_SECURE"],
    )


@auth_bp.route("/AudioWeb/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("player.player"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        display_name = request.form.get("display_name", "").strip()

        if not email or not password:
            flash("Email and password required", "error")
            return render_template("auth/register.html")

        if User.query.filter_by(email=email).first():
            flash("Email already registered", "error")
            return render_template("auth/register.html")

        user = User(email=email, display_name=display_name or email.split("@")[0])
        user.set_password(password)
        user.verification_token = secrets.token_urlsafe(32)
        user.verification_token_expires = datetime.utcnow() + timedelta(hours=24)

        db.session.add(user)
        db.session.flush()  # get user.id
        bucket = f"user-{user.id}"
        user.minio_bucket = bucket

        try:
            minio = get_minio()
            minio.ensure_user_bucket(bucket)
            minio.ensure_wide_bucket(current_app.config["MINIO_WIDE_BUCKET"])
        except Exception:
            pass  # MinIO optional at register time
        db.session.commit()

        send_verification_email(user.email, user.verification_token)
        flash("Check your email to verify your account", "info")
        return redirect(url_for("auth.login"))

    return render_template("auth/register.html")


@auth_bp.route("/AudioWeb/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("player.player"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user, remember=request.form.get("remember") == "on")
            return redirect(request.args.get("next") or url_for("player.player"))
        flash("Invalid email or password", "error")

    return render_template("auth/login.html")


@auth_bp.route("/AudioWeb/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("yt_download.index"))


@auth_bp.route("/AudioWeb/verify")
def verify():
    token = request.args.get("token")
    if not token:
        flash("Invalid verification link", "error")
        return redirect(url_for("auth.login"))

    user = User.query.filter_by(
        verification_token=token,
    ).first()
    if not user:
        flash("Invalid or expired verification link", "error")
        return redirect(url_for("auth.login"))
    if user.verification_token_expires and user.verification_token_expires < datetime.utcnow():
        flash("Verification link expired", "error")
        return redirect(url_for("auth.login"))

    user.email_verified = True
    user.verification_token = None
    user.verification_token_expires = None
    db.session.commit()

    flash("Email verified. You can now log in.", "success")
    return redirect(url_for("auth.login"))
