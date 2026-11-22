"""Unit tests for the pure fusion helpers in ``tortoise_extended.expressions.fusion``.

Covers Reciprocal Rank Fusion, weighted score fusion (min-max / z-score / raw),
MMR diversity re-ranking, and the two adapters that lift ``GraphVectorSearch``
hits and ``HybridSearch`` rows into fusion inputs. No database, no SQL — every
function under test is pure.
"""

import pytest

from tortoise_extended.expressions.fusion import (
    diversity_rerank,
    ranking_from_hits,
    reciprocal_rank_fusion,
    scores_from_hybrid_rows,
    weighted_score_fusion,
)
from tortoise_extended.expressions.graph_vector_search import GraphVectorHit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeNode:
    """Minimal stand-in for a hydrated Tortoise model (only ``pk`` is read)."""

    def __init__(self, pk: int) -> None:
        self.pk = pk


def _hit(pk: int, *, distance: float = 0.0, hops: int = 0) -> GraphVectorHit[_FakeNode]:
    """Build a ``GraphVectorHit`` around a fake node with the given pk."""
    return GraphVectorHit(node=_FakeNode(pk), distance=distance, hops=hops)


# ---------------------------------------------------------------------------
# reciprocal_rank_fusion
# ---------------------------------------------------------------------------


class TestReciprocalRankFusion:
    """RRF: score(d) = sum(1 / (k + rank(d))), 1-based ranks."""

    def test_identical_rankings_match_single_ranking_order(self) -> None:
        """Fusing a ranking with itself keeps that ranking's order."""
        single = reciprocal_rank_fusion([["a", "b", "c"]])
        doubled = reciprocal_rank_fusion([["a", "b", "c"], ["a", "b", "c"]])
        assert [doc for doc, _ in doubled] == [doc for doc, _ in single]
        assert [doc for doc, _ in doubled] == ["a", "b", "c"]
        assert [score for _, score in doubled] == pytest.approx(
            [2.0 * score for _, score in single]
        )

    def test_document_in_both_lists_outranks_single_list_document(self) -> None:
        """Two weak appearances beat one stronger appearance."""
        fused = reciprocal_rank_fusion([["both", "only_first"], ["both"]])
        assert [doc for doc, _ in fused] == ["both", "only_first"]
        assert fused[0][1] == pytest.approx(2 / 61)
        assert fused[1][1] == pytest.approx(1 / 62)

    def test_scores_follow_the_rrf_formula(self) -> None:
        """Exact 1-based RRF weights for a two-ranking example."""
        fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "c", "a"]], k=60.0)
        scores = dict(fused)
        assert scores["a"] == pytest.approx(1 / 61 + 1 / 63)
        assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)
        assert scores["c"] == pytest.approx(1 / 63 + 1 / 62)
        assert [doc for doc, _ in fused] == ["b", "a", "c"]

    def test_absent_document_contributes_zero(self) -> None:
        """A document missing from a ranking gains nothing from it."""
        fused = reciprocal_rank_fusion([["a"], ["b"]])
        scores = dict(fused)
        assert scores["a"] == pytest.approx(1 / 61)
        assert scores["b"] == pytest.approx(1 / 61)

    def test_tie_break_by_first_appearance(self) -> None:
        """Equal scores order by first appearance across the rankings."""
        fused = reciprocal_rank_fusion([["z"], ["y"], ["x"]])
        assert [doc for doc, _ in fused] == ["z", "y", "x"]
        assert fused[0][1] == pytest.approx(fused[1][1])
        assert fused[1][1] == pytest.approx(fused[2][1])

    def test_duplicate_within_one_ranking_counts_once(self) -> None:
        """A repeated document keeps its first rank and does not shift later ones."""
        fused = reciprocal_rank_fusion([["a", "a", "b"]])
        scores = dict(fused)
        assert scores["a"] == pytest.approx(1 / 61)
        assert scores["b"] == pytest.approx(1 / 63)

    def test_empty_inputs(self) -> None:
        """No rankings (or only empty rankings) fuse to an empty list."""
        assert reciprocal_rank_fusion([]) == []
        assert reciprocal_rank_fusion([[], []]) == []

    def test_non_positive_k_raises(self) -> None:
        """k must be positive — the weight is 1 / (k + rank)."""
        for bad_k in (0.0, -1.0):
            with pytest.raises(ValueError, match="k must be positive"):
                reciprocal_rank_fusion([["a"]], k=bad_k)


