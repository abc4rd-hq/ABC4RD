PRAGMA foreign_keys = ON;
BEGIN;

-- Owner-approved pilot target. Future changes are new versioned facts.
CREATE TABLE pilot_price_targets (
    price_target_id TEXT PRIMARY KEY,
    amount_minor INTEGER NOT NULL CHECK(amount_minor = 100),
    currency TEXT NOT NULL CHECK(currency = 'USD'),
    created_at TEXT NOT NULL
);

INSERT INTO pilot_price_targets(price_target_id, amount_minor, currency, created_at)
VALUES ('pilot-price-usd-1', 100, 'USD', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

CREATE TRIGGER immutable_pilot_price_targets_update
BEFORE UPDATE ON pilot_price_targets BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER immutable_pilot_price_targets_delete
BEFORE DELETE ON pilot_price_targets BEGIN SELECT RAISE(ABORT, 'append-only table'); END;

-- Rebuild the ledger so it can model future LIVE observations. Academy Core still
-- has no charge operation: it only records an attempt or an externally evidenced
-- provider fact. Runtime LIVE recording additionally requires an explicit gate.
DROP TRIGGER IF EXISTS immutable_payment_ledger_entries_update;
DROP TRIGGER IF EXISTS immutable_payment_ledger_entries_delete;
DROP INDEX IF EXISTS idx_ledger_identity_time;

ALTER TABLE payment_ledger_entries RENAME TO payment_ledger_entries_v1;

CREATE TABLE payment_ledger_entries (
    ledger_entry_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL CHECK(mode IN ('SANDBOX', 'LIVE')),
    provider TEXT NOT NULL,
    provider_event_ref TEXT NOT NULL,
    fact_type TEXT NOT NULL CHECK(fact_type IN (
        'ATTEMPT',
        'PROVIDER_CONFIRMED_CHARGE',
        'PROVIDER_CONFIRMED_REFUND'
    )),
    entry_type TEXT NOT NULL,
    amount_minor INTEGER NOT NULL CHECK(amount_minor >= 0),
    currency TEXT NOT NULL CHECK(length(currency) = 3),
    abc4rd_id TEXT REFERENCES abc4rd_identities(abc4rd_id),
    provider_evidence_ref TEXT,
    metadata_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    UNIQUE(provider, provider_event_ref)
);

INSERT INTO payment_ledger_entries (
    ledger_entry_id, mode, provider, provider_event_ref, fact_type, entry_type,
    amount_minor, currency, abc4rd_id, provider_evidence_ref, metadata_json, occurred_at
)
SELECT
    ledger_entry_id, mode, provider, provider_event_ref, 'ATTEMPT', entry_type,
    amount_minor, currency, abc4rd_id, NULL, metadata_json, occurred_at
FROM payment_ledger_entries_v1;

DROP TABLE payment_ledger_entries_v1;

CREATE INDEX idx_ledger_identity_time
ON payment_ledger_entries(abc4rd_id, occurred_at);

CREATE TRIGGER immutable_payment_ledger_entries_update
BEFORE UPDATE ON payment_ledger_entries BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER immutable_payment_ledger_entries_delete
BEFORE DELETE ON payment_ledger_entries BEGIN SELECT RAISE(ABORT, 'append-only table'); END;

-- AI-first review metadata. Nullable columns preserve any V1 prototype records;
-- V2 application writes require all three agent identity fields.
ALTER TABLE review_cases ADD COLUMN review_stage TEXT
CHECK(review_stage IN ('PRIMARY', 'INDEPENDENT_REVIEW', 'APPEAL'));
ALTER TABLE review_cases ADD COLUMN prior_review_decision_id TEXT;
ALTER TABLE review_cases ADD COLUMN risk_level TEXT
CHECK(risk_level IN ('NORMAL', 'HIGH'));

ALTER TABLE review_decisions ADD COLUMN reviewer_agent_id TEXT;
ALTER TABLE review_decisions ADD COLUMN reviewer_model TEXT;
ALTER TABLE review_decisions ADD COLUMN reviewer_version TEXT;

-- Notification intent only. No sender, credentials, mailbox address, retry worker,
-- or delivery claim exists in this stage.
CREATE TABLE oversight_outbox_events (
    outbox_event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL CHECK(event_type IN (
        'REVIEW_UNRESOLVED',
        'REVIEW_ADVERSE_DECISION'
    )),
    review_case_id TEXT NOT NULL REFERENCES review_cases(review_case_id),
    review_decision_id TEXT REFERENCES review_decisions(review_decision_id),
    destination_ref TEXT NOT NULL CHECK(destination_ref = 'TBD_OVERSIGHT_MAILBOX'),
    delivery_status TEXT NOT NULL CHECK(delivery_status = 'PENDING_CONFIGURATION'),
    reasons_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_oversight_outbox_created
ON oversight_outbox_events(created_at);

CREATE TRIGGER immutable_oversight_outbox_events_update
BEFORE UPDATE ON oversight_outbox_events BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER immutable_oversight_outbox_events_delete
BEFORE DELETE ON oversight_outbox_events BEGIN SELECT RAISE(ABORT, 'append-only table'); END;

INSERT INTO schema_migrations(version, name, applied_at)
VALUES (2, 'owner_override_ai_review', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

COMMIT;
