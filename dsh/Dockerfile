# pai-stack — Custom DSH Build (Official DSH Binary, Layer by Layer)
#
# This Dockerfile builds DSH from the official npm package @deepseek-ai/dsh
# instead of using the upstream dsh-docker-server image. It makes explicit
# architectural choices to avoid host/container mapping complications:
#
# 1. DSH program lives in the IMAGE (not a volume) — version-pinned, immutable
# 2. Workspace is a BIND MOUNT at a DIFFERENT PATH — no conflict with DSH_HOME
# 3. Toolchain caches on a NAMED VOLUME — not scattered in workspace
# 4. Host UID/GID mapping — not fixed uid 1000 (compose `user:` + baked
#    /data/dsh skeleton, so fresh volumes inherit the right owner)
# 5. Docker CLI for host-daemon reuse — socket mounted at runtime with
#    `group_add: [DOCKER_GID]`, no proxy socket, no extra caps
#
# Usage:
#   docker build -t pai-stack-dsh:custom \
#     --build-arg DSH_VERSION=0.2.0-rc.2 \
#     --build-arg TARGET_UID=1000 \
#     --build-arg TARGET_GID=1000 \
#     --build-arg WITH_DOCKER_SOCKET=true \
#     -f dsh/Dockerfile.custom ./dsh

ARG BASE_IMAGE=node:24-slim
FROM ${BASE_IMAGE} AS base

# ── Build arguments ──────────────────────────────────────────────────────
ARG DSH_VERSION=0.2.0-rc.2
ARG MNEMON_VERSION=0.2.10
ARG GO_VERSION=1.27.1
ARG GO_SHA256_AMD64=63d339f0da5ab53635a56f2490a7984dfe12dfcff22ad749f63edaf590168445
ARG GO_SHA256_ARM64=3450b45a3f9ee8568792736a5c5e70a1f2e9b36c35a8f74958c03e51d7d92bec
ARG UV_VERSION=0.12.23
ARG RUST_VERSION=1.99.0
ARG TARGET_UID=1000
ARG TARGET_GID=1000
ARG WITH_DOCKER_SOCKET=true
# Auth plugin (@xgone/dsh-remote) — matches upstream default
ARG WITH_REMOTE_PLUGIN=true
ARG REMOTE_PLUGIN_VERSION=0.3.5
ARG REMOTE_PLUGIN_NAME=@xgone/dsh-remote
# Memory plugin (dsh-mnemon) — tools surface as mnemon_* (no rename support
# upstream); shared skills keep calling Hermes-native mnemosyne_*
ARG WITH_MNEMON_PLUGIN=true
ARG MNEMON_PLUGIN_VERSION=0.5.24
ARG MNEMON_PLUGIN_NAME=dsh-mnemon

# ── Stage 1: Install system dependencies ────────────────────────────────
FROM base AS deps

USER root

