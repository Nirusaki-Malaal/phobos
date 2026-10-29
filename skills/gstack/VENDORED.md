# gstack skills — vendored provenance

Source: https://github.com/garrytan/gstack (MIT, (c) 2026 Garry Tan)
Version: 1.91.27.0 / commit c285d88 (fetched 2026-10-06, shallow clone)

Scope: curated workflow subset (26 dirs). Browser-dependent skills
(qa, qa-only, scrape, skillify, browse, design-*, canary, benchmark,
ios-*) intentionally excluded — they need gstack's browse/design
binaries, which are not installed in this stack.

Layout: `gstack` (router, from repo-root SKILL.md) + `gstack-<name>`
for each skill (upstream `--prefix` naming). Frontmatter reduced to
`name` + `description` per repo convention; bodies verbatim.

**`name:` is authoritative and does NOT have to match the directory.** Hermes
resolves a skill by `frontmatter_name or skill_name`
(`agent/prompt_builder.py::_entry_name`), so two vendored skills register under
their upstream names rather than their folder names:

| directory | load it as |
|---|---|
| `react-best-practices` | `vercel-react-best-practices` |
| `composition-patterns` | `vercel-composition-patterns` |

Reference them by their `name:` (as `AGENTS.md` does). Renaming those
directories to "fix" the mismatch would silently break every reference.

Descriptions are ours to write, and were rewritten (2026-10-08) so the router can
choose between siblings: the review family splits plan-time vs implemented, and
shipping is two stages — `gstack-ship` stops at PR creation,
`gstack-land-and-deploy` merges and verifies production. Upstream's one-line
descriptions rendered both as "Land and deploy workflow", which misroutes.

Vetting (2026-10-06, via `skill-security-auditor`):
23 PASS clean; `autoplan` CRITICAL = RegExp.exec false positive in a
TS helper; `careful` HIGH = legit `python3 -c` JSON-escaping in a hook
script (hooks don't execute under Hermes/DSH anyway). Safe to install.

Discovery: no compose/config change needed — `./skills` is already
mounted into Hermes (`/opt/pai/skills` via `skills.external_dirs`) and
DSH (`/data/dsh/.agents/skills` via `DSH_AGENTS_HOME`), so both agents
pick these up via `skills_list()` on next session/container start.

## Known gaps (expected — do not go looking)

**`bin/` is only vendored for 3 skills** (`gstack-careful`, `gstack-freeze`,
`gstack-autoplan` — the ones whose hooks actually gate file edits). The other 19
bodies still reference `bin/gstack-question-log`, `bin/gstack-paths`,
`~/.claude/skills/gstack/bin/gstack-skill-start`, etc. **This is handled**: the
shared auto-generated Preamble block carries an explicit degraded mode — when
`SKILL_START_PROTO: 1` is absent it treats `SESSION_KIND` as `interactive`, skips
the onboarding/telemetry gates (they are marker-based and deferred, never lost),
tells the user to run `/gstack-upgrade`, and proceeds. So the missing scripts
degrade the suite, they do not break it.

**Three prose references have no target**, because upstream design docs were not
part of the curated subset: `docs/designs/PLAN_TUNING_V0.md` and `V1.md`
(referenced by `gstack-plan-tune`), and `docs/designs/` (referenced by
`gstack-review` and `gstack-ship`). These are *informational* deep-dives — the
skills work without them. Fixing it means fetching those files from upstream gstack;
do not delete the references, since bodies are verbatim.

Upgrade: re-clone gstack, re-copy the same 26 dirs, re-apply the
frontmatter reduction, update Version/commit above.
