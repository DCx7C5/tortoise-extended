"""tortoise-extended: Tortoise ORM extensions for PostgreSQL workloads.

Monkey-patches tortoise-orm to add:
- VectorField (pgvector type codec)
- HNSWIndex / IVFFlatIndex / GiSTIndex (index types)
- Custom filters for vector similarity search
- RecursiveCTE, GraphTraversal, pathfinding helpers
- GraphVectorSearch (single-query vector + graph compositor with typed results)
- HybridSearch (vector + FTS weighted scoring)
- BaseGraphNodeModel / BaseGraphEdgeModel / BaseHierarchyModel (graph base models in tortoise_extended.models)
- LTreeField + ltree filters (hierarchical data)
- TimescaleDB hypertable migration operations
- Redis caching (optional)

Patches are applied automatically on import, and can also be applied
explicitly via :func:`patch` for consumers that want to make the
monkey-patching visible in their entry point. Either way, importing this
package (and calling :func:`patch` if used) must happen before
``Tortoise.init()``:

    import tortoise_extended

    tortoise_extended.patch()  # explicit; also applied automatically on import

    await Tortoise.init(
        db_url="...",
        modules={"models": ["..."]},
    )

The patches are applied only on a tested tortoise-orm release
(``>=1.1.7,<1.2``) and only when the patched attributes are either absent or
already this package's own objects: an unsupported version or a foreign
definition under a patched name raises instead of silently producing a wrong
or partially applied patch set.

The dev-only typing overlay lives in ``stubs/tortoise-stubs/``; see the drift
note at the top of ``stubs/tortoise-stubs/__init__.pyi``.
"""

from collections.abc import Awaitable, Callable
from importlib.metadata import version as _distribution_version
from types import ModuleType
from typing import cast

from asyncpg import Pool
from tortoise.fields import Field
from tortoise.filters import FilterInfoDict
from tortoise.models import Model as _Model
from tortoise.queryset import QuerySet as _QuerySet

