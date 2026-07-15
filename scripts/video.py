#!/usr/bin/env python3
"""Validate and render agent-authored Remotion recipes without creative heuristics."""

import argparse
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from urllib.parse import urlparse

try:
    from scripts import assets as asset_registry, briefs, research, render as market_render, video_mining
except ImportError:
    import assets as asset_registry
    import briefs
    import render as market_render
    import research
    import video_mining


try:
    from scripts.paths import default_root
except ImportError:
    from paths import default_root

ROOT = default_root()
FPS = 30
SAMPLE_RATE = 44_100
RENDER_TIMEOUT_SECONDS = 20 * 60
FORMATS = {
    "story": (1080, 1920),
    "portrait": (1080, 1350),
    "square": (1080, 1080),
}
LAYOUTS = {"center", "bottom", "split", "full-bleed"}
ENTERS = {"cut", "fade", "rise"}
ASSET_KINDS = {"image", "video"}
AUDIO_STRATEGIES = {
    "intentional_silence",
    "licensed_music",
    "voiceover",
    "music_and_voiceover",
}
SRT_TIME_RE = re.compile(
    r"^(?P<sh>\d{2}):(?P<sm>\d{2}):(?P<ss>\d{2})[,.](?P<sms>\d{3})"
    r"\s+-->\s+"
    r"(?P<eh>\d{2}):(?P<em>\d{2}):(?P<es>\d{2})[,.](?P<ems>\d{3})$"
)


class VideoError(ValueError):
    """A fail-closed video contract or local render error."""


