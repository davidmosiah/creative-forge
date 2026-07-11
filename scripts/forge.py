#!/usr/bin/env python3
"""One portable entry point for research, data, render, QA and publish gates."""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts import assets, audiences, briefs, locales, publish, qa, render, research, signals, validate, video, video_mining, video_qa
except ImportError:
    import assets
    import audiences
    import briefs
    import locales
    import publish
    import qa
    import render
    import research
    import signals
    import validate
    import video
    import video_mining
    import video_qa

ROOT = Path(__file__).resolve().parent.parent


def aggregate_gates(gates: list) -> dict:
    errors, warnings = [], []
    for gate in gates:
        name = gate.get("name", "gate")
        errors.extend(f"{name}: {error}" for error in gate.get("errors", []))
        warnings.extend(f"{name}: {warning}" for warning in gate.get("warnings", []))
    return {"ok": not errors, "errors": errors, "warnings": warnings, "gates": gates}


def validate_app_identity(
    requested_slug: str,
    app: dict,
    signal_data: dict,
    research_data: dict,
) -> list:
    errors = []
    identities = (
        ("app.slug", app.get("slug")),
        ("signals.app", signal_data.get("app")),
        ("research.app", research_data.get("app")),
    )
    for field, actual in identities:
        if actual != requested_slug:
            errors.append(
                f"{field} '{actual}' diverge do app solicitado '{requested_slug}'"
            )
    return errors


def validate_research_refs(recipes: list, research_data: dict) -> list:
    known = {
        creative.get("id"): creative
        for creative in research_data.get("creatives", []) or []
    }
    errors = []
    for recipe in recipes:
        name = recipe.get("name", "recipe")
        refs = recipe.get("research_refs", []) or []
        if not refs:
            errors.append(f"{name} sem research_refs")
        for ref in refs:
            if ref not in known:
                errors.append(f"{name} referencia research_ref inexistente: {ref}")
            elif (
                recipe.get("media_type") == "video"
                and known[ref].get("format") == "unknown"
            ):
                errors.append(
                    f"{name} usa research_ref {ref} com formato desconhecido "
                    "em uma recipe de vídeo"
                )
    return errors


def audit_video_surfaces(
    app_slug: str,
    app: dict,
    *,
    root: Path = ROOT,
) -> dict:
    pattern_path = root / "swipe" / app_slug / "video-patterns.yaml"
    recipe_paths = sorted((root / "recipes" / app_slug / "video").glob("*.yaml"))
    if pattern_path.exists():
        pattern_result = video_mining.audit_video_patterns(
            render.load_yaml(pattern_path),
            expected_app=app_slug,
            root=root,
        )
    else:
        pattern_result = {
            "errors": (
                [f"video recipes existem sem {pattern_path}"] if recipe_paths else []
            ),
            "warnings": (
                [] if recipe_paths else [f"sem video research para {app_slug}"]
            ),
        }
    recipe_errors, recipe_warnings = [], []
    if not recipe_paths:
        recipe_warnings.append(f"sem recipes de vídeo para {app_slug}")
    for recipe_path in recipe_paths:
        result = video.audit_recipe(
            render.load_yaml(recipe_path),
            app,
            root=root,
            expected_app=app_slug,
        )
        recipe_errors.extend(
            f"{recipe_path.name}: {error}" for error in result["errors"]
        )
        recipe_warnings.extend(
            f"{recipe_path.name}: {warning}" for warning in result["warnings"]
        )
    return {
        "research": {
            "errors": list(pattern_result.get("errors", [])),
            "warnings": list(pattern_result.get("warnings", [])),
        },
        "recipes": {"errors": recipe_errors, "warnings": recipe_warnings},
    }


