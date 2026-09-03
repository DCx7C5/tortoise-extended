"""pgcrypto query expression helpers for tortoise-orm.

PostgreSQL's ``pgcrypto`` extension provides cryptographic functions
(``digest``, ``hmac``, ``pgp_sym_encrypt``, ``pgp_sym_decrypt``, ...) that run
entirely inside the database.  Tortoise ORM has no built-in way to reference
these functions in a ``QuerySet``, so this module exposes thin pypika/Tortoise
``Function`` wrappers that render the correct SQL.

.. important::

    This library performs **no encryption itself**.  These helpers only make
    it easy for your *application* to call pgcrypto from inside Tortoise
    queries.  You must ``CREATE EXTENSION IF NOT EXISTS pgcrypto;`` and call
    the functions explicitly — the library never invokes them behind your
    back.

Usage::

    from tortoise_extended.expressions.pgcrypto import (
        Digest,
        DigestHex,
        HMACDigest,
        PGPSymDecrypt,
        PGPSymEncrypt,
    )

    class Document(Model):
        id = fields.IntField(pk=True)
        body = fields.TextField()

    # Annotate with a SHA-256 hex digest of a text column.
    await Document.annotate(hash=DigestHex("body", "sha256")).all()

    # Filter on a digest computed in SQL.
    hash_docs = await Document.annotate(h=Digest("body", "sha256")).filter(
        digest__isnotnull=True
    )

    # Encrypt a plaintext value into the DB using pgp_sym_encrypt.
    ciphertext = await Document.annotate(
        enc=PGPSymEncrypt("body", "super-secret-key")
    ).first()

    # Decrypt stored ciphertext back to plaintext with pgp_sym_decrypt.
    plaintext = await Document.annotate(
        dec=PGPSymDecrypt("body", "super-secret-key")
    ).first()

    # HMAC a value with a shared key.
    mac = await Document.annotate(
        tag=HMACDigest("body", "shared-key", "sha256")
    ).first()

See also :mod:`tortoise_extended.fields.pgcrypto` for :class:`PGPEncryptedField`,
a thin ``BYTEA`` storage column that holds the ciphertext these expressions
produce (but never encrypts or decrypts itself).
"""

from typing import TypeAlias

from pypika_tortoise.terms import Term, ValueWrapper
from pypika_tortoise.terms import Function as PypikaFunction
from tortoise.expressions import CombinedExpression, F, Function

# A value usable as the ``data`` argument to a pgcrypto function: a field name,
# an ``F()`` reference, a combined/plain expression, or a raw pypika term.
DataArg: TypeAlias = str | F | CombinedExpression | Function | Term


class _DigestFunction(PypikaFunction):
    """pypika term emitting ``digest(<data>, '<algorithm>')``."""

    def __init__(self, data: Term, algorithm: str) -> None:
        super().__init__(
            "DIGEST", data, ValueWrapper(algorithm)
        )


class Digest(Function):
    """pgcrypto ``digest(<data>, '<algorithm>')`` — binary hash digest.

    Computes a raw binary hash (``bytea``) of *data* using the named
    algorithm in the database.  ``digest`` is not HMAC — it is the raw
    cryptographic hash and is vulnerable to length-extension attacks; prefer
    :class:`HMACDigest` when a secret key is involved.

    Args:
        data: The value to hash (a field name, ``F()``, or expression).
        algorithm: Hash algorithm name, e.g. ``"sha256"``, ``"sha512"``,
            ``"md5"``.  Rendered as a SQL string literal.

    Returns:
        ``bytea``.
    """

    database_func = _DigestFunction

    def __init__(self, data: DataArg, algorithm: str = "sha256") -> None:
        super().__init__(data, algorithm)


class _DigestHexFunction(PypikaFunction):
    """pypika term emitting ``encode(digest(<data>, '<algorithm>'), 'hex')``."""

    def __init__(self, data: Term, algorithm: str) -> None:
        super().__init__(
            "ENCODE",
            _DigestFunction(data, algorithm),
            ValueWrapper("hex"),
        )


class DigestHex(Function):
    """pgcrypto ``encode(digest(<data>, '<algorithm>'), 'hex')`` — hex digest.

    Computes the hexadecimal (lowercase) string representation of the binary
    hash of *data*.  Convenient for storing/transmitting a digest as text.

    Args:
        data: The value to hash (a field name, ``F()``, or expression).
        algorithm: Hash algorithm name, e.g. ``"sha256"``.  Rendered as a
            SQL string literal.

    Returns:
        ``str`` — a lowercase hex string.
    """

    database_func = _DigestHexFunction

    def __init__(self, data: DataArg, algorithm: str = "sha256") -> None:
        super().__init__(data, algorithm)


