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
TOC page-level reordering scored 0.81-0.92, overlapping the good range — no
single global threshold catches both without flagging clean tables. That TOC
class is therefore caught by a THIRD, structural signal layered on top:
toc_order_suspect() recognizes TOC-shaped pages whose entry structure is
broken (orphan-prefixed entries, missing page-number tails, bare-number
orphan lines, non-monotonic page numbers) and promotes them to order_flagged
regardless of bigram score. A FOURTH signal with the same layering,
orphan_residue_suspect(), catches OCR'd pages where wrapped-line orphan
fragments survived repair (see its block comment for the tuning record) —
another class both recall metrics excuse by construction. Layering: pages already `flagged` for missing
content are not additionally order-flagged, and recall-None (unverifiable)
pages stay unflagged; unlike the bigram signal it does NOT require unigram
to pass the threshold — the field document's page 8 sits at unigram 0.81,
excused by the spillover tolerance, which would block the bigram path
entirely, and the structural evidence is self-contained anyway. Bigram is
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
# Residual risk (now mitigated): page-level/entry-level reordering that
# leaves most individual lines internally intact (this document's TOC bug)
# is NOT reliably caught by this metric at this threshold — the sampled
# TOC pages score 0.81-0.92 and stay below-radar on bigram alone. That
# class is caught by the STRUCTURAL signal below (toc_order_suspect),
# layered into verify() alongside the bigram check.
ORDER_THRESHOLD = 0.75

# ------------------------------------------------- TOC structural signal
#
# toc_order_suspect(): the field document's TOC pages defeated the bigram
# metric (0.81-0.92 vs threshold 0.75, which cannot rise without
# false-flagging clean ruled-table pages — see ORDER_THRESHOLD writeup).
# The catchable difference is STRUCTURE, not token order: a healthy TOC
# entry is "- <secnum> <title> <pagenum>" (vision-fixed pages 5-7/10/11/
# 13/14 of the field document all have this shape), while the scrambled
# extraction emits entries with an orphan fragment fused BEFORE the
# section number ("- único 3.1 Identificador único 165"), entries missing
# the page-number tail, bare-number orphan entries ("- 37 38"), or
# out-of-order page-number tails. Gate first (>= TOC_MIN_ENTRIES list
# lines ending in a bare page number → the page is TOC-like at all; prose
# and ordinary list pages never qualify), then flag on any structural
# break. Thresholds tuned on the field document: broken page 8 scores
# orphan-prefix fraction 0.71; every clean vision-fixed TOC page scores 0
# on all signals.

TOC_MIN_ENTRIES = 8        # list lines with page-number tails => TOC-like
TOC_BROKEN_FRAC = 0.20     # fraction of broken entries that flags
TOC_BARE_NUMBER_MIN = 2    # "- 37 38" orphan lines that flag
TOC_INVERSION_MIN = 2      # page-number tail order inversions that flag

_TOC_LIST_RE = re.compile(r"^\s*[-*+]\s+(\S.*)$")
_TOC_PAGENUM_TAIL_RE = re.compile(r"\s(\d{1,4})$")
_TOC_SECNUM_RE = re.compile(r"^\d{1,2}(?:\.\d{1,2}){0,5}$")
_TOC_MID_SECNUM_RE = re.compile(r"\s\d{1,2}(?:\.\d{1,2}){1,5}\s")
_TOC_BARE_NUMBERS_RE = re.compile(r"^\d{1,4}(?:\s+\d{1,4})+$")
_TOC_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(\d[\w.]*\s+\S.*)$")


