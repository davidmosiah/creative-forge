#!/usr/bin/env python3
"""Generate a static, self-contained HTML dashboard of the creative pipeline.

The dashboard is an evidence viewer, not a control plane: it renders what the
sealed artifacts (QA reports, playback receipts, hosting manifests, publish
manifests/receipts, experiment results) already prove, and nothing else. It
never mutates state, never calls a provider and never invents status. Refresh
by regenerating; open the file directly in a browser.

  python3 scripts/dashboard.py --app sunrise-demo --open
"""

import argparse
import html
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

try:
    from scripts.paths import default_root
except ImportError:
    from paths import default_root

ROOT = default_root()

STAGE_ORDER = (
    "research",
    "signals",
    "briefs",
    "build + QA",
    "hosting",
    "publish PAUSED",
    "learning loop",
)


def esc(value) -> str:
    return html.escape(str(value if value is not None else "—"), quote=True)


def load_yaml(path: Path):
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}


def load_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def collect(app: str, root: Path) -> dict:
    data = {"app": app, "root": root}
    data["app_config"] = load_yaml(root / "apps" / f"{app}.yaml")

    competitors_path = root / "swipe" / app / "competitors.yaml"
    competitors = load_yaml(competitors_path)
    data["research"] = {
        "path": competitors_path if competitors_path.is_file() else None,
        "creatives": len(competitors.get("creatives") or []),
        "expires_at": competitors.get("expires_at"),
    }

    signals_path = root / "signals" / f"{app}.yaml"
    signals = load_yaml(signals_path)
    data["signals"] = {
        "path": signals_path if signals_path.is_file() else None,
        "generated_at": signals.get("generated_at")
        or (signals.get("meta") or {}).get("generated_at"),
    }

    data["briefs"] = sorted((root / "briefs" / app).glob("*.yaml"))

    batches = []
    for report_path in sorted((root / "qa" / app).glob("*/report.json")):
        report = load_json(report_path)
        records = report.get("records") or []
        sheets = [
            report_path.parent / name
            for name in sorted(
                p.name for p in report_path.parent.glob("contact-*.png")
            )
        ]
        batches.append(
            {
                "batch_id": report.get("batch_id") or report_path.parent.name,
                "report_path": report_path,
                "automated": report.get("automated_status"),
                "visual": report.get("visual_status"),
                "reviewer": report.get("visual_reviewer"),
                "reviewed_at": report.get("visual_reviewed_at"),
                "matrix_digest": (report.get("approved_matrix_digest") or "")[:12],
                "n_records": len(records),
                "recipes": sorted({r.get("recipe") for r in records if r.get("recipe")}),
                "markets": sorted(
                    {r.get("market_id") for r in records if r.get("market_id")}
                ),
                "contact_sheets": sheets,
            }
        )
    data["batches"] = batches

    videos = []
    for playback in sorted((root / "qa" / app).glob("*/*/*/playback-report.json")):
        report = load_json(playback)
        artifacts = report.get("artifacts") or report.get("records") or []
        mp4s = sorted(playback.parent.glob("*.mp4"))
        videos.append(
            {
                "path": playback,
                "batch_id": playback.parent.parent.parent.name,
                "locale": playback.parent.parent.name,
                "recipe": playback.parent.name,
                "status": report.get("visual_status") or report.get("status"),
                "n_artifacts": len(artifacts) if isinstance(artifacts, list) else "—",
                "mp4s": mp4s,
            }
        )
    data["videos"] = videos

    hostings = []
    for hosting_path in sorted((root / "runs" / app).glob("*/hosting.json")):
        manifest = load_json(hosting_path)
        verified = hosting_path.with_name("hosting-verified.json")
        hostings.append(
            {
                "path": hosting_path,
                "batch_id": manifest.get("batch_id"),
                "n_items": len(manifest.get("items") or []),
                "base_url": manifest.get("base_url"),
                "verified": verified.is_file(),
            }
        )
    data["hostings"] = hostings

    publishes = []
    for manifest_path in sorted((root / "publish" / app).glob("*/manifest.json")):
        manifest = load_json(manifest_path)
        receipts = sorted(manifest_path.parent.glob("*receipt*.json"))
        publishes.append(
            {
                "path": manifest_path,
                "batch_id": manifest_path.parent.name,
                "n_items": len(manifest.get("items") or []),
                "receipts": receipts,
            }
        )
    data["publishes"] = publishes

    experiments_dir = root / "experiments"
    data["experiments"] = sorted(
        p
        for p in experiments_dir.rglob("*")
        if p.is_file() and app in str(p.relative_to(experiments_dir))
    )
    return data


