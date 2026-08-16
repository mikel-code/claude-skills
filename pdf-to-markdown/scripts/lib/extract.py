# /// script
# requires-python = ">=3.10"
# dependencies = ["pdf-inspector==1.14.2", "docling==2.120.1"]
# ///
"""Tier 1 extraction: pdf-inspector (default) or docling (routed).

extract(pdf_path, route, images_dir=None) -> ExtractResult, per contract.py.
Both engines are declared as dependencies so `uv run` resolves either at once;
each engine's heavy import is deferred into its own function so choosing one
route doesn't pay the other's import cost (docling's first conversion
additionally downloads model weights, possibly >1 GB — expected on first run
only, weights are cached by docling afterwards).

Docling runs with do_ocr=False: design rule is exactly one OCR path in the
pipeline (tier 2, ocrmypdf/tesseract), and this tier is not it. Scanned pages
are expected to have already gone through tier 2 (producing a text layer)
before ever reaching this tier's docling route.

Docling per-page mechanism — serialize ONCE, then split (deliberate):
the earlier implementation called export_to_markdown(page_no=N) once per
page, which resets the markdown serializer's context at every page boundary —
lists continuing onto the next page lose their nesting/numbering context,
split paragraphs join badly, and on an N-page document it re-walks the whole
item tree N times (quadratic on large documents; user-hit on a real 423-page
file). The current implementation exports the whole document exactly once
with a collision-proof `page_break_placeholder` token (verified present and
functional in docling 2.120.1: the serializer emits an internal
#_#_DOCLING_DOC_PAGE_BREAK_prev_next_#_# marker between body items whose
1-indexed provenance page increases, then substitutes the placeholder), and
splits the result on that token. Cross-page constructs are therefore
serialized with full document context; a construct cut by the placeholder may
have its text split across two page entries, which is fine — the
serialization context is what matters, and page attribution stays exact.

Segment -> page mapping: a page with no BODY-layer items emits no break (a
3->5 jump yields ONE break), so segments do NOT map to 1..N blindly. The
break-emission rule is replicated here (_docling_page_sequence: strictly
increasing prov page numbers over body items in reading order — same rule,
same item iteration docling itself uses) and the segment count is asserted
against it; on any mismatch the code falls back LOUDLY (stderr warning) to
the legacy per-page export rather than risk off-by-one page attribution.
Pages absent from the sequence (no body items) yield no entry in
ExtractResult.pages — same empty-page-omission rule as before, per
contract.py: absence is the "no markdown yielded" signal; the orchestrator
flags it as EMPTY_OUTPUT, not this module.

Image export (docling route only): when images_dir is given, the pipeline
enables generate_picture_images (images_scale=2.0) and exports with
ImageRefMode.REFERENCED via save_as_markdown mechanics, so real figures land
as PNG files in images_dir and the markdown carries relative
![](<images_dir basename>/....png) references — i.e. the refs resolve when
the final markdown is saved as a *sibling* of images_dir (the orchestrator's
<output>.assets layout). Pictures whose generated pixel area is below
MIN_PICTURE_AREA_PX2 (margin icons, badges, logos) are filtered by nulling
their in-memory image, which makes the serializer fall back to the usual
`<!-- image -->` placeholder for exactly those items. With images_dir=None
(default) behavior is unchanged: every picture is a placeholder. The
pdf-inspector route does not support image export; images_dir is accepted
and ignored there.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from contract import DocRoute, ExtractResult

# Pictures whose exported pixel area (at images_scale, i.e. the pixels that
# would be written to disk) is below this are treated as page furniture
# (margin icons/badges/logos) and kept as placeholders instead of files.
# 15,000 px^2 at scale 2.0 ~= a 61x61pt original — comfortably above icon
# size, comfortably below any real figure (measured on the motivating
# document: icons 27x29 px, real diagrams >250,000 px^2).
MIN_PICTURE_AREA_PX2 = 15_000

# Docling picture-render scale when images_dir is requested (2.0 ~= 144 DPI).
IMAGES_SCALE = 2.0


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


def _docling_page_sequence(doc) -> list[int]:
    """Ordered pages that receive a segment in the whole-doc export.

    Replicates docling's page-break emission rule (docling_core
    transforms/serializer/common.py::_iterate_items): walking BODY-layer
    items in reading order, a break is emitted each time an item's first
    provenance page number strictly increases. The resulting strictly
    increasing page list has exactly len(breaks)+1 entries — one per
    exported segment, in order. Pages with no body items never appear
    (and get no markdown), matching the empty-page-omission rule.
    """
    from docling_core.types.doc.document import ContentLayer

    seq: list[int] = []
    prev: int | None = None
    for item, _ in doc.iterate_items(
        with_groups=False, included_content_layers={ContentLayer.BODY}
    ):
        prov = getattr(item, "prov", None)
        if not prov:
            continue
        page_no = prov[0].page_no
        if prev is None or page_no > prev:
            seq.append(page_no)
            prev = page_no
    return seq


def _docling_export_full(doc, token: str, images_dir: str | None) -> str:
    """One whole-document markdown export with `token` at page boundaries.

    With images_dir: docling's save_as_markdown is the public API that both
    writes picture files and rewrites their refs relative to the markdown
    file's directory — so the export goes through a throwaway .md file placed
    in images_dir's parent, with artifacts_dir passed as the *relative*
    basename (that is what makes the refs come out relative, e.g.
    "out.assets/image_000003_<hash>.png").
    """
    if images_dir is None:
        return doc.export_to_markdown(page_break_placeholder=token)

    import os
    import tempfile

    from docling_core.types.doc.base import ImageRefMode

    images_path = Path(images_dir)
    images_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        suffix=".md", prefix=".pdf2md-export-", dir=str(images_path.parent)
    )
    os.close(fd)
    tmp_md = Path(tmp_name)
    try:
        doc.save_as_markdown(
            tmp_md,
            artifacts_dir=Path(images_path.name),
            image_mode=ImageRefMode.REFERENCED,
            page_break_placeholder=token,
        )
        return tmp_md.read_text(encoding="utf-8")
    finally:
        tmp_md.unlink(missing_ok=True)


def _filter_small_pictures(doc) -> None:
    """Null the in-memory image of below-threshold pictures.

    A PictureItem whose .image is None cannot be materialized by
    _with_pictures_refs (no page images are generated, so there is no crop
    fallback), so REFERENCED export degrades to the standard
    `<!-- image -->` placeholder for exactly those items — verified
    empirically on docling 2.120.1.
    """
    for pic in doc.pictures:
        try:
            img = pic.get_image(doc=doc)
        except Exception:
            continue  # unreadable image: leave untouched, export decides
        if img is None:
            continue
        if img.size[0] * img.size[1] < MIN_PICTURE_AREA_PX2:
            pic.image = None


def _extract_docling(pdf_path: str, images_dir: str | None = None) -> ExtractResult:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    pipeline_opts = PdfPipelineOptions(do_ocr=False, do_table_structure=True)
    if images_dir is not None:
        pipeline_opts.generate_picture_images = True
        pipeline_opts.images_scale = IMAGES_SCALE
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_opts)}
    )
    result = converter.convert(pdf_path)
    doc = result.document

    if images_dir is not None:
        _filter_small_pictures(doc)

    # Serialize ONCE with full document context, then split into pages on a
    # collision-proof token (see module docstring for why per-page export is
    # wrong). doc.pages / provenance page numbers are 1-indexed in docling,
    # matching this contract's convention with no translation.
    token = f"<<<pdf-to-markdown:page-break:{uuid.uuid4().hex}>>>"
    full_md = _docling_export_full(doc, token, images_dir)
    segments = full_md.split(token)
    page_seq = _docling_page_sequence(doc)

    pages: dict[int, str] = {}
    if len(segments) == len(page_seq):
        for page_no, segment in zip(page_seq, segments):
            md = segment.strip()
            if not md:
                continue  # a page whose items serialize to nothing: omit
            pages[page_no] = md
    elif not page_seq and len(segments) == 1 and not segments[0].strip():
        pass  # document with no body content at all: no pages, by design
    else:
        # Attribution safety net: if the split doesn't line up with the
        # replicated break rule, per-page mapping would risk off-by-one page
        # attribution — fall back to the legacy per-page export (correct
        # attribution, degraded cross-page serialization) and say so loudly.
        print(
            f"extract.py: WARNING: docling page-break split mismatch "
            f"({len(segments)} segments vs {len(page_seq)} expected pages) — "
            f"falling back to per-page export; cross-page constructs may "
            f"serialize without full context",
            file=sys.stderr,
        )
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


def extract(
    pdf_path: str, route: DocRoute, images_dir: str | None = None
) -> ExtractResult:
    """Run tier-1 extraction.

    images_dir: directory for exported figure PNGs (docling route only —
    the pdf-inspector route has no image-export support and ignores it).
    Markdown image refs are written relative to images_dir's parent, so the
    final markdown file is expected to be saved as a sibling of images_dir.
    """
    if route == DocRoute.PDF_INSPECTOR:
        return _extract_pdf_inspector(pdf_path)
    if route == DocRoute.DOCLING:
        return _extract_docling(pdf_path, images_dir=images_dir)
    raise ValueError(f"unknown route: {route!r}")


if __name__ == "__main__":
    import argparse
    import re
    import tempfile

    SCRIPT_DIR = Path(__file__).resolve().parent
    FIXTURES_DIR = SCRIPT_DIR.parent.parent / "tests" / "fixtures"
    DEFAULT_PDF = FIXTURES_DIR / "probe-simple.pdf"

    parser = argparse.ArgumentParser(description="extract.py self-test")
    parser.add_argument("pdf", nargs="?", default=str(DEFAULT_PDF))
    parser.add_argument(
        "engine", nargs="?", choices=["pdf-inspector", "docling", "both"], default="both"
    )
    parser.add_argument(
        "--images-dir", default=None,
        help="export figure images here (docling route only; ad-hoc runs)",
    )
    args = parser.parse_args()

    routes = (
        [DocRoute.PDF_INSPECTOR, DocRoute.DOCLING]
        if args.engine == "both"
        else [DocRoute(args.engine)]
    )
    default_fixture = args.pdf == str(DEFAULT_PDF)

    results: dict[DocRoute, ExtractResult] = {}
    for route in routes:
        print(f"\n=== engine: {route.value} ===", file=sys.stderr)
        res = extract(args.pdf, route, images_dir=args.images_dir)
        results[route] = res
        print(f"title: {res.title!r}", file=sys.stderr)
        for page in sorted(res.pages):
            excerpt = res.pages[page][:200].replace("\n", " / ")
            print(f"  page {page}: {excerpt}", file=sys.stderr)

        if not default_fixture:
            continue  # ad-hoc PDF: listing above is the output, no asserts

        assert len(res.pages) == 2, f"{route.value}: expected 2 pages, got {len(res.pages)}"
        assert 1 in res.pages and 2 in res.pages, f"{route.value}: missing page 1 or 2"

        page1 = res.pages[1]
        assert "Quarterly Report" in page1 or "Quarterly report" in page1, (
            f"{route.value}: page 1 missing 'Quarterly Report' heading"
        )
        page2 = res.pages[2]
        assert "Outlook" in page2, f"{route.value}: page 2 missing 'Outlook'"

    if default_fixture and DocRoute.DOCLING in results:
        docling_page1 = results[DocRoute.DOCLING].pages[1]
        assert any(line.strip().startswith("|") for line in docling_page1.splitlines()), (
            "docling: page 1 missing a markdown table ('|' line)"
        )

        # Split-path empty-page mapping: blank-mid.pdf has a content-free
        # middle page — the whole-doc export emits no break for it, and the
        # segment->page mapping must land content on pages 1 and 3, with
        # page 2 omitted (never misattributed).
        blank_mid = FIXTURES_DIR / "blank-mid.pdf"
        if blank_mid.exists():
            print("\n=== docling split path: blank-mid.pdf ===", file=sys.stderr)
            res = extract(str(blank_mid), DocRoute.DOCLING)
            assert set(res.pages.keys()) == {1, 3}, (
                f"blank-mid: expected pages {{1, 3}}, got {sorted(res.pages)}"
            )
            print(f"  pages: {sorted(res.pages)} (page 2 omitted)", file=sys.stderr)
        else:
            print(f"skipping blank-mid check ({blank_mid} missing)", file=sys.stderr)

        # images_dir option, picture-free document: probe-simple has no
        # PictureItems (none of the synthetic fixtures yield any — docling's
        # layout model doesn't classify their raster content as pictures),
        # so this covers the REFERENCED/save_as_markdown export path and
        # asserts behavior is unchanged when there is nothing to export:
        # same pages, no ![](...) refs, no stray files, no leftover temp .md.
        # Real figure export + icon filtering is validated against real
        # documents via `uv run extract.py <pdf> docling --images-dir <dir>`.
        print("\n=== docling images_dir: probe-simple.pdf ===", file=sys.stderr)
        with tempfile.TemporaryDirectory(prefix="extract-imgtest-") as tmp:
            images_dir = Path(tmp) / "out.assets"
            res = extract(str(DEFAULT_PDF), DocRoute.DOCLING, images_dir=str(images_dir))
            assert set(res.pages.keys()) == {1, 2}, (
                f"images_dir run: expected pages {{1, 2}}, got {sorted(res.pages)}"
            )
            assert "Outlook" in res.pages[2], "images_dir run: page 2 content changed"
            all_md = "\n".join(res.pages.values())
            assert not re.findall(r"!\[[^\]]*\]\(", all_md), (
                "images_dir run: unexpected image ref in picture-free document"
            )
            stray = [p.name for p in Path(tmp).iterdir()
                     if p.name != "out.assets" and not p.is_dir()]
            assert not stray, f"images_dir run: leftover files next to images_dir: {stray}"
            pngs = sorted(images_dir.glob("*.png")) if images_dir.exists() else []
            assert not pngs, f"images_dir run: unexpected PNGs for picture-free doc: {pngs}"
            print("  pages: {1, 2}, no refs, no files — placeholder behavior kept",
                  file=sys.stderr)

    print("\nself-test passed.", file=sys.stderr)
