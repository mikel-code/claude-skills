# /// script
# requires-python = ">=3.10"
# dependencies = ["trafilatura==2.2.0"]
# ///
"""Tier 1 — static fetch + trafilatura extraction.

`fetch_static()` shells out to `curl` (the one checked non-pip dependency for
this tier — no python-requests) and hands the decoded body to
`extract_from_html()`, the offline-testable extraction core shared with
tier_chrome.py (which renders via headless Chrome instead of curl but reuses
this same function on the resulting DOM).

Design notes (see references/design.md, "Tier 1 — static fetch + trafilatura"):
  - curl flags: -L (follow redirects), a realistic desktop-Chrome UA,
    --compressed (gzip/br negotiation), --connect-timeout/--max-time.
  - HTTP status is captured via curl's `-w '%{http_code}'` trick: body goes
    to a temp file via `-o`, so stdout carries *only* the status code — no
    header/body parsing needed. file:// URLs (used by the offline self-test
    and accepted per contract) have no HTTP status; curl reports "000" for
    non-HTTP protocols and that is normalized to None here.
  - Only transport-level failures raise TierError(FETCH_ERROR): curl exiting
    non-zero (DNS, TLS, connection refused, timeout, unreadable file://
    path) or a technically-successful fetch that produced an empty body.
    HTTP >= 400 with a body is returned as a normal TierOutput — the sanity
    gate (gate.py), not this module, decides HTTP_BLOCKED.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

import trafilatura

from contract import GateVerdict, Tier, TierError, TierOutput

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
CONNECT_TIMEOUT = 10  # seconds; capped to the caller's total timeout if lower


# ------------------------------------------------------------ raw-HTML pass
#
# raw_text_len / page_title need the RAW page's visible text, computed
# without trafilatura's boilerplate removal — that's the whole point (the
# gate compares extracted-text mass against this to smell an app shell).
# Method: a stdlib html.parser.HTMLParser pass that drops <script>/<style>
# subtrees and collects every other text node; length is the
# whitespace-collapsed character count. This is a coarse "tags stripped"
# approximation, not a real CSS-visibility check (display:none content still
# counts, as does e.g. content in <noscript>) — adequate for an
# order-of-magnitude ratio check, not for anything more precise.

class _VisibleTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self._text_chunks: list[str] = []
        self._title_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style"):
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_chunks.append(data)
        elif self._skip_depth == 0:
            self._text_chunks.append(data)

    @property
    def visible_text(self) -> str:
        return re.sub(r"\s+", " ", "".join(self._text_chunks)).strip()

    @property
    def title(self) -> str:
        return re.sub(r"\s+", " ", "".join(self._title_chunks)).strip()


def _raw_page_scan(html: str) -> tuple[int, str | None]:
    parser = _VisibleTextExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass  # malformed markup: keep whatever was parsed before the error
    return len(parser.visible_text), (parser.title or None)


# --------------------------------------------------------------- extraction

def extract_from_html(html: str, url: str, tier: Tier = Tier.STATIC,
                       http_status: int | None = None) -> TierOutput:
    """Offline-testable core: raw HTML string -> TierOutput.

    Never raises for extraction failures — trafilatura returning None (no
    extractable content) becomes markdown="" and the gate is left to call it
    EMPTY. Metadata extraction is similarly best-effort: an unparseable
    document yields None fields rather than an exception.
    """
    raw_text_len, page_title = _raw_page_scan(html)

    try:
        markdown = trafilatura.extract(
            html, url=url, output_format="markdown", with_metadata=False,
            include_links=True, include_images=True, include_tables=True,
        )
    except Exception:
        markdown = None
    markdown = markdown or ""

    title = author = date = description = None
    try:
        meta = trafilatura.extract_metadata(html, default_url=url)
    except Exception:
        meta = None
    if meta is not None:
        title = meta.title or None
        author = meta.author or None
        date = meta.date or None
        description = meta.description or None

    return TierOutput(
        tier=tier, markdown=markdown, title=title, author=author, date=date,
        description=description, http_status=http_status,
        raw_text_len=raw_text_len, page_title=page_title,
    )


# -------------------------------------------------------------------- fetch

def _decode_html(raw: bytes, header_charset: str | None = None) -> str:
    """Decode a curl response body to str without adding a chardet-style
    dependency (curl is the only checked non-pip dependency for this tier).
    Method: try strict UTF-8 (the modern-web default) first; on failure, use
    the charset the server declared in its Content-Type header (captured via
    curl -w '%{content_type}' — legacy JP/CN sites often declare Shift-JIS/
    GBK there and nowhere in the markup); failing that, sniff a declared
    charset from a <meta charset=...> tag in the first 4 KB; if everything
    fails, fall back to UTF-8 with replacement characters rather than
    raising. The gate's GARBLED check catches the replacement-heavy case
    downstream — this function is the first line, not the last.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    if header_charset:
        try:
            return raw.decode(header_charset)
        except (LookupError, UnicodeDecodeError):
            pass
    m = re.search(rb'charset=["\']?\s*([\w-]+)', raw[:4096], re.IGNORECASE)
    if m:
        try:
            return raw.decode(m.group(1).decode("ascii", "ignore"))
        except (LookupError, UnicodeDecodeError):
            pass
    return raw.decode("utf-8", errors="replace")


