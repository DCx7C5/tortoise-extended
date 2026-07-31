-- =============================================================================
-- 03-roles.sql — Create roles matching css_mcp config.py defaults
-- =============================================================================
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'coreuser') THEN
        CREATE ROLE coreuser WITH LOGIN PASSWORD 'corepass';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'admuser') THEN
        CREATE ROLE admuser WITH LOGIN PASSWORD 'admpass';
    END IF;
END
$$;
