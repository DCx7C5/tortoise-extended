# TimescaleDB

Managers for TimescaleDB hypertables, compression, retention, and continuous aggregates.

## Import

```python
from tortoise_extended.timescale import (
    HypertableManager,
    CompressionManager,
    RetentionPolicy,
    ContinuousAggregateManager,
)
```

---

## HypertableManager

Convert regular tables to TimescaleDB hypertables.

| Method | Description |
|--------|-------------|
| `create_hypertable(table, time_column?, chunk_interval?, if_not_exists?, migrate_data?)` | Convert table to hypertable |
| `drop_hypertable(table, if_exists?)` | Drop hypertable |
| `is_hypertable(table)` | Check if table is a hypertable |
| `list_hypertables()` | List all hypertables |
| `add_dimension(table, column, chunk_interval?)` | Add partitioning dimension |
| `show_chunks(table, start_time?, end_time?)` | List chunks for a hypertable |

```python
await HypertableManager.create_hypertable(
    "events",
    time_column="created_at",
    chunk_time_interval="7 days",
)
```

---

## CompressionManager

Enable and manage compression on hypertables.

| Method | Description |
|--------|-------------|
| `enable_compression(table, compress_after?)` | Enable compression |
| `disable_compression(table)` | Disable compression |
| `add_compression_policy(table, compress_after?)` | Auto-compress old chunks |
| `remove_compression_policy(table)` | Remove auto-compress policy |
| `compress_chunk(chunk_name)` | Manually compress a chunk |
| `decompress_chunk(chunk_name)` | Decompress a chunk |
| `get_stats(table)` | Get compression statistics |

```python
await CompressionManager.enable_compression("events", compress_after="7 days")
stats = await CompressionManager.get_stats("events")
```

---

## RetentionPolicy

Automatic data retention (drop old chunks).

| Method | Description |
|--------|-------------|
| `set_retention(table, drop_after?)` | Set retention policy |
| `remove_retention(table)` | Remove retention policy |
| `list_policies()` | List all retention policies |
| `get_chunks_to_drop(table, older_than?)` | Preview chunks to drop |

```python
await RetentionPolicy.set_retention("events", drop_after="90 days")
chunks = await RetentionPolicy.get_chunks_to_drop("events", older_than="90 days")
```

---

## ContinuousAggregateManager

Manage materialized views that auto-refresh.

| Method | Description |
|--------|-------------|
| `create(view_name, source_table, query, with_data?)` | Create continuous aggregate |
| `drop(view_name, if_exists?)` | Drop continuous aggregate |
| `refresh(view_name, start_time?, end_time?)` | Refresh aggregate |
| `set_refresh_policy(view, start_offset?, end_offset?, schedule?)` | Auto-refresh policy |
| `remove_refresh_policy(view)` | Remove refresh policy |
| `add_realtime_aggregate(view)` | Enable realtime aggregation |
| `list()` | List all continuous aggregates |

```python
await ContinuousAggregateManager.create(
    "daily_events",
    "events",
    "SELECT time_bucket('1 day', created_at) AS bucket, "
    "COUNT(*) AS count FROM events GROUP BY bucket",
)

await ContinuousAggregateManager.set_refresh_policy(
    "daily_events",
    start_offset="1 hour",
    schedule_interval="1 hour",
)
```

## Requirements

```sql
CREATE EXTENSION IF NOT EXISTS timescaledb;
```

## Notes

- All managers use `connections.get("default")` — ensure Tortoise ORM is initialized
- Hypertable conversion requires an empty table or `migrate_data=True`
- Continuous aggregate queries must use `time_bucket()` function
- Use `migrations/operations.py` for Aerich migration integration
