# Method reference

Re-read this when you need the "why" behind a step. `SKILL.md` is the procedure; this is the
reasoning and the sharp edges.

## What a wargame is (and isn't)

A **plan** breaks work into phases and assumes linearity — a blue-sky path with a high
implied success rate. It rarely shows what happens when reality pushes back. A **wargame**
takes the same mission and fights it move by move:

- **Action** — the executor makes a move.
- **Reaction** — reality humbles it (an error, a wall, a race, a wrong-but-successful result).
- **Counteraction** — the executor resolves it and continues.

That triad is the modern agentic loop. A wargame pre-simulates it so a cheaper model inherits
the capable model's judgment: it already knows every fork and the counter-move for each.

The whole trade is: **spend the top-tier model's judgment once, keep it forever.** You are
banking *how a task goes wrong during execution* so a weaker model can carry it out — or so
you can harden the target — without a smart model looking over its shoulder.

## Is this worth wargaming?

Wargaming is expensive. Apply it only where the payoff is real:

- **High-stakes** — failure is costly, hard to reverse, or user-facing.
- **Failure-prone** — many moving parts, external dependencies, flaky I/O.
- **Unattended** — runs on a schedule / in CI / autonomously, where nobody catches a bad
  success in the moment.
- **The hard 20%** — the part that normally gets an execution 80% of the way and then stalls.

For a low-risk feature, a plain implementation plan is the right tool — say so. Over-applying
wargaming is its own failure mode: it makes the method feel like ceremony.

## How far to wargame — the stopping rule

Each surface has second-, third-, fourth-order consequences. You decide the depth. Stop at
the point where further forks stop changing what the executor would do — usually second or
third order. Don't wargame to infinity; don't stop at the first error either. The goal is a
route robust to the failures that actually occur, not a decision tree for the heat death of
the universe.

## The load-bearing lens: success ≠ correct content

This is the single highest-value habit. Almost every pipeline advances (or *commits / seals /
enables*) on a **status code / exit code / "a file exists"** signal — never on a check that
the *content* is what it claims to be. So every such boundary has a failure mode where the
operation "succeeds" and the result is silently wrong, often permanently.

The recurring shape — "succeeds because… but the content is…":

| Succeeds because… | …but the content is… |
|---|---|
| HTTP 200 + non-empty body + a title parsed | a login-wall / bot-check stub, not the article |
| the transcript is fluent and non-repetitive | a hallucination in the wrong language |
| word-recall passes | missing every table (the actual data) |
| the key *resolves* from the vault | the key is revoked → every call 401s |
| the command exited 0 | the ledger/inbox it wrote is now duplicated/corrupt |

(The rows above span deliberately different domains — web fetch, transcription, doc
conversion, secrets, ledgers — because the pattern is domain-independent. Yours will look
different: a payment that returns 200 but double-charges, a migration that "succeeds" but
half-applied, a deploy whose health check passes on the old build, a cache write that
silently serves stale. Find the equivalent boundary in *your* target.)

When you hit any seal/commit/enable/success boundary, ask: **does this success signal
guarantee the content or side-effect is correct?** If not, that's a move whose "expected
observation" must be a *content* check, and usually where the red-team lands. The systemic
fix a wargame recommends is often a single **content-truth gate** at that boundary rather
than a status-code check.

## The red-team protocol

A wargame that hasn't survived attack is a draft. For each surface:

1. Attack the route as an adversary trying to make it produce a **silently-wrong success** or
   get **stuck**. Use the content-truth lens, boundary conditions, races, exhausted retries,
   partial failures, malformed input, and "what if the thing that returned 0 lied."
2. Record an attack that **failed** (the defense that held) — this proves the route has teeth.
3. Keep attacking until one **lands**. Patch the route with the counter-move that attack
   demanded, and record both the attack and the patch.
4. If nothing lands after genuine effort, do not fabricate a strawman. Either attack from a
   genuinely new angle, or mark the surface `unfalsified — no landing attack found` and move
   on. An honest "couldn't break it" beats a theatrical break.

Diverse lenses beat repetition: correctness, security, does-it-reproduce, what-happens-on-the-
second-run, what-happens-under-concurrency.

## Recon before writing — no fiction

The reason a wargame is worth anything is that it's grounded in the real target. **You may not
write a wargame section without citing real evidence** — a `file:line`, a real command, a real
error string, a real config value. Anything you can't settle from the code is a `RECON NEEDED`
marker with the exact check that settles it. A plausible-sounding fork with no code behind it
is the exact hollow output this method exists to prevent.

## Model & effort

This method standardizes *rigor*, not *intelligence*. The quality ceiling is the authoring
model's. Run it on the most capable model available — Fable 5 is ideal; Opus 4.8 is strong —
at **high or xhigh effort**, with the full target and success criteria given up front in one
well-specified turn. On a weak model you get the format with none of the insight. If banking a
model's judgment "before you lose access" is the motivation, that only works if the *authoring*
runs on the strong model — the executor can be cheap, the author cannot.

## From wargame to repair (when the target already exists)

Wargaming *existing* code turns the red-team into a bug finder: a landing attack on live code
is a real latent defect. Convert those into a prioritized **hardening plan** — the probable,
fixable issues ranked by *realistic probability × impact*, each written as **Symptom →
Evidence (file:line) → Confirm (a repro command) → Fix → Verify**. Include a **"verified
non-issues"** list for things the wargame raised but the code cleared, so the next session
doesn't chase ghosts. Confirm each hypothesis against the real code before proposing a fix —
some won't reproduce, and the plan should say so.
