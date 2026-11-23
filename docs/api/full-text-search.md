# Full-Text Search

PostgreSQL full-text search through Tortoise's own `tortoise.contrib.postgres` surface.

**This page documents upstream, not a `tortoise_extended` feature.** `tortoise-orm` 1.1.x already
ships the whole FTS stack — a generated `tsvector` column, the `SearchVector` / `SearchQuery` /
`SearchRank` / `SearchHeadline` expressions, `Lexeme`, and a GIN index type — and every claim below
was executed against PostgreSQL 18 before it was written. The reason it is documented here is that
three of the pieces fail in ways that look like a broken library, and this repository's
`HybridSearch` composes vector search with the same underlying SQL.

See also: [Hybrid Search](hybrid-search.md) for the vector + FTS compositor, and
[Indexes](indexes.md) for the index types.

## Import

```python
from tortoise.contrib.postgres.fields import TSVectorField
from tortoise.contrib.postgres.indexes import GinIndex
from tortoise.contrib.postgres.search import (
    CombinedSearchVector,
    Lexeme,
    SearchHeadline,
    SearchQuery,
    SearchRank,
    SearchVector,
)
```

There is nothing to add to `tortoise_extended`'s `__all__` — re-exporting upstream symbols would
make the package own their compatibility.

## The generated column

```python
class Doc(Model):
    title = fields.CharField(max_length=64)
    body = fields.TextField(default="")
    search = TSVectorField(source_fields=["title", "body"], config="english")

    class Meta:
        table = "docs"
        indexes = [GinIndex(fields=["search"])]
```

Emits:

```sql
CREATE TABLE IF NOT EXISTS "docs" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "title" VARCHAR(64) NOT NULL,
    "body" TEXT NOT NULL,
    "search" TSVECTOR GENERATED ALWAYS AS (TO_TSVECTOR('english',COALESCE("title", '')) || TO_TSVECTOR('english',COALESCE("body", ''))) STORED NOT NULL
);
CREATE INDEX IF NOT EXISTS "idx_docs_search_..." ON "docs" USING GIN ("search");
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `source_fields` | `Sequence[str] \| str \| None` | `None` | Columns fed into the vector. With `stored=True` these are required |
| `config` | `str \| None` | `None` | PostgreSQL text-search configuration. **Required for a generated column** (see the traps) |
| `weights` | `Sequence[str] \| None` | `None` | One `'A'`–`'D'` per source field; emits `SETWEIGHT(..., 'A')` |
| `stored` | `bool` | `True` | `False` turns off `GENERATED` and stores a plain `tsvector` you maintain yourself |

With `weights=["A", "B"]` the generated expression becomes
`SETWEIGHT(TO_TSVECTOR('english',COALESCE("title", '')),'A') || SETWEIGHT(TO_TSVECTOR('english',COALESCE("body", '')),'B')`,
so a title hit outranks a body hit in `ts_rank`.

`COALESCE(<col>, '')` is why a `NULL` column does not poison the whole vector. Note that
`SearchVector(...)` — the expression form, below — does **not** coalesce, so a `NULL` column there
yields a `NULL` vector.

## Queries

`SearchQuery(value, config=None, search_type="plain", invert=False)` builds a `tsquery`:

| `search_type` | Function emitted | Use for |
|---------------|------------------|---------|
| `"plain"` (default) | `PLAINTO_TSQUERY` | ordinary user input, `&`/`|`/`!` read as operators |
| `"phrase"` | `PHRASETO_TSQUERY` | adjacency matters |
| `"raw"` | `TO_TSQUERY` | input you have already sanitised as tsquery syntax |
| `"websearch"` | `WEBSEARCH_TO_TSQUERY` | search-engine syntax (`-word`, quoted phrases) |

Combination is operator-driven and emits the tsquery operators:

```python
SearchQuery("hello") & SearchQuery("world")   # (PLAINTO_TSQUERY($1) && PLAINTO_TSQUERY($2))
SearchQuery("brown") | SearchQuery("other")   # (PLAINTO_TSQUERY($1) || PLAINTO_TSQUERY($2))
~SearchQuery("hello")                          # !!(PLAINTO_TSQUERY($1))
```

`Lexeme` is the structured alternative for building a raw tsquery by hand:
`Lexeme("quick", prefix=True) & ~Lexeme("slow")` — `prefix=True` appends `*`, `weight="A"` appends
the weight label, and a `SearchQuery` built from a `Lexeme` combination switches itself to
`search_type="raw"`.

### Filtering on the column

The field exposes one path onto the `@@` operator:

```python
rows = await Doc.filter(search__search=SearchQuery("brown")).values_list("title", flat=True)
# WHERE "search" @@ PLAINTO_TSQUERY($1)
```

`Doc.filter(search=SearchQuery("brown"))` — without the extra `__search` — emits
`tsvector = tsquery` and fails with `operator does not exist: tsvector = tsquery`.

## Ranking

```python
SearchRank(vector, query, weights=None, normalization=None, cover_density=False)
```

`cover_density=True` switches `TS_RANK` to `TS_RANK_CD`; `normalization` is passed through as the
integer bitmask PostgreSQL expects (e.g. `32` divides by rank + 1).

Over a **stored** column the vector argument must be a term, not a column name:

```python
from tortoise.expressions import F

