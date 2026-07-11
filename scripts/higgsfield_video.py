#!/usr/bin/env python3
"""Higgsfield schema/cost adapter; paid creation remains deliberately fail-closed."""

import argparse
import hashlib
import ipaddress
import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

CLI = "higgsfield"
DEFAULT_MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
CLI_TIMEOUT_SECONDS = 120
WAIT_TIMEOUT_SECONDS = 21 * 60
FFPROBE_TIMEOUT_SECONDS = 30
PAID_GENERATION_BLOCK_REASON = (
    "geração paga Higgsfield não suportada: o CLI retorna apenas estimativa e "
    "não oferece teto de custo imposto pelo provider nem reconciliação de custo real"
)


def canonical_sha256(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_atomic(path: Path, value: dict) -> None:
    """Durably replace a JSON checkpoint without exposing a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".partial", dir=path.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def validate_params(schema: dict, params: dict) -> list:
    errors = []
    if schema.get("type") != "video":
        errors.append(f"modelo descoberto não é video: {schema.get('type')}")
    definitions = {item.get("name"): item for item in schema.get("params", []) or []}
    for name in params:
        if name not in definitions:
            errors.append(f"param inventado/não descoberto: {name}")
    for name, definition in definitions.items():
        value = params.get(name)
        if definition.get("required") and (value is None or value == ""):
            errors.append(f"param obrigatório ausente: {name}")
            continue
        if value is None:
            continue
        expected_type = str(definition.get("type") or "")
        valid_type = {
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "array": isinstance(value, list),
        }.get(expected_type.split("|")[0], True)
        if not valid_type:
            errors.append(f"param {name} tem tipo inválido; esperado {expected_type}")
        allowed = definition.get("enum")
        if allowed and value not in allowed:
            errors.append(f"param {name}={value!r} fora do enum descoberto {allowed}")
    return errors


def encode_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def parameter_args(params: dict) -> list[str]:
    args = []
    for name, value in params.items():
        if value is None:
            continue
        args.extend([f"--{name.replace('_', '-')}", encode_value(value)])
    return args


def build_cost_command(model: str, params: dict) -> list[str]:
    return [CLI, "generate", "cost", model, *parameter_args(params), "--json"]


def build_create_command(
    model: str, params: dict, *, confirm_spend: bool
) -> list[str]:
    if not confirm_spend:
        raise ValueError("geração paga bloqueada: passe --confirm-spend explicitamente")
    return [CLI, "generate", "create", model, *parameter_args(params), "--json"]


def _run_process_group(
    command: list[str], *, timeout_seconds: float
) -> subprocess.CompletedProcess:
    """Bound every provider process and terminate its complete child process group."""
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.communicate(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
        raise exc
    return subprocess.CompletedProcess(
        command, process.returncode, stdout=stdout, stderr=stderr
    )


def run_json(
    command: list[str], *, timeout_seconds: float = CLI_TIMEOUT_SECONDS
) -> dict:
    try:
        completed = _run_process_group(command, timeout_seconds=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Higgsfield CLI excedeu timeout externo de {timeout_seconds:g}s"
        ) from exc
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"Higgsfield CLI falhou ({completed.returncode}): {message}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Higgsfield CLI não retornou JSON válido") from exc


def discover_model(model: str) -> dict:
    return run_json([CLI, "model", "get", model, "--json"])


def estimate_cost(model: str, params: dict, schema: dict | None = None) -> dict:
    schema = schema or discover_model(model)
    errors = validate_params(schema, params)
    if errors:
        raise ValueError("; ".join(errors))
    return run_json(build_cost_command(model, params))


def _number(value, label: str) -> int | float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{label} precisa ser número finito não negativo")
    return value


def _aware_datetime(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"{label} precisa ser timestamp ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} precisa incluir timezone")
    return parsed


def build_quote_receipt(
    *,
    model: str,
    params: dict,
    amount: int | float,
    unit: str,
    expires_at: str,
    max_amount: int | float,
    provider_cost: dict | None = None,
) -> dict:
    """Seal the exact estimate and the operator's maximum authorized spend."""
    amount = _number(amount, "quote estimate.amount")
    max_amount = _number(max_amount, "quote spend_cap.amount")
    if not isinstance(unit, str) or not unit.strip():
        raise ValueError("quote estimate.unit ausente")
    unit = unit.strip()
    _aware_datetime(expires_at, "quote expires_at")
    if amount > max_amount:
        raise ValueError(
            f"quote estimate {amount} {unit} excede teto {max_amount} {unit}"
        )
    if provider_cost is None:
        raise ValueError("quote exige provider cost receipt")
    provider_amount = _find_numeric_cost(
        provider_cost,
        (
            ("credits", "estimated_credits", "amount")
            if unit == "credits"
            else ("amount", "estimated_cost", "cost")
        ),
    )
    if provider_amount is None:
        raise ValueError(f"provider cost não contém valor em {unit}")
    if provider_amount != amount:
        raise ValueError("quote estimate.amount diverge do provider estimate receipt")
    payload = {
        "version": 1,
        "provider": "higgsfield_cli",
        "model": model,
        "params_sha256": canonical_sha256(params),
        "estimate": {"amount": amount, "unit": unit},
        "expires_at": expires_at,
        "spend_cap": {"amount": max_amount, "unit": unit},
    }
    payload["provider_cost"] = provider_cost
    payload["provider_cost_sha256"] = canonical_sha256(provider_cost)
    return {**payload, "quote_sha256": canonical_sha256(payload)}


def _find_numeric_cost(value, keys: tuple[str, ...]):
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if (
                not isinstance(candidate, bool)
                and isinstance(candidate, (int, float))
            ):
                return candidate
        for nested in value.values():
            found = _find_numeric_cost(nested, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_numeric_cost(nested, keys)
            if found is not None:
                return found
    return None


def build_quote_from_estimate(
    *,
    model: str,
    params: dict,
    provider_cost: dict,
    unit: str,
    expires_at: str,
    max_amount: int | float,
) -> dict:
    keys = (
        ("credits", "estimated_credits", "amount")
        if unit == "credits"
        else ("amount", "estimated_cost", "cost")
    )
    amount = _find_numeric_cost(provider_cost, keys)
    if amount is None:
        raise ValueError(
            f"não foi possível extrair custo em {unit} da resposta do provider"
        )
    return build_quote_receipt(
        model=model,
        params=params,
        amount=amount,
        unit=unit,
        expires_at=expires_at,
        max_amount=max_amount,
        provider_cost=provider_cost,
    )


def validate_quote_receipt(
    quote: dict,
    *,
    model: str,
    params: dict,
    now: datetime | None = None,
    allow_expired: bool = False,
) -> dict:
    if not isinstance(quote, dict) or quote.get("version") != 1:
        raise ValueError("quote receipt inválido")
    supplied_digest = quote.get("quote_sha256")
    sealed = {key: value for key, value in quote.items() if key != "quote_sha256"}
    if not isinstance(supplied_digest, str) or supplied_digest != canonical_sha256(
        sealed
    ):
        raise ValueError("quote digest inválido ou adulterado")
    if quote.get("provider") != "higgsfield_cli":
        raise ValueError("quote provider inválido")
    if quote.get("model") != model:
        raise ValueError("quote model diverge da geração")
    if quote.get("params_sha256") != canonical_sha256(params):
        raise ValueError("quote params divergem da geração")
    provider_cost = quote.get("provider_cost")
    provider_cost_digest = quote.get("provider_cost_sha256")
    if provider_cost is None or provider_cost_digest is None:
        raise ValueError("quote provider cost receipt incompleto")
    if provider_cost_digest != canonical_sha256(provider_cost):
        raise ValueError("quote provider cost digest inválido")

    estimate = quote.get("estimate", {}) or {}
    cap = quote.get("spend_cap", {}) or {}
    amount = _number(estimate.get("amount"), "quote estimate.amount")
    max_amount = _number(cap.get("amount"), "quote spend_cap.amount")
    unit = estimate.get("unit")
    if not isinstance(unit, str) or not unit.strip():
        raise ValueError("quote estimate.unit ausente")
    if cap.get("unit") != unit:
        raise ValueError("quote estimate.unit diverge do spend_cap.unit")
    if amount > max_amount:
        raise ValueError("quote estimate excede teto autorizado")
    provider_amount = _find_numeric_cost(
        provider_cost,
        (
            ("credits", "estimated_credits", "amount")
            if unit == "credits"
            else ("amount", "estimated_cost", "cost")
        ),
    )
    if provider_amount is None or provider_amount != amount:
        raise ValueError("quote estimate.amount diverge do provider estimate receipt")

    expires_at = _aware_datetime(quote.get("expires_at"), "quote expires_at")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now precisa incluir timezone")
    if not allow_expired and expires_at <= current:
        raise ValueError("quote expirou; gere uma nova estimativa antes do create")
    return quote


def find_first(value, keys: set[str]):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and item:
                return item
        for item in value.values():
            found = find_first(item, keys)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_first(item, keys)
            if found:
                return found
    return None


def build_job_checkpoint(
    *, model: str, params: dict, quote: dict, job_id: str
) -> dict:
    payload = {
        "version": 1,
        "provider": "higgsfield_cli",
        "model": model,
        "params_sha256": canonical_sha256(params),
        "quote_sha256": quote["quote_sha256"],
        "job_id": str(job_id),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return {**payload, "checkpoint_sha256": canonical_sha256(payload)}


class CheckpointPersistenceError(RuntimeError):
    """Carries a non-secret sealed recovery payload after a provider job exists."""

    def __init__(self, *, checkpoint_path: Path, checkpoint: dict):
        self.job_id = str(checkpoint["job_id"])
        self.recovery = {
            "action": "persist_checkpoint_before_any_retry",
            "checkpoint_path": str(checkpoint_path),
            "checkpoint": checkpoint,
        }
        super().__init__(
            "checkpoint pós-create não pôde ser persistido; "
            f"recupere job_id {self.job_id} pelo payload estruturado antes de tentar novamente"
        )


def persist_created_job_checkpoint(
    *,
    checkpoint_path: Path,
    model: str,
    params: dict,
    quote: dict,
    job_id: str,
) -> dict:
    """Persist a created job or fail with enough sealed data for manual recovery."""
    checkpoint = build_job_checkpoint(
        model=model, params=params, quote=quote, job_id=job_id
    )
    try:
        write_json_atomic(checkpoint_path, checkpoint)
    except OSError as exc:
        raise CheckpointPersistenceError(
            checkpoint_path=checkpoint_path, checkpoint=checkpoint
        ) from exc
    return checkpoint


def validate_job_checkpoint(
    checkpoint: dict, *, model: str, params: dict, quote: dict
) -> str:
    if not isinstance(checkpoint, dict) or checkpoint.get("version") != 1:
        raise ValueError("checkpoint Higgsfield inválido")
    supplied_digest = checkpoint.get("checkpoint_sha256")
    sealed = {
        key: value
        for key, value in checkpoint.items()
        if key != "checkpoint_sha256"
    }
    if not isinstance(supplied_digest, str) or supplied_digest != canonical_sha256(
        sealed
    ):
        raise ValueError("checkpoint digest inválido ou adulterado")
    if checkpoint.get("provider") != "higgsfield_cli":
        raise ValueError("checkpoint provider inválido")
    if checkpoint.get("model") != model:
        raise ValueError("checkpoint model diverge da geração")
    if checkpoint.get("params_sha256") != canonical_sha256(params):
        raise ValueError("checkpoint params divergem da geração")
    if checkpoint.get("quote_sha256") != quote.get("quote_sha256"):
        raise ValueError("checkpoint quote diverge da geração")
    job_id = checkpoint.get("job_id")
    if not job_id:
        raise ValueError("checkpoint sem job_id")
    return str(job_id)


def probe_mp4(path: Path) -> dict:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=format_name,duration:stream=codec_type,codec_name",
        "-of",
        "json",
        str(path),
    ]
    try:
        completed = _run_process_group(
            command, timeout_seconds=FFPROBE_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"ffprobe excedeu timeout externo de {FFPROBE_TIMEOUT_SECONDS}s"
        ) from exc
    if completed.returncode != 0:
        raise RuntimeError(f"output Higgsfield não é vídeo legível: {completed.stderr.strip()}")
    probe = json.loads(completed.stdout)
    streams = probe.get("streams", []) or []
    if not any(stream.get("codec_type") == "video" for stream in streams):
        raise RuntimeError("output Higgsfield não contém stream de vídeo")
    return probe


def validate_download_url(url: str) -> urllib.parse.SplitResult:
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port or 443
    except (TypeError, ValueError) as exc:
        raise ValueError("URL de download inválida") from exc
    if parsed.scheme.lower() != "https":
        raise ValueError("download Higgsfield exige URL https")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL de download não pode conter credenciais")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL de download sem hostname")

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        # Hostnames are only parsed here. No DNS result is trusted for a later
        # connection; remote transfer is fail-closed below until transport pins it.
        if len(hostname) > 253 or "." not in hostname:
            raise ValueError("hostname de download inválido")
    else:
        if not literal.is_global:
            raise ValueError("download bloqueado para IP privado, local ou reservado")
    return parsed


def download_atomic(
    url: str,
    output: Path,
    *,
    max_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
) -> dict:
    validate_download_url(url)
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("tamanho máximo de download inválido")
    raise RuntimeError(
        "download remoto Higgsfield bloqueado: o transporte atual não fixa o IP "
        "validado ao socket TLS e portanto não elimina DNS rebinding/TOCTOU"
    )


def build_asset_receipt(
    *,
    model: str,
    job_id: str,
    prompt: str,
    schema: dict,
    output: Path,
    cost_estimate: dict,
    commercial_rights_basis: str | None = None,
    quote_sha256: str | None = None,
) -> dict:
    rights_confirmed = bool(commercial_rights_basis)
    receipt = {
        "version": 1,
        "provider": "higgsfield_cli",
        "model": model,
        "job_id": job_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "model_schema_sha256": canonical_sha256(schema),
        "output_path": str(output),
        "output_sha256": file_sha256(output),
        "cost_estimate": cost_estimate,
        "actual_cost": None,
        "cost_status": "estimate_only_unreconciled",
        "rights": {
            "status": (
                "operator_confirmed"
                if rights_confirmed
                else "pending_provider_terms"
            ),
            "commercial_ads": rights_confirmed,
            "basis": commercial_rights_basis,
            "promotion_note": (
                "Asset registry still requires an independent cleared rights entry."
            ),
        },
    }
    if quote_sha256:
        receipt["quote_sha256"] = quote_sha256
    return receipt


def generate(
    *,
    model: str,
    params: dict,
    output: Path,
    receipt_path: Path,
    checkpoint_path: Path,
    quote: dict,
    confirm_spend: bool,
    commercial_rights_basis: str | None = None,
) -> dict:
    # Explicitly consume arguments so future refactors cannot accidentally
    # re-enable a partial path by removing the fail-closed gate as "dead code".
    _ = (
        model,
        params,
        output,
        receipt_path,
        checkpoint_path,
        quote,
        confirm_spend,
        commercial_rights_basis,
    )
    raise RuntimeError(PAID_GENERATION_BLOCK_REASON)


def main() -> None:
    parser = argparse.ArgumentParser(description="creative-forge — Higgsfield video adapter")
    sub = parser.add_subparsers(dest="command", required=True)
    discover_parser = sub.add_parser("discover")
    discover_parser.add_argument("--model", required=True)
    cost_parser = sub.add_parser("cost")
    cost_parser.add_argument("--model", required=True)
    cost_parser.add_argument("--params", required=True, help="JSON file")
    cost_parser.add_argument("--max-cost", type=float)
    cost_parser.add_argument("--cost-unit", default="credits")
    cost_parser.add_argument("--quote-ttl-seconds", type=int, default=900)
    cost_parser.add_argument("--quote-out")
    generate_parser = sub.add_parser("generate")
    generate_parser.add_argument("--model", required=True)
    generate_parser.add_argument("--params", required=True, help="JSON file")
    generate_parser.add_argument("--out", required=True)
    generate_parser.add_argument("--receipt", required=True)
    generate_parser.add_argument("--quote", required=True)
    generate_parser.add_argument("--checkpoint", required=True)
    generate_parser.add_argument("--confirm-spend", action="store_true")
    generate_parser.add_argument("--commercial-rights-basis")
    args = parser.parse_args()
    try:
        if args.command == "discover":
            result = discover_model(args.model)
        else:
            params = json.loads(Path(args.params).read_text())
            if args.command == "cost":
                provider_cost = estimate_cost(args.model, params)
                if args.quote_out and args.max_cost is None:
                    raise ValueError("--quote-out exige --max-cost")
                if args.max_cost is None:
                    result = provider_cost
                else:
                    if args.quote_ttl_seconds <= 0:
                        raise ValueError("--quote-ttl-seconds precisa ser positivo")
                    expires_at = (
                        datetime.now(timezone.utc)
                        + timedelta(seconds=args.quote_ttl_seconds)
                    ).isoformat()
                    quote = build_quote_from_estimate(
                        model=args.model,
                        params=params,
                        provider_cost=provider_cost,
                        unit=args.cost_unit,
                        expires_at=expires_at,
                        max_amount=args.max_cost,
                    )
                    if args.quote_out:
                        write_json_atomic(Path(args.quote_out), quote)
                    result = {"estimate": provider_cost, "quote": quote}
            else:
                quote = json.loads(Path(args.quote).read_text())
                result = generate(
                    model=args.model,
                    params=params,
                    output=Path(args.out),
                    receipt_path=Path(args.receipt),
                    checkpoint_path=Path(args.checkpoint),
                    quote=quote,
                    confirm_spend=args.confirm_spend,
                    commercial_rights_basis=args.commercial_rights_basis,
                )
    except CheckpointPersistenceError as exc:
        print(
            json.dumps(
                {
                    "status": "blocked_checkpoint_recovery_required",
                    "error": str(exc),
                    "recovery": exc.recovery,
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2) from exc
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        sys.exit(f"creative-forge: Higgsfield video BLOCKED: {exc}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