def preflight(app_slug: str, *, root: Path = ROOT) -> dict:
    root = Path(root).resolve()
    app = render.load_yaml(root / "apps" / f"{app_slug}.yaml")
    locale_result = locales.audit_locale_strategy(root, app)
    app_errors, app_warnings = validate.validate_app(app)
    recipe_errors, recipe_warnings = [], []
    recipes = sorted((root / "recipes" / app_slug).glob("*.yaml"))
    video_recipe_paths = sorted(
        (root / "recipes" / app_slug / "video").glob("*.yaml")
    )
    if not recipes:
        if video_recipe_paths:
            recipe_warnings.append(
                "sem recipe de imagem ativa; app permanece válido para build-video"
            )
        else:
            recipe_errors.append("nenhuma recipe ativa de imagem ou vídeo")
    loaded_recipes = []
    for recipe_path in recipes:
        recipe_data = render.load_yaml(recipe_path)
        loaded_recipes.append({"name": recipe_path.stem, **recipe_data})
        errors, warnings = validate.validate_recipe(app, app.get("voice", {}) or {}, recipe_path)
        recipe_errors.extend(errors)
        recipe_warnings.extend(warnings)
    signal_data = signals.load_yaml(root / "signals" / f"{app_slug}.yaml")
    signal_result = signals.audit_signals(
        signal_data,
        expected_app=app_slug,
        expected_app_name=app.get("name"),
    )
    research_data = research.load_yaml(root / "swipe" / app_slug / "competitors.yaml")
    research_result = research.audit_research(research_data)
    identity_result = {
        "errors": validate_app_identity(app_slug, app, signal_data, research_data),
        "warnings": [],
    }
    recipe_errors.extend(validate_research_refs(loaded_recipes, research_data))

    known_research_refs = {
        item.get("id") for item in research_data.get("creatives", []) or []
    }
    brief_errors, brief_warnings, briefs_by_id = [], [], {}
    brief_paths = sorted((root / "briefs" / app_slug).glob("*.yaml"))
    if not brief_paths:
        brief_errors.append(f"nenhum brief em briefs/{app_slug}")
    for brief_path in brief_paths:
        brief = briefs.load_yaml(brief_path)
        brief_id = brief.get("id")
        if brief_id in briefs_by_id:
            brief_errors.append(f"brief id duplicado: {brief_id}")
        briefs_by_id[brief_id] = brief
        audit = briefs.audit_brief(
            brief,
            expected_app=app_slug,
            known_research_refs=known_research_refs,
            supported_markets={
                market.get("id")
                for market in render.app_target_markets(app)
                if market.get("id")
            },
        )
        brief_errors.extend(f"{brief_path.name}: {error}" for error in audit["errors"])
        brief_warnings.extend(
            f"{brief_path.name}: {warning}" for warning in audit["warnings"]
        )

    registry_path = root / "assets" / app_slug / "registry.yaml"
    if registry_path.exists():
        registry = assets.load_yaml(registry_path)
        asset_result = assets.audit_registry(
            registry, expected_app=app_slug, root=root
        )
    else:
        registry = {"assets": []}
        asset_result = {
            "errors": [f"asset registry ausente: assets/{app_slug}/registry.yaml"],
            "warnings": [],
        }
    for recipe in loaded_recipes:
        recipe_name = recipe.get("name", "recipe")
        brief_ref = recipe.get("brief_ref")
        brief = briefs_by_id.get(brief_ref)
        if brief is None:
            recipe_errors.append(
                f"[{recipe_name}] brief_ref inexistente: {brief_ref}"
            )
        else:
            recipe_errors.extend(
                briefs.recipe_binding_errors(recipe, brief, recipe_name)
            )
        recipe_errors.extend(
            assets.recipe_asset_errors(recipe, registry, recipe_name)
        )
    for recipe in loaded_recipes:
        template_name = recipe.get("template") or ""
        meta_path = root / "templates" / "image" / template_name / "meta.yaml"
        template_meta = render.load_yaml(meta_path) if meta_path.exists() else {}
        recipe_errors.extend(
            research.swipe_alignment_errors(
                recipe.get("name", "recipe"),
                template_name,
                template_meta.get("swipe_angles", []) or [],
                recipe.get("research_refs", []) or [],
                research_data,
            )
        )
    audience_path = root / "audiences" / f"{app_slug}.yaml"
    if audience_path.exists():
        audience_plan = audiences.load_yaml(audience_path)
        audience_result = audiences.audit_plan(
            audience_plan, render.app_target_markets(app)
        )
        if audience_plan.get("app") != app_slug:
            audience_result["errors"].append(
                f"plano declara app '{audience_plan.get('app')}', esperado '{app_slug}'"
            )
    else:
        audience_result = {
            "errors": [],
            "warnings": [f"sem audiences/{app_slug}.yaml — publish bloqueará sem ele"],
        }
    video_surfaces = audit_video_surfaces(app_slug, app, root=root)
    result = aggregate_gates(
        [
            {"name": "identity", **identity_result},
            {"name": "locales", **locale_result},
            {"name": "app", "errors": app_errors, "warnings": app_warnings},
            {"name": "recipes", "errors": recipe_errors, "warnings": recipe_warnings},
            {"name": "signals", **signal_result},
            {"name": "research", **research_result},
            {
                "name": "briefs",
                "errors": brief_errors,
                "warnings": brief_warnings,
            },
            {"name": "assets", **asset_result},
            {"name": "video_research", **video_surfaces["research"]},
            {"name": "video_recipes", **video_surfaces["recipes"]},
            {"name": "audiences", **audience_result},
        ]
    )
    result["market_ranking"] = signals.rank_countries(signal_data)
    result["creative_ranking"] = research.rank_creatives(research_data)
    return result


