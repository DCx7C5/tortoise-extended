# Window Functions

Analytic-function builders — `ROW_NUMBER`, `RANK`, `DENSE_RANK`, `NTILE` and windowed aggregates —
for `QuerySet.annotate()`, configured from **field names** instead of pypika terms. Tortoise can
already render an analytic function when it is handed a pypika object; these builders are the
ergonomic, trap-free path to that object. No raw SQL, no new dependency.

## Import

```python
from tortoise_extended import (
    row_number,
    rank,
    dense_rank,
    ntile,
    sum_over,
    avg_over,
    count_over,
    min_over,
    max_over,
)
```

---

## The trap these builders remove

Tortoise's `F()` is **not** a pypika term. `AnalyticFunction.over()` and `.orderby()` stringify
anything that is not a pypika object, so the obvious Tortoise-looking call compiles to broken SQL:

```python
from tortoise.expressions import F
from pypika_tortoise.analytics import RowNumber

# WRONG — F("bucket") is str()'d straight into the window clause
RowNumber().over(F("bucket"))
# ROW_NUMBER() OVER(PARTITION BY <tortoise.expressions.F object at 0x7f...>)
```

PostgreSQL rejects that statement. The builders take names and build the `Field` terms themselves,
so the broken shape cannot be expressed:

```python
from tortoise_extended import row_number

row_number(partition_by="bucket", order_by="n")
# ROW_NUMBER() OVER(PARTITION BY "bucket" ORDER BY "n" ASC)
```

---

## Builders

| Builder | SQL | Position of the field argument |
|---|---|---|
| `row_number(partition_by=(), order_by=())` | `ROW_NUMBER() OVER(...)` | — |
| `rank(partition_by=(), order_by=())` | `RANK() OVER(...)` | — |
| `dense_rank(partition_by=(), order_by=())` | `DENSE_RANK() OVER(...)` | — |
| `ntile(buckets, partition_by=(), order_by=())` | `NTILE(n) OVER(...)` | bucket count only |
| `sum_over(field, partition_by=(), order_by=())` | `SUM("field") OVER(...)` | first positional |
| `avg_over(field, partition_by=(), order_by=())` | `AVG("field") OVER(...)` | first positional |
| `count_over(field=None, partition_by=(), order_by=())` | `COUNT(*) OVER(...)` / `COUNT("field") OVER(...)` | first positional; `None` counts rows |
| `min_over(field, partition_by=(), order_by=())` | `MIN("field") OVER(...)` | first positional |
| `max_over(field, partition_by=(), order_by=())` | `MAX("field") OVER(...)` | first positional |

Common arguments:

| Argument | Type | Meaning |
|---|---|---|
| `partition_by` | `str \| Sequence[str]` | Column name(s) that split the rows into independent windows. Empty means one window over the whole result set. |
| `order_by` | `str \| Sequence[str]` | Order column name(s) **inside** the window. A leading `-` means `DESC` (`"-amount"` → `ORDER BY "amount" DESC`); otherwise `ASC` is explicit. |
| `field` | `str` | Column the aggregate is taken over — a plain identifier, never `F()`, an expression or a `table.column` string. |

Each builder returns the configured pypika analytic term, ready for `annotate()`:

```python
import tortoise_extended  # noqa: F401 — patches; import before Tortoise.init()

rows = await Sale.annotate(
    position=row_number(partition_by="region", order_by="-amount"),
    region_total=sum_over("amount", partition_by="region"),
    region_rows=count_over(partition_by="region"),
).order_by("region", "position")
```

`values()` picks the annotations up automatically (or name them explicitly):

```python
await Sale.annotate(
    position=row_number(partition_by="region", order_by="-amount"),
).values("region", "amount", "position")
```

---

## SQL produced

```sql
-- row_number(partition_by="region", order_by="-amount")
SELECT ..., ROW_NUMBER() OVER(PARTITION BY "region" ORDER BY "amount" DESC) "position"
FROM "sale" ORDER BY "region" ASC, "position" ASC

-- sum_over("amount", partition_by="region", order_by="day")
SELECT ..., SUM("amount") OVER(PARTITION BY "region" ORDER BY "day" ASC) "region_running"
FROM "sale" ORDER BY "region" ASC, "day" ASC

-- count_over(partition_by="region") — no field means COUNT(*)
SELECT ..., COUNT(*) OVER(PARTITION BY "region") "region_rows" FROM "sale"
```

A windowed aggregate **with** `order_by` uses SQL's default frame
(`RANGE UNBOUNDED PRECEDING … CURRENT ROW`), which is a running total; **without** `order_by` it
covers the whole partition:

```python
await Sale.annotate(
    running=sum_over("amount", partition_by="region", order_by="day"),
    total=sum_over("amount", partition_by="region"),
)
```

---

## Validation

`partition_by` / `order_by` names (and the aggregate `field`) must be plain identifiers:
letters, digits and underscores, not starting with a digit. Anything else — `"table.column"`,
a quoted name, an expression — raises `ValueError` naming the value, because the builders do not
perform table resolution or identifier quoting beyond what pypika's `Field` does.

`ntile()` additionally raises `ValueError` when the bucket count is below 1.

---

## Frame clauses are out of scope

`ROWS` / `RANGE` frames are not exposed as builder arguments. A frame is not decoration — it
changes what a windowed aggregate means (whole-partition total vs. running total) — and inventing a
default would silently change results. The returned terms are ordinary pypika analytics, so a
caller who needs an explicit frame configures it directly:

```python
from pypika_tortoise.analytics import CURRENT_ROW, Preceding, Sum
from pypika_tortoise.terms import Field

Sum(Field("amount")).over(Field("region")).orderby(Field("day")).rows(
    Preceding(2), CURRENT_ROW
)
# SUM("amount") OVER(PARTITION BY "region" ORDER BY "day"
#                    ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)
```

A frame written this way is deliberately raw pypika configuration, not part of the builder API.

## Limits

- **Windows run after `WHERE` / `GROUP BY`.** A window result cannot be filtered in the same
  `SELECT`; filter in the application or in an outer query over the annotated result.
- **Names are column names of the queryset's table.** In a query that joins another table, an
  unqualified name can be ambiguous — the builders do not qualify fields.
- **Cost is a sort per partition.** A window with `order_by` sorts its rows (like `ORDER BY`), so a
  large unindexed partition pays `O(n log n)` per window; an index matching the `PARTITION BY` /
  `ORDER BY` columns is what makes it cheap.
- The builders only *build* the term. Execution, `LIMIT`, `OFFSET`, `values()`, `filter()` and
  `paginate()` all remain ordinary Tortoise QuerySet behaviour.

## Requirements

- PostgreSQL (window functions are standard SQL; this package and its suite are PostgreSQL-only).
- `import tortoise_extended` before `Tortoise.init()` — same import-order rule as every other
  patch and registration in this package.
