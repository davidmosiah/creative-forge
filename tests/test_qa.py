import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from PIL import Image
import yaml

from scripts import qa


def full_resolution_reviews(report):
    return [
        {
            "artifact_key": record["artifact_key"],
            "notes": "Test fixture opened at its original pixel dimensions.",
        }
        for record in report.get("records", [])
    ]


def approve_visual(report, reviewer, checks, *, artifact_reviews=None):
    return qa.approve_visual(
        report,
        reviewer,
        checks,
        artifact_reviews=(
            artifact_reviews
            if artifact_reviews is not None
            else full_resolution_reviews(report)
        ),
    )


class QualityGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def png(self, name, size=(1080, 1080), color=(240, 220, 180)):
        path = self.root / name
        Image.new("RGB", size, color).save(path)
        return path

    def spec(self, path, locale="pt-BR", copy_language="pt", size=(1080, 1080)):
        return {
            "path": str(path),
            "locale": locale,
            "copy_language": copy_language,
            "width": size[0],
            "height": size[1],
        }

    def provenance_spec(self, path):
        spec = self.spec(path)
        spec.update(
            {
                "recipe": "morning",
                "format": "square",
                "market_id": "br",
                "app_locale": "pt-BR",
                "media_kind": "image",
                "research_refs": ["meta-pain"],
                "swiped_from": "StrideCo ES — dor→alívio",
                "lineage": {"meta-pain": "competitor_pattern"},
                "claims_used": ["daily_ritual"],
                "template": "pain-headline-cta",
                "concept_lineage": "competitor_pattern",
                "concept_lineage_ref": "meta-pain",
                "execution_lineage": "competitor_pattern",
                "execution_ref": "meta-pain",
                "brief_ref": "pilot",
                "concept_id": "morning-relief",
                "variant_id": "morning-v1",
                "input_files": [],
            }
        )
        recipe_path = self.root / "recipe.yaml"
        recipe_path.write_text(
            yaml.safe_dump(
                {
                    "brief_ref": spec["brief_ref"],
                    "concept_id": spec["concept_id"],
                    "research_refs": spec["research_refs"],
                    "execution_ref": spec["execution_ref"],
                    "swiped_from": spec["swiped_from"],
                },
                sort_keys=False,
            )
        )
        research_path = self.root / "research.yaml"
        research_path.write_text(
            yaml.safe_dump(
                {
                    "creatives": [
                        {
                            "id": "meta-pain",
                            "lineage": "competitor_pattern",
                            "evidence_level": "observed",
                        }
                    ]
                },
                sort_keys=False,
            )
        )
        brief_path = self.root / "brief.yaml"
        brief_path.write_text(
            yaml.safe_dump(
                {
                    "id": spec["brief_ref"],
                    "concepts": [
                        {
                            "id": spec["concept_id"],
                            "lineage": spec["concept_lineage"],
                            "lineage_ref": spec["concept_lineage_ref"],
                            "research_refs": spec["research_refs"],
                        }
                    ],
                },
                sort_keys=False,
            )
        )
        template_path = self.root / "template.yaml"
        template_path.write_text("role: template\n")
        app_path = self.root / "app_config.yaml"
        app_path.write_text("role: app_config\n")
        spec["input_files"] = [
            {"role": "recipe", "path": str(recipe_path)},
            {"role": "research", "path": str(research_path)},
            {"role": "brief", "path": str(brief_path)},
            {"role": "template", "path": str(template_path)},
            {"role": "app_config", "path": str(app_path)},
        ]
        return spec

    def test_missing_output_is_a_blocking_error(self):
        specs = [self.spec(self.root / "missing.png")]

        result = qa.audit_outputs(specs)

        self.assertTrue(any("ausente" in error for error in result["errors"]))

    def test_unexpected_png_in_output_directory_is_blocked(self):
        expected = self.png("expected.png")
        self.png("stale-placeholder.png")

        result = qa.audit_outputs([self.spec(expected)], output_dir=self.root)

        self.assertTrue(any("output inesperado" in error for error in result["errors"]))

    def test_wrong_dimensions_are_a_blocking_error(self):
        path = self.png("wrong.png", size=(100, 100))

        result = qa.audit_outputs([self.spec(path)])

        self.assertTrue(any("dimensão" in error for error in result["errors"]))

    def test_duplicate_pixels_across_different_copy_languages_are_blocked(self):
        first = self.png("pt.png")
        second = self.root / "es.png"
        second.write_bytes(first.read_bytes())
        specs = [
            self.spec(first, locale="pt-BR", copy_language="pt"),
            self.spec(second, locale="es-MX", copy_language="es"),
        ]

        result = qa.audit_outputs(specs)

        self.assertTrue(any("duplicata inesperada" in error for error in result["errors"]))

    def test_duplicate_pixels_are_allowed_for_explicit_english_fallback_markets(self):
        first = self.png("it.png")
        second = self.root / "pl.png"
        second.write_bytes(first.read_bytes())
        specs = [
            self.spec(first, locale="it", copy_language="en"),
            self.spec(second, locale="pl", copy_language="en"),
        ]

        result = qa.audit_outputs(specs)

        self.assertEqual(result["errors"], [])

    def test_visual_approval_requires_every_check_and_is_bound_to_matrix_digest(self):
        path = self.png("one.png")
        automated = qa.audit_outputs([self.spec(path)])
        report = qa.build_report("sunrise-demo", "batch-1", automated)

        with self.assertRaises(ValueError):
            approve_visual(report, "codex", {"copy_correct": True})

        checks = {name: True for name in qa.VISUAL_CHECKS}
        approved = approve_visual(report, "codex", checks)

        self.assertEqual(approved["visual_status"], "approved")
        self.assertEqual(approved["approved_matrix_digest"], report["matrix_digest"])

    def test_visual_approval_seals_full_resolution_notes_per_artifact(self):
        path = self.png("full-resolution.png", size=(1080, 1920))
        report = qa.build_report(
            "sunrise-demo",
            "batch-full-resolution",
            qa.audit_outputs([self.spec(path, size=(1080, 1920))]),
        )
        artifact_key = report["records"][0]["artifact_key"]
        approved = approve_visual(
            report,
            "codex",
            {name: True for name in qa.VISUAL_CHECKS},
            artifact_reviews=[
                {
                    "artifact_key": artifact_key,
                    "notes": "Opened the original 1080x1920 PNG and inspected copy, edges and safe zones.",
                }
            ],
        )

        self.assertTrue(approved["visual_approval_digest"])
        self.assertEqual(
            approved["artifact_reviews"][0]["artifact_key"], artifact_key
        )
        tampered = deepcopy(approved)
        tampered["visual_reviewer"] = ""
        self.assertTrue(
            any("visual approval" in error for error in qa.verify_report_files(tampered))
        )

    def test_visual_approval_rejects_a_forged_artifact_key(self):
        path = self.png("forged-key.png")
        report = qa.build_report(
            "sunrise-demo",
            "batch-forged-key",
            qa.audit_outputs([self.spec(path)]),
        )
        approved = approve_visual(
            report,
            "codex",
            {name: True for name in qa.VISUAL_CHECKS},
        )
        approved["records"][0]["artifact_key"] = "forged-artifact-key"
        approved["artifact_reviews"][0]["artifact_key"] = "forged-artifact-key"
        approved["visual_approval_digest"] = qa.visual_approval_digest(approved)

        errors = qa.verify_report_files(approved)

        self.assertTrue(any("artifact_key" in error for error in errors), errors)

    def test_changed_file_invalidates_an_existing_approval(self):
        path = self.png("one.png")
        automated = qa.audit_outputs([self.spec(path)])
        report = qa.build_report("sunrise-demo", "batch-1", automated)
        report = approve_visual(
            report,
            "codex",
            {name: True for name in qa.VISUAL_CHECKS},
        )
        Image.new("RGB", (1080, 1080), (0, 0, 0)).save(path)

        errors = qa.verify_report_files(report)

        self.assertTrue(any("checksum mudou" in error for error in errors))

    def test_changing_report_app_invalidates_visual_approval(self):
        path = self.png("app-identity.png")
        report = approve_visual(
            qa.build_report(
                "sunrise-demo",
                "batch-app-identity",
                qa.audit_outputs([self.spec(path)]),
            ),
            "codex",
            {name: True for name in qa.VISUAL_CHECKS},
        )
        report["app"] = "demo-app-c"

        errors = qa.verify_report_files(report)

        self.assertTrue(any("report identity" in error for error in errors))

    def test_visual_approval_seals_canonical_inputs_and_verifiable_paths(self):
        path = self.png("sealed.png")
        automated = qa.audit_outputs([self.provenance_spec(path)], require_provenance=True)
        report = qa.build_report("sunrise-demo", "batch-sealed", automated)
        approved = approve_visual(
            report,
            "codex",
            {name: True for name in qa.VISUAL_CHECKS},
        )

        self.assertTrue(approved["input_digest"])
        self.assertEqual(approved["approved_input_digest"], approved["input_digest"])
        self.assertEqual(approved["version"], 2)
        self.assertTrue(approved["provenance_required"])
        self.assertEqual(
            {item["role"] for item in approved["records"][0]["input_files"]},
            {"recipe", "research", "brief", "template", "app_config"},
        )
        self.assertTrue(all(item["sha256"] for item in approved["records"][0]["input_files"]))

    def test_changing_any_provenance_file_after_approval_invalidates_it(self):
        path = self.png("file-tamper.png")
        automated = qa.audit_outputs([self.provenance_spec(path)], require_provenance=True)
        report = approve_visual(
            qa.build_report("sunrise-demo", "batch-file-tamper", automated),
            "codex",
            {name: True for name in qa.VISUAL_CHECKS},
        )

        for input_file in report["records"][0]["input_files"]:
            with self.subTest(role=input_file["role"]):
                input_path = Path(input_file["path"])
                original = input_path.read_text()
                input_path.write_text(original + "tampered: true\n")

                errors = qa.verify_report_files(report)

                self.assertTrue(any(input_file["role"] in error for error in errors))
                input_path.write_text(original)

    def test_changing_embedded_provenance_after_approval_invalidates_it(self):
        path = self.png("metadata-tamper.png")
        automated = qa.audit_outputs([self.provenance_spec(path)], require_provenance=True)
        approved = approve_visual(
            qa.build_report("sunrise-demo", "batch-metadata-tamper", automated),
            "codex",
            {name: True for name in qa.VISUAL_CHECKS},
        )
        mutations = {
            "recipe": "changed-recipe",
            "research_refs": ["meta-other"],
            "swiped_from": "changed source",
            "lineage": {"meta-pain": "exploratory"},
            "claims_used": ["different_claim"],
            "template": "different-template",
        }

        for field, value in mutations.items():
            with self.subTest(field=field):
                tampered = deepcopy(approved)
                tampered["records"][0][field] = value

                errors = qa.verify_report_files(tampered)

                self.assertTrue(any("input digest" in error for error in errors))

    def test_removing_input_digest_cannot_downgrade_an_approved_report(self):
        path = self.png("digest-removal.png")
        approved = approve_visual(
            qa.build_report(
                "sunrise-demo",
                "batch-digest-removal",
                qa.audit_outputs(
                    [self.provenance_spec(path)], require_provenance=True
                ),
            ),
            "codex",
            {name: True for name in qa.VISUAL_CHECKS},
        )
        approved.pop("input_digest")
        recipe_input = next(
            item
            for item in approved["records"][0]["input_files"]
            if item["role"] == "recipe"
        )
        Path(recipe_input["path"]).write_text("tampered: true\n")

        errors = qa.verify_report_files(approved)

        self.assertTrue(any("input digest ausente" in error for error in errors))
        self.assertTrue(any("recipe" in error for error in errors))

    def test_stripping_both_digests_and_part_of_provenance_still_blocks(self):
        path = self.png("stripped-provenance.png")
        approved = approve_visual(
            qa.build_report(
                "sunrise-demo",
                "batch-stripped-provenance",
                qa.audit_outputs(
                    [self.provenance_spec(path)], require_provenance=True
                ),
            ),
            "codex",
            {name: True for name in qa.VISUAL_CHECKS},
        )
        approved.pop("input_digest")
        approved.pop("approved_input_digest")
        approved["version"] = 1
        approved["provenance_required"] = False
        approved["records"][0].pop("input_files")
        approved["records"][0].pop("lineage")
        approved["records"][0]["locale"] = "es-MX"

        errors = qa.verify_report_files(approved)

        self.assertTrue(any("input digest" in error for error in errors))
        with self.assertRaisesRegex(ValueError, "input digest"):
            approve_visual(
                approved,
                "codex",
                {name: True for name in qa.VISUAL_CHECKS},
            )

    def test_visual_approval_refuses_required_provenance_without_digest(self):
        path = self.png("approval-without-digest.png")
        report = qa.build_report(
            "sunrise-demo",
            "batch-approval-without-digest",
            qa.audit_outputs([self.provenance_spec(path)], require_provenance=True),
        )
        report.pop("input_digest")

        with self.assertRaisesRegex(ValueError, "input digest"):
            approve_visual(
                report,
                "codex",
                {name: True for name in qa.VISUAL_CHECKS},
            )

    def test_publish_metadata_changes_invalidate_visual_approval(self):
        path = self.png("publish-metadata.png")
        approved = approve_visual(
            qa.build_report(
                "sunrise-demo",
                "batch-publish-metadata",
                qa.audit_outputs(
                    [self.provenance_spec(path)], require_provenance=True
                ),
            ),
            "codex",
            {name: True for name in qa.VISUAL_CHECKS},
        )
        tampered = deepcopy(approved)
        tampered["records"][0].update(
            {
                "locale": "es-MX",
                "app_locale": "es-MX",
                "copy_language": "es",
                "format": "portrait",
                "width": 1080,
                "height": 1350,
                "media_kind": "video",
            }
        )

        errors = qa.verify_report_files(tampered)

        self.assertTrue(any("input digest" in error for error in errors))

    def test_retargeting_a_sealed_input_to_a_symlink_is_rejected(self):
        path = self.png("symlink-retarget.png")
        approved = approve_visual(
            qa.build_report(
                "sunrise-demo",
                "batch-symlink-retarget",
                qa.audit_outputs(
                    [self.provenance_spec(path)], require_provenance=True
                ),
            ),
            "codex",
            {name: True for name in qa.VISUAL_CHECKS},
        )
        recipe_input = next(
            item
            for item in approved["records"][0]["input_files"]
            if item["role"] == "recipe"
        )
        recipe_path = Path(recipe_input["path"])
        replacement = self.root / "replacement-recipe.yaml"
        replacement.write_bytes(recipe_path.read_bytes())
        recipe_path.unlink()
        recipe_path.symlink_to(replacement)

        errors = qa.verify_report_files(approved)

        self.assertTrue(
            any("recipe" in error and "symlink" in error for error in errors)
        )

    def test_retargeting_a_symlinked_input_directory_is_rejected(self):
        path = self.png("symlink-directory-retarget.png")
        first_dir = self.root / "first-inputs"
        second_dir = self.root / "second-inputs"
        first_dir.mkdir()
        second_dir.mkdir()
        spec = self.provenance_spec(path)
        recipe_input = next(
            item for item in spec["input_files"] if item["role"] == "recipe"
        )
        recipe_bytes = Path(recipe_input["path"]).read_bytes()
        for directory in (first_dir, second_dir):
            (directory / "recipe.yaml").write_bytes(recipe_bytes)
        linked_dir = self.root / "linked-inputs"
        linked_dir.symlink_to(first_dir, target_is_directory=True)
        recipe_input["path"] = str(linked_dir / "recipe.yaml")
        approved = approve_visual(
            qa.build_report(
                "sunrise-demo",
                "batch-symlink-directory-retarget",
                qa.audit_outputs([spec], require_provenance=True),
            ),
            "codex",
            {name: True for name in qa.VISUAL_CHECKS},
        )
        linked_dir.unlink()
        linked_dir.symlink_to(second_dir, target_is_directory=True)

        errors = qa.verify_report_files(approved)

        self.assertTrue(
            any("recipe" in error and "destino" in error for error in errors)
        )

    def test_real_report_requires_complete_provenance_roles(self):
        path = self.png("incomplete.png")
        spec = self.provenance_spec(path)
        spec["input_files"] = spec["input_files"][:-1]

        automated = qa.audit_outputs([spec], require_provenance=True)

        self.assertTrue(any("app_config" in error for error in automated["errors"]))

    def test_real_report_requires_complete_embedded_provenance(self):
        path = self.png("missing-metadata.png")

        for field in (
            "recipe",
            "research_refs",
            "swiped_from",
            "lineage",
            "claims_used",
            "template",
        ):
            with self.subTest(field=field):
                spec = self.provenance_spec(path)
                spec.pop(field)

                automated = qa.audit_outputs([spec], require_provenance=True)

                self.assertTrue(any(field in error for error in automated["errors"]))

    def test_expected_specs_seal_real_recipe_and_claim_evidence_sources(self):
        specs = qa.expected_specs("sunrise-demo")
        spec = next(item for item in specs if item["recipe"] == "morning-walk")

        self.assertEqual(spec["template"], "pain-headline-cta")
        self.assertIn(spec["market_id"], {"br", "mexico", "spain", "italy", "poland", "us"})
        self.assertEqual(set(spec["lineage"]), set(spec["research_refs"]))
        self.assertEqual(set(spec["claim_evidence"]), set(spec["claims_used"]))
        roles = {item["role"] for item in spec["input_files"]}
        self.assertTrue(
            {"recipe", "research", "template", "app_config", "claim_evidence"}
            <= roles
        )
        template_files = {
            Path(item["path"]).name
            for item in spec["input_files"]
            if item["role"] == "template"
        }
        self.assertEqual(template_files, {"meta.yaml", "template.html"})
        self.assertTrue(
            all(Path(item["path"]).is_file() for item in spec["input_files"])
        )

    def test_expected_specs_exclude_drafts_and_seal_strategy_asset_and_engine_sources(self):
        specs = qa.expected_specs("sunrise-demo")
        self.assertNotIn(
            "retired-photo-line-example", {item["recipe"] for item in specs}
        )
        spec = next(item for item in specs if item["recipe"] == "morning-walk")
        names = {Path(item["path"]).name for item in spec["input_files"]}
        roles = {item["role"] for item in spec["input_files"]}

        self.assertTrue(
            {"icon.png", "sun.svg", "render.py", "qa.py", "registry.yaml"} <= names
        )
        self.assertTrue(
            {"brief", "asset_registry", "creative_asset", "engine"} <= roles
        )
        self.assertTrue(
            all(Path(item["path"]).is_file() for item in spec["input_files"])
        )

    def test_input_hashes_are_cached_within_one_audit(self):
        first = self.provenance_spec(self.png("cache-one.png"))
        second = self.provenance_spec(
            self.png("cache-two.png", color=(180, 220, 240))
        )

        with mock.patch.object(qa, "sha256", wraps=qa.sha256) as digest:
            qa.audit_outputs([first, second], require_provenance=True)

        self.assertEqual(digest.call_count, 7)

    def test_story_safe_zone_contract_requires_top_and_bottom_margins(self):
        errors = qa.validate_safe_zones({"safe_zones": {"story": {"top": 220}}})

        self.assertTrue(any("bottom" in error for error in errors))

    def test_every_template_applies_story_safe_zone_css(self):
        templates = (qa.ROOT / "templates" / "image").glob("*/template.html")

        for template in templates:
            css = template.read_text()
            with self.subTest(template=template.parent.name):
                self.assertIn("@media (max-aspect-ratio: 10/16)", css)
                self.assertIn("padding-bottom: 320px", css)

    def test_contact_sheet_is_generated_for_visual_inspection(self):
        paths = [self.png("one.png"), self.png("two.png", color=(180, 220, 240))]
        out = self.root / "contact.png"

        qa.create_contact_sheet(paths, out)

        self.assertTrue(out.exists())
        with Image.open(out) as image:
            self.assertGreater(image.width, 0)
            self.assertGreater(image.height, 0)


if __name__ == "__main__":
    unittest.main()
