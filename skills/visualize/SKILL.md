---
name: visualize
description: "Maintain a living diagram registry in .project/diagrams/ — diagrams that evolve with research, planning, and code. Create, update, version, and sync diagrams. Use for architecture, data flows, state machines, ERDs, sequence diagrams, and geometric visuals. Diagrams are source-controlled, linkable from docs/code, and verifiable against implementation."
---

# Visualize — Living Diagram Registry

This skill manages a **persistent, evolving diagram collection** at `<EXECUTION_DIR>/.project/diagrams/`. Diagrams are not throwaway — they accumulate, version, link to research/planning/code, and can drive synchronization checks.

You are the **diagram custodian**. You create, update, version, and verify diagrams. The registry persists across sessions and agents.

## When to use

- **Architecture/system diagrams** — component topology, data flows, deployment
- **Code structure** — module dependencies, class hierarchies, call graphs
- **Domain models** — ERDs, state machines, aggregate boundaries
- **Sequences/flows** — API call chains, auth flows, event processing
- **Geometric/spatial** — coordinate layouts, network topologies, capacity planes
- **Any diagram that should stay in sync with code/docs**

Do NOT use for one-off explanation visuals that don't need persistence (use a quick Mermaid code block instead).

## Registry structure

```
.project/diagrams/
├── index.json              # Registry: all diagrams, versions, links
├── index.md                # Human-readable registry (auto-generated)
├── architecture/
│   ├── system-topology.mmd
│   ├── system-topology.png
│   ├── data-flow.mmd
│   └── data-flow.png
├── domain/
│   ├── user-aggregate.mmd
│   ├── user-aggregate.png
│   └── session-state.mmd
├── sequences/
│   ├── auth-flow.mmd
│   └── event-processing.mmd
├── geometry/
│   └── capacity-plane.svg
├── planning/
│   └── task-dependencies.mmd
└── research/
    └── concept-map.mmd
```

### `index.json` schema

```json
{
  "version": 1,
  "diagrams": [
    {
      "id": "system-topology",
      "title": "System Topology",
      "category": "architecture",
      "format": "mermaid",
      "source": "architecture/system-topology.mmd",
      "rendered": "architecture/system-topology.png",
      "currentVersion": 3,
      "versions": [
        {"v": 1, "date": "2026-01-15", "author": "hermes", "change": "Initial"},
        {"v": 2, "date": "2026-02-03", "author": "hermes", "change": "Added terrain service"},
        {"v": 3, "date": "2026-03-10", "author": "dsh", "change": "Added open-design bridge"}
      ],
      "links": {
        "research": ["research/architecture-overview.md"],
        "planning": ["planning/2026-03-01-scaling/architecture.md"],
        "code": ["docker-compose.yaml", "hermes/config.yaml"]
      },
      "syncChecks": [
        {"type": "service-presence", "targets": ["llm-gateway", "hermes", "terrain", "dsh", "open-design"]},
        {"type": "port-mapping", "targets": {"llm-gateway": 4000, "hermes": 9119, "terrain": 7878}}
      ]
    }
  ]
}
```

## Core operations

### 1. Create diagram

```
You: "Create architecture diagram for the LLM gateway routing"
Skill: 
  1. Determine category (architecture/sequences/domain/geometry/planning/research)
  2. Choose format: Mermaid (relationships) or SVG (geometry)
  3. Write source to .project/diagrams/<category>/<id>.mmd|.svg
  4. Render to PNG (Mermaid) or verify SVG
  5. Add entry to index.json with v1, links to research/planning/code
  6. Regenerate index.md
  7. Return embed: ![[architecture/system-topology.png|600]]
```

### 2. Update diagram (versioned)

```
You: "Update system-topology to add open-design service"
Skill:
  1. Load index.json, find diagram by id
  2. Read current source, modify
  3. Write new source (overwrite)
  4. Re-render PNG
  5. Increment version in index.json, append to versions[]
  7. Update links if needed
  8. Regenerate index.md
  9. Return embed with new version
```

