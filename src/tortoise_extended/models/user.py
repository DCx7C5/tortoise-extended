"""Django-style email/password auth base model.

Provides :class:`BaseUserModel` — an abstract model with an ``email`` login
identifier, an argon2id ``password_hash`` column, and role flags
(``is_active``/``is_staff``/``is_superuser``) in the Django pattern. Hashing
uses ``argon2-cffi`` (PHC format) and runs in ``asyncio.to_thread`` so the
event loop is never blocked.

Usage::

    from tortoise_extended.models.user import BaseUserModel

    class User(BaseUserModel):
        class Meta:
            table = "users"

    admin = await User.create_superuser("Admin@Example.com", "hunter2")
    await admin.check_password("hunter2")   # True

    user = await User.create_user("alice@example.com", "s3cret!")
    user.email                               # "alice@example.com" (normalized)
    await user.set_password("new-password")  # re-hash in place
    await user.save()
"""

import asyncio
from typing import ClassVar, Self, override

import argon2
from argon2.exceptions import InvalidHashError, VerificationError
from tortoise import fields

from tortoise_extended.models.base import BaseModel

_ARGON2_TIME_COST = 3  # t
_ARGON2_MEMORY_COST = 65536  # m = 64 MiB
_ARGON2_PARALLELISM = 4  # p
_ARGON2_HASH_LEN = 32
_ARGON2_SALT_BYTES = 16
_PASSWORD_HASHER = argon2.PasswordHasher(
    time_cost=_ARGON2_TIME_COST,
    memory_cost=_ARGON2_MEMORY_COST,
    parallelism=_ARGON2_PARALLELISM,
    hash_len=_ARGON2_HASH_LEN,
    salt_len=_ARGON2_SALT_BYTES,
)


class BaseUserModel(BaseModel):
    """Abstract base for email/password authentication (Django naming).

    Email is the login identifier (normalized to lowercase); Django's
    ``username`` field is deliberately omitted. Admins are distinguished by
    the ``is_staff`` / ``is_superuser`` flags on the same table (single-table
    Django pattern) rather than by a separate admin model.

    Attributes:
        email: Login identifier, normalized to lowercase.
        password_hash: argon2id hash, PHC format
            ``$argon2id$v=19$m=65536,t=3,p=4$<salt_b64>$<hash_b64>``.
        is_active: Whether login is allowed.
        is_staff: Whether the user has admin-area access.
        is_superuser: Whether the user has all permissions.
        last_login: Time of the last successful login, if any.
        USERNAME_FIELD: Field used as the login identifier (Django naming).
    """

    email = fields.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        description="Login identifier (normalized to lowercase)",
    )
    password_hash = fields.CharField(
        max_length=255,
        description="argon2id hash, PHC format $argon2id$v=19$m=...,t=...,p=...$salt$hash",
    )
    is_active = fields.BooleanField(
        default=True,
        description="Login allowed",
    )
    is_staff = fields.BooleanField(
        default=False,
        description="Admin-area access",
    )
    is_superuser = fields.BooleanField(
        default=False,
        description="All permissions",
    )
    last_login = fields.DatetimeField(
        null=True,
        default=None,
        use_tz=True,
        description="Last successful login time",
    )

    USERNAME_FIELD: ClassVar[str] = "email"

    class Meta:
        abstract = True

    @classmethod
    async def create_user(cls, email: str, password: str, **kwargs: object) -> Self:
        """Create and persist a regular user.

        Normalizes the email to lowercase, hashes the password, and saves the
        new instance. ``is_active`` defaults to ``True`` and can be overridden
        via ``kwargs``.

        Args:
            email: Login identifier (normalized to lowercase).
            password: Raw password to hash and store.
            **kwargs: Extra model field values.

        Returns:
            The created user instance.

        Raises:
            ValueError: If ``email`` or ``password`` is empty.
        """
        email = cls.normalize_email(email)
        if not email or not password:
            raise ValueError("email and password must not be empty")
        is_active = kwargs.pop("is_active", True)
        user = cls(email=email, is_active=is_active, **kwargs)
        await user.set_password(password)
        await user.save()
        return user

    @classmethod
    async def create_superuser(
        cls, email: str, password: str, **kwargs: object
    ) -> Self:
        """Create and persist a superuser.

        Same as :meth:`create_user` but forces ``is_active``, ``is_staff``,
        and ``is_superuser`` to ``True``.

        Args:
            email: Login identifier (normalized to lowercase).
            password: Raw password to hash and store.
            **kwargs: Extra model field values.

        Returns:
            The created superuser instance.

        Raises:
            ValueError: If ``email`` or ``password`` is empty.
        """
        kwargs["is_active"] = True
        kwargs["is_staff"] = True
        kwargs["is_superuser"] = True
        return await cls.create_user(email, password, **kwargs)

    async def set_password(self, raw_password: str) -> None:
        """Hash ``raw_password`` and assign it to ``password_hash``.

        Does **not** save the model — call :meth:`~tortoise.models.Model.save`
        afterwards to persist the change.

        Args:
            raw_password: Plain-text password to hash.

        Raises:
            ValueError: If ``raw_password`` is empty.
        """
        self.password_hash = await self._hash_password(raw_password)

    async def check_password(self, raw_password: str) -> bool:
        """Verify ``raw_password`` against the stored hash.

        Returns ``False`` when no valid hash is stored (empty or malformed)
        or when the password does not match.

        Args:
            raw_password: Plain-text password to verify.

        Returns:
            ``True`` if the password matches the stored argon2id hash.
        """
        if not self.password_hash:
            return False
        return await self._verify_password(raw_password, self.password_hash)

    @staticmethod
    async def _hash_password(raw_password: str) -> str:
        """Compute an argon2id hash in PHC format.

        Returns a ``$argon2id$v=19$m=65536,t=3,p=4$<salt_b64>$<hash_b64>``
        string. Runs the CPU-bound hash in ``asyncio.to_thread`` so the event
        loop is never blocked.

        Args:
            raw_password: Plain-text password to hash.

        Returns:
            Encoded argon2id hash string.

        Raises:
            ValueError: If ``raw_password`` is empty.
        """
        if not raw_password:
            raise ValueError("password must not be empty")
        return await asyncio.to_thread(_PASSWORD_HASHER.hash, raw_password)

    @staticmethod
    async def _verify_password(raw_password: str, encoded: str) -> bool:
        """Verify ``raw_password`` against a stored PHC argon2id string.

        Runs the CPU-bound ``PasswordHasher.verify`` in ``asyncio.to_thread``.
        Returns ``False`` on malformed/unsupported hashes or on a mismatch.
        ``VerifyMismatchError`` and ``InvalidHashError`` are both caught —
        they are separate exception types in argon2-cffi (the former derives
        from ``VerificationError``, the latter from ``ValueError``), so the
        base class alone is not sufficient.

        Args:
            raw_password: Plain-text password to verify.
            encoded: Stored ``$argon2id$v=19$...`` hash string.

        Returns:
            ``True`` if the password verifies against the stored hash.
        """
        try:
            return await asyncio.to_thread(
                _PASSWORD_HASHER.verify, encoded, raw_password
            )
        except VerificationError, InvalidHashError:
            return False

    @staticmethod
    def normalize_email(email: str) -> str:
        """Normalize a login email to ``strip().lower()``.

        Args:
            email: Raw email address.

        Returns:
            Trimmed, lowercased email.
        """
        return email.strip().lower()

    @override
    def __str__(self) -> str:
        return self.email
