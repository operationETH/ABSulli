#!/bin/sh
set -eu
umask 077

ABSULLI_HOST="${ABSULLI_HOST:-0.0.0.0}"
ABSULLI_PORT="${ABSULLI_PORT:-8272}"
ABSULLI_DATA_DIR="${ABSULLI_DATA_DIR:-/config}"
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

config_permission_error() {
    owner="$(stat -c '%u:%g' "$ABSULLI_DATA_DIR" 2>/dev/null || printf 'unknown')"
    if [ "$(id -u)" = "0" ]; then
        echo "ERROR: Cannot set permissions in $ABSULLI_DATA_DIR (owned by $owner). The container may be missing the CHOWN, FOWNER or DAC_READ_SEARCH capabilities, or the volume's filesystem may not allow ownership changes." >&2
    else
        echo "ERROR: Cannot set permissions in $ABSULLI_DATA_DIR. Running as uid=$(id -u) gid=$(id -g); directory owned by $owner. Fix ownership on the Docker host, or remove the Docker user override and configure PUID/PGID instead." >&2
    fi
    exit 1
}

mkdir -p "$ABSULLI_DATA_DIR"

if [ "$(id -u)" = "0" ]; then
    case "$PUID" in
        *[!0-9]*|'') PUID=1000; echo "WARNING: Invalid PUID; using 1000" >&2 ;;
    esac
    if ! [ "$PUID" -gt 0 ] 2>/dev/null; then
        PUID=1000
        echo "WARNING: PUID must be a positive numeric ID; using 1000" >&2
    fi
    case "$PGID" in
        *[!0-9]*|'') PGID=1000; echo "WARNING: Invalid PGID; using 1000" >&2 ;;
    esac
    if ! [ "$PGID" -gt 0 ] 2>/dev/null; then
        PGID=1000
        echo "WARNING: PGID must be a positive numeric ID; using 1000" >&2
    fi

    if [ -n "$(find "$ABSULLI_DATA_DIR" \( ! -uid "$PUID" -o ! -gid "$PGID" \) -print -quit)" ]; then
        chown -R "$PUID:$PGID" "$ABSULLI_DATA_DIR" 2>/dev/null || config_permission_error
    fi
    echo "Running ABSulli using uid=$PUID and gid=$PGID"
else
    echo "Running ABSulli using current user (uid=$(id -u)) and group (gid=$(id -g))"
fi

chmod 700 "$ABSULLI_DATA_DIR" 2>/dev/null || config_permission_error
find "$ABSULLI_DATA_DIR" -maxdepth 1 -type f \( \
    -name 'absulli.db' -o \
    -name 'absulli.db-wal' -o \
    -name 'absulli.db-shm' -o \
    -name 'secret_key' \
\) -exec chmod 600 {} + || config_permission_error

if [ "$#" -ge 1 ] && [ "$1" = "uvicorn" ]; then
    set -- uvicorn absulli.main:app \
        --host "$ABSULLI_HOST" \
        --port "$ABSULLI_PORT" \
        --no-server-header
fi

if [ "$(id -u)" = "0" ]; then
    exec gosu "$PUID:$PGID" "$@"
fi
exec "$@"
