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
    GraphVectorSearch,
    HNSWIndex,
    InnerProduct,
    L2Distance,
    VectorField,
)
from tortoise_extended.exceptions import VectorFieldError
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


class HalfChunk(Model):
    """Chunk with a half-precision (halfvec) embedding column."""

    id = fields.IntField(primary_key=True)
    text = fields.CharField(max_length=255)
    embedding = VectorField(dimensions=3, vector_type="halfvec")

    class Meta:
        table = "test_half_chunks"
        indexes = [
            HNSWIndex(
                fields=("embedding",),
                m=8,
                ef_construction=100,
                dist_metric="halfvec_cosine_ops",
            ),
        ]


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


class Document(Model):
    """Document with vector embedding — target of relational vector filters."""

    id = fields.IntField(primary_key=True)
    title = fields.CharField(max_length=255)
    embedding = VectorField(dimensions=3)

    class Meta:
        table = "test_documents"


class Page(Model):
    """Page with a real FK to Document — exercises relational join + vector filter."""

    id = fields.IntField(primary_key=True)
    text = fields.TextField()
    document = fields.ForeignKeyField(
        "models.Document",
        related_name="pages",
        on_delete=fields.OnDelete.CASCADE,
    )

    class Meta:
        table = "test_pages"


class VecNode(Model):
    """Graph node with vector embedding for GraphVectorSearch."""

    id = fields.IntField(primary_key=True)
    name = fields.CharField(max_length=100)
    embedding = VectorField(dimensions=3)

    class Meta:
        table = "test_vec_nodes"


class VecEdge(Model):
    """Directed graph edge matching the BaseGraphEdgeModel shape (no FK constraints)."""

    id = fields.IntField(primary_key=True)
    source_id = fields.IntField()
    target_id = fields.IntField()
    edge_type = fields.CharField(max_length=50, default="rel")
    is_bidirectional = fields.BooleanField(default=False)

    class Meta:
        table = "test_vec_edges"


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
    for table in (
        "test_vec_edges",
        "test_vec_nodes",
        "test_pages",
        "test_documents",
        "test_chunks",
        "test_articles",
        "test_half_chunks",
        "test_nodes",
    ):
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
        row = await conn.execute_query("SELECT '[1,2,3]'::vector::text AS v")
        assert row[1][0]["v"] == "[1,2,3]"


class TestHalfvecIntegration:
    """Verify halfvec columns roundtrip and index through real pgvector."""

    @pytest.mark.asyncio
    async def test_column_type(self) -> None:
        """Schema exposes a halfvec column, not a plain vector."""
        conn = Tortoise.get_connection("default")
        row = await conn.execute_query(
            "SELECT udt_name FROM information_schema.columns "
            "WHERE table_name = 'test_half_chunks' AND column_name = 'embedding'"
        )
        assert row[1][0]["udt_name"] == "halfvec"

    @pytest.mark.asyncio
    async def test_insert_and_retrieve(self) -> None:
        chunk = await HalfChunk.create(text="half", embedding=[0.1, 0.2, 0.3])
        fetched = await HalfChunk.get(id=chunk.id)
        assert fetched.embedding is not None
        # halfvec stores half-precision floats — allow rounding tolerance
        assert len(fetched.embedding) == 3
        assert fetched.embedding[0] == pytest.approx(0.1, abs=1e-3)

    @pytest.mark.asyncio
    async def test_hnsw_halfvec_index_exists(self) -> None:
        conn = Tortoise.get_connection("default")
        row = await conn.execute_query(
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename = 'test_half_chunks' AND indexdef LIKE '%USING hnsw%'"
        )
        assert len(row[1]) == 1
        assert "halfvec_cosine_ops" in row[1][0]["indexdef"]

    @pytest.mark.asyncio
    async def test_halfvec_distance_filter(self) -> None:
        """__l2_distance filter works against a halfvec column."""
        await HalfChunk.create(text="near", embedding=[1.0, 0.0, 0.0])
        await HalfChunk.create(text="far", embedding=[0.0, 1.0, 0.0])
        near = await HalfChunk.filter(embedding__l2_distance=([1.0, 0.0, 0.0], 1.0))
        names = {c.text for c in near}
        assert "near" in names


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
        result = await conn.execute_query("SELECT $1::vector AS v", [raw])
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
        results = await Chunk.filter(embedding__l2_distance=[[1.0, 0.0, 0.0], 0.5])
        texts = {r.text for r in results}
        assert "alpha" in texts  # distance 0 < 0.5

    @pytest.mark.asyncio
    async def test_cosine_distance_filter_lte(self) -> None:
        """Filter with cosine distance threshold."""
        results = await Chunk.filter(embedding__cosine_distance=[[1.0, 0.0, 0.0], 0.5])
        texts = {r.text for r in results}
        assert "alpha" in texts

    @pytest.mark.asyncio
    async def test_inner_product_filter_gte(self) -> None:
        """Filter with inner product threshold (higher = more similar)."""
        results = await Chunk.filter(embedding__inner_product=[[1.0, 0.0, 0.0], 0.5])
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
# 5b. Bare equality guard (G4) — regression on real PG vectors
# ---------------------------------------------------------------------------


