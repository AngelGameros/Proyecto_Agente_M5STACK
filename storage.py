"""Cliente MinIO reutilizable: crea buckets públicos de lectura y sube archivos."""
import io
import json
import mimetypes
import uuid

from minio import Minio

import config

_client: Minio | None = None


def get_client() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            config.MINIO_ENDPOINT,
            access_key=config.MINIO_ACCESS_KEY,
            secret_key=config.MINIO_SECRET_KEY,
            secure=config.MINIO_SECURE,
        )
    return _client


def ensure_bucket(bucket: str) -> None:
    """Crea el bucket si no existe y le da política de lectura pública."""
    c = get_client()
    if not c.bucket_exists(bucket):
        c.make_bucket(bucket)
    policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"AWS": ["*"]},
            "Action": ["s3:GetObject"],
            "Resource": [f"arn:aws:s3:::{bucket}/*"],
        }],
    }
    c.set_bucket_policy(bucket, json.dumps(policy))


def public_url(bucket: str, object_name: str) -> str:
    scheme = "https" if config.MINIO_SECURE else "http"
    return f"{scheme}://{config.MINIO_ENDPOINT}/{bucket}/{object_name}"


def upload_bytes(bucket: str, data: bytes, filename: str, prefix: str = "") -> str:
    """Sube bytes a MinIO y devuelve la URL pública del objeto."""
    ensure_bucket(bucket)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    object_name = f"{prefix.strip('/') + '/' if prefix else ''}{uuid.uuid4().hex}.{ext}"
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    get_client().put_object(
        bucket, object_name, io.BytesIO(data), length=len(data), content_type=content_type
    )
    return public_url(bucket, object_name)


def list_objects(bucket: str) -> list[dict]:
    ensure_bucket(bucket)
    return [
        {"name": o.object_name, "size": o.size, "url": public_url(bucket, o.object_name)}
        for o in get_client().list_objects(bucket, recursive=True)
    ]
