"""Soft-deleted rows must not leak into the graph CTEs (rows 3003, 3080).

``SoftDeleteQuerySet`` filters ``deleted_at IS NULL`` eagerly. The recursive
CTEs in ``graph_traversal``, ``pathfinding`` and ``graph_vector_search`` now
apply the same predicate when the model projects a ``deleted_at`` column, and
generate unchanged SQL when it does not.

The column is resolved from ``_meta.fields_db_projection``, so a field *named*
something else but mapped onto ``deleted_at``
(``removed_at = fields.DatetimeField(source_field="deleted_at")``) is filtered
too, while a ``deleted_at`` field mapped onto a different column
(``source_field="gone"``) emits the real column instead of a phantom
``deleted_at`` identifier.

Requires the docker stack (PostgreSQL 18 on ``127.0.0.1:5434``, database
``tortoise_test``).  The DSN honours ``TORTOISE_TEST_DB``.
"""

import datetime as dt

import pytest
from tortoise import Tortoise, fields
from tortoise.models import Model

import tortoise_extended  # noqa: F401 - apply patches
from tortoise_extended import VectorField
from tortoise_extended.expressions._edge_filter import et_clause, live_predicate
from tortoise_extended.expressions._soft_delete import soft_delete_column
from tortoise_extended.expressions.graph_traversal import GraphTraversal
from tortoise_extended.expressions.graph_vector_search import GraphVectorSearch
from tortoise_extended.expressions.pathfinding import all_paths, shortest_path
from tests._pg_env import database_url, pg_skip_reason

# ---------------------------------------------------------------------------
# Config - skip the module if PG is not available
# ---------------------------------------------------------------------------

DB_URL = database_url()


_PG_SKIP_REASON = pg_skip_reason()
pytestmark = pytest.mark.skipif(
    _PG_SKIP_REASON is not None,
    reason=_PG_SKIP_REASON or "",
)


# ---------------------------------------------------------------------------
# Test models - soft-delete aware and plain controls
# ---------------------------------------------------------------------------


class SdNode(Model):
    """Node with a ``deleted_at`` column (soft-delete aware)."""

    id = fields.IntField(primary_key=True)
    name = fields.CharField(max_length=50)
    depth = fields.IntField(default=0)
    deleted_at = fields.DatetimeField(null=True)

    class Meta:
        table = "graph_sd_nodes"


class SdEdge(Model):
    """Edge with a ``deleted_at`` column (soft-delete aware)."""

    id = fields.IntField(primary_key=True)
    source_id = fields.IntField()
    target_id = fields.IntField()
    edge_type = fields.CharField(max_length=20, default="rel")
    is_bidirectional = fields.BooleanField(default=False)
    deleted_at = fields.DatetimeField(null=True)

    class Meta:
        table = "graph_sd_edges"


class PlainNode(Model):
    """Node without ``deleted_at`` - must generate unchanged SQL."""

    id = fields.IntField(primary_key=True)
    name = fields.CharField(max_length=50)
    depth = fields.IntField(default=0)

    class Meta:
        table = "graph_plain_nodes"


class PlainEdge(Model):
    """Edge without ``deleted_at`` - must generate unchanged SQL."""

    id = fields.IntField(primary_key=True)
    source_id = fields.IntField()
    target_id = fields.IntField()
    edge_type = fields.CharField(max_length=20, default="rel")
    is_bidirectional = fields.BooleanField(default=False)

    class Meta:
        table = "graph_plain_edges"


class VecNode(Model):
    """Soft-delete aware node with an embedding for GraphVectorSearch."""

    id = fields.IntField(primary_key=True)
    name = fields.CharField(max_length=50)
    depth = fields.IntField(default=0)
    embedding = VectorField(dimensions=2)
    deleted_at = fields.DatetimeField(null=True)

    class Meta:
        table = "graph_sd_vec_nodes"


class VecEdge(Model):
    """Soft-delete aware edge for GraphVectorSearch."""

    id = fields.IntField(primary_key=True)
    source_id = fields.IntField()
    target_id = fields.IntField()
    edge_type = fields.CharField(max_length=20, default="rel")
    is_bidirectional = fields.BooleanField(default=False)
    deleted_at = fields.DatetimeField(null=True)

    class Meta:
        table = "graph_sd_vec_edges"


class RenamedNode(Model):
    """Node whose soft-delete field is named ``removed_at``.

    It is projected onto the ``deleted_at`` column, so it is a soft-delete
    model by column while carrying no field of that name — the shape a
    field-name probe misses entirely.
    """

    id = fields.IntField(primary_key=True)
    name = fields.CharField(max_length=50)
    depth = fields.IntField(default=0)
    removed_at = fields.DatetimeField(source_field="deleted_at", null=True)

    class Meta:
        table = "graph_renamed_nodes"


