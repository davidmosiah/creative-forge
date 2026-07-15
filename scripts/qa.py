#!/usr/bin/env python3
"""Automated artifact checks plus checksum-bound visual review receipts."""

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

from PIL import Image, ImageDraw, ImageOps
import yaml

try:
    from scripts import briefs, render, research
except ImportError:  # direct execution: python3 scripts/qa.py
    import briefs
    import render
    import research

try:
    from scripts.paths import default_root
except ImportError:
    from paths import default_root

ROOT = default_root()
VISUAL_CHECKS = (
    "copy_correct",
    "readable",
    "imagery_consistent",
    "claims_truthful",
    "safe_zones",
    "no_artifacts",
    # Agent judgment: the artifact remains faithful to its declared lineage.
    # Only competitor_pattern means structural swipe fidelity; original
    # lineages are reviewed against their own hypothesis and evidence anchor.
    "lineage_fidelity",
)
PROVENANCE_FIELDS = (
    "recipe",
    "research_refs",
    "swiped_from",
    "lineage",
    "claims_used",
    "template",
    "concept_lineage",
    "concept_lineage_ref",
    "execution_lineage",
    "execution_ref",
)
PUBLISH_METADATA_FIELDS = (
    "market_id",
    "locale",
    "app_locale",
    "copy_language",
    "format",
    "width",
    "height",
)
SEALED_RECORD_FIELDS = (
    *PROVENANCE_FIELDS,
    *PUBLISH_METADATA_FIELDS,
    "media_kind",
    "claim_evidence",
    "brief_ref",
    "concept_id",
    "variant_id",
    "cta",
    "ad_copy",
    "asset_refs",
)
REQUIRED_INPUT_ROLES = {"recipe", "research", "brief", "template", "app_config"}
CORE_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,127})$")


def lexical_absolute_path(value: str | Path) -> Path:
    """Return an absolute path without following its final symlink."""
    return Path(os.path.abspath(Path(value).expanduser()))


def external_source_roots() -> list[Path]:
    """Roots able to resolve paths authored relative to the canonical checkout."""
    roots = []
    configured = os.environ.get("CREATIVE_FORGE_ROOT")
    if configured:
        roots.append(lexical_absolute_path(configured))
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            common_dir = Path(completed.stdout.strip())
            if not common_dir.is_absolute():
                common_dir = ROOT / common_dir
            common_dir = lexical_absolute_path(common_dir)
            if common_dir.name == ".git":
                roots.append(common_dir.parent)
    except OSError:
        pass
    roots.append(ROOT)
    return list(dict.fromkeys(roots))


