#!/usr/bin/env python3
"""Validate local creative assets, hashes and explicit commercial rights."""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWED_KINDS = {
    "owned",
    "commissioned",
    "licensed",
    "generated",
    "reference_only",
}
EVIDENCE_REQUIRED_KINDS = {"commissioned", "licensed", "generated"}
ALLOWED_RIGHTS_EVIDENCE_KINDS = {
    "commission_agreement",
    "license_certificate",
    "signed_contract",
    "terms_snapshot",
}
ALLOWED_CONSENT_EVIDENCE_KINDS = {
    "signed_release",
    "model_release",
    "talent_release",
    "parent_guardian_release",
}


def load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError:
        sys.exit("creative-forge: PyYAML ausente. Rode: pip3 install -r requirements.txt")
    if not path.exists():
        sys.exit(f"creative-forge: asset registry inexistente: {path}")
    return yaml.safe_load(path.read_text()) or {}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_path(value: str, root: Path) -> Path:
    path = Path(value).expanduser()
    return Path(os.path.abspath(path if path.is_absolute() else root / path))


def audit_registry(
    registry: dict,
    *,
    expected_app: str | None = None,
    root: Path = ROOT,
) -> dict:
    errors, warnings = [], []
    if not isinstance(registry, dict):
        return {
            "errors": ["asset registry precisa ser um objeto YAML"],
            "warnings": warnings,
        }
    if registry.get("version") != 1:
        errors.append("asset registry.version precisa ser 1")
    if not registry.get("app"):
        errors.append("asset registry.app ausente")
    if expected_app is not None and registry.get("app") != expected_app:
        errors.append(
            f"asset registry.app '{registry.get('app')}' diverge do app '{expected_app}'"
        )
    entries = registry.get("assets", [])
    if entries is None:
        entries = []
    elif not isinstance(entries, list):
        errors.append("asset registry.assets precisa ser uma lista")
        entries = []
    if not entries:
        errors.append("asset registry.assets vazio")
    seen = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"asset #{index} precisa ser um objeto")
            continue
        asset_id = entry.get("id") or f"#{index}"
        if not entry.get("id"):
            errors.append(f"asset {asset_id} sem id")
        elif asset_id in seen:
            errors.append(f"asset id duplicado: {asset_id}")
        seen.add(entry.get("id"))
        kind = entry.get("kind")
        if kind not in ALLOWED_KINDS:
            errors.append(f"asset {asset_id} kind inválido: {kind}")
        source = entry.get("source")
        if not source:
            errors.append(f"asset {asset_id} sem source")
        elif not isinstance(source, dict):
            errors.append(f"asset {asset_id} source precisa ser um objeto")
        rights = entry.get("rights", {}) or {}
        if not isinstance(rights, dict):
            errors.append(f"asset {asset_id} rights precisa ser um objeto")
            rights = {}
        if not rights.get("basis"):
            errors.append(f"asset {asset_id} sem rights.basis")

        path_value = entry.get("path")
        if kind == "reference_only":
            if path_value:
                errors.append(f"asset {asset_id} reference_only não pode declarar path de render")
            if rights.get("status") != "reference_only":
                errors.append(f"asset {asset_id} reference_only exige rights.status reference_only")
            if rights.get("commercial_use") is not False:
                errors.append(f"asset {asset_id} reference_only exige commercial_use false")
            continue

        if rights.get("status") != "cleared":
            errors.append(f"asset {asset_id} sem rights.status cleared")
        if rights.get("commercial_use") is not True:
            errors.append(f"asset {asset_id} sem commercial_use true")
        if rights.get("derivative_use") not in {True, False}:
            errors.append(f"asset {asset_id} sem derivative_use explícito")
        scope = rights.get("scope", {}) or {}
        if not isinstance(scope, dict):
            errors.append(f"asset {asset_id} rights.scope precisa ser um objeto")
            scope = {}
        if scope.get("paid_ads") is not True:
            errors.append(f"asset {asset_id} exige rights.scope.paid_ads true")
        platforms = scope.get("platforms")
        if not isinstance(platforms, list) or not platforms or not all(
            isinstance(platform, str) and platform.strip()
            for platform in platforms
        ):
            errors.append(f"asset {asset_id} exige rights.scope.platforms não vazio")
        if kind in EVIDENCE_REQUIRED_KINDS:
            evidence = rights.get("evidence", {}) or {}
            if not isinstance(evidence, dict) or not evidence:
                errors.append(f"asset {asset_id} sem rights.evidence verificável")
            else:
                evidence_kind = evidence.get("kind")
                if evidence_kind not in ALLOWED_RIGHTS_EVIDENCE_KINDS:
                    errors.append(
                        f"asset {asset_id} rights.evidence.kind inválido: "
                        f"{evidence_kind}"
                    )
                evidence_path_value = evidence.get("path")
                if not evidence_path_value:
                    errors.append(f"asset {asset_id} sem rights.evidence.path")
                else:
                    evidence_path = resolve_path(evidence_path_value, root)
                    if path_value and evidence_path == resolve_path(path_value, root):
                        errors.append(
                            f"asset {asset_id} evidence não pode ser o próprio asset"
                        )
                    if evidence_path.is_symlink():
                        errors.append(
                            f"asset {asset_id} rights.evidence usa symlink proibido: "
                            f"{evidence_path}"
                        )
                    elif not evidence_path.is_file():
                        errors.append(
                            f"asset {asset_id} rights.evidence path ausente: "
                            f"{evidence_path}"
                        )
                    else:
                        evidence_hash = evidence.get("sha256")
                        if not evidence_hash:
                            errors.append(
                                f"asset {asset_id} sem rights.evidence sha256"
                            )
                        elif sha256(evidence_path) != evidence_hash:
                            errors.append(
                                f"asset {asset_id} rights.evidence sha256 diverge "
                                "do arquivo"
                            )
        if not path_value:
            errors.append(f"asset {asset_id} sem path")
        else:
            path = resolve_path(path_value, root)
            if path.is_symlink():
                errors.append(f"asset {asset_id} usa symlink proibido: {path}")
            elif not path.is_file():
                errors.append(f"asset {asset_id} path ausente: {path}")
            else:
                expected_hash = entry.get("sha256")
                if not expected_hash:
                    errors.append(f"asset {asset_id} sem sha256")
                elif sha256(path) != expected_hash:
                    errors.append(f"asset {asset_id} sha256 diverge do arquivo")
        if kind == "generated":
            generation = entry.get("generation", {}) or {}
            if not isinstance(generation, dict):
                errors.append(f"asset {asset_id} generation precisa ser um objeto")
                generation = {}
            required = ("provider", "model", "job_id", "prompt_sha256")
            missing = [field for field in required if not generation.get(field)]
            if missing:
                errors.append(
                    f"asset {asset_id} sem generation receipt completo: {', '.join(missing)}"
                )
        if entry.get("depicts_identifiable_people") is True:
            release = entry.get("consent_release", {}) or {}
            if not isinstance(release, dict):
                errors.append(
                    f"asset {asset_id} consent_release precisa ser um objeto"
                )
                release = {}
            if release.get("status") != "cleared":
                errors.append(f"asset {asset_id} com pessoa identificável sem consent_release")
            release_evidence = release.get("evidence", {}) or {}
            if not isinstance(release_evidence, dict) or not release_evidence:
                errors.append(
                    f"asset {asset_id} com pessoa identificável sem "
                    "consent_release.evidence verificável"
                )
            else:
                evidence_kind = release_evidence.get("kind")
                if evidence_kind not in ALLOWED_CONSENT_EVIDENCE_KINDS:
                    errors.append(
                        f"asset {asset_id} consent_release.evidence.kind inválido: "
                        f"{evidence_kind}"
                    )
                release_path_value = release_evidence.get("path")
                if not release_path_value:
                    errors.append(
                        f"asset {asset_id} sem consent_release.evidence.path"
                    )
                else:
                    release_path = resolve_path(release_path_value, root)
                    if release_path.is_symlink():
                        errors.append(
                            f"asset {asset_id} consent release usa symlink proibido: "
                            f"{release_path}"
                        )
                    elif not release_path.is_file():
                        errors.append(
                            f"asset {asset_id} consent release ausente: {release_path}"
                        )
                    else:
                        release_hash = release_evidence.get("sha256")
                        if not release_hash:
                            errors.append(
                                f"asset {asset_id} sem consent_release.evidence sha256"
                            )
                        elif sha256(release_path) != release_hash:
                            errors.append(
                                f"asset {asset_id} consent_release.evidence sha256 "
                                "diverge do arquivo"
                            )
    return {"errors": errors, "warnings": warnings}