def load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - repository dependency
        raise VideoError("PyYAML ausente; instale requirements.txt") from exc
    try:
        with Path(path).open() as handle:
            data = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise VideoError(f"YAML inválido ou inacessível: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise VideoError(f"YAML precisa conter um objeto: {path}")
    return data


def merge_research_registries(
    patterns: dict,
    competitors: dict,
) -> tuple[dict[str, dict], list[str]]:
    """Merge complementary records without allowing identity truth to drift."""
    pattern_by_id = {
        item.get("id"): item
        for item in patterns.get("patterns", []) or []
        if isinstance(item, dict) and item.get("id")
    }
    competitor_by_id = {
        item.get("id"): item
        for item in competitors.get("creatives", []) or []
        if isinstance(item, dict) and item.get("id")
    }
    errors = []
    for record_id in sorted(set(pattern_by_id) & set(competitor_by_id)):
        pattern = pattern_by_id[record_id]
        competitor = competitor_by_id[record_id]
        for field in ("source_url", "lineage", "evidence_level"):
            left, right = pattern.get(field), competitor.get(field)
            if left not in (None, "") and right not in (None, "") and left != right:
                errors.append(
                    f"research registry conflict {record_id}.{field}: "
                    f"video-patterns={left!r} competitors={right!r}"
                )
    merged = {key: dict(value) for key, value in competitor_by_id.items()}
    for key, value in pattern_by_id.items():
        merged[key] = {**merged.get(key, {}), **value}
    return merged, errors


def canonical_digest(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
    ).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _whole_frame(seconds: float) -> int | None:
    frames = seconds * FPS
    rounded = round(frames)
    return rounded if abs(frames - rounded) <= 1e-6 else None


def _srt_seconds(hours: str, minutes: str, seconds: str, millis: str) -> float:
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def parse_srt(path: Path, *, duration_seconds: float) -> list[dict]:
    """Parse localized captions into inert, frame-addressed render props."""
    caption_path = Path(path)
    try:
        raw = caption_path.read_text(encoding="utf-8-sig").strip()
    except OSError as exc:
        raise VideoError(f"captions inacessíveis: {caption_path}: {exc}") from exc
    if not raw:
        raise VideoError(f"captions vazias: {caption_path}")
    cues = []
    previous_end = 0
    for index, block in enumerate(re.split(r"\r?\n\s*\r?\n", raw), start=1):
        lines = [line.rstrip() for line in block.splitlines()]
        if len(lines) < 2:
            raise VideoError(f"captions bloco {index} incompleto: {caption_path}")
        timing_index = 1 if lines[0].strip().isdigit() else 0
        if timing_index >= len(lines):
            raise VideoError(f"captions bloco {index} sem timing")
        match = SRT_TIME_RE.fullmatch(lines[timing_index].strip())
        if not match:
            raise VideoError(f"captions bloco {index} com timing inválido")
        values = match.groupdict()
        start = _srt_seconds(
            values["sh"], values["sm"], values["ss"], values["sms"]
        )
        end = _srt_seconds(
            values["eh"], values["em"], values["es"], values["ems"]
        )
        start_frame = round(start * FPS)
        end_frame = round(end * FPS)
        if start < 0 or end <= start or end > duration_seconds + 1e-6:
            raise VideoError(f"captions bloco {index} fora da duração do vídeo")
        if start_frame < previous_end:
            raise VideoError(f"captions bloco {index} sobrepõe o cue anterior")
        if end_frame <= start_frame:
            raise VideoError(f"captions bloco {index} ocupa menos de 1 frame")
        text = "\n".join(lines[timing_index + 1 :]).strip()
        if not text:
            raise VideoError(f"captions bloco {index} sem texto")
        cues.append(
            {
                "startFrame": start_frame,
                "durationInFrames": end_frame - start_frame,
                "text": text,
            }
        )
        previous_end = end_frame
    return cues


def _template_meta(recipe: dict, root: Path) -> tuple[dict, Path]:
    template = str(recipe.get("template") or "")
    path = root / "templates" / "video" / template / "meta.yaml"
    if not template or not path.is_file():
        return {}, path
    return load_yaml(path), path


def _workspace_asset(value: str, root: Path) -> tuple[Path | None, str | None]:
    if not isinstance(value, str) or not value.strip():
        return None, None
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return None, None
    root = root.resolve()
    raw = Path(value).expanduser()
    candidate = raw if raw.is_absolute() else root / raw
    candidate = candidate.resolve(strict=False)
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return None, None
    return candidate, relative.as_posix()


def _app_market(app: dict, locale: str) -> dict | None:
    markets = (app.get("locales", {}) or {}).get("markets", []) or []
    return next(
        (
            market
            for market in markets
            if isinstance(market, dict)
            and locale in (market.get("id"), market.get("storefront_locale"))
        ),
        None,
    )


def target_markets(recipe: dict, app: dict) -> list[dict]:
    """Canonical app-market records for only this video's declared targets."""
    return market_render.recipe_target_markets(app, recipe)


def localized_for_market(recipe: dict, app: dict, selector: str) -> tuple[dict, dict]:
    """Return (market, localized block), applying an explicit market-ID override."""
    markets = target_markets(recipe, app)
    market = next(
        (
            item
            for item in markets
            if selector in (item["id"], item["locale"])
        ),
        None,
    )
    if market is None:
        raise VideoError(f"market/locale não pertence a target_markets: {selector}")
    locales = recipe.get("locales", {}) or {}
    localized = locales.get(market["locale"])
    if not isinstance(localized, dict):
        raise VideoError(f"locale ausente na recipe: {market['locale']}")
    overrides = recipe.get("market_overrides", {}) or {}
    if not isinstance(overrides, dict):
        raise VideoError("recipe.market_overrides precisa ser um objeto")
    override = overrides.get(market["id"])
    if override is None:
        return market, dict(localized)
    if not isinstance(override, dict):
        raise VideoError(f"market_overrides.{market['id']} precisa ser um objeto")
    return market, {**localized, **override}


def _audit_timeline(
    locale: str,
    scenes,
    copy: dict,
    assets: dict,
    duration_seconds: float,
) -> list[str]:
    errors = []
    if not isinstance(scenes, list) or not scenes:
        return [f"[{locale}] timeline sem cenas"]
    cursor_frames = 0
    seen_ids = set()
    for index, scene in enumerate(scenes):
        tag = f"[{locale}/scene {index}]"
        if not isinstance(scene, dict):
            errors.append(f"{tag} cena inválida")
            continue
        scene_id = scene.get("id")
        if not isinstance(scene_id, str) or not scene_id:
            errors.append(f"{tag} sem id")
        elif scene_id in seen_ids:
            errors.append(f"{tag} id duplicado: {scene_id}")
        else:
            seen_ids.add(scene_id)
        start = _number(scene.get("start_seconds"))
        duration = _number(scene.get("duration_seconds"))
        if start is None or duration is None or duration <= 0:
            errors.append(f"{tag} timeline precisa de start/duration positivos")
            continue
        start_frames = _whole_frame(start)
        duration_frames = _whole_frame(duration)
        if start_frames is None or duration_frames is None:
            errors.append(
                f"{tag} start/duration precisam alinhar exatamente a frames de {FPS} fps"
            )
            continue
        if duration_frames < 1:
            errors.append(f"{tag} duração precisa ocupar ao menos 1 frame")
            continue
        if start_frames != cursor_frames:
            relation = "sobrepõe" if start_frames < cursor_frames else "deixa lacuna em"
            errors.append(
                f"{tag} timeline {relation} frame {cursor_frames} "
                f"(começa no frame {start_frames})"
            )
        cursor_frames = start_frames + duration_frames
        layout = scene.get("layout")
        if layout not in LAYOUTS:
            errors.append(f"{tag} layout não suportado: {layout}")
        enter = scene.get("enter", "cut")
        if enter not in ENTERS:
            errors.append(f"{tag} entrada não suportada: {enter}")
        cta_foreground = scene.get("cta_foreground")
        if cta_foreground is not None and (
            not isinstance(cta_foreground, str) or not cta_foreground.strip()
        ):
            errors.append(f"{tag} cta_foreground inválido")
        asset = scene.get("asset")
        if asset is not None and asset not in assets:
            errors.append(f"{tag} asset desconhecido: {asset}")
        copy_refs = scene.get("copy", {}) or {}
        if not isinstance(copy_refs, dict):
            errors.append(f"{tag} copy precisa mapear slot para chave localizada")
        else:
            for slot, key in copy_refs.items():
                if not isinstance(key, str) or key not in copy:
                    errors.append(
                        f"{tag} copy.{slot} referencia chave localizada ausente: {key}"
                    )
    expected_frames = _whole_frame(duration_seconds)
    if expected_frames is None:
        errors.append(
            f"[{locale}] duration_seconds precisa alinhar a frames de {FPS} fps"
        )
    elif cursor_frames != expected_frames:
        errors.append(
            f"[{locale}] timeline termina no frame {cursor_frames}, "
            f"esperado {expected_frames}"
        )
    return errors


def audit_recipe(
    recipe: dict,
    app: dict,
    *,
    root: Path = ROOT,
    expected_app: str | None = None,
) -> dict:
    """Validate only objective render, evidence, policy, and timing contracts."""
    root = Path(root).resolve()
    errors, warnings = [], []
    app_slug = app.get("slug")
    if not isinstance(app_slug, str) or not app_slug:
        errors.append("app.slug ausente")
    if expected_app is not None and app_slug != expected_app:
        errors.append(
            f"app.slug '{app_slug}' diverge do app solicitado '{expected_app}'"
        )
    if recipe.get("version") != 1:
        errors.append("recipe.version precisa ser 1")
    if recipe.get("media_type") != "video":
        errors.append("recipe precisa declarar media_type: video")
    for field in ("brief_ref", "concept_id", "variant_id"):
        if not recipe.get(field):
            errors.append(f"recipe.{field} ausente")
    for field in ("target_markets", "target_platforms", "research_refs", "asset_refs"):
        value = recipe.get(field)
        if not isinstance(value, list) or not value:
            errors.append(f"recipe.{field} vazio ou ausente")
    meta, meta_path = _template_meta(recipe, root)
    if not meta:
        errors.append(f"template de vídeo inexistente: {meta_path}")
    if recipe.get("composition") != (meta.get("composition") or "CreativeVideo"):
        errors.append("composition diverge do template de vídeo")

    fps = recipe.get("fps")
    expected_fps = meta.get("fps", FPS)
    if fps != expected_fps or fps != FPS:
        errors.append("recipe de vídeo precisa usar 30 fps")
    concurrency = recipe.get("concurrency")
    max_concurrency = meta.get("max_concurrency", 1)
    if concurrency != 1 or concurrency != max_concurrency:
        errors.append("concorrência local precisa ser exatamente 1")
    duration = _number(recipe.get("duration_seconds"))
    if duration is None or duration <= 0:
        errors.append("duration_seconds precisa ser positivo")
        duration = 0.0
    elif _whole_frame(duration) is None:
        errors.append(f"duration_seconds precisa alinhar a frames de {FPS} fps")

    formats = meta.get("formats", {}) or {}
    video_format = recipe.get("format")
    format_meta = formats.get(video_format) if isinstance(formats, dict) else None
    if video_format not in FORMATS or not isinstance(format_meta, dict):
        errors.append(f"formato de vídeo não suportado: {video_format}")
        format_meta = {}
    elif (
        format_meta.get("width"),
        format_meta.get("height"),
    ) != FORMATS[video_format]:
        errors.append(f"dimensões do formato {video_format} divergem do contrato")
    safe_zones = recipe.get("safe_zones", {}) or {}
    minimum_zones = format_meta.get("safe_zones", {}) or {}
    for edge in ("top", "bottom"):
        actual = _number(safe_zones.get(edge))
        minimum = _number(minimum_zones.get(edge))
        if (
            actual is None
            or minimum is None
            or actual < minimum
            or actual >= 0.5
        ):
            errors.append(
                f"safe zone {edge} precisa ser >= {minimum if minimum is not None else '?'} e < 0.5"
            )

    claims = app.get("claims", {}) or {}
    for claim in recipe.get("claims_used", []) or []:
        claim_config = claims.get(claim) if isinstance(claims, dict) else None
        if not isinstance(claim_config, dict) or not claim_config.get("evidence"):
            errors.append(f"claim desconhecida ou sem evidência: {claim}")

    references = recipe.get("references", []) or []
    if not isinstance(references, list):
        errors.append("recipe.references precisa ser uma lista")
        references = []
    seen_reference_ids = set()
    for index, reference in enumerate(references):
        if not isinstance(reference, dict):
            errors.append(f"referência {index} inválida")
            continue
        if reference.get("media_type") != "video":
            errors.append(f"referência {index} precisa ser vídeo")
        reference_id = reference.get("id")
        if not reference_id:
            errors.append(f"referência {index} sem id")
        elif reference_id in seen_reference_ids:
            errors.append(f"referência de vídeo duplicada: {reference_id}")
        seen_reference_ids.add(reference_id)
        if reference.get("usage") != "structural_reference_only":
            errors.append(
                f"referência {index} precisa declarar usage: structural_reference_only"
            )
        source_url = reference.get("source_url")
        if not research.is_valid_http_url(str(source_url or "")):
            errors.append(f"referência {index} sem source_url HTTP(S) válida")
    reference_ids = {
        reference.get("id")
        for reference in references
        if isinstance(reference, dict) and reference.get("id")
    }
    research_refs = set(recipe.get("research_refs", []) or [])
    execution_ref = recipe.get("execution_ref")
    if execution_ref and execution_ref not in research_refs:
        errors.append("recipe.execution_ref precisa estar em research_refs")
    if execution_ref and reference_ids != {execution_ref}:
        errors.append("recipe.references precisa cobrir exatamente execution_ref")
    if not execution_ref and reference_ids:
        errors.append("recipe original não deve declarar referências estruturais")

    patterns_path = root / "swipe" / str(app_slug) / "video-patterns.yaml"
    patterns = {"patterns": []}
    if not patterns_path.is_file():
        if execution_ref or reference_ids:
            errors.append(f"video patterns obrigatório e ausente: {patterns_path}")
    else:
        patterns = load_yaml(patterns_path)
        pattern_audit = video_mining.audit_video_patterns(
            patterns,
            expected_app=app_slug,
            root=root,
        )
        errors.extend(
            f"video patterns: {error}" for error in pattern_audit.get("errors", [])
        )
        warnings.extend(
            f"video patterns: {warning}"
            for warning in pattern_audit.get("warnings", [])
        )
        known_patterns = {
            item.get("id"): item
            for item in patterns.get("patterns", []) or []
            if isinstance(item, dict) and item.get("id")
        }
        references_by_id = {
            item.get("id"): item
            for item in references
            if isinstance(item, dict) and item.get("id")
        }
        if execution_ref and execution_ref not in known_patterns:
            errors.append(
                f"execution_ref audiovisual inexistente em video patterns: {execution_ref}"
            )
        for ref in sorted(reference_ids & set(known_patterns)):
            inline = references_by_id.get(ref) or {}
            canonical = known_patterns[ref]
            if inline.get("source_url") != canonical.get("source_url"):
                errors.append(
                    f"referência {ref} source_url diverge do video pattern canônico"
                )

    assets = recipe.get("assets", {}) or {}
    if not isinstance(assets, dict):
        errors.append("assets precisa ser um objeto de paths locais")
        assets = {}
    for name, asset in assets.items():
        if not isinstance(asset, dict):
            errors.append(f"asset {name} inválido")
            continue
        if asset.get("kind") not in ASSET_KINDS:
            errors.append(f"asset {name} com kind inválido: {asset.get('kind')}")
        if not asset.get("asset_ref"):
            errors.append(f"asset {name} sem asset_ref de direitos")
        path, _ = _workspace_asset(asset.get("path"), root)
        if path is None:
            errors.append(f"asset {name} precisa usar path local dentro do workspace")
        elif not path.is_file():
            errors.append(f"asset {name} ausente: {path}")
    audio = recipe.get("audio")
    declared_asset_refs = set(recipe.get("asset_refs", []) or [])
    inline_asset_refs = {
        item.get("asset_ref")
        for item in assets.values()
        if isinstance(item, dict) and item.get("asset_ref")
    }
    if isinstance(audio, dict):
        audio_ref = audio.get("asset_ref")
        if not audio_ref:
            errors.append("audio.asset_ref de direitos ausente")
        else:
            inline_asset_refs.add(audio_ref)
    registry_path = root / "assets" / str(app_slug) / "registry.yaml"
    registry_by_id = {}
    if not registry_path.is_file():
        errors.append(f"asset registry obrigatório e ausente: {registry_path}")
    else:
        registry = asset_registry.load_yaml(registry_path)
        registry_audit = asset_registry.audit_registry(
            registry, expected_app=app.get("slug"), root=root
        )
        errors.extend(f"asset registry: {error}" for error in registry_audit["errors"])
        errors.extend(asset_registry.recipe_asset_errors(recipe, registry, "video"))
        registry_by_id = {
            item.get("id"): item for item in registry.get("assets", []) or []
        }
        for name, asset in assets.items():
            if not isinstance(asset, dict):
                continue
            registered = registry_by_id.get(asset.get("asset_ref")) or {}
            registered_path = registered.get("path")
            if registered_path and asset.get("path") != registered_path:
                errors.append(
                    f"asset {name} path diverge do asset registry "
                    f"{asset.get('asset_ref')}"
                )
        if isinstance(audio, dict) and audio.get("asset_ref"):
            registered = registry_by_id.get(audio["asset_ref"]) or {}
            if registered.get("path") and audio.get("path") != registered.get("path"):
                errors.append(
                    f"audio path diverge do asset registry {audio['asset_ref']}"
                )

    audio_strategy = recipe.get("audio_strategy")
    if audio_strategy not in AUDIO_STRATEGIES:
        errors.append(f"audio_strategy inválida ou ausente: {audio_strategy}")
    if audio is not None:
        if not isinstance(audio, dict):
            errors.append("audio precisa ser um objeto ou null")
        else:
            path, _ = _workspace_asset(audio.get("path"), root)
            if path is None or not path.is_file():
                errors.append("audio precisa apontar para arquivo local existente")
            volume = _number(audio.get("volume", 1))
            if volume is None or not 0 <= volume <= 1:
                errors.append("audio.volume precisa estar entre 0 e 1")
            if audio_strategy not in {"licensed_music", "music_and_voiceover"}:
                errors.append(
                    f"audio global só é permitido como música; "
                    f"audio_strategy {audio_strategy} exige outra estrutura"
                )
    else:
        if audio_strategy in {"licensed_music", "music_and_voiceover"}:
            errors.append(
                f"audio_strategy {audio_strategy} exige música em recipe.audio"
            )
        if audio_strategy == "intentional_silence":
            warnings.append("recipe sem áudio: render permanece mute-safe")
    if audio_strategy != "intentional_silence" and recipe.get("muted") is True:
        errors.append(f"audio_strategy {audio_strategy} não pode usar muted: true")

    locales = recipe.get("locales")
    if not isinstance(locales, dict) or not locales:
        errors.append("recipe sem locales localizados")
        locales = {}
    approved_ctas = (app.get("voice", {}) or {}).get("approved_ctas", {}) or {}
    app_markets = {
        market.get("id"): market
        for market in (app.get("locales", {}) or {}).get("markets", []) or []
        if isinstance(market, dict) and market.get("id")
    }
    target_markets = recipe.get("target_markets", []) or []
    if len(target_markets) != len(set(target_markets)):
        errors.append("recipe.target_markets contém duplicatas")
    unknown_markets = sorted(set(target_markets) - set(app_markets))
    if unknown_markets:
        errors.append(
            "recipe.target_markets inexistentes no app: " + ", ".join(unknown_markets)
        )
    try:
        selected_markets = market_render.recipe_target_markets(app, recipe)
    except ValueError as exc:
        errors.append(str(exc))
        selected_markets = []
    expected_locales = {market["locale"] for market in selected_markets}
    if isinstance(locales, dict) and set(locales) != expected_locales:
        errors.append(
            f"recipe.target_markets exigem locales {sorted(expected_locales)}, "
            f"recebido {sorted(locales)}"
        )
    overrides = recipe.get("market_overrides", {}) or {}
    if not isinstance(overrides, dict):
        errors.append("recipe.market_overrides precisa ser um objeto")
        overrides = {}
    target_ids = {market["id"] for market in selected_markets}
    for market_id in sorted(set(overrides) - target_ids):
        errors.append(
            f"market_overrides.{market_id} não pertence a target_markets"
        )
    for market_id in sorted(
        market_render.required_market_override_ids(selected_markets)
    ):
        override = overrides.get(market_id)
        if not isinstance(override, dict):
            errors.append(
                f"market_overrides.{market_id} obrigatório: copy_language "
                "compartilhado por mais de um target market"
            )
        else:
            for field in ("copy", "ad_copy"):
                if not isinstance(override.get(field), dict) or not override[field]:
                    errors.append(f"market_overrides.{market_id}.{field} ausente")

    voice = app.get("voice", {}) or {}
    banned_config = voice.get("banned", []) or []
    for market in selected_markets:
        locale = market["locale"]
        try:
            _, localized = localized_for_market(recipe, app, market["id"])
        except VideoError as exc:
            errors.append(str(exc))
            continue
        tag = f"[{market['id']}/{locale}]"
        if not isinstance(localized, dict):
            errors.append(f"{tag} locale inválido")
            continue
        copy_language = localized.get("copy_language")
        if market.get("copy_language") != copy_language:
            errors.append(f"{tag} copy_language diverge do market do app")
        copy = localized.get("copy")
        if not isinstance(copy, dict) or not copy:
            errors.append(f"{tag} copy localizada ausente")
            copy = {}
        ad_copy = localized.get("ad_copy")
        if not isinstance(ad_copy, dict):
            errors.append(f"{tag} ad_copy localizada ausente")
            ad_copy = {}
        for field in ("primary_text", "headline"):
            if not str(ad_copy.get(field) or "").strip():
                errors.append(f"{tag} ad_copy.{field} ausente")
        if isinstance(banned_config, dict):
            banned = [
                *(banned_config.get("global", []) or []),
                *(banned_config.get(copy_language, []) or []),
            ]
        else:
            banned = banned_config
        copy_blob = " ".join(
            str(value) for value in [*copy.values(), *ad_copy.values()]
        ).casefold()
        for term in banned:
            normalized = str(term).strip().casefold()
            if normalized and normalized in copy_blob:
                errors.append(f"{tag} usa termo proibido '{normalized}'")
        cta = copy.get("cta")
        language_ctas = (
            approved_ctas.get(copy_language, [])
            if isinstance(approved_ctas, dict)
            else approved_ctas
        ) or []
        if not language_ctas:
            errors.append(f"{tag} sem política de CTA para {copy_language}")
        elif cta not in language_ctas:
            errors.append(f"{tag} CTA '{cta}' fora da política aprovada")
        errors.extend(
            _audit_timeline(
                locale,
                localized.get("scenes"),
                copy,
                assets,
                duration,
            )
        )
        voiceover = localized.get("voiceover")
        needs_voiceover = audio_strategy in {"voiceover", "music_and_voiceover"}
        if needs_voiceover and not isinstance(voiceover, dict):
            errors.append(f"{tag} audio_strategy {audio_strategy} exige voiceover localizada")
        elif not needs_voiceover and voiceover is not None:
            errors.append(f"{tag} voiceover só é permitida em estratégia com voiceover")
        if isinstance(voiceover, dict):
            voice_ref = voiceover.get("asset_ref")
            if not voice_ref:
                errors.append(f"{tag} voiceover.asset_ref ausente")
            else:
                inline_asset_refs.add(voice_ref)
            voice_path, _ = _workspace_asset(voiceover.get("path"), root)
            if voice_path is None or not voice_path.is_file():
                errors.append(f"{tag} voiceover precisa apontar para arquivo local existente")
            volume = _number(voiceover.get("volume", 1))
            if volume is None or not 0 <= volume <= 1:
                errors.append(f"{tag} voiceover.volume precisa estar entre 0 e 1")
            registered = registry_by_id.get(voice_ref) or {}
            if registered.get("path") and voiceover.get("path") != registered.get("path"):
                errors.append(f"{tag} voiceover path diverge do asset registry {voice_ref}")

        captions_value = localized.get("captions_path")
        if needs_voiceover:
            captions_path, _ = _workspace_asset(captions_value, root)
            if captions_path is None or not captions_path.is_file():
                errors.append(f"{tag} voiceover exige captions_path local por locale")
            else:
                try:
                    parse_srt(captions_path, duration_seconds=duration)
                except VideoError as exc:
                    errors.append(f"{tag} {exc}")
        elif captions_value is not None:
            errors.append(f"{tag} captions_path exige audio_strategy com voiceover")

    if declared_asset_refs != inline_asset_refs:
        errors.append("recipe.asset_refs diverge dos assets usados nas cenas/áudio")

    brief_path = (
        root / "briefs" / str(app_slug) / f"{recipe.get('brief_ref')}.yaml"
    )
    if not brief_path.is_file():
        errors.append(f"brief_ref de vídeo inexistente: {recipe.get('brief_ref')}")
    else:
        brief = briefs.load_yaml(brief_path)
        competitor_path = root / "swipe" / str(app_slug) / "competitors.yaml"
        competitor_data = {}
        if competitor_path.is_file():
            competitor_data = load_yaml(competitor_path)
        research_by_id, registry_errors = merge_research_registries(
            patterns,
            competitor_data,
        )
        errors.extend(registry_errors)
        for ref in sorted(research_refs - set(research_by_id)):
            errors.append(f"research_ref de vídeo inexistente: {ref}")
        brief_audit = briefs.audit_brief(
            brief,
            expected_app=app_slug,
            research_by_id=research_by_id,
            supported_markets=set(app_markets),
        )
        errors.extend(f"brief: {error}" for error in brief_audit["errors"])
        errors.extend(
            briefs.recipe_binding_errors(
                recipe,
                brief,
                "video",
                research_by_id=research_by_id,
            )
        )

    return {
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "warnings": warnings,
    }


def select_locales(
    recipe: dict,
    app: dict | None = None,
    *,
    locale: str | None = None,
    all_markets: bool = False,
) -> list[str]:
    """Exploration selects one market; scaling requires explicit all_markets."""
    if bool(locale) == bool(all_markets):
        raise VideoError("informe --locale ou --all-markets, exclusivamente")
    locales = recipe.get("locales", {}) or {}
    if not isinstance(locales, dict) or not locales:
        raise VideoError("recipe sem locales")
    if app is not None:
        markets = target_markets(recipe, app)
        if all_markets:
            return [market["locale"] for market in markets]
        match = next(
            (
                market
                for market in markets
                if locale in (market["id"], market["locale"])
            ),
            None,
        )
        if match is None:
            raise VideoError(f"market/locale não existe na recipe: {locale}")
        return [match["locale"]]
    if all_markets:
        return list(locales)
    if locale not in locales:
        raise VideoError(f"locale não existe na recipe: {locale}")
    return [str(locale)]


def build_props(
    recipe: dict,
    app: dict,
    locale: str,
    *,
    root: Path = ROOT,
) -> dict:
    result = audit_recipe(recipe, app, root=root)
    if result["errors"]:
        raise VideoError("recipe inválida: " + "; ".join(result["errors"]))
    select_locales(recipe, app, locale=locale)
    root = Path(root).resolve()
    meta, _ = _template_meta(recipe, root)
    format_meta = meta["formats"][recipe["format"]]
    width, height = FORMATS[recipe["format"]]
    market, localized = localized_for_market(recipe, app, locale)
    canonical_locale = market["locale"]
    copy = localized["copy"]
    fps = FPS

    assets = {}
    for name, asset in (recipe.get("assets", {}) or {}).items():
        _, relative = _workspace_asset(asset["path"], root)
        assets[name] = {
            "kind": asset["kind"],
            "path": relative,
            "fit": asset.get("fit", "contain"),
        }
    scenes = []
    for scene in localized["scenes"]:
        scenes.append(
            {
                "id": scene["id"],
                "startFrame": round(float(scene["start_seconds"]) * fps),
                "durationInFrames": round(float(scene["duration_seconds"]) * fps),
                "layout": scene["layout"],
                "background": scene.get("background")
                or (app.get("palette", {}) or {}).get("bg_top", "#111111"),
                "foreground": scene.get("foreground")
                or (app.get("palette", {}) or {}).get("ink", "#ffffff"),
                "accent": scene.get("accent")
                or (app.get("palette", {}) or {}).get("accent", "#ffffff"),
                "ctaForeground": scene.get("cta_foreground"),
                "enter": scene.get("enter", "cut"),
                "asset": scene.get("asset"),
                "text": {
                    slot: copy[key]
                    for slot, key in (scene.get("copy", {}) or {}).items()
                },
            }
        )
    safe_zones = recipe["safe_zones"]
    audio_tracks = []
    audio = recipe.get("audio")
    if audio:
        _, relative = _workspace_asset(audio["path"], root)
        audio_tracks.append(
            {
                "kind": "music",
                "path": relative,
                "volume": float(audio.get("volume", 1)),
            }
        )
    voiceover = localized.get("voiceover")
    if isinstance(voiceover, dict):
        _, relative = _workspace_asset(voiceover["path"], root)
        audio_tracks.append(
            {
                "kind": "voiceover",
                "path": relative,
                "volume": float(voiceover.get("volume", 1)),
            }
        )
    captions = []
    captions_value = localized.get("captions_path")
    if captions_value:
        captions_path, _ = _workspace_asset(captions_value, root)
        captions = parse_srt(
            captions_path,
            duration_seconds=float(recipe["duration_seconds"]),
        )
    palette = app.get("palette", {}) or {}
    return {
        "app": {"slug": app.get("slug"), "name": app.get("name")},
        "brand": {
            "palette": palette,
            "fonts": app.get("fonts", {}) or {},
        },
        "locale": canonical_locale,
        "marketId": market["id"],
        "copyLanguage": localized["copy_language"],
        "format": recipe["format"],
        "width": width,
        "height": height,
        "fps": fps,
        "durationInFrames": round(float(recipe["duration_seconds"]) * fps),
        "safeZones": {
            "topRatio": float(safe_zones["top"]),
            "bottomRatio": float(safe_zones["bottom"]),
            "topPixels": round(height * float(safe_zones["top"])),
            "bottomPixels": round(height * float(safe_zones["bottom"])),
            "minimum": format_meta.get("safe_zones", {}),
        },
        "assets": assets,
        "audioTracks": audio_tracks,
        "captions": captions,
        "muted": bool(recipe.get("muted", False)),
        "scenes": scenes,
    }


def build_render_command(
    props_path: Path,
    output_path: Path,
    *,
    root: Path = ROOT,
    remotion_bin: Path | None = None,
) -> list[str]:
    root = Path(os.path.abspath(Path(root).expanduser()))
    remotion_bin = remotion_bin or (
        root / "remotion" / "node_modules" / ".bin" / "remotion"
    )
    return [
        str(remotion_bin),
        "render",
        str(root / "remotion" / "src" / "index.ts"),
        "CreativeVideo",
        str(Path(output_path).resolve()),
        "--props",
        str(Path(props_path).resolve()),
        "--public-dir",
        str(root),
        "--codec",
        "h264",
        "--audio-codec",
        "aac",
        "--pixel-format",
        "yuv420p",
        "--fps",
        str(FPS),
        "--concurrency",
        "1",
        "--color-space",
        "bt709",
        "--sample-rate",
        str(SAMPLE_RATE),
        "--overwrite",
    ]


def render_contract() -> dict:
    return {
        "composition": "CreativeVideo",
        "codec": "h264",
        "audio_codec": "aac",
        "pixel_format": "yuv420p",
        "fps": FPS,
        "concurrency": 1,
        "color_space": "bt709",
        "sample_rate": SAMPLE_RATE,
    }


def renderer_source_hashes(root: Path) -> dict[str, str]:
    candidates = [
        Path(__file__).resolve(),
        root / "remotion" / "package.json",
        root / "remotion" / "package-lock.json",
        *sorted((root / "remotion" / "src").glob("*.ts*")),
    ]
    return {
        str(path.resolve()): file_sha256(path)
        for path in candidates
        if path.is_file()
    }


def build_render_receipt(
    *,
    recipe: dict,
    app: dict,
    locale: str,
    output_path: Path,
    props: dict,
    root: Path,
) -> dict:
    sources = renderer_source_hashes(root)
    receipt = {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "app": app.get("slug"),
        "brief_ref": recipe.get("brief_ref"),
        "concept_id": recipe.get("concept_id"),
        "variant_id": recipe.get("variant_id"),
        "market_id": props.get("marketId"),
        "locale": props.get("locale"),
        "format": recipe.get("format"),
        "output_path": str(output_path.resolve()),
        "output_sha256": file_sha256(output_path),
        "recipe_digest": canonical_digest(recipe),
        "app_digest": canonical_digest(app),
        "props_digest": canonical_digest(props),
        "render_contract": render_contract(),
        "renderer_sources": sources,
        "renderer_sources_digest": canonical_digest(sources),
    }
    receipt["receipt_digest"] = canonical_digest(receipt)
    return receipt


def render_receipt_errors(
    receipt: dict,
    *,
    recipe: dict,
    app: dict,
    locale: str,
    output_path: Path,
    props: dict,
    root: Path = ROOT,
) -> list[str]:
    errors = []
    output = Path(output_path).resolve()
    if receipt.get("version") != 1:
        errors.append("render receipt version inválida")
    payload = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    if receipt.get("receipt_digest") != canonical_digest(payload):
        errors.append("render receipt digest inválido")
    expected_fields = {
        "app": app.get("slug"),
        "brief_ref": recipe.get("brief_ref"),
        "concept_id": recipe.get("concept_id"),
        "variant_id": recipe.get("variant_id"),
        "market_id": props.get("marketId"),
        "locale": props.get("locale"),
        "format": recipe.get("format"),
        "output_path": str(output),
        "recipe_digest": canonical_digest(recipe),
        "app_digest": canonical_digest(app),
        "props_digest": canonical_digest(props),
        "render_contract": render_contract(),
    }
    for field, expected in expected_fields.items():
        if receipt.get(field) != expected:
            errors.append(f"render receipt {field} diverge do render atual")
    if not output.is_file():
        errors.append(f"render output ausente: {output}")
    elif receipt.get("output_sha256") != file_sha256(output):
        errors.append("render receipt output_sha256 diverge do MP4")
    current_sources = renderer_source_hashes(Path(root).resolve())
    if receipt.get("renderer_sources") != current_sources:
        errors.append("render receipt renderer_sources divergem do engine atual")
    if receipt.get("renderer_sources_digest") != canonical_digest(current_sources):
        errors.append("render receipt renderer_sources_digest inválido")
    return errors


def _write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}-",
            suffix=".partial",
            delete=False,
        ) as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            temporary = Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _safe_workspace_root(root: Path) -> Path:
    lexical_root = Path(os.path.abspath(Path(root).expanduser()))
    if lexical_root.is_symlink():
        raise VideoError(f"workspace root não pode ser symlink: {lexical_root}")
    # Preserve the caller's lexical root (for example macOS /var vs /private/var)
    # while using resolved paths below only for containment verification.
    return lexical_root


