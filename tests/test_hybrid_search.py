"""Unit tests for HybridSearch — initialization and SQL structure.

No database connection required. Tests verify correct class
initialization and method signatures.
"""

import inspect
from unittest.mock import MagicMock

import pytest

from tortoise_extended.expressions.hybrid_search import HybridSearch


@pytest.fixture
def mock_model() -> MagicMock:
    """Create a mock Tortoise model."""
    model = MagicMock()
    model._meta.db_table = "entities"
    return model


class TestHybridSearchInit:
    """Test class initialization."""

    def test_defaults(self, mock_model: MagicMock) -> None:
        search = HybridSearch(model=mock_model)

        assert search.model is mock_model
        assert search.vector_field == "embedding"
        assert search.text_field == "description"
        assert search.tsvector_field == "description_tsv"
        assert search.distance_metric == "cosine"
        assert search.vector_weight == 0.7
        assert search.text_weight == 0.3

    def test_custom_fields(self, mock_model: MagicMock) -> None:
        search = HybridSearch(
            model=mock_model,
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

    def test_tsvector_auto_generated(self, mock_model: MagicMock) -> None:
        search = HybridSearch(model=mock_model, text_field="body")
        assert search.tsvector_field == "body_tsv"


class TestHybridSearchDistanceMetrics:
    """Test distance SQL generation for different metrics."""

    def test_cosine_distance_sql(self, mock_model: MagicMock) -> None:
        search = HybridSearch(model=mock_model, distance_metric="cosine")
        sql = search._distance_sql("embedding", 1)
        assert "<=>" in sql
        assert "embedding" in sql
        assert "$1::vector" in sql

    def test_l2_distance_sql(self, mock_model: MagicMock) -> None:
        search = HybridSearch(model=mock_model, distance_metric="l2")
        sql = search._distance_sql("embedding", 2)
        assert "<->" in sql
        assert "$2::vector" in sql

    def test_inner_product_distance_sql(self, mock_model: MagicMock) -> None:
        search = HybridSearch(model=mock_model, distance_metric="inner_product")
        sql = search._distance_sql("embedding", 3)
        assert "<#>" in sql
        assert "(-1)" in sql
        assert "$3::vector" in sql

    def test_invalid_distance_metric_raises(self, mock_model: MagicMock) -> None:
        with pytest.raises(ValueError, match="distance_metric"):
            HybridSearch(model=mock_model, distance_metric="hamming")


class TestHybridSearchMethod:
    """Test search method signature."""

    def test_search_signature(self, mock_model: MagicMock) -> None:
        search = HybridSearch(model=mock_model)
        sig = inspect.signature(search.search)
        params = list(sig.parameters.keys())
        assert "query_vector" in params
        assert "query_text" in params
        assert "max_results" in params
        assert "min_distance" in params

    def test_search_defaults(self, mock_model: MagicMock) -> None:
        search = HybridSearch(model=mock_model)
        sig = inspect.signature(search.search)
        assert sig.parameters["max_results"].default == 20
        assert sig.parameters["min_distance"].default is None
        assert sig.parameters["query_text"].default is None
