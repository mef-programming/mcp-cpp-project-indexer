from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path


if os.name == "nt":
    import msvcrt
else:
    import fcntl


class IndexLockError(RuntimeError):
    pass


class IndexFileLock:
    def __init__(
        self,
        path: Path,
        *,
        label: str,
        timeout: float = 0.0,
        poll_interval: float = 0.1,
        remove_on_release: bool = False,
    ) -> None:
        self.path = path
        self.label = label
        self.timeout = max(0.0, timeout)
        self.poll_interval = max(0.05, poll_interval)
        self.remove_on_release = remove_on_release
        self._handle = None
        self.acquired = False

    def __enter__(self) -> "IndexFileLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    def acquire(self) -> None:
        if self.acquired:
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+b")
        self._ensure_lock_byte()

        deadline = time.monotonic() + self.timeout

        while True:
            try:
                self._lock_nonblocking()
                self.acquired = True
                self._write_owner()
                return
            except OSError as exc:
                if time.monotonic() >= deadline:
                    self._close_handle()
                    raise IndexLockError(
                        f"Could not acquire {self.label} lock: {self.path}"
                    ) from exc

                time.sleep(self.poll_interval)

    def release(self) -> None:
        if self._handle is None:
            return

        if self.acquired:
            try:
                self._unlock()
            finally:
                self.acquired = False

        self._close_handle()

        if self.remove_on_release:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def _close_handle(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def _ensure_lock_byte(self) -> None:
        assert self._handle is not None
        self._handle.seek(0, os.SEEK_END)

        if self._handle.tell() == 0:
            self._handle.write(b"\0")
            self._handle.flush()

        self._handle.seek(0)

    def _lock_nonblocking(self) -> None:
        assert self._handle is not None
        self._handle.seek(0)

        if os.name == "nt":
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(self) -> None:
        assert self._handle is not None
        self._handle.seek(0)

        if os.name == "nt":
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)

    def _write_owner(self) -> None:
        assert self._handle is not None
        owner = (
            f"label={self.label}\n"
            f"pid={os.getpid()}\n"
            f"host={socket.gethostname()}\n"
            f"python={sys.executable}\n"
            f"time={time.strftime('%Y-%m-%dT%H:%M:%S%z')}\n"
        )
        self._handle.seek(1)
        self._handle.truncate()
        self._handle.write(owner.encode("utf-8", errors="replace"))
        self._handle.flush()


def index_update_lock(index_root: Path, *, timeout: float = 30.0) -> IndexFileLock:
    return IndexFileLock(
        index_root / ".update.lock",
        label="index update",
        timeout=timeout,
    )


def index_watcher_lock(index_root: Path, *, timeout: float = 0.0) -> IndexFileLock:
    return IndexFileLock(
        index_root / ".watcher.lock",
        label="index watcher",
        timeout=timeout,
    )


def index_http_server_lock(
    index_root: Path,
    *,
    host: str,
    port: int,
    timeout: float = 0.0,
) -> IndexFileLock:
    safe_host = "".join(
        ch if ch.isalnum() or ch in {"-", "_"} else "_"
        for ch in host
    ).strip("_") or "host"
    return IndexFileLock(
        index_root / f".http-{safe_host}-{port}.lock",
        label=f"HTTP MCP server {host}:{port}",
        timeout=timeout,
        remove_on_release=True,
    )
