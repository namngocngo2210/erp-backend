"""MinIO cho file export — cùng khuôn ``apps.inventory.infrastructure.document_storage``.

Khác một điểm quan trọng: KHÔNG có hàm đọc-hết-vào-RAM kiểu ``get_object_bytes``.
File export tính bằng chục MB, nạp trọn vào RAM của ``desi-web`` lúc tải là tái lập
đúng vấn đề mà cả thiết kế này sinh ra để dẹp. Chỉ có ``open_stream``.
"""

from __future__ import annotations

from django.conf import settings

try:  # botocore vắng mặt trong image unit-test; client luôn được mock ở đó
    from botocore.exceptions import ClientError as _ClientError
except ImportError:  # pragma: no cover
    _ClientError = Exception


def _bucket() -> str:
    return settings.MINIO_BUCKET_EXPORTS


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


def upload_file(key: str, path: str, content_type: str) -> None:
    client = _client()
    try:
        client.head_bucket(Bucket=_bucket())
    except _ClientError:
        client.create_bucket(Bucket=_bucket())
    client.upload_file(
        Filename=path,
        Bucket=_bucket(),
        Key=key,
        ExtraArgs={"ContentType": content_type},
    )


def open_stream(key: str):
    """Trả về body dạng stream (có .read(size)) để FileResponse đọc theo chunk."""
    return _client().get_object(Bucket=_bucket(), Key=key)["Body"]


def delete_object(key: str) -> None:
    _client().delete_object(Bucket=_bucket(), Key=key)
