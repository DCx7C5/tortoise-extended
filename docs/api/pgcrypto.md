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
from tortoise_extended.expressions.pgcrypto import HMACDigest

# HMAC tag as an annotation
rows = await Document.annotate(
    tag=HMACDigest("body", "shared-key", "sha256")
).all()
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `data` | `str \| F \| Function \| Term` | Required | Value to hash |
| `key` | `str` | Required | Secret key (rendered as a SQL literal) |
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

- **The library never sees or stores keys.**  Keys are rendered as SQL string
  literals in the query — they travel to PostgreSQL but are not logged or
  cached by tortoise-extended.
- **Use application-managed keys.**  Store passphrases/secrets in environment
  variables or a secrets manager; do not hardcode them in model definitions.
- **`Digest` is not HMAC.**  For keyed hashing, prefer `HMACDigest` which is
  not vulnerable to length-extension attacks.