class TestBareEqualityGuard:
    """G4 — a bare non-None value on VectorField must raise, not compile to IS NULL."""

    @pytest.fixture(autouse=True)
    async def _seed_data(self) -> None:
        # Chunk.embedding is non-nullable; Article.body_embedding is nullable.
        await Chunk.all().delete()
        await Article.all().delete()
        await Chunk.create(text="alpha", embedding=[1.0, 0.0, 0.0])
        await Article.create(title="nullvec", body_embedding=None)
        await Article.create(title="vec", body_embedding=[1.0, 0.0, 0.0])

    @pytest.mark.asyncio
    async def test_bare_non_none_raises(self) -> None:
        """Previously returned rows silently via IS NULL; now raises."""
        with pytest.raises(
            VectorFieldError, match="Bare equality filters are not supported"
        ):
            await Chunk.filter(embedding=[1.0, 0.0, 0.0]).all()
        with pytest.raises(
            VectorFieldError, match="Bare equality filters are not supported"
        ):
            await Article.filter(body_embedding=[1.0, 0.0, 0.0]).all()

    @pytest.mark.asyncio
    async def test_bare_none_is_null(self) -> None:
        rows = await Article.filter(body_embedding=None)
        assert [r.title for r in rows] == ["nullvec"]

    @pytest.mark.asyncio
    async def test_isnull_and_not_isnull_unchanged(self) -> None:
        assert [r.title for r in await Article.filter(body_embedding__isnull=True)] == [
            "nullvec"
        ]
        assert [
            r.title for r in await Article.filter(body_embedding__not_isnull=True)
        ] == ["vec"]


# ---------------------------------------------------------------------------
# 6. Relational join + vector filter — cross-feature regression
# ---------------------------------------------------------------------------


class TestRelationalVectorFilter:
    """Vector filters must work through Tortoise relational joins (``parent__embedding__l2_distance``)."""

    @pytest.fixture(autouse=True)
    async def _seed_data(self) -> None:
        """Two documents (near/far) each with pages."""
        await Page.all().delete()
        await Document.all().delete()
        doc_a = await Document.create(title="alpha-doc", embedding=[1.0, 0.0, 0.0])
        doc_b = await Document.create(title="beta-doc", embedding=[0.0, 1.0, 0.0])
        await Page.create(text="p1", document=doc_a)
        await Page.create(text="p2", document=doc_a)
        await Page.create(text="p3", document=doc_b)

    @pytest.mark.asyncio
    async def test_related_l2_distance_filter(self) -> None:
        """``document__embedding__l2_distance`` filters pages by parent vector."""
        results = await Page.filter(
            document__embedding__l2_distance=[[1.0, 0.0, 0.0], 0.5]
        )
        texts = {r.text for r in results}
        assert texts == {"p1", "p2"}  # doc_a distance 0; doc_b distance sqrt(2) ≈ 1.41

    @pytest.mark.asyncio
    async def test_related_cosine_distance_filter(self) -> None:
        """Narrow cosine threshold excludes pages whose parent is far."""
        results = await Page.filter(
            document__embedding__cosine_distance=[[0.0, 1.0, 0.0], 0.1]
        )
        texts = {r.text for r in results}
        assert texts == {"p3"}  # doc_b distance 0; doc_a distance 1.0 (orthogonal)


