import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from scripts import forge


class ForgeVideoBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "apps").mkdir()
        recipe_dir = self.root / "recipes" / "demo" / "video"
        recipe_dir.mkdir(parents=True)
        (self.root / "apps" / "demo.yaml").write_text(
            yaml.safe_dump(
                {
                    "slug": "demo",
                    "locales": {
                        "markets": [
                            {
                                "id": "br",
                                "storefront_locale": "pt-BR",
                                "app_locale": "pt-BR",
                                "copy_language": "pt",
                            },
                            {
                                "id": "spain",
                                "storefront_locale": "es-ES",
                                "app_locale": "es-ES",
                                "copy_language": "es",
                            },
                        ]
                    },
                }
            )
        )
        (recipe_dir / "concept.yaml").write_text(
            yaml.safe_dump(
                {
                    "format": "story",
                    "target_markets": ["br", "spain"],
                    "locales": {"pt-BR": {}, "es-ES": {}},
                }
            )
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_build_video_renders_each_explicit_market_and_prepares_pending_qa(self):
        rendered = []
        prepared = []

        def fake_render(recipe, app, locale, output, **kwargs):
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"video")
            rendered.append((locale, output))
            return output

        def fake_prepare(**kwargs):
            path = (
                self.root
                / "qa"
                / kwargs["app_slug"]
                / kwargs["batch_id"]
                / kwargs["locale"]
                / kwargs["recipe_name"]
                / "playback-report.json"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n")
            prepared.append(kwargs)
            return path

        with mock.patch.object(
            forge, "preflight", return_value={"ok": True, "errors": [], "warnings": []}
        ) as preflight, mock.patch.object(
            forge.video,
            "audit_recipe",
            return_value={"errors": [], "warnings": []},
        ), mock.patch.object(forge.video, "render_video", side_effect=fake_render), mock.patch.object(
            forge.video_qa, "prepare", side_effect=fake_prepare
        ):
            reports = forge.build_video(
                "demo",
                "concept",
                "batch-1",
                all_markets=True,
                root=self.root,
            )

        preflight.assert_called_once_with("demo", root=self.root.resolve())
        self.assertCountEqual([locale for locale, _ in rendered], ["pt-BR", "es-ES"])
        self.assertCountEqual([item["locale"] for item in prepared], ["pt-BR", "es-ES"])
        self.assertEqual(len(reports), 2)
        run = json.loads(
            (self.root / "runs" / "demo" / "batch-1.video.json").read_text()
        )
        self.assertEqual(run["status"], "pending_agent_playback_qa")
        self.assertEqual(len(run["qa_reports"]), 2)

    def test_build_video_requires_one_locale_or_explicit_all_markets(self):
        with mock.patch.object(
            forge, "preflight", return_value={"ok": True, "errors": [], "warnings": []}
        ) as preflight, mock.patch.object(
            forge.video,
            "audit_recipe",
            return_value={"errors": [], "warnings": []},
        ):
            with self.assertRaises(Exception):
                forge.build_video(
                    "demo", "concept", "batch-1", root=self.root
                )
        preflight.assert_called_once_with("demo", root=self.root.resolve())


if __name__ == "__main__":
    unittest.main()
