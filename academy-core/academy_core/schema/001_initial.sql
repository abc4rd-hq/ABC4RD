PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_migrations(version, name, applied_at)
VALUES (1, 'initial_academy_core', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

-- Stable Academy identifier linked to an opaque IAM subject reference.
-- Names, email addresses, passwords, and Keycloak role copies do not belong here.
CREATE TABLE IF NOT EXISTS abc4rd_identities (
    abc4rd_id TEXT PRIMARY KEY,
    external_identity_ref TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

-- Append-only observations of consent actions. The legal consent catalogue and
-- approved collection UX are explicit Pilot Charter placeholders.
CREATE TABLE IF NOT EXISTS consent_records (
    consent_record_id TEXT PRIMARY KEY,
    abc4rd_id TEXT NOT NULL REFERENCES abc4rd_identities(abc4rd_id),
    consent_type TEXT NOT NULL,
    policy_ref TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('GRANTED', 'WITHDRAWN')),
    evidence_ref TEXT,
    source TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);

-- Append-only decisions. Current access is derived from the latest decision by
-- resource; Open edX remains the owner of enrolment and learning progress.
CREATE TABLE IF NOT EXISTS entitlement_records (
    entitlement_record_id TEXT PRIMARY KEY,
    abc4rd_id TEXT NOT NULL REFERENCES abc4rd_identities(abc4rd_id),
    resource_type TEXT NOT NULL,
    resource_ref TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('GRANTED', 'REVOKED')),
    authority_ref TEXT NOT NULL,
    evidence_ref TEXT,
    occurred_at TEXT NOT NULL
);

-- Small integration facts and references, not copies of LMS/CRM/chat payloads.
CREATE TABLE IF NOT EXISTS domain_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_ref TEXT NOT NULL,
    source TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_ref TEXT NOT NULL,
    correlation_id TEXT,
    payload_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

-- Shadow ledger for synthetic/provider sandbox observations only. It does not
-- settle money and must not be treated as a payment provider source of truth.
CREATE TABLE IF NOT EXISTS payment_ledger_entries (
    ledger_entry_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL CHECK(mode = 'SANDBOX'),
    provider TEXT NOT NULL,
    provider_event_ref TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    amount_minor INTEGER NOT NULL CHECK(amount_minor >= 0),
    currency TEXT NOT NULL CHECK(length(currency) = 3),
    abc4rd_id TEXT REFERENCES abc4rd_identities(abc4rd_id),
    metadata_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    UNIQUE(provider, provider_event_ref)
);

-- A review request and its decisions are separate immutable facts. The exact
-- authority matrix and appeal workflow remain Pilot Charter placeholders.
CREATE TABLE IF NOT EXISTS review_cases (
    review_case_id TEXT PRIMARY KEY,
    subject_type TEXT NOT NULL,
    subject_ref TEXT NOT NULL,
    review_kind TEXT NOT NULL,
    requested_authority_role TEXT NOT NULL,
    opened_by_ref TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_decisions (
    review_decision_id TEXT PRIMARY KEY,
    review_case_id TEXT NOT NULL REFERENCES review_cases(review_case_id),
    outcome TEXT NOT NULL CHECK(outcome IN ('APPROVED', 'REJECTED', 'CHANGES_REQUESTED')),
    decided_by_ref TEXT NOT NULL,
    rationale TEXT,
    evidence_ref TEXT,
    occurred_at TEXT NOT NULL
);

-- This is a registry/proof reference, not a credential-signing implementation.
-- Actual payloads belong in storage and issuer/format policy is not yet approved.
CREATE TABLE IF NOT EXISTS credential_records (
    credential_id TEXT PRIMARY KEY,
    abc4rd_id TEXT NOT NULL REFERENCES abc4rd_identities(abc4rd_id),
    credential_type TEXT NOT NULL,
    achievement_ref TEXT NOT NULL,
    format TEXT NOT NULL,
    payload_ref TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
    approval_decision_id TEXT NOT NULL REFERENCES review_decisions(review_decision_id),
    issued_at TEXT NOT NULL
);

-- Global request idempotency protects retries across every write endpoint.
CREATE TABLE IF NOT EXISTS idempotency_keys (
    idempotency_key TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Application-level SHA-256 hash chain. This detects ordinary tampering but is
-- not a substitute for signed/WORM external evidence (future gate).
CREATE TABLE IF NOT EXISTS audit_entries (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id TEXT NOT NULL UNIQUE,
    operation TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_ref TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_ref TEXT NOT NULL,
    details_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_consents_identity_time
ON consent_records(abc4rd_id, consent_type, occurred_at);

CREATE INDEX IF NOT EXISTS idx_entitlements_identity_resource_time
ON entitlement_records(abc4rd_id, resource_type, resource_ref, occurred_at);

CREATE INDEX IF NOT EXISTS idx_events_aggregate_time
ON domain_events(aggregate_type, aggregate_ref, occurred_at);

CREATE INDEX IF NOT EXISTS idx_ledger_identity_time
ON payment_ledger_entries(abc4rd_id, occurred_at);

CREATE INDEX IF NOT EXISTS idx_review_decisions_case_time
ON review_decisions(review_case_id, occurred_at);

-- Core facts are immutable. Corrections are new facts, never UPDATE/DELETE.
CREATE TRIGGER IF NOT EXISTS immutable_abc4rd_identities_update
BEFORE UPDATE ON abc4rd_identities BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER IF NOT EXISTS immutable_abc4rd_identities_delete
BEFORE DELETE ON abc4rd_identities BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER IF NOT EXISTS immutable_consent_records_update
BEFORE UPDATE ON consent_records BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER IF NOT EXISTS immutable_consent_records_delete
BEFORE DELETE ON consent_records BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER IF NOT EXISTS immutable_entitlement_records_update
BEFORE UPDATE ON entitlement_records BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER IF NOT EXISTS immutable_entitlement_records_delete
BEFORE DELETE ON entitlement_records BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER IF NOT EXISTS immutable_domain_events_update
BEFORE UPDATE ON domain_events BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER IF NOT EXISTS immutable_domain_events_delete
BEFORE DELETE ON domain_events BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER IF NOT EXISTS immutable_payment_ledger_entries_update
BEFORE UPDATE ON payment_ledger_entries BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER IF NOT EXISTS immutable_payment_ledger_entries_delete
BEFORE DELETE ON payment_ledger_entries BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER IF NOT EXISTS immutable_review_cases_update
BEFORE UPDATE ON review_cases BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER IF NOT EXISTS immutable_review_cases_delete
BEFORE DELETE ON review_cases BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER IF NOT EXISTS immutable_review_decisions_update
BEFORE UPDATE ON review_decisions BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER IF NOT EXISTS immutable_review_decisions_delete
BEFORE DELETE ON review_decisions BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER IF NOT EXISTS immutable_credential_records_update
BEFORE UPDATE ON credential_records BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER IF NOT EXISTS immutable_credential_records_delete
BEFORE DELETE ON credential_records BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER IF NOT EXISTS immutable_idempotency_keys_update
BEFORE UPDATE ON idempotency_keys BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER IF NOT EXISTS immutable_idempotency_keys_delete
BEFORE DELETE ON idempotency_keys BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER IF NOT EXISTS immutable_audit_entries_update
BEFORE UPDATE ON audit_entries BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER IF NOT EXISTS immutable_audit_entries_delete
BEFORE DELETE ON audit_entries BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
