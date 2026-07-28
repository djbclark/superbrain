#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v secretspec >/dev/null 2>&1; then
    echo "SecretSpec is not installed or not available on PATH." >&2
    exit 127
fi

if [ "$#" -eq 0 ]; then
    if [ -x "$script_dir/.venv/bin/python" ]; then
        set -- "$script_dir/.venv/bin/python" "$script_dir/main.py"
    else
        set -- python "$script_dir/main.py"
    fi
fi

cd "$script_dir"
exec secretspec run --reason "${SECRETSPEC_REASON:-Running SuperBrain}" -- "$@"
