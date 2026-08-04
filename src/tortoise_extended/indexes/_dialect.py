"""Backend-dialect guards for PostgreSQL-only index types."""

from typing import TYPE_CHECKING

from tortoise_extended.exceptions import IndexDefinitionError

if TYPE_CHECKING:
    from tortoise.backends.base.schema_generator import BaseSchemaGenerator


def assert_postgres_dialect(
    schema_generator: BaseSchemaGenerator, index_type: str
) -> None:
    """Raise unless the schema generator targets PostgreSQL.

    HNSW/IVFFlat/GiST index DDL is PostgreSQL-specific (``USING hnsw``,
    ``USING ivfflat``, ``USING gist``).  On SQLite/MySQL/MSSQL/Oracle the
    emitted DDL is invalid and would break ``generate_schemas``, or worse
    silently produce a non-functional schema.  ``generate_schemas`` is a
    dev-only convenience; production migrations run on PostgreSQL where
    these indexes belong.

    Args:
        schema_generator: The active schema generator (carries ``DIALECT``).
        index_type: The index type name used in the error message.

    Raises:
        IndexDefinitionError: If ``DIALECT`` is not ``"postgres"``.
    """
    dialect = getattr(schema_generator, "DIALECT", "postgres")
    if dialect != "postgres":
        raise IndexDefinitionError(
            f"{index_type} is PostgreSQL-only; cannot generate index DDL "
            f"for the {dialect!r} backend. Remove it from Meta.indexes for "
            "non-PostgreSQL backends (e.g. ``indexes = ()`` in test models) "
            "or run schema generation against PostgreSQL."
        )
