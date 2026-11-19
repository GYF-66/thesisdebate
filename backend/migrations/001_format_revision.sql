-- 研辩台格式修订 schema
-- Idempotent migration: safe to run more than once.
-- The current prototype keeps runtime state in memory; this file is the persistence contract.

BEGIN;

CREATE TABLE IF NOT EXISTS format_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    institution TEXT NOT NULL,
    version TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    rules_count INTEGER NOT NULL DEFAULT 0 CHECK (rules_count >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT format_profiles_version_unique UNIQUE (id, version)
);

CREATE TABLE IF NOT EXISTS format_profile_rules (
    id BIGSERIAL PRIMARY KEY,
    profile_id TEXT NOT NULL,
    profile_version TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('format', 'structure', 'reference', 'content')),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    severity TEXT NOT NULL DEFAULT 'medium' CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    config JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT format_profile_rules_profile_fk
        FOREIGN KEY (profile_id, profile_version)
        REFERENCES format_profiles (id, version)
        ON UPDATE CASCADE
        ON DELETE CASCADE,
    CONSTRAINT format_profile_rules_unique UNIQUE (profile_id, profile_version, rule_id)
);

CREATE TABLE IF NOT EXISTS format_runs (
    id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    profile_version TEXT NOT NULL,
    document_version_id TEXT NOT NULL,
    document_name TEXT NOT NULL,
    document_type TEXT NOT NULL CHECK (document_type IN ('docx', 'pdf', 'markdown')),
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    changes_count INTEGER NOT NULL DEFAULT 0 CHECK (changes_count >= 0),
    accepted_count INTEGER NOT NULL DEFAULT 0 CHECK (accepted_count >= 0),
    rejected_count INTEGER NOT NULL DEFAULT 0 CHECK (rejected_count >= 0),
    requested_rules JSONB NOT NULL DEFAULT '[]'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT format_runs_profile_fk
        FOREIGN KEY (profile_id, profile_version)
        REFERENCES format_profiles (id, version)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,
    CONSTRAINT format_runs_counts_consistent
        CHECK (accepted_count + rejected_count <= changes_count),
    CONSTRAINT format_runs_completed_time_check
        CHECK (completed_at IS NULL OR completed_at >= created_at)
);

CREATE TABLE IF NOT EXISTS format_changes (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    document_version_id TEXT NOT NULL,
    anchor_node_id TEXT NOT NULL,
    anchor_start INTEGER NOT NULL CHECK (anchor_start >= 0),
    anchor_end INTEGER NOT NULL CHECK (anchor_end >= anchor_start),
    anchor_quote TEXT NOT NULL DEFAULT '',
    anchor_context_hash TEXT NOT NULL,
    property TEXT NOT NULL,
    before_value TEXT NOT NULL,
    after_value TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    explanation TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL CHECK (kind IN ('format', 'structure', 'reference')),
    replacement TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'rejected')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at TIMESTAMPTZ,
    CONSTRAINT format_changes_run_fk
        FOREIGN KEY (run_id)
        REFERENCES format_runs (id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,
    CONSTRAINT format_changes_document_version_match
        CHECK (BTRIM(document_version_id) <> ''),
    CONSTRAINT format_changes_decision_time_check
        CHECK (
            (status = 'pending' AND decided_at IS NULL)
            OR (status IN ('accepted', 'rejected') AND decided_at IS NOT NULL)
        )
);

CREATE INDEX IF NOT EXISTS idx_format_profiles_institution_version
    ON format_profiles (institution, version);

CREATE INDEX IF NOT EXISTS idx_format_profile_rules_enabled
    ON format_profile_rules (profile_id, profile_version, enabled);

CREATE INDEX IF NOT EXISTS idx_format_runs_profile_status
    ON format_runs (profile_id, profile_version, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_format_runs_document_version
    ON format_runs (document_version_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_format_changes_run_status
    ON format_changes (run_id, status, created_at);

CREATE INDEX IF NOT EXISTS idx_format_changes_rule
    ON format_changes (rule_id, status);

CREATE INDEX IF NOT EXISTS idx_format_changes_anchor
    ON format_changes (document_version_id, anchor_node_id, anchor_start, anchor_end);

COMMIT;
