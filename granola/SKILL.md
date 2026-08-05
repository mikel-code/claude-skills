---
name: granola
description: "Access Granola meeting notes through the Granola API with a leak-resistant Proton Pass key (pass-cli Mode A). List meeting notes, show a note's summary, attendees, and optionally its transcript, and export notes to Markdown files. Use for /granola, \"list my granola meetings/notes\", \"show the notes/summary/transcript of that meeting\", or \"export that meeting to markdown\"."
license: MIT
compatibility: Requires the Proton Pass CLI (pass-cli), Python 3, and network access to public-api.granola.ai
---

# Granola

Read meeting notes from the [Granola API](https://docs.granola.ai) without the
API key ever entering your context. All API access goes through
`scripts/granola.sh`, which resolves the key from Proton Pass at spawn time and
injects it only into the client process (Mode A).

## Security rules — non-negotiable

- **Never** read, print, or export the API key: no `pass-cli item view`, no
  `pass-cli run -- env`, no `echo $GRANOLA_API_KEY`, no copying it into files.
- **Only** access the API through `scripts/granola.sh`. Do not hand-craft
  `curl` calls to `public-api.granola.ai`.
- Never wrap your own process (or any long-lived process) in `pass-cli run` —
  one launcher invocation per command, carrying only this key.

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

The service ships disabled. `granola.sh` prints these steps itself when run
while disabled:

1. In Granola (web app → Settings → API keys), create an API key (`grn_…`).
2. In Proton Pass, store it in the **Agents** vault as item **Granola**, custom
   field named exactly **API Key** (field names are case-sensitive).
3. Bootstrap the dedicated agent session (the user must run this themselves,
   with a service/agent token scoped to the Agents vault):
   `PROTON_PASS_SESSION_DIR="$HOME/.pass-sessions/claude-code" pass-cli login`
4. Set `enabled = true` in this skill's `secrets.toml`.

## Troubleshooting

- **"service is disabled"** → setup above not completed; walk the user through it.
- **"No active session" / pass-cli auth errors** → the Proton session expired.
  Ask the user to re-run step 3 themselves (it is interactive); never attempt
  the login for them.
- **401 from Granola** → key missing/revoked in the Proton Pass item; ask the
  user to check item `Granola`, field `API Key`, in the Agents vault.
- **pass-cli not found** → install Proton Pass CLI, or set `PASS_CLI` to its
  absolute path (cron/scheduled runs have minimal PATH).
