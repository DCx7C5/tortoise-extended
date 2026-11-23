"""Integration tests for the :class:`Graph` facade against live PostgreSQL.

Requires the docker stack (see ``docker-compose.dev.yml``): PostgreSQL 18 with
pgvector/TimescaleDB on ``127.0.0.1:5434``. The module uses the unified test
database (all six extensions installed); override with ``TORTOISE_TEST_DB``.

Run with: uv run pytest tests/test_graph_facade.py -v
"""

import os
import socket
import uuid

import pytest
from tortoise import Tortoise, fields
from tortoise.models import Model

import tortoise_extended  # noqa: F401 — apply patches
from tortoise_extended import Graph, GraphVectorHit, VectorField
from tortoise_extended.exceptions import GraphError
from tortoise_extended.fields import CharField

# ---------------------------------------------------------------------------
# Config — skip entire module if PG is not available
# ---------------------------------------------------------------------------

DB_URL = os.environ.get(
    "TORTOISE_TEST_DB",
    "postgres://postgres:postgres@localhost:5434/tortoise_test_unified",
)


def _pg_available() -> bool:
    """Quick check — can we connect to the test PG?"""
    try:
        sock = socket.create_connection(("localhost", 5434), timeout=2)
        sock.close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="PostgreSQL not available on localhost:5434",
)


# ---------------------------------------------------------------------------
# Test models — defined fresh for facade tests
# ---------------------------------------------------------------------------


class FacadeNode(Model):
    """Graph node: traversal columns, a namespace and a 3-D embedding."""

    id = fields.UUIDField(primary_key=True, default=uuid.uuid4)
    name = CharField(max_length=100)
    depth = fields.IntField(default=0)
    namespace = CharField(max_length=100, default="default")
    embedding = VectorField(dimensions=3, null=True)

    class Meta:
        table = "graph_facade_nodes"


class FacadeEdge(Model):
    """Edge with every optional column the facade can write."""

    id = fields.UUIDField(primary_key=True, default=uuid.uuid4)
    source_id = fields.UUIDField()
    target_id = fields.UUIDField()
    edge_type = CharField(max_length=50, default="rel")
    weight = fields.FloatField(default=1.0)
    label = CharField(max_length=50, default="")
    namespace = CharField(max_length=100, default="default")
    is_bidirectional = fields.BooleanField(default=False)

    class Meta:
        table = "graph_facade_edges"


class NoSourceEdge(Model):
    """Edge model missing ``source_id`` — the facade must refuse it."""

    id = fields.UUIDField(primary_key=True, default=uuid.uuid4)
    target_id = fields.UUIDField()

    class Meta:
        table = "graph_facade_bad_edges"


class OtherNode(Model):
    """Unrelated model — passing it to the facade must raise ``GraphError``."""

    id = fields.UUIDField(primary_key=True, default=uuid.uuid4)
    name = CharField(max_length=100)

    class Meta:
        table = "graph_facade_other_nodes"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_TABLES = (
    "graph_facade_bad_edges",
    "graph_facade_edges",
    "graph_facade_nodes",
    "graph_facade_other_nodes",
)


@pytest.fixture(scope="module", autouse=True)
async def _init_db():
    """Initialize Tortoise ORM for the test module."""
    await Tortoise.init(
        db_url=DB_URL,
        modules={"models": ["tests.test_graph_facade"]},
    )
    conn = Tortoise.get_connection("default")
    # DROP before CREATE: generate_schemas() is IF NOT EXISTS, so a stale table
    # left behind by an aborted run would be silently reused.
    for table in _TABLES:
        await conn.execute_query(f"DROP TABLE IF EXISTS {table} CASCADE")
    await Tortoise.generate_schemas()
    yield
    for table in _TABLES:
        await conn.execute_query(f"DROP TABLE IF EXISTS {table} CASCADE")
    await Tortoise.close_connections()


@pytest.fixture(autouse=True)
async def _clean_tables():
    """Truncate the facade tables before every test."""
    conn = Tortoise.get_connection("default")
    await conn.execute_query(
        "TRUNCATE graph_facade_bad_edges, graph_facade_edges, "
        "graph_facade_nodes, graph_facade_other_nodes CASCADE"
    )
    yield


