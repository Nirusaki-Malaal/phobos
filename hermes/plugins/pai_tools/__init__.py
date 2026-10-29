"""pai-stack native tools for Hermes — registered via plugins/pai_tools.

- pai_notebook_ops — file-based Research Brain vault (Markdown + frontmatter)
- pai_adr_ops      — Living ADRs with SHA-256 symbol drift detection
- pai_ops_design_ops — OpenDesign REST bridge (visual generation / mockups)

Docker is intentionally NOT a tool: both agents drive the mounted
/var/run/docker.sock with the native `docker` + `docker compose` binaries
(see the `docker` skill for socket scope, smart --format output, and
help-first flag discovery).

`pai_code_intel` was removed 2026-10-04. Code intelligence is intentionally
absent from pai-stack; a dedicated lightweight tool replaces it.
"""

from plugins.pai_tools import adr_ops, notebook_ops, ops_design_ops


def register(ctx) -> None:
    """Register all pai-stack tools. Called once by the plugin loader."""
    ctx.register_tool(
        name="pai_notebook_ops",
        toolset="pai",
        schema=notebook_ops.SCHEMA,
        handler=notebook_ops.handle,
        emoji="📓",
    )
    ctx.register_tool(
        name="pai_adr_ops",
        toolset="pai",
        schema=adr_ops.SCHEMA,
        handler=adr_ops.handle,
        emoji="🏛",
    )
    ctx.register_tool(
        name="pai_ops_design_ops",
        toolset="pai",
        schema=ops_design_ops.SCHEMA,
        handler=ops_design_ops.handle,
        emoji="🎨",
    )
