"""Sanity gate for the url-to-markdown tier ladder.

evaluate(out) decides whether a tier's output is real content (OK) or one of
the observed failure modes (escalate). Design authority:
references/design.md, "The sanity gate". Contract: contract.py (frozen).

Bias: a false OK (challenge page emitted as content) is the skill's silent
loss; a false escalation costs one cheap tier hop. So checks lean toward
firing — but every text-marker check carries a *dominance guard* so that a
long real article ABOUT Cloudflare, cookies, or 404 pages never escalates.

HTTP statuses are trusted at face value
---------------------------------------
Any status >= 400 short-circuits the text checks entirely: content served
under an error status is not the requested page, no matter how long or
wordy the body is (branded 404s, padded challenge bodies under 403/503).
Adversarial review confirmed 4xx/5xx never accompany the real requested
document in practice, so the earlier "corroborate a 503 with challenge
markers" nuance was dropped in favor of the simple rule: 404/410 (honest
not-found) -> SOFT_404; every other >= 400 -> HTTP_BLOCKED.

Dominance model
---------------
All text checks run against the *visible* text of the markdown (links reduced
to their anchor text, images/URLs/markdown punctuation stripped, whitespace
collapsed). Real challenge/login/consent/error interstitials are short —
observed pages run well under ~1 500 visible chars — while articles that
merely *mention* the same vocabulary are long. So most markers in the BODY
only fire when the visible text is short; two classes fire regardless of
length because padding was an observed bypass:

- STRONG challenge phrases ("just a moment", "checking your browser", ...
  and their DE/ES/FR/IT/PT equivalents) fire from title OR body at ANY
  length — an adversary can pad a challenge page with prose, but these
  exact phrases never occur in real articles except as literal quotes.
  Residual risk (accepted): an article literally quoting a full challenge
  phrase escalates — a visible exit-4 on a rare page, not a silent loss.
  Vendor/artifact names (cloudflare, turnstile, datadome...) stay under the
  short-content guard so articles about them still pass.
- A SCALED login rule: >= LOGIN_SCALED_HITS distinct login phrases under
  LOGIN_SCALED_MAX visible chars fires even past the tight LOGIN_MAX guard
  (observed bypass: a login wall padded with membership marketing prose).
  A real article about authentication runs past 5 000 chars.

Thresholds (all tuned against the self-test fixtures below; each is a plain
module constant so future fixtures can retune them in one place):

  SHORT_DOMINANCE = 1500   vendor/tech challenge markers in the body count
                           only under this; real CF/DataDome interstitials
                           are < ~600. (Strong phrases: any length, above.)
  LOGIN_MAX = 1200         login vocabulary needs >= LOGIN_MIN_HITS (2)
  LOGIN_MIN_HITS = 2       distinct phrases in content this short; a single
                           "password" mention is too weak to fire.
  LOGIN_TITLE_MAX = 1500   "sign in"/"log in" in the <title> fires up to
                           here (title-match weighs more than body-match).
  LOGIN_SCALED_HITS = 4    scaled rule: this many distinct login phrases
  LOGIN_SCALED_MAX = 5000  under this many visible chars is a padded wall.
  SOFT404_TITLE_MAX = 2000 error pattern in <title> fires under this;
                           "The Art of the 404 Page" essays are longer.
                           Cheery variants ("oops", "whoops"...) are
                           title-only under the same guard.
  SOFT404_BODY_MAX = 800   error pattern in the LEADING 300 visible chars
                           fires only under this (body-match weighs less).
  CONSENT_TIGHT = 900      consent wall: >= 2 distinct phrases under 900
  CONSENT_LOOSE = 2500     chars, or >= 4 distinct phrases under 2500 (CMP
                           dialogs are wordy; long pages with consent
                           *noise* blow past 2500 and always pass).
  APP_SHELL_MIN_RAW = 3000 raw page must have real text mass for the ratio
                           to mean anything.
  APP_SHELL_ABS = 250      extraction under 250 visible chars from a raw
                           page over MIN_RAW, at ratio < APP_SHELL_RATIO,
                           is a shell. 250 sits above observed shell
                           extractions (nav crumbs, "Loading...", ~< 200
                           chars) and below legitimate-short-content
                           (a poem is ~300).
  APP_SHELL_RATIO = 0.05   guards the abs check: 240 chars from a 3.1k page
                           (ratio .077) is a stub site, not a shell.
  NEAR_EMPTY = 80          near-nothing extracted from any raw page >= 2000
                           chars is a shell regardless of ratio.
  EMPTY_MIN = 200          minimum viable content, measured with link
                           anchor text REMOVED (a page of nav links is not
                           content). ~200 chars = a couple of sentences;
                           below that nothing worth emitting was extracted.
  GARBLED_MIN = 20         mojibake: more than this many U+FFFD replacement
  GARBLED_RATIO = 0.05     chars AND more than this fraction of the
                           markdown is U+FFFD -> GARBLED (a legacy-charset
                           page decoded with the utf-8/replace fallback).

Check order is significance order: the most diagnostic verdict must fire
before generic EMPTY, because the verdict routes the escalation (LOGIN_WALL
-> Tier 5 straight away; HTTP_BLOCKED/CHALLENGE -> skip local rendering).
GARBLED runs before all text-marker checks: replacement-char-dense text
would make the marker scans read noise.
"""

