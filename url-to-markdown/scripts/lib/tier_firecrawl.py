#!/usr/bin/env python3
"""Tier 4 — Firecrawl /v2/scrape (keyed, optional).

The only tier gated on a secret. Reads FIRECRAWL_API_KEY from the process
environment (injected by scripts/url_to_markdown.sh via
`pass-cli run --env-file`, or set directly for local testing/CI). Absent or
empty means the tier is simply not configured — this module refuses before
making any network call, so a user who never set up Firecrawl pays nothing
for it, not even a failed request.

Stdlib only (urllib) — no PEP 723 inline metadata needed.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from contract import GateVerdict, Tier, TierError, TierOutput

API_URL = "https://api.firecrawl.dev/v2/scrape"
MAX_TIMEOUT_MS = 300_000  # Firecrawl's own server-side cap on the "timeout" param


def fetch_firecrawl(url: str, timeout: int = 90) -> TierOutput:
    api_key = os.environ.get("FIRECRAWL_API_KEY", "").strip()
    if not api_key:
        raise TierError(
            GateVerdict.NOT_CONFIGURED,
            "firecrawl not configured — see references/proton-pass-setup.md",
        )

    body = json.dumps({
        "url": url,
        "formats": ["markdown"],
        "onlyMainContent": True,
        "proxy": "auto",
        # Deliberate privacy choice (references/design.md, "Privacy note for
        # the runbook"): Firecrawl caches scraped content on its US infra for
        # up to 2 days (`maxAge`) by default. storeInCache=False opts out —
        # true zero-retention is Enterprise-only, but this is the best a
        # free-tier key can do, and it's the right default for a skill that
        # may be pointed at pages the user doesn't want retained anywhere
        # but their own machine.
        "storeInCache": False,
        "timeout": min(timeout * 1000, MAX_TIMEOUT_MS),
    }).encode("utf-8")

    req = urllib.request.Request(
        API_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        if e.code == 402:
            raise TierError(
                GateVerdict.NOT_CONFIGURED,
                "firecrawl credits exhausted (free tier: 1,000/mo)",
                http_status=402,
            ) from e
        if e.code == 429:
            raise TierError(
                GateVerdict.FETCH_ERROR,
                f"firecrawl rate-limited (429): {detail}",
                http_status=429,
            ) from e
        if e.code >= 500:
            raise TierError(
                GateVerdict.FETCH_ERROR,
                f"firecrawl server error ({e.code}): {detail}",
                http_status=e.code,
            ) from e
        # Anything else (400 bad request, 401 bad key, 404, ...) is still a
        # fetch-level failure from this tier's point of view — the gate
        # never sees it, there's nothing to evaluate.
        raise TierError(
            GateVerdict.FETCH_ERROR,
            f"firecrawl returned {e.code}: {detail}",
            http_status=e.code,
        ) from e
    except urllib.error.URLError as e:
        raise TierError(
            GateVerdict.FETCH_ERROR, f"cannot reach firecrawl: {e.reason}"
        ) from e
    except (TimeoutError, OSError) as e:
        raise TierError(
            GateVerdict.FETCH_ERROR, f"firecrawl request timed out: {e}"
        ) from e

    if not payload.get("success", True):
        err = payload.get("error", "unknown error")
        raise TierError(GateVerdict.FETCH_ERROR, f"firecrawl reported failure: {err}")

    data = payload.get("data") or {}
    metadata = data.get("metadata") or {}

    # A blocked target (Cloudflare/DataDome challenge, consent wall, ...)
    # usually still returns HTTP 200 from Firecrawl itself with *some*
    # markdown (the challenge/consent page's own text) and the block
    # surfaced only in metadata.statusCode / metadata.error. We deliberately
    # do NOT raise on that here — we hand the TierOutput to the sanity gate
    # and let it make the challenge/consent-wall/soft-404 call, same as
    # every other tier. Raising here would bypass the gate's job.
    author = metadata.get("author")
    date = (
        metadata.get("publishedTime")
        or metadata.get("article:published_time")
        or metadata.get("date")
    )

    return TierOutput(
        tier=Tier.FIRECRAWL,
        markdown=markdown_from(data),
        title=metadata.get("title"),
        author=author,
        date=date,
        description=metadata.get("description"),
        http_status=metadata.get("statusCode"),
        raw_text_len=None,  # Firecrawl doesn't expose raw pre-extraction text
        page_title=metadata.get("title"),
    )


def markdown_from(data: dict) -> str:
    return data.get("markdown") or ""


if __name__ == "__main__":
    import sys

    test_url = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"

    if not os.environ.get("FIRECRAWL_API_KEY", "").strip():
        try:
            fetch_firecrawl(test_url)
        except TierError as e:
            assert e.verdict == GateVerdict.NOT_CONFIGURED
            print(f"ok (no network): NOT_CONFIGURED — {e.detail}")
        else:
            raise SystemExit("expected TierError(NOT_CONFIGURED) with no API key set")
    else:
        print(f"FIRECRAWL_API_KEY is set — making ONE live call to {test_url} "
              "(this spends a real credit if the key is valid)")
        try:
            out = fetch_firecrawl(test_url)
            print(f"tier={out.tier} http_status={out.http_status} "
                  f"title={out.title!r} markdown_len={len(out.markdown)}")
        except TierError as e:
            print(f"TierError: verdict={e.verdict} http_status={e.http_status} "
                  f"detail={e.detail}")
