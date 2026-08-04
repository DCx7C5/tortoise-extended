# Event Streams (TimescaleDB)

`EventStreamMixin` turns any Tortoise model into a multi-stream time-series
table: hypertable + optional stream dimension, a composite
`(stream, time DESC)` index, compression/retention policies, COPY-based
bulk ingestion, and typed ORM-style query helpers that wrap the raw SQL
Tortoise cannot express (`DISTINCT ON`, `time_bucket`, `first`/`last`).

Requires PostgreSQL + TimescaleDB.

## Quick Reference

```python
from tortoise_extended import EventStreamMixin, TimeBucketRow
```

## Model

```python
import tortoise_extended  # noqa: F401 — apply patches
from tortoise import fields
from tortoise_extended import EventStreamMixin


class Event(EventStreamMixin):
    id = fields.BigIntField(primary_key=True)
    created_at = fields.DatetimeField(use_tz=True)
    stream_id = fields.IntField()
    value = fields.FloatField()
    token_count = fields.IntField(default=0)

    class Meta:
        table = "events"
```

Configuration is class-level:

| Attribute | Default | Meaning |
|-----------|---------|---------|
| `time_field` | `"created_at"` | time partition column |
| `stream_field` | `"stream_id"` | stream/tenant/device partition column |
| `chunk_time_interval` | `"1 day"` | hypertable chunk width |
| `number_partitions` | `4` | space dimension count (power of two; `None` disables) |
| `compress_after` | `None` | optional compression policy delay (e.g. `"7 days"`) |
| `drop_after` | `None` | optional retention policy (e.g. `"90 days"`) |

## DDL — the composite PK requirement

TimescaleDB requires **every unique index / primary key to include all
partitioning columns** (time *and* space). Tortoise only supports
single-column pks, so create the table with a composite pk via raw DDL or a
migration, then call `setup()`:

```sql
CREATE TABLE events (
    id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    stream_id INT NOT NULL,
    value DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (id, created_at, stream_id)
);
```

```python
# Idempotent — safe on every startup
await Event.setup()
```

## Ingestion — COPY

```python
await Event.bulk_insert(
    [
        Event(id=i, stream_id=stream, value=0.5)
        for i, stream in enumerate(range(4), start=1)
    ]
)
```

`bulk_insert` loads rows through asyncpg `COPY` (3–10× faster than
`bulk_create`). It requires **explicit primary keys** on every instance —
COPY cannot use identity defaults; use `bulk_create` when IDs are
database-generated.

## Queries

### Latest event per stream (`DISTINCT ON`)

```python
latest = await Event.latest_per_stream(stream_ids=[1, 2, 3])
# → list[Event], newest per stream, streams ordered ascending
```

Optional `after` (datetime) and `limit` (int) arguments apply the filter
and cap the result after the `DISTINCT ON`.

### Per-stream rollups (`time_bucket`)

```python
rows = await Event.time_series(
    "1 hour",
    aggregate="avg",
    field="value",
    start=now - timedelta(days=1),
    end=now,
    stream_ids=[1],
)
# → list[TimeBucketRow(stream_id, bucket, value, count)]
```

Aggregates: `count`, `avg`, `sum`, `min`, `max`, `first`, `last`
(`first`/`last` use the TimescaleDB ordered aggregates and require the time
column). Bucket widths support `<count> <unit>` — seconds, minutes, hours,
days, weeks. `month`/`year` are rejected (variable-length intervals cannot
be bound as a fixed parameter).

### Pure-ORM range

```python
rows = await Event.in_range(start, end, stream_ids=[1]).all()
```

A plain Tortoise QuerySet (`time >= start AND time < end`, newest first)
for cases that do not need raw SQL.
