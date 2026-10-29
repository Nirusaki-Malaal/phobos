# skill-security-auditor (vendored)

- Source: `alirezarezvani/claude-skills`, path `engineering/skills/skill-security-auditor`
- SHA: `19392f7a08264ed00486a251f5b2098321771f94` (vendored 2026-09-25)
- License: MIT (`LICENSE` copied from repo root @SHA)
- Contents: `SKILL.md` + `scripts/skill_security_auditor.py` (stdlib-only, 1074 lines) + `references/threat-model.md`
- Purpose: pre-install supply-chain gate for third-party skills (injection/exfil/boundary audit) — use before every future vendor. Different domain from `docker-development` (containers) and `senior-backend` (app OWASP).
- Status: pristine upstream copy. Script audited 2026-09-25 (see findings.md §Tier-2).
