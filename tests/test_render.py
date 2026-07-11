import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import render


class RenderSafetyTests(unittest.TestCase):
    def test_failed_renderer_cannot_pass_because_destination_already_exists(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "existing.png"
            out.write_bytes(b"known-good-old-output")

            with mock.patch.object(render, "CHROME", "/usr/bin/false"):
                with self.assertRaises(SystemExit):
                    render.screenshot("<html></html>", 10, 10, out)

            self.assertEqual(out.read_bytes(), b"known-good-old-output")

    def test_renderer_timeout_is_a_failure_and_preserves_previous_output(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake_chrome = root / "slow-chrome"
            fake_chrome.write_text("#!/bin/sh\nsleep 2\n")
            fake_chrome.chmod(0o755)
            out = root / "existing.png"
            out.write_bytes(b"known-good-old-output")

            with mock.patch.object(render, "CHROME", str(fake_chrome)):
                with self.assertRaises(SystemExit):
                    render.screenshot("<html></html>", 10, 10, out, timeout_seconds=0.05)

            self.assertEqual(out.read_bytes(), b"known-good-old-output")

    def test_plain_template_values_are_html_escaped_but_raw_values_are_not(self):
        html = "<p>{{ copy.headline }}</p><div>{{{ mascot }}}</div>"
        ctx = {
            "copy": {"headline": "Calm > rush & noise"},
            "mascot": "<svg><path /></svg>",
        }

        rendered = render.render_template(html, ctx)

        self.assertIn("Calm &gt; rush &amp; noise", rendered)
        self.assertIn("<svg><path /></svg>", rendered)

    def test_legacy_copy_cannot_masquerade_as_another_locale(self):
        recipe = {
            "lang": "pt-BR",
            "copy": {"headline": "Bom dia"},
        }

        with self.assertRaises(ValueError):
            render.resolve_copy(recipe, "es-MX", "en-US")

    def test_market_explicitly_selects_copy_language_and_fallback(self):
        recipe = {
            "locales": {
                "pt": {"headline": "Bom dia"},
                "en": {"headline": "Good morning"},
            }
        }

        copy, is_fallback = render.resolve_copy(
            recipe,
            "it",
            "en",
            copy_language="en",
        )

        self.assertEqual(copy["headline"], "Good morning")
        self.assertTrue(is_fallback)

    def test_app_markets_are_structured_and_keep_store_app_and_copy_locales(self):
        app = {
            "locales": {
                "markets": [
                    {
                        "id": "spain",
                        "countries": ["ES"],
                        "storefront_locale": "es-ES",
                        "app_locale": "es-MX",
                        "copy_language": "es",
                    }
                ]
            }
        }

        markets = render.app_target_markets(app)

        self.assertEqual(markets[0]["locale"], "es-ES")
        self.assertEqual(markets[0]["app_locale"], "es-MX")
        self.assertEqual(markets[0]["copy_language"], "es")

    def test_recipe_target_markets_limit_the_render_matrix(self):
        app = {
            "locales": {
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
                    {
                        "id": "us",
                        "countries": ["US"],
                        "storefront_locale": "en-US",
                        "app_locale": "en",
                        "copy_language": "en",
                    },
                ]
            }
        }

        markets = render.recipe_target_markets(
            app, {"target_markets": ["spain", "mexico"]}
        )

        self.assertEqual([market["id"] for market in markets], ["spain", "mexico"])

    def test_market_ids_cannot_share_one_storefront_locale(self):
        app = {
            "locales": {
                "markets": [
                    {
                        "id": "br-a",
                        "storefront_locale": "pt-BR",
                        "app_locale": "pt-BR",
                        "copy_language": "pt",
                    },
                    {
                        "id": "br-b",
                        "storefront_locale": "pt-BR",
                        "app_locale": "pt-BR",
                        "copy_language": "pt",
                    },
                ]
            }
        }

        with self.assertRaisesRegex(ValueError, "storefront_locale duplicado"):
            render.app_target_markets(app)

    def test_market_override_prevents_shared_language_markets_from_collapsing(self):
        recipe = {
            "locales": {"es": {"headline": "Base en español"}},
            "ad_copy": {
                "es": {"primary_text": "Base", "headline": "Base"}
            },
            "market_overrides": {
                "mexico": {
                    "copy": {"headline": "Tu mañana empieza en paz"},
                    "ad_copy": {
                        "primary_text": "Empieza tu mañana con calma.",
                        "headline": "Cinco minutos para ti",
                    },
                },
                "spain": {
                    "copy": {"headline": "Empieza el día en paz"},
                    "ad_copy": {
                        "primary_text": "Comienza el día con calma.",
                        "headline": "Cinco minutos para empezar",
                    },
                },
            },
        }
        mexico = {
            "id": "mexico",
            "locale": "es-MX",
            "copy_language": "es",
        }
        spain = {
            "id": "spain",
            "locale": "es-ES",
            "copy_language": "es",
        }

        mx_copy, _ = render.resolve_market_copy(recipe, mexico, "en")
        es_copy, _ = render.resolve_market_copy(recipe, spain, "en")

        self.assertEqual(mx_copy["headline"], "Tu mañana empieza en paz")
        self.assertEqual(es_copy["headline"], "Empieza el día en paz")
        self.assertNotEqual(
            render.resolve_market_ad_copy(recipe, mexico),
            render.resolve_market_ad_copy(recipe, spain),
        )

    def test_render_job_count_is_bounded(self):
        self.assertEqual(render.normalize_jobs(4), 4)
        with self.assertRaises(ValueError):
            render.normalize_jobs(0)
        with self.assertRaises(ValueError):
            render.normalize_jobs(9)


if __name__ == "__main__":
    unittest.main()
