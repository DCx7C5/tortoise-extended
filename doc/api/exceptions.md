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
├── FieldDefinitionError
│   ├── VectorFieldError
│   └── LTreeFieldError
├── IndexDefinitionError
├── GraphError
│   ├── GraphTraversalError
│   └── HierarchyError
├── RecursiveCTEError
├── HybridSearchError
├── TimescaleError
├── MigrationOperationError
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
| `VectorFieldError` | `VectorField` receives an invalid dimension or value |
| `LTreeFieldError` | `LTreeField` path validation fails |
| `IndexDefinitionError` | an index uses an invalid distance metric |
| `GraphTraversalError` | traversal parameters are inconsistent |
| `HierarchyError` | a hierarchy move/operation is invalid (e.g. moving a node into its own descendant) |
| `RecursiveCTEError` | a CTE is built without an anchor or union query |
| `HybridSearchError` | `HybridSearch` gets an unsupported `distance_metric` |
| `TimescaleError` | TimescaleDB manager/migration failures |
| `MigrationOperationError` | a migration operation is misconfigured |
| `CacheKeyError` | a `CacheKey` cannot be built from the given parts |
| `CacheSerializationError` | a value cannot be serialized/deserialized |
| `CacheDataError` | cached data has an unexpected shape |
| `CacheBackendNotInitializedError` | a backend is used before `RedisCache.init()` |
| `RedisCacheError` | an infrastructure-level Redis failure (connection, timeout) |

## Fail-open cache behavior

Cache reads and writes are fail-open: the cache decorators, `CacheableModel`,
and `CachedQuerySet` catch `CacheError` (covering both domain errors and
translated Redis infrastructure failures) and fall back to the database —
they never fail the primary operation because of a cache problem.
