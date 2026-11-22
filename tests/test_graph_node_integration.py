"""Integration tests for BaseNode helper methods against live PostgreSQL.

Requires the docker stack (see ``docker-compose.dev.yml``): PostgreSQL 18 on
``127.0.0.1:5434``, database ``tortoise_test``.

Run with: uv run pytest tests/test_graph_node_integration.py -v
"""

import os
import socket
import uuid

import pytest
from tortoise import Tortoise

import tortoise_extended  # noqa: F401 — apply patches
from tortoise_extended.exceptions import GraphError
from tortoise_extended.models import BaseNode


# ---------------------------------------------------------------------------
# Config — skip entire module if PG is not available
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Test model
# ---------------------------------------------------------------------------


class ItGraphNode(BaseNode):
    """BaseNode subclass under test."""

    class Meta:
        table = "graph_node_it_nodes"
        indexes = (("namespace", "depth"),)


class ItGraphNodeGuarded(BaseNode):
    """Subclass with orphan-delete protection enabled."""

    _block_orphan_delete = True

    class Meta:
        table = "graph_node_it_guarded"
        indexes = (("namespace", "depth"),)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
async def _init_db():
    """Initialize Tortoise ORM for the test module."""
    await Tortoise.init(
        db_url=DB_URL,
        modules={"models": ["tests.test_graph_node_integration"]},
    )
    await Tortoise.generate_schemas()
    yield
    conn = Tortoise.get_connection("default")
    await conn.execute_query("DROP TABLE IF EXISTS graph_node_it_guarded CASCADE")
    await conn.execute_query("DROP TABLE IF EXISTS graph_node_it_nodes CASCADE")
    await Tortoise.close_connections()


@pytest.fixture(autouse=True)
async def _clean_table():
    """Truncate the graph node tables before every test."""
    conn = Tortoise.get_connection("default")
    await conn.execute_query("TRUNCATE graph_node_it_nodes CASCADE")
    await conn.execute_query("TRUNCATE graph_node_it_guarded CASCADE")
    yield


async def _make_tree() -> tuple[ItGraphNode, ItGraphNode, ItGraphNode, ItGraphNode]:
    """Seed a two-branch tree in the ``shop`` namespace plus a foreign tree:

    - shop/electronics (root)
      - shop/laptops
        - shop/macbook
      - shop/phones
    - office/office_root (root, must never leak into shop queries)
      - office/office_child
    """
    electronics = await ItGraphNode.create(
        name="electronics",
        namespace="shop",
        depth=0,
        is_root=True,
        child_count=0,
    )
    laptops = await ItGraphNode.create(
        name="laptops",
        namespace="shop",
        parent_id=electronics.pk,
        depth=1,
        child_count=0,
    )
    macbook = await ItGraphNode.create(
        name="macbook",
        namespace="shop",
        parent_id=laptops.pk,
        depth=2,
    )
    phones = await ItGraphNode.create(
        name="phones",
        namespace="shop",
        parent_id=electronics.pk,
        depth=1,
    )
    await ItGraphNode.create(
        name="office_root",
        namespace="office",
        depth=0,
        is_root=True,
    )
    await ItGraphNode.create(
        name="office_child",
        namespace="office",
        depth=1,
    )
    # Re-fetch the returned instances so child_count reflects the
    # post-hook denormalized state (the create-time instances hold the
    # seeded value, not the counter maintained by the save hooks).
    electronics = await ItGraphNode.get(pk=electronics.pk)
    laptops = await ItGraphNode.get(pk=laptops.pk)
    macbook = await ItGraphNode.get(pk=macbook.pk)
    phones = await ItGraphNode.get(pk=phones.pk)
    return electronics, laptops, macbook, phones


# ---------------------------------------------------------------------------
# BaseNode helpers
# ---------------------------------------------------------------------------


