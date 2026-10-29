# Source capability inventory and migration gates

Source reference: local `pai-stack` at commit `c86899da37ce05667bf5b9d3c295bacbc1ff997b`.
All source capabilities below are now ported into optional native profiles. Phobos also has direct Python-tool and skill bridges. The per-row pending notes describe remaining core unification, compatibility fixtures and data migration, not missing source files. See [PORTING.md](PORTING.md).

| Capability | Source surface | Phobos status / migration gate |
|---|---|---|
| Research vault | `list_notebooks`, `create_notebook`, `get_notebook`, `add_note`, `add_source_url`, `add_source_file`, `poll_source_status`, `get_source`, `search`, `ask_notebook` | The native notebook bridge is connected and notebook creation/isolation tests pass. Full action runtime parity and legacy notebook/wiki fixtures remain pending. Preserve `.project/research` Markdown, frontmatter and IDs. |
| Living ADRs | `create_adr`, `check_drift`, `list_adrs` | Pending. Preserve symbol anchors, hashes, ADR numbering and supersession metadata. |
| Terrain | `index`, `refresh`, `search`, `read`, `overview`, `projects`, `unregister`, `source`, `init`, `ask` | Pending. Keep one index writer, canonical per-repo serialization, path confinement and LLM gates. Regenerate host-local indexes rather than copying them. |
| OpenDesign | `health`, `list_projects`, `get_project`, `create_project`, `import_folder`, `get_status` | Pending optional integration; daemon state remains outside project bind mounts. |
| Git tools | Status, log, diff, branches, checkout, commit, push, pull, merge, rebase, stash, tags, remotes, config, worktrees | Pending. Separate read-only actions from owner-authorized mutations; verify against disposable repositories. |
| Browser tools | `search`, `fetch`, `browse`, `search_and_fetch` | Pending optional browser worker. Test extraction, browser cleanup, timeouts and network boundaries. |
| Model synchronization | Provider discovery, probes, latency ordering, gateway route generation, DSH model import | Pending. Provider keys stay gateway-side; preserve reasoning/tool-call pair-shape regression tests before any routing migration. |
| Hermes | Native tool registration, memory plugin, compression, theme and skill provisioning | Pending optional adapter. Do not rewrite or relabel upstream Hermes itself. |
| DSH | Native tools, sessions, model selection, scheduling, memory, Telegram session attachment | Pending optional adapter. Phobos tickets are not a drop-in replacement for DSH sessions. |
| Telegram | Original session controls and conversation routing | New ticket bot implemented. DSH `/attach`, `/workspace`, `/model`, `/compact`, `/unbind` compatibility is pending and is not advertised. |
| Memory | Mnemosyne and DSH mnemon | New explicit project preferences and feedback store implemented. Existing memory database import is pending; do not point Phobos at upstream databases. |
| Skills | Bundled procedural skills and upstream workflow suites | Not copied. Select useful packs, retain original licenses/provenance and verify dependencies before enabling. |
| Agent delegation | Keyless OpenCode subprocess workflows | Pending; optional upstream runtime remains distinct from Phobos's gateway-only assistant. |
| Creative tools | New design, motion, clipping, audio and Blender packs | New scope. Require real editable/exported artifacts before marking available. |

## Upgrade sequence

1. Keep the old installation available while Phobos's core is evaluated independently.
2. Capture representative, non-secret legacy notebook and ADR fixtures and establish expected actions/output.
3. Implement import into a separate project copy; verify IDs, metadata and source references.
4. Add native adapters and opt-in service profiles after core action parity passes.
5. Measure behavior and resource use before migrating production workflows.
6. Back up and restore-test application data before switching the active bot or gateway routing.

No automatic import, old-volume mutation or remote history rewrite is performed by the current implementation.
