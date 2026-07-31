"""Integration tests for graph traversal and pathfinding against live PostgreSQL.

Requires the docker stack (see ``docker-compose.dev.yml``): PostgreSQL 18 with
pgvector/TimescaleDB on ``127.0.0.1:5433``, database ``tortoise_test``.

Run with: uv run pytest tests/test_graph_integration.py -v
"""

import os
import socket
import uuid

import pytest
from tortoise import Tortoise, fields
from tortoise.models import Model

import tortoise_extended  # noqa: F401 — apply patches
from tortoise_extended.expressions.graph_traversal import GraphTraversal
from tortoise_extended.expressions.pathfinding import all_paths, find_cycles, shortest_path

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


class ItNode(Model):
    """Graph node for traversal/pathfinding tests."""

    id = fields.UUIDField(primary_key=True, default=uuid.uuid4)
    name = fields.CharField(max_length=100)
    depth = fields.IntField(default=0)

    class Meta:
        table = "graph_it_nodes"


class ItEdge(Model):
    """Directed edge (optionally bidirectional) with a type label."""

    id = fields.UUIDField(primary_key=True, default=uuid.uuid4)
    source_id = fields.UUIDField()
    target_id = fields.UUIDField()
    edge_type = fields.CharField(max_length=50, default="rel")
    is_bidirectional = fields.BooleanField(default=False)

    class Meta:
        table = "graph_it_edges"


class ItCustomEdge(Model):
    """Edge model with custom source/target field names (H2 regression)."""

    id = fields.UUIDField(primary_key=True, default=uuid.uuid4)
    from_node = fields.UUIDField()
    to_node = fields.UUIDField()
    edge_type = fields.CharField(max_length=50, default="rel")
    is_bidirectional = fields.BooleanField(default=False)

    class Meta:
        table = "graph_it_custom_edges"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
async def _init_db():
    """Initialize Tortoise ORM for the test module."""
    await Tortoise.init(
        db_url=DB_URL,
        modules={"models": ["tests.test_graph_integration"]},
    )
    await Tortoise.generate_schemas()
    yield
    conn = Tortoise.get_connection("default")
    for table in ("graph_it_custom_edges", "graph_it_edges", "graph_it_nodes"):
        await conn.execute_query(f"DROP TABLE IF EXISTS {table} CASCADE")
    await Tortoise.close_connections()


@pytest.fixture(autouse=True)
async def _clean_tables():
    """Truncate graph tables before every test."""
    conn = Tortoise.get_connection("default")
    await conn.execute_query("TRUNCATE graph_it_custom_edges, graph_it_edges, graph_it_nodes CASCADE")
    yield


async def _make_tree() -> tuple[ItNode, ItNode, ItNode, ItNode, ItNode]:
    """Seed the canonical test graph:

    - chain ``a -> b -> c -> d`` via ``parent`` edges
    - shortcut ``b -> d`` via a ``skip`` edge
    - isolated node ``x`` reached from ``a`` via an ``other`` edge
    """
    a = await ItNode.create(name="a", depth=0)
    b = await ItNode.create(name="b", depth=1)
    c = await ItNode.create(name="c", depth=2)
    d = await ItNode.create(name="d", depth=3)
    x = await ItNode.create(name="x", depth=9)
    for src, tgt, et in [
        (a.id, b.id, "parent"),
        (b.id, c.id, "parent"),
        (c.id, d.id, "parent"),
        (b.id, d.id, "skip"),
        (a.id, x.id, "other"),
    ]:
        await ItEdge.create(source_id=src, target_id=tgt, edge_type=et)
    return a, b, c, d, x


# ---------------------------------------------------------------------------
# GraphTraversal — ancestors / descendants / neighbors
# ---------------------------------------------------------------------------


