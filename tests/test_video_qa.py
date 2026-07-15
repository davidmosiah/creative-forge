import tempfile
import unittest
import subprocess
from copy import deepcopy
from pathlib import Path

from PIL import Image
import yaml

from scripts import video_qa


class VideoQaReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.video = (
            self.root
            / "output"
            / "sunrise-demo"
            / "video"
            / "morning-video--pt-BR--story.mp4"
        )
        self.video.parent.mkdir(parents=True)
        self.video.write_bytes(b"sealed-video")
        self.recipe = (
            self.root
            / "recipes"
            / "sunrise-demo"
            / "video"
            / "morning-video.yaml"
        )
        self.recipe.parent.mkdir(parents=True)
        self.recipe.write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "brief_ref": "pilot",
                    "concept_id": "morning-video",
                    "variant_id": "morning-video-v1",
                    "research_refs": ["customer-1", "pattern-1"],
                    "execution_ref": "pattern-1",
                    "swiped_from": "Observed video structure",
                },
                sort_keys=False,
            )
        )
        self.pattern = (
            self.root / "swipe" / "sunrise-demo" / "video-patterns.yaml"
        )
        self.pattern.parent.mkdir(parents=True)
        self.pattern.write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "patterns": [
                        {
                            "id": "pattern-1",
                            "lineage": "competitor_pattern",
                            "source_url": "https://example.com/video-pattern",
                            "evidence_level": "observed",
                        }
                    ],
                },
                sort_keys=False,
            )
        )
        self.research = self.root / "swipe" / "sunrise-demo" / "competitors.yaml"
        self.research.write_text(
            yaml.safe_dump(
                {
                    "creatives": [
                        {
                            "id": "customer-1",
                            "lineage": "customer_insight",
                            "source_url": "https://example.com/customer-insight",
                            "evidence_level": "observed",
                        }
                    ]
                },
                sort_keys=False,
            )
        )
        self.brief = self.root / "briefs" / "sunrise-demo" / "pilot.yaml"
        self.brief.parent.mkdir(parents=True)
        self.brief.write_text(
            yaml.safe_dump(
                {
                    "id": "pilot",
                    "concepts": [
                        {
                            "id": "morning-video",
                            "lineage": "exploratory",
                            "lineage_ref": "customer-1",
                            "research_refs": ["customer-1", "pattern-1"],
                        }
                    ],
                },
                sort_keys=False,
            )
        )
        self.qa_dir = (
            self.root
            / "qa"
            / "sunrise-demo"
            / "batch-1"
            / "pt-BR"
            / "morning-video"
        )
        self.qa_dir.mkdir(parents=True)
        self.props = self.qa_dir / "props.json"
        self.props.write_text(
            '{"fps":10,"scenes":['
            '{"id":"hook","startFrame":0,"durationInFrames":3},'
            '{"id":"proof","startFrame":3,"durationInFrames":3},'
            '{"id":"cta","startFrame":6,"durationInFrames":4}'
            ']}\n'
        )
        self.scene_frames = []
        for scene_id, frame_number, time_seconds in (
            ("hook", 1, 0.1),
            ("proof", 4, 0.4),
            ("cta", 7, 0.7),
        ):
            frame = self.qa_dir / f"{scene_id}.png"
            Image.new("RGB", (10, 20), "white").save(frame)
            self.scene_frames.append(
                {
                    "scene_id": scene_id,
                    "frame": frame_number,
                    "time_seconds": time_seconds,
                    "path": str(frame),
                }
            )
        self.thumbnail = self.root / "thumbnail.png"
        Image.new("RGB", (5, 10), "white").save(self.thumbnail)
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
                "concept_lineage": "exploratory",
                "concept_lineage_ref": "customer-1",
                "execution_lineage": "competitor_pattern",
                "execution_ref": "pattern-1",
                "variant_id": "morning-video-v1",
                "width": 10,
                "height": 20,
                "scene_ids": ["hook", "proof", "cta"],
                "scene_evidence": self.scene_frames,
            }
        ]
        self.inputs = [
            {"role": "recipe", "path": str(self.recipe)},
            {"role": "brief", "path": str(self.brief)},
            {"role": "video_patterns", "path": str(self.pattern)},
            {"role": "research", "path": str(self.research)},
            {"role": "render_props", "path": str(self.props)},
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
        self.assertEqual(
            {item["scene_id"] for item in lock["artifacts"][0]["scene_evidence"]},
            {"hook", "proof", "cta"},
        )

    def test_run_lock_rejects_missing_or_thumbnail_scene_evidence(self):
        missing = deepcopy(self.artifacts)
        missing[0]["scene_evidence"] = missing[0]["scene_evidence"][:-1]
        with self.assertRaisesRegex(ValueError, "scene_evidence"):
            video_qa.seal_run_lock(
                app="sunrise-demo",
                batch_id="batch-1",
                artifacts=missing,
                input_files=self.inputs,
                git_state=video_qa.capture_git_state(self.root),
                tool_versions={"remotion": "4.0.487"},
            )

        thumbnail = deepcopy(self.artifacts)
        thumbnail[0]["scene_evidence"][0]["path"] = str(self.thumbnail)
        with self.assertRaisesRegex(ValueError, "dimensão"):
            video_qa.seal_run_lock(
                app="sunrise-demo",
                batch_id="batch-1",
                artifacts=thumbnail,
                input_files=self.inputs,
                git_state=video_qa.capture_git_state(self.root),
                tool_versions={"remotion": "4.0.487"},
            )

    def test_run_lock_rejects_scene_evidence_outside_the_props_midpoint(self):
        mislabeled = deepcopy(self.artifacts)
        mislabeled[0]["scene_evidence"][1]["frame"] = 999
        mislabeled[0]["scene_evidence"][1]["time_seconds"] = 99.9

        with self.assertRaisesRegex(ValueError, "midpoint|scene frame"):
            video_qa.seal_run_lock(
                app="sunrise-demo",
                batch_id="batch-1",
                artifacts=mislabeled,
                input_files=self.inputs,
                git_state=video_qa.capture_git_state(self.root),
                tool_versions={"remotion": "4.0.487"},
            )

    def test_verify_run_lock_fail_closes_on_malformed_scene_ids(self):
        lock = self.lock()
        lock["artifacts"][0]["scene_ids"] = [{"not": "hashable"}]

        errors = video_qa.verify_run_lock(lock)

        self.assertTrue(any("scene" in error for error in errors), errors)

    def test_verify_run_lock_recomputes_scene_midpoints_after_digest_recalculation(self):
        lock = self.lock()
        lock["artifacts"][0]["scene_evidence"][1]["frame"] = 999
        lock["artifacts"][0]["scene_evidence"][1]["time_seconds"] = 99.9
        lock["lock_digest"] = video_qa.canonical_digest(
            video_qa._lock_payload(lock)
        )

        errors = video_qa.verify_run_lock(lock)

        self.assertTrue(any("midpoint" in error for error in errors), errors)

    def test_verify_run_lock_revalidates_artifact_semantics_after_digest_recalculation(self):
        lock = self.lock()
        lock["artifacts"][0]["technical_status"] = "fail"
        lock["lock_digest"] = video_qa.canonical_digest(
            video_qa._lock_payload(lock)
        )

        errors = video_qa.verify_run_lock(lock)

        self.assertTrue(any("QA técnico" in error for error in errors), errors)

    def test_run_lock_cannot_relabel_lineage_and_recalculate_every_local_digest(self):
        lock = self.lock()
        artifact = lock["artifacts"][0]
        artifact["concept_lineage"] = "own_winner"
        artifact["artifact_key"] = video_qa.artifact_key(
            lock["app"], lock["batch_id"], artifact
        )
        lock["lock_digest"] = video_qa.canonical_digest(
            video_qa._lock_payload(lock)
        )

        errors = video_qa.verify_run_lock(lock)

        self.assertTrue(any("lineage" in error for error in errors), errors)

    def test_run_lock_cannot_retarget_lineage_to_other_committed_files(self):
        alternate = self.root / "alternate"
        alternate.mkdir()
        alternate_recipe = alternate / "recipe.yaml"
        alternate_recipe.write_text(
            yaml.safe_dump(
                {
                    "brief_ref": "pilot",
                    "concept_id": "morning-video",
                    "variant_id": "morning-video-v1",
                    "research_refs": ["winner-1"],
                },
                sort_keys=False,
            )
        )
        alternate_brief = alternate / "brief.yaml"
        alternate_brief.write_text(
            yaml.safe_dump(
                {
                    "id": "pilot",
                    "concepts": [
                        {
                            "id": "morning-video",
                            "lineage": "own_winner",
                            "lineage_ref": "winner-1",
                            "research_refs": ["winner-1"],
                        }
                    ],
                },
                sort_keys=False,
            )
        )
        alternate_research = alternate / "research.yaml"
        alternate_research.write_text(
            yaml.safe_dump(
                {
                    "creatives": [
                        {
                            "id": "winner-1",
                            "lineage": "own_winner",
                            "evidence_level": "performance_data",
                            "performance_metrics": {"installs": 42},
                        }
                    ]
                },
                sort_keys=False,
            )
        )
        subprocess.run(["git", "add", "alternate"], cwd=self.root, check=True)
        subprocess.run(
            ["git", "commit", "-qm", "alternate committed inputs"],
            cwd=self.root,
            check=True,
        )
        lock = self.lock()
        replacements = {
            "recipe": alternate_recipe,
            "brief": alternate_brief,
            "research": alternate_research,
        }
        for item in lock["input_files"]:
            replacement = replacements.get(item.get("role"))
            if replacement is not None:
                item.update(
                    {
                        "path": str(replacement),
                        "resolved_path": str(replacement.resolve()),
                        "sha256": video_qa.sha256(replacement),
                    }
                )
        artifact = lock["artifacts"][0]
        artifact.update(
            {
                "concept_lineage": "own_winner",
                "concept_lineage_ref": "winner-1",
                "execution_lineage": "original",
                "execution_ref": None,
            }
        )
        artifact["artifact_key"] = video_qa.artifact_key(
            lock["app"], lock["batch_id"], artifact
        )
        lock["input_digest"] = video_qa.canonical_digest(
            sorted(
                lock["input_files"],
                key=lambda item: (str(item.get("role")), str(item.get("path"))),
            )
        )
        lock["lock_digest"] = video_qa.canonical_digest(
            video_qa._lock_payload(lock)
        )

        errors = video_qa.verify_run_lock(lock)

        self.assertTrue(any("canônic" in error for error in errors), errors)

    def test_run_lock_rejects_split_view_dotdot_lineage_paths(self):
        attacker = self.root / "attacker-tree"
        attacker_recipe = (
            attacker
            / "recipes"
            / "sunrise-demo"
            / "video"
            / "morning-video.yaml"
        )
        attacker_recipe.parent.mkdir(parents=True)
        attacker_recipe.write_text(
            yaml.safe_dump(
                {
                    "brief_ref": "pilot",
                    "concept_id": "morning-video",
                    "variant_id": "morning-video-v1",
                    "research_refs": ["winner-1"],
                },
                sort_keys=False,
            )
        )
        attacker_brief = (
            attacker / "briefs" / "sunrise-demo" / "pilot.yaml"
        )
        attacker_brief.parent.mkdir(parents=True)
        attacker_brief.write_text(
            yaml.safe_dump(
                {
                    "id": "pilot",
                    "concepts": [
                        {
                            "id": "morning-video",
                            "lineage": "own_winner",
                            "lineage_ref": "winner-1",
                            "research_refs": ["winner-1"],
                        }
                    ],
                },
                sort_keys=False,
            )
        )
        attacker_research = (
            attacker / "swipe" / "sunrise-demo" / "competitors.yaml"
        )
        attacker_research.parent.mkdir(parents=True)
        attacker_research.write_text(
            yaml.safe_dump(
                {
                    "creatives": [
                        {
                            "id": "winner-1",
                            "lineage": "own_winner",
                            "evidence_level": "performance_data",
                            "performance_metrics": {"installs": 42},
                        }
                    ]
                },
                sort_keys=False,
            )
        )
        (attacker / "nest").mkdir()
        escape = self.root / "escape"
        escape.symlink_to(attacker / "nest", target_is_directory=True)
        subprocess.run(
            ["git", "add", "attacker-tree", "escape"],
            cwd=self.root,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-qm", "split view inputs"],
            cwd=self.root,
            check=True,
        )
        lock = self.lock()
        authored_paths = {
            "recipe": f"{escape}/../recipes/sunrise-demo/video/morning-video.yaml",
            "brief": f"{escape}/../briefs/sunrise-demo/pilot.yaml",
            "research": f"{escape}/../swipe/sunrise-demo/competitors.yaml",
        }
        for item in lock["input_files"]:
            authored = authored_paths.get(item.get("role"))
            if authored is not None:
                item["path"] = authored
        artifact = lock["artifacts"][0]
        artifact.update(
            {
                "concept_lineage": "own_winner",
                "concept_lineage_ref": "winner-1",
                "execution_lineage": "original",
                "execution_ref": None,
            }
        )
        artifact["artifact_key"] = video_qa.artifact_key(
            lock["app"], lock["batch_id"], artifact
        )
        lock["input_digest"] = video_qa.canonical_digest(
            sorted(
                lock["input_files"],
                key=lambda item: (str(item.get("role")), str(item.get("path"))),
            )
        )
        lock["lock_digest"] = video_qa.canonical_digest(
            video_qa._lock_payload(lock)
        )

        errors = video_qa.verify_run_lock(lock)

        self.assertTrue(any("authored" in error or "canônic" in error for error in errors), errors)

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

    def test_approval_cannot_erase_reviewer_notes_or_timestamp_and_recalculate_digest(self):
        report = video_qa.build_playback_report(self.lock())
        artifact_key = report["records"][0]["artifact_key"]
        approved = video_qa.approve_artifact(
            report,
            artifact_key,
            reviewer="codex",
            checks=self.checks(),
            notes="Reviewed the full video and every native scene frame.",
        )

        for field, value in (
            ("reviewer", ""),
            ("notes", ""),
            ("reviewed_at", "not-a-timestamp"),
        ):
            with self.subTest(field=field):
                tampered = deepcopy(approved)
                record = tampered["records"][0]
                record[field] = value
                record["approval_digest"] = video_qa.approval_digest(
                    record,
                    tampered["run_lock"]["lock_digest"],
                )

                errors = video_qa.verify_playback_report(tampered)

                self.assertTrue(any(field in error for error in errors), errors)

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

    def test_scene_frame_plan_covers_each_scene_at_native_midpoint(self):
        props = {
            "fps": 30,
            "scenes": [
                {"id": "hook", "startFrame": 0, "durationInFrames": 90},
                {"id": "proof", "startFrame": 90, "durationInFrames": 180},
                {"id": "cta", "startFrame": 270, "durationInFrames": 60},
            ],
        }

        plan = video_qa.scene_frame_plan(props)

        self.assertEqual([item["scene_id"] for item in plan], ["hook", "proof", "cta"])
        self.assertEqual([item["frame"] for item in plan], [44, 179, 299])
        self.assertEqual([item["time_seconds"] for item in plan], [44 / 30, 179 / 30, 299 / 30])

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
        path_root = self.root / "qa-path-root"
        path_root.mkdir()

        with self.assertRaises(ValueError):
            safe_qa_dir(path_root, "sunrise-demo", "/tmp/escape", "pt-BR", "recipe")
        with self.assertRaises(ValueError):
            safe_qa_dir(path_root, "sunrise-demo", "batch", "../escape", "recipe")

        expected = path_root / "qa" / "sunrise-demo" / "batch" / "pt-BR" / "recipe"
        self.assertEqual(
            safe_qa_dir(path_root, "sunrise-demo", "batch", "pt-BR", "recipe"),
            expected,
        )

        outside = path_root / "outside"
        outside.mkdir()
        (path_root / "qa").mkdir()
        (path_root / "qa" / "sunrise-demo").symlink_to(
            outside, target_is_directory=True
        )
        with self.assertRaises(ValueError):
            safe_qa_dir(path_root, "sunrise-demo", "batch", "pt-BR", "recipe")

        (path_root / "qa" / "sunrise-demo").unlink()
        (path_root / "qa").rmdir()
        (path_root / "qa").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "base.*symlink"):
            safe_qa_dir(path_root, "sunrise-demo", "batch", "pt-BR", "recipe")

        root_link = self.root / "qa-path-root-link"
        root_link.symlink_to(path_root, target_is_directory=True)
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
        path_root = self.root / "output-path-root"
        path_root.mkdir()
        outside = path_root / "outside"
        outside.mkdir()
        output = path_root / "output"
        output.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            video_qa.expected_video_path(
                path_root, "sunrise-demo", "ritual", "pt-BR", "story"
            )

        output.unlink()
        output.mkdir()
        (output / "sunrise-demo").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            video_qa.expected_video_path(
                path_root, "sunrise-demo", "ritual", "pt-BR", "story"
            )


if __name__ == "__main__":
    unittest.main()
