#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pdf-inspector==1.14.2",
#     "docling==2.120.1",
#     "pypdf",
# ]
# ///
"""Integration harness for the pdf-to-markdown pipeline.

Imports scripts/lib/*.py by path and runs contract-level checks against the
tests/fixtures/*.pdf corpus (see make_fixtures.py). The lib modules are being
written concurrently by other agents, so every module is imported defensively:
if a module doesn't exist yet (or fails to import for any reason), every
check that depends on it is recorded as SKIPPED rather than crashing the
harness. Checks that *can* run either PASS or FAIL.

Contract reference: scripts/lib/contract.py (frozen — read, never edited by
this file). Design reference: references/design.md.

Run: uv run tests/run_tests.py
Exit: 0 if no check FAILed (SKIPs are fine), 1 if any check FAILed.
"""

from __future__ import annotations

import importlib
import sys
import tempfile
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
LIB_DIR = TESTS_DIR.parent / "scripts" / "lib"

sys.path.insert(0, str(LIB_DIR))

import contract  # noqa: E402  (frozen interface; must load first)

PROBE_SIMPLE = FIXTURES_DIR / "probe-simple.pdf"
PROBE_SCANNED = FIXTURES_DIR / "probe-scanned.pdf"
MIXED = FIXTURES_DIR / "mixed.pdf"
DOCLING_TRAP = FIXTURES_DIR / "docling-trap.pdf"
BLANK_MID = FIXTURES_DIR / "blank-mid.pdf"
SCAN_WITH_CAPTION = FIXTURES_DIR / "scan-with-caption.pdf"

# Known-good synthetic markdown for probe-simple, used as a fallback for
# verify.py checks when extract.py isn't available yet (or to keep the
# verify checks independent of extract.py's own correctness). Content
# mirrors tests/make_fixtures.py::make_probe_simple exactly.
SYNTHETIC_PROBE_SIMPLE_PAGES = {
    1: (
        "# Quarterly Report\n\n"
        "## Revenue Summary\n\n"
        "Revenue grew twelve percent year over year, driven by subscription "
        "renewals and two enterprise contracts signed in March. "
        "Operating costs held flat while headcount rose by four engineers.\n\n"
        "| Region | Q1 | Q2 |\n"
        "| --- | --- | --- |\n"
        "| EMEA | 1.2 | 1.4 |\n"
        "| Americas | 2.1 | 2.3 |\n"
    ),
    2: (
        "# Outlook\n\n"
        "Second-half guidance remains unchanged pending the audit.\n"
    ),
}


# --------------------------------------------------------------- mini-runner

class SkipCheck(Exception):
    """Raise from inside a check body for an environment-level reason that
    isn't 'module not present' (e.g. an optional engine like docling isn't
    installed) — recorded as SKIP, not FAIL."""


_results: list[tuple[str, str, str, str]] = []  # (module, check, status, detail)


def run_check(module: str, name: str, fn) -> None:
    try:
        fn()
    except SkipCheck as e:
        _results.append((module, name, "SKIP", str(e)))
    except AssertionError as e:
        _results.append((module, name, "FAIL", str(e)))
    except Exception as e:  # noqa: BLE001 — a check raising anything is a FAIL
        _results.append((module, name, "FAIL", f"{type(e).__name__}: {e}"))
    else:
        _results.append((module, name, "PASS", ""))


def skip_all(module: str, names: list[str], reason: str) -> None:
    for name in names:
        _results.append((module, name, "SKIP", reason))


def try_import(modname: str):
    try:
        return importlib.import_module(modname), None
    except Exception as e:  # noqa: BLE001 — any import failure => skip module
        return None, e


# ------------------------------------------------------------------- triage

TRIAGE_CHECKS = [
    "probe_simple_is_text_based",
    "probe_simple_no_ocr_pages",
    "probe_scanned_flagged_with_reasons",
    "mixed_page2_flagged",
    "mixed_page1_not_ocr_flagged",
    "docling_trap_page4_flagged_scanned_redo",
    "blank_mid_no_ocr_pages",
    "scan_with_caption_selects_redo",
]


