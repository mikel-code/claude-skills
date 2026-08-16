#!/usr/bin/env bash
# Launcher for the granola skill. Reads the pass:// reference from granola.env
# and runs the client under `pass-cli run`, which resolves it against a
# Proton Pass agent vault-scoped to a dedicated single-item vault, and
# injects the real key into the client process only. This script never sees
# the plaintext key.
#
# The Proton Pass runtime session (~/.pass-sessions/granola) expires every
# ~2h. This script self-heals: if it's dead, it fetches the stored agent
# token via your personal Proton Pass session (must be active) and logs the
# runtime session back in, with no manual step. See
# references/proton-pass-setup.md for what to do when the token itself (not
# just the session) expires — that part stays manual on purpose.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLIENT="$SKILL_DIR/scripts/granola_client.py"
# Deliberately NOT derived from ambient PROTON_PASS_SESSION_DIR: a caller
# (e.g. Claude Code's own harness) may have that set for unrelated reasons
# (see PERSONAL_SESSION_DIR below) — this script's own runtime-session
# concept needs its own, non-colliding override variable.
GRANOLA_SESSION_DIR="${GRANOLA_RUNTIME_SESSION_DIR:-$HOME/.pass-sessions/granola}"
TOKEN_REF="pass://Agents/Granola Agent Token/token"
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

ENV_FILE="$SKILL_DIR/granola.env"
if [ ! -f "$ENV_FILE" ]; then
  cat >&2 <<EOF
granola: not configured — $ENV_FILE is missing.

One-time setup (human present):
  1. In Granola (web app → Settings → API keys), create an API key (grn_…).
  2. In Proton Pass, create a dedicated 'Granola' vault and store the key
     there as item 'Granola' with a custom field named exactly 'API Key'
     (field names are case-sensitive).
  3. Create the granola-reader agent, vault-scoped to that dedicated vault —
     see $SKILL_DIR/references/proton-pass-setup.md for exact commands.
  4. Copy $SKILL_DIR/granola.env.example to $ENV_FILE
     (the reference is already correct; nothing to edit).
EOF
  exit 2
fi

# Self-heal the runtime session: if it's dead, fetch the stored agent token
# via the caller's personal session and log the runtime session back in.
# Runs at most once per invocation — no retry loop.
if ! PROTON_PASS_SESSION_DIR="$GRANOLA_SESSION_DIR" "$PASS_CLI" info >/dev/null 2>&1; then
  TOKEN="$(PROTON_PASS_SESSION_DIR="$PERSONAL_SESSION_DIR" "$PASS_CLI" item view "$TOKEN_REF" 2>/dev/null)" || TOKEN=""
  if [ -z "$TOKEN" ]; then
    cat >&2 <<EOF
granola: runtime session expired and the stored agent token could not be
fetched from '$TOKEN_REF'.

This means either your personal Proton Pass session isn't active (run
'pass-cli login' normally), or the token was never stored there — see
$SKILL_DIR/references/proton-pass-setup.md ("Store the token").
EOF
    exit 3
  fi
  if ! PROTON_PASS_SESSION_DIR="$GRANOLA_SESSION_DIR" PROTON_PASS_PERSONAL_ACCESS_TOKEN="$TOKEN" "$PASS_CLI" login >/dev/null 2>&1; then
    cat >&2 <<EOF
granola: found the stored agent token but could not log the runtime session
in with it. The token has likely reached its own (yearly) expiration — see
$SKILL_DIR/references/proton-pass-setup.md ("Token expired") to renew it.
EOF
    exit 3
  fi
  unset TOKEN
fi

export PROTON_PASS_SESSION_DIR="$GRANOLA_SESSION_DIR"
export PROTON_PASS_AGENT_REASON="${PROTON_PASS_AGENT_REASON:-granola skill: read Granola API key}"
"$PASS_CLI" run --env-file "$ENV_FILE" -- python3 "$CLIENT" "$@"
