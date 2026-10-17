"""Filesystem safeguards and a process-lifetime exclusive local-volume lock."""
from __future__ import annotations

import os
from pathlib import Path
import stat
from typing import BinaryIO


def no_links(path: Path) -> None:
    """Reject symlink/reparse components, including the configured root's parents.

    Trusted private ACLs are required: this is not race-proof against a local
    administrator or another principal allowed to mutate the storage directory.
    """
    for item in [*reversed(path.parents), path]:
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise OSError("Unsafe storage link/reparse point")


def regular(path: Path) -> None:
    no_links(path)
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError("Expected single-link regular managed file")


def sync_file(file: BinaryIO) -> None:
    file.flush()
    os.fsync(file.fileno())


def sync_dir(path: Path) -> None:
    # Python has no portable Windows directory fsync. Windows guarantee here is
    # process termination/restart, not a claim about power loss / device caches.
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class ProcessLock:
    def __init__(self, path: Path):
        regular(path)
        self.file = path.open("a+b")
        try:
            if self.file.seek(0, os.SEEK_END) == 0:
                self.file.write(b"\x00")
                sync_file(self.file)
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.file.close()
            raise

    def close(self) -> None:
        self.file.close()  # OS releases the lock on close or process termination.
