"""Unit tests for TraversalNode / NeighborNode Structs and GraphTraversal row handling.

Exercises the dict-row and tuple-row result branches of
``GraphTraversal.ancestors``, ``GraphTraversal.descendants``, and
``GraphTraversal.neighbors`` — including empty results and non-existent
node IDs — using a monkeypatched ``connections`` singleton (no live DB).

The pattern mirrors ``test_timescale_managers.py``: a ``FakeConn`` returns
canned rows, and ``FakeConnections`` replaces the module-level
``connections`` binding so GraphTraversal never touches a real connection.
"""

from collections.abc import Sequence
from uuid import uuid4

import pytest

from tortoise_extended._types import RowMapping, RowValue
from tortoise_extended.expressions.graph_traversal import (
    GraphTraversal,
    NeighborNode,
    TraversalNode,
)


# ---------------------------------------------------------------------------
# Fake connection infrastructure
# ---------------------------------------------------------------------------


class FakeConn:
    """Returns a canned ``execute_query`` result.

    GraphTraversal unpacks the result as ``_, results = await conn.execute_query(sql, params)``,
    so the fake must return a 2-tuple ``(status, rows)``.
    """

    def __init__(self, rows: Sequence[RowMapping | tuple[RowValue, ...]]) -> None:
        self.rows = rows

    async def execute_query(
        self, _sql: str, *_args: RowValue
    ) -> tuple[int, Sequence[RowMapping | tuple[RowValue, ...]]]:
        return (0, self.rows)


class FakeConnections:
    """Stand-in for the module-level ``connections`` singleton."""

    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    def get(self, _name: str) -> FakeConn:
        return self.conn


def _patch_connections(
    monkeypatch: pytest.MonkeyPatch,
    rows: Sequence[RowMapping | tuple[RowValue, ...]],
) -> None:
    """Replace ``connections`` in graph_traversal module with a fake."""
    conn = FakeConn(rows)
    holder = FakeConnections(conn)
    monkeypatch.setattr(
        "tortoise_extended.expressions.graph_traversal.connections", holder
    )


# ---------------------------------------------------------------------------
# Fake models (minimal stubs so GraphTraversal.__init__ succeeds)
# ---------------------------------------------------------------------------


class _FakeMeta:
    def __init__(self, table: str, fields_map: dict[str, object]) -> None:
        self.db_table = table
        self.fields_map = fields_map
        # The soft-delete probe resolves the *column* a model projects, so the
        # double must carry the projection a real ``MetaInfo`` always has.
        # These fakes declare no ``source_field`` mapping, so field name and
        # column coincide; none of them is ``deleted_at``, which is what makes
        # the assertions prove the untouched-SQL path.
        self.fields_db_projection = {name: name for name in fields_map}


class _FakeNodeModel:
    _meta = _FakeMeta("nodes", {"id": None, "name": None, "depth": None})


class _FakeEdgeModel:
    _meta = _FakeMeta(
        "edges",
        {
            "source_id": None,
            "target_id": None,
            "edge_type": None,
            "is_bidirectional": None,
        },
    )


# ---------------------------------------------------------------------------
# TraversalNode — ancestors (dict-row + tuple-row + empty)
# ---------------------------------------------------------------------------


class TestAncestorsDictRow:
    """ancestors returns TraversalNode from dict rows."""

    def test_dict_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        nid = uuid4()
        rows: list[RowMapping] = [
            {"id": nid, "name": "root", "depth": 0, "path_depth": 2},
            {"id": nid, "name": "mid", "depth": 1, "path_depth": 1},
        ]
        _patch_connections(monkeypatch, rows)

        trav = GraphTraversal(_FakeNodeModel, _FakeEdgeModel)  # type: ignore[arg-type]
        result = _run_async(trav.ancestors(nid, max_depth=5))

        assert len(result) == 2
        assert all(isinstance(n, TraversalNode) for n in result)
        assert result[0].name == "root"
        assert result[0].path_depth == 2
        assert result[1].name == "mid"
        assert result[1].path_depth == 1


class TestAncestorsTupleRow:
    """ancestors returns TraversalNode from tuple rows."""

    def test_tuple_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        nid = uuid4()
        rows: list[tuple[RowValue, ...]] = [
            (nid, "root", 0, 2),
            (nid, "mid", 1, 1),
        ]
        _patch_connections(monkeypatch, rows)

        trav = GraphTraversal(_FakeNodeModel, _FakeEdgeModel)  # type: ignore[arg-type]
        result = _run_async(trav.ancestors(nid, max_depth=5))

        assert len(result) == 2
        assert all(isinstance(n, TraversalNode) for n in result)
        assert result[0].name == "root"
        assert result[0].id == nid
        assert result[1].name == "mid"
        assert result[1].depth == 1


class TestAncestorsEmpty:
    """ancestors returns [] for empty results (e.g. non-existent node)."""

    def test_empty_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        nid = uuid4()
        _patch_connections(monkeypatch, [])

        trav = GraphTraversal(_FakeNodeModel, _FakeEdgeModel)  # type: ignore[arg-type]
        result = _run_async(trav.ancestors(nid, max_depth=5))

        assert result == []


