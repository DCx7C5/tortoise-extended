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

from typing import TYPE_CHECKING, override

from tortoise.indexes import Index

if TYPE_CHECKING:
    from tortoise.backends.base.schema_generator import BaseSchemaGenerator
    from tortoise.models import Model


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
    def get_sql(self, schema_generator: BaseSchemaGenerator, model: type[Model], safe: bool) -> str:
        self.resolve_expressions(model)
        table_name = schema_generator._qualify_table_name(
            model._meta.db_table, model._meta.schema
        )
        index_name = self.name or schema_generator._get_index_name(
            "gist", model, self.field_names
        )
        fields = schema_generator._format_index_fields(self.field_names)
        exists = "IF NOT EXISTS " if safe else ""
        return (
            f'CREATE INDEX {exists}"{index_name}" ON {table_name} '
            f"USING gist ({fields});"
        )
