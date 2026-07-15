#!/usr/bin/env python3
"""Audit paid-creative observations; agents interpret, scripts check arithmetic."""

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import date, datetime
from pathlib import Path

try:
    from scripts import publish
except ImportError:  # direct execution: python3 scripts/experiments.py
    import publish

try:
    from scripts.paths import default_root
except ImportError:
    from paths import default_root

ROOT = default_root()
METRIC_FIELDS = (
    "impressions",
    "clicks",
    "installs",
    "spend_minor",
    "revenue_minor",
    "purchases",
    # Optional video-creative diagnostics (Meta: 3-second plays / ThruPlay).
    # Presence of either marks the experiment as video; image experiments omit both.
    "video_3s_views",
    "video_thruplay_views",
)
METRICS_SOURCE_SCHEMA = "creative-forge/meta-insights-readback@1"
METRICS_SOURCE_FIELDS = {
    "schema",
    "platform",
    "provider",
    "tool",
    "observed_at",
    "binding",
    "metrics",
}
METRICS_SOURCE_BINDING_FIELDS = {
    "app",
    "item_key",
    "brief_ref",
    "concept_id",
    "variant_id",
    "account_id",
    "campaign_id",
    "ad_set_id",
    "creative_id",
    "ad_id",
    "artifact_sha256",
    "date_window",
    "currency",
    "attribution_window",
}
SENSITIVE_ACTION_WORDS = (
    "scale",
    "budget",
    "activate",
    "spend",
    "escalar",
    "orçamento",
    "ativar",
    "gasto",
)


def load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError:
        sys.exit("creative-forge: PyYAML ausente. Rode: pip3 install -r requirements.txt")
    if not path.exists():
        sys.exit(f"creative-forge: experimento inexistente: {path}")
    return yaml.safe_load(path.read_text()) or {}


def is_nonnegative_number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and value >= 0
    )