def fetch_static(url: str, timeout: int = 30) -> TierOutput:
    """curl -> decode -> extract_from_html. Raises TierError(FETCH_ERROR) for
    transport failures or a body-less response; HTTP >= 400 with a body is
    returned normally (the gate decides HTTP_BLOCKED)."""
    is_file = url.startswith("file://")
    fd, body_path_str = tempfile.mkstemp(prefix="url-to-markdown-static-")
    os.close(fd)
    body_path = Path(body_path_str)

    try:
        flags = [
            "-sS", "--compressed", "-A", DEFAULT_UA,
            "--connect-timeout", str(min(CONNECT_TIMEOUT, timeout)),
            "--max-time", str(timeout),
        ]
        if not is_file:
            flags.append("-L")  # follow redirects; no-op/harmless for file://
        # stdout carries "<status>\t<content-type>" (body is in the temp
        # file), so both the status and any header-declared charset survive.
        cmd = ["curl", *flags, "-o", str(body_path),
               "-w", "%{http_code}\t%{content_type}", url]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                   timeout=timeout + 5)
        except subprocess.TimeoutExpired as exc:
            raise TierError(GateVerdict.FETCH_ERROR,
                             f"curl timed out after {timeout}s: {url}") from exc

        if proc.returncode != 0:
            detail = proc.stderr.strip() or f"curl exit code {proc.returncode}"
            raise TierError(GateVerdict.FETCH_ERROR, f"curl failed for {url}: {detail}")

        status_str, _, content_type = proc.stdout.strip().partition("\t")
        http_status = (int(status_str)
                        if status_str.isdigit() and status_str != "000"
                        else None)
        cm = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
        header_charset = cm.group(1) if cm else None

        body = body_path.read_bytes()
        if not body:
            raise TierError(GateVerdict.FETCH_ERROR,
                             f"empty response body for {url}",
                             http_status=http_status)
    finally:
        body_path.unlink(missing_ok=True)

    html = _decode_html(body, header_charset=header_charset)
    return extract_from_html(html, url, tier=Tier.STATIC, http_status=http_status)


# --------------------------------------------------------------- self-test

