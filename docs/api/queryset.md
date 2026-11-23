# QuerySet helpers

Ergonomics for `tortoise.queryset.QuerySet`, plus the free functions behind them.

`.random()` is a **patch site**: `import tortoise_extended` installs it under an identity guard —
absent → installed, this package's own object → no-op, a foreign object → `PatchConflictError`.
`distinct_on()` is a plain async helper, not a patch: Tortoise's builder cannot express the SQL.
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

## `distinct_on()`

One row per group, chosen by an ordering — PostgreSQL's `DISTINCT ON`. Tortoise cannot build it:
pypika-tortoise's `Query.distinct()` takes no arguments and emits the bare `DISTINCT` token,
`QuerySet.distinct()` is a boolean flag, and `ValuesQuery` has neither. AGENTS.md rule 3 names
`DISTINCT ON` as a raw-SQL exception, so this helper writes exactly one statement and hydrates
**real model instances** from it (not dicts).

```python
from tortoise_extended import distinct_on

# Latest reading per sensor. `order_by` must START with the `on` columns.
latest = await distinct_on(
    Reading,
    "sensor_id",
    order_by=("sensor_id", "-observed_at"),
)

# Bounded predicate: values are bound, never inlined.
recent = await distinct_on(
    Reading,
    "sensor_id",
    order_by=("sensor_id", "-observed_at"),
    where="observed_at > $1",
    params=[cutoff],
    limit=10,
)
```

| Argument | Meaning |
|---|---|
| `target` | A model class, or an **unfiltered** queryset (it supplies only the model and the connection, e.g. `.using_db(...)`). |
| `*on` | Model field name(s) identifying a group. Required. |
| `order_by` | Model field name(s) picking the winner; a leading `-` means `DESC`. Must begin with the `on` columns — PostgreSQL requires it. Defaults to the `on` columns ascending (so the *first* row per group wins). |
| `limit` | Optional maximum number of groups. |
| `where` / `params` | Optional `WHERE` fragment with `$1`-style placeholders and the values bound to them, in order. |
| `include_deleted` | When the model declares `deleted_at`, soft-deleted rows are excluded unless this is `True` — the same rule the CTE builders follow. |

Emitted SQL (measured):

```sql
SELECT DISTINCT ON ("sensor_id") "id" AS "id", "sensor_id" AS "sensor_id", … 
FROM "reading"
WHERE (observed_at > $1) AND "deleted_at" IS NULL
ORDER BY "sensor_id" ASC, "observed_at" DESC
LIMIT 10
```

`ValueError` — never a database error, and raised before the connection is resolved, so bad
arguments fail the same way on an unbound model — for: an empty `on`; a name that is not a field of the model;
an `order_by` that does not lead with the `on` columns (or is shorter than them); a `limit` below 1;
placeholders that do not match `params` (missing values, values with no placeholder, skipped
indices); and a queryset carrying filters, annotations, ordering, grouping, `limit` or `offset` —
put predicates in `where`/`params`.

**Hydration:** rows come back through `BaseDBAsyncClient.execute_query_dict` and
`Model._init_from_db`, the same path the executor uses. `_init_from_db` is private to tortoise-orm,
but it is the only supported way to turn a mapping of database column names into a converted
instance; the instances carry `_saved_in_db = True`, so they can be modified and saved again. The
SELECT list is generated from the model's field projection, each column aliased to its own database
name — that aliasing is what `_init_from_db` requires.

**Determinism:** the `on` columns lead the `ORDER BY`, so groups come back in a stable order; append
a tiebreaker (e.g. `"-observed_at"`, then `"-id"`) when two rows in a group can share the ordering
values and the winner must be reproducible.
