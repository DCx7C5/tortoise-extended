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
        self.model = model
        self.vector_field = vector_field
        self.text_field = text_field
        self.tsvector_field = tsvector_field or f"{text_field}_tsv"
        self.distance_metric = distance_metric
        self.vector_weight = vector_weight
        self.text_weight = text_weight

    def _distance_sql(self, field: str, vector_str: str) -> str:
        """Generate distance SQL expression for the selected metric."""
        metric_map = {
            "cosine": f"{field} <=> '{vector_str}'::vector",
            "l2": f"{field} <-> '{vector_str}'::vector",
            "inner_product": f"(-1) * ({field} <#> '{vector_str}'::vector)",
        }
        return metric_map[self.distance_metric]

    async def search(
        self,
        query_vector: list[float] | str,
        query_text: str | None = None,
        max_results: int = 20,
        min_distance: float | None = None,
    ) -> list[dict[str, Any]]:
        """Execute hybrid search with weighted scoring.

        :param query_vector: Query embedding (list of floats or pgvector string).
        :param query_text: Text query for FTS (None = vector-only search).
        :param max_results: Maximum results to return.
        :param min_distance: Minimum distance threshold (None = no filter).
        :returns: List of dicts with model fields + score metadata.
        """
        table = self.model._meta.db_table
        vector_str = (
            query_vector
            if isinstance(query_vector, str)
            else vector_encoder(query_vector)
        )

        distance_expr = self._distance_sql(self.vector_field, vector_str)

        if query_text and self.text_weight > 0:
            # Combined score: weighted vector + text ranking
            sql = f"""
                SELECT
                    t.*,
                    {distance_expr} AS distance,
                    ts_rank_cd(t.{self.tsvector_field}, plainto_tsquery('english', $1)) AS text_score,
                    (
                        {self.vector_weight} * (1.0 - ({distance_expr})) +
                        {self.text_weight} * ts_rank_cd(t.{self.tsvector_field}, plainto_tsquery('english', $1))
                    ) AS combined_score
                FROM {table} t
                WHERE t.{self.vector_field} IS NOT NULL
                AND t.{self.tsvector_field} IS NOT NULL
                {"AND " + distance_expr + f" <= {min_distance}" if min_distance is not None else ""}
                ORDER BY combined_score DESC
                LIMIT $2
            """
            params: list[Any] = [query_text, max_results]
        else:
            # Vector-only search
            sql = f"""
                SELECT
                    t.*,
                    {distance_expr} AS distance,
                    0.0 AS text_score,
                    (1.0 - ({distance_expr})) AS combined_score
                FROM {table} t
                WHERE t.{self.vector_field} IS NOT NULL
                {"AND " + distance_expr + f" <= {min_distance}" if min_distance is not None else ""}
                ORDER BY combined_score DESC
                LIMIT $1
            """
            params = [max_results]

        conn = connections.get("default")
        results, _ = await conn.execute_query(sql, params)
        return [dict(r) for r in results]
