"""Unit tests for the graph node primitives in ``tortoise_extended.models.node``.

Covers the split between the pk-free :class:`NodeMixin` and the UUID7
identity base :class:`BaseNode`, the ``__init_subclass__`` guards, the
UUID4 ``BaseGraphNodeModel`` compatibility alias, and the
:class:`NodeTree` forest builder.  No database is used.
"""

from typing import get_args
from uuid import UUID, uuid4

import msgspec
import pytest
from tortoise import fields

from tortoise_extended.exceptions import GraphError
from tortoise_extended.fields.uuid import UUID4Field, UUID7Field
from tortoise_extended.models import (
    BaseGraphNodeModel,
    BaseModel,
    BaseNode,
    NodeMixin,
    NodeTree,
)
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


class TestNodeTree:
    """NodeTree.to_tree: O(n) forest build with promotion and cycle detection."""

    def test_is_a_msgspec_struct_with_an_empty_children_default(self) -> None:
        assert issubclass(NodeTree, msgspec.Struct)
        assert NodeTree.__struct_fields__ == ("node", "children")
        node = UnitHybridNode(id=1, parent_id=None, depth=0)
        tree = NodeTree(node=node)
        assert tree.node is node
        assert tree.children == []

    def test_nests_children_by_parent_id(self) -> None:
        root = UnitHybridNode(id=1, parent_id=None, depth=0)
        kid = UnitHybridNode(id=2, parent_id=1, depth=1)
        grandchild = UnitHybridNode(id=3, parent_id=2, depth=2)
        forest = UnitHybridNode.to_tree([root, kid, grandchild])
        assert [tree.node.pk for tree in forest] == [1]
        assert [tree.node.pk for tree in forest[0].children] == [2]
        assert [tree.node.pk for tree in forest[0].children[0].children] == [3]

    def test_promotes_a_slice_whose_parent_is_absent(self) -> None:
        # A descendants() slice starts below its root: the parent is not in it.
        kid = UnitHybridNode(id=2, parent_id=1, depth=1)
        grandchild = UnitHybridNode(id=3, parent_id=2, depth=2)
        forest = UnitHybridNode.to_tree([kid, grandchild])
        assert [tree.node.pk for tree in forest] == [2]
        assert [tree.node.pk for tree in forest[0].children] == [3]

    def test_promotes_every_disjoint_root_in_input_order(self) -> None:
        later_root = UnitHybridNode(id=10, parent_id=None, depth=0)
        first_root = UnitHybridNode(id=1, parent_id=None, depth=0)
        child = UnitHybridNode(id=2, parent_id=1, depth=1)
        forest = UnitHybridNode.to_tree([later_root, first_root, child])
        assert [tree.node.pk for tree in forest] == [10, 1]
        assert [tree.node.pk for tree in forest[1].children] == [2]

    def test_preserves_child_input_order(self) -> None:
        parent = UnitHybridNode(id=1, parent_id=None, depth=0)
        zeta = UnitHybridNode(id=3, parent_id=1, depth=1)
        alpha = UnitHybridNode(id=2, parent_id=1, depth=1)
        forest = UnitHybridNode.to_tree([parent, zeta, alpha])
        assert [tree.node.pk for tree in forest[0].children] == [3, 2]

    def test_promotes_unpersisted_nodes(self) -> None:
        unsaved = UnitHybridNode(parent_id=1, depth=1)
        assert unsaved.pk is None
        forest = UnitHybridNode.to_tree([unsaved])
        assert [tree.node for tree in forest] == [unsaved]

    def test_empty_input_yields_an_empty_forest(self) -> None:
        assert UnitHybridNode.to_tree([]) == []

    def test_raises_on_parent_id_cycle(self) -> None:
        first = UnitHybridNode(id=1, parent_id=2, depth=0)
        second = UnitHybridNode(id=2, parent_id=1, depth=1)
        with pytest.raises(GraphError, match="cycle"):
            UnitHybridNode.to_tree([first, second])

    def test_raises_on_self_parent_cycle(self) -> None:
        node = UnitHybridNode(id=1, parent_id=1, depth=0)
        with pytest.raises(GraphError, match="cycle"):
            UnitHybridNode.to_tree([node])

    def test_handles_a_deep_chain_without_recursion(self) -> None:
        length = 5000
        nodes = [
            UnitHybridNode(
                id=index + 1,
                parent_id=index if index else None,
                depth=index,
            )
            for index in range(length)
        ]
        forest = UnitHybridNode.to_tree(nodes)
        assert [tree.node.pk for tree in forest] == [1]
        current = forest[0]
        walked = 0
        while current.children:
            current = current.children[0]
            walked += 1
        assert walked == length - 1