def canonical_digest(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def audit_manifest_binding(data: dict, manifest: dict) -> list:
    errors = []
    payload = {key: value for key, value in manifest.items() if key != "manifest_digest"}
    actual_digest = canonical_digest(payload)
    if manifest.get("manifest_digest") != actual_digest:
        return ["manifest_digest do manifesto não corresponde ao conteúdo canônico"]
    if data.get("manifest_digest") != manifest.get("manifest_digest"):
        errors.append("experiment.manifest_digest diverge do manifesto")
    if data.get("app") != manifest.get("app"):
        errors.append("experiment.app diverge do manifesto")
    matching = [
        item
        for item in manifest.get("items", []) or []
        if item.get("item_key") == data.get("item_key")
    ]
    if len(matching) != 1:
        errors.append("experiment.item_key não resolve exatamente um item do manifesto")
        return errors
    item = matching[0]
    for field in ("brief_ref", "concept_id", "variant_id"):
        if data.get(field) != item.get(field):
            errors.append(f"experiment.{field} diverge do item do manifesto")
    for field in ("account_id", "campaign_id", "ad_set_id"):
        if data.get(field) != manifest.get(field):
            errors.append(f"experiment.{field} diverge do manifesto")
    return errors


def audit_publish_receipt_binding(
    data: dict,
    manifest: dict,
    receipt: dict,
    *,
    expected_app: str | None,
    evidence_root: Path,
    workspace_root: Path,
) -> list:
    errors = publish.verify_receipt(
        manifest,
        receipt,
        expected_app=expected_app,
        evidence_root=evidence_root,
        workspace_root=workspace_root,
        enforce_freshness=False,
    )
    expected_digest = canonical_digest(receipt)
    if data.get("publish_receipt_digest") != expected_digest:
        errors.append("experiment.publish_receipt_digest diverge do publish receipt")
    manifest_items = manifest.get("items", []) or []
    receipt_items = receipt.get("items", []) or []
    if not isinstance(manifest_items, list) or not all(
        isinstance(item, dict) for item in manifest_items
    ):
        return [*errors, "manifest.items inválido"]
    if not isinstance(receipt_items, list) or not all(
        isinstance(item, dict) for item in receipt_items
    ):
        return [*errors, "publish receipt.items inválido"]
    matches = [
        item
        for item in receipt_items
        if item.get("item_key") == data.get("item_key")
    ]
    if len(matches) != 1:
        errors.append("publish receipt não resolve exatamente o item_key do experimento")
        return errors
    item = matches[0]
    for field in (
        "account_id",
        "campaign_id",
        "ad_set_id",
        "creative_id",
        "ad_id",
    ):
        if data.get(field) != item.get(field):
            errors.append(f"experiment.{field} diverge do publish receipt item")
    return errors


def audit_metrics_provenance(data: dict, metrics_source, manifest: dict | None = None) -> list:
    errors = []
    provenance = data.get("metrics_provenance")
    if not isinstance(provenance, dict):
        return ["experiment.metrics_provenance ausente ou inválida"]
    for field in ("platform", "provider", "tool", "response_digest", "observed_at"):
        if not provenance.get(field):
            errors.append(f"experiment.metrics_provenance.{field} ausente")
    if provenance.get("platform") != "meta":
        errors.append("experiment.metrics_provenance.platform precisa ser meta")
    if isinstance(manifest, dict) and provenance.get("provider") != manifest.get("provider"):
        errors.append("experiment.metrics_provenance.provider diverge do manifesto")
    if str(provenance.get("tool") or "").strip().lower() in {
        "",
        "manual",
        "local",
        "unknown",
        "n/a",
    }:
        errors.append("experiment.metrics_provenance.tool precisa nomear a consulta real")
    if metrics_source is None:
        errors.append("metrics source bruto obrigatório para verificar response_digest")
    elif not isinstance(metrics_source, dict):
        errors.append("metrics source normalizado precisa ser objeto JSON")
    else:
        try:
            source_digest = canonical_digest(metrics_source)
        except (TypeError, ValueError):
            source_digest = None
            errors.append("metrics source contém valor JSON não canônico")
        if provenance.get("response_digest") != source_digest:
            errors.append(
                "experiment.metrics_provenance.response_digest diverge do metrics source"
            )
    try:
        observed = datetime.fromisoformat(
            str(provenance.get("observed_at") or "").replace("Z", "+00:00")
        )
        if observed.tzinfo is None:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("experiment.metrics_provenance.observed_at precisa ter timezone")
    date_window = provenance.get("date_window")
    if not isinstance(date_window, dict):
        errors.append("experiment.metrics_provenance.date_window ausente ou inválida")
    else:
        try:
            start = date.fromisoformat(str(date_window.get("start") or ""))
            end = date.fromisoformat(str(date_window.get("end") or ""))
            if start > end:
                errors.append("experiment.metrics_provenance.date_window start excede end")
        except ValueError:
            errors.append("experiment.metrics_provenance.date_window exige datas ISO-8601")
    if provenance.get("currency") != data.get("currency"):
        errors.append("experiment.metrics_provenance.currency diverge do experimento")
    if provenance.get("attribution_window") != data.get("attribution_window"):
        errors.append("experiment.metrics_provenance.attribution_window diverge do experimento")
    for field in (
        "app",
        "item_key",
        "brief_ref",
        "concept_id",
        "variant_id",
        "account_id",
        "campaign_id",
        "ad_set_id",
        "creative_id",
        "ad_id",
    ):
        if provenance.get(field) != data.get(field):
            errors.append(f"experiment.metrics_provenance.{field} diverge do experimento")
    if isinstance(metrics_source, dict):
        if set(metrics_source) != METRICS_SOURCE_FIELDS:
            errors.append("metrics source não segue o envelope normalizado canônico")
        if metrics_source.get("schema") != METRICS_SOURCE_SCHEMA:
            errors.append("metrics source usa schema inválido")
        for field in ("platform", "provider", "tool", "observed_at"):
            if metrics_source.get(field) != provenance.get(field):
                errors.append(
                    f"metrics source.{field} diverge de metrics_provenance.{field}"
                )
        source_binding = metrics_source.get("binding")
        if not isinstance(source_binding, dict) or set(source_binding) != METRICS_SOURCE_BINDING_FIELDS:
            errors.append("metrics source.binding é incompleto ou ambíguo")
        else:
            for field in (
                "app",
                "item_key",
                "brief_ref",
                "concept_id",
                "variant_id",
                "account_id",
                "campaign_id",
                "ad_set_id",
                "creative_id",
                "ad_id",
            ):
                if source_binding.get(field) != data.get(field):
                    errors.append(f"metrics source.binding.{field} diverge do experimento")
            for field in ("date_window", "currency", "attribution_window"):
                if source_binding.get(field) != provenance.get(field):
                    errors.append(
                        f"metrics source.binding.{field} diverge de metrics_provenance"
                    )
            manifest_item = next(
                (
                    item
                    for item in (manifest or {}).get("items", []) or []
                    if item.get("item_key") == data.get("item_key")
                ),
                {},
            )
            if source_binding.get("artifact_sha256") != manifest_item.get("sha256"):
                errors.append(
                    "metrics source.binding.artifact_sha256 diverge do manifesto"
                )
        source_metrics = metrics_source.get("metrics")
        if not isinstance(source_metrics, dict):
            errors.append("metrics source.metrics precisa ser objeto")
        else:
            unknown = sorted(set(source_metrics) - set(METRIC_FIELDS))
            if unknown:
                errors.append(
                    "metrics source.metrics contém campos não normalizados: "
                    + ", ".join(unknown)
                )
            for field, value in source_metrics.items():
                if field in METRIC_FIELDS and not is_nonnegative_number(value):
                    errors.append(
                        f"metrics source.metrics.{field} precisa ser número não-negativo"
                    )
            if source_metrics != (data.get("metrics", {}) or {}):
                errors.append(
                    "experiment.metrics diverge das métricas exatas do source normalizado"
                )
    return errors


def audit_next_brief(data: dict, briefs_root: Path) -> list:
    decision = data.get("agent_decision", {}) or {}
    next_brief_ref = decision.get("next_brief_ref")
    if not next_brief_ref:
        return ["agent_decision.next_brief_ref obrigatório para decisão final"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", str(next_brief_ref)):
        return ["agent_decision.next_brief_ref contém path/ID inseguro"]
    path = briefs_root / str(data.get("app")) / f"{next_brief_ref}.yaml"
    if not path.is_file():
        return [f"agent_decision.next_brief_ref não resolvido: {next_brief_ref}"]
    try:
        import yaml

        brief = yaml.safe_load(path.read_text()) or {}
    except Exception as exc:
        return [f"agent_decision.next_brief_ref inválido: {exc}"]
    errors = []
    if brief.get("id") != next_brief_ref:
        errors.append("next_brief_ref diverge do id do brief resolvido")
    if brief.get("app") != data.get("app"):
        errors.append("next_brief_ref resolve brief de outro app")
    return errors


def audit_experiment(
    data: dict,
    *,
    expected_app: str | None = None,
    manifest: dict | None = None,
    publish_receipt: dict | None = None,
    metrics_source=None,
    briefs_root: Path | None = None,
    evidence_root: Path = ROOT,
    workspace_root: Path = ROOT,
) -> dict:
    errors, warnings = [], []
    if data.get("version") != 1:
        errors.append("experiment.version precisa ser 1")
    for field in (
        "id",
        "app",
        "manifest_digest",
        "publish_receipt_digest",
        "brief_ref",
        "concept_id",
        "variant_id",
        "item_key",
        "account_id",
        "campaign_id",
        "ad_set_id",
        "creative_id",
        "ad_id",
        "market",
    ):
        if not data.get(field):
            errors.append(f"experiment.{field} ausente")
    if data.get("manifest_digest") and not re.fullmatch(
        r"[0-9a-f]{64}", str(data.get("manifest_digest"))
    ):
        errors.append("experiment.manifest_digest precisa ser SHA-256")
    if data.get("publish_receipt_digest") and not re.fullmatch(
        r"[0-9a-f]{64}", str(data.get("publish_receipt_digest"))
    ):
        errors.append("experiment.publish_receipt_digest precisa ser SHA-256")
    if not isinstance(expected_app, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]*", expected_app
    ):
        errors.append("experiment.expected_app externo é obrigatório e deve ser canônico")
    elif data.get("app") != expected_app:
        errors.append(
            f"experiment.app '{data.get('app')}' diverge do app '{expected_app}'"
        )
    currency = str(data.get("currency") or "")
    if not re.fullmatch(r"[A-Z]{3}", currency):
        errors.append("experiment.currency precisa ser ISO-4217 com 3 letras maiúsculas")
    window = data.get("attribution_window")
    if not isinstance(window, dict):
        errors.append("experiment.attribution_window ausente ou inválida")
    else:
        for field in ("click_days", "view_days"):
            value = window.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 30:
                errors.append(f"experiment.attribution_window.{field} precisa estar entre 0 e 30")

    sample_status = data.get("sample_status")
    if sample_status not in {"collecting", "sufficient", "final"}:
        errors.append(f"experiment.sample_status inválido: {sample_status}")
    metrics = data.get("metrics", {}) or {}
    for field in METRIC_FIELDS:
        if field in metrics and not is_nonnegative_number(metrics[field]):
            errors.append(f"experiment.metrics.{field} precisa ser número não-negativo")
    for required in ("impressions", "clicks", "installs", "spend_minor"):
        if required not in metrics:
            errors.append(f"experiment.metrics.{required} ausente")
    impressions = metrics.get("impressions")
    clicks = metrics.get("clicks")
    if is_nonnegative_number(impressions) and is_nonnegative_number(clicks):
        if clicks > impressions:
            errors.append("experiment.metrics.clicks não pode exceder impressions")
    video_3s = metrics.get("video_3s_views")
    video_thruplay = metrics.get("video_thruplay_views")
    if is_nonnegative_number(video_3s) and is_nonnegative_number(video_thruplay):
        # A ThruPlay (15s or full watch) necessarily passed the 3-second mark
        # for any ad-length video; a higher ThruPlay count is fabricated data.
        if video_thruplay > video_3s:
            errors.append(
                "experiment.metrics.video_thruplay_views não pode exceder video_3s_views"
            )
    if "video_thruplay_views" in metrics and "video_3s_views" not in metrics:
        errors.append(
            "experiment.metrics.video_thruplay_views exige video_3s_views na mesma fonte"
        )
    if "revenue_minor" not in metrics:
        warnings.append("ROAS indisponível: revenue_minor ausente")

    decision = data.get("agent_decision")
    if sample_status in {"sufficient", "final"} and not isinstance(decision, dict):
        errors.append("experiment.agent_decision obrigatório para amostra suficiente/final")
    elif isinstance(decision, dict):
        if decision.get("classification") not in {"red", "yellow", "green"}:
            errors.append("agent_decision.classification precisa ser red, yellow ou green")
        for field in ("rationale", "likely_cause", "next_action", "decided_by"):
            if not decision.get(field):
                errors.append(f"agent_decision.{field} ausente")
        next_action = str(decision.get("next_action") or "").lower()
        sensitive = any(word in next_action for word in SENSITIVE_ACTION_WORDS)
        if sensitive and decision.get("requires_human_confirmation") is not True:
            errors.append(
                "agent_decision com ação de spend/activation/budget exige human confirmation"
            )
        if "requires_human_confirmation" not in decision:
            errors.append("agent_decision.requires_human_confirmation ausente")
    else:
        warnings.append("interpretação estratégica pendente do agente")
    if manifest is None:
        errors.append("publish manifest obrigatório para auditar experimento")
    else:
        errors.extend(audit_manifest_binding(data, manifest))
        if not isinstance(publish_receipt, dict):
            errors.append("publish receipt obrigatório para auditar experimento")
        else:
            errors.extend(
                audit_publish_receipt_binding(
                    data,
                    manifest,
                    publish_receipt,
                    expected_app=expected_app,
                    evidence_root=Path(evidence_root),
                    workspace_root=Path(workspace_root),
                )
            )
    errors.extend(audit_metrics_provenance(data, metrics_source, manifest))
    if sample_status == "final":
        errors.extend(audit_next_brief(data, briefs_root or ROOT / "briefs"))
    return {"errors": errors, "warnings": warnings}


def calculate_metrics(data: dict) -> dict:
    metrics = data.get("metrics", {}) or {}
    impressions = metrics.get("impressions")
    clicks = metrics.get("clicks")
    installs = metrics.get("installs")
    spend = metrics.get("spend_minor")
    revenue = metrics.get("revenue_minor")
    unavailable = []

    ctr = clicks / impressions if impressions and is_nonnegative_number(clicks) else None
    if ctr is None:
        unavailable.append("CTR")
    cpi = spend / installs if installs and is_nonnegative_number(spend) else None
    if cpi is None:
        unavailable.append("CPI")
    roas = revenue / spend if spend and is_nonnegative_number(revenue) else None
    if roas is None:
        unavailable.append("ROAS")

    # Video-only diagnostics: hook rate says the first 3 seconds failed or
    # worked; hold rate says the body kept who the hook caught. They are only
    # required — and only counted as missing — when the source reports any
    # video field, so image experiments keep their existing contract.
    video_3s = metrics.get("video_3s_views")
    video_thruplay = metrics.get("video_thruplay_views")
    has_video_metrics = "video_3s_views" in metrics or "video_thruplay_views" in metrics
    hook_rate = None
    hold_rate = None
    if has_video_metrics:
        if impressions and is_nonnegative_number(video_3s):
            hook_rate = video_3s / impressions
        if hook_rate is None:
            unavailable.append("HOOK_RATE")
        if video_3s and is_nonnegative_number(video_thruplay):
            hold_rate = video_thruplay / video_3s
        if hold_rate is None:
            unavailable.append("HOLD_RATE")

    return {
        "ctr": round(ctr, 6) if ctr is not None else None,
        "cpi_minor": round(cpi, 2) if cpi is not None else None,
        "roas": round(roas, 6) if roas is not None else None,
        "hook_rate": round(hook_rate, 6) if hook_rate is not None else None,
        "hold_rate": round(hold_rate, 6) if hold_rate is not None else None,
        "unavailable": unavailable,
        "context": "insufficient_data" if unavailable else "complete",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="creative-forge — experiment evidence gate")
    parser.add_argument("--file", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--publish-receipt", required=True)
    parser.add_argument("--metrics-source", required=True)
    parser.add_argument(
        "--evidence-root",
        default=str(ROOT),
        help="root local dos response_path selados no publish receipt",
    )
    parser.add_argument(
        "--workspace-root",
        default=str(ROOT),
        help="root canônico contendo apps/<app>.yaml",
    )
    parser.add_argument("--app", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    data = load_yaml(Path(args.file))
    manifest = json.loads(Path(args.manifest).read_text())
    publish_receipt = json.loads(Path(args.publish_receipt).read_text())
    metrics_source = json.loads(Path(args.metrics_source).read_text())
    audit = audit_experiment(
        data,
        expected_app=args.app,
        manifest=manifest,
        publish_receipt=publish_receipt,
        metrics_source=metrics_source,
        evidence_root=Path(args.evidence_root),
        workspace_root=Path(args.workspace_root),
    )
    result = {**audit, "calculated_metrics": calculate_metrics(data)}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for warning in audit["warnings"]:
            print(f"  ⚠️  {warning}")
        for error in audit["errors"]:
            print(f"  ❌ {error}")
        print(json.dumps(result["calculated_metrics"], ensure_ascii=False, indent=2))
        print(f"experiment={'PASS' if not audit['errors'] else 'BLOCKED'}")
    if audit["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