class RenamedEdge(Model):
    """Edge with the same renamed soft-delete field."""

    id = fields.IntField(primary_key=True)
    source_id = fields.IntField()
    target_id = fields.IntField()
    edge_type = fields.CharField(max_length=20, default="rel")
    is_bidirectional = fields.BooleanField(default=False)
    removed_at = fields.DatetimeField(source_field="deleted_at", null=True)

    class Meta:
        table = "graph_renamed_edges"


class ElsewhereNode(Model):
    """Node with a field *named* ``deleted_at`` mapped onto another column.

    The field name is the only thing a ``fields_map`` probe can see, so the
    old builders emitted ``n.deleted_at IS NULL`` here — an identifier the
    table does not have. The column is ``gone``.
    """

    id = fields.IntField(primary_key=True)
    name = fields.CharField(max_length=50)
    depth = fields.IntField(default=0)
    deleted_at = fields.DatetimeField(source_field="gone", null=True)

    class Meta:
        table = "graph_where_nodes"


class ElsewhereEdge(Model):
    """Edge with a ``deleted_at`` field mapped onto the ``gone`` column."""

    id = fields.IntField(primary_key=True)
    source_id = fields.IntField()
    target_id = fields.IntField()
    edge_type = fields.CharField(max_length=20, default="rel")
    is_bidirectional = fields.BooleanField(default=False)
    deleted_at = fields.DatetimeField(source_field="gone", null=True)

    class Meta:
        table = "graph_where_edges"


class RenamedVecNode(Model):
    """Embedding node whose soft-delete field is ``removed_at``."""

    id = fields.IntField(primary_key=True)
    name = fields.CharField(max_length=50)
    depth = fields.IntField(default=0)
    embedding = VectorField(dimensions=2)
    removed_at = fields.DatetimeField(source_field="deleted_at", null=True)

    class Meta:
        table = "graph_sd_renamed_vec_nodes"


