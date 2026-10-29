---
name: gitops
description: Git safety and mechanics — branch topology, .worktrees/ sandboxing, the user-confirmation gate for mutating git ops, and the test-first merge/finish flow. Load for status/log/branch/commit/worktree/merge/PR work.
---

# GitOps

> You own git **safety invariants and mechanics**. Shared pai-stack glue is canonical in the
> `agents` skill.

## Invariants (always apply, upstream has none of these)
1. **Topology**: `dev` (if present) is the integration branch, else `main`/`master`. `feature/*`, `fix/*` branch from it; `hotfix/*` branches from `main` and merges back to both.
2. **Sandbox**: new work goes in `.worktrees/<task-slug>` (`git worktree add .worktrees/<slug> -b <type>/<name> <base>`); switch `EXECUTION_DIR` into it; propose `.gitignore`-ing `.worktrees/` if missing.
3. **Confirmation gate**: NEVER run mutating git commands (`worktree add`, `commit`, `merge`, `push`, `rebase`) without proposing the exact command and waiting for explicit approval. Reads (`status`, `log -n`, `diff`, `branch -a`, plumbing) need no confirmation. Conventional Commits for messages.

## Worktree isolation
```bash
git worktree add .worktrees/<slug> -b <type>/<name> <base>
```
- Detect before creating: if `.worktrees/` is absent or the current branch is already a
  feature branch with uncommitted work, stop and report rather than nesting.
- **Clean-baseline gate**: never branch from a dirty tree. `git status --porcelain` must be
  empty, or the worktree inherits unrelated changes and the eventual merge is unreviewable.
- After creating one, `EXECUTION_DIR` moves into it. Every subsequent command is prefixed
  with `cd <EXECUTION_DIR> &&`.
- Propose `.gitignore`-ing `.worktrees/` if it is not already ignored.

## Finishing a branch
1. **Tests first, always.** A branch whose tests were not run before the merge is not
   finished. Run them in the worktree, not on the integration branch.
2. Re-read the full diff, not the summary — `git diff <base>...HEAD`. Review for secrets,
   debug leftovers, and files that should not have been touched.
3. Merge into the integration branch, then delete the worktree *and* the branch:
   ```bash
   git worktree remove .worktrees/<slug> && git branch -d <type>/<name>
   ```
   `branch -d` (not `-D`) so an unmerged branch fails loudly instead of vanishing.
4. Push only on explicit confirmation, and confirm the remote and branch name in the same
   breath as the command.
