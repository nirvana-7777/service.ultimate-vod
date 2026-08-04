#!/usr/bin/env python3
"""
Cross-platform advisory file locking.

Kodi runs each script/service invocation (service startup, RunScript,
manual triggers) as a *separate* Python interpreter. A plain
threading.Lock only protects against races within a single interpreter,
so it cannot prevent a manual "sync now" call from overlapping with the
background service's scheduled sync. This module provides a simple
process-level lock backed by a lock file, using fcntl on POSIX and
msvcrt on Windows.
"""

import os
import sys
import time

import xbmc

IS_WINDOWS = sys.platform.startswith("win")

if IS_WINDOWS:
    import msvcrt
else:
    import fcntl


class LockTimeoutError(Exception):
    """Raised when a lock could not be acquired within the timeout."""
    pass


class FileLock:
    """
    Advisory, cross-process file lock.

    Usage:
        with FileLock(lock_path, timeout=30):
            ... critical section ...

    Raises LockTimeoutError if the lock cannot be acquired in time.
    Safe to use as a no-op-tolerant guard: on unexpected OS errors while
    locking, we log and re-raise rather than silently proceeding, since
    silently proceeding is exactly the bug this class exists to prevent.
    """

    def __init__(self, lock_path, timeout=30, poll_interval=0.5):
        self.lock_path = str(lock_path)
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._fh = None

    def acquire(self):
        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        deadline = time.time() + self.timeout

        # Open (or create) the lock file once; we lock/unlock the same
        # file handle for the lifetime of this FileLock instance.
        self._fh = open(self.lock_path, "a+")

        while True:
            try:
                self._lock_fh(self._fh)
                return self
            except (IOError, OSError):
                if time.time() >= deadline:
                    self._fh.close()
                    self._fh = None
                    raise LockTimeoutError(
                        f"Could not acquire lock {self.lock_path} within {self.timeout}s"
                    )
                time.sleep(self.poll_interval)

    def release(self):
        if self._fh is None:
            return
        try:
            self._unlock_fh(self._fh)
        except (IOError, OSError) as e:
            xbmc.log(f"[Ultimate VOD Service] Error releasing lock {self.lock_path}: {e}",
                      xbmc.LOGWARNING)
        finally:
            try:
                self._fh.close()
            except (IOError, OSError):
                pass
            self._fh = None

    @staticmethod
    def _lock_fh(fh):
        if IS_WINDOWS:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock_fh(fh):
        if IS_WINDOWS:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False