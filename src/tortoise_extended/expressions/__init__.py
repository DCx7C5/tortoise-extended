"""Expressions for tortoise_extended: filters, criteria, CTEs."""

from tortoise_extended.expressions.distinct_on import distinct_on
from tortoise_extended.expressions.fusion import (
    diversity_rerank,
    ranking_from_hits,
    reciprocal_rank_fusion,
    scores_from_hybrid_rows,
    weighted_score_fusion,
)
from tortoise_extended.expressions.graph_filters import (
    CosineDistance,
    HammingDistance,
    InnerProduct,
    JaccardDistance,
    L2Distance,
)
from tortoise_extended.expressions.graph_traversal import (
    NeighborNode,
    TraversalNode,
)
from tortoise_extended.expressions.ltree_filters import (
    LTreeAncestorMatch,
    LTreeAncestorOf,
    LTreeDescendantMatch,
    LTreeDescendantOf,
    LTreeMatch,
)
from tortoise_extended.expressions.pgcrypto import (
    Digest,
    DigestHex,
    HMACDigest,
    PGPSymDecrypt,
    PGPSymEncrypt,
)
from tortoise_extended.expressions.recursive_cte import RecursiveCTE
from tortoise_extended.expressions.windows import (
    avg_over,
    count_over,
    dense_rank,
    max_over,
    min_over,
    ntile,
    rank,
    row_number,
    sum_over,
)

__all__ = [
    "CosineDistance",
    "Digest",
    "DigestHex",
    "HMACDigest",
    "HammingDistance",
    "InnerProduct",
    "JaccardDistance",
    "L2Distance",
    "LTreeAncestorMatch",
    "LTreeAncestorOf",
    "LTreeDescendantMatch",
    "LTreeDescendantOf",
    "LTreeMatch",
    "NeighborNode",
    "PGPSymDecrypt",
    "PGPSymEncrypt",
    "RecursiveCTE",
    "TraversalNode",
    "avg_over",
    "count_over",
    "dense_rank",
    "distinct_on",
    "diversity_rerank",
    "max_over",
    "min_over",
    "ntile",
    "rank",
    "ranking_from_hits",
    "reciprocal_rank_fusion",
    "row_number",
    "scores_from_hybrid_rows",
    "sum_over",
    "weighted_score_fusion",
]
