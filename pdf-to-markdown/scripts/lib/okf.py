"""Open Knowledge Format (OKF) v0.2 frontmatter for pdf-to-markdown output.

Conventions copied from this repo's existing OKF emitters — granola/scripts/
granola_client.py (okf_frontmatter) and yt-transcript/scripts/yt_transcript.py
(okf_frontmatter) — see the self-heal note in okf_frontmatter's docstring for
the specific choices carried over. Hand-formatted YAML, stdlib only: no
pyyaml dependency, so string scalars that need quoting are JSON-encoded
(valid YAML) and everything else is emitted bare, matching the sibling
scripts' style exactly.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from contract import DocRoute, PageRecord, PageStatus


def yaml_str(value: str) -> str:
    """JSON-encode a string for use as a YAML scalar — same helper name and
    behavior as granola_client.py / yt_transcript.py."""
    return json.dumps(value, ensure_ascii=False)


def _yaml_scalar(value: str) -> str:
    """Bare YAML scalar when safe, else quoted. The sibling scripts always
    treat their `resource:` value as a URL and emit it bare (URLs are safe:
    no colon-space, no leading indicator chars). A PDF source path is not
    guaranteed as clean — quote whenever the bare form would be ambiguous."""
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


def _title_from_path(source_path: str) -> str:
    return os.path.splitext(os.path.basename(source_path))[0]


def _derive_description(records: list[PageRecord], max_len: int = 200) -> str:
    """First non-heading, non-empty markdown line across the page records in
    order, truncated — the same "first real line of content" heuristic
    granola_client.py uses on summary_markdown (strip heading/list markers,
    slice to 200 chars)."""
    for record in sorted(records, key=lambda r: r.page):
        if not record.markdown:
            continue
        for line in record.markdown.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            return line.lstrip("-*> ").strip()[:max_len]
    return ""


def okf_frontmatter(*, title: str | None, source_path: str,
                     records: list[PageRecord], route: DocRoute) -> str:
    """Return the complete YAML frontmatter block (--- ... ---) for a
    converted PDF, OKF v0.2, following this repo's existing conventions:

      - type / title / description / resource / tags / generated.by /
        generated.at / sources / status, in that order (granola_client.py,
        yt_transcript.py).
      - title falls back to the source filename stem when not supplied.
      - description is derived (best-effort, empty is fine) rather than
        required — same as granola's summary-derived description.
      - generated.by is "machine:<tool>[/<variant>]" (yt-transcript uses
        "machine:youtube-captions" / "machine:mlx-whisper/<model>"); here
        that's "machine:pdf-to-markdown/<route-value>".
      - generated.at is UTC "%Y-%m-%dT%H:%M:%SZ" (yt_transcript.py).
      - sources is a list of {resource: ...} (both sibling scripts).
      - a custom block — here `conversion:` — carries pipeline-specific
        provenance, mirroring granola's `meeting:` / yt-transcript's
        `video:` custom extension blocks: per-status page counts plus the
        explicit list of vision-produced and flagged-unresolved page
        numbers, so a reader can tell exactly which pages were machine-
        patched or still need a human without re-running the pipeline.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    resolved_title = title or _title_from_path(source_path)
    description = _derive_description(records)

    lines = [
        "---",
        "type: Document",
        f"title: {yaml_str(resolved_title)}",
    ]
    if description:
        lines.append(f"description: {yaml_str(description)}")
    lines.append(f"resource: {_yaml_scalar(source_path)}")
    lines.append('tags: ["document", "pdf", %s]' % yaml_str(route.value))
    lines += [
        "generated:",
        f"  by: {yaml_str(f'machine:pdf-to-markdown/{route.value}')}",
        f"  at: {now}",
        "sources:",
        f"  - resource: {_yaml_scalar(source_path)}",
        "status: stable",
        "conversion:",
        f"  route: {yaml_str(route.value)}",
        f"  pages: {len(records)}",
    ]

    ordered = sorted(records, key=lambda r: r.page)
    by_status: dict[str, int] = {status.value: 0 for status in PageStatus}
    for r in ordered:
        by_status[r.status.value] += 1
    for status in PageStatus:
        lines.append(f"  {status.value.replace('-', '_')}: {by_status[status.value]}")

    vision_pages = [r.page for r in ordered if r.status == PageStatus.VISION]
    flagged_unresolved_pages = [r.page for r in ordered if r.status == PageStatus.FLAGGED_UNRESOLVED]
    lines.append(f"  vision_pages: [{', '.join(str(p) for p in vision_pages)}]")
    lines.append(
        f"  flagged_unresolved_pages: [{', '.join(str(p) for p in flagged_unresolved_pages)}]"
    )
    lines.append("---")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------- self-test

if __name__ == "__main__":
    from contract import Reason

    def _mk(page, status, *, reasons=None, markdown="Some page content here."):
        return PageRecord(page=page, status=status, reasons=reasons or [],
                           tier_history=["triage"], recall=None, markdown=markdown)

    def _check_block(block: str, *, must_contain: list[str]):
        assert block.startswith("---\n"), "frontmatter must start with '---'"
        assert block.rstrip("\n").endswith("---"), "frontmatter must end with '---'"
        for key in must_contain:
            assert key in block, f"missing expected key: {key!r}"
        # Every non-indented top-level "key: value" line's value segment must
        # not itself smuggle an unquoted colon-space (which would break YAML
        # parsing) -- check the lines we emit unquoted (resource/sources).
        for line in block.splitlines():
            if line.startswith("resource:") or line.strip().startswith("- resource:"):
                value = line.split("resource:", 1)[1].strip()
                if not value.startswith('"'):
                    assert ": " not in value, f"unquoted colon-space in bare scalar: {line!r}"

    print("=== clean doc ===")
    clean_records = [
        _mk(1, PageStatus.EXTRACTED, markdown="# Title\n\nFirst real paragraph of content."),
        _mk(2, PageStatus.EXTRACTED),
    ]
    block = okf_frontmatter(title=None, source_path="/tmp/fixtures/report.pdf",
                             records=clean_records, route=DocRoute.PDF_INSPECTOR)
    print(block)
    _check_block(block, must_contain=["type: Document", "title:", "resource:", "generated:",
                                       "  by:", "  at:", "sources:", "status: stable",
                                       "conversion:", "vision_pages:", "flagged_unresolved_pages:"])
    assert 'title: "report"' in block  # fallback from filename stem
    assert '"First real paragraph of content."' in block

    print("=== mixed doc: 1 ocr + 1 flagged, explicit title ===")
    mixed_records = [
        _mk(1, PageStatus.EXTRACTED),
        _mk(2, PageStatus.OCR, reasons=[Reason.SCANNED]),
        _mk(3, PageStatus.VISION),
        _mk(4, PageStatus.FLAGGED_UNRESOLVED, markdown=None),
    ]
    block2 = okf_frontmatter(title="A Report: With a Colon", source_path="/tmp/fixtures/mixed.pdf",
                              records=mixed_records, route=DocRoute.DOCLING)
    print(block2)
    _check_block(block2, must_contain=["type: Document", "conversion:"])
    assert "vision_pages: [3]" in block2
    assert "flagged_unresolved_pages: [4]" in block2
    assert yaml_str("A Report: With a Colon") in block2

    print("=== path needing quoting (colon-space, leading dash) ===")
    quoted = _yaml_scalar("weird: path.pdf")
    assert quoted.startswith('"'), "path with colon-space must be quoted"
    plain = _yaml_scalar("/tmp/fixtures/report.pdf")
    assert not plain.startswith('"'), "plain safe path should stay bare"

    print("\nall okf.py self-tests passed")
