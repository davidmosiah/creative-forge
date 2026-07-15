from pathlib import Path

import yaml

from scripts import qa


def production_spec(root: Path, spec: dict, *, app: str) -> dict:
    """Upgrade a focused test spec to the same provenance contract as production."""
    prepared = dict(spec)
    refs = list(prepared.get("research_refs") or ["meta-pain"])
    prepared.setdefault("recipe", "morning")
    prepared["research_refs"] = refs
    prepared.setdefault("swiped_from", "observed competitor structure")
    prepared.setdefault(
        "lineage",
        {ref: "competitor_pattern" for ref in refs},
    )
    prepared.setdefault("claims_used", [])
    prepared.setdefault("template", "pain-headline-cta")
    prepared.setdefault("concept_lineage", "competitor_pattern")
    prepared.setdefault("concept_lineage_ref", refs[0])
    prepared.setdefault("execution_lineage", "competitor_pattern")
    prepared.setdefault("execution_ref", refs[0])
    prepared.setdefault("market_id", "br")
    prepared.setdefault("media_kind", "image")
    prepared.setdefault("brief_ref", "pilot")
    prepared.setdefault("concept_id", "morning-relief")
    prepared.setdefault("variant_id", prepared["recipe"])
    lineage_map = prepared["lineage"]
    research_path = Path(root) / "swipe" / app / "competitors.yaml"
    research_path.parent.mkdir(parents=True, exist_ok=True)
    research_path.write_text(
        yaml.safe_dump(
            {
                "creatives": [
                    {
                        "id": ref,
                        "lineage": lineage_map[ref],
                        **(
                            {
                                "evidence_level": "performance_data",
                                "performance_metrics": {"installs": 12},
                            }
                            if lineage_map[ref] == "own_winner"
                            else {"evidence_level": "observed"}
                        ),
                    }
                    for ref in refs
                ]
            },
            sort_keys=False,
        )
    )
    brief_path = Path(root) / "briefs" / app / f"{prepared['brief_ref']}.yaml"
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text(
        yaml.safe_dump(
            {
                "id": prepared["brief_ref"],
                "concepts": [
                    {
                        "id": prepared["concept_id"],
                        "lineage": prepared["concept_lineage"],
                        "lineage_ref": prepared["concept_lineage_ref"],
                        "research_refs": refs,
                    }
                ],
            },
            sort_keys=False,
        )
    )
    recipe_path = Path(root) / "recipes" / app / f"{prepared['recipe']}.yaml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text(
        yaml.safe_dump(
            {
                "brief_ref": prepared["brief_ref"],
                "concept_id": prepared["concept_id"],
                "research_refs": refs,
                "execution_ref": prepared.get("execution_ref"),
                "swiped_from": prepared.get("swiped_from", ""),
            },
            sort_keys=False,
        )
    )
    template_path = (
        Path(root)
        / "templates"
        / "image"
        / prepared["template"]
        / "meta.yaml"
    )
    template_path.parent.mkdir(parents=True, exist_ok=True)
    template_path.write_text("role: template\n")
    app_path = Path(root) / "apps" / f"{app}.yaml"
    app_path.parent.mkdir(parents=True, exist_ok=True)
    if not app_path.exists():
        app_path.write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "slug": app,
                    "readiness": {
                        "required_receipts": {
                            "app_store_destination": "required_live"
                        }
                    },
                },
                sort_keys=False,
            )
        )
    inputs = [
        {"role": "recipe", "path": str(recipe_path)},
        {"role": "research", "path": str(research_path)},
        {"role": "brief", "path": str(brief_path)},
        {"role": "template", "path": str(template_path)},
        {"role": "app_config", "path": str(app_path)},
    ]
    prepared.setdefault("input_files", inputs)
    return prepared


def production_report(root: Path, app: str, batch_id: str, specs: list[dict]) -> dict:
    automated = qa.audit_outputs(
        [production_spec(root, spec, app=app) for spec in specs],
        require_provenance=True,
    )
    return qa.build_report(app, batch_id, automated)


def approve_report(report: dict, reviewer: str = "codex") -> dict:
    reviews = [
        {
            "artifact_key": record["artifact_key"],
            "notes": "Opened the original fixture at native resolution.",
        }
        for record in report.get("records", [])
    ]
    return qa.approve_visual(
        report,
        reviewer,
        {name: True for name in qa.VISUAL_CHECKS},
        artifact_reviews=reviews,
    )
