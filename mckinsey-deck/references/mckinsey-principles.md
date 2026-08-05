# McKinsey Communication Discipline — Principles, Criteria, and Examples

A consolidated reference for building McKinsey-style presentations. Covers deck-level architecture, slide-level craft, analytical rigor, and communication strategy. Every principle includes a rationale, concrete examples, and a test for quality.

---

## Part 1 — Deck-level architecture

### 1. Pyramid Principle (Answer-First)

**Principle:** Start with the recommendation. Follow with the key arguments that support it. End with the evidence. Never build toward the conclusion; state it first, then prove it.

**Why:** Executives skim. If the answer is buried at slide 20, they may leave before they see it. Presenting the conclusion first allows them to evaluate the logic from a position of knowing where you are going.

**Structure:**
```
Answer / recommendation (top)
  ├── Key argument 1
  │     └── Evidence A, Evidence B
  ├── Key argument 2
  │     └── Evidence C, Evidence D
  └── Key argument 3
        └── Evidence E, Evidence F
```

**Example (weak → strong):**
- Weak: 20 slides of market data → cost analysis → talent review → "therefore, we recommend entering Southeast Asia"
- Strong: Slide 1 states "Enter Southeast Asia via a joint venture with a local partner — the market grows at 12% CAGR and our capital exposure is capped at €40 M" → following slides prove each pillar

**Test:** Can the executive read only the first two slides and understand both the recommendation and the top-line rationale? If not, restructure.

---

### 2. Day-One Answer / Executive Summary

**Principle:** The deck opens with a complete executive summary that states the answer and its 3-4 supporting pillars. All detail in subsequent slides supports this opening.

**Why:** The executive summary slide is the deck. Everything else is appendix to a skeptic. If stakeholders only read one slide, they get the full picture.

**Structure of the opening summary slide:**
```
### [Full-sentence action title stating the recommendation]

- Pillar 1: [Key argument with the quantified outcome]
- Pillar 2: [Key argument with the quantified outcome]
- Pillar 3: [Key argument with the quantified outcome]
- [Optional Pillar 4]
```

**Example:**
> ### Acquiring NovaTech for €220 M delivers a 3× return on invested capital by 2028 across three levers

> - Market: NovaTech's SaaS revenue growing at 40% YoY addresses the €4.2 B segment we currently cannot reach (Source: Gartner, 2023)
> - Synergies: Combined go-to-market eliminates €35 M in overlapping sales costs within 18 months
> - Execution: Integration risk is contained — 90% of NovaTech's revenue comes from a single, portable platform

**Test:** Read only the executive summary. Does the reader know what to do, why, and what the expected outcome is? If not, revise.

---

### 3. SCQA Opening (Situation–Complication–Question–Answer)

**Principle:** The opening sequence of the deck establishes shared context (Situation), introduces the disruption or problem (Complication), frames what decision the analysis addresses (Question), and delivers the recommendation (Answer). A simplified variant is SCR (Situation–Complication–Resolution).

**Why:** SCQA creates urgency and relevance. It starts on ground the audience already accepts (the Situation), then introduces the tension (Complication) that makes the Question unavoidable. By the time the Answer arrives, the audience is already asking the same question.

**SCQA template:**
| Frame | Purpose | Example |
|---|---|---|
| Situation | Uncontroversial fact the audience already knows and accepts | "Our client has been the market leader in industrial automation for 15 years" |
| Complication | The disruption, gap, or problem that makes the situation untenable | "Digital-first competitors have captured 40% of new accounts; growth has fallen to 3% YoY" |
| Question | The natural question that arises from Situation + Complication | "How should the client sustain growth while protecting its core customer base?" |
| Answer | The recommendation that resolves the question | "Launch a digital-first subsidiary targeting under-35 customers while enhancing personalization for existing accounts" |

**Example (cost-reduction case):**
- Situation: "Client's operating margin has plateaued at 8%; peer average is 15%."
- Complication: "Input costs are rising 6% annually and fixed costs have not been rationalized since 2018."
- Question: "Where can the client close the 7-point margin gap without sacrificing revenue growth?"
- Answer: "Reduce costs by 30% through three initiatives — process redesign (15%), strategic sourcing (€50 M), and organizational delayering."

