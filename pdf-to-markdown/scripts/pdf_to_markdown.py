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
import re
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


_FLAGGED_RE = re.compile(r"<!-- page (\d+): flagged")
_VISION_RE = re.compile(r"<!-- page (\d+): transcribed by vision -->")


def agent_action_block(rep: dict, out_path: Path, pdf_path: Path) -> str:
    """Plain-text next-steps block printed to stdout on exit 4, so the agent
    reads everything it needs from the command output itself — no report.json
    parsing, ever. The JSON stays as the provenance artifact."""
    pending = rep["vision"]["pending_pages"]
    by_page = {p["page"]: p for p in rep["pages"]}
    lines = ["", "== ACTION REQUIRED: vision tier ==",
             f"Pages needing vision: {', '.join(map(str, pending))}"]
    for p in pending:
        pg = by_page[p]
        recall = f" recall={pg['recall']:.2f}" if pg.get("recall") is not None else ""
        lines.append(f"  page {p}: {', '.join(pg['reasons'])}{recall}")
    lines += [
        f"1. Read {pdf_path.resolve()} (pages parameter, max 20/request).",
        f"2. In {out_path.resolve()}, replace each",
        "   '<!-- page N: flagged (...) -->' placeholder with your",
        "   transcription, headed by '<!-- page N: transcribed by vision -->'.",
        "   Cross-foot numeric tables; write [illegible] instead of guessing.",
        f"3. Run: pdf_to_markdown.py --reconcile {out_path.resolve()}",
        "   (re-derives the report statuses from the markdown; exits 0 when",
        "   every page is resolved)",
    ]
    return "\n".join(lines)


def reconcile(md_path: Path) -> int:
    """Post-vision reconciliation: re-derive page statuses from the markdown's
    own markers instead of making the agent hand-edit report.json. A page
    whose flagged placeholder is gone AND whose vision label is present
    becomes status 'vision'; a placeholder still present stays flagged."""
    report_path = md_path.with_suffix(".report.json")
    if not md_path.is_file() or not report_path.is_file():
        print(f"error: need both {md_path} and {report_path}", file=sys.stderr)
        return EXIT_ERROR
    text = md_path.read_text(encoding="utf-8")
    still_flagged = {int(n) for n in _FLAGGED_RE.findall(text)}
    visioned = {int(n) for n in _VISION_RE.findall(text)}

    rep = json.loads(report_path.read_text(encoding="utf-8"))
    for pg in rep["pages"]:
        if (pg["status"] in ("flagged", "flagged-unresolved")
                and pg["page"] in visioned
                and pg["page"] not in still_flagged):
            pg["status"] = "vision"
            pg["tier_history"].append("vision")
            pg["has_markdown"] = True
    counts: dict[str, int] = {k: 0 for k in rep["summary"]}
    for pg in rep["pages"]:
        counts[pg["status"]] = counts.get(pg["status"], 0) + 1
    rep["summary"] = counts
    pending = sorted(pg["page"] for pg in rep["pages"]
                     if pg["status"] == "flagged")
    rep["vision"]["pending_pages"] = pending
    rep["clean"] = not any(pg["status"] in ("flagged", "flagged-unresolved")
                           for pg in rep["pages"])
    report_path.write_text(json.dumps(rep, indent=2), encoding="utf-8")

    resolved = sorted(visioned - still_flagged)
    print(f"reconciled: {len(resolved)} page(s) marked vision"
          + (f" ({', '.join(map(str, resolved))})" if resolved else ""))
    if pending:
        print(f"still pending vision: {', '.join(map(str, pending))}")
    unresolved = [pg["page"] for pg in rep["pages"]
                  if pg["status"] == "flagged-unresolved"]
    if unresolved:
        print(f"unresolved (--no-vision run): {', '.join(map(str, unresolved))}")
    return EXIT_CLEAN if rep["clean"] else EXIT_FLAGGED


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Convert a PDF to OKF-frontmatter markdown (tiered, "
                    "correctness-first — see references/design.md).")
    ap.add_argument("pdf", type=Path, nargs="?")
    ap.add_argument("--reconcile", type=Path, metavar="OUT_MD",
                    help="post-vision: re-derive report statuses from the "
                         "patched markdown's markers, then exit 0/4")
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

    if args.reconcile is not None:
        return reconcile(args.reconcile)
    if args.pdf is None or not args.pdf.is_file():
        print(f"error: {args.pdf or '(no pdf given)'} not found",
              file=sys.stderr)
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
    if code == EXIT_FLAGGED and rep["vision"]["pending_pages"]:
        print(agent_action_block(rep, out, args.pdf))
    return code


if __name__ == "__main__":
    sys.exit(main())
