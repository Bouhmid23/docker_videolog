"""Abstraction de stockage objet compatible S3 (MinIO local et S3 managé)."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os
from typing import Any

import boto3
from botocore.config import Config


@dataclass(frozen=True)
class StorageConfig:
    endpoint_url: str
    public_endpoint_url: str
    access_key: str
    secret_key: str
    region: str
    bucket: str
    force_path_style: bool = True

    @classmethod
    def from_env(cls) -> "StorageConfig":
        endpoint = (
            os.environ.get("S3_ENDPOINT")
            or os.environ.get("MINIO_ENDPOINT")
            or "http://minio:9000"
        )
        public_endpoint = (
            os.environ.get("S3_PUBLIC_ENDPOINT")
            or os.environ.get("MINIO_PUBLIC_URL")
            or "http://localhost:9000"
        )
        access_key = (
            os.environ.get("S3_ACCESS_KEY")
            or os.environ.get("MINIO_ACCESS_KEY")
            or "minioadmin"
        )
        secret_key = (
            os.environ.get("S3_SECRET_KEY")
            or os.environ.get("MINIO_SECRET_KEY")
            or "minioadmin"
        )
        region = os.environ.get("S3_REGION", "us-east-1")
        bucket = (
            os.environ.get("S3_BUCKET")
            or os.environ.get("RECORDINGS_BUCKET")
            or "recordings"
        )
        force_path_style = (
            os.environ.get("S3_FORCE_PATH_STYLE", "true").strip().lower()
            in ("1", "true", "yes")
        )
        return cls(
            endpoint_url=endpoint,
            public_endpoint_url=public_endpoint,
            access_key=access_key,
            secret_key=secret_key,
            region=region,
            bucket=bucket,
            force_path_style=force_path_style,
        )


class ObjectStorage:
    """Client S3 unifié pour l'écriture interne et la signature de liens publics."""

    def __init__(self, config: StorageConfig | None = None):
        self.config = config or StorageConfig.from_env()
        addressing = "path" if self.config.force_path_style else "virtual"
        boto_cfg = Config(
            signature_version="s3v4",
            s3={"addressing_style": addressing},
        )
        self._internal = boto3.client(
            "s3",
            endpoint_url=self.config.endpoint_url,
            aws_access_key_id=self.config.access_key,
            aws_secret_access_key=self.config.secret_key,
            region_name=self.config.region,
            config=boto_cfg,
        )
        self._public = boto3.client(
            "s3",
            endpoint_url=self.config.public_endpoint_url,
            aws_access_key_id=self.config.access_key,
            aws_secret_access_key=self.config.secret_key,
            region_name=self.config.region,
            config=boto_cfg,
        )

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        bucket: str | None = None,
    ) -> str:
        target_bucket = bucket or self.config.bucket
        cleaned = key.lstrip("/")
        self._internal.put_object(
            Bucket=target_bucket,
            Key=cleaned,
            Body=data,
            ContentType=content_type,
        )
        return cleaned

    def get_bytes(self, key: str, *, bucket: str | None = None) -> bytes:
        target_bucket = bucket or self.config.bucket
        resp = self._internal.get_object(Bucket=target_bucket, Key=key.lstrip("/"))
        return resp["Body"].read()

    def delete_object(self, key: str, *, bucket: str | None = None) -> None:
        target_bucket = bucket or self.config.bucket
        self._internal.delete_object(Bucket=target_bucket, Key=key.lstrip("/"))

    def presign_get(
        self,
        key: str,
        *,
        expires_in: int = 3600,
        bucket: str | None = None,
    ) -> str:
        cleaned = key.lstrip("/")
        if not cleaned:
            return ""
        target_bucket = bucket or self.config.bucket
        return self._public.generate_presigned_url(
            "get_object",
            Params={"Bucket": target_bucket, "Key": cleaned},
            ExpiresIn=expires_in,
        )

    def list_objects(
        self,
        *,
        prefix: str | None = None,
        bucket: str | None = None,
    ) -> list[dict[str, Any]]:
        target_bucket = bucket or self.config.bucket
        paginator = self._internal.get_paginator("list_objects_v2")
        kwargs: dict[str, Any] = {"Bucket": target_bucket}
        if prefix:
            kwargs["Prefix"] = prefix
        items: list[dict[str, Any]] = []
        for page in paginator.paginate(**kwargs):
            for obj in page.get("Contents", []):
                items.append(
                    {
                        "key": obj["Key"],
                        "size_bytes": obj.get("Size", 0),
                        "last_modified": obj.get("LastModified"),
                    }
                )
        return items


@lru_cache(maxsize=1)
def get_storage() -> ObjectStorage:
    return ObjectStorage(StorageConfig.from_env())
