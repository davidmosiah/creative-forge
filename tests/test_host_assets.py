import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts import host_assets, qa


def make_png(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


class HostAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.site_dir = root / "site"
        self.site_dir.mkdir()
        self.artifact = root / "output" / "morning-walk--pt-BR--portrait.png"
        self.digest = make_png(self.artifact, b"png-bytes-pt")
        # No provenance-class fields (recipe/template/...) here: that keeps
        # qa.verify_report_files in its simple mode, which test_qa.py already
        # covers in full. Staging against a real sealed report is exercised by
        # the operator flow, not this unit fixture.
        self.records = [
            {
                "path": str(self.artifact),
                "sha256": self.digest,
                "media_kind": "image",
                "format": "portrait",
                "market_id": "br",
                "locale": "pt-BR",
            }
        ]
        self.report = self.approved_report()
        self.hosting = {
            "site_dir": str(self.site_dir),
            "public_subdir": "ads",
            "base_url": "https://sunrise-demo.app",
        }

    def tearDown(self):
        self.temp.cleanup()

    def approved_report(self):
        for record in self.records:
            record["artifact_key"] = qa.artifact_key(record)
        report = qa.build_report(
            "sunrise-demo",
            "20260710-a",
            {
                "status": "pass",
                "errors": [],
                "warnings": [],
                "records": self.records,
            },
        )
        return qa.approve_visual(
            report,
            "test-agent",
            {name: True for name in qa.VISUAL_CHECKS},
        )

    def test_stage_copies_by_content_hash_and_binds_manifest(self):
        manifest = host_assets.stage(self.report, self.hosting, "sunrise-demo")

        item = manifest["items"][0]
        hosted = self.site_dir / item["hosted_relpath"]
        self.assertTrue(hosted.is_file())
        self.assertEqual(hosted.name, f"{self.digest[:16]}.png")
        self.assertEqual(
            item["url"],
            f"https://sunrise-demo.app/ads/sunrise-demo/20260710-a/{self.digest[:16]}.png",
        )
        self.assertEqual(item["sha256"], self.digest)
        self.assertEqual(manifest["qa_matrix_digest"], self.report["matrix_digest"])
        self.assertFalse(manifest["deployed"])

    def test_stage_is_idempotent_for_identical_bytes(self):
        first = host_assets.stage(self.report, self.hosting, "sunrise-demo")
        second = host_assets.stage(self.report, self.hosting, "sunrise-demo")

        self.assertEqual(first["items"][0]["url"], second["items"][0]["url"])

    def test_stage_blocks_unapproved_visual_review(self):
        self.report["visual_status"] = "pending"

        with self.assertRaises(host_assets.HostingBlocked):
            host_assets.stage(self.report, self.hosting, "sunrise-demo")

    def test_stage_blocks_when_artifact_changed_after_approval(self):
        self.artifact.write_bytes(b"tampered-after-qa")

        with self.assertRaises(host_assets.HostingBlocked):
            host_assets.stage(self.report, self.hosting, "sunrise-demo")

    def test_stage_blocks_immutable_hash_collision_with_different_bytes(self):
        destination = (
            self.site_dir / "ads" / "sunrise-demo" / "20260710-a" / f"{self.digest[:16]}.png"
        )
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"other-bytes-already-there")

        with self.assertRaises(host_assets.HostingBlocked):
            host_assets.stage(self.report, self.hosting, "sunrise-demo")

    def test_stage_skips_video_records_instead_of_hosting_them(self):
        video = dict(self.records[0])
        video["media_kind"] = "video"
        video["path"] = str(self.artifact.with_name("clip.mp4"))
        video["sha256"] = make_png(Path(video["path"]), b"mp4-bytes")
        self.records.append(video)
        self.report = self.approved_report()

        manifest = host_assets.stage(self.report, self.hosting, "sunrise-demo")

        self.assertEqual(len(manifest["items"]), 1)
        self.assertEqual(manifest["skipped_non_image_records"], 1)

    def test_stage_blocks_wrong_app(self):
        with self.assertRaises(host_assets.HostingBlocked):
            host_assets.stage(self.report, self.hosting, "demo-app-c")

    def test_config_rejects_base_url_that_already_contains_subdir(self):
        with self.assertRaises(host_assets.HostingBlocked):
            host_assets.load_hosting_config(
                {
                    "publish": {
                        "asset_hosting": {
                            "method": "static_site_dir",
                            "site_dir": str(self.site_dir),
                            "public_subdir": "ads",
                            "base_url": "https://sunrise-demo.app/ads",
                        }
                    }
                }
            )

    def test_verify_accepts_exact_live_bytes_and_rejects_drift(self):
        manifest = host_assets.stage(self.report, self.hosting, "sunrise-demo")

        def live_ok(url):
            hosted = self.site_dir / manifest["items"][0]["hosted_relpath"]
            return hosted.read_bytes()

        receipt = host_assets.verify(manifest, fetcher=live_ok)
        self.assertEqual(receipt["items"][0]["sha256"], self.digest)
        self.assertEqual(receipt["schema"], host_assets.HOSTING_VERIFICATION_SCHEMA)

        def live_drifted(url):
            return b"cdn-returned-something-else"

        with self.assertRaises(host_assets.HostingBlocked):
            host_assets.verify(manifest, fetcher=live_drifted)


if __name__ == "__main__":
    unittest.main()
