# pgcrypto

PostgreSQL pgcrypto hash/digest query-expression helpers.

## Overview

This module provides thin pypika/Tortoise `Function` wrappers that render
[pgcrypto](https://www.postgresql.org/docs/current/pgcrypto.html) SQL
hash and digest functions (`digest`, `hmac`) inside Tortoise ORM queries.

> **Important:** The library performs **no encryption itself**.  The helpers
> only make it easy to call pgcrypto from inside Tortoise queries.  Your
> application supplies the keys — the library never sees or stores them.

## Import

```python
from tortoise_extended.expressions.pgcrypto import (
    Digest,
    DigestHex,
    HMACDigest,
)
```

## Digest

`digest(data, algorithm)` — raw binary hash (`bytea`).

```python
from tortoise_extended.expressions.pgcrypto import Digest

# Binary digest as an annotation
rows = await Document.annotate(hash=Digest("body", "sha256")).all()
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `data` | `str \| F \| Function \| Term` | Required | Value to hash (field name, `F()`, or expression) |
| `algorithm` | `str` | `"sha256"` | Hash algorithm (`"sha256"`, `"sha512"`, `"md5"`, …) |

**Returns:** `bytea`.

> Note: `digest` is the raw cryptographic hash — it is not HMAC and is
> vulnerable to length-extension attacks.  Use `HMACDigest` when a secret
> key is involved.

---

## DigestHex

`digest(data, algorithm)` wrapped in `encode(…, 'hex')` — hex string digest.

```python
from tortoise_extended.expressions.pgcrypto import DigestHex

# Hex-encoded digest
rows = await Document.annotate(hash=DigestHex("body", "sha256")).all()
```

**Parameters:** same as `Digest`.

**Returns:** `str` — a lowercase hex string (e.g. `"a1b2c3…"`) suitable for
storing or transmitting a digest as text.

---

## HMACDigest

`hmac(data, key, algorithm)` — keyed hash (HMAC).

```python
from tortoise.expressions import RawSQL

from tortoise_extended.expressions.pgcrypto import HMACDigest

# HMAC tag as an annotation.  The key is resolved server-side, so the secret
# never enters the statement text (see Security Notes).
rows = await Document.annotate(
    tag=HMACDigest(
        "body",
        RawSQL("current_setting('app.encryption_key')"),
        "sha256",
    )
).all()
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `data` | `str \| F \| Function \| Term` | Required | Value to hash |
| `key` | `str \| F \| Function \| Term` | Required | Secret key — a `str` is quoted as a SQL literal, a term is rendered verbatim |
| `algorithm` | `str` | `"sha256"` | Hash algorithm |

**Returns:** `bytea`.

---

## Requirements

- PostgreSQL with the `pgcrypto` extension enabled:

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

- The Docker image at `docker/postgres-ext/` enables `pgcrypto` at first start
  (`docker/postgres-ext/scripts/00-extensions.sql`); on any other server run the
  `CREATE EXTENSION IF NOT EXISTS pgcrypto;` statement above.

## Security Notes

- **The library never sees or stores keys.**  A `str` key is rendered as a SQL
  string literal, so it travels to PostgreSQL **inside the statement text**:
  it is visible in `pg_stat_activity.query` to any role with
  `pg_read_all_stats`, and is captured by `log_statement`,
  `log_min_duration_statement` and `auto_explain`, as well as by connection
  statement caches.  (`pg_stat_statements` normalises constants, so it is not
  a leak path.)  tortoise-extended itself never logs or caches the key.
- **Keep keys out of the statement text.**  Pass an expression instead of a
  literal — `HMACDigest(Field("body"), RawSQL("current_setting('app.encryption_key')"), "sha256")`
  — so the key is resolved server-side from the session setting and never
  appears in `pg_stat_activity` or the server log.  Set the GUC per connection
  (`SET app.encryption_key = ...`) or per role (`ALTER ROLE ... SET`).
- **Use application-managed keys.**  Store passphrases/secrets in environment
  variables or a secrets manager; do not hardcode them in model definitions.
- **`Digest` is not HMAC.**  For keyed hashing, prefer `HMACDigest` which is
  not vulnerable to length-extension attacks.
