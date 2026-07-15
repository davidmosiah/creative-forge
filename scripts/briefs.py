#!/usr/bin/env python3
"""Validate agent-authored creative briefs without scoring creative judgment."""

import argparse
import json
import math
import re
import sys
from pathlib import Path

try:
    from scripts import research
except ImportError:
    import research

try:
    from scripts.paths import default_root
except ImportError:
    from paths import default_root

ROOT = default_root()
ALLOWED_DESTINATIONS = {"app_store", "custom_product_page", "landing_page"}


def load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError:
        sys.exit("creative-forge: PyYAML ausente. Rode: pip3 install -r requirements.txt")
    if not path.exists():
        sys.exit(f"creative-forge: brief inexistente: {path}")
    return yaml.safe_load(path.read_text()) or {}


def audit_brief(
    brief: dict,
    *,
    expected_app: str | None = None,
    known_research_refs: set[str] | None = None,
    research_by_id: dict[str, dict] | None = None,
    supported_markets: set[str] | None = None,
) -> dict:
    """Check auditable structure; the agent remains responsible for its quality."""
    errors, warnings = [], []
    if brief.get("version") != 1:
        errors.append("brief.version precisa ser 1")
    for field in ("id", "app", "objective", "primary_kpi"):
        if not brief.get(field):
            errors.append(f"brief.{field} ausente")
    if expected_app is not None and brief.get("app") != expected_app:
        errors.append(
            f"brief.app '{brief.get('app')}' diverge do app solicitado '{expected_app}'"
        )
    status = brief.get("status")
    if status not in {"draft", "approved", "archived"}:
        errors.append(f"brief.status inválido: {status}")
    if status == "approved" and not brief.get("approved_by"):
        errors.append("brief approved sem approved_by")
    markets = brief.get("markets")
    if not isinstance(markets, list) or not markets:
        errors.append("brief.markets vazio")
        markets = []
    elif not all(isinstance(market, str) and market.strip() for market in markets):
        errors.append("brief.markets precisa conter IDs string não vazios")
        markets = []
    elif len(markets) != len(set(markets)):
        errors.append("brief.markets contém duplicatas")
    if supported_markets is not None:
        unknown_markets = sorted(set(markets) - set(supported_markets))
        if unknown_markets:
            errors.append(
                "brief.markets inexistentes no app: " + ", ".join(unknown_markets)
            )

    destination = brief.get("destination", {}) or {}
    destination_type = destination.get("type")
    if destination_type not in ALLOWED_DESTINATIONS:
        errors.append(f"brief.destination.type inválido: {destination_type}")
    if not research.is_valid_http_url(str(destination.get("url") or "")):
        errors.append("brief.destination.url precisa ser HTTP(S) válida")

    hypothesis = brief.get("hypothesis", {}) or {}
    for field in ("action", "expected_result", "reason"):
        if not hypothesis.get(field):
            errors.append(f"brief.hypothesis.{field} ausente")

    test_design = brief.get("test_design", {}) or {}
    if not test_design.get("isolated_variable"):
        errors.append("brief.test_design.isolated_variable ausente")
    if not test_design.get("constants"):
        errors.append("brief.test_design.constants vazio")

    measurement = brief.get("measurement")
    if not isinstance(measurement, dict):
        errors.append("brief.measurement ausente ou inválido")
    else:
        observation = measurement.get("observation_window_hours")
        if (
            not isinstance(observation, (int, float))
            or isinstance(observation, bool)
            or not math.isfinite(float(observation))
            or observation <= 0
        ):
            errors.append("brief.measurement.observation_window_hours precisa ser > 0")
        attribution = measurement.get("attribution_window")
        if not isinstance(attribution, dict):
            errors.append("brief.measurement.attribution_window ausente")
        else:
            for field in ("click_days", "view_days"):
                value = attribution.get(field)
                if (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or not 0 <= value <= 30
                ):
                    errors.append(
                        f"brief.measurement.attribution_window.{field} precisa estar entre 0 e 30"
                    )
        if not re.fullmatch(r"[A-Z]{3}", str(measurement.get("currency") or "")):
            errors.append("brief.measurement.currency precisa ser ISO-4217")

    concepts = brief.get("concepts", []) or []
    if not concepts:
        errors.append("brief.concepts vazio")
    seen = set()
    for index, concept in enumerate(concepts):
        concept_id = concept.get("id") or f"#{index}"
        if not concept.get("id"):
            errors.append(f"concept {concept_id} sem id")
        elif concept_id in seen:
            errors.append(f"concept id duplicado: {concept_id}")
        seen.add(concept.get("id"))
        lineage = concept.get("lineage")
        if lineage not in research.ALLOWED_LINEAGE:
            errors.append(f"concept {concept_id} lineage inválida: {lineage}")
        refs = concept.get("research_refs", []) or []
        if not refs:
            errors.append(f"concept {concept_id} sem research_refs")
        lineage_ref = concept.get("lineage_ref")
        if not lineage_ref:
            errors.append(f"concept {concept_id} sem lineage_ref")
        elif lineage_ref not in refs:
            errors.append(
                f"concept {concept_id} lineage_ref '{lineage_ref}' precisa estar em research_refs"
            )
        known_refs = (
            set(research_by_id)
            if research_by_id is not None
            else known_research_refs
        )
        if known_refs is not None:
            for ref in refs:
                if ref not in known_refs:
                    errors.append(f"concept {concept_id} referencia research_ref inexistente: {ref}")
        anchor = (research_by_id or {}).get(lineage_ref)
        if research_by_id is not None and lineage_ref in refs and anchor is None:
            errors.append(
                f"concept {concept_id} lineage_ref inexistente: {lineage_ref}"
            )
        elif anchor is not None and lineage != "exploratory":
            if anchor.get("lineage") != lineage:
                errors.append(
                    f"concept {concept_id} declara {lineage}, mas lineage_ref "
                    f"'{lineage_ref}' é {anchor.get('lineage')}"
                )
            if lineage == "own_winner" and (
                anchor.get("evidence_level") != "performance_data"
                or not anchor.get("performance_metrics")
            ):
                errors.append(
                    f"concept {concept_id} own_winner exige lineage_ref com "
                    "performance_data e performance_metrics"
                )
        if not concept.get("agent_rationale"):
            errors.append(f"concept {concept_id} sem agent_rationale")
    return {"errors": errors, "warnings": warnings}


