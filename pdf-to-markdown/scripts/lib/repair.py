"""Deterministic text repair (contract: repair.py signature block).

Six pure functions fixing defect classes CONFIRMED on a real document
(CCBPBAbril2026.pdf, Banco de España, 424 pp — docling route + tesseract OCR
on decorative pages). Each repair is deterministic and evidence-gated: no
model calls, no guessing. Anything not provably broken is left byte-identical.

1. sanitize(markdown) — control-character / tofu stripping.
   The real document contains 160 literal U+0007 BEL characters (docling's
   rendering of symbol-font bullet glyphs), which display as tofu. Removed:
   C0 controls except \\n and \\t (\\r included — pipeline markdown is
   LF-only, and a stray CR is extractor debris, never content), DEL,
   Unicode private-use-area glyphs (BMP U+E000-U+F8FF and planes 15/16 —
   symbol-font codepoints no reader can render), U+FFFD REPLACEMENT
   CHARACTER (marks already-lost bytes; carries zero content and renders as
   tofu — tier V's recall metric is the loss detector, not this glyph),
   U+FEFF (BOM/ZWNBSP), and the U+FFFE/U+FFFF noncharacters. Deliberately
   KEPT: ZWJ/ZWNJ and other format controls (legitimate in Indic/Persian
   text and emoji), soft hyphen, all printable Unicode. Space handling: a
   junk run is removed together with adjacent spaces/tabs, leaving exactly
   one space when it sat between words ("- 2 \\x07 Obligaciones" ->
   "- 2 Obligaciones") and none at a line edge — this collapses only the
   doubled spaces the removal itself would create; pre-existing spacing in
   clean text is untouched. Idempotent by construction (output contains no
   junk chars, and the regex requires at least one).

2. fix_word_reversal(pages, reference) — the docling reversed-cell defect.
   Confirmed shape: the FIRST visual line of a table cell is emitted with
   its words in exactly reversed order ("cuenta la de Mantenimiento" for
   "Mantenimiento de la cuenta"); continuation lines are appended after it
   in correct order, so a multi-line cell becomes reversed-prefix +
   correct-order tail within one markdown cell. Repair is evidence-gated
   against the order-true poppler reference for the same page:

   * Segmenting: pipe-table rows are split on "|" and each cell is a
     candidate segment (separator rows skipped); other lines contribute one
     segment = the line minus its markdown prefix (heading hashes,
     blockquote marker, list marker), which is preserved verbatim. Lines
     inside fenced code and lines starting with "<" (HTML/comments) are
     never candidates. Minimum segment size: 3 words.
   * Scoring: both sides are normalized exactly as verify.py does (NFKC,
     casefold, _ -> space, number-aware \\w+ tokens; reference
     de-hyphenated) EXCEPT that 1-char tokens are kept — "a"/"y"/"o" are
     load-bearing order evidence in Spanish, and this scorer needs order,
     not recall. score(seq) = fraction of adjacent token bigrams present in
     the page reference's bigram set.
   * Boundary search: for every split point k in [3..n] the candidate is
     reversed(words[:k]) + words[k:] (k == n is full reversal); k is chosen
     to maximize the WHOLE candidate's score, which naturally rewards the
     correct boundary via the junction bigram. Words are whitespace tokens
     of the ORIGINAL text — reversal reorders them but never rewrites them,
     so punctuation/capitalization ride along and the repaired segment is
     the source text re-ordered, not a normalized ghost.
   * Reference quirk absorbed: poppler renders this document's drop-cap
     table terms letter-split ("D escubierto", "S iguiendo"), so the
     bigram set is the UNION of the raw tokenization and a variant with
     "single uppercase letter + lowercase word" welded back. Additive
     only — it can supply reversed-order evidence, never remove original
     support.
   * Swap gates (ALL must hold; false negatives are preferred to false
     positives): over the reversed span only, DISCRIMINATIVE original
     support — original-order bigram hits whose mirrored pair is NOT also
     in the reference — must be near zero: at most max(1, ORIG_MAX *
     n_pairs) hits. Discrimination matters because `pdftotext -layout`
     welds table columns onto one line, minting accidental function-word
     pairs (real case: "Mantenimiento de la cuenta   La entidad gestiona
     la cuenta..." puts both (cuenta, la) and (la, cuenta) in the
     reference); a pair supported in both directions is not "real
     support" for either order. Additionally the reversed-order score
     must be >= REV_MIN (0.60) and beat the discriminative original
     score by >= MARGIN (0.40); and the whole candidate must beat the
     whole original (> s0). The gate applies to the prefix, not the whole
     segment, because a mixed cell's correct-order tail legitimately
     supports the original. Healthy text present in the reference fails
     the near-zero gate (its own directional bigrams are hits); text
     absent from the reference fails REV_MIN. Pages with no/empty
     reference are returned untouched.
   * Known limit: 2-word segments ("expreso Descubierto") are below the
     3-word floor and stay broken by design — a 2-word swap has a single
     bigram of evidence each way, which is a coin flip, and tier V's
     bigram signal still flags the page for vision if it matters.
   * Cell padding: repaired cells are re-padded to the original cell width
     so table column alignment survives byte-for-byte where lengths allow.
   * Returns (pages, {page: segments_fixed}) — counts contain only pages
     with >= 1 fix, and a page with 0 fixes is returned as the SAME string
     object it came in as (never-modify-clean guarantee is checkable by
     identity).

3. clean_ocr_debris(markdown) — tesseract noise on decorative photo pages.
   Confirmed shape: runs of standalone fragment lines ("ie", "&gt;", "o",
   "S", ".", "O a a", "dl") between real blocks. Keep-rules, each checked
   before any removal (err conservative — when in doubt, keep):
     K1 blank lines (spacing is layout, not debris);
     K2 fenced code blocks, fences included (verbatim by definition);
     K3 headings ("# A" is legitimate structure however short);
     K4 table rows / separator rows (any line containing "|");
     K5 list items with content ("- o" could be a legitimate glyph list);
     K6 blockquote lines (quoted text is somebody else's content);
     K7 lines starting with "<" (HTML tags, <!-- image --> placeholders);
     K8 horizontal rules (---, ***, ___);
     K9 lines containing any digit (page numbers, "42", section "3.1");
     K10 any word of >= 3 alphabetic characters (real prose survives);
     K11 1-2 letter words that stand alone as plausible prose
         ("No", "Sí", "Ok", "Go" — dialogue/poem lines), via allowlist;
     K12 ellipsis omission markers ("[…]", "[...]", "(...)", "…", "...")
         — deliberate elision marks inside quoted material, confirmed
         legitimate in the real document's quoted legal text.
   Removal rules on what remains (HTML entities decoded first so "&gt;"
   is judged as ">"):
     R1 no alphanumeric content at all (".", ">", "; :");
     R2 a single 1-2-letter token not in the K11 allowlist
        ("ie", "o", "S", "dl" — never legitimate standalone prose);
     R3 2+ tokens, every one a single letter ("O a a" — multi-word lines
        keep only if any token has >= 2 letters, so "I am" survives).
   A removed line takes one adjacent blank line with it, so the block
   collapses cleanly instead of leaving stacked blanks. Returns
   (markdown, debris_lines_removed) — blank lines are not counted.

4. strip_icon_glyphs(markdown) — margin badge icons read as text.
   The source document decorates paragraphs with margin badge icons whose
   glyphs docling reads as short letter runs. Confirmed forms: "(Y " / "¡0 "
   / bare "Y " / "¡US " / "LJ " / "DA " prefixing paragraphs, list items and
   headings ("## ¡0 Se ha considerado…"), and standalone lines "¡Y", "(Y",
   "¡0", "CÓ S". Generic junk-token shape: 1-4 chars, NO lowercase, drawn
   from uppercase (incl. accented) letters / digits / ¡!()§, stripped only
   when (a) the token is an entire standalone line, or (b) it leads a
   paragraph / list item / heading and is followed by one space and an
   uppercase/¡/«-initial word (junk always precedes a fresh sentence; real
   abbreviations are near-always mid-sentence or followed by lowercase or
   punctuation — "EE. UU." never matches because the token must be followed
   by a space, not a period). Keep-guards, each confirmed against real
   usages in the field document:
     G1 allowlist of Spanish banking/legal abbreviations (UE, BCE, TAE, TS,
        DNI, SMS, ICO, B2B, STS, FEIN, …) — "TS Aquellas en las que…" is a
        known junk-prefix false negative kept BY DESIGN via this guard;
     G2 pure roman-numeral tokens ("## I Criterios…", "## II …" are real
        heading numbers);
     G3 pure digits ("- 1 Resulta…" numbered footnotes/items; standalone
        "19"/"416" page numbers);
     G4 tokens containing «» (real case: «1 Los sujetos obligados…» opens a
        QUOTED article — « + article number is content, never junk) or ")"
        ("## A) Denegar…" enumerations);
     G5 single-letter tokens are never stripped EXCEPT "Y" (below) — "E",
        "A", "O" are real Spanish words and appear in mangled-but-real
        lines;
     G6 bare "Y" is a real conjunction: it is stripped ONLY at
        paragraph/list start, followed by a CAPITALIZED word (Spanish
        orthography never capitalizes after a mid-sentence "Y" except
        proper nouns) whose lowercase form is additionally in a
        function-word/sentence-starter set ("Y Así,…", "Y Sin perjuicio…")
        — "Y María…" (proper noun) survives. If in doubt "Y" stays;
     G7 standalone lines keep any line with an allowlisted or roman token,
        or with no letter/¡ at all (pure digits/punct — page numbers);
     G8 fenced code, table rows, blockquotes and HTML lines are never
        touched.
   Known residuals (documented, not bugs): "HORSES" standalone lines
   (6 chars, over the 1-4 generic bound) and "E TI z …" (single-letter
   token, G5) are left for clean_ocr_debris/vision.

5. drop_duplicate_orphans(markdown) — wrapped-line reordering fragments.
   A fragment of a line is emitted BEFORE the full line: standalone, or
   fused onto the start of the very line (usually a footnote) that contains
   the full text, often truncated mid-word ("entes extremos:" from
   "…siguientes extremos:"; "5 de diciembre…" from "15 de diciembre…";
   "TS de 16…" from "STS de 16…"). Never fuzzy: a prefix/line F is dropped
   only when F itself — whitespace-normalized, case-sensitive, optionally
   minus its first 1-3 characters — reappears as a substring of the rest of
   the same line or the next non-empty line. Split candidates within a
   line: positions after sentence-final punctuation ([.:;»!?] + space +
   uppercase/«/¡/digit), footnote-marker starts (1-2 digit number +
   space + uppercase/«, the number not glued to a bigger number/date),
   and — second field round, generalized against the whole document with
   zero false positives — printed roman/letter list markers ("i)", "a)",
   lowercase follow-word allowed: OCR'd footnotes fuse an orphan before
   "i) la tasa…") plus mid-line dash markers ("— la tasa…"). Bare numbers
   before a lowercase word are deliberately NOT boundaries: that variant
   ate the healthy "21 Véase la STS de 30 de abril de 1960." (a date is
   exactly "number + lowercase") and stays rejected. The two branches
   gate differently:
     (a) standalone lines (a fragment line emitted before its full line)
       keep the original gates unchanged: normalized F is 12-200 chars,
       only plain non-list lines, and the reappearance in the (longer)
       next line must be PROVABLY a tail of a longer run — word-glued
       (immediately preceded by a word character: "sigui|entes", "1|5 de",
       "S|TS") or, full form only, F starts with a lowercase letter (a
       sentence fragment, since real sentences start uppercase). Truncated
       forms (1-3 chars off the front, >= 10 chars left) are tried only
       when the untruncated F does not appear at all, and always require
       the glued match — otherwise any legitimately-quoted sentence would
       strip via its own 1-char truncation;
     (b) fused prefixes (fragment glued onto the start of the very line
       that repeats it) use the generalized gates: F is 10-200 chars;
       word-glued full-form match at >= 10 chars, OR lowercase/digit-start
       full-form containment at >= 12 chars, OR a 1-3-char-truncated form
       word-glued (full form absent, >= 10 chars left), OR — multi-chunk
       evidence, for orphans that were themselves re-wrapped in print — F
       splits into 2-3 whitespace-boundary chunks, EACH >= 12 chars and
       EACH reappearing word-glued (chunks are never truncated; only the
       first chunk may drop 1-3 leading chars, mirroring the whole-
       fragment truncation rule). List items ARE candidates for this
       branch (the field document's OCR'd footnote lists carry fused
       orphans: "- cubierto tácito. a) Del descubierto tácito."); the
       markdown marker is preserved and only the body prefix is cut.
       Fuzzy/edit-distance matching was evaluated and REJECTED (it
       stripped legitimately-quoted near-repetitions); do not add it.
   Common gates: headings, table rows, blockquotes, HTML and fenced code
   are never candidates (broken TOC entries are verify.toc_order_suspect's
   job, not a repair). A legitimate quotation ("Los demás que establezca
   el Banco de España». 5 La norma dice: «Los demás…»") fails every
   branch: it starts uppercase and its match starts at a «/space
   boundary, not word-glued. Returns (markdown, fragments_dropped); a
   dropped standalone line takes one adjacent blank line with it.

6. fix_list_structure(pages, reference) — reference-indent-guided list
   repair (docling route; certified on the field document: 20/20
   random-sample audit correct, zero changes on every repo fixture through
   both engines). Three confirmed defect classes, all gated on the
   `pdftotext -layout` reference's printed indentation:
     A. continuation paragraphs flattened out of items: a paragraph whose
        printed indent proves it belongs INSIDE the preceding list item is
        re-attached (indented one level below the item);
     B. invented markers on top of printed markers: docling emits
        "2. ii) Para exigir…" where print shows "ii)" as the line-leading
        marker — the invented positional "2." is dropped ("- ii) …");
     C. level errors: items of one markdown run whose printed marker
        columns form >= 2 clearly separated columns are re-nested to
        match the print.
   Guards (each tuned through four false-positive rounds — preserve
   exactly; the inline comments carry the per-guard rationale):
   marker-required run matching (class C only acts on runs where EVERY
   item matched a marker-bearing reference line), continuation-indent
   depth requirement with the deep-wrapped-lines check (class A),
   ordinal-chain column merging (jittered/right-aligned markers of one
   printed list collapse to their chain median before clustering), and
   the class-D bare-number gap guard (a bare number is only a printed
   marker when the reference shows a marker->text gap >= 2 columns —
   sentences that merely START with a number never match). Same shape as
   fix_word_reversal: 1-indexed pages, returns (pages, {page: changes}),
   and a page with zero changes comes back as the SAME string object.

Stdlib only; imports contract only (per module rules). Self-test:
`uv run repair.py` — fixtures are verbatim excerpts of the real document's
markdown and its `pdftotext -layout` reference.
"""
from __future__ import annotations

