---
name: opendesign-tool
description: "OpenDesign REST API client for dsh — creates, imports, and monitors OpenDesign projects. Exposes: health, list_projects, get_project, create_project, import_folder, get_status."
---

# OpenDesign Tool for dsh

A TypeScript REST client for the OpenDesign daemon, compatible with the DeepSeek Harness (dsh) agent.

## When to Use

Use this tool when you need to:
- Create new OpenDesign projects
- Import existing folders as projects
- Check project status and details
- List all projects
- Verify OpenDesign daemon health

## Workflow

### 1. Check Health

```
pai_ops_design_ops(action="health")
```

Returns `{"status": 0, "result": {"status": "ok"}}` if the daemon is running.

### 2. List Projects

```
pai_ops_design_ops(action="list_projects")
```

Returns all projects owned by the current token.

### 3. Create a New Project

```
pai_ops_design_ops(action="create_project", name="my-feature")
```

Creates a new project rooted at the shared workspace (`/opt/data/workspace`).

### 4. Import an Existing Folder

```
pai_ops_design_ops(action="import_folder", path="my-project")
```

Imports a folder relative to the workspace into OpenDesign.

### 5. Get Project Details

```
pai_ops_design_ops(action="get_project", project_id="<id>")
```

Returns project metadata.

### 6. Monitor Generation Status

```
pai_ops_design_ops(action="get_status", project_id="<id>")
```

Returns the generation status for a project.

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `OD_API_TOKEN` | (none) | Bearer token for OpenDesign API authentication |
| `OD_HOST` | `http://open-design:7456` | OpenDesign daemon host URL |
| `OD_WORKSPACE` | `/opt/data/workspace` | Shared workspace path (mounted by dsh and Hermes) |

## Rules

- Always check `health` before performing mutating operations
- Use `list_projects` before `create_project` to avoid duplicate names
- Project names should use kebab-case: `my-feature-name`
- Imported paths are relative to `/opt/data/workspace`
- All operations are logged; failures return structured error objects
- Never use OpenDesign for backend, DB, or infrastructure tasks