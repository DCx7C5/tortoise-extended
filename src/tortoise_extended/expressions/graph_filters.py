"""pgvector similarity filter operators for tortoise-orm.

Custom Criterion subclasses for pgvector distance operators:
<-> L2 distance, <#> inner product, <=> cosine distance
"""

from typing import TYPE_CHECKING

from pypika_tortoise.enums import Comparator
from pypika_tortoise.terms import BasicCriterion, Field, Term, ValueWrapper
from tortoise.filters import is_null as _is_null
from tortoise.filters import not_null as _not_null
from tortoise_extended._types import LibraryAny
from tortoise_extended.exceptions import VectorFieldError

if TYPE_CHECKING:
    from tortoise.models import Model


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
    value: LibraryAny,  # pyright: ignore[reportExplicitAny]
    _instance: Model | None = None,
    _field: LibraryAny = None,  # pyright: ignore[reportExplicitAny]
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
        return "[" + ",".join(str(float(x)) for x in value) + "]"  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    return str(value)


def _vector_value_passthrough(
    value: LibraryAny,  # pyright: ignore[reportExplicitAny]
    _instance: LibraryAny = None,  # pyright: ignore[reportExplicitAny]
    _field: LibraryAny = None,  # pyright: ignore[reportExplicitAny]
) -> LibraryAny:  # pyright: ignore[reportExplicitAny]
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


def get_vector_filters(field_name: str, source_field: str) -> dict[str, LibraryAny]:  # pyright: ignore[reportExplicitAny]
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


def _l2_distance_lte(field: Term, value: LibraryAny) -> BasicCriterion:  # pyright: ignore[reportExplicitAny]
    """Filter: L2 distance <= threshold."""
    if isinstance(value, (list, tuple)):
        if len(value) == 2 and isinstance(value[0], list):  # pyright: ignore[reportUnknownArgumentType]
            query_vector, threshold = value  # pyright: ignore[reportUnknownVariableType]
        else:
            query_vector, threshold = value, 1.0  # pyright: ignore[reportUnknownVariableType]
        return L2Distance(field, ValueWrapper(query_vector)).lte(threshold)
    return L2Distance(field, ValueWrapper(value)).lte(1.0)


def _cosine_distance_lte(field: Term, value: LibraryAny) -> BasicCriterion:  # pyright: ignore[reportExplicitAny]
    """Filter: cosine distance <= threshold."""
    if isinstance(value, (list, tuple)):
        if len(value) == 2 and isinstance(value[0], list):  # pyright: ignore[reportUnknownArgumentType]
            query_vector, threshold = value  # pyright: ignore[reportUnknownVariableType]
        else:
            query_vector, threshold = value, 1.0  # pyright: ignore[reportUnknownVariableType]
        return CosineDistance(field, ValueWrapper(query_vector)).lte(threshold)
    return CosineDistance(field, ValueWrapper(value)).lte(1.0)


def _inner_product_gte(field: Term, value: LibraryAny) -> BasicCriterion:  # pyright: ignore[reportExplicitAny]
    """Filter: inner product >= threshold (higher = more similar).

    pgvector's ``<#>`` operator returns the **negative** inner product, so
    ``inner_product >= threshold`` translates to ``<#> <= -threshold``.
    """
    if isinstance(value, (list, tuple)):
        if len(value) == 2 and isinstance(value[0], list):  # pyright: ignore[reportUnknownArgumentType]
            query_vector, threshold = value  # pyright: ignore[reportUnknownVariableType]
        else:
            query_vector, threshold = value, 0.0  # pyright: ignore[reportUnknownVariableType]
        return InnerProduct(field, ValueWrapper(query_vector)).lte(-threshold)
    return InnerProduct(field, ValueWrapper(value)).lte(0.0)
