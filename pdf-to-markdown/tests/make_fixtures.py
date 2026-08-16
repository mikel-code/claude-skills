#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "reportlab",
#     "pypdf",
# ]
# ///
"""Regenerate the pdf-to-markdown test fixture corpus, deterministically.

Fixtures are small (1-3 pages), born from reportlab, with two exceptions
rasterized to simulate scanned pages: `pdftoppm` renders a page to PNG at
150dpi and macOS `sips -s format pdf` wraps it back into an image-only,
text-less PDF page. `pypdf` merges pages across sources where a fixture mixes
born-digital and rasterized pages.

probe-simple.pdf and probe-scanned.pdf already exist in the repo; this script
regenerates them identically-in-content (other agents' self-tests assert on
literal strings 'Quarterly Report' / 'Outlook' in probe-simple, and on
probe-scanned having zero extractable text) so the whole corpus is
reproducible from one script.

Run: uv run tests/make_fixtures.py
"""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

PAGE_W, PAGE_H = letter  # 612 x 792


# --------------------------------------------------------------------- utils

def _rasterize_page(src_pdf: Path, page_num: int, dpi: int = 150) -> Path:
    """Render one 1-indexed page of src_pdf to a standalone, text-less,
    image-only single-page PDF via pdftoppm (PNG) + sips (PNG -> PDF).

    Returns a path to the generated PDF inside a temp dir the caller owns
    (caller is responsible for cleanup via the returned dir's parent, or by
    using this inside a `with tempfile.TemporaryDirectory()` block).
    """
    tmp = Path(tempfile.mkdtemp(prefix="pdf2md-fixture-"))
    png_prefix = tmp / "page"
    subprocess.run(
        [
            "pdftoppm", "-png", "-r", str(dpi),
            "-f", str(page_num), "-l", str(page_num),
            str(src_pdf), str(png_prefix),
        ],
        check=True, capture_output=True,
    )
    pngs = sorted(tmp.glob("page*.png"))
    if not pngs:
        raise RuntimeError(f"pdftoppm produced no PNG for {src_pdf} page {page_num}")
    png_path = pngs[0]
    out_pdf = tmp / "rasterized.pdf"
    subprocess.run(
        ["sips", "-s", "format", "pdf", str(png_path), "--out", str(out_pdf)],
        check=True, capture_output=True,
    )
    return out_pdf


def _draw_ruled_table(c: canvas.Canvas, data: list[list[str]], x: float, y_top: float,
                       col_widths: list[float], row_height: float) -> float:
    """Draw a table WITH grid lines (rules). Returns the y-coordinate of the
    table's bottom edge."""
    n_rows = len(data)
    n_cols = len(col_widths)
    total_w = sum(col_widths)
    y_bottom = y_top - n_rows * row_height

    # horizontal rules
    for r in range(n_rows + 1):
        y = y_top - r * row_height
        c.line(x, y, x + total_w, y)
    # vertical rules
    cx = x
    for cw in col_widths:
        c.line(cx, y_top, cx, y_bottom)
        cx += cw
    c.line(cx, y_top, cx, y_bottom)

    # cell text
    for r, row in enumerate(data):
        row_y = y_top - r * row_height - row_height + (row_height - 10) / 2 + 2
        cx = x
        c.setFont("Helvetica-Bold" if r == 0 else "Helvetica", 10)
        for col_idx, cell in enumerate(row):
            c.drawString(cx + 6, row_y, cell)
            cx += col_widths[col_idx]
    return y_bottom


def _draw_borderless_table(c: canvas.Canvas, data: list[list[str]], x: float, y_top: float,
                            col_widths: list[float], row_height: float) -> float:
    """Draw a table with NO rules/lines at all — pure whitespace alignment via
    fixed-position drawString, right-aligned for numeric-looking columns."""
    n_rows = len(data)
    y_bottom = y_top - n_rows * row_height
    for r, row in enumerate(data):
        row_y = y_top - r * row_height - row_height + (row_height - 10) / 2 + 2
        cx = x
        c.setFont("Helvetica-Bold" if r == 0 else "Helvetica", 10)
        for col_idx, cell in enumerate(row):
            cw = col_widths[col_idx]
            if col_idx == 0:
                c.drawString(cx, row_y, cell)
            else:
                c.drawRightString(cx + cw, row_y, cell)
            cx += cw
    return y_bottom


# ------------------------------------------------------------- probe-simple

