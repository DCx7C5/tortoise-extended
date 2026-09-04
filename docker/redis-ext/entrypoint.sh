#!/bin/sh
# entrypoint.sh — Start redis-server with optional requirepass.
#
# If REDIS_PASSWORD is set in the environment, redis-server is started with
# --requirepass so that clients must authenticate.  If the variable is unset
# or empty, no password is required (the previous dev default).
#
# Usage (compose):
#   environment:
#     REDIS_PASSWORD: ${REDIS_PASSWORD:-}
#
# This file is committed intentionally — it contains no secrets.

set -eu

REDIS_ARGS="/usr/local/etc/redis/redis.conf"

if [ -n "${REDIS_PASSWORD:-}" ]; then
  exec redis-server $REDIS_ARGS --requirepass "$REDIS_PASSWORD"
else
  exec redis-server $REDIS_ARGS
fi
