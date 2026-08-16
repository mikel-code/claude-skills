---
name: doc-to-markdown
description: "Convert an office document (docx/doc, pptx/ppt, xlsx/xls, odt/ods/odp, rtf, epub, csv) to a markdown file with Open Knowledge Format frontmatter, via the anydoc CLI. Refuses PDFs — those need the pdf-to-markdown skill's tiered OCR/verification pipeline. Use when the user asks to convert a Word/PowerPoint/Excel/OpenDocument/RTF/EPUB/CSV file to markdown."
license: MIT
compatibility: Requires Node/npm (anydoc CLI) and Python 3.10+ with uv
---

# doc-to-markdown

Thin wrapper around [anydoc](https://github.com/firecrawl/anydoc) — office
document formats are a solved conversion problem, so this skill is just the
CLI plus OKF frontmatter, no pipeline. Supported formats: docx, doc, pptx,
ppt, xlsx, xls, odt, ods, odp, rtf, epub, csv.

## Command

```bash
uv run <skill-dir>/scripts/doc_to_markdown.py <input-file> [-o OUTPUT.md] [--output-dir DIR]
```

Default output is `<input-stem>.md` in the current directory.

## Output

One markdown file: [Open Knowledge Format (OKF) v0.2](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
frontmatter (`type: Document`, title from the first markdown heading anydoc
produced — else the filename stem —, description from the first paragraph,
`resource`/`sources` pointing at the input file's absolute path,
`generated.by: machine:doc-to-markdown/anydoc`), then anydoc's converted
markdown body unchanged.

## PDFs go elsewhere

This skill refuses PDF input (by extension and by `%PDF-` magic bytes) and
points at the `pdf-to-markdown` skill instead — PDFs need a tiered pipeline
with routing, OCR, and verification that doesn't belong in a trivial wrapper.

## Troubleshooting (exit codes)

- **0** success.
- **1** unexpected error — bad input path, or anydoc itself was invoked
  wrong (a bug in this script, not the document).
- **2** `anydoc` not on PATH — install with `npm install -g @firecrawl/anydoc`
  (requires Node/npm).
- **3** input refused — it's a PDF; use the `pdf-to-markdown` skill.
- **4** anydoc could not convert the document. Its stderr message is passed
  through and tells you why — observed cases: `document is encrypted`
  (password-protected), `unsupported input: unrecognized file content and
  extension` (not one of the supported formats, or a bare extension-less
  file), `malformed document: not a readable zip archive` (corrupt
  docx/xlsx/pptx/odt — these are zip containers), `io error` (permissions or
  path problem). Nothing is written to the output file on any exit-4 failure.