**Test:** Does the Situation feel like something the audience already believes? Does the Complication create a genuine problem? Does the Question follow inevitably? Does the Answer resolve it?

---

### 4. MECE Structure (Mutually Exclusive, Collectively Exhaustive)

**Principle:** Every grouping of arguments, chapters, or slides must be MECE — the items do not overlap (mutually exclusive) and together they cover the entire problem space (collectively exhaustive).

**Why:** Overlapping categories confuse the audience and waste slide real estate. Gaps leave the audience wondering "but what about X?" MECE is the structural backbone of the issue tree.

**Issue tree example (cost reduction):**
```
Total cost reduction potential
  ├── Revenue-side levers (pricing, mix)
  │     ├── Pricing optimization
  │     └── Channel mix shift
  └── Cost-side levers (COGS, OpEx)
        ├── Procurement / direct materials
        ├── Manufacturing efficiency
        └── SG&A rationalization
```
Revenue-side and cost-side are mutually exclusive (no overlap). Together they cover all levers (collectively exhaustive).

**MECE violations to avoid:**
- Overlap: "Digital marketing" and "Social media" (social media is a subset of digital marketing)
- Gap: Covering "pricing" and "procurement" but omitting "logistics" from a cost analysis
- Wrong level: Mixing strategic options ("enter market") with tactical actions ("hire sales reps") at the same level

**Test:** Can you add every relevant point exactly once to exactly one bucket? If any point fits two buckets or fits none, the structure is not MECE.

---

### 5. Hypothesis-Driven Structure

**Principle:** Organize the deck around hypotheses (testable beliefs about the answer), not around topics or functions. Each section corresponds to one hypothesis; the slides in that section present evidence that confirms or refutes it.

**Why:** Topic-organized decks ("Market", "Operations", "Finance") invite boiling-the-ocean analysis. Hypothesis-organized decks ("The market is large enough to justify entry") focus analysis and shorten timelines. If evidence disproves a hypothesis, the structure tells you what to cut or reframe.

**Example:**
- Topic-organized (weak): Section 1 — Industry overview | Section 2 — Company assessment | Section 3 — Financial model
- Hypothesis-organized (strong): Section 1 — The market opportunity exceeds €2 B and is underpenetrated | Section 2 — Our client has the capabilities to compete profitably | Section 3 — The financial case clears the 20% IRR hurdle

**Test:** Can you convert every section title into a yes/no hypothesis? If yes, the structure is hypothesis-driven. If a section is just a topic label ("Financial Overview"), force it into a hypothesis ("The financial model returns €180 M NPV at base-case assumptions").

---

### 6. Deck closes with implications and next steps

**Principle:** The final section of the deck states the implications of the analysis, the recommended actions, owners, timelines, and the next decision the client must make.

**Standard closing sections:**
1. Strategic recommendation (restates answer with conviction; imperative language)
2. Implementation roadmap (phased plan, workstreams, milestones)
3. Risks and mitigations (on a separate slide from the recommendation)
4. Next steps (specific actions, owners, dates)

**Example (next steps slide):**

| Action | Owner | Date |
|---|---|---|
| Approve JV term sheet | CFO + CEO | 15 June 2024 |
| Appoint integration lead | CHRO | 22 June 2024 |
| Sign NDA with NovaTech | Legal | 30 June 2024 |

**Test:** After reading the final two slides, does the audience know exactly what to do next, who is responsible, and by when?

---

## Part 2 — Slide-level craft

### 7. Action Titles (Full-Sentence Governing Thought)

**Principle:** Every content slide (`###`) carries a title that is a complete declarative sentence stating the conclusion of that slide — not a topic label. Reading only the titles in sequence should tell the complete story of the deck (the **horizontal flow test**).

**Why:** Topic labels force the audience to read the entire slide to understand the point. Action titles communicate the insight even to a skimmer. They also enforce one-idea-per-slide discipline: if you cannot write a single action title, the slide has two ideas.

**Weak → strong examples:**

