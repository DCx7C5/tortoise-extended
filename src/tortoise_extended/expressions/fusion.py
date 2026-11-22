"""Fusion utilities: rank fusion, score fusion, and MMR diversity re-ranking.

Three complementary post-processing steps for a retrieval pipeline, plus two
adapters that connect the DB-facing search APIs to them without re-querying
anything:

* :func:`reciprocal_rank_fusion` — merge *rankings* (order only).
* :func:`weighted_score_fusion` — merge *score lists* after per-list
  normalization.
* :func:`diversity_rerank` — greedy Maximal Marginal Relevance re-ranking of an
  already-retrieved candidate set.
* :func:`ranking_from_hits` — ``GraphVectorSearch`` hits -> a ranking.
* :func:`scores_from_hybrid_rows` — ``HybridSearch`` rows -> ``(id, score)``.

Score-vs-distance asymmetry
---------------------------
:class:`~tortoise_extended.expressions.graph_vector_search.GraphVectorHit`
exposes ``distance`` only, and that value carries **no** metric tag: the three
pgvector metrics live on incompatible scales — ``l2`` is unbounded, ``cosine``
distance is at most 2, and ``inner_product`` is a positive inner product
(larger = more similar). Converting a hit distance into a comparable score is
therefore ambiguous, which is why there is deliberately **no**
``scores_from_hits`` adapter here. Callers that want weighted score fusion from
a vector search must normalize the distance themselves (per metric) or use
``HybridSearch``'s ``combined_score``, which is already higher-is-better.

Everything in this module is pure Python: no database access, no SQL, no I/O,
no new dependencies.

Usage::

    from tortoise_extended import (
        diversity_rerank,
        ranking_from_hits,
        reciprocal_rank_fusion,
        scores_from_hybrid_rows,
        weighted_score_fusion,
    )

    # 1. RRF — two retrievers, order only (k defaults to 60.0).
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "c", "a"]])
    # -> [('b', 0.03252...), ('a', 0.03226...), ('c', 0.03200...)]

    # 2. Weighted score fusion — min-max each list, then a weighted sum.
    fused = weighted_score_fusion(
        [[("a", 0.9), ("b", 0.4)], [("b", 10.0), ("a", 2.0)]],
        weights=[0.7, 0.3],
    )

    # 3. MMR diversity re-ranking over an already-retrieved candidate set.
    reranked = diversity_rerank(
        ["doc-1", "doc-2", "doc-3"],
        relevance=lambda doc: relevance_scores[doc],
        similarity=lambda left, right: similarity_matrix[left, right],
        lambda_=0.7,
        max_results=2,
    )

    # 4. GraphVectorSearch hits -> ranking (ids in hit order).
    hits = await GraphVectorSearch(...).search()
    ranking = ranking_from_hits(hits)

    # 5. HybridSearch rows -> (id, combined_score) pairs.
    rows = await HybridSearch(...).search(query_vector=..., query_text=...)
    scores = scores_from_hybrid_rows(rows)
"""

import statistics
from collections.abc import Callable, Hashable, Sequence
from typing import Literal, cast

from tortoise.models import Model

from tortoise_extended._types import RowMapping, RowValue
from tortoise_extended.expressions.graph_vector_search import GraphVectorHit