def stage_status(data: dict) -> dict:
    research_ok = bool(data["research"]["path"] and data["research"]["creatives"])
    approved = [b for b in data["batches"] if b["visual"] == "approved"]
    return {
        "research": ("done" if research_ok else "todo", f"{data['research']['creatives']} creatives"),
        "signals": (
            "done" if data["signals"]["path"] else "todo",
            data["signals"]["generated_at"] or "sem snapshot",
        ),
        "briefs": ("done" if data["briefs"] else "todo", f"{len(data['briefs'])} brief(s)"),
        "build + QA": (
            "done" if approved else ("doing" if data["batches"] else "todo"),
            f"{len(approved)}/{len(data['batches'])} batch(es) aprovados",
        ),
        "hosting": (
            "done"
            if any(h["verified"] for h in data["hostings"])
            else ("doing" if data["hostings"] else "todo"),
            f"{len(data['hostings'])} manifest(s)",
        ),
        "publish PAUSED": (
            "done" if any(p["receipts"] for p in data["publishes"]) else "todo",
            f"{len(data['publishes'])} manifest(s)",
        ),
        "learning loop": (
            "done" if data["experiments"] else "todo",
            f"{len(data['experiments'])} resultado(s)",
        ),
    }


def rel(path: Path, out_dir: Path) -> str:
    return esc(os.path.relpath(path, out_dir))


