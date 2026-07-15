#!/usr/bin/env python3
"""Prepare and verify fail-closed Meta Ads MCP manifests and receipts."""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

try:
    from scripts import audiences, qa, research
except ImportError:
    import audiences
    import qa
    import research

REQUIRED_META_TOOLS = {"ads_create_creative", "ads_create_ad"}
WRITE_TOOL_TOKENS = {
    "create",
    "update",
    "delete",
    "remove",
    "activate",
    "publish",
    "upsert",
    "setstatus",
    "mutate",
    "write",
    "edit",
    "patch",
    "enable",
    "disable",
    "pause",
    "resume",
    "launch",
    "archive",
    "destroy",
    "change",
    "reset",
    "increase",
    "decrease",
    "forget",
    "submit",
    "send",
    "spend",
    "execute",
    "run",
    "perform",
    "apply",
    "commit",
    "deploy",
    "set",
}
COMPACT_WRITE_TOOL_MARKERS = (
    "create",
    "update",
    "delete",
    "remove",
    "activate",
    "publish",
    "upsert",
    "setstatus",
    "setbudget",
    "mutate",
    "write",
    "patch",
    "enable",
    "disable",
    "pause",
    "resume",
    "launch",
    "archive",
    "destroy",
    "change",
    "reset",
    "increase",
    "decrease",
    "forget",
    "submit",
    "send",
    "spend",
    "execute",
    "perform",
    "commit",
    "deploy",
)
READ_ONLY_TOOL_TOKENS = {
    "get",
    "list",
    "read",
    "fetch",
    "query",
    "search",
    "lookup",
    "inspect",
    "check",
    "verify",
    "describe",
}
CAPABILITY_MAX_AGE_SECONDS = 60 * 60
CAPABILITY_FUTURE_TOLERANCE_SECONDS = 5 * 60
LIVE_RECEIPT_MAX_AGE_SECONDS = 60 * 60
SHA256_RE = re.compile(r"[0-9a-f]{64}")
DESTINATION_RECEIPT_TYPES = {
    "app_store_destination",
    "custom_product_page_destination",
    "landing_page_destination",
}
DESTINATION_TYPE_RECEIPTS = {
    "app_store": "app_store_destination",
    "custom_product_page": "custom_product_page_destination",
    "landing_page": "landing_page_destination",
}
try:
    from scripts.paths import default_root
except ImportError:
    from paths import default_root

ROOT = default_root()
MAX_RAW_RESPONSE_BYTES = 64 * 1024 * 1024
PUBLISH_READBACK_SCHEMA = "creative-forge/meta-ad-readback@1"
PUBLISH_READBACK_FIELDS = {
    "schema",
    "provider",
    "tool",
    "observed_at",
    "binding",
    "provider_response",
}
PUBLISH_READBACK_BINDING_FIELDS = {
    "item_key",
    "account_id",
    "campaign_id",
    "ad_set_id",
    "creative_id",
    "ad_id",
    "artifact_sha256",
    "status",
}
PUBLISH_PROVIDER_RESPONSE_FIELDS = {
    "id",
    "creative_id",
    "account_id",
    "campaign_id",
    "ad_set_id",
    "artifact_sha256",
    "status",
}


class PublishBlocked(ValueError):
    pass


def canonical_digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def canonical_json_bytes(value) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode()


def _tool_tokens(value) -> tuple[str, ...]:
    if not isinstance(value, str) or not value.strip():
        return ()
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value.strip())
    return tuple(re.findall(r"[a-z0-9]+", expanded.lower()))


def _normalized_tool_name(value) -> str:
    return "".join(_tool_tokens(value))


def _is_write_tool(value) -> bool:
    tokens = _tool_tokens(value)
    if not tokens:
        return True
    if any(token in WRITE_TOOL_TOKENS for token in tokens):
        return True
    normalized = "".join(tokens)
    if any(marker in normalized for marker in COMPACT_WRITE_TOOL_MARKERS):
        return True
    operation = tokens[0] if tokens[0] in READ_ONLY_TOOL_TOKENS else None
    if operation is None and len(tokens) > 1 and tokens[1] in READ_ONLY_TOOL_TOKENS:
        operation = tokens[1]
    return operation is None


def _is_meta_ad_readback_tool(value) -> bool:
    return _normalized_tool_name(value) == "adsgetad" and not _is_write_tool(value)


def _verified_raw_response_bytes(
    record: dict,
    label: str,
    evidence_root: str | Path,
) -> tuple[dict, bytes]:
    """Revalidate an ignored local provider response without following symlinks."""
    raw_path = record.get("response_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise PublishBlocked(f"{label} sem response_path local")
    relative = Path(raw_path)
    if relative.is_absolute():
        raise PublishBlocked(f"{label} response_path precisa ser relativo")
    if any(part in {"", ".", ".."} for part in relative.parts):
        if ".." in relative.parts:
            raise PublishBlocked(f"{label} response_path escapa evidence_root")
        raise PublishBlocked(f"{label} response_path não é canônico")
    try:
        root = Path(evidence_root).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise PublishBlocked(f"{label} evidence_root inexistente ou inválido") from None
    candidate = Path(os.path.abspath(root / relative))
    if not candidate.is_relative_to(root):
        raise PublishBlocked(f"{label} response_path escapa evidence_root")
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise PublishBlocked(f"{label} response_path contém symlink")
    if not candidate.is_file():
        raise PublishBlocked(f"{label} response_path não aponta para arquivo regular")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise PublishBlocked(f"{label} response_path não pode ser resolvido") from None
    if not resolved.is_relative_to(root):
        raise PublishBlocked(f"{label} response_path escapa evidence_root")
    if candidate.stat().st_size > MAX_RAW_RESPONSE_BYTES:
        raise PublishBlocked(f"{label} raw response excede 64 MiB")
    expected = str(record.get("response_digest") or "")
    if not SHA256_RE.fullmatch(expected):
        raise PublishBlocked(f"{label} sem response_digest SHA-256")
    try:
        payload = candidate.read_bytes()
    except OSError:
        raise PublishBlocked(f"{label} raw response não pode ser lido") from None
    if len(payload) > MAX_RAW_RESPONSE_BYTES:
        raise PublishBlocked(f"{label} raw response excede 64 MiB")
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise PublishBlocked(f"{label} response_digest não corresponde ao arquivo")
    return (
        {
            "response_path": relative.as_posix(),
            "response_digest": actual,
        },
        payload,
    )


def verify_raw_response_evidence(
    record: dict,
    label: str,
    evidence_root: str | Path,
) -> dict:
    """Revalidate an ignored local provider response without following symlinks."""
    evidence, _ = _verified_raw_response_bytes(record, label, evidence_root)
    return evidence


def _reject_duplicate_json_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"chave JSON duplicada: {key}")
        result[key] = value
    return result