def run_triage_checks():
    mod, err = try_import("triage")
    if mod is None:
        skip_all("triage", TRIAGE_CHECKS, f"SKIPPED (module not present): {err}")
        return

    def probe_simple_is_text_based():
        result = mod.triage(str(PROBE_SIMPLE))
        assert isinstance(result, contract.TriageResult), f"got {type(result)}"
        assert result.pdf_type == "text_based", f"pdf_type={result.pdf_type!r}"
        assert result.page_count == 2, f"page_count={result.page_count}"

    def probe_simple_no_ocr_pages():
        result = mod.triage(str(PROBE_SIMPLE))
        assert not result.ocr_pages, f"expected no OCR pages, got {result.ocr_pages}"

    def probe_scanned_flagged_with_reasons():
        result = mod.triage(str(PROBE_SCANNED))
        assert result.ocr_pages, "expected >=1 flagged page for a scanned PDF"
        for page, reasons in result.ocr_pages.items():
            assert reasons, f"page {page} flagged with empty reasons list"
            for r in reasons:
                assert isinstance(r, contract.Reason), f"non-Reason entry: {r!r}"

    def mixed_page2_flagged():
        result = mod.triage(str(MIXED))
        assert result.page_count == 2, f"page_count={result.page_count}"
        assert 2 in result.ocr_pages, f"page 2 not flagged; ocr_pages={result.ocr_pages}"
        assert result.ocr_pages[2], "page 2 flagged with empty reasons"

    def mixed_page1_not_ocr_flagged():
        result = mod.triage(str(MIXED))
        assert 1 not in result.ocr_pages, (
            f"page 1 (born-digital text) wrongly OCR-flagged: {result.ocr_pages.get(1)}"
        )

    def docling_trap_page4_flagged_scanned_redo():
        # The silent-loss trap: doc-level text_based verdict suppresses all
        # pdf-inspector per-page flags, so only an independent per-page
        # signal can catch page 4 (full-page scan + two live text lines).
        result = mod.triage(str(DOCLING_TRAP))
        assert result.route is contract.DocRoute.DOCLING, f"route={result.route}"
        assert 4 in result.ocr_pages, f"trap page not flagged; ocr_pages={result.ocr_pages}"
        assert contract.Reason.SCANNED in result.ocr_pages[4], (
            f"expected SCANNED on page 4, got {result.ocr_pages[4]}"
        )
        assert result.ocr_mode is contract.OcrMode.REDO, (
            f"expected REDO (live lines on the trap page), got {result.ocr_mode}"
        )

    def blank_mid_no_ocr_pages():
        # False-garble regression: clean text pages must not be flagged
        # suspected_garbled_text, and the blank middle page must not drag
        # the doc through OCR — no OCR at all for this fixture.
        result = mod.triage(str(BLANK_MID))
        assert not result.ocr_pages, f"expected no OCR pages, got {result.ocr_pages}"
        assert result.ocr_mode is None, f"expected no OCR mode, got {result.ocr_mode}"

    def scan_with_caption_selects_redo():
        # A scan page with one live caption: --skip-text would skip the
        # whole page (it has text), silently leaving the scan un-OCR'd.
        result = mod.triage(str(SCAN_WITH_CAPTION))
        assert 1 in result.ocr_pages, f"page not flagged; ocr_pages={result.ocr_pages}"
        assert result.ocr_mode is contract.OcrMode.REDO, (
            f"expected REDO for scan+caption, got {result.ocr_mode}"
        )

    run_check("triage", "probe_simple_is_text_based", probe_simple_is_text_based)
    run_check("triage", "probe_simple_no_ocr_pages", probe_simple_no_ocr_pages)
    run_check("triage", "probe_scanned_flagged_with_reasons", probe_scanned_flagged_with_reasons)
    run_check("triage", "mixed_page2_flagged", mixed_page2_flagged)
    run_check("triage", "mixed_page1_not_ocr_flagged", mixed_page1_not_ocr_flagged)
    run_check("triage", "docling_trap_page4_flagged_scanned_redo", docling_trap_page4_flagged_scanned_redo)
    run_check("triage", "blank_mid_no_ocr_pages", blank_mid_no_ocr_pages)
    run_check("triage", "scan_with_caption_selects_redo", scan_with_caption_selects_redo)


# ------------------------------------------------------------------ extract

EXTRACT_CHECKS = [
    "probe_simple_pdf_inspector_engine",
    "probe_simple_docling_engine",
]


