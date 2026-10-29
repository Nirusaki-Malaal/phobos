# pai-stack — OpenDesign with bundled Linux opencode v1 (zen-free, keyless).
#
# Why a custom build: the host macOS opencode is Mach-O/arm64 and cannot
# execute inside this Alpine Linux container, and upstream ships no CLIs by
# design. So we install the Linux build here, plus libc6-compat for the
# glibc-linked binary (mirroring upstream deploy/Dockerfile.local).
#
# Why v1 and NOT v2 (deliberate divergence from hermes, which pins v2.0.16):
# this daemon's opencode adapter targets the v1 CLI surface — `run --dir`
# (sent unconditionally to pin the worktree), `--pure` (connection-test
# isolation), `--variant`, `-s` resume. The v2 track (verified through
# 2.0.18) removed `--dir`/`--pure`, so both Test and chat runs fail with
# "Unrecognized flag". v1.18.x also serves the zen-free models keyless,
# exactly like the Hermes opencode-delegate path. The per-run model comes
# from the OpenDesign UI picker (daemon passes `-m` explicitly), so no
# config file is baked in.
# At runtime HOME=/tmp (tmpfs, see compose) gives opencode a writable home
# for cache/sessions; the daemon itself keeps state in /app/.od (OD_DATA_DIR).
#
# To pin a different base: docker compose build --build-arg OPEN_DESIGN_BASE=<ref>

ARG OPEN_DESIGN_BASE=ghcr.io/nexu-io/od:latest
FROM ${OPEN_DESIGN_BASE}

USER root

# Align the image-seeded /app/.od ownership with the runtime user.
# The upstream base ships /app/.od owned by 1001:1001; Docker copies that
# ownership into a FRESH named volume at container creation — after
# `make design-perms` already ran — so the daemon (running as host UID:GID
# via compose `user:`) gets EACCES on first boot, on every host. Baking the
# host ids in here makes fresh volumes inherit the right owner from the
# start; design-perms stays as belt-and-braces for pre-existing volumes.
# Wired through compose build.args (Makefile exports UID/GID); defaults keep
# plain `docker build` working for the common Linux case.
ARG UID=1000
ARG GID=1000
RUN chown -R ${UID}:${GID} /app/.od

ARG OPENCODE_VERSION=1.18.32
RUN apk add --no-cache libc6-compat curl tar xz \
    && curl -fsSL https://opencode.ai/install | bash -s -- --no-modify-path -v ${OPENCODE_VERSION} \
    && ln -sf /root/.opencode/bin/opencode /usr/local/bin/opencode \
    && chmod -R a+rX /root/.opencode \
    && chmod a+x /root \
    && /usr/local/bin/opencode --version
