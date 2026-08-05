---
name: wargame
description: "Wargame a subsystem — make a top-tier model fight its failure surfaces on paper move-by-move (expected observation, most-likely failure + counter-move, forks with triggers, abort conditions, verification, and a red-team that must actually land), so any cheaper model can execute or harden it blind. Use for \"/wargame <target>\", \"wargame this subsystem / this pipeline / this flow\", or to bank a capable model's judgment before losing access to it. Args: \"<target>\" — a path, subsystem name, or description; optional \"--orders-only\" to emit just the fill-in briefs."
license: MIT
---

# Wargame

A **wargame** is not a plan. A plan assumes the happy path; a wargame fights the mission
move by move — **action → reaction → counteraction** — surfacing every way reality pushes
back and the counter-move for each, so a cheaper model (or a future you) can execute or
harden the target **blind, without asking a single question.**

This skill produces one **battle plan** per failure surface of a target subsystem. Its
value is the *discipline it enforces* — real recon, a red-team that lands, a grade gate —
**not** the template. A wargame that merely fills the section headers with generic forks
and a theatrical red-team is worse than nothing: it looks done. Every hard rule below
exists to prevent that.

> **This skill standardizes rigor, not intelligence.** It does not make the model smarter.
> Run the authoring on the most capable model you have — Fable 5 is ideal, Opus 4.8 is
> strong — at **high or xhigh effort**. On a weak model you get well-formatted, hollow
> wargames. Say this to the user if there's any doubt about the model.

Deeper reference (re-read when you need it): `references/method.md`. The acceptance rubric:
`references/SUCCESS.md`. The order-file shape: `references/order-template.md`. A real,
strong battle plan to match for depth: `references/example-wargame.md`.

---

## Procedure

Work through these in order. The gates (2–4) come **before** any authoring — do not skip
them to "just start writing."

### 1. Parse the target
From the args: a path, a subsystem/pipeline/flow name, or a prose description of what to
wargame. If no target is given, ask what to wargame and wait. Flags:
- `--orders-only` → produce only the fill-in-the-blank order files (empty `wargames/`), for
  the user to author later. Default is to **author the full battle plans now** (the point
  is to bank the judgment while you have the capable model).

### 2. Model & effort gate
State plainly which model is authoring and that this is intelligence-sensitive work. If you
are clearly running on a small/fast model, warn the user the output will be shallow and
recommend re-running on Fable 5 / Opus 4.8 at high–xhigh effort. Do not pretend the skill
compensates for model tier.

### 3. Worth-it gate
Wargaming is expensive and only pays off on the right targets. Proceed only if the target
is **high-stakes, failure-prone, run unattended, or the hard 20%** that normally breaks. If
it's a low-risk feature, say so and offer a plain implementation plan instead — do **not**
wargame by default. (See `references/method.md` → "Is this worth wargaming?")

### 4. Discover the failure surfaces (recon-driven)
Read the target's real code/config and enumerate its **distinct failure surfaces** — the
independent ways it can go wrong (a route, a fetch, a retry, a commit, a schedule, a
secret…). **The number is discovered, never fixed:** a small script may have 2–4, a gnarly
subsystem ~12, something huge 20+. Do not anchor on any prior count.

Present the surface list to the user before authoring. Let them **cap it** (a quick pass on
the top few vs. exhaustive coverage) or add surfaces you missed. Apply the "how far to
wargame" judgment — stop each surface at its second/third-order consequences, not infinity.
This list is the work-plan: exactly one order + one battle plan per surface it contains.

### 5. Scaffold
**Decide WHERE first — do not write into the target's own repo by default.** Wargames are
about the target, not part of it. Writing a `wargames/` tree into the project clutters it,
risks being committed, and can trip the target's own guardrails (pre-commit hooks,
sovereignty rules, lint). Default: create the working dir **outside** the target's git repo
— a sibling directory (e.g. `../<repo>-wargames/`) or a dedicated location the user names.
If the user explicitly wants it inside the repo, confirm and add it to `.gitignore` unless
they want it versioned. When in doubt, state where you intend to write and let them redirect.

Then create the working dir (default name `<target>-wargames/`) with:
- `tasks/NN-<surface>.md` — one **order** per surface, from `references/order-template.md`
  with the placeholders filled for that surface.
