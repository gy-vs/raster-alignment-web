import os
import shutil
import threading
import time
import uuid
from pathlib import Path

from .geo import RasterEntry, read_metadata


class SessionNotFound(KeyError):
    pass


class SessionStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, session_id: str | None = None) -> "Session":
        session_id = session_id or uuid.uuid4().hex
        if not session_id.replace("-", "").isalnum() or len(session_id) > 64:
            raise ValueError("invalid session id")
        path = self.root / session_id
        path.mkdir(parents=True, exist_ok=False)
        session = Session(session_id, path)
        with self._lock:
            self._sessions[session_id] = session
        return session

    def get_or_create(self, session_id: str) -> "Session":
        try:
            return self.get(session_id)
        except SessionNotFound:
            return self.create(session_id)

    def get(self, session_id: str) -> "Session":
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFound(session_id)
        return session

    def shutdown(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.close_files()
        shutil.rmtree(self.root, ignore_errors=True)


class Session:
    def __init__(self, session_id: str, path: Path) -> None:
        self.id = session_id
        self.path = path
        self.created_at = time.time()
        self._entries = {"a": None, "b": None}
        self._lock = threading.Lock()
        self._upload_locks = {"a": threading.Lock(), "b": threading.Lock()}
        self.computation_lock = threading.RLock()

    @property
    def lock(self) -> threading.Lock:
        return self._lock

    def entry(self, slot: str) -> RasterEntry | None:
        with self._lock:
            return self._entries.get(slot)

    def snapshot(self) -> dict[str, RasterEntry | None]:
        with self._lock:
            return dict(self._entries)

    def replace_file(self, slot: str, source_stream, original_filename: str) -> RasterEntry:
        if slot not in self._upload_locks:
            raise ValueError("slot 必须是 a 或 b")
        with self._upload_locks[slot]:
            # Views/point reads hold the computation lock for their whole read.
            # This prevents replacing and unlinking a path while an older request
            # is still rasterizing it.
            with self.computation_lock:
                return self._replace_file_unlocked(slot, source_stream, original_filename)

    def _replace_file_unlocked(self, slot: str, source_stream, original_filename: str) -> RasterEntry:
        safe_stem = Path(original_filename or "upload.tif").name or "upload.tif"
        original_suffix = Path(safe_stem).suffix.lower()
        # Rasterio identifies the content through GDAL's format detection; keeping
        # a .tif spool suffix is safer for less common user-supplied extensions.
        suffix = original_suffix if original_suffix in {".tif", ".tiff"} else ".tif"
        file_id = uuid.uuid4().hex
        target_name = f"{file_id}{suffix}"
        tmp_path = self.path / f".{file_id}.tmp-{uuid.uuid4().hex}"
        final_path = self.path / target_name

        try:
            # Spooled uploads from FastAPI are already temporary files; this stream
            # copy keeps the endpoint independent of that implementation detail.
            with tmp_path.open("wb") as out:
                shutil.copyfileobj(source_stream, out, length=1024 * 1024)
            size_bytes = tmp_path.stat().st_size
            if size_bytes <= 0:
                raise ValueError("上传文件为空")

            metadata = read_metadata(
                file_id=file_id,
                slot=slot,  # type: ignore[arg-type]
                path=str(tmp_path),
                filename=safe_stem,
                size_bytes=size_bytes,
                opened_at=time.time(),
            )
            os.replace(tmp_path, final_path)

            entry = RasterEntry(
                file_id=file_id,
                slot=slot,  # type: ignore[arg-type]
                path=str(final_path),
                filename=safe_stem,
                size_bytes=size_bytes,
                opened_at=time.time(),
                metadata=metadata,
            )
            with self._lock:
                old = self._entries.get(slot)
                self._entries[slot] = entry
            if old is not None:
                try:
                    Path(old.path).unlink(missing_ok=True)
                except OSError:
                    pass
            return entry
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def close_files(self) -> None:
        # Raster files are opened per request; only spooled files need cleanup.
        with self._lock:
            entries = list(self._entries.values())
            self._entries = {"a": None, "b": None}
        for entry in entries:
            if entry is not None:
                try:
                    Path(entry.path).unlink(missing_ok=True)
                except OSError:
                    pass