def toc_order_suspect(markdown: str) -> bool:
    """True when this page's markdown is a TOC whose entry structure is
    broken — the reordered-TOC shape the bigram metric cannot see.

    Purely structural, no reference needed. A page is TOC-like when at
    least TOC_MIN_ENTRIES list lines end in a bare page number (or are
    bare-number orphans); among TOC-like pages, suspect when any holds:
      * >= TOC_BROKEN_FRAC of entries carry an orphan prefix — the entry
        does not START with a section number yet contains a dotted section
        number mid-line ("- único 3.1 Identificador único 165");
      * >= TOC_BROKEN_FRAC of entries lack the page-number tail entirely;
      * >= TOC_BARE_NUMBER_MIN bare-number orphan entries ("- 37 38");
      * >= TOC_INVERSION_MIN inversions in the sequence of page-number
        tails (a real TOC's page numbers are monotonic; one-off quirks
        such as an annex restart are tolerated below the minimum).
    Clean TOCs (all entries "- <secnum> <title> <pagenum>", tails
    monotonic) and non-TOC pages always return False.
    """
    entries: list[str] = []          # list-item bodies
    tails: list[int] = []            # page-number tails, in order
    bare_number_lines = 0
    for line in markdown.split("\n"):
        m = _TOC_LIST_RE.match(line)
        if m:
            body = m.group(1).strip()
            if _TOC_BARE_NUMBERS_RE.match(body):
                bare_number_lines += 1
                continue
            entries.append(body)
            tm = _TOC_PAGENUM_TAIL_RE.search(body)
            if tm:
                tails.append(int(tm.group(1)))
            continue
        hm = _TOC_HEADING_RE.match(line)
        if hm:
            tm = _TOC_PAGENUM_TAIL_RE.search(hm.group(1).strip())
            if tm:
                tails.append(int(tm.group(1)))
    if len(tails) + bare_number_lines < TOC_MIN_ENTRIES:
        return False                 # not TOC-like: never suspect
    if bare_number_lines >= TOC_BARE_NUMBER_MIN:
        return True
    broken_prefix = missing_tail = 0
    for body in entries:
        has_tail = bool(_TOC_PAGENUM_TAIL_RE.search(body))
        if not has_tail:
            missing_tail += 1
        first = body.split(None, 1)[0]
        if (not _TOC_SECNUM_RE.match(first)
                and _TOC_MID_SECNUM_RE.search(f" {body} ")):
            broken_prefix += 1
    n = len(entries)
    if n and broken_prefix / n >= TOC_BROKEN_FRAC:
        return True
    if n and missing_tail / n >= TOC_BROKEN_FRAC:
        return True
    inversions = sum(1 for a, b in zip(tails, tails[1:]) if b < a)
    return inversions >= TOC_INVERSION_MIN


# --------------------------------------------- orphan-residue page signal
#
# orphan_residue_suspect(): tesseract-OCR'd footnote/list zones come back
# with wrapped-line fragments fused before the real line ("al  equivalente
# ses devengados … y i) la tasa anual equivalente de los intereses
# devengados …"). repair.drop_duplicate_orphans removes the PROVABLE
# duplicates, but fragments whose full text never reappears (or reappears
# altered) survive as residue — and both recall metrics excuse them
# (unigram: extra tokens never count against a page; bigram: a minority of
# corrupted lines doesn't drag the page score below ORDER_THRESHOLD).
# The catchable shape is structural: a PLAIN paragraph line containing a
# printed marker boundary (footnote number + uppercase, roman/letter
# paren marker, mid-line dash) whose text BEFORE that boundary starts
# lowercase (or digit-glued-lowercase, "1s  posibles…") — real Spanish
# prose before such a boundary starts uppercase. Tuning, all against the
# 423-page field document (flags exactly its 8 OCR-residue pages
# {87, 150, 317, 330, 332, 333, 342, 386}, zero others):
#   * boundaries are printed markers ONLY — sentence punctuation is NOT a
#     boundary here, because a legitimate cross-page continuation
#     paragraph ("…catorce pagas. A estos efectos…") starts lowercase and
#     contains sentence bounds (field pages 107/315 false-flagged until
#     this was dropped);
#   * lines that START with a printed marker are skipped — "i) TARGET-BE,
#     para…" / "a) Carencia…" / "b Fecha de entrada…" are legitimate list
#     bodies whose lowercase follow-text is content, not residue (field
#     pages 131/133/273/331/337/355/363/408 false-flagged until skipped);
#   * markdown LIST ITEMS are skipped entirely — a fused-orphan list item
#     is repair.drop_duplicate_orphans' territory, and counting them
#     false-flagged field page 343 (one item + one plain residue line);
#   * a page flags at >= RESIDUE_MIN_LINES suspect lines: one lowercase-
#     start line can be a page-break continuation; two on one page is the
#     disease signature.
# Layered into verify() exactly like toc_order_suspect (structural, no
# reference needed, no unigram gate). With OCR now page-scoped upstream,
# future conversions should rarely trip this — it remains the residue
# catcher for genuinely-OCR'd pages.

