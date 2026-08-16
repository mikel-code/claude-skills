---
name: granola
description: "Access Granola meeting notes through the Granola API with a leak-resistant Proton Pass key (pass-cli, dedicated-vault agent). List meeting notes, show a note's summary, attendees, and optionally its transcript, and export notes to Markdown files. Use for /granola, \"list my granola meetings/notes\", \"show the notes/summary/transcript of that meeting\", or \"export that meeting to markdown\"."
license: MIT
compatibility: Requires the Proton Pass CLI (pass-cli), Python 3, and network access to public-api.granola.ai
---

# Granola

Read meeting notes from the [Granola API](https://docs.granola.ai) without the
API key ever entering your context. All API access goes through
`scripts/granola.sh`, which reads a `pass://` reference from `granola.env` and
resolves it via `pass-cli run`, which injects the real key into the client
process only.

The reference lives in `granola.env`, the native dotenv format `pass-cli run
--env-file` consumes directly — not `secrets.toml`. This skill intentionally
diverges from the TOML-registry pattern used elsewhere (e.g. the second-brain
`.tooling/secrets.toml`): there is no cross-vendor standard for `ENV_VAR →
secret-reference` mappings, and dotenv-with-references is the closest thing to
a de-facto one (it's what `pass-cli` and 1Password's `op` both implement,
flag-for-flag identical `--env-file` semantics).

## Security rules — non-negotiable

- **Never** read, print, or export the API key: no `pass-cli item view`, no
  `pass-cli run -- env`, no `echo $GRANOLA_API_KEY`, no copying it into files.
- **Only** access the API through `scripts/granola.sh`. Do not hand-craft
  `curl` calls to `public-api.granola.ai`.
- Never wrap your own process (or any long-lived process) in `pass-cli run` —
  one launcher invocation per command, carrying only this key.
- `pass-cli run`'s output masking is best-effort: it only catches literal
  occurrences of the resolved value in streams it controls. An encoded/
  transformed key, or anything the client writes straight to a file, is not
  covered — don't rely on masking as a guarantee.
- Direct `pass-cli` invocation from a Claude Code session may be hook-blocked
  at the harness level (session repointed to a dead end, plus a `PreToolUse`
  deny on any `pass-cli`/`PROTON_PASS_` command) — this reinforces, not
  replaces, the "only through `scripts/granola.sh`" rule above. If it fires,
  it means something tried to reach `pass-cli` directly; route through the
  script instead of working around the block.

## Commands

Run everything as `<skill-dir>/scripts/granola.sh <subcommand>`:

```
granola.sh list [--limit N] [--all] [--after ISO] [--before ISO]
                [--updated-after ISO] [--folder fol_ID] [--cursor CURSOR]
granola.sh folders
granola.sh show <not_ID> [--transcript]
granola.sh export <not_ID> [--transcript] [-o PATH]
granola.sh sync --dir PATH [--transcripts] [--folder fol_ID] [--full]
```

- `list` prints one note per line: `not_ID  created-at  title — owner`. Default
  page size 20; if more exist it prints the `--cursor` to continue with, or use
  `--all` to follow pagination to the end.
- `show` prints the note as Markdown to stdout: title, date, organiser,
  attendees, folders, Granola web link, and the AI summary. Add `--transcript`
  to append the full transcript (speaker-attributed, timestamped). Only fetch
  the transcript when the user asks for it — transcripts are large.
- `export` writes that same Markdown to a file (default
  `./<date>-<title-slug>.md` in the current directory) and prints the absolute
  path. Use `-o` when the user names a destination. Exported files start with
  an [Open Knowledge Format (OKF) v0.2](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
  YAML frontmatter block (type/title/description/resource/tags/generated/sources
  plus a `meeting:` extension with attendees and organiser).

- `sync` incrementally mirrors notes into `--dir` as OKF-frontmatter Markdown
  files: first run (or `--full`) fetches everything; later runs fetch only
  notes changed since the last sync (tracked in `<dir>/.granola-sync.json`,
  which also pins each note id to its filename — don't edit or delete it).
  **Sync never deletes local files**: notes removed upstream are reported as
  "kept local file" (only detectable on `--full` runs) and left in place; it
  also never overwrites files it doesn't manage. Use this from projects that
  keep a meeting-notes mirror — point the project's CLAUDE.md at
  `granola.sh sync --dir <notes-dir>` and treat the mirrored files as
  read-only source material.

Typical flows: "what meetings did I have this week" → `list --after <monday>`;
"show me the notes from the X meeting" → `list` to find the id, then `show`;
"export it with the transcript" → `export <id> --transcript`.

Rate limit is 5 requests/second — fine for normal use; avoid tight loops over
`show`.

## Setup (one-time, human present)

The service ships unconfigured. `granola.sh` prints a short version of these
steps itself when `granola.env` is missing; full commands, including creating
and renewing the Proton Pass agent token, are in
[references/proton-pass-setup.md](references/proton-pass-setup.md) — **the
user must run all of it themselves**, never on the agent's behalf, since it
handles a raw credential.

1. In Granola (web app → Settings → API keys), create an API key (`grn_…`).
2. In Proton Pass, create a dedicated **Granola** vault and store the key
   there as item **Granola**, custom field named exactly **API Key**
   (field names are case-sensitive; a dedicated vault is required, not just
   an item-level grant — see the runbook for why).
3. Create the `granola-reader` agent, vault-scoped to that dedicated vault —
   see the runbook for exact commands.
4. Copy `granola.env.example` to `granola.env` in this skill directory (the
   reference is already correct; nothing to edit).

## Troubleshooting

- **"not configured" / `granola.env` missing** → setup above not completed;
  walk the user through it.
- **"runtime session expired and the stored agent token could not be
  fetched"** (exit 3) → `granola.sh` self-heals the ~2h runtime session
  automatically on every invocation; this only surfaces when that self-heal
  itself failed — either the user's personal Proton Pass session is stale
  (ask them to run `pass-cli login` normally) or the token was never stored
  (point them at the runbook's "Store the token" step). Never attempt the
  login for them — it requires a token only they hold.
- **"found the stored agent token but could not log the runtime session in"**
  (exit 3) → the agent token itself expired (yearly, not the ~2h session).
  Point the user at the runbook's "Token expired" section; never attempt the
  renewal for them.
- **401 from Granola** → key missing/revoked in the Proton Pass item; ask the
  user to check item `Granola`, field `API Key`, in the dedicated `Granola`
  vault.
- **pass-cli not found** → install Proton Pass CLI, or set `PASS_CLI` to its
  absolute path (cron/scheduled runs have minimal PATH).
