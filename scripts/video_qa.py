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

from PIL import Image

try:
    from scripts import briefs, paths as workspace_paths, research, video
except ImportError:
    import briefs
    import paths as workspace_paths
    import research
    import video

ROOT = workspace_paths.default_root()

PLAYBACK_CHECKS = (
    "full_timeline",
    "muted_comprehension",
    "sound_intent_verified",
    "copy_correct",
    "visual_quality",
    "claims_truthful",
    "cultural_fit",
    "safe_zones",
    "lineage_fidelity",
)
SOUND_STRATEGIES = {
    "intentional_silence",
    "licensed_music",
    "voiceover",
    "music_and_voiceover",
}
ARTIFACT_REQUIRED_FIELDS = (
    "market_id",
    "locale",
    "copy_language",
    "format",
    "duration_seconds",
    "audio_strategy",
    "technical_status",
    "brief_ref",
    "concept_id",
    "concept_lineage",
    "concept_lineage_ref",
    "execution_lineage",
    "variant_id",
    "width",
    "height",
)
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
            "concept_lineage": artifact.get("concept_lineage"),
            "concept_lineage_ref": artifact.get("concept_lineage_ref"),
            "execution_lineage": artifact.get("execution_lineage"),
            "execution_ref": artifact.get("execution_ref"),
            "variant_id": artifact.get("variant_id"),
        }
    )[:20]


def scene_frame_plan(props: dict) -> list[dict]:
    """Return one deterministic midpoint frame for every agent-authored scene."""
    fps = props.get("fps")
    if not isinstance(fps, (int, float)) or isinstance(fps, bool) or fps <= 0:
        raise ValueError("props.fps precisa ser positivo")
    scenes = props.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("props.scenes precisa ser uma lista não vazia")
    plan, seen = [], set()
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            raise ValueError(f"props.scenes[{index}] inválida")
        scene_id = scene.get("id")
        start = scene.get("startFrame")
        duration = scene.get("durationInFrames")
        if not isinstance(scene_id, str) or not scene_id.strip() or scene_id in seen:
            raise ValueError(f"scene id ausente ou duplicado: {scene_id}")
        if not isinstance(start, int) or isinstance(start, bool) or start < 0:
            raise ValueError(f"scene {scene_id} startFrame inválido")
        if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
            raise ValueError(f"scene {scene_id} durationInFrames inválido")
        seen.add(scene_id)
        frame = start + (duration - 1) // 2
        plan.append(
            {
                "scene_id": scene_id,
                "frame": frame,
                "time_seconds": frame / float(fps),
            }
        )
    return plan


def scene_plan_from_inputs(input_files: list[dict]) -> list[dict]:
    props_inputs = [
        item for item in input_files if item.get("role") == "render_props"
    ]
    if len(props_inputs) != 1:
        raise ValueError("run lock exige exatamente um input render_props")
    path = Path(str(props_inputs[0].get("path") or ""))
    try:
        props = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"render_props inválido: {exc}") from exc
    if not isinstance(props, dict):
        raise ValueError("render_props precisa ser objeto JSON")
    return scene_frame_plan(props)


def scene_evidence_metadata_errors(
    artifact: dict,
    expected_plan: list[dict],
) -> list[str]:
    errors = []
    expected_ids = [item["scene_id"] for item in expected_plan]
    if artifact.get("scene_ids") != expected_ids:
        errors.append("scene_ids divergem dos midpoints de render_props")
    evidence = artifact.get("scene_evidence")
    if not isinstance(evidence, list) or not evidence:
        return [*errors, "artifact sem scene_evidence full-resolution"]
    evidence_ids = [
        item.get("scene_id") if isinstance(item, dict) else None for item in evidence
    ]
    valid_ids = all(isinstance(item, str) and item for item in evidence_ids)
    if (
        not valid_ids
        or len(evidence_ids) != len(set(evidence_ids))
        or set(evidence_ids) != set(expected_ids)
    ):
        errors.append("scene_evidence não cobre exatamente todos os scene_ids")
        return errors
    expected_by_id = {item["scene_id"]: item for item in expected_plan}
    seen_paths = []
    for item in evidence:
        expected = expected_by_id[item["scene_id"]]
        if item.get("frame") != expected["frame"]:
            errors.append(
                f"scene frame {item['scene_id']} diverge do midpoint de render_props"
            )
        actual_time = item.get("time_seconds")
        if (
            not isinstance(actual_time, (int, float))
            or isinstance(actual_time, bool)
            or abs(float(actual_time) - expected["time_seconds"]) > 1e-9
        ):
            errors.append(
                f"scene frame {item['scene_id']} time_seconds diverge do midpoint"
            )
        path = str(item.get("path") or "")
        if not path:
            errors.append(f"scene frame {item['scene_id']} sem path")
        seen_paths.append(path)
    if len(seen_paths) != len(set(seen_paths)):
        errors.append("scene_evidence reutiliza o mesmo arquivo em cenas diferentes")
    return errors