| Weak title (topic label) | Why it fails | Strong action title |
|---|---|---|
| "Market Overview" | Generic; reader must infer the point | "European industrial automation market expected to grow ~5% CAGR through 2028, driven by energy-transition investment" |
| "Customer Segments" | Topic without implication | "Our client's revenue is concentrated in two segments, leaving 40% of the market currently untapped" |
| "Profitability Analysis" | Fails the so-what test | "Operating profit has fallen 7 pp since 2020 because SG&A grew twice as fast as revenue" |
| "Digital Strategy" | Too broad and non-specific | "Investing €50 M in omnichannel capabilities will double online sales within two years" |
| "Leadership Representation" | Observation, not conclusion | "Despite modest progress, women are still dramatically underrepresented in leadership" |

**Horizontal flow test:**
Take every `###` title in the deck in order. Read them as sentences in sequence. The result should read like a coherent, complete argument. If any title does not advance the story, rewrite or cut the slide.

**Test:** Can someone understand the deck's complete argument by reading only the slide titles?

---

### 8. One Idea Per Slide

**Principle:** Each content slide communicates exactly one insight. The title states it; the body proves it. If a slide seems to need two points, create two slides.

**Why:** Two ideas on one slide force the audience to decide which to process first. Cognitive load increases; neither idea lands with full force. A 30-slide deck with one clean idea per slide outperforms a 15-slide deck with muddy ones.

**How to test:** Cover the body of the slide. Does the action title express one complete idea? Uncover the body. Does every bullet or data point serve only that one idea? If any bullet belongs to a different idea, it needs its own slide.

**Example of a split:**
- Slide before split: "Revenue is growing but profitability is declining" (two ideas)
- After split:
  - Slide A: "Revenue has grown 18% in three years, driven by new enterprise accounts"
  - Slide B: "Despite revenue growth, EBITDA margin has compressed 4 pp due to cost inflation and under-investment in automation"

---

### 9. Synthesis Over Summary (the "So what?" test)

**Principle:** Every content slide must carry the client's implication — what does the evidence mean for them, and what decision does it call for? The "so what?" is a **review test**, not a slide section: if a slide only reports data without linking to an implication or recommendation, it is cut. Summary reports facts; synthesis draws conclusions that point to action.

**Why:** Data without implication is noise. A summary slide says "SG&A costs increased 25% last year." A synthesis slide says "SG&A now consumes 32% of gross margin — 10 pp above peers — and eliminating this gap would add €80 M to operating income." The second one earns its place in the deck.

**Where the implication lives:** In the action title and/or a closing bullet written as a natural conclusion — not as a labeled `**So what:**` footer appended to every slide. That pattern signals the implication was not properly embedded in the body. Use an explicit implication call-out box only when the slide demands a specific decision from the audience.

**Weak → strong examples:**

| Summary (observation) | Synthesis (implication) |
|---|---|
| "The market grew 8% last year" | "8% market growth creates a €400 M expansion opportunity the client can capture without additional capex" |
| "Employee satisfaction scores declined 12 points" | "A 12-point satisfaction drop correlates with a 2× increase in regrettable attrition — the client risks losing 300 high performers over the next 18 months" |
| "Three competitors entered the segment" | "Three new entrants have collectively captured 22% market share; inaction for another year risks ceding the segment permanently" |

**Test 1:** Replace the closing bullet with "Therefore, the client should [action]." If the action is obvious from the bullets, synthesis is present. If you have to guess, the slide summarizes instead of synthesizes.

**Test 2:** Cover the action title. Read only the body. Is the implication unmistakable without the title? If not, the body needs a stronger closing.

---

### 10. Low Text Density (≤40 Words Per Block)

**Principle:** No body block exceeds ~40 words. Use bullet points, short clauses, or tables. Prose paragraphs have no place on a content slide.

**Why:** Dense text shifts the audience's attention from listening to reading. Executives in a live setting lose the thread; executives skimming a leave-behind stop after two dense slides.

**Formatting tools:**
- **Bullets:** One idea per line; ≤15 words each
- **Tables:** For comparisons, rankings, timelines, options
- **Two-column layout:** Left = arguments; right = data (describe in text what a chart would show)
- **Call-out box:** For the so-what or a single critical number

**Example (too dense → fixed):**

