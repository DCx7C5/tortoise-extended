"""Regression guards for the local ``tortoise-stubs`` typing overlay.

The overlay lives in ``src/tortoise_extended/stubs/`` and is exercised by
basedpyright via the ``stubPath`` setting in ``pyrightconfig.json``. ``.pyi``
files are never executed, so pytest line coverage cannot touch them — the
meaningful coverage measure is **declaration coverage**: every tortoise symbol
that ``tortoise_extended`` imports or monkey-patches must be declared by the
overlay. These tests compute that surface from the source with ``ast`` and
fail fast if a declaration goes missing or the wiring is removed.
"""

import ast
import json
import re
import sys
import types
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src" / "tortoise_extended"
STUBS_DIR = PROJECT_ROOT / "src" / "tortoise_extended" / "stubs"
TORTOISE_STUBS_DIR = STUBS_DIR / "tortoise-stubs"
PYRIGHT_CONFIG = PROJECT_ROOT / "pyrightconfig.json"

# tortoise module path -> overlay stub file (relative to TORTOISE_STUBS_DIR).
# Only these modules are replaced by the overlay; every other tortoise module
# falls back to runtime analysis / the installed tortoise-orm-stubs.
MODULE_TO_STUB = {
    "tortoise": "__init__.pyi",
    "tortoise.signals": "signals.pyi",
    "tortoise.fields": "fields/__init__.pyi",
    "tortoise.fields.base": "fields/base.pyi",
    "tortoise.fields.relational": "fields/relational.pyi",
    "tortoise.fields.boolean": "fields/boolean.pyi",
    "tortoise.filters": "filters/__init__.pyi",
    "tortoise.indexes": "indexes/__init__.pyi",
    "tortoise.models": "models/__init__.pyi",
    "tortoise.validators": "validators.pyi",
    "tortoise.backends.asyncpg.client": "backends/asyncpg/client.pyi",
    "tortoise.backends.base.client": "backends/base/client.pyi",
}

# ``from tortoise import ...`` names that are submodule references (the symbol
# is the module itself, which the overlay covers by declaring the module file).
SUBMODULE_NAMES = {"fields", "models", "filters", "indexes", "validators"}

# Modules the overlay declares that have no runtime counterpart (they exist for
# typing only, so executing stubs against the installed tortoise package must
# resolve them through a placeholder instead of the real package).
OVERLAY_ONLY_MODULES = {
    "tortoise.fields.boolean": ("BooleanField",),
}


def _seed_overlay_only_modules() -> None:
    """Register placeholder modules so stub-to-stub imports of typing-only
    modules (e.g. ``tortoise.fields.boolean``) resolve during ``exec``."""
    for module_name, attrs in OVERLAY_ONLY_MODULES.items():
        if module_name in sys.modules:
            continue
        placeholder = types.ModuleType(module_name)
        for attr in attrs:
            setattr(placeholder, attr, object)
        sys.modules[module_name] = placeholder


