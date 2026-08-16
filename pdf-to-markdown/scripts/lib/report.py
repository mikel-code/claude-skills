"""Per-page conversion report for the pdf-to-markdown pipeline.

Two outputs from the same PageRecord list: a machine-readable JSON report
(build_report) and a compact human summary for stderr (render_report). Also
the no-silent-loss gate (assert_complete), called before any output is
written — see contract.py's module-level docstring for the invariant this
enforces: every input page appears in exactly one PageRecord.
"""

from __future__ import annotations

from contract import DocRoute, PageRecord, PageStatus

# Statuses whose pages are expected to carry actual extracted content. A page
# that is FLAGGED or FLAGGED_UNRESOLVED may or may not have markdown (e.g. a
# best-effort partial extraction attached for a human to salvage) — only
# these statuses are held to "must have non-empty markdown".
_REQUIRES_MARKDOWN = {
    PageStatus.EXTRACTED,
    PageStatus.EXTRACTED_DOCLING,
    PageStatus.OCR,
    PageStatus.VISION,
}

# Statuses that represent an unresolved page — presence of any among the
# records is what makes a report "not clean".
_UNCLEAN_STATUSES = {PageStatus.FLAGGED, PageStatus.FLAGGED_UNRESOLVED}

# Statuses worth calling out individually in the human summary: anything
# that needed more than the default tier-1 extraction. EXTRACTED and
# EXTRACTED_DOCLING pages are the "boring path" and are only ever shown in
# aggregate.
_NOTABLE_STATUSES = {PageStatus.OCR, PageStatus.VISION} | _UNCLEAN_STATUSES


# ------------------------------------------------------------------- report

def build_report(records: list[PageRecord], *, pdf_path: str, route: DocRoute) -> dict:
    """Machine-readable JSON report: one entry per page (via
    PageRecord.to_json()), summary counts by status, and a top-level `clean`
    bool (True iff no FLAGGED/FLAGGED_UNRESOLVED page is present)."""
    ordered = sorted(records, key=lambda r: r.page)
    summary = {status.value: 0 for status in PageStatus}
    for r in records:
        summary[r.status.value] += 1
    clean = all(r.status not in _UNCLEAN_STATUSES for r in records)
    return {
        "source": pdf_path,
        "route": route.value,
        "page_count": len(records),
        "pages": [r.to_json() for r in ordered],
        "summary": summary,
        "clean": clean,
    }


def render_report(report: dict) -> str:
    """Compact human summary for stderr/terminal: one line per notable page
    (anything beyond plain tier-1 extraction — ocr/vision/flagged/
    flagged-unresolved) with its reasons, then a one-line totals summary.
    Pages that extracted cleanly on the default path are folded into the
    totals line only, never listed individually."""
    lines = []
    for page in report["pages"]:
        status = page["status"]
        if status not in {s.value for s in _NOTABLE_STATUSES}:
            continue
        bits = [f"page {page['page']}: {status}"]
        if page["reasons"]:
            bits.append("(" + ", ".join(page["reasons"]) + ")")
        if page["recall"] is not None:
            bits.append(f"recall={page['recall']:.2f}")
        if not page["has_markdown"]:
            bits.append("[no markdown]")
        lines.append(" ".join(bits))

    summary = report["summary"]
    total = report["page_count"]
    counted = ", ".join(
        f"{summary[s.value]} {s.value}" for s in PageStatus if summary[s.value]
    )
    verdict = "clean" if report["clean"] else "FLAGGED"
    lines.append(f"{total} pages ({counted or 'none'}) — {verdict}")
    return "\n".join(lines)


def assert_complete(records: list[PageRecord], page_count: int) -> None:
    """The no-silent-loss gate. Raises AssertionError unless:
      - records cover pages 1..page_count, each exactly once (gapless, no
        duplicates, no stray pages), and
      - every record whose status is EXTRACTED/EXTRACTED_DOCLING/OCR/VISION
        has non-empty markdown (FLAGGED/FLAGGED_UNRESOLVED may have any).
    Must be called before any output is written.
    """
    pages = [r.page for r in records]
    expected = list(range(1, page_count + 1))
    if sorted(pages) != expected:
        seen = set()
        dupes = sorted({p for p in pages if p in seen or seen.add(p)})
        missing = sorted(set(expected) - set(pages))
        extra = sorted(set(pages) - set(expected))
        problems = []
        if missing:
            problems.append(f"missing pages {missing}")
        if extra:
            problems.append(f"unexpected pages {extra}")
        if dupes:
            problems.append(f"duplicate pages {dupes}")
        raise AssertionError(
            f"records do not cover pages 1..{page_count} exactly once: "
            + "; ".join(problems or ["unknown mismatch"])
        )

    empty = sorted(
        r.page for r in records
        if r.status in _REQUIRES_MARKDOWN and not (r.markdown and r.markdown.strip())
    )
    if empty:
        raise AssertionError(
            f"pages {empty} have status requiring markdown "
            "(extracted/extracted-docling/ocr/vision) but markdown is empty"
        )


# --------------------------------------------------------------- self-test

if __name__ == "__main__":
    from contract import Reason

    def _mk(page, status, *, reasons=None, recall=None, markdown="some text"):
        return PageRecord(page=page, status=status, reasons=reasons or [],
                           tier_history=["triage", f"extract:{status.value}"],
                           recall=recall, markdown=markdown)

    print("=== clean 3-page doc ===")
    clean_records = [
        _mk(1, PageStatus.EXTRACTED),
        _mk(2, PageStatus.EXTRACTED),
        _mk(3, PageStatus.EXTRACTED_DOCLING),
    ]
    assert_complete(clean_records, 3)
    report = build_report(clean_records, pdf_path="/tmp/clean.pdf", route=DocRoute.PDF_INSPECTOR)
    assert report["clean"] is True
    assert report["page_count"] == 3
    import json
    print(json.dumps(report, indent=2))
    print(render_report(report))

    print("\n=== mixed doc: 1 ocr + 1 flagged ===")
    mixed_records = [
        _mk(1, PageStatus.EXTRACTED),
        _mk(2, PageStatus.OCR, reasons=[Reason.SCANNED], recall=0.91),
        _mk(3, PageStatus.FLAGGED, reasons=[Reason.LOW_RECALL], recall=0.42,
            markdown=None),
    ]
    assert_complete(mixed_records, 3)
    report2 = build_report(mixed_records, pdf_path="/tmp/mixed.pdf", route=DocRoute.PDF_INSPECTOR)
    assert report2["clean"] is False
    assert report2["summary"][PageStatus.FLAGGED.value] == 1
    print(json.dumps(report2, indent=2))
    print(render_report(report2))

    print("\n=== gapless-violation must raise ===")
    bad_records = [
        _mk(1, PageStatus.EXTRACTED),
        _mk(3, PageStatus.EXTRACTED),  # page 2 missing
    ]
    try:
        assert_complete(bad_records, 3)
    except AssertionError as e:
        print(f"raised as expected: {e}")
    else:
        raise SystemExit("FAIL: assert_complete did not raise on a gap")

    print("\n=== empty-markdown violation must raise ===")
    bad_markdown_records = [
        _mk(1, PageStatus.EXTRACTED, markdown=""),
        _mk(2, PageStatus.EXTRACTED),
    ]
    try:
        assert_complete(bad_markdown_records, 2)
    except AssertionError as e:
        print(f"raised as expected: {e}")
    else:
        raise SystemExit("FAIL: assert_complete did not raise on empty markdown")

    print("\nall report.py self-tests passed")
