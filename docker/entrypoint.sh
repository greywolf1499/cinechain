#!/bin/sh
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

# Reconcile the baked-in cinechain user/group with the requested PUID/PGID
# (default group/user are created at build time with id 1000; only touch
# them when the runtime environment asks for something different).
if [ "$(id -g cinechain)" != "$PGID" ]; then
    groupmod -o -g "$PGID" cinechain
fi
if [ "$(id -u cinechain)" != "$PUID" ]; then
    usermod -o -u "$PUID" cinechain
fi

mkdir -p /config

# Avoid a slow recursive chown on every boot once ownership already matches.
current_owner="$(stat -c '%u:%g' /config)"
if [ "$current_owner" != "${PUID}:${PGID}" ]; then
    echo "Fixing ownership of /config (was ${current_owner}, want ${PUID}:${PGID})..."
    chown "${PUID}:${PGID}" /config
fi

echo "Running database migrations..."
gosu "${PUID}:${PGID}" alembic -c /app/alembic.ini upgrade head

echo "Starting CineChain as ${PUID}:${PGID}..."
exec gosu "${PUID}:${PGID}" "$@"