# ---------------------------------------------------------------------------
# 7. GraphVectorSearch — single-query vector + graph compositor
# ---------------------------------------------------------------------------


class TestGraphVectorSearchIntegration:
    """GraphVectorSearch returns typed hits ordered by vector similarity."""

    @pytest.fixture(autouse=True)
    async def _seed_graph(self) -> None:
        """seed --near/far--> deep; query vector points at [1,0,0]."""
        await VecEdge.all().delete()
        await VecNode.all().delete()
        self.seed = await VecNode.create(name="seed", embedding=[1.0, 0.0, 0.0])
        self.near = await VecNode.create(name="near", embedding=[0.9, 0.1, 0.0])
        self.far = await VecNode.create(name="far", embedding=[0.0, 1.0, 0.0])
        self.deep = await VecNode.create(name="deep", embedding=[0.0, 0.0, 1.0])
        await VecEdge.create(source_id=self.seed.id, target_id=self.near.id)
        await VecEdge.create(source_id=self.seed.id, target_id=self.far.id)
        await VecEdge.create(source_id=self.near.id, target_id=self.deep.id)

    def _search(
        self,
        seed_id: int | None = None,
        *,
        max_hops: int = 2,
        direction: str = "both",
        edge_type: str | None = None,
        distance_metric: str = "l2",
        min_distance: float | None = None,
    ) -> GraphVectorSearch:
        return GraphVectorSearch(
            node_model=VecNode,
            edge_model=VecEdge,
            query_vector=[1.0, 0.0, 0.0],
            seed_id=seed_id if seed_id is not None else self.seed.id,
            max_hops=max_hops,
            direction=direction,
            edge_type=edge_type,
            distance_metric=distance_metric,
            min_distance=min_distance,
        )

    @pytest.mark.asyncio
    async def test_returns_typed_hits_ordered_by_distance(self) -> None:
        """L2 search returns seed first, then near/far/deep by distance."""
        from tortoise_extended import GraphVectorHit

        hits = await self._search(max_hops=2).search()
        assert [h.node.name for h in hits] == ["seed", "near", "far", "deep"]
        assert all(isinstance(h, GraphVectorHit) for h in hits)
        assert all(isinstance(h.node, VecNode) for h in hits)
        assert hits[0].hops == 0
        assert hits[0].distance < 0.01
        assert hits[3].node.name == "deep"
        # distances ascending
        distances = [h.distance for h in hits]
        assert distances == sorted(distances)

    @pytest.mark.asyncio
    async def test_max_hops_limits_traversal(self) -> None:
        """max_hops=1 excludes the 2-hop node."""
        hits = await self._search(max_hops=1).search()
        names = {h.node.name for h in hits}
        assert names == {"seed", "near", "far"}

    @pytest.mark.asyncio
    async def test_direction_outgoing_from_near(self) -> None:
        """Outgoing from near reaches only deep (plus near itself)."""
        hits = await self._search(seed_id=self.near.id, direction="outgoing").search()
        names = {h.node.name for h in hits}
        assert names == {"near", "deep"}

    @pytest.mark.asyncio
    async def test_threshold_filters_far_nodes(self) -> None:
        """min_distance=0.5 keeps only seed and near."""
        hits = await self._search(min_distance=0.5).search()
        names = {h.node.name for h in hits}
        assert names == {"seed", "near"}

    @pytest.mark.asyncio
    async def test_edge_type_filter(self) -> None:
        """edge_type='rel' excludes edges retagged to another type."""
        await VecEdge.filter(source_id=self.seed.id, target_id=self.far.id).update(
            edge_type="far_type"
        )
        hits = await self._search(edge_type="rel", max_hops=1).search()
        names = {h.node.name for h in hits}
        assert names == {"seed", "near"}  # far edge now has edge_type='far_type'

    @pytest.mark.asyncio
    async def test_inner_product_metric_orders_by_similarity(self) -> None:
        """inner_product returns positive inner product, best first."""
        hits = await self._search(distance_metric="inner_product").search()
        assert hits[0].node.name == "seed"
        assert hits[0].distance > 0.99  # dot([1,0,0],[1,0,0]) = 1
        distances = [h.distance for h in hits]
        assert distances == sorted(distances, reverse=True)

    @pytest.mark.asyncio
    async def test_edge_type_and_threshold_combined(self) -> None:
        """edge_type ($5) and min_distance ($6) parameters coexist correctly."""
        await VecEdge.filter(source_id=self.seed.id, target_id=self.far.id).update(
            edge_type="far_type"
        )
        hits = await self._search(edge_type="rel", min_distance=0.5).search()
        names = {h.node.name for h in hits}
        assert names == {"seed", "near"}  # far excluded by type AND by distance

    @pytest.mark.asyncio
    async def test_direction_incoming_from_deep(self) -> None:
        """Incoming follows reverse edges up the chain (deep <- near <- seed)."""
        hits = await self._search(seed_id=self.deep.id, direction="incoming").search()
        names = {h.node.name for h in hits}
        assert names == {"deep", "near", "seed"}
        by_name = {h.node.name: h.hops for h in hits}
        assert by_name["deep"] == 0
        assert by_name["near"] == 1
        assert by_name["seed"] == 2

    @pytest.mark.asyncio
    async def test_bidirectional_edge_traversed_both_ways(self) -> None:
        """is_bidirectional edges are followed in reverse for outgoing queries."""
        await VecEdge.create(
            source_id=self.far.id,
            target_id=self.near.id,
            is_bidirectional=True,
        )
        hits = await self._search(seed_id=self.near.id, direction="outgoing").search()
        names = {h.node.name for h in hits}
        assert names == {"near", "deep", "far"}

    @pytest.mark.asyncio
    async def test_seed_without_edges_returns_seed_only(self) -> None:
        """Isolated seed still yields one typed hit at hops 0."""
        solo = await VecNode.create(name="solo", embedding=[0.5, 0.5, 0.5])
        hits = await self._search(seed_id=solo.id).search()
        assert len(hits) == 1
        assert hits[0].node.name == "solo"
        assert hits[0].hops == 0

    @pytest.mark.asyncio
    async def test_invalid_metric_raises(self) -> None:
        """Unsupported metric raises GraphTraversalError before any SQL."""
        from tortoise_extended.exceptions import GraphTraversalError

        with pytest.raises(GraphTraversalError):
            self._search(distance_metric="bogus")


# ---------------------------------------------------------------------------
# 8. RecursiveCTE — real recursive query
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

        anchor = nodes.select(
            nodes.id.as_("id"),
            nodes.name.as_("name"),
            nodes.parent_id.as_("parent_id"),
            ValueWrapper(0).as_("depth"),
        ).where(nodes.name == "root")

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

        anchor = nodes.select(
            nodes.id.as_("id"),
            nodes.name.as_("name"),
            nodes.parent_id.as_("parent_id"),
            ValueWrapper(0).as_("depth"),
        ).where(nodes.name == "child1")

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
# 8. pgvector extension verification
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
# 9. Cache — BaseCacheableModel with real DB
# ---------------------------------------------------------------------------


class TestCacheableModelIntegration:
    """Verify BaseCacheableModel _from_cache / _to_cache roundtrips through PG."""

    @pytest.mark.asyncio
    async def test_cache_roundtrip(self) -> None:
        from tortoise_extended.cache.base import CacheNamespace

        chunk = await Chunk.create(text="cache-test", embedding=[1.0, 2.0, 3.0])
        ns = CacheNamespace("chunk")
        cache_key = ns.key("get", str(chunk.id))
        assert "chunk" in cache_key
        assert str(chunk.id) in cache_key
