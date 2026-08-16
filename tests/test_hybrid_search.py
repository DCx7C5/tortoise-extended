"""Unit tests for HybridSearch — initialization, validation, and SQL structure.

No database connection required. Uses a real Tortoise model definition (no
mocks), shared with ``test_hybrid_search_integration.py``.
"""

import inspect

import pytest
from tortoise import fields
from tortoise.models import Model

from tortoise_extended.exceptions import HybridSearchError
from tortoise_extended.expressions.hybrid_search import HybridSearch
from tortoise_extended.fields.vector import VectorField


class SearchEntity(Model):
    """Concrete model used by unit and integration hybrid-search tests."""

    id = fields.UUIDField(primary_key=True)
    name = fields.CharField(max_length=100)
    embedding = VectorField(dimensions=3)
    description = fields.TextField()

    class Meta:
        table = "hybrid_it_entities"


class CustomSearchEntity(Model):
    """Model with non-default vector/text field names."""

    id = fields.UUIDField(primary_key=True)
    vec = VectorField(dimensions=2)
    content = fields.TextField()

    class Meta:
        table = "hybrid_custom_entities"


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
            model=CustomSearchEntity,
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
        search = HybridSearch(
            model=CustomSearchEntity, vector_field="vec", text_field="content"
        )
        assert search.tsvector_field == "content_tsv"


class TestHybridSearchFieldValidation:
    """F1 — unknown search fields are rejected at construction time."""

    def test_unknown_vector_field_raises(self) -> None:
        with pytest.raises(HybridSearchError, match="Unknown search field"):
            HybridSearch(model=SearchEntity, vector_field="nope")

    def test_unknown_text_field_raises(self) -> None:
        with pytest.raises(HybridSearchError, match="Unknown search field"):
            HybridSearch(model=SearchEntity, text_field="nope")

    def test_unknown_vector_field_message_names_model(self) -> None:
        with pytest.raises(HybridSearchError, match="SearchEntity"):
            HybridSearch(model=SearchEntity, vector_field="nope")


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
        with pytest.raises(HybridSearchError, match="distance_metric"):
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


class TestHybridSearchSQL:
    """Inspect the generated SQL/params without a database.

    ``_execute`` is stubbed to capture its arguments, so ``search()`` can run
    with no live connection. Covers the F5 threshold operator, F6 normalized
    $N-parameterized scoring, and F1 quote_ident() identifiers.
    """

    @pytest.fixture
    def captured(self, monkeypatch) -> dict[str, object]:
        box: dict[str, object] = {}

        async def _fake_execute(
            sql: str, params: list[str | int | float | None]
        ) -> list[object]:
            box["sql"] = sql
            box["params"] = params
            return []

        monkeypatch.setattr(HybridSearch, "_execute", staticmethod(_fake_execute))
        return box

    @pytest.mark.asyncio
    async def test_identifiers_are_quoted(self, captured) -> None:
        """F1 — table and column names must go through quote_ident()."""
        await HybridSearch(model=SearchEntity).search(
            query_vector=[0.1, 0.2, 0.3],
            query_text="machine learning",
        )
        sql = captured["sql"]
        assert isinstance(sql, str)
        assert '"hybrid_it_entities"' in sql
        assert 't."embedding"' in sql
        assert 't."description_tsv"' in sql

    @pytest.mark.asyncio
    async def test_cosine_threshold_is_lte(self, captured) -> None:
        await HybridSearch(model=SearchEntity).search(
            query_vector=[0.1, 0.2, 0.3], min_distance=0.5
        )
        sql = captured["sql"]
        assert isinstance(sql, str)
        assert ") <= $3" in sql

    @pytest.mark.asyncio
    async def test_inner_product_threshold_is_gte(self, captured) -> None:
        """F5 — inner_product is larger-is-better, so the threshold is >=."""
        await HybridSearch(model=SearchEntity, distance_metric="inner_product").search(
            query_vector=[0.1, 0.2, 0.3], min_distance=0.5
        )
        sql = captured["sql"]
        assert isinstance(sql, str)
        assert ") >= $3" in sql

    @pytest.mark.asyncio
    async def test_combined_inner_product_threshold_is_gte(self, captured) -> None:
        await HybridSearch(model=SearchEntity, distance_metric="inner_product").search(
            query_vector=[0.1, 0.2, 0.3],
            query_text="machine learning",
            min_distance=0.5,
        )
        sql = captured["sql"]
        assert isinstance(sql, str)
        assert ") >= $6" in sql

    @pytest.mark.asyncio
    async def test_weights_bound_as_parameters(self, captured) -> None:
        """F6 — w_v/w_t must be $N params, never interpolated literals."""
        await HybridSearch(model=SearchEntity).search(
            query_vector=[0.1, 0.2, 0.3],
            query_text="machine learning",
        )
        sql = captured["sql"]
        assert isinstance(sql, str)
        assert "$4 * " in sql
        assert "$5 * " in sql
        assert "0.7" not in sql
        assert "0.3" not in sql
        params = captured["params"]
        assert isinstance(params, list)
        assert params[3] == 0.7
        assert params[4] == 0.3

    @pytest.mark.asyncio
    async def test_combined_scoring_is_normalized(self, captured) -> None:
        """F6 — vector component uses 1/(1+dist), not (1 - dist)."""
        await HybridSearch(model=SearchEntity).search(
            query_vector=[0.1, 0.2, 0.3],
            query_text="machine learning",
        )
        sql = captured["sql"]
        assert isinstance(sql, str)
        assert "1.0 / (1.0 + " in sql
        assert "1.0 - " not in sql

    @pytest.mark.asyncio
    async def test_vector_only_scoring_is_normalized(self, captured) -> None:
        await HybridSearch(model=SearchEntity).search(query_vector=[0.1, 0.2, 0.3])
        sql = captured["sql"]
        assert isinstance(sql, str)
        assert "1.0 / (1.0 + " in sql
        assert "1.0 - " not in sql

    @pytest.mark.asyncio
    async def test_inner_product_similarity_is_metric_scaled(self, captured) -> None:
        """F6 — inner-product similarity must increase with the value."""
        await HybridSearch(model=SearchEntity, distance_metric="inner_product").search(
            query_vector=[0.1, 0.2, 0.3]
        )
        sql = captured["sql"]
        assert isinstance(sql, str)
        assert "/ (1.0 + abs(" in sql

    @pytest.mark.asyncio
    async def test_params_are_typed(self, captured) -> None:
        """F6/F10 — params annotation is list[str | int | float | None]."""
        await HybridSearch(model=SearchEntity).search(query_vector=[0.1, 0.2, 0.3])
        params = captured["params"]
        assert isinstance(params, list)
        assert all(
            isinstance(p, (str, int, float)) or p is None for p in params
        )

    def test_execute_is_staticmethod(self) -> None:
        """F10 — _execute is a static method (no instance state)."""
        assert isinstance(inspect.getattr_static(HybridSearch, "_execute"), staticmethod)