def print_preflight(result: dict) -> None:
    for warning in result["warnings"]:
        print(f"  ⚠️  {warning}")
    for error in result["errors"]:
        print(f"  ❌ {error}")
    print(f"preflight={'PASS' if result['ok'] else 'BLOCKED'}")


def clean_output(output_dir: Path) -> int:
    """Remove only top-level PNGs that the renderer can regenerate."""
    output_dir.mkdir(parents=True, exist_ok=True)
    removed = 0
    for path in output_dir.glob("*.png"):
        path.unlink()
        removed += 1
    return removed


def build(app: str, batch_id: str, jobs: int) -> Path:
    result = preflight(app)
    print_preflight(result)
    if not result["ok"]:
        sys.exit(1)
    if not list((ROOT / "recipes" / app).glob("*.yaml")):
        raise ValueError(
            f"build renderiza imagens e {app} não possui recipes de imagem; "
            "use build-video"
        )
    removed = clean_output(ROOT / "output" / app)
    print(f"output cleanup: {removed} PNG(s) regenerável(is) removido(s)")
    sys.stdout.flush()
    started = datetime.now(timezone.utc)
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "render.py"),
            "--app",
            app,
            "--all",
            "--all-formats",
            "--all-locales",
            "--jobs",
            str(jobs),
        ],
        cwd=ROOT,
        check=True,
    )
    report_path = qa.prepare(app, batch_id, generated_after=started)
    run = {
        "app": app,
        "batch_id": batch_id,
        "started_at": started.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "qa_report": str(report_path),
    }
    run_path = ROOT / "runs" / app / f"{batch_id}.json"
    run_path.parent.mkdir(parents=True, exist_ok=True)
    run_path.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n")
    print(f"QA report: {report_path}")
    print("NEXT: abrir todos os contact sheets e registrar qa.py approve")
    return report_path


