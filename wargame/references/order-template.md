# Order template

One `tasks/NN-<surface>.md` per failure surface. Fill every `{{PLACEHOLDER}}` with concrete
detail from recon — the more specific the brief, the better the wargame. If a target is
fully known (you've read all its code), the brief can be concrete with no placeholders left.

---

WARGAME ORDER. You are not executing this mission, you are wargaming it. A cheaper executor
({{EXECUTOR — e.g. a mid-tier model running against this target}}) runs the brief below
later. Your job is the route it will follow.

Recon first, read-only: {{THE EXACT FILES / CONFIG / DOCS TO READ FOR THIS SURFACE, with
paths — name the functions, the branches, the failure strings, the config values}}.

Then fight the mission on paper, move by move, and write it to wargames/NN-{{surface}}.md:

- every move states its expected observation, exactly what you should see if it worked
- every move carries its most likely failure, the cause it signals, and the counter-move
- every fork gets a trigger, if you observe X, take route B
- assumptions recon could not settle get marked RECON NEEDED with the exact check that settles it
- end with abort conditions, and the verification runs the executor must perform with what pass looks like for each

Write it so the executor can run the brief end to end without asking a single question. Never
instruct a route that bypasses {{THE TARGET'S OWN GUARDRAILS — e.g. forcing past a check,
hand-editing protected state, faking a result}}.

=== THE MISSION BRIEF (the executor's orders, not yours) ===

{{WHAT THIS SURFACE MUST ACHIEVE, in concrete terms: the operation, its real inputs/outputs
and paths, and the specific failure modes to exercise — routing errors, timeouts, throttling,
malformed input, partial success, wrong-but-successful content, races, etc. Ground every
failure mode in something recon actually found. End with the one action that proves the
surface is sound: "prove that every input either <correct outcome> or is <honestly recorded
as failed> — never a silently-wrong success."}}