async def _make_pair() -> tuple[FacadeNode, FacadeNode]:
    """Create two isolated nodes ``a`` and ``b``."""
    a = await FacadeNode.create(name="a", depth=0)
    b = await FacadeNode.create(name="b", depth=1)
    return a, b


async def _make_chain() -> tuple[FacadeNode, FacadeNode, FacadeNode]:
    """Create ``a -> b -> c`` through the facade's ``link``."""
    a, b = await _make_pair()
    c = await FacadeNode.create(name="c", depth=2)
    graph = Graph(FacadeNode, FacadeEdge)
    await graph.link(a, b, edge_type="parent")
    await graph.link(b, c, edge_type="parent")
    return a, b, c


# ---------------------------------------------------------------------------
# link / unlink
# ---------------------------------------------------------------------------


class TestLink:
    """link() writes through the owned edge model unconditionally."""

    @pytest.mark.asyncio
    async def test_link_creates_edge_and_returns_model(self) -> None:
        a, b = await _make_pair()
        graph = Graph(FacadeNode, FacadeEdge)

        edge = await graph.link(a, b, edge_type="cites", weight=0.5, label="x")

        assert isinstance(edge, FacadeEdge)
        assert edge.source_id == a.id
        assert edge.target_id == b.id
        assert edge.edge_type == "cites"
        assert edge.weight == 0.5
        assert edge.label == "x"
        assert edge.namespace == "default"

    @pytest.mark.asyncio
    async def test_link_rejects_unknown_edge_field(self) -> None:
        a, b = await _make_pair()
        graph = Graph(FacadeNode, FacadeEdge)

        with pytest.raises(GraphError, match="no field"):
            await graph.link(a, b, nope=1)


class TestUnlink:
    """unlink() deletes one direction and returns the row count."""

    @pytest.mark.asyncio
    async def test_unlink_deletes_directed_edge_only(self) -> None:
        a, b = await _make_pair()
        graph = Graph(FacadeNode, FacadeEdge)
        await graph.link(a, b, edge_type="cites")
        await graph.link(b, a, edge_type="cites")

        assert await graph.unlink(a, b) == 1
        assert await graph.unlink(a, b) == 0
        # the reverse direction is untouched
        assert await FacadeEdge.filter(source_id=b.id, target_id=a.id).count() == 1


# ---------------------------------------------------------------------------
# neighbours
# ---------------------------------------------------------------------------


class TestNeighbours:
    """neighbours() returns hydrated model instances."""

    @pytest.mark.asyncio
    async def test_neighbours_returns_hydrated_models(self) -> None:
        a, b, _ = await _make_chain()
        graph = Graph(FacadeNode, FacadeEdge)

        outgoing = await graph.neighbours(a, direction="outgoing")
        assert [n.name for n in outgoing] == ["b"]
        assert all(isinstance(n, FacadeNode) for n in outgoing)

        both = await graph.neighbours(b, direction="both")
        assert sorted(n.name for n in both) == ["a", "c"]
        assert all(isinstance(n, FacadeNode) for n in both)

    @pytest.mark.asyncio
    async def test_neighbours_scopes_result_to_namespace(self) -> None:
        a, b = await _make_pair()
        outsider = await FacadeNode.create(name="o", depth=0, namespace="other")
        graph = Graph(FacadeNode, FacadeEdge)
        await graph.link(a, b)
        # Edge written directly: the facade refuses cross-namespace link().
        await FacadeEdge.create(source_id=a.id, target_id=outsider.id)

        names = [n.name for n in await graph.neighbours(a, direction="outgoing")]
        assert names == ["b"]


# ---------------------------------------------------------------------------
# shortest_path
# ---------------------------------------------------------------------------


