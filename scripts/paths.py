#!/usr/bin/env python3
"""Resolve repo-local and sibling truth files from canonical or worktree checkouts."""

import os
import subprocess
from pathlib import Path


def absolute(value: str | Path) -> Path:
    return Path(os.path.abspath(Path(value).expanduser()))


def checkout_roots(root: Path) -> list[Path]:
    roots = [absolute(root)]
    configured = os.environ.get("CREATIVE_FORGE_ROOT")
    if configured:
        roots.append(absolute(configured))
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            common = Path(completed.stdout.strip())
            if not common.is_absolute():
                common = root / common
            common = absolute(common)
            if common.name == ".git":
                roots.append(common.parent)
    except OSError:
        pass
    return list(dict.fromkeys(roots))


def resolve_config_path(root: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    candidates = [absolute(candidate / path) for candidate in checkout_roots(root)]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0]
