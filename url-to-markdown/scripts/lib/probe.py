"""Tier 0 — probe: classify a URL before the tier ladder runs.

`probe()` answers exactly one question: is this URL an HTML page (proceed
with the ladder), a PDF or office file (hand off to the sibling skill), or
some other binary (refuse)? See references/design.md, "Tier 0 — probe".

Strategy, cheapest-first:
  1. `curl -sIL` (HEAD, follow redirects) for Content-Type + the final URL
     after redirects. Cheap — no body transferred.
  2. If the server rejected/lied about the HEAD (non-2xx, or an ambiguous
     Content-Type like `application/octet-stream`), or HEAD failed outright,
     fall back to a ranged GET (`-r 0-2047`) and sniff magic bytes from the
     first ~2KB. This is the "servers lie or reject HEAD" case the task
     calls out explicitly — some servers 405 on HEAD, others report a
     generic binary type for everything regardless of what it actually is.
  3. Only PDF/OFFICE kinds trigger a full download (to `download_dir`, for
     handoff to pdf-to-markdown / doc-to-markdown); HTML/OTHER never
     download anything here — the ladder's own tiers do the real fetch.

file:// URLs skip curl entirely and classify straight off the local file's
magic bytes/extension; the "download" for a PDF/OFFICE file:// URL is a
no-op — `downloaded_path` just points at the existing local file instead of
copying it into `download_dir`.

Network-failure policy (deliberate, not an oversight): if BOTH the HEAD and
the ranged-GET sniff fail outright (DNS failure, connection refused, total
timeout — no response at all), `probe()` does NOT raise. It returns
ProbeKind.HTML so the ladder proceeds to Tier 1 (`fetch_static`), which hits
the exact same failure and reports it through the real TierError/gate path
(curl's actual stderr, a proper FETCH_ERROR verdict, tier provenance) —
duplicating that error-handling here would just produce a second, worse
version of it. A probe-level exception would also be a layering violation:
per contract.py, only tier modules raise TierError, and probe.py isn't one.
"""

from __future__ import annotations

import mimetypes
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

from contract import ProbeKind, ProbeResult

# Same value as tier_static.DEFAULT_UA — duplicated rather than imported so
# probe.py stays self-contained (contract.py's module-entry list documents
# exactly one allowed cross-module import, tier_chrome -> tier_static, and
# probe.py isn't part of that).
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

HEAD_TIMEOUT = 12       # HEAD should be near-instant; short budget on purpose
SNIFF_TIMEOUT = 15      # ranged GET fallback — still cheap (~2KB)
DOWNLOAD_TIMEOUT = 90   # full-file download for PDF/OFFICE handoff
SNIFF_BYTES = 2048

_AMBIGUOUS_CONTENT_TYPES = {
    "", "application/octet-stream", "binary/octet-stream",
    "application/unknown", "application/force-download",
}

_OFFICE_MIME_PREFIXES = (
    "application/vnd.openxmlformats-officedocument.",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.oasis.opendocument.",
    "application/rtf",
    "text/rtf",
    "application/epub+zip",
    "text/csv",
)

_HTML_MIME_PREFIXES = ("text/html", "application/xhtml+xml")

_OFFICE_EXTENSIONS = {
    ".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt",
    ".odt", ".ods", ".odp", ".rtf", ".epub", ".csv",
}

_MIME_EXTENSION_OVERRIDES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/msword": ".doc",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.oasis.opendocument.presentation": ".odp",
    "application/rtf": ".rtf",
    "text/rtf": ".rtf",
    "application/epub+zip": ".epub",
    "text/csv": ".csv",
}

_PDF_MAGIC = b"%PDF-"
_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


# ------------------------------------------------------------- small helpers

def _url_extension(url_or_path: str) -> str:
    path = urlparse(url_or_path).path or url_or_path
    return os.path.splitext(path)[1].lower()


def _content_type_key(content_type: str | None) -> str:
    if not content_type:
        return ""
    return content_type.split(";", 1)[0].strip().lower()


