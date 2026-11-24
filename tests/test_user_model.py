"""Tests for ``BaseUserModel`` — Django-style email/password auth.

Covers user creation (email normalization, role flags), argon2id password
hashing, DB persistence round-trips, and login bookkeeping. Runs against the
live PostgreSQL test database (see ``docker-compose.dev.yml``).
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from tortoise import Tortoise
from tortoise.exceptions import IntegrityError

from tortoise_extended.fields import CharField
from tortoise_extended.models import BaseUserModel
from tests._pg_env import database_url, pg_skip_reason

# --- Test models ---------------------------------------------------------


class User(BaseUserModel):
    display_name = CharField(max_length=64, null=True)

    class Meta:
        table = "users"


# --- Config — skip entire module if PG is not available --------------------

DB_URL = database_url()


_PG_SKIP_REASON = pg_skip_reason()
pytestmark = pytest.mark.skipif(
    _PG_SKIP_REASON is not None,
    reason=_PG_SKIP_REASON or "",
)


# --- Fixtures -------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
async def _init_db() -> AsyncGenerator[None, None]:  # pyright: ignore[reportUnusedFunction]
    """Initialize Tortoise ORM against the live PostgreSQL test database."""
    _ = await Tortoise.init(
        db_url=DB_URL,
        modules={"models": ["tests.test_user_model"]},
    )
    conn = Tortoise.get_connection("default")
    await conn.execute_query("DROP TABLE IF EXISTS users CASCADE")
    await Tortoise.generate_schemas()
    yield
    await conn.execute_query("DROP TABLE IF EXISTS users CASCADE")
    await Tortoise.close_connections()


@pytest.fixture(autouse=True)
async def _clean_tables() -> AsyncGenerator[None, None]:
    """Truncate the table before each test."""
    conn = Tortoise.get_connection("default")
    await conn.execute_query("TRUNCATE users CASCADE")
    yield


# --- Tests -----------------------------------------------------------------


class TestModelShape:
    """Abstract base contract and exports."""

    async def test_abstract_meta(self) -> None:
        assert BaseUserModel._meta.abstract is True

    async def test_username_field(self) -> None:
        assert BaseUserModel.USERNAME_FIELD == "email"

    async def test_str_returns_email(self) -> None:
        user = await User.create_user("alice@example.com", "s3cret!")
        assert str(user) == "alice@example.com"

    async def test_exports_top_level(self) -> None:
        from tortoise_extended import BaseUserModel as TopBase

        assert TopBase is BaseUserModel

    async def test_normalize_email(self) -> None:
        assert (
            BaseUserModel.normalize_email("  Alice@Example.COM  ")
            == "alice@example.com"
        )


class TestUserCreation:
    """create_user / create_superuser behavior."""

    async def test_create_user_normalizes_email(self) -> None:
        user = await User.create_user("  Alice@Example.COM  ", "s3cret!")
        assert user.email == "alice@example.com"

    async def test_create_user_default_flags(self) -> None:
        user = await User.create_user("alice@example.com", "s3cret!")
        assert user.is_active is True
        assert user.is_staff is False
        assert user.is_superuser is False

    async def test_create_user_accepts_extra_fields(self) -> None:
        user = await User.create_user(
            "alice@example.com", "s3cret!", display_name="Alice"
        )
        assert user.display_name == "Alice"

    async def test_create_superuser_sets_flags(self) -> None:
        admin = await User.create_superuser("Admin@Example.com", "hunter2")
        assert admin.email == "admin@example.com"
        assert admin.is_active is True
        assert admin.is_staff is True
        assert admin.is_superuser is True

    async def test_create_user_empty_email_raises(self) -> None:
        with pytest.raises(ValueError):
            _ = await User.create_user("", "s3cret!")

    async def test_create_user_empty_password_raises(self) -> None:
        with pytest.raises(ValueError):
            _ = await User.create_user("alice@example.com", "")

    async def test_duplicate_email_raises_integrity_error(self) -> None:
        _ = await User.create_user("alice@example.com", "s3cret!")
        with pytest.raises(IntegrityError):
            _ = await User.create_user("Alice@Example.COM", "other-password")


class TestPasswordHashing:
    """argon2id hash generation and verification."""

    async def test_set_password_hashes(self) -> None:
        user = User(email="alice@example.com")
        await user.set_password("correct horse battery staple")
        assert user.password_hash.startswith("$argon2id$v=19$")

    async def test_check_password_correct(self) -> None:
        user = User(email="alice@example.com")
        await user.set_password("correct horse battery staple")
        assert await user.check_password("correct horse battery staple") is True

    async def test_check_password_wrong(self) -> None:
        user = User(email="alice@example.com")
        await user.set_password("correct horse battery staple")
        assert await user.check_password("wrong password") is False

    async def test_set_password_empty_raises(self) -> None:
        user = User(email="alice@example.com")
        with pytest.raises(ValueError):
            await user.set_password("")

    async def test_same_password_different_salts(self) -> None:
        first = User(email="a@example.com")
        second = User(email="b@example.com")
        await first.set_password("shared-password")
        await second.set_password("shared-password")
        assert first.password_hash != second.password_hash

    async def test_check_password_malformed_hash_false(self) -> None:
        user = User(email="alice@example.com", password_hash="not-a-valid-hash")
        assert await user.check_password("anything") is False


class TestPersistence:
    """Hash survives DB round-trips."""

    async def test_hash_round_trips(self) -> None:
        user = await User.create_user("alice@example.com", "s3cret!")
        fetched = await User.get(email="alice@example.com")
        assert fetched.password_hash == user.password_hash
        assert await fetched.check_password("s3cret!") is True

    async def test_check_password_empty_hash_false(self) -> None:
        user = await User.create(
            email="bob@example.com", password_hash="", is_active=True
        )
        assert user.password_hash == ""
        assert await user.check_password("anything") is False


class TestLoginBookkeeping:
    """last_login timestamp persistence."""

    async def test_last_login_round_trip(self) -> None:
        user = await User.create_user("alice@example.com", "s3cret!")
        last_login = datetime.now(UTC).replace(microsecond=0)
        user.last_login = last_login
        await user.save()
        fetched = await User.get(email="alice@example.com")
        assert fetched.last_login == last_login
