"""Kết nối R2 qua API S3 và đọc thông tin object."""

from dataclasses import dataclass

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from backend.config import R2Settings


def is_missing_object(error: ClientError) -> bool:
    code = error.response["Error"].get("Code")
    return code in {"404", "NotFound", "NoSuchKey"}


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    size: int
    sha256: str | None
    content_type: str
    cache_control: str | None

    @classmethod
    def from_response(cls, response: dict) -> "ObjectInfo":
        return cls(
            size=response["ContentLength"],
            sha256=response.get("Metadata", {}).get("sha256"),
            content_type=response.get("ContentType", "application/octet-stream"),
            cache_control=response.get("CacheControl"),
        )


class R2Client:
    def __init__(self, config: R2Settings) -> None:
        self.bucket = config.bucket_name
        self.upload_max_retries = config.upload_max_retries
        self.s3 = boto3.client(
            "s3",
            endpoint_url=config.endpoint_url,
            region_name="auto",
            aws_access_key_id=config.access_key_id,
            aws_secret_access_key=config.secret_access_key,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"mode": "standard", "total_max_attempts": 3},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
                max_pool_connections=max(10, config.upload_concurrency + 4),
                connect_timeout=config.connect_timeout,
                read_timeout=config.read_timeout,
            ),
        )

    def check_bucket(self) -> None:
        self.s3.head_bucket(Bucket=self.bucket)

    def head(self, key: str) -> ObjectInfo | None:
        try:
            response = self.s3.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if not is_missing_object(exc):
                raise
            return None
        return ObjectInfo.from_response(response)

    def open(self, key: str) -> dict:
        return self.s3.get_object(Bucket=self.bucket, Key=key)
