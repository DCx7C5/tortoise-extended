# Database Schema

## Overview

The GraphRAG schema consists of 12 tables organized into four domains:

1. **Document Processing** — `documents`, `text_units`
2. **Knowledge Graph** — `entities`, `relationships`, `facts`, `entity_merges`
3. **Community Detection** — `communities`, `community_memberships`, `community_reports`
4. **RAPTOR Tree** — `raptor_nodes`, `raptor_tree_edges`
5. **Query Cache** — `query_cache`

## Entity-Relationship Diagram

```
                    ┌─────────────┐
                    │  documents  │
                    └──────┬──────┘
                           │ 1:N
                    ┌──────┴──────┐
                    │ text_units  │
                    └──────┬──────┘
                           │ 1:N
                    ┌──────┴──────┐
                    │    facts    │
                    └──────┬──────┘
                           │ N:1
┌─────────────┐      ┌────┴─────┐      ┌─────────────┐
│   entities  │──────│  (facts) │──────│   entities  │
└──────┬──────┘      └──────────┘      └──────┬──────┘
       │ 1:N                                  │ 1:N
┌──────┴──────┐                        ┌──────┴──────┐
│relationships│                        │relationships│
└──────┬──────┘                        └──────┬──────┘
       │ N:1                                  │ N:1
       └──────────────┬───────────────────────┘
                      │
               ┌──────┴──────┐
               │   entities  │
               └──────┬──────┘
                      │ M:N
               ┌──────┴──────┐
               │  community  │
               │  memberships│
               └──────┬──────┘
                      │ N:1
               ┌──────┴──────┐
               │  communities│
               └──────┬──────┘
                      │ 1:N
               ┌──────┴──────┐
               │  community  │
               │   reports   │
               └─────────────┘
```

## Table Definitions

### documents

Source documents before chunking.

| Column | Type | Constraints |
|--------|------|------------|
| id | UUID | PRIMARY KEY |
| title | TEXT | NOT NULL |
| source | TEXT | NULL |
| metadata | JSONB | NULL |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() |
| updated_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() |

### text_units

Atomic chunks (300-500 tokens) with embeddings.

| Column | Type | Constraints |
|--------|------|------------|
| id | UUID | PRIMARY KEY |
| content | TEXT | NOT NULL |
| document_id | UUID | FK → documents.id |
| token_count | INTEGER | NOT NULL |
| embedding | VECTOR(1536) | NULL |
| metadata | JSONB | NULL |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() |
| updated_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() |

**Indexes:**
- `hnsw_text_units_embedding` (HNSW, cosine)
- `ix_text_units_document_id` (B-tree)
- `ix_text_units_token_count` (B-tree)

### entities

Graph nodes with optional embeddings.

| Column | Type | Constraints |
|--------|------|------------|
| id | UUID | PRIMARY KEY |
| title | TEXT | NOT NULL |
| type | TEXT | NOT NULL |
| description | TEXT | NULL |
| embedding | VECTOR(1536) | NULL |
| metadata | JSONB | NULL |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() |
| updated_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() |

**Indexes:**
- `hnsw_entities_embedding` (HNSW, cosine)
- `ix_entities_type` (B-tree)
- `ix_entities_title` (B-tree, unique)

**Tortoise Relations:**
- `outgoing: ReverseRelation[Relationship]`
- `incoming: ReverseRelation[Relationship]`
- `communities: ManyToManyField[Community]`

### relationships

Directed, typed, weighted graph edges.

| Column | Type | Constraints |
|--------|------|------------|
| id | UUID | PRIMARY KEY |
| source_entity_id | UUID | FK → entities.id NOT NULL |
| target_entity_id | UUID | FK → entities.id NOT NULL |
| type | TEXT | NOT NULL |
| weight | FLOAT | NOT NULL DEFAULT 1.0 |
| description | TEXT | NULL |
| metadata | JSONB | NULL |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() |
| updated_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() |

**Indexes:**
- `ix_relationships_source` (B-tree)
- `ix_relationships_target` (B-tree)
- `ix_relationships_type` (B-tree)
- `ix_relationships_source_type` (B-tree, composite)

**Tortoise Relations:**
- `source: ForeignKey[Entity]`
- `target: ForeignKey[Entity]`

### communities

Hierarchical community structure (Leiden algorithm).

| Column | Type | Constraints |
|--------|------|------------|
| id | UUID | PRIMARY KEY |
| level | INTEGER | NOT NULL |
| parent_community_id | UUID | FK → communities.id NULL |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() |
| updated_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() |

**Tortoise Relations:**
- `parent: ForeignKey[Community]`
- `children: ReverseRelation[Community]`
- `entities: ManyToManyField[Entity]`
- `reports: ReverseRelation[CommunityReport]`

### community_memberships

Entity ↔ Community mapping.

