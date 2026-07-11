#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE="$ROOT/skill/criar-criativos"
CODEX_TARGET="$HOME/.codex/skills/criar-criativos"
CLAUDE_TARGET="$HOME/.claude/skills/criar-criativos"

install_link() {
  local target="$1"
  mkdir -p "$(dirname "$target")"
  if [ -L "$target" ]; then
    if [ "$(readlink "$target")" = "$SOURCE" ]; then
      echo "✓ already installed: $target"
      return
    fi
    echo "refusing to replace different symlink: $target" >&2
    exit 1
  fi
  if [ -e "$target" ]; then
    echo "refusing to replace existing path: $target" >&2
    exit 1
  fi
  ln -s "$SOURCE" "$target"
  echo "✓ installed: $target -> $SOURCE"
}

install_link "$CODEX_TARGET"
install_link "$CLAUDE_TARGET"
