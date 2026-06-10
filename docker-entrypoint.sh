#!/bin/sh
set -eu

ABSULLI_HOST="${ABSULLI_HOST:-0.0.0.0}"
ABSULLI_PORT="${ABSULLI_PORT:-8272}"
ABSULLI_DATA_DIR="${ABSULLI_DATA_DIR:-/config}"
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

mkdir -p "$ABSULLI_DATA_DIR"

if [ "$(id -u)" = "0" ]; then
    groupmod --gid "$PGID" absulli 2>/dev/null || true
    usermod --uid "$PUID" --gid "$PGID" absulli 2>/dev/null || true
    chown -R absulli:absulli "$ABSULLI_DATA_DIR"

    echo "Running ABSulli using user absulli (uid=$(id -u absulli)) and group absulli (gid=$(id -g absulli))"
    USER_PREFIX="gosu absulli"
else
    echo "Running ABSulli using current user (uid=$(id -u)) and group (gid=$(id -g))"
    USER_PREFIX=""
fi

if [ "$#" -ge 1 ] && [ "$1" = "uvicorn" ]; then
    exec $USER_PREFIX uvicorn absulli.main:app \
        --host "$ABSULLI_HOST" \
        --port "$ABSULLI_PORT" \
        --no-server-header
fi

exec $USER_PREFIX "$@"
