# Terrain — Code Intelligence Service

> Optional profile. Start with `make terrain-up`. Reference card for
> [`AGENTS.md`](../AGENTS.md); architecture context in
> [`architecture.md`](architecture.md); process-level view in
> [`architecture.md` → Process model](architecture.md#process-model).

Terrain ([sopaco/terrain](https://github.com/sopaco/terrain), MIT) scans a project
repo into `.terrain/` — Markdown knowledge docs, an agent context layer, and a
repomix source pack. It replaces `pai_code_intel`, removed 2026-10-04.

## Why it is a separate container

The tool it replaces built a tree-sitter index **in every agent's heap**. Two
measurements ended that:

- the workspace-wide surface was **27,291 files**, of which **11,505 (42%)** were a
  Go toolchain that had leaked into the mount via `HOME=/opt/data/workspace`;
- DSH's heap cap is **1,216 MB** (`NODE_OPTIONS=--max-old-space-size=1024`).

Neither agent can afford a resident index, and duplicating one per agent is exactly
what we did not want. Terrain inverts it: **one container owns the index**, agents
trigger jobs and read the result.

```
hermes ──HTTP POST /call──┐
                         ├──→ terrain:7878 ──→ <repo>/.terrain/
dsh    ──MCP JSON-RPC────┘      (one writer)
```

That inversion is a process decision, not a packaging one — the shim is stateless
and the `terrain` binary runs as a fresh process per call, so no agent heap ever
grows. Full view: [`architecture.md` → Process model](architecture.md#process-model).

## Why it is built from source

Terrain publishes prebuilt binaries for `darwin-arm64` and `win32-x64` only.
`@terrain-ai/cli-linux-x64` is **not on npm**, and pai-stack is Linux-only. So
[`terrain/Dockerfile`](../terrain/Dockerfile) builds it in two stages:

| Stage | Base | Contents |
|---|---|---|
| build | `rust:1.94-slim` (Debian 13 trixie, glibc 2.41) | `cargo build --release -p terrain-cli` |
| runtime | `debian:trixie-slim` (Debian 13 trixie, glibc 2.41) | the `terrain` binary + `server.mjs` + `nodejs` |

The two bases **must be the same Debian major**. This was not hypothetical: the
runtime was originally `bookworm-slim` (glibc 2.36) while `rust:1.94-slim` is
trixie (2.41), so the freshly built binary failed at the build's `--version`
gate with `version 'GLIBC_2.38' not found`. `rust:*-slim` tracks current Debian
stable, so re-check both when bumping the toolchain tag rather than assuming the
tag implies a distro.

The runtime also needs `nodejs`: the ENTRYPOINT is `node /opt/terrain/server.mjs`
even though the terrain binary itself is Rust. The shim uses plain JSON-RPC with
no npm dependencies, so the distro package suffices — no install stage, no
version to drift.

`src-tauri` is a cargo workspace member, but `terrain-cli` does not depend on it, so
the webkit/GTK chain never enters the image. `rust-toolchain.toml` pins stable;
edition 2024 means **MSRV 1.94**.

**The first build is slow** (LTO + `codegen-units=1`). Later builds reuse the layer cache.

## Why there is a shim

The Terrain CLI has **no `serve` or daemon subcommand** — it is a CLI only.
Both agents reach it over the network instead of `docker exec`: DSH has no
in-process access to the binary, and Hermes reaches it through
`pai_terrain_ops` rather than the socket. Without a network endpoint neither
would get anything.

[`terrain/server.mjs`](../terrain/server.mjs) fronts the binary on one port with two
protocols:

| Endpoint | Consumer | Protocol |
|---|---|---|
| `GET /healthz` | compose healthcheck, `check_fn` probe | plain HTTP |
| `POST /call` | Hermes (`pai_terrain_ops`) | JSON |
| `POST /mcp` | DSH (`dsh-mcp-client`) | MCP JSON-RPC 2.0 |

MCP is hand-rolled JSON-RPC rather than an SDK dependency, so the image needs no npm
install stage and there is no SDK version to drift.

## Two safety invariants

**1. A closed action allowlist.** `terrain env apply` rewrites `AGENTS.md` and installs
preset skills from `.agents/skills` — it would overwrite files pai-stack owns. So the
shim exposes only:

```
index  refresh  search  read  overview  projects  unregister  source  init  ask
```

`env`, `sdd`, `settings`, `usage` and the raw `tools` surface are unreachable — an
unknown action is rejected by name, so they cannot even be invoked.

**2. Path confinement.** `index`/`refresh`/`init` targets must resolve inside
`/opt/data/workspace`; anything else returns `403` before the Terrain binary runs.
Without this, any agent holding the tool could point Terrain at the container image.

## Token cost

| Action | LLM? | Cost |
|---|---|---|
| `index`, `refresh`, `search`, `read`, `source`, `overview`, `projects`, `unregister` | no | free |
| `init` | yes (Litho doc generation) | keyless Zen — no tokens |
| `ask` | yes (DeepWiki Q&A) | keyless Zen — no tokens |

`refresh` explicitly **skips Litho**, Terrain's LLM doc generator, so incremental
re-indexing is free. LLM actions are refused with `403` unless `TERRAIN_ALLOW_LLM=1`,
and they do **not** use `llm-gateway` — terrain spawns an ACP agent that runs on
OpenCode's keyless Zen models, so no provider key exists in this container.

### `init` and `ask` need an ACP agent — and it must NOT see `OPENAI_API_KEY`

`TERRAIN_ALLOW_LLM=1` is necessary but **not sufficient**. Terrain does not write
its knowledge assets through an OpenAI-compatible endpoint — it **spawns an ACP
agent subprocess** (`settings check-acp` → `spawn_command: "opencode acp"`). The
image ships opencode 2.0.16 in `/opt/opencode` with a keyless Zen config seeded
onto the volume at boot.

**`init` and `ask` work only while `OPENAI_API_KEY` is unset in this container.**
That is counter-intuitive, because the key looks harmless — but it is the single
thing that breaks them:

- With `OPENAI_API_KEY=not-needed` exported, opencode reports the OpenAI provider
  as authenticated (`auth list` → `OpenAI OPENAI_API_KEY environment`).
- `opencode acp` then **ignores `opencode.json`'s model** and pins the session to
  a catalog OpenAI model (`openai/gpt-6.1-sol`). Driving the ACP protocol by hand
  shows the model list is `openai/*` only — Zen and custom providers are not
  offered at all.
- That model is then called against a backend that cannot serve it, and the
  session dies at first prompt with:
  ```
  Error: Litho agent failed: ACP protocol error: Authentication required:
         provider authentication required
  ```
- With the key removed, the `openai` provider is no longer "available", opencode
  falls back to the configured Zen model, and both actions succeed.

So the fix is **subtractive**: delete `OPENAI_BASE_URL` and `OPENAI_API_KEY` from the `terrain` service in `docker-compose.yaml` (there is no `depends_on` on `llm-gateway` either). Terrain then has no link to the gateway at all, and its LLM work runs on OpenCode's keyless Zen models — the same exception `opencode-delegate` already makes in hermes.

Verified working end to end after the change: `init` → 13 human docs +
agent context, `litho_ran: true`, status `partial` → `ready` (~6 min);
`ask` → answered with real file:line citations (~49 s).

Two related traps:

- **The seed never overwrites.** `server.mjs` copies `/opt/terrain/opencode.json`
  to the volume only when absent, so an edited config survives restarts — and a
  *stale* one also survives a config change. Delete
  `/var/lib/terrain/.config/opencode/opencode.json` and restart after editing.
- **`terrain settings set <file.json>` replaces the whole file with no
  validation.** A partial JSON silently drops `base_url`. Always write the full
  `provider`/`model`/`api_key`/`base_url` block.
- **`check-llm` does not validate the model name** — it reported `ready: true`
  for a model the gateway did not serve. Do not trust it.

## Usage

```bash
make terrain-up                      # build + start (opt-in)
make terrain-index d=/opt/data/workspace/github.com/ayedaemon/pai-stack
make terrain-build                   # rebuild after bumping TERRAIN_REF
make terrain-config                  # validate compose config with terrain profile
make terrain-down                    # stop; workspace and volume untouched
make terrain-ask q="where is auth enforced?"   # needs TERRAIN_ALLOW_LLM=1
```

From inside Hermes, via `pai_terrain_ops`:

```jsonc
{"action": "projects"}                                        // what is indexed?
{"action": "index",  "path": "github.com/ayedaemon/pai-stack"} // index (free)
{"action": "search", "query": "workspace mount", "project": "pai-stack"}
{"action": "source", "file": "hermes/plugins/pai_tools/adr_ops.py"}
```

## From DSH

DSH already ships `@deepseek-ai/dsh-mcp-client` plus
`@modelcontextprotocol/{client,core}` v2.0.0, so no DSH plugin work is needed —
register the shim as an MCP server:

```jsonc
{ "mcpServers": { "terrain": { "url": "http://terrain:7878/mcp" } } }
```

Tools appear as `pai_terrain_index`, `pai_terrain_refresh`, `pai_terrain_search`,
and so on — the `pai_` namespace holds even though MCP fans one tool out per
action ([naming.md](naming.md) rule 1). The legacy `terrain_` spelling is
still accepted on the wire.

> **Unverified:** the exact transport DSH's MCP client expects (stdio vs SSE vs
> Streamable HTTP) has not been confirmed against a running DSH. The shim speaks
> Streamable HTTP. If DSH rejects it, the fallback is a `dsh-pai-terrain` TypeScript
> plugin per `docs/dsh.md` Phase 2 — Hermes is unaffected either way.

## `.terrain/` is gitignored

The index is **per-host, not per-repo-lifetime**. Indexing once benefits every agent
on this host — Hermes and DSH share both the workspace mount and the service — but it
does not travel to teammates through git.

To share it instead, commit only the small agent-context file and keep the bulk
regenerated locally:

```gitignore
.terrain/agent/repomix.md
.terrain/agent/codegraph*
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `TERRAIN_REF` | `main` | git ref to build (pin deliberately — `.terrain/` format may change) |
| `TERRAIN_IMAGE` | `pai-stack-terrain:latest` | built image tag |
| `TERRAIN_PORT` | `7878` | host bind port |
| `TERRAIN_BIND_IP` | `127.0.0.1` | host bind address |
| `TERRAIN_ALLOW_LLM` | `0` | `1` enables `ask` / `init`, which run on OpenCode's keyless Zen models — **not** the gateway (see above) |
| `TERRAIN_TOKEN` | *(unset)* | if set, callers must send `x-terrain-token` — see the caveat below |
| `TERRAIN_MEM_LIMIT` | `2g` | memory ceiling |

The project registry lives in the `terrain_data` volume at
`/var/lib/terrain/.terrain/registry.json` — deliberately **not** on the workspace
bind, since it is host state rather than repo content.

## Degradation — this is the designed default, not a failure

**Terrain is a temporary accelerator.** The baseline for locating code in this stack is
`grep` plus targeted reads using the agent's own tools; that path always works and is what
an agent should reach for by default. Terrain adds a shared index when it happens to be
running.

`pai_terrain_ops.check_fn` probes `/healthz` with a 3s timeout and **hides the tool**
when the service is down, so an agent cannot even see a tool that would fail. Removing
Terrain is therefore a supported operation, not a migration:

```bash
make terrain-down          # stop the profile
rm -rf <repo>/.terrain/    # gitignored artifacts, safe to delete
```

Nothing in the core stack references it.

## `TERRAIN_TOKEN` — currently unused, deliberately open

The shim accepts an optional shared secret (`x-terrain-token`) so that a future deployment
which exposes terrain beyond loopback has a gate available. **Today it protects nothing,
and setting it breaks Hermes:** `docker-compose.yaml` passes no `TERRAIN_*` variable to the
hermes container, so `terrain_ops.py` sends an empty token and the shim answers **401 on
every call**.

It is left unset and open on purpose. If you set it, you must also add to the hermes
service's `environment:`:

```yaml
- TERRAIN_URL=http://terrain:7878
- TERRAIN_TOKEN=${TERRAIN_TOKEN:-}
```

Status: **kept, unwired, documented.** Not an oversight — a token is only meaningful once
the bind address stops being `127.0.0.1`.