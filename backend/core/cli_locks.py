"""Exclusive process locks for SuperBrain CLI one-shot commands."""

from __future__ import annotations

import fcntl
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class CliLockUnavailable(RuntimeError):
    """Another SuperBrain process already holds this CLI lock."""


def _runtime_dir() -> Path:
    return Path(
        os.getenv(
            "SUPERBRAIN_RUNTIME_DIR",
            str(Path.home() / ".superbrain-server"),
        )
    )


def lock_path(name: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in name)
    return _runtime_dir() / "locks" / f"{safe}.lock"


@contextmanager
def exclusive_cli_lock(name: str, *, blocking: bool = False) -> Iterator[Path]:
    """
    Hold an exclusive flock for the lifetime of the context.

    Non-blocking by default so accidental double-starts fail fast.
    """
    path = lock_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+", encoding="utf-8")
    flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
    try:
        fcntl.flock(fh, flags)
    except OSError as exc:
        fh.close()
        raise CliLockUnavailable(
            f"Another SuperBrain process is already running `{name}` "
            f"(lock {path})"
        ) from exc
    try:
        fh.seek(0)
        fh.truncate()
        fh.write(f"{os.getpid()}\n")
        fh.flush()
        yield path
    finally:
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
        finally:
            fh.close()


def fail_if_locked(name: str) -> None:
    """Acquire briefly to prove the lock is free; used by thin wrappers."""
    with exclusive_cli_lock(name):
        return


def exit_if_locked(name: str) -> None:
    try:
        fail_if_locked(name)
    except CliLockUnavailable as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(3) from exc
