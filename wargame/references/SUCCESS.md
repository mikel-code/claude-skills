# SUCCESS.md · the definition of a properly wargamed surface

Copy this file into each wargame working directory. A battle plan **passes only when ALL
eight hold** — grade every one against this and record the result in `LEDGER.md`. A plan
that has the section headers but fails any point is NOT done; fail it loudly.

1. **Expected observation.** Every move states exactly what you should see if it worked — a
   file at a real path, a specific exit code, a log line, a concrete value. Not "it should
   work" — *what you observe* when it does.

2. **Failure + counter-move.** Every move carries its most-likely failure, the signal that
   failure gives off, and the counter-move that answers it.

3. **Forks have triggers.** Every fork specifies the observation that triggers it ("if you
   observe X, take route B"). No judgment call is left to the executor.

4. **RECON NEEDED is explicit.** Every assumption recon could not settle is marked
   `RECON NEEDED` with the **exact check** that settles it (a command to run, a file to
   read). Nothing is guessed away.

5. **Abort conditions exist.** The moments to stop and flag rather than improvise are named.
   Bypassing the target's own guardrails (hand-editing protected state, forcing past a
   check, faking a result) is never an acceptable escape.

6. **Verification is spelled out.** The real commands the executor runs, when, and what
   "pass" looks like for each — using the target's actual tools and evidence trail, not
   hand-waving.

7. **Survived a red-team pass.** The doc records the attack that **failed** against it, and
   the attack that **landed** plus the patch it forced. A red-team with no landing attack
   is either incomplete or explicitly marked "unfalsified — no landing attack found."

8. **Executable blind.** A mid-tier model could run the mission end to end against the real
   target without asking a single question, and without violating any of the target's own
   rules.

---

*This rubric is target-agnostic. When you copy it into a working dir you may add a
one-line, target-specific preface (e.g. naming the guardrails that must never be bypassed),
but do not weaken any of the eight points.*
