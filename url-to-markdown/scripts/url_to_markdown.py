#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "trafilatura==2.2.0",
# ]
# ///
"""url-to-markdown ladder orchestrator.

Wires the tiers defined in references/design.md:
probe -> static -> chrome -> jina -> firecrawl, with the sanity gate between
every rung. Tier 5 (claude-in-chrome) is not code: when every automatic tier
fails, this exits EXIT_UNRESOLVED with the full attempt provenance and
SKILL.md's browser workflow takes over. A challenge/consent/shell page is
never emitted as if it were content.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from contract import (  # noqa: E402
    EXIT_CLEAN,
    EXIT_ERROR,
    EXIT_REFUSED_FILE,
    EXIT_UNRESOLVED,
    GateVerdict,
    ProbeKind,
    Tier,
    TierAttempt,
    TierError,
    TierOutput,
)
from deps import check_deps  # noqa: E402
from gate import evaluate  # noqa: E402
from okf import okf_frontmatter, slugify  # noqa: E402
from probe import probe  # noqa: E402
from tier_chrome import fetch_chrome  # noqa: E402
from tier_firecrawl import fetch_firecrawl  # noqa: E402
from tier_jina import fetch_jina  # noqa: E402
from tier_static import fetch_static  # noqa: E402

# Gate verdicts that local rendering cannot fix: once the static tier sees
# these, Tier 2 (chrome) is pointless — jump straight to the remote tiers.
_SKIP_CHROME_ON = {GateVerdict.HTTP_BLOCKED, GateVerdict.CHALLENGE}

_FETCHERS = {
    Tier.STATIC: fetch_static,
    Tier.CHROME: fetch_chrome,
    Tier.JINA: fetch_jina,
    Tier.FIRECRAWL: fetch_firecrawl,
}
_LADDER = [Tier.STATIC, Tier.CHROME, Tier.JINA, Tier.FIRECRAWL]
_REMOTE = {Tier.JINA, Tier.FIRECRAWL}


def _has_credentials(url: str) -> bool:
    parts = urlsplit(url)
    return bool(parts.username or parts.password)


def run_ladder(url: str, only_tier: Tier | None
               ) -> tuple[TierOutput | None, list[TierAttempt]]:
    attempts: list[TierAttempt] = []
    ladder = [only_tier] if only_tier else list(_LADDER)
    skip_chrome = False
    skip_chrome_why = ""
    stop_escalation = False
    creds = _has_credentials(url)
    # Remote readers fetch the URL themselves: a non-http(s) URL (file://,
    # data:) can never succeed there, and sending a local filesystem path to
    # a third party leaks it for zero benefit.
    non_http = urlsplit(url).scheme not in ("http", "https")

    for tier in ladder:
        if stop_escalation:
            attempts.append(TierAttempt(tier, GateVerdict.SKIPPED,
                                        "login wall needs the real browser "
                                        "(tier 5), not another proxy"))
            continue
        if tier is Tier.CHROME and skip_chrome:
            attempts.append(TierAttempt(tier, GateVerdict.SKIPPED,
                                        skip_chrome_why))
            continue
        if tier in _REMOTE and (creds or non_http):
            attempts.append(TierAttempt(
                tier, GateVerdict.SKIPPED,
                "credentialed URL not sent to third-party services" if creds
                else "non-http URL is local; remote readers can't fetch it"))
            continue

        try:
            out = _FETCHERS[tier](url)
        except TierError as e:
            attempts.append(TierAttempt(tier, e.verdict, e.detail))
            continue

        gate = evaluate(out)
        attempts.append(TierAttempt(tier, gate.verdict, gate.detail))
        if gate.verdict is GateVerdict.OK:
            return out, attempts
        if gate.verdict is GateVerdict.LOGIN_WALL:
            stop_escalation = True
        if tier is Tier.STATIC and gate.verdict in _SKIP_CHROME_ON:
            skip_chrome = True
            skip_chrome_why = (f"static tier verdict was "
                               f"{gate.verdict.value} — local rendering "
                               f"won't get past it")

    return None, attempts


def _attempts_json(attempts: list[TierAttempt]) -> list[dict]:
    return [{"tier": a.tier.value, "verdict": a.verdict.value,
             "detail": a.detail} for a in attempts]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Scrape a URL into OKF-frontmatter markdown through a "
                    "cheap-and-private-first tier ladder (see "
                    "references/design.md).")
    ap.add_argument("url")
    out_group = ap.add_mutually_exclusive_group()
    out_group.add_argument("-o", "--output", type=Path, default=None,
                           help="output .md path (default: <slug>.md in cwd)")
    out_group.add_argument("--output-dir", type=Path, default=None)
    ap.add_argument("--tier", choices=[t.value for t in _LADDER],
                    default=None, help="force a single tier (debugging)")
    ap.add_argument("--json", action="store_true",
                    help="print a machine-readable result to stdout")
    args = ap.parse_args()

    check_deps()

    # Tier 0: file URLs are converted by the sibling skills, not scraped.
    download_dir = tempfile.mkdtemp(prefix="url2md-")
    pr = probe(args.url, download_dir)
    if pr.kind in (ProbeKind.PDF, ProbeKind.OFFICE):
        skill = ("pdf-to-markdown" if pr.kind is ProbeKind.PDF
                 else "doc-to-markdown")
        print(f"url-to-markdown: this URL is a {pr.kind.value} file — use "
              f"the {skill} skill on the downloaded copy:\n"
              f"  {pr.downloaded_path}", file=sys.stderr)
        if args.json:
            print(json.dumps({"refused": pr.kind.value,
                              "downloaded_path": pr.downloaded_path,
                              "handoff_skill": skill}))
        return EXIT_REFUSED_FILE
    if pr.kind is ProbeKind.OTHER:
        print(f"url-to-markdown: unsupported content type "
              f"({pr.content_type or 'unknown binary'}) — nothing to "
              f"convert to markdown.", file=sys.stderr)
        if args.json:
            print(json.dumps({"refused": "other",
                              "content_type": pr.content_type,
                              "handoff_skill": None}))
        return EXIT_REFUSED_FILE

    only = Tier(args.tier) if args.tier else None
    out, attempts = run_ladder(pr.final_url or args.url, only)

    if out is None:
        print("url-to-markdown: no automatic tier produced trustworthy "
              "content:", file=sys.stderr)
        for a in attempts:
            print(f"  {a.tier.value:<10} {a.verdict.value:<15} {a.detail}",
                  file=sys.stderr)
        print("\nNext step: the tier-5 workflow (claude-in-chrome, the "
              "user's real browser) — see SKILL.md.", file=sys.stderr)
        if args.json:
            print(json.dumps({"success": False,
                              "attempts": _attempts_json(attempts)},
                             indent=2))
        return EXIT_UNRESOLVED

    out_path = args.output
    if out_path is None:
        out_dir = args.output_dir or Path.cwd()
        out_path = out_dir / (slugify(out.title, args.url) + ".md")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    front = okf_frontmatter(url=args.url, out=out, attempts=attempts,
                            fetched_url=pr.final_url)
    out_path.write_text(front + "\n" + out.markdown.strip() + "\n",
                        encoding="utf-8")

    for a in attempts:
        print(f"  {a.tier.value:<10} {a.verdict.value}", file=sys.stderr)
    print(f"wrote {out_path.resolve()} (tier: {out.tier.value})",
          file=sys.stderr)
    if args.json:
        print(json.dumps({"success": True, "output": str(out_path.resolve()),
                          "tier": out.tier.value,
                          "generated_at":
                              datetime.now(timezone.utc).isoformat(),
                          "attempts": _attempts_json(attempts)}, indent=2))
    else:
        print(out_path.resolve())
    return EXIT_CLEAN


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(EXIT_ERROR)
