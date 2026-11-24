"""Integration tests for ``BaseSoftDeleteNode`` — subtree-aware soft delete.

Requires the docker stack (see ``docker-compose.dev.yml``): PostgreSQL on
``127.0.0.1:5434``, database ``tortoise_test_unified``.  The module skips when
the socket is closed; ``TORTOISE_TEST_DB`` overrides the URL.

Run with: uv run pytest tests/test_soft_delete_node.py -v
"""

from collections.abc import AsyncGenerator
from uuid import UUID, uuid4

import pytest
from tortoise import Tortoise, fields
from tortoise.models import Model

import tortoise_extended  # noqa: F401 — apply patches
from tortoise_extended.expressions.graph_traversal import GraphTraversal
from tortoise_extended.expressions.pathfinding import shortest_path
from tortoise_extended.models import (
    BaseNode,
    BaseSoftDeleteNode,
    SoftDeleteNodeQuerySet,
)
from tests._pg_env import database_url, pg_skip_reason

# --- Test models -----------------------------------------------------------


class SoftNode(BaseSoftDeleteNode, BaseNode):
    """UUID7-keyed node with ``deleted_at`` and subtree soft delete."""

    class Meta:
        table = "soft_delete_nodes"
        indexes = (("namespace", "depth"),)


class GuardedSoftNode(BaseSoftDeleteNode, BaseNode):
    """Soft-delete node that opts into the **hard**-delete orphan guard."""

    _block_orphan_delete = True

    class Meta:
        table = "soft_delete_guarded_nodes"
        indexes = (("namespace", "depth"),)


class SoftEdge(Model):
    """Duck-typed edge model for the CTE builders (no ``deleted_at``)."""

    id = fields.UUIDField(primary_key=True, default=uuid4)
    source_id = fields.UUIDField(db_index=True)
    target_id = fields.UUIDField(db_index=True)
    edge_type = fields.CharField(max_length=32, default="rel")
    is_bidirectional = fields.BooleanField(default=False)

    class Meta:
        table = "soft_delete_node_edges"


# --- Config — skip entire module if PG is not available --------------------


DB_URL = database_url()


_PG_SKIP_REASON = pg_skip_reason()
pytestmark = pytest.mark.skipif(
    _PG_SKIP_REASON is not None,
    reason=_PG_SKIP_REASON or "",
)


# --- Fixtures --------------------------------------------------------------

_TABLES = ("soft_delete_nodes", "soft_delete_guarded_nodes", "soft_delete_node_edges")


