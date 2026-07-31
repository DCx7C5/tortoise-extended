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

from typing import Any

from tortoise import connections

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
    :raises ValueError: If ``distance_metric`` is not one of the supported metrics.

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
        model: type,
        vector_field: str = "embedding",
        text_field: str = "description",
        tsvector_field: str | None = None,
        distance_metric: str = "cosine",
        vector_weight: float = 0.7,
        text_weight: float = 0.3,
    ) -> None:
        if distance_metric not in self._METRICS:
            raise ValueError(
                f"Unsupported distance_metric {distance_metric!r}; "
                f"expected one of {', '.join(self._METRICS)}"
            )
        self.model = model
        self.vector_field = vector_field
        self.text_field = text_field
        self.tsvector_field = tsvector_field or f"{text_field}_tsv"
        self.distance_metric = distance_metric
        self.vector_weight = vector_weight
        self.text_weight = text_weight

    def _distance_sql(self, field: str, param_index: int) -> str:
        """Generate a distance SQL expression for the selected metric.

        The query vector is referenced as ``${param_index}::vector`` so it is
        bound as a query parameter rather than interpolated into the SQL text.
        """
        if self.distance_metric == "cosine":
            return f"{field} <=> ${param_index}::vector"
        if self.distance_metric == "l2":
            return f"{field} <-> ${param_index}::vector"
        return f"(-1) * ({field} <#> ${param_index}::vector)"

    async def search(
        self,
        query_vector: list[float] | str,
        query_text: str | None = None,
        max_results: int = 20,
        min_distance: float | None = None,
    ) -> list[dict[str, Any]]:
        """Execute hybrid search with weighted scoring.

        :param query_vector: Query embedding (list of floats or pgvector string).
            A string is passed as a pgvector literal (``"[0.1,0.2]"``), without
            surrounding SQL quotes.
        :param query_text: Text query for FTS (None = vector-only search).
        :param max_results: Maximum results to return.
        :param min_distance: Minimum distance threshold (None = no filter).
        :returns: List of dicts with model fields + score metadata.
        """
        table = self.model._meta.db_table
        if isinstance(query_vector, str):
            vector_literal = query_vector.strip().strip("'\"")
        else:
            vector_literal = vector_encoder(query_vector)

        if query_text and self.text_weight > 0:
            # Combined score: weighted vector + text ranking.
            # $1 = vector literal, $2 = query_text, $3 = max_results,
            # $4 = min_distance (only when a threshold is set).
            distance_expr = self._distance_sql(self.vector_field, 1)
            distance_filter = (
                f"AND ({distance_expr}) <= $4" if min_distance is not None else ""
            )
            sql = f"""
                SELECT
                    t.*,
                    {distance_expr} AS distance,
                    ts_rank_cd(t.{self.tsvector_field}, plainto_tsquery('english', $2)) AS text_score,
                    (
                        {self.vector_weight} * (1.0 - ({distance_expr})) +
                        {self.text_weight} * ts_rank_cd(t.{self.tsvector_field}, plainto_tsquery('english', $2))
                    ) AS combined_score
                FROM {table} t
                WHERE t.{self.vector_field} IS NOT NULL
                AND t.{self.tsvector_field} IS NOT NULL
                {distance_filter}
                ORDER BY combined_score DESC
                LIMIT $3
            """
            params: list[Any] = [vector_literal, query_text, max_results]
            if min_distance is not None:
                params.append(min_distance)
        else:
            # Vector-only search.
            # $1 = vector literal, $2 = max_results, $3 = min_distance.
            distance_expr = self._distance_sql(self.vector_field, 1)
            distance_filter = (
                f"AND ({distance_expr}) <= $3" if min_distance is not None else ""
            )
            sql = f"""
                SELECT
                    t.*,
                    {distance_expr} AS distance,
                    0.0 AS text_score,
                    (1.0 - ({distance_expr})) AS combined_score
                FROM {table} t
                WHERE t.{self.vector_field} IS NOT NULL
                {distance_filter}
                ORDER BY combined_score DESC
                LIMIT $2
            """
            params = [vector_literal, max_results]
            if min_distance is not None:
                params.append(min_distance)

        conn = connections.get("default")
        _, results = await conn.execute_query(sql, params)
        return [dict(r) for r in results]
