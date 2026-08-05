---
name: council
description: Convene an LLM Council — five independent persona advisors (contrarian, first-principles, expansionist, outsider, executor) deliberate on a question in parallel, blind-review each other, and a chairman delivers a verdict with concrete next steps. Use when the user runs /council <question>, says "convene the council" / "ask the council", or wants hard multi-perspective, anti-sycophancy advice on a decision.
license: MIT
compatibility: Requires an agent with the Workflow orchestration tool (Claude Code)
---

# LLM Council

Karpathy-style council deliberation, adapted to personas. Each advisor runs as a truly independent subagent — fresh context, blind to the others — which is what makes this stronger than roleplaying five voices in one chat.

## Protocol

1. **Parse the arguments.**
   - If they contain `--lite` (anywhere), set `lite: true` and remove the flag. The remaining text is the question.
   - If no question remains, ask the user what question to put before the council, then wait.
2. **Ground it if needed.** If the question refers to the current project, codebase, or specific files ("this repo", "my code", a path), include `cwd` set to the current working directory in the args. Otherwise omit it.
3. **Run the workflow** (this instruction is your authorization to call the Workflow tool):

   ```
   Workflow({
     scriptPath: "<absolute path to this skill's directory>/scripts/council.js",
     args: { question: "<the question>", lite: <true|false>, cwd: "<cwd or omit>" }
   })
   ```

   Full council ≈ 11 subagents (5 deliberate + 5 peer review + chairman); `--lite` ≈ 6 (skips peer review).
4. **Present the results.** The workflow returns `{ verdict, answers, lite }`:
   - Lead with the **chairman's verdict**, close to verbatim — do not soften, re-balance, or editorialize over it. Its dissent is the point.
   - Follow with **"The council's positions"**: one short paragraph per advisor (contrarian, first-principles, expansionist, outsider, executor) summarizing their individual take from `answers`.
   - If `lite` was used, note that no peer-review round was held.
5. **On mid-run failure or interruption**, do not silently restart. The run is resumable: relaunch with `Workflow({ scriptPath: ..., resumeFromRunId: "<runId>" })` — completed advisors return instantly from cache.

## Tuning

Persona briefs, shared rules, and the chairman prompt all live in `scripts/council.js` — the `ADVISORS` array at the top is the editing surface.