class TestGraphNodeTraversal:
    """children / descendants / ancestors / siblings against real PostgreSQL."""

    @pytest.mark.asyncio
    async def test_children(self) -> None:
        electronics, laptops, macbook, phones = await _make_tree()
        children = await electronics.children().all()
        assert [n.name for n in children] == ["laptops", "phones"]
        assert list(await laptops.children().all()) == [macbook]
        assert list(await macbook.children().all()) == []

    @pytest.mark.asyncio
    async def test_descendants_excludes_self_and_foreign_namespaces(self) -> None:
        """descendants() must not include self or other namespaces (C5 regression)."""
        electronics, laptops, macbook, phones = await _make_tree()
        names = {n.name for n in await electronics.descendants().all()}
        assert names == {"laptops", "phones", "macbook"}
        assert "office_root" not in names
        assert "office_child" not in names
        # A leaf node has no descendants
        assert list(await macbook.descendants().all()) == []

    @pytest.mark.asyncio
    async def test_descendants_max_depth(self) -> None:
        electronics, laptops, macbook, phones = await _make_tree()
        names = {n.name for n in await electronics.descendants(max_depth=1).all()}
        assert names == {"laptops", "phones"}

    @pytest.mark.asyncio
    async def test_ancestors_excludes_self_and_foreign_namespaces(self) -> None:
        """ancestors() must not include self or other namespaces (C5 regression)."""
        electronics, laptops, macbook, phones = await _make_tree()
        names = [n.name for n in await macbook.ancestors().all()]
        # Depth-range approximation: shallower nodes in the namespace,
        # ordered by depth (so both laptops and phones appear).
        assert names[0] == "electronics"
        assert "macbook" not in names
        assert "office_root" not in names
        assert "office_child" not in names
        assert list(await electronics.ancestors().all()) == []

    @pytest.mark.asyncio
    async def test_siblings(self) -> None:
        electronics, laptops, macbook, phones = await _make_tree()
        assert [n.name for n in await macbook.siblings().all()] == []
        assert [n.name for n in await phones.siblings().all()] == ["laptops"]

    @pytest.mark.asyncio
    async def test_path_to_root_walks_parent_links(self) -> None:
        """path_to_root() follows parent_id links, so branches never leak in."""
        electronics, laptops, macbook, phones = await _make_tree()
        path = await macbook.path_to_root()
        assert [n.name for n in path] == ["electronics", "laptops", "macbook"]
        # Root node's path is just itself
        assert [n.name for n in await electronics.path_to_root()] == ["electronics"]

    @pytest.mark.asyncio
    async def test_subtree_walks_parent_links(self) -> None:
        """subtree() follows parent_id links, so foreign trees never leak in."""
        electronics, laptops, macbook, phones = await _make_tree()
        subtree = await electronics.subtree()
        assert [n.name for n in subtree] == [
            "electronics",
            "laptops",
            "phones",
            "macbook",
        ]

    @pytest.mark.asyncio
    async def test_is_leaf_and_repr(self) -> None:
        electronics, laptops, macbook, phones = await _make_tree()
        assert not electronics.is_leaf
        assert macbook.is_leaf
        assert "macbook" in repr(macbook)

    @pytest.mark.asyncio
    async def test_uuid_primary_key(self) -> None:
        node = await ItGraphNode.create(name="solo", namespace="shop", depth=0)
        assert isinstance(node.pk, uuid.UUID)


# ---------------------------------------------------------------------------
# subtree() — link-exact BFS traversal vs descendants() depth-range
# ---------------------------------------------------------------------------


class TestGraphNodeSubtree:
    """subtree() walks parent_id links via BFS; descendants() uses depth range."""

    @pytest.mark.asyncio
    async def test_subtree_returns_bfs_order(self) -> None:
        """subtree() yields nodes in breadth-first order from the root."""
        electronics, laptops, macbook, phones = await _make_tree()
        subtree = await electronics.subtree()
        names = [n.name for n in subtree]
        assert names[0] == "electronics"
        # BFS: level-1 children before level-2 grandchildren
        idx_laptops = names.index("laptops")
        idx_phones = names.index("phones")
        idx_macbook = names.index("macbook")
        assert idx_laptops < idx_macbook
        assert idx_phones < idx_macbook

    @pytest.mark.asyncio
    async def test_subtree_mid_tree_start(self) -> None:
        """subtree() from a non-root node includes only its descendants + self."""
        electronics, laptops, macbook, phones = await _make_tree()
        subtree = await laptops.subtree()
        names = {n.name for n in subtree}
        assert names == {"laptops", "macbook"}
        assert "electronics" not in names
        assert "phones" not in names

    @pytest.mark.asyncio
    async def test_subtree_leaf_returns_self_only(self) -> None:
        """A leaf node's subtree is just itself."""
        electronics, laptops, macbook, phones = await _make_tree()
        subtree = await macbook.subtree()
        assert [n.name for n in subtree] == ["macbook"]

    @pytest.mark.asyncio
    async def test_subtree_vs_descendants_agree_on_members(self) -> None:
        """subtree() and descendants() return the same set of names."""
        electronics, laptops, macbook, phones = await _make_tree()
        subtree_names = {n.name for n in await electronics.subtree()}
        desc_names = {n.name for n in await electronics.descendants().all()}
        # subtree includes self; descendants excludes self
        assert subtree_names == desc_names | {"electronics"}

    @pytest.mark.asyncio
    async def test_subtree_respects_max_depth(self) -> None:
        """subtree(max_depth=1) stops at the first level of children."""
        electronics, laptops, macbook, phones = await _make_tree()
        subtree = await electronics.subtree(max_depth=1)
        names = {n.name for n in subtree}
        assert names == {"electronics", "laptops", "phones"}
        assert "macbook" not in names

    @pytest.mark.asyncio
    async def test_subtree_no_foreign_namespace_leakage(self) -> None:
        """subtree() never includes nodes from other namespaces."""
        electronics, laptops, macbook, phones = await _make_tree()
        subtree = await electronics.subtree()
        namespaces = {n.namespace for n in subtree}
        assert namespaces == {"shop"}


