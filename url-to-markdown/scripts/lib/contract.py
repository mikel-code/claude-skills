"""Shared contract for the url-to-markdown tier ladder.

FROZEN INTERFACE: every module in scripts/lib/ codes against this file. Do
not change shapes here without updating every consumer. references/design.md
is the design authority; this file is its executable schema.

Pipeline-wide conventions:
- One URL per invocation. The ladder runs probe -> static -> chrome -> jina
  -> firecrawl, with the sanity gate between every rung; the orchestrator
  owns the ladder, tier modules own exactly one fetch each.
- No silent failure: every tier attempted is recorded as a TierAttempt with
  its gate verdict; a page that defeats all tiers exits EXIT_UNRESOLVED with
  the Tier-5 (claude-in-chrome) handoff, never emits a challenge/consent
  page as if it were content.
- Tier modules raise TierError for fetch-level failures (network, HTTP
  block, tool absent); they never sys.exit. Only deps.py and the
  orchestrator exit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------- exit codes

EXIT_CLEAN = 0            # converted successfully
EXIT_ERROR = 1            # unexpected failure (traceback-worthy)
EXIT_NOT_CONFIGURED = 2   # missing dependency; deps.py printed instructions
EXIT_REFUSED_FILE = 3     # URL points at a PDF/office file -> sibling skill
EXIT_UNRESOLVED = 4       # all automatic tiers failed -> Tier-5 handoff
# 5 is reserved by scripts/url_to_markdown.sh for Proton Pass machinery
# failures that happen before the pipeline runs — not defined here because
# the Python pipeline never emits it.


# ------------------------------------------------------------------- enums

class Tier(str, Enum):
    STATIC = "static"          # curl + trafilatura
    CHROME = "chrome"          # headless Chrome --dump-dom + trafilatura
    JINA = "jina"              # r.jina.ai keyless
    FIRECRAWL = "firecrawl"    # /v2/scrape, keyed, optional
    BROWSER = "browser"        # Tier 5: claude-in-chrome (manual, not code)


class GateVerdict(str, Enum):
    OK = "ok"
    EMPTY = "empty"                  # no/near-no extracted content
    APP_SHELL = "app-shell"          # extracted text << raw page text mass
    CHALLENGE = "challenge"          # Cloudflare/DataDome/Turnstile markers
    CONSENT_WALL = "consent-wall"    # consent text IS the content
    SOFT_404 = "soft-404"            # error/not-found page (incl. honest 404/
                                     # 410 statuses with wordy bodies)
    LOGIN_WALL = "login-wall"        # login form dominates -> Tier 5, not 4
    HTTP_BLOCKED = "http-blocked"    # 401/403/429/5xx/challenge statuses
    GARBLED = "garbled"              # mojibake: U+FFFD-dense extraction
    FETCH_ERROR = "fetch-error"      # network/tool failure (incl. tool absent)
    NOT_CONFIGURED = "not-configured"  # firecrawl skipped: no API key present
    SKIPPED = "skipped"              # orchestrator chose not to run this tier
                                     # (login-wall stop, non-http URL, chrome
                                     # pointless after HTTP block) — gate
                                     # never returns this; FETCH_ERROR and
                                     # NOT_CONFIGURED are likewise tier/
                                     # orchestrator-raised, never gate-returned


class ProbeKind(str, Enum):
    HTML = "html"        # proceed with the ladder
    PDF = "pdf"          # hand off to pdf-to-markdown
    OFFICE = "office"    # hand off to doc-to-markdown
    OTHER = "other"      # unknown binary -> refuse with explanation


# ------------------------------------------------------------------ records

@dataclass
class ProbeResult:
    kind: ProbeKind
    content_type: str | None      # as reported (may be None/lying)
    final_url: str                # after redirects
    downloaded_path: str | None   # set for PDF/OFFICE: temp file for handoff


@dataclass
class TierOutput:
    """One tier's fetch+extract result, before the gate sees it."""
    tier: Tier
    markdown: str                 # extracted content, NO frontmatter block
    title: str | None
    author: str | None
    date: str | None              # ISO-ish publication date if known
    description: str | None
    http_status: int | None      # None when the tier can't know it
    raw_text_len: int | None     # visible text length of the RAW page
                                  # (pre-extraction); None if unknown — the
                                  # gate's app-shell ratio check needs it and
                                  # skips when None
    page_title: str | None       # raw <title>, for soft-404 detection


@dataclass
class GateResult:
    verdict: GateVerdict
    detail: str                   # one line: which marker/threshold fired


@dataclass
class TierAttempt:
    """Provenance record — one per tier the orchestrator tried or skipped."""
    tier: Tier
    verdict: GateVerdict
    detail: str = ""


class TierError(Exception):
    """Fetch-level failure inside a tier module. The orchestrator converts it
    into a TierAttempt with the given verdict and moves on."""

    def __init__(self, verdict: GateVerdict, detail: str,
                 http_status: int | None = None):
        super().__init__(detail)
        self.verdict = verdict
        self.detail = detail
        self.http_status = http_status


# ------------------------------------------------- module entry signatures
#
# probe.py           probe(url: str, download_dir: str) -> ProbeResult
# tier_static.py     fetch_static(url: str, timeout: int = 30) -> TierOutput
#                    extract_from_html(html: str, url: str,
#                                      tier: Tier = Tier.STATIC,
#                                      http_status: int | None = None)
#                                      -> TierOutput   # offline-testable core
# tier_chrome.py     fetch_chrome(url: str, timeout: int = 60) -> TierOutput
#                    (reuses tier_static.extract_from_html — the ONE allowed
#                     cross-module import besides contract)
# tier_jina.py       fetch_jina(url: str, timeout: int = 60) -> TierOutput
# tier_firecrawl.py  fetch_firecrawl(url: str, timeout: int = 90) -> TierOutput
#                    (reads FIRECRAWL_API_KEY from os.environ; raises
#                     TierError(NOT_CONFIGURED, ...) when absent)
# gate.py            evaluate(out: TierOutput) -> GateResult
# okf.py             okf_frontmatter(*, url: str, out: TierOutput,
#                                    attempts: list[TierAttempt]) -> str
#                    slugify(title: str | None, url: str) -> str
# deps.py            check_deps() -> None    # sys.exit(2) w/ instructions
#
# trafilatura extraction params (uniform wherever trafilatura is called):
#   trafilatura.extract(html, url=url, output_format="markdown",
#                       with_metadata=False, include_links=True,
#                       include_images=True, include_tables=True)
#   + trafilatura.extract_metadata(html) for title/author/date/description.
#   with_metadata=False on purpose: okf.py builds the frontmatter; content
#   must not carry trafilatura's own ---title--- block.
#
# Each module ends with `if __name__ == "__main__":` self-test runnable as
# `uv run <module>.py [args]`, preferring offline fixtures/file:// inputs;
# live-network self-tests must be cheap (example.com-class) and clearly
# labeled. PEP 723 inline uv metadata where non-stdlib deps are needed
# (trafilatura pin: current verified version is 2.2.0).
