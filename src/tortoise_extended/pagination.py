"""Offset pagination for tortoise-orm querysets.

Provides:

- :class:`PageInfo` — one page of results plus the metadata page controls
  need (total row count, page count, next/previous flags)
- :func:`paginate` — a count + offset/limit helper for any queryset; the
  same call is also installed as ``QuerySet.paginate`` at import time

Usage::

    from tortoise_extended import paginate

    page = await paginate(
        Article.all().order_by("-published_at"),
        page=2,
        page_size=20,
    )
    page.items        # list[Article] — rows 21-40
    page.total        # total row count across every page
    page.total_pages  # number of pages (0 for an empty result set)
    page.has_next     # True when a later page exists
    page.has_prev     # True when an earlier page exists

Ordering is the caller's job (``order_by``): ``offset`` without an explicit
order is not a stable page boundary, because PostgreSQL may return rows in a
different order for each query. A page past the end is not an error — it
returns an empty ``items`` list with the true ``total``.
"""

import math
from dataclasses import dataclass

from tortoise.models import Model
from tortoise.queryset import QuerySet


@dataclass(frozen=True, slots=True)
class PageInfo[T: Model]:
    """One page of queryset results plus pagination metadata.

    A frozen dataclass, not a msgspec ``Struct``: the payload is hydrated
    model instances, not a wire DTO.

    :param items: The model instances on this page (empty past the end).
    :param page: 1-based page number requested.
    :param page_size: Maximum number of items per page.
    :param total: Total number of rows across all pages.
    """

    items: list[T]
    page: int
    page_size: int
    total: int

    @property
    def total_pages(self) -> int:
        """Number of pages for the current ``total`` / ``page_size``.

        ``0`` when ``total`` is ``0``: an empty result set has no pages, so
        :attr:`has_next` and :attr:`has_prev` are both ``False`` for it.
        """
        if self.total == 0:
            return 0
        return math.ceil(self.total / self.page_size)

    @property
    def has_next(self) -> bool:
        """Whether a page after this one exists."""
        return self.page < self.total_pages

    @property
    def has_prev(self) -> bool:
        """Whether a page before this one exists."""
        return self.page > 1


async def paginate[T: Model](
    queryset: QuerySet[T],
    page: int,
    page_size: int,
) -> PageInfo[T]:
    """Fetch one page of ``queryset`` and its pagination metadata.

    Ordering is the caller's job: pass an explicit ``order_by()`` on the
    queryset. ``offset`` without an order is not a stable page boundary —
    PostgreSQL is free to return rows in a different order for the count
    query and the page query, so rows can be skipped or repeated between
    pages.

    ``count()`` runs a second query: pagination costs one ``COUNT(*)`` plus
    one offset/limit fetch. A page past the end is not an error — the
    returned :class:`PageInfo` has ``items == []`` and the true ``total``.

    :param queryset: The queryset to paginate (call ``order_by()`` first).
    :param page: 1-based page number.
    :param page_size: Maximum number of rows per page.
    :returns: The page's items and metadata.
    :raises ValueError: When ``page`` or ``page_size`` is less than 1; the
        message names the argument and the received value.
    """
    if page < 1:
        raise ValueError(f"page must be >= 1, got {page}")
    if page_size < 1:
        raise ValueError(f"page_size must be >= 1, got {page_size}")

    total = await queryset.count()
    items = await queryset.offset((page - 1) * page_size).limit(page_size)
    return PageInfo(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
    )
