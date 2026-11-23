"""Column-based soft-delete detection shared by the raw-SQL builders.

The recursive-CTE modules (``graph_traversal``, ``pathfinding``,
``graph_vector_search``) and ``distinct_on`` all need to know whether a model
carries a soft-delete marker, so they can append ``<column> IS NULL`` and keep
tombstoned rows out of their results — the visibility rule
``SoftDeleteQuerySet`` applies to ordinary queries.

Why the column decides: the column is what a ``WHERE`` clause can see, and a
model projects one in either of two shapes.

* the ordinary shape declares a field named ``deleted_at``
  (``BaseSoftDeleteModel``); that field's ``source_field`` decides the column.
* the renamed shape projects another field onto the same column —
  ``removed_at = fields.DatetimeField(source_field="deleted_at")`` — so the
  table has a ``deleted_at`` column although no field carries that name.

A field-name probe gets both shapes wrong: it misses the renamed field (no
filter at all, so soft-deleted rows leak into a traversal) and, for a
``deleted_at`` field mapped elsewhere, it emits a column the table does not
have. Resolving the column handles every shape with one rule.

Callers keep ownership of the SQL spelling: ``distinct_on`` quotes the
identifier, the CTE builders interpolate it bare, so this helper returns only
the column name.
"""

from tortoise.models import Model


def soft_delete_column(model: type[Model]) -> str | None:
    """Return the column that marks a row soft-deleted, when the model has one.

    The filter is decided by the **column**, not by a field name: a model
    declares the ``deleted_at`` column either through a field of that name
    (the ``BaseSoftDeleteModel`` shape — its ``source_field`` decides the
    column) or through a field named something else projected onto it
    (``removed_at = fields.DatetimeField(source_field="deleted_at")``). Both
    shapes yield the column; a field named ``deleted_at`` wins if a model ever
    declares both.

    :param model: The model the statement selects from.
    :returns: The ``deleted_at`` column name, or ``None`` when the model
        projects no such column.
    """
    projection = model._meta.fields_db_projection
    if "deleted_at" in projection:
        return projection["deleted_at"]
    if "deleted_at" in projection.values():
        return "deleted_at"
    return None