def seal_scene_evidence(
    artifact: dict,
    expected_plan: list[dict],
) -> list[dict]:
    metadata_errors = scene_evidence_metadata_errors(artifact, expected_plan)
    if metadata_errors:
        raise ValueError("; ".join(metadata_errors))
    evidence = artifact["scene_evidence"]
    expected_size = (artifact.get("width"), artifact.get("height"))
    sealed_evidence = []
    for item in evidence:
        sealed = seal_file(
            {**item, "role": "qa_scene_frame"}, kind="input"
        )
        path = Path(sealed["path"])
        try:
            with Image.open(path) as image:
                actual_size = image.size
        except Exception as exc:
            raise ValueError(f"scene frame inválido {path}: {exc}") from exc
        if actual_size != expected_size:
            raise ValueError(
                f"scene frame {item.get('scene_id')} dimensão {actual_size} "
                f"diverge do vídeo {expected_size}"
            )
        sealed["width"], sealed["height"] = actual_size
        sealed_evidence.append(sealed)
    return sealed_evidence


def _first_symlink_component(path: Path, root: Path) -> Path | None:
    candidate = lexical_absolute(path)
    root = lexical_absolute(root)
    try:
        candidate.relative_to(root)
    except ValueError:
        return candidate
    cursor = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        cursor = cursor / part
        if cursor.is_symlink():
            return cursor
    return None


