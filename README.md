# Claude Skills

A personal collection of [Agent Skills](https://code.claude.com/docs/en/skills) for [Claude Code](https://claude.com/claude-code) — decision-making, planning rigor, and slide-deck production.

Each skill is a self-contained folder with a `SKILL.md` (metadata, triggers, and instructions) plus any scripts or reference material it needs. Claude Code discovers them automatically and invokes them when your request matches, or you can call one explicitly with `/<skill-name>`.

## Installation

### Plugin marketplace (recommended)

From Claude Code, add the marketplace once, then install the skills you want:

```
/plugin marketplace add mikel-code/claude-skills

/plugin install council@mikel-skills
/plugin install wargame@mikel-skills
/plugin install mckinsey-deck@mikel-skills
/plugin install deck-design-prompt@mikel-skills
```

(Also works non-interactively: `claude plugin marketplace add mikel-code/claude-skills && claude plugin install council@mikel-skills`.)

### skills CLI

Using the [skills](https://github.com/vercel-labs/skills) CLI — install everything, or pick with `--skill`:

```bash
npx skills add mikel-code/claude-skills
npx skills add mikel-code/claude-skills --skill wargame
```

### Manual

Clone and copy (or symlink) skills into your user-level skills directory:

```bash
git clone https://github.com/mikel-code/claude-skills.git
cd claude-skills
for d in council deck-design-prompt mckinsey-deck wargame; do
  cp -R "$d" ~/.claude/skills/    # or: ln -s "$(pwd)/$d" ~/.claude/skills/
done
```

Restart Claude Code (or start a new session) and the skills appear in the available-skills list. To install a skill for a single project instead, place it under `<project>/.claude/skills/`.

## Skills

| Skill | What it does |
|---|---|
| [council](council/) | Convene an LLM Council — five independent persona advisors (contrarian, first-principles, expansionist, outsider, executor) deliberate on a question in parallel as blind subagents, peer-review each other, and a chairman delivers a verdict with concrete next steps. Anti-sycophancy by construction. `/council <question>`, with a `--lite` mode that skips peer review. |
| [wargame](wargame/) | Wargame a subsystem — fight its failure surfaces on paper move-by-move (expected observation, most-likely failure + counter-move, forks with triggers, abort conditions, verification, and a red-team that must actually land) so a cheaper model or a future you can execute or harden it blind. `/wargame <target>`, optional `--orders-only`. |
| [mckinsey-deck](mckinsey-deck/) | Create or iterate on slide-deck **content** in Markdown applying McKinsey criteria: Pyramid Principle, action titles, MECE, so-what, quantification with sources. Proposes an outline, then produces one slide at a time with feedback loops. Output is a `deck.md` — content only, no visual references. |
| [deck-design-prompt](deck-design-prompt/) | Reads a finished `deck.md` (from mckinsey-deck) and outputs a ready-to-paste prompt for Claude Design that applies visual design without touching any content. |
| [granola](granola/) | Access [Granola](https://granola.ai) meeting notes through the Granola API — list notes, show a note's summary/attendees/transcript, export to Markdown. The API key lives in Proton Pass behind an item-scoped, audited agent, and is injected per-invocation via `pass-cli run`, so it never enters the agent's context. Requires one-time setup (see the skill's SKILL.md). |
| [yt-transcript](yt-transcript/) | Transcribe a video URL (YouTube or any yt-dlp-supported site) to a markdown file with [OKF](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md) frontmatter. Downloads only the subtitle track when captions exist; otherwise downloads audio-only and transcribes locally with mlx-whisper — deterministic validation (repetition/n-gram loops, compression ratio, timestamp coverage) plus a repair ladder (condition-off → chunked splice → large-v3 → needs-attention). Requires `uv` + `ffmpeg`; whisper fallback needs Apple Silicon. |
| [pdf-to-markdown](pdf-to-markdown/) | Convert a PDF to OKF-frontmatter markdown through a tiered, correctness-first pipeline: triage (pdf-inspector), structure extraction (pdf-inspector, or docling for table-heavy docs), OCR for scanned pages (ocrmypdf), cross-engine verification (poppler), and Claude-vision escalation for pages the tools can't resolve — with a per-page report so nothing is ever dropped silently. Design rationale in the skill's `references/design.md`. |
| [doc-to-markdown](doc-to-markdown/) | Convert office documents (docx/pptx/xlsx, OpenDocument, rtf, epub, csv…) to OKF-frontmatter markdown via [anydoc](https://github.com/firecrawl/anydoc). PDFs are refused by design — they route to `pdf-to-markdown` instead. |
| [url-to-markdown](url-to-markdown/) | Scrape a URL into OKF-frontmatter markdown through a cheap-and-private-first tier ladder: local trafilatura, headless Chrome rendering, keyless Jina Reader, optional Firecrawl — with an adversarially-hardened sanity gate deciding escalation (challenge/consent/login/soft-404/garble detection, multilingual), full tier provenance in the output, and a claude-in-chrome workflow for login-walled pages. File URLs hand off to the sibling converter skills. |

`mckinsey-deck` → `deck-design-prompt` form a pipeline: build the content first, then hand it to design. `doc-to-markdown` and `pdf-to-markdown` split one problem by complexity: office formats are a single-tool conversion, PDFs get the multi-tier pipeline.

## Skill anatomy

```
skill-name/
├── SKILL.md          # Metadata, triggers, usage
├── CHANGELOG.md      # Version history
├── scripts/          # Executable orchestration
├── assets/           # Resources
└── references/       # Detailed docs
```

Notes:

- **council** runs its deliberation through Claude Code's `Workflow` tool via `scripts/council.js` — the SKILL.md tells Claude to pass the absolute path of the installed skill directory, so both copy and symlink installs work.
- **wargame** and **mckinsey-deck** keep their heavyweight methodology in `references/` so the main prompt stays lean.

## License

[MIT](LICENSE)
