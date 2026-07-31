"""Tests for HNSWIndex and IVFFlatIndex."""

import pytest

from tortoise_extended.exceptions import IndexDefinitionError
from tortoise_extended.indexes.hnsw_index import HNSWIndex, IVFFlatIndex


class TestHNSWIndex:
    def test_default_params(self) -> None:
        idx = HNSWIndex(fields=("embedding",))
        assert idx.INDEX_TYPE == "hnsw"
        assert idx.m == 16
        assert idx.ef_construction == 200
        assert idx.dist_metric == "vector_l2_ops"

    def test_custom_params(self) -> None:
        idx = HNSWIndex(fields=("embedding",), m=32, ef_construction=400, dist_metric="vector_cosine_ops")
        assert idx.m == 32
        assert idx.ef_construction == 400
        assert idx.dist_metric == "vector_cosine_ops"

    def test_dist_metric_validation(self) -> None:
        with pytest.raises(IndexDefinitionError, match="Invalid dist_metric"):
            HNSWIndex(fields=("embedding",), dist_metric="euclidean")

    def test_dist_metric_valid_options(self) -> None:
        for metric in ("vector_l2_ops", "vector_ip_ops", "vector_cosine_ops"):
            idx = HNSWIndex(fields=("embedding",), dist_metric=metric)
            assert idx.dist_metric == metric

    def test_describe(self) -> None:
        idx = HNSWIndex(fields=("embedding",), m=32)
        desc = idx.describe()
        assert desc["type"] == "hnsw"
        assert desc["m"] == 32
        assert desc["ef_construction"] == 200

    def test_deconstruct(self) -> None:
        idx = HNSWIndex(fields=("embedding",), m=32, name="my_idx")
        path, _args, kwargs = idx.deconstruct()
        assert "HNSWIndex" in path
        assert kwargs["fields"] == ["embedding"]
        assert kwargs["m"] == 32
        assert kwargs["name"] == "my_idx"

    def test_repr(self) -> None:
        idx = HNSWIndex(fields=("embedding",))
        assert "HNSWIndex" in repr(idx)


class TestIVFFlatIndex:
    def test_default_params(self) -> None:
        idx = IVFFlatIndex(fields=("embedding",))
        assert idx.INDEX_TYPE == "ivfflat"
        assert idx.lists == 100
        assert idx.dist_metric == "vector_l2_ops"

    def test_custom_params(self) -> None:
        idx = IVFFlatIndex(fields=("embedding",), lists=200, dist_metric="vector_ip_ops")
        assert idx.lists == 200
        assert idx.dist_metric == "vector_ip_ops"

    def test_dist_metric_validation(self) -> None:
        with pytest.raises(IndexDefinitionError, match="Invalid dist_metric"):
            IVFFlatIndex(fields=("embedding",), dist_metric="vector_cosine_ops")

    def test_dist_metric_valid_options(self) -> None:
        for metric in ("vector_l2_ops", "vector_ip_ops"):
            idx = IVFFlatIndex(fields=("embedding",), dist_metric=metric)
            assert idx.dist_metric == metric

    def test_describe(self) -> None:
        idx = IVFFlatIndex(fields=("embedding",), lists=200)
        desc = idx.describe()
        assert desc["type"] == "ivfflat"
        assert desc["lists"] == 200

    def test_deconstruct(self) -> None:
        idx = IVFFlatIndex(fields=("embedding",), lists=200, name="my_ivf")
        path, _args, kwargs = idx.deconstruct()
        assert "IVFFlatIndex" in path
        assert kwargs["lists"] == 200
        assert kwargs["name"] == "my_ivf"