def recipe_asset_errors(recipe: dict, registry: dict, recipe_name: str) -> list:
    errors = []
    if not isinstance(recipe, dict):
        return [f"[{recipe_name}] recipe precisa ser um objeto"]
    if not isinstance(registry, dict):
        return [f"[{recipe_name}] asset registry precisa ser um objeto YAML"]
    entries = registry.get("assets", []) or []
    if not isinstance(entries, list):
        return [f"[{recipe_name}] asset registry.assets precisa ser uma lista"]
    known = {
        entry.get("id"): entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("id")
    }
    refs = recipe.get("asset_refs", []) or []
    if not isinstance(refs, list):
        return [f"[{recipe_name}] asset_refs precisa ser uma lista"]
    target_platforms = recipe.get("target_platforms")
    if (
        not isinstance(target_platforms, list)
        or not target_platforms
        or not all(
            isinstance(platform, str) and platform.strip()
            for platform in target_platforms
        )
    ):
        errors.append(f"[{recipe_name}] target_platforms vazio ou inválido")
        target_platforms = []
    elif len(target_platforms) != len(set(target_platforms)):
        errors.append(f"[{recipe_name}] target_platforms contém duplicatas")
    if not refs:
        errors.append(f"[{recipe_name}] sem asset_refs")
    for ref in refs:
        entry = known.get(ref)
        if entry is None:
            errors.append(f"[{recipe_name}] asset_ref inexistente: {ref}")
            continue
        if entry.get("kind") == "reference_only":
            errors.append(f"[{recipe_name}] tenta renderizar asset reference_only: {ref}")
        rights = entry.get("rights", {}) or {}
        if not isinstance(rights, dict):
            errors.append(f"[{recipe_name}] asset {ref} possui rights malformado")
            continue
        if rights.get("status") != "cleared" or rights.get("commercial_use") is not True:
            errors.append(f"[{recipe_name}] asset sem direitos comerciais liberados: {ref}")
        if rights.get("derivative_use") is not True:
            errors.append(
                f"[{recipe_name}] asset {ref} sem derivative_use para composição criativa"
            )
        scope = rights.get("scope", {}) or {}
        if not isinstance(scope, dict) or scope.get("paid_ads") is not True:
            errors.append(f"[{recipe_name}] asset {ref} sem paid_ads no escopo")
            scope = {}
        allowed_platforms = set(scope.get("platforms", []) or [])
        missing_platforms = sorted(set(target_platforms) - allowed_platforms)
        if missing_platforms:
            errors.append(
                f"[{recipe_name}] asset {ref} sem direitos para target_platforms: "
                + ", ".join(missing_platforms)
            )
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="creative-forge — asset rights gate")
    parser.add_argument("--app", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    path = ROOT / "assets" / args.app / "registry.yaml"
    result = audit_registry(load_yaml(path), expected_app=args.app)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for warning in result["warnings"]:
            print(f"  ⚠️  {warning}")
        for error in result["errors"]:
            print(f"  ❌ {error}")
        print(f"assets={'PASS' if not result['errors'] else 'BLOCKED'}")
    if result["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
