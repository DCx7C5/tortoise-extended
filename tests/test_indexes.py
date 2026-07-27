"""Comprehensive tests for GiSTIndex, HNSWIndex, and IVFFlatIndex.

Covers type verification, parameters, validation, describe, deconstruct,
and repr. No database connection required.
"""

import pytest

from tortoise_extended.indexes.hnsw_index import HNSWIndex, IVFFlatIndex
from tortoise_extended.indexes.ltree_index import GiSTIndex


# ---------------------------------------------------------------------------
# GiSTIndex tests
# ---------------------------------------------------------------------------


class TestGiSTIndexType:
    """Verify GiSTIndex INDEX_TYPE."""

    def test_index_type(self) -> None:
        """INDEX_TYPE should be 'gist'."""
        assert GiSTIndex.INDEX_TYPE == "gist"


class TestGiSTIndexParams:
    """Verify GiSTIndex initialization and parameters."""

    def test_fields(self) -> None:
        """Fields should be stored."""
        idx = GiSTIndex(fields=("path", "name"))
        assert idx.field_names == ["path", "name"]

    def test_custom_name(self) -> None:
        """Custom name should be stored."""
        idx = GiSTIndex(fields=("path",), name="my_gist_idx")
        assert idx.name == "my_gist_idx"

    def test_describe(self) -> None:
        """describe() should include fields."""
        idx = GiSTIndex(fields=("path",))
        desc = idx.describe()
        assert "fields" in desc
        assert desc["fields"] == ["path"]

    def test_deconstruct(self) -> None:
        """deconstruct() should round-trip correctly."""
        idx = GiSTIndex(fields=("path",), name="test_idx")
        path, args, kwargs = idx.deconstruct()
        assert "GiSTIndex" in path
        assert kwargs["fields"] == ["path"]
        assert kwargs["name"] == "test_idx"


# ---------------------------------------------------------------------------
# HNSWIndex tests
# ---------------------------------------------------------------------------


class TestHNSWIndexType:
    """Verify HNSWIndex INDEX_TYPE."""

    def test_index_type(self) -> None:
        """INDEX_TYPE should be 'hnsw'."""
        assert HNSWIndex.INDEX_TYPE == "hnsw"


class TestHNSWIndexParams:
    """Verify HNSWIndex initialization and parameters."""

    def test_defaults(self) -> None:
        """Default parameters should be m=16, ef_construction=200, metric=l2."""
        idx = HNSWIndex(fields=("embedding",))
        assert idx.m == 16
        assert idx.ef_construction == 200
        assert idx.dist_metric == "vector_l2_ops"

    def test_custom_params(self) -> None:
        """Custom parameters should be stored."""
        idx = HNSWIndex(
            fields=("embedding",),
            m=32,
            ef_construction=400,
            dist_metric="vector_cosine_ops",
        )
        assert idx.m == 32
        assert idx.ef_construction == 400
        assert idx.dist_metric == "vector_cosine_ops"

    def test_describe(self) -> None:
        """describe() should include m and ef_construction."""
        idx = HNSWIndex(fields=("embedding",), m=32)
        desc = idx.describe()
        assert desc["type"] == "hnsw"
        assert desc["m"] == 32
        assert desc["ef_construction"] == 200

    def test_deconstruct(self) -> None:
        """deconstruct() should round-trip correctly."""
        idx = HNSWIndex(fields=("embedding",), m=32, name="my_idx")
        path, _args, kwargs = idx.deconstruct()
        assert "HNSWIndex" in path
        assert kwargs["fields"] == ["embedding"]
        assert kwargs["m"] == 32
        assert kwargs["name"] == "my_idx"

    def test_repr(self) -> None:
        """Repr should contain HNSWIndex."""
        idx = HNSWIndex(fields=("embedding",))
        assert "HNSWIndex" in repr(idx)


class TestHNSWIndexValidation:
    """Verify HNSWIndex metric validation."""

    def test_invalid_metric_raises(self) -> None:
        """Invalid metric should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid dist_metric"):
            HNSWIndex(fields=("embedding",), dist_metric="euclidean")

    def test_all_valid_metrics(self) -> None:
        """All valid HNSW metrics should be accepted."""
        for metric in ("vector_l2_ops", "vector_ip_ops", "vector_cosine_ops"):
            idx = HNSWIndex(fields=("embedding",), dist_metric=metric)
            assert idx.dist_metric == metric


# ---------------------------------------------------------------------------
# IVFFlatIndex tests
# ---------------------------------------------------------------------------


class TestIVFFlatIndexType:
    """Verify IVFFlatIndex INDEX_TYPE."""

    def test_index_type(self) -> None:
        """INDEX_TYPE should be 'ivfflat'."""
        assert IVFFlatIndex.INDEX_TYPE == "ivfflat"


class TestIVFFlatIndexParams:
    """Verify IVFFlatIndex initialization and parameters."""

    def test_defaults(self) -> None:
        """Default parameters should be lists=100, metric=l2."""
        idx = IVFFlatIndex(fields=("embedding",))
        assert idx.lists == 100
        assert idx.dist_metric == "vector_l2_ops"

    def test_custom_params(self) -> None:
        """Custom parameters should be stored."""
        idx = IVFFlatIndex(fields=("embedding",), lists=200, dist_metric="vector_ip_ops")
        assert idx.lists == 200
        assert idx.dist_metric == "vector_ip_ops"

    def test_describe(self) -> None:
        """describe() should include lists and dist_metric."""
        idx = IVFFlatIndex(fields=("embedding",), lists=200)
        desc = idx.describe()
        assert desc["type"] == "ivfflat"
        assert desc["lists"] == 200

    def test_deconstruct(self) -> None:
        """deconstruct() should round-trip correctly."""
        idx = IVFFlatIndex(fields=("embedding",), lists=200, name="my_ivf")
        path, _args, kwargs = idx.deconstruct()
        assert "IVFFlatIndex" in path
        assert kwargs["lists"] == 200
        assert kwargs["name"] == "my_ivf"


class TestIVFFlatIndexValidation:
    """Verify IVFFlatIndex metric validation."""

    def test_invalid_metric_raises(self) -> None:
        """Invalid metric should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid dist_metric"):
            IVFFlatIndex(fields=("embedding",), dist_metric="vector_cosine_ops")

    def test_all_valid_metrics(self) -> None:
        """All valid IVFFlat metrics should be accepted."""
        for metric in ("vector_l2_ops", "vector_ip_ops"):
            idx = IVFFlatIndex(fields=("embedding",), dist_metric=metric)
            assert idx.dist_metric == metric
