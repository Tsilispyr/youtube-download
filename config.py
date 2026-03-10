"""
Configuration for AudioWeb / AudioWorld application.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "sqlite:///audioweb.db",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # MinIO / S3-compatible storage
    MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "localhost:9000")
    MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
    MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "minioadmin")
    MINIO_SECURE = os.environ.get("MINIO_SECURE", "false").lower() == "true"
    MINIO_BUCKET_PREFIX = os.environ.get("MINIO_BUCKET_PREFIX", "user-")
    MINIO_WIDE_BUCKET = os.environ.get("MINIO_WIDE_BUCKET", "audioweb-library")

    # Email (for verification)
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", "noreply@audioweb.local")

    # App URLs (for email links)
    APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:5000")
