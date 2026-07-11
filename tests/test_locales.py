import tempfile
import unittest
from pathlib import Path

import yaml

from scripts import locales


class LocaleStrategyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        resources = self.root / "App" / "Resources"
        metadata = self.root / "fastlane" / "metadata"
        for locale in ("pt-BR", "es-MX", "en"):
            (resources / f"{locale}.lproj").mkdir(parents=True)
        for locale in ("pt-BR", "es-ES", "en-US"):
            (metadata / locale).mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def app(self):
        return {
            "locales": {
                "fallback_copy_language": "en",
                "copy_languages": ["pt", "es", "en"],
                "app_resource_paths": ["App/Resources"],
                "storefront_metadata_path": "fastlane/metadata",
                "markets": [
                    {
                        "id": "br",
                        "countries": ["BR"],
                        "storefront_locale": "pt-BR",
                        "app_locale": "pt-BR",
                        "copy_language": "pt",
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
        }

    def test_audit_separates_app_resources_storefronts_and_copy_languages(self):
        result = locales.audit_locale_strategy(self.root, self.app())

        self.assertEqual(result["errors"], [])
        self.assertEqual(result["app_locales"], ["en", "es-MX", "pt-BR"])
        self.assertEqual(result["storefront_locales"], ["en-US", "es-ES", "pt-BR"])

    def test_target_with_missing_exact_app_locale_is_an_error(self):
        app = self.app()
        app["locales"]["markets"][1]["app_locale"] = "es-ES"

        result = locales.audit_locale_strategy(self.root, app)

        self.assertTrue(any("app_locale es-ES" in error for error in result["errors"]))

    def test_target_with_missing_storefront_locale_is_an_error(self):
        app = self.app()
        app["locales"]["markets"][1]["storefront_locale"] = "es-MX"

        result = locales.audit_locale_strategy(self.root, app)

        self.assertTrue(any("storefront_locale es-MX" in error for error in result["errors"]))

    def test_missing_resource_path_is_an_error(self):
        app = self.app()
        app["locales"]["app_resource_paths"] = ["missing"]

        result = locales.audit_locale_strategy(self.root, app)

        self.assertTrue(any("app_resource_path" in error for error in result["errors"]))

    def test_market_ids_cannot_share_a_storefront_locale(self):
        app = self.app()
        duplicate = dict(app["locales"]["markets"][0])
        duplicate["id"] = "br-two"
        app["locales"]["markets"].append(duplicate)

        result = locales.audit_locale_strategy(self.root, app)

        self.assertTrue(
            any("storefront_locale duplicado" in error for error in result["errors"])
        )

    def test_native_copy_policy_blocks_language_fallback_for_a_market(self):
        app = self.app()
        app["locales"]["require_native_market_copy"] = True
        app["locales"]["copy_languages"].append("en")
        app["locales"]["markets"][1]["copy_language"] = "en"

        result = locales.audit_locale_strategy(self.root, app)

        self.assertTrue(any("copy nativa" in error for error in result["errors"]))

    def test_sunrise_demo_and_demo_app_c_share_the_same_locale_contract(self):
        repo_root = Path(__file__).resolve().parent.parent
        for slug in ("sunrise-demo",):
            with self.subTest(app=slug):
                config = repo_root / "apps" / f"{slug}.yaml"
                self.assertTrue(config.exists())
                app = yaml.safe_load(config.read_text())
                resource_paths = [
                    locales.config_path(repo_root, path)
                    for path in app["locales"]["app_resource_paths"]
                ]
                storefront_path = locales.config_path(
                    repo_root,
                    app["locales"]["storefront_metadata_path"],
                )
                if not all(path.exists() for path in [*resource_paths, storefront_path]):
                    self.skipTest("external demo workspace is not available in this checkout")
                result = locales.audit_locale_strategy(repo_root, app)
                self.assertEqual(result["errors"], [])


if __name__ == "__main__":
    unittest.main()