rows = (
    await Doc.all()
    .annotate(rank=SearchRank(F("search"), SearchQuery("hello")))
    .filter(rank__gt=0)
    .order_by("-rank", "title")
    .values("title", "rank")
)
# TS_RANK("search",PLAINTO_TSQUERY($1)) "rank" ... WHERE TS_RANK(...) > $3 ORDER BY "rank" DESC,"title" ASC
```

`F()` (or a `pypika_tortoise.terms.Field`) is what makes this work; passing the string `"search"`
wraps it in `SearchVector` instead and produces `TO_TSVECTOR("search")`, which PostgreSQL rejects —
see the traps.

Over **text** columns, pass a `SearchVector`:

```python
SearchRank(SearchVector("title", "body", config="english"), SearchQuery("hello"))
# TS_RANK(TO_TSVECTOR($1,(("title" || $2) || "body")),PLAINTO_TSQUERY($3))
```

`SearchVector(..., weight="A")` looks attractive here and does **not** work through asyncpg — see
the traps. Use `TSVectorField(..., weights=[...])` for weighting.

## Headlines

`SearchHeadline(field, query, config=None, start_sel=..., stop_sel=..., max_words=...,
min_words=..., short_word=..., highlight_all=..., max_fragments=..., fragment_delimiter=...)`
emits `TS_HEADLINE` and returns a `TextField` annotation:

```python
q = (
    Doc.all()
    .annotate(headline=SearchHeadline(
        "body", SearchQuery("brown"), config="english",
        start_sel="<b>", stop_sel="</b>", max_words=8,
    ))
    .values("title", "headline")
)
```

## Traps, all measured

1. **`TSVectorField` without `config=` fails at DDL time.** One-argument `to_tsvector(text)` is
   `STABLE`, not `IMMUTABLE`, and PostgreSQL refuses it in a generation expression:
   `InvalidObjectDefinitionError: generation expression is not immutable`, surfaced through Tortoise
   as `OperationalError`. Always pass `config=` when `stored=True`.
2. **`SearchRank("search", ...)` on a stored column is a type error, not a ranking error.** The
   string is wrapped as `SearchVector("search")` → `TS_RANK(TO_TSVECTOR("search"), ...)` →
   `function to_tsvector(tsvector) does not exist`. Pass `F("search")` or a pypika `Field`.
3. **`SearchVector(..., weight="A")` fails at bind time.** The weight is bound as a parameter whose
   type asyncpg infers as `tsvector`, so `'A'` arrives as a Python `str`:
   `invalid input for query argument $2: 'A' (a bytes-like object is required, not 'str')`. Weights
   belong on `TSVectorField(..., weights=[...])`, where they are inlined into the DDL.
4. **`ts_rank` is not a match indicator for a negated query.** `~SearchQuery("hello")` matches the
   rows that do *not* contain "hello", but `ts_rank` scores those rows `0` and scores the rows it
   does not match — verified: `!hello` gave `matches=true / rank=0.0000` on the row without the term
   and `matches=false / rank=0.0608` on the rows with it. Filter negated queries with
   `filter(search__search=~SearchQuery(...))`, never with `rank__gt=0`.

## What this package adds

Nothing, deliberately. `HybridSearch` keeps its own `ts_rank_cd` expression because it has to
combine a text score with a vector distance in one statement; a `QuerySet` convenience wrapper over
the annotate/filter pair would need a patch site and its own tests for no capability gain. The
`SearchVectorField` item on the roadmap was re-scoped to this page for that reason.