_TABLES = (
    "graph_sd_renamed_vec_nodes",
    "graph_where_edges",
    "graph_where_nodes",
    "graph_renamed_edges",
    "graph_renamed_nodes",
    "graph_sd_vec_edges",
    "graph_sd_vec_nodes",
    "graph_plain_edges",
    "graph_plain_nodes",
    "graph_sd_edges",
    "graph_sd_nodes",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
async def _init_db():
    """Initialize Tortoise ORM for this module."""
    await Tortoise.init(
        db_url=DB_URL,
        modules={"models": ["tests.test_soft_delete_traversal"]},
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
    """Truncate the graph tables before every test."""
    conn = Tortoise.get_connection("default")
    await conn.execute_query(f"TRUNCATE {', '.join(_TABLES)} CASCADE")
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tombstone() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def _chain() -> tuple[SdNode, SdNode, SdNode]:
    """Seed ``a -> b -> c`` and return the three nodes."""
    a = await SdNode.create(id=1, name="a", depth=0)
    b = await SdNode.create(id=2, name="b", depth=1)
    c = await SdNode.create(id=3, name="c", depth=2)
    await SdEdge.create(source_id=a.id, target_id=b.id)
    await SdEdge.create(source_id=b.id, target_id=c.id)
    return a, b, c


async def _ids(rows) -> list[int]:
    return sorted(row.id for row in rows)


# ---------------------------------------------------------------------------
# Predicate helper - pure unit tests (no DB needed, but skipped with the module)
# ---------------------------------------------------------------------------


class TestLivePredicate:
    def test_predicate_text(self) -> None:
        assert live_predicate("n") == "n.deleted_at IS NULL"
        assert live_predicate("e") == "e.deleted_at IS NULL"

    def test_predicate_uses_the_projected_column(self) -> None:
        """A renamed soft-delete field is filtered by its mapped column."""
        assert live_predicate("n", "removed_at") == "n.removed_at IS NULL"

    def test_et_clause_without_filters_is_empty(self) -> None:
        assert et_clause(None, 3) == ("", [])

    def test_et_clause_soft_delete_only(self) -> None:
        assert et_clause(None, 3, "deleted_at") == ("AND e.deleted_at IS NULL", [])

    def test_et_clause_uses_the_projected_column(self) -> None:
        assert et_clause(None, 3, "removed_at") == ("AND e.removed_at IS NULL", [])

    def test_et_clause_combines_edge_type_and_soft_delete(self) -> None:
        clause, params = et_clause("rel", 3, "deleted_at")
        assert clause == "AND e.edge_type = $3 AND e.deleted_at IS NULL"
        assert params == ["rel"]


# ---------------------------------------------------------------------------
# GraphTraversal
# ---------------------------------------------------------------------------


class TestTraversalSoftDelete:
    @pytest.mark.asyncio
    async def test_node_tombstone_hidden_from_traversal(self) -> None:
        a, b, c = await _chain()
        traversal = GraphTraversal(SdNode, SdEdge)
        assert await _ids(await traversal.descendants(a.id, max_depth=5)) == [2, 3]
        await SdNode.filter(id=b.id).update(deleted_at=_tombstone())
        assert await _ids(await traversal.descendants(a.id, max_depth=5)) == []
        assert await _ids(await traversal.ancestors(c.id, max_depth=5)) == []
        assert await _ids(await traversal.neighbors(a.id, direction="outgoing")) == []

    @pytest.mark.asyncio
    async def test_edge_tombstone_not_traversed(self) -> None:
        a, _b, _c = await _chain()
        traversal = GraphTraversal(SdNode, SdEdge)
        await SdEdge.filter(source_id=a.id, target_id=2).update(deleted_at=_tombstone())
        assert await _ids(await traversal.descendants(a.id, max_depth=5)) == []

    @pytest.mark.asyncio
    async def test_plain_model_unaffected(self) -> None:
        a = await PlainNode.create(id=1, name="a", depth=0)
        await PlainNode.create(id=2, name="b", depth=1)
        await PlainEdge.create(source_id=a.id, target_id=2)
        traversal = GraphTraversal(PlainNode, PlainEdge)
        assert await _ids(await traversal.descendants(a.id, max_depth=5)) == [2]


# ---------------------------------------------------------------------------
# Pathfinding
# ---------------------------------------------------------------------------


class TestPathfindingSoftDelete:
    @pytest.mark.asyncio
    async def test_shortest_path_avoids_tombstoned_node(self) -> None:
        a, b, c = await _chain()
        assert await shortest_path(SdNode, SdEdge, a.id, c.id, max_hops=5) is not None
        await SdNode.filter(id=b.id).update(deleted_at=_tombstone())
        assert await shortest_path(SdNode, SdEdge, a.id, c.id, max_hops=5) is None

    @pytest.mark.asyncio
    async def test_all_paths_avoid_tombstoned_edge(self) -> None:
        a, _b, c = await _chain()
        await SdEdge.filter(source_id=a.id, target_id=2).update(deleted_at=_tombstone())
        assert not await all_paths(SdNode, SdEdge, a.id, c.id, max_hops=5)


# ---------------------------------------------------------------------------
# GraphVectorSearch
# ---------------------------------------------------------------------------


class TestGraphVectorSearchSoftDelete:
    @pytest.mark.asyncio
    async def test_vector_search_hides_tombstoned_node(self) -> None:
        for nid, vec in ((1, [1.0, 0.0]), (2, [0.9, 0.1]), (3, [0.8, 0.2])):
            await VecNode.create(id=nid, name=f"n{nid}", embedding=vec)
        await VecEdge.create(source_id=1, target_id=2)
        await VecEdge.create(source_id=2, target_id=3)

        def search() -> GraphVectorSearch:
            return GraphVectorSearch(
                VecNode,
                VecEdge,
                query_vector=[1.0, 0.0],
                seed_id=1,
                max_hops=3,
                direction="outgoing",
                distance_metric="l2",
                max_results=10,
            )

        assert sorted(hit.node.id for hit in await search().search()) == [1, 2, 3]
        await VecNode.filter(id=2).update(deleted_at=_tombstone())
        assert sorted(hit.node.id for hit in await search().search()) == [1]


# ---------------------------------------------------------------------------
# Renamed soft-delete field - the column decides, not the field name (3080)
# ---------------------------------------------------------------------------


async def _renamed_chain() -> tuple[RenamedNode, RenamedNode, RenamedNode]:
    """Seed ``a -> b -> c`` in the renamed-field tables."""
    a = await RenamedNode.create(id=1, name="a", depth=0)
    b = await RenamedNode.create(id=2, name="b", depth=1)
    c = await RenamedNode.create(id=3, name="c", depth=2)
    await RenamedEdge.create(source_id=a.id, target_id=b.id)
    await RenamedEdge.create(source_id=b.id, target_id=c.id)
    return a, b, c


class TestRenamedSoftDeleteField:
    """A field mapped onto ``deleted_at`` is filtered like a ``deleted_at`` field."""

    def test_projection_maps_the_renamed_field_onto_the_column(self) -> None:
        assert RenamedNode._meta.fields_db_projection["removed_at"] == "deleted_at"
        assert RenamedEdge._meta.fields_db_projection["removed_at"] == "deleted_at"
        assert soft_delete_column(RenamedNode) == "deleted_at"
        assert soft_delete_column(RenamedEdge) == "deleted_at"

    @pytest.mark.asyncio
    async def test_node_tombstone_hidden_from_traversal(self) -> None:
        a, b, _c = await _renamed_chain()
        traversal = GraphTraversal(RenamedNode, RenamedEdge)
        assert await _ids(await traversal.descendants(a.id, max_depth=5)) == [2, 3]
        await RenamedNode.filter(id=b.id).update(removed_at=_tombstone())
        assert await _ids(await traversal.descendants(a.id, max_depth=5)) == []
        assert await _ids(await traversal.ancestors(3, max_depth=5)) == []
        assert await _ids(await traversal.neighbors(a.id, direction="outgoing")) == []

    @pytest.mark.asyncio
    async def test_edge_tombstone_not_traversed(self) -> None:
        a, _b, _c = await _renamed_chain()
        traversal = GraphTraversal(RenamedNode, RenamedEdge)
        assert await _ids(await traversal.descendants(a.id, max_depth=5)) == [2, 3]
        await RenamedEdge.filter(source_id=a.id, target_id=2).update(
            removed_at=_tombstone()
        )
        assert await _ids(await traversal.descendants(a.id, max_depth=5)) == []

    @pytest.mark.asyncio
    async def test_tombstone_blocks_shortest_path(self) -> None:
        a, b, c = await _renamed_chain()
        assert (
            await shortest_path(RenamedNode, RenamedEdge, a.id, c.id, max_hops=5)
            is not None
        )
        await RenamedNode.filter(id=b.id).update(removed_at=_tombstone())
        assert (
            await shortest_path(RenamedNode, RenamedEdge, a.id, c.id, max_hops=5)
            is None
        )

    @pytest.mark.asyncio
    async def test_vector_search_hides_renamed_tombstone(self) -> None:
        for nid, vec in ((1, [1.0, 0.0]), (2, [0.9, 0.1]), (3, [0.8, 0.2])):
            await RenamedVecNode.create(id=nid, name=f"n{nid}", embedding=vec)
        await PlainEdge.create(source_id=1, target_id=2)
        await PlainEdge.create(source_id=2, target_id=3)

        def search() -> GraphVectorSearch:
            return GraphVectorSearch(
                RenamedVecNode,
                PlainEdge,
                query_vector=[1.0, 0.0],
                seed_id=1,
                max_hops=3,
                direction="outgoing",
                distance_metric="l2",
                max_results=10,
            )

        assert sorted(hit.node.id for hit in await search().search()) == [1, 2, 3]
        await RenamedVecNode.filter(id=2).update(removed_at=_tombstone())
        assert sorted(hit.node.id for hit in await search().search()) == [1]


class TestDeletedAtFieldMappedElsewhere:
    """A ``deleted_at`` field on another column emits that column, not the name."""

    def test_projection_resolves_the_real_column(self) -> None:
        assert ElsewhereNode._meta.fields_db_projection["deleted_at"] == "gone"
        assert soft_delete_column(ElsewhereNode) == "gone"
        assert soft_delete_column(ElsewhereEdge) == "gone"

    @pytest.mark.asyncio
    async def test_traversal_runs_and_filters_on_the_real_column(self) -> None:
        for nid, depth in ((1, 0), (2, 1), (3, 2)):
            await ElsewhereNode.create(id=nid, name=f"n{nid}", depth=depth)
        await ElsewhereEdge.create(source_id=1, target_id=2)
        await ElsewhereEdge.create(source_id=2, target_id=3)
        traversal = GraphTraversal(ElsewhereNode, ElsewhereEdge)
        # The field-name probe emitted ``n.deleted_at IS NULL`` here - a column
        # this table does not have - so the call raised UndefinedColumn.
        assert await _ids(await traversal.descendants(1, max_depth=5)) == [2, 3]
        await ElsewhereNode.filter(id=2).update(deleted_at=_tombstone())
        assert await _ids(await traversal.descendants(1, max_depth=5)) == []

    @pytest.mark.asyncio
    async def test_edge_mapped_elsewhere_is_filtered_on_its_column(self) -> None:
        for nid, depth in ((1, 0), (2, 1), (3, 2)):
            await ElsewhereNode.create(id=nid, name=f"n{nid}", depth=depth)
        await ElsewhereEdge.create(source_id=1, target_id=2)
        await ElsewhereEdge.create(source_id=2, target_id=3)
        traversal = GraphTraversal(ElsewhereNode, ElsewhereEdge)
        await ElsewhereEdge.filter(source_id=1, target_id=2).update(
            deleted_at=_tombstone()
        )
        assert await _ids(await traversal.descendants(1, max_depth=5)) == []
