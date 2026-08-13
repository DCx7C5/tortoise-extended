"""TimescaleDB stream-table typed helpers and result containers.

Non-model utilities shared by the event-stream base model
(:class:`tortoise_extended.models.event_stream.BaseEventStreamModel`):
typed result rows (``TimeBucketRow``), bucket-width parsing, raw row
fetching, and model-field plumbing.

The model itself lives in ``tortoise_extended.models.event_stream``; this
module intentionally has no imports from ``tortoise_extended.models`` so no
import cycle is possible.

Usage::

    from tortoise_extended.timescale.stream import Aggregate, TimeBucketRow

Requires: TimescaleDB extension
"""

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Literal, TypeAlias, cast

import msgspec
from tortoise import connections, fields
from tortoise.models import Model

from tortoise_extended._types import RowMapping, RowValue

# ── Typed result containers ────────────────────────────────────────────────

Aggregate = Literal["count", "avg", "sum", "min", "max", "first", "last"]

_BUCKET_UNITS: dict[str, Callable[[int], timedelta]] = {
    "microsecond": lambda n: timedelta(microseconds=n),
    "millisecond": lambda n: timedelta(milliseconds=n),
    "second": lambda n: timedelta(seconds=n),
    "minute": lambda n: timedelta(minutes=n),
    "hour": lambda n: timedelta(hours=n),
    "day": lambda n: timedelta(days=n),
    "week": lambda n: timedelta(weeks=n),
}

_SQLParam: TypeAlias = timedelta | datetime | int | str
"""A value that can be bound as an asyncpg query parameter."""


def _bucket_to_timedelta(bucket: str) -> timedelta:
    """Parse a ``time_bucket`` width like ``"1 hour"`` into a fixed interval.

    TimescaleDB also accepts ``"1 month"`` / ``"1 year"``, but those are
    variable-length so asyncpg cannot bind them as a fixed interval —
    they are rejected with a clear error.
    """
    parts = bucket.strip().split()
    if len(parts) != 2:
        raise ValueError(
            f"Invalid bucket {bucket!r}; expected '<count> <unit>' e.g. '1 hour'"
        )
    try:
        count = int(parts[0])
    except ValueError:
        raise ValueError(
            f"Invalid bucket {bucket!r}; count must be an integer"
        ) from None
    unit = parts[1].rstrip("s")
    converter = _BUCKET_UNITS.get(unit)
    if converter is None:
        raise ValueError(
            f"Unsupported bucket unit {parts[1]!r}; supported units: "
            + ", ".join(sorted(_BUCKET_UNITS))
        )
    return converter(count)


class TimeBucketRow(msgspec.Struct):
    """One per-stream time bucket returned by
    :meth:`BaseEventStreamModel.time_series`.

    Attributes:
        stream_id: The stream partition value.
        bucket: Bucket start time (``time_bucket`` truncates to the width).
        value: Aggregated value — ``count`` returns the row count, all other
            aggregates operate on *field*. ``None`` for empty buckets.
        count: Number of raw rows in this bucket.
    """

    stream_id: int | str
    bucket: datetime
    value: float | None
    count: int


# ── Internal helpers ───────────────────────────────────────────────────────


def _field_db_column(field: fields.Field[RowValue], fallback: str) -> str:
    """Resolve a field's physical DB column name."""
    return field.source_field or fallback


def _table_field(cls: type[Model], field_name: str, *, what: str) -> str:
    """Validate that *field_name* exists on the model and return it."""
    if field_name not in cls._meta.fields_map:
        raise ValueError(
            f"{what} field {field_name!r} is not declared on "
            f"{cls.__name__}; available: {', '.join(sorted(cls._meta.fields_map))}"
        )
    return field_name


def _row_to_model_kwargs(
    cls: type[Model],
    row: RowMapping,
) -> dict[str, RowValue]:
    """Map raw DB columns to model-field kwargs (mirrors ``_init_from_db``)."""
    kwargs: dict[str, RowValue] = {}
    for field_name, field in cls._meta.fields_map.items():
        db_column = _field_db_column(field, field_name)
        if db_column in row:
            kwargs[field_name] = row[db_column]
    return kwargs


def _init_from_db(cls: type[Model], kwargs: dict[str, RowValue]) -> Model:
    init = cast(Callable[..., Model], getattr(cls, "_init_from_db"))
    return init(**kwargs)


def _bucket_datetime(value: RowValue) -> datetime:
    """Coerce a ``time_bucket`` result to a tz-aware datetime.

    asyncpg decodes PostgreSQL ``timestamptz`` natively to ``datetime``, so
    the common path passes through unchanged; ISO-8601 strings are parsed as
    a defensive fallback for drivers that return text.
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError(f"time_bucket returned {value!r}; expected a timestamp")


async def _fetch_rows(
    sql: str,
    values: Sequence[_SQLParam] | None = None,
) -> list[RowMapping]:
    """Run raw SQL and return rows as mappings."""
    conn = connections.get("default")
    result = await conn.execute_query(sql, list(values) if values else None)
    rows = result[1]
    return [dict(row) for row in rows]


# ── Public aliases for the event-stream model ─────────────────────────────
#
# ``tortoise_extended.models.event_stream`` consumes these helpers; importing
# the private names across modules would trip basedpyright's
# ``reportPrivateUsage``.  The underscore implementations stay here so the
# private names remain available for direct use/tests.

bucket_to_timedelta = _bucket_to_timedelta
bucket_datetime = _bucket_datetime
fetch_rows = _fetch_rows
field_db_column = _field_db_column
init_from_db = _init_from_db
row_to_model_kwargs = _row_to_model_kwargs
table_field = _table_field
SQLParam = _SQLParam


__all__ = [
    "Aggregate",
    "TimeBucketRow",
    "bucket_datetime",
    "bucket_to_timedelta",
    "fetch_rows",
    "field_db_column",
    "init_from_db",
    "row_to_model_kwargs",
    "table_field",
]
