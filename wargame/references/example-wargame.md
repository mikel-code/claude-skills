# Example battle plan (the quality bar)

> **This is one concrete example, not the domain of the method.** It happens to be a
> capture pipeline (banking a source URL into a knowledge vault) because that's the target
> that was in front of the author with real code to cite. **The method is general** — it
> applies to any high-stakes, failure-prone, or unattended subsystem. Wargame an **API
> endpoint** (auth, rate limits, partial writes, idempotency), a **payment / checkout flow**
> (double-charge, webhook races, refund edges), a **data migration** (partial apply,
> rollback, schema drift), a **deploy / release** (health checks, rollback triggers, config
> drift), a **cron / scheduled job** (overlap, minimal-PATH, missed runs), an **auth flow**,
> a **cache-invalidation path**, an **ETL job**, and so on. Do **not** anchor on the shape
> below (fetch → validate → seal); each target has its own surfaces.
>
> What to copy is the **depth and rigor**, not the domain: cited ground truth, numbered
> moves each with an expected observation + failure + counter-move, forks with explicit
> triggers, `RECON NEEDED` with the exact settling check, abort conditions, verification runs
> with real commands, and a red-team pass that records both an attack that **failed** and one
> that **landed** with the patch it forced. Note how the highest-value finding came straight
> from the "success ≠ correct content" lens — a 200 that isn't the content. That lens is
> domain-independent; your target's wargames should read like this.

---

# WARGAME 02 · Public-web capture (Jina Reader, keyless)

**Mission** · A public URL becomes a sealed `raw/articles/` file with real content, or an honest `needs-attention.md` entry — never a page of login HTML sealed as an article.
**Executor** · Sonnet + Claude Code, cwd `~/second-brain`.
**Ground truth** · `capture_url.sh` lines 161–189 (web branch); `secrets.toml` (jina/firecrawl `enabled = false`); SKILL.md §6.2 routing table; the resolved Jina entry for `jalammar.github.io/illustrated-transformer` in `needs-attention.md`.

The route makes two curl attempts to `https://r.jina.ai/$SRC`:
- **A1**: with `X-Respond-With: frontmatter` **and** `X-With-Generated-Alt: true`.
- **A2** (on A1 failure): same, WITHOUT the alt header (keyless tier resets connections when alt is set).
Then: empty-body guard, title extraction (Jina frontmatter → `Title:` line → first `# ` → slug-from-URL).

---

## Moves

### M1 — Fetch attempt A1 (with generated alt-text)
Run `capture_url.sh <public-url>`.
- **Expected observation** · curl exits 0, `$BODY_FILE` non-empty, markdown body with image alt text populated.
- **Likely failure** · Keyless tier resets the connection specifically because `X-With-Generated-Alt: true` is set. **Signal**: A1 curl non-zero, `curl: (56) recv failure` or connection reset. **Counter-move**: automatic — the script falls to A2 without the alt header. Do not treat A1 failure as capture failure.

### M2 — Fetch attempt A2 (fallback, no alt)
Automatic if M1 failed.
- **Expected observation** · curl exits 0, body non-empty; images keep remote alt text or none. Asset localization (wargame 06) still runs later.
- **Likely failure** · Both A1 and A2 fail (login wall / blocked / genuine network). **Signal**: second curl non-zero. **Counter-move**: `needs_attention "Jina Reader fetch failed (login wall / blocked / network)"`, exit 1. This is correct terminal behavior for this route.
- **Fork** · If A2 returns HTTP 200 but the body is a login/paywall stub (Jina rendered the "please sign in" page) → **trigger**: body is non-empty so the `-s` guard passes, but content is a wall. Route B: detect the wall (see M4), and DO NOT seal; convert to a needs-attention entry with "login wall — browser-session capture required".

### M3 — Empty-response guard
- **Expected observation** · `[[ -s "$BODY_FILE" ]]` true; proceed.
- **Likely failure** · 200 with empty body (Jina hiccup). **Signal**: zero-byte body. **Counter-move**: `needs_attention "Jina Reader returned empty response"`, exit 1 → later resolved by `--retry` (wargame 10).

### M4 — Title extraction + wall detection
Extract title from Jina frontmatter block, else `Title:` line, else first `# `, else slug-from-URL.
- **Expected observation** · A real article title; the Jina frontmatter block is stripped so our frontmatter replaces it.
- **Likely failure** · Title resolves to something like "Sign in" / "Log in" / "Just a moment..." (Cloudflare) / "Access denied". **Signal**: title matches a wall pattern; body is short (< ~500 words) and contains "sign in"/"subscribe to continue". **Counter-move**: treat as M2's wall fork — abort seal, file needs-attention. **This is the load-bearing check**: HTTP success ≠ content success.
- **Fork** · If title falls all the way through to slug-from-URL AND body is substantial → **trigger**: real article with an unparseable title. Route B: accept it; the slug is fine.

