"""Cross-process locks shared by pipeline commands.

The generation lock serializes heavy model work (FLUX, LTX, rembg) so two
generations never run at once on the same GPU/MPS device.
"""

from contextlib import contextmanager

from rich.console import Console

console = Console()

GEN_LOCK_PATH = "/tmp/video-studio-gen.lock"


@contextmanager
def generation_lock():
    import fcntl

    lock_file = open(GEN_LOCK_PATH, "w")
    try:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            console.print("[yellow]Another generation is running — waiting for it to finish…[/yellow]")
            fcntl.flock(lock_file, fcntl.LOCK_EX)  # block until free
        yield
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()
