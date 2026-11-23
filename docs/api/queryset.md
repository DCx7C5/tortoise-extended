# QuerySet helpers

Ergonomics installed on `tortoise.queryset.QuerySet` at import time, plus the free functions behind
them.

Every helper here is a **patch site**: `import tortoise_extended` installs it under an identity
guard — absent → installed, this package's own object → no-op, a foreign object → `PatchConflictError`.
Import the package before `Tortoise.init()`, as always.

## `.random()`

```python
from tortoise_extended import random_ordering

rows = await Article.all().random()                    # every row, shuffled
three = await Article.filter(published=True).random().limit(3)
names = await Article.all().random().values_list("name", flat=True)
page = await paginate(Article.all().random(), page=1, page_size=20)
```

`.random()` returns a **queryset**, not a list: it is
`annotate(_tortoise_extended_random=Random()).order_by("_tortoise_extended_random")` on a clone, so
every other queryset method composes with it and `await` on the result gives the rows. `count()` is
unaffected — the annotation is not part of the `COUNT`.

| Property | Value |
|----------|-------|
| SQL | `SELECT …,RANDOM() "_tortoise_extended_random" … ORDER BY "_tortoise_extended_random" ASC` |
| Cost | `O(n log n)` — PostgreSQL evaluates `RANDOM()` per matching row and sorts |
| Reproducible | No, by design |
| Indexable | No — `RANDOM()` is volatile, so no index can order by it |

The free function `random_ordering(queryset)` is the same object, for callers that would rather not
depend on the method.

**Collision:** the alias is fixed. A queryset that already annotates
`_tortoise_extended_random` raises `ValueError` naming the annotation, because `annotate()`
silently overwrites a duplicate name and the ordering would then depend on call order.

**Large tables:** `TABLESAMPLE SYSTEM (n)` in raw SQL is the escape hatch — it trades uniformity for
speed and returns an approximate row count. This helper deliberately does not implement sampling:
the two behaviours are not interchangeable and the choice belongs to the caller.
