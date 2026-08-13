"""Shared type helpers for ``tortoise-extended``.

``Any`` is banned in runtime code, along with ``object`` and aliases of them
(the former ``LibraryAny`` alias is REMOVED). Runtime annotations use concrete
unions, recursive unions, or ``TypedDict``s only; ``cast()`` to concrete types
is allowed and encouraged at boundary sites where upstream signatures are
``Any``-typed.

NOTE (verified 2026-07-31): the old ``reportExplicitAny`` guidance is gone —
``LibraryAny`` no longer exists, so no ``# pyright: ignore[reportExplicitAny]``
comments are needed or permitted.

The module also hosts the shared ``ParamSpec`` / ``TypeVar`` instances and
``TypeAlias``es that warning-cleanup tasks import instead of re-declaring
them locally (see the FOUNDATION backlog).

Protocols in this module describe *partial* surfaces of upstream Tortoise
classes. They exist so code can call private upstream methods (e.g. ``_run_sql``)
without ``reportPrivateUsage`` — the patched object is ``cast()`` to the
protocol instead of annotated with the upstream class. Signatures mirror the
installed ``tortoise`` runtime exactly (``tortoise/schema_quoting.py``,
``tortoise/backends/base/schema_generator.py``,
``tortoise/migrations/schema_editor/base.py``, ``tortoise/migrations/writer.py``).

NOTE (verified 2026-07-31): ``cast()`` to a Protocol does NOT silence
``reportPrivateUsage`` — pyright checks protected-member access against the
declaring class, so private-method calls on patched objects must go through
``getattr(obj, "_name")`` instead (see ``migrations/operations.py``).
"""

from collections.abc import Callable, Sequence
from datetime import date, datetime, time
from typing import ParamSpec, Protocol, TypeAlias, TypedDict, TypeVar

from tortoise.models import Model

P = ParamSpec("P")
"""Bare ``ParamSpec`` for decorator signatures that forward arbitrary callables."""

R = TypeVar("R")
"""Bare ``TypeVar`` for decorator return types that preserve the wrapped callable."""

T = TypeVar("T")
"""Bare ``TypeVar`` for generic helpers (e.g. ``Model`` subclasses)."""

RowValue: TypeAlias = str | int | float | bool | bytes | None
"""A single SQL result value (PostgreSQL wire types)."""

CoercedValue: TypeAlias = RowValue | datetime | date | time
"""A cache value after field-type coercion (JSON str restored to datetime/date/time)."""

RowMapping: TypeAlias = dict[str, RowValue]
"""A raw SQL result row."""

SerializedRecord: TypeAlias = dict[str, RowValue]
"""A serialized model record as stored in the cache backend."""

ModelKwargs: TypeAlias = dict[str, CoercedValue]
"""Keyword arguments accepted by Model.create / Model.construct mirrors."""

CacheValue: TypeAlias = RowValue | list["CacheValue"] | dict[str, "CacheValue"]
"""A value storable in a cache backend (JSON-serializable shape)."""

FieldDefaultValue: TypeAlias = (
    str | int | float | bool | bytes | list["FieldDefaultValue"] | None
)
"""A field default that is not a callable."""


class FieldInitKwargs(TypedDict, total=False):
    """Concrete kwargs forwarded to ``Field.__init__`` (mirrors upstream names).

    ``null`` / ``default`` / ``description`` are intentionally absent: the
    field constructors declare them as explicit keyword-only parameters and
    forward them directly, so they never flow through ``**kwargs``.
    """

    source_field: str
    generated: bool
    primary_key: bool
    db_default: str | int | float | bool | None
    unique: bool
    db_index: bool
    model: type[Model] | None


class Deconstructable(Protocol):
    """Protocol for Tortoise migration operations that implement ``deconstruct()``."""

    def deconstruct(
        self,
    ) -> tuple[str, tuple[()], dict[str, str | int | float | bool | None]]: ...


class AsyncpgConnection(Protocol):
    """Minimal surface of an asyncpg ``Connection`` used by the codec init.

    Mirrors exactly what ``_pgvector_codec_init`` calls: ``set_type_codec``
    to register the pgvector encoder/decoder and ``fetchval`` to probe the
    extension schema from the catalog.  Real asyncpg connections satisfy the
    protocol structurally; duck-typed test doubles implement the same calls.
    """

    async def set_type_codec(
        self,
        typename: str,
        *,
        schema: str,
        encoder: Callable[[list[float] | str | None], str],
        decoder: Callable[[str], list[float]],
    ) -> None: ...
    async def fetchval(self, query: str) -> str | None: ...


class SchemaGeneratorLike(Protocol):
    """Duck-typed surface of a Tortoise schema generator used by index DDL.

    Covers exactly the private helpers the HNSW/IVFFlat/GiST index builders
    call (``_qualify_table_name``, ``_get_index_name``,
    ``_format_index_fields``).  The methods stay private in the protocol, so
    call sites access them via ``getattr`` to keep ``reportPrivateUsage``
    silent (see module note above).
    """

    def _qualify_table_name(self, table_name: str, schema: str | None = None) -> str: ...
    def _get_index_name(
        self, prefix: str, model: type[Model] | str, field_names: Sequence[str]
    ) -> str: ...
    def _format_index_fields(self, field_names: Sequence[str]) -> str: ...


class SchemaEditorLike(Protocol):
    """Duck-typed surface of a Tortoise migration schema editor.

    Covers exactly the ``_run_sql`` method that the TimescaleDB migration
    operations execute DDL through (mirrors
    ``tortoise.migrations.schema_editor.base.BaseSchemaEditor._run_sql``).
    """

    async def _run_sql(self, sql: str) -> None: ...
