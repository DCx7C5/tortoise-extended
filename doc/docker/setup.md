# Docker Setup

## Overview

The Docker image provides a PostgreSQL 18 instance with pgvector and TimescaleDB pre-installed, along with the complete GraphRAG schema.

## Quick Start

```bash
# Build image
cd pypackages/tortoise-extended
docker build -t tortoise-extended-pg .docker/postgres/

# Run container
docker run -d --name graphrag-db -p 5432:5432 tortoise-extended-pg

# Verify
docker exec graphrag-db psql -U postgres -d graphrag -c "\dt"
```

## Building the Image

### From Project Root

```bash
docker build -t tortoise-extended-pg -f .docker/postgres/Dockerfile .docker/postgres/
```

### Build Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `PG_MAJOR` | `18` | PostgreSQL major version |
| `POSTGIS_VERSION` | `3.5` | PostGIS version (if needed) |

### Multi-Stage Build

The Dockerfile uses a multi-stage build:

1. **Builder stage** — Compiles pgvector and TimescaleDB from source
2. **Final stage** — Copies compiled extensions to clean PostgreSQL image

This reduces image size by ~60% compared to single-stage builds.

## Running the Container

### Basic Usage

```bash
docker run -d \
  --name graphrag-db \
  -p 5432:5432 \
  -e POSTGRES_PASSWORD=postgres \
  tortoise-extended-pg
```

### With Persistent Storage

```bash
docker run -d \
  --name graphrag-db \
  -p 5432:5432 \
  -e POSTGRES_PASSWORD=postgres \
  -v graphrag-data:/var/lib/postgresql/data \
  tortoise-extended-pg
```

### With Custom Configuration

```bash
docker run -d \
  --name graphrag-db \
  -p 5432:5432 \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=graphrag \
  -v graphrag-data:/var/lib/postgresql/data \
  -v ./postgresql.conf:/etc/postgresql/postgresql.conf \
  tortoise-extended-pg \
  postgres -c config_file=/etc/postgresql/postgresql.conf
```

## Connecting

### From Host

```bash
psql -h localhost -p 5432 -U postgres -d graphrag
```

### From Python

```python
import tortoise_extended
from tortoise import Tortoise

await Tortoise.init(
    db_url="asyncpg://postgres:postgres@localhost:5432/graphrag",
    modules={"models": ["myapp.models"]},
)
```

### From Docker Network

```bash
# Create network
docker network create graphrag-net

# Run container
docker run -d \
  --name graphrag-db \
  --network graphrag-net \
  -e POSTGRES_PASSWORD=postgres \
  tortoise-extended-pg

# Connect from another container
psql -h graphrag-db -U postgres -d graphrag
```

## Port Mapping

| Container Port | Host Port | Description |
|----------------|-----------|-------------|
| 5432 | 5432 | PostgreSQL (non-default to avoid conflicts) |

**Why port 5432?**

Most developers have a local PostgreSQL instance on port 5432. Using 5432 avoids conflicts.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_PASSWORD` | `postgres` | Superuser password |
| `POSTGRES_DB` | `graphrag` | Default database |
| `POSTGRES_USER` | `postgres` | Superuser name |

## Volumes

| Container Path | Description |
|----------------|-------------|
| `/var/lib/postgresql/data` | PostgreSQL data directory |
| `/docker-entrypoint-initdb.d` | Init scripts (run on first start) |

### Persistent Storage

```bash
# Named volume
docker volume create graphrag-data
docker run -d -v graphrag-data:/var/lib/postgresql/data tortoise-extended-pg

# Bind mount
docker run -d -v /host/path:/var/lib/postgresql/data tortoise-extended-pg
```

## Init Scripts

The container runs init scripts on first start:

```bash
# Scripts in .docker/postgres/scripts/
init.sql      # Schema creation
functions.sql # Retrieval functions
```

### Custom Init Scripts

```bash
# Mount custom scripts
docker run -d \
  -v ./my-scripts:/docker-entrypoint-initdb.d \
  tortoise-extended-pg
```

### Init Script Order

1. `00-extensions.sql` — Install PostgreSQL extensions
2. `01-init.sql` — Create tables and indexes
3. `02-functions.sql` — Create retrieval functions
4. `99-seed.sql` — Optional seed data

## Health Checks

```bash
# Check if PostgreSQL is ready
docker exec graphrag-db pg_isready -U postgres

# Check database connection
docker exec graphrag-db psql -U postgres -d graphrag -c "SELECT 1"

# Check extensions
docker exec graphrag-db psql -U postgres -d graphrag -c "\dx"
```

## Logs

```bash
# View logs
docker logs graphrag-db

# Follow logs
docker logs -f graphrag-db

# View init logs
docker logs graphrag-db 2>&1 | grep "init"
```

## Stopping and Removing

```bash
# Stop container
docker stop graphrag-db

# Remove container
docker rm graphrag-db

# Remove volume
docker volume rm graphrag-data

# Remove everything
docker stop graphrag-db && docker rm graphrag-db && docker volume rm graphrag-data
```

## Production Considerations

### Resource Limits

```bash
docker run -d \
  --name graphrag-db \
  --memory=4g \
  --cpus=2 \
  -p 5432:5432 \
  -e POSTGRES_PASSWORD=postgres \
  -v graphrag-data:/var/lib/postgresql/data \
  tortoise-extended-pg
```

### Backup Strategy

```bash
# Backup database
docker exec graphrag-db pg_dump -U postgres graphrag > backup.sql

# Restore database
cat backup.sql | docker exec -i graphrag-db psql -U postgres graphrag
```

### Monitoring

```bash
# Check stats
docker exec graphrag-db psql -U postgres -d graphrag -c "
SELECT * FROM pg_stat_activity WHERE datname = 'graphrag';
"

# Check table sizes
docker exec graphrag-db psql -U postgres -d graphrag -c "
SELECT pg_size_pretty(pg_total_relation_size('entities'));
"
```

## Troubleshooting

### Container Won't Start

```bash
# Check logs
docker logs graphrag-db

# Check if port is in use
lsof -i :5432

# Check disk space
df -h
```

### Init Script Fails

```bash
# Check init logs
docker logs graphrag-db 2>&1 | grep "ERROR"

# Run init script manually
docker exec -i graphrag-db psql -U postgres graphrag < .docker/postgres/scripts/init.sql
```

### Connection Refused

```bash
# Check if container is running
docker ps | grep graphrag-db

# Check port mapping
docker port graphrag-db

# Test connection
docker exec graphrag-db pg_isready -U postgres
```

### Performance Issues

```bash
# Check resource usage
docker stats graphrag-db

# Check PostgreSQL config
docker exec graphrag-db psql -U postgres -d graphrag -c "SHOW shared_buffers;"
docker exec graphrag-db psql -U postgres -d graphrag -c "SHOW work_mem;"
```

## Development Workflow

### Hot Reload

```bash
# Start with mounted source
docker run -d \
  --name graphrag-db \
  -p 5432:5432 \
  -v ./scripts:/docker-entrypoint-initdb.d \
  tortoise-extended-pg

# Reinitialize database
docker stop graphrag-db
docker rm graphrag-db
docker run -d \
  --name graphrag-db \
  -p 5432:5432 \
  -v ./scripts:/docker-entrypoint-initdb.d \
  tortoise-extended-pg
```

### Testing

```bash
# Run tests against Docker container
docker run -d --name test-db -p 5434:5432 tortoise-extended-pg

# Run tests
DATABASE_URL="asyncpg://postgres:postgres@localhost:5434/graphrag" pytest

# Cleanup
docker stop test-db && docker rm test-db
```
