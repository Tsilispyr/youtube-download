"""
Admin blueprint — user management panel.
Only accessible to users with is_admin=True.
Mounted at /AudioWeb/admin
"""
import functools
from flask import Blueprint, render_template, jsonify, request, redirect, url_for, flash
from flask_login import login_required, current_user
from extensions import db
from models import User, Song

admin_bp = Blueprint("admin", __name__, url_prefix="/AudioWeb/admin")


def admin_required(f):
    @functools.wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            flash("Admin access required.", "error")
            return redirect(url_for("yt_download.index"))
        return f(*args, **kwargs)
    return decorated


@admin_bp.route("/")
@admin_required
def index():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template("admin/index.html", users=users)


@admin_bp.route("/api/users")
@admin_required
def api_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return jsonify({"users": [u.to_dict() for u in users]})


@admin_bp.route("/api/users/<int:user_id>", methods=["PATCH"])
@admin_required
def api_update_user(user_id):
    if user_id == current_user.id:
        return jsonify({"error": "Cannot modify your own account via admin panel"}), 400
    user = User.query.get_or_404(user_id)
    data = request.get_json(force=True)

    if "display_name" in data:
        user.display_name = str(data["display_name"])[:100]
    if "email_verified" in data:
        user.email_verified = bool(data["email_verified"])
    if "is_admin" in data:
        user.is_admin = bool(data["is_admin"])
    if "is_active" in data:
        user.is_active_account = bool(data["is_active"])
    if "email" in data:
        new_email = str(data["email"]).strip().lower()
        if new_email and new_email != user.email:
            if User.query.filter_by(email=new_email).first():
                return jsonify({"error": "Email already taken"}), 409
            user.email = new_email

    db.session.commit()
    return jsonify({"ok": True, "user": user.to_dict()})


@admin_bp.route("/api/users/<int:user_id>", methods=["DELETE"])
@admin_required
def api_delete_user(user_id):
    if user_id == current_user.id:
        return jsonify({"error": "Cannot delete your own account"}), 400
    user = User.query.get_or_404(user_id)

    # Delete user's songs first
    Song.query.filter_by(user_id=user_id).delete()
    db.session.delete(user)
    db.session.commit()
    return jsonify({"ok": True})


@admin_bp.route("/api/stats")
@admin_required
def api_stats():
    total_users = User.query.count()
    total_songs = Song.query.count()
    verified_users = User.query.filter_by(email_verified=True).count()
    admin_users = User.query.filter_by(is_admin=True).count()
    return jsonify({
        "total_users": total_users,
        "total_songs": total_songs,
        "verified_users": verified_users,
        "admin_users": admin_users,
    })


@admin_bp.route("/promote-first-admin")
def promote_first_admin():
    """One-time bootstrap: makes first registered user admin if no admin exists."""
    if User.query.filter_by(is_admin=True).count() > 0:
        return jsonify({"error": "Admin already exists"}), 403
    user = User.query.order_by(User.created_at.asc()).first()
    if not user:
        return jsonify({"error": "No users yet"}), 404
    user.is_admin = True
    db.session.commit()
    return jsonify({"ok": True, "promoted": user.email})
