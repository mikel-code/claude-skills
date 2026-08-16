"""Open Knowledge Format (OKF) v0.2 frontmatter for url-to-markdown output.

Conventions copied from this repo's existing OKF emitters — granola/scripts/
granola_client.py (okf_frontmatter), yt-transcript/scripts/yt_transcript.py
(okf_frontmatter), and pdf-to-markdown/scripts/lib/okf.py (closest
precedent: same yaml_str/json.dumps quoting helper, generated.by
"machine:<skill>/<variant>" pattern, a custom extension block for
pipeline-specific provenance). Hand-formatted YAML, stdlib only — no pyyaml
dependency, so string scalars that need quoting are JSON-encoded (valid
YAML) and everything else is emitted bare, matching the sibling scripts'
style exactly.

Convention choices made for this skill (flagged here since there wasn't a
single unambiguous precedent to copy):

  - `type: Document` — pdf-to-markdown and doc-to-markdown both use
    "Document" for arbitrary converted text content (yt-transcript uses
    "Video Transcript", granola uses "Meeting Note" — both are for a
    specific, narrower content shape). A scraped web page is the same kind
    of thing pdf-to-markdown/doc-to-markdown produce — a faithful document,
    not necessarily an "article" — so "Document" was picked for consistency
    with those two rather than inventing "Web Article" for this skill alone.

  - author/published date live INSIDE the `conversion:` custom block, not
    as top-level frontmatter keys. No sibling script puts author/date at
    top level: yt-transcript nests `channel`/`published` inside its own
    `video:` custom block, granola nests `organiser` inside `meeting:`.
    Following that precedent here keeps a single custom block
    (`conversion:`) carrying everything pipeline- and provenance-specific
    (tier, author, published, the full ladder) rather than splitting
    author/date across a top-level key and the custom block.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from contract import TierAttempt, TierOutput


def yaml_str(value: str) -> str:
    """JSON-encode a string for use as a YAML scalar — same helper name and
    behavior as granola_client.py / yt_transcript.py / pdf-to-markdown's
    okf.py."""
    return json.dumps(value, ensure_ascii=False)


def _yaml_scalar(value: str) -> str:
    """Bare YAML scalar when safe, else quoted. Sibling scripts always treat
    `resource:` as a URL and emit it bare (URLs have no colon-space, no
    leading indicator chars) — mirrored here, but quote defensively whenever
    the bare form would be ambiguous, same rule as pdf-to-markdown's okf.py."""
    if (
        value == ""
        or value != value.strip()
        or value[0] in "!&*-?|>%@`\"'#[]{},:"
        or ": " in value
        or value.endswith(":")
        or value.lower() in {"null", "~", "true", "false", "yes", "no"}
    ):
        return yaml_str(value)
    return value


# ------------------------------------------------------------------ slugify

def _host(url: str) -> str:
    return urlparse(url).netloc


def _last_path_segment(url: str) -> str:
    """Last non-empty URL path segment, extension stripped (so
    "/blog/my-post.html" yields "my-post", not "my-post.html")."""
    path = urlparse(url).path.rstrip("/")
    segment = path.rsplit("/", 1)[-1] if path else ""
    if not segment:
        return ""
    stem = os.path.splitext(segment)[0]
    return stem or segment


