"""Deploy reviewed checkout code into the local SuperBrain runtime."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional


def runtime_dir() -> Path:
    return Path(
        os.getenv(
            "SUPERBRAIN_RUNTIME_DIR",
            str(Path.home() / ".superbrain-server"),
        )
    )


def resolve_source_backend() -> Path:
    """
    Locate the backend/ directory of the git checkout used as deploy source.

    Prefer SUPERBRAIN_SOURCE_DIR (backend or repo root). Fall back to the
    common local path ~/src/superbrain/backend. Never treat the runtime dir
    itself as the source (that would no-op / corrupt the allow-list flow).
    """
    runtime = runtime_dir().resolve()
    env = os.getenv("SUPERBRAIN_SOURCE_DIR", "").strip()
    candidates: list[Path] = []
    if env:
        p = Path(env).expanduser().resolve()
        candidates.append(p)
        candidates.append(p / "backend")
    candidates.append((Path.home() / "src" / "superbrain" / "backend").resolve())
    # Checkout used only when this module is imported from a non-runtime tree
    here = Path(__file__).resolve().parent.parent
    if here != runtime:
        candidates.append(here)

    seen: set[Path] = set()
    for cand in candidates:
        try:
            cand = cand.resolve()
        except Exception:
            continue
        if cand in seen or cand == runtime:
            continue
        seen.add(cand)
        script = cand / "scripts" / "deploy-local.sh"
        if script.is_file():
            return cand
    raise FileNotFoundError(
        "Could not find a SuperBrain git checkout to deploy from. "
        "Set SUPERBRAIN_SOURCE_DIR to your superbrain checkout "
        "(repo root or backend/), e.g. ~/src/superbrain."
    )


def run_deploy_local(*, restart: bool = False) -> int:
    source = resolve_source_backend()
    script = source / "scripts" / "deploy-local.sh"
    cmd = ["bash", str(script)]
    if restart:
        cmd.append("--restart")
    env = os.environ.copy()
    env.setdefault("SUPERBRAIN_RUNTIME_DIR", str(runtime_dir()))
    print(f"Deploying from {source} → {env['SUPERBRAIN_RUNTIME_DIR']}", flush=True)
    result = subprocess.run(cmd, check=False, env=env)
    return int(result.returncode)
