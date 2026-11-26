# Exceptions

`tortoise-extended` raises a dedicated exception hierarchy instead of bare
builtin exceptions. Every error derives from a single base type,
`TortoiseExtendedError`, so consumers can catch one type:

```python
from tortoise_extended import TortoiseExtendedError

try:
    entity = await Entity.get(id=42)
except TortoiseExtendedError as exc:
    logger.warning("Extension error: %s", exc)
```

## Hierarchy

```
TortoiseExtendedError
├── TortoiseVersionError
├── PatchConflictError
├── FieldDefinitionError
│   ├── VectorFieldError
│   └── LTreeFieldError
├── IndexDefinitionError
├── GraphError
│   ├── GraphTraversalError
│   └── HierarchyError
├── RecursiveCTEError
├── HybridSearchError
├── PgCryptoError
├── TimescaleError
├── MigrationOperationError
├── SetupError
├── VersionMismatchError
└── CacheError
    ├── CacheKeyError
    ├── CacheSerializationError
    ├── CacheDataError
    ├── CacheBackendNotInitializedError
    └── RedisCacheError
```

## When each is raised

| Exception | Raised when |
|-----------|-------------|
| `TortoiseVersionError` | the installed `tortoise-orm` is outside the supported `>=1.1.7,<1.2`, raised at import before any patch is applied |
| `PatchConflictError` | a foreign object already owns an attribute this package patches, so the patch would silently lose |
| `VectorFieldError` | `VectorField` receives an invalid dimension or value |
| `LTreeFieldError` | `LTreeField` path validation fails |
| `IndexDefinitionError` | an index uses an invalid distance metric |
| `GraphTraversalError` | traversal parameters are inconsistent |
| `HierarchyError` | a hierarchy move/operation is invalid (e.g. moving a node into its own descendant) |
| `RecursiveCTEError` | a CTE is built without an anchor or union query |
| `HybridSearchError` | `HybridSearch` gets an unsupported `distance_metric` |
| `PgCryptoError` | a pgcrypto helper (`Digest`, `DigestHex`, `HMACDigest`) gets an algorithm outside the six pgcrypto accepts (`md5`, `sha1`, `sha224`, `sha256`, `sha384`, `sha512`), rejected at construction time |
| `TimescaleError` | TimescaleDB manager/migration failures |
| `MigrationOperationError` | a migration operation is misconfigured |
| `SetupError` | a required PostgreSQL extension cannot be created (typically a missing privilege) |
| `VersionMismatchError` | a `VersionMixin` guarded `save()` lost the race: the row's `version` moved since the instance was loaded (attributes: `model`, `pk`, `expected`, `current`) |
| `CacheKeyError` | a `CacheKey` cannot be built from the given parts |
| `CacheSerializationError` | a value cannot be serialized/deserialized |
| `CacheDataError` | cached data has an unexpected shape |
| `CacheBackendNotInitializedError` | a backend is used before `RedisCache.init()` |
| `RedisCacheError` | an infrastructure-level Redis failure (connection, timeout) |

## Fail-open cache behavior

Cache reads and writes are fail-open: the cache decorators, `BaseCacheableModel`,
and `CachedQuerySet` catch `CacheError` (covering both domain errors and
translated Redis infrastructure failures) and fall back to the database —
they never fail the primary operation because of a cache problem.