def run_extract_checks():
    mod, err = try_import("extract")
    if mod is None:
        skip_all("extract", EXTRACT_CHECKS, f"SKIPPED (module not present): {err}")
        return

    def _assert_two_nonempty_pages(result):
        assert isinstance(result, contract.ExtractResult), f"got {type(result)}"
        assert set(result.pages.keys()) == {1, 2}, f"pages={sorted(result.pages)}"
        for page, md in result.pages.items():
            assert md and md.strip(), f"page {page} markdown is empty"

    def probe_simple_pdf_inspector_engine():
        result = mod.extract(str(PROBE_SIMPLE), contract.DocRoute.PDF_INSPECTOR)
        _assert_two_nonempty_pages(result)

    def probe_simple_docling_engine():
        try:
            result = mod.extract(str(PROBE_SIMPLE), contract.DocRoute.DOCLING)
        except ModuleNotFoundError as e:
            raise SkipCheck(f"docling not installed: {e}") from e
        except ImportError as e:
            raise SkipCheck(f"docling import failed: {e}") from e
        _assert_two_nonempty_pages(result)

    run_check("extract", "probe_simple_pdf_inspector_engine", probe_simple_pdf_inspector_engine)
    run_check("extract", "probe_simple_docling_engine", probe_simple_docling_engine)


# ----------------------------------------------------------------------- ocr

OCR_CHECKS = ["probe_scanned_skip_text_contains_quarterly"]


def run_ocr_checks():
    mod, err = try_import("ocr")
    if mod is None:
        skip_all("ocr", OCR_CHECKS, f"SKIPPED (module not present): {err}")
        return

    def probe_scanned_skip_text_contains_quarterly():
        import subprocess

        with tempfile.TemporaryDirectory(prefix="pdf2md-ocr-test-") as tmp:
            out_path = mod.run_ocr(str(PROBE_SCANNED), contract.OcrMode.SKIP_TEXT, tmp)
            assert out_path, "run_ocr returned falsy path"
            assert Path(out_path).exists(), f"output pdf missing: {out_path}"
            text = subprocess.run(
                ["pdftotext", str(out_path), "-"],
                check=True, capture_output=True, text=True,
            ).stdout
            assert "Quarterly" in text, f"OCR output missing 'Quarterly': {text[:200]!r}"

    run_check("ocr", "probe_scanned_skip_text_contains_quarterly",
               probe_scanned_skip_text_contains_quarterly)


# -------------------------------------------------------------------- verify

VERIFY_CHECKS = [
    "probe_simple_roundtrip_high_recall",
    "deleted_sentence_flags",
]


def _get_probe_simple_pages(extract_mod):
    """Prefer real extract() output; fall back to known-good synthetic
    markdown so verify.py checks don't hard-depend on extract.py."""
    if extract_mod is not None:
        try:
            result = extract_mod.extract(str(PROBE_SIMPLE), contract.DocRoute.PDF_INSPECTOR)
            if set(result.pages.keys()) == {1, 2} and all(result.pages.values()):
                return dict(result.pages)
        except Exception:
            pass
    return dict(SYNTHETIC_PROBE_SIMPLE_PAGES)


def run_verify_checks():
    mod, err = try_import("verify")
    if mod is None:
        skip_all("verify", VERIFY_CHECKS, f"SKIPPED (module not present): {err}")
        return

    extract_mod, _ = try_import("extract")

    def probe_simple_roundtrip_high_recall():
        pages = _get_probe_simple_pages(extract_mod)
        result = mod.verify(str(PROBE_SIMPLE), pages)
        assert isinstance(result, contract.VerifyResult), f"got {type(result)}"
        for page, recall in result.recall.items():
            if recall is None:
                continue
            assert recall >= 0.85, f"page {page} recall too low: {recall}"
        assert not result.flagged, f"unexpected flagged pages on clean round-trip: {result.flagged}"

    def deleted_sentence_flags():
        pages = _get_probe_simple_pages(extract_mod)
        page1 = pages[1]
        deleted_sentence = "Operating costs held flat while headcount rose by four engineers."
        reduced = page1.replace(deleted_sentence, "").strip()
        assert reduced != page1, "fixture sentence not found in page 1 markdown to delete"
        broken_pages = dict(pages)
        broken_pages[1] = reduced
        result = mod.verify(str(PROBE_SIMPLE), broken_pages)
        assert 1 in result.flagged, (
            f"expected page 1 flagged after sentence deletion; "
            f"recall={result.recall.get(1)} flagged={result.flagged}"
        )

    run_check("verify", "probe_simple_roundtrip_high_recall", probe_simple_roundtrip_high_recall)
    run_check("verify", "deleted_sentence_flags", deleted_sentence_flags)


