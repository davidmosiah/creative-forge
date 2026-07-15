import tempfile
import unittest
import sys
from pathlib import Path
from unittest import mock

from scripts import forge


class InstalledPackageRoutingTests(unittest.TestCase):
    def test_build_uses_loaded_renderer_when_workspace_has_no_scripts_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "recipes" / "demo").mkdir(parents=True)
            (root / "recipes" / "demo" / "one.yaml").write_text("template: demo\n")
            report = root / "qa" / "demo" / "batch" / "report.json"

            with (
                mock.patch.object(forge, "ROOT", root),
                mock.patch.object(forge, "preflight", return_value={"ok": True, "errors": [], "warnings": [], "gates": []}),
                mock.patch.object(forge.qa, "prepare", return_value=report),
                mock.patch.object(forge.subprocess, "run") as run,
            ):
                forge.build("demo", "batch", 1)

            command = run.call_args.args[0]
            self.assertEqual(Path(command[1]), Path(forge.render.__file__).resolve())

    def test_prepare_publish_uses_loaded_module_outside_the_workspace(self):
        arguments = [
            "creative-forge",
            "prepare-publish",
            "--qa-report",
            "report.json",
            "--capabilities",
            "capabilities.json",
            "--account-id",
            "act_1",
            "--campaign-id",
            "campaign_1",
            "--ad-set-id",
            "adset_1",
            "--audience-id",
            "audience_1",
            "--readiness-receipt",
            "readiness.json",
            "--out",
            "manifest.json",
        ]
        with tempfile.TemporaryDirectory() as temporary:
            with (
                mock.patch.object(forge, "ROOT", Path(temporary)),
                mock.patch.object(sys, "argv", arguments),
                mock.patch.object(forge.subprocess, "run") as run,
            ):
                run.return_value.returncode = 0
                forge.main()

        command = run.call_args.args[0]
        self.assertEqual(Path(command[1]), Path(forge.publish.__file__).resolve())

    def test_runtime_hints_require_original_images_and_native_scene_frames(self):
        source = Path(forge.__file__).read_text()

        self.assertIn("abrir cada PNG original", source)
        self.assertIn("cada frame nativo", source)
        self.assertIn("--review-file", source)


if __name__ == "__main__":
    unittest.main()
