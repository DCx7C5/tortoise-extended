"""Unit tests for the graph node primitives in ``tortoise_extended.models.node``.

Covers the split between the pk-free :class:`NodeMixin` and the UUID7
identity base :class:`BaseNode`, the ``__init_subclass__`` guards, and the
UUID4 ``BaseGraphNodeModel`` compatibility alias.  No database is used.
"""

from typing import get_args
from uuid import UUID, uuid4

import pytest
from tortoise import fields

from tortoise_extended.fields.uuid import UUID4Field, UUID7Field
from tortoise_extended.models import BaseGraphNodeModel, BaseModel, BaseNode, NodeMixin
from tortoise_extended.models.mixins import TimestampMixin


class UnitHybridNode(BaseModel, NodeMixin):
    """(BaseModel, NodeMixin) hybrid declaring its own BigInt parent key."""

    parent_id = fields.BigIntField(null=True, db_index=True)

    class Meta:
        table = "node_mixin_unit_hybrid"
        indexes = ()


class TwoIdentityBases(BaseModel, BaseNode):
    """Documented caveat: two identity bases — the later base's ``id`` wins.

    ``ModelMeta`` merges abstract-base fields by plain assignment, so
    ``BaseNode`` (listed second) overwrites ``BaseModel``'s BigInt ``id``
    with its UUID7 field.  Tortoise does not raise for this; the test pins
    the measured behaviour so a future upgrade that starts rejecting the
    combination is caught.
    """

    class Meta:
        abstract = True


class TwoIdentityBasesSwapped(BaseNode, BaseModel):
    """Same caveat, reversed basis order: ``BaseModel`` is later, so BigInt wins."""

    class Meta:
        abstract = True


class TestBaseNodeIdentity:
    """BaseNode: UUID7 identity plus inherited timestamps."""

    def test_pk_is_uuid7_field(self) -> None:
        pk_field = BaseNode._meta.fields_map["id"]
        assert isinstance(pk_field, UUID7Field)
        assert pk_field.pk is True
        assert BaseNode._meta.pk_attr == "id"

    def test_auto_generates_time_ordered_uuid7(self) -> None:
        first = BaseNode(name="first")
        second = BaseNode(name="second")
        assert first.id.version == 7
        assert second.id.version == 7
        # Two consecutively generated ids come out time-ordered.
        assert first.id < second.id

    def test_timestamps_inherited_from_timestamp_mixin(self) -> None:
        assert issubclass(BaseNode, TimestampMixin)
        created_at = BaseNode._meta.fields_map["created_at"]
        updated_at = BaseNode._meta.fields_map["updated_at"]
        assert isinstance(created_at, fields.DatetimeField)
        assert isinstance(updated_at, fields.DatetimeField)
        assert created_at.auto_now_add is True
        assert updated_at.auto_now is True


class TestNodeMixinIsPkFree:
    """NodeMixin carries the shared node fields only — no identity, no time."""

    def test_has_no_primary_key_or_id_field(self) -> None:
        assert "id" not in NodeMixin._meta.fields_map
        assert [
            name for name, field in NodeMixin._meta.fields_map.items() if field.pk
        ] == []

    def test_has_no_timestamp_fields(self) -> None:
        assert "created_at" not in NodeMixin._meta.fields_map
        assert "updated_at" not in NodeMixin._meta.fields_map

    def test_parent_id_is_annotation_only(self) -> None:
        assert "parent_id" not in NodeMixin._meta.fields_map
        annotation = NodeMixin.__annotations__["parent_id"]
        assert set(get_args(annotation)) == {UUID, int, type(None)}

    def test_carries_only_the_shared_node_fields(self) -> None:
        assert set(NodeMixin._meta.fields_map) == {
            "child_count",
            "depth",
            "is_root",
            "metadata_json",
            "namespace",
        }


class TestIdentityCombination:
    """A mixin hybrid keeps exactly one pk; two identity bases merge silently."""

    def test_hybrid_has_exactly_one_pk_from_the_identity_base(self) -> None:
        fields_map = UnitHybridNode._meta.fields_map
        assert UnitHybridNode._meta.pk_attr == "id"
        assert [name for name, field in fields_map.items() if field.pk] == ["id"]
        assert isinstance(fields_map["id"], fields.BigIntField)
        assert isinstance(fields_map["parent_id"], fields.BigIntField)

    def test_two_identity_bases_do_not_raise_and_later_base_wins(self) -> None:
        """Documented caveat: the later base's ``id`` overwrites the earlier one's."""
        assert isinstance(TwoIdentityBases._meta.fields_map["id"], UUID7Field)
        assert isinstance(
            TwoIdentityBasesSwapped._meta.fields_map["id"], fields.BigIntField
        )


class TestInitSubclassGuard:
    """__init_subclass__ enforces Meta.indexes and a concrete parent_id field."""

    def test_concrete_without_indexes_raises(self) -> None:
        with pytest.raises(NotImplementedError, match="redeclare indexes"):

            class NoIndexes(BaseModel, NodeMixin):
                parent_id = fields.BigIntField(null=True)

                class Meta:
                    table = "node_mixin_unit_no_indexes"

    def test_concrete_without_parent_id_raises(self) -> None:
        with pytest.raises(NotImplementedError, match="must declare a parent_id field"):

            class NoParentId(BaseModel, NodeMixin):
                class Meta:
                    table = "node_mixin_unit_no_parent"
                    indexes = ()

    def test_empty_indexes_opt_out_is_accepted(self) -> None:
        assert UnitHybridNode.Meta.indexes == ()
        assert UnitHybridNode._meta.indexes == ()


class TestGraphNodeCompatibilityAlias:
    """BaseGraphNodeModel stays on UUID4 identity."""

    def test_ids_are_still_uuid4(self) -> None:
        id_field = BaseGraphNodeModel._meta.fields_map["id"]
        assert isinstance(id_field, UUID4Field)
        assert id_field.default is uuid4
        node = BaseGraphNodeModel(name="legacy")
        assert node.id.version == 4

    def test_alias_extends_base_node(self) -> None:
        assert issubclass(BaseGraphNodeModel, BaseNode)
        assert BaseGraphNodeModel._meta.pk_attr == "id"
        assert [
            name
            for name, field in BaseGraphNodeModel._meta.fields_map.items()
            if field.pk
        ] == ["id"]
