"""Tests for pgcrypto expression wrappers and PGPEncryptedField.

Sections:
  1. ``TestPgcryptoSQL`` — SQL rendering (unit, no DB required).
  2. ``TestPGPEncryptedField`` — SQLite in-memory round-trip for the storage field.
  3. ``TestPgcryptoIntegration`` — live PostgreSQL integration (skipped when PG is down).
"""

import hashlib
import os
import socket

import pytest
from pypika_tortoise import Field
from pypika_tortoise.context import DEFAULT_SQL_CONTEXT

import tortoise_extended  # noqa: F401 — apply patches
from tortoise import Tortoise, fields
from tortoise.models import Model

from tortoise_extended.expressions.pgcrypto import (
    Digest,
    DigestHex,
    HMACDigest,
    PGPSymDecrypt,
    PGPSymEncrypt,
)
from tortoise_extended.expressions.pgcrypto import (
    _DigestFunction,
    _DigestHexFunction,
    _HMACFunction,
    _PGPSymDecryptFunction,
    _PGPSymEncryptFunction,
)
from tortoise_extended.fields.pgcrypto import PGPEncryptedField


# =========================================================================
# Section 1 — SQL rendering (unit, no DB)
# =========================================================================


class TestPgcryptoSQL:
    """Verify the pypika-level SQL rendering for each pgcrypto expression."""

    def test_digest_renders_function_name_and_quoted_algorithm(self) -> None:
        """Digest('body', 'sha256') -> DIGEST('body', 'sha256')."""
        sql = _DigestFunction(Field("body"), "sha256").get_sql(DEFAULT_SQL_CONTEXT)
        upper = sql.upper()
        assert "DIGEST(" in upper
        assert "'sha256'" in sql
        # data column reference is quoted identifier, not a string literal
        assert '"body"' in sql or "body" in sql

    def test_digest_hex_renders_encode_wrapper(self) -> None:
        """DigestHex('body', 'sha256') -> ENCODE(DIGEST(...), 'hex')."""
        sql = _DigestHexFunction(Field("body"), "sha256").get_sql(DEFAULT_SQL_CONTEXT)
        upper = sql.upper()
        assert "ENCODE(" in upper
        assert "DIGEST(" in upper
        assert "'hex'" in sql
        assert "'sha256'" in sql

    def test_hmac_renders_all_three_args(self) -> None:
        """HMACDigest('body', 'key', 'sha256') -> HMAC(..., 'key', 'sha256')."""
        sql = _HMACFunction(Field("body"), "my-secret", "sha256").get_sql(
            DEFAULT_SQL_CONTEXT
        )
        upper = sql.upper()
        assert "HMAC(" in upper
        assert "'my-secret'" in sql
        assert "'sha256'" in sql

    def test_pgp_sym_encrypt_without_options(self) -> None:
        """PGPSymEncrypt('body', 'key') -> PGP_SYM_ENCRYPT(..., 'key')."""
        sql = _PGPSymEncryptFunction(Field("body"), "passphrase").get_sql(
            DEFAULT_SQL_CONTEXT
        )
        upper = sql.upper()
        assert "PGP_SYM_ENCRYPT(" in upper
        assert "'passphrase'" in sql
        # no options argument should appear
        assert "cipher" not in sql.lower()

    def test_pgp_sym_encrypt_with_options(self) -> None:
        """PGPSymEncrypt with options includes the options string literal."""
        sql = _PGPSymEncryptFunction(
            Field("body"), "passphrase", "cipher-algo=aes256"
        ).get_sql(DEFAULT_SQL_CONTEXT)
        upper = sql.upper()
        assert "PGP_SYM_ENCRYPT(" in upper
        assert "'passphrase'" in sql
        assert "'cipher-algo=aes256'" in sql

    def test_pgp_sym_decrypt_without_options(self) -> None:
        """PGPSymDecrypt('body', 'key') -> PGP_SYM_DECRYPT(..., 'key')."""
        sql = _PGPSymDecryptFunction(Field("body"), "passphrase").get_sql(
            DEFAULT_SQL_CONTEXT
        )
        upper = sql.upper()
        assert "PGP_SYM_DECRYPT(" in upper
        assert "'passphrase'" in sql

    def test_pgp_sym_decrypt_with_options(self) -> None:
        """PGPSymDecrypt with options includes the options string literal."""
        sql = _PGPSymDecryptFunction(
            Field("body"), "passphrase", "convert-crlf=1"
        ).get_sql(DEFAULT_SQL_CONTEXT)
        upper = sql.upper()
        assert "PGP_SYM_DECRYPT(" in upper
        assert "'convert-crlf=1'" in sql

    def test_algorithm_and_key_are_string_literals(self) -> None:
        """Algorithm must render as a quoted string literal, never a bare column ref."""
        # Field("body") is the data column; "md5" is the algorithm.
        sql = _DigestFunction(Field("body"), "md5").get_sql(DEFAULT_SQL_CONTEXT)
        # 'md5' rendered as a single-quoted string literal ...
        assert "'md5'" in sql
        # ... and the column name "body" must NOT appear as a string literal too.
        assert "'body'" not in sql

    def test_data_field_is_column_reference(self) -> None:
        """The data argument must be a column reference, not a string literal."""
        sql = _DigestFunction(Field("my_column"), "sha256").get_sql(DEFAULT_SQL_CONTEXT)
        # data column appears as a bare identifier ...
        assert "my_column" in sql
        assert "'my_column'" not in sql
        # ... while the algorithm stays a string literal.
        assert "'sha256'" in sql
        assert "sha256" in sql

    def test_hmac_key_and_algo_are_string_literals(self) -> None:
        """Both key and algorithm in HMAC are SQL string literals; data is a column ref."""
        sql = _HMACFunction(Field("data_col"), "secret", "sha512").get_sql(
            DEFAULT_SQL_CONTEXT
        )
        assert "'secret'" in sql
        assert "'sha512'" in sql
        # data column is a bare identifier, not a literal
        assert "'data_col'" not in sql
        assert "data_col" in sql

    def test_outer_function_classes_also_render(self) -> None:
        """Tortoise Function subclasses (Digest, etc.) produce valid SQL when
        directly instantiated and resolved via pypika.

        This tests the full Function->database_func chain.
        """
        # Digest wraps _DigestFunction — build and render directly
        digest = Digest("body", "sha256")
        # Access the underlying pypika function via the database_func mechanism
        func = digest._get_function_field(Field("body"), "sha256")
        sql = func.get_sql(DEFAULT_SQL_CONTEXT)
        assert "DIGEST(" in sql.upper()
        assert "'sha256'" in sql