def _looks_texty(sample: bytes) -> bool:
    """Coarse binary/text sniff on a short byte sample: no NUL bytes (binary
    formats routinely have them; text doesn't) and mostly printable
    ASCII/whitespace. Good enough to route "some kind of text, not a format
    we recognize" toward HTML (ambiguous-but-texty) rather than OTHER."""
    if not sample or b"\x00" in sample:
        return False
    text_bytes = bytes(range(0x20, 0x7F)) + b"\n\r\t\x0b\x0c"
    printable = sum(1 for b in sample if b in text_bytes)
    return printable / len(sample) > 0.85


# -------------------------------------------------------- kind classification

def _classify_mime(content_type: str | None) -> ProbeKind | None:
    """Content-Type alone -> ProbeKind, or None when ambiguous (caller must
    fall back to magic-byte sniffing)."""
    key = _content_type_key(content_type)
    if not key or key in _AMBIGUOUS_CONTENT_TYPES:
        return None
    if key == "application/pdf":
        return ProbeKind.PDF
    if key.startswith(_OFFICE_MIME_PREFIXES):
        return ProbeKind.OFFICE
    if key.startswith(_HTML_MIME_PREFIXES):
        return ProbeKind.HTML
    if key.startswith("text/"):
        return ProbeKind.HTML  # plain text, markdown, etc. — texty enough
    return None  # some other binary-ish type — confirm via magic bytes


def _classify_magic(magic: bytes, url_or_path: str) -> ProbeKind:
    """Magic bytes (+ extension where the format needs it) -> ProbeKind.
    Always returns a kind — this is the last stop before defaulting to
    HTML, so it never returns None."""
    if magic.startswith(_PDF_MAGIC):
        return ProbeKind.PDF

    ext = _url_extension(url_or_path)

    if any(magic.startswith(sig) for sig in _ZIP_MAGICS):
        # Zip container: docx/xlsx/pptx/odt/ods/odp/epub are all zip-based.
        # A bare zip without a recognized office/epub extension is a plain
        # archive, not a document we know how to hand off.
        return ProbeKind.OFFICE if ext in _OFFICE_EXTENSIONS else ProbeKind.OTHER

    if magic.startswith(_OLE_MAGIC):
        # Legacy OLE compound file — virtually always legacy MS Office in
        # practice, but require the extension to agree before calling it
        # OFFICE, per the task's "magic + extension" rule.
        return ProbeKind.OFFICE if ext in _OFFICE_EXTENSIONS else ProbeKind.OTHER

    if ext == ".csv":
        # CSV is plain text with no magic bytes of its own — the extension
        # (having already ruled out zip/OLE/PDF above) is the whole signal.
        return ProbeKind.OFFICE

    if ext == ".rtf" or magic.lstrip().startswith(b"{\\rtf"):
        return ProbeKind.OFFICE

    if _looks_texty(magic):
        return ProbeKind.HTML  # ambiguous-but-texty default

    return ProbeKind.OTHER


# ----------------------------------------------------------------- transport

def _run_curl(cmd: list[str], timeout: int) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    except (subprocess.TimeoutExpired, OSError):
        return None


def _head(url: str, timeout: int = HEAD_TIMEOUT) -> tuple[int | None, str, str | None] | None:
    """curl -sIL -> (http_code, final_url, content_type), or None if curl
    itself failed (nonzero exit, timeout, not found)."""
    cmd = [
        "curl", "-sIL", "-A", DEFAULT_UA,
        "--connect-timeout", str(min(10, timeout)),
        "--max-time", str(timeout),
        "-o", os.devnull,
        "-w", "%{http_code} %{url_effective} %{content_type}",
        url,
    ]
    proc = _run_curl(cmd, timeout)
    if proc is None or proc.returncode != 0:
        return None
    parts = proc.stdout.strip().split(" ", 2)
    if len(parts) < 2:
        return None
    http_code = int(parts[0]) if parts[0].isdigit() else None
    final_url = parts[1]
    content_type = parts[2].strip() if len(parts) > 2 else ""
    return http_code, final_url, (content_type or None)


