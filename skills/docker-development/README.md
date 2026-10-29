# docker-development (vendored)

- Source: `alirezarezvani/claude-skills`, path `engineering/docker-development/skills/docker-development`
- SHA: `19392f7a08264ed00486a251f5b2098321771f94` (vendored 2026-09-25)
- License: MIT (`LICENSE` copied from repo root @SHA)
- Contents: `SKILL.md` + `scripts/dockerfile_analyzer.py` + `scripts/compose_validator.py` + `references/*.md` (2)
- Caveats: `/docker:*` slash commands + `~/.claude/skills/` install paths are Claude-Code-isms (ignored on Hermes; invoke via `skill_view`). Scripts audited 2026-09-25 (see findings.md §Phase 2).
- Status: pristine upstream copy. Hermes glue lives in local `docker` shim (Phase 4), not here — keeps re-sync clean.
