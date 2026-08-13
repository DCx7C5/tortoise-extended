"""Comprehensive tests for RecursiveCTE.

Covers initialization, chaining, SQL output, as_table, and error handling.
No database connection required.
"""

import pytest
from pypika_tortoise import Field, Table
from pypika_tortoise.context import DEFAULT_SQL_CONTEXT
from pypika_tortoise.queries import QueryBuilder

from tortoise_extended.exceptions import RecursiveCTEError
from tortoise_extended.expressions.recursive_cte import RecursiveCTE


class TestRecursiveCTE:
    """Test RecursiveCTE construction and SQL generation."""

    def test_init(self) -> None:
        """CTE name should be stored."""
        cte = RecursiveCTE("test_cte")
        assert cte.name == "test_cte"

    def test_anchor_returns_self(self) -> None:
        """anchor() should return self for chaining."""
        cte = RecursiveCTE("test_cte")
        result = cte.anchor(None)  # type: ignore[arg-type]
        assert result is cte

    def test_union_returns_self(self) -> None:
        """union() should return self for chaining."""
        cte = RecursiveCTE("test_cte")
        result = cte.union(None)  # type: ignore[arg-type]
        assert result is cte

    def test_build_requires_anchor(self) -> None:
        """build() should raise ValueError without anchor."""
        cte = RecursiveCTE("test_cte")
        with pytest.raises(RecursiveCTEError, match="Anchor query not set"):
            cte.build()

    def test_build_requires_union(self) -> None:
        """build() should raise ValueError without union."""
        t = Table("test")
        cte = RecursiveCTE("test_cte")
        cte.anchor(QueryBuilder().from_(t).select("*"))
        with pytest.raises(RecursiveCTEError, match="Union query not set"):
            cte.build()

    def test_build_returns_query_builder(self) -> None:
        """build() should return a QueryBuilder."""
        t = Table("nodes")
        f_id = Field("id")
        cte = (
            RecursiveCTE("ancestors")
            .anchor(QueryBuilder().from_(t).select(f_id).where(f_id == 42))
            .union(QueryBuilder().from_(Table("ancestors")).select(f_id))
            .build()
        )
        assert isinstance(cte, QueryBuilder)

    def test_build_produces_recursive_sql(self) -> None:
        """SQL output should contain WITH RECURSIVE."""
        t = Table("nodes")
        f_id = Field("id")
        f_depth = Field("depth")
        sql = (
            RecursiveCTE("ancestors")
            .anchor(
                QueryBuilder()
                .from_(t)
                .select(f_id, Field("0").as_("depth"))
                .where(f_id == 42)
            )
            .union(
                QueryBuilder()
                .from_(Table("ancestors"))
                .select(f_id, (f_depth + 1).as_("depth"))
            )
            .build()
            .get_sql()
        )
        assert "RECURSIVE" in sql
        assert "ancestors" in sql
        assert "UNION ALL" in sql

    def test_as_table(self) -> None:
        """as_table() should return a Table."""
        cte = RecursiveCTE("test_cte")
        table = cte.as_table()
        assert table is not None

    def test_as_table_name(self) -> None:
        """as_table() should return Table with correct name."""
        cte = RecursiveCTE("my_recursive_cte")
        table = cte.as_table()
        sql = table.get_sql(DEFAULT_SQL_CONTEXT)
        assert "my_recursive_cte" in sql

    def test_chaining_pattern(self) -> None:
        """Full chaining anchor → union → build should produce valid SQL."""
        t = Table("nodes")
        f_id = Field("id")
        cte = (
            RecursiveCTE("tree")
            .anchor(QueryBuilder().from_(t).select(f_id).where(f_id == 1))
            .union(QueryBuilder().from_(Table("tree")).select(f_id))
            .build()
        )
        sql = cte.get_sql()
        assert "WITH" in sql
        assert "RECURSIVE" in sql
        assert "UNION ALL" in sql
        assert "SELECT" in sql

    def test_sql_selects_from_cte(self) -> None:
        """Built query should SELECT from the CTE name."""
        t = Table("nodes")
        f_id = Field("id")
        cte = (
            RecursiveCTE("ancestors")
            .anchor(QueryBuilder().from_(t).select(f_id).where(f_id == 42))
            .union(QueryBuilder().from_(Table("ancestors")).select(f_id))
            .build()
        )
        sql = cte.get_sql()
        assert 'FROM "ancestors"' in sql
