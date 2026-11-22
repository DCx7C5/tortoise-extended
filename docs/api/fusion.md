# Fusion

Rank fusion, weighted score fusion, and MMR diversity re-ranking — pure Python
post-processing for retrieval pipelines. No database, no SQL, no extra
dependencies.

## Import

```python
from tortoise_extended import (
    diversity_rerank,
    ranking_from_hits,
    reciprocal_rank_fusion,
    scores_from_hybrid_rows,
    weighted_score_fusion,
)
```

---

## reciprocal_rank_fusion

```python
reciprocal_rank_fusion(
    rankings: Sequence[Sequence[Hashable]],
    *,
    k: float = 60.0,
) -> list[tuple[Hashable, float]]
```

Fuse rankings with Reciprocal Rank Fusion:
`score(d) = Σ 1 / (k + rank(d))`, ranks **1-based** (Cormack, Clarke &
Büttcher). Only order matters — the documents' own scores are never consulted,
so rankings produced by incomparable metrics fuse directly. A document absent
from a ranking contributes nothing for it; ties break by first appearance
across the concatenated rankings.

```python
from tortoise_extended import reciprocal_rank_fusion

fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "c", "a"]])
# -> [('b', 0.03252...), ('a', 0.03226...), ('c', 0.03200...)]
```

Raises `ValueError` when `k <= 0`.

---

## weighted_score_fusion

```python
weighted_score_fusion(
    score_lists: Sequence[Sequence[tuple[Hashable, float]]],
    *,
    weights: Sequence[float] | None = None,
    normalization: Literal["minmax", "zscore", "none"] = "minmax",
) -> list[tuple[Hashable, float]]
```

Normalize each list, multiply by its weight, and sum per document. A document
missing from a list contributes 0 for that list; ties break by first
appearance.

| Mode | Per-list transform | Zero-variance list |
|------|--------------------|--------------------|
| `"minmax"` (default) | `(s - min) / (max - min)` | every entry `0.0` |
| `"zscore"` | `(s - mean) / pstdev` (`statistics.fmean` / `statistics.pstdev`) | every entry `0.0` |
| `"none"` | raw values | n/a |

`"none"` is only meaningful when the input scales are already comparable (e.g.
several `HybridSearch` `combined_score` lists). `weights=None` uses equal
weights of `1 / len(score_lists)`.

```python
from tortoise_extended import weighted_score_fusion

fused = weighted_score_fusion(
    [[("a", 0.9), ("b", 0.6)], [("b", 4.0), ("a", 0.0)]],
    weights=[0.7, 0.3],
)
# -> [('a', 0.7), ('b', 0.3)]  # min-max per list, then the weighted sum
```

Raises `ValueError` when `len(weights) != len(score_lists)` (checked even for an
empty `score_lists`) or when `normalization` is not one of the three modes.

---

## diversity_rerank

```python
diversity_rerank[T](
    items: Sequence[T],
    *,
    relevance: Callable[[T], float],
    similarity: Callable[[T, T], float],
    key: Callable[[T], Hashable] | None = None,
    lambda_: float = 0.5,
    max_results: int | None = None,
) -> list[T]
```

Greedy Maximal Marginal Relevance: repeatedly pick the candidate maximizing

```
lambda_ * relevance(d) - (1 - lambda_) * max(similarity(d, s) for s in selected)
```

The first pick has no selected set, so it is the maximum-relevance item.
`lambda_ = 1.0` is pure relevance order; `lambda_ = 0.0` maximizes
dissimilarity from the selected set. Ties break by original input order. Items
with equal `key` values (default: identity, which requires hashable items) are
deduplicated keeping the first.

This re-ranks an **already-retrieved** set — it is not a search. `similarity`
is caller-supplied (embeddings, Jaccard, tag overlap, ...), so no embedding
dependency is added. Cost is `O(n² · k)` similarity evaluations for `n`
candidates and `k` selections: keep `items` to the top of a retrieval funnel,
not the whole corpus.

```python
from tortoise_extended import diversity_rerank

reranked = diversity_rerank(
    ["doc-1", "doc-2", "doc-3"],
    relevance=lambda doc: relevance_scores[doc],
    similarity=lambda left, right: similarity_matrix[left, right],
    lambda_=0.7,
    max_results=2,
)
```

Raises `ValueError` when `lambda_` is outside `[0, 1]` or `max_results` is
negative, and `TypeError` when the default identity key meets an unhashable
item.

---

## ranking_from_hits

```python
ranking_from_hits[ModelT](
    hits: Sequence[GraphVectorHit[ModelT]],
    *,
    key: Callable[[GraphVectorHit[ModelT]], Hashable] | None = None,
) -> list[Hashable]
```

Extract ids from `GraphVectorSearch.search()` hits **in hit order** (already
best-first). RRF needs order only, so this is metric-agnostic: no distance is
read, and `l2`, `cosine` and `inner_product` hits all produce a valid ranking.
The default key is `hit.node.pk`.

```python
from tortoise_extended import GraphVectorSearch, ranking_from_hits

hits = await GraphVectorSearch(...).search()
ranking = ranking_from_hits(hits)
```

---

## scores_from_hybrid_rows

```python
scores_from_hybrid_rows(
    rows: Sequence[RowMapping],
    *,
    id_key: str = "id",
    score_key: str = "combined_score",
) -> list[tuple[Hashable, float]]
```

Extract `(id, score)` pairs from `HybridSearch.search()` rows. Those rows carry
`distance`, `text_score` and `combined_score`; `combined_score` is already
higher-is-better, so it feeds `weighted_score_fusion` directly. A missing key
raises `KeyError` naming the key and listing the available ones; a non-numeric
score raises `TypeError`.

```python
from tortoise_extended import HybridSearch, scores_from_hybrid_rows

rows = await HybridSearch(...).search(query_vector=..., query_text=...)
scores = scores_from_hybrid_rows(rows)  # [(id, combined_score), ...]
```

---

## Why there is no `scores_from_hits`

`GraphVectorHit.distance` carries **no metric tag**, and the three pgvector
metrics live on incompatible scales:

| Metric | `distance` meaning | Range / direction |
|--------|--------------------|-------------------|
| `l2` | Euclidean distance | `[0, ∞)`, smaller = better |
| `cosine` | cosine distance | `[0, 2]`, smaller = better |
| `inner_product` | positive inner product | `(-∞, ∞)`, larger = better |

Converting a hit distance into a comparable score is therefore ambiguous — any
built-in conversion would silently pick a metric. That is deliberately why this
module ships `ranking_from_hits` (order only) and **not** `scores_from_hits`.
Callers wanting weighted score fusion from a vector search must normalize the
distance themselves (per metric), or use `HybridSearch`'s `combined_score`.

## Notes

- All functions are pure: no DB access, no SQL, no I/O.
- `reciprocal_rank_fusion` and `weighted_score_fusion` return descending-score
  lists with a deterministic first-appearance tie-break.
- Duplicate documents inside a single ranking / score list are counted once, at
  their first occurrence.
