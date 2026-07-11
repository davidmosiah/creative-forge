#!/usr/bin/env python3
"""Stage QA-approved creatives onto a public static site and verify the URLs.

The Meta Ads MCP has no image-upload tool: `ads_create_creative` needs a public
`image_url`. This script closes the last manual step between visual QA and
`prepare-publish`:

  stage   copy approved image artifacts into the app site's public ads folder
          under an immutable content-hash filename and write a hosting manifest
          binding sha256 -> URL to the approved QA matrix digest.
  verify  fetch every hosted URL live and require the response bytes to hash to
          the exact approved sha256; write a hosting verification receipt.

Staging never deploys. Deploying the site (git push / vercel) stays a separate
explicit step, and `verify` only passes against the live deployed URLs.
"""

import argparse
import hashlib
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

try:
    from scripts import qa
except ImportError:  # direct execution: python3 scripts/host_assets.py
    import qa

ROOT = Path(__file__).resolve().parent.parent
HOSTING_SCHEMA = "creative-forge/asset-hosting@1"
HOSTING_VERIFICATION_SCHEMA = "creative-forge/asset-hosting-verification@1"
FETCH_TIMEOUT_SECONDS = 30
MAX_REMOTE_BYTES = 32 * 1024 * 1024


class HostingBlocked(Exception):
    pass


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_hosting_config(app_config: dict) -> dict:
    hosting = ((app_config.get("publish") or {}).get("asset_hosting")) or {}
    if hosting.get("method") != "static_site_dir":
        raise HostingBlocked(
            "publish.asset_hosting.method precisa ser 'static_site_dir' no app config"
        )
    for field in ("site_dir", "public_subdir", "base_url"):
        value = hosting.get(field)
        if not isinstance(value, str) or not value.strip():
            raise HostingBlocked(f"publish.asset_hosting.{field} ausente no app config")
    # base_url is the deployed site ROOT; hosted URLs are base_url + hosted_relpath.
    base_url = hosting["base_url"].rstrip("/")
    if not base_url.startswith("https://"):
        raise HostingBlocked("publish.asset_hosting.base_url precisa ser https://")
    subdir = hosting["public_subdir"].strip("/")
    if not subdir or ".." in subdir.split("/"):
        raise HostingBlocked("publish.asset_hosting.public_subdir inválido")
    if base_url.endswith("/" + subdir):
        raise HostingBlocked(
            "publish.asset_hosting.base_url deve ser a raiz do site, sem o public_subdir"
        )
    return {
        "site_dir": hosting["site_dir"],
        "public_subdir": subdir,
        "base_url": base_url,
    }


def resolve_site_dir(site_dir: str, root: Path) -> Path:
    path = Path(site_dir)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not path.is_dir():
        raise HostingBlocked(f"site_dir não existe ou não é diretório: {path}")
    return path


def require_approved_report(report: dict, expected_app: str) -> None:
    if report.get("app") != expected_app:
        raise HostingBlocked(
            f"QA report é do app '{report.get('app')}', esperado '{expected_app}'"
        )
    if report.get("automated_status") != "pass":
        raise HostingBlocked("QA automático não passou")
    if report.get("visual_status") != "approved":
        raise HostingBlocked("revisão visual ainda não foi aprovada")
    file_errors = qa.verify_report_files(report)
    if file_errors:
        raise HostingBlocked("artefatos mudaram após QA: " + "; ".join(file_errors))


