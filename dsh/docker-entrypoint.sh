#!/bin/bash
# pai-stack — Minimal DSH Entrypoint
#
# Assumptions (see docker-compose.dsh.yaml):
# - PID 1 already runs as the host user via compose `user: "${UID}:${GID}"`.
#   No setpriv drop, no per-boot chown -R. Whatever this script creates is
#   owned correctly by creation.
# - Docker access is via the mounted /var/run/docker.sock + `group_add`
#   (no proxy socket). DOCKER_CONFIG lives on the data volume.
# - Workspace bind at ${WORKSPACE_PATH} is never chowned.
#
# This script only: ensures writable dirs, seeds plugins + default workspace
# registry, starts the 3080->3081 forwarder, execs `dsh web`.

set -euo pipefail

elog() { printf '[%s] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*"; }

# Container-internal workspace path. NOTE this is the IN-CONTAINER path, not
# the host path: every pai-stack service binds its ${WORKSPACE_DIR} host
# directory to this one target (docker-compose.yaml — hermes, terrain, dsh and
# open-design all use target /opt/data/workspace), so hermes and dsh agree on
# it by construction and share filepaths. Changing it means changing every
# mount target, which is why it is derived here once instead of at each call
# site below. Compose passes WORKSPACE_DIR because the directory-picker patch
# in the Dockerfile resolves it inside the container.
WORKSPACE_PATH="${WORKSPACE_DIR:-/opt/data/workspace}"

DSH_HOME="${DSH_HOME:-/data/dsh}"
PORT_INNER=3081
SOCAT_PORT="${SOCAT_PORT:-3080}"
SOCAT_MAX_CHILDREN="${SOCAT_MAX_CHILDREN:-64}"
RESCUE_PROFILE="${RESCUE_PROFILE:-web}"
TRUSTED_ARGS=""

elog "[entrypoint] Running as $(id)"

if [ -n "${DSH_TRUSTED_HOSTS:-}" ]; then
    elog "[entrypoint] trusted Host allowlist: ${DSH_TRUSTED_HOSTS}"
    IFS=',' read -ra HOSTS <<< "${DSH_TRUSTED_HOSTS}"
    for host in "${HOSTS[@]}"; do
        host=$(echo "$host" | xargs)
        if [ -n "$host" ]; then
            TRUSTED_ARGS="${TRUSTED_ARGS} --trusted-host ${host}"
        fi
    done
fi

# Docker socket probe: cap_drop:[ALL] means `docker ps` only reports the
# container's effective caps, not the group_add-mounted socket — it cannot
# fail meaningfully here. A cheap -S test covers the real abort case.
if [ -S /var/run/docker.sock ]; then
    elog "[entrypoint] docker socket present, DOCKER_HOST=${DOCKER_HOST:-<default socket>}"
else
    elog "[entrypoint] WARN no /var/run/docker.sock mounted — docker commands will fail"
fi

start_socat() {
    elog "[entrypoint] starting socat forward: 0.0.0.0:${SOCAT_PORT} -> 127.0.0.1:${PORT_INNER}"
    socat "TCP-LISTEN:${SOCAT_PORT},fork,reuseaddr,max-children=${SOCAT_MAX_CHILDREN}" \
          "TCP:127.0.0.1:${PORT_INNER},forever,interval=1" &
    SOCAT_PID=$!
    elog "[entrypoint] socat started with PID ${SOCAT_PID}"
}

# Default workspace = the shared mount root. DSH has no config for this:
# empty registry => stock first-use throws (no xdg-user-dir in image), so
# pre-seed storages/workspace.json iff absent. documentsDirectory row below
# is fallback only (it appends a fixed `deepseek-harness/default-workspace`
# suffix and can never yield the mount root itself).
# Both paths derive from WORKSPACE_PATH so the seeded registry and the
# documentsDirectory row can never drift apart.
seed_workspace_registry() {
    local store_dir="/data/dsh/storages"
    local ws_json="${store_dir}/workspace.json"
    if [ -f "${ws_json}" ]; then
        elog "[workspace-seed] registry present, skipping"
        return 0
    fi
    local ws_id ts
    ws_id="$(cat /proc/sys/kernel/random/uuid 2>/dev/null || echo "")"
    if [ -z "${ws_id}" ]; then
        elog "[workspace-seed] no uuid source, skipping"
        return 0
    fi
    mkdir -p "${store_dir}" 2>/dev/null || true
    ts="$(date -u +%Y-%m-%dT%H:%M:%S.000Z)"
    elog "[workspace-seed] pre-seeding default workspace ${WORKSPACE_PATH}"
    cat > "${ws_json}" <<EOF
{
  "unit": {
    "name": "workspace",
    "version": 2
  },
  "global": {
    "initialized": true,
    "workspaceIds": [
      "${ws_id}"
    ],
    "archivedSessionIds": [],
    "pinnedSessionIds": [],
    "defaultWorkspaceId": "${ws_id}"
  },
  "tables": {
    "workspaces": {
      "${ws_id}": {
        "path": "${WORKSPACE_PATH}",
        "title": "workspace",
        "sessionIds": [],
        "createdAt": "${ts}",
        "updatedAt": "${ts}"
      }
    }
  }
}
EOF
}

ensure_workspace_controller_row() {
    local web_dir="/data/dsh/profiles/web"
    local web_patch="${web_dir}/cordis.patch.yml"
    if [ ! -d "${web_dir}" ]; then
        elog "[workspace-seed] no web profile yet, skipping controller row"
        return 0
    fi
    if grep -q "documentsDirectory" "${web_patch}" 2>/dev/null; then
        elog "[workspace-seed] documentsDirectory already set, skipping"
        return 0
    fi
    elog "[workspace-seed] setting workspace-controller documentsDirectory"
    cat >> "${web_patch}" <<EOF
- id: workspace-controller
  config:
    documentsDirectory: ${WORKSPACE_PATH}
EOF
}

retire_default_workspace_plugin() {
    local web_patch="/data/dsh/profiles/web/cordis.patch.yml"
    local dest_dir="/data/dsh/plugins/dsh-default-workspace"
    if [ -f "${web_patch}" ] && grep -Eq '^[[:space:]]+-[[:space:]]+id:[[:space:]]+default-workspace([[:space:]]|$)' "${web_patch}" 2>/dev/null; then
        elog "[workspace-seed] removing retired default-workspace plugin entry"
        /usr/local/bin/dsh-drop-patch-entry "${web_patch}" default-workspace 2>&1 | while IFS= read -r line; do elog "[workspace-seed] ${line}"; done || true
    fi
    if [ -f "${dest_dir}/package.json" ] && node -e "const p=require('${dest_dir}/package.json'); if(p.name!=='dsh-default-workspace') process.exit(1)" 2>/dev/null; then
        elog "[workspace-seed] removing stale plugin copy at ${dest_dir}"
        rm -rf "${dest_dir}" 2>/dev/null || true
    fi
}

# Disable DeepSeek-hosted web search; force local browser surfing.
# DSH ships `web.searchProvider: deepseek-official` (an Anthropic-messages
# model turn per query via api.deepseek.com). That cost + external dependency
# is off by default here: the agent surfs with local Playwright/Chromium
# (dsh-pai-web, no remote search API) + local fetch-http + notebook/terrain/
# mnemon. `tool-web.search:false` unregisters model-facing `web_search`
# (keeps `web_fetch`); `web-search-deepseek.disabled` stops the provider;
# `web.fetchProvider:http` drops the search pin. Patch replaces whole
# `config`, so tool-web restates every default (fetch 30s, search 60s).
# Gate with DSH_DISABLE_WEB_SEARCH=false to keep upstream search.
ensure_no_deepseek_search() {
    if [ "${DSH_DISABLE_WEB_SEARCH:-true}" != "true" ]; then
        elog "[websearch-seed] DSH_DISABLE_WEB_SEARCH=false, keeping DeepSeek search"
        return 0
    fi
    local web_dir="/data/dsh/profiles/web"
    local web_patch="${web_dir}/cordis.patch.yml"
    if [ ! -d "${web_dir}" ]; then
        elog "[websearch-seed] no web profile yet, skipping (applied on next boot)"
        return 0
    fi
    if grep -q "pai-stack: disable-deepseek-search" "${web_patch}" 2>/dev/null; then
        elog "[websearch-seed] DeepSeek search already disabled, skipping"
        return 0
    fi
    elog "[websearch-seed] disabling DeepSeek search (local Playwright + fetch-http instead)"
    mkdir -p "${web_dir}" 2>/dev/null || true
    touch "${web_patch}" 2>/dev/null || true
    cat >> "${web_patch}" <<'EOF'
# pai-stack: disable-deepseek-search (DSH_DISABLE_WEB_SEARCH=true).
# DeepSeek search costs a model turn per query; DSH surfs with local
# Playwright/Chromium (dsh-pai-web) + web-fetch-http + notebook/terrain/mnemon.
- id: tool-web
  config:
    search: false
    fetch: true
    searchMaxResults: 8
    searchMaxQueries: 4
    fetchTimeoutMs: 30000
    searchTimeoutMs: 60000
    fetchMaxOutputChars: 200000
- id: web-search-deepseek
  disabled: true
- id: web
  config:
    fetchProvider: http
EOF
}

# Force English locale by pre-seeding the locale preference in storages.
# The dsh-automation plugin ships Chinese prompt fragments; DSH locale
# otherwise defaults to browser language. This writes the volume-side
# store (`/data/dsh/storages/locale.json`) — the one mechanism that works
# under `read_only: true`. The former index.html `sed`/`python3` patch that
# ran alongside this always failed silently (it targeted the read-only image
# layer, and upstream ships `<html lang="en">` anyway), so it was removed.
# Gate with DSH_SEED_LOCALE=false to skip.
seed_locale_preference() {
    if [ "${DSH_SEED_LOCALE:-true}" != "true" ]; then
        elog "[locale-seed] DSH_SEED_LOCALE=false, skipping"
        return 0
    fi
    local store_dir="/data/dsh/storages"
    local locale_json="${store_dir}/locale.json"
    if [ -f "${locale_json}" ]; then
        # Check if already set to English
        if grep -q '"preference"[[:space:]]*:[[:space:]]*"en"' "${locale_json}" 2>/dev/null; then
            elog "[locale-seed] locale preference already set to English, skipping"
            return 0
        fi
        elog "[locale-seed] updating locale preference to English"
    else
        elog "[locale-seed] pre-seeding locale preference to English"
    fi
    mkdir -p "${store_dir}" 2>/dev/null || true
    cat > "${locale_json}" <<'EOF'
{
  "unit": {
    "name": "locale",
    "version": 1
  },
  "global": {
    "initialized": true
  },
  "tables": {
    "settings": {
      "locale": {
        "preference": "en"
      }
    }
  }
}
EOF
}

# Read a package.json "version" field without spawning Node (~13ms each).
# grep/sed are shell builtins with no interpreter startup; this replaces the
# former `node -p "require(...).version"` probes (10 per boot) in the
# plugin sync loop. Prints the version, or nothing if absent/invalid.
extract_pkg_version() {
    [ -f "$1" ] || return 0
    sed -n 's/^[[:space:]]*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$1" | head -1
}

# Writable dirs on the data volume (created as the runtime user — no chown).
mkdir -p "${HOME:-/data/dsh/home}" "${HOME:-/data/dsh/home}/.cache" 2>/dev/null || true
case "${TMPDIR:-/data/dsh/tmp}" in
    /data/dsh/*) mkdir -p "${TMPDIR}" 2>/dev/null || true ;;
esac
mkdir -p /data/dsh/home/.docker 2>/dev/null || true

# Force English locale before plugin seeds initialize
seed_locale_preference

# Plugin seeds: ONE joint tree in the image (/opt/dsh-seed — all plugins
# resolved together at build time). The entrypoint copies it once and
# enforces gates at manifest level: only enabled plugins are listed in
# dependencies + dsh.profile.bundles, so disabled plugin code sits on disk
# but never loads. cordis.patch.yml (the user patch layer) is NEVER
# overwritten. cp --no-preserve because cap_drop:[ALL] lacks CAP_FOWNER.
#
# Table columns (pipe-separated):
#   id | runtime gate ("" = always on) | probe (in-tree package.json)
#   | dep name | extra same-version deps (space-separated)
# Runtime gates are DSH_WITH_* (.env.example §8): "false" drops the plugin
# from the manifest (code stays on disk, unloaded — it never *deletes*
# installed files). Build-time WITH_* (Dockerfile) only controls what is
# baked into the image — a runtime gate cannot install what was never baked.
# NOTE: extras share the probe (primary dep) version — the pai plugins are
# versioned in lockstep (see make dsh-versions-check).
sync_plugin_tree() {
    if [ "${RESCUE_PROFILE}" != "web" ]; then
        return 0
    fi
    local web_dir="${WEB_DIR:-/data/dsh/profiles/web}"
    # DSH_SEED_DIR override exists for offline testing (default = image path).
    local seed_dir="${DSH_SEED_DIR:-/opt/dsh-seed}"
    if [ ! -d "${seed_dir}/node_modules" ]; then
        elog "[entrypoint] no joint seed tree in image, skipping plugin sync"
        return 0
    fi
    local need_copy=0
    local managed=""
    local id gate probe dep extras gate_val seed_ver live_ver d
    while IFS='|' read -r id gate probe dep extras; do
        [ -n "${id}" ] || continue
        gate_val="true"
        if [ -n "${gate}" ]; then
            gate_val="${!gate:-true}"
        fi
        if [ "${gate_val}" != "true" ]; then
            # shellcheck disable=SC2086 — intentional word splitting of the table field
            for d in "${dep}" ${extras}; do
                managed="${managed},'${d}':null"
            done
            continue
        fi
        seed_ver="$(extract_pkg_version "${seed_dir}/node_modules/${probe}")"
        if [ -z "${seed_ver}" ]; then
            elog "[entrypoint] ${id} plugin not baked into image, skipping"
            # shellcheck disable=SC2086 — intentional word splitting of the table field
            for d in "${dep}" ${extras}; do
                managed="${managed},'${d}':null"
            done
            continue
        fi
        live_ver="$(extract_pkg_version "${web_dir}/node_modules/${probe}")"
        if [ "${seed_ver}" != "${live_ver}" ]; then
            need_copy=1
        fi
        # shellcheck disable=SC2086 — intentional word splitting of the table field
        for d in "${dep}" ${extras}; do
            managed="${managed},'${d}':'${seed_ver}'"
        done
    done <<'PLUGIN_TABLE'
auth|DSH_WITH_AUTH|@xgone/dsh-remote/package.json|@xgone/dsh-remote|
memory|DSH_WITH_MNEMON|dsh-mnemon/package.json|dsh-mnemon|
automation|DSH_WITH_AUTOMATION|@michengai/dsh-automation/package.json|@michengai/dsh-automation|
telegram|DSH_WITH_TELEGRAM|dsh-pai-telegram/package.json|dsh-pai-telegram|
pai|DSH_WITH_PAI_PLUGINS|dsh-pai-notebook/package.json|dsh-pai-notebook|dsh-pai-adr dsh-pai-design dsh-pai-terrain dsh-pai-git dsh-pai-web
PLUGIN_TABLE
    if [ "${need_copy}" = 1 ] || [ ! -d "${web_dir}/node_modules" ]; then
        elog "[entrypoint] Installing joint plugin tree to web profile"
        mkdir -p "${web_dir}/node_modules"
        rm -rf "${web_dir}/node_modules"
        cp -r --no-preserve=mode,ownership,timestamps "${seed_dir}/node_modules" "${web_dir}/node_modules"
        local f
        for f in pnpm-lock.yaml pnpm-workspace.yaml; do
            if [ -f "${seed_dir}/${f}" ]; then
                cp -r --no-preserve=mode,ownership,timestamps "${seed_dir}/${f}" "${web_dir}/${f}"
            fi
        done
        elog "[entrypoint] Plugin tree installed"
    else
        elog "[entrypoint] Plugin tree already installed, skipping"
    fi
    # Reconcile the manifest: managed deps/bundles match the gates exactly;
    # anything else in the manifest (user-added via `dsh plugin add`) is
    # preserved. Writes only on change.
    node -e "
const fs=require('fs');
const pf='${web_dir}/package.json';
let p={};
try { p=JSON.parse(fs.readFileSync(pf,'utf8')); } catch(e) { p={}; }
p.name='dsh-profile-web';
if (p.private!==false) p.private=true;
if (!p.dependencies || typeof p.dependencies !== 'object') p.dependencies={};
const managed={${managed#,}};
let changed=false;
for (const name of Object.keys(managed)) {
    const want=managed[name];
    if (want == null) {
        if (name in p.dependencies) { delete p.dependencies[name]; changed=true; }
    } else if (p.dependencies[name] !== want) {
        p.dependencies[name]=want; changed=true;
    }
}
p.dsh=p.dsh||{}; p.dsh.profile=p.dsh.profile||{};
const base=['@deepseek-ai/dsh-base','@deepseek-ai/dsh-web-app'];
const wantBundles=base.concat(Object.keys(managed).filter((n)=>managed[n] != null));
const have=Array.isArray(p.dsh.profile.bundles)?p.dsh.profile.bundles:[];
const managedSet=new Set(Object.keys(managed));
const next=[];
for (const b of have) {
    if (managedSet.has(b) && !wantBundles.includes(b)) { changed=true; continue; }
    if (!next.includes(b)) next.push(b);
}
for (const b of wantBundles) {
    if (!next.includes(b)) { next.push(b); changed=true; }
}
if (JSON.stringify(next) !== JSON.stringify(have)) { p.dsh.profile.bundles=next; changed=true; }
if (changed) {
    fs.writeFileSync(pf,JSON.stringify(p,null,2)+'\n');
    console.log('[entrypoint] manifest reconciled to gates');
} else {
    console.log('[entrypoint] manifest in sync, skipping');
}
"
    # Auth only: if the live patch layer is byte-identical to the shipped
    # bundle patch, it was never personalized — restore the empty user layer.
    if [ -f "${web_dir}/cordis.patch.yml" ] \
       && cmp -s "${web_dir}/cordis.patch.yml" "${seed_dir}/node_modules/@xgone/dsh-remote/cordis.patch.yml"; then
        cat > "${web_dir}/cordis.patch.yml" <<'PPF'
# Your patch layer for this dsh profile, applied after every bundle layer:
# a top-level YAML array of loader patch entries (id-targeted config
# overrides, disables, and insert lists; `!!js` expressions allowed).
[]
PPF
        elog "[entrypoint] restored user-layer cordis.patch.yml"
    fi
}

WEB_DIR="/data/dsh/profiles/web"
sync_plugin_tree

# Playwright library into the live web profile (for pre-existing volumes).
# The joint seed carries node_modules/playwright since the image build, but
# volumes synced before that lack it. Copy iff missing — never overwrites
# cordis.patch.yml.
if [ "${RESCUE_PROFILE}" = "web" ] && [ -d "/opt/dsh-seed/node_modules/playwright" ] && [ ! -f "${WEB_DIR}/node_modules/playwright/package.json" ]; then
    elog "[entrypoint] Installing playwright library into live web profile"
    mkdir -p "${WEB_DIR}/node_modules" 2>/dev/null || true
    CP="cp -r --no-preserve=mode,ownership,timestamps"
    rm -rf "${WEB_DIR}/node_modules/playwright"
    ${CP} "/opt/dsh-seed/node_modules/playwright" "${WEB_DIR}/node_modules/playwright"
    elog "[entrypoint] Playwright library installed"
fi

# Automation tasks (official experimental schedule bundle). Manifest-only:
# the bundle ships inside the image and resolves from the dsh installation,
# so no node_modules copy is needed — selecting it in the plugin manager
# does exactly this append to dsh.profile.bundles. Gate with
# DSH_WITH_SCHEDULE=false to leave the shipped composition untouched.
if [ "${RESCUE_PROFILE}" = "web" ]; then
    if [ "${DSH_WITH_SCHEDULE:-true}" = "true" ]; then
        if node -e "const p=require('${WEB_DIR}/package.json'); if(!(p.dsh&&p.dsh.profile&&Array.isArray(p.dsh.profile.bundles)&&p.dsh.profile.bundles.includes('@deepseek-ai/dsh-experimental-schedule-bundle'))) process.exit(1)" 2>/dev/null; then
            elog "[entrypoint] schedule bundle already enabled, skipping"
        else
            elog "[entrypoint] Enabling Automation tasks (schedule bundle)"
            mkdir -p "${WEB_DIR}"
            node -e "
const fs=require('fs');
const pf='${WEB_DIR}/package.json';
let p={};
try { p=JSON.parse(fs.readFileSync(pf,'utf8')); } catch(e) { p={}; }
p.name='dsh-profile-web';
if (p.private!==false) p.private=true;
p.dsh=p.dsh||{}; p.dsh.profile=p.dsh.profile||{};
const want=['@deepseek-ai/dsh-base','@deepseek-ai/dsh-web-app','@deepseek-ai/dsh-experimental-schedule-bundle'];
const have=Array.isArray(p.dsh.profile.bundles)?p.dsh.profile.bundles:[];
for (const b of want) if(!have.includes(b)) have.push(b);
p.dsh.profile.bundles=have;
fs.writeFileSync(pf,JSON.stringify(p,null,2)+'\n');
"
        fi
    else
        # DSH_WITH_SCHEDULE=false: remove the schedule bundle if present
        if node -e "const p=require('${WEB_DIR}/package.json'); if(!(p.dsh&&p.dsh.profile&&Array.isArray(p.dsh.profile.bundles)&&p.dsh.profile.bundles.includes('@deepseek-ai/dsh-experimental-schedule-bundle'))) process.exit(1)" 2>/dev/null; then
            elog "[entrypoint] Disabling Automation tasks (schedule bundle)"
            mkdir -p "${WEB_DIR}"
            node -e "
const fs=require('fs');
const pf='${WEB_DIR}/package.json';
let p=JSON.parse(fs.readFileSync(pf,'utf8'));
if (p.dsh && p.dsh.profile && Array.isArray(p.dsh.profile.bundles)) {
    p.dsh.profile.bundles = p.dsh.profile.bundles.filter(b => b !== '@deepseek-ai/dsh-experimental-schedule-bundle');
    fs.writeFileSync(pf,JSON.stringify(p,null,2)+'\n');
}
"
fi
    fi
fi

retire_default_workspace_plugin
seed_workspace_registry
ensure_workspace_controller_row
ensure_no_deepseek_search

start_socat

cleanup() {
    elog "[entrypoint] Received signal, shutting down..."
    if [ -n "${SOCAT_PID:-}" ] && kill -0 "${SOCAT_PID}" 2>/dev/null; then
        kill "${SOCAT_PID}" 2>/dev/null || true
        wait "${SOCAT_PID}" 2>/dev/null || true
    fi
    exit 0
}
trap cleanup SIGTERM SIGINT SIGQUIT

elog "[entrypoint] starting dsh web (internal 127.0.0.1:${PORT_INNER})"
# shellcheck disable=SC2086
exec dsh --profile "${RESCUE_PROFILE}" --port "${PORT_INNER}" --no-open ${TRUSTED_ARGS}
