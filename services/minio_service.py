"""
MinIO service for per-user buckets and song storage.
- Per-user bucket: user-{user_id}
- Playlists: {playlist_name}/song.mp3
- Single songs: Downloads/song.mp3
- Wide bucket: audioweb-library (for search — copies metadata index)
"""
import io
import os
import re
from typing import Optional

from minio import Minio
from minio.error import S3Error


def sanitize_bucket_name(name: str) -> str:
    """MinIO buckets must be lowercase, 3-63 chars, alphanumeric + hyphen."""
    s = re.sub(r"[^a-z0-9-]", "", name.lower())[:63]
    return s or "default"


class MinIOService:
    def __init__(self, endpoint: str, access_key: str, secret_key: str, secure: bool = False):
        self.client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )

    def ensure_user_bucket(self, bucket_name: str) -> bool:
        """Create user bucket if it doesn't exist."""
        try:
            if not self.client.bucket_exists(bucket_name):
                self.client.make_bucket(bucket_name)
            return True
        except S3Error:
            return False

    def ensure_wide_bucket(self, bucket_name: str) -> bool:
        """Create library-wide bucket for search index if needed."""
        return self.ensure_user_bucket(bucket_name)

    def put_object(
        self,
        bucket: str,
        object_name: str,
        data: bytes | io.BytesIO,
        content_type: str = "audio/mpeg",
        metadata: Optional[dict] = None,
    ) -> bool:
        """Upload a file to MinIO."""
        try:
            if isinstance(data, bytes):
                data = io.BytesIO(data)
            length = data.getbuffer().nbytes
            self.client.put_object(
                bucket,
                object_name,
                data,
                length,
                content_type=content_type,
                metadata=metadata or {},
            )
            return True
        except S3Error:
            return False

    def put_file(self, bucket: str, object_name: str, file_path: str, content_type: str = None) -> bool:
        """Upload a local file to MinIO."""
        try:
            if content_type is None:
                ext = os.path.splitext(file_path)[1].lower()
                content_type = "image/jpeg" if ext in (".jpg", ".jpeg") else (
                    "image/png" if ext == ".png" else "image/webp" if ext == ".webp" else "audio/mpeg"
                )
            self.client.fput_object(
                bucket,
                object_name,
                file_path,
                content_type=content_type,
            )
            return True
        except S3Error:
            return False

    def get_object_url(self, bucket: str, object_name: str, expires_seconds: int = 3600) -> Optional[str]:
        """Generate presigned URL for streaming/download."""
        try:
            return self.client.presigned_get_object(bucket, object_name, expires=expires_seconds)
        except S3Error:
            return None

    def get_object(self, bucket: str, object_name: str) -> Optional[bytes]:
        """Get object bytes."""
        try:
            resp = self.client.get_object(bucket, object_name)
            data = resp.read()
            resp.close()
            resp.release_conn()
            return data
        except S3Error:
            return None

    def list_objects(self, bucket: str, prefix: str = "") -> list:
        """List objects with optional prefix."""
        try:
            return list(self.client.list_objects(bucket, prefix=prefix, recursive=True))
        except S3Error:
            return []

    def delete_object(self, bucket: str, object_name: str) -> bool:
        try:
            self.client.remove_object(bucket, object_name)
            return True
        except S3Error:
            return False
