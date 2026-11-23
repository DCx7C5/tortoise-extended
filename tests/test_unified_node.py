"""Integration tests for ``BaseUnifiedNode`` — the BigInt-keyed node base.

Requires the docker stack (see ``docker-compose.dev.yml``): PostgreSQL on
``127.0.0.1:5434``, database ``tortoise_test_unified``.  The module skips when
the socket is closed; ``TORTOISE_TEST_DB`` overrides the URL.

Run with: uv run pytest tests/test_unified_node.py -v
"""

import os
import socket
from collections.abc import AsyncGenerator
from uuid import UUID

import pytest
from tortoise import Tortoise
from tortoise import fields as tf

import tortoise_extended  # noqa: F401 — apply patches
from tortoise_extended.fields.uuid import UUID7Field
from tortoise_extended.models import BaseUnifiedNode, NodeMixin, UnifiedIdModel

# --- Test model ------------------------------------------------------------


class UnifiedNode(BaseUnifiedNode):
    """Concrete BigInt-keyed node: only ``Meta.table`` and ``Meta.indexes``."""

    name = tf.CharField(max_length=64, description="Human-readable node name")

    class Meta:
        table = "unified_nodes"
        indexes = (("namespace", "depth"),)


# --- Config — skip entire module if PG is not available --------------------


