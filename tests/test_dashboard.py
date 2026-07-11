import json
import tempfile
import unittest
from pathlib import Path

from scripts import dashboard


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "apps").mkdir()
        (self.root / "apps" / "sunrise-demo.yaml").write_text("name: Sunrise Walks\n")
        (self.root / "swipe" / "sunrise-demo").mkdir(parents=True)
        (self.root / "swipe" / "sunrise-demo" / "competitors.yaml").write_text(
            "expires_at: '2026-08-09'\ncreatives:\n  - id: meta-1\n"
        )
        (self.root / "briefs" / "sunrise-demo").mkdir(parents=True)
        (self.root / "briefs" / "sunrise-demo" / "pilot.yaml").write_text("id: pilot\n")
        batch = self.root / "qa" / "sunrise-demo" / "20260710-a"
        batch.mkdir(parents=True)
        (batch / "contact-story.png").write_bytes(b"fake-png")
        (batch / "report.json").write_text(
            json.dumps(
                {
                    "app": "sunrise-demo",
                    "batch_id": "<script>alert(1)</script>",
                    "automated_status": "pass",
                    "visual_status": "approved",
                    "visual_reviewer": "claude",
                    "approved_matrix_digest": "abc123def4567890",
                    "records": [
                        {"recipe": "morning-walk", "market_id": "br", "path": "x", "sha256": "y"}
                    ],
                }
            )
        )
        self.out_dir = self.root / "output" / "sunrise-demo"
        self.out_dir.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def render(self) -> str:
        data = dashboard.collect("sunrise-demo", self.root)
        return dashboard.render(data, self.out_dir)

    def test_dashboard_renders_sealed_evidence_only(self):
        html_text = self.render()

        self.assertIn("Sunrise Walks", html_text)
        self.assertIn("morning-walk", html_text)
        self.assertIn("approved", html_text)
        self.assertIn("../../qa/sunrise-demo/20260710-a/contact-story.png", html_text)
        self.assertIn("nenhum manifest de publish ainda", html_text)
        self.assertIn("o ciclo pago ainda não rodou", html_text)

    def test_dashboard_escapes_untrusted_artifact_strings(self):
        html_text = self.render()

        self.assertNotIn("<script>alert(1)</script>", html_text)
        self.assertIn("&lt;script&gt;", html_text)

    def test_stage_status_derives_from_artifacts(self):
        data = dashboard.collect("sunrise-demo", self.root)
        stages = dashboard.stage_status(data)

        self.assertEqual(stages["research"][0], "done")
        self.assertEqual(stages["build + QA"][0], "done")
        self.assertEqual(stages["publish PAUSED"][0], "todo")
        self.assertEqual(stages["signals"][0], "todo")


if __name__ == "__main__":
    unittest.main()