class TestTraversal:
    """GraphTraversal against real PostgreSQL recursive CTEs."""

    @pytest.mark.asyncio
    async def test_ancestors(self) -> None:
        a, b, c, d, _ = await _make_tree()
        trav = GraphTraversal(ItNode, ItEdge)
        ancestors = await trav.ancestors(d.id, max_depth=5)
        assert sorted(r["name"] for r in ancestors) == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_descendants(self) -> None:
        a, b, c, d, x = await _make_tree()
        trav = GraphTraversal(ItNode, ItEdge)
        descendants = await trav.descendants(a.id, max_depth=5)
        assert sorted(r["name"] for r in descendants) == ["b", "c", "d", "x"]

    @pytest.mark.asyncio
    async def test_descendants_deduplicated_by_node(self) -> None:
        """A node reachable via multiple paths appears once (C1 regression)."""
        a, b, c, d, _ = await _make_tree()
        trav = GraphTraversal(ItNode, ItEdge)
        descendants = await trav.descendants(a.id, max_depth=5)
        names = [r["name"] for r in descendants]
        assert len(names) == len(set(names))
        # b is reachable at depth 1 (direct) and depth 2 (via c); report once
        assert names.count("b") == 1

    @pytest.mark.asyncio
    async def test_edge_type_filter(self) -> None:
        a, b, c, d, _ = await _make_tree()
        trav = GraphTraversal(ItNode, ItEdge)
        descendants = await trav.descendants(a.id, max_depth=5, edge_type="parent")
        assert sorted(r["name"] for r in descendants) == ["b", "c", "d"]

    @pytest.mark.asyncio
    async def test_edge_type_injection_payload_is_parameterized(self) -> None:
        """Malicious edge_type strings must not widen the result (C2 regression)."""
        a, b, c, d, _ = await _make_tree()
        trav = GraphTraversal(ItNode, ItEdge)
        result = await trav.descendants(a.id, max_depth=5, edge_type="x' OR 1=1 --")
        assert result == []

    @pytest.mark.asyncio
    async def test_neighbors_outgoing_and_incoming(self) -> None:
        a, b, c, d, x = await _make_tree()
        trav = GraphTraversal(ItNode, ItEdge)
        outgoing = await trav.neighbors(a.id, direction="outgoing", max_depth=1)
        assert sorted(r["name"] for r in outgoing) == ["b", "x"]
        incoming = await trav.neighbors(d.id, direction="incoming", max_depth=1)
        assert sorted(r["name"] for r in incoming) == ["b", "c"]

    @pytest.mark.asyncio
    async def test_custom_source_target_fields(self) -> None:
        """source_field/target_field must actually drive the SQL (H2 regression)."""
        a, b, c, d, _ = await _make_tree()
        for src, tgt in [(a.id, b.id), (b.id, c.id)]:
            await ItCustomEdge.create(from_node=src, to_node=tgt)
        trav = GraphTraversal(
            ItNode,
            ItCustomEdge,
            source_field="from_node",
            target_field="to_node",
        )
        descendants = await trav.descendants(a.id, max_depth=5)
        assert sorted(r["name"] for r in descendants) == ["b", "c"]


# ---------------------------------------------------------------------------
# GraphTraversal — has_cycle
# ---------------------------------------------------------------------------


class TestHasCycle:
    """Cycle detection against real PostgreSQL."""

    @pytest.mark.asyncio
    async def test_acyclic_graph(self) -> None:
        a, b, c, d, x = await _make_tree()
        trav = GraphTraversal(ItNode, ItEdge)
        assert await trav.has_cycle() is False

    @pytest.mark.asyncio
    async def test_directed_cycle(self) -> None:
        a, b, c, d, _ = await _make_tree()
        await ItEdge.create(source_id=d.id, target_id=a.id, edge_type="back")
        trav = GraphTraversal(ItNode, ItEdge)
        assert await trav.has_cycle() is True

    @pytest.mark.asyncio
    async def test_cycle_type_filtered_out(self) -> None:
        """A cycle using a different edge_type must not match the filter."""
        a, b, c, d, _ = await _make_tree()
        await ItEdge.create(source_id=d.id, target_id=a.id, edge_type="back")
        trav = GraphTraversal(ItNode, ItEdge)
        # the only cycle a->b->c->d->a mixes 'parent' and 'back' edges,
        # so a single-type filter must not report it
        assert await trav.has_cycle(edge_type="parent") is False
        assert await trav.has_cycle(edge_type="back") is False
        # a cycle made entirely of 'back' edges is detected
        p = await ItNode.create(name="p", depth=0)
        q = await ItNode.create(name="q", depth=0)
        await ItEdge.create(source_id=p.id, target_id=q.id, edge_type="back")
        await ItEdge.create(source_id=q.id, target_id=p.id, edge_type="back")
        assert await trav.has_cycle(edge_type="back") is True

    @pytest.mark.asyncio
    async def test_self_loop(self) -> None:
        a, b, c, d, _ = await _make_tree()
        y = await ItNode.create(name="y", depth=0)
        await ItEdge.create(source_id=y.id, target_id=y.id, edge_type="self")
        trav = GraphTraversal(ItNode, ItEdge)
        assert await trav.has_cycle() is True

    @pytest.mark.asyncio
    async def test_bidirectional_pair_is_cycle(self) -> None:
        """a<->b forms the closed walk a -> b -> a."""
        a, b, c, d, x = await _make_tree()
        await ItEdge.create(
            source_id=a.id, target_id=x.id, edge_type="undir", is_bidirectional=True
        )
        trav = GraphTraversal(ItNode, ItEdge)
        assert await trav.has_cycle() is True


