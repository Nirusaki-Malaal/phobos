# Complete pai-stack source port

Source: `pai-stack` commit `c86899da37ce05667bf5b9d3c295bacbc1ff997b`.

All 480 tracked source paths are accounted for: 479 files were copied to their corresponding location or a documentation/license mapping; original `.env.example` settings were merged into Phobos's example. Source Git history, secrets, caches, indexes and runtime volumes were excluded.

| Location | Included capabilities |
|---|---|
| `hermes/` | Image, configuration, provisioning, notebook/ADR/design/Terrain tools, compressor, themes, skins and OpenCode delegation |
| `dsh/` | Image, entrypoint, settings; notebook, ADR, design, Terrain, Git, browser and Telegram plugins; memory/automation provisioning |
| `terrain/` | CLI image, HTTP/MCP bridge, per-repo serialization and keyless ACP configuration |
| `opendesign/` | Design-service image and daemon integration |
| `llm-gateway/` | LiteLLM routes, fallbacks and parameter handling |
| `skills/` | All 54 SKILL.md bundles, supporting scripts, references, licenses and provenance |
| `scripts/` | Model discovery/probing/sync, DSH import and version/gate checks |
| `Makefile.agents` | Service lifecycle, volume permissions, diagnostics and model commands |
| `docs/pai-stack/` | Original README, agent instructions, service docs and archives |
| `licenses/pai-stack.LICENSE` | Original MIT notice |

`compose.yaml` includes `compose.agents.yaml`. Existing profiles remain `gateway`, `hermes`, `dsh`, `terrain`, `design`; Phobos adds its own `telegram` profile. Service DNS names and original workspace paths remain compatible. Fixed global container names were removed in favor of Compose project names. Host ports retain their original values, so avoid running another installation on those ports.

## Start profiles

```sh
make agents-help
make gateway-up hermes-up
make gateway-up dsh-up
make terrain-up
make design-up
```

Configure profile settings from `.env.example` first. Core and native services share `WORKSPACE_DIR` (falling back to `PHOBOS_WORKSPACE_DIR`). Native-agent images are much heavier than the core; the complete stack is not claimed to fit every Raspberry Pi. Use separate bot tokens for Phobos and native-agent pollers.

Hermes and DSH load all skills read-only. Phobos can also list/read every bundle and supporting file. Portable notebook/ADR tools run with fresh per-project environments. Design and Terrain calls need their services. Git, browser, shell, DSH session controls and native memory/automation remain upstream-agent features rather than newly rewritten Phobos core tools.

## Verification

Checks include Phobos tests, compressor tests, DSH Telegram tests, version/gate agreement, all-profile Compose validation and syntax validation. Bridge tests exercise real notebook/ADR writes and project isolation.

Core image build/start checks are separate from heavyweight Hermes/DSH/Terrain/OpenDesign image builds, live providers, native sessions and ARM hardware validation. Source import does not mean every external integration was exercised.

Existing file formats remain supported by the imported tools. User data and upstream memory volumes are not automatically copied or overwritten. See [MIGRATION.md](MIGRATION.md) for action-level targets and remaining unification work.