# ---------------------------------------------------------------------------
# weighted_score_fusion
# ---------------------------------------------------------------------------


class TestWeightedScoreFusion:
    """Per-list normalization, weighting, and summation."""

    def test_equal_weights_minmax_equals_mean_of_normalized_scores(self) -> None:
        """Default weights (1/n) with min-max == the mean of normalized scores."""
        score_lists = [
            [("a", 0.9), ("b", 0.6), ("c", 0.3)],
            [("b", 4.0), ("c", 2.0), ("a", 0.0)],
        ]
        # List 1 min-max: a=1.0, b=0.5, c=0.0
        # List 2 min-max: b=1.0, c=0.5, a=0.0
        # Mean per document: a=(1.0+0.0)/2, b=(0.5+1.0)/2, c=(0.0+0.5)/2
        fused = weighted_score_fusion(score_lists)
        assert [doc for doc, _ in fused] == ["b", "a", "c"]
        assert dict(fused) == pytest.approx({"a": 0.5, "b": 0.75, "c": 0.25})

        explicit = weighted_score_fusion(score_lists, weights=[0.5, 0.5])
        assert [doc for doc, _ in explicit] == ["b", "a", "c"]
        for (doc_a, score_a), (doc_b, score_b) in zip(fused, explicit, strict=True):
            assert doc_a == doc_b
            assert score_a == pytest.approx(score_b)

    def test_zscore_zero_variance_list_yields_zeros(self) -> None:
        """A constant list maps every entry to 0.0 — no ZeroDivisionError."""
        fused = weighted_score_fusion(
            [[("a", 7.0), ("b", 7.0)], [("a", 1.0), ("b", 3.0)]],
            normalization="zscore",
        )
        # List 1: zero variance -> both 0.0. List 2: mean 2, pstdev 1 -> a=-1, b=1.
        assert dict(fused) == pytest.approx({"a": -0.5, "b": 0.5})
        assert [doc for doc, _ in fused] == ["b", "a"]

    def test_zscore_single_list_uses_population_stdev(self) -> None:
        """z-scores come from statistics.fmean / statistics.pstdev."""
        fused = weighted_score_fusion(
            [[("low", 1.0), ("high", 3.0)]], normalization="zscore"
        )
        assert dict(fused) == pytest.approx({"low": -1.0, "high": 1.0})

    def test_zscore_single_value_list_yields_zero(self) -> None:
        """A one-entry list has zero variance -> 0.0."""
        fused = weighted_score_fusion([[("a", 5.0)]], normalization="zscore")
        assert fused == [("a", 0.0)]

    def test_weights_checked_even_when_score_lists_is_empty(self) -> None:
        """An inconsistent weights argument is a bug even with no lists."""
        with pytest.raises(ValueError, match="weights has 1 entries"):
            weighted_score_fusion([], weights=[1.0])

    def test_mismatched_weights_raise(self) -> None:
        """Weights must align positionally with the score lists."""
        with pytest.raises(ValueError, match="weights has 1 entries"):
            weighted_score_fusion([[("a", 1.0)], [("b", 2.0)]], weights=[1.0])

    def test_minmax_zero_span_maps_to_zero(self) -> None:
        """max == min maps every entry to 0.0 instead of dividing by zero."""
        fused = weighted_score_fusion([[("a", 3.0), ("b", 3.0)]])
        assert fused == [("a", 0.0), ("b", 0.0)]

    def test_none_normalization_sums_raw_values(self) -> None:
        """'none' keeps raw values; weights of 1.0 give a plain sum."""
        fused = weighted_score_fusion(
            [[("a", 1.0), ("b", 2.0)], [("a", 10.0)]],
            weights=[1.0, 1.0],
            normalization="none",
        )
        assert dict(fused) == pytest.approx({"a": 11.0, "b": 2.0})
        assert [doc for doc, _ in fused] == ["a", "b"]

    def test_missing_document_contributes_zero(self) -> None:
        """A document absent from a list scores 0 for that list."""
        fused = weighted_score_fusion(
            [[("a", 1.0), ("b", 1.0)], [("a", 1.0)]],
            weights=[1.0, 1.0],
            normalization="none",
        )
        assert dict(fused) == pytest.approx({"a": 2.0, "b": 1.0})

    def test_duplicate_document_in_one_list_keeps_first_occurrence(self) -> None:
        """Repeats inside one list are counted once, at the first value."""
        fused = weighted_score_fusion([[("a", 1.0), ("a", 99.0)]], normalization="none")
        assert fused == [("a", 1.0)]

    def test_empty_score_lists(self) -> None:
        """No lists (or only empty lists) fuse to an empty list."""
        assert weighted_score_fusion([]) == []
        assert weighted_score_fusion([[]]) == []

    def test_invalid_normalization_raises(self) -> None:
        """Unknown normalization modes are rejected at runtime."""
        with pytest.raises(ValueError, match="Unsupported normalization"):
            weighted_score_fusion([[("a", 1.0)]], normalization="bogus")


