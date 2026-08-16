# /// script
# requires-python = ">=3.10"
# ///
"""Tier V — cross-engine silent-loss detector (design.md "Tier V").

pdf-inspector has a documented silent-content-loss bug class: pages where text
exists but quietly vanishes from the markdown while its `confidence` stays 1.0
(confidence measures classification, not extraction completeness). poppler's
pdftotext is a different engine with different failure modes; on healthy PDFs
the two agree, so per-page divergence is a cheap, high-signal flag.

Direction matters: we compute RECALL of poppler's tokens against the markdown
— i.e. "is the markdown MISSING text poppler found?" — never the reverse.
Markdown legitimately adds tokens poppler never saw (heading markers, table
pipes, link URLs, docling artifacts), and none of that should count against a
page. Low recall doesn't mean poppler is right; it means the page warrants a
vision look (design.md Tier 3).

Metric decisions (each is a deliberate false-negative/false-positive trade —
this module's false-negative rate is the pipeline's correctness ceiling):

* Normalization: NFKC on both sides first (folds fi/fl ligatures, fullwidth
  forms, etc. — engines disagree on whether ligatures are decomposed), then
  casefold. Tokens are word-character runs; punctuation and markdown syntax
  characters (#, *, |, `, >, [, ], parens, hyphens) are token separators and
  simply vanish, which does most of the "markdown stripping" for free.
  Underscores are mapped to spaces pre-tokenization because \\w includes "_"
  and _emphasis_ would otherwise mint tokens like "_word_" absent from the
  reference.
* Explicit markdown stripping (the part tokenization can't do): fenced-code
  delimiter lines dropped (fence *content* is real text and kept), link/image
  syntax reduced to its text (URLs would pollute the joined-stream fallback
  below with accidental substrings), inline HTML tags dropped.
* Hyphenation: pdf-inspector rejoins hyphenated line breaks; pdftotext
  -layout does NOT ("sub-\\nscription" stays split). A naive comparison would
  flag every hyphenated paragraph. Two complementary mechanisms:
  (1) the reference is de-hyphenated before tokenization (word-\\nword ->
  wordword), matching pdf-inspector's rejoin behavior; (2) a
  segmentation-insensitive fallback — a still-unmatched reference token also
  counts if it appears as a substring of the markdown's tokens concatenated
  with no separators. (2) also absorbs the inverse case, where (1) welds a
  genuine compound ("well-\\nknown" -> "wellknown") that the markdown keeps
  as two tokens: "well"+"known" are adjacent in the joined stream. The
  fallback requires token length >= FALLBACK_MIN_LEN (5) — short tokens are
  near-guaranteed accidental substrings ("the" in "weather") and are almost
  always matched directly anyway. Fallback credit is per-token, not
  per-occurrence: it is a leniency valve, and leniency here costs recall
  sensitivity, not correctness (it can only raise a score, never flag).
* Numbers: decimal/grouped numbers ("1.2", "1,204") are kept as single
  tokens via a dedicated pattern instead of shattering into droppable single
  digits — numbers are exactly what financial tables silently lose.
* Minimum token length: tokens shorter than MIN_TOKEN_LEN (2) are dropped
  from BOTH sides. Single characters are noise (bullet glyph residue, stray
  column letters, list markers); multi-digit page numbers survive, which is
  acceptable — a genuinely missing page number is drowned out only on pages
  with plenty of other tokens, where it isn't the signal anyway.
* Multiset, not set: counts are compared (bounded intersection over reference
  counts). A page where "revenue" appears 10 times in the reference but once
  in the markdown must NOT score 1.0 — repeated-content loss (table rows,
  list items) is a classic shape of the pdf-inspector bug class.
* Near-empty reference pages: if pdftotext yields < MIN_REF_CHARS (15)
  non-whitespace characters for a page, recall is None and the page is
  skipped, never flagged (contract rule) — a scanned page gives poppler
  nothing, so there is nothing to cross-check. Tier 0/2/3 own those pages.
* Threshold: strict `recall < threshold` flags; a page exactly at the
  threshold passes. Default 0.85 comes from the contract signature.

Scope note: verify() scores only the pages present in `pages`. A PDF page
missing from the dict entirely is ExtractResult's EMPTY_OUTPUT case and is
the orchestrator's job to flag — silently inventing a 0.0 here would double-
report it. Page numbers supplied must exist in the PDF (1-indexed, per the
contract); out-of-range keys raise ValueError rather than silently scoring.

Self-test: `uv run verify.py` (uses tests/fixtures/probe-*.pdf).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import unicodedata
from collections import Counter
from pathlib import Path

from contract import VerifyResult

SCRIPT_DIR = Path(__file__).resolve().parent

MIN_TOKEN_LEN = 2      # drop 1-char tokens: bullet residue, stray letters
MIN_REF_CHARS = 15     # non-ws chars below which a reference page is "empty"
FALLBACK_MIN_LEN = 5   # joined-stream substring fallback needs length to be safe

# Numbers first so "1.2" / "1,204" survive as one token instead of shattering
# into single digits that the min-length rule would then drop.
_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)+|\w+")

_FENCE_RE = re.compile(r"^\s*(?:```|~~~).*$", re.MULTILINE)
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\(\s*[^)\s]*[^)]*\)")
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
# word-\n(indent)word  ->  wordword ; mirrors pdf-inspector's line-break rejoin
_HYPHEN_BREAK_RE = re.compile(r"(?<=\w)-[ \t]*\n[ \t]*(?=\w)")


# ---------------------------------------------------------------- reference

def _pdftotext_pages(pdf_path: str) -> list[str]:
    """All pages' `pdftotext -layout` text in one subprocess call.

    pdftotext terminates every page with \\f, so splitting the whole-document
    output on \\f yields exactly what per-page `-f N -l N` calls would, plus
    the PDF's page count for free (used for the 1-indexed bounds assertion).
    """
    exe = shutil.which("pdftotext")
    if exe is None:
        raise RuntimeError(
            "pdftotext not found — install poppler (brew install poppler); "
            "deps.py should have caught this before tier V ran"
        )
    proc = subprocess.run(
        [exe, "-layout", pdf_path, "-"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"pdftotext failed on {pdf_path} (exit {proc.returncode}): "
            f"{proc.stderr.strip()}"
        )
    parts = proc.stdout.split("\f")
    # Modern poppler emits a trailing \f after the last page -> final empty
    # part. Drop it only if it is genuinely empty, so a hypothetical
    # non-\f-terminated last page is not lost.
    if parts and parts[-1].strip() == "":
        parts.pop()
    return parts


# ------------------------------------------------------------ normalization

def _strip_markdown(md: str) -> str:
    """Remove the markdown syntax tokenization alone can't neutralize.

    Most syntax (#, *, |, `, table rules, blockquote >) is pure punctuation
    and disappears at tokenization. What must be handled explicitly: fence
    delimiter lines (the ``` line's info string, e.g. "python", is not page
    text — fence CONTENT is kept), link/image URLs (kept-out so they don't
    seed accidental substrings in the joined-stream fallback), HTML tags.
    """
    md = _FENCE_RE.sub(" ", md)
    md = _IMAGE_RE.sub(r"\1", md)
    md = _LINK_RE.sub(r"\1", md)
    md = _HTML_TAG_RE.sub(" ", md)
    return md


def _tokens(text: str) -> list[str]:
    """NFKC -> casefold -> underscore-to-space -> tokenize -> min-length."""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = text.replace("_", " ")  # \w matches _, so _emphasis_ would leak in
    return [t for t in _TOKEN_RE.findall(text) if len(t) >= MIN_TOKEN_LEN]


def _page_recall(ref_text: str, md_text: str) -> float | None:
    """Fraction of reference tokens (with multiplicity) found in the markdown.

    None when the reference page is effectively empty (< MIN_REF_CHARS
    non-whitespace chars) — nothing to cross-check.
    """
    if len(re.sub(r"\s", "", ref_text)) < MIN_REF_CHARS:
        return None

    # De-hyphenate ONLY the reference: pdftotext keeps line-break hyphen
    # splits, pdf-inspector rejoins them. The markdown side has no line-break
    # hyphenation to undo, and bullets ("\n- item") never match the pattern
    # (it requires a word char immediately before the hyphen).
    ref_tokens = _tokens(_HYPHEN_BREAK_RE.sub("", ref_text))
    if not ref_tokens:
        return None
    md_tokens = _tokens(_strip_markdown(md_text))

    ref_counts = Counter(ref_tokens)
    md_counts = Counter(md_tokens)

    total = sum(ref_counts.values())
    # Multiset (bounded) intersection: each reference occurrence must be
    # matched by its own markdown occurrence.
    matched = sum(min(n, md_counts[tok]) for tok, n in ref_counts.items())

    # Segmentation-insensitive fallback for tokens with unmatched residue:
    # accept if the token appears inside the markdown's tokens concatenated
    # with no separators (catches word-join/split divergence between engines,
    # incl. both hyphenation directions). Long tokens only — short substrings
    # match accidentally. Credit is the full residue for that token: the
    # fallback is a deliberate leniency valve (can only raise recall).
    md_concat = "".join(md_tokens)
    for tok, n in ref_counts.items():
        residue = n - min(n, md_counts[tok])
        if residue and len(tok) >= FALLBACK_MIN_LEN and tok in md_concat:
            matched += residue

    return matched / total


# ------------------------------------------------------------------- verify

def verify(pdf_path: str, pages: dict[int, str],
           threshold: float = 0.85) -> VerifyResult:
    """Per-page token recall of `pdftotext -layout` output vs the markdown.

    pages: 1-indexed page -> that page's markdown chunk (contract). Only the
    supplied pages are scored; missing pages are the orchestrator's
    EMPTY_OUTPUT concern, not tier V's.
    """
    ref_pages = _pdftotext_pages(pdf_path)
    page_count = len(ref_pages)

    bad = sorted(p for p in pages if not 1 <= p <= page_count)
    if bad:
        raise ValueError(
            f"page number(s) {bad} out of range for {pdf_path} "
            f"({page_count} page(s), 1-indexed)"
        )

    recall: dict[int, float | None] = {}
    for page in sorted(pages):
        recall[page] = _page_recall(ref_pages[page - 1], pages[page])

    flagged = [p for p, r in recall.items() if r is not None and r < threshold]
    return VerifyResult(recall=recall, flagged=flagged, threshold=threshold)


# ---------------------------------------------------------------- self-test

if __name__ == "__main__":
    fixtures = SCRIPT_DIR.parent.parent / "tests" / "fixtures"
    simple = str(fixtures / "probe-simple.pdf")
    scanned = str(fixtures / "probe-scanned.pdf")

    failures = 0

    def check(name: str, cond: bool, detail: str) -> None:
        global failures
        status = "ok  " if cond else "FAIL"
        if not cond:
            failures += 1
        print(f"  {status} {name}  ({detail})")

    ref = _pdftotext_pages(simple)
    assert len(ref) == 2, f"fixture expected 2 pages, got {len(ref)}"

    # 1. Round-trip: markdown reconstructed from pdftotext itself.
    print("case 1: round-trip (pdftotext text fed back as markdown)")
    r = verify(simple, {1: ref[0], 2: ref[1]})
    check("page 1 recall ~= 1.0", r.recall[1] is not None and r.recall[1] > 0.99,
          f"recall={r.recall[1]}")
    check("page 2 recall ~= 1.0", r.recall[2] is not None and r.recall[2] > 0.99,
          f"recall={r.recall[2]}")
    check("nothing flagged", r.flagged == [], f"flagged={r.flagged}")

    # 2. Silent loss: middle prose sentence deleted from page 1.
    print("case 2: page-1 middle prose sentence deleted")
    lost = re.sub(
        r"Revenue grew.*?March\.", "", ref[0], flags=re.DOTALL)
    assert lost != ref[0], "sentence deletion did not apply — fixture drift?"
    r = verify(simple, {1: lost, 2: ref[1]})
    check("page 1 flagged", 1 in r.flagged,
          f"recall={r.recall[1]}, flagged={r.flagged}")
    check("page 2 not flagged", 2 not in r.flagged,
          f"recall={r.recall[2]}, flagged={r.flagged}")

    # 3. Heavy formatting of the same content: no false flag.
    print("case 3: heavily formatted markdown, same content")
    fancy1 = (
        "# Quarterly Report\n\n"
        "## Revenue Summary\n\n"
        "Revenue grew **twelve percent** year over year, driven by "
        "*subscription renewals* and two enterprise contracts signed in "
        "[March](https://example.com/reports/march).\n\n"
        "> Operating costs held flat while headcount rose by four "
        "engineers.\n\n"
        "| Region | Q1 | Q2 |\n"
        "| --- | --- | --- |\n"
        "| EMEA | 1.2 | 1.4 |\n"
        "| Americas | 2.1 | 2.3 |\n"
    )
    fancy2 = "# Outlook\n\nSecond-half guidance remains **unchanged** pending the _audit_.\n"
    r = verify(simple, {1: fancy1, 2: fancy2})
    check("page 1 passes", 1 not in r.flagged, f"recall={r.recall[1]}")
    check("page 2 passes", 2 not in r.flagged, f"recall={r.recall[2]}")

    # 4. Scanned page: pdftotext yields nothing -> recall None, never flagged.
    print("case 4: probe-scanned.pdf (image-only page)")
    r = verify(scanned, {1: "OCR or vision produced something here."})
    check("recall is None", r.recall[1] is None, f"recall={r.recall[1]}")
    check("not flagged", r.flagged == [], f"flagged={r.flagged}")

    # 5. Bounds: 1-indexed page numbers must exist in the PDF.
    print("case 5: out-of-range page number rejected")
    try:
        verify(simple, {3: "x"})
        check("raises ValueError", False, "no exception")
    except ValueError as e:
        check("raises ValueError", True, str(e))

    if failures:
        print(f"\n{failures} self-test check(s) FAILED")
        sys.exit(1)
    print("\nall self-test checks passed")