RESIDUE_MIN_LINES = 2    # suspect lines on one page that flag it
RESIDUE_FRAG_MIN = 2     # pre-boundary fragment length bounds: a residue
RESIDUE_FRAG_MAX = 120   # fragment is (part of) one wrapped print line

# Printed-marker boundaries (mirror repair.py's orphan boundary set minus
# sentence punctuation): footnote number + uppercase (not glued to a
# larger number/date — "30 de abril" never matches), roman/letter paren
# markers (lowercase follow allowed), mid-line dashes.
_RESIDUE_FOOT_RE = re.compile(
    r"(?<![\d.,/§º°-])\d{1,2}[ \t\xa0]+(?=[A-ZÁÉÍÓÚÑÜ«¡])"
)
_RESIDUE_MARK_RE = re.compile(
    r"(?<![\w.,/§º°-])(?:[ivxlcdm]{1,4}\)|[a-z]\)|[—-])[ \t\xa0]+(?=[\w«¡])"
)
# Line-leading printed markers (legitimate list bodies, skipped): roman/
# letter + ")" or ".", or a single lowercase letter directly before an
# uppercase word (table-footnote letters: "b Fecha de entrada en vigor…").
_RESIDUE_LEAD_MARKER_RE = re.compile(
    r"^(?:[ivxlcdm]{1,4}[).]|[a-z][).]|[a-z](?=[ \t\xa0]+[A-ZÁÉÍÓÚÑÜ«¡]))"
)
_RESIDUE_LIST_RE = re.compile(r"^(?:[-*+]|\d{1,3}[.)])\s+")
_RESIDUE_CORRUPT_RE = re.compile(r"^\d{1,2}[a-záéíóúñü]")
_RESIDUE_HR_RE = re.compile(r"^\s*(?:[-*_]\s*){3,}$")
_RESIDUE_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")


def orphan_residue_suspect(markdown: str) -> bool:
    """True when this page's markdown carries >= RESIDUE_MIN_LINES plain
    lines shaped like OCR orphan residue: text before a printed marker
    boundary that starts lowercase/corrupted. Purely structural, no
    reference needed. See the block comment above for the tuning record.
    """
    suspects = 0
    in_fence = False
    for line in markdown.split("\n"):
        if _RESIDUE_FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        body = line.strip()
        if (not body or body.startswith(("#", "|", ">", "<"))
                or _RESIDUE_HR_RE.match(body)
                or _RESIDUE_LIST_RE.match(body)
                or _RESIDUE_LEAD_MARKER_RE.match(body)):
            continue
        cands = sorted(
            {m.start() for m in _RESIDUE_FOOT_RE.finditer(body)}
            | {m.start() for m in _RESIDUE_MARK_RE.finditer(body)}
        )
        pos = next((p for p in cands if p > 0), None)
        if pos is None:
            continue
        frag = body[:pos].rstrip()
        if not (RESIDUE_FRAG_MIN <= len(frag) <= RESIDUE_FRAG_MAX):
            continue
        if frag[:1].islower() or _RESIDUE_CORRUPT_RE.match(frag):
            suspects += 1
            if suspects >= RESIDUE_MIN_LINES:
                return True
    return False


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
    # Structural order signals, same layering: pages whose markdown
    # STRUCTURE is broken join order_flagged regardless of bigram score.
    # Two detectors, both structural and reference-free:
    #   * toc_order_suspect — the field document's reordered TOC pages
    #     score bigram 0.81-0.92 (inside the clean-table range, see
    #     ORDER_THRESHOLD writeup), so no bigram threshold can catch them
    #     without false-flagging clean tables;
    #   * orphan_residue_suspect — OCR'd footnote zones keep unprovable
    #     orphan fragments that unigram recall excuses by construction
    #     (extra tokens never count against a page) and that are too few
    #     per page to move bigram recall.
    # Unlike the bigram path they do NOT require unigram >= threshold (the
    # field document's broken TOC page sits at unigram 0.81, excused by
    # the spillover retry — requiring the threshold would silence the
    # signal). The rest of the contract's layering still applies: a page
    # already `flagged` for missing content is not additionally
    # order-flagged, and recall-None (unverifiable) pages stay unflagged
    # per the contract rule.
    for p in sorted(pages):
        if p in flagged or p in order_flagged or recall[p] is None:
            continue
        if toc_order_suspect(pages[p]) or orphan_residue_suspect(pages[p]):
            order_flagged.append(p)
    order_flagged.sort()
    return VerifyResult(
        recall=recall, flagged=flagged, threshold=threshold,
        bigram=bigram, order_flagged=order_flagged,
    )


