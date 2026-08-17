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
    WORD_ORDER = "word_order"          # tier V: tokens present but order
                                       # corrupted (high unigram, low bigram)
                                       # and not auto-repairable
    OCR_DEBRIS = "ocr_debris"          # OCR over image regions produced
                                       # mostly fragment noise
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
    (recall None) — a scan has nothing to cross-check.

    bigram[p] = same recall computed over adjacent-token PAIRS: the
    word-order signal. High unigram + low bigram = tokens present but
    scrambled (reversed table cells, shuffled TOC lines) — a class token
    recall is blind to by construction. order_flagged = pages whose unigram
    recall passed but bigram recall fell below the order threshold."""
    recall: dict[int, float | None]             # 1-indexed
    flagged: list[int]
    threshold: float
    bigram: dict[int, float | None] = field(default_factory=dict)
    order_flagged: list[int] = field(default_factory=list)


# ------------------------------------------------- module entry signatures
#
# triage.py    triage(pdf_path: str) -> TriageResult
# extract.py   extract(pdf_path: str, route: DocRoute) -> ExtractResult
# ocr.py       run_ocr(pdf_path: str, mode: OcrMode, out_dir: str,
#                      languages: str = "eng",
#                      pages: list[int] | None = None) -> str
#                  # path to text-layer PDF; `pages` scopes OCR to the
#                  # flagged pages — unscoped redo passes corrupt
#                  # born-digital pages (see ocr.py docstring)
# verify.py    verify(pdf_path: str, pages: dict[int, str],
#                     threshold: float = 0.85) -> VerifyResult
#              reference_pages(pdf_path: str) -> dict[int, str]
#                  # poppler -layout text per 1-indexed page; public so
#                  # repair.py can reuse the order-true reference without a
#                  # second pdftotext pass
# repair.py    sanitize(markdown: str) -> str
#                  # strip C0 controls (except \n\t) and private-use glyphs
#              fix_word_reversal(pages: dict[int, str],
#                                reference: dict[int, str]
#                                ) -> tuple[dict[int, str], dict[int, int]]
#                  # deterministic repair: a line whose REVERSED word order
#                  # matches the poppler reference (bigram evidence) while
#                  # the original doesn't gets reversed in place; returns
#                  # (pages, {page: lines_fixed})
#              clean_ocr_debris(markdown: str) -> tuple[str, int]
#                  # drop OCR-noise fragment lines (photo-page tesseract
#                  # debris); returns (markdown, lines_removed)
#              strip_icon_glyphs(markdown: str) -> tuple[str, int]
#                  # remove margin-badge icon glyphs misread as text
#                  # (standalone or paragraph-leading uppercase/symbol runs)
#              drop_duplicate_orphans(markdown: str) -> tuple[str, int]
#                  # remove wrapped-line fragments duplicated before their
#                  # full line (footnote/TOC reordering artifacts); only on
#                  # provable (truncation-tolerant) substring duplicates
#              fix_list_structure(pages: dict[int, str],
#                                 reference: dict[int, str]
#                                 ) -> tuple[dict[int, str], dict[int, int]]
#                  # reference-indent-guided list repair: re-levels items,
#                  # re-attaches continuation paragraphs, and drops invented
#                  # markers on top of printed roman/letter markers — all
#                  # evidence-gated on poppler's marker/text column layout
# verify.py    orphan_residue_suspect(markdown: str) -> bool
#                  # page-level fused-duplicate-fragment detector (invisible
#                  # to unigram AND bigram recall — duplication only adds
#                  # tokens); feeds order_flagged like toc_order_suspect
# verify.py    toc_order_suspect(markdown: str) -> bool
#                  # TOC-shaped page with broken entry structure — feeds
#                  # order_flagged inside verify() regardless of bigram
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
