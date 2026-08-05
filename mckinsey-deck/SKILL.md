---
name: mckinsey-deck
description: Create or iterate on slide-deck content in Markdown applying McKinsey criteria (Pyramid Principle, action titles, MECE, so-what, quantification with sources). User provides context files and a deck goal; skill proposes an outline, then produces one slide at a time with feedback loops. Output is a deck.md file — content only, no visual references.
license: MIT
---

# McKinsey Deck Builder

Produce slide-deck content in Markdown following McKinsey's communication discipline. Scope is **content only**: action titles, body copy, footnotes, and sources. Never write chart types, colors, icons, layout instructions, or any visual reference.

---

## Output format rules

Every deck is a single Markdown file (`deck.md` in the working directory, unless the user specifies otherwise). Slides are separated by `---`.

**Structural rule:** Every slide is bounded by `---`. Each `---` block contains exactly one heading — a single `#` (cover slide, deck's first block only), `##` (section-intro slide, no body), or `###` (content slide, body follows). **Never more than one heading in the same block.**

| Heading level | Slide type | Rules |
|---|---|---|
| `#` | **Cover slide** | Always the deck's **first** `---` block, and the only place a `#` may appear. Format: `# <Deck title>` followed by an optional subtitle line, an optional audience/attribution line, and a date. No action title, no body bullets, no `Sources:` block. Exactly one per deck. |
| `##` | **Section-intro slide** | Format: `## NN — Section title` where `NN` is the **section** index (01, 02 …), not the slide index. No body. No `###` in the same block. |
| `###` | **Content slide** | Format: `### Action title that states the conclusion`. Body content follows. Must be in its own `---` block, separate from any `##`. |

**Canonical layout** opening with the cover, then one section with two content slides:

```
# <Deck title>

<Subtitle — what the deck delivers>

<Audience / attribution> · <Date>

---

## 01 — Situation

---

### Action title for the first content slide

<!-- comparison: time-series -->

Body…

---

### Action title for the second content slide

<!-- comparison: none -->

Body…

---

## 02 — Complication
```

Deck file management:
- First slide: create `deck.md` with a manifest comment at the top (`<!-- deck: <goal summary> | slides written: N of M -->`), then write the cover slide and slide 1. `N of M` counts every `---` block including the `#` cover and `##` section-intro slides.
- Subsequent slides: Edit the file to append the new slide (do not rewrite previously approved slides).
- Update the manifest comment each time.

Comparison tag (content slides only):
- Immediately after the `###` action title, emit a hidden, non-rendering tag on its own line: `<!-- comparison: <type> -->`.
- `<type>` is the Zelazny comparison the message makes: `component` (share of a whole), `item` (ranking), `time-series` (change over time), `distribution` (spread across ranges), `correlation` (relationship between two variables), or `none` (qualitative/conceptual — not chartable).
- This is a content-level signal for the downstream design step — never a visual instruction, and it never appears on the rendered slide. Cover (`#`) and section-intro (`##`) slides take no tag.

---

## McKinsey lenses (apply at every step)

Before proposing the outline, before writing each slide, and before the final pass — check every item:

1. **Pyramid Principle (Minto)** — Answer-first at deck and slide level. The conclusion is the first thing stated, not the last.
2. **Day-one answer** — The deck opens with the answer and its 3-4 supporting pillars before any supporting detail.
3. **Action titles** — Every `###` heading is a full declarative sentence stating the conclusion (not a topic label). Reading only the `###` titles in order must tell the complete story: the **horizontal flow test**.
4. **MECE** — The sections and slides cover the whole problem space with no overlap. Run the MECE test on every grouping.
5. **Quantification with source per figure** — Every number or statistic carries a specific value, not a range or vague qualifier. **Public/third-party figures** also carry an inline reference marker `[N]` immediately after the figure, with the full `Source: <name>, <date>` listed in the slide's end-of-slide `Sources:` block (reuse the same `[N]` when one source backs multiple claims). **Figures that originate from the user's own internal/first-party material carry no citation** — state them plainly with no `[N]` and no `Sources:` entry (see Reference validation).
6. **One idea per slide** — No two independent arguments on the same content slide. If two ideas are needed, create two slides.
7. **Synthesis, not summary (the "So what?" test)** — Every content slide must carry the client's implication: what does the evidence mean for them, and what decision does it call for? Embed the implication in the action title and/or a closing bullet written as a natural conclusion. **Do not append a labeled `**So what:**` footer to every slide** — that is a crutch that signals the implication was not properly embedded in the body. Use an explicit implication call-out only when the slide demands a specific decision from the audience.
8. **Low text density** — ≤40 words per body block. Use bullet points, short clauses, or markdown tables. No paragraphs.
9. **Traceability and consistency** — Figures, codes, names, and acronyms are identical across every slide in the deck. Verify before finalizing each slide.
10. **SCQA opening** — The first slides of the deck follow Situation → Complication → Question → Answer.
11. **Hypothesis-driven** — Each section (`##`) corresponds to a key hypothesis or issue-tree branch, not a generic topic.
12. **Recommendation separate from risks** — The recommendation slide states the answer with conviction, using imperatives. Caveats, constraints, and risks appear on a dedicated slide.
13. **No fluff** — No warm-up bios, no suspense arcs, no hedging language ("it could be argued that…"), no decorative phrases.
14. **Chart-readiness and context** — Decide whether the slide's message is a quantitative comparison or qualitative, and record it in the `<!-- comparison: <type> -->` tag. A chart is warranted only when the message turns on a comparison; a lone figure is a number, not a chart. When the message **is** a comparison, it must carry its comparator — target, prior periods, peer, or threshold (lens applies principle #14, Benchmarking and Context, to exhibits). If that comparator is absent from the sources: **ask the user for it when the comparison is the slide's core, load-bearing message; otherwise drop a `⚠ Missing context — <what is needed>` flag.** Never present a single-value "chart" or a comparison stripped of its benchmark.

---

## Workflow (mandatory; follow exactly)

### Step 1 — Ingest

Read every context file and source the user provides. Read the deck goal carefully. Think hard before responding:
- What is the central question the deck must answer?
- What is the day-one answer hypothesis?
- What is the SCQA frame for the opening?
- What are the 3-5 key hypotheses or drivers the deck must prove?
- What upstream sources (regulations, statutes, URLs, datasets, named studies, agency authorizations) do the context files themselves cite? Note these now — they are the Tier 1 citations for slides, not the filenames.

Do not produce output yet. Proceed to Step 2.

### Step 2 — Propose outline

Output **only** the outline — no slide bodies. Format:

```
## 01 — <Section title>
### <Action title (full sentence)>
### <Action title (full sentence)>

## 02 — <Section title>
### <Action title (full sentence)>
...
```

After the outline, write a short paragraph (≤5 sentences) that:
1. States the day-one answer hypothesis.
2. Explains the SCQA frame.
3. Confirms the horizontal flow test passes (quotes the 3-5 most critical `###` titles that carry the story).
4. Confirms the structure is MECE.

**Stop. Wait for the user's feedback.** Do not write any slide body.

### Step 3 — Iterate on outline

Apply the user's changes. Repeat Step 2 output format + the short paragraph. Continue until the user explicitly approves the outline.

### Step 4 — Write slide 1

After outline approval:
1. Create `deck.md` (or the user-specified path) with the manifest comment.
2. Write the `#` cover slide as the deck's first `---` block: deck title, optional subtitle, optional audience/attribution, and date. Derive the title from the deck goal; keep it factual, no action-title phrasing. Then write slide 1, applying all 13 McKinsey lenses above.
3. Validate every reference in the slide (see Reference validation below).
4. **Format check** — confirm the cover is the first block and the only `#`, and each slide's `---` block contains exactly one heading at the correct level (`#` cover, `##` section-intro, `###` content). If a block has more than one heading, fix before proceeding.
5. After the slide markdown, add a `> **Lens review:**` block listing how each applicable lens is satisfied, and flagging anything marginal. Include these three lines: `Format: one heading, correct level, own --- block — ✓`; `Sources: public upstream cited; internal/first-party claims left uncited; unverifiable claims flagged — ✓`; and `Comparison/context: <type> tagged; comparator present, or asked/flagged when missing — ✓`.
6. **Stop. Wait for the user's feedback.**

### Step 5 — Iterate and advance

Apply the user's feedback to the current slide (use Edit tool to modify only that slide). Then write the next slide. Repeat Step 4 procedure (write + lens review + stop) until all slides in the approved outline are complete.

### Step 6 — Final pass

After the last slide is approved:
1. Re-read `deck.md` in full.
2. Confirm the deck opens with exactly one `#` cover slide and that the executive-summary slide immediately follows it. Run the horizontal flow test on all `###` titles.
3. Verify traceability: every figure, code, and name is consistent throughout.
4. Verify MECE across sections.
5. **Comparison-tag audit** — for every content slide, confirm a `<!-- comparison: <type> -->` tag is present; for each non-`none` tag, confirm the slide body holds its comparator (second series, prior periods, peer, target, or threshold). Where a comparator is missing, ask the user to supply the benchmark; if they cannot, downgrade the tag to `none` (only with their confirmation) and ensure the slide carries a `⚠ Missing context` note. This makes deck.md authoritative before the design step, so deck-design-prompt can trust the tags.
6. Report any remaining issues or flagged references.

---

## Reference validation (mandatory for every slide)

For every figure, statistic, quote, name, attribution, date, or external claim included in a slide apply this hierarchy:

- **Tier 1 — Public upstream source.** If the claim traces to a public, third-party-verifiable source (regulation, statute, URL, dataset, study, agency authorization, named third party), cite *that* upstream source — **not the filename of the internal file it came through**. Example: a context file quoting "per Ley 10/2010 Art. 17, blocking is required" → `Source: Ley 10/2010, Art. 17`, not `Source: Informe AML.md`. Never name an internal context file as a via/intermediate source.
- **Tier 2 — Internal / first-party origin.** If the claim originates from the user's own non-public material — an internal file, proprietary observation, in-house analysis, the user's own thinking, or first-party company data — **include it with no citation at all**: no `[N]` marker and no `Sources:` entry. It is presented as the author's own assertion; a private filename adds no credibility to the audience and is routinely stripped from the deck. Do **not** invent a public source to dress it up.
- **Tier 3 — Unknown / unverifiable.** If a claim is covered by no source and is not first-party knowledge either, flag it explicitly, inline:
  > `⚠ Unverified — please confirm: "<exact claim>" (suggested source if any)`
- **Never pass off an untraceable third-party claim as fact.** If a number purports to be public but you cannot trace it, flag it (Tier 3) — do not cite the internal file in its place.

Reference format: place an inline marker `[N]` immediately after each **Tier 1** figure or claim. Collect those references in a `Sources:` block at the foot of the slide, one per line as `[N] Source: <name>, <date>`; reuse the same number when one source backs multiple claims. **Omit the `Sources:` block entirely when a slide has no Tier 1 citations** — an all-internal slide carries none.

---

## Reference file

All McKinsey criteria, principles, examples, and checklists encoded in this skill are consolidated in:

```
<skill-dir>/references/mckinsey-principles.md
```

Re-read that file at invocation time whenever a deeper reference on any criterion, example, or checklist is needed.
