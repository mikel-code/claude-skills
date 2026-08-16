# /// script
# requires-python = ">=3.10"
# dependencies = ["pdf-inspector==1.14.2", "docling==2.120.1"]
# ///
"""Tier 1 extraction: pdf-inspector (default) or docling (routed).

extract(pdf_path, route) -> ExtractResult, per contract.py. Both engines are
declared as dependencies so `uv run` resolves either at once; each engine's
heavy import is deferred into its own function so choosing one route doesn't
pay the other's import cost (docling's first conversion additionally
downloads model weights, possibly >1 GB — expected on first run only, weights
are cached by docling afterwards).

Docling runs with do_ocr=False: design rule is exactly one OCR path in the
pipeline (tier 2, ocrmypdf/tesseract), and this tier is not it. Scanned pages
are expected to have already gone through tier 2 (producing a text layer)
before ever reaching this tier's docling route.

Pages whose extracted markdown is empty/None are omitted from
ExtractResult.pages for both engines — per contract.py, absence is the
"no markdown yielded" signal; the orchestrator is responsible for flagging
that as EMPTY_OUTPUT, not this module.
"""

from __future__ import annotations

from pathlib import Path

from contract import DocRoute, ExtractResult


def _extract_pdf_inspector(pdf_path: str) -> ExtractResult:
    import pdf_inspector

    result = pdf_inspector.extract_pages_markdown(pdf_path)

    pages: dict[int, str] = {}
    for page_md in result.pages:
        md = page_md.markdown
        if not md:
            continue  # empty/None markdown: omit rather than fabricate
        pages[page_md.page + 1] = md  # PageMarkdown.page is 0-indexed

    # process_pdf() re-parses the document to get classification + metadata;
    # cheap relative to the extraction pass above (tens of ms on typical
    # documents), so it's fine to call for title alone. Guarded because title
    # lookup is a nice-to-have, not load-bearing for the extraction result.
    title: str | None = None
    try:
        title = pdf_inspector.process_pdf(pdf_path).title or None
    except Exception:
        title = None

    return ExtractResult(engine=DocRoute.PDF_INSPECTOR, pages=pages, title=title)


def _extract_docling(pdf_path: str) -> ExtractResult:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    pipeline_opts = PdfPipelineOptions(do_ocr=False, do_table_structure=True)
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_opts)}
    )
    result = converter.convert(pdf_path)
    doc = result.document

    # Per-page export mechanism chosen: DoclingDocument.export_to_markdown(
    # page_no=N). Verified empirically (not just from the signature) against
    # the probe-simple.pdf fixture: doc.pages is a dict keyed by *1-indexed*
    # page number ({1, 2} for a 2-page doc, matching this contract's 1-indexed
    # convention with no translation needed), and export_to_markdown(page_no=N)
    # internally sets MarkdownParams(pages={N}, ...) to filter the document
    # body down to items whose provenance falls on page N, then serializes
    # with the normal markdown serializer (so tables/headings render exactly
    # as they would in a full-document export). This is the native supported
    # per-page path; the alternative — walking doc.texts/doc.tables/... by
    # hand, filtering each item's item.prov[].page_no == N, and re-serializing
    # by item type — would just reimplement what export_to_markdown already
    # does internally, with more surface area for bugs.
    pages: dict[int, str] = {}
    for page_no in doc.pages.keys():
        md = doc.export_to_markdown(page_no=page_no)
        if not md:
            continue
        pages[page_no] = md

    # No cheap document-level title here: DoclingDocument has no metadata
    # title field, and doc.name is just the input filename stem (verified:
    # 'probe-simple.pdf' -> doc.name == 'probe-simple', not the PDF's actual
    # /Title 'untitled') — not real title metadata, so left unset rather than
    # fabricated from the filename.
    return ExtractResult(engine=DocRoute.DOCLING, pages=pages, title=None)


def extract(pdf_path: str, route: DocRoute) -> ExtractResult:
    if route == DocRoute.PDF_INSPECTOR:
        return _extract_pdf_inspector(pdf_path)
    if route == DocRoute.DOCLING:
        return _extract_docling(pdf_path)
    raise ValueError(f"unknown route: {route!r}")


if __name__ == "__main__":
    import argparse
    import sys

    SCRIPT_DIR = Path(__file__).resolve().parent
    DEFAULT_PDF = SCRIPT_DIR.parent.parent / "tests" / "fixtures" / "probe-simple.pdf"

    parser = argparse.ArgumentParser(description="extract.py self-test")
    parser.add_argument("pdf", nargs="?", default=str(DEFAULT_PDF))
    parser.add_argument(
        "engine", nargs="?", choices=["pdf-inspector", "docling", "both"], default="both"
    )
    args = parser.parse_args()

    routes = (
        [DocRoute.PDF_INSPECTOR, DocRoute.DOCLING]
        if args.engine == "both"
        else [DocRoute(args.engine)]
    )

    results: dict[DocRoute, ExtractResult] = {}
    for route in routes:
        print(f"\n=== engine: {route.value} ===", file=sys.stderr)
        res = extract(args.pdf, route)
        results[route] = res
        print(f"title: {res.title!r}", file=sys.stderr)
        for page in sorted(res.pages):
            excerpt = res.pages[page][:200].replace("\n", " / ")
            print(f"  page {page}: {excerpt}", file=sys.stderr)

        assert len(res.pages) == 2, f"{route.value}: expected 2 pages, got {len(res.pages)}"
        assert 1 in res.pages and 2 in res.pages, f"{route.value}: missing page 1 or 2"

        page1 = res.pages[1]
        assert "Quarterly Report" in page1 or "Quarterly report" in page1, (
            f"{route.value}: page 1 missing 'Quarterly Report' heading"
        )
        page2 = res.pages[2]
        assert "Outlook" in page2, f"{route.value}: page 2 missing 'Outlook'"

    if DocRoute.DOCLING in results:
        docling_page1 = results[DocRoute.DOCLING].pages[1]
        assert any(line.strip().startswith("|") for line in docling_page1.splitlines()), (
            "docling: page 1 missing a markdown table ('|' line)"
        )

    print("\nself-test passed.", file=sys.stderr)
