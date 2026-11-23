# pgcrypto

PostgreSQL pgcrypto hash/digest and symmetric-encryption query-expression helpers.

## Overview

This module provides thin pypika/Tortoise `Function` wrappers that render
[pgcrypto](https://www.postgresql.org/docs/current/pgcrypto.html) SQL
functions inside Tortoise ORM queries: the hash/digest family (`digest`,
`hmac`) and the symmetric PGP family (`pgp_sym_encrypt`, `pgp_sym_decrypt`).

> **Important:** The library performs **no encryption itself**.  The helpers
> only make it easy to call pgcrypto from inside Tortoise queries.  Your
> application supplies the keys — the library never sees or stores them.

## Import

```python
from tortoise_extended.expressions.pgcrypto import (
    Digest,
    DigestHex,
    HMACDigest,
    PGPSymDecrypt,
    PGPSymEncrypt,
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

---

## PGPSymEncrypt

`pgp_sym_encrypt(data, key[, options])` — OpenPGP symmetric ciphertext (`bytea`).

Encrypts a **text** expression with a passphrase inside the database. The
result is non-deterministic ciphertext, so it is neither searchable nor
indexable: store it in a `bytea` column
([`PGPEncryptedField`](fields.md#pgpencryptedfield)) and derive a blind index
with `HMACDigest` when you need lookups.

```python
from tortoise.expressions import RawSQL

from tortoise_extended.expressions.pgcrypto import PGPSymEncrypt

await Document.filter(id=doc_id).update(
    secret=PGPSymEncrypt(
        "body_text",
        RawSQL("current_setting('app.encryption_key')"),
        "cipher-algo=aes256",
    )
)
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `data` | `str \| F \| Function \| Term` | Required | Text to encrypt (`text`, not `bytea`) |
| `key` | `str \| Term` | Required | Passphrase; a `str` is a SQL literal (see Security Notes) |
| `options` | `str \| None` | `None` | pgcrypto options, e.g. `"cipher-algo=aes256"`; omitted entirely when `None` |

**Returns:** `bytea` (ciphertext).

---

## PGPSymDecrypt

`pgp_sym_decrypt(data, key[, options])` — plaintext (`text`).

Decrypts ciphertext produced by `PGPSymEncrypt` (or any compatible encoder).
`data` must be a `bytea` expression; nesting `PGPSymEncrypt(...)` inside it is
the usual way to prove a value round-trips.

```python
from tortoise_extended.expressions.pgcrypto import PGPSymDecrypt

doc = await Document.annotate(
    clear=PGPSymDecrypt("secret", RawSQL("current_setting('app.encryption_key')"))
).get(id=doc_id)
print(doc.clear)  # the plaintext
```

**Parameters:** the same three as `PGPSymEncrypt`; `data` is the ciphertext.

**Returns:** `text` — the plaintext. It is returned to the client by design, so
it crosses the connection and sits in the result buffer; decrypt only where the
value is actually needed, never in a list/annotation you do not consume.

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

## Storage type

`PGPEncryptedField` is the matching column — a `bytea` type that refuses
`str`, so plaintext cannot be stored in a column that only looks encrypted.
See [Field Types → PGPEncryptedField](fields.md#pgpencryptedfield).
