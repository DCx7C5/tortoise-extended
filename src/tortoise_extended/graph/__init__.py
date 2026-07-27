"""Graph module for hierarchical data with adjacency list pattern.

Provides:
- GraphNode: Abstract base for graph nodes with adjacency list
- GraphEdge: Abstract base for typed relationships between nodes
- HierarchyModel: Abstract base for ltree-path hierarchy models

Usage::

    from tortoise import fields
    from tortoise_extended.graph import GraphNode, GraphEdge, HierarchyModel

    class Category(GraphNode):
        name = fields.CharField(max_length=100)

        class Meta:
            table = "categories"

    class Relationship(GraphEdge):
        properties = fields.JSONField(default=dict)

        class Meta:
            table = "relationships"
"""

from tortoise_extended.graph.edge import GraphEdge
from tortoise_extended.graph.hierarchy_model import HierarchyModel
from tortoise_extended.graph.node import GraphNode

__all__ = ["GraphEdge", "GraphNode", "HierarchyModel"]
