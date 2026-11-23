"""Integration tests for NodeMixin against live PostgreSQL.

Requires the docker stack (see ``docker-compose.dev.yml``): PostgreSQL on
``127.0.0.1:5434``, database ``tortoise_test``.

Run with: uv run pytest tests/test_node_mixin_integration.py -v
"""

import os
import socket

import pytest
from tortoise import Tortoise, fields
from tortoise.transactions import in_transaction

import tortoise_extended  # noqa: F401 — apply patches
from tortoise_extended._types import CacheValue
from tortoise_extended.exceptions import GraphError
from tortoise_extended.models import BaseModel, NodeMixin


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
# Test models
# ---------------------------------------------------------------------------


class HybridNode(BaseModel, NodeMixin):
    """BigInt-keyed node: the mixin annotation must not create a column."""

    parent_id = fields.BigIntField(null=True, db_index=True)

    class Meta:
        table = "node_mixin_it_hybrid"
        indexes = (("namespace", "depth"),)


class OrderedHybridNode(BaseModel, NodeMixin):
    """BigInt-keyed node ordering traversal by ``namespace``, not by id."""

    parent_id = fields.BigIntField(null=True, db_index=True)
    ordering_field = "namespace"

    class Meta:
        table = "node_mixin_it_ordered"
        indexes = ()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_TABLES = (
    "node_mixin_it_hybrid",
    "node_mixin_it_ordered",
)


