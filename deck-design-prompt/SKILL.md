---
name: deck-design-prompt
description: Reads a completed deck.md (produced by mckinsey-deck) and outputs a ready-to-paste prompt for Claude Design that tells it how to apply visual design without touching any content.
---

# Deck-to-Design Prompt Generator

Produce a Claude Design prompt from a finished `deck.md` file. The prompt tells Claude Design exactly how to interpret the Markdown structure and what it must never touch.

## Steps

1. Read `deck.md` in the current directory, or the path the user specifies.
2. From the manifest comment (`<!-- deck: <goal> | slides written: N of M -->`), extract the deck goal and slide count.
3. Count every `---` separator in the file to confirm total slides (if the count differs from the manifest, use the actual count and note the discrepancy to the user after outputting the prompt).
4. Collect all `##` lines in order — these are the section-intro slides.
5. Read each content slide's `<!-- comparison: <type> -->` tag. For any tag that is a real comparison (not `none`), verify the slide body actually holds a **comparator** — a second comparable series, prior period(s), a peer, a target, or a threshold. A lone single value is not a comparator.
6. Build the **no-chart list**: every content slide whose tag is non-`none` but has no comparator, plus every slide whose body carries a `⚠ Missing context` flag. This skill is **read-only — never edit deck.md**. Instead, the prompt will instruct Claude Design to render these slides as text/table, and you will warn the user about them after the prompt so they can fix the content in mckinsey-deck (add the comparator, or let its final pass downgrade the tag to `none`).
7. Fill in the template below and output it. Output **only** the prompt — no preamble, no commentary, no code fences around it. The user will copy and paste it directly into Claude Design.

## Output template

---

Convert the attached Markdown file into a professional presentation deck. Do not alter, summarise, or reorder any content — every word in the Markdown is deliberate. Your job is to apply visual design, not editorial judgment.

**Context**
[Goal summary from the manifest comment — one sentence, plain text]

**Format**
- One slide per `---` separator. This deck contains [N] slides total.
- Every `---` is a hard slide boundary — do not merge or split under any circumstances.

**Slide hierarchy**
- `## NN — Section title` → Section-intro slide (divider). Display the short title only; no body content exists on these slides.
- `### Full action sentence` → Content slide. The `###` heading is the slide title — display it in full. Everything below the heading is the slide body.

**Sections in this deck**
[Numbered list of the ## lines in order, e.g.:
1. 01 — Opening
2. 02 — Situation
…]

**Footnotes & sources**
- Inline `[N]` markers next to a figure or claim are reference markers — render them as superscript markers on that figure/claim.
- The `Sources:` block at the foot of a slide lists the full references. Render it as a single small, bottom-aligned source line (footnote zone), keeping the `[N]` numbering intact.
- Never promote sources into the body, and never delete them.

**Comparison tags & charts**
- A hidden `<!-- comparison: <type> -->` on a content slide is a chart-form directive — never render the comment as visible text. Build at most ONE exhibit per slide, and it must make the action title's point:
  - `component` → share view (pie or 100% stacked bar)
  - `item` → ranked bar
  - `time-series` → column (few periods) or line (many)
  - `distribution` → histogram
  - `correlation` → scatter or paired bars
  - `none` → no chart; render as text, a table, or a single callout figure
- Carry the slide's source line on the exhibit; one message per exhibit (the action title is its takeaway); declutter — no 3D, no chartjunk.
- **Never invent data, a comparator, an axis, a trend, or a series not present in the slide body.** If a slide lacks the data to build its indicated chart, render it faithfully as text/table instead of fabricating a chart.
- **Render these slides without a chart (text/table only):** [list slide numbers/titles from the no-chart list, or "none"]

**Don'ts**
- Do not truncate action titles — they are deliberately long complete sentences; truncating them destroys the meaning
- Do not merge or split slides — each `---` separator is intentional
- Do not add, remove, or reorder any content
- Do not paraphrase or summarise any text
- Do not add a table of contents, agenda slide, or any slide not present in the Markdown
- Do not render the `<!-- comparison: ... -->` comments as visible text — they are directives, not content
- Do not fabricate any data, comparator, axis, or trend to satisfy a chart; fall back to text/table when the data is not on the slide

---

After outputting the prompt, say nothing else unless one of these applies: a slide-count discrepancy, or one or more chart-suppressed / `⚠ Missing context` slides — in which case list them briefly so the user can add the missing comparator in mckinsey-deck.
