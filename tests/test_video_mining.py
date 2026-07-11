import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import yaml

from scripts import video_mining


class VideoMiningContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def test_video_mining_module_exists(self):
        self.assertIsNotNone(importlib.util.find_spec("scripts.video_mining"))

    def valid_browser_data(self):
        return {
            "version": 1,
            "app": "sunrise-demo",
            "observed_at": "2026-07-10T10:00:00Z",
            "expires_at": "2026-08-09T10:00:00Z",
            "patterns": [
                {
                    "id": "demo-0000000000000003",
                    "mode": "browser_observation",
                    "platform": "meta",
                    "advertiser": "DawnFit",
                    "source_url": (
                        "https://www.facebook.com/ads/library/"
                        "?id=0000000000000003"
                    ),
                    "lineage": "competitor_pattern",
                    "locale": "en",
                    "copy_language": "en",
                    "rights": {
                        "class": "reference_only",
                        "media_reuse": False,
                        "allowed_uses": ["structural_analysis"],
                    },
                    "media": {
                        "format": "square",
                        "duration_seconds": 15.0,
                    },
                    "structure": {
                        "hook": "Sleep-story promise appears immediately",
                        "body": "Narrated Biblical story demonstrates the offer",
                        "cta": "Continue in the app",
                        "agent_judgment": (
                            "The calm promise is legible before product detail."
                        ),
                    },
                }
            ],
        }

    def valid_licensed_data(self):
        media_path = self.root / "licensed.mp4"
        media_path.write_bytes(b"licensed-video-fixture")
        receipt_path = self.root / "license-receipt.txt"
        receipt_path.write_text("licensed for analysis and contact sheets\n")
        data = self.valid_browser_data()
        data["patterns"] = [
            {
                "id": "owned-sleep-story",
                "mode": "licensed_file",
                "platform": "local",
                "advertiser": "Sunrise Walks",
                "source_url": "https://sunrise-demo.app/asset-provenance/sleep-story",
                "lineage": "exploratory",
                "locale": "pt-BR",
                "copy_language": "pt",
                "rights": {
                    "class": "licensed",
                    "status": "verified",
                    "evidence": {
                        "kind": "license_receipt",
                        "path": str(receipt_path),
                    },
                    "allowed_uses": ["analysis", "contact_sheet"],
                },
                "media": {
                    "path": str(media_path),
                    "sha256": hashlib.sha256(media_path.read_bytes()).hexdigest(),
                    "format": "story",
                    "duration_seconds": 12.0,
                },
                "structure": {
                    "timeline": [
                        {
                            "start_seconds": 0.0,
                            "end_seconds": 2.0,
                            "beat": "hook",
                            "derived_fact": "A pergunta de sono abre o vídeo.",
                        },
                        {
                            "start_seconds": 2.0,
                            "end_seconds": 9.5,
                            "beat": "body",
                            "derived_fact": "A narração demonstra a história.",
                        },
                        {
                            "start_seconds": 9.5,
                            "end_seconds": 12.0,
                            "beat": "cta",
                            "derived_fact": "O fechamento convida a ouvir no app.",
                        },
                    ],
                    "agent_judgment": "O agente, não o script, avalia o hook.",
                },
            }
        ]
        return data

    def assert_error_contains(self, result, fragment):
        self.assertTrue(
            any(fragment in error for error in result["errors"]),
            result["errors"],
        )

    def test_valid_browser_observation_passes_without_touching_local_media(self):
        probe = mock.Mock(side_effect=AssertionError("browser mode must not probe"))

        result = video_mining.audit_video_patterns(
            self.valid_browser_data(),
            expected_app="sunrise-demo",
            now=self.now,
            root=self.root,
            probe_files=True,
            probe=probe,
        )

        self.assertEqual(result["errors"], [])
        probe.assert_not_called()

    def test_app_identity_source_url_and_lineage_are_fail_closed(self):
        mutations = (
            ("app", lambda data: data.update(app="demo-app-c")),
            (
                "source_url",
                lambda data: data["patterns"][0].update(source_url="file:///tmp/ad.mp4"),
            ),
            (
                "lineage",
                lambda data: data["patterns"][0].update(lineage="proven_winner"),
            ),
        )
        for expected, mutate in mutations:
            with self.subTest(expected=expected):
                data = self.valid_browser_data()
                mutate(data)

                result = video_mining.audit_video_patterns(
                    data,
                    expected_app="sunrise-demo",
                    now=self.now,
                    root=self.root,
                )

                self.assert_error_contains(result, expected)

    def test_browser_source_domain_must_match_declared_platform(self):
        mismatches = (
            ("meta", "https://ads.tiktok.com/business/creativecenter/topads/1"),
            ("tiktok", "https://www.facebook.com/ads/library/?id=123"),
            ("google", "https://adstransparency.google.com.evil.example/ad/123"),
            ("unknown-network", "https://example.com/ad/123"),
        )
        for platform, source_url in mismatches:
            with self.subTest(platform=platform, source_url=source_url):
                data = self.valid_browser_data()
                data["patterns"][0].update(
                    platform=platform,
                    source_url=source_url,
                )

                result = video_mining.audit_video_patterns(
                    data, now=self.now, root=self.root
                )

                self.assert_error_contains(result, "platform/source_url")

    def test_known_platform_official_subdomains_are_accepted(self):
        sources = (
            ("meta", "https://www.facebook.com/ads/library/?id=123"),
            ("tiktok", "https://ads.tiktok.com/business/creativecenter/topads/1"),
            ("google", "https://adstransparency.google.com/advertiser/123"),
        )
        for platform, source_url in sources:
            with self.subTest(platform=platform):
                data = self.valid_browser_data()
                data["patterns"][0].update(
                    platform=platform,
                    source_url=source_url,
                )

                result = video_mining.audit_video_patterns(
                    data, now=self.now, root=self.root
                )

                self.assertEqual(result["errors"], [])

    def test_observation_window_must_be_timezone_aware_ordered_and_fresh(self):
        mutations = (
            lambda data: data.update(observed_at="2026-07-10T10:00:00"),
            lambda data: data.update(expires_at="2026-07-09T10:00:00Z"),
            lambda data: data.update(expires_at="2026-07-10T11:00:00Z"),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                data = self.valid_browser_data()
                mutate(data)

                result = video_mining.audit_video_patterns(
                    data, now=self.now, root=self.root
                )

                self.assertTrue(result["errors"])
                self.assertTrue(
                    any(
                        "observed_at" in error
                        or "expires_at" in error
                        or "expirad" in error
                        for error in result["errors"]
                    ),
                    result["errors"],
                )

    def test_locale_and_copy_language_must_be_valid_and_aligned(self):
        for locale, copy_language in (("english", "en"), ("es-MX", "en")):
            with self.subTest(locale=locale, copy_language=copy_language):
                data = self.valid_browser_data()
                data["patterns"][0].update(
                    locale=locale, copy_language=copy_language
                )

                result = video_mining.audit_video_patterns(
                    data, now=self.now, root=self.root
                )

                self.assertTrue(
                    any(
                        "locale" in error or "copy_language" in error
                        for error in result["errors"]
                    ),
                    result["errors"],
                )

    def test_browser_observation_rejects_any_local_media_or_reuse_permission(self):
        data = self.valid_browser_data()
        data["patterns"][0]["media"]["path"] = "/tmp/competitor.mp4"
        data["patterns"][0]["rights"]["media_reuse"] = True
        data["patterns"][0]["rights"]["allowed_uses"].append("publish")

        result = video_mining.audit_video_patterns(
            data, now=self.now, root=self.root
        )

        self.assert_error_contains(result, "browser_observation")
        self.assertTrue(
            any("reuse" in error or "reutil" in error for error in result["errors"]),
            result["errors"],
        )

    def test_browser_observation_rejects_path_and_file_aliases(self):
        data = self.valid_browser_data()
        data["patterns"][0]["media"]["local_file"] = "/tmp/competitor.mp4"
        data["patterns"][0]["source_path"] = "/tmp/competitor-source.mp4"

        result = video_mining.audit_video_patterns(
            data, now=self.now, root=self.root
        )

        self.assert_error_contains(result, "local_file")
        self.assert_error_contains(result, "source_path")

    def test_structure_requires_hook_body_cta_or_an_agent_authored_timeline(self):
        data = self.valid_browser_data()
        data["patterns"][0]["structure"] = {"hook": "Only a hook"}

        result = video_mining.audit_video_patterns(
            data, now=self.now, root=self.root
        )

        self.assert_error_contains(result, "hook/body/cta")

    def test_timeline_timestamps_must_be_monotonic_and_within_duration(self):
        data = self.valid_licensed_data()
        timeline = data["patterns"][0]["structure"]["timeline"]
        timeline[1]["start_seconds"] = 1.5
        timeline[2]["end_seconds"] = 12.5

        result = video_mining.audit_video_patterns(
            data, now=self.now, root=self.root
        )

        self.assert_error_contains(result, "monot")
        self.assert_error_contains(result, "duration")

    def test_duration_and_timeline_timestamps_must_be_finite(self):
        invalid_duration = self.valid_browser_data()
        invalid_duration["patterns"][0]["media"]["duration_seconds"] = float("nan")

        duration_result = video_mining.audit_video_patterns(
            invalid_duration, now=self.now, root=self.root
        )

        self.assert_error_contains(duration_result, "duration_seconds")

        invalid_timeline = self.valid_licensed_data()
        invalid_timeline["patterns"][0]["structure"]["timeline"][0][
            "end_seconds"
        ] = float("inf")

        timeline_result = video_mining.audit_video_patterns(
            invalid_timeline, now=self.now, root=self.root
        )

        self.assertTrue(
            any(
                "timestamp" in error or "intervalo" in error
                for error in timeline_result["errors"]
            ),
            timeline_result["errors"],
        )

    def test_licensed_file_requires_local_hash_and_verified_rights_evidence(self):
        valid = video_mining.audit_video_patterns(
            self.valid_licensed_data(), now=self.now, root=self.root
        )
        self.assertEqual(valid["errors"], [])

        mutations = (
            (
                "sha256",
                lambda pattern: pattern["media"].update(sha256="0" * 64),
            ),
            (
                "rights",
                lambda pattern: pattern["rights"].update(status="unverified"),
            ),
            (
                "evidence",
                lambda pattern: pattern["rights"].pop("evidence"),
            ),
        )
        for expected, mutate in mutations:
            with self.subTest(expected=expected):
                data = self.valid_licensed_data()
                mutate(data["patterns"][0])

                result = video_mining.audit_video_patterns(
                    data, now=self.now, root=self.root
                )

                self.assert_error_contains(result, expected)

    def test_ffprobe_is_opt_in_and_duration_drift_is_blocking(self):
        data = self.valid_licensed_data()
        media_path = Path(data["patterns"][0]["media"]["path"])
        probe = mock.Mock(return_value={"duration_seconds": 13.0, "width": 1080, "height": 1920})

        without_probe = video_mining.audit_video_patterns(
            data,
            now=self.now,
            root=self.root,
            probe_files=False,
            probe=probe,
        )
        self.assertEqual(without_probe["errors"], [])
        probe.assert_not_called()

        with_probe = video_mining.audit_video_patterns(
            data,
            now=self.now,
            root=self.root,
            probe_files=True,
            probe=probe,
        )

        probe.assert_called_once_with(media_path)
        self.assert_error_contains(with_probe, "duration")
        self.assertEqual(with_probe["probes"]["owned-sleep-story"]["width"], 1080)

    def test_probe_video_uses_a_fixed_no_shell_ffprobe_command(self):
        media_path = self.root / "safe name.mp4"
        media_path.write_bytes(b"fixture")
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return type(
                "Completed",
                (),
                {
                    "returncode": 0,
                    "stderr": "",
                    "stdout": json.dumps(
                        {
                            "format": {"duration": "4.25", "format_name": "mov,mp4"},
                            "streams": [
                                {
                                    "codec_type": "video",
                                    "codec_name": "h264",
                                    "width": 1080,
                                    "height": 1920,
                                }
                            ],
                        }
                    ),
                },
            )()

        result = video_mining.probe_video(
            media_path,
            runner=runner,
            ffprobe_bin="ffprobe-test",
        )

        command, kwargs = calls[0]
        self.assertEqual(command[0], "ffprobe-test")
        self.assertEqual(command[-1], str(media_path))
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(result["duration_seconds"], 4.25)
        self.assertEqual(result["codec"], "h264")

    def test_contact_sheet_is_only_derived_from_licensed_media_with_safe_ffmpeg(self):
        licensed = self.valid_licensed_data()["patterns"][0]
        output = self.root / "cache" / "sheet.jpg"
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            Path(command[-1]).write_bytes(b"contact-sheet")
            return type("Completed", (), {"returncode": 0, "stderr": ""})()

        result = video_mining.derive_contact_sheet(
            licensed,
            output,
            root=self.root,
            runner=runner,
            ffmpeg_bin="ffmpeg-test",
        )

        command, kwargs = calls[0]
        self.assertEqual(result, output)
        self.assertTrue(output.is_file())
        self.assertEqual(command[0], "ffmpeg-test")
        self.assertTrue(any("tile=4x3" in argument for argument in command))
        self.assertIs(kwargs["shell"], False)

        with self.assertRaises(video_mining.VideoMiningError):
            video_mining.derive_contact_sheet(
                self.valid_browser_data()["patterns"][0],
                output,
                root=self.root,
                runner=runner,
            )

    def test_cli_audits_an_agent_authored_pattern_file(self):
        path = self.root / "patterns.yaml"
        path.write_text(yaml.safe_dump(self.valid_browser_data(), sort_keys=False))
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = video_mining.main(
                [
                    "--app",
                    "sunrise-demo",
                    "--path",
                    str(path),
                    "--now",
                    "2026-07-10T12:00:00Z",
                    "--json",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue())["errors"], [])

    def test_repository_template_documents_both_modes(self):
        template_path = video_mining.ROOT / "swipe" / "_video-pattern-template.yaml"
        data = yaml.safe_load(template_path.read_text())

        self.assertEqual(
            {pattern["mode"] for pattern in data["patterns"]},
            {"browser_observation", "licensed_file"},
        )

    def test_sunrise_demo_demo_pattern_observation_is_structured_and_auditable(self):
        patterns_path = (
            video_mining.ROOT / "swipe" / "sunrise-demo" / "video-patterns.yaml"
        )
        data = yaml.safe_load(patterns_path.read_text())

        result = video_mining.audit_video_patterns(
            data,
            expected_app="sunrise-demo",
            now=self.now,
            root=video_mining.ROOT,
        )

        self.assertEqual(result["errors"], [])
        demo_pattern = next(
            pattern
            for pattern in data["patterns"]
            if pattern["id"] == "demo-0000000000000003"
        )
        self.assertEqual(demo_pattern["mode"], "browser_observation")
        self.assertEqual(demo_pattern["media"]["duration_seconds"], 15.0)
        self.assertNotIn("path", demo_pattern["media"])

        competitors = yaml.safe_load(
            (
                video_mining.ROOT
                / "swipe"
                / "sunrise-demo"
                / "competitors.yaml"
            ).read_text()
        )
        creative = next(
            item
            for item in competitors["creatives"]
            if item["id"] == "demo-0000000000000003"
        )
        self.assertEqual(creative["format"], "video")
        self.assertEqual(creative["aspect_ratio"], "9:16")
        self.assertEqual(creative["duration_seconds"], 15.0)


if __name__ == "__main__":
    unittest.main()