# ---------------------------------------------------------------------------
# diversity_rerank
# ---------------------------------------------------------------------------


def _zero_similarity(_left: str, _right: str) -> float:
    """Similarity stub: everything is equally (dis)similar."""
    return 0.0


class TestDiversityRerank:
    """Greedy MMR: lambda_ * relevance - (1 - lambda_) * max similarity."""

    def test_lambda_one_is_pure_relevance_order(self) -> None:
        """lambda_=1.0 with a flat similarity is the relevance ordering."""
        relevance_scores = {"a": 0.1, "b": 0.9, "c": 0.5}
        reranked = diversity_rerank(
            ["a", "b", "c"],
            relevance=lambda item: relevance_scores[item],
            similarity=_zero_similarity,
            lambda_=1.0,
        )
        assert reranked == ["b", "c", "a"]

    def test_lambda_one_ties_keep_input_order(self) -> None:
        """Equal relevance preserves the original order (deterministic)."""
        reranked = diversity_rerank(
            ["a", "b", "c"],
            relevance=lambda _item: 0.0,
            similarity=_zero_similarity,
            lambda_=1.0,
        )
        assert reranked == ["a", "b", "c"]

    def test_lambda_zero_maximizes_dissimilarity(self) -> None:
        """lambda_=0.0 pushes the near-duplicate of the first pick to the end."""
        similarity_scores = {
            ("a", "b"): 1.0,
            ("a", "c"): 0.0,
            ("b", "a"): 1.0,
            ("b", "c"): 0.0,
            ("c", "a"): 0.0,
            ("c", "b"): 0.0,
        }
        reranked = diversity_rerank(
            ["a", "b", "c"],
            relevance=lambda _item: 0.0,
            similarity=lambda left, right: similarity_scores[left, right],
            lambda_=0.0,
        )
        # First pick: max relevance, tie -> "a". Then "c" (sim 0.0) beats
        # "b" (sim 1.0, fully redundant with "a").
        assert reranked == ["a", "c", "b"]

    def test_lambda_zero_first_pick_is_max_relevance(self) -> None:
        """With no selected set the first pick is still the best relevance."""
        relevance_scores = {"a": 0.2, "b": 0.9, "c": 0.4}
        reranked = diversity_rerank(
            ["a", "b", "c"],
            relevance=lambda item: relevance_scores[item],
            similarity=lambda left, right: 0.0 if left == right else 1.0,
            lambda_=0.0,
        )
        assert reranked[0] == "b"

    def test_lambda_zero_first_pick_is_max_relevance_with_flat_similarity(
        self,
    ) -> None:
        """Even with a flat similarity, input order must not win the first pick."""
        relevance_scores = {"a": 0.1, "b": 0.8, "c": 0.5}
        reranked = diversity_rerank(
            ["a", "b", "c"],
            relevance=lambda item: relevance_scores[item],
            similarity=_zero_similarity,
            lambda_=0.0,
        )
        assert reranked[0] == "b"
        assert reranked != ["a", "b", "c"]

    def test_balanced_lambda_trades_relevance_for_diversity(self) -> None:
        """lambda_=0.5 lets a less relevant but dissimilar item overtake."""
        relevance_scores = {"a": 1.0, "b": 0.9, "c": 0.6}
        similarity_scores = {
            ("a", "b"): 1.0,
            ("a", "c"): 0.0,
            ("b", "a"): 1.0,
            ("b", "c"): 0.0,
            ("c", "a"): 0.0,
            ("c", "b"): 0.0,
        }
        reranked = diversity_rerank(
            ["a", "b", "c"],
            relevance=lambda item: relevance_scores[item],
            similarity=lambda left, right: similarity_scores[left, right],
            lambda_=0.5,
        )
        # "a" first (1.0). Then "b": 0.5*0.9 - 0.5*1.0 = -0.05, "c": 0.5*0.6 = 0.3.
        assert reranked == ["a", "c", "b"]

    def test_lambda_out_of_range_raises(self) -> None:
        """lambda_ must lie in [0, 1]."""
        for bad_lambda in (-0.1, 1.1):
            with pytest.raises(ValueError, match=r"lambda_ must be in \[0, 1\]"):
                diversity_rerank(
                    ["a"],
                    relevance=lambda _item: 0.0,
                    similarity=_zero_similarity,
                    lambda_=bad_lambda,
                )

    def test_negative_max_results_raises(self) -> None:
        """A negative cap is a bug, not an empty result."""
        with pytest.raises(ValueError, match="max_results must be >= 0"):
            diversity_rerank(
                ["a"],
                relevance=lambda _item: 0.0,
                similarity=_zero_similarity,
                max_results=-1,
            )

    def test_max_results_limits_output(self) -> None:
        """max_results truncates the re-ordering, keeping the best picks."""
        relevance_scores = {"a": 0.1, "b": 0.9, "c": 0.5}
        reranked = diversity_rerank(
            ["a", "b", "c"],
            relevance=lambda item: relevance_scores[item],
            similarity=_zero_similarity,
            lambda_=1.0,
            max_results=2,
        )
        assert reranked == ["b", "c"]

    def test_max_results_none_returns_full_reordering(self) -> None:
        """The default re-orders every (deduplicated) item."""
        reranked = diversity_rerank(
            ["a", "b", "c"],
            relevance=lambda _item: 0.0,
            similarity=_zero_similarity,
            lambda_=1.0,
        )
        assert sorted(reranked) == ["a", "b", "c"]

    def test_max_results_zero_returns_empty(self) -> None:
        """A zero cap returns nothing."""
        assert (
            diversity_rerank(
                ["a"],
                relevance=lambda _item: 0.0,
                similarity=_zero_similarity,
                max_results=0,
            )
            == []
        )

    def test_deduplicates_by_key_keeping_first(self) -> None:
        """Items sharing a key collapse to their first occurrence."""
        items = [("doc-1", 1), ("doc-2", 2), ("doc-1", 3)]
        reranked = diversity_rerank(
            items,
            relevance=lambda item: float(item[1]),
            similarity=_zero_similarity,
            key=lambda item: item[0],
            lambda_=1.0,
        )
        assert reranked == [("doc-2", 2), ("doc-1", 1)]

    def test_default_key_is_identity_and_deduplicates(self) -> None:
        """With no key, equal hashable items are deduplicated."""
        reranked = diversity_rerank(
            ["a", "a", "b"],
            relevance=lambda _item: 0.0,
            similarity=_zero_similarity,
            lambda_=1.0,
        )
        assert reranked == ["a", "b"]

    def test_default_key_requires_hashable_items(self) -> None:
        """Unhashable items need an explicit key=."""
        with pytest.raises(TypeError, match="hashable"):
            diversity_rerank(
                [["a"], ["b"]],
                relevance=lambda _item: 0.0,
                similarity=_zero_similarity,
            )


