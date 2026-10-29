# mcp-builder (vendored)

- Source: `anthropics/skills`, path `skills/mcp-builder`
- SHA: `33375500bcea98d610eb30ce10ac4e59b89c390d` (vendored 2026-09-25)
- License: Apache-2.0 (`LICENSE.txt` in this dir)
- Contents: `SKILL.md` + `reference/*.md` (4) + `scripts/` (connections.py, evaluation.py, example XML, requirements.txt)
- Caveats: `scripts/evaluation.py` imports `anthropic` SDK (Claude-locked — route through llm-gateway on Hermes); `requirements.txt` vendored as reference, NOT installed. pai-stack prefers native `pai_tools` plugins — use this only for new external MCP integrations.
- Status: pristine upstream copy.
