# /// script
# requires-python = ">=3.10"
# dependencies = ["trafilatura==2.2.0"]
# ///
"""Tier 2 — local JS rendering via the user's installed Chrome.

`fetch_chrome` shells out to headless Chrome with `--dump-dom` and hands the
resulting HTML to `tier_static.extract_from_html` — the ONE allowed
cross-module import besides contract.py (per contract.py's module-entry
signature notes). This module never reimplements extraction; it only differs
from Tier 1 in *how the HTML is obtained* (a rendered DOM instead of a raw
response body), which is exactly what lets it recover content that static
fetch misses on JS-heavy pages.

This module declares `trafilatura` in its PEP 723 block (even though it is
only imported transitively, via tier_static) so `uv run` resolves the full
import chain when this file is invoked directly.

Chrome gives no HTTP status over `--dump-dom` (it's a local process, not an
HTTP client) — TierOutput.http_status is always None from this tier; the
gate must not depend on it here.

Chrome absence/failure is a gate-fail, not a hard error: per design.md's
"Chrome flag churn" risk, any tool-level failure (binary missing, timeout,
launch failure) raises TierError(FETCH_ERROR, ...) so the orchestrator moves
on to Tier 3 (Jina) instead of aborting the whole ladder. A Chrome run that
*succeeds* but renders nothing (empty DOM, JS error, 404 page) is NOT an
error at this layer — it is handed to extract_from_html like any other HTML
and the sanity gate decides its fate, per design.md's gate philosophy.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time

from contract import GateVerdict, Tier, TierError, TierOutput
from tier_static import extract_from_html

# Default Chrome location on this machine (verified present, v151). Chrome
# ships no stable cross-platform binary name, so macOS gets a hardcoded
# .app path and other platforms fall back to PATH lookups below.
_DEFAULT_MACOS_CHROME = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
)

# PATH fallbacks tried in order when the macOS default isn't there (Linux
# Chrome/Chromium package names; also covers a non-standard macOS install).
_PATH_FALLBACKS = ("google-chrome", "google-chrome-stable", "chromium",
                    "chromium-browser")


def _find_chrome() -> str:
    """Locate a Chrome/Chromium binary, or raise TierError(FETCH_ERROR).

    Discovery order: env override -> verified macOS default -> PATH lookups.
    An explicit env override that doesn't resolve to a real executable is
    treated as a hard miss (no silent fallthrough to the default binary) —
    that's what lets a test point at a nonexistent path and get a
    deterministic FETCH_ERROR.
    """
    override = os.environ.get("URL_TO_MARKDOWN_CHROME")
    if override:
        if os.path.isfile(override) and os.access(override, os.X_OK):
            return override
        raise TierError(
            GateVerdict.FETCH_ERROR,
            f"chrome not found: URL_TO_MARKDOWN_CHROME={override!r} is not "
            "an executable file",
        )

    if os.path.isfile(_DEFAULT_MACOS_CHROME) and os.access(
        _DEFAULT_MACOS_CHROME, os.X_OK
    ):
        return _DEFAULT_MACOS_CHROME

    for name in _PATH_FALLBACKS:
        found = shutil.which(name)
        if found:
            return found

    raise TierError(
        GateVerdict.FETCH_ERROR,
        "chrome not found — install Google Chrome, or set "
        "URL_TO_MARKDOWN_CHROME to a Chrome/Chromium binary",
    )


def _dump_dom(chrome_path: str, url: str, timeout: int) -> str:
    """Run headless Chrome against `url` and return the dumped DOM as HTML.

    Kept as the single function that shells out to Chrome (design.md risk:
    `--headless --dump-dom` flag semantics have shifted across major Chrome
    versions — e.g. the "new" vs "old" headless mode split — so a future fix
    for a broken invocation should only ever need to touch this function).

    Flags:
      --headless --disable-gpu --dump-dom   core rendering + DOM dump
      --no-first-run --disable-extensions   keep the run hermetic
      --mute-audio                          quiet (autoplay media on pages)
      --user-data-dir=<throwaway>           isolated, fresh profile; no
                                             cookies/state persist or leak
                                             between invocations
    stderr (Chrome's DevTools/GCM logging noise) is deliberately discarded,
    never inspected — it is not a reliable success/failure signal.
    """
    user_data_dir = tempfile.mkdtemp(prefix="url-to-markdown-chrome-")
    try:
        cmd = [
            chrome_path,
            "--headless",
            "--disable-gpu",
            "--no-first-run",
            "--disable-extensions",
            "--mute-audio",
            f"--user-data-dir={user_data_dir}",
            "--dump-dom",
            url,
        ]
        # Empirically observed on this machine (Chrome 151, both new and
        # --headless=old): --dump-dom writes the full DOM to stdout in well
        # under a second, but the Chrome process itself does not then exit —
        # it hangs until killed. A plain subprocess.run(timeout=N) would
        # therefore burn the full budget on EVERY call. Instead: stream
        # stdout non-blocking and stop as soon as the serialized document is
        # complete (--dump-dom emits exactly one document, terminated by
        # </html>), killing Chrome ourselves. Deadline only bites for pages
        # that genuinely never finish rendering.
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL)
        except OSError as exc:
            raise TierError(
                GateVerdict.FETCH_ERROR, f"chrome failed to start: {exc}"
            )
        fd = proc.stdout.fileno()
        os.set_blocking(fd, False)
        chunks: list[bytes] = []
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    chunk = os.read(fd, 65536)
                except BlockingIOError:
                    chunk = None
                if chunk:
                    chunks.append(chunk)
                    tail = b"".join(chunks[-2:]).rstrip().lower()
                    if tail.endswith(b"</html>"):
                        break
                elif chunk == b"" or proc.poll() is not None:
                    break  # EOF or Chrome exited on its own
                if time.monotonic() > deadline:
                    break
                if chunk is None:
                    time.sleep(0.05)
        finally:
            proc.kill()
            proc.wait()
        html = b"".join(chunks).decode("utf-8", errors="replace")
        if not html.strip():
            raise TierError(GateVerdict.FETCH_ERROR, "chrome render timeout")
        # No returncode check by design: --dump-dom still writes whatever
        # DOM Chrome ended up with even on navigation failure (e.g. an
        # internal chrome-error:// page), and an empty/error DOM is exactly
        # what extract_from_html + the sanity gate are for — not this layer.
        return html
    finally:
        shutil.rmtree(user_data_dir, ignore_errors=True)


def fetch_chrome(url: str, timeout: int = 60) -> TierOutput:
    """Tier 2: render `url` in headless Chrome, extract via tier_static.

    file:// URLs work unmodified (Chrome navigates them like any other URL;
    exercised directly by this module's self-test).
    """
    chrome_path = _find_chrome()
    html = _dump_dom(chrome_path, url, timeout)
    # Chrome's --dump-dom gives no HTTP status (it's not an HTTP client
    # response) -> http_status stays None, extract_from_html's default.
    return extract_from_html(html, url, tier=Tier.CHROME)


if __name__ == "__main__":
    import pathlib
    import urllib.request

    failures = []

    # (a) JS-injection: static body is an empty div + noscript warning;
    # visible content only exists after a <script> tag sets innerHTML. Only
    # a real rendering engine (not a static fetch) can surface the sentinel.
    scratch_dir = pathlib.Path(
        "/private/tmp/claude-501/-Users-mikel-code-claude-skills/"
        "7f58af9e-0a37-43b4-bc24-18878862d24a/scratchpad/chrome"
    )
    scratch_dir.mkdir(parents=True, exist_ok=True)
    sentinel = "SENTINEL_TEXT_RENDERED_BY_JS_1234"
    js_fixture = scratch_dir / "js_injection_fixture.html"
    js_fixture.write_text(
        "<!doctype html>\n"
        "<html><head><title>JS Injection Test</title></head>\n"
        "<body>\n"
        '<div id="root"></div>\n'
        "<noscript>JavaScript is required to view this page.</noscript>\n"
        "<script>\n"
        "document.getElementById('root').innerHTML = "
        "'<article><h1>JS Injection Test</h1>"
        f"<p>{sentinel} This paragraph proves headless Chrome executed "
        "JavaScript and injected content into the document before "
        "dump-dom captured it. This sentence and the next exist purely "
        "to give the extractor enough prose to treat this as a real "
        "article rather than boilerplate, since very short bodies are "
        "sometimes discarded by content-extraction heuristics as noise "
        "rather than genuine content.</p></article>';\n"
        "</script>\n"
        "</body></html>\n",
        encoding="utf-8",
    )

    print("(a) JS-injection rendering test...")
    try:
        # Short timeout: on this machine Chrome writes --dump-dom output in
        # well under a second but then hangs rather than exiting (see the
        # TimeoutExpired handler in _dump_dom); a short timeout still
        # recovers the completed dump via that path, it just avoids
        # burning the full 60s default per self-test call.
        out = fetch_chrome(js_fixture.as_uri(), timeout=15)
        if sentinel in out.markdown:
            print(f"    PASS — sentinel found, markdown tier={out.tier}")
        else:
            failures.append("(a) sentinel NOT found in extracted markdown")
            print(f"    FAIL — markdown was: {out.markdown!r}")
    except TierError as exc:
        failures.append(f"(a) unexpected TierError: {exc.verdict} {exc.detail}")
        print(f"    FAIL — {exc.verdict}: {exc.detail}")

    # (b) chrome-absent path: point the override at a path that can't exist.
    print("(b) chrome-absent path...")
    prior_override = os.environ.get("URL_TO_MARKDOWN_CHROME")
    os.environ["URL_TO_MARKDOWN_CHROME"] = "/nonexistent/definitely/not/chrome"
    try:
        try:
            fetch_chrome("https://example.com")
            failures.append("(b) fetch_chrome did not raise")
            print("    FAIL — no exception raised")
        except TierError as exc:
            if exc.verdict == GateVerdict.FETCH_ERROR:
                print(f"    PASS — TierError(FETCH_ERROR): {exc.detail}")
            else:
                failures.append(f"(b) wrong verdict: {exc.verdict}")
                print(f"    FAIL — wrong verdict: {exc.verdict}")
    finally:
        if prior_override is None:
            os.environ.pop("URL_TO_MARKDOWN_CHROME", None)
        else:
            os.environ["URL_TO_MARKDOWN_CHROME"] = prior_override

    # (c) cheap live check.
    print("(c) live check: https://example.com ...")
    try:
        # Confirm network is reachable at all before blaming this module.
        urllib.request.urlopen("https://example.com", timeout=10).close()
        out = fetch_chrome("https://example.com", timeout=15)
        if "documentation" in out.markdown.lower():
            print("    PASS — 'documentation' found in extracted markdown")
        else:
            failures.append("(c) 'documentation' NOT found in markdown")
            print(f"    FAIL — markdown was: {out.markdown!r}")
    except TierError as exc:
        failures.append(f"(c) unexpected TierError: {exc.verdict} {exc.detail}")
        print(f"    FAIL — {exc.verdict}: {exc.detail}")
    except Exception as exc:  # network unreachable etc. — not this module's bug
        print(f"    SKIP — live network check unavailable: {exc}")

    print()
    if failures:
        print(f"{len(failures)} failure(s):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("all self-tests passed")