def canonical_video_input_errors(
    *,
    app: str,
    input_files: list[dict],
    artifacts: list[dict],
    git_state: dict,
) -> list[str]:
    """Bind video lineage inputs and artifacts to canonical workspace paths."""
    errors = []
    root_value = git_state.get("repository_root") if isinstance(git_state, dict) else None
    if not root_value:
        return ["video core inputs sem repository_root canônico"]
    root = lexical_absolute(root_value)
    if not isinstance(app, str) or not SAFE_SEGMENT_RE.fullmatch(app):
        return [f"video app inválido para path canônico: {app!r}"]
    root_symlink = _first_symlink_component(root, root)
    if root_symlink is not None:
        errors.append(f"video repository root usa symlink: {root_symlink}")

    recipe_inputs = [
        item
        for item in input_files
        if isinstance(item, dict) and item.get("role") == "recipe"
    ]
    if len(recipe_inputs) != 1:
        return [*errors, "video core input recipe precisa ser único e canônico"]
    recipe_path_value = recipe_inputs[0].get("path")
    if not isinstance(recipe_path_value, (str, os.PathLike)):
        return [*errors, "video core input recipe path authored inválido"]
    recipe_authored_path = os.fspath(recipe_path_value)
    recipe_authored = Path(recipe_authored_path)
    if (
        not recipe_authored.is_absolute()
        or any(part in {".", ".."} for part in recipe_authored.parts)
    ):
        return [*errors, "video core input recipe path authored não é canônico"]
    recipe_path = lexical_absolute(recipe_authored_path)
    canonical_recipe_dir = root / "recipes" / app / "video"
    recipe_name = recipe_path.stem
    expected_recipe_path = canonical_recipe_dir / f"{recipe_name}.yaml"
    if (
        recipe_path.parent != canonical_recipe_dir
        or recipe_path.suffix != ".yaml"
        or not SAFE_SEGMENT_RE.fullmatch(recipe_name)
        or recipe_authored_path != str(expected_recipe_path)
    ):
        errors.append(
            f"video core input recipe não é canônico: {recipe_path} não pertence "
            f"a {canonical_recipe_dir}"
        )
        return errors
    recipe_symlink = _first_symlink_component(recipe_path, root)
    if recipe_symlink is not None:
        errors.append(
            f"video core input recipe usa symlink ancestral não canônico: {recipe_symlink}"
        )
    try:
        recipe = video.load_yaml(recipe_path)
    except video.VideoError as exc:
        return [*errors, f"video recipe canônica inválida: {exc}"]
    brief_ref = recipe.get("brief_ref")
    if not isinstance(brief_ref, str) or not SAFE_SEGMENT_RE.fullmatch(brief_ref):
        return [*errors, f"video brief_ref inválido para path canônico: {brief_ref!r}"]

    role_paths = (
        ("brief", root / "briefs" / app / f"{brief_ref}.yaml", True),
        (
            "video_patterns",
            root / "swipe" / app / "video-patterns.yaml",
            False,
        ),
        ("research", root / "swipe" / app / "competitors.yaml", False),
        ("app_config", root / "apps" / f"{app}.yaml", False),
    )
    for role, expected_path, required in role_paths:
        matches = [
            item
            for item in input_files
            if isinstance(item, dict) and item.get("role") == role
        ]
        if not matches:
            if required:
                errors.append(f"video core input {role} canônico ausente")
            continue
        if len(matches) != 1:
            errors.append(f"video core input {role} precisa ser único e canônico")
            continue
        path_value = matches[0].get("path")
        if not isinstance(path_value, (str, os.PathLike)):
            errors.append(f"video core input {role} path authored inválido")
            continue
        authored_path = os.fspath(path_value)
        authored = Path(authored_path)
        canonical_expected = lexical_absolute(expected_path)
        if (
            not authored.is_absolute()
            or any(part in {".", ".."} for part in authored.parts)
            or authored_path != str(canonical_expected)
        ):
            errors.append(
                f"video core input {role} path authored não é canônico: "
                f"{authored_path!r} != {str(canonical_expected)!r}"
            )
            continue
        symlink = _first_symlink_component(canonical_expected, root)
        if symlink is not None:
            errors.append(
                f"video core input {role} usa symlink ancestral não canônico: {symlink}"
            )

    for artifact in artifacts:
        if not isinstance(artifact, dict):
            errors.append("video artifact inválido para path canônico")
            continue
        try:
            expected_path = expected_video_path(
                root,
                app,
                recipe_name,
                artifact.get("locale"),
                artifact.get("format"),
            )
        except (TypeError, ValueError) as exc:
            errors.append(f"video artifact sem path canônico: {exc}")
            continue
        artifact_path_value = artifact.get("path")
        if not isinstance(artifact_path_value, (str, os.PathLike)):
            errors.append("video artifact path authored inválido")
            continue
        artifact_authored_path = os.fspath(artifact_path_value)
        artifact_authored = Path(artifact_authored_path)
        actual_path = lexical_absolute(artifact_authored_path)
        if (
            not artifact_authored.is_absolute()
            or any(part in {".", ".."} for part in artifact_authored.parts)
            or artifact_authored_path != str(expected_path)
            or actual_path != expected_path
        ):
            errors.append(
                f"video artifact não é canônico para recipe {recipe_name}: "
                f"{actual_path} != {expected_path}"
            )
    return errors


def _lineage_input_yaml(
    input_files: list[dict],
    role: str,
    *,
    required: bool,
) -> tuple[dict | None, list[str]]:
    matches = [
        item
        for item in input_files
        if isinstance(item, dict) and item.get("role") == role
    ]
    if not matches:
        if required:
            return None, [f"run lock sem input {role} para validar lineage"]
        return {}, []
    if len(matches) != 1:
        return None, [f"run lock exige no máximo um input {role} para lineage"]
    try:
        return video.load_yaml(
            lexical_absolute(str(matches[0].get("path") or ""))
        ), []
    except (OSError, video.VideoError) as exc:
        return None, [f"run lock input {role} inválido para lineage: {exc}"]


