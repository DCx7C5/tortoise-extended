# Migrations

Custom migration operations for TimescaleDB and graph retrieval functions.

## Overview

The migrations module provides three custom operations for Tortoise ORM's migration system:

1. **CreateHypertable** — Convert tables to TimescaleDB hypertables
2. **CreateContinuousAggregate** — Create continuous aggregate views
3. **AddRetrievalFunction** — Add SQL functions from functions.sql

## Operations

### CreateHypertable

Convert a table to a TimescaleDB hypertable for time-series data.

```python
from tortoise_extended import CreateHypertable

operations = [
    CreateHypertable(
        table_name="query_cache",
        time_column="created_at",
        chunk_time_interval="7 days",
    ),
]
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `table_name` | `str` | Required | Table to convert |
| `time_column` | `str` | Required | Time column for partitioning |
| `chunk_time_interval` | `str` | `"7 days"` | Chunk size |

**Generated SQL:**
```sql
SELECT create_hypertable('query_cache', 'created_at', 
    chunk_time_interval => INTERVAL '7 days');
```

**Requirements:**
- Table must exist
- Time column must exist
- Table must have no data (or use `migrate_data => true`)

**Example:**
```python
# In migration file
from tortoise.migrations.operations import RunSQL
from tortoise_extended import CreateHypertable

operations = [
    RunSQL(
        sql="CREATE TABLE query_cache (...)",
        reverse_sql="DROP TABLE query_cache;",
    ),
    CreateHypertable(
        table_name="query_cache",
        time_column="created_at",
        chunk_time_interval="7 days",
    ),
]
```

---

### CreateContinuousAggregate

Create a continuous aggregate view for real-time analytics.

```python
from tortoise_extended import CreateContinuousAggregate

operations = [
    CreateContinuousAggregate(
        view_name="daily_entity_stats",
        query="""
            SELECT 
                time_bucket('1 day', created_at) AS bucket,
                type,
                COUNT(*) AS entity_count
            FROM entities
            GROUP BY 1, 2
        """,
        refresh_interval="1 hour",
    ),
]
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `view_name` | `str` | Required | View name |
| `query` | `str` | Required | Aggregate query |
| `refresh_interval` | `str` | `"1 hour"` | Auto-refresh interval |

**Generated SQL:**
```sql
CREATE VIEW daily_entity_stats WITH (timescaledb.continuous) AS
    SELECT 
        time_bucket('1 day', created_at) AS bucket,
        type,
        COUNT(*) AS entity_count
    FROM entities
    GROUP BY 1, 2;

SELECT add_continuous_aggregate_policy('daily_entity_stats',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour');
```

**Requirements:**
- TimescaleDB extension installed
- Query must use `time_bucket()` function
- Query must GROUP BY time bucket

**Example:**
```python
# In migration file
from tortoise_extended import CreateContinuousAggregate

operations = [
    CreateContinuousAggregate(
        view_name="hourly_query_stats",
        query="""
            SELECT 
                time_bucket('1 hour', created_at) AS bucket,
                COUNT(*) AS query_count,
                AVG(hit_count) AS avg_hits
            FROM query_cache
            GROUP BY 1
        """,
        refresh_interval="30 minutes",
    ),
]
```

---

### AddRetrievalFunction

Add a SQL function from the `functions.sql` file.

```python
from tortoise_extended import AddRetrievalFunction

operations = [
    AddRetrievalFunction(
        function_name="local_search",
        sql_file="functions.sql",
    ),
]
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `function_name` | `str` | Required | Function name |
| `sql_file` | `str` | `"functions.sql"` | SQL file path |

**Generated SQL:**
```sql
-- From functions.sql
CREATE OR REPLACE FUNCTION local_search(...)
RETURNS TABLE (...) AS $$
BEGIN
    ...
END;
$$ LANGUAGE plpgsql;
```

**Requirements:**
- `functions.sql` must exist in the package
- Function name must match SQL definition

## Migration File Format

```python
from tortoise import connections
from tortoise.migrations.operations import RunSQL
from tortoise_extended import CreateHypertable, CreateContinuousAggregate

operations = [
    # Create table
    RunSQL(
        sql="""
            CREATE TABLE query_cache (
                id UUID PRIMARY KEY,
                query_hash TEXT NOT NULL UNIQUE,
                query_text TEXT NOT NULL,
                response JSONB NOT NULL,
                hit_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """,
        reverse_sql="DROP TABLE query_cache;",
    ),
    
    # Convert to hypertable
    CreateHypertable(
        table_name="query_cache",
        time_column="created_at",
        chunk_time_interval="7 days",
    ),
    
    # Create continuous aggregate
    CreateContinuousAggregate(
        view_name="daily_cache_stats",
        query="""
            SELECT 
                time_bucket('1 day', created_at) AS bucket,
                COUNT(*) AS query_count,
                AVG(hit_count) AS avg_hits
            FROM query_cache
            GROUP BY 1
        """,
        refresh_interval="1 hour",
    ),
]
```

## Running Migrations

```bash
# Generate migration
aerich migrate --name add_query_cache

# Apply migration
aerich upgrade
```

## Rollback

Each operation supports rollback:

```python
# CreateHypertable rollback
SELECT convert_from_hypertable('query_cache');

# CreateContinuousAggregate rollback
DROP VIEW IF EXISTS daily_cache_stats;

# AddRetrievalFunction rollback
DROP FUNCTION IF EXISTS local_search(...);
```

## Notes

- TimescaleDB operations require the extension to be installed
- Continuous aggregates require `time_bucket()` function
- AddRetrievalFunction loads SQL from the package's `functions.sql`
- All operations are idempotent (safe to run multiple times)
- Test migrations on a copy of production data first
