# Cached Fields (Redis)

`tortoise_extended` ships two field types that keep a scalar column and a Redis key in
step: `CachedField` (Postgres authoritative, Redis is a read-through cache) and
`RedisPrimaryField` (Redis read-first, the column is a real fallback copy).

Both are **wrappers**: they take an existing scalar field instance and reproduce its
database column exactly — SQL type, nullability, default, uniqueness, index, comment,
`describe()` and migration output — while carrying the cache metadata
(`ttl`, `namespace`, `serializer`, `backend`). Adopting one is schema-neutral: the
migrations render the inner field, so a column does not change when you wrap it.

They are optional. Nothing changes for models that do not use them.

## Requirements

- The `redis` extra —
  `uv add "tortoise-extended[redis] @ git+https://github.com/DCx7C5/tortoise-extended@v0.2.0"`.
  (`uv add 'tortoise-extended[redis]'` resolves nothing: there is no PyPI release yet.)
- `RedisCache.init(...)` before the first cache call. Field construction never touches
  Redis; the backend is resolved lazily on each operation.
- The usual rule still applies: `import tortoise_extended` before `Tortoise.init()`.

## The two field types

| | `CachedField` | `RedisPrimaryField` |
|---|---|---|
| Authority | the Postgres column, always | Redis first, column as fallback |
| `cached_get` | Redis hit → decoded; miss → the instance's loaded value, cached, returned | same, but a miss is expected rather than exceptional |
| `cached_set` | sets the attribute **and deletes the key** (cache-aside) | sets the attribute **and** the key (write-through) |
| Durability | the column holds the value at all times | requires `save()` — the column keeps the last **saved** value |
| Redis flushed / evicted | harmless | recovered from the column on the next read |
| Use it for | read-mostly columns you already store | values you read far more often than you write, and can re-save |

Neither type has a "read priority" switch: both read Redis first. The only difference is
what `cached_set` does.

## Usage

```python
import tortoise_extended  # noqa: F401 — apply patches, before Tortoise.init()
from tortoise import Model, fields
from tortoise_extended import CachedField, cached_get, cached_set
from tortoise_extended.cache import RedisCache

await RedisCache.init(url="redis://localhost:6381/0", password="...")

class Article(Model):
    id = fields.IntField(primary_key=True)
    title = CachedField(fields.CharField(max_length=200), ttl=300)
    summary = CachedField(fields.TextField(null=True), ttl=0)  # 0 = no expiry

    class Meta:
        table = "articles"

article = await Article.get(id=1)
title = await cached_get(article, "title")      # Redis, or the column on a miss
await cached_set(article, "title", "New title") # attribute set, key invalidated
await article.save()                            # the column, then the key
```

Write-through instead:

```python
from tortoise_extended import RedisPrimaryField

class Session(Model):
    id = fields.UUIDField(primary_key=True)
    blob = RedisPrimaryField(fields.JSONField(), ttl=60)
```

With `RedisPrimaryField`, `await cached_set(session, "blob", {...})` is visible to other
readers immediately; `await session.save()` is what makes it durable. A value set and
never saved is lost on eviction, and the column returns the last saved value.

## Write-through on save

`CachedFieldsMixin` rewrites keys from the saved row, so ordinary ORM writes keep the
cache consistent without extra calls:

```python
from tortoise_extended import CachedFieldsMixin

class Profile(CachedFieldsMixin, Model):
    id = fields.IntField(primary_key=True)
    bio = CachedField(fields.TextField(null=True))

    class Meta:
        table = "profiles"
```

Put the mixin **first** in the bases. It composes with `BaseCacheableModel` and
`VersionMixin`, but it must be the outermost layer: `VersionMixin.save` performs its
guarded update through `QuerySet.update()` without calling `Model.save()`, so a reversed
order silently skips the cache write.

`save(update_fields=[...])` that omits a cached field **invalidates** that field's key
rather than caching the possibly-unsaved in-memory value.

## TTL

| Value | Meaning |
|---|---|
| `ttl=None` (default) | pass `None` to the backend, which applies its own default (300 s) |
| `ttl=0` | no expiry |
| `ttl=N` | expire after N seconds |

The field never substitutes a default for `0`.

## Bypass paths

These never call `Model.save()` / `Model.delete()`, so keys for the affected rows are not
refreshed:

- `QuerySet.update()` and `QuerySet.delete()`
- `Model.bulk_update` / `QuerySet.bulk_update`, `bulk_create`
- `Model.bulk_insert` (COPY ingestion)
- `save(update_fields=[...])` naming other fields — the omitted field's key is invalidated

`update_or_create()` is **not** a bypass: it calls `instance.save()`.

Keys are bounded by their TTL, and `await cached_invalidate(obj, "field")` clears one
explicitly.

## Failures

| Situation | Read (`cached_get`, `prefetch_cached`) | Write (`cached_set`, `cached_invalidate`) | `save` / `delete` via the mixin |
|---|---|---|---|
| Redis not initialized (`CacheBackendNotInitializedError`) | raises | raises | suppressed, logged, the column write stands |
| Redis error (`RedisCacheError`) | fails open — serves the instance value | raises | suppressed, logged, the column write stands |
| Corrupt/unreadable cached entry | treated as a miss: the key is deleted and the value re-read | — | — |
| Value cannot be encoded (`CacheSerializationError`) | skipped, value still returned | raises | logged as a warning, the save still succeeds |

A cache outage never turns a successful database write into an exception.

## Partial instances

A field deferred with `only()` / `defer()` has no attribute on the instance, and there is
no value to serve or cache. The helpers raise `ValueError` naming `Model.field` and point
at `refresh_from_db()`; the mixin simply skips fields that are not loaded, as Tortoise's
own `save()` does.

## Prefetching

`await prefetch_cached(instances, "field")` warms a list in two round trips: one `MGET`
for every key, then one pipelined write for the misses. An empty list is a no-op and makes
no Redis call, and instances without a primary key are skipped. There is no automatic
prefetch on model load — call it explicitly where it pays.

## Testing

Live cache tests are AUTH-gated like the rest of the Redis suite: they skip unless
`REDIS_PASSWORD` is set. Run them with

```bash
uv run --env-file .env --extra redis pytest
```

See [Cache (Redis)](../api/cache.md) for the backend itself, and
[Fields](../api/fields.md) for the field constructor reference.