# ---------------------------------------------------------------- self-test

# Verbatim excerpts from the field document (CCBPBAbril2026.md). Broken:
# extracted TOC page 8 — entries carry orphan fragments fused before the
# section number. Clean: vision-fixed TOC page 6 — canonical
# "- <secnum> <title> <pagenum>" shape.
_TOC_BROKEN = """\
## 3 Transferencias 164

- único 3.1 Identificador único 165
- de  las transferencias 3.2 Irrevocabilidad de las transferencias 167
- y  gastos de las  operaciones 3.3 Comisiones y gastos de las operaciones de pago 167
- de órdenes de pago 3.4 Recepción de órdenes de pago 169
- de abono 3.5 Fecha de abono 169
- amediatos 3.6 Pagos inmediatos 170
- um 3.6.1  Bizum 170
- transferencias 3.8 Límites de transferencias 173

## 4 Adeudos domiciliados 175

- generales 4.1 Consideraciones generales 175
- de adeudos domiciliados 4.2 Tipología de adeudos domiciliados 175
- e domiciliación o  mandato 4.3 Orden de domiciliación o mandato 176
- domiciliado básico SEPA 4.4 Adeudo domiciliado básico SEPA 176
"""

_TOC_CLEAN = """\
## 2 CUENTAS DE PAGO O A LA VISTA. CUENTAS DE PAGO BÁSICAS

- 1 Cuestiones generales 60
- 2 Apertura de cuentas de pago. Obligaciones derivadas de la normativa de transparencia 61
- 2.1 Explicaciones adecuadas 62
- 2.2 Información precontractual 62
- 2.2.1 Documento informativo de las comisiones. Comparador de comisiones de cuentas de pago 63
- 2.2.2 Servicios vinculados a la cuenta 64
- 2.3 Formalización del contrato 64
- 2.3.1 Cuentas en divisas 65
- 3 Disposición de fondos 67
- 3.1 Disposiciones en efectivo 67
- 3.2 Adeudos sin orden firmada del cliente 68
- 3.3 Cargos por retribuciones en especie (regalos promocionales y otros) 69
"""

# Ordinary numbered/bulleted list page (field-document footnote style):
# list lines, but no page-number tails — must never be TOC-like.
_LIST_NOT_TOC = """\
- 1 Resulta imprescindible su previsión y concreción en la información precontractual y en los propios contratos bancarios.
- 2 Además, en dicha información se deberá recoger lo siguiente:
- El importe concreto de los gastos que se han de repercutir.
- El canal empleado para efectuar las comunicaciones.
- 19 En este sentido, se considera contrario a las buenas prácticas.
- STS n.º 705, de 15 de diciembre de 2015, que declaró la nulidad de la cláusula.
- STS n.º 457, de 24 de julio de 2020, que confirmó la partición de los gastos.
- STS n.º 555, de 26 de octubre de 2020, sobre los gastos de gestoría.
- STS n.º 35, de 27 de enero de 2021, sobre los gastos de tasación.
"""