Too dense:
> "The company's revenue has been growing at a compound annual growth rate of approximately 12% over the past three years, driven primarily by new enterprise customer acquisitions in the DACH region, while at the same time the cost structure has remained largely fixed, which has resulted in meaningful margin expansion across all three business units."

Fixed:
> - Revenue CAGR: ~12% over 3 years, driven by DACH enterprise wins
> - Cost structure: largely fixed → margin expansion across all 3 units

**Test:** Count the words in each body block. Any block over 40 words must be split or compressed.

---

### 11. Traceability and Consistency

**Principle:** Every figure, acronym, product name, and code is identical across every slide in the deck. If "Revenue" is labeled "Net Revenue" on slide 4, it must be "Net Revenue" everywhere. Numbers must be consistent: if a chart shows €220 M and the text says "over €200 M," that is a traceability failure.

**Why:** Inconsistency signals sloppy thinking. A partner reviewing the deck will lose confidence at the first mismatch. Executives in Q&A will ask "Is it 220 or 200?" and the presenter will lose credibility.

**Where it surfaces:**
- Same metric with different labels across slides
- A number in the executive summary that does not match the number in the supporting slide
- An acronym defined on slide 3 but never unpacked earlier or used inconsistently later
- A timeline on slide 12 that contradicts the milestone on slide 7

**Test:** In a final pass, for every number and label in the deck, find its first occurrence and verify every subsequent occurrence is identical.

---

## Part 3 — Analytical rigor

### 12. Quantification with Source for Every Figure

**Principle:** Every number, percentage, or benchmark carries (a) a specific value (not a range or approximation unless ranges are inherent to the data) and (b) a source citation with name and date placed immediately after the figure.

**Why:** McKinsey's credibility rests on data rigor. Unsourced numbers are guesses; vague ranges ("revenues of roughly €100-200 M") signal imprecision.

**Footnote format:** `Source: <name>, <date>` — placed inline on the same bullet or in a footnote at the bottom of the slide. Applies to **public/third-party** figures only.

**First-party / internal data:** Numbers that come from the user's own non-public material (internal files, proprietary or in-house analysis, first-party company data) are presented **without a citation** — no source line. A private filename adds no audience credibility and is routinely removed from the deck. Do not fabricate a public source for them; if a number purports to be public but cannot be traced, flag it as unverified instead.

**Weak → strong:**

| Weak | Strong |
|---|---|
| "The market is large and growing" | "The European industrial automation market reached €18.4 B in 2023 and is forecast to grow at 5.2% CAGR through 2028 (Source: Gartner, European Automation Report, Oct 2023)" |
| "Most customers prefer digital channels" | "67% of enterprise buyers complete vendor evaluation entirely through digital channels, up from 44% in 2019 (Source: Gartner, B2B Buyer Survey, 2023)" |
| "Costs are high" | "SG&A as a % of revenue: client at 28%, peer median at 18% (Source: Company filings, FY2023)" |

**Unverified reference flag:** If a statistic cannot be verified from the context files provided by the user, flag it:
> `⚠ Unverified — please confirm: "<exact claim>" (suggested source: <name>, if known)`

**Test:** Cover all source annotations. Read every number aloud. For each, ask: "If challenged in a board meeting, can we cite where this came from and when it was measured?"

---

### 13. Evidence-Backed Recommendations

**Principle:** Recommendations are derived from evidence, not stated as opinions. The pyramid structure makes the logic transparent: the recommendation is the synthesis of key arguments; each argument is supported by specific data.

**How evidence works in a McKinsey slide:**
1. Action title states the conclusion
2. Body presents 2-4 arguments that together prove the title
3. Each argument is supported by a specific data point with a source
4. The final bullet synthesizes the implication

**Example:**
> ### ### The client should prioritize DACH over Nordics in the first 18 months
>
> - Market size: DACH addressable market is €4.8 B vs. €1.2 B in Nordics (Source: IDC, 2023)
> - Client readiness: Existing DACH relationships cover 3 of the top 10 target accounts; zero in Nordics
> - Competitive density: DACH has 2 entrenched incumbents vs. 5 in Nordics — lower competitive friction
>
> **So what:** Sequencing DACH first concentrates resources on a 4× larger market where we already have access — the Nordics can follow in Phase 2 at lower marginal cost.

