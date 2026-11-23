FROM postgres:18.7@sha256:eb63372c1e05c755f55fa30cbd07aecc443676fc6c0fa90b9002d01537841e1a AS builder

ARG PGVECTOR_VERSION=0.8.6
ARG PGVECTOR_COMMIT=159b79aaad5983fb7459c1e3df2897fbb2d11788
ARG TIMESCALEDB_VERSION=2.29.2
ARG TIMESCALEDB_COMMIT=4a77ff5cf1bf4348c4f0527e042434a289f06e39

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
WORKDIR /build/pgvector
RUN git clone https://github.com/pgvector/pgvector.git . \
    && git checkout ${PGVECTOR_COMMIT}

ENV OPTFLAGS=""
RUN make PG_CONFIG=/usr/bin/pg_config \
    && make install PG_CONFIG=/usr/bin/pg_config

# -- TimescaleDB -------------------------------------------------------------
WORKDIR /build/timescaledb
RUN git clone https://github.com/timescale/timescaledb.git . \
    && git checkout ${TIMESCALEDB_COMMIT}

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


# Healthcheck — wait for accepting connections
HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
    CMD pg_isready -U ${POSTGRES_USER:-postgres} -d ${POSTGRES_DB:-postgres}