import html
import re
import sys
import unicodedata
from collections import Counter

from contract import Reason  # noqa: F401  (re-exported context; see contract)

# ------------------------------------------------------------------ sanitize

# C0 minus \t\n (plus \x7f DEL), PUA (BMP + planes 15/16), BOM/ZWNBSP,
# REPLACEMENT CHARACTER, BMP noncharacters.
_JUNK_CLASS = (
    "\x00-\x08\x0b-\x1f\x7f"          # C0 minus \t\n, plus DEL
    "\ue000-\uf8ff"                      # private use area (BMP)
    "\U000F0000-\U000FFFFD"              # private use plane 15
    "\U00100000-\U0010FFFD"              # private use plane 16
    "\ufeff\ufffd\ufffe\uffff"         # BOM/ZWNBSP, U+FFFD, noncharacters
)
# A junk run plus the spaces/tabs hugging it: removed as one unit so the
# doubled space the removal would leave is collapsed at the same time.
_JUNK_RUN_RE = re.compile(rf"[ \t]*[{_JUNK_CLASS}]+[ \t]*")


def sanitize(markdown: str) -> str:
    """Strip control/PUA/tofu characters; collapse the spaces they leave."""

    def repl(m: re.Match) -> str:
        s, text = m.group(0), m.string
        at_line_start = m.start() == 0 or text[m.start() - 1] == "\n"
        at_line_end = m.end() == len(text) or text[m.end()] == "\n"
        if at_line_start or at_line_end:
            return ""  # edge junk vanishes with its padding: no stray indent
        if s[0] in " \t" or s[-1] in " \t":
            return " "  # junk between words: exactly one space remains
        return ""  # junk welded inside a word was a glyph; drop it

    return _JUNK_RUN_RE.sub(repl, markdown)


# ---------------------------------------------------------- word reversal

# Same shape as verify.py's tokenizer (numbers first so "1.2"/"1,204" stay
# whole), but WITHOUT the min-length filter: 1-char Spanish function words
# ("a", "y", "o") are exactly the order evidence this module scores.
_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)+|\w+")
# Mirrors verify.py: pdftotext keeps line-break hyphenation; rejoin it.
_HYPHEN_BREAK_RE = re.compile(r"(?<=\w)-[ \t]*\n[ \t]*(?=\w)")

MIN_WORDS = 3     # segments shorter than this are never candidates
MIN_K = 3         # smallest reversible span (2-word swaps are coin flips)
ORIG_MAX = 0.20   # reversed span: max original-order bigram support
REV_MIN = 0.60    # reversed span: min reversed-order bigram support
MARGIN = 0.40     # reversed span: min (reversed - original) gap

_TABLE_SEP_RE = re.compile(r"^[\s:|-]+$")
_LINE_PREFIX_RE = re.compile(
    r"^(\s*(?:#{1,6}\s+|>\s*|(?:[-*+]|\d{1,3}[.)])\s+)?)"
)
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")


def _norm_tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).casefold().replace("_", " ")
    return _TOKEN_RE.findall(text)


# Poppler renders drop-cap words letter-split ("D escubierto", "S iguiendo"
# in the real document). Weld candidates: lone uppercase letter + lowercase
# word. The welded variant only AUGMENTS the bigram set (union), so genuine
# short words ("Y esta", "A veces") lose nothing.
_SPLIT_CAP_RE = re.compile(r"(?<!\w)(\w)[ ]+(\w{2,})")


def _weld_split_caps(text: str) -> str:
    return _SPLIT_CAP_RE.sub(
        lambda m: (m.group(1) + m.group(2))
        if m.group(1).isupper() and m.group(2)[0].islower()
        else m.group(0),
        text,
    )


def _ref_bigrams(ref_text: str) -> set[tuple[str, str]]:
    ref_text = _HYPHEN_BREAK_RE.sub("", ref_text)
    toks = _norm_tokens(ref_text)
    bigrams = set(zip(toks, toks[1:]))
    welded = _norm_tokens(_weld_split_caps(ref_text))
    bigrams.update(zip(welded, welded[1:]))
    return bigrams


def _score(tokens: list[str], bigrams: set[tuple[str, str]]) -> float:
    """Fraction of adjacent bigrams supported by the page reference."""
    if len(tokens) < 2:
        return 0.0
    hits = sum(1 for pair in zip(tokens, tokens[1:]) if pair in bigrams)
    return hits / (len(tokens) - 1)


def _repair_segment(seg: str, bigrams: set[tuple[str, str]]) -> str | None:
    """Return the repaired segment text, or None to leave it untouched.

    Words are the ORIGINAL whitespace tokens; the repair is purely a
    reordering (reversed prefix + untouched tail), joined with single
    spaces. Matching happens on normalized tokens only.
    """
    words = seg.split()
    if len(words) < MIN_WORDS:
        return None
    word_toks = [_norm_tokens(w) for w in words]
    flat = [t for wt in word_toks for t in wt]
    if len(flat) < 2:
        return None
    s0 = _score(flat, bigrams)

    best_k, best_s = 0, s0
    for k in range(MIN_K, len(words) + 1):
        cand = [t for wt in (word_toks[:k][::-1] + word_toks[k:]) for t in wt]
        s = _score(cand, bigrams)
        if s > best_s:
            best_k, best_s = k, s

    if best_k == 0:
        return None
    # Gates evaluated over the reversed span only: a mixed cell's healthy
    # tail legitimately supports the original ordering and must not veto
    # the repair — but the span being reversed must itself have (near) zero
    # DISCRIMINATIVE original support and strong reversed-order support.
    # A pair whose mirror is also in the reference supports both orders
    # equally (function-word pairs, -layout cross-column welds) and is not
    # evidence for either; the near-zero gate counts directional hits only,
    # with a one-hit floor for genuinely accidental welds (see module doc).
    prefix_orig = [t for wt in word_toks[:best_k] for t in wt]
    prefix_rev = [t for wt in word_toks[:best_k][::-1] for t in wt]
    n_pairs = len(prefix_orig) - 1
    if n_pairs < 2:  # a span this thin has no usable order evidence
        return None
    so_hits = sum(
        1 for pair in zip(prefix_orig, prefix_orig[1:])
        if pair in bigrams and (pair[1], pair[0]) not in bigrams
    )
    so, sr = so_hits / n_pairs, _score(prefix_rev, bigrams)
    if so_hits > max(1, int(ORIG_MAX * n_pairs)):
        return None
    if sr < REV_MIN or (sr - so) < MARGIN:
        return None
    return " ".join(words[:best_k][::-1] + words[best_k:])


def _repair_cell(cell: str, bigrams: set[tuple[str, str]]) -> str | None:
    """Repair one table cell, preserving its padding/width when possible."""
    core = cell.strip()
    if not core:
        return None
    fixed = _repair_segment(core, bigrams)
    if fixed is None or fixed == core:
        return None
    lead = cell[: len(cell) - len(cell.lstrip())]
    had_trail = cell != cell.rstrip()
    pad = max(len(cell) - len(lead) - len(fixed), 1 if had_trail else 0)
    return lead + fixed + " " * pad


def _repair_page(text: str, ref_text: str) -> tuple[str, int]:
    bigrams = _ref_bigrams(ref_text)
    if not bigrams:
        return text, 0
    out: list[str] = []
    fixed_count = 0
    in_fence = False
    for line in text.split("\n"):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        stripped = line.strip()
        if in_fence or not stripped or stripped.startswith("<"):
            out.append(line)
            continue
        if stripped.startswith("|") and line.count("|") >= 2:
            if _TABLE_SEP_RE.match(line):
                out.append(line)
                continue
            parts = line.split("|")
            changed = False
            for i, part in enumerate(parts):
                fixed = _repair_cell(part, bigrams)
                if fixed is not None:
                    parts[i] = fixed
                    changed = True
                    fixed_count += 1
            out.append("|".join(parts) if changed else line)
            continue
        prefix = _LINE_PREFIX_RE.match(line).group(1)
        seg = line[len(prefix):]
        fixed = _repair_segment(seg, bigrams)
        if fixed is not None and fixed != seg.strip():
            out.append(prefix + fixed)
            fixed_count += 1
        else:
            out.append(line)
    return "\n".join(out), fixed_count


def fix_word_reversal(
    pages: dict[int, str], reference: dict[int, str]
) -> tuple[dict[int, str], dict[int, int]]:
    """Reverse word-reversed segments back, gated on reference bigram evidence.

    pages / reference: 1-indexed page -> markdown / poppler -layout text
    (contract). Pages without a usable reference are returned untouched.
    Returns (pages, {page: segments_fixed}); counts hold only pages with
    >= 1 fix, and untouched pages keep their original string identity.
    """
    out: dict[int, str] = {}
    counts: dict[int, int] = {}
    for page, text in pages.items():
        ref_text = reference.get(page) or ""
        repaired, n = _repair_page(text, ref_text) if ref_text.strip() \
            else (text, 0)
        if n > 0:
            out[page] = repaired
            counts[page] = n
        else:
            out[page] = text  # same object: provably untouched
    return out, counts


