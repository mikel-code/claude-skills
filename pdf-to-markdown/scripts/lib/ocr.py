"""Tier 2 — OCR via ocrmypdf (tesseract engine).

Wraps the `ocrmypdf` CLI as a subprocess (never imports ocrmypdf as a
library — this repo treats it as an external tool the way pdftotext/poppler
is treated elsewhere in the pipeline). Produces a PDF with a positioned
hidden text layer; that PDF feeds back into Tier 1 routing — there is no
separate OCR-to-markdown path (see references/design.md, Tier 2 section).

Flag semantics (from design.md's table — triage.py picks the mode, this
module just executes it):
    --skip-text   mixed documents (default): OCRs only textless pages,
                  copies text pages through unchanged.
    --redo-ocr    mixed pages / stale OCR layers: strips invisible prior
                  OCR, masks real text, OCRs the rest, merges.
    --force-ocr   "selectable but garbage" text (broken ToUnicode/CID
                  maps): rasterizes everything, discards existing text.

ocrmypdf's own exit codes (ocrmypdf.exceptions.ExitCode, mirrored here
verbatim since we shell out rather than import the library) are handled
explicitly — every nonzero exit either raises a RuntimeError naming the
cause and the fix, or (the one documented non-error case) is absorbed:
under --skip-text, a document where every page already has text is not a
failure — ocrmypdf's own already_done_ocr/PriorOcrFoundError path is only
reachable in its *default* mode (no mode flag), which this module never
uses; empirically (v17.10.0) --skip-text with an all-text document just
exits 0 and produces a pass-through copy. This module still guards the
already_done_ocr exit code defensively for --skip-text: if it is ever hit,
that means ocrmypdf declined to produce output for exactly this reason, so
we return the input path unchanged and note it on stderr, rather than
raising.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from contract import OcrMode

# ocrmypdf.exceptions.ExitCode, transcribed (subprocess boundary — no import).
_EXIT_OK = 0
_EXIT_BAD_ARGS = 1
_EXIT_INPUT_FILE = 2
_EXIT_MISSING_DEPENDENCY = 3
_EXIT_INVALID_OUTPUT_PDF = 4
_EXIT_FILE_ACCESS_ERROR = 5
_EXIT_ALREADY_DONE_OCR = 6
_EXIT_CHILD_PROCESS_ERROR = 7
_EXIT_ENCRYPTED_PDF = 8
_EXIT_INVALID_CONFIG = 9
_EXIT_PDFA_CONVERSION_FAILED = 10
_EXIT_OTHER_ERROR = 15
_EXIT_CTRL_C = 130


def _exit_code_error(code: int, mode: OcrMode, pdf_path: str, languages: str,
                     output_path: Path, stderr: str) -> RuntimeError:
    """Build a RuntimeError naming the cause and the fix for a given
    ocrmypdf exit code. `stderr` is appended verbatim so nothing ocrmypdf
    reported is lost."""
    detail = stderr.strip()
    tail = f"\n\nocrmypdf stderr:\n{detail}" if detail else ""

    if code == _EXIT_BAD_ARGS:
        msg = (f"ocrmypdf rejected its own invocation (exit {code}/bad_args) "
               f"— this is a bug in ocr.py's argument construction, not "
               f"something the caller can fix.")
    elif code == _EXIT_INPUT_FILE:
        msg = (f"ocrmypdf could not read '{pdf_path}' (exit {code}/input_file) "
               f"— the file may be corrupt, an unsupported image format, or "
               f"(under --force-ocr) structurally unrasterizable. Fix: "
               f"inspect the file with `qpdf --check {pdf_path}` and repair "
               f"or re-source it.")
    elif code == _EXIT_MISSING_DEPENDENCY:
        msg = (f"ocrmypdf is missing a dependency for languages='{languages}' "
               f"(exit {code}/missing_dependency) — most commonly a "
               f"tesseract language pack that isn't installed. Fix: "
               f"`brew install tesseract-lang` (installs all packs) or "
               f"install the specific traineddata, then confirm with "
               f"`tesseract --list-langs`.")
    elif code == _EXIT_INVALID_OUTPUT_PDF:
        msg = (f"ocrmypdf produced an invalid output PDF (exit "
               f"{code}/invalid_output_pdf). Fix: re-run with -v 1 for "
               f"detail; this can indicate a Ghostscript/PDF post-processing "
               f"problem independent of --output-type pdf.")
    elif code == _EXIT_FILE_ACCESS_ERROR:
        msg = (f"ocrmypdf could not write to '{output_path}' (exit "
               f"{code}/file_access_error). Fix: check that '{output_path.parent}' "
               f"is writable and has free disk space.")
    elif code == _EXIT_ALREADY_DONE_OCR:
        # Only reachable here if ocrmypdf's default-mode PriorOcrFoundError
        # path is somehow hit despite an explicit mode flag; treated as an
        # error for REDO/FORCE (those modes are supposed to handle prior
        # OCR themselves), but see run_ocr()'s SKIP_TEXT special-case above
        # this function — that path never reaches _exit_code_error at all.
        msg = (f"ocrmypdf reported prior OCR it could not reconcile with "
               f"--{mode.name.lower()} (exit {code}/already_done_ocr). Fix: "
               f"try OcrMode.REDO (--redo-ocr) to strip and replace the "
               f"existing OCR layer, or OcrMode.FORCE (--force-ocr) to "
               f"discard it outright.")
    elif code == _EXIT_CHILD_PROCESS_ERROR:
        msg = (f"a subprocess ocrmypdf depends on (tesseract/ghostscript/"
               f"qpdf) failed unexpectedly (exit {code}/child_process_error).")
    elif code == _EXIT_ENCRYPTED_PDF:
        msg = (f"'{pdf_path}' is encrypted (exit {code}/encrypted_pdf) — OCR "
               f"requires the encryption removed first. Fix: "
               f"`qpdf --decrypt [--password=PASSWORD] {pdf_path} "
               f"decrypted.pdf` then re-run OCR on decrypted.pdf.")
    elif code == _EXIT_INVALID_CONFIG:
        msg = (f"a tesseract config could not be parsed (exit "
               f"{code}/invalid_config).")
    elif code == _EXIT_PDFA_CONVERSION_FAILED:
        msg = (f"PDF/A conversion failed (exit "
               f"{code}/pdfa_conversion_failed) — unexpected since ocr.py "
               f"always passes --output-type pdf; check that Ghostscript is "
               f"installed and functional (`brew install ghostscript`).")
    elif code == _EXIT_CTRL_C:
        msg = f"ocrmypdf was interrupted (exit {code}/ctrl_c)."
    else:
        msg = (f"ocrmypdf failed for an unclassified reason (exit {code}"
               f"{'/other_error' if code == _EXIT_OTHER_ERROR else ''}).")
    return RuntimeError(msg + tail)


def run_ocr(pdf_path: str, mode: OcrMode, out_dir: str, languages: str = "eng") -> str:
    """Run ocrmypdf on `pdf_path` with the given mode; return the path of
    the produced text-layer PDF.

    Every nonzero ocrmypdf exit either raises RuntimeError (cause + fix) or,
    for the one documented non-error case — --skip-text on a document where
    every page already has text — returns `pdf_path` unchanged with a note
    on stderr. Never silently swallows a nonzero exit."""
    if shutil.which("ocrmypdf") is None:
        raise RuntimeError(
            "ocrmypdf is not on PATH (exit n/a/missing_dependency). Fix: "
            "`brew install ocrmypdf tesseract-lang`."
        )

    src = Path(pdf_path)
    if not src.is_file():
        raise RuntimeError(f"ocr.py: no such file '{pdf_path}'")

    out_dir_path = Path(out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)
    output_path = out_dir_path / f"{src.stem}.ocr.pdf"

    cmd = [
        "ocrmypdf", mode.value,
        "-l", languages,
        "--output-type", "pdf",
        "-q",
        str(src), str(output_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)

    if proc.returncode == _EXIT_OK:
        return str(output_path)

    if mode == OcrMode.SKIP_TEXT and proc.returncode == _EXIT_ALREADY_DONE_OCR:
        # Documented non-error: every page already had text, so ocrmypdf
        # declined to produce output under --skip-text. Not a failure —
        # the input already has a usable text layer.
        print(
            f"ocr.py: '{pdf_path}' — every page already has text; "
            f"--skip-text produced no output, returning input unchanged.",
            file=sys.stderr,
        )
        return str(src)

    raise _exit_code_error(proc.returncode, mode, pdf_path, languages,
                           output_path, proc.stderr)


# --------------------------------------------------------------------- self-test

if __name__ == "__main__":
    import tempfile

    fixtures = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
    scanned = fixtures / "probe-scanned.pdf"
    simple = fixtures / "probe-simple.pdf"

    pdftotext = shutil.which("pdftotext")
    if pdftotext is None:
        sys.exit("self-test requires pdftotext on PATH (brew install poppler)")

    with tempfile.TemporaryDirectory(prefix="ocr-selftest-") as tmp:
        print(f"[1/2] SKIP_TEXT on image-only scan: {scanned}")
        result_path = run_ocr(str(scanned), OcrMode.SKIP_TEXT, tmp)
        text = subprocess.run([pdftotext, result_path, "-"],
                              capture_output=True, text=True, check=True).stdout
        assert "Quarterly" in text, (
            f"expected 'Quarterly' in OCR'd text, got:\n{text!r}"
        )
        print(f"    OK — output: {result_path}")
        print(f"    OCR'd text contains 'Quarterly':\n{text}")

        print(f"[2/2] SKIP_TEXT on all-text-pages doc: {simple}")
        result_path2 = run_ocr(str(simple), OcrMode.SKIP_TEXT, tmp)
        text2 = subprocess.run([pdftotext, result_path2, "-"],
                               capture_output=True, text=True, check=True).stdout
        assert text2.strip(), "expected non-empty text from the pass-through/OCR'd output"
        print(f"    OK — output: {result_path2} (pass-through or no-op; "
              f"input unchanged path == {result_path2 == str(simple)})")

    print("\nself-test passed")
