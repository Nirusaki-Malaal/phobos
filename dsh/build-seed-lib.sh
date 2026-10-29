#!/bin/sh
# dsh-seed-lib.sh — shared seed-tree builders for dsh/Dockerfile.
#
# Sourced (NOT executed) inside RUN steps:
#     . /usr/local/bin/dsh-seed-lib.sh
# Copied once into the `deps` stage; every later stage inherits it.
#
# Every seed extends the previous joint tree so pnpm resolves the whole
# composition together — exactly what `dsh plugin add` does live. The
# entrypoint then installs one superset tree with no runtime merge
# conflicts by construction.
#
# Registry plugins arrive via `seed_pnpm_add`; in-repo plugins via
# `seed_copy_src` (plain copy, because `pnpm file:` deps break when the
# source is cleaned up). Only the copy path needs `seed_manifest` —
# `pnpm add` records the dependency itself.
# POSIX sh only (runs under /bin/sh in build stages): no arrays, no [[ ]].

# seed_begin <staging> <tmp> <seed-name> [prev-seed]
# Reset staging+tmp; inherit the prev joint tree when it has node_modules,
# else start a fresh profile. The name allowlist below is the set of trees
# this repo produces — anything else fails the build instead of silently
# forking the composition.
seed_begin() {
    _sb_staging="$1"; _sb_tmp="$2"; _sb_name="$3"; _sb_prev="${4:-}"
    rm -rf "${_sb_staging}" "${_sb_tmp}"
    mkdir -p "${_sb_staging}" "${_sb_tmp}"
    if [ -n "${_sb_prev}" ] && [ -d "${_sb_prev}/node_modules" ]; then
        cp -a "${_sb_prev}/." "${_sb_tmp}/"
        node -p "require('${_sb_tmp}/package.json').name" | grep -qE "^(seed|dsh-profile-web|dsh-remote-seed|dsh-mnemon-seed|dsh-auto-seed|dsh-telegram-seed|dsh-pai-seed)$" || exit 1
    else
        printf '{\n  "name": "%s",\n  "private": true,\n  "dependencies": {}\n}\n' "${_sb_name}" > "${_sb_tmp}/package.json"
        printf 'packages:\n  - .\n\nnodeLinker: hoisted\nautoInstallPeers: false\n' > "${_sb_tmp}/pnpm-workspace.yaml"
    fi
}

# seed_pnpm_add <tmp> <pkg@ver> — resolve one registry plugin into the tree.
# PNPM_BIN override exists for offline testing (default = image path).
seed_pnpm_add() {
    PNPM_BIN="${PNPM_BIN:-/usr/local/bin/pnpm}"
    if [ ! -x "${PNPM_BIN}" ]; then
        echo "[seed] FATAL: ${PNPM_BIN} not found" >&2; exit 1
    fi
    cd "$1"
    NPM_CONFIG_CACHE="${NPM_CONFIG_CACHE:-/root/.npm}" "${PNPM_BIN}" add --store-dir "${PNPM_STORE_DIR:-/root/.pnpm-store}" --ignore-scripts "$2"
}

# seed_copy_src <tmp> <src-dir> <dep> — stage one in-repo plugin by copy.
seed_copy_src() {
    mkdir -p "$1/node_modules"
    cp -a "$2" "$1/node_modules/$3"
}

# seed_manifest <tmp> <dep:ver>... — additive-only manifest merge for the
# copy path (pnpm maintains its own). Deps become dependencies AND bundles;
# base+web-app are always present.
seed_manifest() {
    _sm_tmp="$1"; shift
    _sm_deps=""; _sm_bundles=""
    for _sm_dv in "$@"; do
        _sm_d="${_sm_dv%%:*}"; _sm_v="${_sm_dv#*:}"
        _sm_deps="${_sm_deps},'${_sm_d}':'${_sm_v}'"
        _sm_bundles="${_sm_bundles},'${_sm_d}'"
    done
    node -e "
const fs=require('fs');
const pf='${_sm_tmp}/package.json';
let p={};
try { p=JSON.parse(fs.readFileSync(pf,'utf8')); } catch(e) { p={}; }
p.name='dsh-profile-web';
if (p.private!==false) p.private=true;
p.dependencies=Object.assign({},p.dependencies,{${_sm_deps#,}});
p.dsh=p.dsh||{}; p.dsh.profile=p.dsh.profile||{};
const want=['@deepseek-ai/dsh-base','@deepseek-ai/dsh-web-app'${_sm_bundles}];
const have=Array.isArray(p.dsh.profile.bundles)?p.dsh.profile.bundles:[];
for (const b of want) if(!have.includes(b)) have.push(b);
p.dsh.profile.bundles=have;
fs.writeFileSync(pf,JSON.stringify(p,null,2)+'\n');
"
}

# seed_verify <staging> <dep>... [--check-lib] — fail the build on a bad
# plugin (missing patch, non-bundle package, broken first-party lib).
# Explicit `|| exit 1` (not just `set -e`): the guard must hold however the
# caller sourced this file.
seed_verify() {
    _sv_staging="$1"; shift
    _sv_check=0
    for _sv_a in "$@"; do
        if [ "${_sv_a}" = "--check-lib" ]; then _sv_check=1; fi
    done
    for _sv_dep in "$@"; do
        if [ "${_sv_dep}" = "--check-lib" ]; then continue; fi
        test -f "${_sv_staging}/node_modules/${_sv_dep}/package.json" || exit 1
        test -f "${_sv_staging}/node_modules/${_sv_dep}/cordis.patch.yml" || exit 1
        if [ "${_sv_check}" = 1 ]; then
            test -f "${_sv_staging}/node_modules/${_sv_dep}/lib/index.js" || exit 1
        fi
        node -e "const p=require('${_sv_staging}/node_modules/${_sv_dep}/package.json'); if(!p.dsh||!p.dsh.bundle||!p.dsh.bundle.patch) throw new Error('not a DSH bundle: '+p.name); console.log('[seed] plugin', p.name+'@'+p.version, 'bundle patch:', p.dsh.bundle.patch)" || exit 1
        if [ "${_sv_check}" = 1 ]; then
            node --check "${_sv_staging}/node_modules/${_sv_dep}/lib/index.js" || exit 1
        fi
    done
}

# seed_publish <staging> <tmp> — move the finished tree into place.
seed_publish() {
    cp -a "$2/node_modules" "$1/node_modules"
    cp -a "$2/package.json" "$1/package.json"
    for _sp_f in pnpm-lock.yaml pnpm-workspace.yaml; do
        if [ -f "$2/${_sp_f}" ]; then cp -a "$2/${_sp_f}" "$1/${_sp_f}"; fi
    done
    chmod -R a+rX "$1"
    rm -rf "$2"
}
