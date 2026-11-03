"""Lưu ảnh sản phẩm trên MinIO.

Sao đúng pattern của apps/inventory/infrastructure/document_storage.py (lazy import
boto3 để test mock được _client mà không cần boto3 trong image test), chỉ khác bucket.
Không tái dùng thẳng module của inventory: app orders không nên phụ thuộc app inventory.
"""
from __future__ import annotations

from typing import Any

from django.conf import settings

_BUCKET = settings.MINIO_BUCKET_PRODUCTS


def _client():
    import boto3  # lazy: test mock _client → không cần boto3 lúc import

    scheme = "https" if settings.MINIO_USE_SSL else "http"
    return boto3.client(
        "s3",
        endpoint_url=f"{scheme}://{settings.MINIO_ENDPOINT}",
        aws_access_key_id=settings.MINIO_ACCESS_KEY,
        aws_secret_access_key=settings.MINIO_SECRET_KEY,
        region_name="us-east-1",
    )


def put_object(key: str, data: bytes, content_type: str) -> str:
    try:
        from botocore.exceptions import ClientError
    except ImportError:  # botocore vắng trong image unit-test; client được mock
        ClientError = Exception

    client = _client()
    try:
        client.head_bucket(Bucket=_BUCKET)
    except ClientError:
        client.create_bucket(Bucket=_BUCKET)
    client.put_object(Bucket=_BUCKET, Key=key, Body=data, ContentType=content_type)
    return key


def get_object_bytes(key: str) -> tuple[bytes, str]:
    obj = _client().get_object(Bucket=_BUCKET, Key=key)
    return obj["Body"].read(), (obj.get("ContentType") or "application/octet-stream")


def get_object_stream(key: str) -> tuple[Any, str, int | None]:
    """Như `get_object_bytes` nhưng KHÔNG đọc hết object vào RAM.

    Dùng cho endpoint public phục vụ ảnh trực tiếp (không qua hàng đợi/worker):
    trước khi Task 12 áp trần dung lượng upload, một object lớn đọc bằng
    `.read()` là một cú tăng đột biến bộ nhớ trên mỗi request, ở một endpoint
    không cần đăng nhập. `obj["Body"]` của boto3 vốn đã là file-like streaming
    — trả thẳng nó ra thay vì gọi `.read()`.
    """
    obj = _client().get_object(Bucket=_BUCKET, Key=key)
    content_type = obj.get("ContentType") or "application/octet-stream"
    content_length = obj.get("ContentLength")
    return obj["Body"], content_type, content_length


def delete_object(key: str) -> None:
    _client().delete_object(Bucket=_BUCKET, Key=key)