from tortoise_extended._types import AsyncpgConnection, RowValue
from tortoise_extended.bulk import bulk_insert
from tortoise_extended.cache import (
    CachedQuerySet,
    RedisCache,
    RedisCacheBackend,
    cached,
    cached_method,
    invalidate,
)
from tortoise_extended.exceptions import (
    CacheBackendNotInitializedError,
    CacheDataError,
    CacheError,
    CacheKeyError,
    CacheSerializationError,
    FieldDefinitionError,
    GraphError,
    GraphTraversalError,
    HierarchyError,
    HybridSearchError,
    IndexDefinitionError,
    LTreeFieldError,
    MigrationOperationError,
    PatchConflictError,
    RecursiveCTEError,
    RedisCacheError,
    SetupError,
    TimescaleError,
    TortoiseExtendedError,
    TortoiseVersionError,
    VectorFieldError,
    VersionMismatchError,
)
from tortoise_extended.expressions.distinct_on import distinct_on
from tortoise_extended.expressions.fusion import (
    diversity_rerank,
    ranking_from_hits,
    reciprocal_rank_fusion,
    scores_from_hybrid_rows,
    weighted_score_fusion,
)
from tortoise_extended.expressions.graph import Graph
from tortoise_extended.expressions.graph_filters import (
    CosineDistance,
    HammingDistance,
    InnerProduct,
    JaccardDistance,
    L2Distance,
    get_vector_filters,
    vector_encoder,
)
from tortoise_extended.expressions.graph_traversal import (
    GraphTraversal,
    NeighborNode,
    TraversalNode,
)
from tortoise_extended.expressions.graph_vector_search import (
    GraphVectorHit,
    GraphVectorSearch,
)
from tortoise_extended.expressions.hybrid_search import HybridSearch
from tortoise_extended.expressions.ltree_filters import get_ltree_filters
from tortoise_extended.expressions.pathfinding import (
    all_paths,
    find_cycles,
    shortest_path,
)
from tortoise_extended.expressions.pgcrypto import (
    Digest,
    DigestHex,
    HMACDigest,
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
from tortoise_extended.fields.char import CharField
from tortoise_extended.fields.citext import CitextField
from tortoise_extended.fields.inet import InetField
from tortoise_extended.fields.ipv4 import IPv4Field
from tortoise_extended.fields.ltree import LTreeField
from tortoise_extended.fields.macaddr import MacAddr8Field, MacAddrField
from tortoise_extended.fields.path import PathField
from tortoise_extended.fields.pgcrypto import PGPEncryptedField
from tortoise_extended.fields.url import URLField
from tortoise_extended.fields.uuid import UUID4Field, UUID7Field
from tortoise_extended.fields.vector import VectorField
from tortoise_extended.indexes.hnsw_index import HNSWIndex, IVFFlatIndex
from tortoise_extended.indexes.ltree_index import GiSTIndex
from tortoise_extended.lifecycle import SetupReport, ensure_extensions, setup
from tortoise_extended.migrations.operations import (
    CreateContinuousAggregate,
    CreateHypertable,
)
from tortoise_extended.models import (
    BaseCacheableModel,
    BaseEventStreamModel,
    BaseGraphEdgeModel,
    BaseGraphNodeModel,
    BaseHierarchyModel,
    BaseModel,
    BaseNode,
    BaseSample,
    BaseSoftDeleteModel,
    BaseSoftDeleteNode,
    BaseUnifiedNode,
    BaseUserModel,
    NodeMixin,
    NodeTree,
    SampleMixin,
    SoftDeleteNodeQuerySet,
    SoftDeleteQuerySet,
    TimestampEndMixin,
    TimestampMixin,
    UnifiedIdModel,
    VersionMixin,
)
from tortoise_extended.pagination import PageInfo, paginate
from tortoise_extended.queryset import random_ordering
from tortoise_extended.timescale import TimeBucketRow

# ---------------------------------------------------------------------------
# pgvector codec helpers (module-level so every branch is unit-testable)
# ---------------------------------------------------------------------------


def _encode_vector(value: list[float] | str | None) -> str:
    """Encode a vector value into the pgvector text format."""
    if isinstance(value, str):
        return value
    if value:
        return "[" + ",".join(str(x) for x in value) + "]"
    return "[]"


def _decode_vector(value: str) -> list[float]:
    """Decode the pgvector text format into a list of floats."""
    stripped = value.strip("[]")
    if not stripped:
        return []
    return [float(x) for x in stripped.split(",") if x]


async def _pgvector_codec_init(conn: AsyncpgConnection) -> None:
    """Set the pgvector type codec on a single connection.

    Resolves the schema of the ``vector`` / ``halfvec`` types from the
    connection so the codec is registered where the extension actually lives
    (G18) — not hardcoded to ``public``. Gracefully skips if the ``vector``
    extension is not yet created in the database (e.g. before ``CREATE
    EXTENSION vector``) or if the connection does not support custom type
    codecs.
    """
    set_codec = getattr(conn, "set_type_codec", None)
    if set_codec is None:
        return
    schema = "public"
    fetchval = getattr(conn, "fetchval", None)
    if fetchval is not None:
        try:
            resolved = await fetchval(
                "SELECT n.nspname FROM pg_type t "
                "JOIN pg_namespace n ON n.oid = t.typnamespace "
                "WHERE t.typname = 'vector'"
            )
            if resolved is not None:
                schema = str(resolved)
        except Exception:
            # Probe failure (e.g. a non-asyncpg connection or a DB that
            # cannot answer the catalog query): fall back to ``public`` and
            # let ``set_type_codec`` decide — it raises ValueError when the
            # type is absent, which is swallowed below.
            pass
    for type_name in ("vector", "halfvec"):
        try:
            await set_codec(
                type_name,
                encoder=_encode_vector,
                decoder=_decode_vector,
                schema=schema,
            )
        except ValueError, AttributeError:
            # ValueError: "unknown type: pgvector.<type>" — extension not
            # loaded or the type does not exist in this pgvector version.
            # AttributeError: conn doesn't support set_type_codec.
            pass


async def _combined_codec_init(
    conn: AsyncpgConnection,
    original_init: Callable[[AsyncpgConnection], Awaitable[None]] | None,
) -> None:
    """Run the pgvector codec setup before a caller-provided init callback."""
    await _pgvector_codec_init(conn)
    if original_init is not None:
        await original_init(conn)


# ---------------------------------------------------------------------------
# tortoise-orm version guard
# ---------------------------------------------------------------------------

#: Oldest tortoise-orm release the monkey-patches below are tested against.
MIN_VERSION: tuple[int, ...] = (1, 1, 7)

#: First tortoise-orm release that is NOT supported (exclusive upper bound).
MAX_EXCL: tuple[int, ...] = (1, 2)


def _parse_version(raw: str) -> tuple[int, ...]:
    """Parse the numeric release components of a version string.

    ``packaging`` is deliberately not used: it is not a declared dependency
    (it only appears transitively in ``uv.lock``), and the comparison here is
    a plain tuple compare. Only the leading ASCII digits of each
    dot-separated component are kept, and parsing stops at the first
    component that is not entirely numeric, so pre-release and local suffixes
    (and anything after them) are ignored: ``"1.2.0rc1"`` parses as
    ``(1, 2, 0)`` and ``"1.1.9+local.1"`` as ``(1, 1, 9)``. A pre-release of
    an out-of-range release is therefore rejected exactly like the final
    release — ``1.2.0rc1`` still fails the ``< 1.2`` bound because the patches
    may already target 1.2 internals.

    A string with no numeric component at all yields ``()``, which fails the
    lower bound and therefore raises rather than passing silently.
    """
    parts: list[int] = []
    for chunk in raw.split("."):
        digits = ""
        for char in chunk:
            if char not in "0123456789":
                break
            digits += char
        if not digits:
            break
        parts.append(int(digits))
        if len(digits) != len(chunk):
            # Pre-release / local suffix starts here; later dot-separated
            # components belong to the suffix, not to the release number.
            break
    return tuple(parts)


def _installed_tortoise_version() -> str:
    """Return the installed tortoise-orm version string.

    ``tortoise.__version__`` is a real module attribute in the supported 1.1.x
    line (a hardcoded ``"1.1.9"`` on the current install) and is the primary
    source. The distribution-metadata lookup is only a fallback for builds
    that drop the attribute — or leave it empty/non-string, which is
    unusable for parsing.
    """
    import tortoise

    declared = getattr(tortoise, "__version__", None)
    if isinstance(declared, str) and declared:
        return declared
    return _distribution_version("tortoise-orm")


def _check_tortoise_version() -> None:
    """Fail fast when the installed tortoise-orm is outside the tested range.

    The patches below mutate tortoise-orm internals (attribute names, filter
    signatures, the asyncpg pool hook). On an untested release those targets
    can move, and a partially applied patch set surfaces later as confusing
    downstream failures. Refuse to patch instead — never warn and continue.

    Raises:
        TortoiseVersionError: when the installed version is outside
            ``>=1.1.7,<1.2``; the message names the installed version, the
            tested range, and the remedy.
    """
    raw = _installed_tortoise_version()
    parsed = _parse_version(raw)
    if MIN_VERSION <= parsed < MAX_EXCL:
        return
    minimum = ".".join(str(part) for part in MIN_VERSION)
    maximum = ".".join(str(part) for part in MAX_EXCL)
    raise TortoiseVersionError(
        f"tortoise-extended requires tortoise-orm >={minimum},<{maximum}; "
        f"installed version is {raw!r}. The monkey-patches in this package "
        f"target tortoise-orm internals and are only tested in that range. "
        f"Install a supported tortoise-orm (for example "
        f"`uv add 'tortoise-orm[asyncpg]>={minimum},<{maximum}'`) or upgrade "
        f"tortoise-extended to a release that supports {raw!r}."
    )


# ---------------------------------------------------------------------------
# Monkey-patch attribute ownership
# ---------------------------------------------------------------------------


def _install_patched_attribute(
    module: ModuleType,
    attribute: str,
    replacement: type,
) -> None:
    """Install ``replacement`` as ``module.attribute``, refusing foreign objects.

    Presence alone is not enough: a co-installed package that pre-defines the
    same attribute would turn our patch into a silent no-op while
    ``tortoise_extended`` keeps exporting this package's class. Identity is
    the contract —

    * attribute absent → install;
    * attribute already ``replacement`` → idempotent no-op;
    * attribute holds anything else → raise :class:`PatchConflictError`.

    Args:
        module: The module object to patch (``tortoise.fields``,
            ``tortoise.indexes``).
        attribute: The attribute name to claim.
        replacement: This package's class to install.

    Raises:
        PatchConflictError: when a different object already owns the name.
    """
    existing = getattr(module, attribute, None)
    if existing is None:
        setattr(module, attribute, replacement)
        return
    if existing is replacement:
        return
    raise PatchConflictError(
        f"{module.__name__}.{attribute} already holds a foreign object "
        f"({existing!r}), so tortoise-extended cannot install its own "
        f"{replacement.__qualname__}: the patch would silently no-op while "
        f"`tortoise_extended.{replacement.__name__}` still exports this "
        f"package's class. Uninstall the conflicting extension or make "
        f"tortoise-extended the only package patching this attribute."
    )


# ---------------------------------------------------------------------------
# Apply monkey-patches
# ---------------------------------------------------------------------------


def patch() -> None:
    """Apply all monkey-patches to tortoise-orm explicitly.

    Idempotent — safe to call any number of times, including after the
    patches were already applied automatically at import time. Re-applying
    never double-wraps a patched function.

    Usage in a consumer entry point (e.g. ``main.py``)::

        import tortoise_extended

        tortoise_extended.patch()  # apply all patches before Tortoise.init()

        await Tortoise.init(
            db_url="...",
            modules={"models": ["..."]},
        )

    The import itself already applies the patches, so calling ``patch()`` is
    optional but recommended when you want the monkey-patching to be explicit
    in the entry point instead of relying on the import side effect.
    """
    _apply_patches()


def _apply_patches() -> None:
    """Apply all monkey-patches to tortoise-orm.

    Called at import time. Safe to call multiple times (idempotent).

    Before anything is patched, :func:`_check_tortoise_version` refuses to
    continue on a tortoise-orm release outside the tested ``>=1.1.7,<1.2``
    range — a partially applied patch set is worse than a clear failure.

    Every patch follows the same contract: it checks a guard condition
    (identity of the object in the slot, a ``_tortoise_extended_patched``
    flag, or an ``__all__`` membership test) before applying the mutation. If
    the target already holds our own object the patch is a no-op, so
    re-importing the package or calling ``patch()`` more than once has no
    effect. A *foreign* object under a name this package owns raises
    :class:`PatchConflictError` instead of silently skipping the patch.

    Patch sites and their guards:

    0. **Version guard** (:func:`_check_tortoise_version`) —
       :class:`TortoiseVersionError` before any patch is applied.
    1. **VectorField registration** (``tortoise.fields.VectorField``) —
       identity: absent installs, ``VectorField`` is a no-op, anything else
       raises :class:`PatchConflictError`.
    2. **Index types** (``tortoise.indexes.{HNSWIndex,IVFFlatIndex,GiSTIndex}``) —
       the same identity contract, per type.
    3. **Filter extension** (``tortoise.filters.get_filters_for_field``) —
       ``_tortoise_extended_patched`` flag on the module. A foreign wrapper
       is composed with (ours dispatches through it), not rejected.
    4. **asyncpg codec** (``asyncpg.Pool`` init hooks) —
       ``_tortoise_extended_codec_patched`` flag on the pool class. A foreign
       ``create_pool`` wrapper is composed with, not rejected.
    5. **QuerySet pagination** (``tortoise.queryset.QuerySet.paginate``) —
       the same identity contract as sites 1-2: absent installs, our own
       ``paginate`` is a no-op, anything else raises
       :class:`PatchConflictError`.
    6. **QuerySet random ordering** (``tortoise.queryset.QuerySet.random``) —
       the same identity contract as site 5: absent installs, our own
       ``random_ordering`` is a no-op, anything else raises
       :class:`PatchConflictError`.
    7. **COPY bulk insert** (``tortoise.models.Model.bulk_insert``) — the same
       identity contract, compared through ``classmethod.__func__`` because the
       install wraps the function: absent installs, our own ``bulk_insert`` is a
       no-op, anything else raises :class:`PatchConflictError`.

    ``MigrationWriter._format_operation`` is patched separately, at import
    time, by :mod:`tortoise_extended.migrations.operations` — not by this
    function.
    """
    _check_tortoise_version()

    import tortoise.backends.asyncpg.client as _asyncpg_client_mod
    import tortoise.fields as _fields_mod
    import tortoise.filters as _filters_mod
    import tortoise.indexes as _indexes_mod

    # Import-order safety guard: the patches (VectorField, index types,
    # get_filters_for_field, asyncpg codec) are registered against
    # tortoise-orm at patch-application time. If ``Tortoise.init()`` already
    # ran, those registrations are silently missed and models using them
    # break. Guard against that order violation — but only when the patches
    # have NOT already been applied (idempotent re-application via the public
    # ``patch()`` stays safe). This guard protects against a *missing*
    # attribute, so presence — not identity — is the right test here.
    from tortoise import Tortoise

    if Tortoise._inited and not hasattr(_fields_mod, "VectorField"):
        raise TortoiseExtendedError(
            "tortoise-orm is already initialized. `import tortoise_extended` "
            "(or `tortoise_extended.patch()`) must run BEFORE "
            "`Tortoise.init()` so VectorField, the index types, filters, and "
            "the asyncpg codec are registered. Fix your import order / call "
            "site."
        )

    # 1. Register VectorField in the fields module
    _install_patched_attribute(_fields_mod, "VectorField", VectorField)
    if "VectorField" not in _fields_mod.__all__:
        _fields_mod.__all__.append("VectorField")

    # 2. Register HNSWIndex, IVFFlatIndex and GiSTIndex in the indexes module
    for _index_type in (HNSWIndex, IVFFlatIndex, GiSTIndex):
        _install_patched_attribute(_indexes_mod, _index_type.__name__, _index_type)

    # 3. Patch get_filters_for_field to handle VectorField (idempotent)
    if not getattr(_filters_mod, "_tortoise_extended_patched", False):
        # The upstream function is stub-typed with an invariant ``Field[object]``
        # parameter; re-type it through a Callable so the concrete ``RowValue``
        # field can be forwarded (the runtime accepts any field type).
        _original_get_filters = cast(
            Callable[[str, Field[RowValue] | None, str], dict[str, FilterInfoDict]],
            _filters_mod.get_filters_for_field,
        )

        def _patched_get_filters_for_field(
            field_name: str,
            field: Field[RowValue] | None,
            source_field: str,
        ) -> dict[str, FilterInfoDict]:
            if field is not None and isinstance(field, VectorField):
                return get_vector_filters(field_name, source_field)
            if field is not None and isinstance(field, LTreeField):
                return get_ltree_filters(field_name, source_field)
            return _original_get_filters(field_name, field, source_field)

        _filters_mod.get_filters_for_field = _patched_get_filters_for_field
        setattr(_filters_mod, "_tortoise_extended_patched", True)

        # Also patch the local reference in tortoise.models — it imports
        # get_filters_for_field via ``from tortoise.filters import ...`` which
        # captures the original before our patch.
        import tortoise.models as _models_mod

        _models_mod.get_filters_for_field = _patched_get_filters_for_field

    # 4. Register pgvector codec on EVERY asyncpg connection via init callback
    if not getattr(
        _asyncpg_client_mod.AsyncpgDBClient, "_tortoise_extended_codec_patched", False
    ):
        _original_create_pool = _asyncpg_client_mod.AsyncpgDBClient.create_pool

        async def _patched_create_pool(
            self: _asyncpg_client_mod.AsyncpgDBClient,
            **kwargs: str
            | int
            | float
            | bool
            | None
            | Callable[[AsyncpgConnection], Awaitable[None]],
        ) -> Pool:
            # Inject init callback so EVERY new connection gets the codec
            original_init = cast(
                Callable[[AsyncpgConnection], Awaitable[None]] | None,
                kwargs.pop("init", None),
            )

            async def _combined_init(conn: AsyncpgConnection) -> None:
                await _combined_codec_init(conn, original_init)

            kwargs["init"] = _combined_init
            return await _original_create_pool(self, **kwargs)

        _asyncpg_client_mod.AsyncpgDBClient.create_pool = _patched_create_pool
        setattr(
            _asyncpg_client_mod.AsyncpgDBClient,
            "_tortoise_extended_codec_patched",
            True,
        )

    # 5. Register QuerySet.paginate (idempotent identity guard)
    _existing_paginate = getattr(_QuerySet, "paginate", None)
    if _existing_paginate is None:
        setattr(_QuerySet, "paginate", paginate)
    elif _existing_paginate is not paginate:
        raise PatchConflictError(
            f"tortoise.queryset.QuerySet.paginate already holds a foreign "
            f"object ({_existing_paginate!r}), so tortoise-extended cannot "
            f"install its own `paginate`: the patch would silently no-op "
            f"while `tortoise_extended.paginate` still exports this package's "
            f"function. Uninstall the conflicting extension or make "
            f"tortoise-extended the only package patching this attribute."
        )

    # 6. Register QuerySet.random (idempotent identity guard)
    _existing_random = getattr(_QuerySet, "random", None)
    if _existing_random is None:
        setattr(_QuerySet, "random", random_ordering)
    elif _existing_random is not random_ordering:
        raise PatchConflictError(
            f"tortoise.queryset.QuerySet.random already holds a foreign "
            f"object ({_existing_random!r}), so tortoise-extended cannot "
            f"install its own `random`: the patch would silently no-op "
            f"while `tortoise_extended.random_ordering` still exports this "
            f"package's function. Uninstall the conflicting extension or "
            f"make tortoise-extended the only package patching this "
            f"attribute."
        )

    # 7. Register Model.bulk_insert (idempotent identity guard on __func__)
    _existing_bulk_insert = getattr(_Model, "bulk_insert", None)
    if _existing_bulk_insert is None:
        setattr(_Model, "bulk_insert", classmethod(bulk_insert))
    elif getattr(_existing_bulk_insert, "__func__", _existing_bulk_insert) is not (
        bulk_insert
    ):
        raise PatchConflictError(
            f"tortoise.models.Model.bulk_insert already holds a foreign "
            f"object ({_existing_bulk_insert!r}), so tortoise-extended cannot "
            f"install its own `bulk_insert`: the patch would silently no-op "
            f"while `tortoise_extended.bulk_insert` still exports this "
            f"package's function. Uninstall the conflicting extension or make "
            f"tortoise-extended the only package patching this attribute."
        )


__all__ = [
    "BaseCacheableModel",
    "BaseEventStreamModel",
    "BaseGraphEdgeModel",
    "BaseGraphNodeModel",
    "BaseHierarchyModel",
    "BaseModel",
    "BaseNode",
    "BaseSample",
    "BaseSoftDeleteModel",
    "BaseSoftDeleteNode",
    "BaseUnifiedNode",
    "BaseUserModel",
    "CacheBackendNotInitializedError",
    "CacheDataError",
    "CacheError",
    "CacheKeyError",
    "CacheSerializationError",
    "CachedQuerySet",
    "CharField",
    "CitextField",
    "CosineDistance",
    "CreateContinuousAggregate",
    "CreateHypertable",
    "Digest",
    "DigestHex",
    "FieldDefinitionError",
    "GiSTIndex",
    "Graph",
    "GraphError",
    "GraphTraversal",
    "GraphTraversalError",
    "GraphVectorHit",
    "GraphVectorSearch",
    "HMACDigest",
    "HNSWIndex",
    "HammingDistance",
    "HierarchyError",
    "HybridSearch",
    "HybridSearchError",
    "IPv4Field",
    "IVFFlatIndex",
    "IndexDefinitionError",
    "InetField",
    "InnerProduct",
    "JaccardDistance",
    "L2Distance",
    "LTreeField",
    "LTreeFieldError",
    "MacAddr8Field",
    "MacAddrField",
    "MigrationOperationError",
    "NeighborNode",
    "NodeMixin",
    "NodeTree",
    "PGPEncryptedField",
    "PageInfo",
    "PatchConflictError",
    "PathField",
    "RecursiveCTE",
    "RecursiveCTEError",
    "RedisCache",
    "RedisCacheBackend",
    "RedisCacheError",
    "SampleMixin",
    "SetupError",
    "SetupReport",
    "SoftDeleteNodeQuerySet",
    "SoftDeleteQuerySet",
    "TimeBucketRow",
    "TimescaleError",
    "TimestampEndMixin",
    "TimestampMixin",
    "TortoiseExtendedError",
    "TortoiseVersionError",
    "TraversalNode",
    "URLField",
    "UUID4Field",
    "UUID7Field",
    "UnifiedIdModel",
    "VectorField",
    "VectorFieldError",
    "VersionMismatchError",
    "VersionMixin",
    "all_paths",
    "avg_over",
    "bulk_insert",
    "cached",
    "cached_method",
    "count_over",
    "dense_rank",
    "distinct_on",
    "diversity_rerank",
    "ensure_extensions",
    "find_cycles",
    "get_ltree_filters",
    "get_vector_filters",
    "invalidate",
    "max_over",
    "min_over",
    "ntile",
    "paginate",
    "patch",
    "random_ordering",
    "rank",
    "ranking_from_hits",
    "reciprocal_rank_fusion",
    "row_number",
    "scores_from_hybrid_rows",
    "setup",
    "shortest_path",
    "sum_over",
    "vector_encoder",
    "weighted_score_fusion",
]

# Apply patches at import time (idempotent; `patch()` re-applies safely)
_apply_patches()
