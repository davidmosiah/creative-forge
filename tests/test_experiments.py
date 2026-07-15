import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from scripts import experiments, publish


def canonical_json_bytes(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode()


class ExperimentContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.evidence_root = Path(self.temp.name).resolve()
        self.app_config_path = (
            self.evidence_root / "apps" / "sunrise-demo.yaml"
        )
        self.app_config_path.parent.mkdir(parents=True)
        self.app_config_path.write_text(
            "version: 1\n"
            "slug: sunrise-demo\n"
            "readiness:\n"
            "  required_receipts:\n"
            "    app_store_destination: required_live\n"
        )
        self.response_relative = Path("readbacks/paused-item.json")
        self.response_path = self.evidence_root / self.response_relative
        self.response_path.parent.mkdir(parents=True)
        self.readiness_relative = Path("readbacks/readiness.json")
        self.readiness_path = self.evidence_root / self.readiness_relative
        self.readiness_path.write_bytes(b'{"ready":true}')

    def tearDown(self):
        self.temp.cleanup()

    def raw_metrics(self):
        return {
            "schema": "creative-forge/meta-insights-readback@1",
            "platform": "meta",
            "provider": "meta_ads_mcp",
            "tool": "ads_get_insights",
            "observed_at": "2026-07-10T12:10:00Z",
            "binding": {
                "app": "sunrise-demo",
                "item_key": "item-123",
                "brief_ref": "demo-pilot",
                "concept_id": "morning-walk-relief",
                "variant_id": "morning-walk",
                "account_id": "act-123",
                "campaign_id": "campaign-123",
                "ad_set_id": "adset-123",
                "creative_id": "creative-123",
                "ad_id": "ad-123",
                "artifact_sha256": "f" * 64,
                "date_window": {"start": "2026-07-01", "end": "2026-07-07"},
                "currency": "BRL",
                "attribution_window": {"click_days": 7, "view_days": 1},
            },
            "metrics": {
                "impressions": 10000,
                "clicks": 250,
                "installs": 80,
                "spend_minor": 12000,
                "revenue_minor": 18000,
                "purchases": 12,
            },
        }

    def manifest(self):
        manifest = {
            "version": 2,
            "provider": "meta_ads_mcp",
            "app": "sunrise-demo",
            "account_id": "act-123",
            "campaign_id": "campaign-123",
            "ad_set_id": "adset-123",
            "created_at": "2026-07-10T11:50:00Z",
            "requested_status": "PAUSED",
            "activation_allowed": False,
            "readback_requirement": {
                "provider": "meta_ads_mcp",
                "tool": "ads_get_ad",
                "verification_basis": "live_provider_readback",
                "local_validation_sufficient": False,
            },
            "destination": {
                "ref": "default",
                "type": "app_store",
                "url": "https://apps.apple.com/app/id1",
                "custom_product_page_id": None,
            },
            "app_config_provenance": {
                "path": str(self.app_config_path),
                "resolved_path": str(self.app_config_path.resolve()),
                "sha256": hashlib.sha256(
                    self.app_config_path.read_bytes()
                ).hexdigest(),
                "readiness_policy_digest": publish.canonical_digest(
                    {
                        "required_receipts": {
                            "app_store_destination": "required_live"
                        }
                    }
                ),
            },
            "destination_readiness": {
                "receipt_type": "app_store_destination",
                "provider": "app_store_connect_api",
                "tool": "apps_get_app_store_version_localizations",
                "app": "sunrise-demo",
                "status": "ready",
                "destination": {
                    "ref": "default",
                    "type": "app_store",
                    "url": "https://apps.apple.com/app/id1",
                    "custom_product_page_id": None,
                },
                "observed_at": "2026-07-10T11:55:00Z",
                "response_path": self.readiness_relative.as_posix(),
                "response_digest": hashlib.sha256(
                    self.readiness_path.read_bytes()
                ).hexdigest(),
                "verification_basis": "live_provider_readback",
                "local_validation_sufficient": False,
            },
            "runtime_readiness": [],
            "required_readiness_receipt_types": ["app_store_destination"],
            "items": [
                {
                    "item_key": "item-123",
                    "brief_ref": "demo-pilot",
                    "concept_id": "morning-walk-relief",
                    "variant_id": "morning-walk",
                    "sha256": "f" * 64,
                    "requested_status": "PAUSED",
                }
            ],
        }
        manifest["manifest_digest"] = experiments.canonical_digest(manifest)
        return manifest

    def publish_receipt(self, manifest):
        item = {
            "item_key": "item-123",
            "provider": "meta_ads_mcp",
            "tool": "ads_get_ad",
            "account_id": "act-123",
            "campaign_id": "campaign-123",
            "ad_set_id": "adset-123",
            "creative_id": "creative-123",
            "ad_id": "ad-123",
            "artifact_sha256": "f" * 64,
            "status": "PAUSED",
            "observed_at": "2026-07-10T12:00:00Z",
        }
        envelope = {
            "schema": "creative-forge/meta-ad-readback@1",
            "provider": item["provider"],
            "tool": item["tool"],
            "observed_at": item["observed_at"],
            "binding": {
                field: item[field]
                for field in (
                    "item_key",
                    "account_id",
                    "campaign_id",
                    "ad_set_id",
                    "creative_id",
                    "ad_id",
                    "artifact_sha256",
                    "status",
                )
            },
            "provider_response": {
                "id": "ad-123",
                "creative_id": "creative-123",
                "account_id": "act-123",
                "campaign_id": "campaign-123",
                "ad_set_id": "adset-123",
                "artifact_sha256": "f" * 64,
                "status": "PAUSED",
            },
        }
        self.response_path.write_bytes(canonical_json_bytes(envelope))
        item.update(
            response_path=self.response_relative.as_posix(),
            response_digest=hashlib.sha256(self.response_path.read_bytes()).hexdigest(),
        )
        return {
            "manifest_digest": manifest["manifest_digest"],
            "provider": "meta_ads_mcp",
            "verification_basis": "live_provider_readback",
            "local_validation_sufficient": False,
            "delivery_status": "PAUSED",
            "items": [item],
        }

    def valid_experiment(self):
        manifest = self.manifest()
        receipt = self.publish_receipt(manifest)
        raw_metrics = self.raw_metrics()
        experiment = {
            "version": 1,
            "id": "exp-2026-07-01",
            "app": "sunrise-demo",
            "manifest_digest": manifest["manifest_digest"],
            "publish_receipt_digest": experiments.canonical_digest(receipt),
            "brief_ref": "demo-pilot",
            "concept_id": "morning-walk-relief",
            "variant_id": "morning-walk",
            "item_key": "item-123",
            "account_id": "act-123",
            "campaign_id": "campaign-123",
            "ad_set_id": "adset-123",
            "creative_id": "creative-123",
            "ad_id": "ad-123",
            "market": "br",
            "currency": "BRL",
            "attribution_window": {"click_days": 7, "view_days": 1},
            "sample_status": "sufficient",
            "metrics": {
                "impressions": 10000,
                "clicks": 250,
                "installs": 80,
                "spend_minor": 12000,
                "revenue_minor": 18000,
                "purchases": 12,
            },
            "metrics_provenance": {
                "platform": "meta",
                "provider": "meta_ads_mcp",
                "tool": "ads_get_insights",
                "response_digest": experiments.canonical_digest(raw_metrics),
                "observed_at": "2026-07-10T12:10:00Z",
                "date_window": {"start": "2026-07-01", "end": "2026-07-07"},
                "currency": "BRL",
                "attribution_window": {"click_days": 7, "view_days": 1},
                "app": "sunrise-demo",
                "item_key": "item-123",
                "brief_ref": "demo-pilot",
                "concept_id": "morning-walk-relief",
                "variant_id": "morning-walk",
                "account_id": "act-123",
                "campaign_id": "campaign-123",
                "ad_set_id": "adset-123",
                "creative_id": "creative-123",
                "ad_id": "ad-123",
            },
            "agent_decision": {
                "classification": "green",
                "rationale": "Qualified CPI and downstream purchases are stronger than the current baseline.",
                "likely_cause": "The first beat makes the morning use case concrete.",
                "next_action": "Create one hook iteration.",
                "requires_human_confirmation": False,
                "decided_by": "codex",
            },
        }
        return experiment, manifest, receipt, raw_metrics

    def audit(self, experiment, manifest, receipt, raw_metrics):
        return experiments.audit_experiment(
            experiment,
            expected_app="sunrise-demo",
            manifest=manifest,
            publish_receipt=receipt,
            metrics_source=raw_metrics,
            evidence_root=self.evidence_root,
            workspace_root=self.evidence_root,
        )

    def test_valid_experiment_and_computed_metrics_pass(self):
        experiment, manifest, receipt, raw_metrics = self.valid_experiment()

        result = self.audit(experiment, manifest, receipt, raw_metrics)
        metrics = experiments.calculate_metrics(experiment)

        self.assertEqual(result["errors"], [])
        self.assertEqual(metrics["ctr"], 0.025)
        self.assertEqual(metrics["cpi_minor"], 150.0)
        self.assertEqual(metrics["roas"], 1.5)

    def test_publish_receipt_and_metric_source_are_required(self):
        experiment, manifest, receipt, raw_metrics = self.valid_experiment()

        no_receipt = experiments.audit_experiment(
            experiment,
            expected_app="sunrise-demo",
            manifest=manifest,
            metrics_source=raw_metrics,
            evidence_root=self.evidence_root,
            workspace_root=self.evidence_root,
        )
        no_metrics = experiments.audit_experiment(
            experiment,
            expected_app="sunrise-demo",
            manifest=manifest,
            publish_receipt=receipt,
            evidence_root=self.evidence_root,
            workspace_root=self.evidence_root,
        )

        self.assertTrue(any("publish receipt" in error for error in no_receipt["errors"]))
        self.assertTrue(any("metrics source" in error for error in no_metrics["errors"]))

    def test_receipt_and_metric_source_digests_are_verified(self):
        experiment, manifest, receipt, raw_metrics = self.valid_experiment()
        experiment["publish_receipt_digest"] = "0" * 64
        experiment["metrics_provenance"]["response_digest"] = "1" * 64

        result = self.audit(experiment, manifest, receipt, raw_metrics)

        self.assertTrue(any("publish_receipt_digest" in error for error in result["errors"]))
        self.assertTrue(any("response_digest" in error for error in result["errors"]))

    def test_publish_receipt_raw_readback_is_revalidated(self):
        experiment, manifest, receipt, raw_metrics = self.valid_experiment()
        self.response_path.write_bytes(b'{"tampered":true}\n')

        result = self.audit(experiment, manifest, receipt, raw_metrics)

        self.assertTrue(
            any("response_digest não corresponde" in error for error in result["errors"]),
            result["errors"],
        )

    def test_publish_receipt_item_must_match_manifest_provider_and_artifact(self):
        experiment, manifest, receipt, raw_metrics = self.valid_experiment()
        receipt["items"][0]["provider"] = "other-provider"
        receipt["items"][0]["artifact_sha256"] = "0" * 64
        experiment["publish_receipt_digest"] = experiments.canonical_digest(receipt)

        result = self.audit(experiment, manifest, receipt, raw_metrics)

        self.assertTrue(any("provider" in error for error in result["errors"]), result["errors"])
        self.assertTrue(any("artifact_sha256" in error for error in result["errors"]), result["errors"])

    def test_metric_provider_must_match_the_publish_manifest(self):
        experiment, manifest, receipt, raw_metrics = self.valid_experiment()
        experiment["metrics_provenance"]["provider"] = "other-provider"

        result = self.audit(experiment, manifest, receipt, raw_metrics)

        self.assertTrue(any("provider" in error for error in result["errors"]), result["errors"])

    def test_platform_ids_bind_experiment_manifest_receipt_and_metrics(self):
        experiment, manifest, receipt, raw_metrics = self.valid_experiment()
        experiment["ad_id"] = "wrong-ad"
        experiment["metrics_provenance"]["campaign_id"] = "wrong-campaign"

        result = self.audit(experiment, manifest, receipt, raw_metrics)

        self.assertTrue(any("ad_id" in error for error in result["errors"]))
        self.assertTrue(any("metrics_provenance.campaign_id" in error for error in result["errors"]))

    def test_metric_window_currency_attribution_and_lineage_are_bound(self):
        experiment, manifest, receipt, raw_metrics = self.valid_experiment()
        experiment["metrics_provenance"]["date_window"] = {
            "start": "2026-07-08",
            "end": "2026-07-01",
        }
        experiment["metrics_provenance"]["currency"] = "USD"
        experiment["metrics_provenance"]["attribution_window"] = {
            "click_days": 1,
            "view_days": 0,
        }
        experiment["metrics_provenance"]["variant_id"] = "wrong"

        result = self.audit(experiment, manifest, receipt, raw_metrics)

        self.assertTrue(any("date_window" in error for error in result["errors"]))
        self.assertTrue(any("currency" in error for error in result["errors"]))
        self.assertTrue(any("attribution_window" in error for error in result["errors"]))
        self.assertTrue(any("metrics_provenance.variant_id" in error for error in result["errors"]))

    def test_normalized_source_tool_bindings_and_exact_metrics_cannot_diverge(self):
        experiment, manifest, receipt, raw_metrics = self.valid_experiment()
        raw_metrics["tool"] = "ads_get_other"
        raw_metrics["binding"]["ad_id"] = "another-ad"
        raw_metrics["metrics"]["clicks"] = 251
        experiment["metrics_provenance"]["response_digest"] = experiments.canonical_digest(
            raw_metrics
        )

        result = self.audit(experiment, manifest, receipt, raw_metrics)

        self.assertTrue(any("source.tool" in error for error in result["errors"]))
        self.assertTrue(any("binding.ad_id" in error for error in result["errors"]))
        self.assertTrue(any("métricas exatas" in error for error in result["errors"]))

    def test_experiment_reuses_full_historical_publish_verification(self):
        experiment, manifest, receipt, raw_metrics = self.valid_experiment()
        self.readiness_path.write_bytes(b'{"ready":false}')

        result = self.audit(experiment, manifest, receipt, raw_metrics)

        self.assertTrue(
            any("manifest readiness" in error and "response_digest" in error for error in result["errors"]),
            result["errors"],
        )

    def test_attribution_currency_and_external_ids_are_required(self):
        experiment, manifest, receipt, raw_metrics = self.valid_experiment()
        experiment.pop("attribution_window")
        experiment["currency"] = "REAL"
        experiment.pop("creative_id")

        result = self.audit(experiment, manifest, receipt, raw_metrics)

        self.assertTrue(any("attribution_window" in error for error in result["errors"]))
        self.assertTrue(any("currency" in error for error in result["errors"]))
        self.assertTrue(any("creative_id" in error for error in result["errors"]))

    def test_negative_or_arithmetically_impossible_metrics_are_rejected(self):
        experiment, manifest, receipt, raw_metrics = self.valid_experiment()
        experiment["metrics"]["spend_minor"] = -1
        experiment["metrics"]["clicks"] = 10001

        result = self.audit(experiment, manifest, receipt, raw_metrics)

        self.assertTrue(any("spend_minor" in error for error in result["errors"]))
        self.assertTrue(any("clicks" in error and "impressions" in error for error in result["errors"]))

        for invalid in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(invalid=invalid):
                candidate = deepcopy(experiment)
                candidate["metrics"]["spend_minor"] = invalid
                audited = self.audit(candidate, manifest, receipt, raw_metrics)
                self.assertTrue(any("spend_minor" in error for error in audited["errors"]))

    def test_roas_is_unavailable_without_verified_spend_and_revenue(self):
        experiment, _, _, _ = self.valid_experiment()
        experiment["metrics"].pop("revenue_minor")

        metrics = experiments.calculate_metrics(experiment)

        self.assertIsNone(metrics["roas"])
        self.assertIn("ROAS", metrics["unavailable"])

    def test_agent_decision_is_required_for_sufficient_sample(self):
        experiment, manifest, receipt, raw_metrics = self.valid_experiment()
        experiment.pop("agent_decision")

        result = self.audit(experiment, manifest, receipt, raw_metrics)

        self.assertTrue(any("agent_decision" in error for error in result["errors"]))

    def test_scale_or_budget_recommendation_requires_human_confirmation_flag(self):
        experiment, manifest, receipt, raw_metrics = self.valid_experiment()
        experiment["agent_decision"]["next_action"] = "Scale budget by 20 percent"
        experiment["agent_decision"]["requires_human_confirmation"] = False

        result = self.audit(experiment, manifest, receipt, raw_metrics)

        self.assertTrue(any("human" in error for error in result["errors"]))

    def with_video_metrics(self, video_3s=4000, video_thruplay=1500, drop=None):
        experiment, manifest, receipt, raw_metrics = self.valid_experiment()
        video = {"video_3s_views": video_3s, "video_thruplay_views": video_thruplay}
        for field in drop or ():
            video.pop(field, None)
        raw_metrics["metrics"] = {**raw_metrics["metrics"], **video}
        experiment["metrics"] = dict(raw_metrics["metrics"])
        experiment["metrics_provenance"]["response_digest"] = experiments.canonical_digest(
            raw_metrics
        )
        return experiment, manifest, receipt, raw_metrics

    def test_video_experiment_derives_hook_and_hold_rates(self):
        experiment, manifest, receipt, raw_metrics = self.with_video_metrics()

        result = self.audit(experiment, manifest, receipt, raw_metrics)
        metrics = experiments.calculate_metrics(experiment)

        self.assertEqual(result["errors"], [])
        self.assertEqual(metrics["hook_rate"], 0.4)
        self.assertEqual(metrics["hold_rate"], 0.375)
        self.assertEqual(metrics["context"], "complete")

    def test_image_experiment_keeps_contract_without_video_fields(self):
        experiment, _, _, _ = self.valid_experiment()

        metrics = experiments.calculate_metrics(experiment)

        self.assertIsNone(metrics["hook_rate"])
        self.assertIsNone(metrics["hold_rate"])
        self.assertNotIn("HOOK_RATE", metrics["unavailable"])
        self.assertNotIn("HOLD_RATE", metrics["unavailable"])
        self.assertEqual(metrics["context"], "complete")

    def test_partial_video_metrics_become_insufficient_data_not_zero(self):
        experiment, manifest, receipt, raw_metrics = self.with_video_metrics(
            drop=["video_thruplay_views"]
        )

        result = self.audit(experiment, manifest, receipt, raw_metrics)
        metrics = experiments.calculate_metrics(experiment)

        self.assertEqual(result["errors"], [])
        self.assertEqual(metrics["hook_rate"], 0.4)
        self.assertIsNone(metrics["hold_rate"])
        self.assertIn("HOLD_RATE", metrics["unavailable"])
        self.assertEqual(metrics["context"], "insufficient_data")

    def test_thruplay_exceeding_3s_views_is_rejected_as_fabricated(self):
        experiment, manifest, receipt, raw_metrics = self.with_video_metrics(
            video_3s=100, video_thruplay=200
        )

        result = self.audit(experiment, manifest, receipt, raw_metrics)

        self.assertTrue(
            any("video_thruplay_views" in error for error in result["errors"])
        )

    def test_thruplay_without_3s_views_is_rejected(self):
        experiment, manifest, receipt, raw_metrics = self.with_video_metrics(
            drop=["video_3s_views"]
        )

        result = self.audit(experiment, manifest, receipt, raw_metrics)

        self.assertTrue(
            any("exige video_3s_views" in error for error in result["errors"])
        )


if __name__ == "__main__":
    unittest.main()
