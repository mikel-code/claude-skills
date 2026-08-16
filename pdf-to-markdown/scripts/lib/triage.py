# /// script
# requires-python = ">=3.10"
# dependencies = ["pdf-inspector==1.14.2", "pypdf"]
# ///
"""Tier 0 — triage. Classify a PDF and decide routing before any extraction.

Wraps pdf-inspector's detector and emits a TriageResult per contract.py:
which document-level Tier-1 engine to use (pdf-inspector vs docling), which
pages must be OCR'd and why, and which single ocrmypdf mode covers them.

Design rules encoded here (references/design.md is the authority):
- Never trust `needs_ocr`/`pages_needing_ocr` alone (pdf-inspector issues
  #227/#251/#200/#342 — wrong verdicts skew toward unsafely skipping OCR).
  The escalation set is a union of independent signals, one of which
  (signal 4) never consults pdf-inspector at all: pdf-inspector suppresses
  per-page OCR flags under a doc-level text_based verdict, so any signal
  gated on `pdf_type != "text_based"` inherits the very verdict being
  distrusted. Empirically confirmed: a text_based doc whose last page is a
  full-page scan with two live caption lines gets zero flags from
  pdf-inspector and was silently lost on the docling route.
- `confidence` measures classification only, never extraction completeness.
  It is not consulted for any routing decision here.
- When signals conflict, prefer the OcrMode that cannot silently skip a
  broken page (FORCE > REDO > SKIP_TEXT).
- pdf-inspector's own signals are also cross-checked against second engines
  (poppler pdftotext, pypdf) before they can force destructive modes:
  a GARBLED flag on a page whose poppler text is clean prose is a false
  positive (observed: 3-page clean text doc with one blank middle page got
  every text page flagged suspected_garbled_text -> --force-ocr), and a
  NO_TEXT flag on a genuinely blank page (no text, no images, no painting
  operators) has nothing OCR could recover.

Indexing bases — VERIFIED EMPIRICALLY against tests/fixtures/probe-scanned.pdf
(1 page, image-only) with pdf-inspector 1.14.2 on 2026-08-16:
- process_pdf().pages_needing_ocr            -> [1]  => 1-INDEXED
- process_pdf().ocr_reasons_by_page[i].page  ->  1   => 1-INDEXED
  (extract_pages_markdown() doc-level fields match: also 1-indexed)
- extract_pages_markdown().pages[i].page     ->  0   => 0-INDEXED
- classify_pdf().pages_needing_ocr           -> [0]  => 0-INDEXED (!)
  classify_pdf disagrees with process_pdf on the same file, so this module
  never calls classify_pdf; everything comes from process_pdf plus
  extract_pages_markdown, normalized to the contract's 1-indexed convention.
- pages_with_tables/pages_with_columns: probe-simple.pdf's table is on its
  first page and process_pdf reports [1] => 1-indexed, consistent with
  ocr_reasons_by_page on the same result object.

Self-test:
    uv run triage.py [fixture.pdf]     # default: runs the bundled fixtures
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pdf_inspector

try:
    from pypdf import PdfReader
except ImportError:
    # Preferred second engine for page geometry (declared in this module's
    # inline metadata), but callers embedding triage in their own env (the
    # orchestrator's uv script block) may not ship it. The poppler-subprocess
    # fallbacks below (`pdfimages -list`, `pdfinfo`, low-DPI `pdftoppm`
    # render) cover the same signals — poppler is already a hard pipeline
    # dependency (deps.py) — so the safety signals never silently vanish.
    PdfReader = None  # type: ignore[assignment]

from contract import DocRoute, OcrMode, Reason, TriageResult

# Docs shorter than this never route to docling. Rationale (verified on
# tests/fixtures/probe-simple.pdf): pdf-inspector sets is_complex_layout=True
# for a 2-page doc containing one simple ruled 3x3 table, and any single
# table page pushes the tables ratio to >=33% on a <=3-page doc — both
# signals are dominated by one page and are noise at this size. A lone ruled
# table converts fine through pdf-inspector (its dual-mode table detection),
# and Tier V + vision escalation hedge the residual risk cheaply on a doc
# this small. Docling's win is table-heavy documents, not table-containing
# pamphlets.
_MIN_DOCLING_PAGES = 4
_TABLE_RATIO_THRESHOLD = 0.30

# --- Signal-4 thresholds (independent page-scale-scan detector) -------------
#
# A page counts as carrying a "page-scale" image when its largest embedded
# image XObject has pixel area >= page area rendered at 100 DPI x 0.35, i.e.
# (page_w_inches*100) * (page_h_inches*100) * 0.35. Empirical margins
# (letter page => threshold 327,250 px, measured 2026-08-16):
# - every rasterized-scan fixture (probe-scanned, mixed p2, docling-trap p4,
#   scan-with-caption, scan-half — all 150 DPI full-page renders) embeds
#   1275x1650 = 2,103,750 px: 6.4x over threshold;
# - every born-digital fixture page (probe-simple incl. its ruled-table page,
#   borderless-table, two-column, blank-mid, tables-4) embeds zero image
#   XObjects: infinitely under.
# The 0.35 factor means "full-page image at >=59 DPI equivalent" — any real
# scan is >=72 DPI, while a letterhead logo (say 300x300 = 90k px) stays a
# factor >3 below threshold even before accounting for its small share of
# the page.
_PAGE_SCALE_DPI = 100
_PAGE_SCALE_AREA_FRACTION = 0.35
# A page whose pdftotext -layout text is shorter than this is "sparse": the
# live text cannot plausibly be the page's whole content when a page-scale
# image is also present. Fixture margins: trap pages carry 55-145 chars of
# live caption text; the sparsest genuine all-text fixture page in the
# corpus that coexists with *no* image is not at risk (the rule requires the
# image), and real text pages measured 194-1157 chars.
_SPARSE_TEXT_CHARS = 200

# --- GARBLED cross-check thresholds ----------------------------------------
_GARBLE_MIN_CHARS = 40           # poppler text shorter than this is not
                                 # "substantial" evidence against the flag
_GARBLE_MIN_CLEAN_RATIO = 0.85   # printable/word-char density of normal prose
_GARBLE_MIN_VOWEL_RATIO = 0.75   # alpha words containing a vowel (hex/CID
                                 # garbage fails this even when alnum-clean)

# Content-stream operators that paint anything: text showing, path painting,
# shading, XObject invocation, inline images. A page whose stream contains
# none of these is blank. (Token-scan is approximate — string literals could
# alias an operator — but a false hit only *keeps* a flag: conservative.)
_MARKING_OPS = frozenset({
    "Tj", "TJ", "'", '"',                      # text showing
    "S", "s", "f", "F", "f*", "B", "B*", "b", "b*",  # path painting
    "sh", "Do", "BI",                          # shading / xobjects / inline img
})


def _choose_route(page_count: int, is_complex: bool,
                  pages_with_tables: list[int]) -> DocRoute:
    """Document-level Tier-1 routing (design.md: never mix per-page output).

    Final rule: DOCLING iff page_count >= 4 AND (is_complex_layout OR
    tables cover >= 30% of pages); PDF_INSPECTOR otherwise. The page-count
    guard gates *both* signals, not just the ratio — see _MIN_DOCLING_PAGES
    for why is_complex is untrustworthy on tiny docs.
    """
    if page_count < _MIN_DOCLING_PAGES:
        return DocRoute.PDF_INSPECTOR
    if is_complex:
        # Complex layout at real document size: docling's layout model +
        # TableFormer beat pdf-inspector's geometric heuristics there.
        return DocRoute.DOCLING
    if pages_with_tables and len(pages_with_tables) / page_count >= _TABLE_RATIO_THRESHOLD:
        # Tables are load-bearing for this document (TEDS ~0.89 vs 0.814).
        return DocRoute.DOCLING
    return DocRoute.PDF_INSPECTOR


def _to_reason(raw: str, page_has_text: bool) -> Reason:
    """Map a pdf-inspector reason string to the contract's Reason enum.

    Unknown strings (future pdf-inspector versions) are mapped by what the
    page actually contains, so the later mode choice stays safe: a page
    *with* text must not be skip-text'd (treat as GARBLED -> FORCE), a page
    without text is OCR'd by any mode (treat as NO_TEXT).
    """
    try:
        return Reason(raw)
    except ValueError:
        return Reason.GARBLED if page_has_text else Reason.NO_TEXT


# ------------------------------------------------- second-engine page probes

def _poppler_page_text(pdf_path: str, page_count: int) -> dict[int, str]:
    """Per-page text via one `pdftotext -layout` subprocess for the whole doc,
    split on form feeds. 1-indexed. Returns {} if poppler is unavailable or
    fails — callers must treat an absent entry as "no second opinion" and
    keep pdf-inspector's verdict (conservative direction).
    """
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", pdf_path, "-"],
            capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    chunks = proc.stdout.split("\f")
    return {p: (chunks[p - 1] if p - 1 < len(chunks) else "")
            for p in range(1, page_count + 1)}


class _PageGeometry(NamedTuple):
    largest_image_px: int        # pixel area of the biggest image XObject
    page_scale_px: float         # threshold: page area @100DPI * 0.35
    has_marks: bool | None       # content stream contains any marking op;
                                 # None = unknown (poppler fallback path —
                                 # resolved lazily via _page_renders_blank)


def _largest_image_px(xobjects, depth: int = 0) -> int:
    """Largest /Image XObject pixel area, recursing one level into /Form
    XObjects (images are sometimes wrapped in forms)."""
    best = 0
    if xobjects is None:
        return 0
    for name in xobjects:
        try:
            xo = xobjects[name].get_object()
        except Exception:
            continue
        subtype = xo.get("/Subtype")
        if subtype == "/Image":
            best = max(best, int(xo.get("/Width", 0)) * int(xo.get("/Height", 0)))
        elif subtype == "/Form" and depth < 1:
            res = xo.get("/Resources")
            if res is not None:
                best = max(best, _largest_image_px(
                    res.get_object().get("/XObject"), depth + 1))
    return best


def _pypdf_page_geometry(pdf_path: str) -> dict[int, _PageGeometry]:
    """Per-page image/blankness facts via pypdf. 1-indexed. Returns {} if
    pypdf is not importable or cannot read the file — the poppler fallback
    (_poppler_page_geometry) takes over then.
    """
    out: dict[int, _PageGeometry] = {}
    if PdfReader is None:
        return {}
    try:
        reader = PdfReader(pdf_path)
        for i, page in enumerate(reader.pages, 1):
            mb = page.mediabox
            w_in = float(mb.width) / 72.0
            h_in = float(mb.height) / 72.0
            page_scale_px = (w_in * _PAGE_SCALE_DPI) * (h_in * _PAGE_SCALE_DPI) \
                * _PAGE_SCALE_AREA_FRACTION
            res = page.get("/Resources")
            xobjects = None
            if res is not None:
                xobjects = res.get_object().get("/XObject")
            largest = _largest_image_px(xobjects)
            try:
                contents = page.get_contents()
                data = contents.get_data() if contents is not None else b""
            except Exception:
                data = b""
            ops = set(data.decode("latin1", errors="replace").split())
            has_marks = bool(ops & _MARKING_OPS)
            out[i] = _PageGeometry(largest, page_scale_px, has_marks)
    except Exception:
        return {}
    return out


def _poppler_page_geometry(pdf_path: str, page_count: int) -> dict[int, _PageGeometry]:
    """Fallback for _pypdf_page_geometry using poppler subprocesses only:
    `pdfimages -list` for per-page embedded-image pixel areas and `pdfinfo`
    for page sizes. Content-stream marking ops are not cheaply visible from
    poppler, so has_marks is None (unknown) — the blank-page downgrade then
    resolves blankness lazily via _page_renders_blank. Returns {} if poppler
    fails; callers keep pdf-inspector's verdict then.
    """
    try:
        img_out = subprocess.run(
            ["pdfimages", "-list", pdf_path],
            capture_output=True, text=True, check=True,
        ).stdout
        info_out = subprocess.run(
            ["pdfinfo", "-f", "1", "-l", str(page_count), pdf_path],
            capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    areas: dict[int, int] = {}
    for line in img_out.splitlines()[2:]:  # 2 header lines
        parts = line.split()
        if len(parts) < 5 or parts[2] != "image":  # skip mask/smask/stencil
            continue
        try:
            page, w, h = int(parts[0]), int(parts[3]), int(parts[4])
        except ValueError:
            continue
        areas[page] = max(areas.get(page, 0), w * h)
    sizes: dict[int, tuple[float, float]] = {}
    for m in re.finditer(r"Page\s+(\d+)\s+size:\s+([\d.]+)\s+x\s+([\d.]+)", info_out):
        sizes[int(m.group(1))] = (float(m.group(2)), float(m.group(3)))
    out: dict[int, _PageGeometry] = {}
    for page in range(1, page_count + 1):
        w_pts, h_pts = sizes.get(page, (612.0, 792.0))  # letter if unstated
        page_scale_px = (w_pts / 72.0 * _PAGE_SCALE_DPI) \
            * (h_pts / 72.0 * _PAGE_SCALE_DPI) * _PAGE_SCALE_AREA_FRACTION
        out[page] = _PageGeometry(areas.get(page, 0), page_scale_px, None)
    return out


def _page_geometry(pdf_path: str, page_count: int) -> dict[int, _PageGeometry]:
    return (_pypdf_page_geometry(pdf_path)
            or _poppler_page_geometry(pdf_path, page_count))


def _page_renders_blank(pdf_path: str, page: int) -> bool:
    """Last-resort blankness probe (poppler fallback path only): render the
    page at 15 DPI grayscale and check every pixel is (near-)white. Only
    called for pages already known to have zero text and zero images, so the
    cost is negligible and rare. Any failure returns False — "not proven
    blank" keeps the OCR flag (conservative direction).
    """
    try:
        proc = subprocess.run(
            ["pdftoppm", "-gray", "-r", "15",
             "-f", str(page), "-l", str(page), pdf_path],
            capture_output=True, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    data = proc.stdout
    if not data.startswith(b"P5"):
        return False
    # PGM P5 header: magic, then 3 tokens (width, height, maxval) separated
    # by whitespace/comments, then one whitespace byte, then the raster.
    i, tokens = 2, []
    while len(tokens) < 3 and i < len(data):
        while i < len(data) and data[i:i + 1].isspace():
            i += 1
        if data[i:i + 1] == b"#":
            while i < len(data) and data[i] != 0x0A:
                i += 1
            continue
        j = i
        while j < len(data) and not data[j:j + 1].isspace():
            j += 1
        tokens.append(data[i:j])
        i = j
    pixels = data[i + 1:]
    return bool(pixels) and min(pixels) >= 250


def _looks_like_normal_text(text: str) -> bool:
    """Heuristic: is this poppler page text ordinary readable prose?

    Used only to veto a pdf-inspector GARBLED flag. Requires substantial
    length, no U+FFFD, high printable/word-char density, and that most
    alphabetic words contain a vowel (broken-ToUnicode/CID output that
    happens to be alnum — hex runs, wrong-mapped glyph soup — fails the
    vowel test even when it passes the density test).
    """
    t = text.strip()
    if len(t) < _GARBLE_MIN_CHARS or "�" in t:
        return False
    clean = sum(ch.isalnum() or ch.isspace()
                or ch in ".,;:!?'\"()[]{}<>-–—_/\\%$&*+=#@°©®™|"
                for ch in t)
    if clean / len(t) < _GARBLE_MIN_CLEAN_RATIO:
        return False
    words = re.findall(r"[A-Za-z]{2,}", t)
    if not words or sum(len(w) for w in words) < 20:
        return False
    vowelled = sum(1 for w in words if re.search(r"[aeiouyAEIOUY]", w))
    return vowelled / len(words) >= _GARBLE_MIN_VOWEL_RATIO


def _choose_ocr_mode(ocr_pages: dict[int, list[Reason]],
                     page_text_chars: dict[int, int]) -> OcrMode | None:
    """Map the escalation set's reasons to exactly one ocrmypdf mode.

    page_text_chars is the *merged* per-page live-text evidence: the max of
    pdf-inspector's extraction preview length and poppler's pdftotext char
    count. The poppler side is load-bearing — pdf-inspector's preview
    returns empty for image-dominated pages that DO have live text (e.g. a
    scan with one born-digital caption), and deciding REDO-vs-SKIP_TEXT from
    the preview alone made ocrmypdf --skip-text skip exactly those pages.

    Preference order = the mode that cannot silently skip a broken page:
    - FORCE if any page is garbled / the doc has encoding issues: those
      pages *have* text (broken ToUnicode/CID maps extract as garbage), so
      --skip-text would skip precisely the pages that need fixing, and
      --redo-ocr preserves the garbage "real" text it masks out. Only
      rasterize-everything is safe.
    - REDO if any flagged page also yielded real live text (either engine):
      mixed content on the same page (e.g. a scan with one live caption).
      --skip-text skips any page with *any* text, so it would silently
      leave the image region un-OCR'd; --redo-ocr masks the real text and
      OCRs the rest. REDO also OCRs fully textless pages correctly, so it
      safely covers a mix of both kinds of flagged page.
    - SKIP_TEXT when every flagged page is simply scanned/textless: those
      pages have no text to wrongly skip, and untouched text pages are
      copied through byte-identical (no rasterization quality loss).
    """
    if not ocr_pages:
        return None
    all_reasons = {r for reasons in ocr_pages.values() for r in reasons}
    if Reason.GARBLED in all_reasons or Reason.ENCODING in all_reasons:
        return OcrMode.FORCE
    if any(page_text_chars.get(p, 0) > 0 for p in ocr_pages):
        return OcrMode.REDO
    return OcrMode.SKIP_TEXT


def triage(pdf_path: str) -> TriageResult:
    """Tier 0: classify pdf_path and produce routing decisions.

    All page numbers in the returned TriageResult are 1-indexed per
    contract.py; nothing downstream sees raw pdf-inspector objects.
    """
    doc = pdf_inspector.process_pdf(pdf_path)
    pdf_type = doc.pdf_type  # 'text_based'|'scanned'|'image_based'|'mixed'

    # Second-engine per-page facts, computed unconditionally: poppler text
    # chars (one pdftotext call for the whole doc) and pypdf image/blankness
    # geometry. These feed signal 4, the REDO-vs-SKIP_TEXT decision, and the
    # false-positive cross-checks — none of which may derive from
    # pdf-inspector's own classification.
    poppler_text = _poppler_page_text(pdf_path, doc.page_count)
    geometry = _page_geometry(pdf_path, doc.page_count)

    def _poppler_chars(page: int) -> int:
        return len((poppler_text.get(page) or "").strip())

    # Per-page extraction preview — needed to (a) confirm which pages truly
    # have extractable text and (b) detect mixed text+image pages for REDO.
    # Skipped when triage already proves no OCR anywhere (clean text_based
    # doc): the preview costs a full extraction pass.
    need_preview = (
        pdf_type != "text_based"
        or doc.has_encoding_issues
        or bool(doc.pages_needing_ocr)
    )
    # 1-indexed page -> extracted markdown length (PageMarkdown.page is
    # 0-indexed; normalize here, once).
    page_text_len: dict[int, int] = {}
    if need_preview:
        preview = pdf_inspector.extract_pages_markdown(pdf_path)
        for pm in preview.pages:
            page_text_len[pm.page + 1] = len((pm.markdown or "").strip())

    # --- escalation set: union of four independent signals (never trust
    # --- pages_needing_ocr alone; see module docstring).
    ocr_pages: dict[int, list[Reason]] = {}

    def _add(page: int, reason: Reason) -> None:
        reasons = ocr_pages.setdefault(page, [])
        if reason not in reasons:
            reasons.append(reason)

    # Signal 1: pdf-inspector's own verdict, with its per-page reason codes.
    # Both pages_needing_ocr and ocr_reasons_by_page verified 1-indexed.
    reasons_by_page = {e.page: list(e.reasons) for e in doc.ocr_reasons_by_page}
    for page in doc.pages_needing_ocr:
        raw_reasons = reasons_by_page.get(page) or []
        if not raw_reasons:
            # Flagged with no stated reason: classify by page content so the
            # mode choice stays safe (see _to_reason).
            raw_reasons = [""]
        for raw in raw_reasons:
            _add(page, _to_reason(raw, page_text_len.get(page, 0) > 0))
    # A reason entry can exist for a page missing from pages_needing_ocr
    # (belt and braces against upstream disagreement between the two fields).
    for page, raw_reasons in reasons_by_page.items():
        for raw in raw_reasons:
            _add(page, _to_reason(raw, page_text_len.get(page, 0) > 0))

    # Signal 2: doc-level has_encoding_issues. pdf-inspector does not
    # attribute it to pages, so conservatively tag every page — harmless,
    # because ENCODING forces --force-ocr, which is document-wide anyway.
    if doc.has_encoding_issues:
        for page in range(1, doc.page_count + 1):
            _add(page, Reason.ENCODING)

    # Signal 3: for any non-text_based doc, every page not *confirmed* to
    # have extractable text joins the set. This is the hedge against the
    # documented needs_ocr false-negative class (issue #227: ~4.8% of pages
    # got the unsafe verdict): a page in a scanned/mixed/image doc that
    # yielded no text gets OCR'd even if pdf-inspector said it was fine.
    if pdf_type != "text_based":
        for page in range(1, doc.page_count + 1):
            if page_text_len.get(page, 0) == 0:
                _add(page, Reason.NO_TEXT)

    # Signal 4 (UNCONDITIONAL — independent of pdf-inspector entirely): a
    # page carrying a page-scale embedded image while poppler sees only
    # sparse text is a scan (possibly with a live caption/heading on top).
    # This must not be gated on pdf_type: pdf-inspector suppresses per-page
    # OCR flags under a doc-level text_based verdict, so signal 3 never
    # fires exactly when pdf-inspector is wrong about the document — the
    # empirically confirmed silent-loss case (docling-trap2.pdf page 4:
    # text_based doc, zero flags, page's scanned body gone with exit 0).
    # If poppler is unavailable, chars read as 0 and the page-scale image
    # alone flags the page — the error direction is "OCR too much" (REDO
    # preserves live text), never "skip a scan".
    for page, geo in geometry.items():
        if (geo.largest_image_px >= geo.page_scale_px
                and _poppler_chars(page) < _SPARSE_TEXT_CHARS):
            _add(page, Reason.SCANNED)

    # --- false-positive cross-checks (downgrades) --------------------------
    # Pdf-inspector signals can be wrong in the *aggressive* direction too;
    # unchecked, they force destructive modes (--force-ocr rasterizes clean
    # text) or pointless OCR of blank pages. Each downgrade requires positive
    # second-engine evidence; missing evidence keeps the flag.

    # GARBLED cross-check: pdf-inspector flagged the page's text as garbage,
    # but if poppler extracts substantial, ordinary-looking prose from the
    # same page the flag is a false positive (observed on a 3-page clean
    # text doc with one blank middle page: both text pages were flagged
    # suspected_garbled_text, forcing --force-ocr on the whole doc). If
    # poppler's text is also garbage or absent, the garble is genuine and
    # FORCE stands.
    for page in list(ocr_pages):
        reasons = ocr_pages[page]
        if Reason.GARBLED in reasons and _looks_like_normal_text(
                poppler_text.get(page, "")):
            # Downgrade path: drop the garble reason; unflag the page
            # entirely if nothing else implicated it.
            reasons.remove(Reason.GARBLED)
            if not reasons:
                del ocr_pages[page]

    # Blank-page cross-check: a page flagged only as textless/scanned, where
    # poppler confirms zero text AND pypdf finds no image XObject AND the
    # content stream paints nothing, is genuinely blank — there is nothing
    # for OCR to recover, and flagging it only drags the doc through a
    # pointless (or, combined with a garble false positive, destructive)
    # OCR pass. Reasons implying *present* content (GARBLED, ENCODING,
    # VECTOR_TEXT) block the downgrade.
    for page in list(ocr_pages):
        reasons = ocr_pages[page]
        if not all(r in (Reason.NO_TEXT, Reason.SCANNED) for r in reasons):
            continue
        geo = geometry.get(page)
        if (geo is None or page not in poppler_text
                or _poppler_chars(page) != 0
                or geo.largest_image_px != 0):
            continue
        # has_marks False = pypdf saw a markless content stream; None = the
        # poppler fallback couldn't inspect the stream, so prove blankness
        # by rendering the page (near-)all-white at low DPI instead.
        if geo.has_marks is False or (geo.has_marks is None
                                      and _page_renders_blank(pdf_path, page)):
            # Downgrade path: genuinely blank page, unflag.
            del ocr_pages[page]

    ocr_pages = dict(sorted(ocr_pages.items()))

    # Merged live-text evidence for the mode choice (see _choose_ocr_mode:
    # poppler side is load-bearing for REDO-vs-SKIP_TEXT).
    page_text_chars = {
        page: max(page_text_len.get(page, 0), _poppler_chars(page))
        for page in range(1, doc.page_count + 1)
    }

    return TriageResult(
        page_count=doc.page_count,
        pdf_type=pdf_type,
        route=_choose_route(doc.page_count, doc.is_complex_layout,
                            list(doc.pages_with_tables)),
        ocr_pages=ocr_pages,
        ocr_mode=_choose_ocr_mode(ocr_pages, page_text_chars),
        is_complex=bool(doc.is_complex_layout),
        pages_with_tables=sorted(doc.pages_with_tables),   # 1-indexed upstream
        pages_with_columns=sorted(doc.pages_with_columns),  # 1-indexed upstream
    )


if __name__ == "__main__":
    _FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
    default_targets = [
        str(_FIXTURES / "probe-simple.pdf"),
        str(_FIXTURES / "probe-scanned.pdf"),
    ]
    # Newer fixtures (tests/make_fixtures.py) join the self-test when present.
    for optional in ("docling-trap.pdf", "blank-mid.pdf", "scan-with-caption.pdf"):
        if (_FIXTURES / optional).exists():
            default_targets.append(str(_FIXTURES / optional))
    targets = sys.argv[1:] or default_targets
    for target in targets:
        result = triage(target)
        print(f"== {target}")
        print(f"   page_count={result.page_count} pdf_type={result.pdf_type!r} "
              f"route={result.route.value} is_complex={result.is_complex}")
        print(f"   ocr_mode={result.ocr_mode.value if result.ocr_mode else None} "
              f"ocr_pages={{{', '.join(f'{p}: {[r.value for r in rs]}' for p, rs in result.ocr_pages.items()) or ''}}}")
        print(f"   pages_with_tables={result.pages_with_tables} "
              f"pages_with_columns={result.pages_with_columns}")
        # Sanity assertions on the bundled fixtures only.
        name = Path(target).name
        if name == "probe-simple.pdf":
            assert result.pdf_type == "text_based", result.pdf_type
            assert result.route is DocRoute.PDF_INSPECTOR, result.route
            assert result.ocr_pages == {} and result.ocr_mode is None
            assert result.pages_with_tables == [1], result.pages_with_tables
        elif name == "probe-scanned.pdf":
            assert result.pdf_type != "text_based", result.pdf_type
            assert list(result.ocr_pages) == [1], result.ocr_pages
            assert any(r in (Reason.SCANNED, Reason.NO_TEXT)
                       for r in result.ocr_pages[1]), result.ocr_pages
            assert result.ocr_mode is OcrMode.SKIP_TEXT, result.ocr_mode
        elif name == "docling-trap.pdf":
            # Trap page (full-page scan + live caption in a text_based doc)
            # must be caught by signal 4 and OCR'd in text-preserving mode.
            assert result.route is DocRoute.DOCLING, result.route
            assert 4 in result.ocr_pages, result.ocr_pages
            assert Reason.SCANNED in result.ocr_pages[4], result.ocr_pages
            assert result.ocr_mode is OcrMode.REDO, result.ocr_mode
        elif name == "blank-mid.pdf":
            # Clean text doc with a blank middle page: no OCR at all.
            assert result.ocr_pages == {}, result.ocr_pages
            assert result.ocr_mode is None, result.ocr_mode
        elif name == "scan-with-caption.pdf":
            # Scan with one live caption: flagged, and mode must be REDO
            # (--skip-text would skip the page because of the caption).
            assert 1 in result.ocr_pages, result.ocr_pages
            assert result.ocr_mode is OcrMode.REDO, result.ocr_mode
        print("   ok")