def _sniff(url: str, timeout: int = SNIFF_TIMEOUT
           ) -> tuple[int | None, str, str | None, bytes] | None:
    """Ranged GET (first ~2KB) -> (http_code, final_url, content_type,
    magic_bytes), or None if curl itself failed. Used when HEAD lied,
    was rejected, or never came back at all."""
    fd, tmp_path = tempfile.mkstemp(prefix="url-to-markdown-probe-sniff-")
    os.close(fd)
    try:
        cmd = [
            "curl", "-sL", "-A", DEFAULT_UA,
            "--connect-timeout", str(min(10, timeout)),
            "--max-time", str(timeout),
            "-r", f"0-{SNIFF_BYTES - 1}",
            "-o", tmp_path,
            "-w", "%{http_code} %{url_effective} %{content_type}",
            url,
        ]
        proc = _run_curl(cmd, timeout)
        if proc is None or proc.returncode != 0:
            return None
        data = Path(tmp_path).read_bytes()[:SNIFF_BYTES]
        parts = proc.stdout.strip().split(" ", 2)
        http_code = int(parts[0]) if parts and parts[0].isdigit() else None
        final_url = parts[1] if len(parts) > 1 else url
        content_type = parts[2].strip() if len(parts) > 2 else ""
        return http_code, final_url, (content_type or None), data
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _download_filename(final_url: str, content_type: str | None, kind: ProbeKind) -> str:
    path = urlparse(final_url).path
    name = os.path.basename(unquote(path))
    ext = os.path.splitext(name)[1]
    if name and ext:
        return name
    key = _content_type_key(content_type)
    guessed_ext = (_MIME_EXTENSION_OVERRIDES.get(key)
                   or mimetypes.guess_extension(key or "")
                   or (".pdf" if kind == ProbeKind.PDF else ".bin"))
    stem = name or (urlparse(final_url).netloc or "download").replace(":", "_") or "download"
    return f"{stem}{guessed_ext}"


def _download_full(url: str, final_url: str, content_type: str | None, kind: ProbeKind,
                    download_dir: str) -> str | None:
    os.makedirs(download_dir, exist_ok=True)
    dest = os.path.join(download_dir, _download_filename(final_url, content_type, kind))
    cmd = [
        "curl", "-sL", "-A", DEFAULT_UA,
        "--connect-timeout", str(min(10, DOWNLOAD_TIMEOUT)),
        "--max-time", str(DOWNLOAD_TIMEOUT),
        "-o", dest, url,
    ]
    proc = _run_curl(cmd, DOWNLOAD_TIMEOUT)
    if proc is None or proc.returncode != 0:
        return None
    if not os.path.exists(dest) or os.path.getsize(dest) == 0:
        return None
    return dest


# --------------------------------------------------------------- file:// path

def _probe_file(url: str) -> ProbeResult:
    local_path = unquote(urlparse(url).path)
    try:
        with open(local_path, "rb") as fh:
            magic = fh.read(SNIFF_BYTES)
    except OSError:
        # Missing/unreadable local file: same "don't raise, let the real
        # fetch surface it" policy as the network-failure case below, even
        # though there's no network involved — fetch_static's file:// path
        # will hit the identical open() failure and report it properly.
        return ProbeResult(kind=ProbeKind.HTML, content_type=None,
                            final_url=url, downloaded_path=None)

    kind = _classify_magic(magic, local_path)
    content_type = mimetypes.guess_type(local_path)[0]
    # Already local — "downloading" is a no-op; point at the existing file
    # instead of copying it into download_dir.
    downloaded_path = local_path if kind in (ProbeKind.PDF, ProbeKind.OFFICE) else None
    return ProbeResult(kind=kind, content_type=content_type,
                        final_url=url, downloaded_path=downloaded_path)


# --------------------------------------------------------------------- probe

