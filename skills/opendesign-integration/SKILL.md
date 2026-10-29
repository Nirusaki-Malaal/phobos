---
name: opendesign-integration
description: "Bridge Hermes to OpenDesign for visual generation, mockups, and design exploration. Load when the user wants to generate UI designs, explore visual concepts, or create design artifacts via OpenDesign. Delegates all OpenDesign operations to pai_ops_design_ops."
---

# OpenDesign Integration

> Bridges Hermes to the OpenDesign daemon for visual generation tasks.
> **Primary Goal:** Let Hermes create and monitor OpenDesign projects without leaving the pai-stack workflow.

---

## When to Use OpenDesign

| Use OpenDesign | Don't Use OpenDesign |
|---|---|
| UI mockups and wireframes | Backend logic |
| Design exploration and variants | Database schema design |
| Visual asset generation | Code review |
| Frontend prototyping | API design |
| Design-to-code handoff | Infrastructure work |

---

## Workflow

### 1. Check Health

```
pai_ops_design_ops(action="health")
```

If unhealthy, OpenDesign is not running. Start it with `make design-up` (or `docker start open-design`) — note: open-design is in the `design` profile.

### 2. Create or Import Project

**Create new project:**
```
pai_ops_design_ops(action="create_project", name="my-feature")
```

**Import existing folder:**
```
pai_ops_design_ops(action="import_folder", path="my-project")
```

The `path` is relative to `/opt/data/workspace` (the shared mount).

### 3. Monitor Status

```
pai_ops_design_ops(action="get_status", project_id="<id>")
```

### 4. List Projects

```
pai_ops_design_ops(action="list_projects")
```

---

## Handoff Protocol

When Hermes needs OpenDesign to generate something:

1. **Hermes designs** — writes spec to `.project/planning/<slug>/design.md`
2. **Hermes triggers** — `pai_ops_design_ops(action="create_project", name="<slug>")`
3. **OpenDesign generates** — files land in `/opt/data/workspace/<project>`
4. **Hermes reviews** — code-reviewer checks the new files for quality
5. **Hermes iterates** — if changes needed, update spec and re-trigger

---

## Integration with Other Skills

| Skill | Role |
|---|---|
| `full-stack-workflow` | Orchestrates the overall design → implement → review flow |
| `system-design` | Produces the spec that OpenDesign consumes |
| `code-reviewer` | Reviews OpenDesign output |
| `ponytail` | Keeps OpenDesign prompts minimal |

---

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `OD_API_TOKEN` | (none) | Bearer token for OpenDesign API |
| `OD_HOST` | `http://open-design:7456` | Daemon URL |
| `OD_WORKSPACE` | `/opt/data/workspace` | Shared workspace path |

---

## Rules

- Always check `health` before operations
- Always use `list_projects` before `create_project` to avoid duplicates
- Project names use kebab-case: `my-feature-name`
- Imported paths are relative to `/opt/data/workspace`
- All operations are audited to `ops-design-ops.log`
- Never use OpenDesign for backend, DB, or infrastructure tasks