class TestShortestPath:
    """shortest_path() hydrates the pathfinding rows in path order."""

    @pytest.mark.asyncio
    async def test_shortest_path_hydrates_models(self) -> None:
        a, _, c = await _make_chain()
        graph = Graph(FacadeNode, FacadeEdge)

        path = await graph.shortest_path(a, c)

        assert [n.name for n in path] == ["a", "b", "c"]
        assert all(isinstance(n, FacadeNode) for n in path)
        assert path[0].depth == 0

    @pytest.mark.asyncio
    async def test_shortest_path_empty_when_unreachable(self) -> None:
        a, _ = await _make_pair()
        isolated = await FacadeNode.create(name="iso", depth=0)
        graph = Graph(FacadeNode, FacadeEdge)

        assert await graph.shortest_path(a, isolated) == []

    @pytest.mark.asyncio
    async def test_max_depth_limits_the_search(self) -> None:
        a, _, c = await _make_chain()
        graph = Graph(FacadeNode, FacadeEdge)

        assert await graph.shortest_path(a, c, max_depth=1) == []
        assert [n.name for n in await graph.shortest_path(a, c, max_depth=2)] == [
            "a",
            "b",
            "c",
        ]


# ---------------------------------------------------------------------------
# find_cycles
# ---------------------------------------------------------------------------


class TestFindCycles:
    """find_cycles() hydrates every cycle's nodes."""

    @pytest.mark.asyncio
    async def test_find_cycles_hydrates_models(self) -> None:
        a, b = await _make_pair()
        graph = Graph(FacadeNode, FacadeEdge)
        await graph.link(a, b)
        await graph.link(b, a)

        cycles = await graph.find_cycles()

        assert len(cycles) == 1
        assert {n.name for n in cycles[0]} == {"a", "b"}
        assert all(isinstance(n, FacadeNode) for n in cycles[0])

    @pytest.mark.asyncio
    async def test_acyclic_graph_returns_empty(self) -> None:
        await _make_chain()
        graph = Graph(FacadeNode, FacadeEdge)

        assert await graph.find_cycles() == []


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


class TestSearch:
    """search() returns GraphVectorHit instances ordered by distance."""

    @pytest.mark.asyncio
    async def test_search_returns_hits_ordered_by_distance(self) -> None:
        seed = await FacadeNode.create(name="seed", embedding=[1.0, 0.0, 0.0])
        near = await FacadeNode.create(name="near", embedding=[0.9, 0.1, 0.0])
        far = await FacadeNode.create(name="far", embedding=[0.0, 1.0, 0.0])
        graph = Graph(FacadeNode, FacadeEdge)
        await graph.link(seed, near)
        await graph.link(seed, far)

        hits = await graph.search([1.0, 0.0, 0.0], seed=seed, metric="cosine")

        assert [hit.node.name for hit in hits] == ["seed", "near", "far"]
        assert all(isinstance(hit, GraphVectorHit) for hit in hits)
        assert all(isinstance(hit.node, FacadeNode) for hit in hits)
        assert hits[0].hops == 0
        distances = [hit.distance for hit in hits]
        assert distances == sorted(distances)

    @pytest.mark.asyncio
    async def test_k_limits_the_result(self) -> None:
        seed = await FacadeNode.create(name="seed", embedding=[1.0, 0.0, 0.0])
        near = await FacadeNode.create(name="near", embedding=[0.9, 0.1, 0.0])
        graph = Graph(FacadeNode, FacadeEdge)
        await graph.link(seed, near)

        hits = await graph.search([1.0, 0.0, 0.0], seed=seed, k=1)

        assert [hit.node.name for hit in hits] == ["seed"]


# ---------------------------------------------------------------------------
# GraphError paths
# ---------------------------------------------------------------------------


class TestGraphErrors:
    """The three documented GraphError paths."""

    @pytest.mark.asyncio
    async def test_wrong_node_class_raises(self) -> None:
        a, _ = await _make_pair()
        other = await OtherNode.create(name="x")
        graph = Graph(FacadeNode, FacadeEdge)

        with pytest.raises(GraphError, match="not a FacadeNode"):
            await graph.link(a, other)

    @pytest.mark.asyncio
    async def test_node_outside_namespace_raises(self) -> None:
        a, _ = await _make_pair()
        outsider = await FacadeNode.create(name="o", depth=0, namespace="other")
        graph = Graph(FacadeNode, FacadeEdge)

        with pytest.raises(GraphError, match="namespace"):
            await graph.link(a, outsider)

    def test_edge_model_missing_source_field_raises(self) -> None:
        with pytest.raises(GraphError, match="source_id"):
            Graph(FacadeNode, NoSourceEdge)