### M5 — Fallback ladder when keyless Jina is genuinely insufficient
The route's only built-in fallback is A2. Beyond that:
- **Expected observation** · You correctly identify that Firecrawl is `enabled = false` in `secrets.toml` and therefore NOT available without the secrets liturgy (wargame 12). You do not invoke it.
- **Likely failure** · Executor "enables" Firecrawl or hard-codes a key to force the capture. **Signal**: an edit to `secrets.toml` or a key in the command. **Counter-move**: forbidden. The sanctioned fallback is browser-session capture: the human opens the page, the content is filed through `capture_url.sh` as a LOCAL file with `--type` and the real `source_url` (use `--no-seal` so `source_url` can be corrected before seal).
- **Fork** · If the page is login-walled and the human is present → **trigger**: browser-session capture. If unattended (drain) → **trigger**: leave the needs-attention entry Open for the next interactive `--retry`.

### M6 — Seal
Frontmatter assembled (`source_url`, `captured_at`, `capture_method=jina-reader`, `tool_version`, `type=article`, optional `note`), asset localization runs, then `seal.py`.
- **Expected observation** · `raw/articles/<today>-<slug>.md` sealed; line in `wiki/log.md`.
- **Likely failure** · Frontmatter incomplete → seal fails, file deleted, needs-attention. **Signal**: no sealed file, "seal failed" entry. **Counter-move**: the seal step owns this; here just confirm the web route produced complete frontmatter.

---

## RECON NEEDED
- **R1** · What does `r.jina.ai` return for a specific paywalled target (NYT/Medium-metered) — a 403, or a 200 wall stub? **Check**: `curl -si -H 'X-Respond-With: frontmatter' https://r.jina.ai/<paywalled-url> | head -30` and inspect status + first lines. Settles whether M2 or M4 catches it.
- **R2** · Current keyless rate limit before 429. **Check**: capture 5 URLs in quick succession; watch for the first curl failure that is a 429 vs a reset. If 429s appear fast, batch drains must space requests. Cannot be assumed — measure.

## Abort conditions
- Body is a login/paywall/anti-bot wall (M4 patterns) → **abort seal**, file needs-attention "login wall — browser-session capture required". Never seal a wall.
- Both curl attempts fail → **abort**, the script's own needs-attention entry stands; do not retry more than the script's `--retries`.
- Any impulse to enable Firecrawl or inject a key to force the fetch → **abort**, hand to the secrets liturgy or browser-session capture.

## Verification runs
1. **Happy path**: `capture_url.sh 'http://www.incompleteideas.net/IncIdeas/BitterLesson.html'` (known-good) on a scratch — **pass**: sealed article with real body, `capture_method: jina-reader` in frontmatter.
2. **Empty/blocked**: point at a known-403 URL → **pass**: exit 1, `## Open` entry "Jina Reader fetch failed".
3. **Wall not sealed**: capture a metered-paywall article; grep the result — **pass**: EITHER a needs-attention entry OR, if it sealed, the body must be a full article, not a "subscribe to continue" stub. If a stub sealed, that is a FAIL and proves M4's wall check must run before seal.
4. **Alt fallback**: force A1 failure (unreachable alt) and confirm A2 still captures (check `capture_method` sealed) — proves the two-attempt ladder.
5. **No key leak**: `git -C ~/second-brain grep -i 'jina.*key\|firecrawl' -- raw/` returns nothing — the web route never wrote a key into content.

## Red-team pass
- **Attack that FAILED** · "Both curls fail silently and the script seals an empty file." Blocked by the `[[ -s "$BODY_FILE" ]]` guard (M3) plus seal.py's frontmatter+body assertions — an empty body cannot seal. Defense held.
- **Attack that LANDED → patch** · "Serve a 200 login-wall stub." A1/A2 both succeed HTTP-wise, body is non-empty (guard passes), title is 'Sign in', and the stub sealed as a legitimate `raw/articles/` file — a silent, wrong, successful-looking capture. The script has NO wall detection. **Patch**: added M4's explicit wall-pattern check (title ∈ {sign in, log in, just a moment, access denied} AND short body with "subscribe"/"sign in") as a pre-seal gate, verification step 3 to assert it, and the browser-session counter-move. This is the highest-value patch in the route: keyless Jina's most common real failure is a 200 that is not the content.
