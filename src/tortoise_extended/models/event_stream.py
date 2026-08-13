# pyright: reportPrivateUsage=false
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

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, Self, cast, override

from tortoise import connections
from tortoise.exceptions import OperationalError
from tortoise.fields.base import DatabaseDefault

from tortoise_extended._quote import quote_ident
from tortoise_extended._types import CoercedValue
from tortoise_extended.models.base import BaseModel
from tortoise_extended.timescale.compression import CompressionManager
from tortoise_extended.timescale.hypertable import HypertableManager
from tortoise_extended.timescale.retention import RetentionPolicy
from tortoise_extended.timescale.stream import (
    Aggregate,
    TimeBucketRow,
    _SQLParam,
    _bucket_datetime,
    _bucket_to_timedelta,
    _fetch_rows,
    _field_db_column,
    _init_from_db,
    _row_to_model_kwargs,
    _table_field,
)

if TYPE_CHECKING:
    from tortoise.queryset import QuerySet


class BaseEventStreamModel(BaseModel):
    """Abstract base for multi-stream time-series tables.

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

    time_field: ClassVar[str] = "created_at"
    stream_field: ClassVar[str] = "stream_id"
    chunk_time_interval: ClassVar[str] = "1 day"
    number_partitions: ClassVar[int | None] = 4
    compress_after: ClassVar[str | None] = None
    drop_after: ClassVar[str | None] = None

    class Meta:
        abstract = True

    @override
    def __str__(self) -> str:
        return f"<{self.__class__.__name__} {self._meta.db_table}>"

    # ── DDL ───────────────────────────────────────────────────────────────

    @classmethod
    async def setup(cls) -> None:
        """Apply idempotent DDL for this stream table.

        Creates the hypertable (if missing), adds the space dimension on the
        stream column (unless already present), installs the composite
        ``(stream, time DESC)`` index, and applies the configured compression
        and retention policies. Safe to call on every application start.

        The table must already exist with a primary key covering the time and
        stream partition columns (see the module docstring) — otherwise
        TimescaleDB rejects the hypertable conversion.
        """
        _ = _table_field(cls, cls.time_field, what="time")
        _ = _table_field(cls, cls.stream_field, what="stream")
        conn = connections.get("default")
        table = cls._meta.db_table

        await HypertableManager.create_hypertable(
            table,
            time_column=cls.time_field,
            chunk_time_interval=cls.chunk_time_interval,
        )

        if cls.number_partitions is not None and not await cls._has_dimension():
            await HypertableManager.add_dimension(
                table,
                column_name=cls.stream_field,
                number_partitions=cls.number_partitions,
            )

        index_name = f"{table}_{cls.stream_field}_{cls.time_field}_idx"
        await conn.execute_query(
            f"CREATE INDEX IF NOT EXISTS {quote_ident(index_name)} "
            f"ON {quote_ident(table)} "
            f"({quote_ident(cls.stream_field)}, {quote_ident(cls.time_field)} DESC)"
        )

        if cls.compress_after is not None:
            if not await cls._is_compression_enabled():
                await CompressionManager.enable_compression(table)
            await CompressionManager.add_compression_policy(
                table,
                compress_after=cls.compress_after,
            )

        if cls.drop_after is not None:
            await RetentionPolicy.set_retention(
                table,
                drop_after=cls.drop_after,
            )

    @classmethod
    async def _has_dimension(cls) -> bool:
        rows = await _fetch_rows(
            "SELECT 1 FROM timescaledb_information.dimensions "
            "WHERE hypertable_name = $1 AND column_name = $2",
            [cls._meta.db_table, cls.stream_field],
        )
        return bool(rows)

    @classmethod
    async def _is_compression_enabled(cls) -> bool:
        rows = await _fetch_rows(
            "SELECT compression_enabled FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = $1",
            [cls._meta.db_table],
        )
        if not rows:
            return False
        value = rows[0].get("compression_enabled")
        return isinstance(value, bool) and value

    # ── Ingestion ─────────────────────────────────────────────────────────

    @classmethod
    def _assert_pk_present(cls, instances: Sequence[Self]) -> None:
        """Raise when any instance lacks an explicit primary key.

        asyncpg ``COPY`` cannot use identity/serial defaults, so every
        instance must carry its id before insertion.

        :param instances: Instances to check.
        :raises ValueError: If any instance's primary key is ``None``.
        """
        pk_attr = cls._meta.pk_attr
        for inst in instances:
            if getattr(inst, pk_attr) is None:
                raise ValueError(
                    "bulk_insert requires explicit primary keys on every "
                    "instance (asyncpg COPY cannot use identity defaults); "
                    "assign ids before calling"
                )

    @classmethod
    def _copy_db_fields(cls) -> list[str]:
        """Return the database columns a COPY statement should target.

        Excludes relation fields (FK, M2M, backward FK) — they are not
        physical columns on the stream table.

        :returns: Column names in declaration order.
        """
        fields_map = cls._meta.fields_map
        skip = (
            set(cls._meta.fk_fields)
            | set(cls._meta.m2m_fields)
            | cls._meta.backward_fk_fields
        )
        return [
            name
            for name in fields_map
            if name in cls._meta.db_fields and name not in skip
        ]

    @classmethod
    def _omit_db_default_fields(
        cls, instances: Sequence[Self], db_fields: list[str]
    ) -> set[str]:
        """Find ``db_default`` columns that COPY must omit for this batch.

        Mirrors Tortoise ``bulk_create``: a column is omitted when *every*
        instance leaves it at the database default, and mixed usage (some
        instances explicit, some defaulted) raises because a single COPY
        statement cannot represent both.

        :param instances: Instances being inserted.
        :param db_fields: Candidate column names (pre-omission).
        :returns: Columns to drop from the COPY statement.
        :raises OperationalError: If a ``db_default`` column has mixed usage.
        """
        fields_map = cls._meta.fields_map
        db_default_fields = [
            name for name in db_fields if fields_map[name].has_db_default()
        ]
        omit_fields: set[str] = set()
        for field_name in db_default_fields:
            has_default = False
            has_value = False
            for inst in instances:
                if isinstance(getattr(inst, field_name), DatabaseDefault):
                    has_default = True
                else:
                    has_value = True
                if has_default and has_value:
                    raise OperationalError(
                        f"Cannot use bulk_insert() when field {field_name!r} "
                        f"has db_default and some instances provide explicit "
                        f"values while others rely on the database default. "
                        f"Either: (a) set the value explicitly on ALL "
                        f"instances, (b) omit it from ALL instances to use "
                        f"the database default, or (c) split into separate "
                        f"bulk_insert() calls."
                    )
            if has_default and not has_value:
                omit_fields.add(field_name)
        return omit_fields

    @classmethod
    def _copy_records(
        cls, instances: Sequence[Self], db_fields: list[str]
    ) -> list[list[CoercedValue]]:
        """Coerce field values to the COPY row layout.

        :param instances: Instances being inserted.
        :param db_fields: Column names in declaration order.
        :returns: Per-instance value rows, one value per column.
        """
        fields_map = cls._meta.fields_map
        return [
            [
                fields_map[name].to_db_value(getattr(inst, name), inst)
                for name in db_fields
            ]
            for inst in instances
        ]

    @classmethod
    async def bulk_insert(cls, instances: Sequence[Self]) -> int:
        """Insert *instances* via a single asyncpg ``COPY`` statement.

        Every instance must have an explicit primary key — ``COPY`` cannot
        use identity/serial defaults. Use plain ``bulk_create`` when IDs are
        database-generated.

        Field defaults are handled the same way as Tortoise's
        ``bulk_create``:

        * ``auto_now`` / ``auto_now_add`` — populated from
          ``DatetimeField.to_db_value`` (the instance is passed through, so a
          missing timestamp becomes ``now``).
        * ``db_default``-only fields — a column is **omitted from COPY** when
          *every* instance leaves it unset (the database applies its
          default); an :class:`~tortoise.exceptions.OperationalError` is
          raised when usage is mixed (some instances set, some not) because a
          single COPY statement cannot represent both.

        Args:
            instances: Model instances to insert.

        Returns:
            Number of rows inserted.

        Raises:
            ValueError: If any instance lacks a primary key.
            OperationalError: If a ``db_default`` field is set on some
                instances but not others.
        """
        if not instances:
            return 0

        cls._assert_pk_present(instances)

        db_fields = cls._copy_db_fields()
        omit_fields = cls._omit_db_default_fields(instances, db_fields)
        db_fields = [name for name in db_fields if name not in omit_fields]

        fields_map = cls._meta.fields_map
        columns = [_field_db_column(fields_map[name], name) for name in db_fields]
        records = cls._copy_records(instances, db_fields)

        conn = connections.get("default")
        async with conn.acquire_connection() as raw:
            _ = await raw.copy_records_to_table(
                cls._meta.db_table,
                columns=columns,
                records=records,
            )
        return len(instances)

    # ── Queries ───────────────────────────────────────────────────────────

    @classmethod
    def in_range(
        cls,
        start: datetime,
        end: datetime,
        *,
        stream_ids: Sequence[int | str] | None = None,
    ) -> QuerySet[Self]:
        """Return a lazy QuerySet for the time range (pure ORM, no raw SQL).

        Args:
            start: Inclusive lower bound on the time column.
            end: Exclusive upper bound on the time column.
            stream_ids: Optional stream partition filter.
        """
        _ = _table_field(cls, cls.time_field, what="time")
        _ = _table_field(cls, cls.stream_field, what="stream")
        filters: dict[str, datetime] = {
            f"{cls.time_field}__gte": start,
            f"{cls.time_field}__lt": end,
        }
        filter_method = cast(Callable[..., "QuerySet[Self]"], cls.filter)
        q = filter_method(**filters)
        if stream_ids is not None:
            stream_filters: dict[str, list[int | str]] = {
                f"{cls.stream_field}__in": list(stream_ids),
            }
            q = q.filter(**stream_filters)
        return q.order_by(f"-{cls.time_field}")

    @classmethod
    async def latest_per_stream(
        cls,
        *,
        stream_ids: Sequence[int | str] | None = None,
        after: datetime | None = None,
        limit: int | None = None,
    ) -> list[Self]:
        """Return the newest event per stream via ``DISTINCT ON``.

        Args:
            stream_ids: Restrict to these streams (all streams when ``None``).
            after: Only events at or after this time.
            limit: Maximum number of rows (applied after ``DISTINCT ON``).

        Returns:
            Model instances — one per stream, newest first.
        """
        _ = _table_field(cls, cls.time_field, what="time")
        _ = _table_field(cls, cls.stream_field, what="stream")

        stream_col = quote_ident(cls.stream_field)
        time_col = quote_ident(cls.time_field)
        table = quote_ident(cls._meta.db_table)

        placeholders: list[_SQLParam] = []
        where: list[str] = []
        if stream_ids is not None:
            if not stream_ids:
                return []
            pos = len(placeholders)
            placeholders.extend(stream_ids)
            where.append(
                f"s.{stream_col} IN ("
                + ", ".join(f"${i + 1}" for i in range(pos, len(placeholders)))
                + ")"
            )
        if after is not None:
            placeholders.append(after)
            where.append(f"s.{time_col} >= ${len(placeholders)}")

        sql = f"SELECT DISTINCT ON (s.{stream_col}) s.* FROM {table} s"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY s.{stream_col}, s.{time_col} DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"

        rows = await _fetch_rows(sql, placeholders)
        return [
            cast(Self, _init_from_db(cls, _row_to_model_kwargs(cls, row)))
            for row in rows
        ]

    @classmethod
    async def time_series(
        cls,
        bucket: str,
        *,
        aggregate: Aggregate = "count",
        field: str | None = None,
        start: datetime,
        end: datetime,
        stream_ids: Sequence[int | str] | None = None,
    ) -> list[TimeBucketRow]:
        """Return typed per-stream rollups via ``time_bucket``.

        Args:
            bucket: Bucket width (e.g. ``"1 hour"``, ``"30 minutes"``,
                ``"1 day"``). ``month``/``year`` widths are not supported —
                they are variable-length and cannot be bound as a fixed
                interval.
            aggregate: One of ``count``, ``avg``, ``sum``, ``min``, ``max``,
                ``first``, ``last``.
            field: Value column for every aggregate except ``count``.
            start: Inclusive lower bound on the time column.
            end: Exclusive upper bound on the time column.
            stream_ids: Restrict to these streams (all streams when ``None``).

        Returns:
            One :class:`TimeBucketRow` per (stream, bucket), ordered by
            stream then bucket ascending.
        """
        _ = _table_field(cls, cls.time_field, what="time")
        _ = _table_field(cls, cls.stream_field, what="stream")
        if aggregate != "count":
            if field is None:
                raise ValueError(f"aggregate={aggregate!r} requires a value field")
            _ = _table_field(cls, field, what="aggregate")

        stream_col = quote_ident(cls.stream_field)
        time_col = quote_ident(cls.time_field)
        table = quote_ident(cls._meta.db_table)
        value_col = quote_ident(field) if field is not None else None

        placeholders: list[_SQLParam] = [
            _bucket_to_timedelta(bucket),
            start,
            end,
        ]
        where = [
            f"s.{time_col} >= $2",
            f"s.{time_col} < $3",
        ]
        if stream_ids is not None:
            if not stream_ids:
                return []
            pos = len(placeholders)
            placeholders.extend(stream_ids)
            where.append(
                f"s.{stream_col} IN ("
                + ", ".join(f"${i + 1}" for i in range(pos, len(placeholders)))
                + ")"
            )

        if aggregate == "count":
            value_sql = "COUNT(*) AS value"
        elif aggregate in ("first", "last"):
            value_sql = f"{aggregate}(s.{value_col}, s.{time_col}) AS value"
        else:
            value_sql = f"{aggregate}(s.{value_col}) AS value"

        sql = f"""
            SELECT
                time_bucket($1::interval, s.{time_col}) AS bucket,
                s.{stream_col} AS stream_id,
                {value_sql},
                COUNT(*) AS count
            FROM {table} s
            WHERE {" AND ".join(where)}
            GROUP BY bucket, s.{stream_col}
            ORDER BY s.{stream_col}, bucket
        """

        rows = await _fetch_rows(sql, placeholders)
        result: list[TimeBucketRow] = []
        for row in rows:
            bucket_value = row.get("bucket")
            stream_value = row.get("stream_id")
            value_value = row.get("value")
            count_value = row.get("count")
            result.append(
                TimeBucketRow(
                    stream_id=cast(int | str, stream_value),
                    bucket=_bucket_datetime(bucket_value),
                    value=cast(float | None, value_value),
                    count=cast(int, count_value),
                )
            )
        return result