from __future__ import annotations

import re

from contract import GateResult, GateVerdict, TierOutput

# ------------------------------------------------------------- thresholds

SHORT_DOMINANCE = 1500
LOGIN_MAX = 1200
LOGIN_MIN_HITS = 2
LOGIN_TITLE_MAX = 1500
LOGIN_SCALED_HITS = 4
LOGIN_SCALED_MAX = 5000
SOFT404_TITLE_MAX = 2000
SOFT404_BODY_MAX = 800
SOFT404_LEAD = 300
CONSENT_TIGHT = 900
CONSENT_TIGHT_HITS = 2
CONSENT_LOOSE = 2500
CONSENT_LOOSE_HITS = 4
APP_SHELL_MIN_RAW = 3000
APP_SHELL_ABS = 250
APP_SHELL_RATIO = 0.05
NEAR_EMPTY = 80
NEAR_EMPTY_MIN_RAW = 2000
EMPTY_MIN = 200
GARBLED_MIN = 20
GARBLED_RATIO = 0.05

# ---------------------------------------------------------------- markers

# STRONG phrase markers: interstitial wording that never titles OR fills a
# real article. These fire from the <title> and the BODY at ANY content
# length — padding a challenge page with prose was an observed bypass, so
# no dominance guard applies here. Residual risk (accepted by design): an
# article that literally quotes a full phrase ("checking your browser")
# escalates — visible exit-4, not silent loss. Non-English rows cover the
# stock Cloudflare/consent-vendor interstitials in the top-5 EU languages;
# accented and transliterated spellings both appear in the wild, so both
# are listed where they differ.
_CHALLENGE_PHRASES = (
    "just a moment",
    "checking your browser",
    "verifying you are human",
    "verify you are human",
    "enable javascript and cookies",
    "attention required! | cloudflare",
    # German
    "einen moment bitte",
    "überprüfung ihres browsers",
    "ueberpruefung ihres browsers",
    "wir überprüfen ihren browser",
    "wir ueberpruefen ihren browser",
    "aktivieren sie javascript und cookies",
    # Spanish
    "un momento por favor",
    "verificando su navegador",
    "espere mientras verificamos su navegador",
    "habilite javascript y cookies",
    # French
    "vérification de votre navigateur",
    "verification de votre navigateur",
    "nous vérifions votre navigateur",
    "nous verifions votre navigateur",
    "activez javascript et les cookies",
    # Italian
    "verifica del tuo browser",
    "abilita javascript e i cookie",
    # Portuguese
    "verificando seu navegador",
    "ative javascript e cookies",
)
# Tech markers: vendor/artifact names that DO appear in article titles/prose
# ("Understanding Cloudflare Turnstile"). These fire only under the
# short-content dominance guard, wherever they appear.
_CHALLENGE_TECH = (
    "cf-browser-verification",
    "challenge-platform",
    "cf-chl",
    "ddos protection by",
    "datadome",
    "perimeterx",
    "px-captcha",
    "turnstile",
)
# "Access denied" is too generic alone; it counts only alongside a
# Cloudflare ray-ID artifact.
_RAY_RE = re.compile(r"\bcf-ray\b|\bray id\b")