# Install runtime dependencies in a single layer
RUN set -eux; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        python3 \
        python3-dev \
        python3-pip \
        python3-venv \
        python3-yaml \
        build-essential \
        pkg-config \
        libssl-dev \
        gnupg \
        jq \
        socat \
        openssh-client \
    ; \
    rm -rf /var/lib/apt/lists/*; \
    python3 -c "import yaml; print('[deps] python3-yaml', yaml.__version__)"

# Shared seed-tree builders, sourced by every seed stage below (all stages
# descend from `deps`, so one COPY covers them).
COPY build-seed-lib.sh /usr/local/bin/dsh-seed-lib.sh
RUN chmod 755 /usr/local/bin/dsh-seed-lib.sh \
    && sh -n /usr/local/bin/dsh-seed-lib.sh \
    && echo "[deps] seed lib installed"

# ── Stage 2: Install DSH (official npm package) ─────────────────────────
FROM deps AS dsh-install

# Re-declare build args for this stage
ARG DSH_VERSION=0.2.0-rc.2

# Install DSH globally into the image (not a volume)
# NPM_CONFIG_PREFIX=/usr/local ensures it stays in the image layer
RUN set -eux; \
    NPM_CONFIG_PREFIX=/usr/local npm install -g @deepseek-ai/dsh@${DSH_VERSION} \
    && /usr/local/bin/dsh --version

# Verify DSH installation
RUN dsh --version && which dsh

# ── Stage 3: Install Mnemon CLI (for dsh-mnemon plugin) ─────────────────
FROM dsh-install AS mnemon

ARG MNEMON_VERSION=0.2.10

# Install pnpm via corepack (included in Node.js 24)
RUN corepack enable pnpm && corepack prepare pnpm@latest --activate

RUN set -eux; \
    NPM_CONFIG_PREFIX=/usr/local npm install -g @mnemon-dev/mnemon@${MNEMON_VERSION} \
    && /usr/local/bin/mnemon --version \
    && chmod -R a+rX /usr/local/lib/node_modules/@mnemon-dev/mnemon

# ── Plugin seeds are built ONCE in final (single joint tree at /opt/dsh-seed).
# Earlier revisions built one cumulative tree per plugin (remote → mnemon →
# auto → telegram → pai); all four earlier trees were strict subsets of the
# last — ~300MB of duplicated image layers. Gates are now enforced at
# manifest level by the entrypoint (unlisted bundles never load), so one
# tree suffices. See the joint seed build in final below.

# ── Stage 5: Install Go toolchain ───────────────────────────────────────
FROM mnemon AS go-toolchain

ARG GO_VERSION=1.27.1
ARG GO_SHA256_AMD64=63d339f0da5ab53635a56f2490a7984dfe12dfcff22ad749f63edaf590168445
ARG GO_SHA256_ARM64=3450b45a3f9ee8568792736a5c5e70a1f2e9b36c35a8f74958c03e51d7d92bec

RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        amd64) goarch=amd64; go_sha256="${GO_SHA256_AMD64}" ;; \
        arm64) goarch=arm64; go_sha256="${GO_SHA256_ARM64}" ;; \
        *) echo "Unsupported architecture for Go: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/go.tar.gz "https://go.dev/dl/go${GO_VERSION}.linux-${goarch}.tar.gz"; \
    echo "${go_sha256}  /tmp/go.tar.gz" | sha256sum -c -; \
    tar -C /usr/local -xzf /tmp/go.tar.gz; \
    rm -f /tmp/go.tar.gz

ENV GOROOT=/usr/local/go \
    PATH=/usr/local/go/bin:${PATH}

# ── Stage 5: Install uv (Python package manager) ────────────────────────
FROM go-toolchain AS uv-toolchain

ARG UV_VERSION=0.12.23

RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        amd64) uvarch=x86_64 ;; \
        arm64) uvarch=aarch64 ;; \
        *) echo "Unsupported architecture for uv: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/uv.tar.gz \
        "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${uvarch}-unknown-linux-gnu.tar.gz"; \
    tar -C /usr/local/bin -xzf /tmp/uv.tar.gz --strip-components=1 --wildcards '*/uv' '*/uvx'; \
    rm -f /tmp/uv.tar.gz

# ── Stage 6: Install Rust toolchain ─────────────────────────────────────
FROM uv-toolchain AS rust-toolchain

ARG RUST_VERSION=1.99.0

ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:${PATH}

RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        amd64) triple=x86_64-unknown-linux-gnu ;; \
        arm64) triple=aarch64-unknown-linux-gnu ;; \
        *) echo "Unsupported architecture for Rust: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/rustup-init "https://static.rust-lang.org/rustup/dist/${triple}/rustup-init"; \
    chmod +x /tmp/rustup-init; \
    /tmp/rustup-init -y --no-modify-path --profile minimal \
        --component clippy,rustfmt \
        --default-toolchain "${RUST_VERSION}"; \
    rm -f /tmp/rustup-init