def _safe_output_path(output_path: Path, root: Path) -> Path:
    root = _safe_workspace_root(root)
    output = Path(os.path.abspath(Path(output_path).expanduser()))
    if output.suffix.lower() != ".mp4":
        raise VideoError("output precisa terminar em .mp4")
    base = root / "output"
    try:
        relative = output.relative_to(base)
    except ValueError as exc:
        raise VideoError("output precisa ficar dentro de output/") from exc
    cursor = base
    for part in (".", *relative.parts):
        if part != ".":
            cursor = cursor / part
        if cursor.is_symlink():
            raise VideoError(f"output usa symlink proibido: {cursor}")
    resolved = output.resolve(strict=False)
    try:
        resolved.relative_to(base.resolve(strict=False))
    except ValueError as exc:
        raise VideoError("output precisa ficar dentro de output/") from exc
    return output


def _run_process_group(
    command: list[str], *, cwd: Path, timeout: float
) -> subprocess.CompletedProcess:
    """Run Remotion with a bounded lifetime and terminate its browser children."""
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.communicate(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
        raise exc
    return subprocess.CompletedProcess(
        command, process.returncode, stdout=stdout, stderr=stderr
    )


def render_video(
    recipe: dict,
    app: dict,
    locale: str,
    output_path: Path,
    *,
    root: Path = ROOT,
    runner=None,
    remotion_bin: Path | None = None,
    timeout_seconds: float = RENDER_TIMEOUT_SECONDS,
) -> Path:
    """Render locally and atomically promote only a completed MP4."""
    root = _safe_workspace_root(root)
    output = _safe_output_path(output_path, root)
    local_cli = Path(remotion_bin) if remotion_bin else (
        root / "remotion" / "node_modules" / ".bin" / "remotion"
    )
    if not local_cli.is_file():
        raise VideoError(
            "Remotion local não instalado; execute npm install dentro de remotion/"
        )
    props = build_props(recipe, app, locale, root=root)
    output.parent.mkdir(parents=True, exist_ok=True)
    props_path = None
    temporary_output = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output.parent,
            prefix=f".{output.stem}-",
            suffix=".props.json",
            delete=False,
        ) as props_file:
            json.dump(props, props_file, ensure_ascii=False, separators=(",", ":"))
            props_path = Path(props_file.name)
        with tempfile.NamedTemporaryFile(
            dir=output.parent,
            prefix=f".{output.stem}-",
            suffix=".partial.mp4",
            delete=False,
        ) as temporary_file:
            temporary_output = Path(temporary_file.name)
        temporary_output.unlink()
        command = build_render_command(
            props_path,
            temporary_output,
            root=root,
            remotion_bin=local_cli,
        )
        try:
            if runner is None:
                completed = _run_process_group(
                    command, cwd=root, timeout=timeout_seconds
                )
            else:
                completed = runner(
                    command,
                    cwd=root,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=timeout_seconds,
                    start_new_session=True,
                )
        except subprocess.TimeoutExpired as exc:
            raise VideoError(
                f"render Remotion excedeu timeout de {timeout_seconds:g}s"
            ) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "sem saída").strip()
            raise VideoError(f"render Remotion falhou: {detail[:900]}")
        if not temporary_output.is_file() or temporary_output.stat().st_size == 0:
            raise VideoError("Remotion terminou sem produzir MP4 completo")
        os.replace(temporary_output, output)
        receipt = build_render_receipt(
            recipe=recipe,
            app=app,
            locale=locale,
            output_path=output,
            props=props,
            root=root,
        )
        _write_json_atomic(output.with_suffix(".render.json"), receipt)
        return output
    finally:
        if props_path is not None:
            props_path.unlink(missing_ok=True)
        if temporary_output is not None:
            temporary_output.unlink(missing_ok=True)


