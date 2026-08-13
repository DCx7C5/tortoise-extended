"""Unit tests for GiSTIndex — initialization and type verification.

No database connection required.
"""

from types import SimpleNamespace

from tortoise_extended.indexes.ltree_index import (
    GiSTIndex,
    _format_index_fields,
    _get_index_name,
    _qualify_table_name,
)


class FakeSchemaGenerator:
    """Minimal schema-generator stand-in exposing the helper methods."""

    def _qualify_table_name(self, table_name: str, schema: str | None) -> str:
        if schema is None:
            return f'"{table_name}"'
        return f'"{schema}"."{table_name}"'

    def _get_index_name(
        self, prefix: str, model: FakeModel, field_names: list[str]
    ) -> str:
        return f"{prefix}_{type(model).__name__}_{'_'.join(field_names)}"

    def _format_index_fields(self, field_names: list[str]) -> str:
        return ", ".join(f'"{f}"' for f in field_names)


class FakeModel:
    """Minimal model stand-in with the _meta surface get_sql reads."""

    def __init__(self, db_table: str, schema: str | None = None) -> None:
        self._meta = SimpleNamespace(db_table=db_table, schema=schema)


class TestGiSTIndex:
    """Test GiSTIndex class."""

    def test_index_type(self) -> None:
        idx = GiSTIndex(fields=("path",))
        assert idx.INDEX_TYPE == "gist"

    def test_fields(self) -> None:
        idx = GiSTIndex(fields=("path", "name"))
        assert idx.field_names == ["path", "name"]

    def test_custom_name(self) -> None:
        idx = GiSTIndex(fields=("path",), name="my_gist_idx")
        assert idx.name == "my_gist_idx"

    def test_describe(self) -> None:
        idx = GiSTIndex(fields=("path",))
        desc = idx.describe()
        assert "fields" in desc
        assert desc["fields"] == ["path"]

    def test_deconstruct(self) -> None:
        idx = GiSTIndex(fields=("path",), name="test_idx")
        path, args, kwargs = idx.deconstruct()
        assert "GiSTIndex" in path
        assert kwargs["fields"] == ["path"]
        assert kwargs["name"] == "test_idx"


class TestGiSTIndexSql:
    """GiSTIndex.get_sql() and the schema-generator helper wrappers."""

    def test_qualify_table_name_no_schema(self) -> None:
        sg = FakeSchemaGenerator()
        assert _qualify_table_name(sg, "categories", None) == '"categories"'

    def test_qualify_table_name_with_schema(self) -> None:
        sg = FakeSchemaGenerator()
        assert _qualify_table_name(sg, "categories", "app") == '"app"."categories"'

    def test_get_index_name(self) -> None:
        sg = FakeSchemaGenerator()
        assert _get_index_name(sg, "gist", FakeModel("t"), ["path"]) == (
            "gist_FakeModel_path"
        )

    def test_format_index_fields(self) -> None:
        sg = FakeSchemaGenerator()
        assert _format_index_fields(sg, ["path", "name"]) == '"path", "name"'

    def test_get_sql_default_name_safe(self) -> None:
        idx = GiSTIndex(fields=("path",))
        sql = idx.get_sql(FakeSchemaGenerator(), FakeModel("categories"), safe=True)
        assert sql.startswith('CREATE INDEX IF NOT EXISTS "gist_FakeModel_path"')
        assert '"categories"' in sql
        assert 'USING gist ("path");' in sql

    def test_get_sql_default_name_unsafe(self) -> None:
        idx = GiSTIndex(fields=("path",))
        sql = idx.get_sql(FakeSchemaGenerator(), FakeModel("categories"), safe=False)
        assert sql.startswith('CREATE INDEX "gist_FakeModel_path"')
        assert "IF NOT EXISTS" not in sql

    def test_get_sql_custom_name_and_schema(self) -> None:
        idx = GiSTIndex(fields=("path",), name="cat_path_idx")
        sql = idx.get_sql(
            FakeSchemaGenerator(),
            FakeModel("categories", schema="app"),
            safe=True,
        )
        assert '"cat_path_idx"' in sql
        assert '"app"."categories"' in sql
