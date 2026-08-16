---
name: url-to-markdown
description: "Scrape a web URL into a markdown file with Open Knowledge Format frontmatter, through a cheap-and-private-first tier ladder: local trafilatura extraction, headless Chrome rendering, keyless Jina Reader, optional Firecrawl — a sanity gate decides escalation, and login-walled or interactive pages fall back to a claude-in-chrome workflow. Use for /url-to-markdown, \"save this page/article as markdown\", \"scrape this URL\", or \"turn this link into a document\". URLs that point at PDF or office files are handed off to pdf-to-markdown / doc-to-markdown."
license: MIT
compatibility: Requires Python 3.10+ with uv and curl. Tier 2 uses the locally installed Google Chrome (skipped gracefully if absent); tier 4 (Firecrawl) is optional and needs one-time Proton Pass setup.
---

# url-to-markdown

Deterministic, script-driven URL scraping with full tier provenance. The
script runs the ladder (static → Chrome render → Jina → Firecrawl) and a
sanity gate between every rung, so a Cloudflare challenge, consent wall, or
SPA shell is **never** saved as if it were the page. `references/design.md`
records the design and its rationale; read it before changing anything.

Not a WebFetch replacement: use WebFetch to *answer a question about* a page;
use this skill to *save a faithful markdown document* of it.

## Convert

```bash
<skill-dir>/scripts/url_to_markdown.sh <url> [-o out.md | --output-dir DIR]
#   --json     machine-readable result (tier used, full attempt ladder)
#   --tier X   force one tier: static | chrome | jina | firecrawl (debugging)
```

The launcher runs with zero configuration (tiers 0–3 need no keys). If
`url-to-markdown.env` exists, it additionally resolves the Firecrawl key via
Proton Pass and unlocks tier 4 — setup in
[references/proton-pass-setup.md](references/proton-pass-setup.md), which the
**user must run themselves** (raw credentials never flow through the agent).

Default output: `<slug>.md` in the cwd. Scraped content of private/paywalled
pages is the user's data — default the output outside any public repo
working directory.

## Exit codes → what you do next

- **0** — converted. stderr shows which tier produced it and what each
  earlier tier reported; the OKF `conversion:` block records the same.
- **3** — the URL is a file, not a page. stderr names the downloaded copy
  and the sibling skill to run (`pdf-to-markdown` / `doc-to-markdown`) — do
  that next.
- **4** — every automatic tier failed (each one's verdict is listed). Do the
  tier-5 workflow below if the page is worth it; otherwise report the
  verdicts honestly to the user.
- **2** — missing dependency; stderr says what to install.
- **5** — the launcher's Proton Pass machinery failed (stale personal
  session or expired agent token) before the pipeline ran; stderr names the
  fix, and the runbook has the renewal steps. Never attempt the login or
  renewal yourself — they carry raw credentials only the user may handle.

## Tier-5 workflow (you + the user's real browser, on exit 4)

For login-walled, subscription, cookie-gated, or interactive (infinite
scroll / click-to-expand) pages — the cases where no datacenter-side tier
can ever be correct, because only the user's browser has their session.

1. Confirm with the user that fetching this page through **their** browser
   session is what they want (it will use their logins).
2. Using the Chrome browser integration, open the URL in their Chrome. If a
   login or CAPTCHA appears, hand control to the user — never try to get
   past it yourself.
3. Let the page settle; perform whatever interaction reveals the content
   (scroll to the end, expand sections).
4. Capture the rendered page content and save it through the same pipeline
   as the other tiers so output format stays identical — e.g. write the DOM
   to a temp file and run
   `scripts/url_to_markdown.py file:///tmp/…page.html --tier static -o <out>`.
5. Note in your reply that the content came via the user's own browser
   session (the OKF `conversion:` block will show the failed automatic
   tiers; that's expected and correct).

Never work around a block by other means (proxies, header spoofing beyond
the ladder's normal behavior, third-party mirror sites): the ladder IS the
policy — if it fails and tier 5 doesn't apply, the answer is "this page
can't be fetched", reported plainly.

## Routing (informational)

Tier 0 probes content-type/magic bytes and hands file URLs to the sibling
converter skills. Tier 1 is curl + trafilatura (local, covers most articles).
Tier 2 renders JS with the user's installed Chrome (local; skipped when the
static tier already hit an HTTP block — rendering can't fix a 403). Tier 3
is Jina Reader, strictly keyless (20 req/min per IP; it does not bypass
anti-bot walls). Tier 4 is Firecrawl (`proxy: auto` escalating to stealth;
free tier 1,000 credits/month; only runs when configured). URLs containing
embedded credentials are never sent to the remote tiers. A login-wall
verdict stops escalation entirely — no proxy fixes a login page.

## Troubleshooting

- **"missing dependency" (exit 2)** → install what stderr names.
- **Everything fails with `challenge`** → the site is behind aggressive
  anti-bot protection; tier 4 (if configured) was the best automatic shot.
  Tier 5 or "can't fetch" are the remaining honest options.
- **`firecrawl … not-configured` in the ladder** → expected when the env
  file doesn't exist; set up via the runbook only if the user wants tier 4.
- **Firecrawl 402 / credits exhausted** → free tier resets monthly; tier 5
  in the meantime.
- **Jina rate-limited (429)** → keyless limit is 20/min per IP; wait a
  minute and retry.
- **Output has nav/boilerplate noise** → try `--tier firecrawl` (cleanest
  boilerplate removal) or trim by hand; note it in your reply.
