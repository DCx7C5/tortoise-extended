"""pgcrypto query expression helpers for tortoise-orm.

PostgreSQL's ``pgcrypto`` extension provides cryptographic functions
(``digest``, ``hmac``, ...) that run entirely inside the database.  Tortoise
ORM has no built-in way to reference these functions in a ``QuerySet``, so
this module exposes thin pypika/Tortoise ``Function`` wrappers that render
the correct SQL.

.. important::

    This library performs **no encryption itself**.  These helpers only make
    it easy for your *application* to call pgcrypto from inside Tortoise
    queries.  You must ``CREATE EXTENSION IF NOT EXISTS pgcrypto;`` and call
    the functions explicitly — the library never invokes them behind your
    back.

Usage (illustrative; needs async context + configured DB)::

    from tortoise.expressions import RawSQL

    from tortoise_extended.expressions.pgcrypto import (
        Digest,
        DigestHex,
        HMACDigest,
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

    # HMAC a value with a key that stays out of the SQL text: the raw
    # expression is rendered verbatim and resolved server-side, so the secret
    # never appears in the statement, ``pg_stat_activity`` or the server log.
    mac = await Document.annotate(
        tag=HMACDigest(
            "body",
            RawSQL("current_setting('app.encryption_key')"),
            "sha256",
        )
    ).first()
"""

from typing import TypeAlias

from pypika_tortoise.terms import Term, ValueWrapper
from pypika_tortoise.terms import Function as PypikaFunction
from tortoise.expressions import CombinedExpression, F, Function

from tortoise_extended.exceptions import PgCryptoError

# A value usable as the ``data`` argument to a pgcrypto function: a field name,
# an ``F()`` reference, a combined/plain expression, or a raw pypika term.
DataArg: TypeAlias = str | F | CombinedExpression | Function | Term

# A value usable as the HMAC ``key`` argument.  A plain ``str`` is rendered as
# a quoted SQL string literal (the secret then travels in the statement text);
# any pypika term (``RawSQL``, ``Field``, ...) is rendered verbatim, so the key
# can be resolved server-side, e.g.
# ``RawSQL("current_setting('app.encryption_key')")``.
KeyArg: TypeAlias = DataArg

_PGCRYPTO_ALGORITHMS: frozenset[str] = frozenset(
    {
        "md5",
        "sha1",
        "sha256",
        "sha384",
        "sha512",
        "sha224",
    }
)
"""Built-in pgcrypto hash algorithms.

Only these six are accepted by the ``pgcrypto`` extension's ``digest()``
and ``hmac()`` functions.  Any other value is rejected at query-construction
time (rather than producing a confusing database error at execution).
"""


def _validate_algorithm(algorithm: str) -> None:
    """Reject algorithm names not in the built-in pgcrypto allowlist.

    Args:
        algorithm: The algorithm name to validate.

    Raises:
        PgCryptoError: If *algorithm* is not in ``_PGCRYPTO_ALGORITHMS``.
    """
    if algorithm not in _PGCRYPTO_ALGORITHMS:
        raise PgCryptoError(
            f"Unsupported pgcrypto algorithm {algorithm!r}; "
            f"expected one of {', '.join(sorted(_PGCRYPTO_ALGORITHMS))}"
        )


class _DigestFunction(PypikaFunction):
    """pypika term emitting ``digest(<data>, '<algorithm>')``."""

    def __init__(self, data: Term, algorithm: str) -> None:
        super().__init__("DIGEST", data, ValueWrapper(algorithm))


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
        _validate_algorithm(algorithm)
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
        _validate_algorithm(algorithm)
        super().__init__(data, algorithm)


class _HMACFunction(PypikaFunction):
    """pypika term emitting ``hmac(<data>, <key>, '<algorithm>')``."""

    def __init__(self, data: Term, key: Term | str, algorithm: str) -> None:
        # A plain ``str`` key becomes a quoted SQL string literal (the secret
        # then travels in the statement text); a term such as ``RawSQL`` or
        # ``Field`` is rendered verbatim, so the key can be resolved
        # server-side and never appears in the statement.
        key_term: Term = ValueWrapper(key) if isinstance(key, str) else key
        super().__init__("HMAC", data, key_term, ValueWrapper(algorithm))


class HMACDigest(Function):
    """pgcrypto ``hmac(<data>, '<key>', '<algorithm>')`` — keyed hash.

    Computes an HMAC (keyed-hash message authentication code) of *data* using
    *key* and the named algorithm.  Unlike :class:`Digest`, an HMAC binds a
    secret key and is not vulnerable to length-extension attacks.

    .. warning::

        A **plain ``str`` key is rendered as a SQL string literal**, so the
        secret becomes part of the statement text: it is visible in
        ``pg_stat_activity.query`` to roles with ``pg_read_all_stats``, and is
        captured by ``log_statement`` / ``log_min_duration_statement`` /
        ``auto_explain`` and by connection statement caches.  Pass an
        expression instead — ``RawSQL("current_setting('app.encryption_key')")``
        — to resolve the key server-side and keep it out of the statement.

    Args:
        data: The value to hash (a field name, ``F()``, or expression).
        key: The secret key.  A ``str`` is quoted as a SQL string literal (see
            the warning above); any pypika term (``RawSQL``, ``Field``, ...)
            is rendered verbatim.
        algorithm: Hash algorithm name, e.g. ``"sha256"``.  Rendered as a
            SQL string literal.

    Returns:
        ``bytea``.
    """

    database_func = _HMACFunction

    def __init__(self, data: DataArg, key: KeyArg, algorithm: str = "sha256") -> None:
        _validate_algorithm(algorithm)
        super().__init__(data, key, algorithm)


