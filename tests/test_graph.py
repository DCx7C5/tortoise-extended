"""Unit tests for BaseGraphNodeModel, BaseGraphEdgeModel, and BaseHierarchyModel.

Tests model definitions, helper methods, classmethod queries, properties,
str/repr, and abstract status. No database connection required.
"""

import pytest

from tortoise_extended.models import BaseGraphEdgeModel, BaseGraphNodeModel
from tortoise_extended.models import BaseHierarchyModel


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
