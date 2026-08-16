"""GiST index type for PostgreSQL ltree columns.

Provides a GiST index class optimized for ltree hierarchical
path queries (@>, <@, ~, ?@>, ?<@).

Usage::

    from tortoise_extended.indexes.ltree_index import GiSTIndex

    class Category(Model):
        path = LTreeField(max_length=1024)

        class Meta:
            indexes = [GiSTIndex(fields=("path",))]
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, cast, override

from tortoise.indexes import Index
from tortoise.models import Model
from tortoise_extended._quote import quote_ident
from tortoise_extended._types import SchemaGeneratorLike
from tortoise_extended.indexes._dialect import assert_postgres_dialect

if TYPE_CHECKING:
    from tortoise.backends.base.schema_generator import BaseSchemaGenerator


def _qualify_table_name(
    schema_generator: SchemaGeneratorLike, table_name: str, schema: str | None
) -> str:
    """Call the schema generator's ``_qualify_table_name`` helper.

    ``getattr`` is required because pyright flags protected-member access
    against the declaring class (see ``_types.py`` note on Protocols).
    """
    method = cast(
        Callable[[str, str | None], str],
        getattr(schema_generator, "_qualify_table_name"),
    )
    return method(table_name, schema)


def _get_index_name(
    schema_generator: SchemaGeneratorLike,
    prefix: str,
    model: type[Model],
    field_names: list[str],
) -> str:
    """Call the schema generator's ``_get_index_name`` helper."""
    method = cast(
        Callable[[str, type[Model], list[str]], str],
        getattr(schema_generator, "_get_index_name"),
    )
    return method(prefix, model, field_names)


def _format_index_fields(
    schema_generator: SchemaGeneratorLike, field_names: list[str]
) -> str:
    """Call the schema generator's ``_format_index_fields`` helper."""
    method = cast(
        Callable[[list[str]], str],
        getattr(schema_generator, "_format_index_fields"),
    )
    return method(field_names)


class GiSTIndex(Index):
    """GiST (Generalized Search Tree) index for ltree columns.

    GiST is the recommended index type for ltree operators:
    ``@>` (ancestor), ``<@`` (descendant), ``~`` (match).

    :param fields: Field names to index (typically a single LTreeField).
    :param name: Optional custom index name.

    Usage::

        class Category(Model):
            path = LTreeField(max_length=1024)

            class Meta:
                indexes = [GiSTIndex(fields=("path",))]

    Requires: ``CREATE EXTENSION IF NOT EXISTS ltree;``
    """

    INDEX_TYPE = "gist"

    @override
    def get_sql(
        self, schema_generator: BaseSchemaGenerator, model: type[Model], safe: bool
    ) -> str:
        """Generate the ``CREATE INDEX ... USING gist`` DDL.

        :param schema_generator: Active schema generator (PostgreSQL only).
        :param model: The model the index belongs to.
        :param safe: Whether to emit ``IF NOT EXISTS``.
        :returns: The index DDL statement.
        :raises IndexDefinitionError: If the active dialect is not PostgreSQL.
        """
        assert_postgres_dialect(schema_generator, "GiSTIndex")
        self.resolve_expressions(model)
        table_name = _qualify_table_name(
            schema_generator, model._meta.db_table, model._meta.schema
        )
        index_name = self.name or _get_index_name(
            schema_generator, "gist", model, self.field_names
        )
        fields = _format_index_fields(schema_generator, self.field_names)
        exists = "IF NOT EXISTS " if safe else ""
        return (
            f"CREATE INDEX {exists}{quote_ident(index_name)} ON {table_name} "
            f"USING gist ({fields});"
        )