# ------------------------------------------------------------- OCR debris

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+\S")
_HR_RE = re.compile(r"^\s*(?:[-*_]\s*){3,}$")
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)  # alphabetic runs only
# 1-2 letter words that plausibly stand alone as a prose/poem/dialogue line.
_STANDALONE_OK = {"no", "sí", "si", "ok", "go", "ja", "oh", "ay"}
# Ellipsis omission markers: "[…]" / "[...]" / "(...)" / "…" / "..." lines
# mark elided quoted text (real occurrences in the reference document).
_ELLIPSIS_LINE_RE = re.compile(r"^[\[(]?\s*(?:…|\.{3,})\s*[\])]?$")


def _is_debris(line: str) -> bool:
    """True only for lines that are OCR fragment noise, never structure."""
    stripped = line.strip()
    if not stripped:                       # K1
        return False
    if _HEADING_RE.match(line):            # K3
        return False
    if "|" in line:                        # K4
        return False
    if _LIST_ITEM_RE.match(line):          # K5
        return False
    if stripped.startswith(">"):           # K6
        return False
    if stripped.startswith("<"):           # K7
        return False
    if _HR_RE.match(stripped):             # K8
        return False
    text = html.unescape(stripped)         # judge "&gt;" as ">"
    if _ELLIPSIS_LINE_RE.match(text):      # K12
        return False
    if any(c.isdigit() for c in text):     # K9
        return False
    words = _WORD_RE.findall(text)
    if any(len(w) >= 3 for w in words):    # K10
        return False
    if not any(c.isalnum() for c in text):  # R1: pure punctuation/symbols
        return True
    if len(words) == 1:                    # R2 (with K11 allowlist)
        return words[0].casefold() not in _STANDALONE_OK
    # R3: multi-word debris only when EVERY word is a single letter.
    return len(words) >= 2 and all(len(w) == 1 for w in words)


def clean_ocr_debris(markdown: str) -> tuple[str, int]:
    """Remove standalone OCR fragment lines; keep all structural short lines.

    Returns (markdown, debris_lines_removed). Each removed line takes one
    adjacent blank line with it so the surrounding block spacing collapses
    to what it would have been without the debris.
    """
    lines = markdown.split("\n")
    out: list[str] = []
    removed = 0
    in_fence = False
    skip_next_blank = False
    for line in lines:
        if _FENCE_RE.match(line):          # K2: fences + contents verbatim
            in_fence = not in_fence
            out.append(line)
            skip_next_blank = False
            continue
        if in_fence:
            out.append(line)
            continue
        if skip_next_blank and not line.strip() and out and not out[-1].strip():
            skip_next_blank = False
            continue  # swallow the blank the removed line left behind
        skip_next_blank = False
        if _is_debris(line):
            removed += 1
            skip_next_blank = True
            continue
        out.append(line)
    return "\n".join(out), removed


# ------------------------------------------------------------- icon glyphs

# Junk-token alphabet: uppercase (incl. accented) letters, digits, ¡!()§.
# «» are deliberately NOT strippable (guard G4: «1 opens quoted article
# text in the real document); ")" is likewise excluded via a guard so "A)"
# enumerations survive.
_ICON_CHARS = "A-ZÁÉÍÓÚÑÜ0-9¡!()§"
_ICON_PREFIX_RE = re.compile(
    rf"^([{_ICON_CHARS}]{{1,4}}) (?=[A-ZÁÉÍÓÚÑÜ¡«])"
)
_ICON_LINE_RE = re.compile(rf"^[{_ICON_CHARS} ]{{1,4}}$")
_ICON_LINE_PREFIX_RE = re.compile(r"^(\s{0,3}#{1,6}\s+|\s*[-*+]\s+)?")
_ROMAN_RE = re.compile(r"^[IVXLCDM]+$")
_HAS_LETTERISH_RE = re.compile(r"[A-ZÁÉÍÓÚÑÜ¡]")

# G1: real Spanish banking/legal abbreviations that can plausibly lead a
# sentence — never stripped even when every other junk gate matches.
_ABBREV_ALLOW = {
    "EE", "UU", "UE", "CE", "BCE", "BE", "BDE", "TS", "TC", "AN", "AP",
    "STS", "TJUE", "TAE", "TIN", "TEDR", "DNI", "NIF", "NIE", "SMS",
    "PIN", "BIC", "CBP", "FEIN", "FIAE", "ICO", "BOE", "SEPA", "B2B",
    "RD", "RDL", "OM", "IVA", "IAJD", "FGD", "CNMV", "AEAT", "PSD2",
    "EUR", "USD", "GBP", "TFUE", "LCCI", "LSP",
}

# G6: bare "Y" is stripped only when the following capitalized word is a
# common Spanish function/sentence-starter word (lowercased) — this is the
# conservative gate that lets "Y Así, …" strip while "Y María …" (proper
# noun after a real conjunction) survives.
_ES_SENTENCE_STARTERS = {
    "a", "ahora", "al", "ante", "aquellas", "aquellos", "asimismo", "así",
    "aunque", "cabe", "como", "con", "consecuentemente",
    "consiguientemente", "cual", "cualquier", "cuando", "dado", "de",
    "del", "dicha", "dicho", "el", "ella", "ello", "en", "entendemos",
    "entre", "es", "esta", "estas", "este", "estos", "finalmente",
    "hemos", "igualmente", "la", "las", "lo", "los", "no", "nos", "para",
    "por", "respecto", "resulta", "se", "según", "si", "sin", "sobre",
    "también", "toda", "todas", "todo", "todos", "tras", "un", "una",
    "ya",
}


def _icon_token_keeps(token: str) -> bool:
    """True when a candidate junk token must be KEPT (guards G1-G5)."""
    if token in _ABBREV_ALLOW:                     # G1
        return True
    if _ROMAN_RE.match(token):                     # G2: I, II, IV, …
        return True
    if token.isdigit():                            # G3
        return True
    if ")" in token or "«" in token or "»" in token:  # G4
        return True
    if not _HAS_LETTERISH_RE.search(token):        # digits+punct only
        return True
    return False


def strip_icon_glyphs(markdown: str) -> tuple[str, int]:
    """Remove margin-badge icon glyphs docling read as text.

    Two shapes (module doc, item 4): whole standalone junk lines, and junk
    tokens leading a paragraph / list item / heading followed by a fresh
    capitalized sentence. Returns (markdown, tokens_stripped); a removed
    standalone line takes one adjacent blank line with it.
    """
    lines = markdown.split("\n")
    out: list[str] = []
    stripped = 0
    in_fence = False
    skip_next_blank = False
    for line in lines:
        if _FENCE_RE.match(line):                  # G8
            in_fence = not in_fence
            out.append(line)
            skip_next_blank = False
            continue
        if in_fence:
            out.append(line)
            continue
        if skip_next_blank and not line.strip() and out and not out[-1].strip():
            skip_next_blank = False
            continue
        skip_next_blank = False
        s = line.strip()
        if not s or s.startswith(("|", ">", "<")):  # G8
            out.append(line)
            continue
        # Shape (a): the whole line is a junk token run ("¡Y", "CÓ S").
        if _ICON_LINE_RE.match(s):
            tokens = s.split()
            if (
                _HAS_LETTERISH_RE.search(s)        # G7: pure digits stay
                and not any(t in _ABBREV_ALLOW or _ROMAN_RE.match(t)
                            for t in tokens)       # G7
            ):
                stripped += 1
                skip_next_blank = True
                continue
            out.append(line)
            continue
        # Shape (b): junk token leading a paragraph / list item / heading.
        prefix = _ICON_LINE_PREFIX_RE.match(line).group(0)
        seg = line[len(prefix):]
        m = _ICON_PREFIX_RE.match(seg)
        if m:
            token = m.group(1)
            keep = _icon_token_keeps(token)
            if not keep and len(token) == 1:       # G5/G6
                if token != "Y":
                    keep = True
                else:
                    rest_words = seg[m.end():].split()
                    nxt = rest_words[0].strip(".,;:()!?¡¿«»") if rest_words else ""
                    keep = nxt.lower() not in _ES_SENTENCE_STARTERS
            if not keep:
                out.append(prefix + seg[m.end():])
                stripped += 1
                continue
        out.append(line)
    return "\n".join(out), stripped


# ----------------------------------------------------- duplicate orphans

ORPHAN_MIN_LEN = 12    # normalized fragment floor (standalone/lowercase form)
ORPHAN_TRUNC_MIN = 10  # floor after 1-3-char truncation
ORPHAN_MAX_LEN = 200   # a fragment is one wrapped line, never a paragraph
ORPHAN_MAX_TRUNC = 3   # chars the fragment may have lost at its start
ORPHAN_GLUED_MIN = 10  # fused-prefix branch: floor for word-glued matches
ORPHAN_CHUNK_MIN = 12  # multi-chunk evidence: per-chunk floor
ORPHAN_MAX_CHUNKS = 3  # multi-chunk evidence: partition size cap

# Footnote marker: standalone 1-2 digit number + space + uppercase/«¡ —
# the number must not be glued to a larger number/date ("1998 y", "5/2012").
_FOOT_MARKER_RE = re.compile(
    r"(?<![\d.,/§º°-])\d{1,2}[ \t\xa0]+(?=[A-ZÁÉÍÓÚÑÜ«¡])"
)
# Sentence boundary usable as a fragment/real-content split point.
_SENT_BOUND_RE = re.compile(r"[.:;»!?…][ \t\xa0]+(?=[A-ZÁÉÍÓÚÑÜ«¡\d])")
# Printed roman/letter list markers and mid-line dashes: OCR'd footnote
# blocks fuse orphans before these ("de este. ii) la tasa…"), and the
# marker's follow word is usually LOWERCASE, which the sentence-boundary
# pattern deliberately rejects. Bare numbers are NOT in this set — see
# module doc item 5 (the "STS de 30 de abril" keep-case).
_INLINE_MARK_RE = re.compile(
    r"(?<![\w.,/§º°-])(?:[ivxlcdm]{1,4}\)|[a-z]\)|[—-])[ \t\xa0]+(?=[\w«¡])"
)
# Markdown list-item marker (for stripping/preserving in branch (b)).
_ITEM_PREFIX_RE = re.compile(r"^([-*+]\s+|\d{1,3}[.)]\s+)")
# NBSP included: the field document mixes \xa0 into otherwise-plain runs
# ("artículo\xa07.3"), and whitespace flavor must not defeat the
# exact-substring duplicate gate.
_WS_RUN_RE = re.compile(r"[ \t\xa0]+")
_WORD_CHAR_RE = re.compile(r"\w")


def _norm_ws(text: str) -> str:
    return _WS_RUN_RE.sub(" ", text.strip())


def _glued_occurrence(frag: str, target: str) -> bool:
    """True if frag occurs in target immediately preceded by a word char
    (i.e. provably the tail of a longer run: "sigui|entes …")."""
    start = 0
    while True:
        i = target.find(frag, start)
        if i < 0:
            return False
        if i > 0 and _WORD_CHAR_RE.match(target[i - 1]):
            return True
        start = i + 1


def _is_duplicate_fragment(frag: str, *targets: str) -> bool:
    """Module-doc item 5 gates: frag (ws-normalized) is provably duplicate
    content — its full or 1-3-char-truncated form reappears in a target,
    word-glued (or, full form only, boundary-matched when frag starts
    lowercase, i.e. is itself a sentence fragment)."""
    frag = _norm_ws(frag)
    if not (ORPHAN_MIN_LEN <= len(frag) <= ORPHAN_MAX_LEN):
        return False
    starts_low = frag[:1].islower() or frag[:1].isdigit()
    for raw_target in targets:
        target = _norm_ws(raw_target)
        if not target:
            continue
        if frag in target:
            if _glued_occurrence(frag, target) or starts_low:
                return True
            continue  # appears cleanly (quotation) — not evidence of junk
        # Truncated forms: only when the full form is absent, and only
        # word-glued (see module doc for why both restrictions matter).
        for t in range(1, ORPHAN_MAX_TRUNC + 1):
            cand = frag[t:]
            if len(cand) < ORPHAN_TRUNC_MIN:
                break
            if _glued_occurrence(cand, target):
                return True
    return False


