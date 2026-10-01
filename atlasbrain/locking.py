"""Exclusive locks shared by threads/processes on Windows, macOS and Linux.

Lock files must stay in place: replacing/unlinking a locked file creates another
lock domain. Closing the handle releases the lock, including after a crash.
"""
import errno
import os
import time

_WINDOWS = os.name == 'nt'
if _WINDOWS:
    import msvcrt
else:
    import fcntl


def acquire(handle, *, blocking=True):
    if not _WINDOWS:
        fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return
    # Always use the same byte, even for an empty file or an append-mode handle.
    # LK_LOCK stops retrying after ten seconds; our blocking locks wait until free.
    while True:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            if not blocking:
                raise BlockingIOError(errno.EAGAIN, 'AtlasBrain lock is busy') from exc
            time.sleep(.05)


def release(handle):
    if _WINDOWS:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(handle, fcntl.LOCK_UN)
