#!/usr/bin/env python3
"""Automated artifact checks plus checksum-bound visual review receipts."""

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

try:
    from scripts import render
except ImportError:  # direct execution: python3 scripts/qa.py
    import render

ROOT = Path(__file__).resolve().parent.parent
VISUAL_CHECKS = (
    "copy_correct",
    "readable",
    "imagery_consistent",
    "claims_truthful",
    "safe_zones",
    "no_artifacts",
    # the creative visibly follows the cited evidence/pattern structure
    # (compare with swipe/<app>/competitors.yaml hook + source_url) —
    # for current image templates; this is lineage, not performance proof
    "swipe_fidelity",
)
PROVENANCE_FIELDS = (
    "recipe",
    "research_refs",
    "swiped_from",
    "lineage",
    "claims_used",
    "template",
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
REQUIRED_INPUT_ROLES = {"recipe", "research", "template", "app_config"}


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
        records.append(
            {
                **spec,
                "path": str(path),
                **({"input_files": sealed_inputs} if require_provenance else {}),
                "sha256": sha256(path),
                "actual_width": size[0],
                "actual_height": size[1],
                "mode": mode,
            }
        )

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


def approve_visual(report: dict, reviewer: str, checks: dict) -> dict:
    if report.get("automated_status") != "pass":
        raise ValueError("QA automático não passou")
    if report_requires_provenance(report) and not report.get("input_digest"):
        raise ValueError("input digest ausente em report com proveniência obrigatória")
    missing = [name for name in VISUAL_CHECKS if checks.get(name) is not True]
    if missing:
        raise ValueError(f"checks visuais pendentes: {', '.join(missing)}")
    approved = dict(report)
    approved.update(
        {
            "visual_status": "approved",
            "visual_reviewer": reviewer,
            "visual_reviewed_at": datetime.now(timezone.utc).isoformat(),
            "visual_checks": {name: True for name in VISUAL_CHECKS},
            "approved_matrix_digest": report["matrix_digest"],
            "approved_input_digest": report.get("input_digest"),
            "approved_report_identity_digest": report_identity_digest(report),
        }
    )
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


def verify_report_files(report: dict) -> list:
    errors = []
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
            "path": str(lexical_absolute_path(ROOT / "scripts" / "render.py")),
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
    approve_parser.add_argument("--confirm-all", action="store_true")
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
        checks = {name: args.confirm_all for name in VISUAL_CHECKS}
        approved = approve_visual(report, args.reviewer, checks)
        report_path.write_text(json.dumps(approved, ensure_ascii=False, indent=2) + "\n")
        print(f"visual=approved reviewer={args.reviewer} digest={approved['matrix_digest'][:12]}")
    else:
        report = json.loads(Path(args.report).read_text())
        errors = verify_report_files(report)
        print(
            f"automated={report.get('automated_status')} visual={report.get('visual_status')} "
            f"files={'valid' if not errors else 'changed'}"
        )
        for error in errors:
            print(f"  ❌ {error}")
        if errors:
            sys.exit(1)


if __name__ == "__main__":
    main()