# Login vocabulary. Multi-word phrases match as substrings; risky single
# words (below) match with word boundaries. All body login rules require
# >= 2 distinct hits, so no single word is ever a lone trigger.
_LOGIN_PHRASES = (
    "log in",
    "login",
    "sign in",
    "signin",
    "sign up",
    "password",
    "create an account",
    "create account",
    "forgot password",
    "continue with google",
    "continue with facebook",
    "continue with apple",
    "already have an account",
    "subscribe to continue",
    # German
    "jetzt anmelden",
    "passwort vergessen",
    # Spanish
    "iniciar sesión",
    "iniciar sesion",
    "crear una cuenta",
    # French
    "se connecter",
    "mot de passe",
    "créer un compte",
    "creer un compte",
    # Portuguese
    "esqueceu a senha",
)
# Single-word login markers that are substrings of everyday words
# ("entrar" ⊂ "concentrar", "accedi" ⊂ "accediamo") — matched with word
# boundaries, and only ever counted toward multi-hit rules.
_LOGIN_WORDS = ("anmelden", "passwort", "contraseña", "accedi", "entrar",
                "senha")
_LOGIN_WORD_RES = tuple(
    (w, re.compile(rf"(?<!\w){w}(?!\w)")) for w in _LOGIN_WORDS)
_LOGIN_TITLE_RE = re.compile(r"\b(log ?in|sign ?in)\b|\banmelden\b"
                             r"|iniciar sesi[oó]n|se connecter|\baccedi\b")

_SOFT404_RE = re.compile(
    r"\b404\b"
    r"|page not found"
    r"|\bnot found\b"
    r"|(does not|doesn'?t) exist"
    r"|no longer available"
    r"|page (is )?unavailable"
)
# Cheery soft-404 titles ("Oops! | Example News") — title-anchored only,
# under the same SOFT404_TITLE_MAX guard; too colloquial to trust in body
# prose.
_SOFT404_CHEERY_RE = re.compile(
    r"\b(oops|whoops|uh[ -]?oh)\b|well, this is awkward")

_CONSENT_PHRASES = (
    "we value your privacy",
    "accept all cookies",
    "accept cookies",
    "reject all",
    "manage preferences",
    "manage your preferences",
    "cookie settings",
    "consent",
    "legitimate interest",
    # CMP vendors
    "onetrust",
    "didomi",
    "quantcast",
    "cookiebot",
    "usercentrics",
    "trustarc",
    "sourcepoint",
    "iab tcf",
    # German
    "wir verwenden cookies",
    "alle akzeptieren",
    "cookies akzeptieren",
    "alle ablehnen",
    "einwilligung",
    "einstellungen verwalten",
    # Spanish
    "valoramos tu privacidad",
    "acepta las cookies",
    "aceptar todas",
    "aceptar cookies",
    "rechazar todas",
    "consentimiento",
    "gestiona tus preferencias",
    # French
    "tous les cookies",
    "accepter les cookies",
    "j'accepte",
    "j’accepte",
    "consentement",
    "gérer les préférences",
    "gerer les preferences",
    # Italian
    "accetta tutti",
    "accetta i cookie",
    "rifiuta tutti",
    # Portuguese
    "aceitar todos",
    "aceitar cookies",
    "recusar todos",
    "consentimento",
)

# ------------------------------------------------------- text measurement

_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_KEEP_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_AUTOLINK_RE = re.compile(r"<https?://[^>]+>")
_BARE_URL_RE = re.compile(r"https?://\S+")
_MD_PUNCT_RE = re.compile(r"[#>*_`~|]+")
_WS_RE = re.compile(r"\s+")


