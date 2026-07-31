"""Unit tests for pathfinding functions — SQL generation and signatures.

No database connection required. Tests verify correct function signatures
and module structure.
"""

import inspect

from tortoise_extended.expressions.pathfinding import (
    all_paths,
    find_cycles,
    shortest_path,
)


class TestShortestPath:
    """Test shortest_path function."""

    def test_exists(self) -> None:
        assert callable(shortest_path)

    def test_signature(self) -> None:
        sig = inspect.signature(shortest_path)
        params = list(sig.parameters.keys())
        assert "node_model" in params
        assert "edge_model" in params
        assert "from_id" in params
        assert "to_id" in params
        assert "max_hops" in params
        assert "edge_type" in params

    def test_default_max_hops(self) -> None:
        sig = inspect.signature(shortest_path)
        assert sig.parameters["max_hops"].default == 6

    def test_default_edge_type(self) -> None:
        sig = inspect.signature(shortest_path)
        assert sig.parameters["edge_type"].default is None


class TestAllPaths:
    """Test all_paths function."""

    def test_exists(self) -> None:
        assert callable(all_paths)

    def test_signature(self) -> None:
        sig = inspect.signature(all_paths)
        params = list(sig.parameters.keys())
        assert "node_model" in params
        assert "edge_model" in params
        assert "from_id" in params
        assert "to_id" in params
        assert "max_hops" in params
        assert "max_paths" in params
        assert "edge_type" in params

    def test_default_max_paths(self) -> None:
        sig = inspect.signature(all_paths)
        assert sig.parameters["max_paths"].default == 10


class TestFindCycles:
    """Test find_cycles function."""

    def test_exists(self) -> None:
        assert callable(find_cycles)

    def test_signature(self) -> None:
        sig = inspect.signature(find_cycles)
        params = list(sig.parameters.keys())
        assert "node_model" in params
        assert "edge_model" in params
        assert "max_depth" in params
        assert "edge_type" in params

    def test_default_max_depth(self) -> None:
        sig = inspect.signature(find_cycles)
        assert sig.parameters["max_depth"].default == 10