def make_probe_simple(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=letter)

    # --- page 1 ---
    y = PAGE_H - 72
    c.setFont("Helvetica-Bold", 18)
    c.drawString(72, y, "Quarterly Report")
    y -= 28

    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, y, "Revenue Summary")
    y -= 22

    c.setFont("Helvetica", 11)
    prose = [
        "Revenue grew twelve percent year over year, driven by subscription",
        "renewals and two enterprise contracts signed in March.",
        "Operating costs held flat while headcount rose by four engineers.",
    ]
    for line in prose:
        c.drawString(72, y, line)
        y -= 16
    y -= 20

    table = [
        ["Region", "Q1", "Q2"],
        ["EMEA", "1.2", "1.4"],
        ["Americas", "2.1", "2.3"],
    ]
    _draw_ruled_table(c, table, x=72, y_top=y, col_widths=[140, 60, 60], row_height=20)

    c.showPage()

    # --- page 2 ---
    y = PAGE_H - 72
    c.setFont("Helvetica-Bold", 18)
    c.drawString(72, y, "Outlook")
    y -= 28
    c.setFont("Helvetica", 11)
    c.drawString(72, y, "Second-half guidance remains unchanged pending the audit.")

    c.showPage()
    c.save()


# ------------------------------------------------------------ probe-scanned

def make_probe_scanned(path: Path, probe_simple_path: Path) -> None:
    """Page 1 of probe-simple, rasterized at 150dpi, image-only, no text
    layer — a minimal 'scanned page' fixture."""
    rasterized = _rasterize_page(probe_simple_path, page_num=1, dpi=150)
    shutil.copyfile(rasterized, path)
    shutil.rmtree(rasterized.parent, ignore_errors=True)


# --------------------------------------------------------- borderless-table

def make_borderless_table(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=letter)
    y = PAGE_H - 72
    c.setFont("Helvetica-Bold", 18)
    c.drawString(72, y, "Vendor Spend Summary")
    y -= 40

    table = [
        ["Vendor", "Invoices", "Total Spend", "Avg Days to Pay"],
        ["Acme Supply Co.", "14", "$82,340.00", "27"],
        ["Blue Ridge Logistics", "9", "$41,905.50", "33"],
        ["Cascade Materials", "22", "$156,712.75", "19"],
    ]
    _draw_borderless_table(c, table, x=72, y_top=y, col_widths=[220, 80, 110, 110], row_height=22)

    c.showPage()
    c.save()


# ------------------------------------------------------------- two-column

def make_two_column(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=letter)

    left_x = 72
    right_x = 72 + 230 + 20  # ~230pt column + 20pt gutter
    col_w = 230
    top_y = PAGE_H - 90

    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, PAGE_H - 60, "Field Notes")

    c.setFont("Helvetica-Bold", 12)
    c.drawString(left_x, top_y, "Glacier Survey")
    c.drawString(right_x, top_y, "Harbor Survey")

    left_lines = [
        "The glacier retreated eleven meters",
        "since the previous survey season,",
        "consistent with the decade-long",
        "trend recorded by the station.",
        "Crevasse density increased near",
        "the terminus, likely reflecting",
        "faster basal sliding this summer.",
        "Ice core samples were collected",
        "at four depths for isotope",
        "analysis back at the lab.",
        "Weather delayed two of the six",
        "planned transects across the",
        "upper névé field this week.",
        "The team will return in October",
        "for a follow-up mass-balance run.",
    ]
    right_lines = [
        "Container throughput rose eight",
        "percent quarter over quarter as",
        "the new berth came online in May.",
        "Tidal scour near pier four is",
        "being monitored after divers",
        "found sediment buildup exceeding",
        "the maintenance threshold.",
        "Two tugboats were reassigned to",
        "cover the overnight shift after",
        "a crew shortage last month.",
        "The harbor master approved an",
        "extended pilot boarding zone",
        "for the incoming tanker route.",
        "Dredging is scheduled to begin",
        "once the environmental permit",
        "renewal clears next week.",
    ]

    c.setFont("Helvetica", 10)
    line_h = 15
    y = top_y - 20
    for line in left_lines:
        c.drawString(left_x, y, line)
        y -= line_h

    y = top_y - 20
    for line in right_lines:
        c.drawString(right_x, y, line)
        y -= line_h

    # guard rails so both columns visually stay within ~230pt width
    assert col_w <= 240, "column width drifted out of the ~230pt spec"

    c.showPage()
    c.save()


# ------------------------------------------------------------------- mixed

