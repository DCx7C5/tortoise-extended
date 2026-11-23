# Pagination

Offset pagination with the metadata a page widget actually needs — `total`,
`total_pages`, `has_next`, `has_prev` — over the ordinary Tortoise QuerySet.
No raw SQL, no new dependency.

## Import

```python
from tortoise_extended import PageInfo, paginate
```

---

## `QuerySet.paginate(page, page_size)`

Installed on every QuerySet at import time (patch site 5 of `_apply_patches`), so it composes with
the rest of the ORM surface — filters, `select_related`, `annotate`, `prefetch_related`:

```python
import tortoise_extended  # noqa: F401 — installs QuerySet.paginate (and the rest of the patches)

page = await Chunk.filter(active=True).order_by("name").paginate(2, 25)

page.items       # list[Chunk] — the 25 rows of page 2
page.total       # 312      — rows matching the filter, not just this page
page.total_pages # 13       — math.ceil(312 / 25)
page.has_next    # True
page.has_prev    # True
```

**Order is the caller's job.** `offset` without an `order_by` is not a stable page boundary — two
consecutive requests can overlap or skip rows. Paginate a `QuerySet` that already has a
deterministic order:

```python
await Chunk.all().order_by("created_at", "id").paginate(1, 50)
```

**A page past the end is not an error.** `paginate(99, 25)` on 312 rows returns `items == []` with
`total == 312` and `total_pages == 13`, so a client that holds a stale page number still learns the
real shape of the result set instead of getting an exception.

```python
page = await Chunk.all().paginate(99, 25)
page.items, page.total, page.total_pages   # ([], 312, 13)
```

---

## `PageInfo`

A frozen dataclass (not a msgspec Struct — it carries model instances, not wire data):

| Member | Type | Meaning |
|---|---|---|
| `items` | `list[MODEL]` | The rows of this page, in the queryset's order |
| `page` | `int` | The 1-based page number that was requested |
| `page_size` | `int` | Rows per page |
| `total` | `int` | Rows matching the queryset (the `count()` of the **same** filters) |
| `total_pages` | `int` (property) | `math.ceil(total / page_size)`; **0** when `total == 0` |
| `has_next` | `bool` (property) | `page < total_pages` |
| `has_prev` | `bool` (property) | `page > 1` |

An empty table gives `total == 0`, `total_pages == 0`, and both flags `False` — there is no page 1
of nothing.

---

## Free function

The method is a thin wrapper over the function, which is also public for code that would rather
not depend on a QuerySet method:

```python
from tortoise_extended import paginate

page = await paginate(Chunk.filter(active=True).order_by("name"), page=2, page_size=25)
```

```python
async def paginate(
    queryset: QuerySet[MODEL], page: int, page_size: int
) -> PageInfo[MODEL]: ...
```

Raises `ValueError` when `page < 1` or `page_size < 1`; the message names the argument and the
value it received. Values are not clamped — silently turning `page=0` into `page=1` hides a
client-side off-by-one that the caller should see.

---

## Cost

Two queries: `COUNT(*)` over the queryset's filters, then the `LIMIT`/`OFFSET` slice. `OFFSET`
costs `O(offset)` in PostgreSQL — deep pages get slower. For very large tables prefer keyset
(seek) pagination over a unique, ordered column:

```python
last_seen = ...
rows = await Chunk.filter(id__gt=last_seen).order_by("id").limit(25)
```

`paginate()` is the right tool for admin screens, search results and anything that shows a page
count; keyset is the right tool for a scrolling feed.

## Requirements

- None beyond a Tortoise QuerySet. `offset`/`limit`/`count` are portable, and only the
  PostgreSQL test harness in this repository is required for the tests.
