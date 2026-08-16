# Proton Pass setup for the granola skill

Everything on this page is run **by you**, from your own terminal, in your own
personal Proton Pass session — never delegate these commands to the agent.
They handle a raw credential (the agent token), and the whole point of this
skill's design is that raw credentials never flow through the agent.

Two different secrets are in play; don't confuse them:

- **The Granola API key** (`grn_…`) — stored once in Proton Pass, never leaves
  it in cleartext. `granola.sh` resolves it at spawn time via `pass-cli run`.
- **The Proton Pass agent token** (`pst_…`) — what lets `pass-cli` itself
  authenticate as a narrow, read-only identity. This page is mostly about
  managing *this* token.

There are also two separate authentication tracks, which is a common source of
confusion:

- **Your personal session** (the one you're normally logged into) authorizes
  *admin* operations on the agent — `agent create`, `agent renew`, `agent
  access grant`, `agent delete`. These are authorized by you being the
  logged-in account owner; none of them take the token as input, not even
  `renew`. Think of `renew` as "reset this credential," not "prove you hold
  the current one."
- **The isolated runtime session** (`~/.pass-sessions/granola`, what
  `granola.sh` actually uses) is a different, narrower login, authenticated
  *by* the token via `PROTON_PASS_PERSONAL_ACCESS_TOKEN=<token> pass-cli
  login`.

Consequence: you don't need the token to renew it, but you do need it saved
so the runtime session can refresh itself — it expires every ~2h (see
"Renewing" below).

**`granola.sh` refreshes the runtime session automatically.** Every
invocation checks whether `~/.pass-sessions/granola` is still alive; if not,
it fetches the stored token (via your personal session) and logs back in,
with no manual step, before running your command. This only works once the
token is actually stored in Proton Pass (below) — until then, or if your
personal session ever goes stale too, it fails with a clear message instead
of silently hanging.

**Why the token fetch pins the personal session dir explicitly.** If you've
locked down Claude Code's own `pass-cli` access (repointing its ambient
`PROTON_PASS_SESSION_DIR` at a dead-end session, e.g. via `settings.json`'s
top-level `env`, plus a `PreToolUse` hook blocking direct `pass-cli` calls —
see `~/.claude/settings.json` and `~/.claude/hooks/` if you've done this),
that repointing would otherwise break the token fetch too: without an
explicit override, it inherits whatever `PROTON_PASS_SESSION_DIR` is ambient
in the caller's environment, which under a locked-down Claude Code session is
the dead end, not your real personal session. `granola.sh` avoids this by
pinning that one call to `PERSONAL_SESSION_DIR`, which defaults to
`~/Library/Application Support/proton-pass-cli` (pass-cli's own macOS
default) and can be overridden via `PROTON_PASS_PERSONAL_SESSION_DIR` — set
that if this skill ever runs on Linux (default there is
`~/.local/share/proton-pass-cli`) or from a non-default location. This is a
deliberate trust boundary, not an accident: `granola.sh` is a vetted,
narrow, auditable wrapper — the one thing it's trusted to do is reach the
personal session for exactly this token, exactly this field, nothing else.
Any future wrapper script granted the same reach needs the same scrutiny.

**A second, related pitfall under the same lockdown**: `granola.sh`'s own
runtime-session variable (`GRANOLA_SESSION_DIR`) was originally derived as
`${PROTON_PASS_SESSION_DIR:-$HOME/.pass-sessions/granola}` — a convenience
that let an operator override it for testing. Once a caller's environment
*always* sets `PROTON_PASS_SESSION_DIR` (as the Claude Code lockdown does,
unconditionally, for every Bash call), that fallback silently picks up
whatever the caller set it to — the dead-end void directory — instead of
falling through to the real default. The runtime session then tries to log
in to a directory it can't write to, and fails in a way that looks unrelated
(a login failure, not a "wrong directory" error). The fix: the override
variable is now `GRANOLA_RUNTIME_SESSION_DIR`, a name that doesn't collide
with anything a caller's environment might set for unrelated reasons. If you
add other environment-derived defaults to this script later, don't key them
off generic `PROTON_PASS_*` names — anything this ambient can silently break
in ways that are hard to trace back to the cause.

## One-time setup

Run from a shell where your normal, full-privilege Proton Pass session is
active (i.e. you're already logged in with `pass-cli login` the regular way —
not the isolated session this skill uses at runtime).

**Why a dedicated vault, not an item-level grant on a shared vault:**
`pass-cli run --env-file` (what `granola.sh` uses to resolve the secret) only
understands `pass://vault/item/field` references that resolve through a named
*vault*. An item-scoped agent grant (`agent access grant --item-title …`)
creates a share with no vault visibility at all — `pass-cli vault list`
returns empty under that session — so `pass://` resolution fails with
`Could not find vault …` no matter how the reference is written. This isn't a
misconfiguration; it's confirmed in `pass-cli`'s own source, and matches
Proton's documented guidance: *"Organize unique credentials for your agent
inside dedicated vaults, sharing only what you need."* A single-item vault,
vault-scoped, gets you the same isolation (the agent still can't read
anything else) through the officially supported path.

```bash
# 1. Create the Granola API key: Granola web app → Settings → API keys → grn_…

# 2. Create a dedicated vault holding only this one secret
pass-cli vault create --name "Granola"

# 3. Store the key in Proton Pass: vault "Granola", item "Granola",
#    custom field named exactly "API Key" (case-sensitive).

# 4. Create a read-only, audited agent
pass-cli agent create granola-reader --expiration 1y

# 5. Grant it access to the dedicated vault (not the whole Agents vault)
pass-cli agent access grant granola-reader \
  --vault-name "Granola" --role viewer

# 6. Sanity-check the agent exists and its expiry
pass-cli agent list
```

Things that will trip you up (all verified against pass-cli 2.3.2 — the
published docs get some of these wrong):

- **`agent create` prints the token as JSON, exactly once.** It is never shown
  again — copy it now:
  
  ```json
  {"token": "PROTON_PASS_PERSONAL_ACCESS_TOKEN=pst_…::KEY", "instruction": "..."}
  ```
- If an item already exists somewhere else and you're relocating it into the
  new dedicated vault instead of creating fresh, `pass-cli item move
  --from-vault-name "<old>" --item-title "Granola" --to-vault-name "Granola"`
  does it in place — any existing item-level grant on it is invalidated
  automatically (moving invalidates the old share; nothing to revoke by hand).
- `--role viewer` is the default and is genuinely read-only — "cannot edit,
  delete, or share the resource." (`pass-cli share list` under the agent's own
  session may display it as `Role=Owner` regardless — this looks like a
  display quirk in this pass-cli version, not an actual privilege escalation;
  unconfirmed against Proton support, flagged here so it doesn't cause alarm.)
- `--expiration` only accepts `1h, 1d, 1w, 1m, 3m, 6m, 1y` — no arbitrary
  durations.
- `pass-cli agent item view` (from the docs' example flow) doesn't exist; the
  command is plain `pass-cli item view`.
- `pass-cli test` (suggested by `pass-cli agent instructions`) doesn't exist in
  2.3.2 — use `pass-cli info` to check connectivity instead.

**Store the token somewhere you can find it in a year.** The cleanest option is
Proton Pass itself, as its own item, in the Agents vault.

Keep it out of shell history first — on zsh, `HIST_IGNORE_SPACE` excludes any
command line that starts with a leading space from `~/.zsh_history` (one-time
setup: `echo 'setopt HIST_IGNORE_SPACE' >> ~/.zshrc && source ~/.zshrc`; bash's
equivalent is `HISTCONTROL=ignorespace`).

Two ways to get the JSON to `pass-cli`; pick whichever works for you.

**Option A — heredoc directly** (fastest, but fragile if your terminal breaks
the paste mid-block — a stray newline can leave zsh reporting `unmatched '`):

```bash
 pass-cli item create custom --vault-name "Agents" --from-template - <<'EOF'
{
  "title": "Granola Agent Token",
  "note": "pass-cli agent token for the granola skill's dedicated-vault reader. Renew with: pass-cli agent renew granola-reader --expiration 1y",
  "sections": [
    {
      "section_name": "Token",
      "fields": [
        {"field_name": "token", "field_type": "hidden", "value": "pst_…::KEY"}
      ]
    }
  ]
}
EOF
```
(the leading space before `pass-cli` keeps the whole block out of history —
zsh's `HIST_IGNORE_SPACE` excludes any command line starting with a space).
Replace `pst_…::KEY` with the real value before running.

**Option B — via a temp file**, if Option A's paste breaks:

```bash
 nano /tmp/granola-token.json
```
(the leading space keeps this out of history too). Paste in the same JSON as
above, replacing `pst_…::KEY` with the real value, save, then:

```bash
pass-cli item create custom --vault-name "Agents" --from-template /tmp/granola-token.json
rm /tmp/granola-token.json
```

Either way: `custom` items take their title from the JSON's `"title"` field —
there's no separate `--title` flag, unlike `login`/`note` items.

You don't have to use this exact template — creating the item through the
Proton Pass app works too. **Whatever field name you end up with, it must
match `TOKEN_REF` in `granola.sh`** (currently `pass://Agents/Granola Agent
Token/token`) — if you use a different field name, update that constant to
match rather than renaming the field, since renaming an existing item may not
be possible if it came from a fixed app template, while updating the constant
is always a one-line change.

**Enable the skill:**

```bash
cp ~/.claude/skills/granola/granola.env.example ~/.claude/skills/granola/granola.env
# (the reference inside is already correct — nothing to edit)
```

**Verify end-to-end** — this first run is also what bootstraps the isolated
runtime session (`~/.pass-sessions/granola`); `granola.sh` sees no session
there yet, fetches the token you just stored, and logs it in automatically:

```bash
~/.claude/skills/granola/scripts/granola.sh list --limit 1
```

## Renewing — two different expiries, don't conflate them

| | Lifetime | Symptom | Fix |
|---|---|---|---|
| **Session** (`~/.pass-sessions/granola`) | ~2 hours (agent/PAT sessions can't hold a lock) | none — `granola.sh` self-heals it on the next invocation | nothing, unless your personal session is also stale (below) |
| **Token** (`granola-reader` agent) | Whatever `--expiration` was set to (here, 1 year) | self-heal fails with "found the stored agent token but could not log in" | `pass-cli agent renew` (below) — issues a **new** token |

### Session expired (routine — happens constantly, handled automatically)

Nothing to do. `granola.sh` checks the runtime session on every invocation and
re-logs it in for you using the stored token if it's dead. You'll only see
this fail if your **personal** session (the one that fetches the stored
token) has also gone stale — in that case just run `pass-cli login` normally
and retry.

If the runtime session is wedged rather than just expired:

```bash
PROTON_PASS_SESSION_DIR="$HOME/.pass-sessions/granola" pass-cli logout --force
# then run any granola.sh command — it will log back in automatically
```

### Token expired (yearly — you will not remember these steps, that's why this file exists)

```bash
pass-cli agent renew granola-reader --expiration 1y
```

- **The old token stops working immediately.**
- A **new token string is printed once** — same JSON shape as `agent create`.
- **Access grants are preserved** — no need to re-run `agent access grant`.

Then update the `token` field on the `Granola Agent Token` item in Proton
Pass with the new value — that's the only manual step. The next `granola.sh`
invocation will pick it up and self-heal the runtime session automatically;
no need to log in by hand.

## Auditing

This is the payoff of using an agent instead of a plain PAT: every read is
logged with the reason `granola.sh` supplies (`granola skill: read Granola API
key`).

```bash
pass-cli agent monitor granola-reader --limit 20
```

## Lifecycle reference

```bash
pass-cli agent list                                          # list agents + expiry
pass-cli agent delete granola-reader                          # decommission
pass-cli agent access revoke granola-reader --share-id <ID>   # revoke a grant
```

`access revoke` only takes `--share-id` (get it from `pass-cli share list`,
by name) — there's no `--vault-name` shortcut for revoke, unlike grant.

## Install / upgrade

Installed via Homebrew (`/opt/homebrew/bin/pass-cli`). **`pass-cli update`
does not work** for Homebrew installs — use `brew upgrade pass-cli` instead.
Current version at time of writing: 2.3.2.
