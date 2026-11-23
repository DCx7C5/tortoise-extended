"""Integration tests for BaseGraphEdgeModel query classmethods against PostgreSQL.

Covers ``between``, ``between_any``, ``outgoing`` and ``incoming`` including the
optional ``edge_type`` branches (lines previously uncovered).

Requires the docker stack (see ``docker-compose.dev.yml``): PostgreSQL on
``127.0.0.1:5434``, database ``tortoise_test``.  The module is skipped when that
database is not reachable.
"""

import os
import socket
import uuid
from collections.abc import AsyncGenerator

import pytest
from tortoise import Tortoise

import tortoise_extended  # noqa: F401 — apply patches
from tortoise_extended.models import BaseGraphEdgeModel, BaseNode


class QueryEdge(BaseGraphEdgeModel):
    """Concrete BaseGraphEdgeModel subclass used by the query tests."""

    class Meta:
        table = "query_edges"
        # Redeclared — Tortoise does not propagate Meta.indexes from abstract bases.
        indexes = (
            ("source_id", "edge_type"),
            ("target_id", "edge_type"),
            ("source_id", "target_id", "edge_type"),
        )


class QueryNode(BaseNode):
    """Concrete BaseNode subclass used by the query tests."""

    class Meta:
        table = "query_nodes"
        indexes = (("namespace", "depth"),)


class GuardNode(BaseNode):
    """BaseNode subclass with the orphan-delete guard enabled."""

    _block_orphan_delete = True

    class Meta:
        table = "guard_nodes"
        indexes = (("namespace", "depth"),)


# --- Config — skip entire module if PG is not available --------------------

DB_URL = os.environ.get(
    "TORTOISE_TEST_DB",
    "postgres://postgres:postgres@localhost:5434/tortoise_test",
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


_TABLES = (
    "query_edges",
    "query_nodes",
    "guard_nodes",
    # Class-creation probes from TestMetaIndexesGuard.  The tables are never
    # generated (the classes are function-local, so Tortoise does not discover
    # them), but they are dropped defensively so a rerun starts clean.
    "guard_no_indexes_edges",
    "guard_opted_out_edges",
)


@pytest.fixture(scope="module", autouse=True)
async def _init_db() -> AsyncGenerator[None, None]:
    """Initialize Tortoise against the live PostgreSQL test database."""
    _ = await Tortoise.init(
        db_url=DB_URL,
        modules={"models": ["tests.test_graph_edge_queries"]},
    )
    conn = Tortoise.get_connection("default")
    for table in _TABLES:
        await conn.execute_query(f"DROP TABLE IF EXISTS {table} CASCADE")
    await Tortoise.generate_schemas()
    yield
    for table in _TABLES:
        await conn.execute_query(f"DROP TABLE IF EXISTS {table} CASCADE")
    await Tortoise.close_connections()


@pytest.fixture(autouse=True)
async def _clean_table() -> AsyncGenerator[None, None]:
    """Truncate the node/edge tables before each test."""
    conn = Tortoise.get_connection("default")
    await conn.execute_query("TRUNCATE query_edges, query_nodes, guard_nodes CASCADE")
    yield


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
    """BaseNode.path_to_root() defensive cycle guard."""

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
    """BaseNode._block_orphan_delete orphan policy guard."""

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


class TestReparentChildCountSync:
    """M2-FIX regression: reparent keeps ``child_count`` consistent and the
    previous-parent read is performed through ``select_for_update`` on the same
    connection that writes the node."""

    @pytest.mark.asyncio
    async def test_reparent_adjusts_both_parent_counts(self) -> None:
        """Moving a child from one parent to another adjusts both counts."""
        p1 = await QueryNode.create(name="p1", depth=0, parent_id=None)
        p2 = await QueryNode.create(name="p2", depth=0, parent_id=None)
        child = await QueryNode.create(name="child", depth=1, parent_id=p1.id)
        assert (await QueryNode.get(pk=p1.id)).child_count == 1

        # Reparent child from p1 to p2 — move_to() is the only reparent path
        # (save() refuses a persisted parent_id change).
        await child.move_to(p2)

        assert (await QueryNode.get(pk=p1.id)).child_count == 0
        assert (await QueryNode.get(pk=p2.id)).child_count == 1
        assert (await QueryNode.get(pk=child.id)).depth == 1


class TestMetaIndexesGuard:
    """TODO 3007 — ``BaseGraphEdgeModel.__init_subclass__`` index guard.

    Tortoise does not propagate ``Meta.indexes`` from the abstract base, so the
    guard raises at class-creation time instead of silently building an edge
    table without its composite indexes.
    """

    def test_concrete_without_indexes_raises_at_class_creation(self) -> None:
        """A concrete subclass with ``Meta.table`` but no ``indexes`` must raise."""
        with pytest.raises(NotImplementedError, match=r"\.Meta must redeclare indexes"):

            class _NoIndexesEdge(BaseGraphEdgeModel):
                class Meta:
                    table = "guard_no_indexes_edges"

    def test_empty_indexes_tuple_is_accepted(self) -> None:
        """``indexes = ()`` is the documented deliberate opt-out."""

        class _OptedOutEdge(BaseGraphEdgeModel):
            class Meta:
                table = "guard_opted_out_edges"
                indexes = ()

        assert issubclass(_OptedOutEdge, BaseGraphEdgeModel)
        assert _OptedOutEdge._meta.db_table == "guard_opted_out_edges"

    def test_abstract_without_indexes_is_allowed(self) -> None:
        """``Meta.abstract = True`` returns before the indexes check."""

        class _AbstractEdge(BaseGraphEdgeModel):
            class Meta:
                abstract = True

        assert _AbstractEdge._meta.abstract is True

    def test_missing_meta_raises_at_class_creation(self) -> None:
        """No ``Meta`` at all is rejected by the same guard."""
        with pytest.raises(NotImplementedError, match="must declare a Meta class"):

            class _NoMetaEdge(BaseGraphEdgeModel):
                pass