---

### 14. Benchmarking and Context

**Principle:** Numbers are rarely presented in isolation. Pair every key metric with a relevant benchmark: peer median, historical trend, industry standard, or target threshold.

**Why:** "Operating margin at 8%" means nothing without context. "Operating margin at 8% vs. peer median of 15%" creates urgency.

**Common benchmark types:**
- Peer comparison: client metric vs. industry median / best-in-class
- Trend: metric today vs. 2-3 years ago
- Target: current state vs. the strategic goal or covenant threshold
- Best practice: what world-class looks like (McKinsey proprietary benchmarks are not publicly available, so external sources must be cited)

**Example:** Women in the Workplace 2022 (McKinsey/LeanIn) uses benchmarks consistently: "Women represent 48% of entry-level employees but only 26% of C-suite leaders" — the gap between 48% and 26% creates the urgency that drives the deck's recommendations.

**Applied to exhibits:** A chart is structurally a comparison, so this principle is realized most fully in the exhibit — the comparator usually lives *inside* the chart (a target line, prior-period columns, peer bars, a delta callout). A contextless chart is worse than a contextless number: it dresses an empty point up as analysis. Consequently, the moment a message could be charted you have asserted a comparison — and if the sources hold the data point but not its comparator, that missing context must be obtained: ask the user for the benchmark/baseline/target/peer when it is the slide's load-bearing message, otherwise flag it (`⚠ Missing context`). Never emit a single-value "chart."

---

## Part 4 — Communication strategy

### 15. Recommendations as Imperatives, Risks Separately

**Principle:** State recommendations as imperatives ("Launch a digital-first subsidiary targeting customers under 35"), not as possibilities or hedged suggestions. Caveats and risks belong on a separate, dedicated slide.

**Why:** Mixing recommendation and risks dilutes both. The recommendation loses conviction; the risk section loses clarity. Executives need to hear the answer clearly before they evaluate the risks.

**Recommendation slide — language patterns:**
- "Acquire NovaTech for €220 M and close by Q3 2024"
- "Reduce headcount by 15% in the shared-services layer over 12 months"
- "Exit the consumer segment and redeploy €120 M of capital to enterprise"

**Risk slide — structure:**
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Integration delay | Medium | High | Pre-assign integration PMO; milestone gates |
| Customer churn during migration | Low | Medium | White-glove retention program for top 20 accounts |

**Test:** Read only the recommendation slide. Does it state what to do with conviction? Is there any hedging language ("it may be worth considering")? If so, revise.

---

### 16. Anticipating Objections

**Principle:** Build the analysis to address the objections a skeptical executive would raise. The hypothesis-driven structure and issue tree enumerate challenges before the audience does. Sensitive topics are handled explicitly — not avoided.

**Process:**
1. Before finalizing the outline, list the top 3-5 objections a skeptic would raise against the recommendation.
2. Assign each objection to a section or a slide.
3. If evidence rebuts the objection, use it. If it does not, acknowledge the risk and propose a mitigation.

**Example objections and placements:**
- "Is the market really big enough?" → Section: Market opportunity (prove it with data)
- "Can we afford it?" → Section: Financial case (prove IRR > hurdle rate)
- "What if integration fails?" → Risks slide (acknowledge and mitigate)

---

### 17. No Fluff

**Principle:** Every slide element must earn its place. Elements that do not advance the argument are cut.

**What McKinsey decks deliberately omit:**
- Warm-up bios ("About the team" slides) — the client already knows the firm
- Suspense narrative — no gradual build-up to a revelation; the answer comes first
- Hedging language ("It could be argued…", "One potential option might be…")
- Dense data tables that don't lead to a conclusion
- Decorative transitions, stock photos, clip art
- Section summaries that just repeat what was already said
- Corporate boilerplate

**Test:** Point to any element on a slide — a bullet, a number, a label. Ask: "If I remove this, does the argument weaken?" If not, remove it.

---

## Part 5 — Checklists

### Deck-level checklist

