#!/usr/bin/env python3
"""Tests for local deploy source resolution."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import local_deploy


class TestLocalDeploy(unittest.TestCase):
    def test_resolve_prefers_env_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = Path(tmp) / "backend"
            (backend / "scripts").mkdir(parents=True)
            (backend / "scripts" / "deploy-local.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            with patch.dict("os.environ", {"SUPERBRAIN_SOURCE_DIR": str(backend)}):
                self.assertEqual(local_deploy.resolve_source_backend(), backend.resolve())

    def test_resolve_accepts_repo_root_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backend = root / "backend"
            (backend / "scripts").mkdir(parents=True)
            (backend / "scripts" / "deploy-local.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            with patch.dict("os.environ", {"SUPERBRAIN_SOURCE_DIR": str(root)}):
                self.assertEqual(local_deploy.resolve_source_backend(), backend.resolve())


if __name__ == "__main__":
    unittest.main()