# =========================================================================
# Section 2 — Encrypted-field storage (SQLite unit)
# =========================================================================


class SecureNote(Model):
    """Minimal model for testing PGPEncryptedField on SQLite."""

    id = fields.IntField(primary_key=True)
    body = fields.TextField()
    secret = PGPEncryptedField(null=True)

    class Meta:
        table = "test_secure_notes"


class TestPGPEncryptedField:
    """SQLite round-trip: PGPEncryptedField stores bytes without encryption."""

    async def test_bytes_roundtrip(self) -> None:
        """Create a row with ciphertext bytes and read them back unchanged."""
        await Tortoise.init(
            db_url="sqlite::memory:",
            modules={"models": ["tests.test_pgcrypto"]},
        )
        await Tortoise.generate_schemas()
        try:
            ciphertext = b"\x89\x50\x4e\x47\x0d\x0a\x1a\x0a"  # arbitrary bytes
            note = await SecureNote.create(body="hello", secret=ciphertext)
            fetched = await SecureNote.get(id=note.id)
            assert fetched.secret == ciphertext
        finally:
            await Tortoise.close_connections()

    async def test_null_roundtrip(self) -> None:
        """A NULL secret stores as None and reads back as None."""
        await Tortoise.init(
            db_url="sqlite::memory:",
            modules={"models": ["tests.test_pgcrypto"]},
        )
        await Tortoise.generate_schemas()
        try:
            note = await SecureNote.create(body="empty", secret=None)
            fetched = await SecureNote.get(id=note.id)
            assert fetched.secret is None
        finally:
            await Tortoise.close_connections()

    async def test_column_is_blob_on_sqlite(self) -> None:
        """The SQLite column type for PGPEncryptedField is BLOB."""
        await Tortoise.init(
            db_url="sqlite::memory:",
            modules={"models": ["tests.test_pgcrypto"]},
        )
        await Tortoise.generate_schemas()
        try:
            conn = Tortoise.get_connection("default")
            result = await conn.execute_query(
                "SELECT sql FROM sqlite_master WHERE name = 'test_secure_notes'"
            )
            create_sql = result[1][0]["sql"].upper()
            assert "BLOB" in create_sql
        finally:
            await Tortoise.close_connections()

    async def test_no_encryption_happens(self) -> None:
        """The field stores raw bytes — no encryption is applied by the field itself."""
        await Tortoise.init(
            db_url="sqlite::memory:",
            modules={"models": ["tests.test_pgcrypto"]},
        )
        await Tortoise.generate_schemas()
        try:
            plaintext_bytes = b"not-encrypted-at-all"
            note = await SecureNote.create(body="test", secret=plaintext_bytes)
            fetched = await SecureNote.get(id=note.id)
            # field does NOT encrypt — stored bytes must be identical
            assert fetched.secret == plaintext_bytes
            assert fetched.secret != b""  # non-trivial check
        finally:
            await Tortoise.close_connections()