@pytest.fixture(scope="module", autouse=True)
async def _init_db() -> AsyncGenerator[None, None]:
    """Initialize Tortoise ORM against the live PostgreSQL test database."""
    _ = await Tortoise.init(
        db_url=DB_URL,
        modules={"models": ["tests.test_soft_delete_node"]},
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
async def _clean_tables() -> AsyncGenerator[None, None]:
    """Truncate the node and edge tables before every test."""
    conn = Tortoise.get_connection("default")
    await conn.execute_query(
        "TRUNCATE soft_delete_nodes, soft_delete_guarded_nodes, "
        "soft_delete_node_edges CASCADE"
    )
    yield


async def _seed_tree() -> tuple[SoftNode, SoftNode, SoftNode, SoftNode]:
    """Seed ``root -> a -> a1`` plus ``root -> b`` in the ``shop`` namespace."""
    root = await SoftNode.create(namespace="shop", is_root=True, name="root")
    a = await root.add_child(name="a")
    a1 = await a.add_child(name="a1")
    b = await root.add_child(name="b")
    # Re-fetch so child_count reflects the post-hook denormalized state.
    return (
        await SoftNode.get(pk=root.pk),
        await SoftNode.get(pk=a.pk),
        await SoftNode.get(pk=a1.pk),
        await SoftNode.get(pk=b.pk),
    )


async def _seed_graph() -> tuple[SoftNode, SoftNode, SoftNode, SoftNode]:
    """Seed the node tree plus the ``root -> a -> a1`` / ``root -> b`` edges."""
    root, a, a1, b = await _seed_tree()
    _ = await SoftEdge.create(source_id=root.pk, target_id=a.pk)
    _ = await SoftEdge.create(source_id=a.pk, target_id=a1.pk)
    _ = await SoftEdge.create(source_id=root.pk, target_id=b.pk)
    return root, a, a1, b


async def _counter(pk: UUID) -> int:
    """Read ``child_count`` including soft-deleted rows."""
    node = await SoftNode.with_deleted().get(pk=pk)
    return node.child_count


# --- Shape -----------------------------------------------------------------


class TestShape:
    """The base merges the soft-delete column with the adjacency list."""

    def test_is_both_halves(self) -> None:
        assert issubclass(SoftNode, BaseSoftDeleteNode)
        assert issubclass(SoftNode, BaseNode)

    def test_fields_map_has_deleted_at_and_parent_id(self) -> None:
        fields_map = SoftNode._meta.fields_map
        assert "deleted_at" in fields_map
        assert "parent_id" in fields_map
        assert "child_count" in fields_map

    def test_queryset_is_the_node_aware_subclass(self) -> None:
        assert isinstance(SoftNode.all(), SoftDeleteNodeQuerySet)
        assert isinstance(SoftNode.with_deleted(), SoftDeleteNodeQuerySet)
        assert isinstance(SoftNode.only_deleted(), SoftDeleteNodeQuerySet)


# --- Instance path: subtree soft delete / restore --------------------------


class TestSubtreeDelete:
    """``delete()`` / ``restore()`` act on the whole subtree."""

    async def test_delete_hides_exactly_the_subtree(self) -> None:
        root, a, a1, b = await _seed_tree()
        await a.delete()

        assert await SoftNode.all().count() == 2
        assert await SoftNode.only_deleted().count() == 2
        assert await SoftNode.get_or_none(pk=root.pk) is not None
        assert await SoftNode.get_or_none(pk=b.pk) is not None
        assert await SoftNode.get_or_none(pk=a.pk) is None
        assert await SoftNode.get_or_none(pk=a1.pk) is None

    async def test_restore_reverses_the_subtree(self) -> None:
        root, a, a1, b = await _seed_tree()
        await a.delete()
        await a.restore()

        assert await SoftNode.all().count() == 4
        assert await SoftNode.only_deleted().count() == 0

    async def test_child_count_follows_the_live_children_invariant(self) -> None:
        root, a, a1, b = await _seed_tree()
        assert await _counter(root.pk) == 2
        assert await _counter(a.pk) == 1

        await a.delete()
        # Exactly one live child left for the root; the hidden subtree is zeroed.
        assert await _counter(root.pk) == 1
        assert await _counter(a.pk) == 0
        assert await _counter(a1.pk) == 0

        await a.restore()
        assert await _counter(root.pk) == 2
        assert await _counter(a.pk) == 1

    async def test_delete_is_idempotent(self) -> None:
        root, a, a1, b = await _seed_tree()
        await a.delete()
        await a.delete()

        assert await SoftNode.only_deleted().count() == 2
        assert await _counter(root.pk) == 1

    async def test_partial_restore_keeps_the_counters_exact(self) -> None:
        root, a, a1, b = await _seed_tree()
        await a.delete()

        # Restore only the grandchild: ``a`` stays hidden, so its counter stays
        # zeroed and the live root is not touched a second time.
        a1_deleted = await SoftNode.with_deleted().get(pk=a1.pk)
        await a1_deleted.restore()
        assert await _counter(a.pk) == 0
        assert await _counter(root.pk) == 1

        # Restoring ``a`` recomputes its counter from its live children.
        a_deleted = await SoftNode.with_deleted().get(pk=a.pk)
        await a_deleted.restore()
        assert await _counter(a.pk) == 1
        assert await _counter(root.pk) == 2

    async def test_orphan_guard_is_a_hard_delete_guard(self) -> None:
        parent = await GuardedSoftNode.create(
            namespace="ns", is_root=True, name="parent"
        )
        _ = await parent.add_child(name="child")

        await parent.delete()  # must not raise GraphError

        assert await GuardedSoftNode.only_deleted().count() == 2


# --- Queryset path: the defect this base exists to fix ---------------------


class TestQuerysetPath:
    """``filter(...).delete()`` must not leave the children visible."""

    async def test_queryset_delete_expands_to_the_subtree(self) -> None:
        root, a, a1, b = await _seed_tree()
        deleted = await SoftNode.filter(pk=a.pk).delete()

        assert deleted == 2
        assert await SoftNode.all().count() == 2
        assert await SoftNode.get_or_none(pk=a1.pk) is None
        assert await _counter(root.pk) == 1

    async def test_queryset_restore_expands_to_the_subtree(self) -> None:
        root, a, a1, b = await _seed_tree()
        await SoftNode.filter(pk=a.pk).delete()
        restored = await SoftNode.only_deleted().filter(pk=a.pk).restore()

        assert restored == 2
        assert await SoftNode.all().count() == 4
        assert await _counter(root.pk) == 2
        assert await _counter(a.pk) == 1

    async def test_queryset_delete_of_two_roots_decrements_by_two(self) -> None:
        root, a, a1, b = await _seed_tree()
        deleted = await SoftNode.filter(pk__in=[a.pk, b.pk]).delete()

        assert deleted == 3  # a, a1 and b
        assert await _counter(root.pk) == 0

    async def test_hard_delete_removes_only_the_matched_rows(self) -> None:
        root, a, a1, b = await _seed_tree()
        removed = await SoftNode.filter(pk=a1.pk).hard_delete()

        assert removed == 1
        assert await SoftNode.with_deleted().filter(pk=a1.pk).count() == 0
        assert await SoftNode.all().count() == 3


# --- ADDENDUM (row 3003): the CTE builders auto-filter deleted_at ---------


class TestTraversalVisibility:
    """Soft-deleted nodes vanish from the CTE builders without extra work."""

    async def test_soft_deleted_nodes_absent_from_graph_traversal(self) -> None:
        root, a, a1, b = await _seed_graph()
        traversal = GraphTraversal(SoftNode, SoftEdge)

        before = {node.id for node in await traversal.descendants(root.pk)}
        assert before == {a.pk, a1.pk, b.pk}

        await a.delete()
        after = {node.id for node in await traversal.descendants(root.pk)}
        assert after == {b.pk}

    async def test_shortest_path_skips_soft_deleted_intermediates(self) -> None:
        root, a, a1, b = await _seed_graph()
        assert (
            await shortest_path(SoftNode, SoftEdge, from_id=root.pk, to_id=a1.pk)
            is not None
        )

        await a.delete()
        # Restore only the grandchild: it is live, but its only path runs
        # through the still-deleted ``a``.
        a1_deleted = await SoftNode.with_deleted().get(pk=a1.pk)
        await a1_deleted.restore()
        assert (
            await shortest_path(SoftNode, SoftEdge, from_id=root.pk, to_id=a1.pk)
            is None
        )

        # Restoring the intermediate brings the path back.
        a_deleted = await SoftNode.with_deleted().get(pk=a.pk)
        await a_deleted.restore()
        assert (
            await shortest_path(SoftNode, SoftEdge, from_id=root.pk, to_id=a1.pk)
            is not None
        )
