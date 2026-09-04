FROM redis:7-alpine@sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf

# Dev Redis with the repo's tuned config (memory, AOF persistence) and an
# opt-in requirepass via the REDIS_PASSWORD env var (see entrypoint.sh).
# Published as ghcr.io/DCx7C5/tortoise-extended/redis-ext.
COPY redis.conf /usr/local/etc/redis/redis.conf

COPY entrypoint.sh /usr/local/bin/redis-ext-entrypoint.sh
RUN chmod +x /usr/local/bin/redis-ext-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/redis-ext-entrypoint.sh"]

CMD ["redis-server", "/usr/local/etc/redis/redis.conf"]