# ---------------------------------------------------------------------------
# ranking_from_hits
# ---------------------------------------------------------------------------


class TestRankingFromHits:
    """GraphVectorSearch hits -> an ordered id ranking (metric-agnostic)."""

    def test_preserves_hit_order(self) -> None:
        """Ids come back exactly in hit order, not sorted by pk or distance."""
        hits = [
            _hit(30, distance=0.10),
            _hit(10, distance=0.20),
            _hit(20, distance=0.30),
        ]
        assert ranking_from_hits(hits) == [30, 10, 20]

    def test_empty_hits(self) -> None:
        """No hits -> an empty ranking."""
        assert ranking_from_hits([]) == []

    def test_custom_key(self) -> None:
        """A key callable replaces the default ``hit.node.pk``."""
        hits = [_hit(1), _hit(2)]
        assert ranking_from_hits(hits, key=lambda hit: f"node-{hit.node.pk}") == [
            "node-1",
            "node-2",
        ]

    def test_feeds_reciprocal_rank_fusion(self) -> None:
        """Hit rankings fuse with other retrievers' rankings."""
        hits = [_hit(2), _hit(1)]
        fused = reciprocal_rank_fusion([ranking_from_hits(hits), [2, 3]])
        assert [doc for doc, _ in fused] == [2, 1, 3]


