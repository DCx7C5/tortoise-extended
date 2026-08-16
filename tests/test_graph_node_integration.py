"""Integration tests for BaseGraphNodeModel helper methods against live PostgreSQL.

Requires the docker stack (see ``docker-compose.dev.yml``): PostgreSQL 18 on
``127.0.0.1:5433``, database ``tortoise_test``.

Run with: uv run pytest tests/test_graph_node_integration.py -v
"""

import os
import socket
import uuid

import pytest
from tortoise import Tortoise

import tortoise_extended  # noqa: F401 — apply patches
from tortoise_extended.models import BaseGraphNodeModel


# ---------------------------------------------------------------------------
# Config — skip entire module if PG is not available
# ---------------------------------------------------------------------------

DB_URL = os.environ.get(
    "TORTOISE_TEST_DB",
    "postgres://postgres:postgres@localhost:5433/tortoise_test",
)


def _pg_available() -> bool:
    """Quick check — can we connect to the test PG?"""
    try:
        sock = socket.create_connection(("localhost", 5433), timeout=2)
        sock.close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="PostgreSQL not available on localhost:5433",
)


# ---------------------------------------------------------------------------
# Test model
# ---------------------------------------------------------------------------


class ItGraphNode(BaseGraphNodeModel):
    """BaseGraphNodeModel subclass under test."""

    class Meta:
        table = "graph_node_it_nodes"


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
    await conn.execute_query("DROP TABLE IF EXISTS graph_node_it_nodes CASCADE")
    await Tortoise.close_connections()


@pytest.fixture(autouse=True)
async def _clean_table():
    """Truncate the graph node table before every test."""
    conn = Tortoise.get_connection("default")
    await conn.execute_query("TRUNCATE graph_node_it_nodes CASCADE")
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
# BaseGraphNodeModel helpers
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
