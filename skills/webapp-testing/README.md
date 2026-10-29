# webapp-testing (vendored)

- Source: `anthropics/skills`, path `skills/webapp-testing`
- SHA: `33375500bcea98d610eb30ce10ac4e59b89c390d` (vendored 2026-09-25)
- License: Apache-2.0 (`LICENSE.txt` in this dir)
- Contents: `SKILL.md` + `scripts/with_server.py` (stdlib-only) + `examples/*.py` (3)
- Runtime note: scripts that drive a browser need `playwright` installed where Hermes runs it (NOT in hermes image — install on demand, never vendored).
- Status: pristine upstream copy. Fills the testing gap (TDD discipline ≠ browser verification).
