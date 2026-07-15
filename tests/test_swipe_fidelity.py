import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from scripts import publish, qa, research
from tests.qa_fixtures import approve_report, production_report


RESEARCH = {
    "creatives": [
        {"id": "meta-pain", "angle": "pain-to-peace", "lineage": "competitor_pattern"},
        {"id": "demo-pattern", "angle": "morning-relief", "lineage": "competitor_pattern"},
        {"id": "meta-sleep", "angle": "sleep", "lineage": "competitor_pattern"},
    ]
}


class SwipeAlignmentTests(unittest.TestCase):
    def test_matching_angle_passes(self):
        errors = research.swipe_alignment_errors(
            "morning-walk",
            "pain-headline-cta",
            ["pain-to-peace"],
            ["meta-pain", "demo-pattern"],
            RESEARCH,
            swiped_from="pain-to-peace structure",
        )
        self.assertEqual(errors, [])

    def test_disjoint_angles_block(self):
        errors = research.swipe_alignment_errors(
            "review",
            "review-quote",
            ["social-proof"],
            ["meta-pain"],
            RESEARCH,
            swiped_from="pain-to-peace structure",
        )
        self.assertTrue(any("fora da estratégia" in e for e in errors))

    def test_template_without_swipe_angles_blocks(self):
        errors = research.swipe_alignment_errors(
            "morning-walk", "pain-headline-cta", [], ["meta-pain"], RESEARCH,
            swiped_from="pain-to-peace structure",
        )
        self.assertTrue(any("swipe_angles" in e for e in errors))

    def test_angle_comparison_is_normalized(self):
        data = {"creatives": [{"id": "meta-21", "angle": "21-day challenge"}]}
        errors = research.swipe_alignment_errors(
            "desafio", "photo-overlay", ["21-Day-Challenge"], ["meta-21"], data,
            swiped_from="21-day challenge structure",
        )
        self.assertEqual(errors, [])

    def test_unresolved_refs_are_ignored_here(self):
        # missing refs are blocked by validate_research_refs; this gate only
        # judges alignment of the refs that resolve
        errors = research.swipe_alignment_errors(
            "morning-walk",
            "pain-headline-cta",
            ["pain-to-peace"],
            ["meta-missing", "meta-pain"],
            RESEARCH,
            swiped_from="pain-to-peace structure",
        )
        self.assertEqual(errors, [])

    def test_only_competitor_lineage_requires_swipe_structure(self):
        exploratory = research.swipe_alignment_errors(
            "original",
            "free-composition",
            [],
            ["meta-pain"],
            RESEARCH,
            execution_lineage="original",
            execution_ref=None,
            swiped_from="",
        )
        competitor = research.swipe_alignment_errors(
            "adaptation",
            "pain-headline-cta",
            ["pain-to-peace"],
            ["meta-pain"],
            RESEARCH,
            execution_lineage="competitor_pattern",
            execution_ref="meta-pain",
            swiped_from="",
        )

        self.assertEqual(exploratory, [])
        self.assertTrue(any("swiped_from" in error for error in competitor), competitor)

    def test_competitor_lineage_blocks_omitted_swiped_from(self):
        errors = research.swipe_alignment_errors(
            "adaptation",
            "pain-headline-cta",
            ["pain-to-peace"],
            ["meta-pain"],
            RESEARCH,
            execution_lineage="competitor_pattern",
            execution_ref="meta-pain",
            swiped_from=None,
        )

        self.assertTrue(any("swiped_from" in error for error in errors), errors)


class SwipeFidelityQaAndPublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        image = self.root / "creative.png"
        Image.new("RGB", (1080, 1080), "white").save(image)
        self.spec = {
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
            "cta": "Baixe grátis",
        }
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

    def approved_report(self, spec):
        report = production_report(
            self.root,
            "sunrise-demo",
            "batch-1",
            [spec],
        )
        return approve_report(report, "claude")

    def prepare(self, report):
        return publish.prepare_manifest(
            report,
            self.capabilities,
            account_id="act_1",
            campaign_id="campaign_1",
            ad_set_id="adset_1",
            audience_plan=self.audience_plan,
            audience_id="br-cold-broad",
            markets=self.markets,
            publish_policy={"primary_format": "square", "max_ads_per_ad_set": 6},
            app_config=self.app_config,
            briefs=self.briefs,
            readiness_receipt=self.readiness_receipt,
            evidence_root=self.root,
            workspace_root=self.root,
            now=self.now,
        )

    def test_visual_qa_requires_lineage_fidelity_check(self):
        self.assertIn("lineage_fidelity", qa.VISUAL_CHECKS)
        automated = qa.audit_outputs([self.spec])
        report = qa.build_report("sunrise-demo", "batch-1", automated)
        checks = {name: True for name in qa.VISUAL_CHECKS}
        checks["lineage_fidelity"] = False
        with self.assertRaises(ValueError):
            qa.approve_visual(report, "claude", checks)

    def test_manifest_items_carry_swipe_lineage(self):
        manifest = self.prepare(self.approved_report(self.spec))
        item = manifest["items"][0]
        self.assertEqual(item["research_refs"], ["meta-pain"])
        self.assertEqual(item["swiped_from"], "StrideCo ES — dor→alívio")

    def test_record_without_lineage_blocks_publish(self):
        report = self.approved_report(self.spec)
        report["records"][0].pop("research_refs")
        report["records"][0].pop("swiped_from")
        with self.assertRaises(publish.PublishBlocked):
            self.prepare(report)


if __name__ == "__main__":
    unittest.main()
