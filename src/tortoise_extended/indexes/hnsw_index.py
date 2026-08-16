"""HNSW and IVFFlat index types for pgvector."""

from collections.abc import Callable
from typing import TYPE_CHECKING, cast, override

from pypika_tortoise.terms import Term
from tortoise.expressions import Expression
from tortoise.indexes import Index
from tortoise.models import Model
from tortoise_extended._quote import quote_ident
from tortoise_extended._types import RowValue, SchemaGeneratorLike
from tortoise_extended.exceptions import IndexDefinitionError
from tortoise_extended.indexes._dialect import assert_postgres_dialect

if TYPE_CHECKING:
    from tortoise.backends.base.schema_generator import BaseSchemaGenerator

_VALID_HNSW_METRICS = frozenset(
    {
        "vector_l2_ops",
        "vector_ip_ops",
        "vector_cosine_ops",
        "halfvec_l2_ops",
        "halfvec_ip_ops",
        "halfvec_cosine_ops",
    }
)
_VALID_IVFFLAT_METRICS = frozenset(
    {"vector_l2_ops", "vector_ip_ops", "halfvec_l2_ops", "halfvec_ip_ops"}
)


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


class HNSWIndex(Index):
    """HNSW (Hierarchical Navigable Small World) index for vector columns.

    Provides approximate nearest-neighbor search with O(log N) query time.

    :param fields: Field names to index (typically a single VectorField).
    :param m: Max number of connections per layer (default: 16).
    :param ef_construction: Size of the dynamic candidate list during build (default: 200).
    :param dist_metric: Distance metric — ``vector_l2_ops``, ``vector_ip_ops``,
        ``vector_cosine_ops``, or the ``halfvec_*`` equivalents for
        half-precision columns (default: ``vector_l2_ops``).
    :param name: Optional custom index name.

    Usage::

        class Chunk(Model):
            embedding = VectorField(dimensions=1536)

        class Meta:
            indexes = [HNSWIndex(fields=("embedding",), m=32, ef_construction=400)]
    """

    INDEX_TYPE = "hnsw"

    def __init__(
        self,
        *args: Term | Expression,
        fields: tuple[str, ...] | list[str] | None = None,
        name: str | None = None,
        m: int = 16,
        ef_construction: int = 200,
        dist_metric: str = "vector_l2_ops",
    ) -> None:
        if dist_metric not in _VALID_HNSW_METRICS:
            raise IndexDefinitionError(
                f"Invalid dist_metric: {dist_metric!r}. "
                f"Must be one of {sorted(_VALID_HNSW_METRICS)}"
            )
        super().__init__(*args, fields=fields, name=name)
        self.m = m
        self.ef_construction = ef_construction
        self.dist_metric = dist_metric

    @override
    def describe(self) -> dict[str, RowValue]:
        """Return the index definition as a serializable dict.

        Extends the base description with the HNSW build parameters.

        :returns: Dict of index metadata including ``m``, ``ef_construction``
            and ``dist_metric``.
        """
        desc = super().describe()
        desc["m"] = self.m
        desc["ef_construction"] = self.ef_construction
        desc["dist_metric"] = self.dist_metric
        return desc

    @override
    def deconstruct(self) -> tuple[str, list[RowValue], dict[str, RowValue]]:
        """Deconstruct the index into a path, args and kwargs.

        Used by the migration writer to serialize the index definition.
        Includes ``m``, ``ef_construction`` and ``dist_metric`` so the
        definition round-trips exactly.

        :returns: ``(import_path, args, kwargs)`` tuple.
        """
        path, args, kwargs = super().deconstruct()
        kwargs["m"] = self.m
        kwargs["ef_construction"] = self.ef_construction
        kwargs["dist_metric"] = self.dist_metric
        return path, args, kwargs

    @override
    def get_sql(
        self, schema_generator: BaseSchemaGenerator, model: type[Model], safe: bool
    ) -> str:
        """Generate the ``CREATE INDEX ... USING hnsw`` DDL.

        :param schema_generator: Active schema generator (PostgreSQL only).
        :param model: The model the index belongs to.
        :param safe: Whether to emit ``IF NOT EXISTS``.
        :returns: The index DDL statement.
        :raises IndexDefinitionError: If the active dialect is not PostgreSQL.
        """
        # NOTE: Can't use _get_index_sql() — pgvector's USING ... WITH ()
        # syntax doesn't match INDEX_CREATE_TEMPLATE. If Tortoise adds a
        # hook for custom index SQL, migrate to that.
        assert_postgres_dialect(schema_generator, "HNSWIndex")
        self.resolve_expressions(model)
        table_name = _qualify_table_name(
            schema_generator, model._meta.db_table, model._meta.schema
        )
        index_name = self.name or _get_index_name(
            schema_generator, "hnsw", model, self.field_names
        )
        fields = _format_index_fields(schema_generator, self.field_names)
        exists = "IF NOT EXISTS " if safe else ""
        return (
            f"CREATE INDEX {exists}{quote_ident(index_name)} ON {table_name} "
            f"USING hnsw ({fields} {self.dist_metric}) "
            f"WITH (m = {self.m}, ef_construction = {self.ef_construction});"
        )


