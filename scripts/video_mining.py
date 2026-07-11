#!/usr/bin/env python3
"""Validate agent-authored video patterns and inspect licensed local media.

The agent owns hook decomposition, creative judgment and cultural analysis. This
module only validates identity, source provenance, rights, file integrity and
timeline mechanics. Browser observations never acquire or reuse competitor
media; optional FFmpeg tooling is restricted to already licensed local files.
"""

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

try:
    from scripts import research
except ImportError:  # direct execution: python3 scripts/video_mining.py
    import research

ROOT = Path(__file__).resolve().parent.parent
ALLOWED_MODES = {"browser_observation", "licensed_file"}
ALLOWED_MEDIA_FORMATS = {"square", "portrait", "story", "landscape"}
LICENSED_RIGHTS_CLASSES = {
    "owned",
    "commissioned",
    "licensed",
    "generated",
    "public_domain",
}
REQUIRED_LICENSED_USES = {"analysis", "contact_sheet"}
BROWSER_FORBIDDEN_USES = {
    "commercial_asset",
    "derivative_asset",
    "media_reuse",
    "publish",
    "render",
}
BROWSER_FORBIDDEN_KEYS = {
    "asset_path",
    "contact_sheet",
    "contact_sheet_path",
    "download_path",
    "download_url",
    "downloaded_media",
    "file",
    "frame_paths",
    "frames",
    "local_path",
    "media_path",
    "media_url",
    "path",
    "sha256",
    "thumbnail_url",
}
LOCALE_RE = re.compile(
    r"^[a-z]{2,3}(?:-[A-Z][a-z]{3})?(?:-(?:[A-Z]{2}|[0-9]{3}))?$"
)
COPY_LANGUAGE_RE = re.compile(r"^[a-z]{2,3}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PLATFORM_SOURCE_DOMAINS = {
    "meta": {"facebook.com", "instagram.com", "meta.com"},
    "tiktok": {"tiktok.com"},
    "google": {"google.com", "youtube.com", "youtu.be"},
    "youtube": {"google.com", "youtube.com", "youtu.be"},
    "snapchat": {"snapchat.com"},
    "pinterest": {"pinterest.com", "pin.it"},
    "reddit": {"reddit.com"},
    "linkedin": {"linkedin.com"},
    "x": {"x.com", "twitter.com"},
}


class VideoMiningError(ValueError):
    """Raised when licensed-file inspection cannot be performed safely."""


