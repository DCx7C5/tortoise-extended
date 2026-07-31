-- =============================================================================
-- 00-extensions.sql — Create PostgreSQL extensions
-- Run first (alphabetical) on container first start.
-- =============================================================================
CREATE EXTENSION IF NOT EXISTS vector;        -- pgvector 0.8.5
CREATE EXTENSION IF NOT EXISTS ltree;          -- hierarchical paths
CREATE EXTENSION IF NOT EXISTS timescaledb;    -- time-series optimization
CREATE EXTENSION IF NOT EXISTS pg_trgm;        -- trigram similarity (fuzzy search)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";    -- UUID generation
