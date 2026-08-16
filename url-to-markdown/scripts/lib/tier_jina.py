"""Tier 3 — Jina Reader, strictly keyless.

GET https://r.jina.ai/<url> with Accept: application/json and
X-No-Cache: true. No API key, ever: the design assumes the user has none,
and 20 requests/min/IP keyless is ample at this skill's volume — do not add
a JINA_API_KEY slot "just in case" (references/design.md, Tier 3 + Secrets
sections).

Transport: stdlib urllib, not a curl subprocess. Justification: Jina is the
one doing the actual outbound fetch of the target page (browser-like UA,
anti-bot posture, redirects are all its problem, not ours) — unlike Tier 1,
where curl's local fetch realism matters and is the repo's checked
dependency (deps.py). Here we're just a JSON-API client talking to r.jina.ai,
and urllib gives that directly: no subprocess/shell-escaping for headers or
URLs, no parsing a curl `-w` status-code sentinel back out of stdout, and
its HTTPError/URLError hierarchy maps cleanly onto the TierError branches
below. Pure stdlib, so (per deps.py's convention for stdlib-only modules)
this file carries no PEP 723 dependency block.
"""

from __future__ import annotations

import json
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request

from contract import GateVerdict, Tier, TierError, TierOutput

# Module-level so the self-test can point it at a local fake server for the
# offline error-path tests (Jina obviously can't be tested offline
# otherwise). fetch_jina() re-reads this global on every call.
_JINA_BASE = "https://r.jina.ai/"

_AUTHOR_KEYS = (
    "author",
    "article:author",
    "byl",
    "sailthru.author",
    "parsely-author",
    "dc.creator",
    "twitter:creator",
    "og:author",
)

_STATUS_KEY_NAMES = ("httpstatus", "statuscode")


