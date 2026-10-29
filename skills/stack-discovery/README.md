# Stack Discovery

**Stack Discovery** is an MCP skill available in the `pai-stack`.

Auto-detect tech stack for any project under /opt/data/workspace so Hermes picks the right skill before reading code.

## How to Use (For Humans & Agents)

### For Agents
When interacting with the repository, this skill will be automatically loaded or fetched if the context calls for it. The agent is strictly bound by the rules and workflows defined in the `SKILL.md` file.

### For Developers
You can explicitly trigger this skill by prompting your agent (e.g., Hermes, Claude Code, Antigravity) to use it. This forces the agent to read the structured instructions and adhere to your project's standardized workflows, preventing hallucinations.

**Example Prompt:**
> "Use the `stack-discovery` skill to help me with..."

## Technical Details
This skill is loaded natively by Hermes from `./skills/` (mounted read-only via `skills.external_dirs`). The operational logic, ground rules, and system prompt payloads reside in `SKILL.md`, loadable via `skill_view`.
