# DSH (DeepSeek Harness) — Optional Container

DeepSeek Harness on the opt-in `dsh` profile. Never starts with `make gateway-up hermes-up` — run `make dsh-up` explicitly.

## Image

Custom build: `pai-stack-dsh:custom` (built from `dsh/Dockerfile`). Pinned to `@deepseek-ai/dsh@0.2.0-rc.2`.

| Layer | What it is |
|---|---|
| Base | `node:24-slim` + git + socat + openssh-client + ca-certificates |
| Toolchain | `python3` + `uv`, `go`, `rustc`/`cargo` + C toolchain |
| Docker CLI | `docker-ce-cli` + `docker-compose-plugin` + `jq`, client-only (no daemon) |
| DSH program | `@deepseek-ai/dsh@0.2.0-rc.2` global npm install at `/usr/local/lib/node_modules/@deepseek-ai/dsh` (not pre-installed into `/opt/dsh-seed`) |
| Runtime | Seed copied to the `dsh_data` volume (`/data/dsh`, mounted read-write on first boot; offline, version-pinned) |
| Web UI | `dsh web` on `127.0.0.1:3081`, socat forwards `0.0.0.0:3080` → `3081` |
| Auth | `@xgone/dsh-remote` plugin — admin + random 16-char password (printed once in first-boot logs) |
| Security | Non-root (uid 1000), read-only root FS, `no-new-privileges`, `cap_drop: [ALL]` with **no** `cap_add` — zero capabilities total |
| Self-healing | Rescue system: snapshot rollback → plugin removal → escalated rollback → lifeboat |

**Pin a version** (locked to `dsh@0.2.0-rc.2`):

```bash
# .env
DSH_IMAGE=pai-stack-dsh:custom
```

## Toolchain

The container ships **Python 3, Go, and Rust** so agents can run the test suites of the projects they edit instead of only reading them. Installed by [`dsh/Dockerfile`](../dsh/Dockerfile), alongside the Mnemon CLI.

| Tool | Version | Path |
|---|---|---|
| Python | 3.11 (Debian 12) + `python3-dev`/`python3-venv`/`python3-yaml` (PyYAML global) | `/usr/bin/python3` |
| uv | 0.12.23 | `/usr/local/bin/uv`, `uvx` |
| Go | 1.27.1 (linux arm64/amd64, checksum-verified) | `/usr/local/go` |
| Rust | rustc + cargo 1.99.0, minimal profile + `clippy` + `rustfmt` | `/usr/local/cargo`, `/usr/local/rustup` |
| C toolchain | `build-essential`, `pkg-config`, `libssl-dev` | `/usr/bin/cc` |

**Why build-time.** The container is `read_only: true` with `cap_drop: [ALL]`, so nothing can be installed after boot — same constraint that forced `mnemon` into the image.

**Why the C toolchain.** Not for Go or Rust, which compile natively. It's for Python C extensions: `uv sync` on any project pulling `pydantic-core`, `numpy`, `cryptography` or `orjson` needs a compiler and `python3-dev` at install time, and dies halfway without them.

### Caches live on the volume, never in the workspace

`HOME=/opt/data/workspace`, so every default cache path would scatter build artifacts into the shared host workspace that Hermes also reads. `docker-compose.yaml` redirects them onto the `dsh_data` volume instead — the toolchains stay read-only in `/usr/local`, only mutable state moves:

`GOCACHE`, `GOMODCACHE`, `GOPATH`, `CARGO_HOME`, `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR`, `UV_TOOL_DIR`, `UV_TOOL_BIN_DIR`, `PIP_CACHE_DIR` → all under `/data/dsh/toolchains/`. Each directory is created on first use by its own tool; nothing needs a pre-seed step.

#### Consequence: build commands need an approval escalation

That location sits outside the agent's sandbox, and it cannot be otherwise — DSH's sandbox policy (`@deepseek-ai/dsh-sandbox-policy`) exposes only `mode` and `workspaceRoot`: **one** writable root, the session cwd, with no extra-roots allowlist and only three modes (`read-only`, `workspace-write`, `danger-full-access`).

So under the default `workspace-write`, `/data/dsh` is unreachable by design and any command that *writes* a cache — `go build`, `go test`, `cargo build`, `uv sync`, `uvx` — fails with `permission denied` until the call is retried with an approval escalation. Pure-read commands (`go doc`, `cargo fmt --check`, `ruff --version`) are unaffected. Verified Oct-2026: escalated, all three toolchains build and test normally against these exact paths.

Don't try to relocate the caches to dodge this — each alternative is worse:

