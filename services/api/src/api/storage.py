"""Object storage for persona photos: local filesystem (dev) or S3 (prod)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol

from api.settings import settings


class Storage(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str) -> bytes | None: ...
    async def delete(self, key: str) -> None: ...
    async def healthy(self) -> bool: ...


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise ValueError("invalid key")
        return p

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(p.write_bytes, data)

    async def get(self, key: str) -> bytes | None:
        p = self._path(key)
        return await asyncio.to_thread(p.read_bytes) if p.exists() else None

    async def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    async def healthy(self) -> bool:
        return self.root.exists()


class S3Storage:
    def __init__(self, bucket: str, region: str | None) -> None:
        import boto3

        self.bucket = bucket
        self.client = boto3.client("s3", region_name=region)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self.client.put_object, Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
        )

    async def get(self, key: str) -> bytes | None:
        from botocore.exceptions import ClientError

        try:
            obj = await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=key)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return None
            raise
        return await asyncio.to_thread(obj["Body"].read)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=key)

    async def healthy(self) -> bool:
        try:
            await asyncio.to_thread(self.client.head_bucket, Bucket=self.bucket)
            return True
        except Exception:
            return False


def make_storage() -> Storage:
    if settings.storage_backend == "s3":
        if not settings.s3_bucket:
            raise RuntimeError("STORAGE_BACKEND=s3 needs S3_BUCKET")
        return S3Storage(settings.s3_bucket, settings.s3_region)
    return LocalStorage(settings.local_storage_dir)


storage: Storage = make_storage()
