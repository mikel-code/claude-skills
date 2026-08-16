# pdf-to-markdown — design

Status: **design agreed, not yet implemented.** Written 2026-08-16, based on a
four-track research pass (pdf-inspector internals, PyMuPDF family, poppler/OCR/
rasterization, broader 2025–2026 landscape). This file is the durable record of
what was decided and *why*, so future changes (e.g. adopting Granite-Docling
MLX) can be made against the original reasoning instead of re-researching it.

## Scope

Convert a PDF into clean GitHub-Flavored Markdown, **prioritizing correctness
over throughput**. Volume is low (individual documents, not batch pipelines);
platform is macOS/Apple Silicon; installs should stay light; a Claude vision
escalation tier is available from inside Claude Code.

**PDF is a separate skill from other document formats — deliberate split.**
Non-PDF office documents (docx, pptx, xlsx, odt/ods/odp, rtf, epub, csv) are a
solved, trivial problem via [anydoc](https://github.com/firecrawl/anydoc) and
belong in their own skill (planned: `doc-to-markdown`). PDF needs a multi-tier
pipeline with routing, OCR, and verification; keeping it separate means each
skill's context size matches its complexity, and the non-PDF skill can grow
its own transformation scripts later without dragging this pipeline along.

## Core design insight

The tools are interchangeable; **the router and the verifier are the
product**. Every significant failure mode found in research is *silent*:

- pdf-inspector has a documented silent-loss bug class — pages where text
  exists but content quietly disappears from the markdown while `confidence`
  stays 1.0 (`confidence` measures *classification*, not extraction
  completeness; see pdf-inspector issues #227, #251, #200, #342). One closed
  issue measured 4.8% of real-world pages getting a wrong `needs_ocr` verdict,
  almost always in the unsafe direction (skipping needed OCR).
- anydoc, when a page needs OCR, logs a warning and degrades — content is
  missing from output unless you watch logs.
- `ocrmypdf --skip-text` skips any page that has *any* text, so a scanned page
  with one real caption is silently not OCR'd.
- Vision models fail silently: they invent plausible values for fields they
  couldn't read, where classical OCR fails loudly with garbage.

Consequences baked into the architecture: never trust a single signal, never
lose content silently, and **every page must be accounted for** in a per-page
conversion report (extracted / OCR'd / vision-transcribed / flagged-unresolved).

## Architecture

```
Tier 0  triage        pdf-inspector detect (per-page: text/scanned/garbled)
Tier 1  extract       pdf-inspector pdf2md          ── default
        │             docling (OCR off)             ── routed: table-heavy/complex
Tier 2  ocr           ocrmypdf --skip-text / --redo-ocr / --force-ocr (tesseract)
                      → text-layer PDF → back to Tier 1 routing
Tier V  verify        pdftotext -layout cross-check (always, ~free)
Tier 3  vision        flagged pages → Claude Read   ── default, off with --no-vision
                      unflagged sample check        ── opt-in --verify
Output                markdown + per-page report (nothing silent)
```

Escalation is **per-page, not per-document**: a 40-page report with two scanned
appendix pages gets 38 pages of fast local extraction and 2 pages of OCR/
vision. Every tool in the chain supports page-level operation.

### Tier 0 — triage (pdf-inspector detector)

`pdf2md --json` / `detect-pdf --analyze --json` emit per-document and per-page
diagnostics in 10–50ms: `pdf_type` (TextBased/Scanned/ImageBased/Mixed),
`pages_needing_ocr` with machine-readable reasons (`scanned`, `no_text`,
`vector_text`, `suspected_garbled_text`), `has_encoding_issues`, `is_complex`,
`pages_with_tables`, `pages_with_columns`. No other surveyed tool exposes
routing signals this rich — it's the main reason pdf-inspector anchors the
pipeline despite its youth.

**Do not trust `needs_ocr` alone** (issue #227 history): the escalation
trigger is the union `pages_needing_ocr ∪ has_encoding_issues ∪
(pdf_type != TextBased)`.

### Tier 1 — extraction

**Default: pdf-inspector `pdf2md`.** MIT, pure Rust (`cargo install
pdf-inspector`), geometric layout analysis + tagged-PDF structure as a
supplementary signal. Headings via font-size tiers plus a
document-sequence classifier; dual-mode table detection (drawing-op rectangles
+ alignment heuristics), financial-table splitting, continuation tables,
multi-column reading order (projection-profile gutters ≥8pt + region-graph
DAG), header/footer stripping, hyphenation rejoin. Third-party-corpus scores
(Firecrawl-run, opendataloader-bench, 200 PDFs): overall 0.875, reading-order
0.915, tables TEDS 0.814, headings 0.788.

**Routed alternative: docling** (IBM, MIT, `pip install docling`), for
documents where tables are load-bearing. Routing is **document-level, not
per-page output mixing** — stitching pages from two converters produces
inconsistent markdown style. Route to docling when:

- Tier-0 diagnostics say table-heavy/complex (`is_complex`, high
  `pages_with_tables` ratio), or
- Tier-V flags text-recall problems concentrated on table pages → re-convert
  with docling before escalating to vision.

Docling runs **with its OCR disabled** — scanned pages go through Tier 2
first, so there is exactly one OCR path. Docling's TableFormer is
best-in-class on born-digital tables (TEDS ~0.89 vs pymupdf4llm's 0.40 in the
BSWEN benchmark); its weak public benchmark scores are on scan-heavy corpora,
which this pipeline never sends it. Cost: ~1–2 GB model weights on first
install, ~1s/page CPU-only (MPS support upstream is in-progress,
docling#3202) — acceptable at this volume.

### Tier 2 — OCR (ocrmypdf + tesseract)

`brew install ocrmypdf tesseract-lang`. Output is a PDF with a positioned
hidden text layer, which feeds **back into Tier 1 routing** — no separate
OCR-to-markdown path. Flag semantics matter and are easy to get wrong:

| Flag | Use when | Behavior |
|---|---|---|
| `--skip-text` | mixed *documents* (default) | OCRs only textless pages, copies text pages through unchanged |
| `--redo-ocr` | mixed *pages*, stale OCR layers | strips invisible prior OCR, masks real text, OCRs the rest, merges |
| `--force-ocr` | "selectable but garbage" text | rasterizes everything, discards existing text |

The `--force-ocr` case (broken ToUnicode/CID maps — text extracts as hex
garbage) is **exactly what Tier 0's `has_encoding_issues` /
`suspected_garbled_text` detect**; `--skip-text` would wrongly skip those
pages because they technically have text. Route by reason code.

**Engine decision: tesseract, not Apple Vision.** Vision (via the
third-party OCRmyPDF-AppleOCR plugin or `ocrit`) beats tesseract on printed
text per consistent community judgment (no rigorous published CER benchmark),
but the gain doesn't clear the bar: its advantage is largest on photos/
curved/degraded input, which escalate to Claude vision anyway (strictly
better); the plugin is third-party, macOS-only, and a portability/maintenance
cost. Tesseract on clean ~300-DPI scans is already low-single-digit CER.
Documented as an optional upgrade, not a dependency.

### Tier V — verification (always on, ~free)

`pdftotext -layout` (poppler) as a **second engine**, used as a per-page
text-recall check: strip Tier-1 markdown to plain text, compare per-page
token recall against pdftotext's output. Low agreement flags the page.

This is the piece no individual tool provides, and it directly targets
pdf-inspector's silent-loss bug class. The "different engine catches
different failures" theory was verified with concrete cases: broken
ToUnicode/CID fonts where MuPDF-family extractors emit garbage but poppler is
clean (PyMuPDF #3197, #431), engine-version regressions (#3186), and
xref-repair differences (poppler reconstructs damaged xref tables;
pdf-inspector doesn't, its #228). On healthy PDFs the engines agree, so
disagreement is a cheap, high-signal flag. Disagreement doesn't mean
pdftotext is right — it means the page warrants a vision look.

**Known blind spots of this tier** (found by adversarial review after
implementation; each has a mitigation, none a full fix):

- **OCR'd pages verify tautologically.** After Tier 2, both the extractor
  and pdftotext read the *same* tesseract text layer — recall ≈ 1.0 says
  nothing about whether tesseract itself mangled or dropped content. The
  only real check for OCR'd pages is the vision spot-check (`--verify`),
  which SKILL.md tells the agent to aim at exactly those pages.
- **Image content is invisible to both engines.** A page mixing live text
  with a page-scale image satisfies the cross-check on the live text alone.
  Mitigation lives in Tier 0, not here: triage runs an unconditional
  per-page signal (pypdf image-area + pdftotext char count, fully
  independent of pdf-inspector's classification) that routes such pages to
  OCR. This exists because pdf-inspector suppresses per-page OCR flags
  under a document-level `text_based` verdict — gating any hedge on that
  verdict recreates the (once-shipped, review-caught) silent-loss bug.
- **`recall: null` means unverifiable, not verified.** Pages where poppler
  sees (near-)nothing are skipped by the metric; the orchestrator flags the
  suspicious direction (extractor produced a full page where poppler saw
  nothing) rather than letting it pass as clean.

Diagnostic variants if structure must be reconstructed or garbling diagnosed:
`pdftotext -tsv` / `-bbox-layout` (word/block bounding boxes),
`pdftohtml -xml`, `mutool draw -F stext`. `pdftotext -raw` reveals raw
content-stream order (isolates reading-order bugs from font bugs).

### Tier 3 — Claude vision (two modes, different defaults)

Split deliberately so token consumption scales with document brokenness by
default, and with paranoia only on request:

1. **Escalation of flagged pages** — **on by default**. Pages that remain
   broken after tiers 0–2 (garbled, unresolved scans, failed tables, low
   Tier-V agreement) are Read directly. Clean documents consume zero vision
   tokens. `--no-vision` disables it for fully-local runs; flagged pages are
   then reported as unresolved in the conversion report, never silently kept.
2. **Spot-check of unflagged pages** — **opt-in `--verify`**. Samples N pages,
   compares the rendered page against the produced markdown. For documents
   where correctness really matters (contracts, financial reports).

Mechanics: inside Claude Code, the Read tool on a PDF natively supplies text
+ rendered page image per page (max 20 pages per request; `pages` parameter
required over 10 pages) — no rendering tooling needed. If standalone
rendering is ever required: `pdftoppm -png -r 150` — ~150 DPI is the ceiling
worth rendering for standard-tier vision (1568px long edge; more is silently
downscaled), ~200–230 DPI for high-res-tier models (2576px).

Guardrails: vision output is labeled as vision-derived in the report;
numeric/table transcriptions should be cross-validated (totals, cross-footing)
because VLM failure is silent hallucination, not loud garbage.

## Output contract

- The markdown document.
- A per-page conversion report: for every page, one of
  `extracted` / `extracted-docling` / `ocr` / `vision` / `flagged-unresolved`,
  plus the triggering reason codes. No page unaccounted for.
- Distinct exit codes for "converted clean" vs "converted with flagged pages"
  vs "failed", so callers can gate on quality.

## Tools evaluated and rejected (and why — don't re-litigate without new facts)

| Tool | Verdict | Reason |
|---|---|---|
| **pymupdf4llm** | rejected | License landmine: since v1.27 hard-depends on `pymupdf-layout`, which is **Polyform Noncommercial** (not open source; restricts *use*, not just distribution — a problem for a public MIT repo). Also last on tables (0.40) and headings (0.41) in the BSWEN benchmark; its strengths (speed, reading order, triage signals) duplicate pdf-inspector. Dropped on merit even where license is moot. |
| **PyMuPDF (raw)** | rejected | Its per-page triage signals (chars/page, image coverage, U+FFFD ratio) are duplicated by pdf-inspector's detector; AGPL adds license-labeling overhead for zero unique capability here. |
| **marker** (Datalab) | rejected for the skill; optional private experiment | Best local quality on paper (76% olmOCR-bench balanced), but model weights are restrictively licensed (revenue-capped waiver + non-compete) — wrong for a public repo — and on Apple Silicon it falls back to *fast* mode (~67%, similar tier to docling) for a 3–5 GB install at ~0.1 pages/sec. The pages where it would win already escalate to Claude vision. |
| **MinerU** | rejected | OmniDocBench leader (90.67) but ~20 GB footprint, GPU/CUDA-oriented VLM path; its unique wins (dense formulas, CJK tables) are what the vision tier handles at this volume. |
| **markitdown** (Microsoft) | rejected | PDF path is plain `pdfminer.six` — no layout, no OCR; strictly dominated by everything in the pipeline. |
| **Local VLMs** (olmOCR-2, dots.ocr, DeepSeek-OCR, Nanonets) | rejected | Multi-GB models, minutes/page on MPS, quality at or below the Claude vision tier, plus VLM hallucination risk. **Exception to remember**: if offline/no-API operation ever becomes a requirement, olmOCR-2's MLX port (Apache, ~6.4 GB 6-bit, `richardyoung/olmOCR-2-7B-1025-MLX-6bit`) is the pick for the escalation tier. |
| **Hosted OCR APIs** (Mistral OCR, Azure DI, Firecrawl Parse, LlamaParse) | rejected | Redundant given the Claude vision tier already available in-context. If a hosted tier were ever added, Mistral OCR is the best fit (markdown-native, ~$4/1k pages flat, PAYG); Firecrawl is subscription-only since mid-2026. |
| **Apple Vision OCR** | not default; documented optional | See Tier 2 rationale. |

Note on repo visibility: making the repo private / relicensing was evaluated
and **does not change this design** — the exclusions were driven primarily by
quality-on-macOS and install weight, not license. The local-quality ceiling is
set by hardware and by the vision tier's existence, not by licensing.

## Known risks / caveats to carry into implementation

- **pdf-inspector maturity**: ~6 months old, bus factor ≈1 (441/454 commits
  from one author), weekly release cadence, version-scheme churn. Tier V
  exists specifically to hedge this. Pin a known-good version.
- **pdf-inspector `confidence` semantics**: classification confidence only —
  never treat it as extraction-completeness.
- **anydoc is NOT the PDF path**: anydoc's benchmark explicitly excludes PDFs
  ("PDFs are out of scope" — `bench/README.md`); PDFs bypass its document
  model entirely and go straight to pdf-inspector. Use pdf-inspector
  directly for richer diagnostics (anydoc surfaces them log-only).
- **docling MPS**: CPU-only on Apple Silicon today (docling#3202).
- **Poppler is GPL**: invoked strictly as a subprocess (standard, fine for an
  MIT repo); never link.
- **Tesseract langs**: English-only by default; `brew install tesseract-lang`,
  select with `ocrmypdf -l eng+spa`.

## Field hardening (real-document case study, 2026-08-16)

A 423-page Spanish banking compendium (docling route, mixed decorative
pages) surfaced failure modes the fixture corpus missed; each drove a
permanent change:

- **Cross-page structure loss** (lists losing indentation at page breaks,
  furniture interleaving): caused by per-page `export_to_markdown(page_no)`
  resetting serializer context. Fix: one whole-document serialization split
  on docling's page-break placeholder — continuity preserved, page
  accounting kept.
- **Word-reversed table cells** (docling emitted the first line of cells
  reversed: "cuenta la de Mantenimiento"): invisible to unigram recall
  (0.97). Two-part fix: (1) `repair.fix_word_reversal` deterministically
  restores order using the poppler reference (order-true) as ground truth —
  the two engines now repair each other, not just disagree; (2) the
  verifier gained a **bigram recall** metric — high unigram + low bigram =
  order corruption → `word_order` flag → vision. This also catches
  scrambled dot-leader TOC columns, which are not clean reversals and
  can't be auto-repaired.
- **OCR debris on decorative pages** (tesseract hallucinating letters in a
  full-page photo under `--redo-ocr`, then verifying tautologically at
  recall 1.0): `repair.clean_ocr_debris` strips fragment noise from OCR'd
  pages; heavy-debris pages flag `ocr_debris` → vision.
- **Control-character tofu** (160 × U+0007 for symbol-font bullets):
  `repair.sanitize` strips C0/PUA glyphs from every page.
- **Unciteable output**: every page now starts with an invisible
  `<!-- p.N -->` anchor.
- **Lost figures** (543 empty `<!-- image -->` placeholders, including
  data diagrams): opt-in `--images` exports docling picture crops to
  `<out>.assets/` with a size filter that drops margin icons.

Known-not-fixed (documented): superscript footnote markers flatten to
plain digits; margin badge icons can leak short glyph runs ("CF") into
adjacent paragraphs; the docling cell-reversal bug itself is upstream
(worth reporting to docling with the reproducing document).

## Future work (the reason this file exists)

- **Granite-Docling-258M MLX** (`ibm-granite/granite-docling-258M-mlx`): IBM's
  258M-param VLM replacing docling's multi-model pipeline, with an *official*
  MLX build for Apple Silicon — the most Mac-native artifact in the whole
  survey (MIT-family licensing, tiny). When docling ships it as a stable
  pipeline option on macOS, it likely replaces the classic
  layout-model+TableFormer path inside the same `docling` dependency —
  re-evaluate then; absolute accuracy vs marker/MinerU was unestablished at
  time of writing.
- **docling MPS acceleration** (docling#3202): when it lands, docling's
  per-page cost drops; routing thresholds could loosen.
- **Apple Vision as ocrmypdf engine**: revisit if the OCRmyPDF-AppleOCR plugin
  matures or ocrmypdf grows first-party engine plugins.
- **Offline mode**: olmOCR-2 MLX as vision-tier replacement (see table above).
- **pdf-inspector upstream**: watch issues #342 (empty markdown regression),
  #383 (paragraph swallowed before tables), #395 (<8pt gutters), #400/#364
  (CJK ToUnicode) — each is a case Tier V/Tier 3 currently absorbs.

## Key sources

- [pdf-inspector](https://github.com/firecrawl/pdf-inspector) · [anydoc](https://github.com/firecrawl/anydoc) · [Fire-PDF launch](https://www.firecrawl.dev/blog/fire-pdf-launch) · [anydoc+pdf-inspector announcement](https://www.firecrawl.dev/blog/anydoc-and-pdf-inspector)
- [docling](https://github.com/docling-project/docling) · [Granite-Docling MLX](https://huggingface.co/ibm-granite/granite-docling-258M-mlx) · [docling MPS issue](https://github.com/docling-project/docling/issues/3202)
- [ocrmypdf advanced docs](https://ocrmypdf.readthedocs.io/en/latest/advanced.html) · [pdftotext man page](https://manpages.debian.org/testing/poppler-utils/pdftotext.1.en.html)
- [Claude PDF support](https://platform.claude.com/docs/en/build-with-claude/pdf-support) · [Claude vision limits](https://platform.claude.com/docs/en/build-with-claude/vision)
- Benchmarks: [BSWEN 200-PDF benchmark](https://docs.bswen.com/blog/2026-06-04-benchmark-comparison/) · [OmniDocBench](https://github.com/opendatalab/OmniDocBench) · [olmOCR-bench leaderboard](https://benchmarklist.com/benchmarks/olmocr_bench/) · [Marker v2 benchmark breakdown](https://www.marktechpost.com/2026/07/24/datalab-marker-v2-vs-mineru-docling-and-liteparse-benchmark-breakdown/)
- [pymupdf-layout license (Polyform NC)](https://pypi.org/project/pymupdf-layout/) · [PyMuPDF license discussion #971](https://github.com/pymupdf/PyMuPDF/discussions/971) · [GNU GPL FAQ on subprocess boundary](https://www.gnu.org/licenses/gpl-faq.html#MereAggregation)
