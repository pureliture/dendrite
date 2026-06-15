from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from .events import validate_event


class JsonFileSpool:
    DEFAULT_SUBDIRS = ("pending", "processing", "acked", "quarantine")

    def __init__(
        self,
        root: Path | str,
        *,
        subdirs: tuple[str, ...] | None = None,
        root_label: str = "spool",
    ):
        self.root = Path(root)
        self.subdirs = tuple(subdirs or self.DEFAULT_SUBDIRS)
        self.root_label = root_label
        if self.root.is_symlink():
            raise ValueError(f"{self.root_label} root must not be a symlink")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        for name in self.subdirs:
            path = self.root / name
            if path.is_symlink():
                raise ValueError(f"{self.root_label} subdirectory must not be a symlink: {name}")
            path.mkdir(mode=0o700, exist_ok=True)
            os.chmod(path, 0o700)

    def write_json_once(
        self,
        filename: str,
        payload: dict,
        *,
        subdir: str = "pending",
        separators: tuple[str, str] | None = None,
    ) -> Path:
        filename = self._validated_filename(filename)
        existing = self.find_existing(filename)
        if existing is not None:
            return existing
        final_path = self._subdir_path(subdir) / filename
        temp_path = self._write_json_temp(final_path, payload, separators=separators)
        try:
            try:
                os.link(temp_path, final_path)
            except FileExistsError:
                existing = self.find_existing(filename)
                if existing is not None:
                    return existing
                raise
            os.chmod(final_path, 0o600)
            return final_path
        finally:
            temp_path.unlink(missing_ok=True)

    def replace_json(
        self,
        path: Path | str,
        payload: dict,
        *,
        separators: tuple[str, str] | None = None,
    ) -> Path:
        final_path = Path(path)
        self._assert_managed_path(final_path)
        temp_path = self._write_json_temp(final_path, payload, separators=separators)
        try:
            os.replace(temp_path, final_path)
            os.chmod(final_path, 0o600)
        except Exception:
            try:
                temp_path.unlink(missing_ok=True)
            finally:
                raise
        return final_path

    def _write_json_temp(
        self,
        final_path: Path,
        payload: dict,
        *,
        separators: tuple[str, str] | None = None,
    ) -> Path:
        temp_path = final_path.with_name(f".{final_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(temp_path, flags, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, separators=separators)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, 0o600)
        except Exception:
            try:
                temp_path.unlink(missing_ok=True)
            finally:
                raise
        return temp_path

    def find_existing(self, name: str) -> Path | None:
        name = self._validated_filename(name)
        for subdir in self.subdirs:
            candidate = self.root / subdir / name
            if candidate.exists():
                return candidate
        return None

    def files(self, subdir: str) -> list[Path]:
        return sorted(self._subdir_path(subdir).glob("*.json"))

    def claim_next(self, *, empty_message: str = "no pending spool events") -> Path:
        pending = self.files("pending")
        if not pending:
            raise FileNotFoundError(empty_message)
        return self.move_to(pending[0], "processing")

    def ack(self, processing_path: Path | str) -> Path:
        return self.move_to(processing_path, "acked")

    def quarantine(self, processing_path: Path | str) -> Path:
        return self.move_to(processing_path, "quarantine")

    def move_to(self, source_path: Path | str, subdir: str) -> Path:
        target_dir = self._subdir_path(subdir)
        source = Path(source_path)
        self._assert_managed_path(source)
        target = target_dir / source.name
        os.replace(source, target)
        os.chmod(target, 0o600)
        return target

    def depth_counts(self) -> dict[str, int]:
        return {name: len(self.files(name)) for name in self.subdirs}

    def _subdir_path(self, subdir: str) -> Path:
        if subdir not in self.subdirs:
            raise ValueError(f"unsupported spool subdirectory: {subdir}")
        return self.root / subdir

    def _assert_managed_path(self, path: Path) -> None:
        if path.parent not in {self.root / subdir for subdir in self.subdirs}:
            raise ValueError("spool path must be inside a managed subdirectory")

    def _validated_filename(self, filename: str) -> str:
        name = str(filename or "")
        if not name or name in {".", ".."} or "/" in name or "\\" in name or Path(name).name != name:
            raise ValueError("spool filename must be a file name")
        return name


class Spool(JsonFileSpool):
    SUBDIRS = JsonFileSpool.DEFAULT_SUBDIRS

    def __init__(self, root: Path | str):
        super().__init__(root, subdirs=self.SUBDIRS, root_label="spool")

    def enqueue(self, event: dict) -> Path:
        validate_event(event)
        name = f"{event['event_id']}.json"
        return self.write_json_once(name, event)

    def _find_existing(self, name: str) -> Path | None:
        return self.find_existing(name)
