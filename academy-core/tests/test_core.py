import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from academy_core.app import create_app
from academy_core.db import connect
from academy_core.errors import ConflictError, ValidationError
from academy_core.service import AcademyCore


ACTOR = {"actor_type": "SYSTEM", "actor_ref": "test-suite"}


class AcademyCoreTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temporary.name) / "core.db")
        self.core = AcademyCore(self.database)

    def tearDown(self):
        self.temporary.cleanup()

    def identity(self, suffix="1"):
        return self.core.create_identity(
            {"external_identity_ref": "keycloak:subject-%s" % suffix, **ACTOR},
            "identity-%s" % suffix,
        )

    def test_identity_is_opaque_and_idempotent(self):
        first = self.identity()
        replay = self.core.create_identity(
            {"external_identity_ref": "keycloak:subject-1", **ACTOR}, "identity-1"
        )
        self.assertEqual(first, replay)
        self.assertNotIn("name", first)
        self.assertNotIn("email", first)

        with self.assertRaises(ConflictError):
            self.core.create_identity(
                {"external_identity_ref": "keycloak:different", **ACTOR}, "identity-1"
            )

    def test_consent_and_entitlement_are_append_only_facts(self):
        identity = self.identity()
        consent = self.core.record_consent(
            {
                "abc4rd_id": identity["abc4rd_id"],
                "consent_type": "PLACEHOLDER-PILOT-CONSENT",
                "policy_ref": "policy:placeholder-v0",
                "action": "GRANTED",
                "source": "test",
                **ACTOR,
            },
            "consent-1",
        )
        entitlement = self.core.record_entitlement(
            {
                "abc4rd_id": identity["abc4rd_id"],
                "resource_type": "course",
                "resource_ref": "openedx:pilot-placeholder",
                "action": "GRANTED",
                "authority_ref": "decision:placeholder",
                **ACTOR,
            },
            "entitlement-1",
        )
        self.assertEqual(consent["action"], "GRANTED")
        self.assertEqual(entitlement["action"], "GRANTED")

        connection = connect(self.database)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute(
                    "UPDATE consent_records SET action = 'WITHDRAWN' "
                    "WHERE consent_record_id = ?",
                    (consent["consent_record_id"],),
                )
        finally:
            connection.close()

    def test_payment_attempt_is_not_a_charge_and_live_is_gated(self):
        entry = {
            "mode": "SANDBOX",
            "provider": "synthetic-provider",
            "provider_event_ref": "evt-1",
            "fact_type": "ATTEMPT",
            "entry_type": "PLACEHOLDER_EVENT",
            "amount_minor": 100,
            "currency": "USD",
            "metadata": {},
            **ACTOR,
        }
        saved = self.core.record_payment(entry, "payment-1")
        self.assertEqual(saved["mode"], "SANDBOX")
        self.assertEqual(saved["fact_type"], "ATTEMPT")
        self.assertFalse(saved["recognized_charge"])

        live_entry = dict(entry, mode="LIVE", provider_event_ref="evt-live")
        with self.assertRaises(ValidationError):
            self.core.record_payment(live_entry, "payment-live")

        wrong_price = dict(entry, amount_minor=101, provider_event_ref="evt-price")
        with self.assertRaises(ValidationError):
            self.core.record_payment(wrong_price, "payment-price")

        unevidenced_charge = dict(
            entry,
            fact_type="PROVIDER_CONFIRMED_CHARGE",
            provider_event_ref="evt-charge",
        )
        with self.assertRaises(ValidationError):
            self.core.record_payment(unevidenced_charge, "payment-charge")

        duplicate = dict(entry, metadata={"duplicate": True})
        with self.assertRaises(ConflictError):
            self.core.record_payment(duplicate, "payment-2")

    def test_future_live_mode_models_facts_but_has_no_settlement_capability(self):
        configured = AcademyCore(
            self.database,
            live_payment_provider="future-provider",
            live_payment_gate_ref="gate:explicit-placeholder",
        )
        attempt = configured.record_payment(
            {
                "mode": "LIVE",
                "provider": "future-provider",
                "provider_event_ref": "live-attempt-1",
                "fact_type": "ATTEMPT",
                "entry_type": "CHECKOUT_ATTEMPT_PLACEHOLDER",
                "amount_minor": 100,
                "currency": "USD",
                "metadata": {},
                **ACTOR,
            },
            "live-attempt-1",
        )
        self.assertFalse(attempt["recognized_charge"])

        confirmed = configured.record_payment(
            {
                "mode": "LIVE",
                "provider": "future-provider",
                "provider_event_ref": "live-provider-fact-1",
                "fact_type": "PROVIDER_CONFIRMED_CHARGE",
                "entry_type": "PROVIDER_WEBHOOK_PLACEHOLDER",
                "amount_minor": 100,
                "currency": "USD",
                "provider_evidence_ref": "evidence:provider-signature-placeholder",
                "metadata": {},
                **ACTOR,
            },
            "live-provider-fact-1",
        )
        self.assertTrue(confirmed["recognized_charge"])
        self.assertFalse(configured.payment_policy()["settlement_capability"])

    def test_credential_accepts_approved_ai_decision(self):
        identity = self.identity()
        review = self.core.open_review(
            {
                "subject_type": "credential",
                "subject_ref": "achievement:placeholder",
                "review_kind": "FINAL_CREDENTIAL_PLACEHOLDER",
                "review_stage": "PRIMARY",
                "risk_level": "NORMAL",
                "requested_authority_role": "AI_REVIEWER_PLACEHOLDER",
                "opened_by_ref": "workflow:test",
                **ACTOR,
            },
            "review-1",
        )
        with self.assertRaises(ValidationError):
            self.core.decide_review(
                {
                    "review_case_id": review["review_case_id"],
                    "outcome": "APPROVED",
                    "reviewer_agent_id": "agent:primary",
                    "reviewer_model": "model-placeholder",
                    "reviewer_version": "v-placeholder",
                    **ACTOR,
                },
                "decision-system",
            )

        decision = self.core.decide_review(
            {
                "review_case_id": review["review_case_id"],
                "outcome": "APPROVED",
                "reviewer_agent_id": "agent:primary",
                "reviewer_model": "model-placeholder",
                "reviewer_version": "v-placeholder",
                "actor_type": "AI_AGENT",
                "actor_ref": "agent:primary",
            },
            "decision-1",
        )
        credential = self.core.register_credential(
            {
                "abc4rd_id": identity["abc4rd_id"],
                "credential_type": "CERTIFICATE_PLACEHOLDER",
                "achievement_ref": "achievement:placeholder",
                "format": "FORMAT_PLACEHOLDER",
                "payload_ref": "s3:placeholder/credential",
                "payload_sha256": "a" * 64,
                "approval_decision_id": decision["review_decision_id"],
                **ACTOR,
            },
            "credential-1",
        )
        self.assertEqual(credential["approval_decision_id"], decision["review_decision_id"])

    def test_appeal_requires_a_different_agent_and_creates_oversight_outbox(self):
        primary_case = self.core.open_review(
            {
                "subject_type": "assignment",
                "subject_ref": "assignment:placeholder",
                "review_kind": "FINAL_ASSESSMENT_PLACEHOLDER",
                "review_stage": "PRIMARY",
                "risk_level": "HIGH",
                "requested_authority_role": "AI_REVIEWER_PLACEHOLDER",
                "opened_by_ref": "workflow:test",
                **ACTOR,
            },
            "primary-review",
        )
        primary_decision = self.core.decide_review(
            {
                "review_case_id": primary_case["review_case_id"],
                "outcome": "REJECTED",
                "reviewer_agent_id": "agent:primary",
                "reviewer_model": "model-a",
                "reviewer_version": "v1",
                "actor_type": "AI_AGENT",
                "actor_ref": "agent:primary",
            },
            "primary-decision",
        )
        appeal_case = self.core.open_review(
            {
                "subject_type": "assignment",
                "subject_ref": "assignment:placeholder",
                "review_kind": "APPEAL_PLACEHOLDER",
                "review_stage": "APPEAL",
                "prior_review_decision_id": primary_decision["review_decision_id"],
                "risk_level": "NORMAL",
                "requested_authority_role": "INDEPENDENT_AI_REVIEWER_PLACEHOLDER",
                "opened_by_ref": "workflow:test",
                **ACTOR,
            },
            "appeal-review",
        )
        with self.assertRaises(ValidationError):
            self.core.decide_review(
                {
                    "review_case_id": appeal_case["review_case_id"],
                    "outcome": "APPROVED",
                    "reviewer_agent_id": "agent:primary",
                    "reviewer_model": "model-a",
                    "reviewer_version": "v1",
                    "actor_type": "AI_AGENT",
                    "actor_ref": "agent:primary",
                },
                "appeal-same-agent",
            )
        independent = self.core.decide_review(
            {
                "review_case_id": appeal_case["review_case_id"],
                "outcome": "APPROVED",
                "reviewer_agent_id": "agent:independent",
                "reviewer_model": "model-b",
                "reviewer_version": "v2",
                "actor_type": "AI_AGENT",
                "actor_ref": "agent:independent",
            },
            "appeal-independent-agent",
        )
        self.assertEqual(independent["reviewer_agent_id"], "agent:independent")

        outbox = self.core.list_oversight_outbox()
        self.assertEqual(outbox["count"], 3)
        self.assertFalse(outbox["sending_implemented"])
        self.assertTrue(
            all(event["destination_ref"] == "TBD_OVERSIGHT_MAILBOX" for event in outbox["events"])
        )
        self.assertTrue(
            all(event["delivery_status"] == "PENDING_CONFIGURATION" for event in outbox["events"])
        )
        high_risk_event = next(
            event for event in outbox["events"] if "HIGH_RISK" in event["reasons"]
        )
        self.assertIn("UNRESOLVED", high_risk_event["reasons"])

        connection = connect(self.database)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute(
                    "UPDATE oversight_outbox_events SET delivery_status = 'PENDING_CONFIGURATION' "
                    "WHERE outbox_event_id = ?",
                    (outbox["events"][0]["outbox_event_id"],),
                )
        finally:
            connection.close()

    def test_events_and_audit_chain_are_verifiable(self):
        identity = self.identity()
        self.core.record_event(
            {
                "event_type": "openedx.reference.observed",
                "aggregate_type": "abc4rd_identity",
                "aggregate_ref": identity["abc4rd_id"],
                "source": "openedx-adapter-placeholder",
                "payload": {"external_event_ref": "evt:test"},
                **ACTOR,
            },
            "event-1",
        )
        verification = self.core.verify_audit_chain()
        self.assertTrue(verification["valid"])
        self.assertEqual(verification["entries"], 2)

    def test_api_health_and_strict_identity_payload(self):
        app = create_app(self.database)
        status, body = call_wsgi(app, "GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["payment"]["target_amount_minor"], 100)
        self.assertEqual(body["payment"]["target_currency"], "USD")
        self.assertFalse(body["payment"]["live_model_configured"])
        self.assertFalse(body["payment"]["settlement_capability"])

        status, body = call_wsgi(app, "GET", "/v1/oversight-outbox")
        self.assertEqual(status, 200)
        self.assertEqual(body["count"], 0)
        self.assertFalse(body["sending_implemented"])

        status, body = call_wsgi(
            app,
            "POST",
            "/v1/identities",
            {"external_identity_ref": "keycloak:api", "email": "must-not-copy@example.test", **ACTOR},
            idempotency_key="api-identity-1",
        )
        self.assertEqual(status, 422)
        self.assertEqual(body["error"], "validation_error")


def call_wsgi(app, method, path, body=None, idempotency_key=None):
    raw = b"" if body is None else json.dumps(body).encode("utf-8")
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "CONTENT_TYPE": "application/json" if body is not None else "",
        "CONTENT_LENGTH": str(len(raw)),
        "wsgi.input": io.BytesIO(raw),
    }
    if idempotency_key is not None:
        environ["HTTP_IDEMPOTENCY_KEY"] = idempotency_key
    captured = {}

    def start_response(status, headers):
        captured["status"] = int(status.split(" ", 1)[0])
        captured["headers"] = headers

    response = b"".join(app(environ, start_response))
    return captured["status"], json.loads(response.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
