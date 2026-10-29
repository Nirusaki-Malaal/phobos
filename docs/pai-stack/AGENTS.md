# pai-stack — Agent Context

> Read this file first. It explains what this repo is, how the services interconnect,
> and how any AI agent (Hermes, or otherwise) should work within this environment.

## What this is

Two core Docker services that give an AI agent (Hermes) a complete local development environment:
read-write workspace access, persistent local memory, and native procedural skills —
no sidecar containers needed for tools.

## Services & Ports

| Service | Port | Role | Profile |
|---|---|---|---|
| **llm-gateway** | 4000 | Unified LLM Gateway: LiteLLM proxy (routes, fallbacks, provider credentials) | `gateway` |
| **hermes** | 9119 / 8642 | AI agent gateway + web dashboard (native Mnemosyne memory, native `pai_*` tools, native skills) | `hermes` |
| **dsh** | 9229 | DeepSeek Harness agent — second agent on the same workspace | `dsh` |
| **open-design** | 7456 | Design generation / mockup bridge | `design` |
| **terrain** | 7878 | Code intelligence — the single index owner (see below) | `terrain` |

All services are optional profiles. Run any combination:
```bash
make gateway-up hermes-up       # Gateway + Hermes
make gateway-up dsh-up          # Gateway + DSH
make all-up                     # Everything
```

## Container Mounts

| Host path | Container path | Service | Access |
|---|---|---|---|
| `$WORKSPACE_DIR` | `/opt/data/workspace` | hermes | **read-write** (project files + `.project/` vault) |
| `$WORKSPACE_DIR` | `/opt/data/workspace` | terrain | **read-write** (writes `.terrain/` into the repo it indexes) |
| `$WORKSPACE_DIR` | `/opt/data/workspace` | dsh | **read-write** (same container path as hermes — shared filepaths) |
| `$WORKSPACE_DIR` | `/opt/data/workspace` | open-design | **read-write** (import folder for design generation) |

Hermes and dsh both mount the workspace at `/opt/data/workspace`. Hermes writes helper scripts
and scratch tools to `/opt/data`.

Terrain's *project registry* deliberately does **not** live on that mount — it sits
in the `terrain_data` volume at `/var/lib/terrain/.terrain/registry.json`, because
it is host state, not repo content.

OpenDesign's daemon state stays in the `open_design_data` volume at `/app/.od` — never bind-mounted into the workspace to avoid SQLite lock contention.



## Service Interconnection

```
hermes ──→ [Mnemosyne: SQLite]  local persistent memory (working/episodic memory, knowledge graph)
hermes ──→ [pai_tools plugin]   native tools: pai_notebook_ops, pai_adr_ops, pai_ops_design_ops (Docker is native CLI via the `docker` skill, not a tool)
hermes ──→ [pai_terrain_ops]    code intelligence → terrain:7878/call  (HTTP)
dsh    ──→ [dsh-mcp-client]     code intelligence → terrain:7878/mcp   (MCP)
hermes ←─→ [pair/ blackboard] ←─→ dsh   peer queue: pair/queue → claim.py → worktree → done.py; wake via poke.py (docker exec headless)
hermes ──→ [skills: /opt/pai/skills]  native procedural skills via skills_list / skill_view
hermes ──→ llm-gateway:4000     ONLY gateway for LLM completions & reasoning
hermes (pai_notebook_ops) ──→ <EXECUTION_DIR>/.project/research/ (file vault)
hermes (ask_notebook) ──→ llm-gateway:4000 (synthesis)
dsh    ──→ llm-gateway:4000     LLM completions & reasoning
open-design ──→ llm-gateway:4000  LLM completions (BYOK or gateway)
terrain ──→ <repo>/.terrain/    the ONLY writer of index artifacts
terrain ──→ opencode acp (ACP)  only when TERRAIN_ALLOW_LLM=1 (ask/init)
         └─ NOT llm-gateway. Terrain holds no gateway link and no provider key:
            its ACP agent runs OpenCode's keyless Zen models. Do NOT re-add
            OPENAI_API_KEY — it makes opencode pin the ACP session to a catalog
            OpenAI model and both ask/init die. See docs/terrain.md.
```

