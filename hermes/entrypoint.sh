#!/bin/sh
# Hermes entrypoint wrapper:
# 1. Sets permissions on Hermes home / SQLite WAL files
# 2. Copies and normalizes config paths
# 3. Sets dashboard auth password hash if provided
# 4. Spawns filesystem notifier in background
# 5. Delegates to upstream entrypoint

set -e

# Ensure hermes user can write to /opt/hermes/data (directory owned by 502:20 with 755;
# root has only rx as "other", so make it world-writable for root entrypoint operations)
chmod 777 /opt/hermes/data 2>/dev/null || true

# Ensure lazy-packages directory exists inside hermes-data volume
mkdir -p /opt/hermes/data/lazy-packages

# Ensure /opt/data directory exists and is owned by Hermes user (for helper scripts)
mkdir -p /opt/data
chown -R "${HERMES_UID:-1000}:${HERMES_GID:-1000}" /opt/data 2>/dev/null || true

# Ensure Mnemosyne memory provider plugin wrapper and skill are registered
if [ -x /opt/hermes/.venv/bin/mnemosyne-hermes ]; then
    /opt/hermes/.venv/bin/mnemosyne-hermes install --mode wrapper --python /opt/hermes/.venv/bin/python3 --no-bootstrap --force 2>/dev/null || true
fi

# Ensure baked bundled plugins are linked into the user plugins root
# (/opt/hermes/plugins/* is the bundled discovery dir; the symlink makes the
# same tree visible under $HERMES_HOME/plugins for tools that resolve
# $HERMES_HOME-relative paths).
# NOTE: prompt-optimizer is deliberately NOT symlinked: its engine reads
# $HERMES_HOME/plugins/prompt-optimizer/model-profiles.yaml (user-editable)
# and writes metrics.db alongside it. A symlink would redirect those writes
# into the read-only image layer; a real volume dir keeps them persistent.
mkdir -p /opt/hermes/data/plugins
for _p in hermes-labyrinth hermes-memory-ui next-prompt; do
    if [ -d "/opt/hermes/plugins/${_p}" ] && [ ! -e "/opt/hermes/data/plugins/${_p}" ]; then
        ln -s "/opt/hermes/plugins/${_p}" "/opt/hermes/data/plugins/${_p}"
    fi
done
unset _p

# Seed prompt-optimizer user dir on the volume on first start.
# The engine reads $HERMES_HOME/plugins/prompt-optimizer/model-profiles.yaml
# (user-editable, falls back to baked-in defaults when missing) and creates
# metrics.db there via _ensure_db().
mkdir -p /opt/hermes/data/plugins/prompt-optimizer
if [ -f /opt/hermes/plugins/prompt-optimizer/model-profiles.yaml ] && \
   [ ! -f /opt/hermes/data/plugins/prompt-optimizer/model-profiles.yaml ]; then
    cp /opt/hermes/plugins/prompt-optimizer/model-profiles.yaml \
       /opt/hermes/data/plugins/prompt-optimizer/model-profiles.yaml 2>/dev/null || true
fi

