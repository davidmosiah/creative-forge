#!/usr/bin/env python3
"""
creative-forge · audiences.py  (fail-closed audience plans)

Every paid audience is declared per app in audiences/<app>.yaml with hypothesis,
funnel stage, data provenance, confidence and an explicit approval. The publish
manifest requires an approved audience and inherits its market — so creatives
and audience can never drift apart.

Policy encoded here (Meta):
- We copy competitors' PUBLIC signals (country, language, angle, placement),
  never their private audiences (Custom/Lookalike/pixel data — own data only).
- No religious/sensitive interest segmentation: context comes from the
  creative + language + country. Interest targeting is off unless the app's
  plan policy explicitly allows it (with real verified IDs).
- Lookalikes require own seed data with real volume; without it they stay
  blocked instead of pretending.

    python3 scripts/audiences.py --app sunrise-demo
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FUNNEL_STAGES = {"cold", "warm", "high_intent", "lookalike"}
TARGETING_KINDS = {"broad", "custom_audience", "lookalike"}
OPTIMIZATION_EVENTS = {"app_install", "purchase", "trial_start"}
CONFIDENCE_LEVELS = {"low", "medium", "high"}
STATUSES = {"draft", "approved"}
OWN_DATA_SOURCES = {"posthog_signals", "revenuecat", "meta_pixel", "meta_sdk"}
OWN_SEED_KINDS = {"own_purchasers", "own_subscribers", "own_retained_users"}
MIN_LOOKALIKE_SEED = 100


def die(m: str) -> None:
    sys.exit(f"creative-forge: {m}")


def load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError:
        die("PyYAML ausente. Rode: pip3 install -r requirements.txt")
    with open(path) as f:
        return yaml.safe_load(f) or {}


def get_audience(plan: dict, audience_id: str) -> dict | None:
    for audience in plan.get("audiences", []) or []:
        if audience.get("id") == audience_id:
            return audience
    return None


def _check_targeting(tag, audience, market, policy, errors):
    targeting = audience.get("targeting", {}) or {}
    kind = targeting.get("kind")
    if kind not in TARGETING_KINDS:
        errors.append(f"{tag} targeting.kind inválido: {kind}")
        return

    market_countries = sorted(market.get("countries", []) or [])
    countries = sorted(targeting.get("countries", []) or [])
    if countries != market_countries:
        errors.append(
            f"{tag} targeting.countries {countries} diverge do market "
            f"'{market.get('id')}' {market_countries}"
        )

    if targeting.get("interests") and not policy.get("allow_interest_targeting", False):
        errors.append(
            f"{tag} interest targeting proibido pela policy do plano "
            "(contexto vem do criativo/idioma/país)"
        )

    stage = audience.get("funnel_stage")
    if stage == "cold" and kind != "broad":
        errors.append(f"{tag} cold prospecting exige targeting broad, não {kind}")
    if stage in ("warm", "high_intent"):
        if kind != "custom_audience":
            errors.append(f"{tag} estágio {stage} (warm/high-intent) exige custom_audience própria")
        source_kind = (audience.get("data_source", {}) or {}).get("kind")
        if source_kind not in OWN_DATA_SOURCES:
            errors.append(
                f"{tag} estágio {stage} exige data_source próprio "
                f"({', '.join(sorted(OWN_DATA_SOURCES))}), recebido: {source_kind}"
            )
    if stage == "lookalike" or kind == "lookalike":
        seed = targeting.get("seed", {}) or {}
        if seed.get("kind") not in OWN_SEED_KINDS:
            errors.append(
                f"{tag} lookalike exige seed de dados próprios "
                f"({', '.join(sorted(OWN_SEED_KINDS))})"
            )
        size = seed.get("size")
        if not isinstance(size, int) or size < MIN_LOOKALIKE_SEED:
            errors.append(
                f"{tag} lookalike exige seed.size >= {MIN_LOOKALIKE_SEED} "
                f"(recebido: {size}) — sem volume próprio, lookalike fica bloqueado"
            )


def audit_plan(plan: dict, markets: list) -> dict:
    """Validate an audience plan against the app's declared markets."""
    errors, warnings = [], []
    if not plan.get("version"):
        errors.append("[plan] sem 'version'")
    if not plan.get("updated_at"):
        errors.append("[plan] sem 'updated_at'")
    policy = plan.get("policy", {}) or {}
    market_map = {market.get("id"): market for market in markets}

    audience_list = plan.get("audiences", []) or []
    if not audience_list:
        errors.append("[plan] nenhuma audience declarada")

    seen_ids = set()
    for audience in audience_list:
        aud_id = audience.get("id") or "?"
        tag = f"[{aud_id}]"
        if aud_id in seen_ids:
            errors.append(f"{tag} id duplicado")
        seen_ids.add(aud_id)

        market = market_map.get(audience.get("market"))
        if market is None:
            errors.append(
                f"{tag} market '{audience.get('market')}' não existe em "
                "apps/<app>.yaml locales.markets"
            )
            continue

        if audience.get("funnel_stage") not in FUNNEL_STAGES:
            errors.append(f"{tag} funnel_stage inválido: {audience.get('funnel_stage')}")
        if not str(audience.get("hypothesis") or "").strip():
            errors.append(f"{tag} sem hypothesis")
        if audience.get("confidence") not in CONFIDENCE_LEVELS:
            errors.append(f"{tag} confidence inválida: {audience.get('confidence')}")
        if not str(audience.get("confidence_rationale") or "").strip():
            errors.append(f"{tag} sem confidence_rationale")
        if audience.get("optimization_event") not in OPTIMIZATION_EVENTS:
            errors.append(
                f"{tag} optimization_event inválido: {audience.get('optimization_event')} "
                f"(use {', '.join(sorted(OPTIMIZATION_EVENTS))})"
            )

        status = audience.get("status")
        if status not in STATUSES:
            errors.append(f"{tag} status inválido: {status}")
        if status == "approved" and not audience.get("approved_by"):
            errors.append(f"{tag} approved exige approved_by (quem e quando)")

        copy_language = (audience.get("creatives", {}) or {}).get("copy_language")
        if copy_language != market.get("copy_language"):
            errors.append(
                f"{tag} creatives.copy_language '{copy_language}' diverge do market "
                f"'{market.get('id')}' ({market.get('copy_language')})"
            )

        _check_targeting(tag, audience, market, policy, errors)

        if audience.get("confidence") == "low":
            warnings.append(
                f"{tag} confidence low — trate como teste pequeno, não como aposta"
            )

    return {"errors": errors, "warnings": warnings}