All model inference routes through `llm-gateway:4000` (LiteLLM), except the two
keyless-Zen cases noted under *LLM routing posture* below.

**Each service runs independently via its profile.** No service is required to run.
Start only what you need: `make gateway-up hermes-up`, `make gateway-up dsh-up`, etc.
`pai_notebook_ops` runs in-process in Hermes, operating directly on Markdown files in the workspace.

### LLM routing posture (gateway-only)
- Hermes declares exactly one provider (`gateway`); model selection, fallbacks, and
  provider credentials live in `llm-gateway/config.yaml` (+ `scripts/sync-models.py`
  templates). Never add direct provider keys, auxiliary providers, or Hermes-level
  fallbacks — Models.dev catalog reads are metadata-only and fine.
- Known exceptions: `opencode-delegate` calls Zen free models directly (keyless,
  documented in its skill); Terrain's ACP agent (`opencode acp`) does the same for
  `ask`/`init`, which is why terrain carries no gateway link at all; Langfuse
  receives traces (it IS the audit trail).
- Secrets caveat: provider keys are absent from the hermes container *environment*,
  but `.env` lives inside the mounted workspace — file-capable tools can still read
  it. `*.env` reads are hard-denied for delegates; treat Hermes-side reads as auditable.
- Gateway image is digest-pinned (see `docker-compose.yaml`); routes use mistral/
  provider mapping with per-model `drop_params` so reasoning params are stripped
  pre-flight (our LiteLLM synthesizes a conflicting nested object otherwise — Sep-25
  incident). Verify any routing change with a pair-shape probe before committing.
Mnemosyne runs embedded inside Hermes using local ONNX fastembed and SQLite (`hermes-data` volume).

## How Hermes Uses Each Service

### Code Intelligence — Terrain (opt-in profile)

