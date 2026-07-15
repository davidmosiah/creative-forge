#!/usr/bin/env python3
"""Validate competitor-mining evidence and rank observed creative proxies."""

import argparse
import json
import sys
from datetime import date, datetime, timezone
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlparse

try:
    from scripts.paths import default_root
except ImportError:
    from paths import default_root

ROOT = default_root()
REQUIRED_FIELDS = (
    "id",
    "platform",
    "advertiser",
    "market",
    "active_since",
    "format",
    "angle",
    "hook",
    "source_url",
    "evidence_level",
)
ALLOWED_EVIDENCE = {"observed", "longevity_proxy", "performance_data"}
ALLOWED_LINEAGE = {
    "competitor_pattern",
    "own_winner",
    "customer_insight",
    "trend",
    "exploratory",
}


def load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError:
        sys.exit("creative-forge: PyYAML ausente. Rode: pip3 install -r requirements.txt")
    if not path.exists():
        sys.exit(f"creative-forge: research inexistente: {path}")
    return yaml.safe_load(path.read_text()) or {}


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def is_valid_http_url(value: str) -> bool:
    if not value or any(character.isspace() for character in value):
        return False
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
        _ = parsed.port
    except (UnicodeError, ValueError):
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not hostname:
        return False

    authority = parsed.netloc.rsplit("@", 1)[-1]
    if authority.endswith(":"):
        return False
    if "[" in authority or "]" in authority:
        if not authority.startswith("["):
            return False
        try:
            return ip_address(hostname).version == 6
        except ValueError:
            return False

    try:
        ip_address(hostname)
        return True
    except ValueError:
        pass

    if hostname.endswith("."):
        return False
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    if len(ascii_hostname) > 253:
        return False
    labels = ascii_hostname.split(".")
    return all(
        1 <= len(label) <= 63
        and label[0].isalnum()
        and label[-1].isalnum()
        and all(character.isalnum() or character == "-" for character in label)
        for label in labels
    )


def audit_research(
    data: dict,
    now: datetime | None = None,
    expected_app: str | None = None,
) -> dict:
    errors, warnings = [], []
    now = now or datetime.now(timezone.utc)
    if data.get("version") != 1:
        errors.append("research.version precisa ser 1")
    if not data.get("app"):
        errors.append("research.app ausente")
    if expected_app is not None and data.get("app") != expected_app:
        errors.append(
            f"research.app '{data.get('app')}' diverge do app solicitado "
            f"'{expected_app}'"
        )
    try:
        observed_at = parse_time(data.get("observed_at"))
        expires_at = parse_time(data.get("expires_at"))
        if expires_at <= observed_at:
            errors.append("research expires_at precisa ser posterior a observed_at")
        if now > expires_at:
            errors.append(f"pesquisa expirada em {expires_at.isoformat()}")
    except (TypeError, ValueError):
        errors.append("research observed_at/expires_at inválidos")

    creatives = data.get("creatives", []) or []
    if not creatives:
        errors.append("research.creatives vazio")
    seen = set()
    for index, creative in enumerate(creatives):
        tag = creative.get("id") or f"#{index}"
        for field in REQUIRED_FIELDS:
            if not creative.get(field):
                errors.append(f"creative {tag} sem {field}")
        if creative.get("id") in seen:
            errors.append(f"creative id duplicado: {creative.get('id')}")
        seen.add(creative.get("id"))
        evidence = creative.get("evidence_level")
        if evidence == "proven_roas":
            errors.append(
                f"creative {tag} usa proven_roas sem performance data; longevidade é proxy"
            )
        elif evidence not in ALLOWED_EVIDENCE:
            errors.append(f"creative {tag} evidence_level inválido: {evidence}")
        if evidence == "performance_data" and not creative.get("performance_metrics"):
            errors.append(f"creative {tag} sem performance_metrics")
        lineage = creative.get("lineage")
        if lineage is None:
            warnings.append(
                f"creative {tag} sem lineage; migre explicitamente para "
                "competitor_pattern"
            )
        elif lineage not in ALLOWED_LINEAGE:
            errors.append(f"creative {tag} lineage inválida: {lineage}")
        elif lineage == "own_winner" and evidence != "performance_data":
            errors.append(
                f"creative {tag} com lineage own_winner exige performance_data"
            )
        source_url = str(creative.get("source_url") or "")
        if not is_valid_http_url(source_url):
            errors.append(f"creative {tag} source_url precisa ser HTTP(S) válida")
        try:
            date.fromisoformat(str(creative.get("active_since")))
        except ValueError:
            errors.append(f"creative {tag} active_since inválido")
        if creative.get("format") == "unknown":
            warnings.append(f"creative {tag} ainda sem formato verificado")
    return {"errors": errors, "warnings": warnings}