# ── Stage 7: Install Docker CLI (optional) ──────────────────────────────
FROM rust-toolchain AS docker-cli

# Only install Docker CLI if WITH_DOCKER_SOCKET=true
ARG WITH_DOCKER_SOCKET=true

RUN set -eux; \
    if [ "${WITH_DOCKER_SOCKET}" = "true" ]; then \
        apt-get update; \
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends gnupg; \
        install -m 0755 -d /etc/apt/keyrings; \
        curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg; \
        chmod a+r /etc/apt/keyrings/docker.gpg; \
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list; \
        apt-get update; \
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
            docker-ce-cli docker-compose-plugin \
        ; \
        rm -rf /var/lib/apt/lists/*; \
    fi

# ── Stage 8: Final image with user setup and entrypoint ─────────────────
FROM docker-cli AS final

ARG TARGET_UID=1000
ARG TARGET_GID=1000
ARG WITH_DOCKER_SOCKET=true
# Plugin seed versions (all plugins resolve jointly in the single seed build
# below; entrypoint gates are runtime-only DSH_WITH_* — see .env.example §8).
# Auth plugin (@xgone/dsh-remote) — matches upstream default
ARG WITH_REMOTE_PLUGIN=true
ARG REMOTE_PLUGIN_VERSION=0.3.5
ARG REMOTE_PLUGIN_NAME=@xgone/dsh-remote
# Memory plugin (dsh-mnemon) — tools surface as mnemon_* (no rename support
# upstream); shared skills keep calling Hermes-native mnemosyne_*
ARG WITH_MNEMON_PLUGIN=true
ARG MNEMON_PLUGIN_VERSION=0.5.24
ARG MNEMON_PLUGIN_NAME=dsh-mnemon
# Durable automation plugin (@michengai/dsh-automation) — peerDeps cover this
# DSH version explicitly (verified Oct-2026 — the alternatives only allow
# <0.2.0). `--ignore-scripts` blocks lifecycle scripts at seed time.
# Pinned exact in .env (AUTOMATION_PLUGIN_VERSION).
ARG WITH_AUTOMATION_PLUGIN=true
ARG AUTOMATION_PLUGIN_VERSION=0.1.54
ARG AUTOMATION_PLUGIN_NAME=@michengai/dsh-automation
# First-party Telegram plugin (in-repo plugins/dsh-pai-telegram).
# Host-only (inject:[]) so it cannot red-screen the web UI.
ARG WITH_TELEGRAM_PLUGIN=true
ARG TELEGRAM_PLUGIN_NAME=dsh-pai-telegram
# First-party pai-stack plugins (in-repo source at plugins/dsh-pai-*)
# Always baked (in-repo source, zero registry risk); the entrypoint's
# DSH_WITH_PAI_PLUGINS runtime gate controls installation instead.
# (No compose passthrough by design — unlike the registry plugins above.)
ARG WITH_PAI_PLUGINS=true
# Local browser surfing (replaces DeepSeek-hosted web search):
# Playwright library + Chromium. Pinned exact; bump deliberately
# (browser build is ~300MB and needs `make dsh-build && make dsh-up`).
ARG PLAYWRIGHT_VERSION=1.49.1

# ── Joint plugin seed (ALL plugins, ONE tree at /opt/dsh-seed) ──────────
# One tmp profile resolves everything jointly — the same operations the old
# per-plugin stages did, minus the 4 redundant cumulative trees: registry
# plugins via pnpm (in dependency order), in-repo plugins via copy (pnpm
# file: deps break on cleanup), one manifest merge, one verify. Built here
# in final (not as earlier stages) so the cached toolchain layers above
# stay valid. The entrypoint copies this tree once and enforces gates at
# manifest level (unlisted bundles never load).
COPY plugins/dsh-pai-telegram /tmp/dsh-pai-telegram-src
COPY plugins/dsh-pai-notebook /tmp/dsh-pai-notebook-src
COPY plugins/dsh-pai-adr /tmp/dsh-pai-adr-src
COPY plugins/dsh-pai-design /tmp/dsh-pai-design-src
COPY plugins/dsh-pai-terrain /tmp/dsh-pai-terrain-src
COPY plugins/dsh-pai-git /tmp/dsh-pai-git-src
COPY plugins/dsh-pai-web /tmp/dsh-pai-web-src
RUN set -eux; \
    . /usr/local/bin/dsh-seed-lib.sh; \
    seed_begin /opt/dsh-seed /tmp/seed-profile dsh-seed; \
    MANIFEST=""; REGISTRY=""; FIRST=""; \
    if [ "${WITH_REMOTE_PLUGIN}" = "true" ] && [ -n "${REMOTE_PLUGIN_VERSION}" ]; then \
        echo "[seed] Resolving ${REMOTE_PLUGIN_NAME}@${REMOTE_PLUGIN_VERSION} via pnpm"; \
        seed_pnpm_add /tmp/seed-profile "${REMOTE_PLUGIN_NAME}@${REMOTE_PLUGIN_VERSION}"; \
        MANIFEST="${MANIFEST} ${REMOTE_PLUGIN_NAME}:${REMOTE_PLUGIN_VERSION}"; \
        REGISTRY="${REGISTRY} ${REMOTE_PLUGIN_NAME}"; \
    fi; \
    if [ "${WITH_MNEMON_PLUGIN}" = "true" ] && [ -n "${MNEMON_PLUGIN_VERSION}" ]; then \
        echo "[seed] Resolving ${MNEMON_PLUGIN_NAME}@${MNEMON_PLUGIN_VERSION} via pnpm"; \
        seed_pnpm_add /tmp/seed-profile "${MNEMON_PLUGIN_NAME}@${MNEMON_PLUGIN_VERSION}"; \
        MANIFEST="${MANIFEST} ${MNEMON_PLUGIN_NAME}:${MNEMON_PLUGIN_VERSION}"; \
        REGISTRY="${REGISTRY} ${MNEMON_PLUGIN_NAME}"; \
    fi; \
    if [ "${WITH_AUTOMATION_PLUGIN}" = "true" ] && [ -n "${AUTOMATION_PLUGIN_VERSION}" ]; then \
        echo "[seed] Resolving ${AUTOMATION_PLUGIN_NAME}@${AUTOMATION_PLUGIN_VERSION} via pnpm"; \
        seed_pnpm_add /tmp/seed-profile "${AUTOMATION_PLUGIN_NAME}@${AUTOMATION_PLUGIN_VERSION}"; \
        MANIFEST="${MANIFEST} ${AUTOMATION_PLUGIN_NAME}:${AUTOMATION_PLUGIN_VERSION}"; \
        REGISTRY="${REGISTRY} ${AUTOMATION_PLUGIN_NAME}"; \
    fi; \
    if [ "${WITH_TELEGRAM_PLUGIN:-true}" = "true" ]; then \
        echo "[seed] Staging dsh-pai-telegram via manual copy"; \
        seed_copy_src /tmp/seed-profile /tmp/dsh-pai-telegram-src "${TELEGRAM_PLUGIN_NAME}"; \
        _ver="$(node -p "require('/tmp/dsh-pai-telegram-src/package.json').version")"; \
        MANIFEST="${MANIFEST} ${TELEGRAM_PLUGIN_NAME}:${_ver}"; \
        FIRST="${FIRST} ${TELEGRAM_PLUGIN_NAME}"; \
    fi; \
    if [ "${WITH_PAI_PLUGINS:-true}" = "true" ]; then \
        echo "[seed] Staging pai-stack plugins via manual copy"; \
        _ver="$(node -p "require('/tmp/dsh-pai-notebook-src/package.json').version")"; \
        for plugin in dsh-pai-notebook dsh-pai-adr dsh-pai-design dsh-pai-terrain dsh-pai-git dsh-pai-web; do \
            seed_copy_src /tmp/seed-profile "/tmp/${plugin}-src" "${plugin}"; \
            MANIFEST="${MANIFEST} ${plugin}:${_ver}"; \
            FIRST="${FIRST} ${plugin}"; \
        done; \
    fi; \
    seed_manifest /tmp/seed-profile ${MANIFEST}; \
    seed_publish /opt/dsh-seed /tmp/seed-profile; \
    seed_verify /opt/dsh-seed ${REGISTRY}; \
    seed_verify /opt/dsh-seed ${FIRST} --check-lib; \
    rm -rf \
        /tmp/dsh-pai-telegram-src \
        /tmp/dsh-pai-notebook-src \
        /tmp/dsh-pai-adr-src \
        /tmp/dsh-pai-design-src \
        /tmp/dsh-pai-terrain-src \
        /tmp/dsh-pai-git-src \
        /tmp/dsh-pai-web-src; \
    echo '[seed] joint plugin tree staged at /opt/dsh-seed'

# Patch dsh-automation to force English locale (Zg function) at build time
RUN AUTOMATION_CLIENT="/opt/dsh-seed/node_modules/@michengai/dsh-automation/lib/client.js" && \
    if [ -f "${AUTOMATION_CLIENT}" ]; then \
        sed -i 's/function Zg(e){return [^}]*}/function Zg(e){return w3}/g' "${AUTOMATION_CLIENT}" && \
        echo "[seed] Patched dsh-automation Zg function to force English locale"; \
    fi

# Automation tasks (official experimental schedule bundle). Ships inside the
# @deepseek-ai/dsh package itself — no seed copy needed, the entrypoint only
# appends it to the web profile's bundles list. Gate fails the build if a
# DSH_VERSION bump ever drops or renames it.
RUN BUNDLE=/usr/local/lib/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-experimental-schedule-bundle; \
    test -f "${BUNDLE}/package.json" && test -f "${BUNDLE}/cordis.patch.yml" \
    && node -e "const p=require('${BUNDLE}/package.json'); if(!p.dsh||!p.dsh.bundle||!p.dsh.bundle.patch) throw new Error('not a DSH bundle: '+p.name); console.log('[final] schedule bundle', p.name+'@'+p.version)"

# ── Local browser surfing (Playwright + Chromium, replaces DeepSeek search) ─
# DSH has no browser; dsh-pai-web surfs with this instead of the
# deepseek-official search provider (a model turn per query). Browsers live in
# the image layer (read-only OK); profiles/tmp stay on the data volume at
# runtime (see ENV below). `--with-deps` apt-installs the Chromium system
# libs (needs root at build time). The library is also staged into the joint
# seed's node_modules so the plugin's plain `import('playwright')` resolves
# inside the web profile (global installs are invisible to ESM otherwise).
# `--no-shell`: the full chromium-1148 build covers every
# `chromium.launch({headless:true})` call the plugin makes; the extra
# chromium_headless_shell-1148 tree (254 MB) has no launcher in this image.
RUN set -eux; \
    NPM_CONFIG_PREFIX=/usr/local npm install -g "playwright@${PLAYWRIGHT_VERSION}"; \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright npx -y "playwright@${PLAYWRIGHT_VERSION}" install --with-deps --no-shell chromium; \
    chmod -R a+rX /opt/ms-playwright /usr/local/lib/node_modules/playwright; \
    node -p "require('/usr/local/lib/node_modules/playwright/package.json').version" | grep -qx "${PLAYWRIGHT_VERSION}"; \
    if [ -d /opt/dsh-seed/node_modules ]; then \
        rm -rf /opt/dsh-seed/node_modules/playwright; \
        cp -a /usr/local/lib/node_modules/playwright /opt/dsh-seed/node_modules/playwright; \
        node -e " \
            const fs=require('fs'); \
            const pf='/opt/dsh-seed/package.json'; \
            const p=JSON.parse(fs.readFileSync(pf,'utf8')); \
            p.dependencies=Object.assign({},p.dependencies,{'playwright':'${PLAYWRIGHT_VERSION}'}); \
            fs.writeFileSync(pf,JSON.stringify(p,null,2)+'\n'); \
        "; \
        node -p "require('/opt/dsh-seed/node_modules/playwright/package.json').version" | grep -qx "${PLAYWRIGHT_VERSION}"; \
        chmod -R a+rX /opt/dsh-seed/node_modules/playwright; \
        echo "[final] playwright ${PLAYWRIGHT_VERSION} staged into joint seed"; \
    else \
        echo "[final] joint seed not built, playwright global only"; \
    fi; \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright /usr/local/bin/node --input-type=module -e "import('/opt/dsh-seed/node_modules/playwright').then(async(m)=>{const b=await m.chromium.launch({args:['--no-sandbox']});await b.close();console.log('[final] playwright smoke: chromium launches')})" 2>&1 || echo "[final] WARN playwright launch smoke skipped (sandbox unavailable at build)"; \
    echo "[final] playwright browsers at /opt/ms-playwright:"; ls /opt/ms-playwright

# Dedicated runtime user matching the host UID/GID (compose `user:` runs as
# this numeric id from PID 1 — no setpriv, no groupmod). Reuses an existing
# group id when taken (e.g. Debian `dialout` owns 20, macOS staff is 20):
# numeric ownership is what matters, not the name.
RUN set -eux; \
    if getent group "${TARGET_GID}" >/dev/null; then \
        echo "[final] GID ${TARGET_GID} exists ($(getent group "${TARGET_GID}" | cut -d: -f1)), reusing"; \
    else \
        groupadd -g "${TARGET_GID}" dsh; \
    fi; \
    if ! id dsh >/dev/null 2>&1; then \
        useradd -o -m -u "${TARGET_UID}" -g "${TARGET_GID}" -d /data/dsh/home -s /bin/bash dsh; \
    fi; \
    id dsh; \
    # Bake the data-volume skeleton so fresh named volumes inherit the right
    # owner on creation (Docker copies image ownership in). Pre-existing
    # volumes are covered once by `make dsh-perms`.
    mkdir -p /data/dsh/home/.cache /data/dsh/tmp /data/dsh/profiles/web \
        /data/dsh/storages /data/dsh/sessions /data/dsh/auth; \
    chown -R "${TARGET_UID}:${TARGET_GID}" /data/dsh; \
    # Docker access is via compose group_add on the mounted socket, not here.
    echo "[final] runtime user dsh ${TARGET_UID}:${TARGET_GID}"

# Ensure all installed toolchains are readable by the node user
RUN set -eux; \
    chmod -R a+rX /usr/local/go /usr/local/rustup /usr/local/cargo /usr/local/bin/uv /usr/local/bin/uvx /usr/local/bin/dsh /usr/local/bin/mnemon 2>/dev/null || true; \
    # Also ensure the joint plugin seed is readable
    if [ -d /opt/dsh-seed ]; then chmod -R a+rX /opt/dsh-seed; fi

# Single canonical home: /data/dsh/home lives on the data volume, so it is
# writable at runtime (the image layer is read_only:true). Session cwd comes
# from the container working_dir (/opt/data/workspace, set in compose), and
# every cache below must accept writes — paths under /home/node or
# /home/dshuser are dead on a read-only rootfs.
ENV DSH_HOME=/data/dsh \
    HOME=/data/dsh/home \
    PATH=/usr/local/go/bin:/usr/local/cargo/bin:/usr/local/bin:${PATH} \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    # Chromium launch flags are set per-call in dsh-pai-web (--no-sandbox,
    # --disable-dev-shm-usage): the container drops ALL caps, so the sandbox
    # can never initialize; profiles live under TMPDIR (data volume), never
    # in the 128MB /tmp tmpfs.
    GOROOT=/usr/local/go \
    RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    # Toolchain cache directories (on the dsh_toolchains volume via $HOME/.cache)
    GOCACHE=/data/dsh/home/.cache/go/build \
    GOMODCACHE=/data/dsh/home/.cache/go/mod \
    GOPATH=/data/dsh/home/.cache/go/path \
    CARGO_HOME=/data/dsh/home/.cache/cargo \
    UV_CACHE_DIR=/data/dsh/home/.cache/uv/cache \
    UV_PYTHON_INSTALL_DIR=/data/dsh/home/.cache/uv/python \
    UV_TOOL_DIR=/data/dsh/home/.cache/uv/tools \
    UV_TOOL_BIN_DIR=/data/dsh/home/.cache/uv/bin \
    PIP_CACHE_DIR=/data/dsh/home/.cache/pip \
    UV_LINK_MODE=copy \
    DOCKER_CONFIG=/data/dsh/home/.docker \
    # TMPDIR must point to data volume (not tmpfs) for large temp files
    TMPDIR=/data/dsh/tmp \
    # Native bindings must load from executable volume
    NARB_DISABLE_NATIVE_CACHE=1

# /tmp is tmpfs at runtime; image-layer home paths are unused
# (HOME=/data/dsh/home on the volume). Volume content above is the
# ownership seed for fresh named volumes.
RUN mkdir -p /tmp && chmod 1777 /tmp

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD node -e "require('net').connect(3081,'127.0.0.1').on('connect',()=>process.exit(0)).on('error',()=>process.exit(1))"

# Patch-segment tool for entrypoint profile maintenance (stdlib-only python3).
# NOTE: the entrypoint pre-seeds storages/workspace.json with the schema below
# (unit version 2, global/workspaceIds/defaultWorkspaceId, tables.workspaces).
# If DSH_VERSION is bumped, re-verify that schema against a live-written file.
COPY dsh-drop-patch-entry.py /usr/local/bin/dsh-drop-patch-entry
RUN chmod 755 /usr/local/bin/dsh-drop-patch-entry \
    && printf -- '- id: keep\n- insert:\n    - id: default-workspace\n' > /tmp/patch-smoke.yml \
    && python3 /usr/local/bin/dsh-drop-patch-entry /tmp/patch-smoke.yml default-workspace \
    && grep -q "id: keep" /tmp/patch-smoke.yml && ! grep -q "default-workspace" /tmp/patch-smoke.yml \
    && rm -f /tmp/patch-smoke.yml \
    && echo "[final] patch-segment tool installed and verified"

# Directory-picker start directory: stock DSH opens the "Select Workspace
# Directory" browse dialog at the server $HOME (homedir()) with no override.
# Read the container WORKSPACE_DIR first (falls back to home when unset) so
# the dialog opens at the shared mount. Parens are required: ?? and || must
# not mix unparenthesized. If DSH_VERSION is bumped, re-verify this line.
RUN BROWSE_LIB=/usr/local/lib/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-host-directory-picker-browse/lib/index.js \
    && grep -c "const target = resolve(path ?? home);" "${BROWSE_LIB}" | grep -qx "1" \
    && sed -i 's|const target = resolve(path ?? home);|const target = resolve(path ?? (process.env.WORKSPACE_DIR \|\| home));|' "${BROWSE_LIB}" \
    && grep -q "process.env.WORKSPACE_DIR" "${BROWSE_LIB}" \
    && echo "[final] directory-picker start dir patched to WORKSPACE_DIR"

# Entrypoint runs as the host user via compose `user:` (no setpriv).
# It only ensures dirs, seeds plugins + workspace registry, starts the
# 3080->3081 forwarder (non-root bind, port > 1024), and execs dsh web.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh

USER root
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["dsh", "web"]