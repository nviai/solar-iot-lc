#!/bin/sh
# Runs as root: make the bind-mounted data dir writable for the app user,
# then drop privileges and hand over to the real command.
set -e

mkdir -p /app/data
chown -R app:app /app/data

exec setpriv --reuid app --regid app --init-groups "$@"