def execution_binding(
    recipe: dict,
    concept: dict,
    research_by_id: dict[str, dict],
) -> tuple[str, str | None]:
    """Resolve format/structure evidence independently from concept lineage."""
    execution_ref = recipe.get("execution_ref")
    if not execution_ref:
        return "original", None
    anchor = research_by_id.get(execution_ref) or {}
    return str(anchor.get("lineage") or ""), execution_ref


def recipe_binding_errors(
    recipe: dict,
    brief: dict,
    recipe_name: str,
    *,
    research_by_id: dict[str, dict] | None = None,
) -> list:
    errors = []
    brief_ref = recipe.get("brief_ref")
    if not brief_ref:
        errors.append(f"[{recipe_name}] sem brief_ref")
    elif brief_ref != brief.get("id"):
        errors.append(
            f"[{recipe_name}] brief_ref '{brief_ref}' diverge do brief '{brief.get('id')}'"
        )
    concept_id = recipe.get("concept_id")
    concepts = {
        item.get("id"): item for item in brief.get("concepts", []) or []
    }
    known = set(concepts)
    if not concept_id:
        errors.append(f"[{recipe_name}] sem concept_id")
    elif concept_id not in known:
        errors.append(
            f"[{recipe_name}] concept_id '{concept_id}' não existe no brief '{brief.get('id')}'"
        )
    else:
        concept_refs = set(concepts[concept_id].get("research_refs", []) or [])
        recipe_refs = set(recipe.get("research_refs", []) or [])
        if not recipe_refs.issubset(concept_refs):
            errors.append(
                f"[{recipe_name}] research_refs divergem do concept '{concept_id}'"
            )
        concept = concepts[concept_id]
        if "lineage_ref" in recipe:
            errors.append(
                f"[{recipe_name}] lineage_ref pertence ao concept; use execution_ref "
                "para uma referência de formato/execução"
            )
        execution_ref = recipe.get("execution_ref")
        if execution_ref and execution_ref not in recipe_refs:
            errors.append(
                f"[{recipe_name}] research_refs precisa conter execution_ref "
                f"'{execution_ref}'"
            )
        if execution_ref and research_by_id is not None and execution_ref not in research_by_id:
            errors.append(
                f"[{recipe_name}] execution_ref inexistente: {execution_ref}"
            )
    target_markets = recipe.get("target_markets")
    if not isinstance(target_markets, list) or not target_markets:
        errors.append(f"[{recipe_name}] target_markets vazio ou ausente")
    elif not all(
        isinstance(market, str) and market.strip() for market in target_markets
    ):
        errors.append(f"[{recipe_name}] target_markets precisa conter IDs string")
    else:
        if len(target_markets) != len(set(target_markets)):
            errors.append(f"[{recipe_name}] target_markets contém duplicatas")
        outside = sorted(set(target_markets) - set(brief.get("markets", []) or []))
        if outside:
            errors.append(
                f"[{recipe_name}] target_markets fora do brief: " + ", ".join(outside)
            )
    if brief.get("status") != "approved":
        errors.append(f"[{recipe_name}] brief '{brief.get('id')}' não está approved")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="creative-forge — agent-authored brief gate")
    parser.add_argument("--app", required=True)
    parser.add_argument("--brief")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    paths = (
        [Path(args.brief)]
        if args.brief
        else sorted((ROOT / "briefs" / args.app).glob("*.yaml"))
    )
    research_data = research.load_yaml(ROOT / "swipe" / args.app / "competitors.yaml")
    research_by_id = {
        item.get("id"): item
        for item in research_data.get("creatives", []) or []
        if item.get("id")
    }
    app = load_yaml(ROOT / "apps" / f"{args.app}.yaml")
    supported_markets = {
        market.get("id")
        for market in (app.get("locales", {}) or {}).get("markets", []) or []
        if isinstance(market, dict) and market.get("id")
    }
    results = []
    for path in paths:
        result = audit_brief(
            load_yaml(path),
            expected_app=args.app,
            research_by_id=research_by_id,
            supported_markets=supported_markets,
        )
        results.append({"path": str(path), **result})
    errors = [error for result in results for error in result["errors"]]
    if args.json:
        print(json.dumps({"results": results, "errors": errors}, ensure_ascii=False, indent=2))
    else:
        for result in results:
            for warning in result["warnings"]:
                print(f"  ⚠️  {result['path']}: {warning}")
            for error in result["errors"]:
                print(f"  ❌ {result['path']}: {error}")
        print(f"briefs={'PASS' if not errors else 'BLOCKED'}")
    if errors or not paths:
        if not paths and not args.json:
            print(f"  ❌ nenhum brief em briefs/{args.app}")
        sys.exit(1)


if __name__ == "__main__":
    main()