__all__ = [
    "diversity_rerank",
    "ranking_from_hits",
    "reciprocal_rank_fusion",
    "scores_from_hybrid_rows",
    "weighted_score_fusion",
]


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[Hashable]], *, k: float = 60.0
) -> list[tuple[Hashable, float]]:
    """Fuse rankings with Reciprocal Rank Fusion (RRF).

    ``score(d) = sum(1 / (k + rank_i(d)))`` over every ranking *i* that
    contains *d*, with **1-based** ranks (Cormack, Clarke & Buettcher, 2009).
    A document absent from a ranking contributes nothing for that ranking —
    its own score is never consulted, so rankings produced by incomparable
    metrics can be fused directly.

    Each input list is treated as a ranking: a document repeated inside one
    list is counted once, at its first (best) rank.

    :param rankings: Rankings to fuse, best first.
    :param k: RRF smoothing constant (default ``60.0``, the value from the
        original paper). Larger values flatten the influence of the top ranks.
    :returns: ``(document, score)`` pairs sorted by descending score. Ties
        break by first appearance across the concatenated rankings, so the
        result is deterministic.
    :raises ValueError: If *k* is not positive (the weight would divide by
        zero or invert the ranking).

    Cost: ``O(total entries + d log d)`` for ``d`` distinct documents.
    """
    if k <= 0:
        raise ValueError(
            f"k must be positive (got {k!r}); the RRF weight is 1 / (k + rank)"
        )
    scores: dict[Hashable, float] = {}
    first_seen: dict[Hashable, int] = {}
    for ranking in rankings:
        seen_here: set[Hashable] = set()
        for rank, document in enumerate(ranking, start=1):
            if document in seen_here:
                continue
            seen_here.add(document)
            if document not in first_seen:
                first_seen[document] = len(first_seen)
            scores[document] = scores.get(document, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda entry: (-entry[1], first_seen[entry[0]]))


def weighted_score_fusion(
    score_lists: Sequence[Sequence[tuple[Hashable, float]]],
    *,
    weights: Sequence[float] | None = None,
    normalization: Literal["minmax", "zscore", "none"] = "minmax",
) -> list[tuple[Hashable, float]]:
    """Fuse score lists with per-list weights and optional normalization.

    Each list is normalized independently, multiplied by its weight, and
    summed per document; a document missing from a list contributes 0 for that
    list. Ties break by first appearance across the concatenated lists.

    Normalization modes:

    * ``"minmax"`` — ``(s - min) / (max - min)`` per list. A list whose values
      are all equal (including a single-entry list) maps every entry to
      ``0.0`` instead of dividing by zero.
    * ``"zscore"`` — ``(s - mean) / pstdev`` per list, using
      :func:`statistics.fmean` / :func:`statistics.pstdev`. A zero-variance
      list maps every entry to ``0.0`` instead of dividing by zero.
    * ``"none"`` — raw values, summed as-is. Only meaningful when the input
      scales are already comparable (e.g. several ``HybridSearch``
      ``combined_score`` lists); mixing an unbounded L2 distance with a
      bounded cosine distance here is a bug, not a fusion.

    :param score_lists: One ``(document, score)`` sequence per retriever,
        higher score = better. A document repeated inside one list is counted
        once, at its first occurrence.
    :param weights: Per-list weights, positionally aligned with *score_lists*.
        ``None`` (default) uses equal weights of ``1 / len(score_lists)``.
    :param normalization: ``"minmax"`` (default), ``"zscore"``, or ``"none"``.
    :returns: ``(document, score)`` pairs sorted by descending fused score;
        ``[]`` when *score_lists* is empty.
    :raises ValueError: If ``len(weights) != len(score_lists)`` or
        *normalization* is not one of the three modes.
    """
    mode = _validate_normalization(normalization)
    if weights is not None and len(weights) != len(score_lists):
        raise ValueError(
            f"weights has {len(weights)} entries but score_lists has "
            f"{len(score_lists)}; they must align positionally"
        )
    if not score_lists:
        return []
    if weights is None:
        effective_weights = [1.0 / len(score_lists)] * len(score_lists)
    else:
        effective_weights = list(weights)
    fused: dict[Hashable, float] = {}
    first_seen: dict[Hashable, int] = {}
    for weight, score_list in zip(effective_weights, score_lists, strict=True):
        for document, score in _normalize_score_list(score_list, mode):
            if document not in first_seen:
                first_seen[document] = len(first_seen)
            fused[document] = fused.get(document, 0.0) + weight * score
    return sorted(fused.items(), key=lambda entry: (-entry[1], first_seen[entry[0]]))