| Column | Type | Constraints |
|--------|------|------------|
| id | UUID | PRIMARY KEY |
| entity_id | UUID | FK → entities.id NOT NULL |
| community_id | UUID | FK → communities.id NOT NULL |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() |

**Indexes:**
- `ix_community_memberships_entity` (B-tree)
- `ix_community_memberships_community` (B-tree)

**Tortoise Relations:**
- `entity: ForeignKey[Entity]`
- `community: ForeignKey[Community]`

### community_reports

LLM-generated summaries with embeddings.

| Column | Type | Constraints |
|--------|------|------------|
| id | UUID | PRIMARY KEY |
| community_id | UUID | FK → communities.id NOT NULL |
| summary | TEXT | NOT NULL |
| embedding | VECTOR(1536) | NULL |
| rating | FLOAT | NULL |
| metadata | JSONB | NULL |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() |
| updated_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() |

**Indexes:**
- `hnsw_community_reports_embedding` (HNSW, cosine)

**Tortoise Relations:**
- `community: ForeignKey[Community]`

### raptor_nodes

RAPTOR tree nodes at abstraction levels.

| Column | Type | Constraints |
|--------|------|------------|
| id | UUID | PRIMARY KEY |
| level | INTEGER | NOT NULL |
| summary | TEXT | NOT NULL |
| embedding | VECTOR(1536) | NULL |
| metadata | JSONB | NULL |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() |
| updated_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() |

**Indexes:**
- `hnsw_raptor_nodes_embedding` (HNSW, cosine)

**Tortoise Relations:**
- `children: ManyToManyField[RaptorNode]` (via `raptor_tree_edges`)
- `parents: ManyToManyField[RaptorNode]` (via `raptor_tree_edges`)

### raptor_tree_edges

Parent → child edges in RAPTOR tree.

| Column | Type | Constraints |
|--------|------|------------|
| id | UUID | PRIMARY KEY |
| source_node_id | UUID | FK → raptor_nodes.id NOT NULL |
| target_node_id | UUID | FK → raptor_nodes.id NOT NULL |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() |

**Tortoise Relations:**
- `source: ForeignKey[RaptorNode]`
- `target: ForeignKey[RaptorNode]`

### query_cache

Semantic response cache.

| Column | Type | Constraints |
|--------|------|------------|
| id | UUID | PRIMARY KEY |
| query_hash | TEXT | NOT NULL UNIQUE |
| query_text | TEXT | NOT NULL |
| response | JSONB | NOT NULL |
| embedding | VECTOR(1536) | NULL |
| hit_count | INTEGER | NOT NULL DEFAULT 0 |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() |
| updated_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() |

**Indexes:**
- `ix_query_cache_query_hash` (B-tree, unique)

### facts

Time-bounded entity assertions.

| Column | Type | Constraints |
|--------|------|------------|
| id | UUID | PRIMARY KEY |
| entity_id | UUID | FK → entities.id NOT NULL |
| text_unit_id | UUID | FK → text_units.id NOT NULL |
| subject | TEXT | NOT NULL |
| predicate | TEXT | NOT NULL |
| object | TEXT | NOT NULL |
| valid_from | TIMESTAMPTZ | NULL |
| valid_to | TIMESTAMPTZ | NULL |
| confidence | FLOAT | NOT NULL DEFAULT 1.0 |
| metadata | JSONB | NULL |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() |
| updated_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() |

**Indexes:**
- `ix_facts_entity_id` (B-tree)
- `ix_facts_text_unit_id` (B-tree)
- `ix_facts_subject_object` (B-tree, composite)

**Tortoise Relations:**
- `entity: ForeignKey[Entity]`
- `text_unit: ForeignKey[TextUnit]`

### entity_merges

Entity resolution audit trail.

| Column | Type | Constraints |
|--------|------|------------|
| id | UUID | PRIMARY KEY |
| source_entity_id | UUID | FK → entities.id NOT NULL |
| target_entity_id | UUID | FK → entities.id NOT NULL |
| reason | TEXT | NOT NULL |
| confidence | FLOAT | NOT NULL DEFAULT 1.0 |
| metadata | JSONB | NULL |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() |

**Tortoise Relations:**
- `source: ForeignKey[Entity]`
- `target: ForeignKey[Entity]`

## Index Strategy

### Vector Indexes

- **HNSW** — Default for most use cases. Better query performance, higher memory.
- **IVFFlat** — Better for large datasets with frequent inserts. Lower memory, slower queries.

### B-tree Indexes

- Used for exact match and range queries
- Composite indexes for common filter combinations
- Partial indexes for frequently filtered columns

### Full-Text Search

- `tsvector` columns for full-text search
- `ts_rank_cd` for relevance ranking
- `pg_trgm` for fuzzy matching

## Schema Evolution

The schema supports:
- Adding new tables without migration
- Adding new columns with defaults
- Adding new indexes without downtime
- Dropping deprecated columns in future versions
