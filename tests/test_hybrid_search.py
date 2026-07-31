"""Unit tests for HybridSearch — initialization, validation, and SQL structure.

No database connection required. Uses a real Tortoise model definition (no
mocks), shared with ``test_hybrid_search_integration.py``.
"""

import inspect

import pytest
from tortoise import fields
from tortoise.models import Model

from tortoise_extended.expressions.hybrid_search import HybridSearch
from tortoise_extended.fields.vector_field import VectorField


class SearchEntity(Model):
    """Concrete model used by unit and integration hybrid-search tests."""

    id = fields.UUIDField(primary_key=True)
    name = fields.CharField(max_length=100)
    embedding = VectorField(dimensions=3)
    description = fields.TextField()

    class Meta:
        table = "hybrid_it_entities"


class TestHybridSearchInit:
    """Test class initialization."""

    def test_defaults(self) -> None:
        search = HybridSearch(model=SearchEntity)

        assert search.model is SearchEntity
        assert search.vector_field == "embedding"
        assert search.text_field == "description"
        assert search.tsvector_field == "description_tsv"
        assert search.distance_metric == "cosine"
        assert search.vector_weight == 0.7
        assert search.text_weight == 0.3

    def test_custom_fields(self) -> None:
        search = HybridSearch(
            model=SearchEntity,
            vector_field="vec",
            text_field="content",
            tsvector_field="content_fts",
            distance_metric="l2",
            vector_weight=0.5,
            text_weight=0.5,
        )

        assert search.vector_field == "vec"
        assert search.text_field == "content"
        assert search.tsvector_field == "content_fts"
        assert search.distance_metric == "l2"
        assert search.vector_weight == 0.5
        assert search.text_weight == 0.5

    def test_tsvector_auto_generated(self) -> None:
        search = HybridSearch(model=SearchEntity, text_field="body")
        assert search.tsvector_field == "body_tsv"


class TestHybridSearchDistanceMetrics:
    """Test distance SQL generation for different metrics."""

    def test_cosine_distance_sql(self) -> None:
        search = HybridSearch(model=SearchEntity, distance_metric="cosine")
        sql = search._distance_sql("embedding", 1)
        assert "<=>" in sql
        assert "embedding" in sql
        assert "$1::vector" in sql

    def test_l2_distance_sql(self) -> None:
        search = HybridSearch(model=SearchEntity, distance_metric="l2")
        sql = search._distance_sql("embedding", 2)
        assert "<->" in sql
        assert "$2::vector" in sql

    def test_inner_product_distance_sql(self) -> None:
        search = HybridSearch(model=SearchEntity, distance_metric="inner_product")
        sql = search._distance_sql("embedding", 3)
        assert "<#>" in sql
        assert "(-1)" in sql
        assert "$3::vector" in sql

    def test_invalid_distance_metric_raises(self) -> None:
        with pytest.raises(ValueError, match="distance_metric"):
            HybridSearch(model=SearchEntity, distance_metric="hamming")


class TestHybridSearchMethod:
    """Test search method signature."""

    def test_search_signature(self) -> None:
        search = HybridSearch(model=SearchEntity)
        sig = inspect.signature(search.search)
        params = list(sig.parameters.keys())
        assert "query_vector" in params
        assert "query_text" in params
        assert "max_results" in params
        assert "min_distance" in params

    def test_search_defaults(self) -> None:
        search = HybridSearch(model=SearchEntity)
        sig = inspect.signature(search.search)
        assert sig.parameters["max_results"].default == 20
        assert sig.parameters["min_distance"].default is None
        assert sig.parameters["query_text"].default is None
