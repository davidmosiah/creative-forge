#!/usr/bin/env python3
"""Validate fresh acquisition signals and rank markets without inventing ROAS."""

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts.paths import default_root
except ImportError:
    from paths import default_root

ROOT = default_root()


def load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError:
        sys.exit("creative-forge: PyYAML ausente. Rode: pip3 install -r requirements.txt")
    if not path.exists():
        sys.exit(f"creative-forge: signals inexistente: {path}")
    return yaml.safe_load(path.read_text()) or {}


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def audit_signals(
    snapshot: dict,
    now: datetime | None = None,
    expected_app: str | None = None,
    expected_app_name: str | None = None,
) -> dict:
    errors, warnings = [], []
    now = now or datetime.now(timezone.utc)
    if snapshot.get("version") != 1:
        errors.append("signals.version precisa ser 1")
    if not snapshot.get("app"):
        errors.append("signals.app ausente")
    if expected_app is not None and snapshot.get("app") != expected_app:
        errors.append(
            f"signals.app '{snapshot.get('app')}' diverge do app solicitado "
            f"'{expected_app}'"
        )
    try:
        observed_at = parse_time(snapshot.get("observed_at"))
        expires_at = parse_time(snapshot.get("expires_at"))
        if expires_at <= observed_at:
            errors.append("expires_at precisa ser posterior a observed_at")
        if now > expires_at:
            errors.append(f"snapshot expirado em {expires_at.isoformat()}")
    except (TypeError, ValueError):
        errors.append("observed_at/expires_at inválidos; use ISO-8601 com timezone")

    sources = snapshot.get("sources", []) or []
    posthog_sources = [source for source in sources if source.get("kind") == "posthog"]
    if not posthog_sources:
        errors.append("fonte PostHog ausente")
    for source in posthog_sources:
        if not source.get("project_id"):
            errors.append("fonte PostHog sem project_id")
        raw_filters = source.get("production_filters", []) or []
        filters = set(raw_filters)
        for required in ("not_testflight", "not_emulator"):
            if required not in filters:
                errors.append(f"fonte PostHog sem filtro obrigatório {required}")
        app_name_filters = [
            str(value) for value in raw_filters if str(value).startswith("app_name=")
        ]
        if expected_app_name is not None:
            expected_filter = f"app_name={expected_app_name}"
            if len(app_name_filters) != 1:
                rendered = ", ".join(app_name_filters) or "nenhum"
                errors.append(
                    "fonte PostHog precisa ter exatamente um filtro "
                    f"{expected_filter}; recebeu: {rendered}"
                )
            elif app_name_filters[0] != expected_filter:
                errors.append(
                    f"fonte PostHog usa {app_name_filters[0]}, esperado "
                    f"{expected_filter} para o produto {expected_app_name}"
                )
        elif not app_name_filters:
            errors.append("fonte PostHog sem filtro app_name")
        if not source.get("window_days"):
            errors.append("fonte PostHog sem window_days")

    paid_sources = [source for source in sources if source.get("kind") == "paid_media"]
    usable_paid = [source for source in paid_sources if source.get("status") == "available"]
    if not usable_paid:
        warnings.append("ROAS indisponível: snapshot não contém spend/revenue de mídia paga")

    countries = snapshot.get("countries", {}) or {}
    if not countries:
        errors.append("signals.countries vazio")
    for country, metrics in countries.items():
        if len(country) != 2:
            errors.append(f"country code inválido: {country}")
        for key, value in (metrics or {}).items():
            if not isinstance(value, (int, float)) or value < 0:
                errors.append(f"{country}.{key} precisa ser número não-negativo")
    return {"errors": errors, "warnings": warnings}


def rank_countries(snapshot: dict) -> list:
    ranked = []
    for country, metrics in (snapshot.get("countries", {}) or {}).items():
        opened = float(metrics.get("opened_users", 0) or 0)
        denominator = max(opened, 1.0)
        activation = float(metrics.get("ritual_users", 0) or 0) / denominator
        retention = float(metrics.get("retained_users", 0) or 0) / denominator
        intent = float(metrics.get("purchase_started_users", 0) or 0) / denominator
        purchases = float(metrics.get("purchase_users", 0) or 0) / denominator
        trials = float(metrics.get("trial_users", 0) or 0) / denominator
        renewals = float(metrics.get("renewal_users", 0) or 0) / denominator
        score = (
            math.log1p(opened) * 7
            + activation * 30
            + retention * 50
            + intent * 10
            + purchases * 120
            + trials * 60
            + renewals * 8
        )
        confidence = "high" if opened >= 100 else "medium" if opened >= 50 else "low"
        ranked.append(
            {
                "country": country,
                "score": round(score, 2),
                "confidence": confidence,
                "opened_users": int(opened),
                "activation_rate": round(activation, 4),
                "retention_rate": round(retention, 4),
                "purchase_rate": round(purchases, 4),
            }
        )
    return sorted(ranked, key=lambda item: (-item["score"], item["country"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="creative-forge — fresh market signals")
    parser.add_argument("--app", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    snapshot = load_yaml(ROOT / "signals" / f"{args.app}.yaml")
    app = load_yaml(ROOT / "apps" / f"{args.app}.yaml")
    audit = audit_signals(
        snapshot,
        expected_app=args.app,
        expected_app_name=app.get("name"),
    )
    ranking = rank_countries(snapshot)
    result = {**audit, "ranking": ranking}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"creative-forge · signals · {args.app}")
        for warning in audit["warnings"]:
            print(f"  ⚠️  {warning}")
        for error in audit["errors"]:
            print(f"  ❌ {error}")
        print("  ranking:")
        for item in ranking[:10]:
            print(
                f"    · {item['country']}: score={item['score']:.2f} "
                f"confidence={item['confidence']} opens={item['opened_users']}"
            )
    if audit["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
