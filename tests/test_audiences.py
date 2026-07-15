import hashlib
import unittest
from datetime import datetime, timezone

from PIL import Image
import tempfile
from pathlib import Path

from scripts import audiences, publish, qa
from tests.qa_fixtures import approve_report, production_report


def make_markets():
    return [
        {
            "id": "br",
            "countries": ["BR"],
            "locale": "pt-BR",
            "app_locale": "pt-BR",
            "copy_language": "pt",
        },
        {
            "id": "us",
            "countries": ["US"],
            "locale": "en-US",
            "app_locale": "en",
            "copy_language": "en",
        },
    ]


def make_audience(**overrides):
    audience = {
        "id": "br-cold-broad",
        "market": "br",
        "funnel_stage": "cold",
        "hypothesis": "PT nativo + funil orgânico BR forte; broad por país/idioma.",
        "targeting": {
            "kind": "broad",
            "countries": ["BR"],
            "advantage_audience": True,
            "age_min": 18,
        },
        "optimization_event": "app_install",
        "data_source": {
            "kind": "posthog_signals",
            "ref": "signals/sunrise-demo.yaml",
            "observed_at": "2026-07-09T18:04:26Z",
        },
        "confidence": "medium",
        "confidence_rationale": "Fictional demo rationale for the shape of the field.",
        "exclusions": [],
        "status": "approved",
        "approved_by": "demo-operator",
        "creatives": {"copy_language": "pt"},
    }
    audience.update(overrides)
    return audience


def make_plan(*audience_list, **overrides):
    plan = {
        "version": 1,
        "app": "sunrise-demo",
        "updated_at": "2026-07-09",
        "policy": {"allow_interest_targeting": False, "special_ad_categories": []},
        "audiences": list(audience_list) or [make_audience()],
    }
    plan.update(overrides)
    return plan


class AudiencePlanAuditTests(unittest.TestCase):
    def setUp(self):
        self.markets = make_markets()

    def audit(self, plan):
        return audiences.audit_plan(plan, self.markets)

    def test_valid_plan_passes(self):
        result = self.audit(make_plan())
        self.assertEqual(result["errors"], [])

    def test_unknown_market_is_blocked(self):
        result = self.audit(make_plan(make_audience(market="germany")))
        self.assertTrue(any("market" in e for e in result["errors"]))

    def test_country_drift_from_market_is_blocked(self):
        audience = make_audience()
        audience["targeting"] = dict(audience["targeting"], countries=["BR", "PT"])
        result = self.audit(make_plan(audience))
        self.assertTrue(any("countries" in e for e in result["errors"]))

    def test_copy_language_must_match_market(self):
        result = self.audit(make_plan(make_audience(creatives={"copy_language": "en"})))
        self.assertTrue(any("copy_language" in e for e in result["errors"]))

    def test_interest_targeting_is_blocked_when_policy_forbids(self):
        audience = make_audience()
        audience["targeting"] = dict(
            audience["targeting"], interests=[{"id": "0000000000001", "name": "Running"}]
        )
        result = self.audit(make_plan(audience))
        self.assertTrue(any("interest" in e for e in result["errors"]))

    def test_cold_stage_requires_broad_targeting(self):
        audience = make_audience()
        audience["targeting"] = dict(audience["targeting"], kind="custom_audience")
        result = self.audit(make_plan(audience))
        self.assertTrue(any("cold" in e for e in result["errors"]))

    def test_warm_stage_requires_own_data_custom_audience(self):
        audience = make_audience(funnel_stage="warm")
        result = self.audit(make_plan(audience))
        self.assertTrue(any("warm" in e for e in result["errors"]))

    def test_lookalike_requires_min_own_seed(self):
        audience = make_audience(funnel_stage="lookalike")
        audience["targeting"] = dict(
            audience["targeting"], kind="lookalike", seed={"kind": "own_purchasers", "size": 12}
        )
        result = self.audit(make_plan(audience))
        self.assertTrue(any("seed" in e for e in result["errors"]))

    def test_approved_requires_approver(self):
        result = self.audit(make_plan(make_audience(approved_by=None)))
        self.assertTrue(any("approved_by" in e for e in result["errors"]))

    def test_missing_hypothesis_or_rationale_is_blocked(self):
        result = self.audit(
            make_plan(make_audience(hypothesis="", confidence_rationale=""))
        )
        joined = " ".join(result["errors"])
        self.assertIn("hypothesis", joined)
        self.assertIn("confidence_rationale", joined)

    def test_duplicate_ids_are_blocked(self):
        result = self.audit(make_plan(make_audience(), make_audience()))
        self.assertTrue(any("duplicado" in e for e in result["errors"]))

    def test_invalid_optimization_event_is_blocked(self):
        result = self.audit(make_plan(make_audience(optimization_event="page_likes")))
        self.assertTrue(any("optimization_event" in e for e in result["errors"]))

    def test_low_confidence_is_a_warning_not_error(self):
        audience = make_audience(
            confidence="low", status="draft", approved_by=None
        )
        result = self.audit(make_plan(audience))
        self.assertEqual(result["errors"], [])
        self.assertTrue(any("low" in w for w in result["warnings"]))


