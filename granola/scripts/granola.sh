#!/usr/bin/env bash
# Mode A launcher for the granola skill. Resolves the API key reference from
# secrets.toml and runs the client under `pass-cli run`, which injects the real
# key into the client process only. This script never sees the plaintext key.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REGISTRY="$SKILL_DIR/secrets.toml"
CLIENT="$SKILL_DIR/scripts/granola_client.py"

PASS_CLI="${PASS_CLI:-/opt/homebrew/bin/pass-cli}"
if [ ! -x "$PASS_CLI" ]; then
  PASS_CLI="$(command -v pass-cli || true)"
fi
if [ -z "$PASS_CLI" ]; then
  echo "error: pass-cli not found (looked at /opt/homebrew/bin/pass-cli and PATH)." >&2
  echo "Install the Proton Pass CLI, or set PASS_CLI to its absolute path." >&2
  exit 1
fi

# Read one key from the [services.granola] block of secrets.toml.
registry_get() {
  awk -v key="$1" '
    /^\[services\.granola\]/ { in_block = 1; next }
    /^\[/                    { in_block = 0 }
    in_block {
      line = $0
      sub(/[ \t]*#.*$/, "", line)
      if (line ~ "^[ \t]*" key "[ \t]*=") {
        sub(/^[^=]*=[ \t]*/, "", line)
        gsub(/^"|"$/, "", line)
        print line
        exit
      }
    }
  ' "$REGISTRY"
}

ENV_VAR="$(registry_get env_var)"
REFERENCE="$(registry_get reference)"
ENABLED="$(registry_get enabled)"

if [ "$ENABLED" != "true" ]; then
  cat >&2 <<EOF
granola: service is disabled in $REGISTRY (enabled = false).

One-time setup (human present):
  1. In Granola (web app → Settings → API keys), create an API key (grn_…).
  2. In Proton Pass, store it in the 'Agents' vault as item 'Granola' with a
     custom field named exactly 'API Key' (field names are case-sensitive).
  3. Bootstrap the agent session if not done yet:
       PROTON_PASS_SESSION_DIR="\$HOME/.pass-sessions/claude-code" pass-cli login
     (log in with a service/agent token scoped to the 'Agents' vault)
  4. Set enabled = true in $REGISTRY.
EOF
  exit 2
fi

REF_FILE="$(mktemp "${TMPDIR:-/tmp}/granola-ref.XXXXXX")"
chmod 600 "$REF_FILE"
trap 'rm -f "$REF_FILE"' EXIT
printf '%s=%s\n' "$ENV_VAR" "$REFERENCE" > "$REF_FILE"

export PROTON_PASS_SESSION_DIR="${PROTON_PASS_SESSION_DIR:-$HOME/.pass-sessions/claude-code}"
"$PASS_CLI" run --env-file "$REF_FILE" -- python3 "$CLIENT" "$@"