# ---------------------------------------------------------------------------
# descendants — dict-row + tuple-row + empty
# ---------------------------------------------------------------------------


class TestDescendantsDictRow:
    """descendants returns TraversalNode from dict rows."""

    def test_dict_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        nid = uuid4()
        rows: list[RowMapping] = [
            {"id": uuid4(), "name": "child", "depth": 1, "path_depth": 1},
            {"id": uuid4(), "name": "grand", "depth": 2, "path_depth": 2},
        ]
        _patch_connections(monkeypatch, rows)

        trav = GraphTraversal(_FakeNodeModel, _FakeEdgeModel)  # type: ignore[arg-type]
        result = _run_async(trav.descendants(nid, max_depth=5))

        assert len(result) == 2
        assert all(isinstance(n, TraversalNode) for n in result)
        assert result[0].name == "child"
        assert result[1].name == "grand"
        assert result[1].path_depth == 2


class TestDescendantsTupleRow:
    """descendants returns TraversalNode from tuple rows."""

    def test_tuple_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        nid = uuid4()
        child_id = uuid4()
        rows: list[tuple[RowValue, ...]] = [
            (child_id, "child", 1, 1),
        ]
        _patch_connections(monkeypatch, rows)

        trav = GraphTraversal(_FakeNodeModel, _FakeEdgeModel)  # type: ignore[arg-type]
        result = _run_async(trav.descendants(nid, max_depth=5))

        assert len(result) == 1
        assert isinstance(result[0], TraversalNode)
        assert result[0].name == "child"
        assert result[0].id == child_id


class TestDescendantsEmpty:
    """descendants returns [] when no descendants exist."""

    def test_empty_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        nid = uuid4()
        _patch_connections(monkeypatch, [])

        trav = GraphTraversal(_FakeNodeModel, _FakeEdgeModel)  # type: ignore[arg-type]
        result = _run_async(trav.descendants(nid, max_depth=5))

        assert result == []


# ---------------------------------------------------------------------------
# neighbors — dict-row + tuple-row + empty
# ---------------------------------------------------------------------------


class TestNeighborsDictRow:
    """neighbors returns NeighborNode from dict rows."""

    def test_dict_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        nid = uuid4()
        rows: list[RowMapping] = [
            {"id": uuid4(), "name": "alice", "depth": 1, "hops": 1},
            {"id": uuid4(), "name": "bob", "depth": 1, "hops": 2},
        ]
        _patch_connections(monkeypatch, rows)

        trav = GraphTraversal(_FakeNodeModel, _FakeEdgeModel)  # type: ignore[arg-type]
        result = _run_async(trav.neighbors(nid, direction="both"))

        assert len(result) == 2
        assert all(isinstance(n, NeighborNode) for n in result)
        assert result[0].name == "alice"
        assert result[0].hops == 1
        assert result[1].name == "bob"
        assert result[1].hops == 2


class TestNeighborsTupleRow:
    """neighbors returns NeighborNode from tuple rows."""

    def test_tuple_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        nid = uuid4()
        neighbor_id = uuid4()
        rows: list[tuple[RowValue, ...]] = [
            (neighbor_id, "charlie", 2, 1),
        ]
        _patch_connections(monkeypatch, rows)

        trav = GraphTraversal(_FakeNodeModel, _FakeEdgeModel)  # type: ignore[arg-type]
        result = _run_async(trav.neighbors(nid, direction="outgoing"))

        assert len(result) == 1
        assert isinstance(result[0], NeighborNode)
        assert result[0].name == "charlie"
        assert result[0].id == neighbor_id
        assert result[0].depth == 2
        assert result[0].hops == 1


class TestNeighborsEmpty:
    """neighbors returns [] when no neighbors exist."""

    def test_empty_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        nid = uuid4()
        _patch_connections(monkeypatch, [])

        trav = GraphTraversal(_FakeNodeModel, _FakeEdgeModel)  # type: ignore[arg-type]
        result = _run_async(trav.neighbors(nid, direction="both"))

        assert result == []


# ---------------------------------------------------------------------------
# Non-existent node — all three methods return []
# ---------------------------------------------------------------------------


class TestNonExistentNode:
    """Querying a node ID with no edges or matches returns []."""

    def test_ancestors_nonexistent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connections(monkeypatch, [])
        trav = GraphTraversal(_FakeNodeModel, _FakeEdgeModel)  # type: ignore[arg-type]
        result = _run_async(trav.ancestors(uuid4(), max_depth=5))
        assert result == []

    def test_descendants_nonexistent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connections(monkeypatch, [])
        trav = GraphTraversal(_FakeNodeModel, _FakeEdgeModel)  # type: ignore[arg-type]
        result = _run_async(trav.descendants(uuid4(), max_depth=5))
        assert result == []

    def test_neighbors_nonexistent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connections(monkeypatch, [])
        trav = GraphTraversal(_FakeNodeModel, _FakeEdgeModel)  # type: ignore[arg-type]
        result = _run_async(trav.neighbors(uuid4(), direction="both"))
        assert result == []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_async(coro):  # noqa: ANN001, ANN202
    """Run an async coroutine synchronously for unit tests."""
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
