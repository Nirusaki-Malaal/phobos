"""pai_terrain_ops plugin — registers the Terrain code-intelligence tool.

Kept as its own plugin rather than folded into pai_tools because terrain is an
opt-in host capability, not a always-present one. The check_fn probe means the
tool silently disappears from the catalog when `make terrain-up` has not been
run.
"""

from plugins.pai_terrain_ops import terrain_ops


def register(ctx) -> None:
    """Register the terrain tool. Called once by the plugin loader."""
    ctx.register_tool(
        name="pai_terrain_ops",
        toolset="pai",
        schema=terrain_ops.SCHEMA,
        handler=terrain_ops.handle,
        check_fn=terrain_ops.check_available,
        emoji="🗺️",
    )