#!/usr/bin/env python3
"""
creative-forge · validate.py  (locale-aware consistency gate)

For every recipe, checks each locale's copy against the template's char limits
and the app's brand voice — approved CTAs PER LANGUAGE, banned words, required
fields, claims evidence, assets, review provenance, formats, placeholders and
complete copy-language coverage.

Blocks (exit 1) on any error. Run before render.

    python3 scripts/validate.py --app sunrise-demo
    python3 scripts/validate.py --app sunrise-demo --recipe morning-walk
"""
import argparse
import sys
from pathlib import Path

try:
    from scripts import paths, render
except ImportError:
    import paths
    import render

ROOT = Path(__file__).resolve().parent.parent
ALL_FORMATS = ["square", "portrait", "story", "landscape"]


def die(m: str) -> None:
    sys.exit(f"creative-forge: {m}")


def load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError:
        die("PyYAML ausente. Rode: pip3 install -r requirements.txt")
    with open(path) as f:
        return yaml.safe_load(f) or {}


def norm(s) -> str:
    return str(s if s is not None else "").lower()


def base_lang(loc: str) -> str:
    return str(loc).split("-")[0]


def config_path(value: str) -> Path:
    return paths.resolve_config_path(ROOT, value)


def validate_app(app: dict):
    errors, warnings = [], []
    for asset_name, value in (app.get("assets", {}) or {}).items():
        if value and not config_path(value).exists():
            errors.append(f"[app] asset não encontrado ({asset_name}): {value}")
    for claim_name, claim in (app.get("claims", {}) or {}).items():
        evidence = (claim or {}).get("evidence", {}) or {}
        path = evidence.get("path")
        if not evidence:
            errors.append(f"[app] claim '{claim_name}' sem evidence")
        elif path and not config_path(path).exists():
            errors.append(f"[app] evidence da claim '{claim_name}' não encontrado: {path}")
    return errors, warnings


def check_copy(tag, lang, copy, meta, voice, errors, warnings):
    """Validate one locale's copy block."""
    fields = meta.get("copy_fields", {}) or {}
    limits = meta.get("limits", {}) or {}

    for f in fields:
        if not copy.get(f):
            errors.append(f"{tag} falta o campo '{f}'")
    for f, limit in limits.items():
        v = copy.get(f)
        if v and len(str(v)) > limit:
            errors.append(f"{tag} '{f}' tem {len(str(v))} chars (máx {limit}) → estoura o layout")

    # CTA approved for THIS language (approved_ctas may be a per-lang dict or a flat list)
    ctas_cfg = voice.get("approved_ctas", {}) or {}
    if isinstance(ctas_cfg, dict) and lang not in ctas_cfg:
        errors.append(f"{tag} não existe política de CTA para '{lang}'")
        ctas = []
    else:
        ctas = ctas_cfg.get(lang) if isinstance(ctas_cfg, dict) else ctas_cfg
    cta = copy.get("cta")
    if ctas and cta and cta not in ctas:
        errors.append(f"{tag} CTA '{cta}' fora da lista aprovada ({lang})")

    # banned hype/claim words anywhere in this locale's copy
    blob = norm(" ".join(str(v) for v in copy.values()))
    banned_cfg = voice.get("banned", []) or []
    if isinstance(banned_cfg, dict):
        banned = (banned_cfg.get("global", []) or []) + (banned_cfg.get(lang, []) or [])
    else:
        banned = banned_cfg
    for b in (norm(x) for x in banned):
        if b and b in blob:
            errors.append(f"{tag} usa termo proibido '{b}'")

    # soft: does the headline reach for a brand anchor?
    anchors_cfg = voice.get("anchors", []) or []
    anchors_raw = anchors_cfg.get(lang, []) if isinstance(anchors_cfg, dict) else anchors_cfg
    anchors = [norm(a) for a in anchors_raw]
    if anchors:
        head = norm(" ".join(str(copy.get(k, "")) for k in
                    ("pain", "headline", "headline_accent", "verse", "kicker", "quote")))
        if head and not any(a in head for a in anchors):
            warnings.append(f"{tag} nenhuma âncora de marca na headline")