def render(data: dict, out_dir: Path) -> str:
    app = data["app"]
    app_name = (data["app_config"].get("name") or app) if data["app_config"] else app
    stages = stage_status(data)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    parts = []
    parts.append(
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>"
        f"<title>creative-forge — {esc(app_name)}</title>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<style>"
        ":root{color-scheme:dark}"
        "body{background:#0d1117;color:#e6edf3;font:14px/1.5 -apple-system,'SF Pro Text',sans-serif;"
        "margin:0;padding:32px;max-width:1200px;margin-inline:auto}"
        "h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:32px 0 12px;color:#79c0ff}"
        ".muted{color:#8b949e;font-size:12px}"
        ".stages{display:flex;flex-wrap:wrap;gap:8px;margin:20px 0}"
        ".stage{border:1px solid #30363d;border-radius:8px;padding:8px 14px;background:#161b22}"
        ".stage b{display:block;font-size:12px}"
        ".stage span{font-size:11px;color:#8b949e}"
        ".done{border-color:#238636}.done b{color:#3fb950}"
        ".doing{border-color:#9e6a03}.doing b{color:#d29922}"
        ".todo b{color:#8b949e}"
        "table{border-collapse:collapse;width:100%;font-size:13px}"
        "td,th{border:1px solid #30363d;padding:6px 10px;text-align:left;vertical-align:top}"
        "th{background:#161b22}"
        ".ok{color:#3fb950}.pend{color:#d29922}"
        ".sheets{display:flex;flex-wrap:wrap;gap:12px;margin:10px 0}"
        ".sheets figure{margin:0;max-width:560px}"
        ".sheets img,video{max-width:100%;border:1px solid #30363d;border-radius:6px}"
        "figcaption{font-size:11px;color:#8b949e;margin-top:4px}"
        "code{background:#161b22;padding:1px 5px;border-radius:4px;font-size:12px}"
        "</style></head><body>"
    )
    parts.append(f"<h1>creative-forge · {esc(app_name)}</h1>")
    parts.append(
        f"<div class='muted'>evidence viewer gerado em {generated} — "
        "refletindo apenas artefatos selados; nada aqui ativa, gasta ou publica</div>"
    )

    parts.append("<div class='stages'>")
    for name in STAGE_ORDER:
        status, detail = stages[name]
        parts.append(
            f"<div class='stage {status}'><b>{esc(name)}</b><span>{esc(detail)}</span></div>"
        )
    parts.append("</div>")

    parts.append("<h2>Batches de imagem</h2>")
    if data["batches"]:
        parts.append(
            "<table><tr><th>batch</th><th>QA auto</th><th>QA visual</th>"
            "<th>reviewer</th><th>digest</th><th>artefatos</th><th>recipes</th><th>mercados</th></tr>"
        )
        for batch in data["batches"]:
            visual_class = "ok" if batch["visual"] == "approved" else "pend"
            parts.append(
                f"<tr><td>{esc(batch['batch_id'])}</td>"
                f"<td class='{'ok' if batch['automated'] == 'pass' else 'pend'}'>{esc(batch['automated'])}</td>"
                f"<td class='{visual_class}'>{esc(batch['visual'])}</td>"
                f"<td>{esc(batch['reviewer'])}</td><td><code>{esc(batch['matrix_digest'])}</code></td>"
                f"<td>{esc(batch['n_records'])}</td>"
                f"<td>{esc(', '.join(batch['recipes']))}</td>"
                f"<td>{esc(', '.join(batch['markets']))}</td></tr>"
            )
        parts.append("</table>")
        for batch in data["batches"]:
            if not batch["contact_sheets"]:
                continue
            parts.append(f"<h2>Contact sheets · {esc(batch['batch_id'])}</h2>")
            parts.append("<div class='sheets'>")
            for sheet in batch["contact_sheets"]:
                parts.append(
                    f"<figure><a href='{rel(sheet, out_dir)}'>"
                    f"<img loading='lazy' src='{rel(sheet, out_dir)}' alt='{esc(sheet.name)}'></a>"
                    f"<figcaption>{esc(sheet.name)}</figcaption></figure>"
                )
            parts.append("</div>")
    else:
        parts.append("<p class='muted'>nenhum batch ainda — rode forge.py build</p>")

    parts.append("<h2>Vídeos (playback QA)</h2>")
    if data["videos"]:
        parts.append("<div class='sheets'>")
        for video in data["videos"]:
            for mp4 in video["mp4s"]:
                parts.append(
                    f"<figure><video controls preload='metadata' src='{rel(mp4, out_dir)}'></video>"
                    f"<figcaption>{esc(video['recipe'])} · {esc(video['locale'])} · "
                    f"{esc(video['batch_id'])} · QA: {esc(video['status'])}</figcaption></figure>"
                )
            if not video["mp4s"]:
                parts.append(
                    f"<figure><figcaption>{esc(video['recipe'])} · {esc(video['locale'])} — "
                    f"receipt sem MP4 local (output limpo)</figcaption></figure>"
                )
        parts.append("</div>")
    else:
        parts.append("<p class='muted'>nenhum playback QA de vídeo ainda</p>")

    parts.append("<h2>Hospedagem pública</h2>")
    if data["hostings"]:
        parts.append(
            "<table><tr><th>batch</th><th>itens</th><th>base URL</th><th>verificação live</th></tr>"
        )
        for hosting in data["hostings"]:
            verified = (
                "<span class='ok'>verificada</span>"
                if hosting["verified"]
                else "<span class='pend'>pendente (deploy + verify)</span>"
            )
            parts.append(
                f"<tr><td>{esc(hosting['batch_id'])}</td><td>{esc(hosting['n_items'])}</td>"
                f"<td>{esc(hosting['base_url'])}</td><td>{verified}</td></tr>"
            )
        parts.append("</table>")
    else:
        parts.append("<p class='muted'>nenhum hosting manifest — rode host_assets.py stage</p>")

    parts.append("<h2>Publish (sempre PAUSED)</h2>")
    if data["publishes"]:
        parts.append("<table><tr><th>batch</th><th>itens</th><th>receipts</th></tr>")
        for publish in data["publishes"]:
            parts.append(
                f"<tr><td>{esc(publish['batch_id'])}</td><td>{esc(publish['n_items'])}</td>"
                f"<td>{esc(len(publish['receipts']))}</td></tr>"
            )
        parts.append("</table>")
    else:
        parts.append("<p class='muted'>nenhum manifest de publish ainda</p>")

    parts.append("<h2>Learning loop</h2>")
    if data["experiments"]:
        parts.append("<ul>")
        for result in data["experiments"]:
            parts.append(f"<li><code>{rel(result, out_dir)}</code></li>")
        parts.append("</ul>")
    else:
        parts.append(
            "<p class='muted'>nenhum resultado registrado — o ciclo pago ainda não rodou</p>"
        )

    parts.append("</body></html>")
    return "".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True)
    parser.add_argument("--out")
    parser.add_argument("--open", action="store_true", help="abrir no browser (macOS)")
    args = parser.parse_args()

    out = Path(args.out) if args.out else ROOT / "output" / args.app / "dashboard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    data = collect(args.app, ROOT)
    out.write_text(render(data, out.parent), encoding="utf-8")
    print(f"dashboard: {out}")
    if args.open:
        subprocess.run(["open", str(out)], check=False)


if __name__ == "__main__":
    main()
