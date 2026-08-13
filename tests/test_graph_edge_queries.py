"""Integration tests for BaseGraphEdgeModel query classmethods against SQLite.

Covers ``between``, ``between_any``, ``outgoing`` and ``incoming`` including the
optional ``edge_type`` branches (lines previously uncovered).
"""

import uuid

import pytest
from tortoise import Tortoise

import tortoise_extended  # noqa: F401 — apply patches
from tortoise_extended.models import BaseGraphEdgeModel, BaseGraphNodeModel



class QueryEdge(BaseGraphEdgeModel):
    """Concrete BaseGraphEdgeModel subclass used by the SQLite test."""

    class Meta:
        table = "query_edges"
        # Redeclared — Tortoise does not propagate Meta.indexes from abstract bases.
        indexes = (
            ("source_id", "edge_type"),
            ("target_id", "edge_type"),
            ("source_id", "target_id", "edge_type"),
        )


class QueryNode(BaseGraphNodeModel):
    """Concrete BaseGraphNodeModel subclass used by the SQLite test."""

    class Meta:
        table = "query_nodes"


class GuardNode(BaseGraphNodeModel):
    """BaseGraphNodeModel subclass with the orphan-delete guard enabled."""

    _block_orphan_delete = True

    class Meta:
        table = "guard_nodes"


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
    await GuardNode.all().delete()


async def _make_edges() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    a, b, c = (uuid.uuid4() for _ in range(3))
    await QueryEdge.create(source_id=a, target_id=b, edge_type="parent")
    await QueryEdge.create(source_id=b, target_id=c, edge_type="parent")
    await QueryEdge.create(source_id=a, target_id=c, edge_type="skip")
    await QueryEdge.create(source_id=c, target_id=a, edge_type="other")
    return a, b, c


class TestBetween:
    """BaseGraphEdgeModel.between() classmethod."""

    @pytest.mark.asyncio
    async def test_without_edge_type(self) -> None:
        a, _b, _c = await _make_edges()
        edges = await QueryEdge.between(a, _c)
        assert len(edges) == 1
        assert edges[0].edge_type == "skip"

    @pytest.mark.asyncio
    async def test_with_edge_type(self) -> None:
        a, _b, _c = await _make_edges()
        edges = await QueryEdge.between(a, _b, edge_type="parent")
        assert len(edges) == 1
        assert edges[0].target_id == _b

    @pytest.mark.asyncio
    async def test_no_match(self) -> None:
        _a, _b, _c = await _make_edges()
        edges = await QueryEdge.between(_b, _a)
        assert edges == []


class TestBetweenAny:
    """BaseGraphEdgeModel.between_any() classmethod (source OR target)."""

    @pytest.mark.asyncio
    async def test_matches_both_directions(self) -> None:
        _a, b, _c = await _make_edges()
        edges = await QueryEdge.between_any(b)
        # b is target of a->b and source of b->c
        assert len(edges) == 2

    @pytest.mark.asyncio
    async def test_with_edge_type(self) -> None:
        a, _b, _c = await _make_edges()
        edges = await QueryEdge.between_any(a, edge_type="other")
        assert len(edges) == 1
        assert edges[0].source_id == _c


class TestOutgoing:
    """BaseGraphEdgeModel.outgoing() classmethod."""

    @pytest.mark.asyncio
    async def test_outgoing_all(self) -> None:
        a, _b, _c = await _make_edges()
        edges = await QueryEdge.outgoing(a)
        assert len(edges) == 2  # a->b and a->c

    @pytest.mark.asyncio
    async def test_outgoing_filtered(self) -> None:
        a, _b, _c = await _make_edges()
        edges = await QueryEdge.outgoing(a, edge_type="parent")
        assert len(edges) == 1
        assert edges[0].target_id == _b


class TestIncoming:
    """BaseGraphEdgeModel.incoming() classmethod."""

    @pytest.mark.asyncio
    async def test_incoming_all(self) -> None:
        _a, _b, _c = await _make_edges()
        edges = await QueryEdge.incoming(_b)
        assert len(edges) == 1  # a->b only
        assert edges[0].source_id == _a

    @pytest.mark.asyncio
    async def test_incoming_filtered(self) -> None:
        a, _b, _c = await _make_edges()
        edges = await QueryEdge.incoming(_b, edge_type="parent")
        assert len(edges) == 1

    @pytest.mark.asyncio
    async def test_incoming_no_match(self) -> None:
        _a, _b, _c = await _make_edges()
        edges = await QueryEdge.incoming(_a, edge_type="skip")
        assert edges == []


class TestPathToRootCycleGuard:
    """BaseGraphNodeModel.path_to_root() defensive cycle guard."""

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


class TestOrphanDeleteGuard:
    """BaseGraphNodeModel._block_orphan_delete orphan policy guard."""

    @pytest.mark.asyncio
    async def test_delete_blocked_with_children(self) -> None:
        from tortoise_extended.exceptions import GraphError

        parent = await GuardNode.create(name="parent", depth=0, parent_id=None)
        child = await GuardNode.create(name="child", depth=1, parent_id=parent.id)
        with pytest.raises(GraphError, match="reference it as parent"):
            await parent.delete()
        # Nothing was deleted.
        assert await GuardNode.get(pk=parent.id) is not None
        assert await GuardNode.get(pk=child.id) is not None

    @pytest.mark.asyncio
    async def test_delete_allowed_without_children(self) -> None:
        node = await GuardNode.create(name="solo", depth=0, parent_id=None)
        await node.delete()
        assert await GuardNode.filter(pk=node.id).count() == 0

    @pytest.mark.asyncio
    async def test_default_allows_orphan_delete(self) -> None:
        parent = await QueryNode.create(name="parent", depth=0, parent_id=None)
        child = await QueryNode.create(name="child", depth=1, parent_id=parent.id)
        await parent.delete()  # no guard → orphaned child is allowed
        assert await QueryNode.get(pk=child.id) is not None