- [ ] Executive summary follows SCQA/SCR and appears in the first two slides
- [ ] Recommendation stated as an imperative within the first two slides
- [ ] Deck structure is MECE — no overlapping sections, no gaps
- [ ] Sections correspond to key hypotheses or issue-tree branches
- [ ] Horizontal flow test passes (titles alone tell the story)
- [ ] Deck closes with implications, next steps, owners, and dates
- [ ] Risks and caveats are on a separate slide from the recommendation

### Section-level checklist

- [ ] Section intro (`##`) carries a section number and a title that signals the hypothesis
- [ ] Transitions between sections link back to the storyline (e.g., "Having shown the market opportunity, we now examine our client's capability to compete")
- [ ] MECE holds within the section — slides don't overlap and together cover the hypothesis fully

### Slide-level checklist

- [ ] One governing thought per slide (one `###` action title)
- [ ] Action title is a full declarative sentence stating the conclusion
- [ ] Every bullet serves only the action title — nothing is extraneous
- [ ] Explicit so-what: the slide answers "what does this mean for the client?"
- [ ] Synthesis, not summary: the final bullet states the implication, not the observation
- [ ] ≤40 words per body block
- [ ] Every figure has a specific value and a `Source: <name>, <date>` footnote
- [ ] Traceability: figures and labels match all other slides where the same data appears
- [ ] No fluff: every element earns its place

### Reference validation checklist (per slide)

Apply the tiered hierarchy — cite public upstream sources, never an internal filename:

- [ ] **Tier 1 — public upstream** → claim traces to a public, third-party-verifiable source (regulation, statute, URL, dataset, study, agency authorization, named third party) → cite *that* source with `[N]`, **not the filename** (e.g. `Source: Ley 10/2010, Art. 17`, not `Source: Informe AML.md`). Never name an internal file as the via/intermediate source.
- [ ] **Tier 2 — internal / first-party** → claim originates from the user's own non-public material (internal file, proprietary observation, in-house analysis, first-party company data) → include with **no citation**: no `[N]`, no `Sources:` entry. Never fabricate a public source.
- [ ] **Tier 3 — unknown / unverifiable** → claim covered by no source and not first-party → flag `⚠ Unverified — please confirm: "<claim>" (suggested source if any)`
- [ ] No untraceable third-party claim passed off as fact; `Sources:` block omitted when a slide has no Tier 1 citations

---

## Part 6 — Quick-reference: Strong vs. weak patterns

### Titles

| Pattern | Example |
|---|---|
| **Weak: topic label** | "Digital Strategy" |
| **Strong: action title** | "Investing €50 M in omnichannel capabilities will double online sales within two years" |
| **Weak: observation** | "Profitability has declined" |
| **Strong: synthesis** | "Operating profit fell 7 pp since 2020 because SG&A grew at twice the rate of revenue — correcting this adds €80 M to EBITDA at peer-average ratios" |

### Storyline structures

**Market entry (SCQA + pyramid):**
1. Situation: Client is market leader for 15 years; core market growth slowing
2. Complication: Digital-first competitors captured 40% of new accounts; growth at 3%
3. Question: How to sustain growth while protecting core customers?
4. Answer: Launch digital-first subsidiary for under-35 segment + enhance personalization for existing accounts
5. Supporting sections: Market attractiveness → Client capability → Financial case → Implementation

**Cost reduction (SCR):**
1. Situation: Operating margin at 8%; peers average 15%
2. Complication: Input costs rising; fixed-cost base not rationalized since 2018
3. Resolution: Reduce costs 30% via process redesign (15%), strategic sourcing (€50 M), organizational delayering
4. Supporting sections: Benchmarking → Process redesign → Strategic sourcing → Next steps

### Benchmark patterns

| Type | Example |
|---|---|
| Peer median | "Client SG&A/revenue: 28% vs. peer median 18% (Source: Company filings, FY2023)" |
| Trend | "Women in C-suite: 26% in 2023 vs. 17% in 2015 (Source: McKinsey Women in the Workplace, 2023)" |
| Target threshold | "EBITDA at 8.2% — below the 10% covenant threshold that triggers lender review" |
| Best-in-class | "Best-in-class NPS in this segment: 72; client NPS: 41 — a 31-point gap (Source: Bain NPS Benchmark, 2023)" |
