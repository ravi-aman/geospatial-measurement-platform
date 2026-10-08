"""Filesystem-backed object storage for development and single-host deployments."""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from pathlib import Path

from app.storage.base import StorageError, validate_key


class LocalStorage:
    name = "local"

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / validate_key(key)).resolve()
        if not path.is_relative_to(self.root):  # defence in depth; validate_key already forbids '..'
            raise ValueError(f"key escapes storage root: {key!r}")
        return path

    def put_file(self, key: str, source: Path, content_type: str | None = None) -> None:
        target = self._path(key)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # write-then-rename: readers never observe a partially written object
            fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".upload-")
            os.close(fd)
            tmp = Path(tmp_name)
            try:
                shutil.copyfile(source, tmp)
                tmp.replace(target)
            finally:
                with contextlib.suppress(FileNotFoundError):
                    tmp.unlink()
        except OSError as exc:
            raise StorageError(f"local storage write failed for {key}: {exc}") from exc

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def download_to(self, key: str, destination: Path) -> Path:
        try:
            shutil.copyfile(self._path(key), destination)
        except FileNotFoundError as exc:
            raise StorageError(f"object not found: {key}") from exc
        except OSError as exc:
            raise StorageError(f"local storage read failed for {key}: {exc}") from exc
        return destination

    def delete(self, key: str) -> None:
        with contextlib.suppress(FileNotFoundError):
            self._path(key).unlink()

    def check(self) -> None:
        if not os.access(self.root, os.W_OK):
            raise StorageError(f"storage root is not writable: {self.root}")
