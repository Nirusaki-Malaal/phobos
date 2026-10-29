# syntax=docker/dockerfile:1.7
#
# Terrain code-intelligence service for pai-stack.
#
# Terrain publishes prebuilt binaries for darwin-arm64 and win32-x64 only —
# @terrain-ai/cli-linux-x64 is NOT on npm. pai-stack is Linux-only, so we build
# the CLI from source in a multi-stage image.
#
# `src-tauri` is a cargo workspace member, but `terrain-cli` does not depend on
# it (verified in crates/terrain-cli/Cargo.toml: anyhow, clap, serde,
# terrain-core, terrain-agent, dotenvy, serde_json, slug, tokio). Building
# `-p terrain-cli` therefore never pulls in the webkit/GTK dependency chain.
# See .planning/2026-10-04-terrain-service/findings.md §2.

# ─── Stage 1: build ───────────────────────────────────────────────────────────
FROM rust:1.94-slim AS build

# Build toolchain.
#
# `rust:*-slim` ships gcc but NOT g++ / cmake, and the dep tree pulls in
# `aws-lc-sys` — the rustls crypto backend, which compiles AWS-LC from source.
# Verified from a failed build: `cc-rs: failed to find tool "c++"`, i.e. gcc was
# present and only g++ was missing. So install the whole set at once rather than
# discovering each missing piece on a ~7-minute rebuild:
#   g++        C++ compiler (aws-lc-sys)
#   cmake      AWS-LC's build system   (missing → "cmake not found")
#   make       generator back end for cmake
#   perl       AWS-LC source generation
#   pkg-config some cc-rs build scripts probe system libs through it
# git/ ca-certificates: clone the repo, fetch crates
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      g++ cmake make perl pkg-config \
      git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ARG TERRAIN_REF=main
ARG TERRAIN_REPO=https://github.com/sopaco/terrain.git

WORKDIR /src

# Shallow clone of a single ref. Terrain is small (~90 files) so this layer
# invalidates only when TERRAIN_REF changes.
RUN git clone --depth 1 --branch "${TERRAIN_REF}" "${TERRAIN_REPO}" . \
 && git rev-parse HEAD > /terrain-commit

# Cache the dependency graph separately from the source, so ordinary source
# edits do not force a full dependency rebuild.
RUN cargo fetch --locked

COPY . .

# -p terrain-cli only. Do NOT add src-tauri to this — it needs libwebkit2gtk.
RUN cargo build --release --locked -p terrain-cli \
 && install -m 0755 target/release/terrain /usr/local/bin/terrain \
 && /usr/local/bin/terrain --version

# ─── Stage 2: runtime ────────────────────────────────────────────────────────
# Debian slim rather than scratch: terrain shells out to git for HEAD tracking.
#
# MUST be the same Debian major as the `rust:*-slim` builder above — otherwise the
# binary links against a newer glibc than this image ships and dies at the
# `--version` gate below with "version `GLIBC_x.yz' not found". That is not
# hypothetical: rust:1.94-slim is trixie (glibc 2.41) while bookworm-slim is 2.36,
# so the binary needed 2.38/2.39 and would not run. rust:*-slim tracks the current
# Debian stable, so trixie is what pairs with it; re-check both when bumping the
# toolchain tag rather than assuming the tag implies a distro.
FROM debian:trixie-slim

# libstdc++6: aws-lc-sys builds C/C++ into the binary, so it may pull in a C++
# runtime dependency that debian-slim does not ship by default. Cheap insurance
# against a "version `GLIBCXX_...' not found" failure at container start.
#
# nodejs: the ENTRYPOINT is `node /opt/terrain/server.mjs`, so the runtime image
# needs a Node runtime even though the terrain binary is Rust. It was missing —
# the image had never been built, so nothing caught it until the container failed
# to start with `exec: "node": executable file not found in $PATH`. The shim uses
# plain JSON-RPC with no npm dependencies, so the distro package is enough (no
# install stage, no version to drift). Debian trixie ships Node 20.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git ca-certificates curl libstdc++6 nodejs \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --home-dir /home/terrain --shell /usr/sbin/nologin terrain

COPY --from=build /usr/local/bin/terrain /usr/local/bin/terrain
COPY --from=build /terrain-commit /etc/terrain-commit
# `init` runs Litho, which is not built into the binary — terrain loads it as a
# skill from `<crate dir>/../../preset_skills/litho-documents-skill`, which
# resolves to /src/preset_skills/. Copying only the binary left that path absent
# and `init` died with "Litho skill not found at /src/crates/terrain-core/
# ../../preset_skills/litho-documents-skill" *after* the scan had already run.
# The directory name is part of the binary's runtime contract, so it must land at
# that exact path.
COPY --from=build /src/preset_skills /src/preset_skills
COPY server.mjs /opt/terrain/server.mjs
# The ACP agent's config, seeded onto the volume at boot.
COPY opencode.json /opt/terrain/opencode.json

# COPY preserves the build context's file mode, so whatever umask the author had
# when `server.mjs` was written becomes the mode in the image. It is 0600 in this
# checkout, which landed as `-rw------- root:root` and made the container die with
# `EACCES: permission denied, open '/opt/terrain/server.mjs'` the moment it ran as
# `USER terrain` (uid 1000). Same rule as dsh/Dockerfile: everything here is
# written by root, so nothing may depend on root-only bits under cap_drop:[ALL].
RUN chmod a+rX /opt/terrain /usr/local/bin/terrain /etc/terrain-commit /src/preset_skills

# Gate: run the binary in the FINAL image, not just the builder. A missing shared
# library now fails `docker build` with a clear error instead of surfacing later
# as a crash-looping container.
RUN /usr/local/bin/terrain --version