# Verbatim OCR-residue lines from field pages 87 and 330: plain lines with
# a fused orphan fragment (lowercase-start) before a printed marker
# boundary — the shape orphan_residue_suspect exists to catch.
_RESIDUE_87 = """\
al  equivalente ses devengados por el  descubierto durante el  período de \
liquidación de este, y i) la tasa anual equivalente de los intereses \
devengados por el descubierto durante el período de liquidación de este, y

al  equivalente eses se calculará teniendo en cuenta el saldo medio deudor \
del  período de liquidación; - la tasa anual equivalente de los intereses \
se calculará teniendo en cuenta el saldo medio deudor del período de \
liquidación;
"""

_RESIDUE_330 = """\
npendio de consultas a  la  Comisión de Control sobre la aplicación del  \
CBP. 17 Consulta 28.a del compendio de consultas a la Comisión de Control \
sobre la aplicación del CBP .

mpendio de consultas a  la  Comisión de Control sobre la aplicación del  \
CBP. 19 Consulta 28.b del compendio de consultas a la Comisión de Control \
sobre la aplicación del CBP .
"""

# Field page 131/133 shape: lines that START with a printed roman/letter
# marker are legitimate list bodies, never residue.
_RESIDUE_MARKER_LED = """\
i) TARGET-BE, para el procesamiento de grandes pagos. Se trata de un \
sistema de liquidación bruta en tiempo real.

ii) Sistema Nacional de Compensación Electrónica (SNCE), para los pagos \
de pequeño importe. Está gestionado por Iberpay, una empresa privada.

b Fecha de entrada en vigor de la Ley 41/2007. A los préstamos \
hipotecarios anteriores no les resulta de aplicación.
"""

