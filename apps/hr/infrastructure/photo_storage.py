from __future__ import annotations

import logging

from django.conf import settings

logger = logging.getLogger(__name__)

_BUCKET = settings.MINIO_BUCKET_ATTENDANCE


def _client():
    import boto3  # lazy: tests mock _client → boto3 not needed at import time

    scheme = "https" if settings.MINIO_USE_SSL else "http"
    return boto3.client(
        "s3",
        endpoint_url=f"{scheme}://{settings.MINIO_ENDPOINT}",
        aws_access_key_id=settings.MINIO_ACCESS_KEY,
        aws_secret_access_key=settings.MINIO_SECRET_KEY,
        region_name="us-east-1",
    )


def put_photo(key: str, data: bytes, content_type: str) -> str:
    try:
        from botocore.exceptions import ClientError  # lazy: tests mock _client
    except ImportError:  # botocore absent in unit-test image; client is mocked anyway
        ClientError = Exception

    client = _client()
    try:
        client.head_bucket(Bucket=_BUCKET)
    except ClientError:
        client.create_bucket(Bucket=_BUCKET)
    client.put_object(Bucket=_BUCKET, Key=key, Body=data, ContentType=content_type)
    return key


def get_object_bytes(key: str) -> tuple[bytes, str]:
    client = _client()
    obj = client.get_object(Bucket=_BUCKET, Key=key)
    body = obj["Body"].read()
    content_type = obj.get("ContentType") or "application/octet-stream"
    return body, content_type


def presigned_get_url(key: str, expires: int = 300) -> str:
    client = _client()
    return client.generate_presigned_url(
        "get_object", Params={"Bucket": _BUCKET, "Key": key}, ExpiresIn=expires
    )


def delete_photo(key: str) -> bool:
    """Xoá 1 object ảnh chấm công trên MinIO. Dùng bởi retention purge.

    Trả ``True`` nếu xoá thành công. S3-compatible ``delete_object`` là idempotent
    (204 No Content ngay cả khi key không tồn tại) nên exception ở đây là lỗi
    THẬT: ``ClientError`` (permission/bucket sai) hoặc ``BotoCoreError`` (lỗi
    mạng — EndpointConnectionError, ConnectTimeout... — hierarchy RIÊNG, không
    phải subclass của ClientError; phải bắt cả hai kẻo MinIO sập là exception
    xuyên thủng purge task giữa batch). Trả ``False`` trong trường hợp đó để
    caller (purge task) KHÔNG xoá `photo_key` trên AttendanceEvent, giữ dấu vết
    để lần chạy sau thử xoá lại — tránh vừa mất tham chiếu vừa để object mồ côi
    vĩnh viễn trên MinIO mà không ai biết.
    """
    try:
        from botocore.exceptions import (  # lazy: tests mock _client
            BotoCoreError,
            ClientError,
        )

        storage_errors: tuple[type[Exception], ...] = (ClientError, BotoCoreError)
    except ImportError:  # botocore absent in unit-test image; client is mocked anyway
        storage_errors = (Exception,)

    client = _client()
    try:
        client.delete_object(Bucket=_BUCKET, Key=key)
        return True
    except storage_errors:
        logger.warning("failed deleting attendance photo %s from MinIO", key, exc_info=True)
        return False
