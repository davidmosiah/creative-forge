import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from PIL import Image

from scripts import forge, publish, qa
from tests.qa_fixtures import approve_report, production_report


class PublishGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        image = self.root / "creative.png"
        Image.new("RGB", (1080, 1080), "white").save(image)
        spec = {
                    "path": str(image),
                    "recipe": "morning",
                    "format": "square",
                    "locale": "pt-BR",
                    "app_locale": "pt-BR",
                    "copy_language": "pt",
                    "width": 1080,
                    "height": 1080,
                    "research_refs": ["meta-pain"],
                    "swiped_from": "StrideCo ES — dor→alívio",
                    "brief_ref": "pilot",
                    "concept_id": "morning-relief",
                    "variant_id": "morning-a",
                    "cta": "Baixe grátis",
                    "ad_copy": {
                        "primary_text": "Comece a manhã em paz.",
                        "headline": "Uma caminhada ao amanhecer",
                    },
                }
        self.legacy_report = qa.build_report(
            "sunrise-demo",
            "legacy-batch",
            qa.audit_outputs([spec]),
        )
        self.report = production_report(
            self.root,
            "sunrise-demo",
            "batch-1",
            [spec],
        )
        self.capabilities = {
            "provider": "meta_ads_mcp",
            "agent": "claude",
            "checked_at": "2026-07-09T18:00:00Z",
            "tools": ["ads_create_creative", "ads_create_ad", "ads_get_ad"],
            "readback_tool": "ads_get_ad",
        }
        self.now = datetime(2026, 7, 9, 18, 30, tzinfo=timezone.utc)
        self.markets = [
            {
                "id": "br",
                "countries": ["BR"],
                "locale": "pt-BR",
                "app_locale": "pt-BR",
                "copy_language": "pt",
            }
        ]
        self.audience_plan = {
            "version": 1,
            "app": "sunrise-demo",
            "updated_at": "2026-07-09",
            "policy": {"allow_interest_targeting": False},
            "audiences": [
                {
                    "id": "br-cold-broad",
                    "market": "br",
                    "funnel_stage": "cold",
                    "hypothesis": "broad BR",
                    "targeting": {"kind": "broad", "countries": ["BR"]},
                    "optimization_event": "app_install",
                    "confidence": "medium",
                    "confidence_rationale": "funil orgânico BR",
                    "status": "approved",
                    "approved_by": "demo-operator",
                    "creatives": {"copy_language": "pt"},
                }
            ],
        }
        self.app_config = {
            "slug": "sunrise-demo",
            "readiness": {
                "required_receipts": {"app_store_destination": "required_live"}
            },
            "destinations": {
                "default": {
                    "type": "app_store",
                    "url": "https://apps.apple.com/app/id1",
                },
                "custom_product_pages": [],
            },
        }
        self.briefs = {
            "pilot": {
                "id": "pilot",
                "app": "sunrise-demo",
                "destination": {
                    "ref": "default",
                    "type": "app_store",
                    "url": "https://apps.apple.com/app/id1",
                },
            }
        }
        self.readiness_receipt = {
            "receipt_type": "app_store_destination",
            "provider": "app_store_connect_api",
            "tool": "apps_get_app_store_version_localizations",
            "app": "sunrise-demo",
            "status": "ready",
            "verification_basis": "live_provider_readback",
            "local_validation_sufficient": False,
            "observed_at": "2026-07-09T18:10:00Z",
            "response_path": "app-store-readiness.json",
            "response_digest": hashlib.sha256(b'{"ready":true}').hexdigest(),
            "destination": {
                "ref": "default",
                "type": "app_store",
                "url": "https://apps.apple.com/app/id1",
            },
        }
        (self.root / "app-store-readiness.json").write_bytes(b'{"ready":true}')

    def tearDown(self):
        self.temp.cleanup()

    def approve(self):
        return approve_report(self.report, "codex")

    def prepare(self, report, capabilities=None, publish_policy=None):
        return publish.prepare_manifest(
            report,
            capabilities or self.capabilities,
            account_id="act_1",
            campaign_id="campaign_1",
            ad_set_id="adset_1",
            audience_plan=self.audience_plan,
            audience_id="br-cold-broad",
            markets=self.markets,
            publish_policy=publish_policy
            if publish_policy is not None
            else {"primary_format": "square", "max_ads_per_ad_set": 6},
            app_config=self.app_config,
            briefs=self.briefs,
            readiness_receipt=self.readiness_receipt,
            evidence_root=self.root,
            workspace_root=self.root,
            now=self.now,
        )

    def test_legacy_qa_report_cannot_reach_publish(self):
        self.assertEqual(self.legacy_report["version"], 1)

        with self.assertRaisesRegex(publish.PublishBlocked, "proveniência.*version 2"):
            self.prepare(approve_report(self.legacy_report, "codex"))

    def test_manifest_ships_one_ad_per_concept_variant_in_primary_format(self):
        manifest = self.prepare(self.approve())
        self.assertEqual(manifest["format_policy"]["primary_format"], "square")
        self.assertTrue(manifest["format_policy"]["one_ad_per_concept_variant"])
        self.assertEqual(
            manifest["format_policy"]["deduplication_key"],
            "concept_id+variant_id",
        )
        self.assertEqual(len(manifest["items"]), 1)

    def test_missing_primary_format_in_matrix_blocks_publish(self):
        with self.assertRaises(publish.PublishBlocked):
            self.prepare(
                self.approve(),
                publish_policy={"primary_format": "portrait"},
            )

    def test_missing_publish_policy_blocks_publish(self):
        with self.assertRaises(publish.PublishBlocked):
            self.prepare(self.approve(), publish_policy={})

    def test_too_many_variants_block_publish(self):
        with self.assertRaises(publish.PublishBlocked):
            self.prepare(
                self.approve(),
                publish_policy={"primary_format": "square", "max_ads_per_ad_set": 0},
            )

    def test_pending_visual_review_blocks_manifest(self):
        with self.assertRaises(publish.PublishBlocked):
            self.prepare(self.report)

    def test_publish_blocks_audience_plan_from_another_app(self):
        audience_plan = dict(self.audience_plan)
        audience_plan["app"] = "demo-app-c"

        with self.assertRaisesRegex(publish.PublishBlocked, "audience plan.*demo-app-c"):
            publish.prepare_manifest(
                self.approve(),
                self.capabilities,
                account_id="act_1",
                campaign_id="campaign_1",
                ad_set_id="adset_1",
                audience_plan=audience_plan,
                audience_id="br-cold-broad",
                markets=self.markets,
                publish_policy={"primary_format": "square", "max_ads_per_ad_set": 6},
                app_config=self.app_config,
                briefs=self.briefs,
                readiness_receipt=self.readiness_receipt,
                workspace_root=self.root,
                now=self.now,
            )

    def test_changing_report_app_after_visual_approval_blocks_publish(self):
        report = self.approve()
        report["app"] = "demo-app-c"

        with self.assertRaisesRegex(publish.PublishBlocked, "artefatos mudaram após QA"):
            self.prepare(report)

    def test_video_publish_is_blocked_until_upload_processing_and_paused_readback_exist(self):
        report = self.report
        report["records"][0]["media_kind"] = "video"
        report["input_digest"] = qa.canonical_input_digest(report["records"])
        approved = approve_report(report, "codex")

        with self.assertRaisesRegex(publish.PublishBlocked, "vídeo.*capability"):
            self.prepare(approved)

    def test_missing_meta_mcp_tool_blocks_manifest(self):
        capabilities = dict(self.capabilities)
        capabilities["tools"] = ["ads_create_ad"]

        with self.assertRaises(publish.PublishBlocked):
            self.prepare(self.approve(), capabilities)

    def test_stale_capability_receipt_blocks_manifest(self):
        capabilities = dict(self.capabilities)
        capabilities["checked_at"] = "2026-07-09T16:00:00Z"

        with self.assertRaises(publish.PublishBlocked):
            self.prepare(self.approve(), capabilities)

    def test_empty_qa_matrix_blocks_manifest(self):
        report = qa.build_report(
            "sunrise-demo",
            "empty",
            {"status": "pass", "errors": [], "warnings": [], "records": []},
        )
        report.update(
            {
                "version": 2,
                "provenance_required": True,
                "input_digest": qa.canonical_input_digest([]),
            }
        )
        report = approve_report(report, "codex")

        with self.assertRaises(publish.PublishBlocked):
            self.prepare(report)

    def test_manifest_is_paused_only_and_forbids_activation(self):
        manifest = self.prepare(self.approve())

        self.assertFalse(manifest["activation_allowed"])
        self.assertEqual(manifest["requested_status"], "PAUSED")
        self.assertTrue(all(item["requested_status"] == "PAUSED" for item in manifest["items"]))
        self.assertTrue(all(item["creative_name"] for item in manifest["items"]))
        self.assertTrue(all(item["ad_name"] for item in manifest["items"]))

    def test_active_receipt_is_rejected(self):
        manifest = self.prepare(self.approve())
        receipt = {
            "manifest_digest": manifest["manifest_digest"],
            "delivery_status": "ACTIVE",
            "items": [{"item_key": manifest["items"][0]["item_key"], "creative_id": "1", "ad_id": "2"}],
        }

        errors = publish.verify_receipt(
            manifest, receipt, expected_app="sunrise-demo"
        )

        self.assertTrue(any("ACTIVE" in error for error in errors))

    def test_receipt_requires_paused_status_and_rejects_duplicate_items(self):
        manifest = self.prepare(self.approve())
        item = {
            "item_key": manifest["items"][0]["item_key"],
            "creative_id": "1",
            "ad_id": "2",
        }
        receipt = {
            "manifest_digest": manifest["manifest_digest"],
            "delivery_status": "PAUSED",
            "items": [item, dict(item)],
        }

        errors = publish.verify_receipt(
            manifest, receipt, expected_app="sunrise-demo"
        )

        self.assertTrue(any("duplicados" in error for error in errors))
        self.assertTrue(any("não comprova status PAUSED" in error for error in errors))

    def test_receipt_rejects_a_manifest_changed_after_digest_was_created(self):
        manifest = self.prepare(self.approve())
        manifest["campaign_id"] = "campaign_tampered"
        receipt = {
            "manifest_digest": manifest["manifest_digest"],
            "delivery_status": "PAUSED",
            "items": [
                {
                    "item_key": manifest["items"][0]["item_key"],
                    "creative_id": "1",
                    "ad_id": "2",
                    "status": "PAUSED",
                }
            ],
        }

        errors = publish.verify_receipt(
            manifest, receipt, expected_app="sunrise-demo"
        )

        self.assertTrue(any("manifest_digest" in error and "manifesto" in error for error in errors))

    def test_receipt_rejects_missing_or_duplicate_manifest_item_keys(self):
        base = self.prepare(self.approve())
        for label, items in (
            ("missing", [{**base["items"][0], "item_key": ""}]),
            ("duplicate", [base["items"][0], dict(base["items"][0])]),
        ):
            with self.subTest(label=label):
                manifest = {**base, "items": items}
                manifest["manifest_digest"] = publish.canonical_digest(
                    {
                        key: value
                        for key, value in manifest.items()
                        if key != "manifest_digest"
                    }
                )
                receipt = {
                    "manifest_digest": manifest["manifest_digest"],
                    "delivery_status": "PAUSED",
                    "items": [],
                }

                errors = publish.verify_receipt(
                    manifest, receipt, expected_app="sunrise-demo"
                )

                self.assertTrue(
                    any("manifest item_key" in error for error in errors),
                    errors,
                )

    def test_preflight_aggregation_blocks_any_failed_component(self):
        result = forge.aggregate_gates(
            [
                {"name": "locales", "errors": [], "warnings": []},
                {"name": "signals", "errors": ["stale"], "warnings": []},
            ]
        )

        self.assertFalse(result["ok"])
        self.assertIn("signals: stale", result["errors"])

    def test_requested_app_app_config_signals_and_research_must_share_one_slug(self):
        errors = forge.validate_app_identity(
            "sunrise-demo",
            {"slug": "sunrise-demo"},
            {"app": "demo-app-c"},
            {"app": "sunrise-demo"},
        )

        self.assertTrue(any("signals.app" in error and "demo-app-c" in error for error in errors))

    def test_app_config_slug_must_match_requested_slug(self):
        errors = forge.validate_app_identity(
            "sunrise-demo",
            {"slug": "demo-app-c"},
            {"app": "sunrise-demo"},
            {"app": "sunrise-demo"},
        )

        self.assertTrue(any("app.slug" in error and "demo-app-c" in error for error in errors))

    def test_recipe_research_refs_must_resolve_to_structured_evidence(self):
        recipes = [
            {"name": "morning", "research_refs": ["meta-known"]},
            {"name": "sleep", "research_refs": ["meta-missing"]},
        ]
        research_data = {"creatives": [{"id": "meta-known"}]}

        errors = forge.validate_research_refs(recipes, research_data)

        self.assertTrue(any("meta-missing" in error for error in errors))

    def test_unknown_research_format_blocks_a_video_recipe(self):
        recipes = [
            {
                "name": "sleep-video",
                "media_type": "video",
                "research_refs": ["meta-unknown"],
            }
        ]
        research_data = {
            "creatives": [{"id": "meta-unknown", "format": "unknown"}]
        }

        errors = forge.validate_research_refs(recipes, research_data)

        self.assertTrue(any("formato" in error and "meta-unknown" in error for error in errors))

    def test_unknown_research_format_remains_compatible_with_legacy_image_recipe(self):
        recipes = [{"name": "legacy-image", "research_refs": ["meta-unknown"]}]
        research_data = {
            "creatives": [{"id": "meta-unknown", "format": "unknown"}]
        }

        errors = forge.validate_research_refs(recipes, research_data)

        self.assertEqual(errors, [])

    def test_clean_output_removes_only_regenerable_top_level_pngs(self):
        output = self.root / "output"
        nested = output / "keep"
        nested.mkdir(parents=True)
        (output / "old.png").write_bytes(b"old")
        (output / "notes.txt").write_text("keep")
        (nested / "nested.png").write_bytes(b"keep")

        removed = forge.clean_output(output)

        self.assertEqual(removed, 1)
        self.assertFalse((output / "old.png").exists())
        self.assertTrue((output / "notes.txt").exists())
        self.assertTrue((nested / "nested.png").exists())

    def test_video_surfaces_are_part_of_the_app_preflight(self):
        swipe = self.root / "swipe" / "sunrise-demo"
        recipes = self.root / "recipes" / "sunrise-demo" / "video"
        swipe.mkdir(parents=True, exist_ok=True)
        recipes.mkdir(parents=True)
        (swipe / "video-patterns.yaml").write_text("version: 1\n")
        (recipes / "concept.yaml").write_text("version: 1\n")

        with mock.patch.object(
            forge.video_mining,
            "audit_video_patterns",
            return_value={"errors": [], "warnings": [], "probes": {}},
        ) as mining, mock.patch.object(
            forge.video,
            "audit_recipe",
            return_value={"errors": ["video blocked"], "warnings": []},
        ) as recipe_audit:
            result = forge.audit_video_surfaces(
                "sunrise-demo", {"slug": "sunrise-demo"}, root=self.root
            )

        mining.assert_called_once()
        recipe_audit.assert_called_once()
        self.assertTrue(any("video blocked" in error for error in result["recipes"]["errors"]))


if __name__ == "__main__":
    unittest.main()