- `wargames/` — where battle plans land (empty under `--orders-only`).
- `SUCCESS.md` — copy `references/SUCCESS.md`.
- `LEDGER.md` — a table seed: one row per surface, the 8 SUCCESS columns, and a
  "red-team patch" column (see `references/example-wargame.md` sibling `LEDGER.md` shape).

Everything the skill reads during recon is **read-only**; the only files it creates are its
own outputs in this working dir. It never edits the target's code.

### 6. The rigor loop — per surface, in THIS order
Do these steps in sequence for each surface. Draft **all** surfaces to at least a first
pass before polishing any (breadth first), then refine.

1. **RECON FIRST — cited.** Read the real code for this surface before writing a word of
   its wargame. **HARD RULE: no wargame section may exist without real evidence behind it**
   — cite `file:line`, a real command, a real error string, a real config value. If an
   assumption can't be settled from the code, it becomes a `RECON NEEDED` marker with the
   **exact check** that would settle it — never a guess dressed as fact.
2. **Draft the battle plan** → `wargames/NN-<surface>.md`. Required structure (match the
   depth of `references/example-wargame.md`):
   - Numbered **moves**. Each states its **expected observation** (exactly what you see if
     it worked — a file at a real path, an exit code, a log line).
   - Each move carries its **most-likely failure**, the **signal** that failure gives, and
     the **counter-move**.
   - **Forks** with explicit **triggers** ("if you observe X, take route B") — no judgment
     left to the executor.
   - **Abort conditions** — when to stop and flag rather than improvise or bypass a
     guardrail.
   - **Verification runs** — the real commands the executor runs and what "pass" looks like
     for each.
3. **Apply the content-truth lens** (the single highest-value heuristic). At every point
   where the target advances on a **status code / exit code / "a file exists"** signal —
   especially any *seal / commit / enable / success* boundary — ask: *does that success
   signal actually guarantee the **content or side-effect** is correct?* Most real defects
   live exactly here: the op returns 0 but the content is a login-wall, a hallucination, a
   dropped table, a dead key, a duplicated ledger entry. (See `references/method.md` →
   "success ≠ correct content".)
4. **Red-team until one lands.** Attack the route until something gets through, then patch
   the route with the counter-move that attack demanded. **HARD RULE: record BOTH an attack
   that failed AND an attack that landed plus its patch.** If nothing lands after genuine
   effort, either keep attacking from a new angle or explicitly mark the surface
   "unfalsified — no landing attack found" — **never invent a strawman** attack to fill the
   section.
5. **Grade-gate against `SUCCESS.md`** (all 8 points) and record the result in `LEDGER.md`
   with the red-team patch. **HARD RULE: a wargame that fails any of the 8 points is not
   done.** Fail loudly and fix it; do not emit hollow output that merely has the headers.

### 7. Refinement pass
Once every surface has a graded first draft, loop back and push each to its limit — harder
red-team, tighter triggers, real verification commands, RECON items chased down.

### 8. Optional: hardening plan
If the red-team surfaced real defects in existing code (common when the target already
exists), offer a prioritized **hardening plan**: the probable, fixable issues ranked by
*realistic probability × impact*, each as **Symptom → Evidence (file:line) → Confirm →
Fix → Verify**, plus a **"verified non-issues"** list so a later session doesn't chase
ghosts. (This is what turns a wargame of live code into an actionable repair list.)

---

## Hard rules (restate to yourself before finishing)

- **No uncited section.** Every claim traces to real code / a real command, or is a
  `RECON NEEDED` marker with its settling check.
- **The red-team must land.** Record a real attack that got through and the patch it forced.
  No strawmen.
- **Grade-gate.** 8/8 on `SUCCESS.md` or it's not done. Hollow-but-formatted = fail.
- **Worth-it gate.** Don't wargame low-stakes targets. Offer a plain plan instead.
- **Content-truth lens.** Check every success/seal/commit/enable boundary for "success ≠
  correct content."
- **Run on a top-tier model.** This skill enforces structure; the intelligence is the
  model's. Fable 5 / Opus 4.8 at high–xhigh effort.
- **Never wargame a route that bypasses the target's own guardrails** (hand-editing
  protected state, forcing past hooks, faking a result). A route that needs that is wrong —
  fix the route, not the guardrail.

## Optional: parallel authoring
For a target with many surfaces, you *may* fan the per-surface authoring out with the
Workflow tool (draft in parallel, then verify/grade each) — only if the user has opted into
multi-agent orchestration. Otherwise author inline, sequentially. The rigor loop is the
same either way.