# ---------------------------------------------------------------------------
# path_to_root() — deep tree correctness
# ---------------------------------------------------------------------------


class TestGraphNodePathToRoot:
    """path_to_root() follows parent_id links and returns depth-sorted path."""

    @pytest.mark.asyncio
    async def test_deep_tree_path(self) -> None:
        """path_to_root() on a 4-level deep tree returns all ancestors."""
        root = await ItGraphNode.create(
            name="root",
            namespace="shop",
            depth=0,
            is_root=True,
        )
        child = await ItGraphNode.create(
            name="child",
            namespace="shop",
            parent_id=root.pk,
            depth=1,
        )
        grandchild = await ItGraphNode.create(
            name="grandchild",
            namespace="shop",
            parent_id=child.pk,
            depth=2,
        )
        leaf = await ItGraphNode.create(
            name="leaf",
            namespace="shop",
            parent_id=grandchild.pk,
            depth=3,
        )
        path = await leaf.path_to_root()
        assert [n.name for n in path] == ["root", "child", "grandchild", "leaf"]

    @pytest.mark.asyncio
    async def test_path_to_root_single_node(self) -> None:
        """A root node's path is just itself."""
        root = await ItGraphNode.create(
            name="solo",
            namespace="shop",
            depth=0,
            is_root=True,
        )
        path = await root.path_to_root()
        assert [n.name for n in path] == ["solo"]

    @pytest.mark.asyncio
    async def test_path_to_root_respects_namespace(self) -> None:
        """path_to_root() never reaches into a different namespace."""
        electronics, laptops, macbook, phones = await _make_tree()
        path = await macbook.path_to_root()
        names = [n.name for n in path]
        assert "office_root" not in names
        assert "office_child" not in names
        assert names == ["electronics", "laptops", "macbook"]

    @pytest.mark.asyncio
    async def test_path_to_root_dangling_parent_raises(self) -> None:
        """path_to_root() raises GraphError when a parent_id points at a
        deleted row."""
        root = await ItGraphNode.create(
            name="root",
            namespace="shop",
            depth=0,
            is_root=True,
        )
        child = await ItGraphNode.create(
            name="child",
            namespace="shop",
            parent_id=root.pk,
            depth=1,
        )
        # Delete the parent row directly (bypassing the ORM hooks)
        conn = Tortoise.get_connection("default")
        await conn.execute_query(
            "DELETE FROM graph_node_it_nodes WHERE id = $1", [root.pk]
        )
        with pytest.raises(GraphError, match="dangling parent_id"):
            await child.path_to_root()


# ---------------------------------------------------------------------------
# Orphan-guard behavior (_block_orphan_delete)
# ---------------------------------------------------------------------------