class _HMACFunction(PypikaFunction):
    """pypika term emitting ``hmac(<data>, '<key>', '<algorithm>')``."""

    def __init__(self, data: Term, key: str, algorithm: str) -> None:
        super().__init__(
            "HMAC", data, ValueWrapper(key), ValueWrapper(algorithm)
        )


class HMACDigest(Function):
    """pgcrypto ``hmac(<data>, '<key>', '<algorithm>')`` — keyed hash.

    Computes an HMAC (keyed-hash message authentication code) of *data* using
    *key* and the named algorithm.  Unlike :class:`Digest`, an HMAC binds a
    secret key and is not vulnerable to length-extension attacks.

    Args:
        data: The value to hash (a field name, ``F()``, or expression).
        key: The secret key, rendered as a SQL string literal.  Pass an
            application secret — never hardcode it in the model.
        algorithm: Hash algorithm name, e.g. ``"sha256"``.  Rendered as a
            SQL string literal.

    Returns:
        ``bytea``.
    """

    database_func = _HMACFunction

    def __init__(self, data: DataArg, key: str, algorithm: str = "sha256") -> None:
        super().__init__(data, key, algorithm)


class _PGPSymEncryptFunction(PypikaFunction):
    """pypika term emitting ``pgp_sym_encrypt(<data>, '<key>')``."""

    def __init__(self, data: Term, key: str, options: str | None = None) -> None:
        if options is None:
            super().__init__("PGP_SYM_ENCRYPT", data, ValueWrapper(key))
        else:
            super().__init__(
                "PGP_SYM_ENCRYPT", data, ValueWrapper(key), ValueWrapper(options)
            )


class PGPSymEncrypt(Function):
    """pgcrypto ``pgp_sym_encrypt(<data>, '<key>'[, '<options>'])``.

    Symmetric-key PGP encryption of *data* using *key*.  The result is a
    ``bytea`` PGP-encrypted packet suitable for storage in a
    :class:`tortoise_extended.fields.pgcrypto.PGPEncryptedField` column.

    .. note::

        This helper only *renders* the SQL — the actual encryption happens in
        PostgreSQL.  The application supplies the key; the library never sees
        or stores it.

    Args:
        data: The plaintext to encrypt (a field name, ``F()``, or
            expression).
        key: The symmetric passphrase, rendered as a SQL string literal.
        options: Optional pgp_sym_encrypt options string (e.g.
            ``"cipher-algo=aes256, compress-algo=0, disable-mdc"``), rendered
            as a SQL string literal.

    Returns:
        ``bytea`` ciphertext.
    """

    database_func = _PGPSymEncryptFunction

    def __init__(
        self, data: DataArg, key: str, options: str | None = None
    ) -> None:
        super().__init__(data, key, options)


class _PGPSymDecryptFunction(PypikaFunction):
    """pypika term emitting ``pgp_sym_decrypt(<data>, '<key>')``."""

    def __init__(self, data: Term, key: str, options: str | None = None) -> None:
        if options is None:
            super().__init__("PGP_SYM_DECRYPT", data, ValueWrapper(key))
        else:
            super().__init__(
                "PGP_SYM_DECRYPT", data, ValueWrapper(key), ValueWrapper(options)
            )


class PGPSymDecrypt(Function):
    """pgcrypto ``pgp_sym_decrypt(<data>, '<key>'[, '<options>'])``.

    Symmetric-key PGP decryption of *data* using *key*.  *data* is typically
    a :class:`tortoise_extended.fields.pgcrypto.PGPEncryptedField` column
    holding the ciphertext produced by :class:`PGPSymEncrypt`.

    .. note::

        This helper only *renders* the SQL — the actual decryption happens in
        PostgreSQL.  The application supplies the key; the library never sees
        or stores it.

    Args:
        data: The ciphertext to decrypt (a field name, ``F()``, or
            expression).
        key: The symmetric passphrase, rendered as a SQL string literal.
        options: Optional pgp_sym_decrypt options string, rendered as a SQL
            string literal.

    Returns:
        ``str`` plaintext.
    """

    database_func = _PGPSymDecryptFunction

    def __init__(
        self, data: DataArg, key: str, options: str | None = None
    ) -> None:
        super().__init__(data, key, options)


__all__ = [
    "Digest",
    "DigestHex",
    "HMACDigest",
    "PGPSymDecrypt",
    "PGPSymEncrypt",
]