@pytest.fixture(scope="module", autouse=True)
async def _init_db():
    """Initialize Tortoise ORM for the test module."""
    await Tortoise.init(
        db_url=DB_URL,
        modules={"models": ["tests.test_node_mixin_integration"]},
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
    """Truncate the node tables before every test."""
    conn = Tortoise.get_connection("default")
    await conn.execute_query(
        "TRUNCATE node_mixin_it_hybrid, node_mixin_it_ordered CASCADE"
    )
    yield


async def _make_shop_tree() -> tuple[HybridNode, HybridNode, HybridNode, HybridNode]:
    """Seed a two-branch ``shop`` tree plus a foreign ``office`` tree."""
    electronics = await HybridNode.create(
        namespace="shop",
        depth=0,
        is_root=True,
        metadata_json={"label": "electronics"},
    )
    laptops = await HybridNode.create(
        namespace="shop",
        depth=1,
        parent_id=electronics.pk,
        metadata_json={"label": "laptops"},
    )
    macbook = await HybridNode.create(
        namespace="shop",
        depth=2,
        parent_id=laptops.pk,
        metadata_json={"label": "macbook"},
    )
    phones = await HybridNode.create(
        namespace="shop",
        depth=1,
        parent_id=electronics.pk,
        metadata_json={"label": "phones"},
    )
    await HybridNode.create(
        namespace="office",
        depth=0,
        is_root=True,
        metadata_json={"label": "office_root"},
    )
    await HybridNode.create(
        namespace="office",
        depth=1,
        metadata_json={"label": "office_child"},
    )
    # Re-fetch the returned instances so child_count reflects the
    # post-hook denormalized state.
    electronics = await HybridNode.get(pk=electronics.pk)
    laptops = await HybridNode.get(pk=laptops.pk)
    macbook = await HybridNode.get(pk=macbook.pk)
    phones = await HybridNode.get(pk=phones.pk)
    return electronics, laptops, macbook, phones


def _labels(nodes: list[HybridNode]) -> list[str]:
    """Return the ``metadata_json`` label of each node, in order."""
    return [node.metadata_json["label"] for node in nodes]


# ---------------------------------------------------------------------------
# Runtime identity: annotation vs column
# ---------------------------------------------------------------------------


class TestHybridIdentityRuntime:
    """The mixin annotation is not a column; the subclass BigInt field wins."""

    async def test_mixin_annotation_creates_no_column(self) -> None:
        assert "parent_id" not in NodeMixin._meta.fields_map
        assert isinstance(HybridNode._meta.fields_map["parent_id"], fields.BigIntField)
        conn = Tortoise.get_connection("default")
        rows = await conn.execute_query_dict(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = $1 "
            "AND column_name IN ('id', 'parent_id') ORDER BY column_name",
            ["node_mixin_it_hybrid"],
        )
        assert [(row["column_name"], row["data_type"]) for row in rows] == [
            ("id", "bigint"),
            ("parent_id", "bigint"),
        ]

    async def test_bigint_parent_key_round_trips(self) -> None:
        parent = await HybridNode.create(namespace="ns", depth=0)
        child = await HybridNode.create(
            namespace="ns",
            depth=1,
            parent_id=parent.pk,
        )
        assert isinstance(parent.pk, int)
        assert isinstance(child.parent_id, int)
        stored = await HybridNode.get(pk=child.pk)
        assert stored.parent_id == parent.pk


# ---------------------------------------------------------------------------
# child_count denormalization across create / reparent / delete
# ---------------------------------------------------------------------------


class TestChildCount:
    """child_count stays in sync for BigInt-keyed nodes too."""

    async def test_create_increments_parent_child_count(self) -> None:
        parent = await HybridNode.create(namespace="ns", depth=0)
        assert (await HybridNode.get(pk=parent.pk)).child_count == 0
        await HybridNode.create(namespace="ns", depth=1, parent_id=parent.pk)
        assert (await HybridNode.get(pk=parent.pk)).child_count == 1

    async def test_reparent_moves_the_child_and_adjusts_both_counts(self) -> None:
        first = await HybridNode.create(namespace="ns", depth=0)
        second = await HybridNode.create(namespace="ns", depth=0)
        child = await HybridNode.create(
            namespace="ns",
            depth=1,
            parent_id=first.pk,
        )
        child.parent_id = second.pk
        await child.save()
        stored = await HybridNode.get(pk=child.pk)
        assert stored.parent_id == second.pk
        assert (await HybridNode.get(pk=first.pk)).child_count == 0
        assert (await HybridNode.get(pk=second.pk)).child_count == 1

    async def test_delete_decrements_parent_child_count(self) -> None:
        parent = await HybridNode.create(namespace="ns", depth=0)
        child = await HybridNode.create(
            namespace="ns",
            depth=1,
            parent_id=parent.pk,
        )
        assert (await HybridNode.get(pk=parent.pk)).child_count == 1
        await child.delete()
        assert (await HybridNode.get(pk=parent.pk)).child_count == 0


# ---------------------------------------------------------------------------
# ordering_field drives children()/siblings()
# ---------------------------------------------------------------------------


class TestOrderingField:
    """children()/siblings() order by the class's ``ordering_field``."""

    async def test_children_order_by_ordering_field(self) -> None:
        parent = await OrderedHybridNode.create(namespace="root", depth=0)
        zeta = await OrderedHybridNode.create(
            namespace="zeta",
            depth=1,
            parent_id=parent.pk,
        )
        alpha = await OrderedHybridNode.create(
            namespace="alpha",
            depth=1,
            parent_id=parent.pk,
        )
        mike = await OrderedHybridNode.create(
            namespace="mike",
            depth=1,
            parent_id=parent.pk,
        )
        # Ids follow creation order, so id ordering would be zeta/alpha/mike.
        assert [zeta.pk, alpha.pk, mike.pk] == sorted([zeta.pk, alpha.pk, mike.pk])
        children = await parent.children().all()
        assert [node.namespace for node in children] == ["alpha", "mike", "zeta"]

    async def test_siblings_order_by_ordering_field(self) -> None:
        parent = await OrderedHybridNode.create(namespace="root", depth=0)
        await OrderedHybridNode.create(
            namespace="zeta",
            depth=1,
            parent_id=parent.pk,
        )
        alpha = await OrderedHybridNode.create(
            namespace="alpha",
            depth=1,
            parent_id=parent.pk,
        )
        await OrderedHybridNode.create(
            namespace="mike",
            depth=1,
            parent_id=parent.pk,
        )
        siblings = await alpha.siblings().all()
        assert [node.namespace for node in siblings] == ["mike", "zeta"]


# ---------------------------------------------------------------------------
# Link-exact traversal and depth-range namespace scoping
# ---------------------------------------------------------------------------


class TestTraversal:
    """subtree()/path_to_root() follow links; descendants()/ancestors() scope."""

    async def test_children_follow_creation_order_by_default(self) -> None:
        electronics, _, _, _ = await _make_shop_tree()
        children = await electronics.children().all()
        assert _labels(children) == ["laptops", "phones"]

    async def test_subtree_is_breadth_first(self) -> None:
        electronics, laptops, macbook, _ = await _make_shop_tree()
        subtree = await electronics.subtree()
        assert _labels(subtree) == ["electronics", "laptops", "phones", "macbook"]
        assert _labels(await laptops.subtree()) == ["laptops", "macbook"]
        assert _labels(await macbook.subtree()) == ["macbook"]

    async def test_path_to_root_walks_parent_links(self) -> None:
        electronics, laptops, macbook, _ = await _make_shop_tree()
        assert _labels(await macbook.path_to_root()) == [
            "electronics",
            "laptops",
            "macbook",
        ]
        assert _labels(await electronics.path_to_root()) == ["electronics"]

    async def test_descendants_are_scoped_to_namespace(self) -> None:
        electronics, _, _, _ = await _make_shop_tree()
        labels = _labels(await electronics.descendants().all())
        assert labels == ["laptops", "phones", "macbook"]
        assert "office_root" not in labels
        assert "office_child" not in labels

    async def test_ancestors_are_scoped_to_namespace(self) -> None:
        electronics, _, macbook, _ = await _make_shop_tree()
        labels = _labels(await macbook.ancestors().all())
        # Depth-range approximation: every shallower shop node appears.
        assert labels[0] == "electronics"
        assert set(labels) == {"electronics", "laptops", "phones"}
        assert "office_root" not in labels
        assert "office_child" not in labels
        assert await electronics.ancestors().all() == []


# ---------------------------------------------------------------------------
# add_child: INSERT with inherited links, single child_count bump
# ---------------------------------------------------------------------------


class _ForcedRollback(Exception):
    """Sentinel raised inside the transaction to force a rollback."""


class TestAddChild:
    """add_child() links a new child and bumps child_count exactly once."""

    async def test_links_the_child_and_inherits_parent_fields(self) -> None:
        parent = await HybridNode.create(namespace="shop", depth=0, is_root=True)
        child = await parent.add_child(metadata_json={"label": "kid"})
        assert child.pk is not None
        assert child.parent_id == parent.pk
        assert child.depth == 1
        assert child.is_root is False
        assert child.namespace == "shop"
        assert child.metadata_json == {"label": "kid"}
        stored = await HybridNode.get(pk=child.pk)
        assert stored.parent_id == parent.pk
        assert stored.depth == 1

    async def test_accepts_nested_json_metadata(self) -> None:
        """``metadata_json`` takes the recursive JSON shape, not scalars only."""
        parent = await HybridNode.create(namespace="shop", depth=0)
        payload: dict[str, CacheValue] = {
            "label": "kit",
            "nested": {"tags": ["a", "b"], "depth": 2},
            "flags": [True, None, 1.5],
        }
        child = await parent.add_child(metadata_json=payload)
        assert child.metadata_json == payload
        stored = await HybridNode.get(pk=child.pk)
        assert stored.metadata_json == payload

    async def test_bumps_child_count_exactly_once(self) -> None:
        parent = await HybridNode.create(namespace="shop", depth=0)
        assert parent.child_count == 0
        await parent.add_child()
        # In-memory sync plus exactly one DB increment (save() does that one).
        assert parent.child_count == 1
        assert (await HybridNode.get(pk=parent.pk)).child_count == 1
        await parent.add_child()
        assert parent.child_count == 2
        assert (await HybridNode.get(pk=parent.pk)).child_count == 2

    async def test_allows_field_overrides(self) -> None:
        parent = await HybridNode.create(namespace="shop", depth=0)
        child = await parent.add_child(namespace="office", depth=5, is_root=True)
        assert child.namespace == "office"
        assert child.depth == 5
        assert child.is_root is True
        assert child.parent_id == parent.pk

    async def test_requires_a_persisted_parent(self) -> None:
        unsaved = HybridNode(namespace="shop", depth=0)
        assert unsaved.pk is None
        with pytest.raises(GraphError, match="unpersisted"):
            await unsaved.add_child()

    async def test_turns_a_leaf_into_a_branch(self) -> None:
        parent = await HybridNode.create(namespace="shop", depth=0)
        assert parent.is_leaf is True
        await parent.add_child()
        assert parent.is_leaf is False

    async def test_using_db_joins_the_callers_transaction(self) -> None:
        """An explicit connection handle is accepted and yields a consistent tree.

        Measured 2026-11-23: this also passes with ``using_db=None`` — tortoise
        routes writes on the default client to the ambient ``in_transaction()``
        connection, so the argument is not what makes the write atomic. What
        this pins is the public parameter: dropping it fails with ``TypeError``.
        """
        async with in_transaction() as conn:
            parent = await HybridNode.create(
                using_db=conn,
                namespace="atomic",
                depth=0,
                is_root=True,
            )
            first = await parent.add_child(
                using_db=conn, metadata_json={"label": "first"}
            )
            second = await parent.add_child(
                using_db=conn, metadata_json={"label": "second"}
            )
        stored = await HybridNode.all()
        assert {node.pk for node in stored} == {parent.pk, first.pk, second.pk}
        assert await HybridNode.filter(namespace="atomic").count() == 3
        # The denormalized counter maintenance in save() joined the same
        # transaction, so both bumps committed with the rows.
        assert (await HybridNode.get(pk=parent.pk)).child_count == 2

    async def test_using_db_rolls_back_parent_and_children_together(self) -> None:
        """A raise inside the shared transaction removes all three rows.

        Same caveat as above: ambient routing already gives this with
        ``using_db=None``, so this is a contract pin, not a regression test for
        the parameter.
        """
        with pytest.raises(_ForcedRollback):
            async with in_transaction() as conn:
                parent = await HybridNode.create(
                    using_db=conn,
                    namespace="atomic",
                    depth=0,
                    is_root=True,
                )
                await parent.add_child(using_db=conn)
                await parent.add_child(using_db=conn)
                raise _ForcedRollback("forced rollback")
        assert await HybridNode.all().count() == 0


# ---------------------------------------------------------------------------
# move_to: subtree reparenting, depth shift and child_count maintenance
# ---------------------------------------------------------------------------


class TestMoveTo:
    """move_to() reparents a subtree and recomputes depths in bulk."""

    async def test_moves_subtree_with_correct_depths(self) -> None:
        electronics = await HybridNode.create(namespace="shop", depth=0, is_root=True)
        laptops = await HybridNode.create(
            namespace="shop", depth=1, parent_id=electronics.pk
        )
        macbook = await HybridNode.create(
            namespace="shop", depth=2, parent_id=laptops.pk
        )
        charger = await HybridNode.create(
            namespace="shop", depth=3, parent_id=macbook.pk
        )
        kitchen = await HybridNode.create(namespace="shop", depth=0, is_root=True)
        shelf = await HybridNode.create(namespace="shop", depth=1, parent_id=kitchen.pk)

        # Move a root with a three-node subtree under a depth-1 parent.  The
        # depth delta is +2 for every node in the moved subtree.
        await electronics.move_to(shelf)

        # The moved instance reflects its new position — no stale attributes.
        assert electronics.parent_id == shelf.pk
        assert electronics.depth == 2
        assert electronics.is_root is False
        assert electronics.child_count == 1

        moved_laptops = await HybridNode.get(pk=laptops.pk)
        moved_macbook = await HybridNode.get(pk=macbook.pk)
        moved_charger = await HybridNode.get(pk=charger.pk)
        assert (moved_laptops.parent_id, moved_laptops.depth) == (
            electronics.pk,
            3,
        )
        assert (moved_macbook.parent_id, moved_macbook.depth) == (laptops.pk, 4)
        assert (moved_charger.parent_id, moved_charger.depth) == (macbook.pk, 5)

        # Counters: the old parent loses the moved node, the new parent gains
        # it, and the moved node keeps its own child.
        assert (await HybridNode.get(pk=shelf.pk)).child_count == 1
        assert (await HybridNode.get(pk=kitchen.pk)).child_count == 1

    async def test_moved_node_is_the_only_child_moved(self) -> None:
        """Moving one branch leaves its siblings under the old parent."""
        old_root = await HybridNode.create(namespace="shop", depth=0, is_root=True)
        branch = await HybridNode.create(
            namespace="shop", depth=1, parent_id=old_root.pk
        )
        sibling = await HybridNode.create(
            namespace="shop", depth=1, parent_id=old_root.pk
        )
        new_parent = await HybridNode.create(namespace="shop", depth=0, is_root=True)

        await branch.move_to(new_parent)

        assert (await HybridNode.get(pk=old_root.pk)).child_count == 1
        assert (await HybridNode.get(pk=sibling.pk)).parent_id == old_root.pk
        assert (await HybridNode.get(pk=new_parent.pk)).child_count == 1

    async def test_detach_to_root_resets_depth_and_is_root(self) -> None:
        root = await HybridNode.create(namespace="shop", depth=0, is_root=True)
        child = await HybridNode.create(namespace="shop", depth=1, parent_id=root.pk)
        grandchild = await HybridNode.create(
            namespace="shop", depth=2, parent_id=child.pk
        )

        await child.move_to(None)

        assert child.parent_id is None
        assert child.depth == 0
        assert child.is_root is True
        assert (await HybridNode.get(pk=grandchild.pk)).depth == 1
        assert (await HybridNode.get(pk=root.pk)).child_count == 0

    async def test_move_into_own_descendant_raises(self) -> None:
        root = await HybridNode.create(namespace="shop", depth=0, is_root=True)
        child = await HybridNode.create(namespace="shop", depth=1, parent_id=root.pk)
        grandchild = await HybridNode.create(
            namespace="shop", depth=2, parent_id=child.pk
        )

        with pytest.raises(GraphError, match="own descendant"):
            await root.move_to(grandchild)

        # The transaction rolled back: nothing moved.
        assert (await HybridNode.get(pk=root.pk)).parent_id is None
        assert (await HybridNode.get(pk=grandchild.pk)).depth == 2

    async def test_move_into_self_raises(self) -> None:
        root = await HybridNode.create(namespace="shop", depth=0, is_root=True)

        with pytest.raises(GraphError, match="under itself"):
            await root.move_to(root)

    async def test_cross_namespace_move_raises(self) -> None:
        shop_node = await HybridNode.create(namespace="shop", depth=0, is_root=True)
        office_node = await HybridNode.create(namespace="office", depth=0, is_root=True)

        with pytest.raises(GraphError, match="across namespaces"):
            await shop_node.move_to(office_node)

        # Rolled back: the source is untouched and no counter moved.
        stored = await HybridNode.get(pk=shop_node.pk)
        assert stored.parent_id is None
        assert stored.is_root is True
        assert (await HybridNode.get(pk=office_node.pk)).child_count == 0

    async def test_move_to_current_parent_keeps_child_count(self) -> None:
        parent = await HybridNode.create(namespace="shop", depth=0, is_root=True)
        child = await HybridNode.create(namespace="shop", depth=1, parent_id=parent.pk)

        await child.move_to(parent)

        assert child.parent_id == parent.pk
        assert child.depth == 1
        assert (await HybridNode.get(pk=parent.pk)).child_count == 1

    async def test_unpersisted_nodes_are_rejected(self) -> None:
        saved = await HybridNode.create(namespace="shop", depth=0, is_root=True)
        unsaved_parent = HybridNode(namespace="shop", depth=0)

        with pytest.raises(GraphError, match="unpersisted parent"):
            await saved.move_to(unsaved_parent)

        unsaved = HybridNode(namespace="shop", depth=0)
        with pytest.raises(GraphError, match="unpersisted"):
            await unsaved.move_to(None)


# ---------------------------------------------------------------------------
# root() and strict ancestry
# ---------------------------------------------------------------------------


class TestRootAndAncestry:
    """root()/is_ancestor_of()/is_descendant_of() walk parent_id links."""

    async def test_root_of_a_leaf_is_the_top_node_not_itself(self) -> None:
        electronics, laptops, macbook, _ = await _make_shop_tree()
        assert (await macbook.root()).pk == electronics.pk
        assert (await macbook.root()).pk != macbook.pk
        assert (await laptops.root()).pk == electronics.pk

    async def test_root_of_a_root_is_itself(self) -> None:
        electronics, _, _, _ = await _make_shop_tree()
        assert (await electronics.root()).pk == electronics.pk

    async def test_is_ancestor_of_walks_the_parent_chain(self) -> None:
        electronics, laptops, macbook, _ = await _make_shop_tree()
        assert await electronics.is_ancestor_of(macbook) is True
        assert await laptops.is_ancestor_of(macbook) is True
        assert await macbook.is_ancestor_of(electronics) is False

    async def test_is_descendant_of_walks_the_parent_chain(self) -> None:
        electronics, laptops, macbook, _ = await _make_shop_tree()
        assert await macbook.is_descendant_of(electronics) is True
        assert await macbook.is_descendant_of(laptops) is True
        assert await electronics.is_descendant_of(macbook) is False

    async def test_a_node_is_not_its_own_ancestor_or_descendant(self) -> None:
        _, _, macbook, _ = await _make_shop_tree()
        assert await macbook.is_ancestor_of(macbook) is False
        assert await macbook.is_descendant_of(macbook) is False

    async def test_siblings_are_not_ancestors_of_each_other(self) -> None:
        _, laptops, _, phones = await _make_shop_tree()
        assert await laptops.is_ancestor_of(phones) is False
        assert await phones.is_descendant_of(laptops) is False

    async def test_ancestry_does_not_cross_namespaces(self) -> None:
        electronics, _, _, _ = await _make_shop_tree()
        office_child = await HybridNode.get(namespace="office", depth=1)
        assert await electronics.is_ancestor_of(office_child) is False


# ---------------------------------------------------------------------------
# to_tree over a real descendants() slice
# ---------------------------------------------------------------------------


class TestToTreeIntegration:
    """to_tree() promotes a descendants() slice whose root is outside it."""

    async def test_promotes_a_descendants_slice(self) -> None:
        electronics, laptops, macbook, phones = await _make_shop_tree()
        slice_ = await electronics.descendants().all()
        forest = HybridNode.to_tree(slice_)
        assert [tree.node.pk for tree in forest] == [laptops.pk, phones.pk]
        assert [tree.node.pk for tree in forest[0].children] == [macbook.pk]
        assert forest[1].children == []