def make_mixed(path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="pdf2md-mixed-") as tmp_str:
        tmp = Path(tmp_str)
        source_pdf = tmp / "source.pdf"

        c = canvas.Canvas(str(source_pdf), pagesize=letter)

        # --- source page 1: born-digital text, kept as-is in the output ---
        y = PAGE_H - 72
        c.setFont("Helvetica-Bold", 18)
        c.drawString(72, y, "Engineering Change Log")
        y -= 28
        c.setFont("Helvetica", 11)
        for line in [
            "Firmware 4.2.1 fixes a race condition in the sensor bus driver",
            "that occasionally dropped the first telemetry packet after boot.",
            "No hardware changes accompany this release.",
        ]:
            c.drawString(72, y, line)
            y -= 16
        c.showPage()

        # --- source page 2: will be rasterized to become the image page ---
        c.setFont("Helvetica-Bold", 18)
        c.drawString(72, PAGE_H - 72, "Appendix A: Wiring Photo Log")
        c.setFont("Helvetica", 11)
        c.drawString(72, PAGE_H - 100, "Connector J14 was re-terminated per ECO-3391.")
        c.showPage()
        c.save()

        rasterized_p2 = _rasterize_page(source_pdf, page_num=2, dpi=150)

        reader_text = PdfReader(str(source_pdf))
        reader_image = PdfReader(str(rasterized_p2))

        writer = PdfWriter()
        writer.add_page(reader_text.pages[0])   # page 1: text
        writer.add_page(reader_image.pages[0])  # page 2: image-only, no text layer

        with open(path, "wb") as f:
            writer.write(f)

        shutil.rmtree(rasterized_p2.parent, ignore_errors=True)


# --------------------------------------------------------- scan-with-caption

