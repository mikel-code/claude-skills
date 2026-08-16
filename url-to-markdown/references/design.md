# url-to-markdown — design

Status: **design agreed, not yet implemented.** Written 2026-08-16, based on a
three-track research pass (Jina Reader with live keyless endpoint tests,
Firecrawl's August-2026 pricing/API state, and the local-first extraction
landscape). This file is the durable record of what was decided and *why*, so
implementation — and future changes — can be made against the original
reasoning instead of re-researching it.

## Scope

Convert a web URL into clean GitHub-Flavored Markdown with OKF v0.2
frontmatter, **prioritizing correctness, privacy, and zero marginal cost**.
Volume is low (individual pages saved deliberately, not crawling); platform is
macOS with Chrome installed; Claude Code context available (claude-in-chrome
as a manual escalation).

This skill completes the repo's converter family — `doc-to-markdown` (office
files), `pdf-to-markdown` (PDFs), `yt-transcript` (videos) — and **composes
with it**: URLs that point at files are handed off, not scraped (Tier 0).

Out of scope by design: crawling/spidering (one URL per invocation), search
(s.jina.ai requires a key and is a different product), anti-bot *evasion*
built locally (2026 consensus: plain headless browsers are fingerprinted in
seconds; evasion requires paid stealth infrastructure — that is what the
Firecrawl tier is for), and replacing WebFetch for "answer a question about
this page" (WebFetch is the right tool for that; this skill is for producing
a faithful *document*).

## Core design insight

No single tool covers the space, but each gap is covered by exactly one cheap
addition — so the architecture is a ladder where **each rung is one command**
and escalation is decided by a deterministic sanity gate, not by guessing:

- trafilatura cannot render JS → the user's own Chrome can, at zero install.
- Chrome+trafilatura cannot pass anti-bot walls → neither can Jina (it
  explicitly does not circumvent site defenses), but Firecrawl's stealth
  proxy sometimes can.
- Every datacenter-side tier lacks the user's sessions → only the real
  browser (claude-in-chrome) can read login-walled/subscription content, with
  credentials never exposed to the agent.

The ladder is simultaneously the **cost/privacy policy**: content leaves the
machine only when local tiers provably failed; paid credits burn only when the
free remote tier failed.

## Architecture

```
Tier 0  probe      content-type / extension / magic-bytes check
                   → PDF file: download → pdf-to-markdown skill
                   → office file: download → doc-to-markdown skill
Tier 1  static     curl (realistic UA, redirects, timeout)
                   → trafilatura --markdown                    [local, free]
Tier 2  rendered   Chrome --headless --dump-dom → trafilatura  [local, free]
Tier 3  jina       GET https://r.jina.ai/<url>
                   Accept: application/json                    [remote, keyless]
Tier 4  firecrawl  POST /v2/scrape, proxy auto→stealth         [remote, keyed,
                                              gated on env-file existence]
Tier 5  browser    claude-in-chrome workflow (SKILL.md, not code):
                   login walls, subscriptions, infinite scroll  [manual]
Output             <slug>.md with OKF frontmatter + tier provenance;
                   every attempted tier and its gate verdict recorded
```

### Tier 0 — probe

`curl -sIL` (HEAD, follow redirects) for `Content-Type`, falling back to a
ranged GET + magic bytes when servers lie. `application/pdf` → download to a
temp file and tell the agent to run `pdf-to-markdown` on it; office MIME
types/extensions → `doc-to-markdown`. HTML/unknown → Tier 1. This keeps one
OCR/verification pipeline per format instead of a worse duplicate here (Jina
and Firecrawl can both read PDFs, but without pdf-to-markdown's verification
and per-page accounting).

### Tier 1 — static fetch + trafilatura

