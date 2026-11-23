"""Unit tests for BaseGraphNodeModel, BaseGraphEdgeModel, and BaseHierarchyModel.

Tests model definitions, helper methods, classmethod queries, properties,
str/repr, and abstract status — no database connection required.

The ``TestSaveAtomicity`` class at the end is the one exception: it exercises
the transactional write path of :class:`BaseNode.save()` against the live
PostgreSQL test database (see ``docker-compose.dev.yml``) to prove the
atomicity guarantee — a failure in the denormalized ``child_count`` maintenance
must roll back the node row write.  That class is skipped when PostgreSQL is
not reachable; the rest of the module keeps running without a database.
"""

import os
import socket
from collections.abc import AsyncGenerator

import pytest
from tortoise import Tortoise

import tortoise_extended  # noqa: F401 — apply patches
from tortoise_extended.models import BaseGraphEdgeModel, BaseGraphNodeModel, BaseNode
from tortoise_extended.models import BaseHierarchyModel


# --- Config — only TestSaveAtomicity needs PG -------------------------------

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


class TestGraphNode:
    """Tests for BaseGraphNodeModel base class."""

    def test_is_abstract(self) -> None:
        """BaseGraphNodeModel should be abstract."""
        assert getattr(BaseGraphNodeModel.Meta, "abstract", False) is True

    def test_str_representation(self) -> None:
        """BaseGraphNodeModel __str__ should show name and id."""
        node = BaseGraphNodeModel.construct(id="test-id", name="Test Node")
        result = str(node)
        assert "Test Node" in result
        assert "test-id" in result

    def test_repr_representation(self) -> None:
        """BaseGraphNodeModel __repr__ should show class name, id, and name."""
        node = BaseGraphNodeModel.construct(id="test-id", name="Test Node")
        result = repr(node)
        assert "BaseGraphNodeModel" in result
        assert "name='Test Node'" in result

    def test_is_leaf_property(self) -> None:
        """is_leaf should return True when child_count is 0."""
        node = BaseGraphNodeModel.construct(id="test-id", name="Test", child_count=0)
        assert node.is_leaf is True

    def test_is_not_leaf(self) -> None:
        """is_leaf should return False when child_count > 0."""
        node = BaseGraphNodeModel.construct(id="test-id", name="Test", child_count=5)
        assert node.is_leaf is False


class TestGraphEdge:
    """Tests for BaseGraphEdgeModel base class."""

    def test_is_abstract(self) -> None:
        """BaseGraphEdgeModel should be abstract."""
        assert getattr(BaseGraphEdgeModel.Meta, "abstract", False) is True

    def test_str_representation(self) -> None:
        """BaseGraphEdgeModel __str__ should show source, target, and type."""
        edge = BaseGraphEdgeModel.construct(
            id="test-id",
            source_id="source-1",
            target_id="target-1",
            edge_type="parent_of",
        )
        result = str(edge)
        assert "source-1" in result
        assert "target-1" in result
        assert "parent_of" in result

    def test_repr_representation(self) -> None:
        """BaseGraphEdgeModel __repr__ should show class name and key fields."""
        edge = BaseGraphEdgeModel.construct(
            id="test-id",
            source_id="source-1",
            target_id="target-1",
            edge_type="parent_of",
        )
        result = repr(edge)
        assert "BaseGraphEdgeModel" in result
        assert "type='parent_of'" in result

    def test_is_self_loop(self) -> None:
        """is_self_loop should be True when source equals target."""
        edge = BaseGraphEdgeModel.construct(
            id="test-id",
            source_id="same-id",
            target_id="same-id",
            edge_type="self_ref",
        )
        assert edge.is_self_loop is True

    def test_is_not_self_loop(self) -> None:
        """is_self_loop should be False when source differs from target."""
        edge = BaseGraphEdgeModel.construct(
            id="test-id",
            source_id="source-1",
            target_id="target-1",
            edge_type="parent_of",
        )
        assert edge.is_self_loop is False


class TestHierarchyModel:
    """Tests for BaseHierarchyModel abstract base."""

    def test_is_abstract(self) -> None:
        """BaseHierarchyModel should be abstract."""
        assert getattr(BaseHierarchyModel.Meta, "abstract", False) is True

    def test_not_is_abstract(self) -> None:
        """Verify abstract=True is set on Meta."""
        assert BaseHierarchyModel.Meta.abstract is True

    def test_has_all_fields(self) -> None:
        """BaseHierarchyModel should declare all hierarchy fields in _meta."""
        expected_fields = [
            "id",
            "path",
            "name",
            "parent_id",
            "depth",
            "namespace",
            "created_at",
            "updated_at",
        ]
        for field_name in expected_fields:
            assert field_name in BaseHierarchyModel._meta.fields_map, (
                f"Missing field: {field_name}"
            )

    def test_has_all_methods(self) -> None:
        """BaseHierarchyModel should expose all tree operation methods."""
        assert hasattr(BaseHierarchyModel, "get_ancestors")
        assert hasattr(BaseHierarchyModel, "get_descendants")
        assert hasattr(BaseHierarchyModel, "get_children")
        assert hasattr(BaseHierarchyModel, "get_siblings")
        assert hasattr(BaseHierarchyModel, "get_root")
        assert hasattr(BaseHierarchyModel, "get_path_to_root")
        assert hasattr(BaseHierarchyModel, "move_to")
        assert hasattr(BaseHierarchyModel, "validate_hierarchy")


