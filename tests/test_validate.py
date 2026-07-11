import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from scripts import validate


class ValidationGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        template = self.root / "templates" / "image" / "basic"
        template.mkdir(parents=True)
        (template / "template.html").write_text("<html></html>")
        (template / "meta.yaml").write_text(
            yaml.safe_dump(
                {
                    "copy_fields": {"headline": "required", "cta": "required"},
                    "limits": {"headline": 80, "cta": 30},
                    "formats": ["square"],
                }
            )
        )
        review = self.root / "templates" / "image" / "review-quote"
        review.mkdir(parents=True)
        (review / "template.html").write_text("<html></html>")
        (review / "meta.yaml").write_text(
            yaml.safe_dump(
                {
                    "copy_fields": {"quote": "required", "attribution": "required", "cta": "required"},
                    "formats": ["square"],
                    "requires_evidence": "app_store_review",
                }
            )
        )
        self.recipe = self.root / "recipe.yaml"
        self.app = {
            "voice": {
                "approved_ctas": {"pt": ["Baixe grátis"]},
                "banned": {"global": ["#1"], "pt": ["milagre"]},
                "anchors": {"pt": ["paz"]},
            },
            "locales": {"copy_languages": ["pt"]},
            "claims": {
                "daily_peace": {
                    "evidence": {"kind": "repo", "path": "README.md", "verified_at": "2026-07-09"}
                }
            },
        }

    def tearDown(self):
        self.temp.cleanup()

    def write_recipe(self, data, comment=""):
        self.recipe.write_text(yaml.safe_dump(data, allow_unicode=True) + comment)

    def validate(self):
        with mock.patch.object(validate, "ROOT", self.root):
            return validate.validate_recipe(self.app, self.app["voice"], self.recipe)

    def test_placeholder_is_a_blocking_error(self):
        self.write_recipe(
            {
                "template": "basic",
                "format": "square",
                "swiped_from": "competitor-ad-1",
                "claims_used": ["daily_peace"],
                "locales": {"pt": {"headline": "Paz diária", "cta": "Baixe grátis"}},
            },
            "\n# PLACEHOLDER\n",
        )

        errors, _ = self.validate()

        self.assertTrue(any("PLACEHOLDER" in error for error in errors))

    def test_missing_cta_policy_for_copy_language_is_an_error(self):
        self.app["locales"]["copy_languages"] = ["pt", "es"]
        self.write_recipe(
            {
                "template": "basic",
                "format": "square",
                "swiped_from": "competitor-ad-1",
                "claims_used": ["daily_peace"],
                "locales": {
                    "pt": {"headline": "Paz diária", "cta": "Baixe grátis"},
                    "es": {"headline": "Paz diaria", "cta": "Descarga gratis"},
                },
            }
        )

        errors, _ = self.validate()

        self.assertTrue(any("política de CTA" in error and "es" in error for error in errors))

    def test_missing_required_copy_language_is_an_error(self):
        self.app["locales"]["copy_languages"] = ["pt", "en"]
        self.write_recipe(
            {
                "template": "basic",
                "format": "square",
                "swiped_from": "competitor-ad-1",
                "claims_used": ["daily_peace"],
                "locales": {"pt": {"headline": "Paz diária", "cta": "Baixe grátis"}},
            }
        )

        errors, _ = self.validate()

        self.assertTrue(any("sem copy obrigatória" in error and "en" in error for error in errors))

    def test_review_recipe_requires_verifiable_evidence(self):
        self.write_recipe(
            {
                "template": "review-quote",
                "format": "square",
                "swiped_from": "competitor-ad-1",
                "claims_used": ["daily_peace"],
                "locales": {
                    "pt": {
                        "quote": "Meu dia começa em paz.",
                        "attribution": "App Store",
                        "cta": "Baixe grátis",
                    }
                },
            }
        )

        errors, _ = self.validate()

        self.assertTrue(any("evidência app_store_review" in error for error in errors))

    def test_unknown_claim_is_an_error(self):
        self.write_recipe(
            {
                "template": "basic",
                "format": "square",
                "swiped_from": "competitor-ad-1",
                "claims_used": ["miracle_results"],
                "locales": {"pt": {"headline": "Paz diária", "cta": "Baixe grátis"}},
            }
        )

        errors, _ = self.validate()

        self.assertTrue(any("claim não comprovada" in error for error in errors))

    def test_missing_app_asset_is_an_error(self):
        self.app["assets"] = {"icon": "assets/missing-icon.png"}

        with mock.patch.object(validate, "ROOT", self.root):
            errors, _ = validate.validate_app(self.app)

        self.assertTrue(any("asset não encontrado" in error for error in errors))

    def test_localized_ad_copy_is_required(self):
        self.write_recipe(
            {
                "template": "basic",
                "format": "square",
                "swiped_from": "competitor-ad-1",
                "claims_used": ["daily_peace"],
                "locales": {
                    "pt": {"headline": "Paz diária", "cta": "Baixe grátis"}
                },
            }
        )

        errors, _ = self.validate()

        self.assertTrue(any("ad_copy" in error for error in errors))

    def test_missing_recipe_background_is_an_error(self):
        self.write_recipe(
            {
                "template": "basic",
                "format": "square",
                "swiped_from": "competitor-ad-1",
                "claims_used": ["daily_peace"],
                "locales": {"pt": {"headline": "Paz diária", "cta": "Baixe grátis"}},
                "image": {"kind": "higgsfield", "file": "assets/missing.png"},
            }
        )

        errors, _ = self.validate()

        self.assertTrue(any("imagem não encontrada" in error for error in errors))

    def test_shared_copy_language_requires_full_market_id_overrides(self):
        self.app["voice"]["approved_ctas"]["es"] = ["Descarga gratis"]
        self.app["voice"]["anchors"]["es"] = ["paz"]
        self.app["locales"] = {
            "copy_languages": ["es"],
            "markets": [
                {
                    "id": "mexico",
                    "countries": ["MX"],
                    "storefront_locale": "es-MX",
                    "app_locale": "es-MX",
                    "copy_language": "es",
                },
                {
                    "id": "spain",
                    "countries": ["ES"],
                    "storefront_locale": "es-ES",
                    "app_locale": "es-MX",
                    "copy_language": "es",
                },
            ],
        }
        recipe = {
            "template": "basic",
            "format": "square",
            "target_markets": ["mexico", "spain"],
            "swiped_from": "competitor-ad-1",
            "claims_used": ["daily_peace"],
            "locales": {
                "es": {"headline": "Paz diaria", "cta": "Descarga gratis"}
            },
            "ad_copy": {
                "es": {"primary_text": "Empieza en paz.", "headline": "Paz diaria"}
            },
        }
        self.write_recipe(recipe)

        errors, _ = self.validate()

        self.assertTrue(any("market_overrides.mexico" in error for error in errors))
        self.assertTrue(any("market_overrides.spain" in error for error in errors))

        recipe["market_overrides"] = {
            "mexico": {
                "copy": {"headline": "Tu mañana en paz", "cta": "Descarga gratis"},
                "ad_copy": {
                    "primary_text": "Empieza tu mañana en paz.",
                    "headline": "Tu mañana en paz",
                },
            },
            "spain": {
                "copy": {"headline": "Empieza el día en paz", "cta": "Descarga gratis"},
                "ad_copy": {
                    "primary_text": "Comienza el día en paz.",
                    "headline": "Empieza en paz",
                },
            },
        }
        self.write_recipe(recipe)

        errors, _ = self.validate()

        self.assertFalse(any("market_overrides" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
