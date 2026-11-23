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
| `create_hypertable(table, time_column?, chunk_time_interval?, if_not_exists?, migrate_data?, validate_time_column?)` | Convert table to hypertable |
| `drop_hypertable(table, if_exists?)` | Drop hypertable — removes its retention/compression policy jobs first |
| `is_hypertable(table)` | Check if table is a hypertable |
| `list_hypertables(extension_schema?)` | List all hypertables |
| `add_dimension(table, column, chunk_time_interval?, number_partitions?)` | Add partitioning dimension |
| `show_chunks(table, start_time?, end_time?)` | List chunks for a hypertable |

```python
await HypertableManager.create_hypertable(
    "events",
    time_column="created_at",
    chunk_time_interval="7 days",
)
```

``list_hypertables`` returns a list of ``HypertableInfo`` structs with
``table_name``, ``num_chunks``, ``compression_enabled`` and ``table_size``
attributes (accessed with attribute syntax, not dict subscripting):

```python
hypertables = await HypertableManager.list_hypertables()
for ht in hypertables:
    print(f"{ht.table_name}: {ht.num_chunks} chunks")
```

**Dropping a hypertable also removes its policy jobs.**
Retention and columnstore (compression) policies are TimescaleDB scheduler
*jobs*, not table constraints, so `DROP TABLE ... CASCADE` alone would leave
them registered and they would keep failing against a hypertable that no
longer exists. `drop_hypertable()` deletes every job whose
`timescaledb_information.jobs.hypertable_name` (and schema, when the name is
schema-qualified) matches the table before dropping it; a plain table or a
policy-less hypertable matches no rows, so the cleanup is a no-op for them.

**Time-column pre-check.** TimescaleDB requires the partitioning (time) column
to be part of the table's ``PRIMARY KEY`` or a ``UNIQUE`` index, otherwise
``create_hypertable()`` fails with a cryptic server error. By default
``create_hypertable`` runs a cheap introspection query and raises a clear
``TimescaleError`` before attempting the conversion when the time column is
not covered. Pass ``validate_time_column=False`` to skip the pre-check (e.g.
for unusual schemas the introspection can't model, or to avoid the extra
introspection query). If the introspection itself fails, it is ignored and
the hypertable is still attempted.

---

## CompressionManager

Enable and manage compression on hypertables.

| Method | Description |
|--------|-------------|
| `enable_compression(table)` | Enable compression (columnstore reloption) |
| `disable_compression(table)` | Disable compression |
| `add_compression_policy(table, compress_after="7 days")` | Auto-compress old chunks |
| `remove_compression_policy(table)` | Remove auto-compress policy |
| `compress_chunk(chunk_name)` | Manually compress a chunk |
| `decompress_chunk(chunk_name)` | Decompress a chunk |
| `get_stats(table)` | Get compression statistics |

```python
await CompressionManager.enable_compression("events")
await CompressionManager.add_compression_policy("events", compress_after="7 days")
stats = await CompressionManager.get_stats("events")
print(f"Compression ratio: {stats.compression_ratio}")
print(f"Uncompressed chunks: {stats.uncompressed_chunks}")
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

``list_policies`` returns a list of ``RetentionPolicyInfo`` structs with
``table_name``, ``drop_after`` (a dict such as ``{"drop_after": "90 days"}``),
``schedule_interval``, ``initial_start`` and ``scheduled`` attributes
(accessed with attribute syntax, not dict subscripting):

```python
policies = await RetentionPolicy.list_policies()
for policy in policies:
    print(f"{policy.table_name}: keep {policy.drop_after}")
```

---

## ContinuousAggregateManager

Manage materialized views that auto-refresh.

| Method | Description |
|--------|-------------|
| `create(view_name, source_table, query, with_data?)` | Create continuous aggregate |
| `drop(view_name, if_exists?)` | Drop continuous aggregate |
| `refresh(view_name, start_time?, end_time?)` | Refresh aggregate |
| `set_refresh_policy(view, start_offset?, end_offset?, schedule_interval?)` | Auto-refresh policy |
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

``list`` returns a list of ``ContinuousAggregateInfo`` structs with
``view_name`` and ``view_definition`` attributes (accessed with attribute
syntax, not dict subscripting):

```python
aggregates = await ContinuousAggregateManager.list()
for agg in aggregates:
    print(f"{agg.view_name}: {agg.view_definition}")
```

## Stream helpers (`SampleMixin`)

`SampleMixin` (and its identity base `BaseSample`) carries four ergonomic
classmethods for the single-row / single-stream cases. They are documented in
full on [Models → SampleMixin](models.md#samplemixin); the signatures:

| Method | Returns | Purpose |
|---|---|---|
| `record(*, stream=None, at=None, **fields)` | `Self` | Create one row, mapping the `stream` / `at` shorthands onto the partition columns |
| `latest_for_stream(stream)` | `Self \| None` | Newest row for one stream |
| `downsample(interval, *, agg="avg", field=None, stream=None, start=None, end=None)` | `list[TimeBucketRow]` | `time_bucket` rollup over the whole series |
| `gapfill(interval, *, method="locf", field, stream=None, start=None, end=None)` | `list[TimeBucketRow]` | `time_bucket_gapfill` — every bucket in the range, gaps filled or left NULL |

`record()` raises `TimescaleError` when the time column carries no default and
`at` is omitted; `gapfill()` raises when `start` / `end` are missing, `end <=
start`, or `field` is not a numeric column. `Model.latest(*orderings)` is still
the ORM helper — the stream lookup is `latest_for_stream()`.

```python
from tortoise_extended import BaseSample

buckets = await Event.gapfill(
    "1 hour", method="locf", field="value",
    stream=7, start=t0, end=t0 + timedelta(hours=4),
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
