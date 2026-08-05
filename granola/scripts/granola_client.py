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
import time
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


def yaml_str(value):
    return json.dumps(value, ensure_ascii=False)


def okf_frontmatter(note, with_transcript):
    """Open Knowledge Format (OKF) v0.2 frontmatter for an exported note."""
    ev = note.get("calendar_event") or {}
    summary = (note.get("summary_markdown") or note.get("summary_text") or "").strip()
    description = ""
    for line in summary.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        description = line.lstrip("-* ").strip()[:200]
        break
    tags = ["meeting", "granola"] + [f.get("name") for f in note.get("folder_membership") or [] if f.get("name")]
    fm = ["---", "type: Meeting Note", f"title: {yaml_str(note.get('title') or '(untitled)')}"]
    if description:
        fm.append(f"description: {yaml_str(description)}")
    if note.get("web_url"):
        fm.append(f"resource: {note['web_url']}")
    fm.append(f"tags: [{', '.join(yaml_str(t) for t in tags)}]")
    if note.get("updated_at"):
        fm.append(f"generated: {{ by: \"machine:granola\", at: {note['updated_at']} }}")
    fm += ["sources:", f"  - resource: {BASE_URL}/notes/{note.get('id')}", "status: stable", "meeting:"]
    date = ev.get("scheduled_start_time") or note.get("created_at")
    if date:
        fm.append(f"  scheduled_at: {date}")
    if ev.get("organiser"):
        fm.append(f"  organiser: {ev['organiser']}")
    attendees = note.get("attendees") or []
    if attendees:
        fm.append("  attendees:")
        fm += [f"    - {yaml_str(fmt_user(a))}" for a in attendees]
    fm += [f"  transcript_included: {'true' if with_transcript else 'false'}", "---", ""]
    return "\n".join(fm)


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


def default_basename(note):
    date = ((note.get("calendar_event") or {}).get("scheduled_start_time")
            or note.get("created_at") or "")[:10]
    slug = re.sub(r"[^a-z0-9]+", "-", (note.get("title") or "untitled").lower()).strip("-")[:60]
    return f"{date}-{slug or 'untitled'}"


def write_note(path, note, with_transcript):
    with open(path, "w") as fh:
        fh.write(okf_frontmatter(note, with_transcript))
        fh.write(note_markdown(note, with_transcript))


def cmd_export(args):
    note = fetch_note(args.note_id, args.transcript)
    path = args.output or f"{default_basename(note)}.md"
    write_note(path, note, args.transcript)
    print(f"exported: {os.path.abspath(path)}")


def cmd_sync(args):
    """Incrementally mirror notes into a directory. Never deletes local files."""
    target = os.path.abspath(args.dir)
    os.makedirs(target, exist_ok=True)
    state_path = os.path.join(target, ".granola-sync.json")
    state = {"last_sync": None, "notes": {}}
    if os.path.exists(state_path):
        try:
            with open(state_path) as fh:
                state = json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            sys.exit(f"error: cannot read sync state {state_path} ({e}). "
                     "Fix or remove it; local files are never deleted.")

    full = args.full or not state.get("last_sync")
    params = {"folder_id": args.folder, "page_size": 30}
    if not full:
        params["updated_after"] = state["last_sync"]

    listed, cursor = [], None
    while True:
        page = api_get("/notes", {**params, "cursor": cursor})
        listed += page.get("notes", [])
        cursor = page.get("cursor")
        if not (page.get("hasMore") and cursor):
            break
        time.sleep(0.25)

    new = updated = unchanged = 0
    last_sync = state.get("last_sync") or ""
    for summary in listed:
        nid, upd = summary["id"], summary.get("updated_at") or ""
        last_sync = max(last_sync, upd)
        entry = state["notes"].get(nid)
        if entry and entry.get("updated_at") == upd:
            unchanged += 1
            continue
        note = fetch_note(nid, args.transcripts)
        if entry:
            fname = entry["file"]
        else:
            base = default_basename(note)
            fname, i = f"{base}.md", 2
            managed = {e["file"] for e in state["notes"].values()}
            while fname in managed or os.path.exists(os.path.join(target, fname)):
                fname, i = f"{base}-{i}.md", i + 1
        write_note(os.path.join(target, fname), note, args.transcripts)
        state["notes"][nid] = {"file": fname, "updated_at": upd, "title": summary.get("title")}
        new, updated = new + (not entry), updated + bool(entry)
        time.sleep(0.25)

    gone = []
    if full:
        listed_ids = {n["id"] for n in listed}
        gone = sorted((e["file"], nid) for nid, e in state["notes"].items() if nid not in listed_ids)

    if last_sync:
        state["last_sync"] = last_sync
    tmp = state_path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh, indent=1)
    os.replace(tmp, state_path)

    mode = "full" if full else "incremental"
    print(f"synced {target} ({mode}): {new} new, {updated} updated, {unchanged} unchanged")
    for fname, nid in gone:
        print(f"  kept local file whose note is gone upstream: {fname} ({nid})")


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

    py = sub.add_parser("sync", help="incrementally mirror notes into a directory (never deletes)")
    py.add_argument("--dir", required=True, help="target directory for the mirrored notes")
    py.add_argument("--transcripts", action="store_true", help="include transcripts in synced files")
    py.add_argument("--folder", help="only sync notes in this folder (fol_…)")
    py.add_argument("--full", action="store_true",
                    help="re-check every note instead of only changes since last sync")
    py.set_defaults(func=cmd_sync)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
