# code-reviewer (vendored)

- Source: `alirezarezvani/claude-skills`, path `engineering-team/skills/code-reviewer`
- SHA: `19392f7a08264ed00486a251f5b2098321771f94` (vendored 2026-09-25)
- License: MIT (`LICENSE` copied from repo root @SHA)
- Contents: `SKILL.md` + `rules/universal.md` + `languages/*.md` (13) + `scripts/*.py` (3, stdlib-only) + `assets/` + `expected_outputs/` fixtures
- Relation: Hermes builtin `requesting-code-review` owns the review *flow*; this skill adds severity rubrics, language-specific gout, and deterministic `--json` automation. Complementary, not duplicate.
- Status: pristine upstream copy. Scripts audited 2026-09-25 (see findings.md §Tier-2).