class TestGraphNodeOrphanGuard:
    """_block_orphan_delete=True raises GraphError on delete with children."""

    @pytest.mark.asyncio
    async def test_delete_node_with_children_raises(self) -> None:
        """Deleting a parent that still has children raises GraphError."""
        parent = await ItGraphNodeGuarded.create(
            name="parent",
            namespace="ns",
            depth=0,
        )
        await ItGraphNodeGuarded.create(
            name="child",
            namespace="ns",
            parent_id=parent.pk,
            depth=1,
        )
        with pytest.raises(GraphError, match="child node"):
            await parent.delete()

    @pytest.mark.asyncio
    async def test_delete_leaf_succeeds(self) -> None:
        """Deleting a leaf node (no children) succeeds."""
        parent = await ItGraphNodeGuarded.create(
            name="parent",
            namespace="ns",
            depth=0,
        )
        child = await ItGraphNodeGuarded.create(
            name="child",
            namespace="ns",
            parent_id=parent.pk,
            depth=1,
        )
        await child.delete()
        remaining = await ItGraphNodeGuarded.filter(namespace="ns").all()
        assert [n.name for n in remaining] == ["parent"]

    @pytest.mark.asyncio
    async def test_delete_after_removing_children_succeeds(self) -> None:
        """Once all children are removed, the parent can be deleted."""
        parent = await ItGraphNodeGuarded.create(
            name="parent",
            namespace="ns",
            depth=0,
        )
        child = await ItGraphNodeGuarded.create(
            name="child",
            namespace="ns",
            parent_id=parent.pk,
            depth=1,
        )
        await child.delete()
        await parent.delete()
        remaining = await ItGraphNodeGuarded.filter(namespace="ns").all()
        assert remaining == []

    @pytest.mark.asyncio
    async def test_default_subclass_allows_orphan_delete(self) -> None:
        """ItGraphNode (without _block_orphan_delete) allows deleting a parent
        even when children exist."""
        parent = await ItGraphNode.create(
            name="parent",
            namespace="ns",
            depth=0,
        )
        await ItGraphNode.create(
            name="child",
            namespace="ns",
            parent_id=parent.pk,
            depth=1,
        )
        # Should NOT raise
        await parent.delete()
        remaining = await ItGraphNode.filter(namespace="ns").all()
        assert [n.name for n in remaining] == ["child"]


# ---------------------------------------------------------------------------
# child_count denormalization across create / reparent / delete
# ---------------------------------------------------------------------------


class TestGraphNodeChildCount:
    """child_count stays in sync across create, reparent, and delete."""

    @pytest.mark.asyncio
    async def test_create_increments_child_count(self) -> None:
        """Creating a child increments the parent's child_count."""
        parent = await ItGraphNode.create(
            name="parent",
            namespace="ns",
            depth=0,
        )
        parent = await ItGraphNode.get(pk=parent.pk)
        assert parent.child_count == 0
        await ItGraphNode.create(
            name="child",
            namespace="ns",
            parent_id=parent.pk,
            depth=1,
        )
        parent = await ItGraphNode.get(pk=parent.pk)
        assert parent.child_count == 1

    @pytest.mark.asyncio
    async def test_create_two_children(self) -> None:
        """Two children yield child_count == 2."""
        parent = await ItGraphNode.create(
            name="parent",
            namespace="ns",
            depth=0,
        )
        await ItGraphNode.create(
            name="c1",
            namespace="ns",
            parent_id=parent.pk,
            depth=1,
        )
        await ItGraphNode.create(
            name="c2",
            namespace="ns",
            parent_id=parent.pk,
            depth=1,
        )
        parent = await ItGraphNode.get(pk=parent.pk)
        assert parent.child_count == 2

    @pytest.mark.asyncio
    async def test_reparent_adjusts_both_parents(self) -> None:
        """Moving a child from one parent to another adjusts both counts."""
        p1 = await ItGraphNode.create(
            name="p1",
            namespace="ns",
            depth=0,
        )
        p2 = await ItGraphNode.create(
            name="p2",
            namespace="ns",
            depth=0,
        )
        child = await ItGraphNode.create(
            name="child",
            namespace="ns",
            parent_id=p1.pk,
            depth=1,
        )
        p1 = await ItGraphNode.get(pk=p1.pk)
        assert p1.child_count == 1

        # Reparent: child now belongs to p2
        child.parent_id = p2.pk
        await child.save()

        p1 = await ItGraphNode.get(pk=p1.pk)
        p2 = await ItGraphNode.get(pk=p2.pk)
        assert p1.child_count == 0
        assert p2.child_count == 1

    @pytest.mark.asyncio
    async def test_delete_decrements_child_count(self) -> None:
        """Deleting a child decrements the parent's child_count."""
        parent = await ItGraphNode.create(
            name="parent",
            namespace="ns",
            depth=0,
        )
        child = await ItGraphNode.create(
            name="child",
            namespace="ns",
            parent_id=parent.pk,
            depth=1,
        )
        parent = await ItGraphNode.get(pk=parent.pk)
        assert parent.child_count == 1
        await child.delete()
        parent = await ItGraphNode.get(pk=parent.pk)
        assert parent.child_count == 0

    @pytest.mark.asyncio
    async def test_child_count_survives_full_lifecycle(self) -> None:
        """child_count remains correct after a full create-reparent-delete cycle."""
        p1 = await ItGraphNode.create(
            name="p1",
            namespace="ns",
            depth=0,
        )
        p2 = await ItGraphNode.create(
            name="p2",
            namespace="ns",
            depth=0,
        )
        child = await ItGraphNode.create(
            name="child",
            namespace="ns",
            parent_id=p1.pk,
            depth=1,
        )
        # Reparent p1 -> p2
        child.parent_id = p2.pk
        await child.save()
        # Delete child
        await child.delete()
        p1 = await ItGraphNode.get(pk=p1.pk)
        p2 = await ItGraphNode.get(pk=p2.pk)
        assert p1.child_count == 0
        assert p2.child_count == 0


