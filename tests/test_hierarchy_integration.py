"""Integration tests for LTreeField filters and HierarchyModel against live PostgreSQL.

Requires the docker stack (see ``docker-compose.dev.yml``): PostgreSQL 18 on
``127.0.0.1:5433`` with the ``ltree`` extension, database ``tortoise_test``.

Run with: uv run pytest tests/test_hierarchy_integration.py -v
"""

import os
import socket

import pytest
from tortoise import Tortoise

import tortoise_extended  # noqa: F401 — apply patches
from tortoise_extended.exceptions import HierarchyError
from tortoise_extended.graph.hierarchy_model import HierarchyModel

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


class Category(HierarchyModel):
    """HierarchyModel subclass under test."""

    class Meta:
        table = "hierarchy_it_categories"
        verbose_name = "Category"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
async def _init_db():
    """Initialize Tortoise ORM for the test module."""
    await Tortoise.init(
        db_url=DB_URL,
        modules={"models": ["tests.test_hierarchy_integration"]},
    )
    await Tortoise.generate_schemas()
    yield
    conn = Tortoise.get_connection("default")
    await conn.execute_query("DROP TABLE IF EXISTS hierarchy_it_categories CASCADE")
    await Tortoise.close_connections()


@pytest.fixture(autouse=True)
async def _clean_table():
    """Truncate the hierarchy table before every test."""
    conn = Tortoise.get_connection("default")
    await conn.execute_query("TRUNCATE hierarchy_it_categories CASCADE")
    yield


async def _make_tree() -> tuple[Category, Category, Category, Category]:
    """Seed a two-branch category tree in the ``shop`` namespace:

    - electronics
      - laptops
        - macbook
      - phones
    """
    electronics = await Category.create(
        path="electronics", name="electronics", parent_id=None, depth=0, namespace="shop"
    )
    laptops = await Category.create(
        path="electronics.laptops", name="laptops", parent_id=electronics.pk,
        depth=1, namespace="shop",
    )
    macbook = await Category.create(
        path="electronics.laptops.macbook", name="macbook",
        parent_id=laptops.pk, depth=2, namespace="shop",
    )
    phones = await Category.create(
        path="electronics.phones", name="phones", parent_id=electronics.pk,
        depth=1, namespace="shop",
    )
    return electronics, laptops, macbook, phones


# ---------------------------------------------------------------------------
# ltree filters — registered via _apply_patches (C4 regression)
# ---------------------------------------------------------------------------


class TestLTreeFilters:
    """Direct ltree filter operators against real PostgreSQL."""

    @pytest.mark.asyncio
    async def test_ancestor_of(self) -> None:
        electronics, laptops, macbook, phones = await _make_tree()
        # ltree @> includes self — the path is its own ancestor
        q = await Category.filter(path__ancestor_of="electronics.laptops.macbook").order_by("path")
        assert [n.name for n in q] == ["electronics", "laptops", "macbook"]

    @pytest.mark.asyncio
    async def test_descendant_of(self) -> None:
        electronics, laptops, macbook, phones = await _make_tree()
        # ltree <@ includes self — the path is its own descendant
        q = await Category.filter(path__descendant_of="electronics").order_by("path")
        assert [n.name for n in q] == ["electronics", "laptops", "macbook", "phones"]

    @pytest.mark.asyncio
    async def test_match(self) -> None:
        electronics, laptops, macbook, phones = await _make_tree()
        q = await Category.filter(path__match="*.laptops.*").order_by("path")
        assert sorted(n.name for n in q) == ["laptops", "macbook"]

    @pytest.mark.asyncio
    async def test_exact_filter(self) -> None:
        electronics, laptops, macbook, phones = await _make_tree()
        node = await Category.get(path="electronics")
        assert node.pk == electronics.pk

    @pytest.mark.asyncio
    async def test_in_filter(self) -> None:
        electronics, laptops, macbook, phones = await _make_tree()
        q = await Category.filter(path__in=["electronics", "electronics.phones"]).order_by("path")
        assert [n.name for n in q] == ["electronics", "phones"]

    @pytest.mark.asyncio
    async def test_not_filter(self) -> None:
        electronics, laptops, macbook, phones = await _make_tree()
        q = await Category.filter(path__not="electronics").order_by("path")
        assert sorted(n.name for n in q) == ["laptops", "macbook", "phones"]

    @pytest.mark.asyncio
    async def test_isnull_filter(self) -> None:
        electronics, laptops, macbook, phones = await _make_tree()
        assert await Category.filter(path__isnull=True).count() == 0
        assert await Category.filter(path__not_isnull=True).count() == 4