# ---------------------------------------------------------------------------
# scores_from_hybrid_rows
# ---------------------------------------------------------------------------


class TestScoresFromHybridRows:
    """HybridSearch rows -> (id, combined_score) pairs."""

    def test_reads_combined_score(self) -> None:
        """``combined_score`` (higher-is-better) is the extracted score."""
        rows = [
            {"id": 7, "distance": 0.25, "text_score": 0.5, "combined_score": 0.81},
            {"id": 3, "distance": 0.30, "text_score": 0.2, "combined_score": 0.62},
        ]
        assert scores_from_hybrid_rows(rows) == [(7, 0.81), (3, 0.62)]

    def test_missing_score_key_raises_keyerror_naming_the_key(self) -> None:
        """A row without ``combined_score`` fails loudly and names the key."""
        rows = [{"id": 1, "distance": 0.1, "text_score": 0.2}]
        with pytest.raises(KeyError, match="combined_score"):
            scores_from_hybrid_rows(rows)

    def test_missing_id_key_raises_keyerror_naming_the_key(self) -> None:
        """A row without the id column fails loudly and names the key."""
        rows = [{"combined_score": 0.5}]
        with pytest.raises(KeyError, match="required key 'id'"):
            scores_from_hybrid_rows(rows)

    def test_custom_keys(self) -> None:
        """Both row keys are configurable."""
        rows = [{"node_id": 5, "score": 0.4}]
        assert scores_from_hybrid_rows(rows, id_key="node_id", score_key="score") == [
            (5, 0.4)
        ]

    def test_integer_score_is_coerced_to_float(self) -> None:
        """PostgreSQL may hand back an int for an integral score."""
        rows = [{"id": 1, "combined_score": 2}]
        assert scores_from_hybrid_rows(rows) == [(1, 2.0)]

    def test_non_numeric_score_raises_type_error(self) -> None:
        """A string score is a contract break, not a silent zero."""
        rows = [{"id": 1, "combined_score": "0.5"}]
        with pytest.raises(TypeError, match="combined_score"):
            scores_from_hybrid_rows(rows)

    def test_feeds_weighted_score_fusion(self) -> None:
        """Hybrid rows fuse with a second retriever's score list."""
        rows = [{"id": 1, "combined_score": 0.9}, {"id": 2, "combined_score": 0.1}]
        fused = weighted_score_fusion(
            [scores_from_hybrid_rows(rows), [(2, 0.8), (1, 0.2)]],
            normalization="none",
        )
        # Equal weights: id 1 -> 0.45 + 0.1, id 2 -> 0.05 + 0.4.
        assert [doc for doc, _ in fused] == [1, 2]