class _PGPSymFunction(PypikaFunction):
    """pypika term emitting ``pgp_sym_encrypt`` / ``pgp_sym_decrypt``.

    Shared by both wrappers: the only difference is the SQL function name.
    The key follows the :class:`HMACDigest` rule (a ``str`` becomes a quoted
    literal, a term is rendered verbatim), and *options* is appended only when
    given, because both pgcrypto functions take it as an optional third
    argument.
    """

    def __init__(
        self,
        name: str,
        data: Term,
        key: Term | str,
        options: str | None,
    ) -> None:
        key_term: Term = ValueWrapper(key) if isinstance(key, str) else key
        args: list[Term] = [data, key_term]
        if options is not None:
            args.append(ValueWrapper(options))
        super().__init__(name, *args)


class _PGPSymEncryptFunction(_PGPSymFunction):
    """pypika term emitting ``pgp_sym_encrypt(<data>, <key>[, '<options>'])``."""

    def __init__(self, data: Term, key: Term | str, options: str | None) -> None:
        super().__init__("PGP_SYM_ENCRYPT", data, key, options)


class _PGPSymDecryptFunction(_PGPSymFunction):
    """pypika term emitting ``pgp_sym_decrypt(<data>, <key>[, '<options>'])``."""

    def __init__(self, data: Term, key: Term | str, options: str | None) -> None:
        super().__init__("PGP_SYM_DECRYPT", data, key, options)


class PGPSymEncrypt(Function):
    """pgcrypto ``pgp_sym_encrypt(<data>, '<key>'[, '<options>'])`` — ciphertext.

    Encrypts *data* with a symmetric (passphrase) key inside the database and
    returns OpenPGP ciphertext as ``bytea``.  Store the result in a
    ``bytea`` column — :class:`~tortoise_extended.fields.pgcrypto.PGPEncryptedField`
    is exactly that column.

    .. warning::

        A **plain ``str`` key is rendered as a SQL string literal**, so the
        passphrase becomes part of the statement text: visible in
        ``pg_stat_activity.query`` and captured by ``log_statement`` /
        ``auto_explain``.  Pass a term instead —
        ``RawSQL("current_setting('app.encryption_key')")`` — to resolve the
        key server-side and keep it out of the statement.

    Args:
        data: The value to encrypt (a field name, ``F()``, or expression).
        key: The passphrase.  A ``str`` is quoted as a SQL string literal (see
            the warning above); any pypika term is rendered verbatim.
        options: Optional pgcrypto options string, e.g.
            ``"cipher-algo=aes256"``.  Rendered as a SQL string literal.

    Returns:
        ``bytea`` — non-deterministic ciphertext, so the value is not
        searchable or indexable; derive a blind index with :class:`HMACDigest`
        when you need lookups.
    """

    database_func = _PGPSymEncryptFunction

    def __init__(
        self,
        data: DataArg,
        key: KeyArg,
        options: str | None = None,
    ) -> None:
        super().__init__(data, key, options)


class PGPSymDecrypt(Function):
    """pgcrypto ``pgp_sym_decrypt(<data>, '<key>'[, '<options>'])`` — plaintext.

    Decrypts OpenPGP ciphertext produced by :class:`PGPSymEncrypt` (or by any
    compatible encoder) inside the database.  *data* must be a ``bytea``
    expression — a ``bytea`` column, or the result of ``PGPSymEncrypt`` nested
    directly, which is the usual way to verify a stored row decrypts.

    .. warning::

        The passphrase obeys the same rule as :class:`PGPSymEncrypt`: a plain
        ``str`` key is part of the statement text, a term is resolved
        server-side.  The **plaintext** is returned to the client as ``text``,
        so it travels over the connection and lands in the result buffer — the
        library cannot avoid that; decrypt only where you need the value.

    Args:
        data: The ciphertext (a field name, ``F()``, or expression).
        key: The passphrase (see the warning above).
        options: Optional pgcrypto options string.

    Returns:
        ``text`` — the decrypted plaintext.
    """

    database_func = _PGPSymDecryptFunction

    def __init__(
        self,
        data: DataArg,
        key: KeyArg,
        options: str | None = None,
    ) -> None:
        super().__init__(data, key, options)


__all__ = [
    "Digest",
    "DigestHex",
    "HMACDigest",
    "PGPSymDecrypt",
    "PGPSymEncrypt",
]
