#!/usr/bin/env python3
"""Granola API client for the granola skill.

Runs as the Mode A consumer: expects GRANOLA_API_KEY in its environment,
injected by `pass-cli run` (see scripts/granola.sh). The key is used only in
the Authorization header and is never echoed.

Subcommands:
  list    [--limit N] [--all] [--after ISO] [--before ISO] [--updated-after ISO]
          [--folder fol_ID] [--cursor CURSOR]
  folders
  show    <not_ID> [--transcript]
  export  <not_ID> [--transcript] [-o PATH]
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://public-api.granola.ai/v1"


def api_get(path, params=None):
    key = os.environ.get("GRANOLA_API_KEY")
    if not key:
        sys.exit("error: GRANOLA_API_KEY not set — run this through scripts/granola.sh, "
                 "which injects it via pass-cli.")
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            sys.exit("error: Granola rejected the API key (401). Check the 'Granola' item "
                     "in the Proton Pass 'Agents' vault holds a valid grn_… key in field 'API Key'.")
        if e.code == 404:
            sys.exit(f"error: not found (404): {path}")
        if e.code == 429:
            sys.exit("error: rate limited (429) — Granola allows 5 requests/second. Retry shortly.")
        sys.exit(f"error: Granola API returned {e.code} for {path}: {e.read().decode(errors='replace')[:300]}")
    except urllib.error.URLError as e:
        sys.exit(f"error: cannot reach Granola API: {e.reason}")


def short_date(iso):
    return (iso or "").replace("T", " ")[:16]


def fmt_user(u):
    if not u:
        return "unknown"
    name, email = u.get("name"), u.get("email")
    return f"{name} <{email}>" if name else (email or "unknown")


def cmd_list(args):
    params = {
        "page_size": None if args.all else args.limit,
        "created_after": args.after,
        "created_before": args.before,
        "updated_after": args.updated_after,
        "folder_id": args.folder,
        "cursor": args.cursor,
    }
    fetched = 0
    while True:
        data = api_get("/notes", params)
        notes = data.get("notes", [])
        for n in notes:
            print(f"{n['id']}  {short_date(n.get('created_at'))}  "
                  f"{n.get('title') or '(untitled)'}  — {fmt_user(n.get('owner'))}")
        fetched += len(notes)
        if args.all and data.get("hasMore") and data.get("cursor"):
            params["cursor"] = data["cursor"]
            continue
        if data.get("hasMore") and data.get("cursor"):
            print(f"\n(more available — continue with: --cursor {data['cursor']})")
        break
    if fetched == 0:
        print("(no notes matched)")


def cmd_folders(_args):
    data = api_get("/folders")
    folders = data.get("folders", [])
    for f in folders:
        parent = f.get("parent_folder_id")
        print(f"{f['id']}  {f.get('name')}" + (f"  (parent: {parent})" if parent else ""))
    if not folders:
        print("(no folders)")


def render_transcript(segments):
    lines, prev = [], None
    for seg in segments:
        sp = seg.get("speaker", {})
        label = (sp.get("name") or sp.get("diarization_label")
                 or {"me": "Me", "them": "Them"}.get(sp.get("attribution"))
                 or sp.get("source", "unknown"))
        clock = (seg.get("start_time") or "")[11:19]
        if label != prev:
            lines.append(f"\n**{label}** [{clock}]")
            prev = label
        lines.append(seg.get("text", ""))
    return "\n".join(lines).strip()


def note_markdown(note, with_transcript):
    title = note.get("title") or "(untitled)"
    ev = note.get("calendar_event") or {}
    date = ev.get("scheduled_start_time") or note.get("created_at")
    attendees = ", ".join(fmt_user(a) for a in note.get("attendees") or []) or "unknown"
    md = [f"# {title}", ""]
    md.append(f"- **Date:** {short_date(date)}")
    if ev.get("organiser"):
        md.append(f"- **Organiser:** {ev['organiser']}")
    md.append(f"- **Attendees:** {attendees}")
    folders = note.get("folder_membership") or []
    if folders:
        md.append(f"- **Folders:** {', '.join(f.get('name', '?') for f in folders)}")
    if note.get("web_url"):
        md.append(f"- **Granola:** {note['web_url']}")
    md += ["", "## Summary", "", note.get("summary_markdown") or note.get("summary_text") or "(no summary)"]
    if with_transcript:
        segments = note.get("transcript") or []
        md += ["", "## Transcript", "",
               render_transcript(segments) if segments else "(no transcript available)"]
    return "\n".join(md) + "\n"


def fetch_note(note_id, with_transcript):
    params = {"include": "transcript"} if with_transcript else None
    return api_get(f"/notes/{note_id}", params)


def cmd_show(args):
    note = fetch_note(args.note_id, args.transcript)
    print(note_markdown(note, args.transcript), end="")


def cmd_export(args):
    note = fetch_note(args.note_id, args.transcript)
    if args.output:
        path = args.output
    else:
        date = ((note.get("calendar_event") or {}).get("scheduled_start_time")
                or note.get("created_at") or "")[:10]
        slug = re.sub(r"[^a-z0-9]+", "-", (note.get("title") or "untitled").lower()).strip("-")[:60]
        path = f"{date}-{slug or 'untitled'}.md"
    with open(path, "w") as fh:
        fh.write(note_markdown(note, args.transcript))
    print(f"exported: {os.path.abspath(path)}")


def main():
    p = argparse.ArgumentParser(prog="granola")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="list notes")
    pl.add_argument("--limit", type=int, default=20)
    pl.add_argument("--all", action="store_true", help="follow pagination to the end")
    pl.add_argument("--after", help="created after (ISO 8601)")
    pl.add_argument("--before", help="created before (ISO 8601)")
    pl.add_argument("--updated-after", help="updated after (ISO 8601)")
    pl.add_argument("--folder", help="folder id (fol_…)")
    pl.add_argument("--cursor", help="pagination cursor from a previous call")
    pl.set_defaults(func=cmd_list)

    pf = sub.add_parser("folders", help="list folders")
    pf.set_defaults(func=cmd_folders)

    ps = sub.add_parser("show", help="show a note (markdown to stdout)")
    ps.add_argument("note_id")
    ps.add_argument("--transcript", action="store_true", help="include the transcript")
    ps.set_defaults(func=cmd_show)

    pe = sub.add_parser("export", help="export a note to a markdown file")
    pe.add_argument("note_id")
    pe.add_argument("--transcript", action="store_true", help="include the transcript")
    pe.add_argument("-o", "--output", help="output path (default: ./<date>-<title>.md)")
    pe.set_defaults(func=cmd_export)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
