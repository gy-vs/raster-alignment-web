"""In-memory session store. Single-user local tool: no accounts, no sharing.

Each session keeps two independently addressed slots ("a" / "b"). Re-uploading
a slot replaces its dataset with a NEW dataset_id, so stale view results from
an old file can never be rendered as if they belonged to the new one.
"""
from __future__ import annotations

import os
import tempfile
import threading
import uuid
from dataclasses import dataclass, field

from .rasterio_ops import Dataset

SLOTS = ("a", "b")


@dataclass
class Slot:
    dataset: Dataset | None = None
    path: str | None = None
    band: int = 1


@dataclass
class Session:
    session_id: str
    slots: dict[str, Slot] = field(
        default_factory=lambda: {"a": Slot(), "b": Slot()}
    )
    created_files: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def get_slot(self, slot_name: str) -> Slot:
        if slot_name not in SLOTS:
            raise KeyError(f"未知槽位 {slot_name!r}，只支持 a/b")
        return self.slots[slot_name]

    def replace_slot(self, slot_name: str, path: str, filename: str) -> Dataset:
        slot = self.get_slot(slot_name)
        dataset_id = uuid.uuid4().hex
        # Open FIRST; only after success close and unlink the old file.
        new_ds = Dataset(dataset_id, filename, path)
        old_ds = slot.dataset
        old_path = slot.path
        slot.dataset = new_ds
        slot.path = path
        slot.band = 1
        self.created_files.append(path)
        if old_ds is not None:
            old_ds.close()
        if old_path and old_path != path:
            try:
                os.unlink(old_path)
            except OSError:
                pass
        return new_ds

    def close(self):
        with self.lock:
            for slot in self.slots.values():
                if slot.dataset is not None:
                    slot.dataset.close()
                    slot.dataset = None
            for p in self.created_files:
                try:
                    os.unlink(p)
                except OSError:
                    pass
            self.created_files.clear()


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._guard = threading.Lock()

    def create(self) -> Session:
        sid = uuid.uuid4().hex
        s = Session(session_id=sid)
        with self._guard:
            self._sessions[sid] = s
        return s

    def get(self, sid: str) -> Session:
        with self._guard:
            s = self._sessions.get(sid)
        if s is None:
            raise KeyError("会话不存在或已过期，请刷新页面开始新的检查。")
        return s

    def drop(self, sid: str):
        with self._guard:
            s = self._sessions.pop(sid, None)
        if s is not None:
            s.close()


async def save_slot_upload_async(fileobj, temp_dir: str,
                                 filename: str) -> str:
    """Stream an UploadFile to a temp path (files may be hundreds of MB)."""
    safe = "".join(c for c in os.path.basename(filename) if c.isalnum()
                   or c in "._-")[:120] or "upload.tif"
    fd, path = tempfile.mkstemp(
        prefix="geocmp_", suffix="_" + safe, dir=temp_dir
    )
    with os.fdopen(fd, "wb") as out:
        while True:
            chunk = await fileobj.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    return path


store = SessionStore()
