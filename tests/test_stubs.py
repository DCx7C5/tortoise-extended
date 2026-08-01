"""Regression guards for the local ``tortoise-stubs`` typing overlay.

The overlay lives in ``src/tortoise_extended/stubs/`` and is only exercised by
basedpyright via the ``stubPath`` setting in ``pyrightconfig.json`` — it is
never executed at runtime. These tests fail fast if the overlay or its wiring
is accidentally removed or broken.
"""

import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STUBS_DIR = PROJECT_ROOT / "src" / "tortoise_extended" / "stubs"
TORTOISE_STUBS_DIR = STUBS_DIR / "tortoise-stubs"
PYRIGHT_CONFIG = PROJECT_ROOT / "pyrightconfig.json"


class TestStubOverlayPresent:
    """The stub files exist and cover the overlaid tortoise modules."""

    @staticmethod
    def _stub_files() -> set[Path]:
        return {p.relative_to(STUBS_DIR) for p in STUBS_DIR.rglob("*.pyi")}

    def test_core_modules_have_stubs(self) -> None:
        relative = {str(p) for p in self._stub_files()}
        expected = {
            "tortoise-stubs/models/__init__.pyi",
            "tortoise-stubs/fields/__init__.pyi",
            "tortoise-stubs/fields/base.pyi",
            "tortoise-stubs/fields/relational.pyi",
            "tortoise-stubs/filters/__init__.pyi",
            "tortoise-stubs/indexes/__init__.pyi",
            "tortoise-stubs/validators.pyi",
            "tortoise-stubs/backends/asyncpg/client.pyi",
        }
        missing = expected - relative
        assert not missing, f"Missing stub files: {sorted(missing)}"

    def test_stub_modules_not_empty(self) -> None:
        for path in self._stub_files():
            content = (STUBS_DIR / path).read_text(encoding="utf-8")
            assert content.strip(), f"Stub file is empty: {path}"


class TestStubWiring:
    """pyrightconfig.json still points at the stub overlay."""

    def test_stub_path_configured(self) -> None:
        config = json.loads(PYRIGHT_CONFIG.read_text(encoding="utf-8"))
        stub_path = Path(config["stubPath"])
        assert stub_path == STUBS_DIR.relative_to(PROJECT_ROOT), config["stubPath"]
        assert (PROJECT_ROOT / stub_path).is_dir()

    def test_migrations_not_excluded(self) -> None:
        """The migrations exclude entry must never be re-added."""
        config = json.loads(PYRIGHT_CONFIG.read_text(encoding="utf-8"))
        excludes = config.get("exclude", [])
        assert not any(re.search(r"migrations", e) for e in excludes), excludes


class TestStubApiSurface:
    """Key symbols the package relies on are declared in the overlay."""

    def test_models_stub_declares_querysets(self) -> None:
        content = (TORTOISE_STUBS_DIR / "models" / "__init__.pyi").read_text(
            encoding="utf-8"
        )
        assert "QuerySet" in content
        assert "QuerySetSingle" in content
        assert "class Model" in content

    def test_asyncpg_client_stub_declares_client(self) -> None:
        content = (
            TORTOISE_STUBS_DIR / "backends" / "asyncpg" / "client.pyi"
        ).read_text(encoding="utf-8")
        assert "class AsyncpgDBClient" in content
        assert "create_pool" in content
