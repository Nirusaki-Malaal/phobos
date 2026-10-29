# Delegation (OpenCode, keyless)

Hermes supervises; OpenCode v2 (pinned in the `hermes` image) implements headless in-container. Delegates use **only keyless free models** (default `opencode/muse-spark-1.3-contributor-free`) — no login, no keys to rotate.

```
Hermes (supervise) ──opencode-delegate run──→ opencode --standalone (background)
  poll: opencode-delegate status → stall/429? resume → review diff → merge on confirmation
```

- Wrapper `/usr/local/bin/opencode-delegate` (`run`, `status`, `resume`, `logs`, `kill`): enforces free-model allowlist, scopes runs to `/opt/data`, works on `opencode/<task>-<date>` branches, meters tokens per run.
- Containment: v2 hard-deny policies (`docker *`, `*push*`, `*.env` reads) override project configs + post-run log audit. Policies are pattern-based and bypassable by adversarial prompts — the audit grep is the backstop.
- Merges always require explicit user confirmation.
- Orchestration: Kanban `coder` worker + `build_task` pattern. Full protocol: `skill_view(name="opencode-delegate")`.
