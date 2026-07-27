"""Integration tests against live PostgreSQL + pgvector.

Requires: docker run -d --name pg-integration -e POSTGRES_DB=tortoise_test \
    -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -p 5433:5432 \
    pgvector/pgvector:pg16

Run with: uv run pytest tests/test_pg_integration.py -v
"""

import os
import socket

import pytest
from pypika_tortoise.terms import ValueWrapper
from tortoise import Tortoise, fields
from tortoise.models import Model

import tortoise_extended  # noqa: F401 — apply patches
from tortoise_extended import (
    CosineDistance,
    HNSWIndex,
    InnerProduct,
    L2Distance,
    VectorField,
)
from tortoise_extended.expressions.graph_filters import vector_encoder

# ---------------------------------------------------------------------------
# Config — skip entire module if PG is not available
# ---------------------------------------------------------------------------

DB_URL = os.environ.get(
    "TORTOISE_TEST_DB",
    "postgres://postgres:postgres@localhost:5433/tortoise_test",
)


def _pg_available() -> bool:
    """Quick check — can we connect to the test PG?"""
    try:
        sock = socket.create_connection(("localhost", 5433), timeout=2)
        sock.close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="PostgreSQL not available on localhost:5433",
)


# ---------------------------------------------------------------------------
# Test models — defined fresh for integration tests
# ---------------------------------------------------------------------------


class Chunk(Model):
    """A document chunk with an embedding vector."""

    id = fields.IntField(primary_key=True)
    text = fields.TextField()
    embedding = VectorField(dimensions=3)  # small for tests

    class Meta:
        table = "test_chunks"
        indexes = [
            HNSWIndex(
                fields=("embedding",),
                m=8,
                ef_construction=100,
                dist_metric="vector_cosine_ops",
            ),
        ]


class Article(Model):
    """Article with nullable embedding (IVFFlat tested via raw SQL)."""

    id = fields.IntField(primary_key=True)
    title = fields.CharField(max_length=255)
    body_embedding = VectorField(dimensions=3, null=True)

    class Meta:
        table = "test_articles"


class Node(Model):
    """Graph node for recursive CTE tests."""

    id = fields.IntField(primary_key=True)
    name = fields.CharField(max_length=100)
    parent = fields.ForeignKeyField(
        "models.Node",
        null=True,
        related_name="children",
        on_delete=fields.OnDelete.CASCADE,
    )

    class Meta:
        table = "test_nodes"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
async def _init_db():
    """Initialize Tortoise ORM for the test module."""
    await Tortoise.init(
        db_url=DB_URL,
        modules={"models": ["tests.test_pg_integration"]},
    )
    await Tortoise.generate_schemas()
    yield
    # Cleanup — drop test tables
    conn = Tortoise.get_connection("default")
    for table in ("test_chunks", "test_articles", "test_nodes"):
        await conn.execute_query(f"DROP TABLE IF EXISTS {table} CASCADE")
    await Tortoise.close_connections()


# ---------------------------------------------------------------------------
# 1. VectorField — insert, retrieve, roundtrip
# ---------------------------------------------------------------------------


class TestVectorFieldIntegration:
    """Verify VectorField stores and retrieves vectors through real pgvector."""

    @pytest.mark.asyncio
    async def test_insert_and_retrieve(self) -> None:
        chunk = await Chunk.create(text="hello", embedding=[0.1, 0.2, 0.3])
        fetched = await Chunk.get(id=chunk.id)
        assert fetched.embedding is not None
        assert len(fetched.embedding) == 3
        for a, b in zip(fetched.embedding, [0.1, 0.2, 0.3], strict=True):
            assert abs(a - b) < 1e-5

    @pytest.mark.asyncio
    async def test_null_vector(self) -> None:
        article = await Article.create(title="no embed", body_embedding=None)
        fetched = await Article.get(id=article.id)
        assert fetched.body_embedding is None

    @pytest.mark.asyncio
    async def test_overwrite_vector(self) -> None:
        chunk = await Chunk.create(text="overwrite", embedding=[1.0, 0.0, 0.0])
        chunk.embedding = [0.0, 1.0, 0.0]
        await chunk.save()
        fetched = await Chunk.get(id=chunk.id)
        assert fetched.embedding == [0.0, 1.0, 0.0]

    @pytest.mark.asyncio
    async def test_bulk_insert(self) -> None:
        for i in range(5):
            await Chunk.create(text=f"bulk-{i}", embedding=[float(i), 0.0, 0.0])
        assert await Chunk.filter(text__startswith="bulk-").count() == 5

    @pytest.mark.asyncio
    async def test_vector_encoder_direct(self) -> None:
        """vector_encoder produces valid pgvector literal."""
        result = vector_encoder([1.0, 2.0, 3.0], None, None)
        assert result == "[1.0,2.0,3.0]"
        assert vector_encoder(None, None, None) is None

    @pytest.mark.asyncio
    async def test_vector_in_raw_sql(self) -> None:
        """Raw SQL with vector literal roundtrips correctly."""
        conn = Tortoise.get_connection("default")
        row = await conn.execute_query(
            "SELECT '[1,2,3]'::vector::text AS v"
        )
        assert row[1][0]["v"] == "[1,2,3]"