# Seed bundled dashboard themes into HERMES_HOME (repo is source of truth;
# re-synced every start). The hermes-data volume shadows anything baked into
# /opt/hermes/data at build time, so this must happen at container start.
mkdir -p /opt/hermes/data/dashboard-themes
if [ -d /opt/hermes/dashboard-themes ]; then
    cp -f /opt/hermes/dashboard-themes/*.yaml /opt/hermes/data/dashboard-themes/ 2>/dev/null || true
fi

# Seed bundled TUI skins into HERMES_HOME (same volume-shadowing reason).
mkdir -p /opt/hermes/data/skins
if [ -d /opt/hermes/skins ]; then
    cp -f /opt/hermes/skins/*.yaml /opt/hermes/data/skins/ 2>/dev/null || true
fi

# Seed the default pet sprite into HERMES_HOME on first start (same
# volume-shadowing reason; re-synced only when missing so a user-removed pet
# stays removed). Purely cosmetic — no effect on tokens or agent behavior.
mkdir -p /opt/hermes/data/pets
if [ -d /opt/hermes/pets-seed/pixel-black-cat ] && \
   [ ! -e /opt/hermes/data/pets/pixel-black-cat ]; then
    cp -r /opt/hermes/pets-seed/pixel-black-cat /opt/hermes/data/pets/pixel-black-cat 2>/dev/null || true
fi

# Ensure SQLite WAL/SHM files are created group/world-writable
umask 000
find /opt/hermes/data -type f \( -name '*.db' -o -name '*.db-wal' -o -name '*.db-shm' -o -name '*.db.dispatch.lock' -o -name '*.db.init.lock' \) -exec chmod -c a+rw {} + 2>/dev/null || true

# Fix tmpfs ownership for s6-overlay (run as root before dropping privileges)
# Don't use -R on /run (contains read-only bind mounts like docker.sock)
chown "${HERMES_UID:-1000}:${HERMES_GID:-1000}" /run /tmp /var/run
echo "DEBUG: after chown, /run owner=$(stat -c %u /run) /var_run owner=$(stat -c %u /var/run) chown_exit=$?" >&2
ls -ld /run /var/run /tmp >&2

# Remove existing config (may be owned by root from cont-init) so hermes user can recreate
rm -f /opt/hermes/data/hermes-config.yaml
cp /tmp/hermes-config.yaml.host /opt/hermes/data/hermes-config.yaml

DATA_DIR="${HERMES_DATA_DIR:-/opt/data/workspace}"

# Dynamically normalize paths in config to match DATA_DIR
python3 -c "
import re
with open('/opt/hermes/data/hermes-config.yaml', 'r') as f:
    content = f.read()
content = content.replace('/opt/hermes/data/workspace', '\${DATA_DIR}')
content = content.replace('/stack_root', '\${DATA_DIR}')
content = re.sub(r'(/opt/data)+/workspace', '\${DATA_DIR}', content)
content = re.sub(r'(?<!/opt/data)/workspace', '\${DATA_DIR}', content)
with open('/opt/hermes/data/hermes-config.yaml', 'w') as f:
    f.write(content)
"

# Ensure CLI reads the same config
rm -f /opt/hermes/data/config.yaml
cp /opt/hermes/data/hermes-config.yaml /opt/hermes/data/config.yaml

# Silence harmless upstream syntax warning in update_cmd.py
sed -i 's/venv\\Scripts/venv\\\\Scripts/g' /opt/hermes/hermes_cli/update_cmd.py 2>/dev/null || true

# If password env var is set, regenerate hash in config
if [ -n "${HERMES_DASHBOARD_BASIC_AUTH_PASSWORD:-}" ]; then
    HASH=$(sh -c '. /opt/hermes/.venv/bin/activate && python3 -c "
from plugins.dashboard_auth.basic import hash_password
import os
print(hash_password(os.environ[\"HERMES_DASHBOARD_BASIC_AUTH_PASSWORD\"]))
"' 2>/dev/null || true)

    if [ -n "$HASH" ]; then
        sed -i "s|password_hash:.*|password_hash: ${HASH}|" /opt/hermes/data/hermes-config.yaml
        sed -i "s|password_hash:.*|password_hash: ${HASH}|" /opt/hermes/data/config.yaml
    fi
fi

export HERMES_CONFIG=/opt/hermes/data/hermes-config.yaml

# Own the volume by the HOST uid/gid (Makefile: UID/GID = id -u/id -g) so files
# hermes creates land on the host as the host user instead of as root.
# This is NOT a privilege drop: the gateway stays uid 0 (verified via
# /proc/1/status -> "Uid: 0 0 0 0"), which is precisely what reaches the
# root:root docker socket. Contrast dsh, which runs non-root and therefore
# reaches the daemon via its entrypoint's proxy socket (DOCKER_HOST).
chown -R "${HERMES_UID:-1000}:${HERMES_GID:-1000}" /opt/hermes/data 2>/dev/null || true

# Drop privileges to host UID/GID before starting upstream entrypoint (s6-overlay)
# --init-groups requires user in /etc/passwd (created later by cont-init), so use --keep-groups
# Use a wrapper to chown /run AFTER dropping privileges (preinit checks /run ownership)
exec setpriv --reuid="${HERMES_UID:-1000}" --regid="${HERMES_GID:-1000}" --keep-groups /bin/sh -c 'echo "DEBUG wrapper: uid=$(id -u) /run owner=$(stat -c %u /run) before chown" >&2; chown "${HERMES_UID:-1000}:${HERMES_GID:-1000}" /run /tmp /var/run 2>/dev/null || true; echo "DEBUG wrapper: /run owner=$(stat -c %u /run) after chown" >&2; exec /opt/hermes/docker/entrypoint-dispatch.sh "$@"' "$@"