Code intelligence is provided by [Terrain](https://github.com/sopaco/terrain), run as
a **single container that owns the index**. It replaces the removed `pai_code_intel`,
which built an in-process tree-sitter index over the whole workspace (42% of which was
a leaked Go toolchain, and far more than DSH's 1.2 GB heap could hold).

```
agent ──→ terrain:7878 ──→ terrain CLI ──→ <repo>/.terrain/
                            (one writer)      agent context, knowledge docs, source pack
```

- **Single index owner.** Only the `terrain` container writes `.terrain/`. Agents
  trigger jobs and read the result — neither keeps a private in-memory index, so the
  DSH heap cap is a non-issue.
- **Per-repo by construction.** Each scan writes `.terrain/` *inside* the repo it
  indexes, which structurally enforces the `EXECUTION_DIR` boundary below.
- **Indexing is free.** `index`/`refresh` make no LLM call. `refresh` explicitly skips
  Litho, Terrain's LLM doc generator. Only `ask` and `init` invoke an LLM — via
  Terrain's own ACP agent on keyless Zen models, not the gateway — and the service
  refuses them unless `TERRAIN_ALLOW_LLM=1`.
- **Degrades cleanly.** Terrain is optional. `pai_terrain_ops` probes `/healthz` and
  hides itself when the service is down; without it, locate code with `grep` + targeted
  reads. Nothing breaks.
- **`.terrain/` is gitignored.** The index is per-host, not per-repo-lifetime.

Start it with `make terrain-up`; see [`docs/terrain.md`](docs/terrain.md).

### Skills — procedural knowledge (native Hermes skills)

Hermes loads skill `<name>` via `skill_view(name="<name>")` before working on any unknown stack or process (or lists them via `skills_list()`).
Skills are markdown files in `./skills/`, mounted read-only into Hermes via `skills.external_dirs` — zero model tokens to load.

| Skill | Purpose |
|---|---|
| `stack-discovery` | Detect stack markers before any codebase work |
| `python` | Python discovery router (uv-run) → `senior-backend` for authoring |
| `docker` | Docker discovery router → `docker-development` (validators advisory-only) |
| `react` | React discovery router → `vercel-react-best-practices` et al. |
| `nodejs` | Node.js discovery → `senior-backend` for backend authoring |
| `sql` | DB discovery router → `supabase-postgres-best-practices` for depth |
| `planning` | Planning discipline: paths, 2-op rule, 3-strike, plus plan authorship + execution with verification gates (self-contained) |
| `system-design` | System design methodology, capacity planning, and trade-off matrices |
| `gitops` | Git safety + mechanics: topology, `.worktrees/` sandboxing, confirmation gate, test-first merge (self-contained) |
| `vercel-react-best-practices` / `vercel-composition-patterns` / `frontend-design` | Vendored React/perf/design depth (MIT/MIT/Apache-2.0) |
| `supabase-postgres-best-practices` / `senior-backend` / `docker-development` | Vendored Postgres/backend/Docker depth + scripts (MIT) |
| `web-design-guidelines` / `webapp-testing` / `mcp-builder` | Vendored UI-audit / browser-testing / MCP scaffolding (MIT/Apache-2.0) |
| `code-reviewer` / `skill-security-auditor` | Vendored review rubrics + skill supply-chain gate (MIT) |
| `gstack` + 25 `gstack-*` | Vendored gstack workflow suite: router, office-hours/spec/autoplan, plan reviews (ceo/eng/design/devex), review/investigate/cso/health, ship/land-and-deploy/document-*, learn/retro/context-*, careful/freeze/guard (MIT; provenance in `skills/gstack/VENDORED.md`) |
| `mermaid` | Mermaid diagram authoring guide (type selection, syntax safety, C4 abstraction protocol) |
| `research` | Per-project wiki (SCHEMA/index/log, ingest/query/lint) in `.project/research/` — load for wiki/kb/notes tasks |
| `opencode-delegate` | Keyless OpenCode delegation (background+poll, branch review) — load for implementation handoffs |
| `agents` | Ground rules injected at session start |

#### Tools (native `pai` toolset, `pai_tools` plugin)

| Tool | Purpose | Allowed actions |
|---|---|---|
| `pai_notebook_ops` | Query and manage per-project Research Brain (file vault in `.project/research/`) | `list_notebooks`, `create_notebook`, `search`, `add_note`, `add_source_url`, `poll_source_status`, `get_source`, `add_source_file`, `ask_notebook`, `get_notebook` |
| `pai_adr_ops` | Living ADR creation & code symbol drift detection | `create_adr`, `check_drift`, `list_adrs` |
| `pai_ops_design_ops` | OpenDesign visual generation / mockups bridge | (see `opendesign-integration` skill) |
| `pai_terrain_ops` | Code intelligence via the Terrain service (opt-in; hidden when down) | `index`, `refresh`, `search`, `read`, `overview`, `projects`, `unregister`, `source`, `init`, `ask` |
| `skill_view` | Load a procedural skill by name | `name="<skill>"` |
| `skills_list` | Discover all available native skills | (none) |

Docker is native CLI over the mounted socket (see `docker` skill) — no tool, no audit log.

### Mnemosyne — agent memory (decisions, execution outcomes, lessons learned)

Mnemosyne is Hermes's native, local-first agent memory layer. It operates entirely within Hermes
using embedded SQLite (`/opt/hermes/data/mnemosyne/data/mnemosyne.db`) and local ONNX vector embeddings (`fastembed`).

- **Automatic capture**: every turn (decisions, tool calls, user preferences, failure modes) is automatically indexed.
- **Dynamic prompt prefetch**: relevant working memories are fetched and injected before model turns.
- **Recall tools**: `mnemosyne_recall` (hybrid semantic + FTS5 + recency search), `mnemosyne_remember` (explicit fact recording), `mnemosyne_sleep` (episodic consolidation), `mnemosyne_stats`.
- **Knowledge graph**: `mnemosyne_triple_add`, `mnemosyne_triple_query` for relationship traversal.

| Retrieval System | Scope | Storage | Role |
|---|---|---|---|
| **Research Brain** | External knowledge & notes | `<EXECUTION_DIR>/.project/research/` | RFCs, API docs, papers, research notes in Markdown |
| **Mnemosyne** | Agent experience | `/opt/hermes/data/mnemosyne` | Decisions, prior fixes, session continuity, user preferences |

## Startup Protocol: Workspace Understanding & Boundaries

> **CRITICAL INVARIANT**: The host workspace (`$WORKSPACE_DIR` mounted to `/opt/data/workspace`)
> is often a multi-project container holding several independent repositories or projects side-by-side.
> Hermes must never confuse one project with another or treat `/opt/data/workspace` as a monolithic project
> **unless the workspace root itself is confirmed to be a single isolated repository**.

On **Turn 1 of every session**:
1. **Discover skills**: Call `skills_list()` to confirm `research` and other skills are available.
2. **Recall Known Boundaries + services (parallel)**: Call `mnemosyne_recall(query="workspace projects structure boundaries")` and run `docker ps` with the pai-stack label filter (see `docker` skill).
3. **Survey Directory Structure**: List `/opt/data/workspace` (depth 1) to identify project subdirectories and identify root markers (`.git/`, `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `Makefile`).
4. **Determine Execution Mode**:
   - **Single-project mode**: If `/opt/data/workspace` itself contains root markers (`.git/`, manifest files) and NO subdirectories with their own root markers → `EXECUTION_DIR = /opt/data/workspace`. Treat workspace as the project.
   - **Multi-project mode**: If subdirectories each contain root markers → pick one subdirectory as `EXECUTION_DIR = /opt/data/workspace/<project>`. Announce the choice.
5. **Orient on the stack**: If the stack is unknown, load the `stack-discovery` skill first; otherwise read the project's manifest (`pyproject.toml`, `package.json`, …).
6. **Orient on wiki (IFF research task OR `<EXECUTION_DIR>/.project/research/SCHEMA.md` exists)**: Load `skill_view(name="research")`, then read `.project/research/{SCHEMA.md,index.md,log.md}` tail-20 only (index-first, top-3 pages max).
7. **Persist Boundaries**: Record discovered project boundaries using `mnemosyne_remember(content="Workspace Project: '<name>' at /opt/data/workspace/<name>...")`.
8. **Enforce Boundary Isolation**: Strictly avoid cross-project contamination of files, git branches, or planning files.
9. **Check the index (if terrain is running)**: `pai_terrain_ops(action="projects")`. If `EXECUTION_DIR` is absent from the list, `pai_terrain_ops(action="index", path="<project>")` before code work — it costs no LLM tokens.

## Execution Directory Invariant

Hermes must strictly isolate its operations to a single project execution directory:
1. **Declare `EXECUTION_DIR` on Turn 1**: Hermes explicitly chooses `EXECUTION_DIR = /opt/data/workspace/<project>` (or `/opt/data/workspace` only if the workspace root is confirmed to be a single repo). Announce this to the user.
2. **Strict Persistence Throughout Session**:
   - **Terminal Commands**: ALWAYS run within or prefix with `cd <EXECUTION_DIR> && <cmd>`. Never run build/test/git commands in `/opt/data/workspace` root.
   - **Planning Artifacts**: Write exclusively to `<EXECUTION_DIR>/.project/planning/<YYYY-MM-DD-slug>/`.
   - **Code Changes**: Anchor all edits inside `<EXECUTION_DIR>`.

## Where Hermes Writes

| What | Where | When |
|---|---|---|
| Code changes | `<EXECUTION_DIR>/<file>` | After user confirms the proposed diff |
| Planning files | `<EXECUTION_DIR>/.project/planning/<slug>/` | When starting a complex task |
| Helper scripts & scratch tools | `/opt/data/<tool_name>` | For internal Hermes operations |
| Agent memory (Mnemosyne DB) | `hermes-data` volume (`/opt/hermes/data/mnemosyne/data/`) | Post-turn capture & explicit remember calls |
| App state (DB, sessions) | `hermes-data` Docker volume | Automatically — never touches workspace |

Planning files (`task_plan.md`, `findings.md`, `progress.md`) are developer artifacts.
They live in the project and are searchable with `grep` in future sessions.

## Planning Discipline (planning-with-files)

For any task with 3+ steps, research, or multi-file changes, Hermes creates:

```
<EXECUTION_DIR>/.project/planning/<YYYY-MM-DD-slug>/
  task_plan.md   — phases, current status, decisions, next step
  findings.md    — discoveries: symbols, blast radius, root causes, key facts
  progress.md    — session log: what ran, errors, what was written
```

Key rules:
- **2-operation rule**: after every 2 search/read operations → write findings to `findings.md`
- **Propose before writing**: show diff, wait for user confirmation, then write
- **3-strike protocol**: attempt 1 (diagnose) → attempt 2 (different approach) → attempt 3 (rethink) → escalate

See the `planning` skill for the full discipline.

## Key Config Files

| File | Purpose |
|---|---|
| [`hermes/config.yaml`](hermes/config.yaml) | System prompt, model providers, skills, knowledgebase |
| [`docker-compose.yaml`](docker-compose.yaml) | All service definitions, mounts, resource limits (profiles: gateway, hermes, terrain, dsh, design) |
| [`hermes/plugins/pai_tools/`](hermes/plugins/pai_tools/) | Native Hermes tools: `pai_notebook_ops`, `pai_adr_ops`, `pai_ops_design_ops` |
| [`hermes/plugins/pai_terrain_ops/`](hermes/plugins/pai_terrain_ops/) | Code-intel tool: `pai_terrain_ops` → terrain service |
| [`terrain/`](terrain/) | Terrain multi-stage image + HTTP/MCP shim |
| [`skills/`](skills/) | Bundled SKILL.md files (one per skill, mounted into Hermes) |
| [`skills/agents/SKILL.md`](skills/agents/SKILL.md) | Ground rules injected into Hermes context |
| [`.env.example`](.env.example) | Template for `WORKSPACE_DIR`, LLM config, API keys |

## Quick Start for Agents

1. Read this file (`AGENTS.md` at repo root)
2. Read [`skills/agents/SKILL.md`](skills/agents/SKILL.md) for operational ground rules
3. Read [`hermes/config.yaml`](hermes/config.yaml) for the full system prompt
4. Check [`docker-compose.yaml`](docker-compose.yaml) for current mount paths and port bindings
5. For code questions: prefer `pai_terrain_ops(action="search"|"source")` if the repo is indexed; otherwise `grep`, then read only the files that match.


## System Architecture Maintenance Protocol

**CRITICAL RULE:** This project relies on a living, highly accurate Mermaid system design diagram located in `docs/architecture.md` to onboard users and track data flow. 

Whenever you modify the project's structural architecture (e.g., adding a new module, database table, API route, service, or altering core data flows), you **MUST** update the Mermaid diagram in `docs/architecture.md` before completing the task. Always load `skill_view(name="mermaid")` first for syntax rules and abstraction depth protocol.

### Diagram Requirements:
1. **Format:** Use a Mermaid `graph TD` or `graph LR` flowchart. 
2. **Abstraction Level:** Keep it high-level. Map components, services, databases, and core interactions. Do not map individual functions, classes, or files unless they represent an entire service.
3. **Interactive Linking:** You must use Mermaid's `click` syntax to make nodes clickable. Link every major node directly to its corresponding detailed documentation file or core source code directory so users can drill down.
4. **Validation:** Ensure the Mermaid syntax is strictly valid and uses proper escaping for special characters.
