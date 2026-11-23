"""Tests for pgcrypto expression wrappers (hash/digest only).

Sections:
  1. ``TestPgcryptoSQL`` — SQL rendering (unit, no DB required).
  2. ``TestPgcryptoIntegration`` — live PostgreSQL integration (skipped when PG is down).
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
)
from tortoise_extended.exceptions import PgCryptoError
from tortoise_extended.expressions.pgcrypto import (
    _DigestFunction,
    _DigestHexFunction,
    _HMACFunction,
)


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
# Section 1b — Algorithm allowlist (SEC-M03)
# =========================================================================


class TestPgcryptoAlgorithmAllowlist:
    """SEC-M03: only built-in pgcrypto algorithms are accepted."""

    def test_allows_all_six_algorithms(self) -> None:
        for algo in ("md5", "sha1", "sha256", "sha224", "sha384", "sha512"):
            assert Digest("body", algo) is not None

    def test_digest_rejects_unknown_algorithm(self) -> None:
        with pytest.raises(PgCryptoError, match="Unsupported pgcrypto algorithm"):
            Digest("body", "nope")

    def test_digest_hex_rejects_unknown_algorithm(self) -> None:
        with pytest.raises(PgCryptoError, match="Unsupported pgcrypto algorithm"):
            DigestHex("body", "nope")

    def test_hmac_digest_rejects_unknown_algorithm(self) -> None:
        with pytest.raises(PgCryptoError, match="Unsupported pgcrypto algorithm"):
            HMACDigest("body", "key", "nope")

    def test_rejects_sql_injection_attempt(self) -> None:
        payload = "sha256'; DROP TABLE foo; --"
        with pytest.raises(PgCryptoError, match="Unsupported pgcrypto algorithm"):
            Digest("body", payload)


# =========================================================================
# Section 2 — Live PostgreSQL integration
# =========================================================================

# ---------------------------------------------------------------------------
# Gating — skip only the live-PG integration section when PG is unavailable
# ---------------------------------------------------------------------------

_DB_URL = os.environ.get(
    "TORTOISE_TEST_DB",
    "postgres://postgres:postgres@localhost:5434/tortoise_test",
)


def _pg_available() -> bool:
    """Quick check — can we connect to the test PG?"""
    try:
        sock = socket.create_connection(("localhost", 5434), timeout=2)
        sock.close()
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Test models — defined fresh for integration tests
# ---------------------------------------------------------------------------


class SimpleDocument(Model):
    """Minimal document model for testing hash/digest functions on PostgreSQL."""

    id = fields.IntField(primary_key=True)
    plain_text = fields.TextField()

    class Meta:
        table = "test_simple_documents"


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestPgcryptoIntegration:
    """Live PostgreSQL tests for pgcrypto hash/digest functions.

    Skipped (entire class) when PostgreSQL is not available on
    ``localhost:5434``, so the pure unit tests still run on machines without
    PostgreSQL.

    The ``_init_pg_db`` fixture is ``function``-scoped so every test starts
    from an empty ``test_simple_documents`` table: it creates the schema and
    drops it again on teardown.
    """

    pytestmark = pytest.mark.skipif(
        not _pg_available(),
        reason="PostgreSQL not available on localhost:5434",
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
        await conn.execute_query("DROP TABLE IF EXISTS test_simple_documents CASCADE")
        await Tortoise.close_connections()

    async def test_digest_hex_roundtrip(self) -> None:
        """DigestHex produces a 64-char hex string matching hashlib.sha256."""
        plaintext = "hello, pgcrypto!"
        expected_hex = hashlib.sha256(plaintext.encode()).hexdigest()
        assert len(expected_hex) == 64

        doc = await SimpleDocument.create(plain_text=plaintext)
        result = await SimpleDocument.annotate(h=DigestHex("plain_text", "sha256")).get(
            id=doc.id
        )

        assert result.h is not None
        assert len(str(result.h)) == 64
        assert str(result.h) == expected_hex

    async def test_digest_raw_binary(self) -> None:
        """Digest returns a bytea value (raw binary hash)."""
        plaintext = "binary-test"
        doc = await SimpleDocument.create(plain_text=plaintext)
        result = await SimpleDocument.annotate(raw=Digest("plain_text", "sha256")).get(
            id=doc.id
        )

        # Raw digest is bytes-like; sha256 produces 32 bytes
        assert result.raw is not None
        raw_bytes = (
            bytes(result.raw) if not isinstance(result.raw, bytes) else result.raw
        )
        assert len(raw_bytes) == 32

    async def test_hmac_digest(self) -> None:
        """HMACDigest produces a bytea value (keyed hash)."""
        plaintext = "hmac-test"
        key = "shared-secret"
        doc = await SimpleDocument.create(plain_text=plaintext)
        result = await SimpleDocument.annotate(
            mac=HMACDigest("plain_text", key, "sha256")
        ).get(id=doc.id)

        assert result.mac is not None
        mac_bytes = (
            bytes(result.mac) if not isinstance(result.mac, bytes) else result.mac
        )
        assert len(mac_bytes) == 32  # SHA-256 HMAC is 32 bytes
