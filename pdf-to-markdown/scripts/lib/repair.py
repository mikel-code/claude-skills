"""Deterministic text repair (contract: repair.py signature block).

Three pure functions fixing defect classes CONFIRMED on a real document
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

Stdlib only; imports contract only (per module rules). Self-test:
`uv run repair.py` — fixtures are verbatim excerpts of the real document's
markdown and its `pdftotext -layout` reference.
"""
from __future__ import annotations

import html
import re
import sys
import unicodedata

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

    if failures:
        print(f"\n{failures} self-test check(s) FAILED")
        sys.exit(1)
    print("\nall self-test checks passed")
