#!/usr/bin/env python3
"""Seal technical video evidence and agent-authored per-artifact playback QA."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts import paths as workspace_paths, video
except ImportError:
    import paths as workspace_paths
    import video

ROOT = Path(__file__).resolve().parent.parent

PLAYBACK_CHECKS = (
    "full_timeline",
    "muted_comprehension",
    "sound_intent_verified",
    "copy_correct",
    "visual_quality",
    "claims_truthful",
    "cultural_fit",
    "safe_zones",
    "swipe_fidelity",
)
SOUND_STRATEGIES = {
    "intentional_silence",
    "licensed_music",
    "voiceover",
    "music_and_voiceover",
}
SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,127})$")
COMMAND_TIMEOUT_SECONDS = 120


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_digest(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
    ).hexdigest()


def lexical_absolute(value: str | Path) -> Path:
    return Path(os.path.abspath(Path(value).expanduser()))


def safe_qa_dir(
    root: Path,
    app_slug: str,
    batch_id: str,
    locale: str,
    recipe_name: str,
) -> Path:
    lexical_root = lexical_absolute(root)
    if lexical_root.is_symlink():
        raise ValueError(f"workspace root de QA usa symlink proibido: {lexical_root}")
    segments = {
        "app": app_slug,
        "batch_id": batch_id,
        "locale": locale,
        "recipe": recipe_name,
    }
    for label, value in segments.items():
        if (
            not isinstance(value, str)
            or value in {".", ".."}
            or not SAFE_SEGMENT_RE.fullmatch(value)
        ):
            raise ValueError(f"{label} inválido para path de QA: {value!r}")
    base = lexical_absolute(lexical_root / "qa")
    candidate = lexical_absolute(
        base / app_slug / batch_id / locale / recipe_name
    )
    try:
        candidate.relative_to(base)
    except ValueError as exc:  # defense in depth beyond segment validation
        raise ValueError("path de QA escapou de qa/") from exc
    resolved_base = base.resolve(strict=False)
    resolved_candidate = candidate.resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_base)
    except ValueError as exc:
        raise ValueError("path de QA resolve fora de qa/ por symlink") from exc
    cursor = base
    if cursor.is_symlink():
        raise ValueError(f"path base de QA usa symlink proibido: {cursor}")
    for part in candidate.relative_to(base).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"path de QA usa symlink proibido: {cursor}")
    return candidate


def expected_video_path(
    root: Path,
    app_slug: str,
    recipe_name: str,
    locale: str,
    video_format: str,
) -> Path:
    # Reuse the same segment policy as QA paths before composing output names.
    safe_qa_dir(root, app_slug, "path-check", locale, recipe_name)
    if video_format not in video.FORMATS:
        raise ValueError(f"formato inválido para output de vídeo: {video_format}")
    candidate = lexical_absolute(
        Path(root)
        / "output"
        / app_slug
        / "video"
        / f"{recipe_name}--{locale}--{video_format}.mp4"
    )
    try:
        return video._safe_output_path(candidate, Path(root))
    except video.VideoError as exc:
        raise ValueError(str(exc)) from exc


def assert_expected_video_path(actual: Path, expected: Path) -> None:
    if lexical_absolute(actual) != lexical_absolute(expected):
        raise ValueError(
            f"vídeo não corresponde ao output canônico do render: "
            f"{lexical_absolute(actual)} != {lexical_absolute(expected)}"
        )


def seal_file(item: dict, *, kind: str) -> dict:
    role = item.get("role") if kind == "input" else "artifact"
    path_value = item.get("path")
    if not path_value:
        raise ValueError(f"{kind} {role} sem path")
    path = lexical_absolute(path_value)
    if path.is_symlink():
        raise ValueError(f"{kind} {role} usa symlink proibido: {path}")
    if not path.is_file():
        raise ValueError(f"{kind} {role} ausente: {path}")
    if kind == "input" and not item.get("role"):
        raise ValueError(f"input sem role: {path}")
    return {
        **item,
        "path": str(path),
        "resolved_path": str(path.resolve(strict=True)),
        "sha256": sha256(path),
    }


def _lock_payload(lock: dict) -> dict:
    return {key: value for key, value in lock.items() if key != "lock_digest"}


def artifact_key(app: str, batch_id: str, artifact: dict) -> str:
    return canonical_digest(
        {
            "app": app,
            "batch_id": batch_id,
            "path": artifact.get("path"),
            "sha256": artifact.get("sha256"),
            "market_id": artifact.get("market_id"),
            "locale": artifact.get("locale"),
            "format": artifact.get("format"),
            "brief_ref": artifact.get("brief_ref"),
            "concept_id": artifact.get("concept_id"),
            "variant_id": artifact.get("variant_id"),
        }
    )[:20]


def seal_run_lock(
    *,
    app: str,
    batch_id: str,
    artifacts: list,
    input_files: list,
    git_state: dict,
    tool_versions: dict,
) -> dict:
    if not app or not batch_id:
        raise ValueError("app e batch_id são obrigatórios")
    if not artifacts:
        raise ValueError("run lock sem artifacts")
    if not input_files:
        raise ValueError("run lock sem input_files")
    if not tool_versions:
        raise ValueError("run lock sem tool_versions")
    git_errors = verify_git_state(git_state)
    if git_errors:
        raise ValueError("git state inválido: " + "; ".join(git_errors))
    sealed_inputs = [seal_file(item, kind="input") for item in input_files]
    sealed_artifacts = []
    for artifact in artifacts:
        sealed = seal_file(artifact, kind="artifact")
        for field in (
            "market_id",
            "locale",
            "copy_language",
            "format",
            "duration_seconds",
            "audio_strategy",
            "technical_status",
            "brief_ref",
            "concept_id",
            "variant_id",
        ):
            if sealed.get(field) in (None, ""):
                raise ValueError(f"artifact sem {field}: {sealed['path']}")
        if sealed["audio_strategy"] not in SOUND_STRATEGIES:
            raise ValueError(f"audio_strategy inválida: {sealed['audio_strategy']}")
        if sealed["technical_status"] != "pass":
            raise ValueError(f"artifact sem QA técnico PASS: {sealed['path']}")
        sealed["artifact_key"] = artifact_key(app, batch_id, sealed)
        sealed_artifacts.append(sealed)
    input_digest = canonical_digest(
        sorted(
            sealed_inputs,
            key=lambda item: (str(item.get("role")), str(item.get("path"))),
        )
    )
    lock = {
        "version": 2,
        "app": app,
        "batch_id": batch_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_state["git_sha"],
        "git_state": deepcopy(git_state),
        "tool_versions": dict(tool_versions),
        "input_files": sealed_inputs,
        "input_digest": input_digest,
        "artifacts": sealed_artifacts,
    }
    lock["lock_digest"] = canonical_digest(lock)
    return lock


def capture_git_state(root: Path) -> dict:
    """Capture a clean repository state that can be revalidated at approval time."""
    lexical_root = lexical_absolute(root)
    if lexical_root.is_symlink():
        raise ValueError(f"git repository root usa symlink proibido: {lexical_root}")
    resolved_root = lexical_root.resolve(strict=True)
    top = run_command(["git", "rev-parse", "--show-toplevel"], cwd=lexical_root)
    head = run_command(["git", "rev-parse", "HEAD"], cwd=lexical_root)
    status = run_command(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=lexical_root,
    )
    if top.returncode != 0 or head.returncode != 0 or status.returncode != 0:
        raise ValueError("estado Git indisponível para run lock")
    repository_root = lexical_absolute((top.stdout or "").strip()).resolve(strict=True)
    if repository_root != resolved_root:
        raise ValueError(
            f"root informado não é a raiz Git: {resolved_root} != {repository_root}"
        )
    status_text = status.stdout or ""
    if status_text:
        raise ValueError("run lock exige worktree Git limpo")
    return {
        "repository_root": str(repository_root),
        "git_sha": (head.stdout or "").strip(),
        "worktree_clean": True,
        "worktree_status_sha256": hashlib.sha256(status_text.encode()).hexdigest(),
    }


def verify_git_state(state: dict) -> list:
    errors = []
    if not isinstance(state, dict):
        return ["git_state ausente ou malformado"]
    root_value = state.get("repository_root")
    git_sha = state.get("git_sha")
    status_digest = state.get("worktree_status_sha256")
    if not root_value or not git_sha or not status_digest:
        return ["git_state incompleto"]
    root = lexical_absolute(root_value)
    if root.is_symlink():
        return [f"git repository root virou symlink: {root}"]
    try:
        current = capture_git_state(root)
    except (OSError, ValueError) as exc:
        return [f"git state atual diverge: {exc}"]
    if state.get("worktree_clean") is not True:
        errors.append("git_state não declara worktree limpo")
    for field in (
        "repository_root",
        "git_sha",
        "worktree_clean",
        "worktree_status_sha256",
    ):
        if state.get(field) != current.get(field):
            errors.append(f"git_state atual diverge em {field}")
    return errors


def verify_sealed_file(item: dict, *, kind: str) -> list:
    errors = []
    role = item.get("role") if kind == "input" else "artifact"
    path = lexical_absolute(item.get("path", ""))
    if path.is_symlink():
        errors.append(f"{kind} {role} virou symlink: {path}")
    elif not path.is_file():
        errors.append(f"{kind} {role} desapareceu: {path}")
    else:
        resolved = str(path.resolve(strict=True))
        if resolved != item.get("resolved_path"):
            errors.append(f"{kind} {role} mudou destino resolvido: {path}")
        if sha256(path) != item.get("sha256"):
            errors.append(f"{kind} {role} checksum mudou: {path}")
    return errors


def verify_run_lock(lock: dict) -> list:
    errors = []
    if lock.get("version") != 2:
        errors.append("run lock version inválida")
    if lock.get("lock_digest") != canonical_digest(_lock_payload(lock)):
        errors.append("run lock digest não corresponde ao conteúdo")
    embedded_input_digest = canonical_digest(
        sorted(
            lock.get("input_files", []) or [],
            key=lambda item: (str(item.get("role")), str(item.get("path"))),
        )
    )
    if embedded_input_digest != lock.get("input_digest"):
        errors.append("run lock input digest não corresponde aos inputs")
    if lock.get("git_sha") != (lock.get("git_state") or {}).get("git_sha"):
        errors.append("run lock git_sha diverge de git_state")
    errors.extend(verify_git_state(lock.get("git_state")))
    for item in lock.get("input_files", []) or []:
        errors.extend(verify_sealed_file(item, kind="input"))
    seen_artifact_keys = set()
    for item in lock.get("artifacts", []) or []:
        current_key = item.get("artifact_key")
        if current_key in seen_artifact_keys:
            errors.append(f"run lock artifact_key duplicado: {current_key}")
        seen_artifact_keys.add(current_key)
        expected_key = artifact_key(lock.get("app"), lock.get("batch_id"), item)
        if current_key != expected_key:
            errors.append(f"run lock artifact_key inválido: {current_key}")
        errors.extend(verify_sealed_file(item, kind="artifact"))
    return errors


def report_identity_digest(report: dict) -> str:
    return canonical_digest(
        {
            "version": report.get("version"),
            "app": report.get("app"),
            "batch_id": report.get("batch_id"),
            "run_lock_digest": (report.get("run_lock") or {}).get("lock_digest"),
            "artifact_keys": [
                item.get("artifact_key") for item in report.get("records", []) or []
            ],
        }
    )


def build_playback_report(run_lock: dict) -> dict:
    errors = verify_run_lock(run_lock)
    if errors:
        raise ValueError("run lock inválido: " + "; ".join(errors))
    report = {
        "version": 1,
        "app": run_lock["app"],
        "batch_id": run_lock["batch_id"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_lock": deepcopy(run_lock),
        "status": "pending",
        "records": [
            {
                "artifact_key": artifact["artifact_key"],
                "path": artifact["path"],
                "sha256": artifact["sha256"],
                "market_id": artifact["market_id"],
                "locale": artifact["locale"],
                "copy_language": artifact["copy_language"],
                "format": artifact["format"],
                "audio_strategy": artifact["audio_strategy"],
                "brief_ref": artifact["brief_ref"],
                "concept_id": artifact["concept_id"],
                "variant_id": artifact["variant_id"],
                "status": "pending",
                "checks": {},
            }
            for artifact in run_lock["artifacts"]
        ],
    }
    report["report_identity_digest"] = report_identity_digest(report)
    return report


def approval_digest(record: dict, run_lock_digest: str) -> str:
    return canonical_digest(
        {
            "run_lock_digest": run_lock_digest,
            "artifact_key": record.get("artifact_key"),
            "path": record.get("path"),
            "sha256": record.get("sha256"),
            "market_id": record.get("market_id"),
            "locale": record.get("locale"),
            "copy_language": record.get("copy_language"),
            "format": record.get("format"),
            "audio_strategy": record.get("audio_strategy"),
            "brief_ref": record.get("brief_ref"),
            "concept_id": record.get("concept_id"),
            "variant_id": record.get("variant_id"),
            "status": record.get("status"),
            "checks": record.get("checks"),
            "reviewer": record.get("reviewer"),
            "reviewed_at": record.get("reviewed_at"),
            "notes": record.get("notes"),
        }
    )


def approve_artifact(
    report: dict,
    artifact_key: str,
    *,
    reviewer: str,
    checks: dict,
    notes: str,
) -> dict:
    verification = verify_playback_report(report, allow_pending=True)
    if verification:
        raise ValueError("playback report inválido: " + "; ".join(verification))
    if not reviewer or not str(notes).strip():
        raise ValueError("reviewer e notes são obrigatórios")
    missing = [name for name in PLAYBACK_CHECKS if checks.get(name) is not True]
    if missing:
        raise ValueError("playback checks pendentes: " + ", ".join(missing))
    approved = deepcopy(report)
    record = next(
        (item for item in approved["records"] if item["artifact_key"] == artifact_key),
        None,
    )
    if record is None:
        raise ValueError(f"artifact_key inexistente: {artifact_key}")
    record.update(
        {
            "status": "approved",
            "checks": {name: True for name in PLAYBACK_CHECKS},
            "reviewer": reviewer,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "notes": str(notes).strip(),
        }
    )
    record["approval_digest"] = approval_digest(
        record, approved["run_lock"]["lock_digest"]
    )
    approved["status"] = (
        "approved"
        if all(item.get("status") == "approved" for item in approved["records"])
        else "pending"
    )
    return approved


def verify_playback_report(report: dict, *, allow_pending: bool = False) -> list:
    errors = []
    if report.get("report_identity_digest") != report_identity_digest(report):
        errors.append("playback report identity digest não corresponde ao conteúdo")
    lock = report.get("run_lock", {}) or {}
    if report.get("app") != lock.get("app"):
        errors.append("playback report.app diverge do run lock")
    if report.get("batch_id") != lock.get("batch_id"):
        errors.append("playback report.batch_id diverge do run lock")
    errors.extend(verify_run_lock(lock))
    lock_artifacts = {
        item.get("artifact_key"): item for item in lock.get("artifacts", []) or []
    }
    record_keys = [item.get("artifact_key") for item in report.get("records", []) or []]
    if len(record_keys) != len(set(record_keys)):
        errors.append("playback report contém artifact_keys duplicados")
    if set(record_keys) != set(lock_artifacts):
        errors.append("playback records não cobrem exatamente os run lock artifacts")
    approved_count = 0
    for record in report.get("records", []) or []:
        locked = lock_artifacts.get(record.get("artifact_key"))
        if locked is not None:
            for field in (
                "path",
                "sha256",
                "market_id",
                "locale",
                "copy_language",
                "format",
                "audio_strategy",
                "brief_ref",
                "concept_id",
                "variant_id",
            ):
                if record.get(field) != locked.get(field):
                    errors.append(
                        f"artifact {record.get('artifact_key')} diverge do run lock artifact em {field}"
                    )
        if record.get("status") == "approved":
            approved_count += 1
            expected = approval_digest(record, lock.get("lock_digest"))
            if record.get("approval_digest") != expected:
                errors.append(
                    f"artifact {record.get('artifact_key')} approval digest inválido"
                )
            missing = [
                name for name in PLAYBACK_CHECKS if record.get("checks", {}).get(name) is not True
            ]
            if missing:
                errors.append(
                    f"artifact {record.get('artifact_key')} checks pendentes: {', '.join(missing)}"
                )
        elif not allow_pending:
            errors.append(f"artifact {record.get('artifact_key')} sem approval")
    expected_status = (
        "approved"
        if report.get("records") and approved_count == len(report["records"])
        else "pending"
    )
    if report.get("status") != expected_status:
        errors.append("playback report status inconsistente")
    return errors


def audit_sound_contract(
    audio_strategy: str,
    *,
    max_volume_db: float | None,
    captions_path: str | Path | None,
) -> dict:
    errors, warnings = [], []
    if audio_strategy not in SOUND_STRATEGIES:
        errors.append(f"audio_strategy inválida: {audio_strategy}")
        return {"errors": errors, "warnings": warnings}
    effectively_silent = max_volume_db is None or max_volume_db <= -60
    if audio_strategy == "intentional_silence":
        if not effectively_silent:
            errors.append("audio_strategy declara silêncio, mas áudio audível foi detectado")
        else:
            warnings.append("silêncio intencional confirmado tecnicamente")
    elif effectively_silent:
        errors.append(f"silêncio acidental detectado para audio_strategy {audio_strategy}")
    if audio_strategy in {"voiceover", "music_and_voiceover"}:
        if not captions_path:
            errors.append("voiceover exige captions por locale")
        else:
            path = lexical_absolute(captions_path)
            if not path.is_file() or not path.read_text().strip():
                errors.append(f"captions ausentes ou vazias: {path}")
    return {"errors": errors, "warnings": warnings}


def run_command(command: list[str], *, cwd: Path = ROOT) -> subprocess.CompletedProcess:
    try:
        return video._run_process_group(
            command,
            cwd=Path(cwd),
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(
            f"comando excedeu timeout de {COMMAND_TIMEOUT_SECONDS}s: {command[0]}"
        ) from exc


def command_version(command: list[str], *, cwd: Path = ROOT) -> str:
    completed = run_command(command, cwd=cwd)
    if completed.returncode != 0:
        raise ValueError(f"tool version falhou: {' '.join(command)}")
    output = (completed.stdout or completed.stderr or "").strip().splitlines()
    if not output:
        raise ValueError(f"tool version sem output: {' '.join(command)}")
    return output[0]


def remotion_version(remotion_bin: Path, remotion_root: Path) -> str:
    completed = run_command(
        [str(remotion_bin), "versions"], cwd=remotion_root
    )
    if completed.returncode != 0:
        raise ValueError("Remotion versions falhou")
    match = re.search(r"On version:\s*([^\s]+)", completed.stdout or "")
    if not match:
        raise ValueError("Remotion versions não retornou a versão")
    return match.group(1)


def analyze_max_volume(path: Path) -> tuple[float | None, list[str]]:
    completed = run_command(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ]
    )
    output = (completed.stderr or "") + "\n" + (completed.stdout or "")
    match = re.search(r"max_volume:\s*(-?(?:inf|\d+(?:\.\d+)?))\s*dB", output)
    if completed.returncode != 0 or not match:
        return None, ["ffmpeg volumedetect não retornou max_volume"]
    raw = match.group(1)
    return (-999.0 if raw == "-inf" else float(raw)), []


def analyze_black_segments(path: Path) -> tuple[list[dict], list[str]]:
    completed = run_command(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-i",
            str(path),
            "-vf",
            "blackdetect=d=0.2:pix_th=0.10",
            "-an",
            "-f",
            "null",
            "-",
        ]
    )
    if completed.returncode != 0:
        return [], ["ffmpeg blackdetect falhou"]
    segments = []
    for match in re.finditer(
        r"black_start:(?P<start>[\d.]+)\s+black_end:(?P<end>[\d.]+)\s+"
        r"black_duration:(?P<duration>[\d.]+)",
        completed.stderr or "",
    ):
        segments.append({key: float(value) for key, value in match.groupdict().items()})
    return segments, []


def derive_visual_evidence(
    video_path: Path,
    qa_dir: Path,
    *,
    duration_seconds: float,
) -> tuple[Path, Path]:
    qa_dir.mkdir(parents=True, exist_ok=True)
    poster = qa_dir / "poster.png"
    contact = qa_dir / "contact-1fps.jpg"
    rows = max(1, int((float(duration_seconds) + 4) // 5))
    commands = [
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            "0.5",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            str(poster),
        ],
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            f"fps=1,scale=216:-2,tile=5x{rows}:padding=4:margin=4",
            "-frames:v",
            "1",
            str(contact),
        ],
    ]
    for command in commands:
        completed = run_command(command)
        if completed.returncode != 0:
            raise ValueError(
                "FFmpeg não gerou evidência visual: "
                + (completed.stderr or "sem detalhe")[:500]
            )
    if not poster.is_file() or not contact.is_file():
        raise ValueError("evidência visual não foi criada")
    return poster, contact


def collect_run_inputs(
    *,
    root: Path,
    app_slug: str,
    app: dict,
    recipe: dict,
    recipe_path: Path,
    props_path: Path,
    poster_path: Path,
    contact_path: Path,
    locale: str,
    captions_path: Path | None = None,
    render_receipt_path: Path | None = None,
) -> list[dict]:
    candidates = [
        ("app_config", root / "apps" / f"{app_slug}.yaml"),
        ("recipe", recipe_path),
        (
            "brief",
            root / "briefs" / app_slug / f"{recipe.get('brief_ref')}.yaml",
        ),
        ("video_patterns", root / "swipe" / app_slug / "video-patterns.yaml"),
        ("research", root / "swipe" / app_slug / "competitors.yaml"),
        ("asset_registry", root / "assets" / app_slug / "registry.yaml"),
        (
            "video_template",
            root / "templates" / "video" / str(recipe.get("template")) / "meta.yaml",
        ),
        ("render_props", props_path),
        ("qa_poster", poster_path),
        ("qa_contact_sheet", contact_path),
        ("engine", root / "scripts" / "video.py"),
        ("engine", Path(__file__)),
        ("remotion_package", root / "remotion" / "package.json"),
        ("remotion_lock", root / "remotion" / "package-lock.json"),
    ]
    if captions_path is not None:
        candidates.append(("captions", captions_path))
    if render_receipt_path is not None:
        candidates.append(("render_receipt", render_receipt_path))
    candidates.extend(
        ("remotion_source", path)
        for path in sorted((root / "remotion" / "src").glob("*.ts*"))
    )
    for asset in (recipe.get("assets", {}) or {}).values():
        if isinstance(asset, dict) and asset.get("path"):
            candidates.append(("creative_asset", root / asset["path"]))
    audio = recipe.get("audio")
    if isinstance(audio, dict) and audio.get("path"):
        candidates.append(("audio_asset", root / audio["path"]))
    _, localized = video.localized_for_market(recipe, app, locale)
    voiceover = localized.get("voiceover")
    if isinstance(voiceover, dict) and voiceover.get("path"):
        candidates.append(("voiceover_asset", root / voiceover["path"]))
    registry_path = root / "assets" / app_slug / "registry.yaml"
    if registry_path.is_file():
        registry = video.load_yaml(registry_path)
        entries = registry.get("assets", []) if isinstance(registry, dict) else []
        by_id = {
            entry.get("id"): entry
            for entry in entries or []
            if isinstance(entry, dict) and entry.get("id")
        }
        for ref in recipe.get("asset_refs", []) or []:
            entry = by_id.get(ref) or {}
            rights = entry.get("rights", {}) or {}
            if isinstance(rights, dict):
                evidence = rights.get("evidence", {}) or {}
                if isinstance(evidence, dict) and evidence.get("path"):
                    candidates.append(
                        (
                            "rights_evidence",
                            workspace_paths.resolve_config_path(root, evidence["path"]),
                        )
                    )
            release = entry.get("consent_release", {}) or {}
            if isinstance(release, dict):
                release_evidence = release.get("evidence", {}) or {}
                release_path = (
                    release_evidence.get("path")
                    if isinstance(release_evidence, dict)
                    else None
                ) or release.get("path")
                if release_path:
                    candidates.append(
                        (
                            "consent_release_evidence",
                            workspace_paths.resolve_config_path(root, release_path),
                        )
                    )
    for claim in recipe.get("claims_used", []) or []:
        evidence = ((app.get("claims", {}) or {}).get(claim) or {}).get("evidence", {})
        if evidence.get("path"):
            candidates.append(
                (
                    "claim_evidence",
                    workspace_paths.resolve_config_path(root, evidence["path"]),
                )
            )
    inputs, seen = [], set()
    for role, path in candidates:
        key = (role, str(lexical_absolute(path)))
        if key in seen:
            continue
        seen.add(key)
        inputs.append({"role": role, "path": str(path)})
    return inputs


def prepare(
    *,
    app_slug: str,
    recipe_name: str,
    locale: str,
    video_path: Path,
    batch_id: str,
    root: Path = ROOT,
) -> Path:
    root = Path(root).resolve()
    app = video.load_yaml(root / "apps" / f"{app_slug}.yaml")
    recipe_path = root / "recipes" / app_slug / "video" / f"{recipe_name}.yaml"
    recipe = video.load_yaml(recipe_path)
    recipe_audit = video.audit_recipe(
        recipe, app, root=root, expected_app=app_slug
    )
    if recipe_audit["errors"]:
        raise ValueError("recipe de vídeo inválida: " + "; ".join(recipe_audit["errors"]))
    selected = video.select_locales(recipe, app, locale=locale)
    locale = selected[0]
    canonical_video = expected_video_path(
        root, app_slug, recipe_name, locale, recipe["format"]
    )
    assert_expected_video_path(video_path, canonical_video)
    video_path = canonical_video
    props = video.build_props(recipe, app, locale, root=root)
    render_receipt_path = video_path.with_suffix(".render.json")
    if not render_receipt_path.is_file():
        raise ValueError(f"render receipt ausente: {render_receipt_path}")
    try:
        render_receipt = json.loads(render_receipt_path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"render receipt JSON inválido: {exc}") from exc
    render_errors = video.render_receipt_errors(
        render_receipt,
        recipe=recipe,
        app=app,
        locale=locale,
        output_path=video_path,
        props=props,
        root=root,
    )
    if render_errors:
        raise ValueError("render receipt inválido: " + "; ".join(render_errors))
    technical = video.audit_video(
        video_path,
        expected_format=recipe["format"],
        expected_duration_seconds=float(recipe["duration_seconds"]),
    )
    errors = list(technical["errors"])
    max_volume_db, volume_errors = analyze_max_volume(video_path)
    errors.extend(volume_errors)
    market, localized = video.localized_for_market(recipe, app, locale)
    captions_path = localized.get("captions_path")
    if captions_path:
        captions_path = str(root / captions_path)
    sound = audit_sound_contract(
        recipe["audio_strategy"],
        max_volume_db=max_volume_db,
        captions_path=captions_path,
    )
    errors.extend(sound["errors"])
    black_segments, black_errors = analyze_black_segments(video_path)
    errors.extend(black_errors)
    long_black = [segment for segment in black_segments if segment["duration"] > 0.5]
    if long_black:
        errors.append(f"segmento preto > 0.5s detectado: {long_black}")
    if errors:
        raise ValueError("QA técnico de vídeo bloqueado: " + "; ".join(errors))

    qa_dir = safe_qa_dir(root, app_slug, batch_id, locale, recipe_name)
    qa_dir.mkdir(parents=True, exist_ok=True)
    props_path = qa_dir / "props.json"
    props_path.write_text(json.dumps(props, ensure_ascii=False, indent=2) + "\n")
    poster, contact = derive_visual_evidence(
        video_path,
        qa_dir,
        duration_seconds=float(recipe["duration_seconds"]),
    )
    inputs = collect_run_inputs(
        root=root,
        app_slug=app_slug,
        app=app,
        recipe=recipe,
        recipe_path=recipe_path,
        props_path=props_path,
        poster_path=poster,
        contact_path=contact,
        locale=locale,
        captions_path=Path(captions_path) if captions_path else None,
        render_receipt_path=render_receipt_path,
    )
    git_state = capture_git_state(root)
    remotion_bin = root / "remotion" / "node_modules" / ".bin" / "remotion"
    tool_versions = {
        "python": sys.version.split()[0],
        "remotion": remotion_version(remotion_bin, root / "remotion"),
        "ffmpeg": command_version(["ffmpeg", "-version"]),
        "ffprobe": command_version(["ffprobe", "-version"]),
    }
    artifact = {
        "path": str(video_path),
        "market_id": market["id"],
        "locale": locale,
        "copy_language": localized["copy_language"],
        "format": recipe["format"],
        "duration_seconds": float(recipe["duration_seconds"]),
        "audio_strategy": recipe["audio_strategy"],
        "technical_status": "pass",
        "brief_ref": recipe["brief_ref"],
        "concept_id": recipe["concept_id"],
        "variant_id": recipe["variant_id"],
        "technical_warnings": [
            *technical["warnings"],
            *sound["warnings"],
        ],
        "max_volume_db": max_volume_db,
        "black_segments": black_segments,
        "poster_path": str(poster),
        "contact_sheet_path": str(contact),
        "captions_path": captions_path,
    }
    lock = seal_run_lock(
        app=app_slug,
        batch_id=batch_id,
        artifacts=[artifact],
        input_files=inputs,
        git_state=git_state,
        tool_versions=tool_versions,
    )
    lock_path = qa_dir / "run.lock.json"
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n")
    report = build_playback_report(lock)
    report["run_lock_path"] = str(lock_path)
    report_path = qa_dir / "playback-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description="creative-forge — video playback receipts")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--app", required=True)
    prepare_parser.add_argument("--recipe", required=True)
    prepare_parser.add_argument("--locale", required=True)
    prepare_parser.add_argument("--video", required=True)
    prepare_parser.add_argument("--batch-id", required=True)
    status = sub.add_parser("status")
    status.add_argument("--report", required=True)
    approve = sub.add_parser("approve")
    approve.add_argument("--report", required=True)
    approve.add_argument("--artifact-key", required=True)
    approve.add_argument("--reviewer", required=True)
    approve.add_argument("--notes", required=True)
    approve.add_argument("--confirm-all", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        try:
            report_path = prepare(
                app_slug=args.app,
                recipe_name=args.recipe,
                locale=args.locale,
                video_path=Path(args.video).resolve(),
                batch_id=args.batch_id,
            )
        except (OSError, ValueError, video.VideoError) as exc:
            sys.exit(f"creative-forge: video QA BLOCKED: {exc}")
        print(report_path)
        return
    report_path = Path(args.report)
    report = json.loads(report_path.read_text())
    if args.command == "approve":
        checks = {name: args.confirm_all for name in PLAYBACK_CHECKS}
        try:
            report = approve_artifact(
                report,
                args.artifact_key,
                reviewer=args.reviewer,
                checks=checks,
                notes=args.notes,
            )
        except ValueError as exc:
            sys.exit(f"creative-forge: video approval BLOCKED: {exc}")
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    errors = verify_playback_report(report, allow_pending=args.command == "status")
    print(f"video_playback={report.get('status')} files={'valid' if not errors else 'changed'}")
    for error in errors:
        print(f"  ❌ {error}")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