# Terrain locates Litho via `<crate dir>/../../preset_skills/…`, i.e.
# /src/crates/terrain-core/../../preset_skills/litho-documents-skill. POSIX
# resolves `..` by walking the path, so EVERY intermediate component must exist.
# Copying preset_skills alone is therefore not enough: with /src/crates absent
# the traversal fails ENOENT even though /src/preset_skills sits right there —
# verified in the built image, where a direct read of the skill succeeded and the
# same read through `../..` did not. The empty crate dir is load-bearing.
#
# The `cat` is the gate: it exercises the precise path the binary will use, so a
# future refactor that moves preset_skills fails the build instead of silently
# degrading `init` to "partial" at runtime.
RUN mkdir -p /src/crates/terrain-core \
 && cat /src/crates/terrain-core/../../preset_skills/litho-documents-skill/skill-card.md > /dev/null \
 && echo "litho skill reachable via the path terrain resolves"

# ── ACP agent ────────────────────────────────────────────────────────────────
# Terrain does NOT generate its knowledge assets through the OpenAI-compatible
# endpoint — it SPAWNS an ACP agent subprocess (`terrain settings check-acp`
# reports spawn_command "opencode acp"). With nothing on PATH, `init` exits in
# ~0.2s having spent nothing, returns "please configure an ACP agent in Settings
# first", and the project is stuck at status "partial" — search/read/source work
# against the scan output, but the synthesised human docs and agent context never
# exist.
#
# Installed while HOME is still /root, because the installer drops the binary in
# $HOME/.opencode and HOME only becomes the /var/lib/terrain volume at the ENV
# below. Installing after that line would put the binary on the volume, where a
# volume wipe would silently remove it — the same shadowing trap that makes
# upstream dsh install to /usr/local rather than NPM_CONFIG_PREFIX.
#
# It is then MOVED to /opt/opencode, not symlinked in place. Debian's /root is
# 0700, so a uid-1000 `terrain` process cannot traverse it and
# /usr/local/bin/opencode -> /root/.opencode/bin/opencode is a dangling symlink
# for exactly the user this image runs as. hermes can symlink into /root because
# hermes runs as root; terrain cannot. The build-time gate below runs as root too,
# so it did not catch this — the ACP agent must be verified AS the runtime uid.
#
# Agent is reachable and runs as the uid this image uses; see the opencode
# section for why it is pointed at the gateway.
#
# The agent talks to llm-gateway, NOT OpenCode's Zen free models. Two reasons.
# AGENTS.md makes the gateway the only sanctioned inference path for terrain
# ("terrain ──→ llm-gateway:4000 only when TERRAIN_ALLOW_LLM=1"), so routing the
# ACP agent elsewhere would add a second, undocumented backend. And `opencode acp`
# has no --model flag and will not resolve a keyless Zen model: asked for one it
# dies at session creation with "ACP protocol error: Authentication required:
# provider authentication required", even though plain `opencode run` resolves
# that same model fine. OPENAI_BASE_URL/OPENAI_API_KEY are already exported for
# terrain's own routing, so the `openai` provider needs no extra configuration.
ARG OPENCODE_VERSION=2.0.16
RUN curl -fsSL https://opencode.ai/v2/install | bash -s -- --no-modify-path -v ${OPENCODE_VERSION} \
 && /root/.opencode/bin/opencode --version \
 && mv /root/.opencode /opt/opencode \
 && chmod -R a+rX /opt/opencode \
 && ln -sf /opt/opencode/bin/opencode /usr/local/bin/opencode \
 && opencode --version \
 # Run the gate as the uid this image actually runs as. Without it the ACP agent
 # can ship broken: root's `opencode --version` passes while a uid-1000
 # `terrain` gets EACCES. HOME is still /root at this point in the build (the ENV
 # below changes it), and /root is 0700, so the gate gets a writable HOME of its
 # own; at runtime XDG_DATA_HOME points at the volume instead.
 && HOME=/tmp XDG_CONFIG_HOME=/tmp/oc-cfg XDG_DATA_HOME=/tmp/oc-gate \
    setpriv --reuid=1000 --regid=1000 --clear-groups opencode --version

# Terrain's project registry lives OUTSIDE any repo, at ~/.terrain/registry.json.
# It must never default to the shared workspace mount — see findings.md §8.
#
# XDG_CONFIG_HOME must be WRITABLE, not a path in the image layer. opencode
# appends its own `opencode/` under it and mkdirs on startup — verified: with
# XDG_CONFIG_HOME=/etc/opencode even `opencode acp --help` dies with
# `EACCES: permission denied, mkdir '/etc/opencode/opencode'`, which would also
# fail at runtime under read_only:true. So it lives on the volume and the shim
# seeds it from the pristine copy in the image on boot (same pattern dsh's and
# hermes's entrypoints use for config-on-volume).
# XDG_DATA_HOME is opencode's cache/session state — the other writable path.
ENV HOME=/var/lib/terrain \
    XDG_CONFIG_HOME=/var/lib/terrain/.config \
    XDG_DATA_HOME=/var/lib/terrain/.opencode-data \
    TERRAIN_PORT=7878 \
    TERRAIN_WORKSPACE=/opt/data/workspace \
    NODE_ENV=production

# Pristine source for the seed; the writable copy lives on the volume above.
RUN mkdir -p /var/lib/terrain /var/lib/terrain/.config /opt/data/workspace \
 && chown -R terrain:terrain /var/lib/terrain /opt/data/workspace

USER terrain
WORKDIR /opt/data/workspace
EXPOSE 7878

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${TERRAIN_PORT}/healthz" || exit 1

ENTRYPOINT ["node", "/opt/terrain/server.mjs"]