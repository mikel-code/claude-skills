#!/usr/bin/env bash
# Launcher for the url-to-markdown skill.
#
# Unlike granola (which needs its one API key on every invocation),
# url-to-markdown's tiers 0-3 (probe/static/chrome/jina) work with ZERO
# configuration and zero secrets. Only Tier 4 (Firecrawl) is keyed, and it's
# entirely optional — the ladder degrades gracefully without it (see
# references/design.md). So this launcher has two branches:
#
#   - url-to-markdown.env absent (the common case until a user opts into
#     Firecrawl): run the client directly, no pass-cli/session machinery at
#     all. Nothing here should require the user to have Proton Pass set up
#     just to convert a page with the free tiers.
#   - url-to-markdown.env present: resolve FIRECRAWL_API_KEY via
#     `pass-cli run --env-file`, which injects the real key into the client
#     process only — this script never sees the plaintext key. Same
#     self-healing runtime-session pattern as granola.sh (see
#     references/proton-pass-setup.md for what stays manual: yearly token
#     renewal).
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLIENT="$SKILL_DIR/scripts/url_to_markdown.py"
ENV_FILE="$SKILL_DIR/url-to-markdown.env"

UV="${UV:-/opt/homebrew/bin/uv}"
if [ ! -x "$UV" ]; then
  UV="$(command -v uv || true)"
fi
if [ -z "$UV" ]; then
  echo "error: uv not found (looked at /opt/homebrew/bin/uv and PATH)." >&2
  echo "Install uv (https://docs.astral.sh/uv/), or set UV to its absolute path." >&2
  exit 1
fi

# --- Zero-config path: no env file, no secrets, no pass-cli involvement. ---
if [ ! -f "$ENV_FILE" ]; then
  exec "$UV" run "$CLIENT" "$@"
fi

# --- Firecrawl-enabled path: env file present, resolve via pass-cli. ---

# Deliberately NOT derived from ambient PROTON_PASS_SESSION_DIR: a caller
# (e.g. Claude Code's own harness) may have that set for unrelated reasons
# (see PERSONAL_SESSION_DIR below) — this script's own runtime-session
# concept needs its own, non-colliding override variable.
URL_TO_MARKDOWN_SESSION_DIR="${URL_TO_MARKDOWN_RUNTIME_SESSION_DIR:-$HOME/.pass-sessions/url-to-markdown}"
TOKEN_REF="pass://Agents/URL-to-Markdown Agent Token/token"
# The token fetch below must target the caller's real personal session,
# never whatever PROTON_PASS_SESSION_DIR happens to be ambient (e.g. Claude
# Code's harness pins its own Bash calls to a dead-end session dir by
# design). Override via PROTON_PASS_PERSONAL_SESSION_DIR if this skill runs
# somewhere other than macOS with pass-cli's default session location.
PERSONAL_SESSION_DIR="${PROTON_PASS_PERSONAL_SESSION_DIR:-$HOME/Library/Application Support/proton-pass-cli}"

PASS_CLI="${PASS_CLI:-/opt/homebrew/bin/pass-cli}"
if [ ! -x "$PASS_CLI" ]; then
  PASS_CLI="$(command -v pass-cli || true)"
fi
if [ -z "$PASS_CLI" ]; then
  echo "error: pass-cli not found (looked at /opt/homebrew/bin/pass-cli and PATH)." >&2
  echo "Install the Proton Pass CLI, or set PASS_CLI to its absolute path." >&2
  exit 1
fi

# Self-heal the runtime session: if it's dead, fetch the stored agent token
# via the caller's personal session and log the runtime session back in.
# Runs at most once per invocation — no retry loop.
if ! PROTON_PASS_SESSION_DIR="$URL_TO_MARKDOWN_SESSION_DIR" "$PASS_CLI" info >/dev/null 2>&1; then
  TOKEN="$(PROTON_PASS_SESSION_DIR="$PERSONAL_SESSION_DIR" "$PASS_CLI" item view "$TOKEN_REF" 2>/dev/null)" || TOKEN=""
  if [ -z "$TOKEN" ]; then
    cat >&2 <<EOF
url-to-markdown: runtime session expired and the stored agent token could not
be fetched from '$TOKEN_REF'.

This means either your personal Proton Pass session isn't active (run
'pass-cli login' normally), or the token was never stored there — see
$SKILL_DIR/references/proton-pass-setup.md ("Store the token").
EOF
    exit 5
  fi
  if ! PROTON_PASS_SESSION_DIR="$URL_TO_MARKDOWN_SESSION_DIR" PROTON_PASS_PERSONAL_ACCESS_TOKEN="$TOKEN" "$PASS_CLI" login >/dev/null 2>&1; then
    cat >&2 <<EOF
url-to-markdown: found the stored agent token but could not log the runtime
session in with it. The token has likely reached its own (yearly)
expiration — see $SKILL_DIR/references/proton-pass-setup.md ("Token
expired") to renew it.
EOF
    exit 5
  fi
  unset TOKEN
fi

export PROTON_PASS_SESSION_DIR="$URL_TO_MARKDOWN_SESSION_DIR"
export PROTON_PASS_AGENT_REASON="${PROTON_PASS_AGENT_REASON:-url-to-markdown skill: read Firecrawl API key}"
exec "$PASS_CLI" run --env-file "$ENV_FILE" -- "$UV" run "$CLIENT" "$@"
