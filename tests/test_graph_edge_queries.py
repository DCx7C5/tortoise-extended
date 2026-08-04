"""Integration tests for GraphEdge query classmethods against SQLite.

Covers ``between``, ``between_any``, ``outgoing`` and ``incoming`` including the
optional ``edge_type`` branches (lines previously uncovered).
"""

import uuid

import pytest
from tortoise import Tortoise

import tortoise_extended  # noqa: F401 — apply patches
from tortoise_extended.graph.edge import GraphEdge
from tortoise_extended.graph.node import GraphNode


class QueryEdge(GraphEdge):
    """Concrete GraphEdge subclass used by the SQLite test."""

    class Meta:
        table = "query_edges"
        # Redeclared — Tortoise does not propagate Meta.indexes from abstract bases.
        indexes = (
            ("source_id", "edge_type"),
            ("target_id", "edge_type"),
            ("source_id", "target_id", "edge_type"),
        )


class QueryNode(GraphNode):
    """Concrete GraphNode subclass used by the SQLite test."""

    class Meta:
        table = "query_nodes"


@pytest.fixture(scope="module", autouse=True)
async def _init_db():
    """Initialize Tortoise with a shared in-memory SQLite DB."""
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["tests.test_graph_edge_queries"]},
    )
    await Tortoise.generate_schemas()
    yield
    await Tortoise.close_connections()


@pytest.fixture(autouse=True)
async def _clean_table():
    await QueryEdge.all().delete()
    await QueryNode.all().delete()


async def _make_edges() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    a, b, c = (uuid.uuid4() for _ in range(3))
    await QueryEdge.create(source_id=a, target_id=b, edge_type="parent")
    await QueryEdge.create(source_id=b, target_id=c, edge_type="parent")
    await QueryEdge.create(source_id=a, target_id=c, edge_type="skip")
    await QueryEdge.create(source_id=c, target_id=a, edge_type="other")
    return a, b, c


class TestBetween:
    """GraphEdge.between() classmethod."""

    @pytest.mark.asyncio
    async def test_without_edge_type(self) -> None:
        a, _b, _c = await _make_edges()
        edges = await QueryEdge.between(str(a), str(_c))
        assert len(edges) == 1
        assert edges[0].edge_type == "skip"

    @pytest.mark.asyncio
    async def test_with_edge_type(self) -> None:
        a, _b, _c = await _make_edges()
        edges = await QueryEdge.between(str(a), str(_b), edge_type="parent")
        assert len(edges) == 1
        assert edges[0].target_id == _b

    @pytest.mark.asyncio
    async def test_no_match(self) -> None:
        _a, _b, _c = await _make_edges()
        edges = await QueryEdge.between(str(_b), str(_a))
        assert edges == []


class TestBetweenAny:
    """GraphEdge.between_any() classmethod (source OR target)."""

    @pytest.mark.asyncio
    async def test_matches_both_directions(self) -> None:
        _a, b, _c = await _make_edges()
        edges = await QueryEdge.between_any(str(b))
        # b is target of a->b and source of b->c
        assert len(edges) == 2

    @pytest.mark.asyncio
    async def test_with_edge_type(self) -> None:
        a, _b, _c = await _make_edges()
        edges = await QueryEdge.between_any(str(a), edge_type="other")
        assert len(edges) == 1
        assert edges[0].source_id == _c


class TestOutgoing:
    """GraphEdge.outgoing() classmethod."""

    @pytest.mark.asyncio
    async def test_outgoing_all(self) -> None:
        a, _b, _c = await _make_edges()
        edges = await QueryEdge.outgoing(str(a))
        assert len(edges) == 2  # a->b and a->c

    @pytest.mark.asyncio
    async def test_outgoing_filtered(self) -> None:
        a, _b, _c = await _make_edges()
        edges = await QueryEdge.outgoing(str(a), edge_type="parent")
        assert len(edges) == 1
        assert edges[0].target_id == _b


class TestIncoming:
    """GraphEdge.incoming() classmethod."""

    @pytest.mark.asyncio
    async def test_incoming_all(self) -> None:
        _a, _b, _c = await _make_edges()
        edges = await QueryEdge.incoming(str(_b))
        assert len(edges) == 1  # a->b only
        assert edges[0].source_id == _a

    @pytest.mark.asyncio
    async def test_incoming_filtered(self) -> None:
        a, _b, _c = await _make_edges()
        edges = await QueryEdge.incoming(str(_b), edge_type="parent")
        assert len(edges) == 1

    @pytest.mark.asyncio
    async def test_incoming_no_match(self) -> None:
        _a, _b, _c = await _make_edges()
        edges = await QueryEdge.incoming(str(_a), edge_type="skip")
        assert edges == []


class TestPathToRootCycleGuard:
    """GraphNode.path_to_root() defensive cycle guard."""

    @pytest.mark.asyncio
    async def test_cycle_terminates(self) -> None:
        """A parent cycle must not hang — the visited set breaks it."""
        a = await QueryNode.create(name="a", depth=0, parent_id=None)
        b = await QueryNode.create(name="b", depth=1, parent_id=a.id)
        # Corrupt the DB so a's parent points back to b → cycle.
        await QueryNode.filter(id=a.id).update(parent_id=b.id)
        refreshed_a = await QueryNode.get(pk=a.id)
        path = await refreshed_a.path_to_root()
        # Both nodes are collected exactly once before the guard trips.
        assert {n.id for n in path} == {a.id, b.id}
        assert len(path) == 2
