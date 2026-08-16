#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pdf-inspector==1.14.2",
#   "docling==2.120.1",
#   "pypdf",
# ]
# ///
"""pdf-to-markdown pipeline orchestrator.

Wires the tiers defined in references/design.md:
triage -> (ocr -> re-extract) -> extract -> verify -> report/okf output.

Tier 3 (vision) is not code: pages this script cannot resolve are flagged in
the JSON report and left as visible placeholders in the markdown; SKILL.md
tells Claude to Read those pages and patch them. Exit code 4 signals that
handoff. Nothing is ever dropped silently — assert_complete gates the output.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from contract import (  # noqa: E402
    EXIT_CLEAN,
    EXIT_ERROR,
    EXIT_FLAGGED,
    DocRoute,
    PageRecord,
    PageStatus,
    Reason,
)
from deps import check_deps  # noqa: E402
from extract import extract  # noqa: E402
from ocr import run_ocr  # noqa: E402
from okf import okf_frontmatter  # noqa: E402
from report import assert_complete, build_report, render_report  # noqa: E402
from triage import triage  # noqa: E402
from verify import verify  # noqa: E402

PLACEHOLDER = "<!-- page {page}: flagged ({reasons}) — awaiting vision transcription -->"


def convert(pdf_path: Path, *, no_vision: bool, verify_requested: bool,
            languages: str) -> tuple[str, dict, int]:
    """Returns (markdown_document, json_report, exit_code)."""
    try:
        tri = triage(str(pdf_path))
    except Exception as e:
        print(f"error: could not parse {pdf_path} — likely encrypted, "
              f"truncated, or not a PDF ({e}).\n"
              f"If encrypted: decrypt first with "
              f"`qpdf --decrypt in.pdf out.pdf` and retry.", file=sys.stderr)
        sys.exit(EXIT_ERROR)
    working_pdf = pdf_path

    # Tier 2: one OCR pass at most, then everything downstream uses the
    # text-layer PDF. Reason codes chose the mode in triage. An OCR tool
    # failure degrades to vision (pages stay flagged) rather than aborting.
    ocr_pages: set[int] = set(tri.ocr_pages)
    ocr_failed = False
    tmp_dir: str | None = None
    if tri.ocr_mode is not None and ocr_pages:
        tmp_dir = tempfile.mkdtemp(prefix="pdf2md-ocr-")
        try:
            working_pdf = Path(run_ocr(str(pdf_path), tri.ocr_mode, tmp_dir,
                                       languages=languages))
        except RuntimeError as e:
            print(f"ocr tier failed, escalating pages to vision: {e}",
                  file=sys.stderr)
            ocr_failed = True

    # Tier 1 on the working PDF, route decided by triage on the original.
    ext = extract(str(working_pdf), tri.route)

    # Assemble one record per page — the no-silent-loss invariant starts here.
    records: list[PageRecord] = []
    for page in range(1, tri.page_count + 1):
        md = ext.pages.get(page)
        reasons = list(tri.ocr_pages.get(page, []))
        history = ["triage", f"extract:{tri.route.value}"]
        if page in ocr_pages and tri.ocr_mode is not None:
            history.insert(1, f"ocr:{tri.ocr_mode.value}")
        if md and not (ocr_failed and page in ocr_pages):
            status = (PageStatus.OCR if page in ocr_pages
                      else PageStatus.EXTRACTED_DOCLING
                      if tri.route is DocRoute.DOCLING
                      else PageStatus.EXTRACTED)
        else:
            status = PageStatus.FLAGGED
            if not reasons:
                reasons.append(Reason.EMPTY_OUTPUT)
            elif page in ocr_pages:
                reasons.append(Reason.OCR_FAILED)
        records.append(PageRecord(page=page, status=status, reasons=reasons,
                                  tier_history=history, markdown=md))

    # Tier V: cross-engine recall against the working (text-layer) PDF.
    # Known blind spot (see design.md): on OCR'd pages both sides read the
    # same tesseract layer, so recall≈1.0 is tautological there — the vision
    # spot-check (--verify) is the only real check for those pages.
    ver = verify(str(working_pdf),
                 {r.page: r.markdown for r in records if r.markdown})
    for r in records:
        r.recall = ver.recall.get(r.page)
        r.tier_history.append("verify")
        if r.page in ver.flagged and r.status is not PageStatus.FLAGGED:
            r.status = PageStatus.FLAGGED
            r.reasons.append(Reason.LOW_RECALL)
        elif (r.recall is None and r.markdown
              and len(r.markdown.strip()) > 100
              and r.status in (PageStatus.EXTRACTED,
                               PageStatus.EXTRACTED_DOCLING)):
            # Poppler sees (near-)nothing where the extractor produced a full
            # page: maximal cross-engine disagreement, not "nothing to check"
            # — e.g. broken-font asymmetries. Unverifiable ≠ verified.
            r.status = PageStatus.FLAGGED
            r.reasons.append(Reason.LOW_RECALL)

    # Tier 3 handoff vs. --no-vision downgrade.
    flagged = [r for r in records if r.status is PageStatus.FLAGGED]
    if no_vision:
        for r in flagged:
            r.status = PageStatus.FLAGGED_UNRESOLVED

    # The OCR'd working copy is temporary: the vision workflow reads the
    # source PDF, so nothing downstream needs it retained.
    if tmp_dir is not None:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    assert_complete(records, tri.page_count)
    rep = build_report(records, pdf_path=str(pdf_path), route=tri.route)
    rep["source_original"] = str(pdf_path)
    rep["ocr_applied"] = tri.ocr_mode.value if (tri.ocr_mode and ocr_pages
                                                and not ocr_failed) else None
    rep["vision"] = {
        "enabled": not no_vision,
        "pending_pages": [r.page for r in records
                          if r.status is PageStatus.FLAGGED],
        "spot_check_requested": verify_requested,
    }

    body_parts = []
    for r in records:
        if r.markdown and r.status not in (PageStatus.FLAGGED,
                                           PageStatus.FLAGGED_UNRESOLVED):
            body_parts.append(r.markdown.strip())
        else:
            # Flagged pages keep any partial markdown *after* the marker so
            # vision review sees what the extractor thought was there.
            marker = PLACEHOLDER.format(
                page=r.page, reasons=", ".join(x.value for x in r.reasons))
            body_parts.append(marker if not r.markdown
                              else marker + "\n\n" + r.markdown.strip())
    # PDF /Title metadata is often a placeholder; okf falls back to the
    # filename stem on None.
    title = ext.title if ext.title and ext.title.lower() != "untitled" else None
    front = okf_frontmatter(title=title, source_path=str(pdf_path),
                            records=records, route=tri.route)
    document = front + "\n" + "\n\n".join(body_parts) + "\n"

    exit_code = EXIT_CLEAN if rep["clean"] else EXIT_FLAGGED
    return document, rep, exit_code


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Convert a PDF to OKF-frontmatter markdown (tiered, "
                    "correctness-first — see references/design.md).")
    ap.add_argument("pdf", type=Path)
    out_group = ap.add_mutually_exclusive_group()
    out_group.add_argument("-o", "--output", type=Path, default=None,
                           help="output .md path (default: <pdf-stem>.md in cwd)")
    out_group.add_argument("--output-dir", type=Path, default=None)
    ap.add_argument("--no-vision", action="store_true",
                    help="do not hand flagged pages to vision; mark them "
                         "unresolved in the report instead")
    ap.add_argument("--verify", action="store_true",
                    help="request a vision spot-check of unflagged pages "
                         "(recorded in the report for the agent workflow)")
    ap.add_argument("--json", action="store_true",
                    help="print the machine-readable report to stdout")
    ap.add_argument("-l", "--languages", default="eng",
                    help="OCR languages, tesseract codes (e.g. eng+spa)")
    args = ap.parse_args()

    if not args.pdf.is_file():
        print(f"error: {args.pdf} not found", file=sys.stderr)
        return EXIT_ERROR
    check_deps()

    out = args.output
    if out is None:
        out_dir = args.output_dir or Path.cwd()
        out = out_dir / (args.pdf.stem + ".md")
    out.parent.mkdir(parents=True, exist_ok=True)

    document, rep, code = convert(args.pdf, no_vision=args.no_vision,
                                  verify_requested=args.verify,
                                  languages=args.languages)
    out.write_text(document, encoding="utf-8")
    rep["output"] = str(out.resolve())
    report_path = out.with_suffix(".report.json")
    report_path.write_text(json.dumps(rep, indent=2), encoding="utf-8")

    print(render_report(rep), file=sys.stderr)
    print(f"wrote {out.resolve()}", file=sys.stderr)
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        print(out.resolve())
    return code


if __name__ == "__main__":
    sys.exit(main())
