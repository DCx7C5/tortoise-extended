"""HNSW and IVFFlat index types for pgvector."""

from typing import TYPE_CHECKING, Any, override

from tortoise.indexes import Index

if TYPE_CHECKING:
    from tortoise.backends.base.schema_generator import BaseSchemaGenerator
    from tortoise.models import Model

_VALID_HNSW_METRICS = frozenset({"vector_l2_ops", "vector_ip_ops", "vector_cosine_ops"})
_VALID_IVFFLAT_METRICS = frozenset({"vector_l2_ops", "vector_ip_ops"})


class HNSWIndex(Index):
    """HNSW (Hierarchical Navigable Small World) index for vector columns.

    Provides approximate nearest-neighbor search with O(log N) query time.

    :param fields: Field names to index (typically a single VectorField).
    :param m: Max number of connections per layer (default: 16).
    :param ef_construction: Size of the dynamic candidate list during build (default: 200).
    :param dist_metric: Distance metric — ``vector_l2_ops``, ``vector_ip_ops``,
        or ``vector_cosine_ops`` (default: ``vector_l2_ops``).
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
        *args: Any,
        fields: tuple[str, ...] | list[str] | None = None,
        name: str | None = None,
        m: int = 16,
        ef_construction: int = 200,
        dist_metric: str = "vector_l2_ops",
    ) -> None:
        if dist_metric not in _VALID_HNSW_METRICS:
            raise ValueError(
                f"Invalid dist_metric: {dist_metric!r}. "
                f"Must be one of {sorted(_VALID_HNSW_METRICS)}"
            )
        super().__init__(*args, fields=fields, name=name)
        self.m = m
        self.ef_construction = ef_construction
        self.dist_metric = dist_metric

    @override
    def describe(self) -> dict:
        desc = super().describe()
        desc["m"] = self.m
        desc["ef_construction"] = self.ef_construction
        desc["dist_metric"] = self.dist_metric
        return desc

    @override
    def deconstruct(self) -> tuple[str, list[Any], dict[str, Any]]:
        path, args, kwargs = super().deconstruct()
        kwargs["m"] = self.m
        kwargs["ef_construction"] = self.ef_construction
        kwargs["dist_metric"] = self.dist_metric
        return path, args, kwargs

    @override
    def get_sql(self, schema_generator: BaseSchemaGenerator, model: type[Model], safe: bool) -> str:
        # NOTE: Can't use _get_index_sql() — pgvector's USING ... WITH ()
        # syntax doesn't match INDEX_CREATE_TEMPLATE. If Tortoise adds a
        # hook for custom index SQL, migrate to that.
        self.resolve_expressions(model)
        table_name = schema_generator._qualify_table_name(
            model._meta.db_table, model._meta.schema
        )
        index_name = self.name or schema_generator._get_index_name(
            "hnsw", model, self.field_names
        )
        fields = schema_generator._format_index_fields(self.field_names)
        exists = "IF NOT EXISTS " if safe else ""
        return (
            f'CREATE INDEX {exists}"{index_name}" ON {table_name} '
            f"USING hnsw ({fields} {self.dist_metric}) "
            f"WITH (m = {self.m}, ef_construction = {self.ef_construction});"
        )


class IVFFlatIndex(Index):
    """IVFFlat (Inverted File with Flat quantization) index for vector columns.

    Partitions vectors into lists for faster search. Requires an existing
    table with data before creating (specifies lists count).

    :param fields: Field names to index.
    :param lists: Number of lists (recommended: rows / 1000 for up to 1M rows).
    :param dist_metric: Distance metric — ``vector_l2_ops`` or ``vector_ip_ops``.
    :param name: Optional custom index name.
    """

    INDEX_TYPE = "ivfflat"

    def __init__(
        self,
        *args: Any,
        fields: tuple[str, ...] | list[str] | None = None,
        name: str | None = None,
        lists: int = 100,
        dist_metric: str = "vector_l2_ops",
    ) -> None:
        if dist_metric not in _VALID_IVFFLAT_METRICS:
            raise ValueError(
                f"Invalid dist_metric: {dist_metric!r}. "
                f"Must be one of {sorted(_VALID_IVFFLAT_METRICS)}"
            )
        super().__init__(*args, fields=fields, name=name)
        self.lists = lists
        self.dist_metric = dist_metric

    @override
    def describe(self) -> dict:
        desc = super().describe()
        desc["lists"] = self.lists
        desc["dist_metric"] = self.dist_metric
        return desc

    @override
    def deconstruct(self) -> tuple[str, list[Any], dict[str, Any]]:
        path, args, kwargs = super().deconstruct()
        kwargs["lists"] = self.lists
        kwargs["dist_metric"] = self.dist_metric
        return path, args, kwargs

    @override
    def get_sql(self, schema_generator: BaseSchemaGenerator, model: type[Model], safe: bool) -> str:
        # NOTE: Can't use _get_index_sql() — pgvector's USING ... WITH ()
        # syntax doesn't match INDEX_CREATE_TEMPLATE. If Tortoise adds a
        # hook for custom index SQL, migrate to that.
        self.resolve_expressions(model)
        table_name = schema_generator._qualify_table_name(
            model._meta.db_table, model._meta.schema
        )
        index_name = self.name or schema_generator._get_index_name(
            "ivfflat", model, self.field_names
        )
        fields = schema_generator._format_index_fields(self.field_names)
        exists = "IF NOT EXISTS " if safe else ""
        return (
            f'CREATE INDEX {exists}"{index_name}" ON {table_name} '
            f"USING ivfflat ({fields} {self.dist_metric}) "
            f"WITH (lists = {self.lists});"
        )
