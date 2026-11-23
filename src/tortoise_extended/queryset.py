"""Random ordering for tortoise-orm querysets.

Provides:

- :func:`random_ordering` — a copy of a queryset ordered by ``RANDOM()``; the
  same call is installed as ``QuerySet.random`` at import time

Usage::

    from tortoise_extended import random_ordering

    # Chainable: every QuerySet method still works on the result
    rows = await Article.all().random()
    three = await Article.filter(published=True).random().limit(3)
    names = await Article.all().random().values_list("name", flat=True)

PostgreSQL evaluates ``ORDER BY RANDOM()`` by computing a random value for
**every** matching row and sorting the result: the cost is ``O(n log n)`` in
the row count, whether you asked for one row or all of them. That is fine for
a few thousand rows and wrong for a few million — for those, sample with
``TABLESAMPLE SYSTEM (n)`` in raw SQL (which trades uniformity for speed and
returns an approximate row count) or keep a pre-shuffled key column.
"""

from tortoise.contrib.postgres.functions import Random
from tortoise.models import Model
from tortoise.queryset import QuerySet

RANDOM_ALIAS = "_tortoise_extended_random"
"""Annotation name used internally; a caller annotation with this name is refused."""


def random_ordering[T: Model](queryset: QuerySet[T]) -> QuerySet[T]:
    """Return a copy of ``queryset`` ordered randomly.

    Implemented as ``annotate(_tortoise_extended_random=Random()).order_by(...)``
    rather than a new query path, so every other queryset method — ``filter``,
    ``values``, ``limit``, ``offset``, ``paginate`` — composes with it and
    ``count()`` is unaffected (the annotation is not part of the ``COUNT``).

    ``RANDOM()`` is not indexed and cannot be: the function is volatile, so
    the database has no way to answer the sort without evaluating it per row.
    Ordering is not reproducible between two calls, which is the point.

    :param queryset: The queryset to order.
    :returns: A new queryset ordered by a random value.
    :raises ValueError: When the queryset already annotates
        :data:`RANDOM_ALIAS`; the message names the annotation. ``annotate``
        silently overwrites a duplicate name, which would make the result
        depend on call order instead of failing.
    """
    # ``_annotations`` is the only place tortoise keeps annotation names, and
    # ``annotate`` overwrites on collision. Read it defensively: this is the
    # one private attribute the helper needs, and it must not turn a missing
    # attribute (a future tortoise rewrite) into an AttributeError.
    annotations = getattr(queryset, "_annotations", None)
    if isinstance(annotations, dict) and RANDOM_ALIAS in annotations:
        raise ValueError(
            f"queryset for {queryset.model.__name__} already annotates "
            f"{RANDOM_ALIAS!r}; rename that annotation before calling "
            f".random(), or the random ordering would silently replace it"
        )
    return queryset.annotate(**{RANDOM_ALIAS: Random()}).order_by(RANDOM_ALIAS)