class TestAbstractIndexGuard:
    """G3 regression — abstract Meta.indexes never propagate, so concrete
    subclasses must redeclare them. The __init_subclass__ guard enforces this
    at import time instead of silently dropping indexes."""

    def test_concrete_subclass_without_meta_raises(self) -> None:
        """A concrete subclass with no Meta at all must raise."""
        with pytest.raises(NotImplementedError, match="must declare a Meta class"):
            type("NoMetaCat", (BaseHierarchyModel,), {})

    def test_concrete_subclass_without_indexes_raises(self) -> None:
        """A concrete subclass whose Meta omits indexes must raise."""
        meta = type("Meta", (), {"table": "cats"})
        with pytest.raises(NotImplementedError, match="must redeclare indexes"):
            type("NoIndexCat", (BaseHierarchyModel,), {"Meta": meta})

    def test_abstract_subclass_allowed(self) -> None:
        """Abstract intermediate subclasses are exempt from the guard."""
        meta = type("Meta", (), {"abstract": True})
        cls = type("AbstractCat", (BaseHierarchyModel,), {"Meta": meta})
        assert getattr(cls.Meta, "abstract", False) is True

    def test_concrete_subclass_explicit_empty_indexes_allowed(self) -> None:
        """indexes = () is an explicit opt-out and must not raise."""
        meta = type("Meta", (), {"table": "cats", "indexes": ()})
        cls = type("NoIndexCat2", (BaseHierarchyModel,), {"Meta": meta})
        assert cls.Meta.indexes == ()

    def test_edge_subclass_without_indexes_raises(self) -> None:
        """BaseGraphEdgeModel enforces the same redeclaration guard."""
        meta = type("Meta", (), {"table": "rels"})
        with pytest.raises(NotImplementedError, match="must redeclare indexes"):
            type("NoIndexEdge", (BaseGraphEdgeModel,), {"Meta": meta})


class TestHierarchyModelProperties:
    """Tests for BaseHierarchyModel computed properties."""

    def test_is_root_true(self) -> None:
        """is_root should be True when depth=0 and parent_id=None."""
        node = BaseHierarchyModel.construct(
            id=1, path=["root"], name="Root", depth=0, parent_id=None
        )
        assert node.is_root is True

    def test_is_root_false_depth_not_zero(self) -> None:
        """is_root should be False when depth > 0."""
        node = BaseHierarchyModel.construct(
            id=2, path=["root", "child"], name="Child", depth=1, parent_id=1
        )
        assert node.is_root is False

    def test_is_root_false_has_parent(self) -> None:
        """is_root should be False when parent_id is not None."""
        node = BaseHierarchyModel.construct(
            id=3, path=["root"], name="Node", depth=0, parent_id=1
        )
        assert node.is_root is False

    def test_path_str_with_list(self) -> None:
        """path_str should join list path with dots."""
        node = BaseHierarchyModel.construct(
            id=1, path=["root", "child", "leaf"], name="Leaf", depth=2
        )
        assert node.path_str == "root.child.leaf"

    def test_path_str_with_string(self) -> None:
        """path_str should pass through string path."""
        node = BaseHierarchyModel.construct(
            id=1, path="root.child", name="Child", depth=1
        )
        assert node.path_str == "root.child"

    def test_path_str_with_none(self) -> None:
        """path_str should return empty string for None path."""
        node = BaseHierarchyModel.construct(id=1, path=None, name="Orphan", depth=0)
        assert node.path_str == ""