def check_ad_copy(tag, lang, copy, voice, errors):
    if not isinstance(copy, dict):
        errors.append(f"{tag} ad_copy localizada ausente")
        return
    for field in ("primary_text", "headline"):
        if not str(copy.get(field) or "").strip():
            errors.append(f"{tag} ad_copy.{field} ausente")
    blob = norm(" ".join(str(value) for value in copy.values()))
    banned_cfg = voice.get("banned", []) or []
    banned = (
        (banned_cfg.get("global", []) or []) + (banned_cfg.get(lang, []) or [])
        if isinstance(banned_cfg, dict)
        else banned_cfg
    )
    for term in (norm(value) for value in banned):
        if term and term in blob:
            errors.append(f"{tag} ad_copy usa termo proibido '{term}'")


def validate_recipe(app, voice, recipe_path):
    errors, warnings = [], []
    stem = recipe_path.stem
    recipe = load_yaml(recipe_path)

    tpl_name = recipe.get("template")
    if not tpl_name:
        return [f"[{stem}] recipe sem 'template'"], []
    if not (ROOT / "templates" / "image" / tpl_name / "template.html").exists():
        return [f"[{stem}] template inexistente: {tpl_name}"], []
    meta = load_yaml(ROOT / "templates" / "image" / tpl_name / "meta.yaml")

    # format supported by this template?
    fmt = recipe.get("format", "square")
    if fmt not in meta.get("formats", ALL_FORMATS):
        errors.append(f"[{stem}] formato '{fmt}' não suportado por {tpl_name}")

    # collect (locale_key, copy) blocks — multi-locale or legacy single
    if recipe.get("locales"):
        blocks = [(k, v or {}) for k, v in recipe["locales"].items()]
    else:
        blocks = [(recipe.get("lang", "pt"), recipe.get("copy", {}) or {})]

    for key, copy in blocks:
        check_copy(f"[{stem}/{key}]", base_lang(key), copy, meta, voice, errors, warnings)

    # Production coverage is recipe-scoped. target_markets is the source of
    # truth; a concept must not silently expand to every configured app market.
    try:
        target_markets = render.recipe_target_markets(app, recipe)
    except ValueError as exc:
        errors.append(f"[{stem}] {exc}")
        target_markets = []
    required = (
        {market["copy_language"] for market in target_markets}
        if target_markets
        else {
            base_lang(x)
            for x in (app.get("locales", {}) or {}).get("copy_languages", [])
        }
    )
    covered = {base_lang(k) for k, _ in blocks}
    for miss in sorted(required - covered):
        errors.append(f"[{stem}] sem copy obrigatória em '{miss}'")

    ad_copy = recipe.get("ad_copy")
    if not isinstance(ad_copy, dict):
        errors.append(f"[{stem}] ad_copy localizada ausente")
        ad_copy = {}
    for language in sorted(required):
        localized_ad = ad_copy.get(language)
        if not isinstance(localized_ad, dict):
            errors.append(f"[{stem}] sem ad_copy obrigatória em '{language}'")
        else:
            check_ad_copy(f"[{stem}/{language}]", language, localized_ad, voice, errors)

    overrides = recipe.get("market_overrides", {}) or {}
    if not isinstance(overrides, dict):
        errors.append(f"[{stem}] market_overrides precisa ser um objeto")
        overrides = {}
    target_ids = {market["id"] for market in target_markets}
    for market_id in sorted(set(overrides) - target_ids):
        errors.append(
            f"[{stem}] market_overrides.{market_id} não pertence a target_markets"
        )
    required_overrides = render.required_market_override_ids(target_markets)
    for market in target_markets:
        market_id = market["id"]
        override = overrides.get(market_id)
        if market_id in required_overrides and not isinstance(override, dict):
            errors.append(
                f"[{stem}] market_overrides.{market_id} obrigatório: "
                f"copy_language '{market['copy_language']}' é compartilhado por mais de um target market"
            )
            continue
        if override is None:
            continue
        if not isinstance(override, dict):
            errors.append(f"[{stem}] market_overrides.{market_id} precisa ser um objeto")
            continue
        override_copy = override.get("copy")
        override_ad_copy = override.get("ad_copy")
        if not isinstance(override_copy, dict):
            errors.append(f"[{stem}] market_overrides.{market_id}.copy ausente")
        else:
            check_copy(
                f"[{stem}/{market_id}]",
                market["copy_language"],
                override_copy,
                meta,
                voice,
                errors,
                warnings,
            )
        check_ad_copy(
            f"[{stem}/{market_id}]",
            market["copy_language"],
            override_ad_copy,
            voice,
            errors,
        )

    # Every factual claim must be backed by app-level evidence.
    known_claims = app.get("claims", {}) or {}
    for claim in recipe.get("claims_used", []) or []:
        claim_cfg = known_claims.get(claim) or {}
        if not claim_cfg or not claim_cfg.get("evidence"):
            errors.append(f"[{stem}] claim não comprovada: '{claim}'")

    # Some templates require source evidence, e.g. a real App Store review.
    required_evidence = meta.get("requires_evidence")
    if required_evidence:
        evidence = recipe.get("evidence", {}) or {}
        if evidence.get("kind") != required_evidence or not evidence.get("source_url"):
            errors.append(
                f"[{stem}] falta evidência {required_evidence} com source_url verificável"
            )

    image_file = (recipe.get("image", {}) or {}).get("file")
    if image_file and not config_path(image_file).exists():
        errors.append(f"[{stem}] imagem não encontrada: {image_file}")

    # Current image templates retain competitor-pattern lineage.
    if not recipe.get("swiped_from"):
        errors.append(
            f"[{stem}] sem 'swiped_from' — declare o padrão observado que foi adaptado"
        )
    raw = recipe_path.read_text()
    if "⚠️" in raw or "PLACEHOLDER" in raw.upper():
        errors.append(f"[{stem}] contém PLACEHOLDER — publicação bloqueada")

    return errors, warnings


