"""pgvector similarity filter operators for tortoise-orm.

Custom Criterion subclasses for pgvector distance operators:
<-> L2 distance, <#> inner product, <=> cosine distance
"""

from typing import TYPE_CHECKING, TypeAlias, cast

from pypika_tortoise.enums import Comparator
from pypika_tortoise.terms import BasicCriterion, Field, Term, ValueWrapper
from tortoise.fields import Field as TortoiseField
from tortoise.filters import FilterInfoDict
from tortoise.filters import is_null as _is_null
from tortoise.filters import not_null as _not_null
from tortoise_extended._types import RowValue
from tortoise_extended.exceptions import VectorFieldError

if TYPE_CHECKING:
    from tortoise.models import Model

_VectorValue: TypeAlias = list[float] | tuple[float, ...] | str
"""A query vector: a sequence of floats or a pgvector literal string."""

_VectorFilterValue: TypeAlias = (
    list[_VectorValue | float] | tuple[_VectorValue | float, ...] | str
)
"""A similarity-filter value: plain vector or ``[vector, threshold]`` compound."""


# pgvector operator comparators — define as simple value holders for BasicCriterion


class L2DistanceOp(Comparator):
    l2_distance = " <-> "


class InnerProductOp(Comparator):
    inner_product = " <#> "


class CosineDistanceOp(Comparator):
    cosine_distance = " <=> "


class HammingDistanceOp(Comparator):
    hamming_distance = " <~> "


class JaccardDistanceOp(Comparator):
    jaccard_distance = " <%> "


# Criterion wrappers — these generate the actual SQL


class L2Distance(BasicCriterion):
    """<-> operator: L2 (Euclidean) distance between vectors."""

    def __init__(self, left: Term | str, right: Term, alias: str | None = None) -> None:
        if isinstance(left, str):
            left = Field(left)
        super().__init__(L2DistanceOp.l2_distance, left, right, alias=alias)


class InnerProduct(BasicCriterion):
    """<#> operator: inner product (negative) between vectors."""

    def __init__(self, left: Term | str, right: Term, alias: str | None = None) -> None:
        if isinstance(left, str):
            left = Field(left)
        super().__init__(InnerProductOp.inner_product, left, right, alias=alias)


class CosineDistance(BasicCriterion):
    """<=> operator: cosine distance between vectors."""

    def __init__(self, left: Term | str, right: Term, alias: str | None = None) -> None:
        if isinstance(left, str):
            left = Field(left)
        super().__init__(CosineDistanceOp.cosine_distance, left, right, alias=alias)


class HammingDistance(BasicCriterion):
    """<~> operator: Hamming distance between binary vectors."""

    def __init__(self, left: Term | str, right: Term, alias: str | None = None) -> None:
        if isinstance(left, str):
            left = Field(left)
        super().__init__(HammingDistanceOp.hamming_distance, left, right, alias=alias)


class JaccardDistance(BasicCriterion):
    """<%> operator: Jaccard distance between sparse vectors."""

    def __init__(self, left: Term | str, right: Term, alias: str | None = None) -> None:
        if isinstance(left, str):
            left = Field(left)
        super().__init__(JaccardDistanceOp.jaccard_distance, left, right, alias=alias)


# Vector-specific encoders