# ---------------------------------------------------------------------------
# HierarchyModel methods — the C4 failure surface
# ---------------------------------------------------------------------------


class TestHierarchyModelQueries:
    """HierarchyModel ancestor/descendant traversal against real PostgreSQL."""

    @pytest.mark.asyncio
    async def test_get_ancestors(self) -> None:
        electronics, laptops, macbook, phones = await _make_tree()
        ancestors = await macbook.get_ancestors()
        assert [n.name for n in ancestors] == ["electronics", "laptops"]

    @pytest.mark.asyncio
    async def test_get_ancestors_include_self(self) -> None:
        electronics, laptops, macbook, phones = await _make_tree()
        ancestors = await macbook.get_ancestors(include_self=True)
        assert [n.name for n in ancestors] == ["electronics", "laptops", "macbook"]

    @pytest.mark.asyncio
    async def test_get_descendants(self) -> None:
        electronics, laptops, macbook, phones = await _make_tree()
        descendants = await electronics.get_descendants()
        assert sorted(n.name for n in descendants) == ["laptops", "macbook", "phones"]

    @pytest.mark.asyncio
    async def test_get_descendants_include_self(self) -> None:
        electronics, laptops, macbook, phones = await _make_tree()
        descendants = await electronics.get_descendants(include_self=True)
        assert [n.name for n in descendants] == ["electronics", "laptops", "macbook", "phones"]

    @pytest.mark.asyncio
    async def test_get_children(self) -> None:
        electronics, laptops, macbook, phones = await _make_tree()
        children = await electronics.get_children()
        assert [n.name for n in children] == ["laptops", "phones"]

    @pytest.mark.asyncio
    async def test_get_siblings(self) -> None:
        electronics, laptops, macbook, phones = await _make_tree()
        assert list(await macbook.get_siblings()) == []
        siblings = await laptops.get_siblings()
        assert [n.name for n in siblings] == ["phones"]

    @pytest.mark.asyncio
    async def test_get_root(self) -> None:
        electronics, laptops, macbook, phones = await _make_tree()
        root = await macbook.get_root()
        assert root is not None
        assert root.name == "electronics"

    @pytest.mark.asyncio
    async def test_get_path_to_root(self) -> None:
        electronics, laptops, macbook, phones = await _make_tree()
        path = await macbook.get_path_to_root()
        assert [n.name for n in path] == ["electronics", "laptops", "macbook"]


class TestHierarchyMutations:
    """HierarchyModel subtree moves and validation against real PostgreSQL."""

    @pytest.mark.asyncio
    async def test_move_to(self) -> None:
        electronics, laptops, macbook, phones = await _make_tree()
        kitchen = await Category.create(
            path="kitchen", name="kitchen", parent_id=None, depth=0, namespace="shop"
        )
        await phones.move_to(kitchen)
        moved = await Category.get(pk=phones.pk)
        assert moved.path_str == "kitchen.phones"
        assert moved.depth == 1

    @pytest.mark.asyncio
    async def test_move_to_cascades_descendants(self) -> None:
        electronics, laptops, macbook, phones = await _make_tree()
        kitchen = await Category.create(
            path="kitchen", name="kitchen", parent_id=None, depth=0, namespace="shop"
        )
        await laptops.move_to(kitchen)
        moved_macbook = await Category.get(pk=macbook.pk)
        assert moved_macbook.path_str == "kitchen.laptops.macbook"
        assert moved_macbook.depth == 2

    @pytest.mark.asyncio
    async def test_move_into_own_descendant_raises(self) -> None:
        electronics, laptops, macbook, phones = await _make_tree()
        with pytest.raises(HierarchyError):
            await electronics.move_to(macbook)

    @pytest.mark.asyncio
    async def test_validate_hierarchy(self) -> None:
        electronics, laptops, macbook, phones = await _make_tree()
        assert await macbook.validate_hierarchy() == []

    @pytest.mark.asyncio
    async def test_validate_hierarchy_detects_bad_path(self) -> None:
        electronics, laptops, macbook, phones = await _make_tree()
        await Category.filter(pk=macbook.pk).update(path="electronics.laptops.notmacbook")
        bad = await Category.get(pk=macbook.pk)
        errors = await bad.validate_hierarchy()
        assert any("Path mismatch" in e for e in errors), errors

    @pytest.mark.asyncio
    async def test_namespace_isolation(self) -> None:
        electronics, laptops, macbook, phones = await _make_tree()
        await Category.create(
            path="electronics", name="electronics", parent_id=None, depth=0, namespace="other"
        )
        roots = await Category.filter(path="electronics", namespace="shop")
        assert [r.namespace for r in roots] == ["shop"]


