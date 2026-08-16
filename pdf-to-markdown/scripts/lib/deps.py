"""Dependency gate for the pdf-to-markdown pipeline.

Checks external (non-pip) tools and exits EXIT_NOT_CONFIGURED with install
instructions when something is missing — the granola gating pattern: fail
early with the fix on stderr, distinct exit code, never a mid-run surprise.

pip-level deps (pdf-inspector, docling) are declared in the orchestrator's
inline uv metadata and resolved by `uv run`; they are not checked here.
"""

from __future__ import annotations

import shutil
import sys

from contract import EXIT_NOT_CONFIGURED

_TOOLS = {
    "pdftotext": "brew install poppler          # tier V cross-check",
    "ocrmypdf": "brew install ocrmypdf          # tier 2 OCR",
    "tesseract": "brew install tesseract tesseract-lang   # OCR engine + languages",
}


def check_deps(*, need_ocr: bool = True) -> None:
    missing = {t: fix for t, fix in _TOOLS.items()
               if shutil.which(t) is None
               and (need_ocr or t == "pdftotext")}
    if not missing:
        return
    print("pdf-to-markdown: missing dependencies —\n", file=sys.stderr)
    for tool, fix in missing.items():
        print(f"  {tool:<10} {fix}", file=sys.stderr)
    print("\nInstall the above and re-run.", file=sys.stderr)
    sys.exit(EXIT_NOT_CONFIGURED)


if __name__ == "__main__":
    check_deps()
    print("all external dependencies present")