def main() -> None:
    ap = argparse.ArgumentParser(description="creative-forge — validate recipes (locale-aware)")
    ap.add_argument("--app", required=True)
    ap.add_argument("--recipe")
    args = ap.parse_args()

    app_path = ROOT / "apps" / f"{args.app}.yaml"
    if not app_path.exists():
        die(f"app config inexistente: {app_path}")
    app = load_yaml(app_path)
    voice = app.get("voice", {}) or {}
    if not voice:
        print(f"⚠️  {args.app} não define 'voice' — pulando checagem de consistência de marca.")

    rdir = ROOT / "recipes" / args.app
    if args.recipe:
        p = rdir / (args.recipe if args.recipe.endswith(".yaml") else f"{args.recipe}.yaml")
        if not p.exists():
            die(f"recipe não encontrada: {args.recipe}")
        recipes = [p]
    else:
        recipes = sorted(rdir.glob("*.yaml"))
        if not recipes:
            die(f"nenhuma recipe em {rdir}")

    all_err, all_warn = validate_app(app)
    for r in recipes:
        e, w = validate_recipe(app, voice, r)
        all_err += e
        all_warn += w

    print(f"creative-forge · validate · app={args.app} · {len(recipes)} recipe(s)")
    for w in all_warn:
        print(f"  ⚠️  {w}")
    for e in all_err:
        print(f"  ❌ {e}")
    print()
    if all_err:
        print(f"✗ {len(all_err)} erro(s), {len(all_warn)} aviso(s) — corrija os erros antes de gerar.")
        sys.exit(1)
    print(f"✓ tudo consistente: {len(recipes)} recipe(s), {len(all_warn)} aviso(s).")


if __name__ == "__main__":
    main()