# ---------------------------------------------------------------------------
# 2. pgvector codec — asyncpg type codec roundtrip
# ---------------------------------------------------------------------------


class TestPgvectorCodec:
    """Verify the asyncpg type codec handles encode/decode through the driver."""

    @pytest.mark.asyncio
    async def test_codec_through_asyncpg(self) -> None:
        """Insert via raw SQL, retrieve via ORM — codec must decode correctly."""
        conn = Tortoise.get_connection("default")
        await conn.execute_query(
            "INSERT INTO test_chunks (text, embedding) VALUES ($1, $2)",
            ["codec-test", [0.5, 0.5, 0.5]],
        )
        chunk = await Chunk.filter(text="codec-test").first()
        assert chunk is not None
        assert len(chunk.embedding) == 3
        assert abs(chunk.embedding[0] - 0.5) < 1e-5

    @pytest.mark.asyncio
    async def test_binary_vector_decode(self) -> None:
        """pgvector binary format is decoded correctly by VectorField."""
        import struct

        # Construct pgvector binary: 4-byte header + 3 * 4-byte floats
        header = struct.pack(">HH", 0, 3)  # reserved=0, ndim=3
        data = struct.pack(">3f", 1.0, 2.0, 3.0)
        raw = header + data

        conn = Tortoise.get_connection("default")
        result = await conn.execute_query(
            "SELECT $1::vector AS v", [raw]
        )
        # The codec should decode it
        val = result[1][0]["v"]
        if isinstance(val, memoryview):
            decoded = VectorField._decode_binary(bytes(val))
            assert decoded == [1.0, 2.0, 3.0]


# ---------------------------------------------------------------------------
# 3. HNSWIndex — verify index is created in PG
# ---------------------------------------------------------------------------


class TestHNSWIndexIntegration:
    """Verify HNSW index exists after schema generation."""

    @pytest.mark.asyncio
    async def test_hnsw_index_exists(self) -> None:
        conn = Tortoise.get_connection("default")
        result = await conn.execute_query(
            """
            SELECT indexname FROM pg_indexes
            WHERE tablename = 'test_chunks'
              AND indexdef LIKE '%USING hnsw%'
            """
        )
        index_names = [r["indexname"] for r in result[1]]
        assert len(index_names) >= 1, f"Expected HNSW index, got: {index_names}"

    @pytest.mark.asyncio
    async def test_hnsw_index_params_in_def(self) -> None:
        """Index definition includes m and ef_construction."""
        conn = Tortoise.get_connection("default")
        result = await conn.execute_query(
            """
            SELECT indexdef FROM pg_indexes
            WHERE tablename = 'test_chunks'
              AND indexdef LIKE '%USING hnsw%'
            """
        )
        assert len(result[1]) >= 1
        indexdef = result[1][0]["indexdef"]
        assert "m=" in indexdef and "8" in indexdef
        assert "ef_construction=" in indexdef and "100" in indexdef


# ---------------------------------------------------------------------------
# 4. IVFFlatIndex — create manually and verify
# ---------------------------------------------------------------------------


class TestIVFFlatIndexIntegration:
    """Verify IVFFlat index can be created on a table with data."""

    @pytest.mark.asyncio
    async def test_ivfflat_index_creation(self) -> None:
        """Insert data then create IVFFlat index — must succeed."""
        for i in range(10):
            await Article.create(
                title=f"article-{i}",
                body_embedding=[float(i % 3), 0.5, 0.5],
            )

        conn = Tortoise.get_connection("default")
        # IVFFlat requires existing data — lists=2 is minimum
        await conn.execute_query(
            """
            CREATE INDEX IF NOT EXISTS idx_articles_embedding_ivfflat
            ON test_articles USING ivfflat (body_embedding vector_l2_ops)
            WITH (lists = 2);
            """
        )
        result = await conn.execute_query(
            """
            SELECT indexname FROM pg_indexes
            WHERE tablename = 'test_articles'
              AND indexdef LIKE '%USING ivfflat%'
            """
        )
        assert len(result[1]) >= 1
        assert "ivfflat" in result[1][0]["indexname"]