### 3. Sync check (verify diagram matches reality)

```
You: "Verify system-topology matches current docker-compose"
Skill:
  1. Load diagram's syncChecks from index.json
  2. For each check:
     - service-presence: grep docker-compose.yaml for service names
     - port-mapping: verify ports match
     - code-structure: grep/terrain search for modules
  3. Report: PASS / FAIL (with specifics)
  4. Optionally: auto-update diagram if drift detected (with confirmation)
```

### 4. Link diagram to research/planning/code

```
You: "Link system-topology to the scaling planning doc"
Skill:
  1. Add planning entry to diagram's links.planning[]
  2. Add reverse link in planning doc (manual or via planning skill)
  3. Regenerate index.md
```

### 5. List / search diagrams

```
You: "Show all architecture diagrams"
Skill: Query index.json, return formatted list with versions, last modified, links
```

## Mermaid workflow (persistent)

1. **Source lives at** `.project/diagrams/<category>/<id>.mmd`
2. **Render** with `mmdc`:
   ```bash
   mmdc -i .project/diagrams/architecture/system-topology.mmd \
        -o .project/diagrams/architecture/system-topology.png \
        -b transparent -w 1200
   ```
3. **Verify** — open PNG. Wrong? Edit `.mmd`, re-render.
4. **Embed** in any response/doc:
   ```
   ![[architecture/system-topology.png|600]]
   ```
   Obsidian resolves from vault root (workspace mount).

## SVG workflow (persistent)

1. **Source lives at** `.project/diagrams/<category>/<id>.svg`
2. **Verify** — open in browser/viewer. Iterate until correct.
3. **Embed**:
   ```
   ![[geometry/capacity-plane.svg|600]]
   ```

## Integration with other skills

| Skill | Interaction |
|-------|-------------|
| `teach` | Phase 3 Connect step: create/update diagram in registry, embed |
| `research` | `pai_notebook_ops` notes can reference diagram IDs; diagrams can link to research notes |
| `planning` | Planning docs reference diagrams; diagrams link to planning phases |
| `pai_terrain_ops` | Sync checks can query terrain for code structure (modules, deps) |
| `pai_adr_ops` | ADRs can embed diagrams; diagrams can link to ADRs |
| `gitops` | Diagram changes committed with code; `index.json` in git |

## Sync check types (extensible)

| Type | Purpose | Example |
|------|---------|---------|
| `service-presence` | All listed services exist in compose | `targets: ["hermes", "terrain"]` |
| `port-mapping` | Ports match compose/config | `targets: {"hermes": 9119}` |
| `code-structure` | Modules/packages match terrain index | `targets: ["hermes/plugins/pai_tools"]` |
| `adr-alignment` | Diagram matches ADR decisions | `targets: ["adr-003-gateway-routing"]` |
| `custom` | Arbitrary verification script | `script: "verify-topology.sh"` |

## Quick reference

| Operation | Command pattern |
|-----------|-----------------|
| Create | `visualize create --id <id> --category <cat> --format mermaid|svg --title "..." --brief "..."` |
| Update | `visualize update --id <id> --change "..."` |
| Sync check | `visualize sync --id <id>` |
| List | `visualize list [--category <cat>]` |
| Show | `visualize show --id <id>` |
| Link | `visualize link --id <id> --research|--planning|--code <path>` |

## Embedding in responses

Always embed using the **relative path from vault root** (workspace mount):

```
![[architecture/system-topology.png|600]]
![[sequences/auth-flow.png|500]]
![[geometry/capacity-plane.svg|700]]
```

## Why stateful?

- **Diagrams as documentation** — they *are* the architecture docs, not illustrations
- **Drift detection** — sync checks catch code/docs divergence automatically
- **Knowledge accumulation** — each version captures a decision point
- **Cross-reference** — research → planning → code → diagram → research (full loop)
- **Agent continuity** — Hermes, DSH, future agents all see the same living diagrams

Load this skill when working with architecture, planning, research, or any diagram that should persist and evolve.