def video_lineage_contract_errors(
    input_files: list[dict],
    artifacts: list[dict],
) -> list[str]:
    """Rebind sealed artifact labels to the sealed recipe/brief/research truth."""
    errors = []
    recipe, input_errors = _lineage_input_yaml(
        input_files, "recipe", required=True
    )
    errors.extend(input_errors)
    brief, input_errors = _lineage_input_yaml(
        input_files, "brief", required=True
    )
    errors.extend(input_errors)
    patterns, input_errors = _lineage_input_yaml(
        input_files, "video_patterns", required=False
    )
    errors.extend(input_errors)
    competitors, input_errors = _lineage_input_yaml(
        input_files, "research", required=False
    )
    errors.extend(input_errors)
    if recipe is None or brief is None or patterns is None or competitors is None:
        return errors

    research_by_id, registry_errors = video.merge_research_registries(
        patterns,
        competitors,
    )
    errors.extend(registry_errors)

    brief_ref = recipe.get("brief_ref")
    if not brief_ref or brief_ref != brief.get("id"):
        errors.append(
            f"video lineage brief_ref {brief_ref!r} diverge do brief {brief.get('id')!r}"
        )
    concept_id = recipe.get("concept_id")
    concepts = {
        item.get("id"): item
        for item in brief.get("concepts", []) or []
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    concept = concepts.get(concept_id)
    if concept is None:
        errors.append(
            f"video lineage concept_id {concept_id!r} não existe no brief selado"
        )
        return errors

    concept_lineage = concept.get("lineage")
    concept_lineage_ref = concept.get("lineage_ref")
    if concept_lineage not in research.ALLOWED_LINEAGE:
        errors.append(f"video concept lineage inválida: {concept_lineage}")
    concept_refs_value = concept.get("research_refs")
    if not isinstance(concept_refs_value, list) or not all(
        isinstance(item, str) and item for item in concept_refs_value
    ):
        errors.append("video concept research_refs inválidas para lineage")
        concept_refs = set()
    else:
        concept_refs = set(concept_refs_value)
    recipe_refs_value = recipe.get("research_refs")
    if not isinstance(recipe_refs_value, list) or not all(
        isinstance(item, str) and item for item in recipe_refs_value
    ):
        errors.append("video recipe research_refs inválidas para lineage")
        recipe_refs = set()
    else:
        recipe_refs = set(recipe_refs_value)
    if not recipe_refs.issubset(concept_refs):
        errors.append("video recipe research_refs divergem do concept selado")
    for ref in sorted(concept_refs | recipe_refs):
        if ref not in research_by_id:
            errors.append(f"video lineage research_ref inexistente: {ref}")
    if not isinstance(concept_lineage_ref, str) or not concept_lineage_ref:
        errors.append("video concept sem lineage_ref selada")
        concept_anchor = None
    else:
        if concept_lineage_ref not in concept_refs:
            errors.append("video concept lineage_ref não está em research_refs")
        concept_anchor = research_by_id.get(concept_lineage_ref)
        if concept_anchor is None:
            errors.append(
                f"video concept lineage_ref inexistente: {concept_lineage_ref}"
            )
    if concept_anchor is not None and concept_lineage != "exploratory":
        if concept_anchor.get("lineage") != concept_lineage:
            errors.append(
                "video concept lineage diverge da lineage_ref selada: "
                f"{concept_lineage} != {concept_anchor.get('lineage')}"
            )
        if concept_lineage == "own_winner" and (
            concept_anchor.get("evidence_level") != "performance_data"
            or not concept_anchor.get("performance_metrics")
        ):
            errors.append(
                "video concept own_winner exige lineage_ref com performance_data "
                "e performance_metrics"
            )

    execution_ref = recipe.get("execution_ref")
    if execution_ref and execution_ref not in recipe_refs:
        errors.append("video execution_ref não está em recipe.research_refs")
    if execution_ref and execution_ref not in research_by_id:
        errors.append(f"video execution_ref inexistente: {execution_ref}")
    execution_lineage, expected_execution_ref = briefs.execution_binding(
        recipe,
        concept,
        research_by_id,
    )
    if execution_lineage not in {*research.ALLOWED_LINEAGE, "original"}:
        errors.append(
            f"video execution lineage não resolve em evidência válida: {execution_lineage}"
        )

    expected = {
        "brief_ref": brief_ref,
        "concept_id": concept_id,
        "concept_lineage": concept_lineage,
        "concept_lineage_ref": concept_lineage_ref,
        "execution_lineage": execution_lineage,
        "execution_ref": expected_execution_ref,
        "variant_id": recipe.get("variant_id"),
    }
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            errors.append("video lineage artifact inválido")
            continue
        for field, expected_value in expected.items():
            if artifact.get(field) != expected_value:
                errors.append(
                    f"artifact {artifact.get('path', '<sem path>')} {field} diverge "
                    f"da lineage selada: {artifact.get(field)!r} != {expected_value!r}"
                )
    return errors


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
    expected_scene_plan = scene_plan_from_inputs(sealed_inputs)
    sealed_artifacts = []
    for artifact in artifacts:
        sealed = seal_file(artifact, kind="artifact")
        metadata_errors = artifact_metadata_errors(sealed)
        if metadata_errors:
            raise ValueError("; ".join(metadata_errors))
        sealed["scene_evidence"] = seal_scene_evidence(
            sealed,
            expected_scene_plan,
        )
        sealed["scene_plan"] = deepcopy(expected_scene_plan)
        sealed["artifact_key"] = artifact_key(app, batch_id, sealed)
        sealed_artifacts.append(sealed)
    canonical_errors = canonical_video_input_errors(
        app=app,
        input_files=sealed_inputs,
        artifacts=sealed_artifacts,
        git_state=git_state,
    )
    if canonical_errors:
        raise ValueError("; ".join(canonical_errors))
    lineage_errors = video_lineage_contract_errors(
        sealed_inputs,
        sealed_artifacts,
    )
    if lineage_errors:
        raise ValueError("; ".join(lineage_errors))
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


def artifact_metadata_errors(artifact: dict) -> list[str]:
    """Revalidate semantic artifact invariants, independent of the lock digest."""
    errors = []
    path = artifact.get("path", "artifact")
    for field in ARTIFACT_REQUIRED_FIELDS:
        if artifact.get(field) in (None, ""):
            errors.append(f"artifact sem {field}: {path}")
    if artifact.get("audio_strategy") not in SOUND_STRATEGIES:
        errors.append(f"audio_strategy inválida: {artifact.get('audio_strategy')}")
    if artifact.get("technical_status") != "pass":
        errors.append(f"artifact sem QA técnico PASS: {path}")
    if artifact.get("concept_lineage") not in research.ALLOWED_LINEAGE:
        errors.append(f"artifact com concept_lineage inválida: {path}")
    allowed_execution = {*research.ALLOWED_LINEAGE, "original"}
    execution_lineage = artifact.get("execution_lineage")
    execution_ref = artifact.get("execution_ref")
    if execution_lineage not in allowed_execution:
        errors.append(f"artifact com execution_lineage inválida: {path}")
    if execution_lineage == "competitor_pattern" and not execution_ref:
        errors.append(f"artifact competitor_pattern sem execution_ref: {path}")
    if execution_lineage == "original" and execution_ref:
        errors.append(f"artifact original não pode declarar execution_ref: {path}")
    return errors


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
    errors.extend(
        canonical_video_input_errors(
            app=lock.get("app"),
            input_files=lock.get("input_files", []) or [],
            artifacts=lock.get("artifacts", []) or [],
            git_state=lock.get("git_state") or {},
        )
    )
    errors.extend(
        video_lineage_contract_errors(
            lock.get("input_files", []) or [],
            lock.get("artifacts", []) or [],
        )
    )
    try:
        expected_scene_plan = scene_plan_from_inputs(lock.get("input_files", []) or [])
    except (TypeError, ValueError) as exc:
        errors.append(f"run lock render_props inválido: {exc}")
        expected_scene_plan = None
    seen_artifact_keys = set()
    for item in lock.get("artifacts", []) or []:
        current_key = item.get("artifact_key")
        if current_key in seen_artifact_keys:
            errors.append(f"run lock artifact_key duplicado: {current_key}")
        seen_artifact_keys.add(current_key)
        expected_key = artifact_key(lock.get("app"), lock.get("batch_id"), item)
        if current_key != expected_key:
            errors.append(f"run lock artifact_key inválido: {current_key}")
        errors.extend(artifact_metadata_errors(item))
        errors.extend(verify_sealed_file(item, kind="artifact"))
        evidence = item.get("scene_evidence", []) or []
        if expected_scene_plan is not None:
            if item.get("scene_plan") != expected_scene_plan:
                errors.append(
                    f"artifact {current_key} scene_plan diverge de render_props"
                )
            errors.extend(
                f"artifact {current_key} {error}"
                for error in scene_evidence_metadata_errors(
                    item,
                    expected_scene_plan,
                )
            )
        for frame in evidence if isinstance(evidence, list) else []:
            if not isinstance(frame, dict):
                errors.append(f"artifact {current_key} scene frame inválido")
                continue
            errors.extend(verify_sealed_file(frame, kind="input"))
            path = Path(frame.get("path", ""))
            if path.is_file():
                try:
                    with Image.open(path) as image:
                        if image.size != (item.get("width"), item.get("height")):
                            errors.append(
                                f"artifact {current_key} scene frame dimensão divergente"
                            )
                except Exception as exc:
                    errors.append(f"artifact {current_key} scene frame inválido: {exc}")
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
                "concept_lineage": artifact["concept_lineage"],
                "concept_lineage_ref": artifact["concept_lineage_ref"],
                "execution_lineage": artifact["execution_lineage"],
                "execution_ref": artifact.get("execution_ref"),
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
            "concept_lineage": record.get("concept_lineage"),
            "concept_lineage_ref": record.get("concept_lineage_ref"),
            "execution_lineage": record.get("execution_lineage"),
            "execution_ref": record.get("execution_ref"),
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
                "concept_lineage",
                "concept_lineage_ref",
                "execution_lineage",
                "execution_ref",
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
            if not str(record.get("reviewer") or "").strip():
                errors.append(
                    f"artifact {record.get('artifact_key')} reviewer ausente"
                )
            if not str(record.get("notes") or "").strip():
                errors.append(f"artifact {record.get('artifact_key')} notes ausentes")
            try:
                reviewed_at = datetime.fromisoformat(
                    str(record.get("reviewed_at")).replace("Z", "+00:00")
                )
                if reviewed_at.tzinfo is None:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(
                    f"artifact {record.get('artifact_key')} reviewed_at inválido"
                )
        elif record.get("status") != "pending":
            errors.append(
                f"artifact {record.get('artifact_key')} status inválido: "
                f"{record.get('status')}"
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
    scene_plan: list[dict],
    expected_size: tuple[int, int],
) -> tuple[Path, Path, list[dict]]:
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
    scene_evidence = []
    for index, item in enumerate(scene_plan, start=1):
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", item["scene_id"]).strip("-")
        frame_path = qa_dir / f"scene-{index:02d}-{safe_id or 'scene'}.png"
        completed = run_command(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{item['time_seconds']:.6f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                str(frame_path),
            ]
        )
        if completed.returncode != 0 or not frame_path.is_file():
            raise ValueError(
                f"FFmpeg não gerou frame da cena {item['scene_id']}: "
                + (completed.stderr or "sem detalhe")[:500]
            )
        with Image.open(frame_path) as image:
            if image.size != expected_size:
                raise ValueError(
                    f"frame da cena {item['scene_id']} tem {image.size}, "
                    f"esperado {expected_size}"
                )
        scene_evidence.append({**item, "path": str(frame_path)})
    return poster, contact, scene_evidence


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
    video_patterns_path = root / "swipe" / app_slug / "video-patterns.yaml"
    research_path = root / "swipe" / app_slug / "competitors.yaml"
    if video_patterns_path.is_file():
        candidates.append(("video_patterns", video_patterns_path))
    if research_path.is_file():
        candidates.append(("research", research_path))
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
    frame_plan = scene_frame_plan(props)
    expected_size = video.FORMATS[recipe["format"]]
    poster, contact, scene_evidence = derive_visual_evidence(
        video_path,
        qa_dir,
        duration_seconds=float(recipe["duration_seconds"]),
        scene_plan=frame_plan,
        expected_size=expected_size,
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
    brief = video.load_yaml(
        root / "briefs" / app_slug / f"{recipe.get('brief_ref')}.yaml"
    )
    concept = next(
        (
            item
            for item in brief.get("concepts", []) or []
            if item.get("id") == recipe.get("concept_id")
        ),
        {},
    )
    patterns_path = root / "swipe" / app_slug / "video-patterns.yaml"
    patterns = (
        video.load_yaml(patterns_path)
        if patterns_path.is_file()
        else {"patterns": []}
    )
    competitor_path = root / "swipe" / app_slug / "competitors.yaml"
    competitors = video.load_yaml(competitor_path) if competitor_path.is_file() else {}
    research_by_id, registry_errors = video.merge_research_registries(
        patterns,
        competitors,
    )
    if registry_errors:
        raise ValueError("research registries inválidos: " + "; ".join(registry_errors))
    execution_lineage, execution_ref = briefs.execution_binding(
        recipe,
        concept,
        research_by_id,
    )
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
        "concept_lineage": concept.get("lineage"),
        "concept_lineage_ref": concept.get("lineage_ref"),
        "execution_lineage": execution_lineage,
        "execution_ref": execution_ref,
        "variant_id": recipe["variant_id"],
        "width": expected_size[0],
        "height": expected_size[1],
        "scene_ids": [item["scene_id"] for item in frame_plan],
        "scene_evidence": scene_evidence,
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
    approve.add_argument("--review-file", required=True)
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
        review = json.loads(Path(args.review_file).read_text())
        declared_checks = review.get("checks", [])
        if isinstance(declared_checks, dict):
            checks = declared_checks
        else:
            checks = {
                name: name in set(declared_checks or []) for name in PLAYBACK_CHECKS
            }
        try:
            report = approve_artifact(
                report,
                args.artifact_key,
                reviewer=args.reviewer,
                checks=checks,
                notes=review.get("notes", ""),
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
