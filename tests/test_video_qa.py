import tempfile
import unittest
import subprocess
from copy import deepcopy
from pathlib import Path

from scripts import video_qa


class VideoQaReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.video = self.root / "creative.mp4"
        self.video.write_bytes(b"sealed-video")
        self.recipe = self.root / "recipe.yaml"
        self.recipe.write_text("version: 1\n")
        self.pattern = self.root / "patterns.yaml"
        self.pattern.write_text("version: 1\n")
        self.artifacts = [
            {
                "path": str(self.video),
                "market_id": "br",
                "locale": "pt-BR",
                "copy_language": "pt",
                "format": "story",
                "duration_seconds": 15,
                "audio_strategy": "intentional_silence",
                "technical_status": "pass",
                "brief_ref": "pilot",
                "concept_id": "morning-video",
                "variant_id": "morning-video-v1",
            }
        ]
        self.inputs = [
            {"role": "recipe", "path": str(self.recipe)},
            {"role": "video_patterns", "path": str(self.pattern)},
        ]
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(
            ["git", "config", "user.email", "video-qa@example.invalid"],
            cwd=self.root,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Video QA Test"],
            cwd=self.root,
            check=True,
        )
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(
            ["git", "commit", "-qm", "fixture"], cwd=self.root, check=True
        )

    def tearDown(self):
        self.temp.cleanup()

    def lock(self):
        return video_qa.seal_run_lock(
            app="sunrise-demo",
            batch_id="batch-1",
            artifacts=self.artifacts,
            input_files=self.inputs,
            git_state=video_qa.capture_git_state(self.root),
            tool_versions={"remotion": "4.0.487", "ffmpeg": "8.0"},
        )

    def checks(self):
        return {name: True for name in video_qa.PLAYBACK_CHECKS}

    def test_run_lock_seals_artifacts_inputs_tools_and_identity(self):
        lock = self.lock()

        self.assertEqual(video_qa.verify_run_lock(lock), [])
        self.assertEqual(lock["app"], "sunrise-demo")
        self.assertEqual(lock["artifacts"][0]["sha256"], video_qa.sha256(self.video))
        self.assertTrue(lock["input_digest"])
        self.assertTrue(lock["lock_digest"])
        self.assertEqual(lock["git_sha"], lock["git_state"]["git_sha"])

    def test_mutating_artifact_or_recipe_invalidates_run_lock(self):
        lock = self.lock()
        self.video.write_bytes(b"changed")
        self.recipe.write_text("version: 2\n")

        errors = video_qa.verify_run_lock(lock)

        self.assertTrue(any("artifact" in error for error in errors))
        self.assertTrue(any("recipe" in error for error in errors))

    def test_playback_approval_is_per_artifact_and_requires_every_agent_check(self):
        report = video_qa.build_playback_report(self.lock())
        artifact_key = report["records"][0]["artifact_key"]
        incomplete = self.checks()
        incomplete["cultural_fit"] = False

        with self.assertRaisesRegex(ValueError, "cultural_fit"):
            video_qa.approve_artifact(
                report,
                artifact_key,
                reviewer="codex",
                checks=incomplete,
                notes="Inspected the full timeline.",
            )

        approved = video_qa.approve_artifact(
            report,
            artifact_key,
            reviewer="codex",
            checks=self.checks(),
            notes="Full 1 fps timeline and transitions inspected; intentional silence verified.",
        )

        self.assertEqual(approved["status"], "approved")
        self.assertEqual(video_qa.verify_playback_report(approved), [])

    def test_report_identity_or_approval_mutation_is_rejected(self):
        report = video_qa.build_playback_report(self.lock())
        artifact_key = report["records"][0]["artifact_key"]
        approved = video_qa.approve_artifact(
            report,
            artifact_key,
            reviewer="codex",
            checks=self.checks(),
            notes="Reviewed.",
        )
        approved["app"] = "demo-app-c"
        approved["records"][0]["notes"] = "tampered"

        errors = video_qa.verify_playback_report(approved)

        self.assertTrue(any("identity" in error for error in errors))
        self.assertTrue(any("approval" in error for error in errors))

    def test_report_cannot_claim_another_app_even_with_recalculated_identity(self):
        report = video_qa.build_playback_report(self.lock())
        report["app"] = "demo-app-c"
        report["report_identity_digest"] = video_qa.report_identity_digest(report)

        errors = video_qa.verify_playback_report(report, allow_pending=True)

        self.assertTrue(any("report.app" in error for error in errors))

    def test_git_drift_invalidates_run_lock(self):
        lock = self.lock()
        (self.root / "untracked.txt").write_text("drift\n")

        errors = video_qa.verify_run_lock(lock)

        self.assertTrue(any("git state" in error for error in errors))

    def test_pending_record_cannot_drift_from_its_run_lock_artifact(self):
        report = video_qa.build_playback_report(self.lock())
        report["records"][0]["locale"] = "es-MX"
        report["report_identity_digest"] = video_qa.report_identity_digest(report)

        errors = video_qa.verify_playback_report(report, allow_pending=True)

        self.assertTrue(any("run lock artifact" in error for error in errors))

    def test_sound_and_caption_contract_distinguishes_intentional_silence(self):
        silent = video_qa.audit_sound_contract(
            "intentional_silence", max_volume_db=-91.0, captions_path=None
        )
        accidental = video_qa.audit_sound_contract(
            "licensed_music", max_volume_db=-91.0, captions_path=None
        )
        voice_without_captions = video_qa.audit_sound_contract(
            "voiceover", max_volume_db=-12.0, captions_path=None
        )

        self.assertEqual(silent["errors"], [])
        self.assertTrue(any("silêncio" in error for error in accidental["errors"]))
        self.assertTrue(any("captions" in error for error in voice_without_captions["errors"]))

    def test_collect_run_inputs_includes_localized_captions(self):
        captions = self.root / "pt-BR.srt"
        captions.write_text("1\n00:00:00,000 --> 00:00:01,000\nOlá\n")
        poster = self.root / "poster.png"
        poster.write_bytes(b"poster")
        contact = self.root / "contact.jpg"
        contact.write_bytes(b"contact")

        inputs = video_qa.collect_run_inputs(
            root=self.root,
            app_slug="sunrise-demo",
            app={
                "locales": {
                    "markets": [
                        {
                            "id": "br",
                            "storefront_locale": "pt-BR",
                            "app_locale": "pt-BR",
                            "copy_language": "pt",
                        }
                    ]
                }
            },
            recipe={
                "template": "modular-story",
                "assets": {},
                "target_markets": ["br"],
                "locales": {"pt-BR": {}},
            },
            recipe_path=self.recipe,
            props_path=self.root / "props.json",
            poster_path=poster,
            contact_path=contact,
            locale="pt-BR",
            captions_path=captions,
            render_receipt_path=self.root / "creative.render.json",
        )

        caption_inputs = [item for item in inputs if item["role"] == "captions"]
        self.assertEqual(caption_inputs, [{"role": "captions", "path": str(captions)}])

    def test_collect_run_inputs_seals_rights_and_consent_evidence(self):
        rights_dir = self.root / "assets" / "sunrise-demo" / "rights"
        rights_dir.mkdir(parents=True)
        license_path = rights_dir / "license.txt"
        release_path = rights_dir / "release.txt"
        license_path.write_text("paid Meta ads licensed\n")
        release_path.write_text("signed talent release\n")
        registry_path = rights_dir.parent / "registry.yaml"
        registry_path.write_text(
            """version: 1
app: sunrise-demo
assets:
  - id: talent-video
    kind: licensed
    rights:
      evidence: {path: assets/sunrise-demo/rights/license.txt}
    consent_release:
      evidence: {path: assets/sunrise-demo/rights/release.txt}
"""
        )
        poster = self.root / "poster.png"
        poster.write_bytes(b"poster")
        contact = self.root / "contact.jpg"
        contact.write_bytes(b"contact")

        inputs = video_qa.collect_run_inputs(
            root=self.root,
            app_slug="sunrise-demo",
            app={
                "locales": {
                    "markets": [
                        {
                            "id": "br",
                            "storefront_locale": "pt-BR",
                            "app_locale": "pt-BR",
                            "copy_language": "pt",
                        }
                    ]
                }
            },
            recipe={
                "template": "modular-story",
                "assets": {},
                "asset_refs": ["talent-video"],
                "target_markets": ["br"],
                "locales": {"pt-BR": {}},
            },
            recipe_path=self.recipe,
            props_path=self.root / "props.json",
            poster_path=poster,
            contact_path=contact,
            locale="pt-BR",
        )

        self.assertIn(
            {"role": "rights_evidence", "path": str(license_path.resolve())}, inputs
        )
        self.assertIn(
            {
                "role": "consent_release_evidence",
                "path": str(release_path.resolve()),
            },
            inputs,
        )

    def test_qa_path_segments_cannot_escape_the_qa_root(self):
        safe_qa_dir = video_qa.safe_qa_dir

        with self.assertRaises(ValueError):
            safe_qa_dir(self.root, "sunrise-demo", "/tmp/escape", "pt-BR", "recipe")
        with self.assertRaises(ValueError):
            safe_qa_dir(self.root, "sunrise-demo", "batch", "../escape", "recipe")

        expected = self.root / "qa" / "sunrise-demo" / "batch" / "pt-BR" / "recipe"
        self.assertEqual(
            safe_qa_dir(self.root, "sunrise-demo", "batch", "pt-BR", "recipe"),
            expected,
        )

        outside = self.root / "outside"
        outside.mkdir()
        (self.root / "qa").mkdir()
        (self.root / "qa" / "sunrise-demo").symlink_to(
            outside, target_is_directory=True
        )
        with self.assertRaises(ValueError):
            safe_qa_dir(self.root, "sunrise-demo", "batch", "pt-BR", "recipe")

        (self.root / "qa" / "sunrise-demo").unlink()
        (self.root / "qa").rmdir()
        (self.root / "qa").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "base.*symlink"):
            safe_qa_dir(self.root, "sunrise-demo", "batch", "pt-BR", "recipe")

        root_link = self.root.parent / f"{self.root.name}-qa-link"
        root_link.symlink_to(self.root, target_is_directory=True)
        try:
            with self.assertRaisesRegex(ValueError, "root.*symlink"):
                safe_qa_dir(root_link, "sunrise-demo", "batch", "pt-BR", "recipe")
        finally:
            root_link.unlink()

    def test_video_path_must_match_the_canonical_render_output(self):
        expected = video_qa.expected_video_path(
            self.root,
            "sunrise-demo",
            "morning-ritual",
            "pt-BR",
            "story",
        )

        self.assertEqual(
            expected,
            self.root
            / "output"
            / "sunrise-demo"
            / "video"
            / "morning-ritual--pt-BR--story.mp4",
        )
        with self.assertRaises(ValueError):
            video_qa.assert_expected_video_path(
                self.root / "unrelated.mp4", expected
            )

    def test_expected_video_path_rejects_symlink_output_base_and_ancestor(self):
        outside = self.root / "outside"
        outside.mkdir()
        output = self.root / "output"
        output.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            video_qa.expected_video_path(
                self.root, "sunrise-demo", "ritual", "pt-BR", "story"
            )

        output.unlink()
        output.mkdir()
        (output / "sunrise-demo").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            video_qa.expected_video_path(
                self.root, "sunrise-demo", "ritual", "pt-BR", "story"
            )


if __name__ == "__main__":
    unittest.main()
