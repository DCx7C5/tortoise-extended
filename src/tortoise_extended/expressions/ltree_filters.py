"""pgtree filter operators for tortoise-orm.

Custom Criterion subclasses for ltree operators:
- @> ancestor of
- <@ descendant of
- ~ ltree match
- ?@> ancestor match
- ?<@ descendant match
"""

from typing import TYPE_CHECKING

from pypika_tortoise.enums import Comparator
from pypika_tortoise.terms import BasicCriterion, Field, Term, ValueWrapper
from tortoise.fields import Field as TortoiseField
from tortoise.filters import FilterInfoDict
from tortoise.filters import is_null as _is_null
from tortoise.filters import not_null as _not_null
from tortoise_extended._types import RowValue

if TYPE_CHECKING:
    from tortoise.models import Model


# ltree operator comparators

class LTreeAncestorOfOp(Comparator):
    ancestor_of = " @> "


class LTreeDescendantOfOp(Comparator):
    descendant_of = " <@ "


class LTreeMatchOp(Comparator):
    match = " ~ "


class LTreeAncestorMatchOp(Comparator):
    ancestor_match = " ?@> "


class LTreeDescendantMatchOp(Comparator):
    descendant_match = " ?<@ "


# Criterion wrappers

class LTreeAncestorOf(BasicCriterion):
    """@> operator: is ancestor of (left is ancestor of right)."""

    def __init__(self, left: Term | str, right: Term, alias: str | None = None) -> None:
        if isinstance(left, str):
            left = Field(left)
        super().__init__(LTreeAncestorOfOp.ancestor_of, left, right, alias=alias)


class LTreeDescendantOf(BasicCriterion):
    """<@ operator: is descendant of (left is descendant of right)."""

    def __init__(self, left: Term | str, right: Term, alias: str | None = None) -> None:
        if isinstance(left, str):
            left = Field(left)
        super().__init__(LTreeDescendantOfOp.descendant_of, left, right, alias=alias)


class LTreeMatch(BasicCriterion):
    """~ operator: ltree match (left matches lquery pattern)."""

    def __init__(self, left: Term | str, right: Term, alias: str | None = None) -> None:
        if isinstance(left, str):
            left = Field(left)
        super().__init__(LTreeMatchOp.match, left, right, alias=alias)


class LTreeAncestorMatch(BasicCriterion):
    """?@> operator: has ancestor match (left has ancestor matching lquery)."""

    def __init__(self, left: Term | str, right: Term, alias: str | None = None) -> None:
        if isinstance(left, str):
            left = Field(left)
        super().__init__(LTreeAncestorMatchOp.ancestor_match, left, right, alias=alias)


class LTreeDescendantMatch(BasicCriterion):
    """?<@ operator: has descendant match (left has descendant matching lquery)."""

    def __init__(self, left: Term | str, right: Term, alias: str | None = None) -> None:
        if isinstance(left, str):
            left = Field(left)
        super().__init__(LTreeDescendantMatchOp.descendant_match, left, right, alias=alias)


# ltree-specific encoders