def _visible_text(md: str, *, keep_link_text: bool = True) -> str:
    """Markdown -> approximate visible text. keep_link_text=False also drops
    link anchor text (used for the EMPTY minimum: nav links aren't content)."""
    t = _IMG_RE.sub(" ", md)
    t = _LINK_KEEP_RE.sub(r"\1" if keep_link_text else " ", t)
    t = _AUTOLINK_RE.sub(" ", t)
    t = _BARE_URL_RE.sub(" ", t)
    t = _MD_PUNCT_RE.sub("", t)
    return _WS_RE.sub(" ", t).strip()


def _norm(s: str | None) -> str:
    """Lowercase + collapse whitespace; keeps punctuation so markers like
    'attention required! | cloudflare' and 'cf-browser-verification' match."""
    return _WS_RE.sub(" ", (s or "").lower()).strip()


def _dedupe_hits(hits: list[str]) -> list[str]:
    """Drop hits that are substrings of another hit, so one word never
    counts twice ('consent' ⊂ 'consentimiento'/'consentement')."""
    return [h for h in hits if not any(h != o and h in o for o in hits)]


def _login_hits(body_l: str) -> list[str]:
    hits = [p for p in _LOGIN_PHRASES if p in body_l]
    hits += [w for w, rx in _LOGIN_WORD_RES if rx.search(body_l)]
    return _dedupe_hits(hits)


def _consent_hits(body_l: str) -> list[str]:
    return _dedupe_hits([p for p in _CONSENT_PHRASES if p in body_l])


def _challenge_marker(title_l: str, body_l: str, vlen: int) -> str | None:
    """Return a detail string if challenge markers fire, else None. Strong
    phrases fire at any length; tech markers only under the dominance
    guard (see the marker-list comments for the rationale)."""
    for m in _CHALLENGE_PHRASES:
        if m in title_l:
            return f"challenge marker '{m}' in title"
    for m in _CHALLENGE_PHRASES:
        if m in body_l:
            return f"strong challenge phrase '{m}' in content ({vlen} chars)"
    if vlen <= SHORT_DOMINANCE:
        for m in _CHALLENGE_TECH:
            if m in body_l or m in title_l:
                return (f"challenge marker '{m}' in short content "
                        f"({vlen} chars)")
        if "access denied" in body_l and _RAY_RE.search(body_l):
            return (f"'access denied' + ray-id artifact in short content "
                    f"({vlen} chars)")
    return None


# ------------------------------------------------------------------- gate