# ---------------------------------------------------------------------------
# Namespace scoping — traversal isolation
# ---------------------------------------------------------------------------


class TestGraphNodeNamespaceScoping:
    """descendants / ancestors never leak across namespace boundaries."""

    @pytest.mark.asyncio
    async def test_descendants_are_scoped_to_namespace(self) -> None:
        """descendants() only returns nodes in the same namespace."""
        electronics, laptops, macbook, phones = await _make_tree()
        # Add a node in shop that is at depth=2 but NOT reachable via
        # electronics (it is a child of office_root's lineage)
        # This is already set up: office_root and office_child exist.
        shop_desc = {n.name for n in await electronics.descendants().all()}
        assert shop_desc == {"laptops", "phones", "macbook"}
        assert "office_root" not in shop_desc
        assert "office_child" not in shop_desc

    @pytest.mark.asyncio
    async def test_ancestors_are_scoped_to_namespace(self) -> None:
        """ancestors() only returns nodes in the same namespace."""
        electronics, laptops, macbook, phones = await _make_tree()
        shop_anc = {n.name for n in await macbook.ancestors().all()}
        assert "office_root" not in shop_anc
        assert "office_child" not in shop_anc

    @pytest.mark.asyncio
    async def test_siblings_cross_namespace(self) -> None:
        """siblings() filters by parent_id, not namespace — two nodes with
        the same parent but different namespaces are siblings."""
        parent_a = await ItGraphNode.create(
            name="p_a",
            namespace="ns_a",
            depth=0,
        )
        child_a = await ItGraphNode.create(
            name="child_a",
            namespace="ns_a",
            parent_id=parent_a.pk,
            depth=1,
        )
        await ItGraphNode.create(
            name="child_b",
            namespace="ns_b",
            parent_id=parent_a.pk,
            depth=1,
        )
        # Both share the same parent_id — they are siblings by parent link
        sibs = await child_a.siblings().all()
        assert [s.name for s in sibs] == ["child_b"]

    @pytest.mark.asyncio
    async def test_deep_namespace_tree_isolation(self) -> None:
        """A 3-level deep tree in one namespace never leaks into another."""
        ns1_root = await ItGraphNode.create(
            name="ns1_root",
            namespace="alpha",
            depth=0,
            is_root=True,
        )
        ns1_mid = await ItGraphNode.create(
            name="ns1_mid",
            namespace="alpha",
            parent_id=ns1_root.pk,
            depth=1,
        )
        await ItGraphNode.create(
            name="ns1_leaf",
            namespace="alpha",
            parent_id=ns1_mid.pk,
            depth=2,
        )
        ns2_root = await ItGraphNode.create(
            name="ns2_root",
            namespace="beta",
            depth=0,
            is_root=True,
        )
        await ItGraphNode.create(
            name="ns2_child",
            namespace="beta",
            parent_id=ns2_root.pk,
            depth=1,
        )
        desc = {n.name for n in await ns1_root.descendants().all()}
        assert desc == {"ns1_mid", "ns1_leaf"}
        assert "ns2_root" not in desc
        assert "ns2_child" not in desc