DB_URL = os.environ.get(
    "TORTOISE_TEST_DB",
    "postgres://postgres:postgres@localhost:5434/tortoise_test_unified",
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


# --- Fixtures --------------------------------------------------------------

_TABLES = ("unified_nodes",)


@pytest.fixture(scope="module", autouse=True)
async def _init_db() -> AsyncGenerator[None, None]:
    """Initialize Tortoise ORM against the live PostgreSQL test database."""
    _ = await Tortoise.init(
        db_url=DB_URL,
        modules={"models": ["tests.test_unified_node"]},
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
    """Truncate the node table before every test."""
    conn = Tortoise.get_connection("default")
    await conn.execute_query("TRUNCATE unified_nodes CASCADE")
    yield


async def _seed_chain() -> tuple[UnifiedNode, UnifiedNode, UnifiedNode]:
    """Create ``root -> child -> grandchild`` in the ``shop`` namespace."""
    root = await UnifiedNode.create(namespace="shop", is_root=True, name="root")
    child = await root.add_child(name="child")
    grandchild = await child.add_child(name="grandchild")
    return root, child, grandchild


# --- Shape: the merged identity, uid and integer parent key ----------------


class TestUnifiedNodeShape:
    """BaseUnifiedNode merges BigInt identity, UUID7 uid and NodeMixin."""

    def test_identity_is_bigint_pk_plus_uuid7_uid(self) -> None:
        fields_map = UnifiedNode._meta.fields_map
        assert UnifiedNode._meta.pk_attr == "id"
        assert isinstance(fields_map["id"], tf.BigIntField)
        assert fields_map["id"].pk is True
        assert isinstance(fields_map["uid"], UUID7Field)
        assert fields_map["uid"].unique is True

    def test_parent_id_is_a_bigint_column(self) -> None:
        parent_field = UnifiedNode._meta.fields_map["parent_id"]
        assert isinstance(parent_field, tf.BigIntField)
        assert parent_field.null is True

    def test_inherits_both_halves_and_the_node_helpers(self) -> None:
        assert issubclass(UnifiedNode, UnifiedIdModel)
        assert issubclass(UnifiedNode, NodeMixin)
        # NodeMixin's default ordering is the BigInt pk.
        assert UnifiedNode.ordering_field == "id"

    def test_base_is_abstract_and_declares_the_index(self) -> None:
        assert BaseUnifiedNode.Meta.abstract is True
        assert BaseUnifiedNode.Meta.indexes == (("namespace", "depth"),)

    async def test_postgres_column_types(self) -> None:
        conn = Tortoise.get_connection("default")
        rows = await conn.execute_query_dict(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = $1 "
            "AND column_name IN ('id', 'parent_id', 'uid') ORDER BY column_name",
            ["unified_nodes"],
        )
        assert [(row["column_name"], row["data_type"]) for row in rows] == [
            ("id", "bigint"),
            ("parent_id", "bigint"),
            ("uid", "uuid"),
        ]


# --- A 3-level BigInt-keyed tree ------------------------------------------


class TestThreeLevelTree:
    """add_child / descendants / move_to / to_tree on integer parent keys."""

    async def test_add_child_builds_integer_parent_links(self) -> None:
        root, child, grandchild = await _seed_chain()

        assert isinstance(root.id, int)
        assert isinstance(root.uid, UUID)
        assert isinstance(child.parent_id, int)
        assert child.parent_id == root.id
        assert grandchild.parent_id == child.id
        assert (child.depth, grandchild.depth) == (1, 2)
        assert root.child_count == 1
        stored = await UnifiedNode.get(pk=grandchild.pk)
        assert stored.parent_id == child.id

    async def test_descendants_walk_the_chain(self) -> None:
        root, child, grandchild = await _seed_chain()

        descendants = await root.descendants().all()
        assert [node.name for node in descendants] == ["child", "grandchild"]
        assert [node.id for node in descendants] == [child.id, grandchild.id]
        assert all(isinstance(node.parent_id, int) for node in descendants)

    async def test_move_to_reparents_with_integer_keys(self) -> None:
        root, child, grandchild = await _seed_chain()
        kitchen = await UnifiedNode.create(
            namespace="shop", is_root=True, name="kitchen"
        )
        shelf = await kitchen.add_child(name="shelf")

        await child.move_to(shelf)

        # The moved instance reflects its new position, still BigInt-keyed.
        assert child.parent_id == shelf.id
        assert isinstance(child.parent_id, int)
        assert child.depth == shelf.depth + 1

        stored_child = await UnifiedNode.get(pk=child.id)
        stored_grandchild = await UnifiedNode.get(pk=grandchild.id)
        assert stored_child.parent_id == shelf.id
        assert stored_grandchild.parent_id == child.id
        assert stored_grandchild.depth == shelf.depth + 2
        # Counters: old parent lost the branch, new parent gained it.
        assert (await UnifiedNode.get(pk=root.id)).child_count == 0
        assert (await UnifiedNode.get(pk=shelf.id)).child_count == 1
        assert await stored_child.validate_node() == []
        assert await stored_grandchild.validate_node() == []

    async def test_to_tree_nests_the_descendants_slice(self) -> None:
        root, child, grandchild = await _seed_chain()

        forest = UnifiedNode.to_tree(await root.descendants().all())

        # The slice's top node is promoted: its parent lives outside it.
        assert len(forest) == 1
        assert forest[0].node.id == child.id
        assert isinstance(forest[0].node.parent_id, int)
        assert [tree.node.id for tree in forest[0].children] == [grandchild.id]
        assert forest[0].children[0].children == []

    async def test_uid_stays_unique_across_rows(self) -> None:
        root, child, grandchild = await _seed_chain()
        other = await UnifiedNode.create(namespace="shop", is_root=True, name="other")

        nodes = [root, child, grandchild, other]
        uids = [node.uid for node in nodes]
        assert all(isinstance(uid, UUID) for uid in uids)
        assert len(set(uids)) == len(nodes)
        assert all(isinstance(node.id, int) for node in nodes)

        conn = Tortoise.get_connection("default")
        rows = await conn.execute_query_dict(
            "SELECT count(*) AS total, count(DISTINCT uid) AS distinct_uids "
            "FROM unified_nodes"
        )
        assert rows[0]["total"] == rows[0]["distinct_uids"] == len(nodes)


# --- Exports ---------------------------------------------------------------


class TestExports:
    """BaseUnifiedNode is re-exported from the models package and top level."""

    def test_exports(self) -> None:
        from tortoise_extended import BaseUnifiedNode as TopLevel
        from tortoise_extended.models import BaseUnifiedNode as ModelsLevel

        assert TopLevel is BaseUnifiedNode
        assert ModelsLevel is BaseUnifiedNode