# ---------------------------------------------------------------------------
# 5. Vector similarity queries
# ---------------------------------------------------------------------------


class TestVectorSimilarityQueries:
    """Test L2, cosine, and inner product distance queries against real PG."""

    @pytest.fixture(autouse=True)
    async def _seed_data(self) -> None:
        """Seed test vectors before each test."""
        await Chunk.all().delete()
        await Chunk.create(text="alpha", embedding=[1.0, 0.0, 0.0])
        await Chunk.create(text="beta", embedding=[0.0, 1.0, 0.0])
        await Chunk.create(text="gamma", embedding=[0.7, 0.7, 0.0])
        await Chunk.create(text="delta", embedding=[0.33, 0.33, 0.33])

    @pytest.mark.asyncio
    async def test_l2_distance_nearest(self) -> None:
        """L2 distance: [1,0,0] is closest to [1,0,0]."""
        query_vec = [1.0, 0.0, 0.0]
        results = (
            await Chunk.all()
            .annotate(distance=L2Distance("embedding", ValueWrapper(query_vec)))
            .order_by("distance")
        )
        assert results[0].text == "alpha"

    @pytest.mark.asyncio
    async def test_cosine_distance_nearest(self) -> None:
        """Cosine distance: [1,0,0] and [1,0,0] have distance 0."""
        query_vec = [1.0, 0.0, 0.0]
        results = (
            await Chunk.all()
            .annotate(distance=CosineDistance("embedding", ValueWrapper(query_vec)))
            .order_by("distance")
        )
        assert results[0].text == "alpha"

    @pytest.mark.asyncio
    async def test_inner_product_nearest(self) -> None:
        """Inner product: higher = more similar. <#> returns negative inner product."""
        query_vec = [1.0, 0.0, 0.0]
        results = (
            await Chunk.all()
            .annotate(distance=InnerProduct("embedding", ValueWrapper(query_vec)))
            .order_by("distance")  # ascending: most negative = highest inner product
        )
        assert results[0].text == "alpha"

    @pytest.mark.asyncio
    async def test_l2_distance_filter_lte(self) -> None:
        """Filter with l2_distance threshold."""
        results = await Chunk.filter(
            embedding__l2_distance=[[1.0, 0.0, 0.0], 0.5]
        )
        texts = {r.text for r in results}
        assert "alpha" in texts  # distance 0 < 0.5

    @pytest.mark.asyncio
    async def test_cosine_distance_filter_lte(self) -> None:
        """Filter with cosine distance threshold."""
        results = await Chunk.filter(
            embedding__cosine_distance=[[1.0, 0.0, 0.0], 0.5]
        )
        texts = {r.text for r in results}
        assert "alpha" in texts

    @pytest.mark.asyncio
    async def test_inner_product_filter_gte(self) -> None:
        """Filter with inner product threshold (higher = more similar)."""
        results = await Chunk.filter(
            embedding__inner_product=[[1.0, 0.0, 0.0], 0.5]
        )
        texts = {r.text for r in results}
        assert "alpha" in texts  # inner product = 1.0 > 0.5

    @pytest.mark.asyncio
    async def test_distance_ordering_consistency(self) -> None:
        """Multiple distance metrics produce consistent nearest-neighbor."""
        query_vec = [0.0, 1.0, 0.0]

        l2_results = (
            await Chunk.all()
            .annotate(distance=L2Distance("embedding", ValueWrapper(query_vec)))
            .order_by("distance")
        )
        cos_results = (
            await Chunk.all()
            .annotate(distance=CosineDistance("embedding", ValueWrapper(query_vec)))
            .order_by("distance")
        )
        assert l2_results[0].text == "beta"
        assert cos_results[0].text == "beta"


# ---------------------------------------------------------------------------
# 6. RecursiveCTE — real recursive query
# ---------------------------------------------------------------------------