def stage(report: dict, hosting: dict, app: str, root: Path = ROOT) -> dict:
    require_approved_report(report, app)
    site_dir = resolve_site_dir(hosting["site_dir"], root)
    batch_id = report.get("batch_id")
    if not isinstance(batch_id, str) or not batch_id.strip() or "/" in batch_id:
        raise HostingBlocked(f"batch_id inválido no QA report: {batch_id!r}")

    target_root = site_dir / hosting["public_subdir"] / app / batch_id
    target_root_resolved = target_root.resolve()
    if not target_root_resolved.is_relative_to(site_dir):
        raise HostingBlocked("destino de hospedagem escapa do site_dir")

    items = []
    skipped_videos = 0
    for record in report.get("records", []) or []:
        if record.get("media_kind") != "image":
            skipped_videos += 1
            continue
        artifact = Path(record["path"])
        digest = record["sha256"]
        suffix = artifact.suffix.lower()
        if suffix != ".png":
            raise HostingBlocked(f"formato de artefato não suportado: {artifact.name}")
        hosted_name = f"{digest[:16]}{suffix}"
        destination = target_root_resolved / hosted_name
        if destination.exists():
            if qa.sha256(destination) != digest:
                raise HostingBlocked(
                    f"conflito imutável: {destination} existe com bytes diferentes"
                )
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            tmp = destination.with_name(destination.name + ".tmp")
            tmp.write_bytes(artifact.read_bytes())
            if qa.sha256(tmp) != digest:
                tmp.unlink(missing_ok=True)
                raise HostingBlocked(f"checksum divergente ao copiar {artifact}")
            tmp.replace(destination)
        relpath = f"{hosting['public_subdir']}/{app}/{batch_id}/{hosted_name}"
        items.append(
            {
                "recipe": record.get("recipe"),
                "format": record.get("format"),
                "market_id": record.get("market_id"),
                "locale": record.get("locale"),
                "artifact_path": str(artifact),
                "sha256": digest,
                "hosted_relpath": relpath,
                "url": f"{hosting['base_url']}/{relpath}",
            }
        )
    if not items:
        raise HostingBlocked("nenhum artefato de imagem aprovado para hospedar")

    return {
        "schema": HOSTING_SCHEMA,
        "app": app,
        "batch_id": batch_id,
        "staged_at": utcnow_iso(),
        "site_dir": str(site_dir),
        "base_url": hosting["base_url"],
        "qa_matrix_digest": report.get("matrix_digest"),
        "qa_approved_matrix_digest": report.get("approved_matrix_digest"),
        "skipped_non_image_records": skipped_videos,
        "items": items,
        "deployed": False,
        "note": "staging local; deploy do site e 'verify' live continuam obrigatórios",
    }


def default_fetcher(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "creative-forge-host-verify"})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
        if getattr(response, "status", 200) != 200:
            raise HostingBlocked(f"URL {url} respondeu status {response.status}")
        body = response.read(MAX_REMOTE_BYTES + 1)
        if len(body) > MAX_REMOTE_BYTES:
            raise HostingBlocked(f"URL {url} excede o limite de {MAX_REMOTE_BYTES} bytes")
        return body


def verify(manifest: dict, fetcher=default_fetcher) -> dict:
    if manifest.get("schema") != HOSTING_SCHEMA:
        raise HostingBlocked(f"hosting manifest não usa schema {HOSTING_SCHEMA}")
    items = manifest.get("items") or []
    if not items:
        raise HostingBlocked("hosting manifest sem items")
    results = []
    for item in items:
        url = item.get("url")
        expected = item.get("sha256")
        if not url or not expected:
            raise HostingBlocked(f"item de hosting incompleto: {item}")
        body = fetcher(url)
        actual = hashlib.sha256(body).hexdigest()
        if actual != expected:
            raise HostingBlocked(
                f"conteúdo live de {url} tem sha256 {actual}, esperado {expected}"
            )
        results.append({"url": url, "sha256": expected, "bytes": len(body)})
    return {
        "schema": HOSTING_VERIFICATION_SCHEMA,
        "app": manifest.get("app"),
        "batch_id": manifest.get("batch_id"),
        "verified_at": utcnow_iso(),
        "qa_matrix_digest": manifest.get("qa_matrix_digest"),
        "items": results,
    }


def load_app_config(app: str, root: Path = ROOT) -> dict:
    config_path = root / "apps" / f"{app}.yaml"
    if not config_path.is_file():
        raise HostingBlocked(f"app config ausente: {config_path}")
    return yaml.safe_load(config_path.read_text()) or {}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    stage_parser = sub.add_parser("stage", help="copiar aprovados para o site estático")
    stage_parser.add_argument("--app", required=True)
    stage_parser.add_argument("--qa-report", required=True)
    stage_parser.add_argument("--out", required=True)

    verify_parser = sub.add_parser("verify", help="verificar URLs live contra sha256")
    verify_parser.add_argument("--hosting", required=True)
    verify_parser.add_argument("--out", required=True)

    args = parser.parse_args()
    try:
        if args.command == "stage":
            report = json.loads(Path(args.qa_report).read_text())
            hosting = load_hosting_config(load_app_config(args.app))
            manifest = stage(report, hosting, args.app)
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
            print(f"staged {len(manifest['items'])} artefato(s) em {manifest['site_dir']}")
            print(f"hosting manifest: {out}")
            print(
                "NEXT: deploy do site (git push / vercel) e depois "
                f"`python3 scripts/host_assets.py verify --hosting {out} "
                f"--out {out.with_name('hosting-verified.json')}`"
            )
        else:
            manifest = json.loads(Path(args.hosting).read_text())
            receipt = verify(manifest)
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
            print(f"verified {len(receipt['items'])} URL(s) live")
            print(f"hosting verification receipt: {out}")
    except HostingBlocked as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
