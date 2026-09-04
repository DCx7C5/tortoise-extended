"""Unit tests for CharField (TEXT-backed character field).

Covers SQL type, max_length enforcement, validators, constraints dict,
and null handling. No database connection required.
"""

import pytest
from tortoise.validators import MaxLengthValidator

from tortoise_extended.fields.char import CharField


# ---------------------------------------------------------------------------
# SQL type
# ---------------------------------------------------------------------------


class TestCharFieldSQLType:
    """Verify CharField emits TEXT on all backends."""

    def test_sql_type(self) -> None:
        """SQL_TYPE should be 'TEXT', not VARCHAR."""
        assert CharField.SQL_TYPE == "TEXT"

    def test_field_type(self) -> None:
        """field_type should be str."""
        assert CharField.field_type is str

# ---------------------------------------------------------------------------
# max_length
# ---------------------------------------------------------------------------


class TestCharFieldMaxLength:
    """Verify max_length is stored and enforced."""

    def test_max_length_stored(self) -> None:
        """max_length should be stored as an integer."""
        field = CharField(max_length=200)
        assert field.max_length == 200

    def test_max_length_coerced_to_int(self) -> None:
        """Float-like max_length should be coerced to int."""
        field = CharField(max_length=100.0)  # type: ignore[arg-type]
        assert field.max_length == 100

    def test_max_length_zero_raises(self) -> None:
        """max_length < 1 should raise ValueError."""
        with pytest.raises(ValueError, match="'max_length' must be >= 1"):
            CharField(max_length=0)

    def test_max_length_negative_raises(self) -> None:
        """Negative max_length should raise ValueError."""
        with pytest.raises(ValueError, match="'max_length' must be >= 1"):
            CharField(max_length=-1)


# ---------------------------------------------------------------------------
# constraints dict
# ---------------------------------------------------------------------------


class TestCharFieldConstraints:
    """Verify constraints property exposes max_length."""

    def test_constraints_dict(self) -> None:
        """constraints should be a dict with max_length."""
        field = CharField(max_length=150)
        assert field.constraints == {"max_length": 150}

    def test_constraints_reflect_length(self) -> None:
        """constraints dict should use the actual max_length."""
        field = CharField(max_length=300)
        assert field.constraints["max_length"] == 300


# ---------------------------------------------------------------------------
# validators
# ---------------------------------------------------------------------------


class TestCharFieldValidators:
    """Verify MaxLengthValidator is wired up."""

    def test_validators_present(self) -> None:
        """Field should have at least one validator."""
        field = CharField(max_length=100)
        assert len(field.validators) >= 1

    def test_max_length_validator_wired(self) -> None:
        """First validator should be MaxLengthValidator."""
        field = CharField(max_length=100)
        validator = field.validators[0]
        assert isinstance(validator, MaxLengthValidator)

    def test_validator_max_length_matches(self) -> None:
        """MaxLengthValidator should use the field's max_length."""
        field = CharField(max_length=200)
        validator = field.validators[0]
        assert isinstance(validator, MaxLengthValidator)
        assert validator.max_length == 200


# ---------------------------------------------------------------------------
# null handling
# ---------------------------------------------------------------------------


class TestCharFieldNullMode:
    """Verify null parameter."""

    def test_not_null_by_default(self) -> None:
        """Default should be not-null."""
        field = CharField(max_length=100)
        assert field.null is False

    def test_null_allowed(self) -> None:
        """null=True should set null on the field."""
        field = CharField(max_length=100, null=True)
        assert field.null is True


# ---------------------------------------------------------------------------
# constructor kwargs passthrough
# ---------------------------------------------------------------------------


class TestCharFieldKwargs:
    """Verify kwargs are forwarded to the base Field."""

    def test_unique(self) -> None:
        """unique=True should be accepted (unlike TextField)."""
        field = CharField(max_length=100, unique=True)
        assert field.unique is True

    def test_index(self) -> None:
        """db_index=True should be accepted and stored as index."""
        field = CharField(max_length=100, db_index=True)
        assert field.index is True

    def test_description(self) -> None:
        """description should be stored."""
        field = CharField(max_length=100, description="A slug")
        assert field.description == "A slug"

    def test_default(self) -> None:
        """Default value should be stored."""
        field = CharField(max_length=100, default="hello")
        assert field.default == "hello"
