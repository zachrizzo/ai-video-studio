"""Cross-process locks shared by pipeline commands.

The generation lock serializes heavy model work (FLUX, LTX, rembg) so two
generations never run at once on the same GPU/MPS device.

Locking primitive per OS: ``fcntl.flock`` on POSIX, ``msvcrt.locking`` on
Windows (which has no fcntl). msvcrt's blocking mode gives up after ~10s, so
blocking acquisition is a retry loop around the non-blocking form.
"""

import os
import sys
import tempfile
import time
from contextlib import contextmanager

from rich.console import Console

console = Console()

GEN_LOCK_PATH = os.path.join(tempfile.gettempdir(), "video-studio-gen.lock")

_IS_WINDOWS = sys.platform == "win32"


def _try_lock(lock_file) -> bool:
    """Attempt a non-blocking exclusive lock. Returns False if already held."""
    if _IS_WINDOWS:
        import msvcrt

        try:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    else:
        import fcntl

        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False


def _lock_blocking(lock_file) -> None:
    """Block until the exclusive lock is acquired."""
    if _IS_WINDOWS:
        # msvcrt.LK_LOCK raises after ~10 retries, so poll LK_NBLCK ourselves.
        while not _try_lock(lock_file):
            time.sleep(1.0)
    else:
        import fcntl

        fcntl.flock(lock_file, fcntl.LOCK_EX)


def _unlock(lock_file) -> None:
    if _IS_WINDOWS:
        import msvcrt

        try:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass  # already released with the file handle
    else:
        import fcntl

        fcntl.flock(lock_file, fcntl.LOCK_UN)


@contextmanager
def generation_lock():
    lock_file = open(GEN_LOCK_PATH, "w")
    try:
        if not _try_lock(lock_file):
            console.print("[yellow]Another generation is running — waiting for it to finish…[/yellow]")
            _lock_blocking(lock_file)
        yield
    finally:
        _unlock(lock_file)
        lock_file.close()


@contextmanager
def file_lock(lock_path):
    """Cross-process mutual exclusion on an arbitrary file.

    Used to serialize read-modify-write critical sections against a shared
    file (e.g. a JSON registry) so concurrent writers don't clobber one
    another's changes.
    """
    lock_file = open(lock_path, "w")
    try:
        _lock_blocking(lock_file)
        yield
    finally:
        _unlock(lock_file)
        lock_file.close()