# -------------------------------------------------------------- report / okf

REPORT_CHECKS = [
    "assert_complete_raises_on_gap",
    "assert_complete_passes_when_complete",
    "build_report_well_formed",
    "render_report_nonempty",
]
OKF_CHECKS = ["okf_frontmatter_well_formed"]


def _synthetic_records(n: int) -> list["contract.PageRecord"]:
    return [
        contract.PageRecord(
            page=p,
            status=contract.PageStatus.EXTRACTED,
            reasons=[],
            tier_history=["triage", "extract:pdf-inspector"],
            recall=0.97,
            markdown=f"# Page {p}\n\nSynthetic content for page {p}.\n",
        )
        for p in range(1, n + 1)
    ]


def run_report_checks():
    mod, err = try_import("report")
    if mod is None:
        skip_all("report", REPORT_CHECKS, f"SKIPPED (module not present): {err}")
        return None

    def assert_complete_raises_on_gap():
        records = _synthetic_records(3)
        del records[1]  # remove page 2 -> gap
        raised = False
        try:
            mod.assert_complete(records, 3)
        except Exception:
            raised = True
        assert raised, "assert_complete did not raise on a page-count gap"

    def assert_complete_passes_when_complete():
        records = _synthetic_records(3)
        mod.assert_complete(records, 3)  # must not raise

    def build_report_well_formed():
        records = _synthetic_records(2)
        report = mod.build_report(records, pdf_path=str(PROBE_SIMPLE), route=contract.DocRoute.PDF_INSPECTOR)
        assert isinstance(report, dict), f"build_report did not return a dict: {type(report)}"
        import json
        json.dumps(report)  # must be JSON-serializable

    def render_report_nonempty():
        records = _synthetic_records(2)
        report = mod.build_report(records, pdf_path=str(PROBE_SIMPLE), route=contract.DocRoute.PDF_INSPECTOR)
        text = mod.render_report(report)
        assert isinstance(text, str) and text.strip(), "render_report returned empty/non-string"

    run_check("report", "assert_complete_raises_on_gap", assert_complete_raises_on_gap)
    run_check("report", "assert_complete_passes_when_complete", assert_complete_passes_when_complete)
    run_check("report", "build_report_well_formed", build_report_well_formed)
    run_check("report", "render_report_nonempty", render_report_nonempty)
    return mod


def run_okf_checks():
    mod, err = try_import("okf")
    if mod is None:
        skip_all("okf", OKF_CHECKS, f"SKIPPED (module not present): {err}")
        return

    def okf_frontmatter_well_formed():
        records = _synthetic_records(2)
        text = mod.okf_frontmatter(
            title="Quarterly Report",
            source_path=str(PROBE_SIMPLE),
            records=records,
            route=contract.DocRoute.PDF_INSPECTOR,
        )
        assert isinstance(text, str) and text.strip(), "okf_frontmatter returned empty/non-string"
        lines = text.strip().splitlines()
        assert lines[0].strip() == "---", f"frontmatter doesn't open with '---': {lines[0]!r}"
        assert "---" in [l.strip() for l in lines[1:]], "frontmatter has no closing '---'"

    run_check("okf", "okf_frontmatter_well_formed", okf_frontmatter_well_formed)


# ----------------------------------------- repair2 (field-defect round 2)
#
# Regression checks for the second field-hardening round against
# CCBPBAbril2026.md: icon-glyph junk (repair.strip_icon_glyphs), duplicate
# orphan fragments (repair.drop_duplicate_orphans), and structural TOC
# order detection (verify.toc_order_suspect). Fixture strings are verbatim
# (abridged) excerpts of the real defect document.

REPAIR2_CHECKS = [
    "icon_glyphs_stripped",
    "icon_glyphs_keep_negatives",
    "duplicate_orphans_dropped",
    "duplicate_orphans_keep_negatives",
    "toc_order_suspect_positive_negative",
]

