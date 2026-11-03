from __future__ import annotations

from django.conf import settings

_BUCKET = settings.MINIO_BUCKET_DOCUMENTS


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


def put_object(key: str, data: bytes, content_type: str) -> str:
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


def delete_object(key: str) -> None:
    client = _client()
    client.delete_object(Bucket=_BUCKET, Key=key)
