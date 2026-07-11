import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import paths


class WorkspacePathTests(unittest.TestCase):
    def test_current_worktree_wins_for_repo_local_files(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            worktree = base / "worktree"
            canonical = base / "canonical"
            (worktree / "assets").mkdir(parents=True)
            (canonical / "assets").mkdir(parents=True)
            (worktree / "assets" / "icon.png").write_bytes(b"new")
            (canonical / "assets" / "icon.png").write_bytes(b"old")

            with mock.patch.dict(os.environ, {"CREATIVE_FORGE_ROOT": str(canonical)}):
                resolved = paths.resolve_config_path(worktree, "assets/icon.png")

            self.assertEqual(resolved, (worktree / "assets" / "icon.png").resolve())

    def test_canonical_checkout_resolves_external_siblings_from_a_worktree(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            worktree = base / "isolated" / "creative-forge"
            canonical = base / "Developer" / "06-tools-scripts" / "creative-forge"
            sibling = base / "Developer" / "01-products" / "App" / "README.md"
            worktree.mkdir(parents=True)
            canonical.mkdir(parents=True)
            sibling.parent.mkdir(parents=True)
            sibling.write_text("truth")

            with mock.patch.dict(os.environ, {"CREATIVE_FORGE_ROOT": str(canonical)}):
                resolved = paths.resolve_config_path(
                    worktree, "../../01-products/App/README.md"
                )

            self.assertEqual(resolved, sibling.resolve())


if __name__ == "__main__":
    unittest.main()
