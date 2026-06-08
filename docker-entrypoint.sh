#!/usr/bin/env sh
set -eu

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
CONFIG_DIR="${ABSULLI_CONFIG_DIR:-/config}"

mkdir -p "$CONFIG_DIR"

# Match the internal app user to the requested host UID/GID.
groupmod -o -g "$PGID" absulli
usermod -o -u "$PUID" -g "$PGID" absulli

# Make bind-mounted config folders writable on first run.
# This lets Docker create the host folder as root, then the entrypoint fixes it.
if [ -d "$CONFIG_DIR" ]; then
    find "$CONFIG_DIR" \! \( -uid "$(id -u absulli)" -gid "$(id -g absulli)" \) -print0 | xargs -0r chown absulli:absulli
fi

echo "Running ABSulli using user absulli (uid=$(id -u absulli)) and group absulli (gid=$(id -g absulli))"

exec gosu absulli "$@"