def diversity_rerank[T](
    items: Sequence[T],
    *,
    relevance: Callable[[T], float],
    similarity: Callable[[T, T], float],
    key: Callable[[T], Hashable] | None = None,
    lambda_: float = 0.5,
    max_results: int | None = None,
) -> list[T]:
    """Greedy Maximal Marginal Relevance (MMR) re-ranking.

    Repeatedly selects the remaining candidate maximizing

    ``lambda_ * relevance(d) - (1 - lambda_) * max(similarity(d, s) for s in selected)``

    The first pick has no selected set, so it is the maximum-relevance item.
    ``lambda_ = 1.0`` is pure relevance order; ``lambda_ = 0.0`` maximizes
    dissimilarity from the selected set (the first pick is still the
    max-relevance item, since there is nothing to be dissimilar to).

    This re-ranks an **already-retrieved** set — it is not a search: every
    candidate must already be in *items*. *similarity* is supplied by the
    caller (embeddings, Jaccard, tag overlap, ...); this module adds no
    embedding dependency.

    Cost: ``O(n^2 * k)`` similarity evaluations for ``n`` candidates and ``k``
    selections (each of the ``k`` rounds scores up to ``n`` candidates against
    up to ``k`` already-selected items). Keep *items* to the top of a
    retrieval funnel (tens to a few hundred), not the whole corpus.

    :param items: Candidate items, in the retriever's own order — that order is
        the deterministic tie-break.
    :param relevance: First-stage relevance of one item, higher = better.
    :param similarity: Pairwise similarity (higher = more redundant).
    :param key: Identity function used to deduplicate. Items with equal keys
        are deduplicated keeping the first. ``None`` (default) uses the item
        itself, which must then be hashable.
    :param lambda_: Relevance/diversity trade-off in ``[0, 1]``; ``0.5`` is
        balanced, ``1.0`` is pure relevance, ``0.0`` is pure diversity.
    :param max_results: Maximum number of items to return; ``None`` (default)
        returns the full re-ordering.
    :returns: The re-ranked items (deduplicated), at most *max_results* long.
    :raises ValueError: If *lambda_* is outside ``[0, 1]`` or *max_results* is
        negative.
    :raises TypeError: If *key* is ``None`` and an item is not hashable.
    """
    if not 0.0 <= lambda_ <= 1.0:
        raise ValueError(f"lambda_ must be in [0, 1] (got {lambda_!r})")
    if max_results is not None and max_results < 0:
        raise ValueError(f"max_results must be >= 0 or None (got {max_results!r})")

    def key_of(item: T) -> Hashable:
        """Dedupe key: the caller's *key*, else the item itself."""
        if key is not None:
            return key(item)
        if isinstance(item, Hashable):
            return item
        raise TypeError(
            f"diversity_rerank needs hashable items or an explicit key= "
            f"callable; got {type(item).__name__}"
        )

    candidates: list[T] = []
    seen: set[Hashable] = set()
    for item in items:
        item_key = key_of(item)
        if item_key in seen:
            continue
        seen.add(item_key)
        candidates.append(item)
    limit = (
        len(candidates) if max_results is None else min(max_results, len(candidates))
    )
    if limit == 0:
        return []
    relevance_of = [relevance(item) for item in candidates]
    remaining = list(range(len(candidates)))
    selected: list[int] = []
    while len(selected) < limit:
        best_index = -1
        best_value = float("-inf")
        for index in remaining:
            if selected:
                redundancy = max(
                    similarity(candidates[index], candidates[chosen])
                    for chosen in selected
                )
                value = lambda_ * relevance_of[index] - (1.0 - lambda_) * redundancy
            else:
                # First pick: no selected set yet, so the penalty term is
                # undefined and the pick is the unscaled max-relevance item.
                value = relevance_of[index]
            if value > best_value:
                best_value = value
                best_index = index
        if best_index < 0:
            break
        selected.append(best_index)
        remaining.remove(best_index)
    return [candidates[index] for index in selected]


