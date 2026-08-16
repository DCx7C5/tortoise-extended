"""Hybrid search combining vector similarity and full-text search.

Provides weighted scoring that blends pgvector distance operators
with PostgreSQL full-text search ranking.

Usage::

    from tortoise_extended.expressions.hybrid_search import HybridSearch

    search = HybridSearch(
        model=Entity,
        vector_field="embedding",
        text_field="description",
    )

    results = await search.search(
        query_vector=[0.1, 0.2, ...],
        query_text="machine learning",
    )
"""

from typing import TYPE_CHECKING

from tortoise import connections

from tortoise_extended._quote import quote_ident
from tortoise_extended._types import RowMapping
from tortoise_extended.exceptions import HybridSearchError

if TYPE_CHECKING:
    from tortoise.models import Model

from tortoise_extended.expressions.graph_filters import vector_encoder


class HybridSearch:
    """Combined vector similarity + full-text search with weighted scoring.

    Blends pgvector distance operators with PostgreSQL ``ts_rank_cd``
    to produce a single relevance score. Useful for RAG pipelines where
    both semantic similarity and keyword matching matter.

    :param model: The Tortoise ORM model to search.
    :param vector_field: Name of the VectorField column.
    :param text_field: Name of the TextField for FTS (stored as tsvector).
    :param tsvector_field: Name of the tsvector column (default: ``{text_field}_tsv``).
    :param distance_metric: ``"cosine"``, ``"l2"``, or ``"inner_product"``.
    :param vector_weight: Weight for vector similarity (default: 0.7).
    :param text_weight: Weight for text ranking (default: 0.3).
    :raises HybridSearchError: If ``distance_metric`` is unsupported, or
        ``vector_field``/``text_field`` is not declared on the model.

    Usage::

        search = HybridSearch(
            model=Entity,
            vector_field="embedding",
            text_field="description",
            vector_weight=0.7,
            text_weight=0.3,
        )

        results = await search.search(
            query_vector=[0.1, 0.2, ...],
            query_text="machine learning framework",
            max_results=20,
        )

        for r in results:
            print(f"{r['name']}: score={r['combined_score']:.3f}")
    """

    _METRICS: tuple[str, ...] = ("cosine", "l2", "inner_product")

    def __init__(
        self,
        model: type[Model],
        vector_field: str = "embedding",
        text_field: str = "description",
        tsvector_field: str | None = None,
        distance_metric: str = "cosine",
        vector_weight: float = 0.7,
        text_weight: float = 0.3,
    ) -> None:
        if distance_metric not in self._METRICS:
            raise HybridSearchError(
                f"Unsupported distance_metric {distance_metric!r}; "
                f"expected one of {', '.join(self._METRICS)}"
            )
        fields_map = model._meta.fields_map
        for name in (vector_field, text_field):
            if name not in fields_map:
                raise HybridSearchError(
                    f"Unknown search field {name!r} on {model.__name__}; "
                    f"expected one of {', '.join(sorted(fields_map))}"
                )
        resolved_tsvector = tsvector_field or f"{text_field}_tsv"
        self.model = model
        self.vector_field = vector_field
        self.text_field = text_field
        self.tsvector_field = resolved_tsvector
        self.distance_metric = distance_metric
        self.vector_weight = vector_weight
        self.text_weight = text_weight
        # Quoted once at construction so every SQL block interpolates only
        # quote_ident()-wrapped identifiers (never raw caller input).
        self._table = quote_ident(model._meta.db_table)
        self._vector_field = quote_ident(vector_field)
        self._tsvector_field = quote_ident(resolved_tsvector)

    def _distance_sql(self, field: str, param_index: int) -> str:
        """Generate a distance SQL expression for the selected metric.

        The query vector is referenced as ``${param_index}::vector`` so it is
        bound as a query parameter rather than interpolated into the SQL text.
        The *field* identifier is double-quoted before interpolation.
        """
        field = quote_ident(field)
        if self.distance_metric == "cosine":
            return f"({field} <=> ${param_index}::vector)"
        if self.distance_metric == "l2":
            return f"({field} <-> ${param_index}::vector)"
        return f"((-1) * ({field} <#> ${param_index}::vector))"

    def _threshold_op(self) -> str:
        """Return the min-distance comparison operator for the metric.

        ``l2``/``cosine`` distances are smaller-is-better, so the threshold
        caps the distance (``<=``). For ``inner_product`` the distance
        expression is the positive inner product, which is larger-is-better,
        so the threshold is a floor (``>=``).
        """
        return ">=" if self.distance_metric == "inner_product" else "<="

    def _similarity_sql(self, distance_expr: str) -> str:
        """Generate a normalized similarity expression (higher = more similar).

        ``1 / (1 + dist)`` bounds the vector component to ``(0, 1]`` for
        ``l2``/``cosine`` distances instead of assuming ``distance ∈ [0, 1]``
        (cosine distance reaches 2 and l2 is unbounded). For
        ``inner_product`` the distance expression is already the positive
        inner product (larger = more similar), so a monotone increasing
        logistic-like transform is used instead.
        """
        if self.distance_metric == "inner_product":
            return f"({distance_expr}) / (1.0 + abs({distance_expr}))"
        return f"1.0 / (1.0 + ({distance_expr}))"

    @staticmethod
    async def _execute(
        sql: str, params: list[str | int | float | None]
    ) -> list[RowMapping]:
        """Run a parameterized query on the default connection.

        :param sql: SQL statement.
        :param params: Query parameters.
        :returns: Result rows as dicts.
        """
        conn = connections.get("default")
        _, results = await conn.execute_query(sql, params)
        return [dict(r) for r in results]

    async def search(
        self,
        query_vector: list[float] | str,
        query_text: str | None = None,
        max_results: int = 20,
        min_distance: float | None = None,
    ) -> list[RowMapping]:
        """Execute hybrid search with weighted scoring.

        :param query_vector: Query embedding (list of floats or pgvector string).
            A string is passed as a pgvector literal (``"[0.1,0.2]"``), without
            surrounding SQL quotes.
        :param query_text: Text query for FTS (None = vector-only search).
        :param max_results: Maximum results to return.
        :param min_distance: Minimum distance threshold (None = no filter).
        :returns: List of dicts with model fields + score metadata.
        """
        table = self._table
        vf = self._vector_field
        tsv = self._tsvector_field
        if isinstance(query_vector, str):
            vector_literal = query_vector.strip().strip("'\"")
        else:
            vector_literal = vector_encoder(query_vector)

        if query_text and self.text_weight > 0:
            # Combined score: weighted vector + text ranking.
            # $1 = vector literal, $2 = query_text, $3 = max_results,
            # $4 = vector_weight, $5 = text_weight,
            # $6 = min_distance (only when a threshold is set).
            distance_expr = self._distance_sql(self.vector_field, 1)
            threshold_op = self._threshold_op()
            distance_filter = (
                f"AND ({distance_expr}) {threshold_op} $6"
                if min_distance is not None
                else ""
            )
            similarity = self._similarity_sql(distance_expr)
            sql = f"""
                SELECT
                    t.*,
                    {distance_expr} AS distance,
                    ts_rank_cd(t.{tsv}, plainto_tsquery('english', $2)) AS text_score,
                    (
                        $4 * {similarity} +
                        $5 * ts_rank_cd(t.{tsv}, plainto_tsquery('english', $2))
                    ) AS combined_score
                FROM {table} t
                WHERE t.{vf} IS NOT NULL
                AND t.{tsv} IS NOT NULL
                {distance_filter}
                ORDER BY combined_score DESC
                LIMIT $3
            """
            params: list[str | int | float | None] = [
                vector_literal,
                query_text,
                max_results,
                self.vector_weight,
                self.text_weight,
            ]
            if min_distance is not None:
                params.append(min_distance)
        else:
            # Vector-only search.
            # $1 = vector literal, $2 = max_results, $3 = min_distance.
            distance_expr = self._distance_sql(self.vector_field, 1)
            threshold_op = self._threshold_op()
            distance_filter = (
                f"AND ({distance_expr}) {threshold_op} $3"
                if min_distance is not None
                else ""
            )
            similarity = self._similarity_sql(distance_expr)
            sql = f"""
                SELECT
                    t.*,
                    {distance_expr} AS distance,
                    0.0 AS text_score,
                    {similarity} AS combined_score
                FROM {table} t
                WHERE t.{vf} IS NOT NULL
                {distance_filter}
                ORDER BY combined_score DESC
                LIMIT $2
            """
            params = [vector_literal, max_results]
            if min_distance is not None:
                params.append(min_distance)

        return await self._execute(sql, params)
