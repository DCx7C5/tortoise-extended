"""Integration tests for NodeMixin against live PostgreSQL.

Requires the docker stack (see ``docker-compose.dev.yml``): PostgreSQL on
``127.0.0.1:5434``, database ``tortoise_test``.

Run with: uv run pytest tests/test_node_mixin_integration.py -v
"""

import os
import socket

import pytest
from tortoise import Tortoise, fields

import tortoise_extended  # noqa: F401 — apply patches
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


@pytest.fixture(scope="module", autouse=True)
async def _init_db():
    """Initialize Tortoise ORM for the test module."""
    await Tortoise.init(
        db_url=DB_URL,
        modules={"models": ["tests.test_node_mixin_integration"]},
    )
    await Tortoise.generate_schemas()
    yield
    conn = Tortoise.get_connection("default")
    await conn.execute_query("DROP TABLE IF EXISTS node_mixin_it_ordered CASCADE")
    await conn.execute_query("DROP TABLE IF EXISTS node_mixin_it_hybrid CASCADE")
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
