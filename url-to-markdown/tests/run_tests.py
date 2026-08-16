#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "trafilatura==2.2.0",
# ]
# ///
"""Integration harness for the url-to-markdown tier ladder.

Imports scripts/lib/*.py by path and runs contract-level checks against the
tests/fixtures/*.html corpus (hand-authored, offline). The lib modules are
being written concurrently by other agents, so every module is imported
defensively: if a module doesn't exist yet (or fails to import for any
reason), every check that depends on it is recorded as SKIPPED rather than
crashing the harness. Checks that *can* run either PASS or FAIL.

Contract reference: scripts/lib/contract.py (frozen — read, never edited by
this file). Design reference: references/design.md (sanity-gate and
implementation-recipe sections).

No live-network checks run by default (design rule: tests never depend on
the network). A tiny opt-in live smoke check (Jina/example.com-class) is
gated behind RUN_LIVE=1 and skipped otherwise.

Run: uv run tests/run_tests.py
Exit: 0 if no check FAILed (SKIPs are fine), 1 if any check FAILed.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
LIB_DIR = TESTS_DIR.parent / "scripts" / "lib"
PDF2MD_FIXTURES_DIR = TESTS_DIR.parent.parent / "pdf-to-markdown" / "tests" / "fixtures"

sys.path.insert(0, str(LIB_DIR))

import contract  # noqa: E402  (frozen interface; must load first)

ARTICLE = FIXTURES_DIR / "article.html"
APP_SHELL = FIXTURES_DIR / "app-shell.html"
JS_CONTENT = FIXTURES_DIR / "js-content.html"
CHALLENGE = FIXTURES_DIR / "challenge.html"
CONSENT_WALL = FIXTURES_DIR / "consent-wall.html"
SOFT_404 = FIXTURES_DIR / "soft-404.html"
LOGIN_WALL = FIXTURES_DIR / "login-wall.html"
# Adversarial-review regression fixtures (confirmed gate bypasses, fixed):
CHALLENGE_PADDED = FIXTURES_DIR / "challenge-padded.html"
LOGIN_MARKETING = FIXTURES_DIR / "login-wall-marketing.html"
CONSENT_WALL_DE = FIXTURES_DIR / "consent-wall-de.html"
CONSENT_WALL_ES = FIXTURES_DIR / "consent-wall-es.html"
SOFT_404_CHEERY = FIXTURES_DIR / "soft-404-cheery.html"
SSO_ARTICLE = FIXTURES_DIR / "sso-article.html"
PROBE_PDF = PDF2MD_FIXTURES_DIR / "probe-simple.pdf"

ALL_FIXTURES = [ARTICLE, APP_SHELL, JS_CONTENT, CHALLENGE, CONSENT_WALL, SOFT_404, LOGIN_WALL,
                CHALLENGE_PADDED, LOGIN_MARKETING, CONSENT_WALL_DE, CONSENT_WALL_ES,
                SOFT_404_CHEERY, SSO_ARTICLE]


def file_url(path: Path) -> str:
    return "file://" + str(path.resolve())


# --------------------------------------------------------------- mini-runner
# Same shape as pdf-to-markdown/tests/run_tests.py: SkipCheck for
# environment-level "can't run this" reasons, try_import for "module not
# present yet", run_check as the try/except wrapper each check goes through.

class SkipCheck(Exception):
    """Raise from inside a check body for an environment-level reason that
    isn't 'module not present' (e.g. Chrome isn't installed) — recorded as
    SKIP, not FAIL."""


_results: list[tuple[str, str, str, str]] = []  # (module, check, status, detail)


def run_check(module: str, name: str, fn) -> None:
    try:
        fn()
    except SkipCheck as e:
        _results.append((module, name, "SKIP", str(e)))
    except AssertionError as e:
        _results.append((module, name, "FAIL", str(e)))
    except Exception as e:  # noqa: BLE001 — a check raising anything is a FAIL
        _results.append((module, name, "FAIL", f"{type(e).__name__}: {e}"))
    else:
        _results.append((module, name, "PASS", ""))


def skip_all(module: str, names: list[str], reason: str) -> None:
    for name in names:
        _results.append((module, name, "SKIP", reason))


def try_import(modname: str):
    try:
        return importlib.import_module(modname), None
    except Exception as e:  # noqa: BLE001 — any import failure => skip module
        return None, e


# -------------------------------------------------------- gate x fixtures

GATE_MATRIX_CHECKS = [
    "article_is_ok",
    "challenge_is_challenge",
    "consent_wall_is_consent_wall",
    "soft_404_is_soft_404",
    "login_wall_is_login_wall",
    "app_shell_not_ok",
    "js_content_static_not_ok",
]


def _gate_verdict_for_fixture(tier_static_mod, gate_mod, path: Path) -> "contract.GateResult":
    html = path.read_text(encoding="utf-8")
    out = tier_static_mod.extract_from_html(html, file_url(path))
    assert isinstance(out, contract.TierOutput), f"extract_from_html returned {type(out)}"
    return gate_mod.evaluate(out)


def run_gate_matrix_checks():
    tier_static_mod, err1 = try_import("tier_static")
    gate_mod, err2 = try_import("gate")
    if tier_static_mod is None or gate_mod is None:
        missing = []
        if tier_static_mod is None:
            missing.append(f"tier_static ({err1})")
        if gate_mod is None:
            missing.append(f"gate ({err2})")
        skip_all("gate", GATE_MATRIX_CHECKS,
                  f"SKIPPED (module(s) not present): {', '.join(missing)}")
        return

    def article_is_ok():
        result = _gate_verdict_for_fixture(tier_static_mod, gate_mod, ARTICLE)
        assert result.verdict == contract.GateVerdict.OK, (
            f"article.html verdict={result.verdict}, detail={result.detail!r}"
        )

    def challenge_is_challenge():
        result = _gate_verdict_for_fixture(tier_static_mod, gate_mod, CHALLENGE)
        assert result.verdict == contract.GateVerdict.CHALLENGE, (
            f"challenge.html verdict={result.verdict}, detail={result.detail!r}"
        )

    def consent_wall_is_consent_wall():
        result = _gate_verdict_for_fixture(tier_static_mod, gate_mod, CONSENT_WALL)
        assert result.verdict == contract.GateVerdict.CONSENT_WALL, (
            f"consent-wall.html verdict={result.verdict}, detail={result.detail!r}"
        )

    def soft_404_is_soft_404():
        result = _gate_verdict_for_fixture(tier_static_mod, gate_mod, SOFT_404)
        assert result.verdict == contract.GateVerdict.SOFT_404, (
            f"soft-404.html verdict={result.verdict}, detail={result.detail!r}"
        )

    def login_wall_is_login_wall():
        result = _gate_verdict_for_fixture(tier_static_mod, gate_mod, LOGIN_WALL)
        assert result.verdict == contract.GateVerdict.LOGIN_WALL, (
            f"login-wall.html verdict={result.verdict}, detail={result.detail!r}"
        )

    def app_shell_not_ok():
        # raw_text_len only counts VISIBLE text (script/style content is
        # invisible), so app-shell.html's large inline JS blob does not
        # inflate raw_text_len the way a naive byte-size check would expect.
        # The fixture is authored so the near-empty *extracted* text (just
        # the <noscript> fallback line, ~50 chars) trips the absolute EMPTY
        # threshold rather than the raw-vs-extracted APP_SHELL ratio check —
        # both verdicts are accepted here since either proves the gate
        # caught the SPA skeleton; what must never happen is OK.
        result = _gate_verdict_for_fixture(tier_static_mod, gate_mod, APP_SHELL)
        assert result.verdict in (contract.GateVerdict.APP_SHELL, contract.GateVerdict.EMPTY), (
            f"app-shell.html verdict={result.verdict}, detail={result.detail!r}"
        )
        assert result.verdict != contract.GateVerdict.OK

    def js_content_static_not_ok():
        # js-content.html's body content only exists after an inline
        # <script> mutates the DOM; the static (non-rendering) path must
        # never see it, so this must not verdict OK.
        result = _gate_verdict_for_fixture(tier_static_mod, gate_mod, JS_CONTENT)
        assert result.verdict != contract.GateVerdict.OK, (
            f"js-content.html (static) verdict={result.verdict}, "
            f"detail={result.detail!r} — static extraction should not see "
            f"JS-injected content"
        )

    run_check("gate", "article_is_ok", article_is_ok)
    run_check("gate", "challenge_is_challenge", challenge_is_challenge)
    run_check("gate", "consent_wall_is_consent_wall", consent_wall_is_consent_wall)
    run_check("gate", "soft_404_is_soft_404", soft_404_is_soft_404)
    run_check("gate", "login_wall_is_login_wall", login_wall_is_login_wall)
    run_check("gate", "app_shell_not_ok", app_shell_not_ok)
    run_check("gate", "js_content_static_not_ok", js_content_static_not_ok)


# ---------------------------------------------------- gate adversarial
# Regression checks for the confirmed bypasses found by the adversarial
# review (scratchpad review2/adv_gate.py + adv_mojibake.py): honest error
# statuses, non-English walls, padded challenge/login walls, cheery
# soft-404 titles, and mojibake extractions must all gate; the crafted
# long-form negative (an article ABOUT authentication) must still pass.

GATE_ADVERSARIAL_CHECKS = [
    "honest_404_status_is_soft_404",
    "honest_500_status_is_http_blocked",
    "consent_wall_de_is_consent_wall",
    "consent_wall_es_is_consent_wall",
    "padded_challenge_is_challenge",
    "login_marketing_is_login_wall",
    "sso_article_negative_is_ok",
    "garbled_extraction_is_garbled",
    "soft_404_cheery_title_is_soft_404",
]


def run_gate_adversarial_checks():
    tier_static_mod, err1 = try_import("tier_static")
    gate_mod, err2 = try_import("gate")
    if tier_static_mod is None or gate_mod is None:
        missing = []
        if tier_static_mod is None:
            missing.append(f"tier_static ({err1})")
        if gate_mod is None:
            missing.append(f"gate ({err2})")
        skip_all("gate-adversarial", GATE_ADVERSARIAL_CHECKS,
                 f"SKIPPED (module(s) not present): {', '.join(missing)}")
        return

    def _verdict(path: Path, http_status: int | None = None) -> "contract.GateResult":
        html = path.read_text(encoding="utf-8")
        out = tier_static_mod.extract_from_html(html, file_url(path), http_status=http_status)
        return gate_mod.evaluate(out)

    def honest_404_status_is_soft_404():
        # A wordy branded error body served under an honest 404 must gate on
        # the status alone — content under an error status is not the page.
        result = _verdict(SOFT_404_CHEERY, http_status=404)
        assert result.verdict == contract.GateVerdict.SOFT_404, (
            f"http 404 verdict={result.verdict}, detail={result.detail!r}"
        )

    def honest_500_status_is_http_blocked():
        # Real article content under a 5xx status still gates: the tier
        # honestly reported an error status.
        result = _verdict(ARTICLE, http_status=500)
        assert result.verdict == contract.GateVerdict.HTTP_BLOCKED, (
            f"http 500 verdict={result.verdict}, detail={result.detail!r}"
        )

    def consent_wall_de_is_consent_wall():
        result = _verdict(CONSENT_WALL_DE)
        assert result.verdict == contract.GateVerdict.CONSENT_WALL, (
            f"consent-wall-de.html verdict={result.verdict}, detail={result.detail!r}"
        )

    def consent_wall_es_is_consent_wall():
        result = _verdict(CONSENT_WALL_ES)
        assert result.verdict == contract.GateVerdict.CONSENT_WALL, (
            f"consent-wall-es.html verdict={result.verdict}, detail={result.detail!r}"
        )

    def padded_challenge_is_challenge():
        # ~3.4k chars of padding around a challenge body must not defeat
        # the strong-phrase (any-length) rule.
        result = _verdict(CHALLENGE_PADDED)
        assert result.verdict == contract.GateVerdict.CHALLENGE, (
            f"challenge-padded.html verdict={result.verdict}, detail={result.detail!r}"
        )

    def login_marketing_is_login_wall():
        # Medium-style wall padded with membership marketing prose: the
        # scaled rule (>= 4 distinct login phrases under 5000 chars) fires.
        result = _verdict(LOGIN_MARKETING)
        assert result.verdict == contract.GateVerdict.LOGIN_WALL, (
            f"login-wall-marketing.html verdict={result.verdict}, detail={result.detail!r}"
        )

    def sso_article_negative_is_ok():
        # Negative for the scaled login rule: a real >= 5000-char article
        # about authentication providers, dense with login vocabulary.
        result = _verdict(SSO_ARTICLE)
        assert result.verdict == contract.GateVerdict.OK, (
            f"sso-article.html verdict={result.verdict}, detail={result.detail!r}"
        )

    def garbled_extraction_is_garbled():
        # Synthetic TierOutput mimicking tier_static's utf-8/replace decode
        # fallback on a legacy-charset page: 70% U+FFFD must gate GARBLED.
        out = contract.TierOutput(
            tier=contract.Tier.STATIC,
            markdown="�" * 700 + "x" * 300,
            title=None, author=None, date=None, description=None,
            http_status=200, raw_text_len=1500, page_title="Some Article")
        result = gate_mod.evaluate(out)
        assert result.verdict == contract.GateVerdict.GARBLED, (
            f"70%-U+FFFD output verdict={result.verdict}, detail={result.detail!r}"
        )
        # a normal article must be unaffected by the mojibake check
        clean = _verdict(ARTICLE)
        assert clean.verdict == contract.GateVerdict.OK, (
            f"article.html verdict={clean.verdict}, detail={clean.detail!r}"
        )

    def soft_404_cheery_title_is_soft_404():
        # "Oops! | Example News" behind a 200: cheery title patterns are
        # soft-404 evidence under the existing title length guard.
        result = _verdict(SOFT_404_CHEERY, http_status=200)
        assert result.verdict == contract.GateVerdict.SOFT_404, (
            f"soft-404-cheery.html verdict={result.verdict}, detail={result.detail!r}"
        )

    run_check("gate-adversarial", "honest_404_status_is_soft_404", honest_404_status_is_soft_404)
    run_check("gate-adversarial", "honest_500_status_is_http_blocked", honest_500_status_is_http_blocked)
    run_check("gate-adversarial", "consent_wall_de_is_consent_wall", consent_wall_de_is_consent_wall)
    run_check("gate-adversarial", "consent_wall_es_is_consent_wall", consent_wall_es_is_consent_wall)
    run_check("gate-adversarial", "padded_challenge_is_challenge", padded_challenge_is_challenge)
    run_check("gate-adversarial", "login_marketing_is_login_wall", login_marketing_is_login_wall)
    run_check("gate-adversarial", "sso_article_negative_is_ok", sso_article_negative_is_ok)
    run_check("gate-adversarial", "garbled_extraction_is_garbled", garbled_extraction_is_garbled)
    run_check("gate-adversarial", "soft_404_cheery_title_is_soft_404", soft_404_cheery_title_is_soft_404)


# -------------------------------------------------------- gate negatives
# Proves the consent-noise-vs-consent-IS-the-content dominance logic: the
# same banner markup that must survive as harmless noise inside the full
# article must flip the verdict to CONSENT_WALL when it's the only content
# on the page.

GATE_NEGATIVE_CHECKS = [
    "consent_banner_alone_is_consent_wall",
    "full_article_with_banner_still_ok",
]


def run_gate_negative_checks():
    tier_static_mod, err1 = try_import("tier_static")
    gate_mod, err2 = try_import("gate")
    if tier_static_mod is None or gate_mod is None:
        missing = []
        if tier_static_mod is None:
            missing.append(f"tier_static ({err1})")
        if gate_mod is None:
            missing.append(f"gate ({err2})")
        skip_all("gate-negative", GATE_NEGATIVE_CHECKS,
                  f"SKIPPED (module(s) not present): {', '.join(missing)}")
        return

    import re

    def _banner_only_html() -> str:
        html = ARTICLE.read_text(encoding="utf-8")
        m = re.search(r'<div id="cookie-consent-banner".*?</div>\s*(?=<header)', html, re.S)
        assert m, "cookie-consent-banner div not found in article.html fixture"
        return "<html><head><title>Harbor Naturalist</title></head><body>" + m.group(0) + "</body></html>"

    def consent_banner_alone_is_consent_wall():
        out = tier_static_mod.extract_from_html(_banner_only_html(), file_url(ARTICLE))
        result = gate_mod.evaluate(out)
        assert result.verdict == contract.GateVerdict.CONSENT_WALL, (
            f"isolated consent banner verdict={result.verdict}, detail={result.detail!r} "
            f"— dominance logic did not fire when consent text IS the content"
        )

    def full_article_with_banner_still_ok():
        result = _gate_verdict_for_fixture(tier_static_mod, gate_mod, ARTICLE)
        assert result.verdict == contract.GateVerdict.OK, (
            f"full article (banner present as noise) verdict={result.verdict}, "
            f"detail={result.detail!r} — consent banner should be dropped as "
            f"noise, not dominate the verdict"
        )

    run_check("gate-negative", "consent_banner_alone_is_consent_wall", consent_banner_alone_is_consent_wall)
    run_check("gate-negative", "full_article_with_banner_still_ok", full_article_with_banner_still_ok)


# -------------------------------------------------------------- tier_static

TIER_STATIC_CHECKS = [
    "article_markdown_has_table_and_prose",
    "article_nav_footer_absent_or_minimal",
    "article_metadata_title_author",
]


def run_tier_static_checks():
    mod, err = try_import("tier_static")
    if mod is None:
        skip_all("tier_static", TIER_STATIC_CHECKS, f"SKIPPED (module not present): {err}")
        return

    def _fetch_article() -> "contract.TierOutput":
        out = mod.fetch_static(file_url(ARTICLE))
        assert isinstance(out, contract.TierOutput), f"fetch_static returned {type(out)}"
        return out

    def article_markdown_has_table_and_prose():
        out = _fetch_article()
        md = out.markdown
        assert "High pools" in md and "Mid pools" in md, "table rows missing from markdown"
        assert "|" in md, "no GFM table pipes found in markdown"
        assert "tide pool" in md.lower() or "tide-pool" in md.lower(), "article prose missing"

    def article_nav_footer_absent_or_minimal():
        out = _fetch_article()
        md = out.markdown
        assert "Species Guide" not in md, "nav link leaked into extracted markdown"
        assert "All rights reserved" not in md, "footer boilerplate leaked into extracted markdown"

    def article_metadata_title_author():
        out = _fetch_article()
        assert out.title and "Tide Pools" in out.title, f"title={out.title!r}"
        assert out.author and "Okafor" in out.author, f"author={out.author!r}"

    run_check("tier_static", "article_markdown_has_table_and_prose", article_markdown_has_table_and_prose)
    run_check("tier_static", "article_nav_footer_absent_or_minimal", article_nav_footer_absent_or_minimal)
    run_check("tier_static", "article_metadata_title_author", article_metadata_title_author)


# -------------------------------------------------------------- tier_chrome

TIER_CHROME_CHECKS = ["js_content_sentinel_rendered"]


def run_tier_chrome_checks():
    mod, err = try_import("tier_chrome")
    if mod is None:
        skip_all("tier_chrome", TIER_CHROME_CHECKS, f"SKIPPED (module not present): {err}")
        return

    def js_content_sentinel_rendered():
        try:
            out = mod.fetch_chrome(file_url(JS_CONTENT))
        except contract.TierError as e:
            raise SkipCheck(f"chrome tier unavailable: {e.detail}") from e
        assert isinstance(out, contract.TierOutput), f"fetch_chrome returned {type(out)}"
        assert "SENTINEL-4471-CHROME-RENDER" in out.markdown, (
            f"JS-injected sentinel not found in chrome-rendered markdown: {out.markdown[:200]!r}"
        )

    run_check("tier_chrome", "js_content_sentinel_rendered", js_content_sentinel_rendered)


# ------------------------------------------------------------------- probe

PROBE_CHECKS = ["probe_pdf_kind", "probe_html_kind"]


def run_probe_checks():
    mod, err = try_import("probe")
    if mod is None:
        skip_all("probe", PROBE_CHECKS, f"SKIPPED (module not present): {err}")
        return

    def probe_pdf_kind():
        if not PROBE_PDF.exists():
            raise SkipCheck(f"pdf-to-markdown fixture missing: {PROBE_PDF}")
        with tempfile.TemporaryDirectory(prefix="u2m-probe-") as tmp:
            result = mod.probe(file_url(PROBE_PDF), tmp)
        assert isinstance(result, contract.ProbeResult), f"probe() returned {type(result)}"
        assert result.kind == contract.ProbeKind.PDF, f"kind={result.kind}"

    def probe_html_kind():
        with tempfile.TemporaryDirectory(prefix="u2m-probe-") as tmp:
            result = mod.probe(file_url(ARTICLE), tmp)
        assert isinstance(result, contract.ProbeResult), f"probe() returned {type(result)}"
        assert result.kind == contract.ProbeKind.HTML, f"kind={result.kind}"

    run_check("probe", "probe_pdf_kind", probe_pdf_kind)
    run_check("probe", "probe_html_kind", probe_html_kind)


# --------------------------------------------------------------------- okf

OKF_CHECKS = ["okf_frontmatter_well_formed", "slugify_nonempty"]


def _synthetic_tier_output() -> "contract.TierOutput":
    return contract.TierOutput(
        tier=contract.Tier.STATIC,
        markdown="# Why Tide Pools Keep Their Own Weather\n\nSynthetic body text.\n",
        title="Why Tide Pools Keep Their Own Weather",
        author="Marguerite Okafor",
        date="2026-06-02",
        description="A field report on tide pool microclimates.",
        http_status=200,
        raw_text_len=4341,
        page_title="Why Tide Pools Keep Their Own Weather — Harbor Naturalist",
    )


def _synthetic_attempts() -> list["contract.TierAttempt"]:
    return [contract.TierAttempt(tier=contract.Tier.STATIC, verdict=contract.GateVerdict.OK, detail="ok")]


def run_okf_checks():
    mod, err = try_import("okf")
    if mod is None:
        skip_all("okf", OKF_CHECKS, f"SKIPPED (module not present): {err}")
        return

    def okf_frontmatter_well_formed():
        out = _synthetic_tier_output()
        text = mod.okf_frontmatter(
            url="https://harbornaturalist.example/tide-pools",
            out=out,
            attempts=_synthetic_attempts(),
        )
        assert isinstance(text, str) and text.strip(), "okf_frontmatter returned empty/non-string"
        lines = text.strip().splitlines()
        assert lines[0].strip() == "---", f"frontmatter doesn't open with '---': {lines[0]!r}"
        assert "---" in [l.strip() for l in lines[1:]], "frontmatter has no closing '---'"
        assert "url-to-markdown" in text, "generated.by should reference url-to-markdown"
        assert "static" in text, "conversion tier (static) not recorded in frontmatter"

    def slugify_nonempty():
        slug = mod.slugify("Why Tide Pools Keep Their Own Weather",
                            "https://harbornaturalist.example/tide-pools")
        assert isinstance(slug, str) and slug.strip(), "slugify returned empty/non-string"
        assert " " not in slug, f"slug contains spaces: {slug!r}"

    run_check("okf", "okf_frontmatter_well_formed", okf_frontmatter_well_formed)
    run_check("okf", "slugify_nonempty", slugify_nonempty)


# -------------------------------------------------------------------- deps

DEPS_CHECKS = ["check_deps_does_not_exit_when_curl_present"]


def run_deps_checks():
    mod, err = try_import("deps")
    if mod is None:
        skip_all("deps", DEPS_CHECKS, f"SKIPPED (module not present): {err}")
        return

    def check_deps_does_not_exit_when_curl_present():
        import shutil as _shutil
        if _shutil.which("curl") is None:
            raise SkipCheck("curl not installed on this machine")
        mod.check_deps()  # must not sys.exit / raise

    run_check("deps", "check_deps_does_not_exit_when_curl_present", check_deps_does_not_exit_when_curl_present)


# -------------------------------------------------------------------- e2e

E2E_CHECKS = [
    "article_converts_clean",
    "challenge_exits_unresolved_no_clean_content",
]

ORCHESTRATOR = TESTS_DIR.parent / "scripts" / "url_to_markdown.py"


def _run_orchestrator(url: str, out_path: Path):
    try:
        proc = subprocess.run(
            ["uv", "run", str(ORCHESTRATOR), url, "-o", str(out_path)],
            capture_output=True, text=True, timeout=180,
        )
    except FileNotFoundError as e:  # uv itself missing
        raise SkipCheck(f"uv not on PATH: {e}") from e
    if proc.returncode == contract.EXIT_NOT_CONFIGURED:
        raise SkipCheck(f"orchestrator deps not configured: {proc.stderr[-200:]!r}")
    return proc


def run_e2e_checks():
    if not ORCHESTRATOR.exists():
        skip_all("e2e", E2E_CHECKS, f"SKIPPED (orchestrator not present): {ORCHESTRATOR}")
        return

    def article_converts_clean():
        with tempfile.TemporaryDirectory(prefix="u2m-e2e-") as tmp:
            out_path = Path(tmp) / "article.md"
            proc = _run_orchestrator(file_url(ARTICLE), out_path)
            assert proc.returncode == contract.EXIT_CLEAN, (
                f"exit={proc.returncode}; stderr tail: {proc.stderr[-300:]!r}"
            )
            assert out_path.exists(), "no output markdown file written"
            text = out_path.read_text(encoding="utf-8")
            assert text.startswith("---"), "output missing OKF frontmatter"
            assert "Tide Pools" in text, "output missing article content"

    def challenge_exits_unresolved_no_clean_content():
        with tempfile.TemporaryDirectory(prefix="u2m-e2e-") as tmp:
            out_path = Path(tmp) / "challenge.md"
            proc = _run_orchestrator(file_url(CHALLENGE), out_path)
            assert proc.returncode == contract.EXIT_UNRESOLVED, (
                f"exit={proc.returncode} (expected EXIT_UNRESOLVED={contract.EXIT_UNRESOLVED}); "
                f"stderr tail: {proc.stderr[-300:]!r}"
            )
            if out_path.exists():
                text = out_path.read_text(encoding="utf-8")
                assert "Just a moment" not in text, (
                    "challenge page content emitted as if it were clean article content"
                )
            combined = proc.stdout + proc.stderr
            assert "CHALLENGE" in combined or "challenge" in combined.lower(), (
                f"no mention of the CHALLENGE gate verdict in report/output: {combined[-500:]!r}"
            )

    run_check("e2e", "article_converts_clean", article_converts_clean)
    run_check("e2e", "challenge_exits_unresolved_no_clean_content", challenge_exits_unresolved_no_clean_content)


# --------------------------------------------------------------- live (opt-in)
# Design rule: tests never depend on the network by default. This single
# smoke check is gated behind RUN_LIVE=1 and otherwise always SKIPped.

LIVE_CHECKS = ["jina_keyless_example_com"]


def run_live_checks():
    if os.environ.get("RUN_LIVE") != "1":
        skip_all("live", LIVE_CHECKS, "SKIPPED (set RUN_LIVE=1 to run live-network checks)")
        return

    mod, err = try_import("tier_jina")
    if mod is None:
        skip_all("live", LIVE_CHECKS, f"SKIPPED (module not present): {err}")
        return

    def jina_keyless_example_com():
        out = mod.fetch_jina("https://example.com")
        assert isinstance(out, contract.TierOutput), f"fetch_jina returned {type(out)}"
        assert out.markdown and out.markdown.strip(), "empty markdown from live Jina fetch"

    run_check("live", "jina_keyless_example_com", jina_keyless_example_com)


# --------------------------------------------------------------------- main

def print_table() -> bool:
    """Print PASS/FAIL/SKIP table. Returns True if any check FAILed."""
    width_module = max(len(m) for m, *_ in _results) if _results else 6
    width_name = max(len(n) for _, n, *_ in _results) if _results else 4

    print()
    print(f"{'MODULE':<{width_module}}  {'CHECK':<{width_name}}  STATUS  DETAIL")
    print("-" * (width_module + width_name + 40))
    any_fail = False
    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    for module, name, status, detail in _results:
        counts[status] += 1
        if status == "FAIL":
            any_fail = True
        detail_shown = detail if status != "PASS" else ""
        print(f"{module:<{width_module}}  {name:<{width_name}}  {status:<6}  {detail_shown}")
    print("-" * (width_module + width_name + 40))
    print(f"Total: {len(_results)}   PASS={counts['PASS']}  FAIL={counts['FAIL']}  SKIP={counts['SKIP']}")
    return any_fail


def main() -> None:
    for fixture in ALL_FIXTURES:
        if not fixture.exists():
            print(f"error: fixture missing: {fixture}", file=sys.stderr)
            sys.exit(1)

    run_gate_matrix_checks()
    run_gate_adversarial_checks()
    run_gate_negative_checks()
    run_tier_static_checks()
    run_tier_chrome_checks()
    run_probe_checks()
    run_okf_checks()
    run_deps_checks()
    run_e2e_checks()
    run_live_checks()

    any_fail = print_table()
    sys.exit(1 if any_fail else 0)


if __name__ == "__main__":
    main()