class PublishAudienceGateTests(unittest.TestCase):
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
                    "cta": "Baixe grátis",
                }
        report = production_report(
            self.root,
            "sunrise-demo",
            "batch-1",
            [spec],
        )
        self.report = approve_report(report, "claude")
        self.capabilities = {
            "provider": "meta_ads_mcp",
            "agent": "claude",
            "checked_at": "2026-07-09T18:00:00Z",
            "tools": ["ads_create_creative", "ads_create_ad", "ads_get_ad"],
            "readback_tool": "ads_get_ad",
        }
        self.now = datetime(2026, 7, 9, 18, 30, tzinfo=timezone.utc)
        self.markets = make_markets()
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

    def prepare(self, plan, audience_id="br-cold-broad"):
        return publish.prepare_manifest(
            self.report,
            self.capabilities,
            account_id="act_1",
            campaign_id="campaign_1",
            ad_set_id="adset_1",
            audience_plan=plan,
            audience_id=audience_id,
            markets=self.markets,
            publish_policy={"primary_format": "square", "max_ads_per_ad_set": 6},
            app_config=self.app_config,
            briefs=self.briefs,
            readiness_receipt=self.readiness_receipt,
            evidence_root=self.root,
            workspace_root=self.root,
            now=self.now,
        )

    def test_manifest_embeds_audience_and_filters_to_its_market(self):
        manifest = self.prepare(make_plan())
        self.assertEqual(manifest["audience"]["id"], "br-cold-broad")
        self.assertEqual(manifest["audience"]["countries"], ["BR"])
        self.assertEqual(manifest["audience"]["optimization_event"], "app_install")
        self.assertEqual(len(manifest["items"]), 1)
        self.assertTrue(all(i["locale"] == "pt-BR" for i in manifest["items"]))

    def test_draft_audience_blocks_publish(self):
        plan = make_plan(make_audience(status="draft", approved_by=None))
        with self.assertRaises(publish.PublishBlocked):
            self.prepare(plan)

    def test_unknown_audience_id_blocks_publish(self):
        with self.assertRaises(publish.PublishBlocked):
            self.prepare(make_plan(), audience_id="us-cold-broad")

    def test_invalid_plan_blocks_publish(self):
        plan = make_plan(make_audience(market="germany"))
        with self.assertRaises(publish.PublishBlocked):
            self.prepare(plan)

    def test_market_without_creatives_blocks_publish(self):
        us_audience = make_audience(
            id="us-cold-broad",
            market="us",
            creatives={"copy_language": "en"},
        )
        us_audience["targeting"] = dict(us_audience["targeting"], countries=["US"])
        plan = make_plan(make_audience(), us_audience)
        with self.assertRaises(publish.PublishBlocked):
            self.prepare(plan, audience_id="us-cold-broad")


if __name__ == "__main__":
    unittest.main()