def audit_video(
    path: Path,
    *,
    expected_format: str,
    expected_duration_seconds: float,
    runner=None,
    timeout_seconds: float = 30,
) -> dict:
    """Use ffprobe for technical QA only; never infer creative quality."""
    errors, warnings = [], []
    video_path = Path(path).resolve()
    if not video_path.is_file():
        return {
            "status": "fail",
            "errors": [f"vídeo ausente: {video_path}"],
            "warnings": [],
            "probe": {},
        }
    if expected_format not in FORMATS:
        return {
            "status": "fail",
            "errors": [f"formato esperado inválido: {expected_format}"],
            "warnings": [],
            "probe": {},
        }
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,codec_name,pix_fmt,width,height,r_frame_rate,"
        "sample_rate,channels,channel_layout,color_space:format=duration",
        "-of",
        "json",
        str(video_path),
    ]
    try:
        if runner is None:
            completed = _run_process_group(
                command, cwd=Path.cwd(), timeout=timeout_seconds
            )
        else:
            completed = runner(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds,
                start_new_session=True,
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        errors.append(f"ffprobe indisponível: {exc}")
        return {"status": "fail", "errors": errors, "warnings": warnings, "probe": {}}
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "sem saída").strip()
        errors.append(f"ffprobe falhou: {detail[:900]}")
        return {"status": "fail", "errors": errors, "warnings": warnings, "probe": {}}
    try:
        probe = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        errors.append(f"ffprobe retornou JSON inválido: {exc}")
        return {"status": "fail", "errors": errors, "warnings": warnings, "probe": {}}
    streams = probe.get("streams", []) or []
    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"),
        None,
    )
    if video_stream is None:
        errors.append("MP4 sem stream de vídeo")
    else:
        if video_stream.get("codec_name") != "h264":
            errors.append("codec de vídeo precisa ser h264")
        if video_stream.get("pix_fmt") != "yuv420p":
            errors.append("pixel format precisa ser yuv420p")
        if video_stream.get("color_space") != "bt709":
            errors.append("color space precisa ser bt709")
        expected_size = FORMATS[expected_format]
        actual_size = (video_stream.get("width"), video_stream.get("height"))
        if actual_size != expected_size:
            errors.append(
                f"dimensões precisam ser {expected_size[0]}x{expected_size[1]}, recebido {actual_size[0]}x{actual_size[1]}"
            )
        try:
            frame_rate = float(Fraction(str(video_stream.get("r_frame_rate"))))
        except (ValueError, ZeroDivisionError):
            frame_rate = 0
        if abs(frame_rate - FPS) > 0.001:
            errors.append(f"frame rate precisa ser 30 fps, recebido {frame_rate:g}")
    audio_streams = [
        stream for stream in streams if stream.get("codec_type") == "audio"
    ]
    if not audio_streams:
        warnings.append("MP4 sem áudio; permitido por contrato mute-safe")
    for audio in audio_streams:
        if audio.get("codec_name") != "aac":
            errors.append("codec de áudio precisa ser aac")
        try:
            sample_rate = int(audio.get("sample_rate"))
        except (TypeError, ValueError):
            sample_rate = 0
        if sample_rate != SAMPLE_RATE:
            errors.append(
                f"sample rate precisa ser {SAMPLE_RATE} Hz, recebido {sample_rate}"
            )
        if audio.get("channels") != 2 or audio.get("channel_layout") != "stereo":
            errors.append("áudio precisa ser estéreo com 2 canais")
    try:
        duration = float((probe.get("format", {}) or {}).get("duration"))
    except (TypeError, ValueError):
        duration = -1
    if abs(duration - float(expected_duration_seconds)) > 0.15:
        errors.append(
            f"duração precisa ser {expected_duration_seconds:g}s (±0.15s), recebida {duration:g}s"
        )
    return {
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "warnings": warnings,
        "probe": probe,
        "command": command,
    }


