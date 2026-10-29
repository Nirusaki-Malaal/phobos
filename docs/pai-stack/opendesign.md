# OpenDesign (optional)

Design studio on the opt-in `design` profile. Never starts with `make gateway-up hermes-up` — run `make design-up` explicitly.

## Run

```bash
make design-up      # create volume + fix ownership + build/start open-design
make design-logs    # tail logs
make design-down    # stop container only (workspace + volume untouched)
make design-config  # validate compose config with design profile
make design-build   # rebuild image (bundled linux opencode)
```

UI: http://127.0.0.1:7456 (bound to `127.0.0.1` only by default). Login user `open-design` + `OD_API_TOKEN` from `.env` (default `admin` — replace with `openssl rand -hex 32` for anything beyond local use).

> Browse via `127.0.0.1`, not `localhost`: upstream reserves `localhost` as the
> sandboxed preview-iframe origin, so a `localhost` tab loads the shell but all
> `/api` calls (including generation) get `403 Powered preview origin cannot
> access this API route`. DNS names need the LAN setup below.

## LAN / DNS access (host IP instead of loopback)

Three gates must all pass; all are project-side config:

```bash
# .env
OPEN_DESIGN_BIND_IP="0.0.0.0"
OPEN_DESIGN_ALLOWED_ORIGINS="http://192.168.1.46:7456,http://office2:7456"
OD_API_TOKEN="<openssl rand -hex 32>"   # never LAN-expose the default `admin`
```

```bash
make design-up   # warns if LAN-bound with a default/empty token
```

1. **TCP bind** (`OPEN_DESIGN_BIND_IP`, default `127.0.0.1`): `0.0.0.0` publishes the port on the LAN so host IPs/DNS names can connect at all.
2. **Origin allowlist** (`OPEN_DESIGN_ALLOWED_ORIGINS`, exact `scheme://host:port`, comma-separated): the daemon default-denies non-loopback browser origins with `403 Cross-origin requests are not allowed`.
3. **Auth**: inside Docker the browser peer is never loopback, so each origin needs its own login (`open-design` + token; browsers cache Basic per-origin).

Prefer a TLS proxy / Tailscale over raw LAN exposure.

## Workspace sharing

`${WORKSPACE_DIR}` is mounted at `/opt/data/workspace` (same path as Hermes and DSH). In the UI: **Import folder → `/opt/data/workspace/<project>`**. Generated files land as real files in the host workspace where Hermes code intel sees them. Daemon state stays in the `open_design_data` volume (`/app/.od`) — never bind-mount `.od` into the workspace (SQLite lock contention, index noise).

Link a folder from CLI:

```bash
make design-import d=/opt/data/workspace/<dir> [n=<name>]   # container path, not host path
```

Fix volume ownership to host UID:GID:

```bash
make design-perms
```

## Image

Built locally as `pai-stack-open-design:latest` (`./opendesign/Dockerfile`): upstream base + Linux opencode v1.18.32 + `libc6-compat`. v1 is deliberate — the daemon's adapter needs `--dir` / `--pure` / `--variant` / `-s`, removed in the v2 track. The model comes from the UI picker per run; no config baked in. `HOME=/tmp` (tmpfs) gives opencode a writable home; daemon state is unaffected.

Stock upstream instead (no bundled opencode — generation then needs BYOK keys):

```bash
OPEN_DESIGN_IMAGE=ghcr.io/nexu-io/od:<version> make design-up
# or pin base at build: --build-arg OPEN_DESIGN_BASE=ghcr.io/nexu-io/od:<ver|digest>
```

No `mem_limit` on purpose: daemon + opencode server + run exceed upstream's 384m and get SIGKILLed (`AGENT_SIGNAL_SIGKILL`). `pids_limit: 256` stays.

## Auth notes

Single-tenant `OD_API_TOKEN`. Browser: user `open-design` + token. API: `Authorization: Bearer <token>`. Never publish on LAN/public without a TLS proxy in front.
