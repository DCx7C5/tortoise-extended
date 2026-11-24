"""Unit tests for the tortoise ``BooleanField`` typing overlay.

The local ``stubs`` overlay types ``BooleanField`` concretely (null
literal overloads narrow the element type to ``bool`` / ``bool | None``) so
model declarations like ``fields.BooleanField(default=False)`` are fully
known under basedpyright strict mode. These tests pin the runtime surface
that the overlay mirrors.
"""

from collections.abc import AsyncGenerator

import pytest
import tortoise_extended  # noqa: F401 — apply patches
from tortoise import Tortoise, fields, models

from tortoise_extended.fields import CharField
from tests._pg_env import database_url, pg_skip_reason


DB_URL = database_url()


class BooleanPage(models.Model):
    """Model with a non-null and a nullable BooleanField."""

    id = fields.IntField(primary_key=True)
    title = CharField(max_length=255)
    published = fields.BooleanField(default=False)
    archived = fields.BooleanField(null=True)

    class Meta:
        table = "boolean_pages"


class TestBooleanFieldDefinition:
    """BooleanField class attributes and construction parameters."""

    def test_field_type_is_bool(self) -> None:
        field = fields.BooleanField()
        assert field.field_type is bool

    def test_sql_type_is_bool(self) -> None:
        assert fields.BooleanField.SQL_TYPE == "BOOL"

    def test_null_defaults_to_false(self) -> None:
        assert fields.BooleanField().null is False

    def test_null_true_constructor(self) -> None:
        assert fields.BooleanField(null=True).null is True

    def test_default_false_is_preserved(self) -> None:
        assert fields.BooleanField(default=False).default is False

    def test_default_true_is_preserved(self) -> None:
        assert fields.BooleanField(default=True).default is True


class TestBooleanFieldCoercion:
    """to_python_value / to_db_value truthiness coercion."""

    def test_to_python_value_none(self) -> None:
        assert fields.BooleanField().to_python_value(None) is None

    def test_to_python_value_int(self) -> None:
        field = fields.BooleanField()
        assert field.to_python_value(1) is True
        assert field.to_python_value(0) is False

    def test_to_python_value_bool(self) -> None:
        field = fields.BooleanField()
        assert field.to_python_value(True) is True
        assert field.to_python_value(False) is False

    def test_to_db_value_none(self) -> None:
        assert fields.BooleanField().to_db_value(None, None) is None

    def test_to_db_value_int(self) -> None:
        field = fields.BooleanField()
        assert field.to_db_value(1, None) is True
        assert field.to_db_value(0, None) is False


@pytest.fixture(scope="module")
async def _pg_db() -> AsyncGenerator[None, None]:
    """Initialize Tortoise ORM against the live PostgreSQL test database."""
    _ = await Tortoise.init(
        db_url=DB_URL,
        modules={"models": [__name__]},
    )
    conn = Tortoise.get_connection("default")
    await conn.execute_query("DROP TABLE IF EXISTS boolean_pages CASCADE")
    await Tortoise.generate_schemas()
    yield
    await conn.execute_query("DROP TABLE IF EXISTS boolean_pages CASCADE")
    await Tortoise.close_connections()


_PG_SKIP_REASON = pg_skip_reason()
@pytest.mark.skipif(
    _PG_SKIP_REASON is not None,
    reason=_PG_SKIP_REASON or "",
)
class TestBooleanFieldModel:
    """PostgreSQL round-trip: insert / fetch / filter / update through Tortoise."""

    async def test_round_trip(self, _pg_db: None) -> None:
        page = await BooleanPage.create(title="alpha", published=True)
        assert page.published is True
        assert page.archived is None

        fetched = await BooleanPage.get(id=page.id)
        assert fetched.published is True
        assert fetched.archived is None

        # Default applied on create without an explicit value.
        page2 = await BooleanPage.create(title="beta")
        assert page2.published is False

        # Filtering on the boolean column (alpha=True, beta=False).
        assert await BooleanPage.filter(published=True).count() == 1
        assert await BooleanPage.filter(published=False).count() == 1

        # Nullable boolean round-trips to None.
        page3 = await BooleanPage.create(title="gamma", archived=None)
        assert page3.archived is None

        # Update path.
        page2.published = True
        await page2.save(update_fields=["published"])
        assert (await BooleanPage.get(id=page2.id)).published is True
        assert await BooleanPage.filter(published=True).count() == 2
        assert await BooleanPage.filter(published=False).count() == 1