def ltree_encoder(
    value: RowValue | list[str | int | float] | tuple[str | int | float, ...],
    _instance: Model | None = None,
    _field: TortoiseField[RowValue] | None = None,
) -> str | None:
    """Encode a list/tuple of strings into ltree path.

    Args:
        value: List of path components or string
        _instance: Model instance (unused, Tortoise encoder signature)
        _field: Field instance (unused, Tortoise encoder signature)

    Returns:
        ltree string, or None if value is None
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return ".".join(str(v) for v in value)
    return str(value)


def _lquery_encoder(
    value: RowValue,
    _instance: Model | None = None,
    _field: TortoiseField[RowValue] | None = None,
) -> str | None:
    """Encode lquery pattern for ltree match operations.

    Args:
        value: lquery pattern string
        _instance: Model instance (unused, Tortoise encoder signature)
        _field: Field instance (unused, Tortoise encoder signature)

    Returns:
        lquery string, or None if value is None
    """
    if value is None:
        return None
    return str(value)


# Filter definitions for LTreeField

def get_ltree_filters(field_name: str, source_field: str) -> dict[str, FilterInfoDict]:
    """Return filter definitions for an LTreeField.

    Provides the standard filters that make sense for ltree paths
    (exact, not, in, not_in, isnull, not_isnull) plus the ltree
    operators:

    - ``__ancestor_of``: left @> right (is ancestor of)
    - ``__descendant_of``: left <@ right (is descendant of)
    - ``__match``: left ~ right (ltree match)
    - ``__ancestor_match``: left ?@> right (has ancestor match)
    - ``__descendant_match``: left ?<@ right (has descendant match)
    """
    from tortoise.filters import bool_encoder
    from tortoise.filters import list_encoder
    from tortoise.filters import not_equal, not_in, is_in
    from tortoise.filters import operator

    return {
        field_name: {
            "field": field_name,
            "source_field": source_field,
            "operator": operator.eq,
            "value_encoder": ltree_encoder,
        },
        f"{field_name}__not": {
            "field": field_name,
            "source_field": source_field,
            "operator": not_equal,
            "value_encoder": ltree_encoder,
        },
        f"{field_name}__in": {
            "field": field_name,
            "source_field": source_field,
            "operator": is_in,
            "value_encoder": list_encoder,
        },
        f"{field_name}__not_in": {
            "field": field_name,
            "source_field": source_field,
            "operator": not_in,
            "value_encoder": list_encoder,
        },
        f"{field_name}__isnull": {
            "field": field_name,
            "source_field": source_field,
            "operator": _is_null,
            "value_encoder": bool_encoder,
        },
        f"{field_name}__not_isnull": {
            "field": field_name,
            "source_field": source_field,
            "operator": _not_null,
            "value_encoder": bool_encoder,
        },
        f"{field_name}__ancestor_of": {
            "field": field_name,
            "source_field": source_field,
            "operator": _ancestor_of_filter,
            "value_encoder": ltree_encoder,
        },
        f"{field_name}__descendant_of": {
            "field": field_name,
            "source_field": source_field,
            "operator": _descendant_of_filter,
            "value_encoder": ltree_encoder,
        },
        f"{field_name}__match": {
            "field": field_name,
            "source_field": source_field,
            "operator": _match_filter,
            "value_encoder": _lquery_encoder,
        },
        f"{field_name}__ancestor_match": {
            "field": field_name,
            "source_field": source_field,
            "operator": _ancestor_match_filter,
            "value_encoder": _lquery_encoder,
        },
        f"{field_name}__descendant_match": {
            "field": field_name,
            "source_field": source_field,
            "operator": _descendant_match_filter,
            "value_encoder": _lquery_encoder,
        },
    }


# Filter operator functions

def _ancestor_of_filter(field: Term, value: RowValue) -> BasicCriterion:
    """Filter: field @> value (is ancestor of)."""
    return LTreeAncestorOf(field, ValueWrapper(value))


def _descendant_of_filter(field: Term, value: RowValue) -> BasicCriterion:
    """Filter: field <@ value (is descendant of)."""
    return LTreeDescendantOf(field, ValueWrapper(value))


def _match_filter(field: Term, value: RowValue) -> BasicCriterion:
    """Filter: field ~ value (ltree match)."""
    return LTreeMatch(field, ValueWrapper(value))


def _ancestor_match_filter(field: Term, value: RowValue) -> BasicCriterion:
    """Filter: field ?@> value (has ancestor match)."""
    return LTreeAncestorMatch(field, ValueWrapper(value))


def _descendant_match_filter(field: Term, value: RowValue) -> BasicCriterion:
    """Filter: field ?<@ value (has descendant match)."""
    return LTreeDescendantMatch(field, ValueWrapper(value))