class IVFFlatIndex(Index):
    """IVFFlat (Inverted File with Flat quantization) index for vector columns.

    Partitions vectors into lists for faster search. Requires an existing
    table with data before creating (specifies lists count).

    :param fields: Field names to index.
    :param lists: Number of lists (recommended: rows / 1000 for up to 1M rows).
    :param dist_metric: Distance metric — ``vector_l2_ops``, ``vector_ip_ops``,
        or the ``halfvec_*`` equivalents for half-precision columns.
    :param name: Optional custom index name.
    """

    INDEX_TYPE = "ivfflat"

    def __init__(
        self,
        *args: Term | Expression,
        fields: tuple[str, ...] | list[str] | None = None,
        name: str | None = None,
        lists: int = 100,
        dist_metric: str = "vector_l2_ops",
    ) -> None:
        if dist_metric not in _VALID_IVFFLAT_METRICS:
            raise IndexDefinitionError(
                f"Invalid dist_metric: {dist_metric!r}. "
                f"Must be one of {sorted(_VALID_IVFFLAT_METRICS)}"
            )
        super().__init__(*args, fields=fields, name=name)
        self.lists = lists
        self.dist_metric = dist_metric

    @override
    def describe(self) -> dict[str, RowValue]:
        """Return the index definition as a serializable dict.

        Extends the base description with the IVFFlat build parameters.

        :returns: Dict of index metadata including ``lists`` and
            ``dist_metric``.
        """
        desc = super().describe()
        desc["lists"] = self.lists
        desc["dist_metric"] = self.dist_metric
        return desc

    @override
    def deconstruct(self) -> tuple[str, list[RowValue], dict[str, RowValue]]:
        """Deconstruct the index into a path, args and kwargs.

        Used by the migration writer to serialize the index definition.
        Includes ``lists`` and ``dist_metric`` so the definition round-trips
        exactly.

        :returns: ``(import_path, args, kwargs)`` tuple.
        """
        path, args, kwargs = super().deconstruct()
        kwargs["lists"] = self.lists
        kwargs["dist_metric"] = self.dist_metric
        return path, args, kwargs

    @override
    def get_sql(
        self, schema_generator: BaseSchemaGenerator, model: type[Model], safe: bool
    ) -> str:
        """Generate the ``CREATE INDEX ... USING ivfflat`` DDL.

        :param schema_generator: Active schema generator (PostgreSQL only).
        :param model: The model the index belongs to.
        :param safe: Whether to emit ``IF NOT EXISTS``.
        :returns: The index DDL statement.
        :raises IndexDefinitionError: If the active dialect is not PostgreSQL.
        """
        # NOTE: Can't use _get_index_sql() — pgvector's USING ... WITH ()
        # syntax doesn't match INDEX_CREATE_TEMPLATE. If Tortoise adds a
        # hook for custom index SQL, migrate to that.
        assert_postgres_dialect(schema_generator, "IVFFlatIndex")
        self.resolve_expressions(model)
        table_name = _qualify_table_name(
            schema_generator, model._meta.db_table, model._meta.schema
        )
        index_name = self.name or _get_index_name(
            schema_generator, "ivfflat", model, self.field_names
        )
        fields = _format_index_fields(schema_generator, self.field_names)
        exists = "IF NOT EXISTS " if safe else ""
        return (
            f"CREATE INDEX {exists}{quote_ident(index_name)} ON {table_name} "
            f"USING ivfflat ({fields} {self.dist_metric}) "
            f"WITH (lists = {self.lists});"
        )