class TestHierarchyModelDunder:
    """Tests for BaseHierarchyModel __str__ and __repr__."""

    def test_str_representation(self) -> None:
        """__str__ should show 'name (path)' format."""
        node = BaseHierarchyModel.construct(
            id=1, path=["root", "child"], name="Child", depth=1
        )
        result = str(node)
        assert result == "Child (root.child)"

    def test_str_with_string_path(self) -> None:
        """__str__ should work with string path."""
        node = BaseHierarchyModel.construct(
            id=1, path="root.child", name="Child", depth=1
        )
        result = str(node)
        assert result == "Child (root.child)"

    def test_str_with_none_path(self) -> None:
        """__str__ should show empty path for None."""
        node = BaseHierarchyModel.construct(id=1, path=None, name="Orphan", depth=0)
        result = str(node)
        assert result == "Orphan ()"

    def test_repr_representation(self) -> None:
        """__repr__ should show class name, id, and path."""
        node = BaseHierarchyModel.construct(
            id=42, path=["root", "child"], name="Child", depth=1
        )
        result = repr(node)
        assert "BaseHierarchyModel" in result
        assert "id=42" in result
        assert "root.child" in result

    def test_repr_with_none_path(self) -> None:
        """__repr__ should show empty string for None path."""
        node = BaseHierarchyModel.construct(id=1, path=None, name="Orphan", depth=0)
        result = repr(node)
        assert "BaseHierarchyModel" in result
        assert "id=1" in result


class RollbackNode(BaseNode):
    """BaseNode subclass used by the PostgreSQL atomicity test.

    Concrete child of :class:`BaseNode` — the atomic write path under test
    lives on :class:`NodeMixin`, so the deprecated ``BaseGraphNodeModel``
    alias (UUID4 identity) is not involved.  ``Meta.indexes`` is redeclared
    because Tortoise does not propagate the abstract base's indexes (see the
    ``NodeMixin.__init_subclass__`` guard in ``node.py``).
    """

    class Meta:
        table = "rollback_nodes"
        indexes = (("namespace", "depth"),)


@pytest.fixture(scope="module")
async def _rollback_db() -> AsyncGenerator[None, None]:
    """Initialize Tortoise against the live PostgreSQL test database.

    Non-autouse and module-scoped: only the ``TestSaveAtomicity`` tests request
    it, so the remaining tests in this module continue to run without a
    database connection.
    """
    _ = await Tortoise.init(
        db_url=DB_URL,
        modules={"models": ["tests.test_graph"]},
    )
    conn = Tortoise.get_connection("default")
    await conn.execute_query("DROP TABLE IF EXISTS rollback_nodes CASCADE")
    await Tortoise.generate_schemas()
    yield
    await conn.execute_query("DROP TABLE IF EXISTS rollback_nodes CASCADE")
    await Tortoise.close_connections()


class _CounterFailure(Exception):
    """Sentinel raised in place of the child_count maintenance UPDATE."""


class TestSaveAtomicity:
    """Q4-TEST — a failing child_count write rolls back the node INSERT.

    ``BaseNode.save()`` runs the node write and the denormalized
    ``child_count`` maintenance inside a single ``in_transaction()`` block
    when ``using_db is None`` (see ``save()``). This proves the atomicity
    guarantee: if the parent's ``child_count`` UPDATE fails after the node row
    is written, the whole transaction rolls back and the node INSERT is undone
    — a partial failure can never leave ``child_count`` drifted.
    """

    pytestmark = pytest.mark.skipif(
        not _pg_available(),
        reason="PostgreSQL not available on localhost:5434",
    )

    @pytest.fixture(autouse=True)
    async def _clean_table(self) -> AsyncGenerator[None, None]:
        """Truncate the node table before each test."""
        conn = Tortoise.get_connection("default")
        await conn.execute_query("TRUNCATE rollback_nodes CASCADE")
        yield

    @pytest.mark.asyncio
    async def test_child_count_failure_rolls_back_node_insert(
        self, _rollback_db: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed counter UPDATE rolls back the child node INSERT."""
        parent = await RollbackNode.create(name="parent", namespace="ns", depth=0)
        assert (await RollbackNode.get(pk=parent.pk)).child_count == 0

        # Force the child_count maintenance path (the parent queryset's
        # ``.update(...)``) to fail. The node's own INSERT goes through the
        # executor (``Model.save``), not ``QuerySet.update``, so this only
        # breaks the counter maintenance — exactly the step we want to fail.
        def _fail_counter_update(_qs, **_kwargs):
            raise _CounterFailure("simulated child_count maintenance failure")

        monkeypatch.setattr("tortoise.queryset.QuerySet.update", _fail_counter_update)

        child = RollbackNode(name="child", namespace="ns", parent_id=parent.pk, depth=1)

        with pytest.raises(_CounterFailure, match="simulated child_count"):
            # Create path with ``parent_id`` and ``using_db=None`` — this runs
            # inside ``in_transaction()``, so the failure must roll back the
            # node INSERT that already succeeded internally.
            await child.save()

        # The node INSERT was rolled back — the child must not be persisted.
        assert await RollbackNode.filter(name="child").count() == 0
        # And the parent's denormalized counter is unchanged.
        assert (await RollbackNode.get(pk=parent.pk)).child_count == 0