- `/opt/data/workspace` **root** is outside the boundary too; the boundary is the session cwd (the project dir), not the workspace root.
- `/tmp` is a 128 MB tmpfs, which a cargo registry exhausts.
- Putting them in the project buries GBs in a git working tree.

### Usage

```bash
# Python — uv-managed interpreters, not the system one
uv python install 3.13            # lands in UV_PYTHON_INSTALL_DIR (the volume)
cd "$EXECUTION_DIR" && uv sync && uv run pytest

# Go — build cache and module cache are already pointed at the volume
cd "$EXECUTION_DIR" && go test ./...

# Rust — the rustup proxies in /usr/local/cargo/bin dispatch via RUSTUP_HOME,
# so overriding CARGO_HOME at runtime costs nothing
cd "$EXECUTION_DIR" && cargo test
```

### Caveats

| Symptom | Cause / fix |
|---|---|
| `pip install` → `externally-managed-environment` | Debian's PEP 668 marker. Expected — use `uv` or a venv, never a system-wide `pip`. |
| `cargo install`'d binary not found | It lands in `$CARGO_HOME/bin`, which isn't on `PATH`. `export PATH="$CARGO_HOME/bin:$PATH"`. |
| `error: $HOME differs from euid-obtained home directory` | Benign. `HOME` is the workspace bind mount, uid 1000's home is `/home/node`. rustup prints it and continues. |
| `rustup self update` fails | `RUSTUP_HOME` is in the read-only image layer. Bump `RUST_VERSION` and rebuild instead. |
| Build output appears in the repo | Expected. `cargo test` writes `target/` into the project, and `go build -o` writes the binary to the cwd — caches are redirected, *outputs* are not. Both belong in `.gitignore`. |
| `go: creating work dir: mkdir /data/dsh/tmp/…: permission denied` | Sandbox, not a broken install — the cache is on the volume, outside the writable root. Re-run with an approval escalation. Same for cargo's `Cannot create temporary file in /data/dsh/tmp/` and uv's `Failed to initialize cache`. See [Consequence](#consequence-build-commands-need-an-approval-escalation). |
| Disk | The volume grows with module/registry caches. `docker volume prune` is safe — it costs a re-download. |

### Bumping versions

Edit the ARGs at the top of [`dsh/Dockerfile`](../dsh/Dockerfile) and `make dsh-build && make dsh-up`.

- **Go** — `GO_VERSION` *and both* `GO_SHA256_*`. Read the new checksums from `https://go.dev/dl/?mode=json`; the build fails on a mismatch rather than installing an unverified tarball.
- **uv** — the release tag from GitHub.
- **Rust** — take the version from the `[pkg.rust]` block of `https://static.rust-lang.org/dist/channel-rust-<v>.toml`, **not** the first `version =` line of `channel-rust-stable.toml`: that one is *cargo's* (reads `0.100.0` while rustc reads `1.99.0`).
- **Playwright** — `PLAYWRIGHT_VERSION` in `.env` (= compose arg = Dockerfile `ARG` = `dsh-pai-web` dependency, currently 1.49.1). Keep all three in sync; then `make dsh-build && make dsh-up`.

After any bump, run `make dsh-versions-check` — it asserts every pin agrees across `.env.example`, compose defaults, Dockerfile defaults, and plugin manifests, that all six `dsh-pai-*` plugins share one version (the entrypoint upgrade gate keys the whole tree off `dsh-pai-notebook`), and that every `DSH_WITH_*` gate the entrypoint reads is wired and documented.

The image verifies every toolchain at the end of the build (`go version`, `cargo fmt --version`, …). A missing tool fails the build rather than surfacing mid-test-run.

## Core DSH Plugins (bundled in image)

| Plugin | Tool | Purpose |
|---|---|---|
| `dsh-mnemon` | `mnemon_*` | Core memory engine (20+ tools) |
| `@michengai/dsh-automation` | `automation_*` | Scheduled coding tasks (cron/interval, run-once) |
| `dsh-pai-telegram` | — | Telegram bot for DSH |
| `dsh-pai-notebook` | `pai_notebook_ops` | Research vault per project |
| `dsh-pai-adr` | `pai_adr_ops` | Living ADRs + drift detection |
| `dsh-pai-design` | `pai_design_ops` | OpenDesign integration |
| `dsh-pai-terrain` | `pai_terrain_ops` | Code intelligence via Terrain |
| `dsh-pai-git` | `pai_git_ops` | Git + worktree ops |
| `dsh-pai-web` | `pai_web_ops` | Local web surfing (Playwright/Chromium, no remote search API) |

## Web search: DeepSeek off, local Chromium on

DSH ships a DeepSeek-hosted search provider (`web.searchProvider: deepseek-official`
via `api.deepseek.com`, one auxiliary model turn per query). It is **disabled by
default** (`DSH_DISABLE_WEB_SEARCH=true`): on every boot the entrypoint appends an
idempotent overlay to `profiles/web/cordis.patch.yml` — `tool-web.search:false`
(unregisters model-facing `web_search`, keeps local `web_fetch`/http),
`web-search-deepseek.disabled:true`, `web.fetchProvider:http`. Set
`DSH_DISABLE_WEB_SEARCH=false` to keep upstream search.

The replacement is local: `dsh-pai-web` (`pai_web_ops`) never calls `web_search`.
`search`/`search_and_fetch` drive local headless Chromium (Playwright 1.49.1,
baked into the image at `/opt/ms-playwright`, `--no-sandbox`), Bing first with
DuckDuckGo HTML as fallback, and parse result links — no API key, no model turn.
(Bing verified 200 + organic results from container egress; DDG HTML 202s with an
empty shell from here, so it stays fallback-only. Google SERP is deliberately not
used — it serves a bot-check shell to datacenter IPs.) `fetch` tries
local `web_fetch` (http) first, then Chromium (JS-rendered pages), then plain
fetch. New `browse` action = JS-rendered fetch of one URL. Pair with
`pai_notebook_ops` / `pai_terrain_ops` / `mnemon_recall` and cite URLs.

Constraints: Chromium profiles live under `TMPDIR=/data/dsh/tmp` (the 128 MB
`/tmp` tmpfs is too small); first browser launch after a build writes there, so
it needs the usual approval escalation under `workspace-write`. Neither HTML engine
has a recency filter — `recency_days` is accepted and ignored with a note. Per-engine
hit counts log as `[pai-web] surfSearch bing=N / ddg=N` in the container logs, so the
next engine blockage shows up there instead of failing silent.

## Core Hermes plugins (bundled in image)

| Plugin | Tool | Purpose |
|---|---|---|
| `pai_adr_ops` | `pai_adr_ops` | Living ADRs + drift detection |
| `pai_ops_design_ops` | `pai_ops_design_ops` | OpenDesign integration |
| `pai_notebook_ops` | `pai_notebook_ops` | Research vault per project |
| `pai_terrain_ops` | `pai_terrain_ops` | Code intel via Terrain |

Memory: **Mnemosyne** (embedded in Hermes) — native `mnemosyne_*` tools.

(`pai_git_ops` / `pai_web_ops` are DSH-only plugins — see the DSH table above, not Hermes.)

## Schema Fixes (2026-10-11)

- `pai_terrain_ops`: `limit` validated manually in `execute()` (1–200); `minimum`/`maximum` are not used in tool schemas (unsupported by the schemastery DSL)
- `pai_web_ops`: `max_results` (1–50) and `recency_days` (1–365) validated manually in `execute()` for the same reason
- Official experimental schedule bundle is off by default (`DSH_WITH_SCHEDULE=false`); scheduled tasks run via `@michengai/dsh-automation`

## Key DSH Plugins

### `@michengai/dsh-automation`

Scheduled coding tasks with full UI: `dsh plugin --profile web add @michengai/dsh-automation`. Creates cron/interval tasks that run in fresh sessions. Run history, pause/resume, run-now, per-task concurrency.

### `dsh-mnemon`

Core memory engine (20+ `mnemon_*` tools). Stores in `dsh_data` volume. Provides `mnemon_remember`, `mnemon_recall`, `mnemon_runtime_memory`, `mnemon_memory_bodies`, `mnemon_document_*`, `mnemon_link`, `mnemon_forget`, `mnemon_status`.

### `dsh-pai-telegram`

Telegram bot for DSH: chat round-trip, live drafts, inline approvals/questions, group chats. Configure via `DSH_TELEGRAM_BOT_TOKEN` + allowlists.

### Pai-stack plugins (loaded via seed)

| Plugin | Tool | Purpose |
|---|---|---|
| `dsh-pai-notebook` | `pai_notebook_ops` | Research vault per project |
| `dsh-pai-adr` | `pai_adr_ops` | Living ADRs + drift detection |
| `dsh-pai-design` | `pai_design_ops` | OpenDesign integration |
| `dsh-pai-terrain` | `pai_terrain_ops` | Code intelligence via Terrain |
| `dsh-pai-git` | `pai_git_ops` | Git + worktree |
| `dsh-pai-web` | `pai_web_ops` | Local web surfing (search/fetch/search_and_fetch/browse) |

### Plugin seeds & gates

All plugins resolve jointly into **one** image tree (`/opt/dsh-seed`, ~150 MB).
On every boot the entrypoint copies it once and reconciles the profile
manifest: only plugins with their `DSH_WITH_*` gate on are listed in
`dependencies` + `dsh.profile.bundles`, so gated-off code stays on disk but
never loads. Steady-state boots copy nothing and rewrite nothing (both steps
log `skipping` / `in sync`). Per-engine `sync_plugin_tree` logs plus
`[pai-web] surfSearch` hit counts in `make dsh-logs` show what ran.

| Gate | Default | Effect when `false` |
|---|---|---|
| `DSH_WITH_AUTH` / `DSH_WITH_MNEMON` | `true` | Login / memory plugin unloaded |
| `DSH_WITH_AUTOMATION` | `true` | Cron plugin unloaded (use `DSH_WITH_SCHEDULE` for the stock bundle instead) |
| `DSH_WITH_TELEGRAM` | `true` | Telegram plugin unloaded |
| `DSH_WITH_PAI_PLUGINS` | `true` | All `dsh-pai-*` plugins unloaded (incl. the sound plugin) |
| `DSH_DISABLE_WEB_SEARCH` | `true` | Keeps upstream DeepSeek search (see above) |

`make dsh-versions-check` asserts every pin and gate is wired consistently.

## Docker Socket

Mounted read-only: `/var/run/docker.sock:/var/run/docker.sock:ro`. DSH uses `docker-ce-cli` + `docker-compose-plugin` from the image — no daemon inside.

## Auth

`@xgone/dsh-remote` plugin. First boot prints admin password to logs. Fetch with `make dsh-password`. Change immediately.

## Quick Start

```bash
make dsh-up           # starts gateway + dsh
make dsh-logs         # tail logs
make dsh-password     # print admin password (first boot only)
```

Open DSH Web: http://localhost:9229

## Common Issues

| Symptom | Fix |
|---|---|
| `pip install` → `externally-managed-environment` | Use `uv` or a venv |
| `cargo install` binary not found | `export PATH="$CARGO_HOME/bin:$PATH"` |
| `cargo install` binary not found | It lands in `$CARGO_HOME/bin`, which isn't on `PATH`. `export PATH="$CARGO_HOME/bin:$PATH"`. |
| `error: $HOME differs from euid-obtained home directory` | Benign. `HOME` is the workspace bind mount, uid 1000's home is `/home/node`. rustup prints it and continues. |
| `rustup self update` fails | `RUSTUP_HOME` is in the read-only image layer. Bump `RUST_VERSION` and rebuild instead. |
| Build output appears in the repo | Expected. `cargo test` writes `target/` into the project, and `go build -o` writes the binary to the cwd — caches are redirected, *outputs* are not. Both belong in `.gitignore`. |
| `go: creating work dir: mkdir /data/dsh/tmp/…: permission denied` | Sandbox, not a broken install — the cache is on the volume, outside the writable root. Re-run with an approval escalation. Same for cargo's `Cannot create temporary file in /data/dsh/tmp/` and uv's `Failed to initialize cache`. See [Consequence](#consequence-build-commands-need-an-approval-escalation). |
| Disk | The volume grows with module/registry caches. `docker volume prune` is safe — it costs a re-download. |

### Bumping versions

Edit the ARGs at the top of [`dsh/Dockerfile`](../dsh/Dockerfile) and `make dsh-build && make dsh-up`.

- **Go** — `GO_VERSION` *and both* `GO_SHA256_*`. Read the new checksums from `https://go.dev/dl/?mode=json`; the build fails on a mismatch rather than installing an unverified tarball.
- **uv** — the release tag from GitHub.
- **Rust** — take the version from the `[pkg.rust]` block of `https://static.rust-lang.org/dist/channel-rust-<v>.toml`, **not** the first `version =` line of `channel-rust-stable.toml`: that one is *cargo's* (reads `0.100.0` while rustc reads `1.99.0`).

The image verifies every toolchain at the end of the build (`go version`, `cargo fmt --version`, …). A missing tool fails the build rather than surfacing mid-test-run.

## Pets

DSH ships a desktop pet system. Default: pixel-black-cat. Manage via `dsh pets install <name>` / `dsh pets list`.

## Access

- Web UI: http://localhost:9229
- API: http://localhost:9229/api
- WebSocket: ws://localhost:9229/ws