`curl` with a realistic browser UA and sane timeout, piped into
**trafilatura** (`pip install trafilatura`, pure Python + lxml, no browser).
Why trafilatura and not Readability/newspaper/resiliparse:
- Maintained quality leader: F1 ≈ 0.945 on the ScrapingHub
  article-extraction benchmark vs ≈ 0.80 for Mozilla Readability; top of its
  own wider evaluation (F ≈ 0.909). Resiliparse trades precision for recall
  (22.8% boilerplate admitted vs trafilatura's 6.6%) — wrong trade for a
  quality-focused skill. ReaderLM-style model extraction underperforms
  heuristics on multi-type benchmarks (WCXB F1 0.741).
- It collapses extract + convert into one step: native `--markdown` output
  (GFM-ish, tables on by default, code-block support in the 2.x line) plus
  metadata extraction (title/author/date/sitename) that feeds OKF directly.
Options: `--links --images` on; if table/code fidelity ever disappoints, put
pandoc or the Go `html-to-markdown` binary behind trafilatura's XML output
rather than switching extractors.
Soft-paywall note: static fetch sometimes beats a rendering browser here —
content present in HTML but hidden by JS/CSS survives Tier 1.

### Tier 2 — local JS rendering via the user's Chrome

`"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless
--dump-dom <url>` piped into the same trafilatura step. Zero install; renders
SPAs. Caveats: fresh profile (no cookies), no wait heuristics beyond page
load, occasional flag churn across Chrome versions — acceptable for a
fallback tier. Playwright was rejected: 200–600 MB of browser payload to
solve the same problem for a low-volume skill. No public measurement of "what
fraction of pages need JS" is rigorous; working assumption ~1 in 3 overall,
much lower for articles/blogs/docs (weakly evidenced — don't over-optimize
around it).

### Tier 3 — Jina Reader, strictly keyless

`GET https://r.jina.ai/<url>` with `Accept: application/json` (structured
envelope: title, description, content, links, usage) and a client timeout
(~30–60 s). Verified live (2026-08-16): the keyless tier is fully alive —
**20 requests/min per IP**, JSON mode, JS rendering, selector headers
(`X-Target-Selector`, `X-Remove-Selector`, `X-Wait-For-Selector`), engine
selection, `X-No-Cache` all work without a key. The user has no Jina key and
the design assumes none, ever: 20 rpm is ample at this volume.
Useful knobs: `X-No-Cache: true` when freshness matters (stale cached
snapshots several days old were observed); `X-Engine: browser` to force full
rendering.
Known limits (why Tier 4 exists): does NOT bypass Cloudflare/DataDome (and
paid tiers wouldn't change that); heavy shadow-DOM SPAs can return shells;
no sessions → paywalls fail. Quality: excellent on prose; leaks nav
boilerplate on complex layouts (mitigate with `X-Remove-Selector` if a site
matters). Strategic note: Jina was acquired by Elastic (Oct 2025); API
unchanged so far, continuity worth watching. Reader repo is Apache-2.0 and
self-hostable as a hedge, but hosted-keyless is the design.

### Tier 4 — Firecrawl (the only keyed tier)

`POST https://api.firecrawl.dev/v2/scrape` with defaults — they are already
right for this use case: `formats: ["markdown"]`, `onlyMainContent: true`,
JS rendering included, `proxy: "auto"` (escalates to stealth automatically:
1 credit basic, 5 credits stealth), `blockAds: true`. Official CLI
(`firecrawl-cli`) honors `FIRECRAWL_API_KEY`, which drops straight into the
repo's pass-cli secrets pattern; plain curl is equally fine.
Economics (verified Aug 2026): **free tier is 1,000 credits/month,
recurring** (improved from the earlier one-time cap), no card required, no
rollover; paid is subscription-only (Hobby $16/mo — no pay-as-you-go). At
this skill's volume the free tier is effectively unlimited.
Failure semantics to handle: 402 = credits exhausted (→ Tier 5 pointer),
429 = rate/concurrency, blocked pages surface in `metadata.statusCode`/
`metadata.error`. Independent benchmarking says Firecrawl beats Jina on
boilerplate removal but is mediocre against hard anti-bot walls (~34% on
protected-site benchmarks) — stealth is a better shot, not a guarantee.
Self-hosting was evaluated and rejected: AGPL core, five-service docker
stack, and the anti-bot layer (Fire-engine) is not open source — you'd run
infrastructure to get a *worse* scraper than the free cloud tier.
Privacy note for the runbook: scraped content is cached on Firecrawl's US
infra by default (`maxAge` 2 days); `storeInCache: false` is available; true
zero-data-retention is Enterprise-only.

### Tier 5 — claude-in-chrome (manual workflow, not code)

For pages where every datacenter tier is structurally wrong: login-required,
subscription/paywalled content the user legitimately has access to,
cookie-consent walls that gate server-side, infinite-scroll or
click-to-expand pages. The SKILL.md workflow: open the page in the user's
real Chrome via the Chrome integration (real session, real fingerprint;
Claude pauses for the human at logins/CAPTCHAs), let the page settle /
perform the scrolling, then capture the rendered DOM and run it through the
same trafilatura step so output format stays identical. This tier is
instructions for the agent, not pipeline code — same pattern as
pdf-to-markdown's vision tier.

## The sanity gate (escalation decider)

Deterministic checks on every tier's output; failure → next tier. Chosen over
cross-tier comparison (running two tiers and diffing) because web pages have
no ground truth and benign trimming differences would make disagreement
noisy; the gate targets the actual observed failure modes:

- **Empty/near-empty** extraction, or extracted text ≪ raw-HTML text mass
  (app-shell smell — the SPA returned a skeleton).
- **Challenge markers**: "Just a moment…", "Enable JavaScript and cookies",
  "Verifying you are human", Turnstile/DataDome artifacts.
- **Consent/error pages**: cookie-wall phrases dominating the content;
  soft-404 (title says error/not-found while HTTP said 200); login-form
  dominance (password field + minimal content → route to Tier 5, not Tier 4,
  since no proxy fixes a login wall).
- HTTP-level: 401/403/429 and CAPTCHAs route straight past Tier 2 (rendering
  locally won't fix them).

**No silent failure**: the output records which tier produced the result and
which tiers failed with which gate verdict; if Tier 4 also fails, the skill
reports that honestly with the Tier-5 pointer instead of emitting a
challenge page as if it were content. This is pdf-to-markdown's per-page
accounting philosophy scaled to a per-URL problem.

**Post-implementation hardening (adversarial review, 2026-08-16).** The
first-shipped gate was reviewed by crafting bypass pages; six confirmed
escapes were fixed and are pinned by regression fixtures/checks:
honest 404/410/5xx statuses with wordy branded bodies now gate on the
status itself (content served under an error status is not the requested
page); strong challenge phrases ("just a moment", "checking your browser",
…) fire at any content length, not only on short pages (padding had
defeated the dominance guard — vendor names like "cloudflare" stay
dominance-guarded so articles about them pass); login walls wrapped in
marketing prose are caught by a scaled multi-phrase rule; the marker lists
carry German/Spanish/French/Italian/Portuguese wall vocabulary; a GARBLED
verdict catches U+FFFD-dense extractions (and tier 1 now honors the
Content-Type header charset, the case that manufactured them); soft-404
title patterns include the "Oops!" family. Remaining accepted limitations,
documented rather than fixed: the marker lists cover the top EU languages
only — stock walls in other locales can still pass (the ladder's remote
tiers and the user's own reading remain the backstop); an article literally
quoting a full challenge phrase escalates to exit 4 (visible false
positive, preferred over the silent false negative); `<noscript>` SEO text
extracted by tier 1 is accepted as content by the soft-paywall principle
even though a browser user wouldn't see it; local file:// URLs are never
sent to remote tiers (they'd leak local paths for zero benefit — this also
keeps the offline test suite genuinely offline).

## Output contract

- `<slug>.md` (slug from title or URL path; default cwd, `-o`/`--output-dir`
  like the sibling skills — and like them, private page content should be
  exported *outside* this public repo's working directory).
- OKF v0.2 frontmatter per repo convention (copy shapes from
  `yt-transcript/scripts/yt_transcript.py` / `granola/scripts/granola_client.py`):
  type, title/author/published date (trafilatura metadata or Jina envelope),
  description, `resource:` = the URL, tags,
  `generated.by: machine:url-to-markdown/<tier>`, `generated.at`, sources,
  plus a `conversion:` block with the tier ladder attempted and gate
  verdicts.
- Exit codes: 0 success; 1 unexpected; 2 missing dependency (trafilatura /
  curl) — granola-style gate with install instructions; 4 all automatic
  tiers failed (Tier-5 handoff, mirroring pdf-to-markdown's exit-4 vision
  handoff); distinct code for "refused: Tier 0 detected a file URL" with the
  sibling-skill pointer (doc-to-markdown uses 3 for its refusal — keep that
  convention).

## Secrets (per CLAUDE.md — one key total)

- Optional `url-to-markdown/url-to-markdown.env` (gitignored; committed
  `.env.example`): `FIRECRAWL_API_KEY=pass://Firecrawl/<item>/<field>`.
  Launcher resolves via `pass-cli run --env-file` — never any other way.
- Tiers 0–3 must work with **zero configuration**; Tier 4 gates on the env
  file's existence (exit-2 pattern with setup instructions on stderr).
- Jina: strictly keyless — no key exists and none is wanted; do not add a
  `JINA_API_KEY` slot "just in case".
- Vault = service, agent = skill: agent `url-to-markdown-reader`, viewer
  role, on the **Firecrawl** vault. If the user's Firecrawl key still lives
  in the legacy shared "Agents" vault, the runbook flags the migration to a
  dedicated single-item "Firecrawl" vault as a user decision — the skill
  never performs vault surgery.

## Tools evaluated and rejected (don't re-litigate without new facts)

| Option | Verdict | Reason |
|---|---|---|
| **WebFetch (built-in)** | wrong tool for this job | Summarizes via a small model (lossy, non-reproducible), no JS rendering, truncates large pages. Right for Q&A about a page; this skill exists precisely for the faithful-document case. |
| **Mozilla Readability / readability-cli** | rejected | Benchmarks ~F 0.80 vs trafilatura's ~0.94; outputs HTML so it still needs a converter; core lib slow-moving, original CLI unmaintained. |
| **Resiliparse / boilerpipe lineage / jusText** | rejected | Resiliparse wins on speed (irrelevant here) by admitting 3× more boilerplate; boilerpipe dead; jusText survives only as trafilatura's internal fallback. |
| **Playwright (local rendering)** | rejected | 200–600 MB browser payload to duplicate what the user's installed Chrome does for free at this volume. Revisit only if Chrome-flag churn becomes a real maintenance burden. |
| **monolith / single-file-cli** | rejected | Archivers, not extractors; monolith has no JS engine and itself recommends the Chrome `--dump-dom` trick this design uses directly. |
| **pandoc / turndown / markdownify / html-to-markdown as primary** | deferred | Unnecessary while trafilatura emits markdown natively; pandoc or the Go html-to-markdown binary is the designated upgrade path behind trafilatura's XML if table/code fidelity disappoints. |
| **pure.md, Tavily Extract, Exa /contents, markdown.new** | rejected | Redundant given keyless Jina + free-tier Firecrawl; each is another account/key or a niche constraint (Tavily ~$0.04/page; pure.md small-operator availability risk; markdown.new Cloudflare-Pro-sites-only). |
| **Self-hosted Firecrawl** | rejected | AGPL five-service stack; the anti-bot layer (Fire-engine) is closed — strictly worse than the free cloud tier for one-off scrapes. |
| **Self-hosted Jina Reader** | not now | Apache-2.0 and viable, but hosted-keyless costs nothing; noted only as a hedge against post-Elastic consolidation. |
| **Local anti-bot evasion (stealth plugins, FlareSolverr, curl-impersonate alone)** | rejected on principle | 2026 consensus: dead or dying against modern TLS/fingerprint weighting; an arms race a skill should not be in. Walled pages go to Firecrawl stealth or the user's real browser. |

## Known risks / caveats to carry into implementation

- **Jina/Elastic continuity**: API unchanged since the Oct 2025 acquisition,
  but the long-term standalone future is uncertain; the tier ladder degrades
  gracefully (Tier 3 dropping out leaves 1-2-4-5 functional).
- **Firecrawl free-tier terms can change** (they already changed twice in
  2026, in both directions); the 402 path must read as "tier unavailable",
  never as a hard failure of the skill.
- **Chrome flag churn**: `--headless --dump-dom` semantics have shifted
  across major Chrome versions; keep the invocation isolated in one function
  with a comment, and treat "Chrome absent/failed" as gate-fail → Tier 3,
  not as an error.
- **Stale Jina cache**: snapshots days old were observed; default to
  `X-No-Cache: true` (correctness over speed, per this repo's taste) and
  note the token cost is irrelevant keyless.
- **Consent-wall vs consent-noise**: DOM extractors usually drop CMP markup;
  the gate must distinguish "consent text present as noise" (fine, strip)
  from "consent text IS the content" (gate-fail).
- **Legal/ToS posture**: the skill fetches single pages the user explicitly
  asks for, respects the target's availability decisions (no evasion
  locally), and routes legitimately-authorized gated content through the
  user's own browser session — keep this framing in SKILL.md.

## Future work

- **Fidelity upgrade path**: pandoc or Go `html-to-markdown` behind
  trafilatura XML output if GFM table/code quality disappoints on real use.
- **Batch mode** (multiple URLs, one invocation) only if real usage demands
  it — the rate limits (Jina 20 rpm) and Firecrawl credits are per-URL
  concerns the current design ignores safely at low volume.
- **`--tier` override flag** for debugging/forcing (e.g. `--tier jina`) —
  cheap to add during implementation.
- **Defuddle** (Obsidian Web Clipper's Readability successor) — young;
  re-check benchmarks in a year as a possible trafilatura complement.

## Implementation recipe (when the time comes)

Reuse the pdf-to-markdown build process: freeze a `contract.py` (tier result
shape, gate verdict enum, exit codes) → parallel module agents (tier
wrappers: Sonnet; sanity gate + escalation orchestrator: Fable) → integration
→ adversarial review hunting for gate bypasses (a challenge page emitted as
content is this skill's "silent loss"). Fixtures: saved HTML of a real
article, an app shell, a Cloudflare challenge page, a consent wall, a
soft-404 — served from local files so tests never depend on the network.

## Key sources

- Jina: [jina.ai/reader](https://jina.ai/reader/) · [github.com/jina-ai/reader](https://github.com/jina-ai/reader) (Apache-2.0) · live keyless tests 2026-08-16 (20 rpm header, JSON envelope, stale-cache observation) · [Elastic acquisition](https://finance.yahoo.com/news/elastic-completes-acquisition-jina-ai-130200685.html)
- Firecrawl: [pricing](https://www.firecrawl.dev/pricing) (1,000 credits/mo free, recurring; no PAYG) · [/v2/scrape API](https://docs.firecrawl.dev/api-reference/endpoint/scrape) · [proxies/stealth](https://docs.firecrawl.dev/features/stealth-mode) · [self-host guide](https://docs.firecrawl.dev/contributing/self-host) · [Apify: Jina vs Firecrawl](https://blog.apify.com/jina-ai-vs-firecrawl/) · [protected-site benchmark cite](https://prospeo.io/s/firecrawl-alternatives)
- Local: [trafilatura](https://trafilatura.readthedocs.io/) + [evaluation](https://trafilatura.readthedocs.io/en/latest/evaluation.html) · [ScrapingHub benchmark](https://github.com/scrapinghub/article-extraction-benchmark) · [WCXB multi-type benchmark](https://arxiv.org/html/2605.21097) · [Chrome headless --dump-dom (Willison)](https://til.simonwillison.net/chrome/headless) · [Playwright footprint](https://datawookie.dev/blog/2025-06-06-playwright-browser-footprint/)
- Claude Code: [Chrome integration docs](https://code.claude.com/docs/en/chrome) · WebFetch internals (community reverse-engineering; summarize-not-fetch behavior, no-JS, truncation)
- Anti-bot reality: [Scrapfly 2026](https://scrapfly.io/blog/posts/how-to-bypass-cloudflare-anti-scraping) and related — local evasion is a losing arms race.
