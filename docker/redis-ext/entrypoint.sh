#!/bin/sh
# entrypoint.sh — Start redis-server with mandatory AUTH.
#
# REDIS_PASSWORD is required. A dev container that accepts unauthenticated
# commands is a file-write pivot, not a convenience: any process that can reach
# it can `CONFIG SET dir <path>` + `SAVE` and write a file as the redis user.
# Publishing the port on 127.0.0.1 narrows *who* can reach it; it does not
# narrow what an unauthenticated client can do once it has.
#
# Usage (compose):
#   environment:
#     REDIS_PASSWORD: ${REDIS_PASSWORD:?set it in the project env file}   # non-empty
#
# The live cache tests read the same variable to build their URL
# (tests/_redis_env.py; REDIS_HOST / REDIS_PORT default to localhost:6381).
#
# This file is committed intentionally — it contains no secrets.

set -eu

if [ -z "${REDIS_PASSWORD:-}" ]; then
  echo "redis-ext: REDIS_PASSWORD is required — set it in .env (see .env.example)." >&2
  echo "An unauthenticated Redis accepts CONFIG SET dir + SAVE, i.e. arbitrary file writes." >&2
  exit 1
fi

exec redis-server /usr/local/etc/redis/redis.conf --requirepass "$REDIS_PASSWORD"