def evaluate(out: TierOutput) -> GateResult:
    md = out.markdown or ""
    body_l = _norm(md)
    # page_title is the raw <title>; fall back to extracted title when the
    # tier couldn't capture the raw one (e.g. some remote envelopes).
    title_l = _norm(out.page_title if out.page_title is not None else out.title)
    visible = _visible_text(md)
    vlen = len(visible)
    nolink_len = len(_visible_text(md, keep_link_text=False))

    # 1. HTTP status >= 400 — trusted at face value. Content served under an
    #    error status is not the requested page, however long the body is
    #    (branded 404 pages, padded challenge bodies): the tier honestly
    #    told us. 404/410 are honest not-found -> SOFT_404 (same routing as
    #    a soft 404 behind a 200); everything else (400, 401, 403, 405,
    #    407, 429, 5xx...) -> HTTP_BLOCKED. No content-corroboration
    #    nuance: adversarial review found no real 4xx/5xx response that
    #    carried the requested document.
    st = out.http_status
    if st is not None and st >= 400:
        if st in (404, 410):
            return GateResult(
                GateVerdict.SOFT_404, f"http {st} (honest not-found status)")
        return GateResult(GateVerdict.HTTP_BLOCKED, f"http {st}")

    # 2. GARBLED — mojibake from a bad decode (e.g. a legacy-charset page
    #    decoded via the utf-8/replace fallback). Must run before all
    #    text-marker checks: replacement-char-dense text turns them into
    #    noise readers.
    bad = md.count("�")
    if bad > GARBLED_MIN and bad / len(md) > GARBLED_RATIO:
        return GateResult(
            GateVerdict.GARBLED,
            f"mojibake: {bad} U+FFFD replacement chars "
            f"({bad / len(md):.0%} of {len(md)}-char markdown)")

    # 3. CHALLENGE — anti-bot interstitial served as the page.
    detail = _challenge_marker(title_l, body_l, vlen)
    if detail:
        return GateResult(GateVerdict.CHALLENGE, detail)

    # 4. LOGIN_WALL — routes to Tier 5 (no proxy fixes a login form).
    if vlen <= LOGIN_TITLE_MAX and _LOGIN_TITLE_RE.search(title_l):
        return GateResult(
            GateVerdict.LOGIN_WALL,
            f"sign-in title '{title_l[:60]}' with short content ({vlen} chars)")
    login_hits = _login_hits(body_l)
    if vlen <= LOGIN_MAX and len(login_hits) >= LOGIN_MIN_HITS:
        return GateResult(
            GateVerdict.LOGIN_WALL,
            f"login vocabulary dominates short content ({vlen} chars): "
            + ", ".join(f"'{h}'" for h in login_hits[:4]))
    # Scaled rule: a wall padded with marketing prose still concentrates
    # many distinct login phrases; a real article about authentication
    # runs past LOGIN_SCALED_MAX visible chars.
    if vlen < LOGIN_SCALED_MAX and len(login_hits) >= LOGIN_SCALED_HITS:
        return GateResult(
            GateVerdict.LOGIN_WALL,
            f"{len(login_hits)} distinct login phrases in {vlen}-char "
            "content: " + ", ".join(f"'{h}'" for h in login_hits[:5]))

    # 5. SOFT_404 — error page behind a 200/unknown status (honest 404/410
    #    statuses were already handled in check 1).
    if out.http_status in (None, 200):
        m = _SOFT404_RE.search(title_l)
        if m and vlen <= SOFT404_TITLE_MAX:
            return GateResult(
                GateVerdict.SOFT_404,
                f"error pattern '{m.group(0)}' in title, {vlen} chars of content")
        m = _SOFT404_CHEERY_RE.search(title_l)
        if m and vlen <= SOFT404_TITLE_MAX:
            return GateResult(
                GateVerdict.SOFT_404,
                f"cheery error title '{m.group(0)}', {vlen} chars of content")
        lead = visible[:SOFT404_LEAD].lower()
        m = _SOFT404_RE.search(lead)
        if m and vlen <= SOFT404_BODY_MAX:
            return GateResult(
                GateVerdict.SOFT_404,
                f"error pattern '{m.group(0)}' leads short content ({vlen} chars)")

    # 6. CONSENT_WALL — consent text IS the content (noise in a long page
    #    blows past CONSENT_LOOSE and passes).
    hits = _consent_hits(body_l)
    if ((vlen <= CONSENT_TIGHT and len(hits) >= CONSENT_TIGHT_HITS)
            or (vlen <= CONSENT_LOOSE and len(hits) >= CONSENT_LOOSE_HITS)):
        return GateResult(
            GateVerdict.CONSENT_WALL,
            f"consent vocabulary dominates {vlen}-char content: "
            + ", ".join(f"'{h}'" for h in hits[:4]))

    # 7. APP_SHELL — extraction found almost nothing in a text-rich raw page.
    #    Skipped when raw_text_len is None (remote tiers can't report it).
    raw = out.raw_text_len
    if raw is not None and raw > 0:
        ratio = vlen / raw
        if (raw >= APP_SHELL_MIN_RAW and vlen < APP_SHELL_ABS
                and ratio < APP_SHELL_RATIO):
            return GateResult(
                GateVerdict.APP_SHELL,
                f"app-shell: {vlen} chars extracted from {raw}-char raw page "
                f"(ratio {ratio:.3f})")
        if raw >= NEAR_EMPTY_MIN_RAW and vlen < NEAR_EMPTY:
            return GateResult(
                GateVerdict.APP_SHELL,
                f"app-shell: near-empty extraction ({vlen} chars) from "
                f"{raw}-char raw page")

    # 8. EMPTY — below minimum viable content, links excluded.
    if nolink_len < EMPTY_MIN:
        return GateResult(
            GateVerdict.EMPTY,
            f"empty: {nolink_len} non-link chars (< {EMPTY_MIN} minimum)")

    # 9. OK
    return GateResult(GateVerdict.OK, f"ok: {vlen} chars of content")