def _is_duplicate_prefix(frag: str, *targets: str) -> bool:
    """Branch (b) gates (module doc item 5): frag is provably a duplicated
    fused prefix. Wider than _is_duplicate_fragment in exactly three
    certified ways — glued floor 10 (vs 12), truncated-form floor 10, and
    multi-chunk evidence — and never used for standalone lines."""
    frag = _norm_ws(frag)
    if not (ORPHAN_GLUED_MIN <= len(frag) <= ORPHAN_MAX_LEN):
        return False
    starts_low = frag[:1].islower() or frag[:1].isdigit()
    for raw_target in targets:
        target = _norm_ws(raw_target)
        if not target:
            continue
        if frag in target and (
            _glued_occurrence(frag, target)
            or (starts_low and len(frag) >= ORPHAN_MIN_LEN)
        ):
            return True
        # Truncated forms: only when the full form is absent, and only
        # word-glued (same reasoning as _is_duplicate_fragment).
        for t in range(1, ORPHAN_MAX_TRUNC + 1):
            cand = frag[t:]
            if len(cand) < ORPHAN_GLUED_MIN:
                break
            if frag not in target and _glued_occurrence(cand, target):
                return True
        # Multi-chunk evidence: the orphan was itself re-wrapped in print,
        # so its pieces reappear glued at separate spots ("al  equivalente
        # ses devengados…" -> "anu|al equivalente" + "los intere|ses
        # devengados…"). Strictly 2-3 chunks (one chunk is the cases
        # above), each >= ORPHAN_CHUNK_MIN chars and each word-glued; only
        # the FIRST chunk may drop 1-3 leading chars (the whole-fragment
        # truncation rule), other chunks are never truncated — per-chunk
        # truncation would let short accidental substrings assemble a
        # false proof.
        words = frag.split(" ")
        if len(words) >= 2:
            def _partition(ws: list[str], depth: int, first: bool) -> bool:
                if not ws:
                    return depth >= 2
                if depth >= ORPHAN_MAX_CHUNKS:
                    return False
                for k in range(len(ws), 0, -1):
                    chunk = " ".join(ws[:k])
                    ok = (len(chunk) >= ORPHAN_CHUNK_MIN
                          and _glued_occurrence(chunk, target))
                    if not ok and first:
                        for t in range(1, ORPHAN_MAX_TRUNC + 1):
                            cand = chunk[t:]
                            if len(cand) < ORPHAN_CHUNK_MIN:
                                break
                            if _glued_occurrence(cand, target):
                                ok = True
                                break
                    if ok and _partition(ws[k:], depth + 1, False):
                        return True
                return False

            if _partition(words, 0, True):
                return True
    return False


def _is_plain_line(stripped: str) -> bool:
    return not (
        not stripped
        or stripped.startswith(("#", "|", ">", "<"))
        or _LIST_ITEM_RE.match(stripped)
        or _HR_RE.match(stripped)
    )


def drop_duplicate_orphans(markdown: str) -> tuple[str, int]:
    """Drop reordered wrapped-line fragments that duplicate adjacent text.

    (a) a standalone plain line whose text reappears inside the (longer)
    next non-empty line is dropped — original gates, _is_duplicate_fragment,
    plain non-list lines only; (b) a leading fragment fused before a
    footnote marker / sentence boundary / printed roman-letter-dash marker
    whose text reappears later in the same line (or the next one) is
    stripped off — generalized gates, _is_duplicate_prefix, list-item
    bodies included (their markdown marker is preserved). Nothing fuzzy.
    Returns (markdown, fragments_dropped).
    """
    lines = markdown.split("\n")
    out: list[str] = []
    dropped = 0
    in_fence = False
    skip_next_blank = False
    for idx, line in enumerate(lines):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out.append(line)
            skip_next_blank = False
            continue
        if in_fence:
            out.append(line)
            continue
        if skip_next_blank and not line.strip() and out and not out[-1].strip():
            skip_next_blank = False
            continue
        skip_next_blank = False
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "|", ">", "<")) \
                or _HR_RE.match(stripped):
            out.append(line)
            continue
        item_m = _ITEM_PREFIX_RE.match(stripped) \
            if _LIST_ITEM_RE.match(stripped) else None
        marker, body = (item_m.group(1), stripped[item_m.end():]) \
            if item_m else ("", stripped)
        next_line = next(
            (l for l in lines[idx + 1:] if l.strip()), "")
        # (a) whole-line orphan preceding the full line (never list items).
        if (
            item_m is None
            and len(_norm_ws(next_line)) > len(_norm_ws(stripped))
            and _is_duplicate_fragment(stripped, next_line)
        ):
            dropped += 1
            skip_next_blank = True
            continue
        # (b) fragment fused onto the start of the body that duplicates it.
        cut = None
        candidates = sorted(
            {m.start() for m in _FOOT_MARKER_RE.finditer(body)}
            | {m.end() for m in _SENT_BOUND_RE.finditer(body)}
            | {m.start() for m in _INLINE_MARK_RE.finditer(body)}
        )
        # Take the LAST passing split: the orphan may itself contain
        # sentence punctuation ("…la ley (art. 164 del Código Civil). 1
        # Salvo…"), so an earlier boundary can pass while leaving junk
        # behind. Safe because every passing prefix is, by the gate,
        # provably duplicated in what remains.
        for pos in candidates:
            if pos == 0 or pos > ORPHAN_MAX_LEN + 50:
                continue
            frag = body[:pos].rstrip()
            if _is_duplicate_prefix(frag, body[pos:], next_line):
                cut = pos
        if cut is not None:
            lead = line[: len(line) - len(line.lstrip())]
            out.append(lead + marker + body[cut:])
            dropped += 1
            continue
        out.append(line)
    return "\n".join(out), dropped


# ------------------------------------------------------- list structure

# Guard constants (module doc item 6 — tuned through four false-positive
# rounds on the field document; do not loosen without re-auditing):
LIST_MATCH_TOKENS = 4   # normalized-prefix tokens an md<->ref match needs
LIST_BARE_NUM_GAP = 2   # class-D: bare number needs this marker->text gap
LIST_CLUSTER_TOL = 3    # ref columns within this merge into one level
LIST_LEVEL_MIN_SEP = 4  # clusters must be this far apart to be levels
LIST_INDENT = 4         # markdown spaces per nesting level

# Plain \w+ tokens (NFKC, casefolded): list matching needs identity, not
# the number-aware shapes of the reversal scorer.
_LS_TOKEN_RE = re.compile(r"\w+")
# Printed marker shapes seen in `-layout` output. Bare numbers are matched
# here but only CLASSIFIED as markers when the marker->text gap >=
# LIST_BARE_NUM_GAP (the class-D guard): a sentence that merely starts
# with a number ("15 de diciembre…") prints with a single space.
_LS_REF_MARKER_RE = re.compile(
    r"^(\s*)"
    r"(•|·|—|-|\d{1,3}|[ivxlcdm]{1,7}[).]|[a-zA-Z][).])"
    r"(\s+)(\S.*)$"
)
# Markdown item line (docling emits one line per block).
_LS_ITEM_RE = re.compile(r"^(\s*)([-*+]|\d{1,3}\.)\s+(\S.*)$")
# Candidate printed-marker token leading the item text on the md side.
_LS_PRINTED_RE = re.compile(
    r"^(\d{1,3}|[ivxlcdm]{1,7}[).]|[a-zA-Z] ?[).])\s+(\S)"
)
# Lines never treated as items/paragraphs (structure that is not list
# content); <!-- comments are classified as neutral furniture separately.
_LS_SKIP_RE = re.compile(r"^\s*(#{1,6}\s|\||>|<|```|~~~|!\[|---\s*$)")
_LS_ROMAN_CHARS_RE = re.compile(r"[ivxlcdm]{2,7}")


def _ls_toks(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).casefold()
    return _LS_TOKEN_RE.findall(text)


def _ls_norm_marker(m: str) -> str:
    return m.rstrip(".)").replace(" ", "").casefold()


class _RefLine:
    """One non-blank `pdftotext -layout` line: printed indent + marker."""

    __slots__ = ("indent", "marker", "gap", "text_toks", "full_toks")

    def __init__(self, raw: str):
        # Tabs expand at the poppler default stop (8); stray BEL glyphs
        # (symbol-font bullets, see sanitize) would shift every column.
        exp = raw.expandtabs(8).replace("\x07", "")
        self.indent = len(exp) - len(exp.lstrip())
        m = _LS_REF_MARKER_RE.match(exp)
        if m:
            self.marker = m.group(2)
            self.gap = len(m.group(3))
            self.text_toks = _ls_toks(m.group(4))[: LIST_MATCH_TOKENS + 2]
        else:
            self.marker = None
            self.gap = 0
            self.text_toks = []
        self.full_toks = _ls_toks(exp)[: LIST_MATCH_TOKENS + 2]

    def marker_confirmed(self, tok: str) -> bool:
        """Is `tok` (md-side candidate printed marker) the printed marker
        of this line, per the class-D rules? Roman/letter+paren markers are
        proof by being the line-leading token; a BARE NUMBER additionally
        needs the marker column separated from the text column by >=
        LIST_BARE_NUM_GAP spaces — otherwise it is just a sentence that
        starts with a number."""
        if self.marker is None:
            return False
        if _ls_norm_marker(self.marker) != _ls_norm_marker(tok):
            return False
        if tok.rstrip(".)").isdigit():
            return self.gap >= LIST_BARE_NUM_GAP   # class-D column proof
        return True

def _ls_parse_ref(ref_text: str) -> list[_RefLine]:
    return [_RefLine(l) for l in ref_text.split("\n") if l.strip()]


def _ls_match_ref(md_toks: list[str], ref_lines: list[_RefLine],
                  start: int) -> int | None:
    """Find the ref line index >= start whose full-line or post-marker
    tokens start with md_toks[:LIST_MATCH_TOKENS]. Monotonic forward
    search with a one-time wrap to 0 (docling reading order occasionally
    differs from print order). Fewer than 2 tokens is too little evidence
    to match on at all."""
    need = md_toks[:LIST_MATCH_TOKENS]
    if len(need) < LIST_MATCH_TOKENS and len(need) < 2:
        return None
    n = len(ref_lines)
    order = list(range(start, n)) + list(range(0, start))
    for i in order:
        rl = ref_lines[i]
        if rl.full_toks[: len(need)] == need:
            return i
        if rl.marker is not None and rl.text_toks[: len(need)] == need:
            return i
    return None


class _ListBlock:
    """One markdown line, classified (docling emits one line per block)."""

    __slots__ = ("line", "kind", "md_marker", "text", "ref", "ref_idx",
                 "new_indent", "new_marker", "attach")

    def __init__(self, line: str):
        self.line = line
        self.kind = "other"
        self.md_marker = ""       # "-" / "N."
        self.text = ""
        self.ref: _RefLine | None = None
        self.ref_idx: int | None = None
        self.new_indent: int | None = None
        self.new_marker: str | None = None
        self.attach = False       # class A: indent under previous item
        s = line.strip()
        if not s or s.startswith("<!--"):
            self.kind = "blank"   # comments/placeholders: neutral furniture
            return
        m = _LS_ITEM_RE.match(line)
        if m and not _LS_SKIP_RE.match(line):
            self.kind = "item"
            self.md_marker = m.group(2)
            self.text = m.group(3)
        elif not _LS_SKIP_RE.match(line):
            self.kind = "para"
            self.text = s

    def cur_level(self) -> int:
        ind = len(self.line) - len(self.line.lstrip())
        return ind // LIST_INDENT


def _ls_body_col(ref_lines: list[_RefLine]) -> int:
    """Smallest printed indent that >= 2 marker-less >= 5-token lines
    share: the page's plain-paragraph column. The >= 2 requirement keeps
    one-off furniture (running footers) from defining the column; the
    < 60 cap drops far-right decoration. Falls back to 0."""
    counts = Counter(
        rl.indent for rl in ref_lines
        if rl.marker is None and len(rl.full_toks) >= 5 and rl.indent < 60
    )
    cands = [ind for ind, n in counts.items() if n >= 2]
    return min(cands) if cands else 0


