# Claude Skills

A personal collection of [Agent Skills](https://code.claude.com/docs/en/skills) for [Claude Code](https://claude.com/claude-code) — decision-making, planning rigor, and slide-deck production.

Each skill is a self-contained folder with a `SKILL.md` (metadata, triggers, and instructions) plus any scripts or reference material it needs. Claude Code discovers them automatically and invokes them when your request matches, or you can call one explicitly with `/<skill-name>`.

## Installation

Clone the repo and copy (or symlink) the skills you want into your user-level skills directory:

```bash
git clone https://github.com/mikel-code/claude-skills.git
cd claude-skills

# Copy everything:
for d in council deck-design-prompt mckinsey-deck wargame; do
  cp -R "$d" ~/.claude/skills/
done

# …or symlink so `git pull` keeps them up to date:
for d in council deck-design-prompt mckinsey-deck wargame; do
  ln -s "$(pwd)/$d" ~/.claude/skills/
done
```

Restart Claude Code (or start a new session) and the skills appear in the available-skills list.

> To install a skill for a single project instead, place it under `<project>/.claude/skills/`.

## Skills

| Skill | What it does |
|---|---|
| [council](council/) | Convene an LLM Council — five independent persona advisors (contrarian, first-principles, expansionist, outsider, executor) deliberate on a question in parallel as blind subagents, peer-review each other, and a chairman delivers a verdict with concrete next steps. Anti-sycophancy by construction. `/council <question>`, with a `--lite` mode that skips peer review. |
| [wargame](wargame/) | Wargame a subsystem — fight its failure surfaces on paper move-by-move (expected observation, most-likely failure + counter-move, forks with triggers, abort conditions, verification, and a red-team that must actually land) so a cheaper model or a future you can execute or harden it blind. `/wargame <target>`, optional `--orders-only`. |
| [mckinsey-deck](mckinsey-deck/) | Create or iterate on slide-deck **content** in Markdown applying McKinsey criteria: Pyramid Principle, action titles, MECE, so-what, quantification with sources. Proposes an outline, then produces one slide at a time with feedback loops. Output is a `deck.md` — content only, no visual references. |
| [deck-design-prompt](deck-design-prompt/) | Reads a finished `deck.md` (from mckinsey-deck) and outputs a ready-to-paste prompt for Claude Design that applies visual design without touching any content. |

`mckinsey-deck` → `deck-design-prompt` form a pipeline: build the content first, then hand it to design.

## Skill anatomy

```
skill-name/
├── SKILL.md          # frontmatter (name, description/triggers) + instructions
├── *.js              # workflow scripts, if the skill orchestrates subagents
└── references/       # detailed method docs loaded on demand
```

Notes:

- **council** runs its deliberation through Claude Code's `Workflow` tool via `council.js` — the SKILL.md tells Claude to pass the absolute path of the installed skill directory, so both copy and symlink installs work.
- **wargame** and **mckinsey-deck** keep their heavyweight methodology in `references/` so the main prompt stays lean.

## License

[MIT](LICENSE)