_R2_ICON_JUNK = (
    "(Y La aplicación de estos principios de claridad y transparencia.\n\n"
    "- (Y Cuando son varias las cuestiones controvertidas planteadas.\n\n"
    "Y Así, a efectos de acreditar la realización de estas comunicaciones.\n\n"
    "¡Y\n\nCÓ S\n\n"
    "## ¡0 Se ha considerado, en los informes emitidos, que las entidades:\n"
)
_R2_ICON_KEEP = (
    "## I Criterios de buenas prácticas aplicables\n\n"
    "## A) Denegar su aplicación, de forma inmediata\n\n"
    "TS Aquellas en las que se excluyen los beneficios del fiador.\n\n"
    "«1 Los sujetos obligados identificarán a cuantas personas físicas.\n\n"
    "- 1 Resulta imprescindible su previsión y concreción.\n\n"
    "Y ello en razón de que no se precisa sino acreditar la aceptación.\n\n"
    "EE. UU. La regulación aplicable difiere de la europea.\n\n19\n"
)
_R2_ORPHAN_FUSED = (
    "entes extremos: 4 El artículo 7.3 de la orden señala lo siguiente: "
    "«Los documentos contractuales deberán recoger de forma explícita y "
    "clara los siguientes extremos:"
)
_R2_ORPHAN_TRUNC = (
    "nás que establezca el  Banco de España». Los demás que establezca el "
    "Banco de España»."
)
_R2_ORPHAN_KEEP = (
    "Los demás que establezca el Banco de España». 5 La norma indica lo "
    "siguiente: «Los demás que establezca el Banco de España»."
)
_R2_TOC_BROKEN = "\n".join(
    ["- único 3.1 Identificador único 165",
     "- de las transferencias 3.2 Irrevocabilidad de las transferencias 167",
     "- y gastos de las operaciones 3.3 Comisiones y gastos 167",
     "- de órdenes de pago 3.4 Recepción de órdenes de pago 169",
     "- de abono 3.5 Fecha de abono 169",
     "- amediatos 3.6 Pagos inmediatos 170",
     "- um 3.6.1 Bizum 170",
     "- transferencias 3.8 Límites de transferencias 173",
     "- generales 4.1 Consideraciones generales 175"])
_R2_TOC_CLEAN = "\n".join(
    [f"- 2.{i} Entrada de prueba número {i} {60 + i}" for i in range(1, 10)])


def run_repair2_checks():
    repair_mod, repair_err = try_import("repair")
    verify_mod, verify_err = try_import("verify")

    def icon_glyphs_stripped():
        if repair_mod is None:
            raise SkipCheck(f"repair not present: {repair_err}")
        if not hasattr(repair_mod, "strip_icon_glyphs"):
            raise SkipCheck("repair.strip_icon_glyphs not implemented")
        cleaned, n = repair_mod.strip_icon_glyphs(_R2_ICON_JUNK)
        assert n == 6, f"expected 6 strips, got {n}"
        assert cleaned.startswith("La aplicación"), cleaned[:40]
        assert "- Cuando son varias" in cleaned
        assert "\nAsí, a efectos" in cleaned
        assert "## Se ha considerado" in cleaned
        assert "¡Y" not in cleaned and "CÓ S" not in cleaned

    def icon_glyphs_keep_negatives():
        if repair_mod is None:
            raise SkipCheck(f"repair not present: {repair_err}")
        if not hasattr(repair_mod, "strip_icon_glyphs"):
            raise SkipCheck("repair.strip_icon_glyphs not implemented")
        kept, n = repair_mod.strip_icon_glyphs(_R2_ICON_KEEP)
        assert n == 0 and kept == _R2_ICON_KEEP, f"stripped={n}"

    def duplicate_orphans_dropped():
        if repair_mod is None:
            raise SkipCheck(f"repair not present: {repair_err}")
        if not hasattr(repair_mod, "drop_duplicate_orphans"):
            raise SkipCheck("repair.drop_duplicate_orphans not implemented")
        fixed, n = repair_mod.drop_duplicate_orphans(_R2_ORPHAN_FUSED)
        assert n == 1 and fixed.startswith("4 El artículo 7.3"), (n, fixed[:40])
        fixed, n = repair_mod.drop_duplicate_orphans(_R2_ORPHAN_TRUNC)
        assert n == 1 and fixed == (
            "Los demás que establezca el Banco de España»."), (n, fixed)

    def duplicate_orphans_keep_negatives():
        if repair_mod is None:
            raise SkipCheck(f"repair not present: {repair_err}")
        if not hasattr(repair_mod, "drop_duplicate_orphans"):
            raise SkipCheck("repair.drop_duplicate_orphans not implemented")
        kept, n = repair_mod.drop_duplicate_orphans(_R2_ORPHAN_KEEP)
        assert n == 0 and kept == _R2_ORPHAN_KEEP, f"dropped={n}"

    def toc_order_suspect_positive_negative():
        if verify_mod is None:
            raise SkipCheck(f"verify not present: {verify_err}")
        if not hasattr(verify_mod, "toc_order_suspect"):
            raise SkipCheck("verify.toc_order_suspect not implemented")
        assert verify_mod.toc_order_suspect(_R2_TOC_BROKEN), (
            "broken TOC excerpt not suspect")
        assert not verify_mod.toc_order_suspect(_R2_TOC_CLEAN), (
            "clean TOC excerpt wrongly suspect")
        assert not verify_mod.toc_order_suspect(
            SYNTHETIC_PROBE_SIMPLE_PAGES[1]), "prose page wrongly suspect"

    for name in REPAIR2_CHECKS:
        run_check("repair2", name, locals()[name])


