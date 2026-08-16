# CLAUDE.md

Instructions for Claude Code when working in this repo.

## Secrets in skills

When a skill needs an API key or other credential, follow the pattern
established by `granola` — see `granola/references/proton-pass-setup.md` for
the full worked example and exact commands.

- **Reference file, not a bespoke registry.** `<skill>/<skill>.env.example`
  (committed) → `<skill>/<skill>.env` (gitignored), holding
  `ENV_VAR=pass://vault/item/field`. This is `pass-cli run --env-file`'s
  native format — there is no cross-vendor standard for `ENV_VAR → secret
  reference` mappings, and dotenv-with-references is the closest thing to a
  de-facto one (1Password's `op` and Proton Pass's `pass-cli` implement it
  with near-identical flags).
- **Resolve via `pass-cli run`, nothing else.** The launcher
  (`<skill>/scripts/<skill>.sh`) does
  `pass-cli run --env-file <skill>.env -- <command>`. Never `pass-cli item
  view`, `run -- env`, or echo the resolved var — those print the plaintext
  value. Masking on `run` is best-effort (catches literal occurrences in
  streams it controls; doesn't catch encoded/transformed values or anything
  written straight to a file by the client).
- **Vault name = the service, agent name = the skill.** These are not the
  same thing, and it only looks that way when a skill and its service happen
  to share a name (e.g. `granola`/`Granola`). A vault holds a credential for
  an external *service* — multiple skills may legitimately want the same one
  (a `url-to-markdown` skill and a future `web-research` skill could both
  need Firecrawl). So: one dedicated single-item vault per **service**,
  reused across skills that need it; one agent per **skill**, even when two
  skills share a vault — that's what lets you revoke one skill's access
  without touching the other, and gives audit-log separation by cryptographic
  identity rather than a self-reported reason string.
  ```bash
  pass-cli vault create --name "<Service>"
  # store the credential in that vault, then, once per skill that needs it:
  pass-cli agent create <skill>-reader --expiration 1y
  pass-cli agent access grant <skill>-reader \
    --vault-name "<Service>" --role viewer
  ```
  This gives read-only, minimum-privilege access plus an audit trail
  (`pass-cli agent monitor <skill>-reader`). **Item-scoped grants
  (`--item-title`) do not work with `pass-cli run --env-file`**: `pass://`
  resolution requires a named *vault*, and an item-scoped session has zero
  vault visibility (`pass-cli vault list` returns empty under it) — every
  `pass://` form fails with `Could not find vault …`, confirmed against
  `pass-cli`'s own source. A dedicated single-item vault gets the same
  isolation through the officially documented mechanism instead — Proton's
  own guidance is to organize agent credentials in vaults, not to lean on
  item-level shares for automation. Reserve item-level grants for what they're
  actually for: sharing one item from an otherwise-private vault with another
  *person*, consumed via explicit `pass-cli item view --share-id --item-id`,
  never `run`/`inject`.
- **One session directory per skill, via a skill-specific override
  variable — never the bare `PROTON_PASS_SESSION_DIR` name.** Default to
  `~/.pass-sessions/<skill>`, but make it overridable through something like
  `<SKILL>_RUNTIME_SESSION_DIR`, not `${PROTON_PASS_SESSION_DIR:-default}`.
  If the *caller's* environment ever sets `PROTON_PASS_SESSION_DIR`
  unconditionally for unrelated reasons (see the lockdown note below — this
  is exactly what happened), a bare `${PROTON_PASS_SESSION_DIR:-default}`
  fallback silently inherits that instead of falling through to the
  skill's own default, and the runtime session ends up trying to
  authenticate against the wrong directory. This failure mode is
  non-obvious: it looks like a login/token problem, not a "wrong directory"
  problem. Session dirs are otherwise just files on disk read per-invocation;
  skills with their own dir don't interact at all, including when run
  concurrently in the same project.
- **Gate on file existence**, not a config flag: if `<skill>.env` is missing,
  print setup instructions to stderr and exit non-zero (reserve a distinct
  exit code for "not configured" vs. "pass-cli not found" vs. auth failure —
  see `granola/scripts/granola.sh` for the pattern).
- **Self-heal the runtime session, don't make the user babysit it.** Agent/PAT
  sessions expire every ~2h; store the agent token as its own Proton Pass item
  (e.g. `Agents/<Skill> Agent Token`, field `token`) and have the launcher
  check `pass-cli info` against its session dir before running — on failure,
  fetch the token via the caller's *personal* session and `pass-cli login`
  with it, then proceed. This makes routine expiry invisible; only two things
  should ever surface to the user: the personal session also being stale
  (rare — they use it daily), or the token itself hitting its yearly
  expiration (`agent renew` is a manual, deliberate step — don't auto-renew
  on failure, since that would mean an unattended process can rotate its own
  long-lived credential). See `granola/scripts/granola.sh` for the reference
  implementation.
- **If Claude Code's own `pass-cli` access is locked down** (its Bash calls
  repointed to a dead-end `PROTON_PASS_SESSION_DIR` via `settings.json`
  `env`, plus a `PreToolUse` hook denying direct `pass-cli`/`PROTON_PASS_*`
  commands — check `~/.claude/settings.json` and `~/.claude/hooks/` for
  whether this repo's user has set this up), a vetted wrapper script is the
  one thing still trusted to reach the personal session, by design. Two
  things break under that lockdown if not handled explicitly: (1) any
  `pass-cli` call meant to use the *personal* session must pin
  `PROTON_PASS_SESSION_DIR` to a skill-specific override variable
  (e.g. `<SKILL>_PERSONAL_SESSION_DIR`, defaulting to pass-cli's own
  platform default path) rather than relying on "no override" — the ambient
  value is no longer empty, it's the dead end; (2) per the point above, the
  skill's own runtime-session variable must not derive from bare
  `PROTON_PASS_SESSION_DIR` either, for the same reason. Both mistakes were
  made and fixed once already in `granola.sh` — read it before writing a new
  wrapper under an active lockdown.
- **Document setup and renewal in `<skill>/references/proton-pass-setup.md`**,
  written for the human to run themselves. Creating the agent is fine to do on
  the user's behalf if you're already driving a shell with their permission,
  but logging in with a raw PAT/agent token is not — that command carries a
  live secret through the tool-call transcript and should be left to the user
  (Claude Code's own permission classifier will typically block it anyway).