class TestHierarchyEdgeBranches:
    """Coverage for the defensive/edge branches of HierarchyModel helpers."""

    @pytest.mark.asyncio
    async def test_get_ancestors_empty_path(self) -> None:
        detached = Category.construct(path=None, name="detached", depth=0)
        qs = detached.get_ancestors()
        assert list(await qs) == []

    @pytest.mark.asyncio
    async def test_get_descendants_empty_path(self) -> None:
        detached = Category.construct(path=None, name="detached", depth=0)
        assert list(await detached.get_descendants()) == []

    @pytest.mark.asyncio
    async def test_get_siblings_root(self) -> None:
        electronics, _laptops, _macbook, _phones = await _make_tree()
        assert list(await electronics.get_siblings()) == []

    @pytest.mark.asyncio
    async def test_get_root_empty_path(self) -> None:
        detached = Category.construct(path=None, name="detached", depth=0)
        assert await detached.get_root() is None

    @pytest.mark.asyncio
    async def test_get_root_single_label(self) -> None:
        electronics, _laptops, _macbook, _phones = await _make_tree()
        assert await electronics.get_root() is electronics

    @pytest.mark.asyncio
    async def test_get_path_to_root_empty_path(self) -> None:
        detached = Category.construct(path=None, name="detached", depth=0)
        assert await detached.get_path_to_root() == [detached]

    @pytest.mark.asyncio
    async def test_get_path_to_root_appends_detached_self(self) -> None:
        electronics, _laptops, _macbook, _phones = await _make_tree()
        detached = Category.construct(
            path="electronics.phones", name="phones", depth=1, namespace="shop"
        )
        path = await detached.get_path_to_root()
        names = [n.name for n in path]
        assert "electronics" in names
        assert detached in path  # unsaved instance appended

    @pytest.mark.asyncio
    async def test_move_to_missing_path_raises(self) -> None:
        electronics, _laptops, _macbook, _phones = await _make_tree()
        detached = Category.construct(path=None, name="detached", depth=0)
        with pytest.raises(HierarchyError, match="Both source and target must have paths"):
            await detached.move_to(electronics)

    @pytest.mark.asyncio
    async def test_validate_hierarchy_empty_path(self) -> None:
        detached = Category.construct(path=None, name="detached", depth=0)
        errors = await detached.validate_hierarchy()
        assert errors == [f"Node {detached.pk} has no path"]

    @pytest.mark.asyncio
    async def test_validate_hierarchy_depth_mismatch(self) -> None:
        _electronics, _laptops, _macbook, _phones = await _make_tree()
        wrong = await Category.create(
            path="electronics.laptops.macbook", name="macbook",
            parent_id=_laptops.pk, depth=5, namespace="shop",
        )
        errors = await wrong.validate_hierarchy()
        assert any("Depth mismatch" in e for e in errors)

    @pytest.mark.asyncio
    async def test_validate_hierarchy_missing_parent(self) -> None:
        _electronics, _laptops, _macbook, _phones = await _make_tree()
        orphan = await Category.create(
            path="electronics.ghost", name="ghost",
            parent_id=999999999, depth=1, namespace="shop",
        )
        errors = await orphan.validate_hierarchy()
        assert any("does not exist" in e for e in errors)

    @pytest.mark.asyncio
    async def test_validate_hierarchy_non_prefix_parent(self) -> None:
        _electronics, _laptops, _macbook, _phones = await _make_tree()
        bad = await Category.create(
            path="electronics.laptops.macbook", name="macbook",
            parent_id=_phones.pk, depth=2, namespace="shop",
        )
        errors = await bad.validate_hierarchy()
        assert any("is not a prefix" in e for e in errors)
