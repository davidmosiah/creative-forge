import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from scripts import experiments, publish, qa


def canonical_json_bytes(value):
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode()


def publish_readback_envelope(item, provider_response=None):
    return {
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
        "provider_response": provider_response
        or {
            "id": item["ad_id"],
            "creative_id": item["creative_id"],
            "account_id": item["account_id"],
            "campaign_id": item["campaign_id"],
            "ad_set_id": item["ad_set_id"],
            "artifact_sha256": item["artifact_sha256"],
            "status": item["status"],
        },
    }


class PublishEvidenceContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        first = self.root / "first.png"
        second = self.root / "second.png"
        Image.new("RGB", (1080, 1080), "white").save(first)
        Image.new("RGB", (1080, 1080), "black").save(second)
        specs = []
        for path, variant in ((first, "hook-a"), (second, "hook-b")):
            specs.append(
                {
                    "path": str(path),
                    "recipe": variant,
                    "format": "square",
                    "locale": "pt-BR",
                    "app_locale": "pt-BR",
                    "copy_language": "pt",
                    "width": 1080,
                    "height": 1080,
                    "research_refs": ["meta-pain"],
                    "swiped_from": "observed pain-to-peace pattern",
                    "brief_ref": "pilot",
                    "concept_id": "morning-relief",
                    "variant_id": variant,
                    "cta": "Baixe grátis",
                    "ad_copy": {
                        "primary_text": "Comece a manhã em paz.",
                        "headline": "Uma caminhada ao amanhecer",
                    },
                }
            )
        automated = qa.audit_outputs(specs)
        report = qa.build_report("sunrise-demo", "batch-1", automated)
        self.report = qa.approve_visual(
            report, "codex", {name: True for name in qa.VISUAL_CHECKS}
        )
        single_report = qa.build_report(
            "sunrise-demo", "batch-1-single", qa.audit_outputs(specs[:1])
        )
        self.single_report = qa.approve_visual(
            single_report, "codex", {name: True for name in qa.VISUAL_CHECKS}
        )
        self.now = datetime(2026, 7, 9, 18, 30, tzinfo=timezone.utc)
        self.capabilities = {
            "provider": "meta_ads_mcp",
            "agent": "codex",
            "checked_at": "2026-07-09T18:00:00Z",
            "tools": ["ads_create_creative", "ads_create_ad", "ads_get_ad"],
            "readback_tool": "ads_get_ad",
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
                    "status": "configured",
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
            **self.raw_evidence("app-store-readiness.json", b'{"ready":true}'),
            "destination": {
                "ref": "default",
                "type": "app_store",
                "url": "https://apps.apple.com/app/id1",
            },
        }
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
                    "confidence_rationale": "owned funnel evidence",
                    "status": "approved",
                    "approved_by": "demo-operator",
                    "creatives": {"copy_language": "pt"},
                }
            ],
        }

    def tearDown(self):
        self.temp.cleanup()

    def raw_evidence(self, name, payload):
        path = self.root / name
        path.write_bytes(payload)
        return {
            "response_path": name,
            "response_digest": hashlib.sha256(payload).hexdigest(),
        }

    def paused_evidence(self, name, item, provider_response=None):
        return self.raw_evidence(
            name,
            canonical_json_bytes(
                publish_readback_envelope(item, provider_response)
            ),
        )

    def prepare(self, **overrides):
        arguments = {
            "account_id": "act_1",
            "campaign_id": "campaign_1",
            "ad_set_id": "adset_1",
            "audience_plan": self.audience_plan,
            "audience_id": "br-cold-broad",
            "markets": [
                {
                    "id": "br",
                    "countries": ["BR"],
                    "locale": "pt-BR",
                    "app_locale": "pt-BR",
                    "copy_language": "pt",
                }
            ],
            "publish_policy": {"primary_format": "square", "max_ads_per_ad_set": 6},
            "app_config": self.app_config,
            "briefs": self.briefs,
            "readiness_receipt": self.readiness_receipt,
            "evidence_root": self.root,
            "now": self.now,
        }
        arguments.update(overrides)
        report = arguments.pop("report", self.report)
        return publish.prepare_manifest(report, self.capabilities, **arguments)

    def test_manifest_preserves_every_concept_variant_pair(self):
        manifest = self.prepare()

        self.assertEqual(len(manifest["items"]), 2)
        self.assertEqual(
            {(item["concept_id"], item["variant_id"]) for item in manifest["items"]},
            {("morning-relief", "hook-a"), ("morning-relief", "hook-b")},
        )
        item = manifest["items"][0]
        self.assertEqual(item["brief_ref"], "pilot")
        self.assertEqual(item["concept_id"], "morning-relief")
        self.assertEqual(item["variant_id"], "hook-a")
        self.assertEqual(item["cta"], "Baixe grátis")
        self.assertEqual(item["ad_copy"]["headline"], "Uma caminhada ao amanhecer")
        self.assertEqual(manifest["destination"]["ref"], "default")
        self.assertIsNone(manifest["destination"]["custom_product_page_id"])
        self.assertEqual(
            manifest["readback_requirement"]["tool"], "ads_get_ad"
        )
        self.assertFalse(
            manifest["readback_requirement"]["local_validation_sufficient"]
        )

    def test_live_destination_readiness_receipt_is_mandatory_before_manifest(self):
        with self.assertRaisesRegex(publish.PublishBlocked, "readiness.*live"):
            self.prepare(readiness_receipt=None)

    def test_all_stage_required_readiness_receipts_are_live_and_bound(self):
        app_config = copy.deepcopy(self.app_config)
        app_config["readiness"]["required_receipts"].update(
            {
                "meta_app_events": "pending_live_check",
                "attribution_mapping": "pending_live_check",
                "meta_video_publish": "blocked_missing_capability",
            }
        )
        with self.assertRaisesRegex(publish.PublishBlocked, "meta_app_events"):
            self.prepare(app_config=app_config)

        bundle = {
            "version": 1,
            "app": "sunrise-demo",
            "receipts": [
                self.readiness_receipt,
                {
                    "receipt_type": "meta_app_events",
                    "provider": "meta_ads_mcp",
                    "tool": "events_get_status",
                    "app": "sunrise-demo",
                    "status": "ready",
                    "verification_basis": "live_provider_readback",
                    "local_validation_sufficient": False,
                    "observed_at": "2026-07-09T18:15:00Z",
                    **self.raw_evidence("meta-events.json", b'{"events":"ready"}'),
                },
                {
                    "receipt_type": "attribution_mapping",
                    "provider": "revenuecat_api",
                    "tool": "integrations_get_meta",
                    "app": "sunrise-demo",
                    "status": "ready",
                    "verification_basis": "live_provider_readback",
                    "local_validation_sufficient": False,
                    "observed_at": "2026-07-09T18:20:00Z",
                    **self.raw_evidence(
                        "attribution.json", b'{"mapping":"ready"}'
                    ),
                },
            ],
        }
        manifest = self.prepare(
            app_config=app_config,
            readiness_receipt=bundle,
        )

        self.assertEqual(
            {item["receipt_type"] for item in manifest["runtime_readiness"]},
            {"meta_app_events", "attribution_mapping"},
        )

    def test_custom_product_page_id_and_url_are_bound_to_live_readiness(self):
        app_config = copy.deepcopy(self.app_config)
        app_config["readiness"]["required_receipts"] = {
            "custom_product_page_destination": "required_live"
        }
        app_config["destinations"]["custom_product_pages"] = [
            {
                "id": "cpp-morning",
                "type": "custom_product_page",
                "url": "https://apps.apple.com/app/id1?ppid=morning",
            }
        ]
        briefs = copy.deepcopy(self.briefs)
        briefs["pilot"]["destination"] = {
            "ref": "cpp-morning",
            "type": "custom_product_page",
            "url": "https://apps.apple.com/app/id1?ppid=morning",
        }
        readiness = copy.deepcopy(self.readiness_receipt)
        readiness["receipt_type"] = "custom_product_page_destination"
        readiness["destination"] = {
            "ref": "cpp-morning",
            "type": "custom_product_page",
            "url": "https://apps.apple.com/app/id1?ppid=morning",
            "custom_product_page_id": "cpp-morning",
        }

        manifest = self.prepare(
            app_config=app_config,
            briefs=briefs,
            readiness_receipt=readiness,
        )

        self.assertEqual(
            manifest["destination"]["custom_product_page_id"], "cpp-morning"
        )
        self.assertEqual(
            manifest["destination"]["url"],
            "https://apps.apple.com/app/id1?ppid=morning",
        )

    def test_copy_and_variant_metadata_cannot_change_after_visual_approval(self):
        tampered = copy.deepcopy(self.report)
        tampered["records"][0]["cta"] = "CTA adulterado"

        with self.assertRaisesRegex(publish.PublishBlocked, "artefatos mudaram"):
            self.prepare(report=tampered)

    def test_capability_must_name_a_declared_readback_tool(self):
        capabilities = dict(self.capabilities)
        capabilities.pop("readback_tool")

        with self.assertRaisesRegex(publish.PublishBlocked, "readback_tool"):
            publish.prepare_manifest(
                self.report,
                capabilities,
                account_id="act_1",
                campaign_id="campaign_1",
                ad_set_id="adset_1",
                audience_plan=self.audience_plan,
                audience_id="br-cold-broad",
                markets=[
                    {
                        "id": "br",
                        "countries": ["BR"],
                        "locale": "pt-BR",
                        "app_locale": "pt-BR",
                        "copy_language": "pt",
                    }
                ],
                publish_policy={"primary_format": "square"},
                app_config=self.app_config,
                briefs=self.briefs,
                readiness_receipt=self.readiness_receipt,
                evidence_root=self.root,
                now=self.now,
            )

    def test_create_tool_cannot_masquerade_as_readback(self):
        capabilities = dict(self.capabilities)
        capabilities["readback_tool"] = "ads_create_ad"

        with self.assertRaisesRegex(publish.PublishBlocked, "readback_tool"):
            publish.prepare_manifest(
                self.report,
                capabilities,
                account_id="act_1",
                campaign_id="campaign_1",
                ad_set_id="adset_1",
                audience_plan=self.audience_plan,
                audience_id="br-cold-broad",
                markets=[
                    {
                        "id": "br",
                        "countries": ["BR"],
                        "locale": "pt-BR",
                        "app_locale": "pt-BR",
                        "copy_language": "pt",
                    }
                ],
                publish_policy={"primary_format": "square"},
                app_config=self.app_config,
                briefs=self.briefs,
                readiness_receipt=self.readiness_receipt,
                evidence_root=self.root,
                now=self.now,
            )

    def test_receipt_requires_exact_live_readback_evidence_for_each_artifact(self):
        manifest = self.prepare(report=self.single_report)
        item = manifest["items"][0]
        receipt_item = {
            "item_key": item["item_key"],
            "provider": "meta_ads_mcp",
            "tool": "ads_get_ad",
            "account_id": "act_1",
            "campaign_id": "campaign_1",
            "ad_set_id": "adset_1",
            "creative_id": "creative-1",
            "ad_id": "ad-1",
            "artifact_sha256": item["sha256"],
            "status": "PAUSED",
            "observed_at": "2026-07-09T18:30:00Z",
        }
        receipt_item.update(
            self.paused_evidence("paused-ad-1.json", receipt_item)
        )
        receipt = {
            "manifest_digest": manifest["manifest_digest"],
            "provider": "meta_ads_mcp",
            "verification_basis": "live_provider_readback",
            "local_validation_sufficient": False,
            "delivery_status": "PAUSED",
            "items": [receipt_item],
        }

        self.assertEqual(
            publish.verify_receipt(
                manifest, receipt, now=self.now, evidence_root=self.root
            ), []
        )
        receipt["items"][0].pop("response_digest")
        errors = publish.verify_receipt(
            manifest, receipt, now=self.now, evidence_root=self.root
        )
        self.assertTrue(any("response_digest" in error for error in errors))
        receipt["items"][0]["response_digest"] = hashlib.sha256(
            (self.root / "paused-ad-1.json").read_bytes()
        ).hexdigest()
        receipt["items"][0]["observed_at"] = "2026-07-09T17:00:00Z"
        errors = publish.verify_receipt(
            manifest, receipt, now=self.now, evidence_root=self.root
        )
        self.assertTrue(any("observed_at" in error for error in errors))

        receipt["items"][0]["observed_at"] = "2026-07-09T18:30:00Z"
        stale_now = datetime(2026, 7, 9, 20, 0, tzinfo=timezone.utc)
        errors = publish.verify_receipt(
            manifest, receipt, now=stale_now, evidence_root=self.root
        )
        self.assertTrue(any("expirado" in error for error in errors))

    def test_paused_receipt_rejects_opaque_noncanonical_or_cross_bound_response(self):
        manifest = self.prepare(report=self.single_report)
        item = manifest["items"][0]
        receipt_item = {
            "item_key": item["item_key"],
            "provider": "meta_ads_mcp",
            "tool": "ads_get_ad",
            "account_id": "act_1",
            "campaign_id": "campaign_1",
            "ad_set_id": "adset_1",
            "creative_id": "creative-1",
            "ad_id": "ad-1",
            "artifact_sha256": item["sha256"],
            "status": "PAUSED",
            "observed_at": "2026-07-09T18:30:00Z",
        }
        receipt = {
            "manifest_digest": manifest["manifest_digest"],
            "provider": "meta_ads_mcp",
            "verification_basis": "live_provider_readback",
            "local_validation_sufficient": False,
            "delivery_status": "PAUSED",
            "items": [receipt_item],
        }
        receipt_item.update(self.raw_evidence("opaque.json", b"provider response"))
        opaque = publish.verify_receipt(
            manifest, receipt, now=self.now, evidence_root=self.root
        )
        self.assertTrue(any("JSON" in error for error in opaque), opaque)

        envelope = publish_readback_envelope(receipt_item)
        pretty = json.dumps(envelope, indent=2).encode()
        receipt_item.update(self.raw_evidence("pretty.json", pretty))
        noncanonical = publish.verify_receipt(
            manifest, receipt, now=self.now, evidence_root=self.root
        )
        self.assertTrue(any("canônic" in error for error in noncanonical), noncanonical)

        envelope["binding"]["ad_id"] = "another-ad"
        receipt_item.update(
            self.raw_evidence("wrong-ad.json", canonical_json_bytes(envelope))
        )
        cross_bound = publish.verify_receipt(
            manifest, receipt, now=self.now, evidence_root=self.root
        )
        self.assertTrue(any("ad_id" in error for error in cross_bound), cross_bound)

        envelope = publish_readback_envelope(
            receipt_item,
            provider_response={
                "id": receipt_item["ad_id"],
                "creative_id": receipt_item["creative_id"],
                "account_id": receipt_item["account_id"],
                "campaign_id": receipt_item["campaign_id"],
                "ad_set_id": receipt_item["ad_set_id"],
                "artifact_sha256": receipt_item["artifact_sha256"],
                "status": "ACTIVE",
            },
        )
        receipt_item.update(
            self.raw_evidence("active-provider.json", canonical_json_bytes(envelope))
        )
        contradictory = publish.verify_receipt(
            manifest, receipt, now=self.now, evidence_root=self.root
        )
        self.assertTrue(
            any("provider_response" in error for error in contradictory),
            contradictory,
        )

    def test_historical_receipt_may_be_old_but_never_from_the_future(self):
        manifest = self.prepare(report=self.single_report)
        item = manifest["items"][0]
        receipt_item = {
            "item_key": item["item_key"],
            "provider": "meta_ads_mcp",
            "tool": "ads_get_ad",
            "account_id": "act_1",
            "campaign_id": "campaign_1",
            "ad_set_id": "adset_1",
            "creative_id": "creative-1",
            "ad_id": "ad-1",
            "artifact_sha256": item["sha256"],
            "status": "PAUSED",
            "observed_at": "2099-01-01T00:00:00Z",
        }
        receipt_item.update(
            self.raw_evidence(
                "future.json",
                canonical_json_bytes(publish_readback_envelope(receipt_item)),
            )
        )
        receipt = {
            "manifest_digest": manifest["manifest_digest"],
            "provider": "meta_ads_mcp",
            "verification_basis": "live_provider_readback",
            "local_validation_sufficient": False,
            "delivery_status": "PAUSED",
            "items": [receipt_item],
        }

        errors = publish.verify_receipt(
            manifest,
            receipt,
            now=self.now,
            evidence_root=self.root,
            enforce_freshness=False,
        )

        self.assertTrue(any("futuro" in error for error in errors), errors)

    def test_readiness_raw_response_is_required_revalidated_and_cannot_escape(self):
        missing = copy.deepcopy(self.readiness_receipt)
        missing.pop("response_path")
        with self.assertRaisesRegex(publish.PublishBlocked, "response_path"):
            self.prepare(readiness_receipt=missing)

        changed = copy.deepcopy(self.readiness_receipt)
        (self.root / changed["response_path"]).write_bytes(b'{"ready":false}')
        with self.assertRaisesRegex(publish.PublishBlocked, "response_digest"):
            self.prepare(readiness_receipt=changed)

        outside = self.root.parent / f"{self.root.name}-outside.json"
        outside.write_bytes(b"outside")
        self.addCleanup(outside.unlink, missing_ok=True)
        escaped = copy.deepcopy(self.readiness_receipt)
        escaped.update(
            response_path=f"../{outside.name}",
            response_digest=hashlib.sha256(b"outside").hexdigest(),
        )
        with self.assertRaisesRegex(publish.PublishBlocked, "escapa evidence_root"):
            self.prepare(readiness_receipt=escaped)

    def test_readiness_raw_response_rejects_symlink(self):
        target = self.root / "readiness-target.json"
        target.write_bytes(b"target")
        link = self.root / "readiness-link.json"
        link.symlink_to(target)
        receipt = copy.deepcopy(self.readiness_receipt)
        receipt.update(
            response_path=link.name,
            response_digest=hashlib.sha256(b"target").hexdigest(),
        )

        with self.assertRaisesRegex(publish.PublishBlocked, "symlink"):
            self.prepare(readiness_receipt=receipt)

    def test_paused_receipt_rejects_duplicate_external_ids_and_changed_raw_response(self):
        manifest = self.prepare(report=self.single_report)
        first_manifest_item = manifest["items"][0]
        second_manifest_item = copy.deepcopy(first_manifest_item)
        second_manifest_item["item_key"] = "second-item"
        second_manifest_item["sha256"] = "f" * 64
        manifest["items"].append(second_manifest_item)
        manifest_payload = {
            key: value for key, value in manifest.items() if key != "manifest_digest"
        }
        manifest["manifest_digest"] = publish.canonical_digest(manifest_payload)

        first_evidence = self.raw_evidence("paused-first.json", b"first")
        second_evidence = self.raw_evidence("paused-second.json", b"second")
        base = {
            "provider": "meta_ads_mcp",
            "tool": "ads_get_ad",
            "account_id": "act_1",
            "campaign_id": "campaign_1",
            "ad_set_id": "adset_1",
            "creative_id": "creative-shared",
            "ad_id": "ad-shared",
            "status": "PAUSED",
            "observed_at": "2026-07-09T18:30:00Z",
        }
        receipt = {
            "manifest_digest": manifest["manifest_digest"],
            "provider": "meta_ads_mcp",
            "verification_basis": "live_provider_readback",
            "local_validation_sufficient": False,
            "delivery_status": "PAUSED",
            "items": [
                {
                    **base,
                    "item_key": first_manifest_item["item_key"],
                    "artifact_sha256": first_manifest_item["sha256"],
                    **first_evidence,
                },
                {
                    **base,
                    "item_key": second_manifest_item["item_key"],
                    "artifact_sha256": second_manifest_item["sha256"],
                    **second_evidence,
                },
            ],
        }

        errors = publish.verify_receipt(
            manifest, receipt, now=self.now, evidence_root=self.root
        )
        self.assertTrue(any("creative_id duplicado" in error for error in errors))
        self.assertTrue(any("ad_id duplicado" in error for error in errors))

        receipt["items"][1]["creative_id"] = "creative-2"
        receipt["items"][1]["ad_id"] = "ad-2"
        (self.root / second_evidence["response_path"]).write_bytes(b"tampered")
        errors = publish.verify_receipt(
            manifest, receipt, now=self.now, evidence_root=self.root
        )
        self.assertTrue(any("response_digest" in error for error in errors))

        outside = self.root.parent / f"{self.root.name}-paused-outside.json"
        outside.write_bytes(b"outside-paused")
        self.addCleanup(outside.unlink, missing_ok=True)
        receipt["items"][1].update(
            response_path=f"../{outside.name}",
            response_digest=hashlib.sha256(b"outside-paused").hexdigest(),
        )
        errors = publish.verify_receipt(
            manifest, receipt, now=self.now, evidence_root=self.root
        )
        self.assertTrue(any("escapa evidence_root" in error for error in errors))

    def test_paused_verification_revalidates_manifest_readiness_raw_response(self):
        manifest = self.prepare(report=self.single_report)
        item = manifest["items"][0]
        paused_evidence = self.raw_evidence("paused-readback.json", b"paused")
        receipt = {
            "manifest_digest": manifest["manifest_digest"],
            "provider": "meta_ads_mcp",
            "verification_basis": "live_provider_readback",
            "local_validation_sufficient": False,
            "delivery_status": "PAUSED",
            "items": [
                {
                    "item_key": item["item_key"],
                    "provider": "meta_ads_mcp",
                    "tool": "ads_get_ad",
                    "account_id": "act_1",
                    "campaign_id": "campaign_1",
                    "ad_set_id": "adset_1",
                    "creative_id": "creative-1",
                    "ad_id": "ad-1",
                    "artifact_sha256": item["sha256"],
                    "status": "PAUSED",
                    "observed_at": "2026-07-09T18:30:00Z",
                    **paused_evidence,
                }
            ],
        }
        (self.root / self.readiness_receipt["response_path"]).write_bytes(
            b'{"ready":false}'
        )

        errors = publish.verify_receipt(
            manifest, receipt, now=self.now, evidence_root=self.root
        )

        self.assertTrue(
            any("manifest readiness" in error and "response_digest" in error for error in errors),
            errors,
        )


class ExperimentManifestBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.evidence_root = Path(self.temp.name)
        self.response_relative = Path("readbacks/paused-item.json")
        self.response_path = self.evidence_root / self.response_relative
        self.response_path.parent.mkdir(parents=True)
        self.readiness_relative = Path("readbacks/readiness.json")
        self.readiness_path = self.evidence_root / self.readiness_relative
        self.readiness_path.write_bytes(b'{"ready":true}')

    def tearDown(self):
        self.temp.cleanup()

    def experiment(self):
        return {
            "version": 1,
            "id": "exp-1",
            "app": "sunrise-demo",
            "manifest_digest": "b" * 64,
            "item_key": "item-1",
            "brief_ref": "pilot",
            "concept_id": "morning-relief",
            "variant_id": "hook-a",
            "account_id": "act-1",
            "campaign_id": "campaign-1",
            "ad_set_id": "adset-1",
            "creative_id": "creative-1",
            "ad_id": "ad-1",
            "market": "br",
            "currency": "BRL",
            "attribution_window": {"click_days": 7, "view_days": 1},
            "sample_status": "sufficient",
            "metrics": {
                "impressions": 100,
                "clicks": 5,
                "installs": 2,
                "spend_minor": 1000,
            },
            "agent_decision": {
                "classification": "yellow",
                "rationale": "Early directional signal.",
                "likely_cause": "Hook may be too abstract.",
                "next_action": "Draft a clearer hook.",
                "requires_human_confirmation": False,
                "decided_by": "codex",
            },
        }

    def manifest(self):
        manifest = {
            "provider": "meta_ads_mcp",
            "app": "sunrise-demo",
            "account_id": "act-1",
            "campaign_id": "campaign-1",
            "ad_set_id": "adset-1",
            "created_at": "2026-07-10T11:50:00Z",
            "readback_requirement": {"tool": "ads_get_ad"},
            "destination_readiness": {
                "receipt_type": "app_store_destination",
                "response_path": self.readiness_relative.as_posix(),
                "response_digest": hashlib.sha256(
                    self.readiness_path.read_bytes()
                ).hexdigest(),
            },
            "runtime_readiness": [],
            "items": [
                {
                    "item_key": "item-1",
                    "brief_ref": "pilot",
                    "concept_id": "morning-relief",
                    "variant_id": "hook-a",
                    "sha256": "f" * 64,
                }
            ],
        }
        manifest["manifest_digest"] = experiments.canonical_digest(manifest)
        return manifest

    def metric_source(self):
        return {
            "schema": "creative-forge/meta-insights-readback@1",
            "platform": "meta",
            "provider": "meta_ads_mcp",
            "tool": "ads_get_insights",
            "observed_at": "2026-07-10T12:10:00Z",
            "binding": {
                "app": "sunrise-demo",
                "item_key": "item-1",
                "brief_ref": "pilot",
                "concept_id": "morning-relief",
                "variant_id": "hook-a",
                "account_id": "act-1",
                "campaign_id": "campaign-1",
                "ad_set_id": "adset-1",
                "creative_id": "creative-1",
                "ad_id": "ad-1",
                "artifact_sha256": "f" * 64,
                "date_window": {"start": "2026-07-01", "end": "2026-07-07"},
                "currency": "BRL",
                "attribution_window": {"click_days": 7, "view_days": 1},
            },
            "metrics": {
                "impressions": 100,
                "clicks": 5,
                "installs": 2,
                "spend_minor": 1000,
            },
        }

    def receipt(self, manifest):
        item = {
            "item_key": "item-1",
            "provider": "meta_ads_mcp",
            "tool": "ads_get_ad",
            "account_id": "act-1",
            "campaign_id": "campaign-1",
            "ad_set_id": "adset-1",
            "creative_id": "creative-1",
            "ad_id": "ad-1",
            "artifact_sha256": "f" * 64,
            "status": "PAUSED",
            "observed_at": "2026-07-10T12:00:00Z",
        }
        self.response_path.write_bytes(
            canonical_json_bytes(publish_readback_envelope(item))
        )
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

    def bind_evidence(self, experiment, manifest):
        receipt = self.receipt(manifest)
        source = self.metric_source()
        experiment["manifest_digest"] = manifest["manifest_digest"]
        experiment["publish_receipt_digest"] = experiments.canonical_digest(receipt)
        experiment["metrics_provenance"] = {
            "platform": "meta",
            "provider": "meta_ads_mcp",
            "tool": "ads_get_insights",
            "response_digest": experiments.canonical_digest(source),
            "observed_at": "2026-07-10T12:10:00Z",
            "date_window": {"start": "2026-07-01", "end": "2026-07-07"},
            "currency": "BRL",
            "attribution_window": {"click_days": 7, "view_days": 1},
            **{
                field: experiment[field]
                for field in (
                    "app",
                    "item_key",
                    "brief_ref",
                    "concept_id",
                    "variant_id",
                    "account_id",
                    "campaign_id",
                    "ad_set_id",
                    "creative_id",
                    "ad_id",
                )
            },
        }
        return receipt, source

    def audit(self, experiment, manifest, *, briefs_root=None):
        receipt, source = self.bind_evidence(experiment, manifest)
        return experiments.audit_experiment(
            experiment,
            manifest=manifest,
            publish_receipt=receipt,
            metrics_source=source,
            briefs_root=briefs_root,
            evidence_root=self.evidence_root,
        )

    def test_experiment_must_match_the_bound_manifest_item(self):
        experiment = self.experiment()
        manifest = self.manifest()
        experiment["variant_id"] = "unrelated"

        result = self.audit(experiment, manifest)

        self.assertTrue(any("variant_id" in error for error in result["errors"]))

    def test_final_decision_requires_a_resolvable_next_brief(self):
        experiment = self.experiment()
        manifest = self.manifest()
        experiment["sample_status"] = "final"
        experiment["agent_decision"]["next_brief_ref"] = "next-round"
        with tempfile.TemporaryDirectory() as directory:
            briefs_root = Path(directory)
            missing = self.audit(experiment, manifest, briefs_root=briefs_root)
            brief_path = briefs_root / "sunrise-demo" / "next-round.yaml"
            brief_path.parent.mkdir(parents=True)
            brief_path.write_text("id: next-round\napp: sunrise-demo\n")
            resolved = self.audit(experiment, manifest, briefs_root=briefs_root)

        self.assertTrue(any("next_brief_ref" in error for error in missing["errors"]))
        self.assertEqual(resolved["errors"], [])

    def test_next_brief_ref_cannot_escape_the_app_brief_directory(self):
        experiment = self.experiment()
        manifest = self.manifest()
        experiment["sample_status"] = "final"
        experiment["agent_decision"]["next_brief_ref"] = "../outside"
        with tempfile.TemporaryDirectory() as directory:
            briefs_root = Path(directory)
            (briefs_root / "sunrise-demo").mkdir()
            outside = briefs_root / "outside.yaml"
            outside.write_text("id: ../outside\napp: sunrise-demo\n")

            result = self.audit(experiment, manifest, briefs_root=briefs_root)

        self.assertTrue(any("next_brief_ref" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