def norm_angle(value: str) -> str:
    return " ".join(str(value or "").lower().replace("-", " ").split())


def swipe_alignment_errors(
    recipe_name: str,
    template_name: str,
    template_swipe_angles: list,
    research_refs: list,
    research_data: dict,
    *,
    execution_lineage: str = "competitor_pattern",
    execution_ref: str | None = None,
    swiped_from: str | None = None,
) -> list:
    """Competitor-pattern recipes must materialize at least one cited angle.

    Alignment proves lineage only; it never proves competitor performance.
    Unresolved refs are judged by validate_research_refs, not here."""
    errors = []
    if execution_lineage != "competitor_pattern":
        return errors
    if not str(swiped_from or "").strip():
        errors.append(
            f"{recipe_name}: competitor_pattern sem swiped_from — declare a estrutura observada"
        )
    template_angles = {norm_angle(a) for a in template_swipe_angles or [] if a}
    if not template_angles:
        errors.append(
            f"{recipe_name}: template '{template_name}' sem swipe_angles no meta.yaml "
            "— fora do contrato de padrões observados"
        )
        return errors
    by_id = {c.get("id"): c for c in research_data.get("creatives", []) or []}
    refs_for_alignment = [execution_ref] if execution_ref else list(research_refs or [])
    if execution_ref and execution_ref not in research_refs:
        errors.append(
            f"{recipe_name}: execution_ref '{execution_ref}' não está em research_refs"
        )
    anchor = by_id.get(execution_ref) if execution_ref else None
    if anchor is not None and anchor.get("lineage") != "competitor_pattern":
        errors.append(
            f"{recipe_name}: execution_ref '{execution_ref}' não é competitor_pattern"
        )
    cited_angles = {
        norm_angle(by_id[ref].get("angle"))
        for ref in refs_for_alignment
        if ref in by_id and by_id[ref].get("lineage", "competitor_pattern") == "competitor_pattern"
    }
    cited_angles.discard("")
    if cited_angles and not (cited_angles & template_angles):
        errors.append(
            f"{recipe_name}: criativo fora da estratégia de cópia — template "
            f"'{template_name}' materializa {sorted(template_angles)}, mas os "
            f"padrões citados têm ângulos {sorted(cited_angles)}"
        )
    return errors


def rank_creatives(data: dict, as_of: datetime | None = None) -> list:
    as_of = as_of or datetime.now(timezone.utc)
    ranked = []
    for creative in data.get("creatives", []) or []:
        active_since = date.fromisoformat(str(creative["active_since"]))
        days_running = max(0, (as_of.date() - active_since).days)
        evidence_bonus = 10_000 if creative.get("evidence_level") == "performance_data" else 0
        ranked.append({**creative, "days_running": days_running, "score": days_running + evidence_bonus})
    return sorted(ranked, key=lambda item: (-item["score"], item["id"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="creative-forge — competitor research evidence")
    parser.add_argument("--app", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    data = load_yaml(ROOT / "swipe" / args.app / "competitors.yaml")
    audit = audit_research(data, expected_app=args.app)
    ranking = rank_creatives(data)
    result = {**audit, "ranking": ranking}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"creative-forge · research · {args.app}")
        for warning in audit["warnings"]:
            print(f"  ⚠️  {warning}")
        for error in audit["errors"]:
            print(f"  ❌ {error}")
        for item in ranking[:10]:
            print(
                f"  · {item['advertiser']} / {item['market']} / {item['angle']} "
                f"({item['days_running']}d, {item['evidence_level']})"
            )
    if audit["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
