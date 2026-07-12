#!/usr/bin/env python3
"""
creative-forge · render.py

Turn an app config + a creative recipe + an HTML template into a ready-to-upload
ad image (PNG) — fully headless, no SaaS, no login. The only inputs are plain
YAML the agent (or a human) can edit. See PLAYBOOK.md for the full loop.

Usage:
    python3 scripts/render.py --app sunrise-demo --recipe morning-walk
    python3 scripts/render.py --app sunrise-demo --all
    python3 scripts/render.py --app sunrise-demo --recipe morning-walk --format story

Formats use template-declared support and format-specific safe zones:
    square   1080x1080   Meta feed / IG feed
    portrait 1080x1350   Meta/IG feed (max vertical real estate)
    story    1080x1920   Stories / Reels / TikTok
    landscape 1200x628   horizontal/link ads

Output lands in output/<app>/<recipe>--<storefront-locale>--<format>.png
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import html as html_lib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from scripts.paths import default_root
except ImportError:
    from paths import default_root

ROOT = default_root()

FORMATS = {
    "square":    (1080, 1080),   # 1:1     Meta / IG feed
    "portrait":  (1080, 1350),   # 4:5     feed, max vertical estate
    "story":     (1080, 1920),   # 9:16    Stories / Reels / TikTok
    "landscape": (1200, 628),    # 1.91:1  link ad / horizontal feed
}


def normalize_jobs(value: int) -> int:
    jobs = int(value)
    if jobs < 1 or jobs > 8:
        raise ValueError("jobs precisa estar entre 1 e 8")
    return jobs

# Headless renderer. Override with CHROME_BIN if Chrome lives elsewhere.
CHROME = os.environ.get(
    "CHROME_BIN",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def die(msg: str) -> None:
    sys.exit(f"creative-forge: {msg}")


def load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError:
        die("PyYAML não está instalado. Rode:  pip3 install -r requirements.txt")
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        die(f"arquivo não encontrado: {path}")


def dotted(ctx: dict, path: str) -> str:
    """Resolve 'a.b.c' against nested dicts. Missing/None -> '' (fields optional)."""
    cur = ctx
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return ""
    return "" if cur is None else str(cur)


def render_template(html: str, ctx: dict) -> str:
    # {{{ raw }}} first — no escaping, for inline SVG / HTML.
    html = re.sub(r"\{\{\{\s*(.+?)\s*\}\}\}", lambda m: dotted(ctx, m.group(1).strip()), html)
    # {{ var }} — escaped plain text substitution.
    html = re.sub(
        r"\{\{\s*(.+?)\s*\}\}",
        lambda m: html_lib.escape(dotted(ctx, m.group(1).strip()), quote=True),
        html,
    )
    return html


def base_lang(locale: str) -> str:
    return str(locale).split("-")[0]


def app_target_markets(app: dict) -> list:
    cfg = app.get("locales", {}) or {}
    markets = cfg.get("markets", []) or []
    if not markets:
        raise ValueError("app sem locales.markets estruturado")
    normalized = []
    seen_ids = set()
    seen_storefronts = set()
    for market in markets:
        if not isinstance(market, dict):
            raise ValueError("cada locale market precisa ser um objeto")
        market_id = market.get("id")
        storefront = market.get("storefront_locale")
        app_locale = market.get("app_locale")
        copy_language = market.get("copy_language")
        if not market_id or not storefront or not app_locale or not copy_language:
            raise ValueError(
                f"market {market.get('id', '?')} precisa de storefront_locale, "
                "app_locale, copy_language e id"
            )
        if market_id in seen_ids:
            raise ValueError(f"market id duplicado: {market_id}")
        seen_ids.add(market_id)
        if storefront in seen_storefronts:
            raise ValueError(
                f"storefront_locale duplicado entre markets: {storefront}; "
                "paths e receipts exigem storefront locale único por market_id"
            )
        seen_storefronts.add(storefront)
        normalized.append(
            {
                **market,
                "locale": storefront,
                "app_locale": app_locale,
                "copy_language": base_lang(copy_language),
            }
        )
    return normalized


def recipe_target_markets(app: dict, recipe: dict) -> list:
    """Resolve a recipe's explicit market IDs to the app's canonical markets.

    Recipe order is preserved so an agent-authored rollout remains auditable.
    Missing, duplicate, or unknown market IDs fail closed instead of silently
    expanding the render matrix to every configured app market.
    """
    target_ids = recipe.get("target_markets")
    if not isinstance(target_ids, list) or not target_ids:
        raise ValueError("recipe.target_markets vazio ou ausente")
    if any(not isinstance(item, str) or not item for item in target_ids):
        raise ValueError("recipe.target_markets precisa conter ids não vazios")
    if len(target_ids) != len(set(target_ids)):
        raise ValueError("recipe.target_markets contém duplicatas")
    configured = {market["id"]: market for market in app_target_markets(app)}
    unknown = [market_id for market_id in target_ids if market_id not in configured]
    if unknown:
        raise ValueError(
            "recipe.target_markets inexistentes no app: " + ", ".join(unknown)
        )
    return [configured[market_id] for market_id in target_ids]


def required_market_override_ids(markets: list) -> set[str]:
    """Markets that would otherwise silently share one base-language copy."""
    by_language = {}
    for market in markets:
        by_language.setdefault(market["copy_language"], []).append(market["id"])
    shared_language_ids = {
        market_id
        for market_ids in by_language.values()
        if len(market_ids) > 1
        for market_id in market_ids
    }
    explicitly_required = {
        market["id"]
        for market in markets
        if market.get("require_market_copy_override") is True
    }
    return shared_language_ids | explicitly_required


def resolve_copy(
    recipe: dict,
    locale: str,
    fallback: str,
    copy_language: str | None = None,
):
    """(copy, is_fallback) for a target locale: exact → base language → fallback
    locale/base → first defined → legacy `copy:`. is_fallback=True means the
    market renders in the fallback language (we don't guess a translation)."""
    locales = recipe.get("locales")
    requested = base_lang(copy_language or locale)
    fallback_language = base_lang(fallback)
    market_uses_fallback = requested != base_lang(locale)
    if locales:
        for key in (copy_language, requested):
            if not key:
                continue
            if key in locales:
                return locales[key] or {}, market_uses_fallback
        for key in (fallback, fallback_language):
            if key in locales:
                return locales[key] or {}, True
        raise ValueError(
            f"recipe sem copy para {locale} e sem fallback configurado {fallback}"
        )

    legacy_lang = str(recipe.get("lang", ""))
    if not legacy_lang:
        raise ValueError("recipe legada com 'copy' precisa declarar 'lang'")
    if base_lang(legacy_lang) != requested:
        raise ValueError(
            f"recipe legada em {legacy_lang} não fornece copy_language {requested}"
        )
    return recipe.get("copy", {}) or {}, market_uses_fallback


def _market_override(recipe: dict, market_id: str) -> dict:
    overrides = recipe.get("market_overrides", {}) or {}
    if not isinstance(overrides, dict):
        raise ValueError("recipe.market_overrides precisa ser um objeto")
    value = overrides.get(market_id, {}) or {}
    if not isinstance(value, dict):
        raise ValueError(f"market_overrides.{market_id} precisa ser um objeto")
    return value


def resolve_market_copy(recipe: dict, market: dict, fallback: str):
    """Resolve on-canvas copy for one market, preferring its explicit override."""
    override = _market_override(recipe, market["id"])
    if "copy" in override:
        copy = override.get("copy")
        if not isinstance(copy, dict) or not copy:
            raise ValueError(
                f"market_overrides.{market['id']}.copy precisa ser um objeto"
            )
        return copy, False
    return resolve_copy(
        recipe,
        market["locale"],
        fallback,
        copy_language=market["copy_language"],
    )


def resolve_market_ad_copy(recipe: dict, market: dict) -> dict:
    """Resolve off-canvas copy with the same market-ID precedence as the image."""
    override = _market_override(recipe, market["id"])
    if "ad_copy" in override:
        ad_copy = override.get("ad_copy")
        if not isinstance(ad_copy, dict) or not ad_copy:
            raise ValueError(
                f"market_overrides.{market['id']}.ad_copy precisa ser um objeto"
            )
        return ad_copy
    blocks = recipe.get("ad_copy", {}) or {}
    if not isinstance(blocks, dict):
        raise ValueError("recipe.ad_copy precisa ser um objeto")
    value = blocks.get(market["copy_language"]) or blocks.get(market["locale"]) or {}
    return value if isinstance(value, dict) else {}


def build_context(
    app: dict,
    recipe: dict,
    locale: str,
    copy: dict,
    market: dict | None = None,
) -> dict:
    ctx = dict(app)                       # name, palette, fonts, store... at top level
    ctx["copy"] = copy or {}
    ctx["recipe"] = recipe
    ctx["lang"] = locale
    ctx["market"] = dict(market or {})
    ctx["market_id"] = (market or {}).get("id", "")
    ctx["image"] = recipe.get("image", {}) or {}

    assets = app.get("assets", {}) or {}

    # Inline the mascot SVG so headless Chrome needs no network.
    mascot_rel = assets.get("mascot_svg", "")
    mascot_path = ROOT / mascot_rel if mascot_rel else None
    ctx["mascot_svg_inline"] = mascot_path.read_text() if (mascot_path and mascot_path.exists()) else ""

    # Icon as an absolute file:// URL (headless Chrome can't resolve relatives).
    icon_rel = assets.get("icon", "")
    icon_path = ROOT / icon_rel if icon_rel else None
    ctx["icon_url"] = f"file://{icon_path.resolve()}" if (icon_path and icon_path.exists()) else ""

    # Optional background photo (e.g. a Higgsfield generation) the recipe points
    # to via image.file — resolved to an absolute file:// URL for photo-overlay.
    bg_rel = (recipe.get("image", {}) or {}).get("file", "")
    bg_path = ROOT / bg_rel if bg_rel else None
    ctx["bg_image_url"] = f"file://{bg_path.resolve()}" if (bg_path and bg_path.exists()) else ""

    return ctx


def screenshot(
    html_str: str,
    w: int,
    h: int,
    out_png: Path,
    timeout_seconds: float = 30,
) -> None:
    if not Path(CHROME).exists():
        die(f"Chrome não encontrado em {CHROME!r}. Defina CHROME_BIN.")
    out_png.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        html_path = Path(td) / "ad.html"
        html_path.write_text(html_str)
        with tempfile.NamedTemporaryFile(
            dir=out_png.parent,
            prefix=f".{out_png.stem}-",
            suffix=".tmp.png",
            delete=False,
        ) as tmp_file:
            tmp_png = Path(tmp_file.name)
        tmp_png.unlink()
        cmd = [
            CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--force-device-scale-factor=1", f"--window-size={w},{h}",
            f"--screenshot={tmp_png}", str(html_path),
        ]
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            tmp_png.unlink(missing_ok=True)
            die(f"Chrome excedeu o timeout de {timeout_seconds}s.")
        if res.returncode != 0 or not tmp_png.exists() or tmp_png.stat().st_size == 0:
            tmp_png.unlink(missing_ok=True)
            detail = (res.stderr or res.stdout or "sem saída do Chrome")[:900]
            die(f"Chrome não gerou um PNG válido (exit {res.returncode}).\n{detail}")
        tmp_png.replace(out_png)


def template_formats(tpl_name: str) -> list:
    """Formats a template renders well in (from its meta.yaml; default: all)."""
    meta = load_yaml(ROOT / "templates" / "image" / tpl_name / "meta.yaml")
    fmts = meta.get("formats") or list(FORMATS)
    return [f for f in fmts if f in FORMATS]


def render_recipe(app: dict, app_slug: str, recipe_path: Path, fmt_override: str | None,
                  all_formats: bool, markets: list, fallback: str, jobs: int = 1) -> list:
    recipe = load_yaml(recipe_path)
    target_markets = recipe_target_markets(app, recipe)
    requested_ids = {market["id"] for market in markets}
    markets = [
        market for market in target_markets if market["id"] in requested_ids
    ]
    if not markets:
        raise ValueError(
            f"{recipe_path.stem} não segmenta nenhum dos markets solicitados"
        )
    tpl_name = recipe.get("template")
    if not tpl_name:
        die(f"recipe sem 'template': {recipe_path}")
    tpl = ROOT / "templates" / "image" / tpl_name / "template.html"
    if not tpl.exists():
        die(f"template não existe: {tpl}")
    tpl_src = tpl.read_text()

    # which formats
    if fmt_override:
        formats = [fmt_override]
    elif all_formats:
        formats = template_formats(tpl_name)
    else:
        formats = [recipe.get("format", "square")]

    out_dir = ROOT / "output" / app_slug
    out_dir.mkdir(parents=True, exist_ok=True)

    render_jobs = []
    for market in markets:
        locale = market["locale"]
        copy, is_fallback = resolve_market_copy(recipe, market, fallback)
        html = render_template(
            tpl_src, build_context(app, recipe, locale, copy, market=market)
        )
        for fmt in formats:
            if fmt not in FORMATS:
                die(f"formato inválido: {fmt} (use {', '.join(FORMATS)})")
            w, h = FORMATS[fmt]
            out_png = out_dir / f"{recipe_path.stem}--{locale}--{fmt}.png"
            tag = f" ⇢{base_lang(fallback)}" if is_fallback else ""
            render_jobs.append((html, w, h, out_png, locale, tag))

    def render_one(job):
        html, width, height, out_png, locale, tag = job
        screenshot(html, width, height, out_png)
        return out_png, width, height, locale, tag

    outs = []
    with ThreadPoolExecutor(max_workers=normalize_jobs(jobs)) as executor:
        futures = [executor.submit(render_one, job) for job in render_jobs]
        for future in as_completed(futures):
            out_png, width, height, locale, tag = future.result()
            print(
                f"  ✓ {out_png.relative_to(ROOT)}  ({width}×{height}, {locale}{tag})",
                flush=True,
            )
            outs.append(out_png)
    return outs


def main() -> None:
    ap = argparse.ArgumentParser(description="creative-forge — render ad images from recipes")
    ap.add_argument("--app", required=True, help="app slug (reads apps/<slug>.yaml)")
    ap.add_argument("--recipe", help="recipe name or path (recipes/<app>/<name>.yaml)")
    ap.add_argument("--all", action="store_true", help="render every recipe for the app")
    ap.add_argument("--format", choices=list(FORMATS), help="render only this format (override)")
    ap.add_argument("--all-formats", action="store_true",
                    help="render each recipe in every format its template supports")
    ap.add_argument("--locale", help="render only this locale (e.g. es-MX)")
    ap.add_argument("--all-locales", action="store_true",
                    help="render every target market in the app's locales.targets")
    ap.add_argument("--jobs", type=int, default=4, help="parallel Chrome jobs (1-8; default 4)")
    args = ap.parse_args()
    try:
        jobs = normalize_jobs(args.jobs)
    except ValueError as exc:
        die(str(exc))

    app_path = ROOT / "apps" / f"{args.app}.yaml"
    if not app_path.exists():
        die(f"app config não existe: {app_path}")
    app = load_yaml(app_path)
    app.setdefault("slug", args.app)

    loc_cfg = app.get("locales", {}) or {}
    fallback = loc_cfg.get("fallback_copy_language", "en")
    targets = app_target_markets(app)
    if args.locale:
        markets = [
            market
            for market in targets
            if args.locale in (market["locale"], market.get("id"))
        ]
        if not markets:
            die(f"locale/market não configurado para {args.app}: {args.locale}")
    elif args.all_locales:
        markets = targets
    else:
        markets = [targets[0]]   # primary target market

    recipes_dir = ROOT / "recipes" / args.app
    if args.all:
        recipes = sorted(recipes_dir.glob("*.yaml"))
        if not recipes:
            die(f"nenhuma recipe em {recipes_dir}")
    elif args.recipe:
        p = Path(args.recipe)
        if not p.exists():
            name = args.recipe if args.recipe.endswith(".yaml") else f"{args.recipe}.yaml"
            p = recipes_dir / name
        if not p.exists():
            die(f"recipe não encontrada: {args.recipe}")
        recipes = [p]
    else:
        die("passe --recipe <nome> ou --all")

    print(f"creative-forge · app={args.app} · {len(recipes)} recipe(s) · {len(markets)} market(s)")
    total = 0
    for r in recipes:
        total += len(
            render_recipe(
                app,
                args.app,
                r,
                args.format,
                args.all_formats,
                markets,
                fallback,
                jobs=jobs,
            )
        )
    print(f"done. {total} PNG(s).")


if __name__ == "__main__":
    main()