def _has_credentials(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    return bool(parts.username or parts.password)


def _extract_author(metadata: dict) -> str | None:
    if not isinstance(metadata, dict):
        return None
    lower_map = {
        k.lower(): v for k, v in metadata.items() if isinstance(k, str)
    }
    for key in _AUTHOR_KEYS:
        val = lower_map.get(key)
        if val:
            return str(val)
    # fallback: any key that merely mentions "author"
    for k, v in metadata.items():
        if isinstance(k, str) and "author" in k.lower() and v:
            return str(v)
    return None


def _extract_target_http_status(data: dict) -> int | None:
    """Extract the *target* page's HTTP status from the envelope.

    Confirmed live 2026-08-16 (https://example.com through r.jina.ai):
    `data` actually carries `httpStatus` (int, e.g. 200) and
    `httpStatusText` (e.g. "OK") — fields not mentioned in the task's
    original envelope sketch, which only listed
    {code, status, data:{title,description,url,content,publishedTime?,
    metadata,usage}}. The live envelope also has `data.warning` and
    `data.external` beyond that sketch. 'code'/'status' at the envelope
    *top level* are Jina's own response status, not the target's — do not
    confuse the two. This scans data/data.metadata for an httpStatus-ish
    key (case/separator-insensitive) so it still finds the field if Jina
    ever renames it; returns None only if it's genuinely absent.
    """
    candidates = []
    if isinstance(data, dict):
        candidates.append(data)
        md = data.get("metadata")
        if isinstance(md, dict):
            candidates.append(md)
    for d in candidates:
        for k, v in d.items():
            if not isinstance(k, str):
                continue
            normalized = k.lower().replace("-", "").replace("_", "")
            if normalized in _STATUS_KEY_NAMES:
                try:
                    return int(v)
                except (TypeError, ValueError):
                    continue
    return None


def _error_detail_from_body(e: urllib.error.HTTPError) -> str:
    try:
        raw = e.read()
    except Exception:
        return str(e.reason)
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
        if isinstance(payload, dict):
            msg = payload.get("message") or payload.get("status") or payload
            return str(msg)[:300]
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    text = raw.decode("utf-8", errors="replace").strip()
    return text[:300] if text else str(e.reason)


def fetch_jina(url: str, timeout: int = 60) -> TierOutput:
    if _has_credentials(url):
        raise TierError(
            GateVerdict.FETCH_ERROR,
            "refusing to send credentialed URL to a third-party reader",
        )

    target = _JINA_BASE + url
    req = urllib.request.Request(
        target,
        headers={"Accept": "application/json", "X-No-Cache": "true"},
        method="GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise TierError(
                GateVerdict.FETCH_ERROR,
                "jina rate-limited (20/min keyless) — retry later",
                http_status=429,
            ) from e
        detail = _error_detail_from_body(e)
        raise TierError(
            GateVerdict.FETCH_ERROR,
            f"jina error (http {e.code}): {detail}",
            http_status=e.code,
        ) from e
    except (socket.timeout, TimeoutError) as e:
        raise TierError(GateVerdict.FETCH_ERROR, "jina timeout") from e
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise TierError(GateVerdict.FETCH_ERROR, "jina timeout") from e
        raise TierError(
            GateVerdict.FETCH_ERROR, f"jina fetch failed: {e.reason}"
        ) from e

    try:
        envelope = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        raise TierError(
            GateVerdict.FETCH_ERROR, f"jina returned non-JSON response: {e}"
        ) from e

    code = envelope.get("code")
    if code != 200:
        message = (
            envelope.get("message")
            or envelope.get("status")
            or str(envelope)[:300]
        )
        raise TierError(
            GateVerdict.FETCH_ERROR,
            f"jina envelope error (code {code}): {message}",
        )

    data = envelope.get("data")
    if not isinstance(data, dict):
        raise TierError(
            GateVerdict.FETCH_ERROR, "jina envelope missing 'data' object"
        )

    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    return TierOutput(
        tier=Tier.JINA,
        markdown=data.get("content") or "",
        title=data.get("title"),
        author=_extract_author(metadata),
        date=data.get("publishedTime"),
        description=data.get("description"),
        http_status=_extract_target_http_status(data),
        raw_text_len=None,
        page_title=data.get("title"),
    )


if __name__ == "__main__":
    import http.server
    import threading

    failures: list[str] = []

    def check(label: str, fn) -> None:
        try:
            fn()
            print(f"PASS: {label}")
        except Exception as e:  # noqa: BLE001 - self-test wants to report, not crash
            print(f"FAIL: {label}: {e}")
            failures.append(label)

    # -- (a) LIVE, cheap: real call to r.jina.ai for example.com -----------
    def test_live_example() -> None:
        out = fetch_jina("https://example.com", timeout=30)
        assert out.title == "Example Domain", f"title={out.title!r}"
        assert "documentation" in out.markdown.lower(), (
            f"'documentation' not found in markdown: {out.markdown[:300]!r}"
        )

    check("live example.com fetch", test_live_example)

    # -- (b) credentialed-URL refusal (no network involved) -----------------
    def test_credentialed_refused() -> None:
        try:
            fetch_jina("https://user:pass@example.com")
        except TierError as e:
            assert e.verdict == GateVerdict.FETCH_ERROR
            assert "credentialed" in e.detail
            return
        raise AssertionError("expected TierError, none raised")

    check("credentialed URL refusal", test_credentialed_refused)

    # -- (c) offline error-path tests via a local fake server ---------------
    # Jina can't be exercised offline for its error paths, so these point
    # _JINA_BASE at 127.0.0.1 instead of r.jina.ai for the duration of each
    # sub-test, restoring it immediately after. No extra live Jina calls.

    def _run_fake_server(handler_cls):
        server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, port

    class _RateLimitHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib method name
            self.send_response(429)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"rate limited")

        def log_message(self, *args) -> None:  # silence
            pass

    def test_rate_limit() -> None:
        global _JINA_BASE
        server, port = _run_fake_server(_RateLimitHandler)
        old_base = _JINA_BASE
        try:
            _JINA_BASE = f"http://127.0.0.1:{port}/"
            try:
                fetch_jina("https://example.com", timeout=5)
            except TierError as e:
                assert e.verdict == GateVerdict.FETCH_ERROR
                assert "rate-limited" in e.detail
                return
            raise AssertionError("expected TierError for 429")
        finally:
            _JINA_BASE = old_base
            server.shutdown()

    check("fake-server 429 -> TierError rate-limit message", test_rate_limit)

    class _BadEnvelopeHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                b'{"code": 451, "status": 451, '
                b'"message": "blocked by target"}'
            )

        def log_message(self, *args) -> None:
            pass

    def test_bad_envelope() -> None:
        global _JINA_BASE
        server, port = _run_fake_server(_BadEnvelopeHandler)
        old_base = _JINA_BASE
        try:
            _JINA_BASE = f"http://127.0.0.1:{port}/"
            try:
                fetch_jina("https://example.com", timeout=5)
            except TierError as e:
                assert e.verdict == GateVerdict.FETCH_ERROR
                assert "451" in e.detail
                return
            raise AssertionError("expected TierError for envelope code!=200")
        finally:
            _JINA_BASE = old_base
            server.shutdown()

    check("fake-server envelope code!=200 -> TierError", test_bad_envelope)

    if failures:
        print(f"\n{len(failures)} test(s) failed: {failures}")
        sys.exit(1)
    print("\nall tier_jina self-tests passed")