def slugify(title: str | None, url: str) -> str:
    """title if present, else the last meaningful URL path segment, else the
    host; lowercased, alnum+hyphens only, trimmed to ~60 chars — same shape
    as yt_transcript.py's slugify, generalized to take a URL fallback chain
    instead of always requiring a title."""
    source = title or _last_path_segment(url) or _host(url) or "page"
    slug = source.lower()
    # Keep dots/underscores through the strip pass (unlike yt_transcript.py's
    # slugify, which only ever sees prose titles) so a host fallback like
    # "example.com" separates into "example-com" instead of collapsing to
    # "examplecom" — dots are a meaningful word boundary in hostnames.
    slug = re.sub(r"[^a-z0-9\s._-]", "", slug)
    slug = re.sub(r"[\s._]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug[:60].rstrip("-") or "page"


def _fallback_title(url: str) -> str:
    """Human-readable title fallback when a tier didn't supply one: the last
    path segment turned into words (hyphens/underscores -> spaces, title
    -cased), else the host, else the raw URL."""
    seg = _last_path_segment(url)
    if seg:
        words = re.sub(r"[-_]+", " ", seg).strip()
        if words:
            return words.title()
    return _host(url) or url


# ------------------------------------------------------------- description

def _derive_description(markdown: str, max_len: int = 200) -> str:
    """First non-heading, non-empty line of the extracted markdown,
    truncated — the same "first real line of content" heuristic
    granola_client.py uses on summary_markdown and pdf-to-markdown's okf.py
    uses across page records (strip heading/list markers, slice to 200
    chars)."""
    for line in (markdown or "").splitlines():
        line = line.strip()
        # Skip non-prose lines: headings, table rows/rules, images, code
        # fences — a description of "| Part of [a series] on |" helps nobody.
        if (not line or line.startswith(("#", "|", "```", "![", "+--"))
                or set(line) <= set("-| :")):
            continue
        return line.lstrip("-*> ").strip()[:max_len]
    return ""


def _plausible_author(author: str | None) -> str | None:
    """Extractor author fields are heuristic and sometimes come back as
    page-chrome garbage (e.g. Wikipedia's 'Authority control databases …').
    A real byline is short; drop anything that plainly isn't one."""
    if not author:
        return None
    author = author.strip()
    if len(author) > 60 or "\n" in author:
        return None
    return author


# ------------------------------------------------------------- frontmatter

def okf_frontmatter(*, url: str, out: TierOutput, attempts: list[TierAttempt],
                    fetched_url: str | None = None) -> str:
    """Return the complete YAML frontmatter block (--- ... ---) for a
    converted URL, OKF v0.2, following this repo's existing conventions:

      - type / title / description / resource / tags / generated.by /
        generated.at / sources / status, in that order (granola_client.py,
        yt_transcript.py, pdf-to-markdown's okf.py).
      - title falls back through out.title -> slug-source chain (see
        _fallback_title) when the winning tier didn't supply one.
      - description falls back to the first real content line when the tier
        didn't supply one — same as granola's summary-derived description
        and pdf-to-markdown's page-record-derived description.
      - generated.by is "machine:<tool>[/<variant>]" — here
        "machine:url-to-markdown/<tier-value>" (yt-transcript:
        "machine:youtube-captions" / "machine:mlx-whisper/<model>";
        pdf-to-markdown: "machine:pdf-to-markdown/<route-value>").
      - generated.at is UTC "%Y-%m-%dT%H:%M:%SZ" (all three precedents).
      - sources is a list of {resource: ...} (all three precedents).
      - `conversion:` is the custom extension block (mirrors granola's
        `meeting:` / yt-transcript's `video:`), carrying: the winning tier,
        author/published date when the tier supplied them (see module
        docstring for why they live here and not at top level), and the
        full tier ladder as a list of {tier, verdict, detail} — the
        no-silent-failure provenance record: every tier the orchestrator
        attempted or explicitly skipped (e.g. Firecrawl with
        GateVerdict.NOT_CONFIGURED) appears here, not just the winner.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    title = out.title or _fallback_title(url)
    description = out.description or _derive_description(out.markdown)
    host = _host(url) or "unknown"

    lines = [
        "---",
        "type: Document",
        f"title: {yaml_str(title)}",
    ]
    if description:
        lines.append(f"description: {yaml_str(description)}")
    lines.append(f"resource: {_yaml_scalar(url)}")
    lines.append(f'tags: [{yaml_str("web")}, {yaml_str(host)}, {yaml_str(out.tier.value)}]')
    lines += [
        "generated:",
        f"  by: {yaml_str(f'machine:url-to-markdown/{out.tier.value}')}",
        f"  at: {now}",
        "sources:",
        f"  - resource: {_yaml_scalar(url)}",
        "status: stable",
        "conversion:",
        f"  tier: {yaml_str(out.tier.value)}",
    ]
    # resource: stays the URL the user asked for; when redirects landed the
    # fetch somewhere else, the effective URL is provenance worth keeping.
    if fetched_url and fetched_url != url:
        lines.append(f"  fetched: {yaml_str(fetched_url)}")
    author = _plausible_author(out.author)
    if author:
        lines.append(f"  author: {yaml_str(author)}")
    if out.date:
        lines.append(f"  published: {yaml_str(out.date)}")

    lines.append("  ladder:")
    for attempt in attempts:
        lines.append(f"    - tier: {yaml_str(attempt.tier.value)}")
        lines.append(f"      verdict: {yaml_str(attempt.verdict.value)}")
        lines.append(f"      detail: {yaml_str(attempt.detail)}")

    lines.append("---")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------- self-test

if __name__ == "__main__":
    import sys

    from contract import GateVerdict, Tier

    failures: list[str] = []

    def check(label: str, cond: bool, extra: str = "") -> None:
        status = "ok" if cond else "FAIL"
        print(f"  [{status}] {label}" + (f" — {extra}" if extra and not cond else ""))
        if not cond:
            failures.append(label)

    def _check_block(block: str, *, must_contain: list[str]) -> None:
        check("starts with '---'", block.startswith("---\n"))
        check("ends with '---'", block.rstrip("\n").endswith("---"))
        for key in must_contain:
            check(f"contains {key!r}", key in block)
        # Every bare-scalar resource: line must not smuggle an unquoted
        # colon-space (which would break YAML parsing).
        for line in block.splitlines():
            if line.startswith("resource:") or line.strip().startswith("- resource:"):
                value = line.split("resource:", 1)[1].strip()
                if not value.startswith('"'):
                    check(f"no unquoted colon-space in {line!r}", ": " not in value)

    print("=== full ladder: static challenge, chrome challenge, "
          "firecrawl not-configured, jina ok ===")
    attempts = [
        TierAttempt(tier=Tier.STATIC, verdict=GateVerdict.CHALLENGE,
                    detail="Cloudflare 'Just a moment' marker"),
        TierAttempt(tier=Tier.CHROME, verdict=GateVerdict.CHALLENGE,
                    detail="same challenge markers after rendering"),
        TierAttempt(tier=Tier.FIRECRAWL, verdict=GateVerdict.NOT_CONFIGURED,
                    detail="no FIRECRAWL_API_KEY — skill.env not configured"),
        TierAttempt(tier=Tier.JINA, verdict=GateVerdict.OK,
                    detail="clean extraction, no gate markers"),
    ]
    out = TierOutput(
        tier=Tier.JINA,
        markdown="# Heading\n\nFirst real paragraph of the article body, long "
                 "enough to be a plausible description candidate.",
        title="A Title: With a Colon",
        author="Jane Doe",
        date="2026-08-01",
        description=None,
        http_status=200,
        raw_text_len=1200,
        page_title="A Title: With a Colon",
    )
    block = okf_frontmatter(url="https://example.com/blog/my-post", out=out, attempts=attempts)
    print(block)
    _check_block(block, must_contain=[
        "type: Document", "title:", "resource:", "generated:", "  by:", "  at:",
        "sources:", "status: stable", "conversion:", "  tier:", "  ladder:",
    ])
    check("title quoting handles embedded colon", yaml_str("A Title: With a Colon") in block)
    check("machine:url-to-markdown/jina in generated.by",
          'machine:url-to-markdown/jina' in block)
    check("author present in conversion: block", '  author: "Jane Doe"' in block)
    check("published present in conversion: block", '  published: "2026-08-01"' in block)
    for attempt in attempts:
        check(f"ladder entry for {attempt.tier.value}", yaml_str(attempt.tier.value) in block)
        check(f"verdict for {attempt.tier.value}: {attempt.verdict.value}",
              yaml_str(attempt.verdict.value) in block)
    check("NOT_CONFIGURED skip recorded", '"not-configured"' in block)
    check("CHALLENGE fail recorded", '"challenge"' in block)
    check("static and chrome both recorded (not just the winner)",
          block.count('"static"') >= 1 and block.count('"chrome"') >= 1)

    print("\n=== minimal: no author/date/description, empty ladder-detail ===")
    out2 = TierOutput(
        tier=Tier.STATIC, markdown="", title=None, author=None, date=None,
        description=None, http_status=200, raw_text_len=0, page_title=None,
    )
    block2 = okf_frontmatter(url="https://example.org/", out=out2, attempts=[])
    print(block2)
    _check_block(block2, must_contain=["type: Document", "title:", "conversion:", "  ladder:"])
    check("title falls back to host when no path segment/title",
          "example.org" in block2)
    check("no stray 'author:' key when out.author is None",
          "  author:" not in block2)
    check("no stray 'published:' key when out.date is None",
          "  published:" not in block2)

    print("\n=== slugify ===")
    check("slugify prefers title", slugify("Hello, World!", "https://x.com/a") == "hello-world")
    check("slugify falls back to last path segment",
          slugify(None, "https://example.com/blog/my-great-post.html") == "my-great-post")
    check("slugify falls back to host when no path",
          slugify(None, "https://example.com/") == "example-com")
    check("slugify trims to 60 chars",
          len(slugify("x" * 200, "https://example.com/")) <= 60)

    print()
    if failures:
        print(f"{len(failures)} failure(s):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("all okf.py self-tests passed")