def _reject_nonfinite_json(value):
    raise ValueError(f"constante JSON não finita: {value}")


def verify_publish_readback_evidence(
    record: dict,
    label: str,
    evidence_root: str | Path,
) -> dict:
    """Verify a canonical, cross-bound Meta PAUSED readback envelope.

    A digest proves only that the local bytes did not change. The envelope makes
    those bytes machine-checkable and binds the provider result to the exact
    receipt item; it does not authenticate that the provider call happened.
    """
    evidence, payload = _verified_raw_response_bytes(record, label, evidence_root)
    try:
        decoded = payload.decode("utf-8")
        envelope = json.loads(
            decoded,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise PublishBlocked(
            f"{label} raw response precisa ser JSON UTF-8 estruturado e sem ambiguidade"
        ) from None
    if not isinstance(envelope, dict):
        raise PublishBlocked(f"{label} raw response JSON precisa ser objeto")
    if payload != canonical_json_bytes(envelope):
        raise PublishBlocked(
            f"{label} raw response JSON não usa a serialização canônica exigida"
        )
    if set(envelope) != PUBLISH_READBACK_FIELDS:
        raise PublishBlocked(
            f"{label} raw response não segue o envelope canônico de readback"
        )
    if envelope.get("schema") != PUBLISH_READBACK_SCHEMA:
        raise PublishBlocked(f"{label} raw response usa schema de readback inválido")
    provider_response = envelope.get("provider_response")
    if (
        not isinstance(provider_response, dict)
        or set(provider_response) != PUBLISH_PROVIDER_RESPONSE_FIELDS
    ):
        raise PublishBlocked(
            f"{label} provider_response normalizado é incompleto ou ambíguo"
        )
    binding = envelope.get("binding")
    if not isinstance(binding, dict) or set(binding) != PUBLISH_READBACK_BINDING_FIELDS:
        raise PublishBlocked(f"{label} binding do raw response é incompleto ou ambíguo")
    expected_envelope = {
        "provider": record.get("provider"),
        "tool": record.get("tool"),
        "observed_at": record.get("observed_at"),
    }
    for field, expected in expected_envelope.items():
        if envelope.get(field) != expected:
            raise PublishBlocked(f"{label} raw response {field} diverge do receipt")
    for field in sorted(PUBLISH_READBACK_BINDING_FIELDS):
        if binding.get(field) != record.get(field):
            raise PublishBlocked(
                f"{label} raw response binding.{field} diverge do receipt"
            )
    if binding.get("status") != "PAUSED":
        raise PublishBlocked(f"{label} raw response não comprova status PAUSED")
    expected_provider_response = {
        "id": binding.get("ad_id"),
        "creative_id": binding.get("creative_id"),
        "account_id": binding.get("account_id"),
        "campaign_id": binding.get("campaign_id"),
        "ad_set_id": binding.get("ad_set_id"),
        "artifact_sha256": binding.get("artifact_sha256"),
        "status": binding.get("status"),
    }
    if provider_response != expected_provider_response:
        raise PublishBlocked(
            f"{label} provider_response normalizado diverge do binding PAUSED"
        )
    return evidence


def _parse_live_time(
    value,
    label: str,
    now: datetime,
    *,
    enforce_max_age: bool = True,
) -> datetime:
    try:
        observed_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if observed_at.tzinfo is None:
            raise ValueError
    except (TypeError, ValueError):
        raise PublishBlocked(f"{label} sem timestamp ISO-8601 válido") from None
    age_seconds = (now - observed_at).total_seconds()
    if enforce_max_age and age_seconds > LIVE_RECEIPT_MAX_AGE_SECONDS:
        raise PublishBlocked(f"{label} expirado; faça nova consulta live")
    if age_seconds < -CAPABILITY_FUTURE_TOLERANCE_SECONDS:
        raise PublishBlocked(f"{label} está no futuro")
    return observed_at


def _resolve_destination(
    records: list,
    app_config: dict,
    briefs: dict,
) -> tuple[dict, dict, str]:
    brief_refs = {record.get("brief_ref") for record in records}
    if None in brief_refs or "" in brief_refs:
        raise PublishBlocked("item sem brief_ref selado no QA")
    if len(brief_refs) != 1:
        raise PublishBlocked("um manifesto precisa apontar para um único brief_ref")
    brief_ref = next(iter(brief_refs))
    brief = (briefs or {}).get(brief_ref)
    if not isinstance(brief, dict):
        raise PublishBlocked(f"brief_ref '{brief_ref}' não resolvido")
    if brief.get("id") != brief_ref or brief.get("app") != app_config.get("slug"):
        raise PublishBlocked(f"brief_ref '{brief_ref}' diverge do app/ID esperado")

    requested = brief.get("destination", {}) or {}
    destination_type = requested.get("type")
    destination_ref = requested.get("ref") or (
        "default" if destination_type == "app_store" else None
    )
    configured = app_config.get("destinations", {}) or {}
    if destination_type == "app_store" and destination_ref == "default":
        selected = configured.get("default", {}) or {}
        custom_product_page_id = None
        receipt_type = "app_store_destination"
    elif destination_type == "custom_product_page":
        pages = configured.get("custom_product_pages", []) or []
        selected = next(
            (
                page
                for page in pages
                if page.get("id") == destination_ref
                or page.get("ref") == destination_ref
            ),
            None,
        ) or {}
        custom_product_page_id = selected.get("id") or selected.get("ref")
        receipt_type = "custom_product_page_destination"
    elif destination_type == "landing_page":
        selected = next(
            (
                page
                for page in configured.get("landing_pages", []) or []
                if page.get("id") == destination_ref
                or page.get("ref") == destination_ref
            ),
            None,
        ) or {}
        custom_product_page_id = None
        receipt_type = "landing_page_destination"
    else:
        raise PublishBlocked(f"destination.type não suportado: {destination_type}")

    if not selected:
        raise PublishBlocked(
            f"destination '{destination_ref}' não existe em apps/{app_config.get('slug')}.yaml"
        )
    if selected.get("type") != destination_type:
        raise PublishBlocked("destination.type do brief diverge da app config")
    if requested.get("url") != selected.get("url"):
        raise PublishBlocked("destination.url do brief diverge da app config")
    return (
        {
            "ref": destination_ref,
            "type": destination_type,
            "url": selected.get("url"),
            "custom_product_page_id": custom_product_page_id,
        },
        brief,
        receipt_type,
    )


def _verify_destination_readiness(
    app_config: dict,
    destination: dict,
    receipt_type: str,
    readiness_receipt: dict | None,
    now: datetime,
    evidence_root: str | Path,
) -> dict:
    required = (
        ((app_config.get("readiness", {}) or {}).get("required_receipts", {}) or {})
    )
    if receipt_type not in required:
        raise PublishBlocked(
            f"readiness não declara o gate específico '{receipt_type}'"
        )
    if not isinstance(readiness_receipt, dict):
        raise PublishBlocked(
            f"readiness receipt live '{receipt_type}' obrigatório antes de qualquer write"
        )
    for field in ("provider", "tool"):
        if not readiness_receipt.get(field):
            raise PublishBlocked(f"readiness receipt live sem {field}")
    if _is_write_tool(readiness_receipt.get("tool")):
        raise PublishBlocked(
            "readiness receipt live tool precisa ser read-only; não pode ser create/write"
        )
    if readiness_receipt.get("receipt_type") != receipt_type:
        raise PublishBlocked("readiness receipt live é de outro gate")
    if readiness_receipt.get("app") != app_config.get("slug"):
        raise PublishBlocked("readiness receipt live é de outro app")
    if readiness_receipt.get("status") != "ready":
        raise PublishBlocked("readiness receipt live não comprova status ready")
    if readiness_receipt.get("verification_basis") != "live_provider_readback":
        raise PublishBlocked("readiness precisa de consulta live ao provider")
    if readiness_receipt.get("local_validation_sufficient") is not False:
        raise PublishBlocked(
            "readiness deve declarar que validação local não substitui consulta live"
        )
    _parse_live_time(readiness_receipt.get("observed_at"), "readiness receipt live", now)
    response_evidence = verify_raw_response_evidence(
        readiness_receipt,
        "readiness receipt live",
        evidence_root,
    )
    receipt_destination = readiness_receipt.get("destination", {}) or {}
    for field in ("ref", "type", "url"):
        if receipt_destination.get(field) != destination.get(field):
            raise PublishBlocked(
                f"readiness receipt live destination.{field} diverge do brief"
            )
    if (
        destination.get("custom_product_page_id")
        and receipt_destination.get("custom_product_page_id")
        != destination.get("custom_product_page_id")
    ):
        raise PublishBlocked("readiness receipt live é de outro CPP")
    return {
        "receipt_type": receipt_type,
        "provider": readiness_receipt.get("provider"),
        "tool": readiness_receipt.get("tool"),
        "app": readiness_receipt.get("app"),
        "status": readiness_receipt.get("status"),
        "destination": dict(receipt_destination),
        "observed_at": readiness_receipt.get("observed_at"),
        **response_evidence,
        "verification_basis": "live_provider_readback",
        "local_validation_sufficient": False,
    }


def _readiness_records(payload: dict | None, expected_app: str) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("receipts")
    if raw is None:
        return [payload]
    if payload.get("app") != expected_app:
        raise PublishBlocked("readiness bundle é de outro app")
    if not isinstance(raw, list) or not raw:
        raise PublishBlocked("readiness bundle sem receipts")
    if not all(isinstance(item, dict) for item in raw):
        raise PublishBlocked("readiness bundle contém receipt inválido")
    return list(raw)


def _required_readiness_types(
    app_config: dict,
    selected_destination_receipt: str,
) -> list[str]:
    required = (
        ((app_config.get("readiness", {}) or {}).get("required_receipts", {}) or {})
    )
    if not isinstance(required, dict):
        raise PublishBlocked("app readiness.required_receipts precisa ser objeto")
    if selected_destination_receipt not in required:
        raise PublishBlocked(
            f"readiness não declara o gate específico '{selected_destination_receipt}'"
        )
    destination_policy = str(
        required.get(selected_destination_receipt) or ""
    ).strip().lower()
    if (
        not destination_policy
        or destination_policy in {"not_required", "not_applicable", "false"}
        or "blocked" in destination_policy
    ):
        raise PublishBlocked(
            f"readiness gate {selected_destination_receipt} não está habilitado: "
            f"{required.get(selected_destination_receipt)}"
        )
    selected = {selected_destination_receipt}
    for receipt_type, policy in required.items():
        if receipt_type == selected_destination_receipt:
            continue
        if receipt_type in DESTINATION_RECEIPT_TYPES:
            continue
        if receipt_type == "meta_video_publish":
            continue
        normalized_policy = str(policy or "").strip().lower()
        if not normalized_policy or normalized_policy in {
            "not_required",
            "not_applicable",
            "false",
        }:
            continue
        if "blocked" in normalized_policy:
            raise PublishBlocked(
                f"readiness gate {receipt_type} está bloqueado: {policy}"
            )
        selected.add(str(receipt_type))
    return sorted(selected)


def _seal_app_config_provenance(
    app_config: dict,
    workspace_root: str | Path,
) -> dict:
    root = qa.lexical_absolute_path(workspace_root)
    app = app_config.get("slug")
    if not isinstance(app, str) or not qa.CORE_PATH_SEGMENT_RE.fullmatch(app):
        raise PublishBlocked(f"app slug inválido para config canônica: {app!r}")
    path = root / "apps" / f"{app}.yaml"
    symlink = qa.first_symlink_component(path, root)
    if symlink is not None:
        raise PublishBlocked(f"app config canônica usa symlink: {symlink}")
    if not path.is_file():
        raise PublishBlocked(f"app config canônica ausente: {path}")
    try:
        loaded = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise PublishBlocked(f"app config canônica inválida: {exc}") from exc
    if not isinstance(loaded, dict) or loaded.get("slug") != app:
        raise PublishBlocked("app config canônica diverge do app solicitado")
    configured_policy = (
        ((app_config.get("readiness", {}) or {}).get("required_receipts", {}) or {})
    )
    loaded_policy = (
        ((loaded.get("readiness", {}) or {}).get("required_receipts", {}) or {})
    )
    if configured_policy != loaded_policy:
        raise PublishBlocked(
            "readiness policy recebida diverge de apps/<app>.yaml canônico"
        )
    return {
        "path": str(path),
        "resolved_path": str(path.resolve(strict=True)),
        "sha256": qa.sha256(path),
        "readiness_policy_digest": canonical_digest(
            {"required_receipts": loaded_policy}
        ),
    }


def _verify_app_config_provenance(
    manifest: dict,
    workspace_root: str | Path,
    expected_app: str | None,
) -> tuple[list[str], list[str]]:
    errors = []
    provenance = manifest.get("app_config_provenance")
    if not isinstance(provenance, dict):
        return [], ["manifest sem app_config_provenance canônica"]
    root = qa.lexical_absolute_path(workspace_root)
    if not isinstance(expected_app, str) or not qa.CORE_PATH_SEGMENT_RE.fullmatch(
        expected_app
    ):
        return [], [f"verify receipt expected_app inválido: {expected_app!r}"]
    app = expected_app
    path = root / "apps" / f"{app}.yaml"
    if provenance.get("path") != str(path):
        errors.append("manifest app_config_provenance.path não é canônico")
    symlink = qa.first_symlink_component(path, root)
    if symlink is not None:
        errors.append(f"manifest app config usa symlink ancestral: {symlink}")
        return [], errors
    if not path.is_file():
        return [], [*errors, f"manifest app config canônica ausente: {path}"]
    try:
        resolved = str(path.resolve(strict=True))
        loaded = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        return [], [*errors, f"manifest app config canônica inválida: {exc}"]
    if provenance.get("resolved_path") != resolved:
        errors.append("manifest app_config_provenance.resolved_path diverge")
    if provenance.get("sha256") != qa.sha256(path):
        errors.append("manifest app_config_provenance.sha256 diverge")
    if not isinstance(loaded, dict) or loaded.get("slug") != app:
        return [], [*errors, "manifest app config canônica é de outro app"]
    loaded_policy = (
        ((loaded.get("readiness", {}) or {}).get("required_receipts", {}) or {})
    )
    expected_policy_digest = canonical_digest(
        {"required_receipts": loaded_policy}
    )
    if provenance.get("readiness_policy_digest") != expected_policy_digest:
        errors.append("manifest readiness policy digest diverge da app config canônica")
    destination = manifest.get("destination") or {}
    destination_receipt = DESTINATION_TYPE_RECEIPTS.get(
        destination.get("type") if isinstance(destination, dict) else None
    )
    if not destination_receipt:
        return [], [*errors, "manifest destination não resolve readiness policy"]
    try:
        required_types = _required_readiness_types(loaded, destination_receipt)
    except PublishBlocked as exc:
        return [], [*errors, f"manifest app readiness policy inválida: {exc}"]
    return required_types, errors


def _verify_required_runtime_readiness(
    app_config: dict,
    receipts: list[dict],
    *,
    selected_destination_type: str,
    now: datetime,
    evidence_root: str | Path,
) -> list[dict]:
    required = (
        ((app_config.get("readiness", {}) or {}).get("required_receipts", {}) or {})
    )
    summaries = []
    for receipt_type, policy in required.items():
        if receipt_type == selected_destination_type:
            continue
        if receipt_type in DESTINATION_RECEIPT_TYPES:
            continue
        if receipt_type == "meta_video_publish":
            # Image publication is the only implemented manifest path. Video
            # is blocked earlier and will require this receipt when supported.
            continue
        normalized_policy = str(policy or "").strip().lower()
        if not normalized_policy or normalized_policy in {
            "not_required",
            "not_applicable",
            "false",
        }:
            continue
        if "blocked" in normalized_policy:
            raise PublishBlocked(
                f"readiness gate {receipt_type} está bloqueado: {policy}"
            )
        receipt = next(
            (item for item in receipts if item.get("receipt_type") == receipt_type),
            None,
        )
        if not isinstance(receipt, dict):
            raise PublishBlocked(
                f"readiness receipt live '{receipt_type}' obrigatório antes de qualquer write"
            )
        for field in ("provider", "tool"):
            if not receipt.get(field):
                raise PublishBlocked(f"readiness {receipt_type} sem {field}")
        if _is_write_tool(receipt.get("tool")):
            raise PublishBlocked(
                f"readiness {receipt_type} tool precisa ser read-only; "
                "não pode ser create/write"
            )
        if receipt.get("app") != app_config.get("slug"):
            raise PublishBlocked(f"readiness {receipt_type} é de outro app")
        if receipt.get("status") != "ready":
            raise PublishBlocked(f"readiness {receipt_type} não está ready")
        if receipt.get("verification_basis") != "live_provider_readback":
            raise PublishBlocked(f"readiness {receipt_type} não veio de readback live")
        if receipt.get("local_validation_sufficient") is not False:
            raise PublishBlocked(
                f"readiness {receipt_type} precisa negar suficiência local"
            )
        _parse_live_time(
            receipt.get("observed_at"), f"readiness {receipt_type}", now
        )
        response_evidence = verify_raw_response_evidence(
            receipt,
            f"readiness {receipt_type}",
            evidence_root,
        )
        summaries.append(
            {
                "receipt_type": receipt_type,
                "provider": receipt.get("provider"),
                "tool": receipt.get("tool"),
                "app": receipt.get("app"),
                "status": receipt.get("status"),
                "observed_at": receipt.get("observed_at"),
                **response_evidence,
                "verification_basis": "live_provider_readback",
                "local_validation_sufficient": False,
            }
        )
    return summaries


def resolve_audience(audience_plan: dict, audience_id: str, markets: list) -> tuple:
    """Return (audience, market) or raise PublishBlocked — plan must be valid,
    the audience approved, and its market declared by the app."""
    audit = audiences.audit_plan(audience_plan, markets)
    if audit["errors"]:
        raise PublishBlocked(
            "audience plan inválido: " + "; ".join(audit["errors"])
        )
    audience = audiences.get_audience(audience_plan, audience_id)
    if audience is None:
        raise PublishBlocked(f"audience '{audience_id}' não existe no plano")
    if audience.get("status") != "approved":
        raise PublishBlocked(
            f"audience '{audience_id}' não está approved "
            "(aprove no audiences/<app>.yaml antes do publish)"
        )
    market = next(
        (m for m in markets if m.get("id") == audience.get("market")), None
    )
    if market is None:
        raise PublishBlocked(f"market do audience '{audience_id}' não resolvido")
    return audience, market


def prepare_manifest(
    report: dict,
    capabilities: dict,
    *,
    account_id: str,
    campaign_id: str,
    ad_set_id: str,
    audience_plan: dict,
    audience_id: str,
    markets: list,
    publish_policy: dict,
    app_config: dict,
    briefs: dict,
    readiness_receipt: dict | None,
    evidence_root: str | Path = ROOT,
    workspace_root: str | Path | None = None,
    now: datetime | None = None,
    expected_app: str | None = None,
) -> dict:
    if report.get("automated_status") != "pass":
        raise PublishBlocked("QA automático não passou")
    if report.get("visual_status") != "approved":
        raise PublishBlocked("revisão visual ainda não foi aprovada")
    if report.get("version") != 2 or report.get("provenance_required") is not True:
        raise PublishBlocked(
            "publicação exige QA com proveniência version 2; regenere a batch"
        )
    workspace_root = Path(workspace_root) if workspace_root is not None else ROOT
    file_errors = qa.verify_report_files(report, expected_root=workspace_root)
    if file_errors:
        raise PublishBlocked("artefatos mudaram após QA: " + "; ".join(file_errors))
    app_config_provenance = _seal_app_config_provenance(
        app_config,
        workspace_root,
    )
    if any(
        record.get("media_kind") == "video"
        for record in report.get("records", []) or []
    ):
        raise PublishBlocked(
            "vídeo sem capability comprovada de upload, processamento, criação "
            "e readback PAUSED; render local não autoriza publicação Meta"
        )
    report_app = report.get("app")
    audience_app = audience_plan.get("app")
    if audience_app != report_app:
        raise PublishBlocked(
            f"audience plan do app '{audience_app}' diverge do report '{report_app}'"
        )
    if expected_app is not None and report_app != expected_app:
        raise PublishBlocked(
            f"app config '{expected_app}' diverge do report '{report_app}'"
        )
    if capabilities.get("provider") != "meta_ads_mcp":
        raise PublishBlocked("capability receipt não é do provider meta_ads_mcp")
    if not capabilities.get("agent"):
        raise PublishBlocked("capability receipt sem agent")
    now = now or datetime.now(timezone.utc)
    try:
        checked_at = datetime.fromisoformat(
            str(capabilities.get("checked_at")).replace("Z", "+00:00")
        )
        if checked_at.tzinfo is None:
            raise ValueError
        age_seconds = (now - checked_at).total_seconds()
        if age_seconds > CAPABILITY_MAX_AGE_SECONDS:
            raise PublishBlocked("capability receipt expirado; descubra as tools novamente")
        if age_seconds < -CAPABILITY_FUTURE_TOLERANCE_SECONDS:
            raise PublishBlocked("capability receipt está no futuro")
    except (TypeError, ValueError):
        raise PublishBlocked("capability receipt sem checked_at ISO-8601 válido") from None
    missing_tools = sorted(REQUIRED_META_TOOLS - set(capabilities.get("tools", [])))
    if missing_tools:
        raise PublishBlocked("Meta Ads MCP sem tools obrigatórias: " + ", ".join(missing_tools))
    readback_tool = capabilities.get("readback_tool")
    if not isinstance(readback_tool, str) or not readback_tool.strip():
        raise PublishBlocked("capability receipt sem readback_tool real")
    if readback_tool not in set(capabilities.get("tools", [])):
        raise PublishBlocked("capability readback_tool não aparece nas tools descobertas")
    if not _is_meta_ad_readback_tool(readback_tool):
        raise PublishBlocked(
            "capability readback_tool precisa ser o getter read-only ads_get_ad; "
            "não pode ser uma tool de create/write"
        )
    if readback_tool.strip().lower() in {"readback", "manual", "local", "unknown", "n/a"}:
        raise PublishBlocked("capability readback_tool é placeholder, não uma tool real")
    if not all((account_id, campaign_id, ad_set_id)):
        raise PublishBlocked("account_id, campaign_id e ad_set_id são obrigatórios")

    audience, market = resolve_audience(audience_plan, audience_id, markets)

    # One ad per CONCEPT+VARIANT in the primary format. Publishing every format
    # as a separate ad fragments learning, but collapsing distinct variants of
    # the same concept silently destroys the experiment design.
    primary_format = (publish_policy or {}).get("primary_format")
    if not primary_format:
        raise PublishBlocked(
            "app sem publish.primary_format — defina em apps/<app>.yaml "
            "(um ad por conceito, formato primário)"
        )
    max_ads = int((publish_policy or {}).get("max_ads_per_ad_set", 6))

    records = report.get("records", []) or []
    if not records:
        raise PublishBlocked("matriz de QA vazia")
    records = [r for r in records if r.get("locale") == market.get("locale")]
    if not records:
        raise PublishBlocked(
            f"matriz de QA não contém criativos do mercado '{market.get('id')}' "
            f"({market.get('locale')}) exigido pelo audience '{audience_id}'"
        )
    records = [r for r in records if r.get("format") == primary_format]
    if not records:
        raise PublishBlocked(
            f"matriz de QA não contém o formato primário '{primary_format}' "
            f"para o mercado '{market.get('id')}'"
        )
    destination, brief, receipt_type = _resolve_destination(
        records, app_config, briefs
    )
    readiness_records = _readiness_records(
        readiness_receipt, str(app_config.get("slug") or "")
    )
    destination_receipt = next(
        (
            item
            for item in readiness_records
            if item.get("receipt_type") == receipt_type
        ),
        None,
    )
    readiness_evidence = _verify_destination_readiness(
        app_config,
        destination,
        receipt_type,
        destination_receipt,
        now,
        evidence_root,
    )
    runtime_readiness = _verify_required_runtime_readiness(
        app_config,
        readiness_records,
        selected_destination_type=receipt_type,
        now=now,
        evidence_root=evidence_root,
    )
    required_readiness_types = _required_readiness_types(
        app_config,
        receipt_type,
    )
    observed_readiness_types = sorted(
        {
            readiness_evidence["receipt_type"],
            *(item["receipt_type"] for item in runtime_readiness),
        }
    )
    if observed_readiness_types != required_readiness_types:
        raise PublishBlocked(
            "readiness receipts verificados divergem da policy canônica do app"
        )

    for record in records:
        for field in ("concept_id", "variant_id"):
            if not record.get(field):
                raise PublishBlocked(
                    f"item {record.get('recipe')} sem {field} selado no QA"
                )
        if not str(record.get("cta") or "").strip():
            raise PublishBlocked(
                f"item {record.get('recipe')} sem CTA localizada selada no QA"
            )
        ad_copy = record.get("ad_copy")
        if not isinstance(ad_copy, dict):
            raise PublishBlocked(
                f"item {record.get('recipe')} sem ad_copy localizada selada no QA"
            )
        for field in ("primary_text", "headline"):
            if not str(ad_copy.get(field) or "").strip():
                raise PublishBlocked(
                    f"item {record.get('recipe')} sem ad_copy.{field}"
                )
    selected_by_variant = {}
    for record in sorted(
        records,
        key=lambda item: (
            str(item.get("concept_id")),
            str(item.get("variant_id")),
            str(item.get("sha256")),
        ),
    ):
        variant_key = (record.get("concept_id"), record.get("variant_id"))
        if variant_key in selected_by_variant:
            raise PublishBlocked(
                "seleção ambígua: mais de um artefato primário para "
                f"concept_id={variant_key[0]} variant_id={variant_key[1]}"
            )
        selected_by_variant[variant_key] = record
    records = list(selected_by_variant.values())
    if len(records) > max_ads:
        raise PublishBlocked(
            f"{len(records)} variantes excedem max_ads_per_ad_set={max_ads} — "
            "divida em rodadas de teste"
        )
    items = []
    for record in records:
        if not record.get("research_refs"):
            raise PublishBlocked(
                f"item {record.get('recipe')}/{record.get('locale')}/"
                f"{record.get('format')} sem linhagem de evidência (research_refs) — "
                "este workflow publica somente criativos com evidência rastreável; "
                "regenere a batch com o pipeline atual"
            )
        concept_lineage = record.get("concept_lineage")
        concept_lineage_ref = record.get("concept_lineage_ref")
        execution_lineage = record.get("execution_lineage")
        execution_ref = record.get("execution_ref")
        if concept_lineage not in research.ALLOWED_LINEAGE:
            raise PublishBlocked(
                f"item {record.get('recipe')} sem concept_lineage válida selada no QA"
            )
        if not str(concept_lineage_ref or "").strip():
            raise PublishBlocked(
                f"item {record.get('recipe')} sem concept_lineage_ref selada no QA"
            )
        if execution_lineage not in {*research.ALLOWED_LINEAGE, "original"}:
            raise PublishBlocked(
                f"item {record.get('recipe')} sem execution_lineage válida selada no QA"
            )
        if execution_ref and execution_ref not in record.get("research_refs", []):
            raise PublishBlocked(
                f"item {record.get('recipe')} execution_ref não rastreável no QA"
            )
        if execution_lineage == "competitor_pattern" and not execution_ref:
            raise PublishBlocked(
                f"item {record.get('recipe')} competitor_pattern sem execution_ref"
            )
        if execution_lineage == "competitor_pattern" and not str(
            record.get("swiped_from") or ""
        ).strip():
            raise PublishBlocked(
                f"item {record.get('recipe')} execution competitor_pattern sem swiped_from"
            )
        item_key = hashlib.sha256(
            (
                f"{record.get('brief_ref')}|{record.get('concept_id')}|"
                f"{record.get('variant_id')}|{record.get('locale')}|"
                f"{record.get('format')}|"
                f"{record.get('sha256')}"
            ).encode()
        ).hexdigest()[:20]
        deterministic_name = "-".join(
            str(value)
            for value in (
                report.get("app"),
                report.get("batch_id"),
                record.get("concept_id"),
                record.get("variant_id"),
                record.get("locale"),
                record.get("format"),
                item_key,
            )
        )
        item = {
            "item_key": item_key,
            "recipe": record.get("recipe"),
            "brief_ref": record.get("brief_ref"),
            "concept_id": record.get("concept_id"),
            "variant_id": record.get("variant_id"),
            "locale": record.get("locale"),
            "copy_language": record.get("copy_language"),
            "format": record.get("format"),
            "image_path": record.get("path"),
            "sha256": record.get("sha256"),
            "research_refs": list(record.get("research_refs", []) or []),
            "swiped_from": record.get("swiped_from", ""),
            "concept_lineage": concept_lineage,
            "concept_lineage_ref": concept_lineage_ref,
            "execution_lineage": execution_lineage,
            "execution_ref": execution_ref,
            "creative_name": f"creative-{deterministic_name}",
            "ad_name": f"ad-{deterministic_name}",
            "requested_status": "PAUSED",
            "destination_ref": destination.get("ref"),
        }
        if record.get("cta"):
            item["cta"] = record.get("cta")
        if isinstance(record.get("ad_copy"), dict) and record.get("ad_copy"):
            item["ad_copy"] = dict(record.get("ad_copy"))
        items.append(item)
    manifest = {
        "version": 2,
        "provider": "meta_ads_mcp",
        "app": report.get("app"),
        "batch_id": report.get("batch_id"),
        "created_at": now.isoformat(),
        "qa_matrix_digest": report.get("matrix_digest"),
        "capability_checked_at": capabilities.get("checked_at"),
        "capability_agent": capabilities.get("agent"),
        "destination": destination,
        "app_config_provenance": app_config_provenance,
        "destination_readiness": readiness_evidence,
        "runtime_readiness": runtime_readiness,
        "required_readiness_receipt_types": required_readiness_types,
        "brief": {
            "ref": brief.get("id"),
            "objective": brief.get("objective"),
            "primary_kpi": brief.get("primary_kpi"),
        },
        "account_id": account_id,
        "campaign_id": campaign_id,
        "ad_set_id": ad_set_id,
        "audience": {
            "id": audience.get("id"),
            "market": audience.get("market"),
            "funnel_stage": audience.get("funnel_stage"),
            "targeting_kind": (audience.get("targeting", {}) or {}).get("kind"),
            "countries": list((audience.get("targeting", {}) or {}).get("countries", [])),
            "optimization_event": audience.get("optimization_event"),
            "copy_language": (audience.get("creatives", {}) or {}).get("copy_language"),
            "approved_by": audience.get("approved_by"),
            "plan_updated_at": audience_plan.get("updated_at"),
        },
        "format_policy": {
            "primary_format": primary_format,
            "max_ads_per_ad_set": max_ads,
            "one_ad_per_concept_variant": True,
            "deduplication_key": "concept_id+variant_id",
        },
        "readback_requirement": {
            "provider": "meta_ads_mcp",
            "tool": readback_tool,
            "verification_basis": "live_provider_readback",
            "local_validation_sufficient": False,
        },
        "requested_status": "PAUSED",
        "activation_allowed": False,
        "items": items,
    }
    manifest["manifest_digest"] = canonical_digest(manifest)
    return manifest


def verify_receipt(
    manifest: dict,
    receipt: dict,
    *,
    expected_app: str,
    now: datetime | None = None,
    evidence_root: str | Path = ROOT,
    workspace_root: str | Path = ROOT,
    enforce_freshness: bool = True,
) -> list:
    errors = []
    now = now or datetime.now(timezone.utc)
    manifest_payload = {
        key: value for key, value in manifest.items() if key != "manifest_digest"
    }
    if manifest.get("manifest_digest") != canonical_digest(manifest_payload):
        return [
            "manifest_digest do manifesto não corresponde ao conteúdo canônico "
            "do manifesto"
        ]
    if not isinstance(expected_app, str) or not qa.CORE_PATH_SEGMENT_RE.fullmatch(
        expected_app
    ):
        errors.append(f"verify receipt expected_app inválido: {expected_app!r}")
    elif manifest.get("app") != expected_app:
        errors.append(
            f"manifest app '{manifest.get('app')}' diverge de expected_app "
            f"'{expected_app}'"
        )
    if manifest.get("version") != 2:
        errors.append("manifest version precisa ser 2")
    if manifest.get("provider") != "meta_ads_mcp":
        errors.append("manifest provider precisa ser meta_ads_mcp")
    readback_requirement = manifest.get("readback_requirement")
    if not isinstance(readback_requirement, dict):
        errors.append("manifest readback_requirement precisa ser objeto")
        readback_requirement = {}
    if readback_requirement.get("provider") != "meta_ads_mcp":
        errors.append("manifest readback provider precisa ser meta_ads_mcp")
    if readback_requirement.get("verification_basis") != "live_provider_readback":
        errors.append("manifest readback exige verification_basis live_provider_readback")
    if readback_requirement.get("local_validation_sufficient") is not False:
        errors.append("manifest readback precisa negar suficiência da validação local")
    readback_tool = readback_requirement.get("tool")
    normalized_readback_tool = str(readback_tool or "").strip().lower()
    if not normalized_readback_tool:
        errors.append("manifest readback tool real é obrigatória")
    elif not _is_meta_ad_readback_tool(readback_tool):
        errors.append(
            "manifest readback tool precisa ser o getter read-only ads_get_ad; "
            "não pode ser uma tool de create/write"
        )
    elif normalized_readback_tool in {
        "readback",
        "manual",
        "local",
        "unknown",
        "n/a",
    }:
        errors.append("manifest readback tool é placeholder")
    if manifest.get("requested_status") != "PAUSED":
        errors.append("manifest requested_status precisa ser PAUSED")
    if manifest.get("activation_allowed") is not False:
        errors.append("manifest activation_allowed precisa ser false")
    manifest_items = manifest.get("items", []) or []
    if not isinstance(manifest_items, list) or not manifest_items:
        errors.append("manifest items precisa ser uma lista não vazia")
        manifest_items = []
    for item in manifest_items:
        if not isinstance(item, dict):
            errors.append("manifest item precisa ser objeto")
        elif item.get("requested_status") != "PAUSED":
            errors.append(
                f"manifest item {item.get('item_key')} requested_status precisa ser PAUSED"
            )
    manifest_keys = [
        item.get("item_key") if isinstance(item, dict) else None
        for item in manifest_items
    ]
    if any(not isinstance(key, str) or not key.strip() for key in manifest_keys):
        errors.append("manifest item_key precisa ser string não vazia")
    valid_manifest_keys = [
        key for key in manifest_keys if isinstance(key, str) and key.strip()
    ]
    if len(valid_manifest_keys) != len(set(valid_manifest_keys)):
        errors.append("manifest item_key duplicado")
    if receipt.get("manifest_digest") != manifest.get("manifest_digest"):
        errors.append("receipt manifest_digest não corresponde ao manifesto")
    if receipt.get("provider") != manifest.get("provider"):
        errors.append("receipt provider não corresponde ao manifesto")
    if receipt.get("verification_basis") != "live_provider_readback":
        errors.append("receipt não comprova consulta live ao provider")
    if receipt.get("local_validation_sufficient") is not False:
        errors.append(
            "receipt precisa declarar que validação local não substitui consulta live"
        )
    runtime_readiness = manifest.get("runtime_readiness", [])
    if not isinstance(runtime_readiness, list):
        errors.append("manifest runtime_readiness precisa ser lista")
        runtime_readiness = []
    required_readiness = manifest.get("required_readiness_receipt_types")
    if not isinstance(required_readiness, list) or not required_readiness:
        errors.append("manifest required readiness receipt types precisa ser lista não vazia")
        required_readiness = []
    elif not all(
        isinstance(item, str) and item.strip() for item in required_readiness
    ):
        errors.append("manifest required readiness receipt types contém valor inválido")
        required_readiness = []
    elif len(required_readiness) != len(set(required_readiness)):
        errors.append("manifest required readiness receipt types contém duplicatas")
    canonical_required_readiness, policy_errors = _verify_app_config_provenance(
        manifest,
        workspace_root,
        expected_app,
    )
    errors.extend(policy_errors)
    if required_readiness != canonical_required_readiness:
        errors.append(
            "manifest required readiness receipt types divergem da app config canônica"
        )
    readiness_evidence = [
        ("destination readiness", manifest.get("destination_readiness"))
    ]
    readiness_evidence.extend(
        (f"runtime readiness {index}", evidence)
        for index, evidence in enumerate(runtime_readiness)
    )
    observed_readiness_types = [
        evidence.get("receipt_type")
        for _, evidence in readiness_evidence
        if isinstance(evidence, dict)
    ]
    if len(observed_readiness_types) != len(set(observed_readiness_types)):
        errors.append("manifest readiness receipt_type duplicado")
    if set(observed_readiness_types) != set(required_readiness):
        errors.append(
            "manifest required readiness receipt types divergem das evidências seladas"
        )
    destination = manifest.get("destination") or {}
    expected_destination_receipt = DESTINATION_TYPE_RECEIPTS.get(
        destination.get("type") if isinstance(destination, dict) else None
    )
    destination_evidence = manifest.get("destination_readiness")
    if not isinstance(destination_evidence, dict):
        destination_evidence = {}
    if not expected_destination_receipt:
        errors.append("manifest destination.type não resolve readiness receipt")
    elif destination_evidence.get("receipt_type") != expected_destination_receipt:
        errors.append(
            "manifest destination readiness receipt_type diverge de destination.type"
        )
    for label, evidence in readiness_evidence:
        if not isinstance(evidence, dict):
            errors.append(f"manifesto sem {label} evidence selada")
            continue
        if not evidence.get("provider") or not evidence.get("tool"):
            errors.append(f"manifest {label} sem provider/tool")
        if _is_write_tool(evidence.get("tool")):
            errors.append(
                f"manifest {label} readiness tool precisa ser read-only; "
                "não pode ser create/write"
            )
        if not evidence.get("receipt_type"):
            errors.append(f"manifest {label} sem receipt_type")
        if evidence.get("app") != manifest.get("app"):
            errors.append(f"manifest {label} app diverge do manifesto")
        if evidence.get("status") != "ready":
            errors.append(f"manifest {label} status precisa ser ready")
        if label == "destination readiness":
            evidence_destination = evidence.get("destination")
            manifest_destination = manifest.get("destination")
            if not isinstance(evidence_destination, dict):
                errors.append("manifest destination readiness sem destination selada")
            elif not isinstance(manifest_destination, dict):
                errors.append("manifest destination inválida")
            else:
                for field in (
                    "ref",
                    "type",
                    "url",
                    "custom_product_page_id",
                ):
                    if evidence_destination.get(field) != manifest_destination.get(field):
                        errors.append(
                            "manifest destination readiness destination."
                            f"{field} diverge do manifesto"
                        )
        if evidence.get("verification_basis") != "live_provider_readback":
            errors.append(f"manifest {label} não comprova readiness por readback live")
        if evidence.get("local_validation_sufficient") is not False:
            errors.append(f"manifest {label} precisa negar suficiência local")
        try:
            _parse_live_time(
                evidence.get("observed_at"),
                f"manifest {label}",
                now,
                enforce_max_age=enforce_freshness,
            )
        except PublishBlocked as exc:
            errors.append(str(exc))
        try:
            verify_raw_response_evidence(
                evidence,
                f"manifest readiness {label} {evidence.get('receipt_type')}",
                evidence_root,
            )
        except PublishBlocked as exc:
            errors.append(str(exc))
    status = receipt.get("delivery_status")
    if status != "PAUSED":
        errors.append(f"delivery_status precisa ser PAUSED, recebido {status}")
    expected = {
        item.get("item_key")
        for item in manifest_items
        if isinstance(item, dict) and isinstance(item.get("item_key"), str) and item.get("item_key")
    }
    received_items = receipt.get("items", []) or []
    received_keys = [item.get("item_key") for item in received_items]
    received = set(received_keys)
    if len(received_keys) != len(received):
        errors.append("receipt contém item_keys duplicados")
    if received != expected:
        errors.append("receipt não cobre exatamente todos os items do manifesto")
    expected_items = {
        item.get("item_key"): item for item in manifest_items if isinstance(item, dict)
    }
    readback_tool = readback_requirement.get("tool")
    try:
        manifest_created_at = datetime.fromisoformat(
            str(manifest.get("created_at")).replace("Z", "+00:00")
        )
        if manifest_created_at.tzinfo is None:
            raise ValueError
    except (TypeError, ValueError):
        manifest_created_at = None
        errors.append("manifesto sem created_at ISO-8601 válido")
    seen_creative_ids = {}
    seen_ad_ids = {}
    for item in received_items:
        expected_item = expected_items.get(item.get("item_key"), {})
        if not item.get("creative_id") or not item.get("ad_id"):
            errors.append(f"receipt item {item.get('item_key')} sem creative_id/ad_id")
        for field, seen in (
            ("creative_id", seen_creative_ids),
            ("ad_id", seen_ad_ids),
        ):
            external_id = item.get(field)
            if external_id:
                if external_id in seen:
                    errors.append(
                        f"receipt {field} duplicado entre items "
                        f"{seen[external_id]} e {item.get('item_key')}"
                    )
                else:
                    seen[external_id] = item.get("item_key")
        if item.get("status") != "PAUSED":
            errors.append(
                f"receipt item {item.get('item_key')} não comprova status PAUSED"
            )
        expected_values = {
            "provider": manifest.get("provider"),
            "tool": readback_tool,
            "account_id": manifest.get("account_id"),
            "campaign_id": manifest.get("campaign_id"),
            "ad_set_id": manifest.get("ad_set_id"),
            "artifact_sha256": expected_item.get("sha256"),
        }
        for field, expected_value in expected_values.items():
            if item.get(field) != expected_value:
                errors.append(
                    f"receipt item {item.get('item_key')} {field} diverge do manifesto"
                )
        try:
            observed_at = datetime.fromisoformat(
                str(item.get("observed_at")).replace("Z", "+00:00")
            )
            if observed_at.tzinfo is None:
                raise ValueError
            if manifest_created_at and observed_at < manifest_created_at:
                errors.append(
                    f"receipt item {item.get('item_key')} observed_at antecede o manifesto"
                )
            try:
                _parse_live_time(
                    item.get("observed_at"),
                    f"receipt item {item.get('item_key')} readback live",
                    now,
                    enforce_max_age=enforce_freshness,
                )
            except PublishBlocked as exc:
                errors.append(str(exc))
        except (TypeError, ValueError):
            errors.append(
                f"receipt item {item.get('item_key')} sem observed_at ISO-8601 válido"
            )
        try:
            verify_publish_readback_evidence(
                item,
                f"receipt item {item.get('item_key')} readback live",
                evidence_root,
            )
        except PublishBlocked as exc:
            errors.append(str(exc))
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="creative-forge — Meta Ads MCP handoff")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--qa-report", required=True)
    prepare_parser.add_argument("--capabilities", required=True)
    prepare_parser.add_argument("--account-id", required=True)
    prepare_parser.add_argument("--campaign-id", required=True)
    prepare_parser.add_argument("--ad-set-id", required=True)
    prepare_parser.add_argument("--audience-id", required=True)
    prepare_parser.add_argument("--readiness-receipt", required=True)
    prepare_parser.add_argument("--evidence-root", default=str(ROOT))
    prepare_parser.add_argument("--out", required=True)
    verify_parser = sub.add_parser("verify-receipt")
    verify_parser.add_argument("--manifest", required=True)
    verify_parser.add_argument("--receipt", required=True)
    verify_parser.add_argument("--app", required=True)
    verify_parser.add_argument("--evidence-root", default=str(ROOT))
    args = parser.parse_args()

    if args.command == "prepare":
        try:
            from scripts import render
        except ImportError:
            import render

        report = json.loads(Path(args.qa_report).read_text())
        capabilities = json.loads(Path(args.capabilities).read_text())
        readiness_receipt = json.loads(Path(args.readiness_receipt).read_text())
        app_slug = report.get("app")
        root = default_root()
        plan_path = root / "audiences" / f"{app_slug}.yaml"
        try:
            if not plan_path.exists():
                raise PublishBlocked(
                    f"sem audience plan para '{app_slug}' ({plan_path})"
                )
            app_cfg = render.load_yaml(root / "apps" / f"{app_slug}.yaml")
            briefs = {
                path.stem: render.load_yaml(path)
                for path in sorted((root / "briefs" / app_slug).glob("*.yaml"))
            }
            manifest = prepare_manifest(
                report,
                capabilities,
                account_id=args.account_id,
                campaign_id=args.campaign_id,
                ad_set_id=args.ad_set_id,
                audience_plan=audiences.load_yaml(plan_path),
                audience_id=args.audience_id,
                markets=render.app_target_markets(app_cfg),
                publish_policy=app_cfg.get("publish", {}) or {},
                app_config=app_cfg,
                briefs=briefs,
                readiness_receipt=readiness_receipt,
                evidence_root=Path(args.evidence_root),
                expected_app=app_cfg.get("slug"),
            )
        except PublishBlocked as exc:
            sys.exit(f"creative-forge: publish blocked: {exc}")
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        print(out)
    else:
        manifest = json.loads(Path(args.manifest).read_text())
        receipt = json.loads(Path(args.receipt).read_text())
        errors = verify_receipt(
            manifest,
            receipt,
            expected_app=args.app,
            evidence_root=Path(args.evidence_root),
            workspace_root=ROOT,
        )
        for error in errors:
            print(f"  ❌ {error}")
        if errors:
            sys.exit(1)
        print("✓ publish receipt válido; todos os ads permanecem PAUSED")


if __name__ == "__main__":
    main()
