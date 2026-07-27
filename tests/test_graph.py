"""Unit tests for GraphNode, GraphEdge, and HierarchyModel.

Tests model definitions, helper methods, classmethod queries, properties,
str/repr, and abstract status. No database connection required.
"""

from tortoise_extended.graph.edge import GraphEdge
from tortoise_extended.graph.hierarchy_model import HierarchyModel
from tortoise_extended.graph.node import GraphNode


class TestGraphNode:
    """Tests for GraphNode base class."""

    def test_is_abstract(self) -> None:
        """GraphNode should be abstract."""
        assert getattr(GraphNode.Meta, "abstract", False) is True

    def test_str_representation(self) -> None:
        """GraphNode __str__ should show name and id."""
        node = GraphNode.construct(id="test-id", name="Test Node")
        result = str(node)
        assert "Test Node" in result
        assert "test-id" in result

    def test_repr_representation(self) -> None:
        """GraphNode __repr__ should show class name, id, and name."""
        node = GraphNode.construct(id="test-id", name="Test Node")
        result = repr(node)
        assert "GraphNode" in result
        assert "name='Test Node'" in result

    def test_is_leaf_property(self) -> None:
        """is_leaf should return True when child_count is 0."""
        node = GraphNode.construct(id="test-id", name="Test", child_count=0)
        assert node.is_leaf is True

    def test_is_not_leaf(self) -> None:
        """is_leaf should return False when child_count > 0."""
        node = GraphNode.construct(id="test-id", name="Test", child_count=5)
        assert node.is_leaf is False


class TestGraphEdge:
    """Tests for GraphEdge base class."""

    def test_is_abstract(self) -> None:
        """GraphEdge should be abstract."""
        assert getattr(GraphEdge.Meta, "abstract", False) is True

    def test_str_representation(self) -> None:
        """GraphEdge __str__ should show source, target, and type."""
        edge = GraphEdge.construct(
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
        """GraphEdge __repr__ should show class name and key fields."""
        edge = GraphEdge.construct(
            id="test-id",
            source_id="source-1",
            target_id="target-1",
            edge_type="parent_of",
        )
        result = repr(edge)
        assert "GraphEdge" in result
        assert "type='parent_of'" in result

    def test_is_self_loop(self) -> None:
        """is_self_loop should be True when source equals target."""
        edge = GraphEdge.construct(
            id="test-id",
            source_id="same-id",
            target_id="same-id",
            edge_type="self_ref",
        )
        assert edge.is_self_loop is True

    def test_is_not_self_loop(self) -> None:
        """is_self_loop should be False when source differs from target."""
        edge = GraphEdge.construct(
            id="test-id",
            source_id="source-1",
            target_id="target-1",
            edge_type="parent_of",
        )
        assert edge.is_self_loop is False


class TestHierarchyModel:
    """Tests for HierarchyModel abstract base."""

    def test_is_abstract(self) -> None:
        """HierarchyModel should be abstract."""
        assert getattr(HierarchyModel.Meta, "abstract", False) is True

    def test_not_is_abstract(self) -> None:
        """Verify abstract=True is set on Meta."""
        assert HierarchyModel.Meta.abstract is True

    def test_has_all_fields(self) -> None:
        """HierarchyModel should declare all hierarchy fields in _meta."""
        expected_fields = ["id", "path", "name", "parent_id", "depth", "namespace", "created_at", "updated_at"]
        for field_name in expected_fields:
            assert field_name in HierarchyModel._meta.fields_map, f"Missing field: {field_name}"

    def test_has_all_methods(self) -> None:
        """HierarchyModel should expose all tree operation methods."""
        assert hasattr(HierarchyModel, "get_ancestors")
        assert hasattr(HierarchyModel, "get_descendants")
        assert hasattr(HierarchyModel, "get_children")
        assert hasattr(HierarchyModel, "get_siblings")
        assert hasattr(HierarchyModel, "get_root")
        assert hasattr(HierarchyModel, "get_path_to_root")
        assert hasattr(HierarchyModel, "move_to")
        assert hasattr(HierarchyModel, "validate_hierarchy")


class TestHierarchyModelProperties:
    """Tests for HierarchyModel computed properties."""

    def test_is_root_true(self) -> None:
        """is_root should be True when depth=0 and parent_id=None."""
        node = HierarchyModel.construct(
            id=1, path=["root"], name="Root", depth=0, parent_id=None
        )
        assert node.is_root is True

    def test_is_root_false_depth_not_zero(self) -> None:
        """is_root should be False when depth > 0."""
        node = HierarchyModel.construct(
            id=2, path=["root", "child"], name="Child", depth=1, parent_id=1
        )
        assert node.is_root is False

    def test_is_root_false_has_parent(self) -> None:
        """is_root should be False when parent_id is not None."""
        node = HierarchyModel.construct(
            id=3, path=["root"], name="Node", depth=0, parent_id=1
        )
        assert node.is_root is False

    def test_path_str_with_list(self) -> None:
        """path_str should join list path with dots."""
        node = HierarchyModel.construct(
            id=1, path=["root", "child", "leaf"], name="Leaf", depth=2
        )
        assert node.path_str == "root.child.leaf"

    def test_path_str_with_string(self) -> None:
        """path_str should pass through string path."""
        node = HierarchyModel.construct(
            id=1, path="root.child", name="Child", depth=1
        )
        assert node.path_str == "root.child"

    def test_path_str_with_none(self) -> None:
        """path_str should return empty string for None path."""
        node = HierarchyModel.construct(
            id=1, path=None, name="Orphan", depth=0
        )
        assert node.path_str == ""


class TestHierarchyModelDunder:
    """Tests for HierarchyModel __str__ and __repr__."""

    def test_str_representation(self) -> None:
        """__str__ should show 'name (path)' format."""
        node = HierarchyModel.construct(
            id=1, path=["root", "child"], name="Child", depth=1
        )
        result = str(node)
        assert result == "Child (root.child)"

    def test_str_with_string_path(self) -> None:
        """__str__ should work with string path."""
        node = HierarchyModel.construct(
            id=1, path="root.child", name="Child", depth=1
        )
        result = str(node)
        assert result == "Child (root.child)"

    def test_str_with_none_path(self) -> None:
        """__str__ should show empty path for None."""
        node = HierarchyModel.construct(
            id=1, path=None, name="Orphan", depth=0
        )
        result = str(node)
        assert result == "Orphan ()"

    def test_repr_representation(self) -> None:
        """__repr__ should show class name, id, and path."""
        node = HierarchyModel.construct(
            id=42, path=["root", "child"], name="Child", depth=1
        )
        result = repr(node)
        assert "HierarchyModel" in result
        assert "id=42" in result
        assert "root.child" in result

    def test_repr_with_none_path(self) -> None:
        """__repr__ should show empty string for None path."""
        node = HierarchyModel.construct(
            id=1, path=None, name="Orphan", depth=0
        )
        result = repr(node)
        assert "HierarchyModel" in result
        assert "id=1" in result
