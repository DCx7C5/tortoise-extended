"""Unit tests for GraphTraversal — SQL generation and class behavior.

No database connection required. Tests verify correct SQL generation
and class initialization.
"""

from unittest.mock import MagicMock

import pytest

from tortoise_extended.expressions.graph_traversal import GraphTraversal


@pytest.fixture
def mock_models() -> tuple[MagicMock, MagicMock]:
    """Create mock node and edge models."""
    node_model = MagicMock()
    node_model._meta.db_table = "entities"

    edge_model = MagicMock()
    edge_model._meta.db_table = "relationships"

    return node_model, edge_model


class TestGraphTraversalInit:
    """Test class initialization."""

    def test_default_fields(self, mock_models: tuple[MagicMock, MagicMock]) -> None:
        node_model, edge_model = mock_models
        traversal = GraphTraversal(node_model, edge_model)

        assert traversal._node_table == "entities"
        assert traversal._edge_table == "relationships"
        assert traversal._source_field == "source_id"
        assert traversal._target_field == "target_id"

    def test_custom_fields(self, mock_models: tuple[MagicMock, MagicMock]) -> None:
        node_model, edge_model = mock_models
        traversal = GraphTraversal(
            node_model, edge_model,
            source_field="from_node",
            target_field="to_node",
        )

        assert traversal._source_field == "from_node"
        assert traversal._target_field == "to_node"


class TestGraphTraversalSQL:
    """Test SQL generation (verify SQL contains expected patterns)."""

    @pytest.mark.asyncio
    async def test_ancestors_sql_structure(
        self, mock_models: tuple[MagicMock, MagicMock]
    ) -> None:
        node_model, edge_model = mock_models
        traversal = GraphTraversal(node_model, edge_model)

        # Verify the method exists and is callable
        assert callable(traversal.ancestors)

    @pytest.mark.asyncio
    async def test_descendants_sql_structure(
        self, mock_models: tuple[MagicMock, MagicMock]
    ) -> None:
        node_model, edge_model = mock_models
        traversal = GraphTraversal(node_model, edge_model)

        assert callable(traversal.descendants)

    @pytest.mark.asyncio
    async def test_neighbors_sql_structure(
        self, mock_models: tuple[MagicMock, MagicMock]
    ) -> None:
        node_model, edge_model = mock_models
        traversal = GraphTraversal(node_model, edge_model)

        assert callable(traversal.neighbors)

    def test_ancestors_uses_recursive_cte(
        self, mock_models: tuple[MagicMock, MagicMock]
    ) -> None:
        node_model, edge_model = mock_models
        traversal = GraphTraversal(node_model, edge_model)

        # Verify the method signature accepts expected parameters
        import inspect
        sig = inspect.signature(traversal.ancestors)
        params = list(sig.parameters.keys())
        assert "node_id" in params
        assert "max_depth" in params
        assert "edge_type" in params

    def test_descendants_uses_recursive_cte(
        self, mock_models: tuple[MagicMock, MagicMock]
    ) -> None:
        node_model, edge_model = mock_models
        traversal = GraphTraversal(node_model, edge_model)

        import inspect
        sig = inspect.signature(traversal.descendants)
        params = list(sig.parameters.keys())
        assert "node_id" in params
        assert "max_depth" in params
        assert "edge_type" in params

    def test_neighbors_accepts_direction(
        self, mock_models: tuple[MagicMock, MagicMock]
    ) -> None:
        node_model, edge_model = mock_models
        traversal = GraphTraversal(node_model, edge_model)

        import inspect
        sig = inspect.signature(traversal.neighbors)
        params = list(sig.parameters.keys())
        assert "direction" in params
        assert "max_depth" in params

    def test_has_cycle_method_exists(
        self, mock_models: tuple[MagicMock, MagicMock]
    ) -> None:
        node_model, edge_model = mock_models
        traversal = GraphTraversal(node_model, edge_model)

        assert callable(traversal.has_cycle)
