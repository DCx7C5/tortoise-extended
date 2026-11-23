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

# Bounded predicate: the fragment is trusted SQL, its placeholder values are bound.
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
| `target` | A model class, or a queryset carrying only its model and connection — `Model.all()`, optionally `.using_db(...)`. On a `BaseSoftDeleteModel`, the `deleted_at IS NULL` condition `SoftDeleteQuerySet` injects into `all()` is accepted, because the helper re-applies that same condition itself (unless `include_deleted=True`); any other carried state raises `ValueError`. A model **instance** is neither form and raises `TypeError` up front. |
| `*on` | Model field name(s) identifying a group. Required. |
| `order_by` | Model field name(s) picking the winner; a leading `-` means `DESC`. Must begin with the `on` columns — this helper requires it, so the winning row per group is defined and the output stable. Defaults to the `on` columns ascending (so the *first* row per group wins). |
| `limit` | Optional maximum number of groups, an integer of at least 1. A `bool` is refused by name: `bool` is an `int` in Python, but PostgreSQL rejects `LIMIT true` with `argument of LIMIT must be type bigint, not type boolean`. |
| `where` | Optional `WHERE` fragment — **trusted SQL text**, concatenated into the statement exactly as written. Never pass user input here; put every value behind a `$1`-style placeholder. |
| `params` | Values bound to those placeholders, in order — sent as query parameters, never inlined. |
| `include_deleted` | When the model projects a **`deleted_at` column**, soft-deleted rows are excluded unless this is `True`. The rule keys on the column, not on a field name: a field named `deleted_at` (whose `source_field` decides the column) and a differently named field mapped onto the column — e.g. `removed_at = fields.DatetimeField(source_field="deleted_at")` — are both honoured. The CTE builders (`pathfinding`, `GraphTraversal`, `GraphVectorSearch`) still probe the field *name* and emit a literal `deleted_at` identifier, so a renamed field gets no filter there. |

Emitted SQL (measured):

```sql
SELECT DISTINCT ON ("sensor_id") "id" AS "id", "sensor_id" AS "sensor_id", … 
FROM "reading"
WHERE (observed_at > $1) AND "deleted_at" IS NULL
ORDER BY "sensor_id" ASC, "observed_at" DESC
LIMIT 10
```

`ValueError` — never a database error. Argument mistakes are raised before the connection is resolved,
so bad arguments fail the same way on an unbound model; the backend guard below necessarily runs after
it. Refused: an empty `on`; a name that is not a field of the model;
an `order_by` that does not lead with the `on` columns (or is shorter than them); a `limit` below 1 or
a boolean `limit` (`True`/`False`; Python's `bool` is an `int`, so the `< 1` check alone lets
`LIMIT true` through); a `$0` or zero-padded (`$01`) placeholder; placeholders that do not match
`params` (missing values, values with no placeholder, skipped indices); a queryset carrying filters,
annotations, ordering, grouping, `DISTINCT`, `select_for_update()`, `prefetch_related()`,
`.only()`/`.values()` field narrowing, `limit` or `offset` — put predicates in `where`/`params`; and a
connection whose client is not the asyncpg backend (see Hydration).

`TypeError` — before any of the above — when `target` is neither a model class nor a `QuerySet`: a
model **instance** carries row state this helper cannot translate, and letting one through would
surface as an `AttributeError` further down instead of naming the mistake. A `Model.get(...)` result
is *not* in this group — it is a `QuerySet` carrying a filter and a limit, so it fails as carried
state (`ValueError`) like any other.

**Placeholders are quote-aware.** `$1` is a parameter only in SQL code: inside a single-quoted
literal (`''` escapes a quote), a double-quoted identifier (`""` escapes), a dollar-quoted body
(`$$…$$` / `$tag$…$tag$`), a `--` line comment or a `/* */` block comment (nested, as PostgreSQL
allows) it is plain text. So `where="title <> '$1'"` is accepted without params, and a `$2` hidden
inside a comment is not counted as bound. `$0` and any leading-zero index are refused by name,
because PostgreSQL numbers parameters from `$1` and rejects both.

**`where` is trusted SQL.** Only `params` are bound: the fragment itself is spliced into the
statement verbatim (inside parentheses) so it must never contain user input — a fragment built by
string-formatting caller data is SQL injection. Pass the data through placeholders instead.

Two consequences of splicing to keep in mind: a `--` line comment in the fragment swallows the
`ORDER BY` the helper appends after it (PostgreSQL then reports a syntax error at end of input), and
a `/* … */` block comment is fine. Neither is a place to hide a predicate — the fragment is code.

**Hydration:** rows come back through `BaseDBAsyncClient.execute_query_dict` and
`Model._init_from_db`, the same path the executor uses. `_init_from_db` is private to tortoise-orm,
but it is the only supported way to turn a mapping of database column names into a converted
instance; the instances carry `_saved_in_db = True`, so they can be modified and saved again. The
SELECT list is generated from the model's field projection, each column aliased to its own database
name — that aliasing is what `_init_from_db` requires.

**asyncpg backend only.** The `where` placeholders are `$n` tokens and the rows are hydrated from
`execute_query_dict`, both asyncpg-shaped. Tortoise can also reach PostgreSQL through psycopg, where
`$1` inside a statement is literal text, so a connection whose client is not
`tortoise.backends.asyncpg.client.AsyncpgDBClient` is refused with `ValueError` naming the client,
before the statement is executed. Configure the model's connection with the `asyncpg` engine.

**Determinism:** the `on` columns lead the `ORDER BY`, so groups come back in a stable order; append
a tiebreaker (e.g. `"-observed_at"`, then `"-id"`) when two rows in a group can share the ordering
values and the winner must be reproducible.
