FROM redis:7-alpine

# Dev Redis with the repo's tuned config (memory, AOF persistence, no protection
# for localhost-only dev). Published as ghcr.io/DCx7C5/tortoise-extended/redis-ext.
COPY redis.conf /usr/local/etc/redis/redis.conf

CMD ["redis-server", "/usr/local/etc/redis/redis.conf"]