# -------------------------------------------------------- end-to-end (e2e)

E2E_CHECKS = [
    "probe_simple_converts_clean",
    "docling_trap_page_not_silently_lost",
    "vision_flow_action_block_and_reconcile",
]

ORCHESTRATOR = TESTS_DIR.parent / "scripts" / "pdf_to_markdown.py"


def _run_orchestrator(pdf: Path, tmp: str):
    """Run the orchestrator as a real subprocess (its own uv env), with
    --no-vision so exit codes are deterministic. Returns
    (proc, markdown_text_or_None, report_dict_or_None)."""
    import json
    import subprocess

    out_md = Path(tmp) / (pdf.stem + ".md")
    try:
        proc = subprocess.run(
            ["uv", "run", str(ORCHESTRATOR), str(pdf),
             "-o", str(out_md), "--no-vision"],
            capture_output=True, text=True, timeout=600,
        )
    except FileNotFoundError as e:  # uv itself missing
        raise SkipCheck(f"uv not on PATH: {e}") from e
    if proc.returncode == contract.EXIT_NOT_CONFIGURED:
        raise SkipCheck(f"orchestrator deps not configured: {proc.stderr[-200:]!r}")
    md = out_md.read_text(encoding="utf-8") if out_md.exists() else None
    report_path = out_md.with_suffix(".report.json")
    report = None
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
    return proc, md, report


