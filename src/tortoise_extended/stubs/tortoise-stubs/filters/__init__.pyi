# pyright: reportExplicitAny=false
"""Type stubs for ``tortoise.filters``.

Partial stub: the runtime module is analyzed from its ``py.typed`` source;
this stub additionally declares the module surface that ``tortoise_extended``
patches at import time (``get_filters_for_field`` and the
``_tortoise_extended_patched`` flag) plus the filter helpers used by the
ltree/graph filter extensions.
"""

import operator as operator
from collections.abc import Callable, Iterable
from typing import Any, NotRequired, TypedDict

from pypika_tortoise.queries import Table
from pypika_tortoise.terms import Criterion, Term
from tortoise.fields import Field
from tortoise.models import Model


class FilterInfoDict(TypedDict):
    field: str
    operator: Callable[..., Any]
    backward_key: NotRequired[str]
    table: NotRequired[Table]
    value_encoder: NotRequired[Callable[..., Any]]
    source_field: NotRequired[str]
    is_tsvector: NotRequired[bool]


def get_filters_for_field(
    field_name: str, field: Field[object] | None, source_field: str
) -> dict[str, FilterInfoDict]: ...


def is_in(field: Term, value: Any) -> Criterion: ...
def not_in(field: Term, value: Any) -> Criterion: ...
def not_equal(field: Term, value: Any) -> Criterion: ...
def is_null(field: Term, value: Any) -> Criterion: ...
def not_null(field: Term, value: Any) -> Criterion: ...


def bool_encoder(value: Any, instance: Model, field: Field) -> bool: ...
def list_encoder(values: Iterable[Any], instance: Model, field: Field) -> list[Any]: ...


_tortoise_extended_patched: bool

