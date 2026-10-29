---
name: opencode-delegate
description: Delegate coding tasks (bug fixes, small features, multi-file refactors) to keyless OpenCode free models running headless in the hermes container. Use when a task is handed off for implementation, or when polling/resuming/reviewing a delegated run.
---

# OpenCode Delegate — keyless background delegation

> Hermes delegates implementation to OpenCode v2 (pinned in image, `opencode --version`).
> Model is ALWAYS a keyless free model (default `opencode/muse-spark-1.3-contributor-free`).
> No credentials exist anywhere in this path — no `auth login`, no API keys, no exceptions.

## Preflight (every delegation session, once)
`command -v opencode && opencode --version` (you run inside hermes already — no docker exec needed).
If the binary is missing → report degraded (image predates delegation), do NOT proceed.

## Launch (background + poll — shell caps apply, never foreground-run)
```
opencode-delegate run <EXECUTION_DIR> <slug-YYYY-MM-DD> "<task prompt>"
```
- `<slug>` is unique per task (idempotency guard refuses reuse).
- Wrapper validates: dir under `/opt/data`, model in free allowlist, creates
  `opencode/<slug>-<date>` branch in git repos (records base SHA), launches
  `opencode run --standalone --agent delegate --format json`, writes
  `/opt/data/opencode/<slug>/{meta.json,out.jsonl,project}`.
- Record `meta.json` (pid, model, branch, base SHA) in `progress.md`.
- Optional 4th arg overrides model — wrapper refuses non-free IDs and `pai/*`
  unless `OPENCODE_ALLOW_GATEWAY=1` (fallback path, disabled until proven).

## Poll (short shell calls until `"running": false`)
```
opencode-delegate status <slug>
```
Returns steps, tokens in/out (metering — log totals per run), session id, last text.
Poll every few turns; do other work between polls. Never `tail` the raw jsonl by hand —
`status` parses it.

## Resume (timeout, 429, or killed run)
```
opencode-delegate resume <slug> ["extra prompt"]
```
Resumes by session id (validated primitive). Back off on 429; abort after 3 resumes
and report. Kill by exact PID from `meta.json` only — never `pkill -f` (self-match
kills your own shell).

## Review → merge (propose-before-write survives delegation)
1. Delegate commits on its task branch (enforced in its system prompt); review with
   `git -C <EXECUTION_DIR> diff --stat <base>..<branch>` (fall back to worktree
   `git diff --stat` if the branch tip equals base, then commit it yourself on the branch).
2. Re-run the delegate's test command yourself if cheap.
3. Show the user the diff summary; merge only on explicit confirmation.
4. Audit: grep the run log for `docker`, `push`, `.env`, and secret shapes
   (`sk-`, `eyJ`, `ghp_`, `xox`, `Bearer`, `AKIA`). Any hit → escalate, do NOT merge.
5. Delete stale `opencode/<slug>-*` branches after merge; note token totals in `progress.md`.

## Rules
- Secrets-adjacent repos (anything holding live keys, e.g. pai-stack itself): Zen-free
  is fine for code, but prefer `pai/default` local-only once proven; never paste secrets
  into task prompts.
- One delegate per project dir at a time unless slugs are unique and branches distinct.
- Free tier throttles: space out parallel delegates; serialize on 429.