def ranking_from_hits[ModelT: Model](
    hits: Sequence[GraphVectorHit[ModelT]],
    *,
    key: Callable[[GraphVectorHit[ModelT]], Hashable] | None = None,
) -> list[Hashable]:
    """Extract a ranking (ordered document ids) from graph-vector hits.

    Returns ids in hit order — ``GraphVectorSearch.search()`` already orders
    hits best-first — which is all :func:`reciprocal_rank_fusion` needs. It is
    deliberately metric-agnostic: no distance is read, so ``l2``, ``cosine``
    and ``inner_product`` hits all produce a valid ranking.

    :param hits: Hits returned by ``GraphVectorSearch.search()``.
    :param key: Extracts the document id from one hit. ``None`` (default) uses
        ``hit.node.pk``.
    :returns: The ids, in hit order.
    """
    if key is None:
        return [cast(Hashable, hit.node.pk) for hit in hits]
    return [key(hit) for hit in hits]


def scores_from_hybrid_rows(
    rows: Sequence[RowMapping],
    *,
    id_key: str = "id",
    score_key: str = "combined_score",
) -> list[tuple[Hashable, float]]:
    """Extract ``(id, score)`` pairs from ``HybridSearch`` result rows.

    ``HybridSearch.search()`` returns :data:`~tortoise_extended._types.RowMapping`
    rows whose metadata keys are ``distance``, ``text_score`` and
    ``combined_score``. ``combined_score`` is already higher-is-better, so it
    feeds :func:`weighted_score_fusion` directly (with ``"none"`` when every
    input shares that scale, or a normalizing mode when mixing retrievers).

    :param rows: Rows returned by ``HybridSearch.search()``.
    :param id_key: Row key holding the document id (default ``"id"``).
    :param score_key: Row key holding the score (default ``"combined_score"``).
    :returns: ``(id, score)`` pairs in row order.
    :raises KeyError: If a row lacks *id_key* or *score_key*; the message names
        the missing key and lists the available ones.
    :raises TypeError: If the score value is not an ``int`` or ``float``.
    """
    return [(_row_value(row, id_key), _row_float(row, score_key)) for row in rows]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_normalization(normalization: str) -> str:
    """Validate the normalization mode, returning it as a plain ``str``.

    The public signature narrows the parameter to a ``Literal``; widening it
    here keeps the runtime guard reachable instead of statically dead.
    """
    if normalization not in ("minmax", "zscore", "none"):
        raise ValueError(
            f"Unsupported normalization {normalization!r}; "
            f"expected 'minmax', 'zscore', or 'none'"
        )
    return normalization


def _normalize_score_list(
    score_list: Sequence[tuple[Hashable, float]], mode: str
) -> list[tuple[Hashable, float]]:
    """Deduplicate one score list and normalize it in place-free fashion.

    *mode* is already validated by :func:`weighted_score_fusion`; ``"minmax"``
    and ``"zscore"`` map a zero-variance list to all-zero scores.
    """
    deduped: list[tuple[Hashable, float]] = []
    seen: set[Hashable] = set()
    for document, score in score_list:
        if document in seen:
            continue
        seen.add(document)
        deduped.append((document, score))
    if mode == "none" or not deduped:
        return deduped
    values = [score for _, score in deduped]
    if mode == "minmax":
        low = min(values)
        span = max(values) - low
        if span == 0.0:
            return [(document, 0.0) for document, _ in deduped]
        return [(document, (score - low) / span) for document, score in deduped]
    mean = statistics.fmean(values)
    deviation = statistics.pstdev(values)
    if deviation == 0.0:
        return [(document, 0.0) for document, _ in deduped]
    return [(document, (score - mean) / deviation) for document, score in deduped]


def _row_value(row: RowMapping, key: str) -> RowValue:
    """Return ``row[key]``, raising a self-describing ``KeyError`` when absent."""
    if key not in row:
        available = ", ".join(sorted(row)) or "<empty row>"
        raise KeyError(
            f"row is missing required key {key!r}; available keys: {available}"
        )
    return row[key]


def _row_float(row: RowMapping, key: str) -> float:
    """Return ``row[key]`` as a ``float``, rejecting non-numeric values."""
    value = _row_value(row, key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(
            f"row value for {key!r} must be int or float, got {type(value).__name__}"
        )
    return float(value)
