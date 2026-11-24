FROM postgres:18.7@sha256:eb63372c1e05c755f55fa30cbd07aecc443676fc6c0fa90b9002d01537841e1a AS builder

# Both dependencies are pinned to a full 40-character commit sha — the source
# tree's equivalent of the digest pin on the base image.  The build fails on
# anything that is not a sha (a branch or tag would compile unreviewed code)
# and on a checkout that does not resolve to it.  The human-readable version
# is a comment beside the pin, not a separate ARG: an unused ARG reads like a
# version pin and is not one.
# pgvector 0.8.6 (tag v0.8.6)
ARG PGVECTOR_COMMIT=8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c
# TimescaleDB 2.29.2 (tag 2.29.2)
ARG TIMESCALEDB_COMMIT=165b77b9670b8f13103f9d3da733559d27ca4293

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        curl \
        git \
        libreadline-dev \
        zlib1g-dev \
        libicu-dev \
        pkg-config \
        rustc \
        cargo \
        postgresql-server-dev-18=18.7-1.pgdg13+5 \
    && rm -rf /var/lib/apt/lists/*

# -- pgvector ----------------------------------------------------------------
RUN echo "${PGVECTOR_COMMIT}" | grep -Eq '^[0-9a-f]{40}$' \
    || { echo "PGVECTOR_COMMIT must be a 40-character lowercase hex sha" >&2; exit 1; }

WORKDIR /build/pgvector
RUN git clone https://github.com/pgvector/pgvector.git . \
    && git checkout "${PGVECTOR_COMMIT}" \
    && test "$(git rev-parse HEAD)" = "${PGVECTOR_COMMIT}" \
    && test "$(git describe --tags --exact-match HEAD)" = "v0.8.6"

RUN make PG_CONFIG=/usr/bin/pg_config \
    && make install PG_CONFIG=/usr/bin/pg_config

# -- TimescaleDB -------------------------------------------------------------
RUN echo "${TIMESCALEDB_COMMIT}" | grep -Eq '^[0-9a-f]{40}$' \
    || { echo "TIMESCALEDB_COMMIT must be a 40-character lowercase hex sha" >&2; exit 1; }

WORKDIR /build/timescaledb
RUN git clone https://github.com/timescale/timescaledb.git . \
    && git checkout "${TIMESCALEDB_COMMIT}" \
    && test "$(git rev-parse HEAD)" = "${TIMESCALEDB_COMMIT}" \
    && test "$(git describe --tags --exact-match HEAD)" = "2.29.2"

RUN ./bootstrap -DAPPLY_LIBRARY_TWEAKS=ON -DREGRESS_CHECKS=OFF -DCMAKE_BUILD_TYPE=Release \
    && cd build && make -j$(nproc) && make install

FROM postgres:18.7@sha256:eb63372c1e05c755f55fa30cbd07aecc443676fc6c0fa90b9002d01537841e1a


# Copy compiled extension artifacts from builder
COPY --from=builder /usr/lib/postgresql/18/lib/vector.so      /usr/lib/postgresql/18/lib/
COPY --from=builder /usr/share/postgresql/18/extension/vector*  /usr/share/postgresql/18/extension/

COPY --from=builder /usr/lib/postgresql/18/lib/timescaledb.so       /usr/lib/postgresql/18/lib/
COPY --from=builder /usr/lib/postgresql/18/lib/timescaledb-*.so     /usr/lib/postgresql/18/lib/
COPY --from=builder /usr/share/postgresql/18/extension/timescaledb*  /usr/share/postgresql/18/extension/

COPY scripts/00-extensions.sql /docker-entrypoint-initdb.d/

# Custom entrypoint: generates role/database init SQL from the
# DB_USER_*/DB_PASS_*/DB_NAME_* env groups, then delegates to the official
# postgres entrypoint (which runs all /docker-entrypoint-initdb.d/*.sql).
COPY entrypoint.sh /usr/local/bin/tortoise-entrypoint.sh
RUN chmod +x /usr/local/bin/tortoise-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/tortoise-entrypoint.sh"]

# Pre-load extensions into shared_preload_libraries
# (must be set before first startup — sed on the sample config)
RUN sed -i \
        -e "s/#shared_preload_libraries = ''/shared_preload_libraries = 'timescaledb'/" \
        -e "s/#max_locks_per_transaction = 64/max_locks_per_transaction = 256/" \
    /usr/share/postgresql/18/postgresql.conf.sample


# Healthcheck — probe THIS postmaster, never the host socket and never a
# database that may not exist. Two measured traps, both fixed by the two flags:
#   * the container bind-mounts /var/run/postgresql, which the css-mcp stack
#     also publishes, so a socket-less pg_isready answers for whichever
#     container owns .s.PGSQL.5432 — it can report "accepting" while this
#     postmaster is dead. -h 127.0.0.1 forces the loopback TCP listener.
#   * pg_isready treats an ERROR reply (e.g. "database does not exist") as
#     "accepting", so -d ${POSTGRES_DB} couples liveness to the application
#     database and spams a FATAL line every interval when that DB is absent.
#     -d postgres is the maintenance DB, which always exists.
HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
    CMD pg_isready -h 127.0.0.1 -p 5432 -U ${POSTGRES_USER:-postgres} -d postgres
