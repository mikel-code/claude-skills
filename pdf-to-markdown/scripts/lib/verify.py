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

Bigram / word-order detection (contract.py VerifyResult.bigram/order_flagged):
unigram recall is a bag-of-words check — a page whose lines or table cells
have their word order scrambled (docling word-reversal bug; shuffled TOC
entries) can score 0.97+ on unigram recall while being unusable. The fix
reuses the exact same normalized token sequences (ref: de-hyphenated +
tokenized; md: markdown-stripped + tokenized) and additionally compares
adjacent-token PAIRS with the same bounded-multiset recall math. Order is a
*secondary* signal layered on top of, never instead of, unigram recall:
a page must first pass the unigram bar (or it's already `flagged` for a
content reason) before low bigram recall promotes it to `order_flagged`.
ORDER_THRESHOLD = 0.75 (see the constant's own comment for the full writeup)
was picked empirically against a real 423-page document: docling's
word-reversed table-cell pages scored bigram 0.69/0.71, cleanly below every
sampled good page (prose and ruled-table alike, floor 0.81); that document's
TOC page-level reordering scored 0.81-0.92, overlapping the good range, so it
is a documented residual false-negative rather than a threshold miss — no
single global threshold catches both without flagging clean tables. Bigram is
None exactly when unigram is None (nothing to cross-check either way); the
vacuous case of a one-token reference page (no possible bigram) scores
bigram 1.0, not None — it cannot be evidence of scrambling.

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

# Bigram recall below this (while unigram already passed `threshold`) marks a
# page order_flagged rather than flagged. Chosen empirically against a real
# 423-page document (CCBPBAbril2026.pdf) with docling as the extractor —
# reconstructed via docling's actual per-page markdown export (do_ocr=False,
# do_table_structure=True, same params as extract.py) cross-checked against
# this module's own reference_pages(). Measured distributions:
#
#   pages 103/104 (docling word-reversed table-cell lines): bigram 0.69, 0.71
#   pages 5-14    (TOC, page-level entry/fragment duplication+reorder):
#                 bigram 0.81-0.92  <- OVERLAPS the good-page range below
#   good prose + good ruled-table pages (30-35, 45-55, and 13 table pages
#   spread across the document): bigram 0.81-0.97, floor set by a table page
#
# The severe word-reversal case (103/104) is cleanly separable from every
# good page sampled (good floor 0.806 vs bad ceiling 0.712, a 0.094 gap).
# The TOC-style corruption is NOT cleanly separable — its scores land inside
# the good-page range because only a minority of lines on those pages are
# actually disordered, so most adjacent-token pairs stay intact. Any
# threshold high enough to catch the TOC pages would also flag multiple
# known-good ruled-table pages (violates the "clean tables must not flag"
# requirement) for the same reason: normal pipe-table linearization already
# costs bigram recall (~0.81-0.97) independent of any bug. 0.75 sits in the
# clean gap for the severe case, roughly equidistant from both edges
# (+0.038 over the bad ceiling, -0.056 under the good floor) — biased
# slightly toward not false-flagging clean tables since that failure mode is
# the one explicitly ruled out.
#
# Residual risk: page-level/entry-level reordering that leaves most
# individual lines internally intact (this document's TOC bug) is NOT
# reliably caught by this metric at this threshold — 12/12 of the sampled
# TOC pages score above 0.75 and stay order_flagged=False. Catching that
# class would need a different signal (e.g. TOC-specific entry-number
# monotonicity), out of scope here.
ORDER_THRESHOLD = 0.75

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


def reference_pages(pdf_path: str) -> dict[int, str]:
    """Public per-page poppler `-layout` text, 1-indexed. Contract-exposed so
    repair.py can reuse the same order-true reference this module scores
    against, without a second pdftotext subprocess pass. Every page of the
    PDF is present (even blank ones) — callers filter, this doesn't."""
    return {i: text for i, text in enumerate(_pdftotext_pages(pdf_path), start=1)}


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


def _bigrams(tokens: list[str]) -> list[tuple[str, str]]:
    """Adjacent-token pairs, in sequence order — the word-order signal.
    A single-token (or empty) sequence has none."""
    return list(zip(tokens, tokens[1:]))


def _page_recall(ref_text: str, md_text: str) -> tuple[float | None, float | None]:
    """(unigram, bigram) recall of the reference against the markdown.

    Both None when the reference page is effectively empty (< MIN_REF_CHARS
    non-whitespace chars) — nothing to cross-check. Built from the *same*
    normalized token sequences (dehyphenation, markdown-stripping,
    tokenization all applied once, shared by both metrics) so the two scores
    are directly comparable — a divergence between them is a word-order
    signal, not a normalization artifact.
    """
    if len(re.sub(r"\s", "", ref_text)) < MIN_REF_CHARS:
        return None, None

    # De-hyphenate ONLY the reference: pdftotext keeps line-break hyphen
    # splits, pdf-inspector rejoins them. The markdown side has no line-break
    # hyphenation to undo, and bullets ("\n- item") never match the pattern
    # (it requires a word char immediately before the hyphen).
    ref_tokens = _tokens(_HYPHEN_BREAK_RE.sub("", ref_text))
    if not ref_tokens:
        return None, None
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

    unigram = matched / total

    # Bigram: identical multiset-recall math, one token pair at a time, on
    # the SAME token sequences above (no fallback — segmentation drift is
    # already absorbed on the unigram side; a fallback here would blur the
    # exact signal this metric exists to catch, which is pair adjacency).
    ref_bigrams = Counter(_bigrams(ref_tokens))
    md_bigrams = Counter(_bigrams(md_tokens))
    total_bg = sum(ref_bigrams.values())
    if total_bg == 0:
        # Single-token reference page: no possible pair, so no possible
        # evidence of scrambling. Vacuously 1.0, not None (unigram is
        # defined here, and contract ties bigram-None to unigram-None only).
        bigram = 1.0
    else:
        matched_bg = sum(min(n, md_bigrams[bg]) for bg, n in ref_bigrams.items())
        bigram = matched_bg / total_bg

    return unigram, bigram


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
    bigram: dict[int, float | None] = {}
    for page in sorted(pages):
        recall[page], bigram[page] = _page_recall(ref_pages[page - 1], pages[page])

    # Spillover tolerance: whole-construct serialization (extract.py's
    # docling path) attributes a cross-page paragraph/list entirely to the
    # page where it starts, so the NEXT page's poppler reference contains
    # text whose markdown legitimately lives on a neighbor. Empirically (423-
    # page real document): pages failing the plain per-page check had 97-100%
    # of their "missing" tokens sitting in an adjacent page's markdown. So a
    # failing page gets one retry against its own + both neighbors' markdown;
    # passing the widened check means nothing is lost — only attributed
    # differently — and the page is not flagged. The recorded recall stays
    # the honest per-page number.
    flagged = []
    for p, r in recall.items():
        if r is None or r >= threshold:
            continue
        neighborhood = "\n\n".join(
            pages.get(q, "") for q in (p - 1, p, p + 1))
        spill_recall, _ = _page_recall(ref_pages[p - 1], neighborhood)
        if spill_recall is None or spill_recall < threshold:
            flagged.append(p)
    # Order flagging is layered on top of, not instead of, unigram recall: a
    # page must already be passing (unigram >= threshold, so not in `flagged`)
    # before a low bigram score promotes it. This is deliberate — a page with
    # genuinely missing content is already `flagged` for that reason, and
    # piling WORD_ORDER onto it would be redundant/misleading, not additive.
    order_flagged = [
        p for p in sorted(pages)
        if recall[p] is not None and recall[p] >= threshold
        and bigram[p] is not None and bigram[p] < ORDER_THRESHOLD
    ]
    return VerifyResult(
        recall=recall, flagged=flagged, threshold=threshold,
        bigram=bigram, order_flagged=order_flagged,
    )


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

    # 6. reference_pages(): public per-page poppler text, matches internal.
    print("case 6: reference_pages() public accessor")
    rp = reference_pages(simple)
    check("keys are 1-indexed page numbers", set(rp) == {1, 2}, f"keys={sorted(rp)}")
    check("page 1 text matches internal extraction", rp[1] == ref[0], "mismatch")
    check("page 2 text matches internal extraction", rp[2] == ref[1], "mismatch")

    # 7. Synthetic word-order case: reverse word order of half of page 1's
    # lines. Same multiset of words (unigram stays high) but local adjacency
    # is broken on the reversed lines (bigram drops) -> order_flagged, not
    # flagged (content isn't missing, just scrambled).
    print("case 7: synthetic word-order scramble (half the lines reversed)")
    lines = ref[0].splitlines()
    scrambled_lines = [
        " ".join(reversed(line.split())) if i % 2 == 0 and line.split() else line
        for i, line in enumerate(lines)
    ]
    scrambled = "\n".join(scrambled_lines)
    assert scrambled != ref[0], "line reversal did not change page 1 text — fixture drift?"
    r = verify(simple, {1: scrambled, 2: ref[1]})
    check("page 1 unigram recall stays high", r.recall[1] is not None and r.recall[1] >= 0.85,
          f"recall={r.recall[1]}")
    check("page 1 bigram recall drops below unigram",
          r.bigram[1] is not None and r.recall[1] is not None and r.bigram[1] < r.recall[1] - 0.15,
          f"unigram={r.recall[1]} bigram={r.bigram[1]}")
    check("page 1 not in flagged (content isn't missing)", 1 not in r.flagged,
          f"flagged={r.flagged}")
    check("page 1 in order_flagged", 1 in r.order_flagged,
          f"bigram={r.bigram[1]} order_flagged={r.order_flagged}")
    check("page 2 not order_flagged", 2 not in r.order_flagged,
          f"order_flagged={r.order_flagged}")

    # 8. Formatting-heavy case (case 3's fixtures) must NOT order-flag: same
    # content, same word order, just markdown syntax added around it.
    print("case 8: heavily formatted markdown does not order-flag")
    r = verify(simple, {1: fancy1, 2: fancy2})
    check("page 1 not order_flagged", 1 not in r.order_flagged,
          f"bigram={r.bigram[1]} order_flagged={r.order_flagged}")
    check("page 2 not order_flagged", 2 not in r.order_flagged,
          f"bigram={r.bigram[2]} order_flagged={r.order_flagged}")

    if failures:
        print(f"\n{failures} self-test check(s) FAILED")
        sys.exit(1)
    print("\nall self-test checks passed")
