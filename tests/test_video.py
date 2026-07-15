import importlib.util
import hashlib
import json
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
VIDEO_MODULE_PATH = ROOT / "scripts" / "video.py"


def load_video_module():
    if not VIDEO_MODULE_PATH.exists():
        return None
    spec = importlib.util.spec_from_file_location("scripts.video", VIDEO_MODULE_PATH)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


video = load_video_module()


class VideoWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        template = self.root / "templates" / "video" / "modular-story"
        template.mkdir(parents=True)
        (template / "meta.yaml").write_text(
            yaml.safe_dump(
                {
                    "name": "modular-story",
                    "composition": "CreativeVideo",
                    "fps": 30,
                    "max_concurrency": 1,
                    "formats": {
                        "story": {
                            "width": 1080,
                            "height": 1920,
                            "safe_zones": {"top": 0.14, "bottom": 0.20},
                        },
                        "portrait": {
                            "width": 1080,
                            "height": 1350,
                            "safe_zones": {"top": 0.08, "bottom": 0.10},
                        },
                        "square": {
                            "width": 1080,
                            "height": 1080,
                            "safe_zones": {"top": 0.08, "bottom": 0.10},
                        },
                    },
                },
                sort_keys=False,
            )
        )
        asset = self.root / "assets" / "demo" / "icon.png"
        asset.parent.mkdir(parents=True)
        asset.write_bytes(b"image")
        self.registry_path = asset.parent / "registry.yaml"
        self.write_registry()
        pattern_dir = self.root / "swipe" / "demo"
        pattern_dir.mkdir(parents=True)
        self.pattern_path = pattern_dir / "video-patterns.yaml"
        self.pattern_path.write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "app": "demo",
                    "observed_at": "2020-01-01T00:00:00Z",
                    "expires_at": "2099-01-01T00:00:00Z",
                    "patterns": [
                        {
                            "id": "reference-video",
                            "mode": "browser_observation",
                            "platform": "meta",
                            "advertiser": "Example",
                            "source_url": "https://www.facebook.com/ads/library/?id=123",
                            "lineage": "competitor_pattern",
                            "locale": "pt-BR",
                            "copy_language": "pt",
                            "rights": {
                                "class": "reference_only",
                                "media_reuse": False,
                                "allowed_uses": ["structural_analysis"],
                            },
                            "media": {
                                "format": "story",
                                "duration_seconds": 15,
                            },
                            "structure": {
                                "hook": "Abertura observada",
                                "body": "Demonstração observada",
                                "cta": "Fechamento observado",
                            },
                        }
                    ],
                },
                sort_keys=False,
            )
        )
        brief_dir = self.root / "briefs" / "demo"
        brief_dir.mkdir(parents=True)
        self.brief_path = brief_dir / "demo-brief.yaml"
        self.brief_path.write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "id": "demo-brief",
                    "app": "demo",
                    "status": "approved",
                    "approved_by": "tester",
                    "objective": "Test one agent-authored video hypothesis",
                    "primary_kpi": "qualified_install_rate",
                    "markets": ["br"],
                    "destination": {
                        "type": "app_store",
                        "url": "https://example.com/app",
                    },
                    "hypothesis": {
                        "action": "Show the ritual",
                        "expected_result": "Improve qualified installs",
                        "reason": "Observed evidence supports testing it",
                    },
                    "test_design": {
                        "isolated_variable": "hook",
                        "constants": ["offer", "destination"],
                    },
                    "measurement": {
                        "observation_window_hours": 72,
                        "attribution_window": {"click_days": 7, "view_days": 1},
                        "currency": "BRL",
                    },
                    "concepts": [
                        {
                            "id": "demo-concept",
                            "lineage": "competitor_pattern",
                            "lineage_ref": "reference-video",
                            "research_refs": ["reference-video"],
                            "agent_rationale": "Test the observed structure with owned assets.",
                        }
                    ],
                },
                sort_keys=False,
            )
        )
        self.app = {
            "slug": "demo",
            "name": "Demo",
            "palette": {
                "bg_top": "#fff3d6",
                "bg_bottom": "#e8c58f",
                "ink": "#2d2118",
                "accent": "#704126",
                "on_accent": "#ffffff",
            },
            "fonts": {"headline": "Georgia", "body": "Arial"},
            "voice": {"approved_ctas": {"pt": ["Baixe grátis"]}},
            "claims": {
                "daily_ritual": {
                    "evidence": {"kind": "repo", "path": "README.md"}
                }
            },
            "locales": {
                "markets": [
                    {
                        "id": "br",
                        "storefront_locale": "pt-BR",
                        "app_locale": "pt-BR",
                        "copy_language": "pt",
                    }
                ]
            },
        }

    def write_registry(self, extra_assets=None):
        icon = self.root / "assets" / "demo" / "icon.png"
        entries = [
            {
                "id": "demo-icon",
                "kind": "owned",
                "path": "assets/demo/icon.png",
                "sha256": hashlib.sha256(icon.read_bytes()).hexdigest(),
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
        ]
        entries.extend(extra_assets or [])
        self.registry_path.write_text(
            yaml.safe_dump(
                {"version": 1, "app": "demo", "assets": entries},
                sort_keys=False,
            )
        )
        self.recipe = {
            "version": 1,
            "media_type": "video",
            "brief_ref": "demo-brief",
            "concept_id": "demo-concept",
            "variant_id": "demo-video-v1",
            "target_markets": ["br"],
            "target_platforms": ["meta"],
            "template": "modular-story",
            "composition": "CreativeVideo",
            "format": "story",
            "fps": 30,
            "duration_seconds": 15,
            "concurrency": 1,
            "audio_strategy": "intentional_silence",
            "safe_zones": {"top": 0.14, "bottom": 0.20},
            "claims_used": ["daily_ritual"],
            "research_refs": ["reference-video"],
            "execution_ref": "reference-video",
            "asset_refs": ["demo-icon"],
            "references": [
                {
                    "id": "reference-video",
                    "media_type": "video",
                    "source_url": "https://www.facebook.com/ads/library/?id=123",
                    "usage": "structural_reference_only",
                }
            ],
            "assets": {
                "icon": {
                    "kind": "image",
                    "path": "assets/demo/icon.png",
                    "asset_ref": "demo-icon",
                }
            },
            "locales": {
                "pt-BR": {
                    "copy_language": "pt",
                    "copy": {
                        "hook": "Sua noite pode terminar em paz.",
                        "promise": "Uma história calma para desacelerar.",
                        "cta": "Baixe grátis",
                    },
                    "ad_copy": {
                        "primary_text": "Encerre o dia com uma caminhada guiada e serena.",
                        "headline": "Uma noite de paz",
                        "description": "Um momento calmo antes de dormir.",
                    },
                    "scenes": [
                        {
                            "id": "hook",
                            "start_seconds": 0,
                            "duration_seconds": 5,
                            "layout": "center",
                            "background": "#fff3d6",
                            "foreground": "#2d2118",
                            "copy": {"headline": "hook"},
                            "asset": "icon",
                            "enter": "fade",
                        },
                        {
                            "id": "promise",
                            "start_seconds": 5,
                            "duration_seconds": 6,
                            "layout": "bottom",
                            "background": "#e8c58f",
                            "foreground": "#2d2118",
                            "copy": {"headline": "promise"},
                            "enter": "rise",
                        },
                        {
                            "id": "cta",
                            "start_seconds": 11,
                            "duration_seconds": 4,
                            "layout": "center",
                            "background": "#704126",
                            "foreground": "#ffffff",
                            "copy": {"cta": "cta"},
                            "enter": "cut",
                        },
                    ],
                }
            },
        }

    def tearDown(self):
        self.temp.cleanup()

    def function(self, name):
        self.assertIsNotNone(video, "scripts/video.py ainda não existe")
        function = getattr(video, name, None)
        self.assertIsNotNone(function, f"scripts.video.{name} ainda não existe")
        return function

    def audit(self, recipe=None):
        return self.function("audit_recipe")(
            recipe or self.recipe,
            self.app,
            root=self.root,
        )

    def test_local_remotion_manifest_pins_compatible_runtime(self):
        package_path = ROOT / "remotion" / "package.json"
        self.assertTrue(package_path.is_file(), "remotion/package.json ausente")
        package = json.loads(package_path.read_text())
        dependencies = package["dependencies"]

        self.assertEqual(dependencies["remotion"], "4.0.487")
        self.assertEqual(dependencies["@remotion/cli"], "4.0.487")
        self.assertEqual(dependencies["react"], dependencies["react-dom"])
        self.assertNotRegex(dependencies["react"], r"^[~^><=]")
        self.assertGreaterEqual(int(dependencies["react"].split(".")[0]), 18)
        self.assertNotIn(
            "render",
            package.get("scripts", {}),
            "raw Remotion render bypasses the bounded Python wrapper",
        )

    def test_valid_agent_authored_recipe_passes_objective_contracts(self):
        result = self.audit()

        self.assertEqual(result["errors"], [])

    def test_original_video_execution_does_not_require_a_structural_reference(self):
        candidate = deepcopy(self.recipe)
        candidate.pop("execution_ref")
        candidate["references"] = []

        result = self.audit(candidate)

        self.assertEqual(result["errors"], [])

    def test_original_video_can_use_concept_research_without_video_patterns_file(self):
        candidate = deepcopy(self.recipe)
        candidate.pop("execution_ref")
        candidate["references"] = []
        competitor_path = self.root / "swipe" / "demo" / "competitors.yaml"
        competitor_path.write_text(
            yaml.safe_dump(
                {
                    "creatives": [
                        {
                            "id": "reference-video",
                            "lineage": "competitor_pattern",
                            "evidence_level": "observed",
                            "source_url": "https://example.com/concept-evidence",
                        }
                    ]
                },
                sort_keys=False,
            )
        )
        self.pattern_path.unlink()

        result = self.audit(candidate)

        self.assertEqual(result["errors"], [])

    def test_duplicate_research_id_cannot_conflict_across_video_registries(self):
        competitor_path = self.root / "swipe" / "demo" / "competitors.yaml"
        competitor_path.write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "app": "demo",
                    "creatives": [
                        {
                            "id": "reference-video",
                            "source_url": "https://www.facebook.com/ads/library/?id=999",
                            "lineage": "competitor_pattern",
                        }
                    ],
                },
                sort_keys=False,
            )
        )

        result = self.audit()

        self.assertTrue(
            any("registry conflict" in error for error in result["errors"]),
            result["errors"],
        )

    def test_app_identity_registry_and_video_patterns_are_fail_closed(self):
        audit_recipe = self.function("audit_recipe")
        wrong_app = audit_recipe(
            self.recipe,
            self.app,
            root=self.root,
            expected_app="another-app",
        )
        self.assertTrue(any("app.slug" in error for error in wrong_app["errors"]))

        self.registry_path.rename(self.registry_path.with_suffix(".missing"))
        missing_registry = self.audit()
        self.assertTrue(
            any("asset registry" in error for error in missing_registry["errors"])
        )
        self.registry_path.with_suffix(".missing").rename(self.registry_path)

        self.pattern_path.rename(self.pattern_path.with_suffix(".missing"))
        missing_patterns = self.audit()
        self.assertTrue(
            any("video patterns" in error for error in missing_patterns["errors"])
        )
        self.pattern_path.with_suffix(".missing").rename(self.pattern_path)

        self.brief_path.rename(self.brief_path.with_suffix(".missing"))
        missing_brief = self.audit()
        self.assertTrue(any("brief" in error for error in missing_brief["errors"]))

    def test_reference_url_is_bound_to_the_canonical_video_pattern(self):
        candidate = deepcopy(self.recipe)
        candidate["references"][0]["source_url"] = "https://example.com/unrelated"

        result = self.audit(candidate)

        self.assertTrue(any("source_url" in error for error in result["errors"]))

    def test_timeline_numbers_are_finite_and_frame_aligned(self):
        invalid_values = (float("nan"), float("inf"), float("-inf"))
        for value in invalid_values:
            with self.subTest(value=value):
                candidate = deepcopy(self.recipe)
                candidate["duration_seconds"] = value
                self.assertTrue(self.audit(candidate)["errors"])

        subframe = deepcopy(self.recipe)
        subframe["locales"]["pt-BR"]["scenes"] = [
            {
                "id": "too-short",
                "start_seconds": 0,
                "duration_seconds": 0.01,
                "layout": "center",
                "copy": {"headline": "hook"},
            },
            {
                "id": "rest",
                "start_seconds": 0.01,
                "duration_seconds": 14.99,
                "layout": "center",
                "copy": {"headline": "promise"},
            },
        ]
        result = self.audit(subframe)
        self.assertTrue(any("frame" in error for error in result["errors"]))

    def test_video_copy_obeys_brand_banned_terms(self):
        self.app["voice"]["banned"] = {
            "global": ["garantida"],
            "pt": ["milagre"],
        }
        candidate = deepcopy(self.recipe)
        candidate["locales"]["pt-BR"]["copy"]["promise"] = (
            "Uma cura garantida por milagre."
        )

        result = self.audit(candidate)

        self.assertTrue(any("termo proibido" in error for error in result["errors"]))

    def test_missing_locale_or_copy_is_blocking(self):
        without_locales = deepcopy(self.recipe)
        without_locales.pop("locales")
        without_copy = deepcopy(self.recipe)
        without_copy["locales"]["pt-BR"].pop("copy")

        for candidate, expected in (
            (without_locales, "locales"),
            (without_copy, "copy"),
        ):
            with self.subTest(expected=expected):
                result = self.audit(candidate)
                self.assertTrue(any(expected in error for error in result["errors"]))

    def test_every_video_locale_requires_native_off_canvas_ad_copy(self):
        missing = deepcopy(self.recipe)
        missing["locales"]["pt-BR"].pop("ad_copy")
        incomplete = deepcopy(self.recipe)
        incomplete["locales"]["pt-BR"]["ad_copy"].pop("headline")

        missing_result = self.audit(missing)
        incomplete_result = self.audit(incomplete)

        self.assertTrue(any("ad_copy" in error for error in missing_result["errors"]))
        self.assertTrue(any("ad_copy.headline" in error for error in incomplete_result["errors"]))

    def test_video_ad_copy_obeys_the_same_banned_copy_policy(self):
        candidate = deepcopy(self.recipe)
        candidate["locales"]["pt-BR"]["ad_copy"]["headline"] = "O melhor app"
        self.app["voice"]["banned"] = {"pt": ["melhor app"]}

        result = self.audit(candidate)

        self.assertTrue(any("termo proibido" in error for error in result["errors"]))

    def test_strategy_and_rights_lineage_fields_are_required(self):
        for field in (
            "brief_ref",
            "concept_id",
            "variant_id",
            "target_markets",
            "target_platforms",
            "research_refs",
            "asset_refs",
        ):
            with self.subTest(field=field):
                candidate = deepcopy(self.recipe)
                candidate.pop(field)

                result = self.audit(candidate)

                self.assertTrue(any(field in error for error in result["errors"]))

    def test_target_markets_and_localized_scene_plans_cannot_drift(self):
        candidate = deepcopy(self.recipe)
        candidate["target_markets"] = ["missing-market"]

        result = self.audit(candidate)

        self.assertTrue(any("target_markets" in error for error in result["errors"]))

    def test_scene_timeline_must_be_contiguous_and_within_duration(self):
        overlap = deepcopy(self.recipe)
        overlap["locales"]["pt-BR"]["scenes"][1]["start_seconds"] = 4
        gap = deepcopy(self.recipe)
        gap["locales"]["pt-BR"]["scenes"][1]["start_seconds"] = 6
        overrun = deepcopy(self.recipe)
        overrun["locales"]["pt-BR"]["scenes"][-1]["duration_seconds"] = 5

        for candidate in (overlap, gap, overrun):
            with self.subTest(candidate=candidate):
                result = self.audit(candidate)
                self.assertTrue(
                    any("timeline" in error for error in result["errors"])
                )

    def test_unknown_claim_is_blocking(self):
        candidate = deepcopy(self.recipe)
        candidate["claims_used"] = ["miracle_result"]

        result = self.audit(candidate)

        self.assertTrue(any("claim" in error for error in result["errors"]))

    def test_cta_outside_app_policy_is_blocking(self):
        candidate = deepcopy(self.recipe)
        candidate["locales"]["pt-BR"]["copy"]["cta"] = "Compre agora"

        result = self.audit(candidate)

        self.assertTrue(any("CTA" in error for error in result["errors"]))

    def test_non_video_reference_is_blocking(self):
        candidate = deepcopy(self.recipe)
        candidate["references"][0]["media_type"] = "image"

        result = self.audit(candidate)

        self.assertTrue(any("refer" in error and "vídeo" in error for error in result["errors"]))

    def test_format_dimensions_and_safe_zones_are_fail_closed(self):
        unknown = deepcopy(self.recipe)
        unknown["format"] = "landscape"
        unsafe = deepcopy(self.recipe)
        unsafe["safe_zones"] = {"top": 0.10, "bottom": 0.10}

        unknown_result = self.audit(unknown)
        unsafe_result = self.audit(unsafe)

        self.assertTrue(any("formato" in error for error in unknown_result["errors"]))
        self.assertTrue(any("safe zone" in error for error in unsafe_result["errors"]))

    def test_fps_and_concurrency_are_fixed_for_reliable_local_rendering(self):
        wrong_fps = deepcopy(self.recipe)
        wrong_fps["fps"] = 24
        parallel = deepcopy(self.recipe)
        parallel["concurrency"] = 2

        fps_result = self.audit(wrong_fps)
        concurrency_result = self.audit(parallel)

        self.assertTrue(any("30 fps" in error for error in fps_result["errors"]))
        self.assertTrue(
            any("concorrência" in error for error in concurrency_result["errors"])
        )

    def test_audio_strategy_must_match_the_declared_audio_input(self):
        missing = deepcopy(self.recipe)
        missing.pop("audio_strategy")
        contradictory = deepcopy(self.recipe)
        contradictory["audio_strategy"] = "licensed_music"

        self.assertTrue(
            any("audio_strategy" in error for error in self.audit(missing)["errors"])
        )
        self.assertTrue(
            any(
                "audio_strategy" in error
                for error in self.audit(contradictory)["errors"]
            )
        )

    def test_audio_input_requires_a_rights_registry_reference(self):
        audio_path = self.root / "assets" / "demo" / "music.wav"
        audio_path.write_bytes(b"licensed-audio-fixture")
        candidate = deepcopy(self.recipe)
        candidate["audio_strategy"] = "licensed_music"
        candidate["audio"] = {
            "path": "assets/demo/music.wav",
            "volume": 0.8,
            "asset_ref": "demo-music",
        }
        candidate["asset_refs"].append("demo-music")
        self.write_registry(
            [
                {
                    "id": "demo-music",
                    "kind": "owned",
                    "path": "assets/demo/music.wav",
                    "sha256": hashlib.sha256(audio_path.read_bytes()).hexdigest(),
                    "source": {"kind": "app_repository"},
                    "rights": {
                        "status": "cleared",
                        "commercial_use": True,
                        "derivative_use": True,
                        "basis": "owned_by_app",
                        "scope": {"paid_ads": True, "platforms": ["meta"]},
                    },
                }
            ]
        )

        self.assertEqual(self.audit(candidate)["errors"], [])
        candidate["audio"].pop("asset_ref")

        self.assertTrue(
            any("audio.asset_ref" in error for error in self.audit(candidate)["errors"])
        )

    def test_voiceover_is_localized_and_captions_enter_render_props(self):
        voice_path = self.root / "assets" / "demo" / "voice-pt.mp3"
        voice_path.write_bytes(b"localized-voice-fixture")
        captions_path = self.root / "assets" / "demo" / "voice-pt.srt"
        captions_path.write_text(
            "1\n00:00:00,000 --> 00:00:02,000\nComece em paz\n\n"
            "2\n00:00:02,000 --> 00:00:04,000\nUm passo por vez\n"
        )
        self.write_registry(
            [
                {
                    "id": "demo-voice-pt",
                    "kind": "owned",
                    "path": "assets/demo/voice-pt.mp3",
                    "sha256": hashlib.sha256(voice_path.read_bytes()).hexdigest(),
                    "source": {"kind": "app_repository"},
                    "rights": {
                        "status": "cleared",
                        "commercial_use": True,
                        "derivative_use": True,
                        "basis": "owned_by_app",
                        "scope": {"paid_ads": True, "platforms": ["meta"]},
                    },
                }
            ]
        )
        candidate = deepcopy(self.recipe)
        candidate["audio_strategy"] = "voiceover"
        candidate["muted"] = False
        candidate["asset_refs"].append("demo-voice-pt")
        candidate["locales"]["pt-BR"].update(
            {
                "voiceover": {
                    "path": "assets/demo/voice-pt.mp3",
                    "asset_ref": "demo-voice-pt",
                    "volume": 1,
                },
                "captions_path": "assets/demo/voice-pt.srt",
            }
        )

        result = self.audit(candidate)
        self.assertEqual(result["errors"], [])
        props = self.function("build_props")(
            candidate, self.app, "pt-BR", root=self.root
        )
        self.assertEqual(props["audioTracks"][0]["kind"], "voiceover")
        self.assertEqual(props["audioTracks"][0]["path"], "assets/demo/voice-pt.mp3")
        self.assertEqual([cue["text"] for cue in props["captions"]], [
            "Comece em paz",
            "Um passo por vez",
        ])
        self.assertEqual(props["captions"][0]["durationInFrames"], 60)

        global_voice = deepcopy(candidate)
        global_voice["audio"] = global_voice["locales"]["pt-BR"].pop("voiceover")
        self.assertTrue(
            any("voiceover" in error for error in self.audit(global_voice)["errors"])
        )

    def test_assets_must_be_local_paths_inside_the_workspace(self):
        remote = deepcopy(self.recipe)
        remote["assets"]["icon"]["path"] = "https://example.com/icon.png"
        escaped = deepcopy(self.recipe)
        escaped["assets"]["icon"]["path"] = "../outside.png"

        for candidate in (remote, escaped):
            with self.subTest(candidate=candidate):
                result = self.audit(candidate)
                self.assertTrue(any("asset" in error for error in result["errors"]))

    def test_props_resolve_only_the_selected_localized_scene_plan(self):
        build_props = self.function("build_props")

        props = build_props(self.recipe, self.app, "pt-BR", root=self.root)

        self.assertEqual(props["locale"], "pt-BR")
        self.assertEqual((props["width"], props["height"]), (1080, 1920))
        self.assertEqual(props["fps"], 30)
        self.assertEqual(props["durationInFrames"], 450)
        self.assertEqual(props["scenes"][1]["startFrame"], 150)
        self.assertEqual(props["scenes"][1]["text"]["headline"], self.recipe["locales"]["pt-BR"]["copy"]["promise"])
        self.assertNotIn("locales", props)

    def test_market_selection_requires_locale_or_explicit_all_markets(self):
        select_locales = self.function("select_locales")

        with self.assertRaises(Exception):
            select_locales(self.recipe)
        with self.assertRaises(Exception):
            select_locales(self.recipe, locale="pt-BR", all_markets=True)
        self.assertEqual(
            select_locales(self.recipe, locale="pt-BR"),
            ["pt-BR"],
        )
        self.assertEqual(
            select_locales(self.recipe, all_markets=True),
            ["pt-BR"],
        )

    def test_video_market_id_selector_and_native_overrides_do_not_collapse_spanish(self):
        self.app["locales"]["markets"] = [
            {
                "id": "mexico",
                "storefront_locale": "es-MX",
                "app_locale": "es-MX",
                "copy_language": "es",
            },
            {
                "id": "spain",
                "storefront_locale": "es-ES",
                "app_locale": "es-MX",
                "copy_language": "es",
            },
        ]
        self.app["voice"]["approved_ctas"]["es"] = ["Descarga gratis"]
        candidate = deepcopy(self.recipe)
        candidate["target_markets"] = ["mexico", "spain"]
        spanish = deepcopy(candidate["locales"]["pt-BR"])
        spanish["copy_language"] = "es"
        spanish["copy"] = {
            "hook": "Empieza la mañana en paz.",
            "promise": "Una oración serena para comenzar el día.",
            "cta": "Descarga gratis",
        }
        spanish["ad_copy"] = {
            "primary_text": "Empieza el día con una oración serena.",
            "headline": "Una mañana en paz",
            "description": "Cinco minutos para empezar.",
        }
        candidate["locales"] = {
            "es-MX": deepcopy(spanish),
            "es-ES": deepcopy(spanish),
        }
        brief = yaml.safe_load(self.brief_path.read_text())
        brief["markets"] = ["mexico", "spain"]
        self.brief_path.write_text(yaml.safe_dump(brief, sort_keys=False))

        missing = self.audit(candidate)

        self.assertTrue(
            any("market_overrides.mexico" in error for error in missing["errors"])
        )
        self.assertTrue(
            any("market_overrides.spain" in error for error in missing["errors"])
        )

        candidate["market_overrides"] = {
            "mexico": {
                "copy": {
                    "hook": "¿Tu mañana arrancó a mil?",
                    "promise": "Haz una pausa con una oración serena.",
                    "cta": "Descarga gratis",
                },
                "ad_copy": {
                    "primary_text": "Empieza tu mañana con calma.",
                    "headline": "Cinco minutos de paz",
                    "description": "Una pausa antes de arrancar.",
                },
            },
            "spain": {
                "copy": {
                    "hook": "¿Has empezado el día a mil?",
                    "promise": "Haz una pausa con una oración serena.",
                    "cta": "Descarga gratis",
                },
                "ad_copy": {
                    "primary_text": "Comienza el día con calma.",
                    "headline": "Cinco minutos de paz",
                    "description": "Una pausa antes de empezar.",
                },
            },
        }

        result = self.audit(candidate)
        mx_props = self.function("build_props")(
            candidate, self.app, "es-MX", root=self.root
        )
        es_props = self.function("build_props")(
            candidate, self.app, "es-ES", root=self.root
        )
        mx_by_market_id = self.function("build_props")(
            candidate, self.app, "mexico", root=self.root
        )

        self.assertEqual(result["errors"], [])
        self.assertEqual(
            self.function("select_locales")(
                candidate, self.app, locale="mexico"
            ),
            ["es-MX"],
        )
        self.assertEqual(
            self.function("select_locales")(candidate, self.app, all_markets=True),
            ["es-MX", "es-ES"],
        )
        self.assertEqual(mx_props["marketId"], "mexico")
        self.assertEqual(es_props["marketId"], "spain")
        self.assertEqual(mx_by_market_id["locale"], "es-MX")
        self.assertNotEqual(
            mx_props["scenes"][0]["text"]["headline"],
            es_props["scenes"][0]["text"]["headline"],
        )

    def test_render_command_uses_local_cli_and_fixed_safe_encoding(self):
        build_command = self.function("build_render_command")
        props_path = self.root / "props.json"
        output_path = self.root / "output" / "demo.mp4"
        local_cli = self.root / "remotion" / "node_modules" / ".bin" / "remotion"

        command = build_command(
            props_path,
            output_path,
            root=self.root,
            remotion_bin=local_cli,
        )

        self.assertEqual(command[0], str(local_cli))
        self.assertIn(str(self.root / "remotion" / "src" / "index.ts"), command)
        self.assertIn("CreativeVideo", command)
        for flag, value in (
            ("--codec", "h264"),
            ("--audio-codec", "aac"),
            ("--pixel-format", "yuv420p"),
            ("--fps", "30"),
            ("--concurrency", "1"),
            ("--color-space", "bt709"),
            ("--sample-rate", "44100"),
        ):
            self.assertEqual(command[command.index(flag) + 1], value)

    def test_video_output_rejects_symlink_root_base_and_existing_ancestors(self):
        safe_output = self.function("_safe_output_path")
        outside = self.root / "outside"
        outside.mkdir()

        root_link = self.root.parent / f"{self.root.name}-link"
        root_link.symlink_to(self.root, target_is_directory=True)
        try:
            with self.assertRaisesRegex(Exception, "root.*symlink"):
                safe_output(
                    root_link / "output" / "demo" / "video.mp4", root_link
                )
        finally:
            root_link.unlink()

        output_base = self.root / "output"
        output_base.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(Exception, "symlink"):
            safe_output(output_base / "demo" / "video.mp4", self.root)
        output_base.unlink()

        output_base.mkdir()
        (output_base / "demo").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(Exception, "symlink"):
            safe_output(output_base / "demo" / "video.mp4", self.root)

    def test_render_promotes_the_completed_mp4_atomically(self):
        render_video = self.function("render_video")
        local_cli = self.root / "remotion" / "node_modules" / ".bin" / "remotion"
        local_cli.parent.mkdir(parents=True)
        local_cli.write_text("#!/bin/sh\n")
        local_cli.chmod(0o755)
        output = self.root / "output" / "demo" / "sleep.mp4"
        output.parent.mkdir(parents=True)
        output.write_bytes(b"previous")

        def successful_runner(command, **kwargs):
            self.assertEqual(output.read_bytes(), b"previous")
            temporary_output = next(Path(value) for value in command if str(value).endswith(".mp4"))
            self.assertNotEqual(temporary_output, output)
            temporary_output.write_bytes(b"complete-video")
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

        rendered = render_video(
            self.recipe,
            self.app,
            "pt-BR",
            output,
            root=self.root,
            runner=successful_runner,
            remotion_bin=local_cli,
        )

        self.assertEqual(rendered, output)
        self.assertEqual(output.read_bytes(), b"complete-video")
        self.assertEqual(list(output.parent.glob("*.partial.mp4")), [])
        receipt_path = output.with_suffix(".render.json")
        self.assertTrue(receipt_path.is_file())
        receipt = json.loads(receipt_path.read_text())
        props = self.function("build_props")(
            self.recipe, self.app, "pt-BR", root=self.root
        )
        self.assertEqual(
            self.function("render_receipt_errors")(
                receipt,
                recipe=self.recipe,
                app=self.app,
                locale="pt-BR",
                output_path=output,
                props=props,
                root=self.root,
            ),
            [],
        )

        output.write_bytes(b"tampered-video")
        self.assertTrue(
            self.function("render_receipt_errors")(
                receipt,
                recipe=self.recipe,
                app=self.app,
                locale="pt-BR",
                output_path=output,
                props=props,
                root=self.root,
            )
        )

    def test_failed_render_preserves_the_previous_output(self):
        render_video = self.function("render_video")
        local_cli = self.root / "remotion" / "node_modules" / ".bin" / "remotion"
        local_cli.parent.mkdir(parents=True)
        local_cli.write_text("#!/bin/sh\n")
        local_cli.chmod(0o755)
        output = self.root / "output" / "demo" / "sleep.mp4"
        output.parent.mkdir(parents=True)
        output.write_bytes(b"previous")

        def failed_runner(command, **kwargs):
            temporary_output = next(Path(value) for value in command if str(value).endswith(".mp4"))
            temporary_output.write_bytes(b"partial")
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="failed")

        with self.assertRaises(Exception):
            render_video(
                self.recipe,
                self.app,
                "pt-BR",
                output,
                root=self.root,
                runner=failed_runner,
                remotion_bin=local_cli,
            )

        self.assertEqual(output.read_bytes(), b"previous")
        self.assertEqual(list(output.parent.glob("*.partial.mp4")), [])

    def test_timed_out_render_preserves_previous_output(self):
        render_video = self.function("render_video")
        local_cli = self.root / "remotion" / "node_modules" / ".bin" / "remotion"
        local_cli.parent.mkdir(parents=True)
        local_cli.write_text("#!/bin/sh\n")
        local_cli.chmod(0o755)
        output = self.root / "output" / "demo" / "timeout.mp4"
        output.parent.mkdir(parents=True)
        output.write_bytes(b"previous")

        captured = {}

        def timed_out(command, **kwargs):
            captured.update(kwargs)
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        with self.assertRaisesRegex(video.VideoError, "timeout"):
            render_video(
                self.recipe,
                self.app,
                "pt-BR",
                output,
                root=self.root,
                runner=timed_out,
                remotion_bin=local_cli,
            )

        self.assertEqual(captured["timeout"], video.RENDER_TIMEOUT_SECONDS)
        self.assertTrue(captured["start_new_session"])
        self.assertEqual(output.read_bytes(), b"previous")

    def test_ffprobe_audit_accepts_h264_yuv420p_and_optional_aac(self):
        audit_video = self.function("audit_video")
        path = self.root / "video.mp4"
        path.write_bytes(b"video")
        captured = {}
        probe = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "pix_fmt": "yuv420p",
                    "width": 1080,
                    "height": 1920,
                    "r_frame_rate": "30/1",
                    "color_space": "bt709",
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "sample_rate": "44100",
                    "channels": 2,
                    "channel_layout": "stereo",
                },
            ],
            "format": {"duration": "15.033"},
        }

        def runner(command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(probe), stderr="")

        result = audit_video(
            path,
            expected_format="story",
            expected_duration_seconds=15,
            runner=runner,
        )

        self.assertEqual(result["errors"], [])
        self.assertEqual(captured["command"][0], "ffprobe")
        self.assertEqual(captured["command"][-1], str(path.resolve()))
        self.assertFalse(captured["kwargs"].get("shell", False))

    def test_ffprobe_audit_rejects_wrong_video_contract(self):
        audit_video = self.function("audit_video")
        path = self.root / "video.mp4"
        path.write_bytes(b"video")
        probe = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "hevc",
                    "pix_fmt": "yuv444p",
                    "width": 720,
                    "height": 1280,
                    "r_frame_rate": "24/1",
                    "color_space": "bt470bg",
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "sample_rate": "44100",
                    "channels": 1,
                    "channel_layout": "mono",
                }
            ],
            "format": {"duration": "13.0"},
        }

        result = audit_video(
            path,
            expected_format="story",
            expected_duration_seconds=15,
            runner=lambda command, **kwargs: subprocess.CompletedProcess(
                command, 0, stdout=json.dumps(probe), stderr=""
            ),
        )

        joined = " ".join(result["errors"])
        self.assertIn("h264", joined)
        self.assertIn("yuv420p", joined)
        self.assertIn("1080x1920", joined)
        self.assertIn("30 fps", joined)
        self.assertIn("bt709", joined)
        self.assertIn("estéreo", joined)
        self.assertIn("duração", joined)

    def test_remotion_sources_are_props_driven_and_audio_optional(self):
        paths = [
            ROOT / "remotion" / "src" / "index.ts",
            ROOT / "remotion" / "src" / "Root.tsx",
            ROOT / "remotion" / "src" / "CreativeVideo.tsx",
            ROOT / "remotion" / "src" / "types.ts",
        ]
        for path in paths:
            self.assertTrue(path.is_file(), f"fonte Remotion ausente: {path.name}")
        combined = "\n".join(path.read_text() for path in paths)

        for contract in (
            "registerRoot",
            "Composition",
            "calculateMetadata",
            "Sequence",
            "staticFile",
            "Html5Audio",
            "props.scenes",
            "props.captions",
            "props.audioTracks",
        ):
            self.assertIn(contract, combined)

    def test_real_sunrise_demo_video_recipe_passes(self):
        recipe_path = ROOT / "recipes" / "sunrise-demo" / "video" / "morning-ritual.yaml"
        self.assertTrue(recipe_path.is_file(), "recipe Sunrise Walks de vídeo ausente")
        load_yaml = self.function("load_yaml")
        audit_recipe = self.function("audit_recipe")
        recipe = load_yaml(recipe_path)
        app = load_yaml(ROOT / "apps" / "sunrise-demo.yaml")

        result = audit_recipe(recipe, app, root=ROOT)

        self.assertEqual(result["errors"], [])


if __name__ == "__main__":
    unittest.main()