def main() -> None:
    try:
        from scripts import render
    except ImportError:
        import render

    ap = argparse.ArgumentParser(description="creative-forge — audit audience plan")
    ap.add_argument("--app", required=True)
    args = ap.parse_args()

    app_path = ROOT / "apps" / f"{args.app}.yaml"
    if not app_path.exists():
        die(f"app config inexistente: {app_path}")
    plan_path = ROOT / "audiences" / f"{args.app}.yaml"
    if not plan_path.exists():
        die(f"sem audience plan: {plan_path} (publish bloqueia sem ele)")

    app = render.load_yaml(app_path)
    plan = load_yaml(plan_path)
    if plan.get("app") != args.app:
        die(f"audience plan declara app '{plan.get('app')}', esperado '{args.app}'")
    result = audit_plan(plan, render.app_target_markets(app))

    print(f"creative-forge · audiences · app={args.app} · "
          f"{len(plan.get('audiences', []) or [])} audience(s)")
    for warning in result["warnings"]:
        print(f"  ⚠️  {warning}")
    for error in result["errors"]:
        print(f"  ❌ {error}")
    if result["errors"]:
        sys.exit(1)
    approved = sum(
        1 for a in plan.get("audiences", []) or [] if a.get("status") == "approved"
    )
    print(f"✓ plano válido: {approved} approved, "
          f"{len(plan.get('audiences', []) or []) - approved} draft.")


if __name__ == "__main__":
    main()
