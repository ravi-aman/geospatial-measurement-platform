from __future__ import annotations

from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from app.config import Settings
from app.storage import StorageError, build_storage
from app.storage.base import validate_key
from app.storage.local import LocalStorage
from app.storage.s3 import S3Storage


@pytest.mark.parametrize("key", ["../x", "/abs", "a//b", "UPPER", "", "a/../b", "x" * 600])
def test_invalid_keys_rejected(key: str) -> None:
    with pytest.raises(ValueError, match="invalid storage key"):
        validate_key(key)


def test_valid_key() -> None:
    assert validate_key("uploads/ab/abcdef.kml") == "uploads/ab/abcdef.kml"


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "source.bin"
    path.write_bytes(b"hello geospatial")
    return path


class TestLocalStorage:
    def test_roundtrip(self, tmp_path: Path, source: Path) -> None:
        storage = LocalStorage(tmp_path / "root")
        assert not storage.exists("uploads/ab/x.kml")
        storage.put_file("uploads/ab/x.kml", source)
        assert storage.exists("uploads/ab/x.kml")
        out = storage.download_to("uploads/ab/x.kml", tmp_path / "copy.bin")
        assert out.read_bytes() == b"hello geospatial"
        storage.delete("uploads/ab/x.kml")
        storage.delete("uploads/ab/x.kml")  # idempotent
        assert not storage.exists("uploads/ab/x.kml")
        storage.check()

    def test_missing_object(self, tmp_path: Path) -> None:
        with pytest.raises(StorageError, match="not found"):
            LocalStorage(tmp_path).download_to("uploads/none.kml", tmp_path / "o")

    def test_overwrite_is_atomic_and_leaves_no_temp_files(self, tmp_path: Path, source: Path) -> None:
        storage = LocalStorage(tmp_path / "root")
        storage.put_file("k/a.bin", source)
        storage.put_file("k/a.bin", source)
        assert [p.name for p in (tmp_path / "root" / "k").iterdir()] == ["a.bin"]


class TestS3Storage:
    @pytest.fixture
    def s3(self) -> S3Storage:
        with mock_aws():
            client = boto3.client("s3", region_name="us-east-1")
            client.create_bucket(Bucket="geo-bucket")
            yield S3Storage("geo-bucket", prefix="env/", client=client)  # type: ignore[misc]

    def test_roundtrip_with_prefix(self, s3: S3Storage, source: Path, tmp_path: Path) -> None:
        assert not s3.exists("uploads/ab/x.zip")
        s3.put_file("uploads/ab/x.zip", source, "application/zip")
        assert s3.exists("uploads/ab/x.zip")
        head = s3._client.head_object(Bucket="geo-bucket", Key="env/uploads/ab/x.zip")
        assert head["ContentType"] == "application/zip"
        assert s3.download_to("uploads/ab/x.zip", tmp_path / "d.bin").read_bytes() == b"hello geospatial"
        s3.delete("uploads/ab/x.zip")
        assert not s3.exists("uploads/ab/x.zip")
        s3.check()

    def test_errors_are_wrapped(self, s3: S3Storage, tmp_path: Path) -> None:
        with pytest.raises(StorageError):
            s3.download_to("uploads/missing.zip", tmp_path / "x")
        with pytest.raises(StorageError):
            S3Storage("no-such-bucket", client=s3._client).check()


def test_factory(tmp_path: Path) -> None:
    local = build_storage(Settings(_env_file=None, storage_local_root=tmp_path))  # type: ignore[call-arg]
    assert local.name == "local"
    with pytest.raises(ValueError, match="S3_BUCKET"):
        build_storage(Settings(_env_file=None, storage_backend="s3"))  # type: ignore[call-arg]
    with mock_aws():
        s3 = build_storage(Settings(_env_file=None, storage_backend="s3", s3_bucket="b", s3_region="us-east-1"))  # type: ignore[call-arg]
        assert s3.name == "s3"