def _recipe_path(app: str, name: str) -> Path:
    filename = name if name.endswith(".yaml") else f"{name}.yaml"
    path = ROOT / "recipes" / app / "video" / filename
    if not path.is_file():
        raise VideoError(f"recipe de vídeo inexistente: {path}")
    return path


def _print_audit(result: dict) -> None:
    for warning in result.get("warnings", []):
        print(f"  ⚠️  {warning}")
    for error in result.get("errors", []):
        print(f"  ❌ {error}")
    print(f"video={'PASS' if not result.get('errors') else 'BLOCKED'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="creative-forge — local Remotion video")
    sub = parser.add_subparsers(dest="command", required=True)
    recipe_parser = sub.add_parser("audit-recipe")
    recipe_parser.add_argument("--app", required=True)
    recipe_parser.add_argument("--recipe", required=True)
    render_parser = sub.add_parser("render")
    render_parser.add_argument("--app", required=True)
    render_parser.add_argument("--recipe", required=True)
    market = render_parser.add_mutually_exclusive_group(required=True)
    market.add_argument("--locale")
    market.add_argument("--all-markets", action="store_true")
    audit_parser = sub.add_parser("audit-video")
    audit_parser.add_argument("--path", required=True)
    audit_parser.add_argument("--format", required=True, choices=sorted(FORMATS))
    audit_parser.add_argument("--duration", required=True, type=float)
    args = parser.parse_args(argv)
    try:
        if args.command == "audit-video":
            result = audit_video(
                Path(args.path),
                expected_format=args.format,
                expected_duration_seconds=args.duration,
            )
            _print_audit(result)
            return 1 if result["errors"] else 0
        app = load_yaml(ROOT / "apps" / f"{args.app}.yaml")
        recipe_path = _recipe_path(args.app, args.recipe)
        recipe = load_yaml(recipe_path)
        if args.command == "audit-recipe":
            result = audit_recipe(recipe, app, expected_app=args.app)
            _print_audit(result)
            return 1 if result["errors"] else 0
        render_audit = audit_recipe(recipe, app, expected_app=args.app)
        if render_audit["errors"]:
            raise VideoError("recipe inválida: " + "; ".join(render_audit["errors"]))
        locales = select_locales(
            recipe,
            app,
            locale=args.locale,
            all_markets=args.all_markets,
        )
        for locale in locales:
            output = (
                ROOT
                / "output"
                / args.app
                / "video"
                / f"{recipe_path.stem}--{locale}--{recipe['format']}.mp4"
            )
            rendered = render_video(recipe, app, locale, output)
            print(rendered)
        return 0
    except VideoError as exc:
        print(f"creative-forge: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
