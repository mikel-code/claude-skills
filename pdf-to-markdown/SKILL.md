---
name: pdf-to-markdown
description: "Convert a PDF into OKF-frontmatter markdown through a tiered, correctness-first pipeline: local structure extraction (pdf-inspector, docling for table-heavy docs), OCR for scanned pages (ocrmypdf), cross-engine verification (poppler), and Claude-vision escalation for pages the tools can't resolve. Use for /pdf-to-markdown, \"convert this PDF to markdown\", \"extract the text/tables from this PDF\", or \"turn this paper/report into markdown\". Non-PDF office documents belong to the doc-to-markdown skill instead."
license: MIT
compatibility: Requires Python 3.10+ with uv, and Homebrew poppler + ocrmypdf + tesseract (+ tesseract-lang for non-English). First docling run downloads ~1-2 GB of models.
---

# pdf-to-markdown

Deterministic, script-driven PDF conversion with a per-page audit trail. The
script does the tiered work (triage → extract → OCR → verify); **you are
tier 3** — the script hands you exactly the pages it could not resolve, and
you transcribe them visually. `references/design.md` records the full design
and its rationale; read it before changing anything.

## Convert

```bash
uv run <skill-dir>/scripts/pdf_to_markdown.py <file.pdf> [-o out.md] [--output-dir DIR]
#   --json           print the machine-readable report to stdout
#   --no-vision      fully local: flagged pages stay unresolved, no vision step
#   --verify         request a vision spot-check of unflagged pages too
#   -l eng+spa       OCR languages (tesseract codes)
```

Outputs `<stem>.md` (OKF v0.2 frontmatter + content) and
`<stem>.report.json` (per-page status: every page is accounted for — nothing
is ever silently dropped).

## Exit codes → what you do next

- **0** — clean. Every page extracted; born-digital pages cross-verified
  against a second engine. Caveat: OCR'd pages pass verification
  tautologically (both engines read the same tesseract output), so for
  documents where correctness is critical, offer the user `--verify`.
  Deliver the file.
- **4** — converted, but pages need vision (`report.vision.pending_pages`).
  Do the tier-3 workflow below, then deliver.
- **2** — missing Homebrew dependency; stderr names the `brew install` fix.
- **1** — unexpected failure; read stderr, don't retry blindly.

## Tier-3 workflow (you, on exit 4)

1. Read `<stem>.report.json` → `vision.pending_pages` and each page's
   `reasons` (scanned / low_recall / suspected_garbled_text / empty_output…).
2. Read those pages of the **source PDF** directly with the Read tool
   (`pages` parameter; ≤20 pages per request) — you get text + page image.
3. In the markdown file, replace each
   `<!-- page N: flagged (…) — awaiting vision transcription -->` placeholder
   with your transcription of that page. If partial extractor output follows
   the placeholder, use it as a hint but trust the page image.
4. Label what you produced: keep an HTML comment
   `<!-- page N: transcribed by vision -->` above the transcribed content.
5. **Numeric tables: cross-foot before accepting.** Vision transcription
   fails silently (plausible wrong values) — check totals/sums where the
   table implies them; if a figure is illegible, write `[illegible]` rather
   than a guess.
6. Update the report file: set those pages' `status` to `"vision"` and
   `clean` accordingly, so provenance survives.

With `--verify`, additionally sample 2–3 *unflagged* pages, Read them, and
compare against the markdown; report discrepancies to the user rather than
silently patching them. Prioritize `"ocr"` pages and pages with
`recall: null` in the report — those are exactly the ones the automatic
cross-check cannot really vouch for (OCR verification is tautological:
both engines read the same tesseract text layer).

If the user asked for `--no-vision` (or offline), skip all of this and tell
them which pages are unresolved and why.

## Routing (informational)

Triage picks the extractor per document: pdf-inspector by default; docling
when layout is complex or table-heavy (better table structure, slower,
model-based). Scanned/garbled pages get one ocrmypdf pass whose mode is
chosen by reason code (`--skip-text` / `--redo-ocr` / `--force-ocr` — see
design.md for why garbled-text pages must use force). Every page's markdown
is then cross-checked against poppler's independent extraction; low recall
flags the page rather than trusting either engine.

## Troubleshooting

- **"missing dependencies" (exit 2)** → run the printed `brew install` lines.
- **First run is slow / downloads models** → docling fetching weights;
  one-time, cached under `~/.cache`.
- **Wrong language OCR** → pass `-l` with the right tesseract codes
  (needs `tesseract-lang` for non-English).
- **Report says `low_recall` on a page that looks fine** → the verifier is
  conservative by design; Read the page, confirm visually, and if the
  markdown is actually complete just note it in the report (status stays
  auditable either way).
- **Encrypted PDF** → not supported; ask the user for a decrypted copy.
