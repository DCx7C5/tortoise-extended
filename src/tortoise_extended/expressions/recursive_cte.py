"""Recursive CTE support for tortoise-orm.

Works WITH pypika-tortoise's native CTE detection — no monkey-patching needed.
pypika-tortoise auto-detects recursion when a CTE's ``from_`` references itself.

Usage::

    from tortoise_extended.expressions.recursive_cte import RecursiveCTE
    from pypika_tortoise import Table
    from pypika_tortoise.terms import RawSQL

    table = Table("nodes")

    # Graph traversal: find all ancestors of node_id=42
    cte = (
        RecursiveCTE("ancestors")
        .anchor(
            # Base case: the starting node
            table.select(
                table.c.id.as_("id"),
                table.c.parent_id.as_("parent_id"),
                RawSQL("0").as_("depth"),
            ).where(table.c.id == 42)
        )
        .union(
            # Recursive step: follow parent edges
            # Must reference the CTE alias in FROM
            table.select(
                table.c.id.as_("id"),
                table.c.parent_id.as_("parent_id"),
                (RawSQL("ancestors.depth") + 1).as_("depth"),
            )
            .join(Table("ancestors"))
            .on(table.c.parent_id == Table("ancestors").c.id)
        )
        .build()
    )
    # cte is a QueryBuilder: WITH RECURSIVE ancestors AS (...) SELECT * FROM ancestors
"""

from pypika_tortoise import Table
from pypika_tortoise.queries import QueryBuilder


class RecursiveCTE:
    """Builds a recursive CTE by composing anchor + recursive step.

    pypika-tortoise auto-detects recursion when the CTE's from_ references
    its own alias, so we just need to wire the pieces together correctly.

    :param name: The CTE alias name.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._anchor_query: QueryBuilder | None = None
        self._union_query: QueryBuilder | None = None

    def anchor(self, query: QueryBuilder) -> RecursiveCTE:
        """Set the anchor (non-recursive base case) query."""
        self._anchor_query = query
        return self

    def union(self, query: QueryBuilder) -> RecursiveCTE:
        """Set the recursive step query (must reference the CTE name)."""
        self._union_query = query
        return self

    def build(self) -> QueryBuilder:
        """Build the final CTE query.

        Returns a ``QueryBuilder`` with:
        ``WITH RECURSIVE <name> AS (<anchor> UNION ALL <recursive>) SELECT * FROM <name>``

        The caller can chain ``.select()``, ``.where()`` etc. on the result.
        """
        if self._anchor_query is None:
            raise ValueError("Anchor query not set — call .anchor() first")
        if self._union_query is None:
            raise ValueError("Union query not set — call .union() first")

        # UNION ALL the anchor and recursive step
        combined = self._anchor_query.union_all(self._union_query)

        # Register as a CTE on a new QueryBuilder.
        # pypika-tortoise detects recursion when the CTE's from_ references
        # its own alias — the _SetOperation inherits from_ from its members.
        return QueryBuilder().with_(combined, self.name).from_(self.name).select("*")

    def as_table(self) -> Table:
        """Return a Table reference that can be used in FROM clauses."""
        return Table(self.name)
