#!/bin/sh
# Wrapper to remount /run with exec after s6-overlay mounts it

# Start base entrypoint in background
/opt/hermes/docker/entrypoint-dispatch.sh gateway run &

BASE_PID=$!

# Wait for s6-overlay to mount tmpfs on /run (indicated by /run/s6 existing)
for i in $(seq 1 30); do
    if [ -d /run/s6 ]; then
        break
    fi
    sleep 1
done

# Remount /run with exec
mount -o remount,exec /run 2>/dev/null || true

# Wait for base entrypoint
wait $BASE_PID