def run_e2e_checks():
    if not ORCHESTRATOR.exists():
        skip_all("e2e", E2E_CHECKS, f"SKIPPED (orchestrator not present): {ORCHESTRATOR}")
        return

    def probe_simple_converts_clean():
        with tempfile.TemporaryDirectory(prefix="pdf2md-e2e-") as tmp:
            proc, md, report = _run_orchestrator(PROBE_SIMPLE, tmp)
            assert proc.returncode == contract.EXIT_CLEAN, (
                f"exit={proc.returncode}; stderr tail: {proc.stderr[-300:]!r}"
            )
            assert report is not None, "no .report.json written"
            assert report.get("clean") is True, f"report not clean: {report.get('summary')}"
            statuses = {p["page"]: p["status"] for p in report["pages"]}
            assert set(statuses) == {1, 2}, f"pages in report: {sorted(statuses)}"
            assert all(s.startswith("extracted") for s in statuses.values()), statuses
            assert md and "Quarterly Report" in md, "markdown missing page-1 heading"

    def docling_trap_page_not_silently_lost():
        # The confirmed CRITICAL failure shape was: exit 0, page 4 reported
        # extracted-*, and the scanned body ('Revenue Summary' etc.) absent
        # from the markdown — silent content loss with a clean report. Any
        # of these being false is acceptable (page OCR'd, or flagged, or
        # content present, or a non-clean exit); all three together is the bug.
        import shutil as _shutil

        for tool in ("ocrmypdf", "tesseract"):
            if _shutil.which(tool) is None:
                raise SkipCheck(f"{tool} not installed; trap page needs the OCR tier")
        with tempfile.TemporaryDirectory(prefix="pdf2md-e2e-") as tmp:
            proc, md, report = _run_orchestrator(DOCLING_TRAP, tmp)
            assert report is not None, (
                f"no .report.json written; exit={proc.returncode} "
                f"stderr tail: {proc.stderr[-300:]!r}"
            )
            trap = next((p for p in report["pages"] if p["page"] == 4), None)
            assert trap is not None, f"page 4 missing from report: {report['pages']}"
            scanned_body_present = bool(md) and (
                "Revenue Summary" in md or "Quarterly Report" in md
            )
            silently_lost = (
                proc.returncode == 0
                and trap["status"].startswith("extracted")
                and not scanned_body_present
            )
            assert not silently_lost, (
                f"trap page silently lost: exit 0, page 4 status={trap['status']!r}, "
                f"scanned body absent from markdown"
            )

    def vision_flow_action_block_and_reconcile():
        # Exit-4 runs must print the ACTION REQUIRED block on stdout (so the
        # agent needs no report.json parsing), and --reconcile must flip a
        # vision-patched page to status 'vision' and exit 0. Forced flagged
        # run: bogus OCR language on the scanned fixture — fully offline.
        import json
        import re as _re
        import subprocess

        with tempfile.TemporaryDirectory(prefix="pdf2md-e2e-") as tmp:
            out_md = Path(tmp) / "out.md"
            proc = subprocess.run(
                ["uv", "run", str(ORCHESTRATOR), str(PROBE_SCANNED),
                 "-l", "xyz", "-o", str(out_md)],
                capture_output=True, text=True, timeout=600,
            )
            assert proc.returncode == contract.EXIT_FLAGGED, (
                f"exit={proc.returncode}; stderr tail: {proc.stderr[-300:]!r}")
            assert "ACTION REQUIRED" in proc.stdout, "action block not on stdout"
            assert "--reconcile" in proc.stdout, "reconcile step not named"
            text = out_md.read_text(encoding="utf-8")
            patched = _re.sub(
                r"<!-- page 1: flagged[^\n]*-->",
                "<!-- page 1: transcribed by vision -->\n\n# Patched",
                text)
            out_md.write_text(patched, encoding="utf-8")
            rproc = subprocess.run(
                ["uv", "run", str(ORCHESTRATOR), "--reconcile", str(out_md)],
                capture_output=True, text=True, timeout=600,
            )
            assert rproc.returncode == contract.EXIT_CLEAN, (
                f"reconcile exit={rproc.returncode}: {rproc.stderr[-200:]!r}")
            report = json.loads(
                out_md.with_suffix(".report.json").read_text(encoding="utf-8"))
            assert report["pages"][0]["status"] == "vision", report["pages"][0]
            assert report["clean"] is True and not report["vision"]["pending_pages"]

    run_check("e2e", "probe_simple_converts_clean", probe_simple_converts_clean)
    run_check("e2e", "docling_trap_page_not_silently_lost", docling_trap_page_not_silently_lost)
    run_check("e2e", "vision_flow_action_block_and_reconcile", vision_flow_action_block_and_reconcile)


# --------------------------------------------------------------------- main

def print_table() -> bool:
    """Print PASS/FAIL/SKIP table. Returns True if any check FAILed."""
    width_module = max(len(m) for m, *_ in _results) if _results else 6
    width_name = max(len(n) for _, n, *_ in _results) if _results else 4

    print()
    print(f"{'MODULE':<{width_module}}  {'CHECK':<{width_name}}  STATUS  DETAIL")
    print("-" * (width_module + width_name + 40))
    any_fail = False
    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    for module, name, status, detail in _results:
        counts[status] += 1
        if status == "FAIL":
            any_fail = True
        detail_shown = detail if status != "PASS" else ""
        print(f"{module:<{width_module}}  {name:<{width_name}}  {status:<6}  {detail_shown}")
    print("-" * (width_module + width_name + 40))
    print(f"Total: {len(_results)}   PASS={counts['PASS']}  FAIL={counts['FAIL']}  SKIP={counts['SKIP']}")
    return any_fail


def main() -> None:
    for fixture in (PROBE_SIMPLE, PROBE_SCANNED, MIXED,
                    DOCLING_TRAP, BLANK_MID, SCAN_WITH_CAPTION):
        if not fixture.exists():
            print(f"error: fixture missing: {fixture} — run make_fixtures.py first", file=sys.stderr)
            sys.exit(1)

    run_triage_checks()
    run_extract_checks()
    run_ocr_checks()
    run_verify_checks()
    run_repair2_checks()
    run_report_checks()
    run_okf_checks()
    run_e2e_checks()

    any_fail = print_table()
    sys.exit(1 if any_fail else 0)


if __name__ == "__main__":
    main()