def _ls_family(marker: str) -> str:
    """Marker family for chain/level assignment. Dash is DISTINCT from
    bullet: the field document nests dash items under bullet items."""
    m = _ls_norm_marker(marker)
    if marker in "•·":
        return "bullet"
    if marker in "—-":
        return "dash"
    if m.isdigit():
        return "number"
    if _LS_ROMAN_CHARS_RE.fullmatch(m):
        return "roman"
    return "letter"   # single letters incl. ambiguous i/v/x/c


def _ls_ordinal(marker: str, fam: str) -> int | None:
    """Ordinal of a marker within its family (None: bullets/dashes have
    no ordinal — one chain per family)."""
    m = _ls_norm_marker(marker)
    if fam == "number":
        return int(m)
    if fam == "letter":
        return ord(m) - ord("a") + 1 if len(m) == 1 else None
    if fam == "roman":
        vals = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100,
                "d": 500, "m": 1000}
        tot = 0
        for ch, nxt in zip(m, m[1:] + " "):
            v = vals.get(ch, 0)
            tot += -v if vals.get(nxt, 0) > v else v
        return tot or None
    return None


def _ls_repair_page(md: str, ref_text: str,
                    carry: dict | None = None) -> tuple[str, int, dict]:
    """Repair one page. Returns (new_md, changes, carry_out).

    carry: {"text_col": int, "md_indent": int} describing the trailing
    list item of the PREVIOUS page (for page-initial class-A attachment
    of a paragraph that continues an item across the page break), or None.
    """
    ref_lines = _ls_parse_ref(ref_text)
    body_col = _ls_body_col(ref_lines)
    blocks = [_ListBlock(l) for l in md.split("\n")]
    changes = 0
    cursor = 0

    # -- pass 1: match items and paragraphs to reference lines -----------
    for b in blocks:
        if b.kind not in ("item", "para"):
            continue
        i = _ls_match_ref(_ls_toks(b.text), ref_lines, cursor)
        if i is not None:
            b.ref = ref_lines[i]
            b.ref_idx = i
            cursor = i + 1

    # -- pass 2: class B — invented numeric marker over printed marker ---
    for b in blocks:
        if b.kind != "item" or b.ref is None:
            continue
        pm = _LS_PRINTED_RE.match(b.text)
        if not pm:
            continue
        if not b.ref.marker_confirmed(pm.group(1)):
            continue   # incl. the class-D bare-number gap guard
        if re.fullmatch(r"\d{1,3}\.", b.md_marker):
            # docling invented a positional number on top of the printed
            # marker -> drop it, keep "- <printed>".
            b.new_marker = "-"
            changes += 1

    # -- pass 3: runs, re-leveling (C) and orphan attachment (A) ---------
    prev_item: _ListBlock | None = None
    prev_carry = carry
    run: list[_ListBlock] = []
    runs: list[list[_ListBlock]] = []
    for b in blocks:
        if b.kind == "item":
            run.append(b)
            prev_item = b
            prev_carry = None
        elif b.kind == "blank":
            continue
        elif b.kind == "para" and b.ref is not None:
            # Class A candidate: printed indent deep enough to prove the
            # paragraph sits INSIDE the preceding item (anchor = the
            # item's printed text column; page-initial paragraphs anchor
            # on the carried previous-page item), AND clearly deeper than
            # the page's own plain-paragraph column.
            anchor_col = None
            if prev_item is not None and prev_item.ref is not None:
                anchor_col = (prev_item.ref.indent
                              + len(prev_item.ref.marker or "")
                              + prev_item.ref.gap)
            elif prev_item is None and prev_carry:
                anchor_col = prev_carry["text_col"]
            deep_enough = (
                anchor_col is not None
                and b.ref.marker is None
                and b.ref.indent >= anchor_col - 2
                and b.ref.indent >= body_col + 4
            )
            # Deep-wrapped-lines check: the wrapped remainder of the
            # printed paragraph must sit in the list zone too. Rejects
            # margin-icon paragraphs, whose FIRST line alone is pushed
            # right by the icon while the rest returns to the body column.
            if deep_enough and b.ref_idx is not None:
                j = b.ref_idx + 1
                while j < len(ref_lines) and ref_lines[j].marker is None:
                    if ref_lines[j].indent < (anchor_col or 0) - 2:
                        deep_enough = False
                        break
                    j += 1
                    if j - b.ref_idx > 8:
                        break
            if deep_enough:
                b.attach = True
                continue   # attached: does not break the run
            if run:
                runs.append(run)
                run = []
            prev_item = None
        else:
            if run:
                runs.append(run)
                run = []
            prev_item = None
            prev_carry = None
    if run:
        runs.append(run)

    for r in runs:
        # Marker-required run matching: leveling evidence must come from
        # PRINTED MARKER columns. A marker-less matched ref line is a
        # wrapped continuation (its indent is a text column, not a marker
        # column) and yields false levels (two-column TOCs, page-initial
        # item tails). Every item in the run must match a marker-bearing
        # reference line — partially-evidenced runs are left alone.
        matched = [b for b in r
                   if b.ref is not None and b.ref.marker is not None]
        if len(matched) != len(r) or len(r) < 2:
            continue
        # Same-family column merge: right-aligned/jittered markers of one
        # printed list ("i)" at 18, "ii)" at 14) must not read as two
        # levels; each marker family collapses to its chain median first.
        fams = {id(b): _ls_family(b.ref.marker) for b in r}
        # Ambiguous single letters that are also roman chars ("i)", "v)",
        # "c)", "x)"): alphabet adjacency wins (a "c)" preceded by a "b)"
        # letter item is a letter); otherwise the presence of unambiguous
        # multi-char roman markers ("ii)", "iii)") makes it roman.
        letter_seen: set[str] = set()
        has_multiroman = any(f == "roman" for f in fams.values())
        for b in r:
            m = _ls_norm_marker(b.ref.marker)
            if fams[id(b)] == "letter":
                if re.fullmatch(r"[ivxlcdm]", m) and has_multiroman \
                        and chr(ord(m) - 1) not in letter_seen:
                    fams[id(b)] = "roman"
                else:
                    letter_seen.add(m)
        # Ordinal-chain column merging: consecutive-ordinal same-family
        # items ("i)","ii)" / "5","6","7" / "a)","b)") are one printed
        # list — their jittered marker columns collapse to the chain
        # median. An ordinal BREAK ("…7" then "2": outer list resumes)
        # starts a new chain, keeping genuinely re-used families at
        # different levels separable.
        chain_of: dict[int, tuple] = {}
        last: dict[str, tuple[int | None, int]] = {}   # fam -> (ord, chain)
        counter = 0
        for b in r:
            fam = fams[id(b)]
            o = _ls_ordinal(b.ref.marker, fam)
            prev = last.get(fam)
            if prev is not None and (o is None or prev[0] is None
                                     or o == prev[0] + 1):
                chain = prev[1]
            else:
                counter += 1
                chain = counter
            chain_of[id(b)] = (fam, chain)
            last[fam] = (o, chain)
        chain_cols: dict[tuple, list[int]] = {}
        for b in r:
            chain_cols.setdefault(chain_of[id(b)], []).append(b.ref.indent)
        chain_col = {c: sorted(cs)[len(cs) // 2]
                     for c, cs in chain_cols.items()}
        eff_col = {id(b): chain_col[chain_of[id(b)]] for b in r}
        cols = sorted(set(eff_col.values()))
        clusters: list[list[int]] = []
        for c in cols:
            if clusters and c - clusters[-1][-1] <= LIST_CLUSTER_TOL:
                clusters[-1].append(c)
            else:
                clusters.append([c])
        if len(clusters) < 2:
            continue   # one printed column: nothing to re-level
        centers = [sum(cl) / len(cl) for cl in clusters]
        if any(hi - lo < LIST_LEVEL_MIN_SEP
               for lo, hi in zip(centers, centers[1:])):
            continue   # columns too close to be distinct levels
        col_level = {c: li for li, cl in enumerate(clusters) for c in cl}
        for b in r:
            lvl = col_level[eff_col[id(b)]]
            if lvl != b.cur_level():
                b.new_indent = lvl * LIST_INDENT
                changes += 1

    # -- pass 4: emit ----------------------------------------------------
    out: list[str] = []
    last_item_indent = (carry or {}).get("md_indent", 0)
    carry_out: dict = {}
    for b in blocks:
        if b.kind == "item":
            ind = b.new_indent if b.new_indent is not None \
                else len(b.line) - len(b.line.lstrip())
            marker = b.new_marker or b.md_marker
            if b.new_indent is None and b.new_marker is None:
                out.append(b.line)
            else:
                out.append(" " * ind + marker + " " + b.text)
            last_item_indent = ind
            if b.ref is not None:
                carry_out = {
                    "text_col": (b.ref.indent + len(b.ref.marker or "")
                                 + b.ref.gap),
                    "md_indent": ind,
                }
        elif b.kind == "para" and b.attach:
            new = " " * (last_item_indent + LIST_INDENT) + b.text
            if new != b.line:
                changes += 1
            out.append(new)
        else:
            out.append(b.line)
            # carry_out deliberately survives trailing paragraphs
            # (footnote zones sit below the list in print order); the
            # next page's indent gate is the real guard against false
            # attachment.
    return "\n".join(out), changes, carry_out


def fix_list_structure(
    pages: dict[int, str], reference: dict[int, str]
) -> tuple[dict[int, str], dict[int, int]]:
    """Repair list structure (classes A/B/C) against the poppler reference.

    pages / reference: 1-indexed page -> markdown / poppler -layout text
    (contract). Pages without a usable reference, or without any markdown
    list item at all, are returned untouched (and reset the cross-page
    carry — an attachment must never jump a skipped page). Returns
    (pages, {page: changes}); counts hold only pages with >= 1 change,
    and untouched pages keep their original string identity.
    """
    out: dict[int, str] = {}
    counts: dict[int, int] = {}
    carry: dict = {}
    for p in sorted(pages):
        ref = reference.get(p, "")
        has_item = ("- " in pages[p]
                    or re.search(r"^\d{1,3}\. ", pages[p], re.M))
        if not ref.strip() or not has_item:
            out[p] = pages[p]
            carry = {}
            continue
        new, n, carry = _ls_repair_page(pages[p], ref, carry or None)
        out[p] = new if n else pages[p]   # same object: provably untouched
        if n:
            counts[p] = n
    return out, counts


# ---------------------------------------------------------------- self-test

# Fixtures below are VERBATIM excerpts from the real defect document:
# ~/tmp/ccbpb/CCBPBAbril2026.md (docling markdown) and the matching
# `pdftotext -layout` pages. Only table-cell padding was trimmed.

_MD_103 = """\
Cuadro 1

Lista de terminología estandarizada europea

| Término | Definición |
|---|---|
| cuenta la de Mantenimiento | ella. con operar pueda cliente el que para cuenta la gestiona entidad La |
| débito de tarjeta una de mantenimiento y Emisión | cliente. del cuenta la a asociada pago de tarjeta una facilita entidad La El importe de cada una de las operaciones realizadas con la tarjeta se carga directamente y en su totalidad a la cuenta del cliente. |
| expreso Descubierto | pueda último este que anticipado por acuerdan cliente el y entidad La disponer de fondos cuando no quede saldo disponible en su cuenta. En el acuerdo se determina la cantidad máxima de la que puede disponerse y si el cliente deberá abonar comisiones e intereses. |
| Transferencia | la desde fondos transfiere entidad la cliente, del instrucciones Siguiendo cuenta del cliente a otra cuenta. |
| permanente Orden | periódicamente realiza entidad la cliente, del instrucciones Siguiendo transferencias de un importe determinado desde la cuenta del cliente a otra cuenta. |
| bancaria Domiciliación | que entidad la a ordenar (beneficiario) tercero un a permite cliente El transfiera fondos desde la cuenta del cliente a la del beneficiario. La entidad transfiere los fondos al beneficiario en la fecha o fechas acordadas entre el cliente y el beneficiario. El importe de dichos adeudos puede variar. |
"""

_REF_103 = """\
Cuadro 1
Lista de terminología estandarizada europea

Término                                                Definición
Mantenimiento de la cuenta                             La entidad gestiona la cuenta para que el cliente pueda operar con ella.
Emisión y mantenimiento de una tarjeta de débito       La entidad facilita una tarjeta de pago asociada a la cuenta del cliente.
                                                       El importe de cada una de las operaciones realizadas con la tarjeta se
                                                       carga directamente y en su totalidad a la cuenta del cliente.
D escubierto expreso                                   La entidad y el cliente acuerdan por anticipado que este último pueda
                                                       disponer de fondos cuando no quede saldo disponible en su cuenta.
                                                       En el acuerdo se determina la cantidad máxima de la que puede
                                                       disponerse y si el cliente deberá abonar comisiones e intereses.
Transferencia                                          S iguiendo instrucciones del cliente, la entidad transfiere fondos desde la
                                                       cuenta del cliente a otra cuenta.
O rden permanente                                      S iguiendo instrucciones del cliente, la entidad realiza periódicamente
                                                       transferencias de un importe determinado desde la cuenta del cliente
                                                       a otra cuenta.
D omiciliación bancaria                                El cliente permite a un tercero (beneficiario) ordenar a la entidad que
                                                       transfiera fondos desde la cuenta del cliente a la del beneficiario.
                                                       La entidad transfiere los fondos al beneficiario en la fecha o fechas
                                                       acordadas entre el cliente y el beneficiario. El importe de dichos
                                                       adeudos puede variar.
R etirada de efectivo                                  El cliente retira efectivo de su cuenta.

FUENTE: Reglamento Delegado 2018/32.
"""

# Healthy prose from the same real document (md around the Capítulo 1
# introduction) and its real reference page — must come back untouched.
_MD_HEALTHY = """\
## Introducción

Entre  los  servicios  bancarios  que  prestan  las  entidades  de  crédito  se  encuentra,  como  uno de los más característicos, la captación de fondos de los clientes, esencialmente en forma de depósitos reembolsables.

La cuenta bancaria es, habitualmente, el primer producto que el cliente bancario contrata con su entidad y, además, sirve de base y soporte operativo para la gran mayoría de los productos bancarios.

| Retirada de efectivo a débito mediante tarjeta en cajeros automáticos | El cliente retira efectivo de su cuenta a través de un cajero automático de otra entidad, mediante tarjeta, a cuenta del saldo disponible. |
"""

_REF_HEALTHY = """\
Introducción

Entre los servicios bancarios que prestan las entidades de crédito se encuentra, como uno
de los más característicos, la captación de fondos de los clientes, esencialmente en forma de
depósitos reembolsables.

La cuenta bancaria es, habitualmente, el primer producto que el cliente bancario contrata con
su entidad y, además, sirve de base y soporte operativo para la gran mayoría de los productos
bancarios.

Retirada de efectivo a débito mediante tarjeta                   El cliente retira efectivo de su cuenta a través de un cajero automático de
en cajeros automáticos                                           otra entidad, mediante tarjeta, a cuenta del saldo disponible.
"""

# Verbatim real debris block (Capítulo 1 divider photo page, tesseract OCR).
_MD_DEBRIS = """\
## Capítulo 1

Cuentas bancarias. Introducción y aspectos generales

Compendio de criterios de buenas prácticas bancarias. 2.ª actualización, abril 2026

ie

&gt;

o

S

.

.

O a a

o

S

dl

.

<!-- image -->

## Introducción

Entre  los  servicios  bancarios  que  prestan  las  entidades  de  crédito  se  encuentra,  como  uno de los más característicos, la captación de fondos de los clientes.
"""

# Real icon-glyph shapes from the field document (lines 593/629-631/659/
# 700/742/1536 and the "TS"/"«1"/"## I" keep-cases), abridged.
_MD_ICONS = """\
(Y La aplicación de estos principios de claridad y transparencia en relación con las peticiones de información presenta ciertas singularidades.

- (Y Cuando son varias las cuestiones controvertidas planteadas por el reclamante, se considera mala práctica bancaria.

Y Así,  a  efectos de acreditar la realización de estas comunicaciones a través del sitio web de la entidad, se requiere que esta aporte la prueba.

¡Y

(Y

¡0

CÓ S

CF

## ¡0 Se ha considerado, en los informes emitidos, que las entidades incurren en una mala práctica bancaria cuando:

¡US Asimismo, no se encuentra ajustado a las buenas prácticas bancarias el cobro de la comisión.

LJ Finalmente, si un cliente pide asistencia a su entidad para localizar un ingreso, debe ser atendido.
"""

_MD_ICONS_KEEP = """\
## I Criterios de buenas prácticas aplicables en el momento de la contratación

## II Criterios aplicables a medidas restrictivas aplicadas al cliente

## A) Denegar su aplicación, de forma inmediata y motivada

TS Aquellas en las que se excluyen los beneficios del fiador antes explicados: excusión, división y orden.

«1 Los sujetos obligados identificarán a cuantas personas físicas o jurídicas pretendan establecer relaciones de negocio.

EE. UU. La regulación aplicable difiere de la europea en varios aspectos.

- 1 Resulta imprescindible su previsión y concreción en la información precontractual.

- STS n.º 705, de 15 de diciembre de 2015, que declaró la nulidad de la cláusula.

Y ello en razón de que, para la baja del cotitular fallecido, no se precisa sino acreditar la aceptación.

DNI caducado: la entidad debe desplegar una especial diligencia.

19

416

HORSES
"""

# Real duplicate-orphan lines from the field document (lines 637, 1837,
# 1845, 1847, 1903, 8094), abridged where marked (…).
_ORPHAN_P22 = (
    "5  de diciembre de 1998 y 10 mayo de 1999. 1 Como las de la Audiencia "
    "Provincial de Córdoba (Sección 2.ª) de 23 de octubre de 1991, 3 de "
    "diciembre de 1994, 26 de junio de 1995, 15 de diciembre de 1998 y 10 "
    "mayo de 1999."
)
_ORPHAN_P85_A = (
    "entes extremos: 4 El artículo 7.3 de la orden señala lo siguiente: "
    "«Los documentos contractuales relativos a servicios bancarios deberán "
    "recoger de forma explícita y clara los siguientes extremos:"
)
_ORPHAN_P85_B = (
    "nás que establezca el  Banco de España». Los demás que establezca el "
    "Banco de España»."
)
_ORPHAN_P85_C = (
    "licación e interpretación». 5 La  norma  décima,  apartado  3,  indica "
    "lo siguiente: «[…] No se incluirá en el contrato ningún concepto que "
    "resulte innecesario o irrelevante para su correcta aplicación e "
    "interpretación»."
)
_ORPHAN_TS = (
    "TS de 16 de diciembre de 2014. 17 Véase la STS de 16 de diciembre "
    "de 2014."
)
# Standalone-orphan shape: fragment line emitted before the full line.
_ORPHAN_STANDALONE = (
    "5 de diciembre de 1998 y 10 mayo de 1999.\n\n"
    "1 Como las de la Audiencia Provincial de Córdoba de 23 de octubre de "
    "1991, 15 de diciembre de 1998 y 10 mayo de 1999.\n"
)
# Legitimate quotation: the leading sentence reappears inside the footnote
# quote — but at a « boundary, not word-glued, and it starts uppercase.
_ORPHAN_LEGIT_QUOTE = (
    "Los demás que establezca el Banco de España». 5 La norma indica lo "
    "siguiente: «Los demás que establezca el Banco de España»."
)

# Verbatim page 86 of the field document (docling markdown): a class-B
# invented marker ("2. ii) Para exigir…" on top of the printed "ii)")
# and a class-C run (bullet items printed one level under the bare-
# number "3" marker column, flattened to a single level by docling).
_LIST_MD_86 = """\
<!-- image -->

distintos en función del canal empleado y, como tales, deberán especificarse en la cláusula que los contemple.

- El canal empleado para efectuar las comunicaciones, que deberá ser el adecuado, de forma que guarde proporcionalidad con el saldo reclamado. Podrán establecerse umbrales  de  deuda  mínimos  exentos  de  su  aplicación,  de  los  que  deberá  ser informado el cliente.
- La incompatibilidad de este gasto con otras penalizaciones, en la medida en que se enmarca en la reconducción del pago de la deuda, por lo que no se podrán establecer otras penalizaciones en el contrato por el mismo concepto.
- 3 Finalmente, de producirse el efectivo devengo de los citados gastos:
- Se deberá informar de estos con carácter previo a su cargo, preferiblemente en la propia comunicación, en la que se indicarán el saldo deudor impagado, el importe que se va a adeudar conforme a lo pactado y el plazo disponible para regularizar la situación.
- No cabrá el cobro de más de una comunicación para un mismo saldo impagado, ni siquiera cuando este se prolongue en sucesivas liquidaciones.
- Las  comunicaciones  deberán  ser  respetuosas  con  la  privacidad  del  cliente, proporcionales a los deberes de información y no resultar excesivas.

Además de lo anterior, y a la vista del resultado de las actuaciones supervisoras llevadas a cabo por el Banco de España, se considera necesario añadir algunas precisiones destinadas a una mejor definición de estos criterios de buenas prácticas y motivadas por el hecho de que la generación  de  estos  gastos  suele  guardar  relación  con  situaciones  de  descubierto  o  de «números rojos» en cuenta corriente.

Así, para que la actuación de una entidad financiera se considere ajustada a las buenas prácticas bancarias, cuando se pueda producir o se produzca efectivamente un descubierto tácito en cuenta:

- i) Se deberá dar la posibilidad a los clientes de desautorizar a la entidad el cargo de recibos en descubierto, lo que puede tener lugar tanto en la contratación de la cuenta como  posteriormente,  con  ocasión  del  envío  al  cliente  de  cualquiera  de  las comunicaciones periódicas que prevé la normativa.
2. ii) Para exigir el reembolso del saldo deudor deberá realizarse la liquidación previa del descubierto  tácito,  siendo  este,  por  tanto,  el  momento  a  partir  del  cual  podrán repercutirse los correspondientes GRPD, siempre y cuando: a) se mantenga el saldo deudor  pendiente  de  regularizar,  y  b)  se  hayan  realizado  las  comunicaciones oportunas, en los términos exigidos por las buenas prácticas señaladas.
"""

# Matching verbatim `pdftotext -layout` page 86.
_LIST_REF_86 = """\
                                                                                                              CAPÍTULO 2




                          distintos en función del canal empleado y, como tales, deberán especificarse en la
                          cláusula que los contemple.

                        • El canal empleado para efectuar las comunicaciones, que deberá ser el adecuado,
                          de forma que guarde proporcionalidad con el saldo reclamado. Podrán establecerse
                          umbrales de deuda mínimos exentos de su aplicación, de los que deberá ser
                          informado el cliente.

                        • La incompatibilidad de este gasto con otras penalizaciones, en la medida en que se
                          enmarca en la reconducción del pago de la deuda, por lo que no se podrán establecer
                          otras penalizaciones en el contrato por el mismo concepto.

              3         Finalmente, de producirse el efectivo devengo de los citados gastos:

                        • Se deberá informar de estos con carácter previo a su cargo, preferiblemente en la
                          propia comunicación, en la que se indicarán el saldo deudor impagado, el importe
                          que se va a adeudar conforme a lo pactado y el plazo disponible para regularizar la
                          situación.

                        • No cabrá el cobro de más de una comunicación para un mismo saldo impagado, ni
                          siquiera cuando este se prolongue en sucesivas liquidaciones.

                        • Las comunicaciones deberán ser respetuosas con la privacidad del cliente,
                          proporcionales a los deberes de información y no resultar excesivas.

        Además de lo anterior, y a la vista del resultado de las actuaciones supervisoras llevadas a cabo
        por el Banco de España, se considera necesario añadir algunas precisiones destinadas a una
        mejor definición de estos criterios de buenas prácticas y motivadas por el hecho de que la
        generación de estos gastos suele guardar relación con situaciones de descubierto o de
        «números rojos» en cuenta corriente.

        Así, para que la actuación de una entidad financiera se considere ajustada a las buenas prácticas
        bancarias, cuando se pueda producir o se produzca efectivamente un descubierto tácito en
        cuenta:

                  i)    Se deberá dar la posibilidad a los clientes de desautorizar a la entidad el cargo de
                        recibos en descubierto, lo que puede tener lugar tanto en la contratación de la cuenta
                        como posteriormente, con ocasión del envío al cliente de cualquiera de las
                        comunicaciones periódicas que prevé la normativa.

                  ii)   Para exigir el reembolso del saldo deudor deberá realizarse la liquidación previa del
                        descubierto tácito, siendo este, por tanto, el momento a partir del cual podrán
                        repercutirse los correspondientes GRPD, siempre y cuando: a) se mantenga el saldo
                        deudor pendiente de regularizar, y b) se hayan realizado las comunicaciones
                        oportunas, en los términos exigidos por las buenas prácticas señaladas.


Banco de España         Compendio de criterios de buenas prácticas bancarias. 2.ª actualización, abril 2026           86
"""

# Page 87 head (trimmed before the footnote zone, which the orphan
# fixtures cover): three class-A continuation paragraphs flattened out
# of their items, incl. the page-initial one whose anchor is the
# cross-page carry from page 86's trailing item.
_LIST_MD_87 = """\
<!-- image -->

En el caso de girarse estos GRPD de forma previa a la liquidación del descubierto, se entenderá  que  dichos  gastos  forman  parte  de  los  conceptos  repercutidos  por  la propia prestación del servicio de descubierto y, por tanto, del coste del crédito, a efectos de lo establecido en la norma 13.ª, apartado 7.e), de la Circular 5/2012 6 .

- iii) En el momento de la apertura del descubierto, y con independencia de las previsiones del artículo\xa020.3 de la Ley 16/2011 7 ,  deberá informarse al cliente, de forma clara y concisa, del período de que dispone para regularizar el saldo deudor antes de su liquidación y, en su caso, de la posible repercusión de los GRPD en el supuesto de que no sea satisfecho dicho saldo deudor.

Por otra parte, se estima conveniente advertir a la clientela de las consecuencias del eventual adeudo en descubierto de comisiones o gastos por la prestación de servicios singulares solicitados por su parte.

- iv) Una vez que se realice la liquidación del descubierto, el cliente debe disponer de un plazo mínimo para satisfacer el saldo deudor antes de que se proceda, en su caso, a la  realización de gestiones para su reclamación y antes del eventual cargo de los GRPD.

Los  saldos  en  descubierto  liquidados  y  reclamados  que  no  sean  satisfechos  no podrán  ser  objeto  de  una  nueva  liquidación  por  la  concesión  de  un  servicio  de descubierto que ya fue prestado, no pudiendo girarse, por tanto, nuevas comisiones por descubierto ni GRPD adicionales por estos mismos saldos.
"""

# Matching `pdftotext -layout` page 87 head (the \t\t indent runs are
# verbatim poppler output; _RefLine expands them at tab stop 8).
_LIST_REF_87 = """\
                                                                                                                                CAPÍTULO 2




                  \t\t En el caso de girarse estos GRPD de forma previa a la liquidación del descubierto, se
                         entenderá que dichos gastos forman parte de los conceptos repercutidos por la
                         propia prestación del servicio de descubierto y, por tanto, del coste del crédito, a
                         efectos de lo establecido en la norma 13.ª, apartado 7.e), de la Circular 5/20126.


                  iii) En el momento de la apertura del descubierto, y con independencia de las previsiones
                         del artículo 20.3 de la Ley 16/20117, deberá informarse al cliente, de forma clara y
                         concisa, del período de que dispone para regularizar el saldo deudor antes de su
                         liquidación y, en su caso, de la posible repercusión de los GRPD en el supuesto de que
                         no sea satisfecho dicho saldo deudor.


                  \t\t Por otra parte, se estima conveniente advertir a la clientela de las consecuencias del
                         eventual adeudo en descubierto de comisiones o gastos por la prestación de servicios
                         singulares solicitados por su parte.


                  iv) Una vez que se realice la liquidación del descubierto, el cliente debe disponer de un
                         plazo mínimo para satisfacer el saldo deudor antes de que se proceda, en su caso, a
                         la realización de gestiones para su reclamación y antes del eventual cargo de los
                         GRPD.


                  \t\t Los saldos en descubierto liquidados y reclamados que no sean satisfechos no
                         podrán ser objeto de una nueva liquidación por la concesión de un servicio de
                         descubierto que ya fue prestado, no pudiendo girarse, por tanto, nuevas comisiones
                         por descubierto ni GRPD adicionales por estos mismos saldos.
"""

if __name__ == "__main__":
    failures = 0

    def check(name: str, cond: bool, detail: str = "") -> None:
        global failures
        status = "ok  " if cond else "FAIL"
        if not cond:
            failures += 1
        print(f"  {status} {name}" + (f"  ({detail})" if detail else ""))

    # ---------------------------------------------------------- sanitize
    print("sanitize")
    # Real pattern: BEL emitted for a symbol-font bullet inside a TOC line.
    s = sanitize("- 2 \x07 Obligaciones de identificación 42")
    check("BEL between words -> single space",
          s == "- 2 Obligaciones de identificación 42", repr(s))
    s = sanitize("\x07 Obligaciones")
    check("line-leading BEL stripped with padding", s == "Obligaciones",
          repr(s))
    s = sanitize("texto\x00\x08fin\x7f y  pua")
    check("C0/DEL/PUA removed", s == "textofin y pua", repr(s))
    s = sanitize("col1\tcol2\nline2")
    check("\\t and \\n preserved", s == "col1\tcol2\nline2", repr(s))
    s = sanitize("a﻿b c�d")
    check("BOM and U+FFFD removed", s == "ab cd", repr(s))
    healthy = "Entre  los  servicios — «texto» ✓ años 2018/32.\n\n| a | b |"
    check("healthy text untouched (incl. doubled spaces)",
          sanitize(healthy) == healthy)
    once = sanitize("x \x07 y \x07")
    check("idempotent", sanitize(once) == once, repr(once))

    # -------------------------------------------------- fix_word_reversal
    print("fix_word_reversal (real Cuadro 1 rows, page 103)")
    fixed, counts = fix_word_reversal({103: _MD_103}, {103: _REF_103})
    out = fixed[103]
    check("counts reported", counts.get(103, 0) >= 5, f"counts={counts}")
    check("'Mantenimiento de la cuenta' restored",
          "| Mantenimiento de la cuenta" in out)
    check("full first-line cell restored",
          "La entidad gestiona la cuenta para que el cliente pueda operar "
          "con ella." in out)
    check("reversed form gone", "cuenta la de Mantenimiento" not in out
          and "ella. con operar pueda" not in out)
    check("mixed cell: prefix repaired at the right boundary",
          "La entidad facilita una tarjeta de pago asociada a la cuenta del "
          "cliente. El importe de cada una de las operaciones" in out)
    # 2-word term cells are below the 3-word floor: stay broken BY DESIGN
    # (documented limit — no evidence base for a 2-word swap).
    check("2-word term cell left alone (documented limit)",
          "| expreso Descubierto" in out)
    check("mixed cell: Descubierto definition prefix restored",
          "La entidad y el cliente acuerdan por anticipado que este último "
          "pueda disponer de fondos" in out)
    check("Transferencia: reversed span includes trailing 'la'",
          "Siguiendo instrucciones del cliente, la entidad transfiere "
          "fondos desde la cuenta del cliente a otra cuenta." in out)
    check("Domiciliación prefix restored",
          "El cliente permite a un tercero (beneficiario) ordenar a la "
          "entidad que transfiera fondos desde la cuenta del cliente" in out)
    check("healthy tail untouched",
          "El importe de dichos adeudos puede variar." in out)

    print("fix_word_reversal (healthy real prose + healthy table row)")
    fixed, counts = fix_word_reversal({30: _MD_HEALTHY}, {30: _REF_HEALTHY})
    check("zero changes on healthy page", counts == {}, f"counts={counts}")
    check("healthy page returned as the same object",
          fixed[30] is _MD_HEALTHY)

    print("fix_word_reversal (edge cases)")
    fixed, counts = fix_word_reversal({1: _MD_103}, {1: ""})
    check("empty reference -> untouched", counts == {} and fixed[1] is _MD_103)
    fixed, counts = fix_word_reversal({1: _MD_103}, {})
    check("missing reference -> untouched", counts == {} and fixed[1] is _MD_103)
    md = "cuenta la de Mantenimiento y algo más aquí"
    ref = "Texto sin ninguna relación con ese contenido en absoluto."
    fixed, counts = fix_word_reversal({1: md}, {1: ref})
    check("no reference support either way -> untouched", counts == {},
          f"counts={counts}")

    # --------------------------------------------------- clean_ocr_debris
    print("clean_ocr_debris (real Capítulo 1 debris block)")
    cleaned, n = clean_ocr_debris(_MD_DEBRIS)
    check("all 11 debris lines removed", n == 11, f"removed={n}")
    for frag in ("\nie\n", "\n&gt;\n", "\no\n", "\nS\n", "\n.\n",
                 "\nO a a\n", "\ndl\n"):
        if frag in cleaned:
            check(f"debris {frag.strip()!r} gone", False, "still present")
            break
    else:
        check("no debris fragments remain", True)
    check("headings kept", "## Capítulo 1" in cleaned
          and "## Introducción" in cleaned)
    check("real prose kept",
          "Cuentas bancarias. Introducción y aspectos generales" in cleaned
          and "Entre  los  servicios  bancarios" in cleaned)
    check("footer line kept",
          "Compendio de criterios de buenas prácticas bancarias. "
          "2.ª actualización, abril 2026" in cleaned)
    check("image placeholder kept", "<!-- image -->" in cleaned)
    check("no stacked blank lines left", "\n\n\n" not in cleaned)
    again, n2 = clean_ocr_debris(cleaned)
    check("idempotent", n2 == 0 and again == cleaned, f"second pass={n2}")

    print("clean_ocr_debris (healthy short lines: zero removals)")
    healthy_short = "\n".join([
        "# A",                        # 1-char heading (K3)
        "",
        "- o",                        # short list item (K5)
        "- 2  Obligaciones de identificación 42",  # TOC-like (K9/K10)
        "",
        "| a | b |",                  # tiny table row (K4)
        "|---|---|",
        "",
        "> ok",                       # blockquote (K6)
        "",
        "42",                         # bare page number (K9)
        "",
        "No",                         # standalone word, allowlist (K11)
        "",
        "[…]",                        # quoted-text omission marker (K12)
        "",
        "[...]",                      # ASCII variant (K12)
        "",
        "I am",                       # poem-like short line (R3 spares it)
        "",
        "So we beat on",              # poem line with a >=3-letter word (K10)
        "",
        "---",                        # horizontal rule (K8)
        "",
        "```",
        "x",                          # inside fence (K2)
        "```",
    ])
    cleaned, n = clean_ocr_debris(healthy_short)
    check("zero removals", n == 0, f"removed={n}")
    check("content byte-identical", cleaned == healthy_short)

    # ------------------------------------------------- strip_icon_glyphs
    print("strip_icon_glyphs (real field-document shapes)")
    cleaned, n = strip_icon_glyphs(_MD_ICONS)
    check("all 11 icon tokens stripped", n == 11, f"stripped={n}")
    check("paragraph '(Y ' prefix gone",
          cleaned.startswith("La aplicación de estos principios"))
    check("list-item '(Y ' prefix gone",
          "- Cuando son varias las cuestiones" in cleaned)
    check("bare 'Y ' before sentence-starter gone",
          "\nAsí,  a  efectos de acreditar" in cleaned)
    check("heading-fused '¡0' gone",
          "## Se ha considerado, en los informes emitidos" in cleaned)
    check("'¡US ' and 'LJ ' prefixes gone",
          "\nAsimismo, no se encuentra ajustado" in cleaned
          and "\nFinalmente, si un cliente pide asistencia" in cleaned)
    for frag in ("¡Y", "(Y", "¡0", "CÓ S", "CF"):
        if frag in cleaned:
            check(f"standalone {frag!r} gone", False, "still present")
            break
    else:
        check("all standalone icon lines gone", True)
    check("no stacked blank lines left", "\n\n\n" not in cleaned)
    again, n2 = strip_icon_glyphs(cleaned)
    check("idempotent", n2 == 0 and again == cleaned, f"second pass={n2}")

    print("strip_icon_glyphs (real keep-cases: zero changes)")
    kept, n = strip_icon_glyphs(_MD_ICONS_KEEP)
    check("zero strips on keep fixture", n == 0, f"stripped={n}")
    check("keep fixture byte-identical", kept == _MD_ICONS_KEEP)

    # -------------------------------------------- drop_duplicate_orphans
    print("drop_duplicate_orphans (real fused-fragment lines)")
    fixed, n = drop_duplicate_orphans(_ORPHAN_P22)
    check("p22 date fragment stripped", n == 1
          and fixed.startswith("1 Como las de la Audiencia"), repr(fixed[:40]))
    check("p22 footnote body intact",
          fixed.endswith("15 de diciembre de 1998 y 10 mayo de 1999."))
    fixed, n = drop_duplicate_orphans(_ORPHAN_P85_A)
    check("p85 'entes extremos:' stripped", n == 1
          and fixed.startswith("4 El artículo 7.3"), repr(fixed[:40]))
    fixed, n = drop_duplicate_orphans(_ORPHAN_P85_B)
    check("p85 'nás que establezca…' (1-char-truncated) stripped", n == 1
          and fixed == "Los demás que establezca el Banco de España».",
          repr(fixed))
    fixed, n = drop_duplicate_orphans(_ORPHAN_P85_C)
    check("p85 'licación e interpretación».' stripped", n == 1
          and fixed.startswith("5 La  norma  décima"), repr(fixed[:40]))
    fixed, n = drop_duplicate_orphans(_ORPHAN_TS)
    check("'TS de 16…' (uppercase, word-glued in STS) stripped", n == 1
          and fixed.startswith("17 Véase la STS"), repr(fixed))
    fixed, n = drop_duplicate_orphans(_ORPHAN_STANDALONE)
    check("standalone orphan line dropped", n == 1
          and fixed.lstrip("\n").startswith("1 Como las de la Audiencia"),
          repr(fixed[:40]))

    print("drop_duplicate_orphans (legitimate content: zero changes)")
    fixed, n = drop_duplicate_orphans(_ORPHAN_LEGIT_QUOTE)
    check("legit quoted sentence before footnote kept", n == 0
          and fixed == _ORPHAN_LEGIT_QUOTE, f"dropped={n}")
    fixed, n = drop_duplicate_orphans(_MD_HEALTHY)
    check("healthy real prose untouched", n == 0 and fixed == _MD_HEALTHY,
          f"dropped={n}")
    legit_short = "Transferencia\n\nSiguiendo instrucciones del cliente, la entidad transfiere fondos desde la cuenta a otra cuenta.\n"
    fixed, n = drop_duplicate_orphans(legit_short)
    check("short term line before its definition kept", n == 0,
          f"dropped={n}")
    legit_footnote = (
        "- 19 En este sentido, se considera contrario a las buenas "
        "prácticas que la entidad realice manifestaciones genéricas.")
    fixed, n = drop_duplicate_orphans(legit_footnote)
    check("list-item footnote unchanged (marker at column 0 is no boundary)",
          n == 0 and fixed == legit_footnote, f"dropped={n}")
    again, n2 = drop_duplicate_orphans(
        drop_duplicate_orphans(_ORPHAN_P85_B)[0])
    check("idempotent", n2 == 0, f"second pass={n2}")

    # ---------------- drop_duplicate_orphans (generalized branch (b)) ----
    # Verbatim OCR'd-footnote lines from field pages 87/330 — the fused
    # orphans the original gates missed (short glued fragments, printed
    # roman/letter/dash markers, re-wrapped multi-chunk orphans).
    print("drop_duplicate_orphans (generalized fused-prefix rules)")
    fixed, n = drop_duplicate_orphans(
        "ratorio de: Como excepción a lo anterior, cuando las comisiones "
        "giren sobre el mayor descubierto, la tasa anual equivalente se "
        "obtendrá como sumatorio de:")
    check("glued fragment of 11 chars stripped (floor 10)", n == 1
          and fixed.startswith("Como excepción"), repr(fixed[:40]))
    fixed, n = drop_duplicate_orphans(
        "al  equivalente eses se calculará teniendo en cuenta el saldo "
        "medio deudor del  período de liquidación; - la tasa anual "
        "equivalente de los intereses se calculará teniendo en cuenta el "
        "saldo medio deudor del período de liquidación;")
    check("multi-chunk orphan before mid-line dash stripped", n == 1
          and fixed.startswith("- la tasa anual equivalente"),
          repr(fixed[:44]))
    fixed, n = drop_duplicate_orphans(
        "al  equivalente ses devengados por el  descubierto durante el  "
        "período de liquidación de este, y i) la tasa anual equivalente "
        "de los intereses devengados por el descubierto durante el "
        "período de liquidación de este, y")
    check("multi-chunk orphan before roman 'i)' marker stripped", n == 1
          and fixed.startswith("i) la tasa anual equivalente"),
          repr(fixed[:40]))
    fixed, n = drop_duplicate_orphans(
        "- cubierto tácito. a) Del descubierto tácito.")
    check("list-item fused orphan stripped, md marker kept", n == 1
          and fixed == "- a) Del descubierto tácito.", repr(fixed))
    fixed, n = drop_duplicate_orphans(
        "- tipo deudor. c) Del tipo deudor.")
    check("lowercase 12-char containment inside a list item", n == 1
          and fixed == "- c) Del tipo deudor.", repr(fixed))

    print("drop_duplicate_orphans (generalized keep-negatives)")
    sts = "21 Véase la STS de 30 de abril de 1960."
    fixed, n = drop_duplicate_orphans(sts)
    check("'STS de 30 de abril' kept (bare number + lowercase is no "
          "boundary)", n == 0 and fixed == sts, f"dropped={n}")
    numstart = (
        "15 de diciembre de 1998 y 10 mayo de 1999 son las fechas de las "
        "sentencias de la Audiencia Provincial citadas en el texto.")
    fixed, n = drop_duplicate_orphans(numstart)
    check("sentence starting with a number kept", n == 0
          and fixed == numstart, f"dropped={n}")
    short_frag = (
        "de este. ii) la tasa anual equivalente de las comisiones que, a "
        "causa de la concesión del descubierto, se adeuden durante el "
        "período de liquidación de este.")
    fixed, n = drop_duplicate_orphans(short_frag)
    check("8-char fragment kept (below glued floor 10 — residue is "
          "verify.orphan_residue_suspect's job)", n == 0
          and fixed == short_frag, f"dropped={n}")
    fixed, n = drop_duplicate_orphans(_ORPHAN_LEGIT_QUOTE)
    check("legit quoted repetition still kept under generalized gates",
          n == 0 and fixed == _ORPHAN_LEGIT_QUOTE, f"dropped={n}")

    # ------------------------------------------------- fix_list_structure
    print("fix_list_structure (field pages 86-87: classes A, B, C)")
    lf_pages = {86: _LIST_MD_86, 87: _LIST_MD_87}
    lf_refs = {86: _LIST_REF_86, 87: _LIST_REF_87}
    lf_fixed, lf_counts = fix_list_structure(lf_pages, lf_refs)
    check("change counts match the field run", lf_counts == {86: 6, 87: 3},
          f"counts={lf_counts}")
    out86, out87 = lf_fixed[86], lf_fixed[87]
    check("class B: invented '2.' dropped, printed 'ii)' kept",
          "- ii) Para exigir el reembolso" in out86
          and "2. ii)" not in out86)
    check("class C: bullets re-nested under the printed '3' marker",
          "\n    - El canal empleado" in out86
          and "\n    - Las  comunicaciones" in out86
          and "\n- 3 Finalmente" in out86)
    check("class A: page-initial paragraph attached via cross-page carry",
          out87.startswith("<!-- image -->\n\n    En el caso de girarse"))
    check("class A: in-page continuation paragraphs attached",
          "\n    Por otra parte, se estima" in out87
          and "\n    Los  saldos  en  descubierto" in out87)
    check("items with printed roman markers keep their level",
          "\n- iii) En el momento" in out87 and "\n- iv) Una vez" in out87)
    again, again_counts = fix_list_structure(dict(lf_fixed), lf_refs)
    check("idempotent", again_counts == {}
          and again[86] is lf_fixed[86] and again[87] is lf_fixed[87],
          f"second pass={again_counts}")

    print("fix_list_structure (class-D guard and negatives)")
    # Bare number leading a sentence: printed with a single space, so it is
    # NOT a marker — the invented "1." must survive (class-D keep case).
    md_d = ("1. 15 de diciembre de 2015, fecha de la sentencia del "
            "Tribunal Supremo que se cita.")
    ref_d = ("     15 de diciembre de 2015, fecha de la sentencia del "
             "Tribunal Supremo que se cita.")
    lf_fixed, lf_counts = fix_list_structure({1: md_d}, {1: ref_d})
    check("bare number + 1-space gap: no marker, item untouched",
          lf_counts == {} and lf_fixed[1] is md_d, f"counts={lf_counts}")
    # Same number printed as a real marker (gap >= 2): class B fires.
    ref_d2 = ("     15   Véase la STS de 16 de diciembre de 2014 y sus "
              "concordantes posteriores.")
    md_d2 = ("3. 15 Véase la STS de 16 de diciembre de 2014 y sus "
             "concordantes posteriores.")
    lf_fixed, lf_counts = fix_list_structure({1: md_d2}, {1: ref_d2})
    check("bare number + 2-space gap: marker confirmed, class B fires",
          lf_counts == {1: 1} and lf_fixed[1].startswith("- 15 Véase"),
          f"counts={lf_counts} out={lf_fixed[1][:24]!r}")
    # Healthy list at one printed column with faithful reference: no
    # changes, same object.
    md_ok = (
        "- 1 Resulta imprescindible su previsión y concreción en la "
        "información precontractual.\n"
        "- 2 Además, en dicha información se deberá recoger lo "
        "siguiente:\n\n"
        "Texto normal del cuerpo en su columna habitual que continúa\n"
        "en una segunda línea impresa en la misma columna del cuerpo.\n")
    ref_ok = (
        "  1   Resulta imprescindible su previsión y concreción en la\n"
        "      información precontractual.\n"
        "  2   Además, en dicha información se deberá recoger lo "
        "siguiente:\n\n"
        "Texto normal del cuerpo en su columna habitual que continúa\n"
        "en una segunda línea impresa en la misma columna del cuerpo.\n")
    lf_fixed, lf_counts = fix_list_structure({1: md_ok}, {1: ref_ok})
    check("healthy single-column list untouched (same object)",
          lf_counts == {} and lf_fixed[1] is md_ok, f"counts={lf_counts}")
    # Table page (no markdown list items): the page gate skips it whole.
    lf_fixed, lf_counts = fix_list_structure({103: _MD_103}, {103: _REF_103})
    check("table page without list items skipped (same object)",
          lf_counts == {} and lf_fixed[103] is _MD_103,
          f"counts={lf_counts}")
    # Empty/missing reference: untouched.
    lf_fixed, lf_counts = fix_list_structure({86: _LIST_MD_86}, {86: ""})
    check("empty reference -> untouched", lf_counts == {}
          and lf_fixed[86] is _LIST_MD_86, f"counts={lf_counts}")

    if failures:
        print(f"\n{failures} self-test check(s) FAILED")
        sys.exit(1)
    print("\nall self-test checks passed")
