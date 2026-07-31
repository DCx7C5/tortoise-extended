# Migrations

Custom migration operations for TimescaleDB.

## Overview

The migrations module provides two custom operations for Tortoise ORM's migration system:

1. **CreateHypertable** — Convert tables to TimescaleDB hypertables
2. **CreateContinuousAggregate** — Create continuous aggregate views

Both serialize through Tortoise's normal migration writer (a monkey-patch
handles their `deconstruct()` output generically), so `aerich` can generate
and apply them like any built-in operation.

## Operations

### CreateHypertable

Convert a table to a TimescaleDB hypertable for time-series data.

```python
from tortoise_extended import CreateHypertable

operations = [
    CreateHypertable(
        table_name="events",
        time_column="created_at",
        chunk_time_interval="7 days",
    ),
]
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `table_name` | `str` | Required | Table to convert |
| `time_column` | `str` | `"created_at"` | Time column for partitioning |
| `chunk_time_interval` | `str` | `"7 days"` | Chunk size interval |
| `migrate_data` | `bool` | `True` | Migrate existing rows |

**Generated SQL:**
```sql
SELECT create_hypertable('events', 'created_at',
                         if_not_exists => TRUE, migrate_data => TRUE);
-- only when chunk_time_interval != '7 days':
ALTER TABLE "events" SET (timescaledb.chunk_time_interval = '7 days');
```

**Rollback:** `SELECT remove_hypertable('events', if_exists => TRUE)`

---

### CreateContinuousAggregate

Create a TimescaleDB continuous aggregate view and attach its refresh policy.

```python
from tortoise_extended import CreateContinuousAggregate

operations = [
    CreateContinuousAggregate(
        view_name="daily_event_stats",
        query="""
            SELECT time_bucket('1 day', created_at) AS bucket,
                   COUNT(*) AS event_count
            FROM events
            GROUP BY 1
        """,
        refresh_interval="1 hour",
    ),
]
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `view_name` | `str` | Required | Materialized view name |
| `query` | `str` | Required | Aggregate SELECT (must group by a `time_bucket(...)` column) |
| `time_column` | `str` | `"time_bucket"` | Bucket column name |
| `refresh_interval` | `str` | `"1 hour"` | Refresh policy interval |

**Generated SQL:**
```sql
CREATE MATERIALIZED VIEW IF NOT EXISTS "daily_event_stats"
WITH (timescaledb.continuous) AS <query>;
SELECT add_continuous_aggregate_policy(
    'daily_event_stats',
    start_offset => INTERVAL '1 hour',
    end_offset => INTERVAL '0',
    schedule_interval => INTERVAL '1 hour');
```

**Rollback:** `DROP MATERIALIZED VIEW IF EXISTS "daily_event_stats"`

## Migration File Format

```python
from tortoise import connections
from tortoise.migrations.operations import RunSQL
from tortoise_extended import CreateHypertable, CreateContinuousAggregate

operations = [
    # Create table
    RunSQL(
        sql="""
            CREATE TABLE events (
                id UUID PRIMARY KEY,
                payload JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """,
        reverse_sql="DROP TABLE events;",
    ),

    # Convert to hypertable
    CreateHypertable(
        table_name="events",
        time_column="created_at",
        chunk_time_interval="7 days",
    ),

    # Create continuous aggregate
    CreateContinuousAggregate(
        view_name="daily_event_stats",
        query="""
            SELECT
                time_bucket('1 day', created_at) AS bucket,
                COUNT(*) AS event_count
            FROM events
            GROUP BY 1
        """,
        refresh_interval="1 hour",
    ),
]
```

## Running Migrations

```bash
# Generate migration
aerich migrate --name add_events

# Apply migration
aerich upgrade
```

## Notes

- TimescaleDB operations require the TimescaleDB extension to be installed
- Continuous aggregates require `time_bucket()` in the aggregate query
- Both operations are idempotent (`IF NOT EXISTS` / `if_exists => TRUE`)
- Test migrations on a copy of production data first