def resolve_external_input(value: str, roots: list[Path]) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return lexical_absolute_path(path)
    candidates = [lexical_absolute_path(root / path) for root in roots]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[-1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_input_digest(records: list) -> str:
    """Bind embedded provenance to the exact source-file checksums."""
    payload = []
    for record in records:
        metadata = {
            field: record[field]
            for field in SEALED_RECORD_FIELDS
            if field in record
        }
        for field in ("research_refs", "claims_used"):
            if isinstance(metadata.get(field), list):
                metadata[field] = sorted(metadata[field])
        input_files = []
        for item in record.get("input_files", []) or []:
            if not isinstance(item, dict):
                continue
            sealed = dict(item)
            if sealed.get("path") is not None:
                sealed["path"] = str(sealed["path"])
            input_files.append(sealed)
        metadata["input_files"] = sorted(
            input_files,
            key=lambda item: json.dumps(
                item, sort_keys=True, separators=(",", ":"), default=str
            ),
        )
        metadata["output_path"] = str(record.get("path", ""))
        payload.append(metadata)
    payload.sort(
        key=lambda item: json.dumps(
            item, sort_keys=True, separators=(",", ":"), default=str
        )
    )
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


def artifact_key(record: dict) -> str:
    """Stable key for one full-resolution artifact in an image QA report."""
    payload = {
        field: record.get(field)
        for field in (
            "path",
            "sha256",
            "market_id",
            "locale",
            "format",
            "brief_ref",
            "concept_id",
            "variant_id",
        )
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:20]


def seal_input_files(spec: dict, errors: list, hash_cache: dict[Path, str]) -> list:
    """Validate provenance inputs and replace any supplied hash with a fresh one."""
    output_path = spec.get("path", "output")
    for field in (*PROVENANCE_FIELDS, *PUBLISH_METADATA_FIELDS):
        if field not in spec:
            errors.append(f"proveniência {output_path}: campo ausente: {field}")

    raw_inputs = spec.get("input_files")
    if not isinstance(raw_inputs, list):
        errors.append(f"proveniência {output_path}: input_files ausente ou inválido")
        raw_inputs = []

    sealed_inputs = []
    roles = set()
    for index, item in enumerate(raw_inputs):
        if not isinstance(item, dict):
            errors.append(
                f"proveniência {output_path}: input_files[{index}] inválido"
            )
            continue
        role = item.get("role")
        path_value = item.get("path")
        if not isinstance(role, str) or not role:
            errors.append(
                f"proveniência {output_path}: input_files[{index}] sem role"
            )
            continue
        roles.add(role)
        if not path_value:
            errors.append(f"input {role} sem path: {output_path}")
            continue
        path = lexical_absolute_path(path_value)
        sealed = {**item, "role": role, "path": str(path)}
        if path.is_symlink():
            errors.append(f"input {role} usa symlink, o que é proibido: {path}")
        elif not path.is_file():
            errors.append(f"input {role} ausente: {path}")
        else:
            sealed["resolved_path"] = str(path.resolve(strict=True))
            if path not in hash_cache:
                hash_cache[path] = sha256(path)
            sealed["sha256"] = hash_cache[path]
        sealed_inputs.append(sealed)

    for role in sorted(REQUIRED_INPUT_ROLES - roles):
        errors.append(f"proveniência {output_path}: input role ausente: {role}")
    return sealed_inputs


def _provenance_yaml(record: dict, role: str, errors: list) -> dict:
    matches = [
        item
        for item in record.get("input_files", []) or []
        if isinstance(item, dict) and item.get("role") == role
    ]
    if len(matches) != 1:
        errors.append(
            f"lineage contract exige exatamente um input {role}; recebeu {len(matches)}"
        )
        return {}
    path = Path(matches[0].get("path", ""))
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        errors.append(f"lineage contract não leu {role} {path}: {exc}")
        return {}
    if not isinstance(data, dict):
        errors.append(f"lineage contract {role} precisa conter um objeto YAML")
        return {}
    return data


def lineage_contract_errors(record: dict) -> list:
    """Bind sealed QA lineage fields back to the exact brief/research/recipe."""
    errors = []
    brief = _provenance_yaml(record, "brief", errors)
    research_data = _provenance_yaml(record, "research", errors)
    recipe = _provenance_yaml(record, "recipe", errors)
    if errors:
        return errors

    research_by_id = {
        item.get("id"): item
        for item in research_data.get("creatives", []) or []
        if isinstance(item, dict) and item.get("id")
    }
    brief_ref = record.get("brief_ref")
    concept_id = record.get("concept_id")
    if brief.get("id") != brief_ref:
        errors.append(
            f"lineage contract brief_ref diverge: report={brief_ref!r}, brief={brief.get('id')!r}"
        )
    concepts = {
        item.get("id"): item
        for item in brief.get("concepts", []) or []
        if isinstance(item, dict) and item.get("id")
    }
    concept = concepts.get(concept_id)
    if concept is None:
        errors.append(f"lineage contract concept_id inexistente no brief: {concept_id}")
        return errors

    concept_lineage = concept.get("lineage")
    concept_ref = concept.get("lineage_ref")
    if record.get("concept_lineage") != concept_lineage:
        errors.append("lineage contract concept_lineage diverge do brief selado")
    if record.get("concept_lineage_ref") != concept_ref:
        errors.append("lineage contract concept_lineage_ref diverge do brief selado")
    concept_refs = set(concept.get("research_refs", []) or [])
    if not concept_ref or concept_ref not in concept_refs:
        errors.append("lineage contract lineage_ref não pertence aos research_refs do concept")
    anchor = research_by_id.get(concept_ref)
    if anchor is None:
        errors.append(f"lineage contract lineage_ref inexistente no research: {concept_ref}")
    elif concept_lineage not in research.ALLOWED_LINEAGE:
        errors.append(f"lineage contract concept_lineage inválida: {concept_lineage}")
    elif concept_lineage != "exploratory" and anchor.get("lineage") != concept_lineage:
        errors.append("lineage contract concept lineage diverge da evidência selada")
    if concept_lineage == "own_winner" and anchor is not None:
        if (
            anchor.get("evidence_level") != "performance_data"
            or not anchor.get("performance_metrics")
        ):
            errors.append(
                "lineage contract own_winner exige performance_data e performance_metrics"
            )

    recipe_refs = list(recipe.get("research_refs", []) or [])
    record_refs = list(record.get("research_refs", []) or [])
    if record_refs != recipe_refs:
        errors.append("lineage contract research_refs divergem da recipe selada")
    if not set(recipe_refs).issubset(concept_refs):
        errors.append("lineage contract recipe usa research_refs fora do concept")
    expected_lineage_map = {
        ref: (research_by_id.get(ref) or {}).get("lineage") for ref in recipe_refs
    }
    if record.get("lineage") != expected_lineage_map:
        errors.append("lineage contract mapa lineage diverge do research selado")
    for ref in recipe_refs:
        if ref not in research_by_id:
            errors.append(f"lineage contract research_ref inexistente: {ref}")

    for field in ("brief_ref", "concept_id", "execution_ref"):
        if record.get(field) != recipe.get(field):
            errors.append(f"lineage contract {field} diverge da recipe selada")
    if str(record.get("swiped_from") or "") != str(recipe.get("swiped_from") or ""):
        errors.append("lineage contract swiped_from diverge da recipe selada")
    execution_lineage, execution_ref = briefs.execution_binding(
        recipe,
        concept,
        research_by_id,
    )
    if record.get("execution_lineage") != execution_lineage:
        errors.append("lineage contract execution_lineage diverge da recipe selada")
    if record.get("execution_ref") != execution_ref:
        errors.append("lineage contract execution_ref diverge da recipe selada")
    if execution_lineage == "competitor_pattern" and not str(
        recipe.get("swiped_from") or ""
    ).strip():
        errors.append("lineage contract competitor execution sem swiped_from")
    return errors


def audit_outputs(
    specs: list,
    generated_after: datetime | None = None,
    output_dir: Path | None = None,
    require_provenance: bool = False,
) -> dict:
    errors, warnings, records = [], [], []
    input_hash_cache = {}
    expected_paths = {Path(spec["path"]).resolve() for spec in specs}
    if output_dir is not None:
        for path in sorted(output_dir.glob("*.png")):
            if path.resolve() not in expected_paths:
                errors.append(f"output inesperado: {path}")
    for spec in specs:
        path = Path(spec["path"])
        sealed_inputs = (
            seal_input_files(spec, errors, input_hash_cache)
            if require_provenance
            else None
        )
        if not path.exists():
            errors.append(f"output ausente: {path}")
            continue
        if generated_after:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if mtime < generated_after.astimezone(timezone.utc):
                errors.append(f"output stale: {path}")
        try:
            with Image.open(path) as image:
                size = image.size
                mode = image.mode
        except Exception as exc:
            errors.append(f"PNG inválido {path}: {exc}")
            continue
        expected = (int(spec["width"]), int(spec["height"]))
        if size != expected:
            errors.append(f"dimensão inválida {path}: {size}, esperado {expected}")
        if mode != "RGB":
            errors.append(f"modo de cor inválido {path}: {mode}, esperado RGB")
        record = {
                **spec,
                "path": str(path),
                **({"input_files": sealed_inputs} if require_provenance else {}),
                "sha256": sha256(path),
                "actual_width": size[0],
                "actual_height": size[1],
                "mode": mode,
            }
        record["artifact_key"] = artifact_key(record)
        if require_provenance:
            errors.extend(lineage_contract_errors(record))
        records.append(record)

    hashes = {}
    for record in records:
        hashes.setdefault(record["sha256"], []).append(record)
    for checksum, group in hashes.items():
        languages = {record["copy_language"] for record in group}
        if len(group) > 1 and len(languages) > 1:
            locales = ", ".join(sorted(record["locale"] for record in group))
            errors.append(
                f"duplicata inesperada entre copy_languages diferentes ({locales}): {checksum[:12]}"
            )

    result = {
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "warnings": warnings,
        "records": records,
    }
    if require_provenance:
        result["input_digest"] = canonical_input_digest(records)
        result["provenance_required"] = True
    return result


def matrix_digest(records: list) -> str:
    payload = [
        {"path": record["path"], "sha256": record["sha256"]}
        for record in sorted(records, key=lambda item: item["path"])
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def report_identity_digest(report: dict) -> str:
    """Seal report-level identity independently from artifact provenance."""
    payload = {
        "version": report.get("version"),
        "provenance_required": report.get("provenance_required"),
        "app": report.get("app"),
        "batch_id": report.get("batch_id"),
        "matrix_digest": report.get("matrix_digest"),
        "input_digest": report.get("input_digest"),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def build_report(app: str, batch_id: str, automated: dict) -> dict:
    provenance_required = bool(
        automated.get("provenance_required") or automated.get("input_digest")
    )
    input_digest = automated.get("input_digest")
    if input_digest is None and any(
        any(field in record for field in (*PROVENANCE_FIELDS, "claim_evidence"))
        for record in automated.get("records", [])
    ):
        # Metadata-only v1 reports remain compatible, but their embedded
        # publish lineage is still sealed and cannot be downgraded away.
        input_digest = canonical_input_digest(automated["records"])
    return {
        "version": 2 if provenance_required else 1,
        "provenance_required": provenance_required,
        "app": app,
        "batch_id": batch_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "automated_status": automated["status"],
        "errors": list(automated["errors"]),
        "warnings": list(automated["warnings"]),
        "records": list(automated["records"]),
        "matrix_digest": matrix_digest(automated["records"]),
        "input_digest": input_digest,
        "visual_status": "pending",
        "visual_checks": {},
    }


def _normalized_artifact_reviews(
    report: dict,
    artifact_reviews: list | None,
    *,
    allow_legacy: bool,
) -> list:
    records = report.get("records", []) or []
    for record in records:
        if not isinstance(record, dict) or record.get("artifact_key") != artifact_key(record):
            raise ValueError("report contém artifact_key inválido")
    records_by_key = {
        record.get("artifact_key"): record
        for record in records
        if record.get("artifact_key")
    }
    if len(records_by_key) != len(records):
        raise ValueError("report contém artifact_key ausente ou duplicado")
    if artifact_reviews is None:
        production_report = bool(
            report.get("provenance_required")
            or int(report.get("version") or 0) >= 2
        )
        if production_report or not allow_legacy:
            raise ValueError(
                "artifact_reviews por imagem são obrigatórias; abra cada PNG original"
            )
        artifact_reviews = [
            {
                "artifact_key": key,
                "notes": "legacy non-production report",
            }
            for key in records_by_key
        ]
    if not isinstance(artifact_reviews, list):
        raise ValueError("artifact_reviews precisa ser uma lista")
    supplied_keys = [
        item.get("artifact_key") if isinstance(item, dict) else None
        for item in artifact_reviews
    ]
    if len(supplied_keys) != len(set(supplied_keys)):
        raise ValueError("artifact_reviews contém artifact_key duplicado")
    if set(supplied_keys) != set(records_by_key):
        raise ValueError("artifact_reviews não cobre exatamente todos os artifacts")
    normalized = []
    for item in artifact_reviews:
        key = item.get("artifact_key")
        notes = str(item.get("notes") or "").strip()
        if not notes:
            raise ValueError(f"artifact {key} sem notes de inspeção full-resolution")
        record = records_by_key[key]
        normalized.append(
            {
                "artifact_key": key,
                "path": record.get("path"),
                "sha256": record.get("sha256"),
                "width": record.get("actual_width"),
                "height": record.get("actual_height"),
                "notes": notes,
            }
        )
    return sorted(normalized, key=lambda item: item["artifact_key"])


def visual_approval_digest(report: dict) -> str:
    payload = {
        "visual_reviewer": report.get("visual_reviewer"),
        "visual_reviewed_at": report.get("visual_reviewed_at"),
        "visual_checks": report.get("visual_checks"),
        "artifact_reviews": report.get("artifact_reviews"),
        "approved_matrix_digest": report.get("approved_matrix_digest"),
        "approved_input_digest": report.get("approved_input_digest"),
        "approved_report_identity_digest": report.get(
            "approved_report_identity_digest"
        ),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def approve_visual(
    report: dict,
    reviewer: str,
    checks: dict,
    *,
    artifact_reviews: list | None = None,
) -> dict:
    if report.get("automated_status") != "pass":
        raise ValueError("QA automático não passou")
    verification_errors = verify_report_files(report)
    if verification_errors:
        raise ValueError("QA report inválido: " + "; ".join(verification_errors))
    if not str(reviewer or "").strip():
        raise ValueError("reviewer é obrigatório")
    if report_requires_provenance(report) and not report.get("input_digest"):
        raise ValueError("input digest ausente em report com proveniência obrigatória")
    missing = [name for name in VISUAL_CHECKS if checks.get(name) is not True]
    if missing:
        raise ValueError(f"checks visuais pendentes: {', '.join(missing)}")
    normalized_reviews = _normalized_artifact_reviews(
        report, artifact_reviews, allow_legacy=True
    )
    approved = dict(report)
    approved.update(
        {
            "visual_status": "approved",
            "visual_reviewer": str(reviewer).strip(),
            "visual_reviewed_at": datetime.now(timezone.utc).isoformat(),
            "visual_checks": {name: True for name in VISUAL_CHECKS},
            "approved_matrix_digest": report["matrix_digest"],
            "approved_input_digest": report.get("input_digest"),
            "approved_report_identity_digest": report_identity_digest(report),
            "artifact_reviews": normalized_reviews,
        }
    )
    approved["visual_approval_digest"] = visual_approval_digest(approved)
    return approved


def report_requires_provenance(report: dict) -> bool:
    records = report.get("records", []) or []
    has_input_structure = any("input_files" in record for record in records)
    has_any_provenance_field = any(
        any(field in record for field in (*PROVENANCE_FIELDS, "claim_evidence"))
        for record in records
    )
    has_seal = bool(report.get("input_digest") or report.get("approved_input_digest"))
    explicitly_required = bool(
        report.get("provenance_required") or report.get("version") == 2
    )
    if explicitly_required or has_seal or has_input_structure:
        return True
    return has_any_provenance_field


def first_symlink_component(path: str | Path, root: str | Path) -> Path | None:
    """Return a symlink anywhere on a supposedly canonical workspace path."""
    root_path = lexical_absolute_path(root)
    candidate = lexical_absolute_path(path)
    try:
        candidate.relative_to(root_path)
    except ValueError:
        return candidate
    cursor = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        cursor = cursor / part
        if cursor.is_symlink():
            return cursor
    return None


def canonical_core_input_errors(report: dict, expected_root: str | Path) -> list:
    """Bind mutable provenance paths to this runtime's canonical workspace."""
    errors = []
    root = lexical_absolute_path(expected_root)
    app_value = report.get("app")
    app = str(app_value or "")
    if not isinstance(app_value, str) or not CORE_PATH_SEGMENT_RE.fullmatch(app):
        return [f"report app inválido para core input canônico: {app_value!r}"]
    root_symlink = first_symlink_component(root, root)
    if root_symlink is not None:
        errors.append(f"workspace root usa symlink: {root_symlink}")
    for record in report.get("records", []) or []:
        brief_ref = record.get("brief_ref")
        recipe_name = record.get("recipe")
        invalid_segments = []
        for label, value in (("brief_ref", brief_ref), ("recipe", recipe_name)):
            if not isinstance(value, str) or not CORE_PATH_SEGMENT_RE.fullmatch(value):
                errors.append(
                    f"record {label} inválido para core input canônico: {value!r}"
                )
                invalid_segments.append(label)
        expected = {
            "app_config": root / "apps" / f"{app}.yaml",
            "research": root / "swipe" / app / "competitors.yaml",
        }
        if "brief_ref" not in invalid_segments:
            expected["brief"] = root / "briefs" / app / f"{brief_ref}.yaml"
        if "recipe" not in invalid_segments:
            expected["recipe"] = root / "recipes" / app / f"{recipe_name}.yaml"
        inputs = record.get("input_files", []) or []
        for role, expected_path in expected.items():
            matches = [
                item
                for item in inputs
                if isinstance(item, dict) and item.get("role") == role
            ]
            if len(matches) != 1:
                errors.append(
                    f"core input {role} precisa ser único para path canônico"
                )
                continue
            path_value = matches[0].get("path")
            if not isinstance(path_value, (str, os.PathLike)):
                errors.append(f"core input {role} path canônico inválido")
                continue
            authored_path = os.fspath(path_value)
            canonical_path = lexical_absolute_path(expected_path)
            if (
                not Path(authored_path).is_absolute()
                or any(part in {".", ".."} for part in Path(authored_path).parts)
                or authored_path != str(canonical_path)
            ):
                errors.append(
                    f"core input {role} path authored não é canônico: "
                    f"{authored_path!r} != {str(canonical_path)!r}"
                )
                continue
            symlink = first_symlink_component(canonical_path, root)
            if symlink is not None:
                errors.append(
                    f"core input {role} usa symlink ancestral não canônico: {symlink}"
                )
                continue
            try:
                canonical_resolved = str(canonical_path.resolve(strict=True))
            except OSError as exc:
                errors.append(f"core input {role} canônico inacessível: {exc}")
                continue
            if matches[0].get("resolved_path") != canonical_resolved:
                errors.append(
                    f"core input {role} resolved_path não corresponde ao path canônico"
                )
    return errors


def verify_report_files(
    report: dict,
    *,
    expected_root: str | Path | None = None,
) -> list:
    errors = []
    if expected_root is not None:
        errors.extend(canonical_core_input_errors(report, expected_root))
    for record in report.get("records", []):
        path = Path(record["path"])
        if not path.exists():
            errors.append(f"arquivo aprovado desapareceu: {path}")
        elif sha256(path) != record["sha256"]:
            errors.append(f"checksum mudou após aprovação: {path}")
    current_digest = matrix_digest(report.get("records", []))
    report_input_digest = report.get("input_digest")
    approved_input_digest = report.get("approved_input_digest")
    provenance_mode = report_requires_provenance(report)
    if provenance_mode:
        if not report_input_digest:
            errors.append("input digest ausente em report com proveniência")
        expected_input_digest = report_input_digest or approved_input_digest
        require_input_roles = bool(
            report.get("provenance_required")
            or any("input_files" in record for record in report.get("records", []))
        )
        refreshed_records = []
        input_hash_cache = {}
        for record in report.get("records", []):
            refreshed_record = dict(record)
            refreshed_inputs = []
            roles = set()
            for index, item in enumerate(record.get("input_files", []) or []):
                if not isinstance(item, dict):
                    errors.append(
                        f"input de proveniência inválido no índice {index}: "
                        f"{record.get('path', 'output')}"
                    )
                    continue
                refreshed = dict(item)
                role = str(item.get("role") or "desconhecido")
                roles.add(role)
                path_value = item.get("path")
                if not path_value:
                    errors.append(
                        f"input {role} sem path no report: {record.get('path', 'output')}"
                    )
                else:
                    path = Path(path_value)
                    if path.is_symlink():
                        errors.append(
                            f"input {role} usa symlink após aprovação: {path}"
                        )
                    elif not path.is_file():
                        errors.append(f"input {role} desapareceu após aprovação: {path}")
                    else:
                        current_resolved_path = str(path.resolve(strict=True))
                        sealed_resolved_path = item.get("resolved_path")
                        if (
                            sealed_resolved_path
                            and current_resolved_path != sealed_resolved_path
                        ):
                            errors.append(
                                f"destino resolvido do input {role} mudou após "
                                f"aprovação: {path}"
                            )
                        if sealed_resolved_path:
                            refreshed["resolved_path"] = current_resolved_path
                        if path not in input_hash_cache:
                            input_hash_cache[path] = sha256(path)
                        current_hash = input_hash_cache[path]
                        if current_hash != item.get("sha256"):
                            errors.append(
                                f"checksum do input {role} mudou após aprovação: {path}"
                            )
                        refreshed["sha256"] = current_hash
                refreshed_inputs.append(refreshed)
            if require_input_roles:
                for role in sorted(REQUIRED_INPUT_ROLES - roles):
                    errors.append(
                        f"input role {role} desapareceu do report: "
                        f"{record.get('path', 'output')}"
                    )
            refreshed_record["input_files"] = refreshed_inputs
            refreshed_records.append(refreshed_record)
            if require_input_roles:
                errors.extend(lineage_contract_errors(record))

        embedded_digest = canonical_input_digest(report.get("records", []))
        refreshed_digest = canonical_input_digest(refreshed_records)
        if expected_input_digest and embedded_digest != expected_input_digest:
            errors.append("input digest não corresponde à proveniência embutida no report")
        if expected_input_digest and refreshed_digest != expected_input_digest:
            errors.append("input digest não corresponde aos arquivos de proveniência atuais")
    if report.get("visual_status") == "approved":
        approved_report_identity = report.get("approved_report_identity_digest")
        if not approved_report_identity:
            errors.append("report identity digest aprovado ausente")
        elif approved_report_identity != report_identity_digest(report):
            errors.append("report identity digest aprovado não corresponde ao report")
        if report.get("approved_matrix_digest") != current_digest:
            errors.append("matrix digest aprovado não corresponde ao report")
        if provenance_mode and not approved_input_digest:
            errors.append("input digest aprovado ausente em report com proveniência")
        elif report_input_digest and approved_input_digest != report_input_digest:
            errors.append("input digest aprovado não corresponde ao report")
        try:
            normalized_reviews = _normalized_artifact_reviews(
                report,
                report.get("artifact_reviews"),
                allow_legacy=False,
            )
            if normalized_reviews != report.get("artifact_reviews"):
                errors.append("artifact_reviews não correspondem aos artifacts aprovados")
        except ValueError as exc:
            errors.append(f"visual approval inválida: {exc}")
        if not str(report.get("visual_reviewer") or "").strip():
            errors.append("visual approval sem reviewer")
        try:
            reviewed_at = datetime.fromisoformat(
                str(report.get("visual_reviewed_at")).replace("Z", "+00:00")
            )
            if reviewed_at.tzinfo is None:
                raise ValueError
        except (TypeError, ValueError):
            errors.append("visual approval sem visual_reviewed_at ISO-8601 válido")
        missing_checks = [
            name
            for name in VISUAL_CHECKS
            if (report.get("visual_checks") or {}).get(name) is not True
        ]
        if missing_checks:
            errors.append(
                "visual approval checks pendentes: " + ", ".join(missing_checks)
            )
        if report.get("visual_approval_digest") != visual_approval_digest(report):
            errors.append("visual approval digest inválido")
    return errors


def validate_safe_zones(meta: dict) -> list:
    errors = []
    story = (meta.get("safe_zones", {}) or {}).get("story", {}) or {}
    for edge in ("top", "bottom"):
        value = story.get(edge)
        if not isinstance(value, (int, float)) or value < 200:
            errors.append(f"safe_zones.story.{edge} precisa ser >= 200px")
    return errors


def create_contact_sheet(paths: list[Path], out: Path, columns: int = 3) -> None:
    tile_w, tile_h, label_h = 320, 420, 34
    rows = max(1, math.ceil(len(paths) / columns))
    sheet = Image.new("RGB", (columns * tile_w, rows * (tile_h + label_h)), "#151515")
    draw = ImageDraw.Draw(sheet)
    for index, path in enumerate(paths):
        with Image.open(path) as source:
            thumb = ImageOps.contain(source.convert("RGB"), (tile_w - 12, tile_h - 12))
        x = (index % columns) * tile_w + (tile_w - thumb.width) // 2
        y0 = (index // columns) * (tile_h + label_h)
        y = y0 + (tile_h - thumb.height) // 2
        sheet.paste(thumb, (x, y))
        draw.text((x, y0 + tile_h + 8), path.name[:48], fill="white")
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)


def expected_specs(app_slug: str) -> list:
    app_path = ROOT / "apps" / f"{app_slug}.yaml"
    research_path = ROOT / "swipe" / app_slug / "competitors.yaml"
    registry_path = ROOT / "assets" / app_slug / "registry.yaml"
    source_roots = external_source_roots()
    app = render.load_yaml(app_path)
    research = render.load_yaml(research_path)
    asset_registry = (
        render.load_yaml(registry_path) if registry_path.exists() else {"assets": []}
    )
    assets_by_id = {
        item.get("id"): item for item in asset_registry.get("assets", []) or []
    }
    research_by_id = {
        creative.get("id"): creative
        for creative in research.get("creatives", []) or []
    }
    shared_inputs = [
        {"role": "app_config", "path": str(lexical_absolute_path(app_path))},
        {"role": "research", "path": str(lexical_absolute_path(research_path))},
        {
            "role": "engine",
            "component": "render",
            "path": str(lexical_absolute_path(Path(render.__file__).resolve())),
        },
        {
            "role": "engine",
            "component": "qa",
            "path": str(lexical_absolute_path(__file__)),
        },
    ]
    for asset_name, asset_value in (app.get("assets", {}) or {}).items():
        if not asset_value:
            continue
        asset_path = Path(asset_value)
        if not asset_path.is_absolute():
            asset_path = ROOT / asset_path
        shared_inputs.append(
            {
                "role": "app_asset",
                "asset": asset_name,
                "path": str(lexical_absolute_path(asset_path)),
            }
        )
    specs = []
    for recipe_path in sorted((ROOT / "recipes" / app_slug).glob("*.yaml")):
        recipe = render.load_yaml(recipe_path)
        markets = render.recipe_target_markets(app, recipe)
        template = recipe["template"]
        template_dir = ROOT / "templates" / "image" / template
        research_refs = list(recipe.get("research_refs", []) or [])
        claims_used = list(recipe.get("claims_used", []) or [])
        lineage = {
            ref: (research_by_id.get(ref) or {}).get("lineage")
            for ref in research_refs
        }
        claim_evidence = {
            claim: ((app.get("claims", {}) or {}).get(claim) or {}).get("evidence")
            for claim in claims_used
        }
        input_files = [
            *[dict(item) for item in shared_inputs],
            {"role": "recipe", "path": str(lexical_absolute_path(recipe_path))},
            {
                "role": "template",
                "path": str(lexical_absolute_path(template_dir / "meta.yaml")),
            },
            {
                "role": "template",
                "path": str(lexical_absolute_path(template_dir / "template.html")),
            },
        ]
        brief_ref = recipe.get("brief_ref")
        brief_path = ROOT / "briefs" / app_slug / f"{brief_ref}.yaml"
        brief = render.load_yaml(brief_path) if brief_ref and brief_path.exists() else {}
        concept = next(
            (
                item
                for item in brief.get("concepts", []) or []
                if item.get("id") == recipe.get("concept_id")
            ),
            {},
        )
        execution_lineage, execution_ref = briefs.execution_binding(
            recipe,
            concept,
            research_by_id,
        )
        if brief_ref:
            input_files.append(
                {"role": "brief", "path": str(lexical_absolute_path(brief_path))}
            )
        if registry_path.exists():
            input_files.append(
                {
                    "role": "asset_registry",
                    "path": str(lexical_absolute_path(registry_path)),
                }
            )
        asset_refs = list(recipe.get("asset_refs", []) or [])
        for asset_ref in asset_refs:
            asset_entry = assets_by_id.get(asset_ref) or {}
            asset_value = asset_entry.get("path")
            if not asset_value:
                continue
            asset_path = Path(asset_value)
            if not asset_path.is_absolute():
                asset_path = ROOT / asset_path
            input_files.append(
                {
                    "role": "creative_asset",
                    "asset": asset_ref,
                    "path": str(lexical_absolute_path(asset_path)),
                }
            )
        recipe_image = ((recipe.get("image", {}) or {}).get("file"))
        if recipe_image:
            image_path = Path(recipe_image)
            if not image_path.is_absolute():
                image_path = ROOT / image_path
            input_files.append(
                {
                    "role": "recipe_image",
                    "path": str(lexical_absolute_path(image_path)),
                }
            )
        for claim, evidence in claim_evidence.items():
            evidence_path = (evidence or {}).get("path")
            if evidence_path:
                input_files.append(
                    {
                        "role": "claim_evidence",
                        "claim": claim,
                        "path": str(
                            resolve_external_input(evidence_path, source_roots)
                        ),
                    }
                )
        for market in markets:
            localized_copy, _ = render.resolve_market_copy(
                recipe,
                market,
                (app.get("locales", {}) or {}).get("fallback_copy_language")
                or app.get("default_lang"),
            )
            localized_ad_copy = render.resolve_market_ad_copy(recipe, market)
            for fmt in render.template_formats(template):
                width, height = render.FORMATS[fmt]
                specs.append(
                    {
                        "path": str(
                            ROOT
                            / "output"
                            / app_slug
                            / f"{recipe_path.stem}--{market['locale']}--{fmt}.png"
                        ),
                        "recipe": recipe_path.stem,
                        "format": fmt,
                        "market_id": market["id"],
                        "locale": market["locale"],
                        "app_locale": market["app_locale"],
                        "copy_language": market["copy_language"],
                        "width": width,
                        "height": height,
                        "media_kind": recipe.get("media_kind", "image"),
                        "research_refs": research_refs,
                        "swiped_from": recipe.get("swiped_from", ""),
                        "lineage": lineage,
                        "claims_used": claims_used,
                        "claim_evidence": claim_evidence,
                        "brief_ref": brief_ref,
                        "concept_id": recipe.get("concept_id"),
                        "concept_lineage": concept.get("lineage"),
                        "concept_lineage_ref": concept.get("lineage_ref"),
                        "execution_lineage": execution_lineage,
                        "execution_ref": execution_ref,
                        "variant_id": recipe.get("variant_id") or recipe_path.stem,
                        **(
                            {"cta": localized_copy.get("cta")}
                            if localized_copy.get("cta")
                            else {}
                        ),
                        **(
                            {"ad_copy": dict(localized_ad_copy)}
                            if isinstance(localized_ad_copy, dict)
                            and localized_ad_copy
                            else {}
                        ),
                        "asset_refs": asset_refs,
                        "template": template,
                        "input_files": [dict(item) for item in input_files],
                    }
                )
    return specs


def prepare(app: str, batch_id: str, generated_after: datetime | None = None) -> Path:
    specs = expected_specs(app)
    automated = audit_outputs(
        specs,
        generated_after=generated_after,
        output_dir=ROOT / "output" / app,
        require_provenance=True,
    )
    templates = {
        render.load_yaml(ROOT / "recipes" / app / f"{record['recipe']}.yaml")["template"]
        for record in automated["records"]
    }
    for template in sorted(templates):
        meta = render.load_yaml(ROOT / "templates" / "image" / template / "meta.yaml")
        automated["errors"].extend(
            f"template {template}: {error}" for error in validate_safe_zones(meta)
        )
    automated["status"] = "pass" if not automated["errors"] else "fail"
    report = build_report(app, batch_id, automated)
    qa_dir = ROOT / "qa" / app / batch_id
    by_format = {}
    for record in report["records"]:
        by_format.setdefault(record["format"], []).append(Path(record["path"]))
    contact_sheets = []
    for fmt, paths in sorted(by_format.items()):
        contact = qa_dir / f"contact-{fmt}.png"
        create_contact_sheet(paths, contact)
        contact_sheets.append(str(contact))
    report["contact_sheets"] = contact_sheets
    report_path = qa_dir / "report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description="creative-forge — QA gate")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--app", required=True)
    prepare_parser.add_argument("--batch-id", required=True)
    prepare_parser.add_argument("--generated-after")
    approve_parser = sub.add_parser("approve")
    approve_parser.add_argument("--report", required=True)
    approve_parser.add_argument("--reviewer", required=True)
    approve_parser.add_argument("--review-file", required=True)
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--report", required=True)
    args = parser.parse_args()

    if args.command == "prepare":
        generated_after = (
            datetime.fromisoformat(args.generated_after.replace("Z", "+00:00"))
            if args.generated_after
            else None
        )
        report_path = prepare(args.app, args.batch_id, generated_after)
        report = json.loads(report_path.read_text())
        print(report_path)
        print(f"automated={report['automated_status']} visual={report['visual_status']}")
        if report["automated_status"] != "pass":
            for error in report["errors"]:
                print(f"  ❌ {error}")
            sys.exit(1)
    elif args.command == "approve":
        report_path = Path(args.report)
        report = json.loads(report_path.read_text())
        review = json.loads(Path(args.review_file).read_text())
        declared_checks = review.get("checks", [])
        if isinstance(declared_checks, dict):
            checks = declared_checks
        else:
            checks = {name: name in set(declared_checks or []) for name in VISUAL_CHECKS}
        approved = approve_visual(
            report,
            args.reviewer,
            checks,
            artifact_reviews=review.get("artifact_reviews"),
        )
        report_path.write_text(json.dumps(approved, ensure_ascii=False, indent=2) + "\n")
        print(f"visual=approved reviewer={args.reviewer} digest={approved['matrix_digest'][:12]}")
    else:
        report = json.loads(Path(args.report).read_text())
        errors = verify_report_files(report, expected_root=ROOT)
        print(
            f"automated={report.get('automated_status')} visual={report.get('visual_status')} "
            f"files={'valid' if not errors else 'changed'}"
        )
        for error in errors:
            print(f"  ❌ {error}")
        for record in report.get("records", []) or []:
            print(
                f"  · artifact_key={record.get('artifact_key')} "
                f"path={record.get('path')}"
            )
        if errors:
            sys.exit(1)


if __name__ == "__main__":
    main()
