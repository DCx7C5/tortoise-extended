"""Regression test: every symbol in ``__all__`` must be importable.

Verifies that the top-level ``tortoise_extended.__all__`` list contains only
symbols that can be imported from ``tortoise_extended``, and that key
subpackage-level ``__all__`` entries are importable from their respective
package. Catches accidental missing re-exports and stale ``__all__`` entries.
"""

import importlib

import pytest

import tortoise_extended

_top_level_all: list[str] = getattr(tortoise_extended, "__all__", [])


class TestTopLevelAllImportable:
    """Every name in ``tortoise_extended.__all__`` must be importable."""

    def test_all_list_is_nonempty(self) -> None:
        """__all__ should not be empty."""
        assert len(_top_level_all) > 0

    @pytest.mark.parametrize("symbol_name", _top_level_all)
    def test_symbol_importable(self, symbol_name: str) -> None:
        """Each symbol in __all__ must be an attribute of the top-level module."""
        assert hasattr(tortoise_extended, symbol_name), (
            f"tortoise_extended.__all__ contains {symbol_name!r} "
            f"but it is not importable from the top-level module"
        )

    @pytest.mark.parametrize("symbol_name", _top_level_all)
    def test_symbol_not_none(self, symbol_name: str) -> None:
        """Each symbol in __all__ should be a real object, not None."""
        obj = getattr(tortoise_extended, symbol_name)
        assert obj is not None


class TestAllSorted:
    """__all__ should be alphabetically sorted for maintainability."""

    def test_sorted(self) -> None:
        """__all__ entries should be in lexicographic order."""
        assert _top_level_all == sorted(_top_level_all)


class TestSubpackageAllImportable:
    """Every symbol in a subpackage's __all__ must be importable from that subpackage."""

    @pytest.mark.parametrize(
        "pkg_path",
        [
            "tortoise_extended.expressions",
            "tortoise_extended.fields",
            "tortoise_extended.indexes",
            "tortoise_extended.models",
            "tortoise_extended.timescale",
        ],
    )
    def test_all_symbols_importable(self, pkg_path: str) -> None:
        """Each symbol in a subpackage's __all__ must be an attribute of that subpackage."""
        pkg = importlib.import_module(pkg_path)
        sub_all: list[str] = getattr(pkg, "__all__", [])
        for name in sub_all:
            assert hasattr(pkg, name), (
                f"{pkg_path}.__all__ contains {name!r} "
                f"but it is not importable from {pkg_path}"
            )


class TestModelsAllCoversReexports:
    """``tortoise_extended.models.__all__`` must cover all model re-exports."""

    def test_models_all_match_imports(self) -> None:
        """models.__all__ should contain all names imported in models/__init__.py."""
        models_pkg = importlib.import_module("tortoise_extended.models")
        models_all: list[str] = getattr(models_pkg, "__all__", [])
        expected = {
            "BaseCacheableModel",
            "BaseEventStreamModel",
            "BaseGraphEdgeModel",
            "BaseGraphNodeModel",
            "BaseHierarchyModel",
            "BaseModel",
            "BaseNode",
            "BaseSample",
            "BaseSoftDeleteModel",
            "BaseUserModel",
            "NodeMixin",
            "NodeTree",
            "SampleMixin",
            "SoftDeleteQuerySet",
            "TimestampEndMixin",
            "TimestampMixin",
            "UnifiedIdModel",
        }
        assert set(models_all) == expected


class TestFieldsAllSubsetOfTopLevel:
    """``tortoise_extended.fields.__all__`` entries must all appear in the top-level __all__."""

    def test_fields_all_subset(self) -> None:
        fields_pkg = importlib.import_module("tortoise_extended.fields")
        fields_all: list[str] = getattr(fields_pkg, "__all__", [])
        top_set = set(_top_level_all)
        missing = [s for s in fields_all if s not in top_set]
        assert not missing, (
            f"fields.__all__ contains {missing!r} "
            f"but they are NOT in tortoise_extended.__all__"
        )


class TestIndexesAllSubsetOfTopLevel:
    """``tortoise_extended.indexes.__all__`` entries must all appear in the top-level __all__."""

    def test_indexes_all_subset(self) -> None:
        indexes_pkg = importlib.import_module("tortoise_extended.indexes")
        indexes_all: list[str] = getattr(indexes_pkg, "__all__", [])
        top_set = set(_top_level_all)
        missing = [s for s in indexes_all if s not in top_set]
        assert not missing, (
            f"indexes.__all__ contains {missing!r} "
            f"but they are NOT in tortoise_extended.__all__"
        )
