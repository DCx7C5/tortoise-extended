# Docker Configuration

## PostgreSQL Configuration

### Default Settings

The container uses PostgreSQL 18 with optimized defaults for GraphRAG workloads.

### Configuration File

```bash
# Mount custom config
docker run -d \
  -v ./postgresql.conf:/etc/postgresql/postgresql.conf \
  tortoise-extended-pg \
  postgres -c config_file=/etc/postgresql/postgresql.conf
```

### Recommended Settings

```conf
# Memory
shared_buffers = 1GB
effective_cache_size = 3GB
work_mem = 256MB
maintenance_work_mem = 512MB

# Connections
max_connections = 100
superuser_reserved_connections = 3

# Write Ahead Log
wal_buffers = 64MB
min_wal_size = 1GB
max_wal_size = 4GB

# Query Planning
random_page_cost = 1.1
effective_io_concurrency = 200

# Parallel Workers
max_worker_processes = 4
max_parallel_workers_per_gather = 2
max_parallel_workers = 4
max_parallel_maintenance_workers = 2

# TimescaleDB
timescaledb.max_background_workers = 8
```

## Extensions

### Pre-installed Extensions

| Extension | Version | Purpose |
|-----------|---------|---------|
| pgvector | 0.8.5 | Vector search |
| TimescaleDB | 2.21.2 | Time-series data |
| pg_trgm | 1.6 | Trigram similarity |
| uuid-ossp | 1.6 | UUID generation |

### Enable Extensions

```sql
-- pgvector
CREATE EXTENSION IF NOT EXISTS vector;

-- TimescaleDB
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- pg_trgm
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Full-text search
CREATE EXTENSION IF NOT EXISTS unaccent;
```

## Authentication

### Password Authentication

```bash
# Set password
docker run -d \
  -e POSTGRES_PASSWORD=secure_password \
  tortoise-extended-pg

# Connect with password
psql -h localhost -p 5432 -U postgres -d graphrag
```

### Trust Authentication (Development Only)

```conf
# pg_hba.conf
local   all             all                                     trust
host    all             all             127.0.0.1/32            trust
host    all             all             ::1/128                 trust
```

### SSL Authentication

```bash
# Generate certificates
openssl req -new -x509 -days 365 -nodes -text \
  -out server.crt -keyout server-key.pem \
  -subj "/CN=graphrag-db"

# Mount certificates
docker run -d \
  -v ./certs/server.crt:/var/lib/postgresql/server.crt \
  -v ./certs/server-key.pem:/var/lib/postgresql/server-key.pem \
  -e POSTGRES_PASSWORD=postgres \
  tortoise-extended-pg \
  postgres -c ssl=on \
           -c ssl_cert_file=/var/lib/postgresql/server.crt \
           -c ssl_key_file=/var/lib/postgresql/server-key.pem
```

## Networking

### Port Configuration

```bash
# Map to different host port
docker run -d -p 5434:5432 tortoise-extended-pg

# Map to specific interface
docker run -d -p 127.0.0.1:5432:5432 tortoise-extended-pg
```

### Docker Network

```bash
# Create network
docker network create graphrag-net

# Run on network
docker run -d \
  --name graphrag-db \
  --network graphrag-net \
  -e POSTGRES_PASSWORD=postgres \
  tortoise-extended-pg

# Connect from app container
docker run -d \
  --name graphrag-app \
  --network graphrag-net \
  -e DATABASE_URL="asyncpg://postgres:postgres@graphrag-db:5432/graphrag" \
  graphrag-app
```

## Storage

### Volume Types

```bash
# Named volume (recommended)
docker volume create graphrag-data
docker run -d -v graphrag-data:/var/lib/postgresql/data tortoise-extended-pg

# Bind mount
docker run -d -v /host/path:/var/lib/postgresql/data tortoise-extended-pg

# Tempfs (testing only)
docker run -d --tmpfs /var/lib/postgresql/data tortoise-extended-pg
```

### Storage Optimization

```bash
# Use SSD-optimized storage
docker run -d \
  -v graphrag-data:/var/lib/postgresql/data \
  --storage-opt size=50G \
  tortoise-extended-pg
```

## Resource Limits

### Memory

```bash
# Limit memory
docker run -d --memory=4g tortoise-extended-pg

# Memory with swap
docker run -d --memory=4g --memory-swap=8g tortoise-extended-pg
```

### CPU

```bash
# Limit CPU
docker run -d --cpus=2 tortoise-extended-pg

# CPU shares
docker run -d --cpu-shares=512 tortoise-extended-pg
```

### Disk I/O

```bash
# Limit read/write
docker run -d --device-read-bps=/dev/sda:10mb --device-write-bps=/dev/sda:10mb tortoise-extended-pg
```

## Security

### Non-root User

```dockerfile
# In Dockerfile
RUN groupadd -r postgres && useradd -r -g postgres postgres
USER postgres
```

### Read-only Filesystem

```bash
docker run -d --read-only --tmpfs /tmp tortoise-extended-pg
```

### Capabilities

```bash
# Drop all capabilities
docker run -d --cap-drop=ALL --cap-add=NET_BIND_SERVICE tortoise-extended-pg
```

## Monitoring

### Health Checks

```dockerfile
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD pg_isready -U postgres || exit 1
```

### Metrics

```bash
# Enable pg_stat_statements
docker run -d \
  -e POSTGRES_PASSWORD=postgres \
  tortoise-extended-pg \
  postgres -c shared_preload_libraries=pg_stat_statements

# Query stats
docker exec graphrag-db psql -U postgres -d graphrag -c "
SELECT * FROM pg_stat_statements ORDER BY total_time DESC LIMIT 10;
"
```

### Logging

```bash
# Enable query logging
docker run -d \
  -e POSTGRES_PASSWORD=postgres \
  tortoise-extended-pg \
  postgres -c log_statement=all

# View logs
docker logs -f graphrag-db
```

## Backup and Restore

### Backup

```bash
# Dump database
docker exec graphrag-db pg_dump -U postgres graphrag > backup.sql

# Dump with options
docker exec graphrag-db pg_dump -U postgres -Fc graphrag > backup.dump

# Dump from host
pg_dump -h localhost -p 5432 -U postgres graphrag > backup.sql
```

### Restore

```bash
# Restore from dump
cat backup.sql | docker exec -i graphrag-db psql -U postgres graphrag

# Restore custom format
docker exec -i graphrag-db pg_restore -U postgres -d graphrag < backup.dump
```

### Automated Backups

```bash
# Cron job
0 2 * * * docker exec graphrag-db pg_dump -U postgres graphrag | gzip > /backups/graphrag-$(date +\%Y\%m\%d).sql.gz
```

## Development

### Hot Reload

```bash
# Mount source code
docker run -d \
  -v ./src:/app/src \
  -v ./scripts:/docker-entrypoint-initdb.d \
  graphrag-app
```

### Debugging

```bash
# Attach debugger
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres tortoise-extended-pg

# Connect with psql
psql -h localhost -p 5432 -U postgres -d graphrag

# Check queries
SELECT * FROM pg_stat_activity;
```

## Production Checklist

- [ ] Persistent storage configured
- [ ] Resource limits set
- [ ] SSL enabled
- [ ] Backup strategy in place
- [ ] Monitoring configured
- [ ] Logging enabled
- [ ] Health checks configured
- [ ] Network security configured
- [ ] Non-root user configured
- [ ] Read-only filesystem (if possible)
