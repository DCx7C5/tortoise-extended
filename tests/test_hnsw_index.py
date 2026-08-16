"""Tests for HNSWIndex and IVFFlatIndex."""

from types import SimpleNamespace

import pytest

from tortoise_extended.exceptions import IndexDefinitionError
from tortoise_extended.indexes.hnsw_index import HNSWIndex, IVFFlatIndex


class FakeSchemaGenerator:
    """Minimal schema-generator stand-in exposing the helper methods."""

    def _qualify_table_name(self, table_name: str, schema: str | None) -> str:
        if schema is None:
            return f'"{table_name}"'
        return f'"{schema}"."{table_name}"'

    def _get_index_name(
        self, prefix: str, model: FakeModel, field_names: list[str]
    ) -> str:
        return f"{prefix}_{type(model).__name__}_{'_'.join(field_names)}"

    def _format_index_fields(self, field_names: list[str]) -> str:
        return ", ".join(f'"{f}"' for f in field_names)


class FakeModel:
    """Minimal model stand-in with the _meta surface get_sql reads."""

    def __init__(self, db_table: str, schema: str | None = None) -> None:
        self._meta = SimpleNamespace(db_table=db_table, schema=schema)


class TestHNSWIndex:
    def test_default_params(self) -> None:
        idx = HNSWIndex(fields=("embedding",))
        assert idx.INDEX_TYPE == "hnsw"
        assert idx.m == 16
        assert idx.ef_construction == 200
        assert idx.dist_metric == "vector_l2_ops"

    def test_custom_params(self) -> None:
        idx = HNSWIndex(
            fields=("embedding",),
            m=32,
            ef_construction=400,
            dist_metric="vector_cosine_ops",
        )
        assert idx.m == 32
        assert idx.ef_construction == 400
        assert idx.dist_metric == "vector_cosine_ops"

    def test_dist_metric_validation(self) -> None:
        with pytest.raises(IndexDefinitionError, match="Invalid dist_metric"):
            HNSWIndex(fields=("embedding",), dist_metric="euclidean")

    def test_dist_metric_valid_options(self) -> None:
        for metric in (
            "vector_l2_ops",
            "vector_ip_ops",
            "vector_cosine_ops",
            "halfvec_l2_ops",
            "halfvec_ip_ops",
            "halfvec_cosine_ops",
        ):
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
        idx = IVFFlatIndex(
            fields=("embedding",), lists=200, dist_metric="vector_ip_ops"
        )
        assert idx.lists == 200
        assert idx.dist_metric == "vector_ip_ops"

    def test_dist_metric_validation(self) -> None:
        with pytest.raises(IndexDefinitionError, match="Invalid dist_metric"):
            IVFFlatIndex(fields=("embedding",), dist_metric="vector_cosine_ops")

    def test_dist_metric_valid_options(self) -> None:
        for metric in (
            "vector_l2_ops",
            "vector_ip_ops",
            "halfvec_l2_ops",
            "halfvec_ip_ops",
        ):
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


class TestHNSWIndexSql:
    """HNSWIndex.get_sql() DDL generation."""

    def test_get_sql_safe(self) -> None:
        idx = HNSWIndex(fields=("embedding",), m=32, ef_construction=400)
        sql = idx.get_sql(FakeSchemaGenerator(), FakeModel("chunks"), safe=True)
        assert sql.startswith('CREATE INDEX IF NOT EXISTS "hnsw_FakeModel_embedding"')
        assert '"chunks"' in sql
        assert 'USING hnsw ("embedding" vector_l2_ops)' in sql
        assert "WITH (m = 32, ef_construction = 400);" in sql

    def test_get_sql_unsafe_custom(self) -> None:
        idx = HNSWIndex(
            fields=("embedding",),
            name="emb_idx",
            dist_metric="vector_cosine_ops",
        )
        sql = idx.get_sql(FakeSchemaGenerator(), FakeModel("chunks"), safe=False)
        assert sql.startswith('CREATE INDEX "emb_idx"')
        assert "IF NOT EXISTS" not in sql
        assert "vector_cosine_ops" in sql

    def test_get_sql_escapes_embedded_quote_name(self) -> None:
        """A custom name containing SQL must stay inside the quoted identifier."""
        idx = HNSWIndex(fields=("embedding",), name='x"; DROP TABLE t;--')
        sql = idx.get_sql(FakeSchemaGenerator(), FakeModel("chunks"), safe=False)
        # The embedded double quote is escaped as "" — the full payload stays
        # inside one quoted identifier, so it cannot break out of the DDL.
        assert sql.startswith('CREATE INDEX "x""; DROP TABLE t;--" ON "chunks" ')


class TestIVFFlatIndexSql:
    """IVFFlatIndex.get_sql() DDL generation."""

    def test_get_sql_safe(self) -> None:
        idx = IVFFlatIndex(fields=("embedding",), lists=200)
        sql = idx.get_sql(FakeSchemaGenerator(), FakeModel("chunks"), safe=True)
        assert sql.startswith(
            'CREATE INDEX IF NOT EXISTS "ivfflat_FakeModel_embedding"'
        )
        assert 'USING ivfflat ("embedding" vector_l2_ops)' in sql
        assert "WITH (lists = 200);" in sql

    def test_get_sql_unsafe_custom(self) -> None:
        idx = IVFFlatIndex(
            fields=("embedding",),
            name="ivf_idx",
            lists=50,
            dist_metric="vector_ip_ops",
        )
        sql = idx.get_sql(FakeSchemaGenerator(), FakeModel("chunks"), safe=False)
        assert sql.startswith('CREATE INDEX "ivf_idx"')
        assert "IF NOT EXISTS" not in sql
        assert "vector_ip_ops" in sql
        assert "WITH (lists = 50);" in sql

    def test_get_sql_escapes_embedded_quote_name(self) -> None:
        """A custom name containing SQL must stay inside the quoted identifier."""
        idx = IVFFlatIndex(fields=("embedding",), name='x"; DROP TABLE t;--')
        sql = idx.get_sql(FakeSchemaGenerator(), FakeModel("chunks"), safe=False)
        assert sql.startswith('CREATE INDEX "x""; DROP TABLE t;--" ON "chunks" ')


class TestDialectGuard:
    """G8 — HNSW/IVFFlat/GiST must refuse non-PostgreSQL schema generators."""

    def _sqlite_generator(self) -> FakeSchemaGenerator:
        gen = FakeSchemaGenerator()
        gen.DIALECT = "sqlite"
        return gen

    def test_hnsw_raises_on_sqlite(self) -> None:
        idx = HNSWIndex(fields=("embedding",))
        with pytest.raises(IndexDefinitionError, match="PostgreSQL-only"):
            idx.get_sql(self._sqlite_generator(), FakeModel("chunks"), safe=True)

    def test_ivfflat_raises_on_sqlite(self) -> None:
        idx = IVFFlatIndex(fields=("embedding",))
        with pytest.raises(IndexDefinitionError, match="PostgreSQL-only"):
            idx.get_sql(self._sqlite_generator(), FakeModel("chunks"), safe=True)

    def test_hnsw_accepts_postgres(self) -> None:
        gen = FakeSchemaGenerator()
        gen.DIALECT = "postgres"
        sql = HNSWIndex(fields=("embedding",)).get_sql(
            gen, FakeModel("chunks"), safe=False
        )
        assert sql.startswith('CREATE INDEX "hnsw_FakeModel_embedding"')
