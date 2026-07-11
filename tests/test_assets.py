import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts import assets


class AssetRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.image = self.root / "owned.png"
        self.image.write_bytes(b"owned asset")
        self.rights_evidence = self.root / "provider-terms.pdf"
        self.rights_evidence.write_bytes(b"provider terms snapshot")
        self.consent_evidence = self.root / "talent-release.pdf"
        self.consent_evidence.write_bytes(b"signed talent release")

    def tearDown(self):
        self.temp.cleanup()

    def valid_registry(self):
        return {
            "version": 1,
            "app": "sunrise-demo",
            "assets": [
                {
                    "id": "owned-hero",
                    "kind": "owned",
                    "path": str(self.image),
                    "sha256": hashlib.sha256(self.image.read_bytes()).hexdigest(),
                    "source": {"kind": "app_repository"},
                    "rights": {
                        "status": "cleared",
                        "commercial_use": True,
                        "derivative_use": True,
                        "basis": "owned_by_app",
                        "scope": {
                            "paid_ads": True,
                            "platforms": ["meta"],
                        },
                    },
                }
            ],
        }

    def test_owned_asset_with_matching_hash_and_rights_passes(self):
        result = assets.audit_registry(
            self.valid_registry(), expected_app="sunrise-demo"
        )

        self.assertEqual(result["errors"], [])

    def test_hash_and_commercial_rights_are_required(self):
        registry = self.valid_registry()
        registry["assets"][0].pop("sha256")
        registry["assets"][0]["rights"]["commercial_use"] = False

        result = assets.audit_registry(registry)

        self.assertTrue(any("sha256" in error for error in result["errors"]))
        self.assertTrue(any("commercial_use" in error for error in result["errors"]))

    def test_reference_only_competitor_media_cannot_have_a_render_path(self):
        registry = self.valid_registry()
        registry["assets"][0].update(
            {"kind": "reference_only", "source": {"kind": "competitor"}}
        )
        registry["assets"][0]["rights"].update(
            {"status": "reference_only", "commercial_use": False}
        )

        result = assets.audit_registry(registry)

        self.assertTrue(any("reference_only" in error and "path" in error for error in result["errors"]))

    def test_generated_asset_requires_generation_receipt(self):
        registry = self.valid_registry()
        registry["assets"][0]["kind"] = "generated"

        result = assets.audit_registry(registry)

        self.assertTrue(any("generation" in error for error in result["errors"]))

    def non_owned_registry(self, kind):
        registry = self.valid_registry()
        asset = registry["assets"][0]
        asset["kind"] = kind
        asset["rights"].update(
            {
                "basis": "provider_terms_snapshot",
                "scope": {
                    "paid_ads": True,
                    "platforms": ["meta"],
                },
                "evidence": {
                    "kind": "terms_snapshot",
                    "path": str(self.rights_evidence),
                    "sha256": hashlib.sha256(
                        self.rights_evidence.read_bytes()
                    ).hexdigest(),
                },
            }
        )
        if kind == "generated":
            asset["generation"] = {
                "provider": "example-provider",
                "model": "video-v1",
                "job_id": "job-123",
                "prompt_sha256": "a" * 64,
            }
        return registry

    def test_non_owned_assets_require_verifiable_rights_evidence(self):
        for kind in ("commissioned", "licensed", "generated"):
            with self.subTest(kind=kind):
                registry = self.non_owned_registry(kind)
                registry["assets"][0]["rights"].pop("evidence")

                result = assets.audit_registry(registry, root=self.root)

                self.assertTrue(
                    any("rights.evidence" in error for error in result["errors"]),
                    result["errors"],
                )

    def test_non_owned_rights_evidence_file_and_hash_are_verified(self):
        registry = self.non_owned_registry("licensed")
        registry["assets"][0]["rights"]["evidence"]["sha256"] = "0" * 64

        result = assets.audit_registry(registry, root=self.root)

        self.assertTrue(
            any("rights.evidence sha256" in error for error in result["errors"]),
            result["errors"],
        )

    def test_non_owned_evidence_must_be_a_rights_record_not_the_asset_itself(self):
        registry = self.non_owned_registry("licensed")
        evidence = registry["assets"][0]["rights"]["evidence"]
        evidence.update(
            {
                "kind": "video_file",
                "path": str(self.image),
                "sha256": hashlib.sha256(self.image.read_bytes()).hexdigest(),
            }
        )

        result = assets.audit_registry(registry, root=self.root)

        self.assertTrue(
            any("rights.evidence.kind inválido" in error for error in result["errors"]),
            result["errors"],
        )
        self.assertTrue(
            any("evidence não pode ser o próprio asset" in error for error in result["errors"]),
            result["errors"],
        )

    def test_non_owned_assets_require_explicit_paid_ads_scope_and_platforms(self):
        registry = self.non_owned_registry("commissioned")
        registry["assets"][0]["rights"]["scope"] = {
            "paid_ads": False,
            "platforms": [],
        }

        result = assets.audit_registry(registry, root=self.root)

        self.assertTrue(
            any("paid_ads true" in error for error in result["errors"]),
            result["errors"],
        )
        self.assertTrue(
            any("scope.platforms" in error for error in result["errors"]),
            result["errors"],
        )

    def test_owned_assets_also_require_explicit_paid_ads_scope(self):
        registry = self.valid_registry()
        registry["assets"][0]["rights"].pop("scope")

        result = assets.audit_registry(registry, root=self.root)

        self.assertTrue(
            any("scope.paid_ads" in error for error in result["errors"]),
            result["errors"],
        )

    def test_non_owned_asset_with_verified_evidence_and_paid_ads_scope_passes(self):
        for kind in ("commissioned", "licensed", "generated"):
            with self.subTest(kind=kind):
                result = assets.audit_registry(
                    self.non_owned_registry(kind), root=self.root
                )

                self.assertEqual(result["errors"], [])

    def test_identifiable_people_require_hashed_consent_release_evidence(self):
        registry = self.valid_registry()
        asset = registry["assets"][0]
        asset["depicts_identifiable_people"] = True
        asset["consent_release"] = {"status": "cleared"}

        missing = assets.audit_registry(registry, root=self.root)
        asset["consent_release"]["evidence"] = {
            "kind": "signed_release",
            "path": str(self.consent_evidence),
            "sha256": hashlib.sha256(self.consent_evidence.read_bytes()).hexdigest(),
        }
        valid = assets.audit_registry(registry, root=self.root)

        self.assertTrue(
            any("consent_release.evidence" in error for error in missing["errors"]),
            missing["errors"],
        )
        self.assertEqual(valid["errors"], [])

    def test_recipe_cannot_use_reference_only_or_unknown_asset(self):
        registry = self.valid_registry()
        registry["assets"].append(
            {
                "id": "competitor-frame",
                "kind": "reference_only",
                "source": {"kind": "competitor"},
                "rights": {
                    "status": "reference_only",
                    "commercial_use": False,
                    "derivative_use": False,
                    "basis": "observation_only",
                },
            }
        )

        errors = assets.recipe_asset_errors(
            {
                "asset_refs": ["competitor-frame", "missing"],
                "target_platforms": ["meta"],
            },
            registry,
            "recipe-a",
        )

        self.assertTrue(any("reference_only" in error for error in errors))
        self.assertTrue(any("missing" in error for error in errors))

    def test_recipe_requires_target_platform_rights_and_derivative_use(self):
        registry = self.valid_registry()
        registry["assets"][0]["rights"]["scope"]["platforms"] = ["tiktok"]
        registry["assets"][0]["rights"]["derivative_use"] = False

        errors = assets.recipe_asset_errors(
            {
                "asset_refs": ["owned-hero"],
                "target_platforms": ["meta"],
            },
            registry,
            "recipe-a",
        )

        self.assertTrue(any("meta" in error for error in errors), errors)
        self.assertTrue(any("derivative_use" in error for error in errors), errors)

    def test_recipe_without_target_platforms_is_blocked(self):
        errors = assets.recipe_asset_errors(
            {"asset_refs": ["owned-hero"]},
            self.valid_registry(),
            "recipe-a",
        )

        self.assertTrue(any("target_platforms" in error for error in errors), errors)

    def test_malformed_registry_shapes_return_auditable_errors(self):
        malformed_registries = (
            [],
            {"version": 1, "app": "sunrise-demo", "assets": {}},
            {"version": 1, "app": "sunrise-demo", "assets": ["not-an-object"]},
            {
                "version": 1,
                "app": "sunrise-demo",
                "assets": [
                    {
                        "id": "broken",
                        "kind": "generated",
                        "source": "not-an-object",
                        "rights": "not-an-object",
                        "generation": "not-an-object",
                    }
                ],
            },
        )

        for registry in malformed_registries:
            with self.subTest(registry=registry):
                result = assets.audit_registry(registry, root=self.root)
                self.assertTrue(result["errors"])
                self.assertTrue(all(isinstance(error, str) for error in result["errors"]))

    def test_recipe_asset_gate_handles_malformed_registry_without_exception(self):
        for registry in ([], {"assets": {}}, {"assets": ["bad-entry"]}):
            with self.subTest(registry=registry):
                errors = assets.recipe_asset_errors(
                    {"asset_refs": ["owned-hero"]}, registry, "recipe-a"
                )
                self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