def _caption_overlay(lines: list[tuple[float, float, str, str, int]]) -> PdfReader:
    """Build a single-page overlay PDF (in memory) from (x, y, font, text,
    size) tuples, for merging live text onto a rasterized page."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for x, y, font, text, size in lines:
        c.setFont(font, size)
        c.drawString(x, y, text)
    c.showPage()
    c.save()
    buf.seek(0)
    return PdfReader(buf)


def make_scan_with_caption(path: Path, probe_scanned_path: Path) -> None:
    """The ocrmypdf-mode trap page: a full-page scan image PLUS one live
    born-digital caption line. `--skip-text` would skip the whole page
    because of the caption; triage must select `--redo-ocr` for it."""
    scan = PdfReader(str(probe_scanned_path))
    overlay = _caption_overlay([
        (72, 30, "Helvetica", "Figure 1: scanned quarterly report page, archival copy.", 10),
    ])
    page = scan.pages[0]
    page.merge_page(overlay.pages[0])
    writer = PdfWriter()
    writer.add_page(page)
    with open(path, "wb") as f:
        writer.write(f)


# ------------------------------------------------------------- docling-trap

def make_docling_trap(path: Path, probe_simple_path: Path) -> None:
    """4-page doc engineered to (a) route to docling (>=4 pages, 2 ruled-table
    pages => tables ratio 50%) while (b) hiding a scanned page that
    pdf-inspector's doc-level text_based verdict suppresses all flags for:
    page 4 is a full-page rasterized image of probe-simple page 1 with two
    live text lines drawn on top ('Exhibit D' style). Triage's independent
    page-scale-image signal must catch page 4 (SCANNED, mode REDO) or the
    docling route silently drops the scanned body with a clean report."""
    with tempfile.TemporaryDirectory(prefix="pdf2md-trap-") as tmp_str:
        tmp = Path(tmp_str)
        born_digital = tmp / "born-digital.pdf"

        c = canvas.Canvas(str(born_digital), pagesize=letter)
        # pages 1-2: two distinct ruled-table (ledger) pages
        for part in (1, 2):
            c.setFont("Helvetica-Bold", 16)
            c.drawString(72, PAGE_H - 72, f"Ledger Part {part}")
            data = [["Account", "Debit", "Credit"]] + [
                [f"Acct {part}0{i}", f"{100 + part * 10 + i}.00", f"{90 + part * 10 + i}.50"]
                for i in range(6)
            ]
            _draw_ruled_table(c, data, x=72, y_top=PAGE_H - 110,
                              col_widths=[160, 80, 80], row_height=20)
            c.showPage()
        # page 3: narrative text
        c.setFont("Helvetica-Bold", 16)
        c.drawString(72, PAGE_H - 72, "Narrative")
        c.setFont("Helvetica", 11)
        for i in range(4):
            c.drawString(72, PAGE_H - 100 - 16 * i,
                         f"Discussion line {i + 1}: balances reconciled without exception.")
        c.showPage()
        c.save()

        # page 4: full-page rasterized image + two live text lines on top
        rasterized = _rasterize_page(probe_simple_path, page_num=1, dpi=150)
        scan_reader = PdfReader(str(rasterized))
        overlay = _caption_overlay([
            (72, PAGE_H - 60, "Helvetica-Bold", "Exhibit D: Archived Filing", 14),
            (72, PAGE_H - 80, "Helvetica", "Reproduced below from the archive scan.", 11),
        ])
        trap_page = scan_reader.pages[0]
        trap_page.merge_page(overlay.pages[0])

        writer = PdfWriter()
        for pg in PdfReader(str(born_digital)).pages:
            writer.add_page(pg)
        writer.add_page(trap_page)
        with open(path, "wb") as f:
            writer.write(f)
        shutil.rmtree(rasterized.parent, ignore_errors=True)


# ---------------------------------------------------------------- blank-mid

def make_blank_mid(path: Path) -> None:
    """3 clean text pages with page 2 intentionally blank. Regression fixture
    for the false-garble class: pdf-inspector flags the *text* pages of this
    doc suspected_garbled_text (forcing --force-ocr); triage's poppler
    cross-check must veto that, and the blank page must not be OCR-flagged
    either — the doc converts with no OCR at all."""
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica", 12)
    c.drawString(72, 700, "Page one has ordinary paragraph text for the record.")
    c.showPage()
    c.showPage()  # page 2 intentionally blank
    c.setFont("Helvetica", 12)
    c.drawString(72, 700, "Page three resumes the ordinary paragraph text.")
    c.showPage()
    c.save()


# ------------------------------------------------------------------- verify

def _pdftotext(path: Path, layout: bool = False) -> str:
    cmd = ["pdftotext"]
    if layout:
        cmd.append("-layout")
    cmd += [str(path), "-"]
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def _check(label: str, cond: bool, detail: str = "") -> None:
    status = "OK" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise SystemExit(f"fixture verification failed: {label} {detail}")


def verify_fixtures() -> None:
    print("Verifying generated fixtures...")

    simple_text = _pdftotext(FIXTURES_DIR / "probe-simple.pdf")
    _check("probe-simple.pdf contains 'Quarterly Report'", "Quarterly Report" in simple_text)
    _check("probe-simple.pdf contains 'Outlook'", "Outlook" in simple_text)

    scanned_text = _pdftotext(FIXTURES_DIR / "probe-scanned.pdf")
    _check("probe-scanned.pdf has no extractable text", scanned_text.strip() == "",
           detail=repr(scanned_text[:80]))

    borderless_text = _pdftotext(FIXTURES_DIR / "borderless-table.pdf")
    _check("borderless-table.pdf has text", "Vendor Spend Summary" in borderless_text)
    _check("borderless-table.pdf has table content", "Cascade Materials" in borderless_text)
    # no vector rules: inspect the decoded content stream directly for path
    # drawing/stroke/fill operators (l=lineto, re=rect, S/s/f/F/B/b=paint).
    reader = PdfReader(str(FIXTURES_DIR / "borderless-table.pdf"))
    content = reader.pages[0].get_contents().get_data().decode("latin1")
    drawing_ops = {"l", "re", "S", "s", "f", "F", "B", "b"}
    found_ops = set(content.split()) & drawing_ops
    _check("borderless-table.pdf has no vector rules", not found_ops, detail=f"found ops: {found_ops}")

    two_col_text = _pdftotext(FIXTURES_DIR / "two-column.pdf", layout=True)
    _check("two-column.pdf has left column content", "Glacier Survey" in two_col_text)
    _check("two-column.pdf has right column content", "Harbor Survey" in two_col_text)
    _check("two-column.pdf has left column body text", "crevasse density".lower() in two_col_text.lower())
    _check("two-column.pdf has right column body text", "container throughput".lower() in two_col_text.lower())

    mixed_p1 = subprocess.run(["pdftotext", "-f", "1", "-l", "1", str(FIXTURES_DIR / "mixed.pdf"), "-"],
                               check=True, capture_output=True, text=True).stdout
    mixed_p2 = subprocess.run(["pdftotext", "-f", "2", "-l", "2", str(FIXTURES_DIR / "mixed.pdf"), "-"],
                               check=True, capture_output=True, text=True).stdout
    _check("mixed.pdf page 1 has born-digital text", "Engineering Change Log" in mixed_p1)
    _check("mixed.pdf page 2 has no extractable text", mixed_p2.strip() == "", detail=repr(mixed_p2[:80]))

    def _page_text(pdf: Path, page: int) -> str:
        return subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), str(pdf), "-"],
            check=True, capture_output=True, text=True).stdout

    def _largest_image_px(pdf: Path, page: int) -> int:
        pg = PdfReader(str(pdf)).pages[page - 1]
        res = pg.get("/Resources")
        xobjs = res.get_object().get("/XObject") if res is not None else None
        best = 0
        if xobjs is not None:
            for name in xobjs:
                xo = xobjs[name].get_object()
                if xo.get("/Subtype") == "/Image":
                    best = max(best, int(xo.get("/Width", 0)) * int(xo.get("/Height", 0)))
        return best

    swc = FIXTURES_DIR / "scan-with-caption.pdf"
    swc_text = _pdftotext(swc).strip()
    _check("scan-with-caption.pdf live caption extractable", "Figure 1" in swc_text)
    _check("scan-with-caption.pdf text is sparse (scan body is image-only)",
           len(swc_text) < 200, detail=f"{len(swc_text)} chars")
    _check("scan-with-caption.pdf carries a page-scale image",
           _largest_image_px(swc, 1) >= 327_250,
           detail=f"{_largest_image_px(swc, 1)} px")

    trap = FIXTURES_DIR / "docling-trap.pdf"
    _check("docling-trap.pdf has 4 pages", len(PdfReader(str(trap)).pages) == 4)
    _check("docling-trap.pdf page 1 is a ledger table page", "Ledger Part 1" in _page_text(trap, 1))
    _check("docling-trap.pdf page 2 is a ledger table page", "Ledger Part 2" in _page_text(trap, 2))
    _check("docling-trap.pdf page 3 is narrative", "balances reconciled" in _page_text(trap, 3))
    trap_p4 = _page_text(trap, 4).strip()
    _check("docling-trap.pdf page 4 live lines extractable", "Exhibit D" in trap_p4)
    _check("docling-trap.pdf page 4 text is sparse (scan body is image-only)",
           len(trap_p4) < 200, detail=f"{len(trap_p4)} chars")
    _check("docling-trap.pdf page 4 carries a page-scale image",
           _largest_image_px(trap, 4) >= 327_250,
           detail=f"{_largest_image_px(trap, 4)} px")
    _check("docling-trap.pdf pages 1-3 carry no images",
           all(_largest_image_px(trap, p) == 0 for p in (1, 2, 3)))

    blank = FIXTURES_DIR / "blank-mid.pdf"
    _check("blank-mid.pdf page 1 has text", "Page one" in _page_text(blank, 1))
    _check("blank-mid.pdf page 2 is blank", _page_text(blank, 2).strip() == "",
           detail=repr(_page_text(blank, 2)[:80]))
    _check("blank-mid.pdf page 3 has text", "Page three" in _page_text(blank, 3))

    print("All fixture checks passed.")


# --------------------------------------------------------------------- main

def main() -> None:
    for tool in ("pdftoppm", "pdftotext", "sips"):
        if shutil.which(tool) is None:
            print(f"error: required tool '{tool}' not found on PATH", file=sys.stderr)
            sys.exit(1)

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    probe_simple_path = FIXTURES_DIR / "probe-simple.pdf"
    probe_scanned_path = FIXTURES_DIR / "probe-scanned.pdf"
    borderless_path = FIXTURES_DIR / "borderless-table.pdf"
    two_column_path = FIXTURES_DIR / "two-column.pdf"
    mixed_path = FIXTURES_DIR / "mixed.pdf"

    print(f"Generating fixtures into {FIXTURES_DIR} ...")

    print("  probe-simple.pdf")
    make_probe_simple(probe_simple_path)

    print("  probe-scanned.pdf (rasterized from probe-simple page 1)")
    make_probe_scanned(probe_scanned_path, probe_simple_path)

    print("  borderless-table.pdf")
    make_borderless_table(borderless_path)

    print("  two-column.pdf")
    make_two_column(two_column_path)

    print("  mixed.pdf")
    make_mixed(mixed_path)

    print("  scan-with-caption.pdf (probe-scanned + live caption)")
    make_scan_with_caption(FIXTURES_DIR / "scan-with-caption.pdf", probe_scanned_path)

    print("  docling-trap.pdf (2 table pages + narrative + scan-with-live-lines)")
    make_docling_trap(FIXTURES_DIR / "docling-trap.pdf", probe_simple_path)

    print("  blank-mid.pdf (text / blank / text)")
    make_blank_mid(FIXTURES_DIR / "blank-mid.pdf")

    verify_fixtures()


if __name__ == "__main__":
    main()
