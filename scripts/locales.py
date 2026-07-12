#!/usr/bin/env python3
"""
creative-forge · locales.py — the "understand the app" layer.

Audits three independent truth surfaces: in-app `.lproj` resources, Fastlane
storefront metadata and configured acquisition markets/copy languages.

This is what makes the workflow app-aware instead of guessing.

    python3 scripts/locales.py --app sunrise-demo
"""
import argparse
import sys
from pathlib import Path

try:
    from scripts import paths
except ImportError:
    import paths

ROOT = paths.default_root()


def die(m: str) -> None:
    sys.exit(f"creative-forge: {m}")


def load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError:
        die("PyYAML ausente. Rode: pip3 install -r requirements.txt")
    with open(path) as f:
        return yaml.safe_load(f) or {}


def base(loc: str) -> str:
    return str(loc).split("-")[0]


def config_path(root: Path, value: str) -> Path:
    return paths.resolve_config_path(root, value)


def audit_locale_strategy(root: Path, app: dict) -> dict:
    """Audit app resources, App Store metadata and acquisition markets separately."""
    errors, warnings = [], []
    cfg = app.get("locales", {}) or {}
    copy_languages = sorted({base(x) for x in cfg.get("copy_languages", []) or []})
    fallback = base(cfg.get("fallback_copy_language", ""))

    if not copy_languages:
        errors.append("locales.copy_languages vazio")
    if not fallback:
        errors.append("locales.fallback_copy_language ausente")
    elif fallback not in copy_languages:
        errors.append(f"fallback_copy_language '{fallback}' não está em copy_languages")

    app_locales = set()
    resource_paths = cfg.get("app_resource_paths", []) or []
    if not resource_paths:
        errors.append("locales.app_resource_paths vazio")
    for raw_path in resource_paths:
        path = config_path(root, raw_path)
        if not path.exists():
            errors.append(f"app_resource_path não encontrado: {raw_path}")
            continue
        app_locales.update(p.name.removesuffix(".lproj") for p in path.rglob("*.lproj") if p.is_dir())

    storefront_locales = set()
    metadata_value = cfg.get("storefront_metadata_path")
    if not metadata_value:
        errors.append("locales.storefront_metadata_path ausente")
    else:
        metadata_path = config_path(root, metadata_value)
        if not metadata_path.exists():
            errors.append(f"storefront_metadata_path não encontrado: {metadata_value}")
        else:
            skip = {"review_information", "default"}
            storefront_locales.update(
                p.name for p in metadata_path.iterdir() if p.is_dir() and p.name not in skip
            )

    markets = cfg.get("markets", []) or []
    if not markets:
        errors.append("locales.markets vazio")
    seen_ids = set()
    seen_storefronts = set()
    for market in markets:
        if not isinstance(market, dict):
            errors.append("cada market precisa ser um objeto estruturado")
            continue
        market_id = market.get("id")
        if not market_id:
            errors.append("market sem id")
        elif market_id in seen_ids:
            errors.append(f"market id duplicado: {market_id}")
        else:
            seen_ids.add(market_id)
        if not market.get("countries"):
            errors.append(f"market {market_id or '?'} sem countries")
        app_locale = market.get("app_locale")
        storefront = market.get("storefront_locale")
        if storefront in seen_storefronts:
            errors.append(
                f"storefront_locale duplicado entre markets: {storefront}"
            )
        elif storefront:
            seen_storefronts.add(storefront)
        copy_language = base(market.get("copy_language", ""))
        if app_locale not in app_locales:
            errors.append(f"market {market_id or '?'} usa app_locale {app_locale} ausente no app")
        if storefront not in storefront_locales:
            errors.append(
                f"market {market_id or '?'} usa storefront_locale {storefront} ausente no Fastlane"
            )
        if copy_language not in copy_languages:
            errors.append(
                f"market {market_id or '?'} usa copy_language {copy_language or '?'} fora de copy_languages"
            )
        if cfg.get("require_native_market_copy") is True:
            native_language = base(app_locale or "")
            if copy_language != native_language:
                errors.append(
                    f"market {market_id or '?'} exige copy nativa '{native_language}', "
                    f"mas usa '{copy_language or '?'}'"
                )

    return {
        "errors": errors,
        "warnings": warnings,
        "app_locales": sorted(app_locales),
        "storefront_locales": sorted(storefront_locales),
        "copy_languages": copy_languages,
        "markets": markets,
        "fallback_copy_language": fallback,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="creative-forge — locale strategy vs app reality")
    ap.add_argument("--app", required=True)
    args = ap.parse_args()

    app = load_yaml(ROOT / "apps" / f"{args.app}.yaml")
    if not app.get("locales"):
        die(f"{args.app} não define bloco 'locales'.")
    result = audit_locale_strategy(ROOT, app)
    print(f"creative-forge · locales · {args.app}")
    print(f"  app locales: {', '.join(result['app_locales']) or '—'}")
    print(f"  storefront locales: {', '.join(result['storefront_locales']) or '—'}")
    print(f"  copy languages: {', '.join(result['copy_languages']) or '—'}")
    print(f"  markets ({len(result['markets'])}):")
    for market in result["markets"]:
        if not isinstance(market, dict):
            continue
        print(
            f"    · {market.get('id', '?')}: countries={','.join(market.get('countries', []))} "
            f"store={market.get('storefront_locale')} app={market.get('app_locale')} "
            f"copy={market.get('copy_language')}"
        )
    for warning in result["warnings"]:
        print(f"  ⚠️  {warning}")
    for error in result["errors"]:
        print(f"  ❌ {error}")
    if result["errors"]:
        sys.exit(1)
    print("  ✓ locale strategy consistente")


if __name__ == "__main__":
    main()