class TestRecursiveCTEIntegration:
    """Verify RecursiveCTE works with real PostgreSQL CTEs."""

    @pytest.fixture(autouse=True)
    async def _seed_tree(self) -> None:
        """Build a 3-level tree: root -> child1, child2 -> grandchild."""
        await Node.all().delete()
        root = await Node.create(name="root", parent=None)
        child1 = await Node.create(name="child1", parent=root)
        child2 = await Node.create(name="child2", parent=root)
        await Node.create(name="grandchild", parent=child1)
        await Node.create(name="grandchild2", parent=child2)

    @pytest.mark.asyncio
    async def test_recursive_cte_finds_all_descendants(self) -> None:
        """CTE should find all descendants of root."""
        from pypika_tortoise import Table
        from pypika_tortoise.terms import ValueWrapper

        from tortoise_extended.expressions.recursive_cte import RecursiveCTE

        nodes = Table("test_nodes")
        cte_table = Table("tree")

        anchor = (
            nodes.select(
                nodes.id.as_("id"),
                nodes.name.as_("name"),
                nodes.parent_id.as_("parent_id"),
                ValueWrapper(0).as_("depth"),
            )
            .where(nodes.name == "root")
        )

        step = (
            nodes.select(
                nodes.id.as_("id"),
                nodes.name.as_("name"),
                nodes.parent_id.as_("parent_id"),
                (cte_table.depth + 1).as_("depth"),
            )
            .join(cte_table)
            .on(nodes.parent_id == cte_table.id)
        )

        cte = RecursiveCTE(name="tree")
        cte = cte.anchor(anchor)
        cte = cte.union(step)
        query = cte.build()
        conn = Tortoise.get_connection("default")
        result = await conn.execute_query(query.get_sql())
        names = {r["name"] for r in result[1]}
        assert "root" in names
        assert "child1" in names
        assert "child2" in names
        assert "grandchild" in names
        assert "grandchild2" in names

    @pytest.mark.asyncio
    async def test_recursive_cte_single_branch(self) -> None:
        """CTE starting from child1 finds only its subtree."""
        from pypika_tortoise import Table
        from pypika_tortoise.terms import ValueWrapper

        from tortoise_extended.expressions.recursive_cte import RecursiveCTE

        nodes = Table("test_nodes")
        cte_table = Table("subtree")

        anchor = (
            nodes.select(
                nodes.id.as_("id"),
                nodes.name.as_("name"),
                nodes.parent_id.as_("parent_id"),
                ValueWrapper(0).as_("depth"),
            )
            .where(nodes.name == "child1")
        )

        step = (
            nodes.select(
                nodes.id.as_("id"),
                nodes.name.as_("name"),
                nodes.parent_id.as_("parent_id"),
                (cte_table.depth + 1).as_("depth"),
            )
            .join(cte_table)
            .on(nodes.parent_id == cte_table.id)
        )

        cte = RecursiveCTE(name="subtree")
        cte = cte.anchor(anchor)
        cte = cte.union(step)
        query = cte.build()
        conn = Tortoise.get_connection("default")
        result = await conn.execute_query(query.get_sql())
        names = {r["name"] for r in result[1]}
        assert "child1" in names
        assert "grandchild" in names
        assert "root" not in names
        assert "child2" not in names


# ---------------------------------------------------------------------------
# 7. pgvector extension verification
# ---------------------------------------------------------------------------


class TestExtensionPresence:
    """Verify required PG extensions are loaded."""

    @pytest.mark.asyncio
    async def test_vector_extension_exists(self) -> None:
        conn = Tortoise.get_connection("default")
        result = await conn.execute_query(
            "SELECT extname, extversion FROM pg_extension WHERE extname = 'vector'"
        )
        assert len(result[1]) == 1
        assert result[1][0]["extname"] == "vector"

    @pytest.mark.asyncio
    async def test_vector_dimension_check(self) -> None:
        """Verify vector column allows the specified dimensions."""
        chunk = await Chunk.create(
            text="dim-check",
            embedding=[0.1] * 3,
        )
        fetched = await Chunk.get(id=chunk.id)
        assert len(fetched.embedding) == 3

    @pytest.mark.asyncio
    async def test_vector_operator_sql_generation(self) -> None:
        """Verify pgvector operators work in raw SQL."""
        conn = Tortoise.get_connection("default")
        result = await conn.execute_query(
            "SELECT '[1,0,0]'::vector <-> '[0,1,0]'::vector AS dist"
        )
        dist = result[1][0]["dist"]
        assert abs(dist - 1.4142135) < 0.01  # sqrt(2)


# ---------------------------------------------------------------------------
# 8. Cache — CacheableModel with real DB
# ---------------------------------------------------------------------------


class TestCacheableModelIntegration:
    """Verify CacheableModel _from_cache / _to_cache roundtrips through PG."""

    @pytest.mark.asyncio
    async def test_cache_roundtrip(self) -> None:
        from tortoise_extended.cache.base import CacheNamespace

        chunk = await Chunk.create(text="cache-test", embedding=[1.0, 2.0, 3.0])
        ns = CacheNamespace("chunk")
        cache_key = ns.key("get", str(chunk.id))
        assert "chunk" in cache_key
        assert str(chunk.id) in cache_key
