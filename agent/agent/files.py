"""Export files handed to the api by id, never by host path.

Each export job writes into its own 0700 directory `<root>/<file_id>/`. When the
job succeeds, the one regular file in it is registered; anything else (no file,
several, a symlink, over the size cap) removes the directory. A registered file is
read back only through `file_id`: not a symlink, real path inside its directory,
opened with O_NOFOLLOW, same size as when registered. Files expire after `ttl_s`,
and the oldest are evicted when the total passes `max_total_bytes`. The sweeper
also removes directories it does not know, such as leftovers of an earlier run.
"""

import asyncio
import base64
import hashlib
import logging
import os
import shutil
import stat
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import Field

from agent.errors import NotFound
from agent.protocol import Chunk, Streaming
from agent.validators import Args, parse_args

log = logging.getLogger("casino-agent")

CHUNK_BYTES = 64 * 1024
TTL_S = 3600
MAX_TOTAL_BYTES = 512 * 1024 * 1024
SWEEP_INTERVAL_S = 600


@dataclass
class StoredFile:
    file_id: str
    name: str
    size: int
    created: float


class FileRef(Args):
    file_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class FileStore:
    """Allocates, registers, serves and expires export files under `root`."""

    def __init__(
        self,
        root: Path,
        ttl_s: float = TTL_S,
        max_total_bytes: int = MAX_TOTAL_BYTES,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = root
        self.ttl_s = ttl_s
        self.max_total_bytes = max_total_bytes
        self.clock = clock
        self.files: dict[str, StoredFile] = {}
        self._allocated: dict[str, float] = {}

    def _ensure_root(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink() or not self.root.is_dir():
            raise RuntimeError(f"export root {self.root} is not a directory")
        os.chmod(self.root, 0o700)

    def allocate(self) -> tuple[str, Path]:
        """A new empty 0700 directory for one job's export, and its id."""
        self._ensure_root()
        file_id = uuid.uuid4().hex
        directory = self.root / file_id
        directory.mkdir(mode=0o700)
        self._allocated[file_id] = self.clock()
        return file_id, directory

    def discard(self, file_id: str) -> None:
        self._allocated.pop(file_id, None)
        self.files.pop(file_id, None)
        shutil.rmtree(self.root / file_id, ignore_errors=True)

    def seal(self, file_id: str, ok: bool) -> StoredFile | None:
        """Register the job's single regular file, or remove the directory and return None."""
        created = self._allocated.pop(file_id, self.clock())
        directory = self.root / file_id
        if not ok:
            self.discard(file_id)
            return None
        try:
            entries = list(os.scandir(directory))
        except OSError as e:
            log.warning("export %s: directory unreadable (%s)", file_id, e)
            self.discard(file_id)
            return None
        if len(entries) != 1 or not entries[0].is_file(follow_symlinks=False):
            log.warning(
                "export %s: expected one regular file, found %d entries", file_id, len(entries)
            )
            self.discard(file_id)
            return None
        entry = entries[0]
        size = entry.stat(follow_symlinks=False).st_size
        if size > self.max_total_bytes:
            log.warning(
                "export %s: %d bytes is over the %d byte cap", file_id, size, self.max_total_bytes
            )
            self.discard(file_id)
            return None
        stored = StoredFile(file_id=file_id, name=entry.name, size=size, created=created)
        self.files[file_id] = stored
        self._evict()
        return self.files.get(file_id)

    def _evict(self) -> None:
        total = sum(f.size for f in self.files.values())
        for old in sorted(self.files.values(), key=lambda f: f.created):
            if total <= self.max_total_bytes:
                return
            log.info("export %s evicted: total over the size cap", old.file_id)
            total -= old.size
            self.discard(old.file_id)

    def _path(self, file_id: str) -> tuple[StoredFile, str]:
        stored = self.files.get(file_id)
        if stored is None or self.clock() - stored.created > self.ttl_s:
            raise NotFound(file_id)
        directory = os.path.join(os.path.realpath(self.root), file_id)
        path = os.path.join(directory, stored.name)
        if (
            os.path.realpath(self.root / file_id) != directory
            or os.path.islink(path)
            or os.path.realpath(path) != path
        ):
            log.warning("export %s: file is no longer a plain file in its directory", file_id)
            raise NotFound(file_id)
        return stored, path

    def open_file(self, file_id: str) -> tuple[StoredFile, int]:
        """(record, fd) of a registered file; NotFound when it is missing, expired or altered."""
        stored, path = self._path(file_id)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as e:
            log.warning("export %s: cannot open (%s)", file_id, type(e).__name__)
            raise NotFound(file_id) from None
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size != stored.size:
            os.close(fd)
            log.warning("export %s: changed since it was registered", file_id)
            raise NotFound(file_id)
        return stored, fd

    def read(self, file_id: str) -> Streaming:
        """Chunks of the file (base64, CHUNK_BYTES each), then {file_id, name, bytes, sha256}."""
        stored, fd = self.open_file(file_id)
        digest = hashlib.sha256()
        sent = [0]

        async def frames() -> AsyncIterator[dict[str, Any]]:
            with os.fdopen(fd, "rb") as f:
                seq = 0
                while block := f.read(CHUNK_BYTES):
                    digest.update(block)
                    sent[0] += len(block)
                    yield {"seq": seq, "data": base64.b64encode(block).decode()}
                    seq += 1

        async def final() -> dict[str, Any]:
            return {
                "file_id": file_id,
                "name": stored.name,
                "bytes": sent[0],
                "sha256": digest.hexdigest(),
            }

        return Streaming(frames=frames(), final=final, frame=Chunk)

    def sweep(self) -> int:
        """Remove expired files and any directory under root this store does not know."""
        now = self.clock()
        removed = 0
        for file_id, stored in list(self.files.items()):
            if now - stored.created > self.ttl_s:
                self.discard(file_id)
                removed += 1
        if self.root.is_dir():
            for entry in os.scandir(self.root):
                known = entry.name in self.files or entry.name in self._allocated
                if not known:
                    if entry.is_dir(follow_symlinks=False):
                        shutil.rmtree(entry.path, ignore_errors=True)
                    else:
                        os.unlink(entry.path)
                    removed += 1
        if removed:
            log.info("swept %d export(s)", removed)
        return removed

    async def sweep_forever(self, interval_s: float = SWEEP_INTERVAL_S) -> None:
        while True:
            try:
                self.sweep()
            except OSError as e:
                log.warning("export sweep failed: %s", e)
            await asyncio.sleep(interval_s)

    def handlers(self) -> dict[str, Callable[[dict[str, Any]], Any]]:
        async def file_read(raw: dict[str, Any]) -> Streaming:
            return self.read(parse_args(FileRef, raw).file_id)

        return {"file.read": file_read}