# Field page 107 shape: a cross-page continuation paragraph legitimately
# starts lowercase and contains sentence punctuation — sentence bounds are
# deliberately NOT residue boundaries.
_RESIDUE_CONTINUATION = """\
mínimo, a los servicios incluidos en la lista de terminología \
estandarizada. Dicho sitio web deberá estar debidamente actualizado.

de estos y de los resultados de las búsquedas. El artículo de la Orden \
establece los requisitos del comparador de comisiones.
"""

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

    # 9. toc_order_suspect on real field-document TOC excerpts.
    print("case 9: toc_order_suspect structural signal")
    check("broken extracted TOC (orphan-prefixed entries) is suspect",
          toc_order_suspect(_TOC_BROKEN), "expected True")
    check("clean vision-fixed TOC is not suspect",
          not toc_order_suspect(_TOC_CLEAN), "expected False")
    check("ordinary numbered-list page is not TOC-like",
          not toc_order_suspect(_LIST_NOT_TOC), "expected False")
    check("plain prose page is not TOC-like",
          not toc_order_suspect(ref[0]), "expected False")
    bare = _TOC_CLEAN + "\n- 37 38\n- 41 42\n"
    check("bare-number orphan entries flag a TOC page",
          toc_order_suspect(bare), "expected True")
    shuffled = "\n".join(
        [f"- {i} Entrada de prueba {p}" for i, p in
         enumerate([60, 74, 62, 71, 64, 69, 65, 67, 68, 66], start=1)])
    check("non-monotonic page-number tails flag a TOC page",
          toc_order_suspect(shuffled), "expected True")
    missing = "\n".join(
        [f"- {i} Entrada de prueba {50 + i}" for i in range(1, 9)]
        + ["- ctual  y prácticas previas 1.1",
           "- otra entrada truncada sin número 2.3"])
    check("missing page-number tails flag a TOC page",
          toc_order_suspect(missing), "expected True")

    # 10. verify() wiring: a page whose markdown contains a structurally
    # broken TOC joins order_flagged even though its bigram recall is
    # perfect (the field failure mode: TOC pages scored bigram 0.81-0.92,
    # above ORDER_THRESHOLD). Reference recall is unaffected by the extra
    # markdown (recall only asks "is reference text missing?"), so the page
    # passes unigram — the structural signal alone must promote it.
    print("case 10: verify() wires TOC suspicion into order_flagged")
    r = verify(simple, {1: ref[0] + "\n" + _TOC_BROKEN, 2: ref[1]})
    check("page 1 passes unigram", 1 not in r.flagged,
          f"recall={r.recall[1]}")
    check("page 1 bigram is above ORDER_THRESHOLD (bigram alone missed it)",
          r.bigram[1] is not None and r.bigram[1] >= ORDER_THRESHOLD,
          f"bigram={r.bigram[1]}")
    check("page 1 order_flagged via TOC structure", 1 in r.order_flagged,
          f"order_flagged={r.order_flagged}")
    check("page 2 untouched", 2 not in r.order_flagged,
          f"order_flagged={r.order_flagged}")

    # 11. Clean TOC through the same wiring: no flag.
    print("case 11: clean TOC page does not order-flag")
    r = verify(simple, {1: ref[0] + "\n" + _TOC_CLEAN, 2: ref[1]})
    check("page 1 not order_flagged", 1 not in r.order_flagged,
          f"order_flagged={r.order_flagged}")

    # 12. orphan_residue_suspect on real field-document shapes.
    print("case 12: orphan_residue_suspect structural signal")
    check("OCR'd footnote block with 2 fused-orphan lines is suspect",
          orphan_residue_suspect(_RESIDUE_87), "expected True")
    check("corrupted footnote pair (npendio/mpendio) is suspect",
          orphan_residue_suspect(_RESIDUE_330), "expected True")
    one_line = _RESIDUE_87.split("\n\n")[0]
    check("a single residue line does not flag (page-break tolerance)",
          not orphan_residue_suspect(one_line), "expected False")
    check("marker-led list bodies are not residue",
          not orphan_residue_suspect(_RESIDUE_MARKER_LED), "expected False")
    check("lowercase continuation paragraphs are not residue",
          not orphan_residue_suspect(_RESIDUE_CONTINUATION),
          "expected False")
    sts_lines = ("21 Véase la STS de 30 de abril de 1960.\n\n"
                 "22 Véase la STS de 27 de mayo de 1998.")
    check("'STS de 30 de abril' footnotes are not residue "
          "(number + lowercase is no boundary)",
          not orphan_residue_suspect(sts_lines), "expected False")
    num_start = (
        "15 de diciembre de 1998 y 10 mayo de 1999. 17 Consulta 28.a "
        "del compendio de consultas.\n\n"
        "23 de octubre de 1991 y 3 de diciembre de 1994. 19 Consulta "
        "28.b del compendio de consultas.")
    check("sentences starting with a number are not residue",
          not orphan_residue_suspect(num_start), "expected False")
    quoted = (
        "Los demás que establezca el Banco de España». 5 La norma indica "
        "lo siguiente: «Los demás que establezca el Banco de España».\n\n"
        "Los demás que establezca el Banco de España». 6 La norma repite "
        "lo siguiente: «Los demás que establezca el Banco de España».")
    check("quoted repetitions before footnote markers are not residue",
          not orphan_residue_suspect(quoted), "expected False")
    check("list items never count as residue lines",
          not orphan_residue_suspect(
              "- cubierto tácito. a) Del descubierto tácito.\n\n"
              "- orte del  descubierto tácito. b) Del importe del "
              "descubierto tácito."), "expected False")
    check("plain prose page is not residue",
          not orphan_residue_suspect(ref[0]), "expected False")
    check("broken TOC page is not residue (toc_order_suspect's class)",
          not orphan_residue_suspect(_TOC_BROKEN), "expected False")

    # 13. verify() wiring: a page whose markdown carries the residue block
    # joins order_flagged even though unigram recall passes (extra tokens
    # never count against a page) and bigram is untouched on the healthy
    # majority of the page.
    print("case 13: verify() wires orphan residue into order_flagged")
    r = verify(simple, {1: ref[0] + "\n" + _RESIDUE_87, 2: ref[1]})
    check("page 1 passes unigram", 1 not in r.flagged,
          f"recall={r.recall[1]}")
    check("page 1 order_flagged via residue signal", 1 in r.order_flagged,
          f"order_flagged={r.order_flagged}")
    check("page 2 untouched", 2 not in r.order_flagged,
          f"order_flagged={r.order_flagged}")

    if failures:
        print(f"\n{failures} self-test check(s) FAILED")
        sys.exit(1)
    print("\nall self-test checks passed")
