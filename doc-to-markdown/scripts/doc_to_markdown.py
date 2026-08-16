# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Convert an office document to an OKF-frontmatter markdown file via anydoc.

anydoc (https://github.com/firecrawl/anydoc, `npm install -g @firecrawl/anydoc`)
does all the actual conversion — this script only gates the dependency,
refuses PDFs (they belong to the pdf-to-markdown skill's tiered pipeline),
and prepends Open Knowledge Format (OKF) v0.2 frontmatter to anydoc's output.

Usage:
    uv run doc_to_markdown.py <input-file> [-o OUTPUT.md] [--output-dir DIR]
Options:
    -o, --output FILE   output markdown path (default: <input-stem>.md in cwd)
    --output-dir DIR    directory for the default filename (ignored if -o given)
Exit codes:
    0  success
    1  unexpected error (bad input path, anydoc usage error, etc.)
    2  anydoc not found on PATH
    3  input refused (PDF — use the pdf-to-markdown skill instead)
    4  anydoc could not convert the document (encrypted/unsupported/malformed)
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_NOT_CONFIGURED = 2
EXIT_REFUSED = 3
EXIT_CONVERT_FAILED = 4

PDF_REFUSAL_MSG = (
    "PDFs need the pdf-to-markdown skill (tiered pipeline with OCR and "
    "verification) — this skill handles office formats only."
)

# ------------------------------------------------------------------ helpers


def yaml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def is_pdf(path: Path) -> bool:
    """Refuse by extension AND by magic bytes — an office file with a wrong
    extension, or a .pdf-named file that isn't really one, both matter."""
    if path.suffix.lower() == ".pdf":
        return True
    try:
        with path.open("rb") as fh:
            return fh.read(5) == b"%PDF-"
    except OSError:
        return False


def check_anydoc() -> str:
    anydoc = shutil.which("anydoc")
    if anydoc:
        return anydoc
    print(
        "doc-to-markdown: 'anydoc' not found on PATH.\n\n"
        "Install it with:\n"
        "  npm install -g @firecrawl/anydoc\n\n"
        "Requires Node.js/npm. See https://github.com/firecrawl/anydoc.",
        file=sys.stderr,
    )
    sys.exit(EXIT_NOT_CONFIGURED)


def run_anydoc(anydoc: str, input_path: Path) -> str:
    """Run anydoc, returning the converted markdown. Never returns partial
    output on failure — either the full markdown or a hard exit.

    anydoc exit codes observed (v0.1.8): 0 success; 1 the document could not
    be read or converted (stderr message classifies why — 'document is
    encrypted', 'malformed document: ...', 'unsupported input: ...', 'io
    error: ...'); 2 usage error (shouldn't happen — we always pass one file
    argument)."""
    try:
        result = subprocess.run(
            [anydoc, str(input_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        print(f"doc-to-markdown: could not run anydoc: {exc}", file=sys.stderr)
        sys.exit(EXIT_UNEXPECTED)

    if result.returncode == 0:
        return result.stdout

    message = result.stderr.strip() or "(no error output)"
    if result.returncode == 1:
        print(
            f"doc-to-markdown: anydoc could not convert '{input_path.name}': {message}",
            file=sys.stderr,
        )
        sys.exit(EXIT_CONVERT_FAILED)

    # exit 2 (usage error) or anything else unforeseen — this is our bug, not
    # a document problem, since we always invoke anydoc with one file arg.
    print(
        f"doc-to-markdown: anydoc invocation failed unexpectedly (exit {result.returncode}): {message}",
        file=sys.stderr,
    )
    sys.exit(EXIT_UNEXPECTED)


# ------------------------------------------------------------ frontmatter


def first_heading(lines: list[str]) -> str | None:
    for line in lines:
        m = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if m:
            return m.group(1).strip()
    return None


def first_paragraph(lines: list[str]) -> str | None:
    """First contiguous run of non-blank, non-heading, non-table-row lines."""
    buf: list[str] = []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("|"):
            if buf:
                break
            continue
        buf.append(s)
    return " ".join(buf) if buf else None


def okf_frontmatter(markdown: str, input_path: Path) -> str:
    lines = markdown.splitlines()
    title = first_heading(lines) or input_path.stem
    description = first_paragraph(lines)
    resource = str(input_path.resolve())
    ext = input_path.suffix.lower().lstrip(".") or "document"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    fm = [
        "---",
        "type: Document",
        f"title: {yaml_str(title)}",
    ]
    if description:
        fm.append(f"description: {yaml_str(description[:200])}")
    fm += [
        f"resource: {yaml_str(resource)}",
        f'tags: [{yaml_str("document")}, {yaml_str(ext)}]',
        "generated:",
        '  by: "machine:doc-to-markdown/anydoc"',
        f"  at: {now}",
        "sources:",
        f"  - resource: {yaml_str(resource)}",
        "status: stable",
        "---",
    ]
    return "\n".join(fm) + "\n"


# --------------------------------------------------------------------- main


def main() -> None:
    ap = argparse.ArgumentParser(description="Office document -> OKF markdown (via anydoc)")
    ap.add_argument("input", help="path to the input document")
    ap.add_argument("-o", "--output", default="", help="output markdown path")
    ap.add_argument("--output-dir", default=".", help="directory for the default filename")
    args = ap.parse_args()

    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"doc-to-markdown: input file not found: {input_path}", file=sys.stderr)
        sys.exit(EXIT_UNEXPECTED)

    if is_pdf(input_path):
        print(PDF_REFUSAL_MSG, file=sys.stderr)
        sys.exit(EXIT_REFUSED)

    anydoc = check_anydoc()
    markdown = run_anydoc(anydoc, input_path)

    frontmatter = okf_frontmatter(markdown, input_path)
    body = markdown if markdown.endswith("\n") else markdown + "\n"
    out_path = Path(args.output) if args.output else Path(args.output_dir) / f"{input_path.stem}.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(frontmatter + "\n" + body, encoding="utf-8")
    print(str(out_path))


if __name__ == "__main__":
    main()