def vector_encoder(
    value: RowValue | list[float] | tuple[float, ...],
    _instance: Model | None = None,
    _field: TortoiseField[list[float]] | None = None,
) -> str | None:
    """Encode a list/tuple of floats into pgvector ``'[1.0,0.0,...]'`` string.

    Called directly in annotation expressions::

        .annotate(distance=L2Distance("embedding", ValueWrapper(vector_encoder(q))))

    For filter value encoding, use ``_vector_value_passthrough`` instead — the
    operator functions handle compound ``[vector, threshold]`` values internally.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(str(float(x)) for x in value) + "]"
    return str(value)


def _vector_value_passthrough(
    value: _VectorFilterValue,
    _instance: Model | None = None,
    _field: TortoiseField[list[float]] | None = None,
) -> _VectorFilterValue:
    """Identity encoder for Tortoise filter value_encoder slot.

    The distance operators (``_l2_distance_lte`` etc.) receive the raw
    ``[vector, threshold]`` or ``[vector]`` value and call ``vector_encoder``
    on the vector component internally.  Using ``vector_encoder`` here would
    corrupt compound values by trying to iterate the threshold.
    """
    return value


# Filter definitions for VectorField


def _vector_eq_guard(_field: Term, _value: bool) -> BasicCriterion:
    """Reject bare equality filters on vector columns.

    pgvector has no ``=`` operator for vectors.  ``filter(embedding=<vec>)``
    with a non-None value previously compiled to ``IS NULL`` — the
    ``bool_encoder`` coerced the vector to ``True`` — silently returning the
    wrong rows.  Tortoise redirects ``None`` to the ``__isnull`` filter
    before this operator runs, so any value reaching here is non-None and is
    an error.

    Args:
        _field: The vector column term (unused — always raises).
        _value: The ``bool_encoder``-encoded filter value (unused).

    Raises:
        VectorFieldError: Always, with guidance to the supported operators.
    """
    raise VectorFieldError(
        "Bare equality filters are not supported on VectorField columns. "
        "Use None (IS NULL), embedding__isnull, embedding__not_isnull, or "
        "the similarity operators (__l2_distance, __cosine_distance, "
        "__inner_product)."
    )


def get_vector_filters(field_name: str, source_field: str) -> dict[str, FilterInfoDict]:
    """Return filter definitions for a VectorField.

    pgvector does not support ``=`` on vector columns, so the base filter
    only allows ``isnull`` checks.  Use the distance operators for
    similarity queries.  A bare non-None value raises :class:`VectorFieldError`
    instead of silently compiling to ``IS NULL``.
    """
    from tortoise.filters import bool_encoder

    return {
        field_name: {
            "field": field_name,
            "source_field": source_field,
            "operator": _vector_eq_guard,
            "value_encoder": bool_encoder,
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
        f"{field_name}__l2_distance": {
            "field": field_name,
            "source_field": source_field,
            "operator": _l2_distance_lte,
            "value_encoder": _vector_value_passthrough,
        },
        f"{field_name}__cosine_distance": {
            "field": field_name,
            "source_field": source_field,
            "operator": _cosine_distance_lte,
            "value_encoder": _vector_value_passthrough,
        },
        f"{field_name}__inner_product": {
            "field": field_name,
            "source_field": source_field,
            "operator": _inner_product_gte,
            "value_encoder": _vector_value_passthrough,
        },
    }


def _parse_vector_threshold(
    value: _VectorFilterValue,
    default_threshold: float,
    operator: str,
) -> tuple[_VectorValue, float]:
    """Parse a similarity-filter value into ``(query_vector, threshold)``.

    Supports two documented shapes:

    - plain vector: ``[0.1, 0.2, ...]`` → default threshold
    - compound: ``[[0.1, 0.2, ...], 0.5]`` → explicit threshold

    A compound-looking value whose second element is not a number (e.g. a
    flat ``[[v1], [v2]]`` where the caller meant two vectors) raises
    :class:`VectorFieldError` instead of silently producing invalid SQL
    (G20).
    """
    if isinstance(value, (list, tuple)) and len(value) == 2:
        first, threshold = value[0], value[1]
        if isinstance(first, list):
            if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
                raise VectorFieldError(
                    f"{operator} compound value must be [query_vector, threshold]; "
                    f"threshold must be a number, got {threshold!r}"
                )
            return first, float(threshold)
    return cast(_VectorValue, value), default_threshold


def _l2_distance_lte(field: Term, value: _VectorFilterValue) -> BasicCriterion:
    """Filter: L2 distance <= threshold."""
    query_vector, threshold = _parse_vector_threshold(value, 1.0, "__l2_distance")
    return L2Distance(field, ValueWrapper(query_vector)).lte(threshold)


def _cosine_distance_lte(field: Term, value: _VectorFilterValue) -> BasicCriterion:
    """Filter: cosine distance <= threshold."""
    query_vector, threshold = _parse_vector_threshold(value, 1.0, "__cosine_distance")
    return CosineDistance(field, ValueWrapper(query_vector)).lte(threshold)


def _inner_product_gte(field: Term, value: _VectorFilterValue) -> BasicCriterion:
    """Filter: inner product >= threshold (higher = more similar).

    pgvector's ``<#>`` operator returns the **negative** inner product, so
    ``inner_product >= threshold`` translates to ``<#> <= -threshold``.
    """
    query_vector, threshold = _parse_vector_threshold(value, 0.0, "__inner_product")
    return InnerProduct(field, ValueWrapper(query_vector)).lte(-threshold)