def load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - repository dependency
        raise VideoMiningError("PyYAML ausente; instale requirements.txt") from exc
    if not path.is_file():
        raise VideoMiningError(f"video patterns inexistente: {path}")
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except Exception as exc:
        raise VideoMiningError(f"video patterns YAML inválido: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise VideoMiningError(f"video patterns precisa ser um objeto YAML: {path}")
    return data


def _lexical_absolute(value: str | Path, root: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return Path(os.path.abspath(path))


def _parse_aware_datetime(value) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timezone ausente")
    return parsed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _tag(pattern: dict, index: int) -> str:
    return str(pattern.get("id") or f"#{index}")


def _host_matches_domain(host: str, allowed_domain: str) -> bool:
    return host == allowed_domain or host.endswith(f".{allowed_domain}")


def _validate_platform_source(pattern: dict, tag: str, mode: str, errors: list):
    platform = str(pattern.get("platform") or "").strip().lower()
    if mode == "licensed_file":
        if platform != "local":
            errors.append(
                f"pattern {tag} licensed_file platform/source_url exige platform local"
            )
        return
    if mode != "browser_observation":
        return
    allowed_domains = PLATFORM_SOURCE_DOMAINS.get(platform)
    if not allowed_domains:
        errors.append(
            f"pattern {tag} platform/source_url usa platform não suportada: {platform}"
        )
        return
    try:
        parsed = urlsplit(str(pattern.get("source_url") or ""))
        host = (parsed.hostname or "").lower()
    except ValueError:
        host = ""
        parsed = None
    if (
        parsed is None
        or parsed.scheme.lower() != "https"
        or not host
        or not any(_host_matches_domain(host, domain) for domain in allowed_domains)
    ):
        errors.append(
            f"pattern {tag} platform/source_url incompatíveis: {platform} não aceita "
            f"{host or 'host ausente'}"
        )


def _forbidden_browser_fields(value, prefix=""):
    if isinstance(value, dict):
        for key, nested in value.items():
            current = f"{prefix}.{key}" if prefix else str(key)
            normalized_key = str(key).lower()
            if (
                normalized_key in BROWSER_FORBIDDEN_KEYS
                or normalized_key.endswith("_path")
                or normalized_key.endswith("_file")
            ):
                yield current
            yield from _forbidden_browser_fields(nested, current)
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            yield from _forbidden_browser_fields(nested, f"{prefix}[{index}]")


def _validate_structure(pattern: dict, tag: str, duration: float | None, errors: list):
    structure = pattern.get("structure")
    if not isinstance(structure, dict):
        errors.append(f"pattern {tag} sem structure escrita pelo agente")
        return

    complete_triplet = all(
        isinstance(structure.get(field), str) and structure[field].strip()
        for field in ("hook", "body", "cta")
    )
    timeline = structure.get("timeline")
    has_timeline = isinstance(timeline, list) and bool(timeline)
    if not complete_triplet and not has_timeline:
        errors.append(
            f"pattern {tag} precisa de hook/body/cta completos ou timeline "
            "escrita pelo agente"
        )
        return
    if timeline is None:
        return
    if not isinstance(timeline, list) or not timeline:
        errors.append(f"pattern {tag} timeline inválida")
        return

    previous_end = 0.0
    for index, beat in enumerate(timeline):
        beat_tag = f"pattern {tag} timeline[{index}]"
        if not isinstance(beat, dict):
            errors.append(f"{beat_tag} inválida")
            continue
        start = beat.get("start_seconds")
        end = beat.get("end_seconds")
        if not _is_number(start) or not _is_number(end):
            errors.append(f"{beat_tag} timestamps precisam ser numéricos")
            continue
        start, end = float(start), float(end)
        if start < 0 or end <= start:
            errors.append(f"{beat_tag} intervalo inválido: {start}..{end}")
        if start < previous_end - 1e-9:
            errors.append(f"{beat_tag} timestamps não são monotônicos")
        if duration is not None and end > duration + 1e-6:
            errors.append(
                f"{beat_tag} end_seconds {end} excede media.duration_seconds "
                f"{duration}"
            )
        if not isinstance(beat.get("beat"), str) or not beat["beat"].strip():
            errors.append(f"{beat_tag} sem beat")
        if (
            not isinstance(beat.get("derived_fact"), str)
            or not beat["derived_fact"].strip()
        ):
            errors.append(f"{beat_tag} sem derived_fact escrito pelo agente")
        previous_end = max(previous_end, end)


def _validate_browser_observation(pattern: dict, tag: str, errors: list):
    rights = pattern.get("rights")
    if not isinstance(rights, dict):
        errors.append(f"pattern {tag} browser_observation sem rights")
        return
    if rights.get("class") != "reference_only":
        errors.append(
            f"pattern {tag} browser_observation rights.class precisa ser "
            "reference_only"
        )
    if rights.get("media_reuse") is not False:
        errors.append(
            f"pattern {tag} browser_observation precisa declarar media_reuse: false"
        )
    allowed_uses = rights.get("allowed_uses")
    if not isinstance(allowed_uses, list) or "structural_analysis" not in allowed_uses:
        errors.append(
            f"pattern {tag} browser_observation permite somente structural_analysis"
        )
        allowed_uses = allowed_uses if isinstance(allowed_uses, list) else []
    forbidden_uses = sorted(BROWSER_FORBIDDEN_USES & set(allowed_uses))
    if forbidden_uses:
        errors.append(
            f"pattern {tag} browser_observation proíbe reuse/publicação da mídia: "
            + ", ".join(forbidden_uses)
        )
    forbidden_fields = sorted(set(_forbidden_browser_fields(pattern)))
    if forbidden_fields:
        errors.append(
            f"pattern {tag} browser_observation aceita apenas fatos derivados; "
            "campos de mídia local proibidos: "
            + ", ".join(forbidden_fields)
        )


def _validate_rights_evidence(
    rights: dict, tag: str, root: Path, errors: list
) -> None:
    evidence = rights.get("evidence")
    if not isinstance(evidence, dict):
        errors.append(f"pattern {tag} rights.evidence ausente ou inválida")
        return
    evidence_path = evidence.get("path")
    evidence_url = evidence.get("source_url") or evidence.get("url")
    if evidence_path:
        path = _lexical_absolute(evidence_path, root)
        if path.is_symlink() or not path.is_file():
            errors.append(f"pattern {tag} rights.evidence path inválido: {path}")
    elif evidence_url:
        if not research.is_valid_http_url(str(evidence_url)):
            errors.append(f"pattern {tag} rights.evidence URL inválida")
    else:
        errors.append(
            f"pattern {tag} rights.evidence precisa de path local ou URL HTTP(S)"
        )


def _validate_licensed_file(
    pattern: dict, tag: str, root: Path, errors: list
) -> Path | None:
    rights = pattern.get("rights")
    if not isinstance(rights, dict):
        errors.append(f"pattern {tag} licensed_file sem rights documentados")
        rights = {}
    if rights.get("class") not in LICENSED_RIGHTS_CLASSES:
        errors.append(
            f"pattern {tag} rights.class precisa ser owned, commissioned, "
            "licensed, generated ou public_domain"
        )
    if rights.get("status") != "verified":
        errors.append(f"pattern {tag} rights.status precisa ser verified")
    _validate_rights_evidence(rights, tag, root, errors)
    allowed_uses = rights.get("allowed_uses")
    if not isinstance(allowed_uses, list):
        allowed_uses = []
    missing_uses = sorted(REQUIRED_LICENSED_USES - set(allowed_uses))
    if missing_uses:
        errors.append(
            f"pattern {tag} rights.allowed_uses sem: {', '.join(missing_uses)}"
        )

    media = pattern.get("media")
    if not isinstance(media, dict):
        errors.append(f"pattern {tag} licensed_file sem media")
        return None
    path_value = media.get("path")
    if not path_value:
        errors.append(f"pattern {tag} licensed_file sem media.path local")
        return None
    if research.is_valid_http_url(str(path_value)):
        errors.append(f"pattern {tag} licensed_file media.path precisa ser local")
        return None
    path = _lexical_absolute(path_value, root)
    if path.is_symlink() or not path.is_file():
        errors.append(f"pattern {tag} licensed_file media.path inválido: {path}")
        return None
    expected_hash = str(media.get("sha256") or "").lower()
    if not SHA256_RE.fullmatch(expected_hash):
        errors.append(f"pattern {tag} media.sha256 ausente ou inválido")
    else:
        current_hash = _sha256(path)
        if current_hash != expected_hash:
            errors.append(f"pattern {tag} media.sha256 não corresponde ao arquivo")
    return path


def probe_video(
    path: str | Path,
    *,
    runner=subprocess.run,
    ffprobe_bin: str = "ffprobe",
    timeout: float = 15,
) -> dict:
    """Read technical metadata from a licensed local file without invoking a shell."""
    media_path = _lexical_absolute(path, ROOT)
    if media_path.is_symlink() or not media_path.is_file():
        raise VideoMiningError(f"arquivo local inválido para ffprobe: {media_path}")
    command = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format=duration,format_name:stream=codec_type,codec_name,width,height",
        "-of",
        "json",
        str(media_path),
    ]
    try:
        completed = runner(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VideoMiningError(f"ffprobe falhou: {exc}") from exc
    if completed.returncode != 0:
        detail = str(getattr(completed, "stderr", "") or "").strip()[:500]
        raise VideoMiningError(f"ffprobe retornou erro: {detail or completed.returncode}")
    try:
        payload = json.loads(completed.stdout)
        duration = float((payload.get("format") or {}).get("duration"))
        video_stream = next(
            stream
            for stream in payload.get("streams", []) or []
            if stream.get("codec_type") == "video"
        )
        if duration <= 0:
            raise ValueError("duration não positiva")
    except (KeyError, TypeError, ValueError, StopIteration, json.JSONDecodeError) as exc:
        raise VideoMiningError(f"ffprobe não retornou vídeo/duração válidos: {exc}") from exc
    return {
        "duration_seconds": duration,
        "format_name": (payload.get("format") or {}).get("format_name"),
        "codec": video_stream.get("codec_name"),
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
    }


def audit_video_patterns(
    data: dict,
    *,
    expected_app: str | None = None,
    now: datetime | None = None,
    root: Path = ROOT,
    probe_files: bool = False,
    probe=probe_video,
) -> dict:
    """Validate mechanical contracts while leaving creative judgment to the agent."""
    errors, warnings, probes = [], [], {}
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now precisa conter timezone")
    if not isinstance(data, dict):
        return {
            "errors": ["video patterns precisa ser um objeto"],
            "warnings": warnings,
            "probes": probes,
        }
    if data.get("version") != 1:
        errors.append("video_patterns.version precisa ser 1")
    app = data.get("app")
    if not isinstance(app, str) or not app.strip():
        errors.append("video_patterns.app ausente")
    if expected_app is not None and app != expected_app:
        errors.append(
            f"video_patterns.app '{app}' diverge do app solicitado '{expected_app}'"
        )
    try:
        observed_at = _parse_aware_datetime(data.get("observed_at"))
        expires_at = _parse_aware_datetime(data.get("expires_at"))
        if expires_at <= observed_at:
            errors.append("video_patterns expires_at precisa ser posterior a observed_at")
        if observed_at > now:
            errors.append("video_patterns observed_at não pode estar no futuro")
        if now > expires_at:
            errors.append(f"pesquisa de vídeo expirada em {expires_at.isoformat()}")
    except (TypeError, ValueError):
        errors.append(
            "video_patterns observed_at/expires_at inválidos; use ISO-8601 com timezone"
        )

    patterns = data.get("patterns")
    if not isinstance(patterns, list) or not patterns:
        errors.append("video_patterns.patterns vazio ou inválido")
        patterns = []
    seen = set()
    for index, pattern in enumerate(patterns):
        if not isinstance(pattern, dict):
            errors.append(f"pattern #{index} inválido")
            continue
        tag = _tag(pattern, index)
        error_count_before_pattern = len(errors)
        for field in (
            "id",
            "mode",
            "platform",
            "advertiser",
            "source_url",
            "lineage",
            "locale",
            "copy_language",
        ):
            if not pattern.get(field):
                errors.append(f"pattern {tag} sem {field}")
        if pattern.get("id") in seen:
            errors.append(f"pattern id duplicado: {pattern.get('id')}")
        seen.add(pattern.get("id"))

        mode = pattern.get("mode")
        if mode not in ALLOWED_MODES:
            errors.append(f"pattern {tag} mode inválido: {mode}")
        if not research.is_valid_http_url(str(pattern.get("source_url") or "")):
            errors.append(f"pattern {tag} source_url precisa ser HTTP(S) válida")
        _validate_platform_source(pattern, tag, str(mode or ""), errors)
        if pattern.get("lineage") not in research.ALLOWED_LINEAGE:
            errors.append(f"pattern {tag} lineage inválida: {pattern.get('lineage')}")

        locale = pattern.get("locale")
        copy_language = pattern.get("copy_language")
        if not isinstance(locale, str) or not LOCALE_RE.fullmatch(locale):
            errors.append(f"pattern {tag} locale inválido: {locale}")
        if (
            not isinstance(copy_language, str)
            or not COPY_LANGUAGE_RE.fullmatch(copy_language)
        ):
            errors.append(f"pattern {tag} copy_language inválido: {copy_language}")
        elif isinstance(locale, str) and LOCALE_RE.fullmatch(locale):
            if locale.split("-", 1)[0] != copy_language:
                errors.append(
                    f"pattern {tag} locale '{locale}' diverge de copy_language "
                    f"'{copy_language}'"
                )

        media = pattern.get("media")
        duration = None
        if not isinstance(media, dict):
            errors.append(f"pattern {tag} sem media")
        else:
            media_format = media.get("format")
            if media_format not in ALLOWED_MEDIA_FORMATS:
                errors.append(f"pattern {tag} media.format inválido: {media_format}")
            raw_duration = media.get("duration_seconds")
            if not _is_number(raw_duration) or float(raw_duration) <= 0:
                errors.append(f"pattern {tag} media.duration_seconds precisa ser > 0")
            else:
                duration = float(raw_duration)

        _validate_structure(pattern, tag, duration, errors)
        local_path = None
        if mode == "browser_observation":
            _validate_browser_observation(pattern, tag, errors)
        elif mode == "licensed_file":
            local_path = _validate_licensed_file(pattern, tag, root, errors)

        if (
            probe_files
            and mode == "licensed_file"
            and local_path is not None
            and len(errors) == error_count_before_pattern
        ):
            try:
                technical = probe(local_path)
                probes[tag] = technical
                probed_duration = float(technical.get("duration_seconds"))
                if duration is not None and abs(probed_duration - duration) > 0.25:
                    errors.append(
                        f"pattern {tag} media.duration_seconds {duration} diverge da "
                        f"duration do ffprobe {probed_duration}"
                    )
            except (OSError, TypeError, ValueError, VideoMiningError) as exc:
                errors.append(f"pattern {tag} ffprobe falhou: {exc}")

    return {"errors": errors, "warnings": warnings, "probes": probes}


def derive_contact_sheet(
    pattern: dict,
    output: str | Path | None = None,
    *,
    root: Path = ROOT,
    runner=subprocess.run,
    ffmpeg_bin: str = "ffmpeg",
    timeout: float = 60,
    frame_count: int = 12,
    columns: int = 4,
) -> Path:
    """Derive a cached sheet from licensed media; never accepts browser evidence."""
    if pattern.get("mode") != "licensed_file":
        raise VideoMiningError(
            "contact sheet exige licensed_file; browser_observation nunca baixa mídia"
        )
    errors = []
    tag = str(pattern.get("id") or "pattern")
    media_path = _validate_licensed_file(pattern, tag, Path(root), errors)
    if errors or media_path is None:
        raise VideoMiningError("; ".join(errors))
    duration = (pattern.get("media") or {}).get("duration_seconds")
    if not _is_number(duration) or float(duration) <= 0:
        raise VideoMiningError("media.duration_seconds precisa ser > 0")
    if not isinstance(frame_count, int) or frame_count <= 0:
        raise VideoMiningError("frame_count precisa ser inteiro > 0")
    if not isinstance(columns, int) or columns <= 0:
        raise VideoMiningError("columns precisa ser inteiro > 0")

    output_path = (
        _lexical_absolute(output, Path(root))
        if output is not None
        else Path(root) / "output" / ".video-mining" / f"{tag}-contact-sheet.jpg"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows = math.ceil(frame_count / columns)
    filters = (
        f"fps={frame_count}/{float(duration):.6f},"
        f"scale=320:-2,tile={columns}x{rows}:padding=4:margin=4"
    )
    command = [
        ffmpeg_bin,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(media_path),
        "-vf",
        filters,
        "-frames:v",
        "1",
        str(output_path),
    ]
    try:
        completed = runner(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VideoMiningError(f"ffmpeg falhou: {exc}") from exc
    if completed.returncode != 0:
        detail = str(getattr(completed, "stderr", "") or "").strip()[:500]
        raise VideoMiningError(f"ffmpeg retornou erro: {detail or completed.returncode}")
    if not output_path.is_file():
        raise VideoMiningError(f"ffmpeg não criou contact sheet: {output_path}")
    return output_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="creative-forge — agent-driven video pattern mining"
    )
    parser.add_argument("--app", required=True)
    parser.add_argument("--path")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--contact-sheet", metavar="PATTERN_ID")
    parser.add_argument("--out")
    parser.add_argument("--now", help="ISO-8601 override for deterministic audits")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    path = Path(args.path) if args.path else ROOT / "swipe" / args.app / "video-patterns.yaml"
    try:
        data = load_yaml(path)
        now = _parse_aware_datetime(args.now) if args.now else None
        result = audit_video_patterns(
            data,
            expected_app=args.app,
            now=now,
            root=ROOT,
            probe_files=args.probe,
        )
        if args.contact_sheet and not result["errors"]:
            pattern = next(
                (
                    item
                    for item in data.get("patterns", []) or []
                    if item.get("id") == args.contact_sheet
                ),
                None,
            )
            if pattern is None:
                result["errors"].append(
                    f"pattern para contact sheet não encontrado: {args.contact_sheet}"
                )
            else:
                sheet = derive_contact_sheet(pattern, args.out, root=ROOT)
                result["contact_sheet"] = str(sheet)
    except (TypeError, ValueError, VideoMiningError) as exc:
        result = {"errors": [str(exc)], "warnings": [], "probes": {}}

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"creative-forge · video mining · {args.app}")
        for warning in result["warnings"]:
            print(f"  ⚠️  {warning}")
        for error in result["errors"]:
            print(f"  ❌ {error}")
        if result.get("contact_sheet"):
            print(f"  · contact sheet: {result['contact_sheet']}")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
