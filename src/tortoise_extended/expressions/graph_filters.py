"""pgvector similarity filter operators for tortoise-orm.

Custom Criterion subclasses for pgvector distance operators:
<-> L2 distance, <#> inner product, <=> cosine distance
"""

from typing import TYPE_CHECKING, Any

from pypika_tortoise.enums import Comparator
from pypika_tortoise.terms import BasicCriterion, Field, Term, ValueWrapper
from tortoise.filters import is_null as _is_null
from tortoise.filters import not_null as _not_null

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


def vector_encoder(value: Any, _instance: Model | None = None, _field: Any = None) -> str | None:
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


def _vector_value_passthrough(value: Any, _instance: Any = None, _field: Any = None) -> Any:
    """Identity encoder for Tortoise filter value_encoder slot.

    The distance operators (``_l2_distance_lte`` etc.) receive the raw
    ``[vector, threshold]`` or ``[vector]`` value and call ``vector_encoder``
    on the vector component internally.  Using ``vector_encoder`` here would
    corrupt compound values by trying to iterate the threshold.
    """
    return value


# Filter definitions for VectorField

def get_vector_filters(field_name: str, source_field: str) -> dict[str, Any]:
    """Return filter definitions for a VectorField.

    pgvector does not support ``=`` on vector columns, so the base filter
    only allows ``isnull`` checks.  Use the distance operators for
    similarity queries.
    """
    from tortoise.filters import bool_encoder

    return {
        field_name: {
            "field": field_name,
            "source_field": source_field,
            "operator": _is_null,
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


def _l2_distance_lte(field: Term, value: Any) -> Any:
    """Filter: L2 distance <= threshold."""
    if isinstance(value, (list, tuple)):
        if len(value) == 2 and isinstance(value[0], list):
            query_vector, threshold = value
        else:
            query_vector, threshold = value, 1.0
        return L2Distance(field, ValueWrapper(query_vector)).lte(threshold)
    return L2Distance(field, ValueWrapper(value)).lte(1.0)


def _cosine_distance_lte(field: Term, value: Any) -> Any:
    """Filter: cosine distance <= threshold."""
    if isinstance(value, (list, tuple)):
        if len(value) == 2 and isinstance(value[0], list):
            query_vector, threshold = value
        else:
            query_vector, threshold = value, 1.0
        return CosineDistance(field, ValueWrapper(query_vector)).lte(threshold)
    return CosineDistance(field, ValueWrapper(value)).lte(1.0)


def _inner_product_gte(field: Term, value: Any) -> Any:
    """Filter: inner product >= threshold (higher = more similar).

    pgvector's ``<#>`` operator returns the **negative** inner product, so
    ``inner_product >= threshold`` translates to ``<#> <= -threshold``.
    """
    if isinstance(value, (list, tuple)):
        if len(value) == 2 and isinstance(value[0], list):
            query_vector, threshold = value
        else:
            query_vector, threshold = value, 0.0
        return InnerProduct(field, ValueWrapper(query_vector)).lte(-threshold)
    return InnerProduct(field, ValueWrapper(value)).lte(0.0)
