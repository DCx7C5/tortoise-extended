"""TimescaleDB event-stream base model with typed, ORM-style query helpers.

Turns any subclass into a multi-stream time-series table:

* **DDL** — :meth:`BaseEventStreamModel.setup` idempotently creates the
  hypertable, adds a space dimension on the stream column, installs the
  composite ``(stream, time DESC)`` index, and optionally wires compression
  and retention policies.
* **Ingestion** — :meth:`BaseEventStreamModel.bulk_insert` loads model
  instances through asyncpg ``COPY`` (3-10x faster than ``bulk_create`` for
  high-rate streams).
* **Queries** — the ORM-style helpers wrap the raw SQL that Tortoise cannot
  express (``DISTINCT ON``, ``time_bucket``, ``first``/``last``):

  - :meth:`BaseEventStreamModel.latest_per_stream` — newest event per stream.
  - :meth:`BaseEventStreamModel.time_series` — typed per-stream rollups.
  - :meth:`BaseEventStreamModel.in_range` — pure-ORM time/stream filter.

Requires: TimescaleDB extension

Note:
    TimescaleDB requires every unique index / primary key to include all
    partitioning columns (time **and** space). Tortoise models only support
    single-column pks, so stream tables must be created with a composite
    primary key via raw DDL / migrations before calling :meth:`setup`::

        CREATE TABLE events (
            id BIGINT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            stream_id INT NOT NULL,
            value DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (id, created_at, stream_id)
        )

Usage::

    import tortoise_extended  # noqa: F401 — apply patches
    from tortoise import fields
    from tortoise_extended.models import BaseEventStreamModel

    class Event(BaseEventStreamModel):
        created_at = fields.DatetimeField(auto_now_add=True, use_tz=True)
        stream_id = fields.IntField()
        value = fields.FloatField()
        token_count = fields.IntField(default=0)

        class Meta:
            table = "events"

    # One-time idempotent DDL after Tortoise.init()
    await Event.setup()

    # High-throughput ingestion (explicit IDs required — COPY cannot use
    # identity defaults)
    await Event.bulk_insert(
        [Event(id=i, stream_id=s, value=0.5) for i, s in ...]
    )

    # Latest event per stream (DISTINCT ON)
    await Event.latest_per_stream(stream_ids=[1, 2])

    # Per-stream hourly averages
    await Event.time_series(
        "1 hour", aggregate="avg", field="value",
        start=now - timedelta(days=1), end=now,
    )

    # Pure-ORM time/stream range
    await Event.in_range(now - timedelta(hours=1), now, stream_ids=[1]).all()
"""

from tortoise_extended.models.sample import BaseSample


class BaseEventStreamModel(BaseSample):
    """Deprecated compatibility alias for :class:`BaseSample`.

    .. deprecated::
        Subclass :class:`~tortoise_extended.models.sample.BaseSample` (BigInt
        identity + stream helpers) or :class:`~tortoise_extended.models.sample.SampleMixin`
        (pk-free) instead.  This alias stays for backwards compatibility and
        emits no runtime warning.

    Abstract base for multi-stream time-series tables.

    Subclasses declare their own fields and **must** set
    ``class Meta: table = "..."``. Configuration is class-level:

    * ``time_field`` — time partition column (default ``"created_at"``).
    * ``stream_field`` — stream/tenant/device partition column (default
      ``"stream_id"``).
    * ``chunk_time_interval`` — hypertable chunk width (default ``"1 day"``).
    * ``number_partitions`` — space dimension partition count on the stream
      column (power of two; default 4; ``None`` disables the dimension).
    * ``compress_after`` — optional compression policy delay (e.g. ``"7
      days"``); ``None`` disables.
    * ``drop_after`` — optional retention policy (e.g. ``"90 days"``);
      ``None`` disables.

    Note:
        The hypertable partition column must be ``NOT NULL`` (Tortoise
        ``DatetimeField(auto_now_add=True)`` already is).
    """

    class Meta:
        abstract = True
