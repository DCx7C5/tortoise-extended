#!/usr/bin/env bash

set -o errexit
set -o nounset

# Optional requirepass for the dev Redis. When REDIS_PASSWORD is set, the
# server requires AUTH for every connection (defense-in-depth even though the
# host binding is 127.0.0.1:6380). Never commit the secret itself — export it
# through the docker-compose .env file (REDIS_PASSWORD=...).
#
# Uses an entrypoint (instead of baking a hardcoded requirepass into
# redis.conf) so no password is committed to the repository and the same image
# can run both with and without auth.
CONF="/usr/local/etc/redis/redis.conf"

if [ -n "${REDIS_PASSWORD:-}" ]; then
    exec redis-server "$CONF" --requirepass "$REDIS_PASSWORD"
else
    exec redis-server "$CONF"
fi