def probe(url: str, download_dir: str) -> ProbeResult:
    if url.startswith("file://"):
        return _probe_file(url)

    head = _head(url)
    http_code, final_url, content_type = (None, url, None) if head is None else head

    kind = _classify_mime(content_type)

    # Always sniff the first bytes, even when HEAD confidently said
    # text/html: servers lie, and an html-labeled PDF that slips past here
    # would end up converted by a remote reader without pdf-to-markdown's
    # verification pipeline — defeating Tier 0's whole purpose. The ranged
    # 2KB GET is cheap; magic bytes override a lying Content-Type.
    sniff = _sniff(url)
    if sniff is None:
        if head is None:
            # Total network failure on both the HEAD and the sniff GET —
            # see the module docstring's "Network-failure policy".
            return ProbeResult(kind=ProbeKind.HTML, content_type=None,
                                final_url=url, downloaded_path=None)
        # HEAD worked but the sniff GET failed — trust what HEAD gave us
        # (kind may still be None here, defaulted to HTML below).
    else:
        sniff_code, sniff_final_url, sniff_content_type, magic = sniff
        final_url = sniff_final_url or final_url
        if content_type is None:
            content_type = sniff_content_type
        if http_code is None:
            http_code = sniff_code
        magic_kind = _classify_magic(magic, final_url)
        # Magic bytes are ground truth for the binary kinds; they override a
        # lying text/html header. An HTML-ish sniff (no binary magic) never
        # overrides a *binary* HEAD verdict, since 2KB of preamble can look
        # texty.
        if magic_kind in (ProbeKind.PDF, ProbeKind.OFFICE):
            kind = magic_kind
        elif kind is None:
            kind = magic_kind

    if kind is None:
        kind = ProbeKind.HTML  # ambiguous-but-texty default, per spec

    downloaded_path = None
    if kind in (ProbeKind.PDF, ProbeKind.OFFICE):
        downloaded_path = _download_full(url, final_url, content_type, kind, download_dir)

    return ProbeResult(kind=kind, content_type=content_type,
                        final_url=final_url, downloaded_path=downloaded_path)


# --------------------------------------------------------------- self-test

if __name__ == "__main__":
    import sys
    import zipfile

    SCRATCH_DIR = Path(
        "/private/tmp/claude-501/-Users-mikel-code-claude-skills/"
        "7f58af9e-0a37-43b4-bc24-18878862d24a/scratchpad/probe-okf"
    )
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_DIR = SCRATCH_DIR / "downloads"

    failures: list[str] = []

    def check(label: str, cond: bool, extra: str = "") -> None:
        status = "ok" if cond else "FAIL"
        print(f"  [{status}] {label}" + (f" — {extra}" if extra and not cond else ""))
        if not cond:
            failures.append(label)

    # (a) file:// PDF fixture -> PDF kind
    print("(a) file:// PDF fixture")
    pdf_fixture = Path(
        "/Users/mikel/code/claude-skills/pdf-to-markdown/tests/fixtures/probe-simple.pdf"
    )
    result_a = probe(pdf_fixture.as_uri(), str(DOWNLOAD_DIR))
    check("kind == PDF", result_a.kind == ProbeKind.PDF, str(result_a.kind))
    check("downloaded_path points at the existing local file (no copy)",
          result_a.downloaded_path == str(pdf_fixture), str(result_a.downloaded_path))

    # (b) scratch .docx-like zip and .csv -> OFFICE
    print("(b) scratch .docx-like zip and .csv")
    docx_fixture = SCRATCH_DIR / "fake.docx"
    with zipfile.ZipFile(docx_fixture, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<document/>")
    result_b1 = probe(docx_fixture.as_uri(), str(DOWNLOAD_DIR))
    check("docx-like zip -> OFFICE", result_b1.kind == ProbeKind.OFFICE, str(result_b1.kind))
    check("docx downloaded_path set", result_b1.downloaded_path == str(docx_fixture))

    csv_fixture = SCRATCH_DIR / "data.csv"
    csv_fixture.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    result_b2 = probe(csv_fixture.as_uri(), str(DOWNLOAD_DIR))
    check("csv -> OFFICE", result_b2.kind == ProbeKind.OFFICE, str(result_b2.kind))

    # (c) LIVE, cheap: https://example.com -> HTML
    print("(c) LIVE: https://example.com")
    try:
        result_c = probe("https://example.com", str(DOWNLOAD_DIR))
        check("https://example.com -> HTML", result_c.kind == ProbeKind.HTML, str(result_c.kind))
        check("no download for HTML", result_c.downloaded_path is None)
    except Exception as exc:  # network unavailable in this sandbox, etc.
        print(f"    SKIP — live network check unavailable: {exc}")

    # (d) scratch .png -> OTHER
    print("(d) scratch .png")
    png_fixture = SCRATCH_DIR / "image.png"
    png_fixture.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    result_d = probe(png_fixture.as_uri(), str(DOWNLOAD_DIR))
    check("png -> OTHER", result_d.kind == ProbeKind.OTHER, str(result_d.kind))
    check("no download for OTHER", result_d.downloaded_path is None)

    print()
    if failures:
        print(f"{len(failures)} failure(s):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("all probe.py self-tests passed")