# =========================================================================
# Section 3 — Live PostgreSQL integration
# =========================================================================

# ---------------------------------------------------------------------------
# Gating — skip only the live-PG integration section when PG is unavailable
# ---------------------------------------------------------------------------

_DB_URL = os.environ.get(
    "TORTOISE_TEST_DB",
    "postgres://postgres:postgres@localhost:5433/tortoise_test",
)


def _pg_available() -> bool:
    """Quick check — can we connect to the test PG?"""
    try:
        sock = socket.create_connection(("localhost", 5433), timeout=2)
        sock.close()
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Test models — defined fresh for integration tests
# ---------------------------------------------------------------------------


class SecretDocument(Model):
    """Document with plaintext and encrypted secret fields."""

    id = fields.IntField(primary_key=True)
    plain_text = fields.TextField()
    secret = PGPEncryptedField(null=True)

    class Meta:
        table = "test_secret_documents"


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestPgcryptoIntegration:
    """Live PostgreSQL tests for pgcrypto functions and PGPEncryptedField.

    Skipped (entire class) when PostgreSQL is not available on
    ``localhost:5433``, so unit/sqlite tests still run on non-PG machines.

    The ``_init_pg_db`` fixture is ``function``-scoped because each SQLite
    unit test in this module calls ``Tortoise.init``/``close_connections``
    itself, so a module-scoped PG connection would be torn down mid-suite.
    """

    pytestmark = pytest.mark.skipif(
        not _pg_available(),
        reason="PostgreSQL not available on localhost:5433",
    )

    @pytest.fixture(autouse=True)
    async def _init_pg_db(self):
        """Re-initialize Tortoise ORM against live PostgreSQL per test."""
        await Tortoise.init(
            db_url=_DB_URL,
            modules={"models": ["tests.test_pgcrypto"]},
        )
        await Tortoise.generate_schemas()
        yield
        conn = Tortoise.get_connection("default")
        await conn.execute_query("DROP TABLE IF EXISTS test_secret_documents CASCADE")
        await Tortoise.close_connections()

    async def test_digest_hex_roundtrip(self) -> None:
        """DigestHex produces a 64-char hex string matching hashlib.sha256."""
        plaintext = "hello, pgcrypto!"
        expected_hex = hashlib.sha256(plaintext.encode()).hexdigest()
        assert len(expected_hex) == 64

        doc = await SecretDocument.create(
            plain_text=plaintext, secret=None
        )
        result = await SecretDocument.annotate(
            h=DigestHex("plain_text", "sha256")
        ).get(id=doc.id)

        assert result.h is not None
        assert len(str(result.h)) == 64
        assert str(result.h) == expected_hex

    async def test_pgp_sym_encrypt_decrypt_roundtrip(self) -> None:
        """Encrypt then decrypt returns the original plaintext."""
        plaintext = "sensitive data here"
        passphrase = "test-passphrase-123"

        # Insert plaintext, then UPDATE secret with pgp_sym_encrypt
        doc = await SecretDocument.create(
            plain_text=plaintext, secret=None
        )
        await SecretDocument.filter(id=doc.id).update(
            secret=PGPSymEncrypt("plain_text", passphrase)
        )

        # Decrypt the stored ciphertext
        result = await SecretDocument.annotate(
            dec=PGPSymDecrypt("secret", passphrase)
        ).get(id=doc.id)

        assert result.dec == plaintext

    async def test_wrong_key_raises(self) -> None:
        """Decrypting with the wrong key raises a PostgreSQL error."""
        plaintext = "protected content"
        correct_key = "right-key"
        wrong_key = "wrong-key"

        doc = await SecretDocument.create(
            plain_text=plaintext, secret=None
        )
        await SecretDocument.filter(id=doc.id).update(
            secret=PGPSymEncrypt("plain_text", correct_key)
        )

        with pytest.raises(Exception):
            await SecretDocument.annotate(
                dec=PGPSymDecrypt("secret", wrong_key)
            ).get(id=doc.id)

    async def test_pg_encrypted_field_stores_bytes(self) -> None:
        """PGPEncryptedField stores raw ciphertext bytes on real PostgreSQL (BYTEA)."""
        ciphertext = b"\x01\x02\x03\x04\x05"
        doc = await SecretDocument.create(
            plain_text="unused", secret=ciphertext
        )
        fetched = await SecretDocument.get(id=doc.id)
        assert fetched.secret == ciphertext

    async def test_pgp_sym_encrypt_with_options(self) -> None:
        """pgp_sym_encrypt with cipher-algo option round-trips correctly."""
        plaintext = "aes256-test"
        passphrase = "options-key"

        doc = await SecretDocument.create(
            plain_text=plaintext, secret=None
        )
        await SecretDocument.filter(id=doc.id).update(
            secret=PGPSymEncrypt("plain_text", passphrase, "cipher-algo=aes256")
        )

        result = await SecretDocument.annotate(
            dec=PGPSymDecrypt("secret", passphrase)
        ).get(id=doc.id)

        assert result.dec == plaintext

    async def test_digest_raw_binary(self) -> None:
        """Digest returns a bytea value (raw binary hash)."""
        plaintext = "binary-test"
        doc = await SecretDocument.create(
            plain_text=plaintext, secret=None
        )
        result = await SecretDocument.annotate(
            raw=Digest("plain_text", "sha256")
        ).get(id=doc.id)

        # Raw digest is bytes-like; sha256 produces 32 bytes
        assert result.raw is not None
        raw_bytes = bytes(result.raw) if not isinstance(result.raw, bytes) else result.raw
        assert len(raw_bytes) == 32

    async def test_hmac_digest(self) -> None:
        """HMACDigest produces a bytea value (keyed hash)."""
        plaintext = "hmac-test"
        key = "shared-secret"
        doc = await SecretDocument.create(
            plain_text=plaintext, secret=None
        )
        result = await SecretDocument.annotate(
            mac=HMACDigest("plain_text", key, "sha256")
        ).get(id=doc.id)

        assert result.mac is not None
        mac_bytes = bytes(result.mac) if not isinstance(result.mac, bytes) else result.mac
        assert len(mac_bytes) == 32  # SHA-256 HMAC is 32 bytes
