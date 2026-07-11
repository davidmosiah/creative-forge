#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# creative-forge · Higgsfield adapter (AI photo generation)   [WIRED ✓]
#
# Confirmed live 2026-07-09 with higgsfield CLI v1.1.10. Real command surface,
# no fabricated flags. Generates a competitor-style lifestyle photo and saves it
# as a local PNG you can reference from a photo-overlay recipe.
#
# Portable: plain shell — Claude, Codex, and a human all call it the same way.
# (Higgsfield also ships an MCP at https://mcp.higgsfield.ai/mcp; the CLI path is
#  preferred here because shell is trivially portable across agents.)
#
# One-time setup:
#   bash scripts/higgsfield.sh install       # npm i -g @higgsfield/cli
#   bash scripts/higgsfield.sh login         # OAuth (opens a browser once)
#   bash scripts/higgsfield.sh workspace     # pick the billing workspace
# Then:
#   bash scripts/higgsfield.sh generate "a park path at golden sunrise, \
#       sunrise over a park path, cinematic, no text" assets/sunrise-demo/generated/dawn.png
#
# Cost: soul_cinematic ≈ 0.12 credit / image (1:1, 2k). Check with `account`.
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail
export PATH="/opt/homebrew/bin:$PATH"

MODEL="${HF_MODEL:-soul_cinematic}"   # override with HF_MODEL=flux_2 etc.

need() { command -v higgsfield >/dev/null 2>&1 || { echo "higgsfield not installed — run: bash scripts/higgsfield.sh install"; exit 1; }; }

install()   { echo "Installing Higgsfield CLI…"; npm install -g @higgsfield/cli; echo "Next: bash scripts/higgsfield.sh login"; }
login()     { need; higgsfield auth login; }
workspace() { need; higgsfield workspace list; echo; echo "Select with: higgsfield workspace set <ID>"; }
account()   { need; higgsfield account status; }
models()    { need; higgsfield model list --image; }

# discover the accepted params for the current model (aspect ratios, quality…)
discover()  { need; higgsfield model get "$MODEL"; }

# generate <prompt> <out.png> [aspect=1:1] [quality=2k]
generate() {
  need
  local prompt="${1:-}" out="${2:-}" aspect="${3:-1:1}" quality="${4:-2k}"
  if [ -z "$prompt" ] || [ -z "$out" ]; then
    echo 'usage: higgsfield.sh generate "<prompt>" <out.png> [aspect] [quality]'; exit 1
  fi
  echo "Generating with $MODEL ($aspect, $quality)…"
  local url
  url="$(higgsfield generate create "$MODEL" \
          --prompt "$prompt" --aspect_ratio "$aspect" --quality "$quality" \
          --wait --wait-timeout 3m --wait-interval 5s 2>&1 | tail -1)"
  case "$url" in
    https://*) mkdir -p "$(dirname "$out")"; curl -sS -L -o "$out" "$url"; echo "✓ $out" ;;
    *) echo "generation failed:"; echo "$url"; exit 2 ;;
  esac
}

cmd="${1:-}"; shift || true
case "$cmd" in
  install)   install ;;
  login)     login ;;
  workspace) workspace ;;
  account)   account ;;
  models)    models ;;
  discover)  discover ;;
  generate)  generate "$@" ;;
  *) echo "usage: higgsfield.sh {install|login|workspace|account|models|discover|generate <prompt> <out.png> [aspect] [quality]}" ; exit 1 ;;
esac
