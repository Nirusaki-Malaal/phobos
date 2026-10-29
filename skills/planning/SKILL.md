---
name: planning
description: Planning discipline for multi-step work — owns the persistent task_plan.md/findings.md/progress.md discipline in <EXECUTION_DIR>/.project/planning/, plus plan authorship and step-by-step execution with verification gates. Load when a task has 3+ steps, research, or multi-file changes.
---

# Planning

> You own planning **discipline, paths, authorship and execution**. Shared pai-stack glue
> (EXECUTION_DIR, vault, citations, propose-before-write) is canonical in the `agents`
> skill — follow it, it is not repeated here.

## When to use
- IF a task needs 3+ steps, multi-file changes, or research, THEN load this skill.
- IF the task is a single-file edit or one-off lookup, THEN skip.

## Pai paths (non-negotiable)
```
<EXECUTION_DIR>/.project/planning/<YYYY-MM-DD-slug>/
  task_plan.md   ← phases, decisions, ## Next Step (update per phase)
  findings.md    ← discoveries (2-operation rule: write after every 2 searches/reads)
  progress.md    ← session log, errors, test results
```
- One plan per task. Re-read `task_plan.md` before major decisions.
- Log ALL errors in `task_plan.md` (`| Error | Attempt | Resolution |`).
- 3-strike protocol: diagnose → different approach → rethink → escalate with attempts table.

## Plan authorship

A plan is not a to-do list. It is a record of what you decided and why, such that a future
session with no memory of this conversation can resume safely.

1. **Phases over steps.** Group work into 3–9 phases, each ending at a *runnable state*. A
   phase that leaves the system broken is not finished.
2. **Order by dependency, not severity.** A one-line config fix that unblocks the docs comes
   before the security fix, because you cannot describe behaviour until it runs. Severity
   wins only when something is live-exploitable now.
3. **Every phase carries a gate** — the observable check that proves it worked. If you cannot
   write the gate, the phase is not yet understood.
4. **Mark decisions explicitly.** Anything that is a judgement call rather than a fact gets
   its own item with the question spelled out, so the user can answer it in one line.
5. **Record the non-obvious reasoning.** Anyone can read the final state; what is expensive to
   re-derive is *why this order* and *what was already ruled out*.

## Execution

1. Re-read `task_plan.md` before starting each phase and after any decision.
2. Do **one** phase per session turn where possible; land it runnable, then stop.
3. Propose diffs before writing code changes. See the `agents` skill.
4. After every 2 search/read operations, write what you learned to `findings.md` with
   `path:line` anchors.
5. Verify with a command, not an inspection. "It looks right" is not a gate.
6. Mark items complete as you go; a plan whose checkboxes are all open is worthless.

## File-anchored findings

Every claim in `findings.md` cites `path:line`. A finding without an anchor is a hunch —
either anchor it or drop it. Findings that turn out to be wrong get deleted, not hedged.

## Outputs
- `task_plan.md` always has a single `## Next Step` before you conclude.
- Planning files are indexed — future sessions find them via code intelligence.