# ---------------------------------------------------------------------------
# Pathfinding — shortest_path / all_paths / find_cycles
# ---------------------------------------------------------------------------


class TestShortestPath:
    """shortest_path against real PostgreSQL."""

    @pytest.mark.asyncio
    async def test_shortest_path_with_type_filter(self) -> None:
        a, b, c, d, _ = await _make_tree()
        path = await shortest_path(
            ItNode, ItEdge, from_id=a.id, to_id=d.id, max_hops=6, edge_type="parent"
        )
        assert path is not None
        assert [n["name"] for n in path] == ["a", "b", "c", "d"]

    @pytest.mark.asyncio
    async def test_shortest_path_uses_fastest_route(self) -> None:
        a, b, c, d, _ = await _make_tree()
        path = await shortest_path(ItNode, ItEdge, from_id=a.id, to_id=d.id, max_hops=6)
        assert path is not None
        # the 'skip' edge b -> d makes this shorter than the parent chain
        assert [n["name"] for n in path] == ["a", "b", "d"]

    @pytest.mark.asyncio
    async def test_no_path_returns_none(self) -> None:
        a, b, c, d, x = await _make_tree()
        isolated = await ItNode.create(name="iso", depth=0)
        path = await shortest_path(ItNode, ItEdge, from_id=a.id, to_id=isolated.id, max_hops=6)
        assert path is None

    @pytest.mark.asyncio
    async def test_injection_payload_returns_none(self) -> None:
        a, b, c, d, _ = await _make_tree()
        path = await shortest_path(
            ItNode, ItEdge,
            from_id=a.id, to_id=d.id, max_hops=6, edge_type="x' OR 1=1 --",
        )
        assert path is None


class TestAllPaths:
    """all_paths against real PostgreSQL."""

    @pytest.mark.asyncio
    async def test_all_paths_with_type_filter(self) -> None:
        a, b, c, d, _ = await _make_tree()
        paths = await all_paths(
            ItNode, ItEdge,
            from_id=a.id, to_id=d.id, max_hops=6, max_paths=10, edge_type="parent",
        )
        names = ["-".join(n["name"] for n in p) for p in paths]
        assert names == ["a-b-c-d"]

    @pytest.mark.asyncio
    async def test_all_paths_unfiltered(self) -> None:
        a, b, c, d, _ = await _make_tree()
        paths = await all_paths(
            ItNode, ItEdge, from_id=a.id, to_id=d.id, max_hops=6, max_paths=10
        )
        names = sorted("-".join(n["name"] for n in p) for p in paths)
        # both routes a->b->d and a->b->c->d exist; shortest first per row
        assert "a-b-d" in names
        assert "a-b-c-d" in names

    @pytest.mark.asyncio
    async def test_max_paths_limit(self) -> None:
        a, b, c, d, _ = await _make_tree()
        paths = await all_paths(
            ItNode, ItEdge, from_id=a.id, to_id=d.id, max_hops=6, max_paths=1
        )
        assert len(paths) == 1


class TestFindCycles:
    """find_cycles against real PostgreSQL."""

    @pytest.mark.asyncio
    async def test_acyclic_graph_has_no_cycles(self) -> None:
        a, b, c, d, x = await _make_tree()
        cycles = await find_cycles(ItNode, ItEdge, max_depth=10)
        assert cycles == []

    @pytest.mark.asyncio
    async def test_cycles_are_canonical_and_complete(self) -> None:
        a, b, c, d, x = await _make_tree()
        await ItEdge.create(source_id=d.id, target_id=a.id, edge_type="back")
        y = await ItNode.create(name="y", depth=0)
        await ItEdge.create(source_id=y.id, target_id=y.id, edge_type="self")

        cycles = await find_cycles(ItNode, ItEdge, max_depth=10)
        names = [tuple(n["name"] for n in cyc) for cyc in cycles]

        # exactly the 3 simple cycles, each reported once (no rotations):
        # the 4-cycle a->b->c->d->a, the triangle a->b->d->a (via 'skip'),
        # and the self-loop y->y
        assert len(names) == 3, names
        assert any(set(t) == {"a", "b", "c", "d"} for t in names), names
        assert any(set(t) == {"a", "b", "d"} for t in names), names
        assert ("y",) in names, names

    @pytest.mark.asyncio
    async def test_bidirectional_pair_cycle(self) -> None:
        a, b, c, d, x = await _make_tree()
        await ItEdge.create(
            source_id=a.id, target_id=x.id, edge_type="undir", is_bidirectional=True
        )
        cycles = await find_cycles(ItNode, ItEdge, max_depth=10)
        names = [tuple(n["name"] for n in cyc) for cyc in cycles]
        assert len(names) == 1, names
        assert set(names[0]) == {"a", "x"}, names
