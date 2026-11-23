"""COPY-based bulk insert for any model (PostgreSQL only).

Tortoise's ``bulk_create`` renders one multi-row ``INSERT`` per batch. For
high-rate ingestion asyncpg's ``COPY`` is 3-10x faster, but Tortoise exposes no
way to reach it — which is why this package ships one. The function is installed
as ``Model.bulk_insert`` by the patch set (site 7), so it is available on every
model, not only TimescaleDB streams; :class:`~tortoise_extended.models.sample.SampleMixin`
keeps its own method as a thin delegate.

Semantics, and why each rule exists:

* **Explicit primary keys are required.** ``COPY`` bypasses identity/serial
  defaults, so a missing id would silently insert a row the ORM cannot address.
* **Every relation-bearing model is refused.** A ``COPY`` record is a flat
  column list, so a foreign key, a many-to-many link or a backward FK cannot be
  written without dropping data on the floor. The old stream-only implementation
  skipped those columns; for a general model that means inserting ``NULL`` where
  the caller set a value, which is a silent data loss — so
  :func:`bulk_insert` raises :class:`ValueError` naming the fields and points at
  ``bulk_create`` instead.
* **``db_default`` columns are omitted when *every* instance defers to them**,
  mirroring ``bulk_create``; mixed usage raises ``OperationalError`` because one
  ``COPY`` statement cannot express both.
* **``auto_now`` / ``auto_now_add`` are stamped** through the field's
  ``to_db_value``, exactly like ``bulk_create``.

Every instance is marked persisted after a successful ``COPY``, so a later
``instance.save()`` updates the row instead of inserting a duplicate.

Requires: PostgreSQL (asyncpg backend). See ``docs/api/models.md``.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING

from tortoise import connections
from tortoise.exceptions import OperationalError
from tortoise.fields.base import DatabaseDefault

from tortoise_extended._types import CoercedValue

if TYPE_CHECKING:
    from tortoise.models import Model


def field_db_column(field: object, fallback: str) -> str:
    """Resolve a field's physical DB column name.

    :param field: The model field (its ``source_field`` wins when set).
    :param fallback: The field name, used when no ``source_field`` is declared.
    :returns: The column name to target in the ``COPY`` statement.
    """
    return getattr(field, "source_field", None) or fallback


def relation_fields[M: Model](cls: type[M]) -> set[str]:
    """Return every relation field declared on *cls*.

    ``_meta.fetch_fields`` is the union of every relation kind (FK, O2O,
    many-to-many, backward FK, backward O2O); all of them lose their value in a
    flat ``COPY`` record, so their presence makes a model ineligible.

    :param cls: The model to inspect.
    :returns: Relation field names.
    """
    return set(cls._meta.fetch_fields)


def assert_pk_present[M: Model](cls: type[M], instances: Sequence[M]) -> None:
    """Raise when any instance lacks an explicit primary key.

    asyncpg ``COPY`` cannot use identity/serial defaults, so every instance must
    carry its id before insertion.

    :param cls: The model being inserted.
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


def assert_no_relations[M: Model](cls: type[M]) -> None:
    """Refuse a model whose relation fields a ``COPY`` record cannot carry.

    :param cls: The model being inserted.
    :raises ValueError: If the model declares any FK, M2M or backward-FK field.
    """
    relations = relation_fields(cls)
    if relations:
        raise ValueError(
            "bulk_insert cannot write relation fields "
            f"({', '.join(sorted(relations))}) on {cls.__name__}: a COPY "
            "record is a flat column list, so a foreign key or many-to-many "
            "link would be silently dropped. Use bulk_create() for this "
            "model, or insert through the related model's own table."
        )


def copy_db_fields[M: Model](cls: type[M]) -> list[str]:
    """Return the database columns a COPY statement should target.

    Keys off ``fields_db_projection`` (field name → column name) rather than
    ``db_fields`` (a set of *column* names): a field whose ``source_field``
    differs from its attribute name would otherwise be dropped silently and the
    COPY would leave its column at the default — the value the caller set is
    simply never written.

    Excludes relation fields — :func:`assert_no_relations` has already refused a
    model that has any, so this only filters them out for the shared code path
    the stream mixin uses.

    :param cls: The model being inserted.
    :returns: Field names in declaration order (columns resolved by
        :func:`field_db_column`).
    """
    fields_map = cls._meta.fields_map
    projection = cls._meta.fields_db_projection
    skip = relation_fields(cls)
    return [name for name in fields_map if name in projection and name not in skip]


def omit_db_default_fields[M: Model](
    cls: type[M], instances: Sequence[M], db_fields: list[str]
) -> set[str]:
    """Find ``db_default`` columns that COPY must omit for this batch.

    Mirrors Tortoise ``bulk_create``: a column is omitted when *every* instance
    leaves it at the database default, and mixed usage (some instances explicit,
    some defaulted) raises because a single COPY statement cannot represent both.

    :param cls: The model being inserted.
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


def copy_records[M: Model](
    cls: type[M], instances: Sequence[M], db_fields: list[str]
) -> list[list[CoercedValue]]:
    """Coerce field values to the COPY row layout.

    :param cls: The model being inserted.
    :param instances: Instances being inserted.
    :param db_fields: Column names in declaration order.
    :returns: Per-instance value rows, one value per column.
    """
    fields_map = cls._meta.fields_map
    return [
        [fields_map[name].to_db_value(getattr(inst, name), inst) for name in db_fields]
        for inst in instances
    ]


async def bulk_insert[M: Model](cls: type[M], instances: Sequence[M]) -> int:
    """Insert *instances* via a single asyncpg ``COPY`` statement.

    Available on every model as ``Model.bulk_insert`` (patch site 7) and on
    :class:`~tortoise_extended.models.sample.SampleMixin` under the same name.

    :param cls: The model to insert into.
    :param instances: Model instances to insert.
    :returns: Number of rows inserted.
    :raises ValueError: If any instance lacks a primary key, or the model
        declares relation fields (both decided before a connection is used).
    :raises OperationalError: If a ``db_default`` field is set on some
        instances but not others.
    """
    if not instances:
        return 0

    assert_pk_present(cls, instances)
    assert_no_relations(cls)

    db_fields = copy_db_fields(cls)
    omit_fields = omit_db_default_fields(cls, instances, db_fields)
    db_fields = [name for name in db_fields if name not in omit_fields]

    fields_map = cls._meta.fields_map
    columns = [field_db_column(fields_map[name], name) for name in db_fields]
    records = copy_records(cls, instances, db_fields)

    conn = connections.get("default")
    async with conn.acquire_connection() as raw:
        _ = await raw.copy_records_to_table(
            cls._meta.db_table,
            columns=columns,
            records=records,
        )
    for inst in instances:
        setattr(inst, "_saved_in_db", True)
    return len(instances)