def build_video(
    app: str,
    recipe_name: str,
    batch_id: str,
    *,
    locale: str | None = None,
    all_markets: bool = False,
    root: Path = ROOT,
) -> list[Path]:
    """Render video sequentially and create pending per-locale playback QA."""
    root = Path(root).resolve()
    result = preflight(app, root=root)
    print_preflight(result)
    if not result["ok"]:
        raise ValueError("preflight bloqueou build-video")
    app_config = render.load_yaml(root / "apps" / f"{app}.yaml")
    recipe_file = recipe_name if recipe_name.endswith(".yaml") else f"{recipe_name}.yaml"
    recipe_path = root / "recipes" / app / "video" / recipe_file
    if not recipe_path.is_file():
        raise ValueError(f"recipe de vídeo inexistente: {recipe_path}")
    recipe = render.load_yaml(recipe_path)
    audit = video.audit_recipe(
        recipe, app_config, root=root, expected_app=app
    )
    if audit["errors"]:
        raise ValueError("recipe de vídeo inválida: " + "; ".join(audit["errors"]))
    selected = video.select_locales(
        recipe,
        app_config,
        locale=locale,
        all_markets=all_markets,
    )
    # Validates all user-controlled path segments before writing output/run files.
    video_qa.safe_qa_dir(root, app, batch_id, selected[0], recipe_path.stem)
    reports = []
    artifacts = []
    started = datetime.now(timezone.utc)
    for selected_locale in selected:
        output = video_qa.expected_video_path(
            root,
            app,
            recipe_path.stem,
            selected_locale,
            recipe["format"],
        )
        rendered = video.render_video(
            recipe,
            app_config,
            selected_locale,
            output,
            root=root,
        )
        report = video_qa.prepare(
            app_slug=app,
            recipe_name=recipe_path.stem,
            locale=selected_locale,
            video_path=rendered,
            batch_id=batch_id,
            root=root,
        )
        reports.append(report)
        artifacts.append(str(rendered))
    run = {
        "version": 1,
        "app": app,
        "batch_id": batch_id,
        "recipe": recipe_path.stem,
        "started_at": started.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending_agent_playback_qa",
        "artifacts": artifacts,
        "qa_reports": [str(path) for path in reports],
    }
    run_path = root / "runs" / app / f"{batch_id}.video.json"
    run_path.parent.mkdir(parents=True, exist_ok=True)
    run_path.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n")
    for report in reports:
        print(f"Video QA report: {report}")
    print("NEXT: inspecionar cada vídeo completo e registrar video_qa.py approve")
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description="creative-forge — end-to-end workflow")
    sub = parser.add_subparsers(dest="command", required=True)
    preflight_parser = sub.add_parser("preflight")
    preflight_parser.add_argument("--app", required=True)
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--app", required=True)
    build_parser.add_argument("--batch-id")
    build_parser.add_argument("--jobs", type=int, default=4)
    video_parser = sub.add_parser("build-video")
    video_parser.add_argument("--app", required=True)
    video_parser.add_argument("--recipe", required=True)
    video_parser.add_argument("--batch-id")
    video_market = video_parser.add_mutually_exclusive_group(required=True)
    video_market.add_argument("--locale")
    video_market.add_argument("--all-markets", action="store_true")
    prepare_parser = sub.add_parser("prepare-publish")
    prepare_parser.add_argument("--qa-report", required=True)
    prepare_parser.add_argument("--capabilities", required=True)
    prepare_parser.add_argument("--account-id", required=True)
    prepare_parser.add_argument("--campaign-id", required=True)
    prepare_parser.add_argument("--ad-set-id", required=True)
    prepare_parser.add_argument("--audience-id", required=True)
    prepare_parser.add_argument("--readiness-receipt", required=True)
    prepare_parser.add_argument("--out", required=True)
    args = parser.parse_args()

    if args.command == "preflight":
        result = preflight(args.app)
        print_preflight(result)
        if not result["ok"]:
            sys.exit(1)
    elif args.command == "build":
        batch_id = args.batch_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        build(args.app, batch_id, render.normalize_jobs(args.jobs))
    elif args.command == "build-video":
        batch_id = args.batch_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        try:
            build_video(
                args.app,
                args.recipe,
                batch_id,
                locale=args.locale,
                all_markets=args.all_markets,
            )
        except (OSError, ValueError, video.VideoError) as exc:
            sys.exit(f"creative-forge: build-video BLOCKED: {exc}")
    else:
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "publish.py"),
                "prepare",
                "--qa-report",
                args.qa_report,
                "--capabilities",
                args.capabilities,
                "--account-id",
                args.account_id,
                "--campaign-id",
                args.campaign_id,
                "--ad-set-id",
                args.ad_set_id,
                "--audience-id",
                args.audience_id,
                "--readiness-receipt",
                args.readiness_receipt,
                "--out",
                args.out,
            ],
            cwd=ROOT,
            check=False,
        )
        if completed.returncode:
            sys.exit(completed.returncode)


if __name__ == "__main__":
    main()
