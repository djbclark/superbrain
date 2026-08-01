"""
Opt-in historic category-playlist backfill, in the style of upstream SuperBrain
optional features (``config/ngrok_enabled.txt``): a small config flag file, a
background supervisor owned by the API process, and ``superbrain`` CLI flags
(plus matching HTTP endpoints for app/automation).

Enable file: ``config/category_playlist_backfill_enabled.txt``
(presence means enabled; content is conventionally ``enabled``).

While the API is running and the file exists, a lifespan supervisor keeps the
``--sync-category-playlists`` worker running (same subprocess pattern as queued
analysis). Removing the file cancels backfill across reboots until
``superbrain --sync-category-playlists-start`` is run again.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

ENABLE_FLAG_NAME = "category_playlist_backfill_enabled.txt"
# Brief interim name from the LaunchAgent experiment; migrated on read.
_LEGACY_ENABLE_FLAG_NAMES = (
    "category_playlist_backfill.enabled",
)
LEGACY_BACKFILL_LAUNCH_LABEL = (
    "com.djbclark.superbrain.category-playlist-backfill"
)
POLL_SECONDS = 2.0
BACKFILL_RESTART_DELAY = 5.0


def runtime_dir() -> Path:
    return Path(
        os.getenv(
            "SUPERBRAIN_RUNTIME_DIR",
            str(Path.home() / ".superbrain-server"),
        )
    )


def enable_flag_path(runtime: Optional[Path] = None) -> Path:
    root = runtime or runtime_dir()
    return root / "config" / ENABLE_FLAG_NAME


def _migrate_legacy_enable_flag(runtime: Path) -> None:
    dest = enable_flag_path(runtime)
    if dest.is_file():
        return
    for name in _LEGACY_ENABLE_FLAG_NAMES:
        legacy = runtime / "config" / name
        if legacy.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("enabled\n", encoding="utf-8")
            legacy.unlink()
            return


def is_backfill_enabled(runtime: Optional[Path] = None) -> bool:
    root = runtime or runtime_dir()
    _migrate_legacy_enable_flag(root)
    return enable_flag_path(root).is_file()


def backfill_log_path() -> Path:
    return Path.home() / "Library" / "Logs" / "superbrain" / "category-playlist-backfill.log"


def _python_bin(runtime: Optional[Path] = None) -> str:
    root = runtime or runtime_dir()
    venv = root / ".venv" / "bin" / "python"
    if venv.is_file():
        return str(venv)
    return os.environ.get("PYTHON", "python3")


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *args],
        check=False,
        capture_output=True,
        text=True,
    )


def remove_legacy_backfill_launch_agent() -> None:
    _launchctl("bootout", f"gui/{os.getuid()}/{LEGACY_BACKFILL_LAUNCH_LABEL}")
    legacy_plist = (
        Path.home()
        / "Library"
        / "LaunchAgents"
        / f"{LEGACY_BACKFILL_LAUNCH_LABEL}.plist"
    )
    if legacy_plist.exists():
        legacy_plist.unlink()


def migrate_launch_agent_off_service_py(runtime: Optional[Path] = None) -> bool:
    """One-shot: if LaunchAgent still points at removed service.py, use api.py."""
    runtime = runtime or runtime_dir()
    plist = Path.home() / "Library" / "LaunchAgents" / "com.djbclark.superbrain.plist"
    if not plist.is_file():
        return False
    text = plist.read_text(encoding="utf-8")
    if "service.py" not in text:
        return False
    updated = text.replace(f"{runtime}/service.py", f"{runtime}/api.py")
    updated = updated.replace("/service.py</string>", "/api.py</string>")
    if updated == text:
        return False
    plist.write_text(updated, encoding="utf-8")
    target = f"gui/{os.getuid()}/com.djbclark.superbrain"
    _launchctl("bootout", target)
    boot = _launchctl("bootstrap", f"gui/{os.getuid()}", str(plist))
    if boot.returncode != 0:
        _launchctl("load", "-w", str(plist))
    _launchctl("kickstart", "-k", target)
    time.sleep(1.0)
    return True


def _backfill_process_running(runtime: Optional[Path] = None) -> bool:
    runtime = runtime or runtime_dir()
    try:
        return (
            subprocess.run(
                ["pgrep", "-f", f"{runtime}/main.py --sync-category-playlists"],
                check=False,
                capture_output=True,
            ).returncode
            == 0
        )
    except Exception:
        return False


def _stop_backfill_processes(runtime: Optional[Path] = None) -> None:
    runtime = runtime or runtime_dir()
    subprocess.run(
        ["pkill", "-f", f"{runtime}/main.py --sync-category-playlists"],
        check=False,
        capture_output=True,
    )
    subprocess.run(
        ["pkill", "-f", "main.py --sync-category-playlists"],
        check=False,
        capture_output=True,
    )


class PlaylistBackfillSupervisor:
    """api.py lifespan thread: align worker with the enable flag file."""

    def __init__(self, runtime: Optional[Path] = None) -> None:
        self.runtime = runtime or runtime_dir()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._proc: Optional[subprocess.Popen] = None
        self._log_fh = None
        self._next_start = 0.0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="playlist-backfill-supervisor",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._terminate_child(reason="api shutdown")
        if self._thread is not None:
            self._thread.join(timeout=20)
            self._thread = None
        if self._log_fh is not None:
            try:
                self._log_fh.close()
            except Exception:
                pass
            self._log_fh = None

    def _child_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _terminate_child(self, *, reason: str) -> None:
        if not self._child_running():
            self._proc = None
            return
        assert self._proc is not None
        logger.info("Stopping category playlist backfill (%s)", reason)
        self._proc.send_signal(signal.SIGTERM)
        try:
            self._proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=5)
        self._proc = None

    def _spawn_child(self) -> None:
        if self._child_running():
            return
        now = time.monotonic()
        if now < self._next_start:
            return
        log_path = backfill_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if self._log_fh is None:
            self._log_fh = open(log_path, "a", buffering=1)
        cmd = [
            _python_bin(self.runtime),
            str(self.runtime / "main.py"),
            "--sync-category-playlists",
        ]
        logger.info("Starting category playlist backfill")
        self._proc = subprocess.Popen(
            cmd,
            cwd=str(self.runtime),
            env=os.environ.copy(),
            stdout=self._log_fh,
            stderr=subprocess.STDOUT,
        )

    def _reconcile(self) -> None:
        want = is_backfill_enabled(self.runtime)
        if want:
            if not self._child_running():
                if self._proc is not None and self._proc.poll() is not None:
                    logger.info(
                        "Category playlist backfill exited (%s); will restart while enabled",
                        self._proc.returncode,
                    )
                    self._next_start = time.monotonic() + BACKFILL_RESTART_DELAY
                    self._proc = None
                self._spawn_child()
        else:
            self._terminate_child(reason="enable flag absent")

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._reconcile()
            except Exception:
                logger.exception("Category playlist backfill reconcile failed")
            self._stop.wait(POLL_SECONDS)


_supervisor: Optional[PlaylistBackfillSupervisor] = None


def start_api_backfill_supervisor(runtime: Optional[Path] = None) -> PlaylistBackfillSupervisor:
    global _supervisor
    remove_legacy_backfill_launch_agent()
    migrate_launch_agent_off_service_py(runtime=runtime)
    if _supervisor is None:
        _supervisor = PlaylistBackfillSupervisor(runtime=runtime)
    _supervisor.start()
    return _supervisor


def stop_api_backfill_supervisor() -> None:
    global _supervisor
    if _supervisor is not None:
        _supervisor.stop()
        _supervisor = None


def backfill_service_status(runtime: Optional[Path] = None) -> dict[str, Any]:
    runtime = runtime or runtime_dir()
    return {
        "enabled": is_backfill_enabled(runtime),
        "enable_flag": str(enable_flag_path(runtime)),
        "process_running": _backfill_process_running(runtime),
    }


def start_category_playlist_backfill(runtime: Optional[Path] = None) -> dict[str, Any]:
    """Write enable flag (ngrok-style); API supervisor starts the worker."""
    runtime = runtime or runtime_dir()
    remove_legacy_backfill_launch_agent()
    migrate_launch_agent_off_service_py(runtime=runtime)
    flag = enable_flag_path(runtime)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("enabled\n", encoding="utf-8")
    for name in _LEGACY_ENABLE_FLAG_NAMES:
        legacy = runtime / "config" / name
        if legacy.is_file():
            legacy.unlink()
    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline:
        if _backfill_process_running(runtime):
            break
        time.sleep(0.5)
    status = backfill_service_status(runtime)
    status["action"] = "started"
    return status


def stop_category_playlist_backfill(runtime: Optional[Path] = None) -> dict[str, Any]:
    """Remove enable flag and stop the worker."""
    runtime = runtime or runtime_dir()
    remove_legacy_backfill_launch_agent()
    flag = enable_flag_path(runtime)
    if flag.exists():
        flag.unlink()
    for name in _LEGACY_ENABLE_FLAG_NAMES:
        legacy = runtime / "config" / name
        if legacy.is_file():
            legacy.unlink()
    _stop_backfill_processes(runtime)
    time.sleep(0.4)
    status = backfill_service_status(runtime)
    status["action"] = "stopped"
    return status
