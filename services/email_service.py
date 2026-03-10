"""
Email service for verification and notifications.
"""
from flask import current_app
from flask_mail import Message


def send_verification_email(email: str, token: str) -> bool:
    """Send email verification link."""
    app = current_app._get_current_object()
    url = f"{app.config.get('APP_BASE_URL', '')}/AudioWeb/verify?token={token}"
    msg = Message(
        subject="Verify your AudioWeb email",
        recipients=[email],
        body=f"Click to verify: {url}",
        html=f'<p>Click <a href="{url}">here</a> to verify your email.</p>',
    )
    try:
        from extensions import mail
        mail.send(msg)
        return True
    except Exception:
        return False