def _self_test() -> None:
    import json

    failures: list[str] = []

    def check(label: str, cond: bool, extra: str = "") -> None:
        status = "ok" if cond else "FAIL"
        print(f"  [{status}] {label}" + (f" — {extra}" if extra and not cond else ""))
        if not cond:
            failures.append(label)

    # (a) extract_from_html on an inline sample: heading, paragraphs, a
    # table, and a nav bar that must NOT survive extraction.
    print("(a) extract_from_html — inline sample article")
    sample_html = """<!doctype html>
<html lang="en">
<head>
  <title>The Sample Article</title>
  <meta name="author" content="Ada Lovelace">
  <meta name="description" content="A short article used for testing.">
  <meta name="date" content="2026-01-15">
</head>
<body>
  <nav>
    <a href="/">Home</a> | <a href="/about">About</a> | <a href="/contact">Contact</a>
    <span>Subscribe now for exclusive nav-bar content!</span>
  </nav>
  <article>
    <h1>The Sample Article</h1>
    <p>This is the first paragraph of a genuinely long-enough article body,
       written so that trafilatura's content heuristics have something
       substantial to latch onto instead of discarding it as boilerplate.</p>
    <p>This is a second paragraph, continuing the article with more prose
       so the extractor sees a coherent block of real body content rather
       than a single short line that might get pruned.</p>
    <table>
      <tr><th>Year</th><th>Value</th></tr>
      <tr><td>2024</td><td>42</td></tr>
      <tr><td>2025</td><td>47</td></tr>
    </table>
  </article>
  <footer>Copyright notice, unrelated to article body.</footer>
</body>
</html>"""

    out = extract_from_html(sample_html, "https://example.org/sample-article")
    check("title extracted", out.title == "The Sample Article", repr(out.title))
    check("author extracted", out.author == "Ada Lovelace", repr(out.author))
    check("date extracted", out.date == "2026-01-15", repr(out.date))
    check("description extracted",
          out.description == "A short article used for testing.", repr(out.description))
    check("page_title (raw <title>) captured", out.page_title == "The Sample Article",
          repr(out.page_title))
    check("nav bar dropped from markdown", "Subscribe now" not in out.markdown, out.markdown)
    check("nav links dropped from markdown", "Home" not in out.markdown.split("\n")[0:2],
          "nav leaked near top")
    check("table survived extraction", "42" in out.markdown and "47" in out.markdown, out.markdown)
    check("body prose present", "long-enough article body" in out.markdown, out.markdown)
    check("raw_text_len counts more than extracted (nav+footer included)",
          out.raw_text_len is not None and out.raw_text_len > len(out.markdown),
          f"raw_text_len={out.raw_text_len} markdown_len={len(out.markdown)}")
    check("http_status is None (not supplied)", out.http_status is None)
    check("tier defaults to STATIC", out.tier == Tier.STATIC)

    # extract_from_html must not raise on unextractable content.
    empty_out = extract_from_html("<html><body></body></html>", "https://example.org/empty")
    check("empty page -> markdown='' (no raise)", empty_out.markdown == "")

    # (b) fetch_static on a file:// URL.
    print("(b) fetch_static — file:// URL")
    scratch = Path(__file__).resolve().parent
    fixture = scratch / "_fixture.html"
    fixture.write_text("""<!doctype html>
<html><head><title>Local Fixture</title>
<meta name="author" content="Grace Hopper"></head>
<body><article><h1>Local Fixture</h1>
<p>Content served from a local file:// URL, long enough for trafilatura
to treat it as a real article body rather than noise to discard.</p>
</article></body></html>""", encoding="utf-8")
    try:
        file_out = fetch_static(f"file://{fixture}")
        check("file:// fetch succeeds", file_out.markdown != "")
        check("file:// http_status is None (no HTTP semantics)",
              file_out.http_status is None, str(file_out.http_status))
        check("file:// title extracted", file_out.title == "Local Fixture", repr(file_out.title))
        check("file:// content present", "local file" in file_out.markdown.lower(),
              file_out.markdown)

        missing_raised = False
        try:
            fetch_static(f"file://{scratch}/_does_not_exist.html")
        except TierError as exc:
            missing_raised = exc.verdict == GateVerdict.FETCH_ERROR
        check("missing file:// path raises TierError(FETCH_ERROR)", missing_raised)
    finally:
        fixture.unlink(missing_ok=True)

    # (c) LIVE — cheap network check, clearly labeled.
    print("(c) LIVE — fetch_static(https://example.com)")
    try:
        live_out = fetch_static("https://example.com", timeout=15)
        check("LIVE http_status == 200", live_out.http_status == 200,
              str(live_out.http_status))
        check("LIVE markdown contains 'documentation'",
              "documentation" in live_out.markdown.lower(), live_out.markdown)
    except TierError as exc:
        check("LIVE fetch_static(https://example.com)", False,
              f"TierError({exc.verdict}): {exc.detail} — network unavailable in this sandbox?")

    print()
    if failures:
        print(f"SELF-TEST FAILED: {len(failures)} check(s): {json.dumps(failures)}", file=sys.stderr)
        sys.exit(1)
    print("SELF-TEST: all checks passed")


if __name__ == "__main__":
    _self_test()