def _stub_names(stub_path: Path) -> set[str]:
    """Names declared by a stub file (classes, functions, attributes, imports).

    Class bodies are included — methods and class attributes are declared
    names too (e.g. ``AsyncpgDBClient.create_pool``).
    """
    tree = ast.parse(stub_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.TypeAlias) and isinstance(node.name, ast.Name):
            names.add(node.name.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _usage_surface() -> dict[str, set[str]]:
    """tortoise module -> symbols imported by ``tortoise_extended`` source."""
    usage: dict[str, set[str]] = {}
    for py_file in SRC_DIR.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
                if module != "tortoise" and not module.startswith("tortoise."):
                    continue
                symbols = usage.setdefault(module, set())
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    if alias.name in SUBMODULE_NAMES:
                        continue  # submodule reference — covered by module file
                    symbols.add(alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("tortoise."):
                        module = alias.name
                        usage.setdefault(module, set())  # module itself
    return usage


class TestStubSymbolCoverage:
    """Every tortoise symbol imported by the package is declared in the overlay."""

    def test_imported_symbols_are_declared(self) -> None:
        usage = _usage_surface()
        failures: list[str] = []
        for module, symbols in sorted(usage.items()):
            stub_rel = MODULE_TO_STUB.get(module)
            if stub_rel is None:
                continue  # runtime-covered module — not the overlay's job
            stub_path = TORTOISE_STUBS_DIR / stub_rel
            assert stub_path.is_file(), (
                f"tortoise_extended imports from {module} but no overlay stub "
                f"exists at tortoise-stubs/{stub_rel}"
            )
            declared = _stub_names(stub_path)
            for symbol in sorted(symbols):
                if symbol not in declared:
                    failures.append(f"{module}.{symbol} (stub: {stub_rel})")
        assert not failures, (
            "Stub overlay missing declarations used by the package:\n  "
            + "\n  ".join(failures)
        )

    def test_every_overlay_module_is_used(self) -> None:
        """Every stub module must back at least one import or patch target."""
        usage = _usage_surface()
        used = {module for module in usage if module in MODULE_TO_STUB}
        # backends/asyncpg/client is imported as a whole module (patch target)
        used.add("tortoise.backends.asyncpg.client")
        # signals is a user-facing module stub (model signal decorators) with
        # no direct ``tortoise_extended`` import — the overlay types it for
        # user models even though no package code pulls it in.
        used.add("tortoise.signals")
        # stub-to-stub imports (e.g. fields/__init__.pyi -> relational, validators)
        for stub_rel in MODULE_TO_STUB.values():
            stub_path = TORTOISE_STUBS_DIR / stub_rel
            if not stub_path.is_file():
                continue
            tree = ast.parse(stub_path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.ImportFrom) and node.module in MODULE_TO_STUB:
                    used.add(node.module)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in MODULE_TO_STUB:
                            used.add(alias.name)
        unused = set(MODULE_TO_STUB) - used
        assert not unused, f"Overlay modules with no backing usage: {sorted(unused)}"


class TestStubPatchSurface:
    """The ``_apply_patches()`` monkey-patch targets are declared in the overlay."""

    def test_fields_vectorfield_patch_target(self) -> None:
        declared = _stub_names(TORTOISE_STUBS_DIR / "fields" / "__init__.pyi")
        assert "VectorField" in declared, (
            "_apply_patches() assigns fields.VectorField at import time"
        )

    def test_indexes_registered_types(self) -> None:
        declared = _stub_names(TORTOISE_STUBS_DIR / "indexes" / "__init__.pyi")
        for name in ("Index", "HNSWIndex", "IVFFlatIndex", "GiSTIndex"):
            assert name in declared, (
                f"_apply_patches() registers {name} on tortoise.indexes"
            )

    def test_filters_patch_flag_and_function(self) -> None:
        declared = _stub_names(TORTOISE_STUBS_DIR / "filters" / "__init__.pyi")
        for name in (
            "get_filters_for_field",
            "_tortoise_extended_patched",
            "FilterInfoDict",
        ):
            assert name in declared

    def test_models_get_filters_for_field_reexport(self) -> None:
        declared = _stub_names(TORTOISE_STUBS_DIR / "models" / "__init__.pyi")
        assert "get_filters_for_field" in declared, (
            "_apply_patches() replaces the local reference in tortoise.models"
        )

    def test_asyncpg_codec_patch_targets(self) -> None:
        declared = _stub_names(
            TORTOISE_STUBS_DIR / "backends" / "asyncpg" / "client.pyi"
        )
        for name in ("AsyncpgDBClient", "create_pool", "_tortoise_extended_codec_patched"):
            assert name in declared


class TestStubTortoiseRoot:
    """The root ``tortoise`` stub declares the package-level surface used by
    ``tortoise_extended``: the ``Tortoise`` class with ``classproperty`` state,
    connection accessors, and the ``fields``/``models`` submodule re-exports."""

    def test_tortoise_class_surface(self) -> None:
        declared = _stub_names(TORTOISE_STUBS_DIR / "__init__.pyi")
        for name in (
            "Tortoise",
            "apps",
            "_inited",
            "init",
            "get_connection",
            "close_connections",
            "generate_schemas",
            "is_inited",
            "init_models",
            "init_app",
            "describe_model",
            "describe_models",
            "_drop_database",
        ):
            assert name in declared, f"root tortoise stub missing {name}"

    def test_reexports_declared(self) -> None:
        declared = _stub_names(TORTOISE_STUBS_DIR / "__init__.pyi")
        for name in (
            "connections",
            "get_connections",
            "Apps",
            "Model",
            "ModelMeta",
            "fields",
            "models",
        ):
            assert name in declared, f"root tortoise stub missing re-export {name}"


class TestStubSignals:
    """The ``tortoise.signals`` stub declares the enum and handler decorators."""

    def test_signals_module_declares_decorators(self) -> None:
        declared = _stub_names(TORTOISE_STUBS_DIR / "signals.pyi")
        for name in ("Signals", "post_save", "pre_save", "pre_delete", "post_delete"):
            assert name in declared, f"signals stub missing {name}"


class TestStubBooleanField:
    """The ``tortoise.fields.boolean`` stub fully types ``BooleanField``."""

    def test_boolean_field_is_concrete_class(self) -> None:
        source = (TORTOISE_STUBS_DIR / "fields" / "boolean.pyi").read_text(
            encoding="utf-8"
        )
        assert re.search(r"class BooleanField\(Field\[", source), (
            "BooleanField must be a class generic over Field, not a function "
            "overload chain"
        )
        assert "field_type: ClassVar[type] = bool" in source

    def test_boolean_field_null_literal_overloads(self) -> None:
        source = (TORTOISE_STUBS_DIR / "fields" / "boolean.pyi").read_text(
            encoding="utf-8"
        )
        assert "BooleanField[bool]" in source
        assert "BooleanField[bool | None]" in source
        assert re.search(r"null: Literal\[False\]", source)
        assert re.search(r"null: Literal\[True\]", source)


class TestStubExecutable:
    """Every overlay stub is valid, executable Python.

    ``.pyi`` files are normally never executed, so pytest line coverage cannot
    touch them. Executing each stub via ``compile``/``exec`` with its real
    path as the filename makes coverage.py trace the stub lines — the stubs
    then show up as covered in the report, and the run doubles as a
    syntax/name-resolution check (stub bodies are ``...``/assignments, so
    execution is safe; annotations are deferred under PEP 649).
    """

    def test_all_overlay_stubs_execute(self) -> None:
        stub_files = sorted(STUBS_DIR.rglob("*.pyi"))
        assert stub_files, "no stub files found under stubs/"
        _seed_overlay_only_modules()
        failures: list[str] = []
        for stub in stub_files:
            source = stub.read_text(encoding="utf-8")
            try:
                code = compile(source, str(stub), "exec")
                namespace = {"__name__": f"tortoise_extended_stubs.{stub.stem}"}
                exec(code, namespace)
            except Exception as exc:  # pragma: no cover
                failures.append(
                    f"{stub.relative_to(PROJECT_ROOT)}: "
                    f"{type(exc).__name__}: {exc}"
                )
        assert not failures, (
            "stub files must compile and execute cleanly:\n  "
            + "\n  ".join(failures)
        )


class TestStubOverlayPresent:
    """The stub files exist and cover the overlaid tortoise modules."""

    def test_core_modules_have_stubs(self) -> None:
        for module, stub_rel in MODULE_TO_STUB.items():
            stub_path = TORTOISE_STUBS_DIR / stub_rel
            assert stub_path.is_file(), f"{module} overlay missing: {stub_rel}"
            assert stub_path.read_text(encoding="utf-8").strip()


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
