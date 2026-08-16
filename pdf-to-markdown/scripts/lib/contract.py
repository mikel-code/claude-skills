"""Shared contract for the pdf-to-markdown pipeline.

FROZEN INTERFACE: every module in scripts/lib/ codes against this file. Do not
change shapes here without updating every consumer. references/design.md is
the design authority; this file is its executable schema.

Conventions that hold pipeline-wide:
- Pages are 1-indexed everywhere in this contract. pdf-inspector's
  PageMarkdown.page is 0-indexed while PdfResult.pages_needing_ocr is
  1-indexed — triage.py normalizes both to 1-indexed at the boundary, and
  nothing downstream may touch raw pdf-inspector objects.
- Every page of the input PDF appears in exactly one PageRecord. A page with
  no usable content is still a record (status FLAGGED_UNRESOLVED), never an
  omission. This is the no-silent-loss invariant; report.py asserts it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------- exit codes

EXIT_CLEAN = 0            # converted, no flagged pages
EXIT_ERROR = 1            # unexpected failure (traceback-worthy)
EXIT_NOT_CONFIGURED = 2   # missing dependency; deps.py printed instructions
EXIT_FLAGGED = 4          # converted, but >=1 page needs vision or is unresolved


# ------------------------------------------------------------------- enums

class PageStatus(str, Enum):
    EXTRACTED = "extracted"                  # tier 1, pdf-inspector
    EXTRACTED_DOCLING = "extracted-docling"  # tier 1, docling route
    OCR = "ocr"                              # tier 2 produced the text layer
    VISION = "vision"          # tier 3: Claude transcribed/patched this page
    FLAGGED = "flagged"        # needs tier 3; script can't resolve it
    FLAGGED_UNRESOLVED = "flagged-unresolved"  # --no-vision left it broken


class Reason(str, Enum):
    """Why a page was routed/flagged. Mirrors pdf-inspector reason codes plus
    pipeline-internal ones. Values are stable strings used in the JSON report."""
    SCANNED = "scanned"                # pdf-inspector: page is a scan
    NO_TEXT = "no_text"                # pdf-inspector: no text objects
    VECTOR_TEXT = "vector_text"        # pdf-inspector: text drawn as outlines
    GARBLED = "suspected_garbled_text"  # pdf-inspector: text quality layer
    ENCODING = "encoding_issues"       # doc-level has_encoding_issues
    LOW_RECALL = "low_recall"          # tier V: pdftotext disagreement
    OCR_FAILED = "ocr_failed"          # tier 2 ran but page still unextractable
    EMPTY_OUTPUT = "empty_output"      # extractor returned no markdown for a
                                       # page that triage said has content


class OcrMode(str, Enum):
    """ocrmypdf flag choice — ocr.py maps reasons to exactly one mode.
    Design rule: GARBLED/ENCODING pages have text, so --skip-text would skip
    them; they require FORCE. SCANNED/NO_TEXT documents use SKIP_TEXT; pages
    mixing real text and image regions use REDO."""
    SKIP_TEXT = "--skip-text"
    REDO = "--redo-ocr"
    FORCE = "--force-ocr"


class DocRoute(str, Enum):
    PDF_INSPECTOR = "pdf-inspector"
    DOCLING = "docling"


# ------------------------------------------------------------------ records

@dataclass
class PageRecord:
    """One per input page. The report is a list of these — complete, ordered,
    1-indexed, gapless."""
    page: int                                   # 1-indexed
    status: PageStatus
    reasons: list[Reason] = field(default_factory=list)
    tier_history: list[str] = field(default_factory=list)  # e.g. ["triage",
                                                # "extract:pdf-inspector",
                                                # "ocr:--skip-text", "verify"]
    recall: float | None = None                 # tier V per-page token recall
    markdown: str | None = None                 # this page's markdown chunk

    def to_json(self) -> dict:
        return {
            "page": self.page,
            "status": self.status.value,
            "reasons": [r.value for r in self.reasons],
            "tier_history": self.tier_history,
            "recall": self.recall,
            "has_markdown": bool(self.markdown),
        }


@dataclass
class TriageResult:
    """triage.py output. Never trust a single pdf-inspector signal: the
    escalation set is the union pages_needing_ocr ∪ has_encoding_issues-pages
    ∪ (pdf_type != text_based ⇒ all pages)."""
    page_count: int
    pdf_type: str                               # text_based|scanned|image_based|mixed
    route: DocRoute                             # document-level tier-1 choice
    ocr_pages: dict[int, list[Reason]]          # 1-indexed page -> reasons
    ocr_mode: OcrMode | None                    # None = no OCR needed
    is_complex: bool
    pages_with_tables: list[int]                # 1-indexed
    pages_with_columns: list[int]               # 1-indexed


@dataclass
class ExtractResult:
    """extract.py output for one extractor run. Pages absent from `pages`
    did not yield markdown — the orchestrator must flag them (EMPTY_OUTPUT),
    never drop them."""
    engine: DocRoute
    pages: dict[int, str]                       # 1-indexed page -> markdown
    title: str | None


@dataclass
class VerifyResult:
    """verify.py output. recall[p] in [0,1] = fraction of pdftotext's
    normalized tokens for page p found in the markdown for page p.
    flagged = pages under threshold. Pages pdftotext finds empty are skipped
    (recall None) — a scan has nothing to cross-check."""
    recall: dict[int, float | None]             # 1-indexed
    flagged: list[int]
    threshold: float


# ------------------------------------------------- module entry signatures
#
# triage.py    triage(pdf_path: str) -> TriageResult
# extract.py   extract(pdf_path: str, route: DocRoute) -> ExtractResult
# ocr.py       run_ocr(pdf_path: str, mode: OcrMode, out_dir: str,
#                      languages: str = "eng") -> str   # path to text-layer PDF
# verify.py    verify(pdf_path: str, pages: dict[int, str],
#                     threshold: float = 0.85) -> VerifyResult
# report.py    build_report(records: list[PageRecord], *, pdf_path: str,
#                           route: DocRoute) -> dict       # JSON report
#              render_report(report: dict) -> str          # human summary
#              assert_complete(records, page_count)        # no-silent-loss gate
# okf.py       okf_frontmatter(*, title, source_path, records, route) -> str
# deps.py      check_deps(*, need_ocr: bool = True) -> None  # sys.exit(2) w/ text
#
# All modules: stdlib + the deps above only; no module imports another except
# contract. Each module ends with a `if __name__ == "__main__":` self-test
# runnable as `uv run <module>.py <fixture.pdf>`.