# -------------------------------------------------------------- self-test

if __name__ == "__main__":
    from contract import Tier

    def _t(md: str, *, title: str | None = None, status: int | None = 200,
           raw: int | None = None, tier: Tier = Tier.STATIC) -> TierOutput:
        return TierOutput(
            tier=tier, markdown=md, title=None, author=None, date=None,
            description=None, http_status=status, raw_text_len=raw,
            page_title=title)

    filler = " ".join(
        ["The quick brown fox jumps over the lazy dog near the riverbank "
         "at dawn, long before the ferries cross."] * 30)  # ~3.3k chars

    # (a) long real article that MENTIONS challenge/consent vocabulary.
    # Deliberately does NOT quote a full strong phrase — vendor names
    # (Cloudflare, Turnstile) stay under the dominance guard, so this must
    # pass; quoting a full phrase is the documented residual risk below.
    article_about_cf = (
        "When Cloudflare decides a request is suspicious it serves its "
        "verification interstitial and runs Turnstile in the background. "
        "Meanwhile GDPR banners ask you to accept cookies and give consent "
        "before reading anything at all.\n\n" + filler)

    # (a2) residual risk, accepted by design: an article that literally
    # quotes a full strong challenge phrase escalates (visible exit-4).
    article_quoting_challenge = (
        "The interstitial says \"Checking your browser before accessing\" "
        "and then reloads.\n\n" + filler)

    # (b) short but legitimate content — a poem, ~270 visible chars
    poem = ("The harbour lights are low tonight,\n"
            "the tide has turned for home.\n"
            "A gull rides out the failing light\n"
            "across the folded foam.\n"
            "The nets are dry, the boats are still,\n"
            "the day has burned away,\n"
            "and somewhere past the darkened hill\n"
            "the morning finds its way.")

    # (c) actual challenge pages
    challenge_body = (
        "Just a moment...\n\nChecking your browser before accessing "
        "example-shop.com.\n\nEnable JavaScript and cookies to continue.\n\n"
        "Ray ID: 8f3a2b1c9d4e5f60")
    challenge_body_only = (
        "Please wait while we verify your request.\n\n"
        "cf-browser-verification cf-chl-widget\n\nPerformance & security by "
        "Cloudflare")
    # padded with prose to defeat the old SHORT_DOMINANCE guard (observed
    # bypass: 3.4k-char challenge page under a benign site-name title)
    challenge_padded = (
        "Please verify you are a human before continuing.\n\n"
        "Checking your browser before accessing example-shop.com. This "
        "process is automatic; your browser will redirect shortly.\n\n"
        + filler)
    challenge_de = (
        "Einen Moment bitte, wir ueberpruefen Ihren Browser. Bitte "
        "aktivieren Sie JavaScript und Cookies, um fortzufahren. Diese "
        "Ueberpruefung erfolgt automatisch und dauert nur wenige Sekunden.")

    # (d) consent-dominated pages (EN, DE, ES)
    consent_page = (
        "We value your privacy\n\nWe and our 142 partners store and/or "
        "access information on your device. Accept all cookies, reject all, "
        "or manage preferences. Consent is collected via OneTrust; you can "
        "withdraw it at any time in the cookie settings.")
    consent_de = (
        "Wir verwenden Cookies\n\nWir und unsere 87 Partner speichern "
        "und/oder greifen auf Informationen auf Ihrem Geraet zu, um "
        "personalisierte Anzeigen zu zeigen. Ihre Einwilligung koennen Sie "
        "jederzeit widerrufen.\n\nAlle akzeptieren. Alle ablehnen. "
        "Einstellungen verwalten.")
    consent_es = (
        "Valoramos tu privacidad\n\nNosotros y nuestros 95 socios "
        "almacenamos o accedemos a informacion en tu dispositivo. Acepta "
        "las cookies para continuar leyendo, rechazalas todas o gestiona "
        "tus preferencias. Puedes retirar tu consentimiento en cualquier "
        "momento.")

    # (e) app shell — ~200 chars extracted from a 30k-char raw page
    shell = ("Home About Products Pricing Blog Careers Contact Support "
             "Loading application, please wait. You need to enable "
             "JavaScript to run this app. Menu Search Language English "
             "Deutsch Francais Terms")

    # (f) soft 404s
    soft404 = ("Sorry, the page you were looking for does not exist. It may "
               "have been moved or deleted. Try searching instead.")
    soft404_cheery = (
        "Oops! Something went sideways. We looked everywhere, but we "
        "couldn't track down what you were after. Maybe it moved, maybe "
        "the link was mistyped. Try heading back to our homepage, browse "
        "the latest headlines, or use the search box to find what you "
        "came for.")

    # (g) login walls
    login = ("Sign in to continue reading\n\nEmail\n\nPassword\n\n"
             "Continue with Google\n\nNew here? Create an account.")
    # padded with membership-marketing prose (observed bypass: 3.4k chars)
    login_marketing = (
        "Read the full story with a free account. Sign in to continue "
        "reading. Enter your email and password, or create an account in "
        "seconds. Continue with Google, continue with Apple, or use your "
        "email address. Already have an account? Log in below.\n\n"
        "Membership gives you unlimited access to expert reporting, "
        "ad-free reading, curated newsletters, audio versions of every "
        "story, and offline reading in our apps.\n\n" + filler)
    # negative for the scaled rule: a real article ABOUT authentication,
    # >= 5000 visible chars, mentioning many login phrases
    sso_article = (
        "Choosing an authentication provider means weighing how users "
        "sign in day to day. Some products let people log in with an "
        "email and password; others push continue with Google or magic "
        "links. A good forgot password flow matters as much as the "
        "create an account screen, and every vendor claims their hosted "
        "login page is the safest choice.\n\n" + filler + "\n\n" + filler)

    # (h) mojibake — 70% replacement characters
    garbled = "�" * 700 + "x" * 300

    # (i) empty / nav-links-only
    nav_only = ("[Home](/) [World](/world) [Business](/business) "
                "[Tech](/tech) [Sports](/sports) [Subscribe](/sub)")

    cases: list[tuple[str, TierOutput, GateVerdict]] = [
        # positives — one per verdict
        ("h: 403 blocked",
         _t("Access denied", status=403), GateVerdict.HTTP_BLOCKED),
        ("429 blocked",
         _t("slow down", status=429), GateVerdict.HTTP_BLOCKED),
        ("400 blocked (honest error status)",
         _t(filler, status=400), GateVerdict.HTTP_BLOCKED),
        ("500 blocked (honest error status)",
         _t(filler, status=500), GateVerdict.HTTP_BLOCKED),
        ("503 blocked regardless of content",
         _t(filler, title="Status writeup", status=503),
         GateVerdict.HTTP_BLOCKED),
        ("503 + challenge body still blocked",
         _t(challenge_body, status=503), GateVerdict.HTTP_BLOCKED),
        ("honest 404 with wordy branded body",
         _t(soft404_cheery, title="Whoops | Example Store", status=404),
         GateVerdict.SOFT_404),
        ("honest 410 gone",
         _t(filler, title="Gone | Example", status=410), GateVerdict.SOFT_404),
        ("c: challenge via title",
         _t(challenge_body, title="Just a moment...", status=None),
         GateVerdict.CHALLENGE),
        ("challenge via short body",
         _t(challenge_body_only, status=200), GateVerdict.CHALLENGE),
        ("padded challenge, benign title",
         _t(challenge_padded, title="example-shop.com", status=200),
         GateVerdict.CHALLENGE),
        ("german challenge via title",
         _t(challenge_de, title="Einen Moment bitte...", status=200),
         GateVerdict.CHALLENGE),
        ("german challenge via body only",
         _t(challenge_de, title="beispiel-shop.de", status=200),
         GateVerdict.CHALLENGE),
        ("residual risk: article quoting full challenge phrase",
         _t(article_quoting_challenge, title="Anatomy of an Interstitial",
            status=200), GateVerdict.CHALLENGE),
        ("g: login wall",
         _t(login, status=200), GateVerdict.LOGIN_WALL),
        ("login wall padded with marketing prose",
         _t(login_marketing, title="Member stories that matter", status=200),
         GateVerdict.LOGIN_WALL),
        ("f: soft 404 via title",
         _t(soft404, title="404 - Page Not Found", status=200),
         GateVerdict.SOFT_404),
        ("soft 404 via leading body",
         _t(soft404, title=None, status=None), GateVerdict.SOFT_404),
        ("soft 404 via cheery title",
         _t(soft404_cheery, title="Oops! | Example News", status=200),
         GateVerdict.SOFT_404),
        ("d: consent wall",
         _t(consent_page, status=200), GateVerdict.CONSENT_WALL),
        ("german consent wall",
         _t(consent_de, title="Beispiel Nachrichten", status=200),
         GateVerdict.CONSENT_WALL),
        ("spanish consent wall",
         _t(consent_es, title="Diario Ejemplo", status=200),
         GateVerdict.CONSENT_WALL),
        ("e: app shell 200/30k",
         _t(shell, status=200, raw=30000), GateVerdict.APP_SHELL),
        ("near-empty vs large raw",
         _t("Loading...", status=200, raw=25000), GateVerdict.APP_SHELL),
        ("near-empty vs modest raw (2nd branch)",
         _t("Loading...", status=200, raw=2200), GateVerdict.APP_SHELL),
        ("garbled: 70% replacement chars",
         _t(garbled, title="Article", status=200), GateVerdict.GARBLED),
        ("i: empty",
         _t("", status=200), GateVerdict.EMPTY),
        ("nav-links-only page",
         _t(nav_only, status=200), GateVerdict.EMPTY),
        # tricky negatives — must all be OK
        ("a: article ABOUT cloudflare+cookies",
         _t(article_about_cf, title="How Cloudflare's Bot Detection Works",
            status=200, raw=9000), GateVerdict.OK),
        ("b: short legit poem",
         _t(poem, title="Harbour Nocturne", status=200, raw=1800),
         GateVerdict.OK),
        ("article about 404 pages",
         _t("Error pages are a design opportunity. " + filler,
            title="The Art of the 404 Page", status=200), GateVerdict.OK),
        ("long article, jina (raw=None)",
         _t(filler, title="A fine essay", status=None, raw=None,
            tier=Tier.JINA), GateVerdict.OK),
        ("consent noise in long article",
         _t(filler + " Click accept all cookies to dismiss the banner and "
            "give consent, then keep reading.", status=200), GateVerdict.OK),
        ("long article about SSO/auth providers",
         _t(sso_article, title="Choosing an Authentication Provider",
            status=200), GateVerdict.OK),
        ("sprinkle of U+FFFD in normal article",
         _t(filler + " �� ", status=200), GateVerdict.OK),
    ]

    failures = []
    for name, out, want in cases:
        got = evaluate(out)
        ok = got.verdict is want
        if not ok:
            failures.append((name, want, got))
        print(f"{'PASS' if ok else 'FAIL':4s}  {name:48s} "
              f"{got.verdict.value:12s} {got.detail}")

    # spot-check that details name what fired
    assert "403" in evaluate(_t("x", status=403)).detail
    assert "just a moment" in evaluate(
        _t(challenge_body, title="Just a moment...", status=None)).detail
    d = evaluate(_t(shell, status=200, raw=30000)).detail
    assert "30000" in d and "app-shell" in d, d
    d = evaluate(_t(garbled, status=200)).detail
    assert "700" in d and "mojibake" in d, d
    d = evaluate(_t(soft404_cheery, title="Whoops | Example Store",
                    status=404)).detail
    assert "404" in d, d

    if failures:
        for name, want, got in failures:
            print(f"\nFAILED {name}: wanted {want.value}, "
                  f"got {got.verdict.value} ({got.detail})")
        raise SystemExit(1)
    print(f"\nall {len(cases)} gate self-test cases passed")
