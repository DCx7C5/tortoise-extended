FROM redis:7-alpine@sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf

# Dev Redis with the repo's tuned config (memory, AOF persistence, no protection
# for localhost-only dev). Published as ghcr.io/DCx7C5/tortoise-extended/redis-ext.
COPY redis.conf /usr/local/etc/redis/redis.conf

CMD ["redis-server", "/usr/local/etc/redis/redis.conf"]
