import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

from .db import connect, initialize
from .errors import ConflictError, NotFoundError, ValidationError


ZERO_HASH = "0" * 64
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PILOT_PRICE_MINOR = 100
PILOT_PRICE_CURRENCY = "USD"
PAYMENT_FACT_TYPES = (
    "ATTEMPT",
    "PROVIDER_CONFIRMED_CHARGE",
    "PROVIDER_CONFIRMED_REFUND",
)
REVIEW_STAGES = ("PRIMARY", "INDEPENDENT_REVIEW", "APPEAL")
ADVERSE_OUTCOMES = ("REJECTED", "CHANGES_REQUESTED")
OVERSIGHT_DESTINATION = "TBD_OVERSIGHT_MAILBOX"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _timestamp(value: Optional[str]) -> str:
    if value is None:
        return _utc_now()
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("occurred_at must be a non-empty ISO-8601 timestamp")
    candidate = value.strip()
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValidationError("occurred_at must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValidationError("occurred_at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _new_id() -> str:
    return str(uuid.uuid4())


def _required_text(data: Dict[str, Any], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("%s must be a non-empty string" % name)
    return value.strip()


def _optional_text(data: Dict[str, Any], name: str) -> Optional[str]:
    value = data.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("%s must be null or a non-empty string" % name)
    return value.strip()


def _keys(
    data: Dict[str, Any], required: Iterable[str], optional: Iterable[str] = ()
) -> None:
    if not isinstance(data, dict):
        raise ValidationError("JSON body must be an object")
    required_set = set(required)
    allowed = required_set | set(optional)
    missing = sorted(name for name in required_set if name not in data)
    unknown = sorted(set(data) - allowed)
    if missing:
        raise ValidationError("missing fields: %s" % ", ".join(missing))
    if unknown:
        raise ValidationError("unknown fields: %s" % ", ".join(unknown))


class AcademyCore:
    """Transactional application service for the small Academy-owned boundary."""

    def __init__(
        self,
        database: str,
        live_payment_provider: Optional[str] = None,
        live_payment_gate_ref: Optional[str] = None,
    ):
        self.database = database
        self.live_payment_provider = live_payment_provider.strip() if live_payment_provider else None
        self.live_payment_gate_ref = live_payment_gate_ref.strip() if live_payment_gate_ref else None
        if bool(self.live_payment_provider) != bool(self.live_payment_gate_ref):
            raise ValidationError(
                "LIVE modelling requires both live_payment_provider and live_payment_gate_ref"
            )
        initialize(database)

    def _write(
        self,
        operation: str,
        idempotency_key: str,
        request_data: Dict[str, Any],
        callback: Callable[[sqlite3.Connection], Tuple[str, str, Dict[str, Any]]],
    ) -> Dict[str, Any]:
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValidationError("Idempotency-Key is required")
        key = idempotency_key.strip()
        request_hash = hashlib.sha256(_canonical(request_data).encode("utf-8")).hexdigest()
        connection = connect(self.database)
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT operation, request_hash, response_json FROM idempotency_keys "
                "WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            if existing is not None:
                if existing["operation"] != operation or existing["request_hash"] != request_hash:
                    raise ConflictError("Idempotency-Key was already used for a different request")
                response = json.loads(existing["response_json"])
                connection.commit()
                return response

            resource_type, resource_id, response = callback(connection)
            connection.execute(
                "INSERT INTO idempotency_keys "
                "(idempotency_key, operation, request_hash, resource_type, resource_id, "
                "response_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    key,
                    operation,
                    request_hash,
                    resource_type,
                    resource_id,
                    _canonical(response),
                    _utc_now(),
                ),
            )
            connection.commit()
            return response
        except sqlite3.IntegrityError as error:
            connection.rollback()
            raise ConflictError("record conflicts with an existing immutable fact") from error
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _actor(data: Dict[str, Any]) -> Tuple[str, str]:
        return _required_text(data, "actor_type"), _required_text(data, "actor_ref")

    @staticmethod
    def _identity_exists(connection: sqlite3.Connection, abc4rd_id: str) -> None:
        row = connection.execute(
            "SELECT 1 FROM abc4rd_identities WHERE abc4rd_id = ?", (abc4rd_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError("ABC4RD ID was not found")

    @staticmethod
    def _emit_event(
        connection: sqlite3.Connection,
        event_type: str,
        aggregate_type: str,
        aggregate_ref: str,
        actor_type: str,
        actor_ref: str,
        occurred_at: str,
        payload: Dict[str, Any],
        source: str = "academy-core",
        correlation_id: Optional[str] = None,
    ) -> str:
        event_id = _new_id()
        connection.execute(
            "INSERT INTO domain_events "
            "(event_id, event_type, aggregate_type, aggregate_ref, source, actor_type, "
            "actor_ref, correlation_id, payload_json, occurred_at, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                event_type,
                aggregate_type,
                aggregate_ref,
                source,
                actor_type,
                actor_ref,
                correlation_id,
                _canonical(payload),
                occurred_at,
                _utc_now(),
            ),
        )
        return event_id

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        operation: str,
        actor_type: str,
        actor_ref: str,
        object_type: str,
        object_ref: str,
        details: Dict[str, Any],
        occurred_at: str,
    ) -> str:
        previous = connection.execute(
            "SELECT entry_hash FROM audit_entries ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous_hash = previous["entry_hash"] if previous is not None else ZERO_HASH
        audit_id = _new_id()
        material = {
            "audit_id": audit_id,
            "operation": operation,
            "actor_type": actor_type,
            "actor_ref": actor_ref,
            "object_type": object_type,
            "object_ref": object_ref,
            "details": details,
            "occurred_at": occurred_at,
            "previous_hash": previous_hash,
        }
        entry_hash = hashlib.sha256(_canonical(material).encode("utf-8")).hexdigest()
        connection.execute(
            "INSERT INTO audit_entries "
            "(audit_id, operation, actor_type, actor_ref, object_type, object_ref, "
            "details_json, occurred_at, previous_hash, entry_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                audit_id,
                operation,
                actor_type,
                actor_ref,
                object_type,
                object_ref,
                _canonical(details),
                occurred_at,
                previous_hash,
                entry_hash,
            ),
        )
        return audit_id

    @staticmethod
    def _enqueue_oversight(
        connection: sqlite3.Connection,
        event_type: str,
        review_case_id: str,
        review_decision_id: Optional[str],
        reasons: Iterable[str],
        payload: Dict[str, Any],
        created_at: str,
    ) -> str:
        outbox_event_id = _new_id()
        connection.execute(
            "INSERT INTO oversight_outbox_events "
            "(outbox_event_id, event_type, review_case_id, review_decision_id, "
            "destination_ref, delivery_status, reasons_json, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                outbox_event_id,
                event_type,
                review_case_id,
                review_decision_id,
                OVERSIGHT_DESTINATION,
                "PENDING_CONFIGURATION",
                _canonical(list(reasons)),
                _canonical(payload),
                created_at,
            ),
        )
        return outbox_event_id

    def payment_policy(self) -> Dict[str, Any]:
        return {
            "target_amount_minor": PILOT_PRICE_MINOR,
            "target_currency": PILOT_PRICE_CURRENCY,
            "live_model_configured": bool(
                self.live_payment_provider and self.live_payment_gate_ref
            ),
            "live_payment_provider": self.live_payment_provider,
            "settlement_capability": False,
        }

    def create_identity(self, data: Dict[str, Any], idempotency_key: str) -> Dict[str, Any]:
        _keys(
            data,
            required=("external_identity_ref", "actor_type", "actor_ref"),
            optional=("occurred_at",),
        )
        external_ref = _required_text(data, "external_identity_ref")
        actor_type, actor_ref = self._actor(data)

        def create(connection: sqlite3.Connection):
            abc4rd_id = _new_id()
            occurred_at = _timestamp(data.get("occurred_at"))
            connection.execute(
                "INSERT INTO abc4rd_identities "
                "(abc4rd_id, external_identity_ref, created_at) VALUES (?, ?, ?)",
                (abc4rd_id, external_ref, occurred_at),
            )
            response = {
                "abc4rd_id": abc4rd_id,
                "external_identity_ref": external_ref,
                "created_at": occurred_at,
            }
            self._emit_event(
                connection,
                "core.identity.created",
                "abc4rd_identity",
                abc4rd_id,
                actor_type,
                actor_ref,
                occurred_at,
                {"external_identity_ref": external_ref},
            )
            self._audit(
                connection,
                "identity.create",
                actor_type,
                actor_ref,
                "abc4rd_identity",
                abc4rd_id,
                response,
                occurred_at,
            )
            return "abc4rd_identity", abc4rd_id, response

        return self._write("identity.create", idempotency_key, data, create)

    def record_consent(self, data: Dict[str, Any], idempotency_key: str) -> Dict[str, Any]:
        _keys(
            data,
            required=(
                "abc4rd_id",
                "consent_type",
                "policy_ref",
                "action",
                "source",
                "actor_type",
                "actor_ref",
            ),
            optional=("evidence_ref", "occurred_at"),
        )
        abc4rd_id = _required_text(data, "abc4rd_id")
        action = _required_text(data, "action").upper()
        if action not in ("GRANTED", "WITHDRAWN"):
            raise ValidationError("action must be GRANTED or WITHDRAWN")
        actor_type, actor_ref = self._actor(data)

        def create(connection: sqlite3.Connection):
            self._identity_exists(connection, abc4rd_id)
            record_id = _new_id()
            occurred_at = _timestamp(data.get("occurred_at"))
            response = {
                "consent_record_id": record_id,
                "abc4rd_id": abc4rd_id,
                "consent_type": _required_text(data, "consent_type"),
                "policy_ref": _required_text(data, "policy_ref"),
                "action": action,
                "evidence_ref": _optional_text(data, "evidence_ref"),
                "source": _required_text(data, "source"),
                "occurred_at": occurred_at,
            }
            connection.execute(
                "INSERT INTO consent_records "
                "(consent_record_id, abc4rd_id, consent_type, policy_ref, action, "
                "evidence_ref, source, occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(response[name] for name in response),
            )
            self._emit_event(
                connection,
                "core.consent.recorded",
                "abc4rd_identity",
                abc4rd_id,
                actor_type,
                actor_ref,
                occurred_at,
                {"consent_record_id": record_id, "action": action},
            )
            self._audit(
                connection,
                "consent.record",
                actor_type,
                actor_ref,
                "consent_record",
                record_id,
                response,
                occurred_at,
            )
            return "consent_record", record_id, response

        return self._write("consent.record", idempotency_key, data, create)

    def record_entitlement(self, data: Dict[str, Any], idempotency_key: str) -> Dict[str, Any]:
        _keys(
            data,
            required=(
                "abc4rd_id",
                "resource_type",
                "resource_ref",
                "action",
                "authority_ref",
                "actor_type",
                "actor_ref",
            ),
            optional=("evidence_ref", "occurred_at"),
        )
        abc4rd_id = _required_text(data, "abc4rd_id")
        action = _required_text(data, "action").upper()
        if action not in ("GRANTED", "REVOKED"):
            raise ValidationError("action must be GRANTED or REVOKED")
        actor_type, actor_ref = self._actor(data)

        def create(connection: sqlite3.Connection):
            self._identity_exists(connection, abc4rd_id)
            record_id = _new_id()
            occurred_at = _timestamp(data.get("occurred_at"))
            response = {
                "entitlement_record_id": record_id,
                "abc4rd_id": abc4rd_id,
                "resource_type": _required_text(data, "resource_type"),
                "resource_ref": _required_text(data, "resource_ref"),
                "action": action,
                "authority_ref": _required_text(data, "authority_ref"),
                "evidence_ref": _optional_text(data, "evidence_ref"),
                "occurred_at": occurred_at,
            }
            connection.execute(
                "INSERT INTO entitlement_records "
                "(entitlement_record_id, abc4rd_id, resource_type, resource_ref, action, "
                "authority_ref, evidence_ref, occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(response[name] for name in response),
            )
            self._emit_event(
                connection,
                "core.entitlement.recorded",
                "abc4rd_identity",
                abc4rd_id,
                actor_type,
                actor_ref,
                occurred_at,
                {"entitlement_record_id": record_id, "action": action},
            )
            self._audit(
                connection,
                "entitlement.record",
                actor_type,
                actor_ref,
                "entitlement_record",
                record_id,
                response,
                occurred_at,
            )
            return "entitlement_record", record_id, response

        return self._write("entitlement.record", idempotency_key, data, create)

    def record_event(self, data: Dict[str, Any], idempotency_key: str) -> Dict[str, Any]:
        _keys(
            data,
            required=(
                "event_type",
                "aggregate_type",
                "aggregate_ref",
                "source",
                "actor_type",
                "actor_ref",
                "payload",
            ),
            optional=("correlation_id", "occurred_at"),
        )
        if not isinstance(data["payload"], dict):
            raise ValidationError("payload must be a JSON object")
        actor_type, actor_ref = self._actor(data)

        def create(connection: sqlite3.Connection):
            occurred_at = _timestamp(data.get("occurred_at"))
            event_id = self._emit_event(
                connection,
                _required_text(data, "event_type"),
                _required_text(data, "aggregate_type"),
                _required_text(data, "aggregate_ref"),
                actor_type,
                actor_ref,
                occurred_at,
                data["payload"],
                source=_required_text(data, "source"),
                correlation_id=_optional_text(data, "correlation_id"),
            )
            response = {"event_id": event_id, "recorded_at": _utc_now()}
            self._audit(
                connection,
                "event.record",
                actor_type,
                actor_ref,
                "domain_event",
                event_id,
                {
                    "event_type": data["event_type"],
                    "aggregate_type": data["aggregate_type"],
                    "aggregate_ref": data["aggregate_ref"],
                    "source": data["source"],
                },
                occurred_at,
            )
            return "domain_event", event_id, response

        return self._write("event.record", idempotency_key, data, create)

    def record_payment(self, data: Dict[str, Any], idempotency_key: str) -> Dict[str, Any]:
        _keys(
            data,
            required=(
                "mode",
                "provider",
                "provider_event_ref",
                "fact_type",
                "entry_type",
                "amount_minor",
                "currency",
                "metadata",
                "actor_type",
                "actor_ref",
            ),
            optional=("abc4rd_id", "provider_evidence_ref", "occurred_at"),
        )
        mode = _required_text(data, "mode").upper()
        if mode not in ("SANDBOX", "LIVE"):
            raise ValidationError("mode must be SANDBOX or LIVE")
        provider = _required_text(data, "provider")
        if mode == "LIVE":
            if not self.live_payment_provider or not self.live_payment_gate_ref:
                raise ValidationError(
                    "LIVE ledger modelling is disabled until provider and gate configuration exist"
                )
            if provider != self.live_payment_provider:
                raise ValidationError("LIVE provider does not match the configured provider")
        fact_type = _required_text(data, "fact_type").upper()
        if fact_type not in PAYMENT_FACT_TYPES:
            raise ValidationError(
                "fact_type must be ATTEMPT, PROVIDER_CONFIRMED_CHARGE, "
                "or PROVIDER_CONFIRMED_REFUND"
            )
        amount = data["amount_minor"]
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise ValidationError("amount_minor must be a non-negative integer")
        currency = _required_text(data, "currency").upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValidationError("currency must be a three-letter code")
        if amount != PILOT_PRICE_MINOR or currency != PILOT_PRICE_CURRENCY:
            raise ValidationError("pilot price target is USD 1.00 (amount_minor=100)")
        if not isinstance(data["metadata"], dict):
            raise ValidationError("metadata must be a JSON object")
        provider_evidence_ref = _optional_text(data, "provider_evidence_ref")
        if fact_type != "ATTEMPT" and provider_evidence_ref is None:
            raise ValidationError("provider-confirmed facts require provider_evidence_ref")
        abc4rd_id = _optional_text(data, "abc4rd_id")
        actor_type, actor_ref = self._actor(data)

        def create(connection: sqlite3.Connection):
            if abc4rd_id is not None:
                self._identity_exists(connection, abc4rd_id)
            entry_id = _new_id()
            occurred_at = _timestamp(data.get("occurred_at"))
            response = {
                "ledger_entry_id": entry_id,
                "mode": mode,
                "provider": provider,
                "provider_event_ref": _required_text(data, "provider_event_ref"),
                "fact_type": fact_type,
                "entry_type": _required_text(data, "entry_type"),
                "amount_minor": amount,
                "currency": currency,
                "abc4rd_id": abc4rd_id,
                "provider_evidence_ref": provider_evidence_ref,
                "metadata": data["metadata"],
                "recognized_charge": fact_type == "PROVIDER_CONFIRMED_CHARGE",
                "occurred_at": occurred_at,
            }
            connection.execute(
                "INSERT INTO payment_ledger_entries "
                "(ledger_entry_id, mode, provider, provider_event_ref, fact_type, entry_type, "
                "amount_minor, currency, abc4rd_id, provider_evidence_ref, metadata_json, "
                "occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry_id,
                    mode,
                    response["provider"],
                    response["provider_event_ref"],
                    fact_type,
                    response["entry_type"],
                    amount,
                    currency,
                    abc4rd_id,
                    provider_evidence_ref,
                    _canonical(data["metadata"]),
                    occurred_at,
                ),
            )
            self._emit_event(
                connection,
                "core.payment_ledger.recorded",
                "payment_ledger_entry",
                entry_id,
                actor_type,
                actor_ref,
                occurred_at,
                {
                    "mode": mode,
                    "provider_event_ref": response["provider_event_ref"],
                    "fact_type": fact_type,
                    "recognized_charge": response["recognized_charge"],
                },
            )
            self._audit(
                connection,
                "payment.record",
                actor_type,
                actor_ref,
                "payment_ledger_entry",
                entry_id,
                response,
                occurred_at,
            )
            return "payment_ledger_entry", entry_id, response

        return self._write("payment.record", idempotency_key, data, create)

    def open_review(self, data: Dict[str, Any], idempotency_key: str) -> Dict[str, Any]:
        _keys(
            data,
            required=(
                "subject_type",
                "subject_ref",
                "review_kind",
                "review_stage",
                "risk_level",
                "requested_authority_role",
                "opened_by_ref",
                "actor_type",
                "actor_ref",
            ),
            optional=("prior_review_decision_id", "occurred_at"),
        )
        review_stage = _required_text(data, "review_stage").upper()
        if review_stage not in REVIEW_STAGES:
            raise ValidationError(
                "review_stage must be PRIMARY, INDEPENDENT_REVIEW, or APPEAL"
            )
        risk_level = _required_text(data, "risk_level").upper()
        if risk_level not in ("NORMAL", "HIGH"):
            raise ValidationError("risk_level must be NORMAL or HIGH")
        prior_decision_id = _optional_text(data, "prior_review_decision_id")
        if review_stage == "PRIMARY" and prior_decision_id is not None:
            raise ValidationError("PRIMARY review cannot reference a prior decision")
        if review_stage != "PRIMARY" and prior_decision_id is None:
            raise ValidationError("independent review or appeal requires a prior decision")
        actor_type, actor_ref = self._actor(data)

        def create(connection: sqlite3.Connection):
            if prior_decision_id is not None:
                prior = connection.execute(
                    "SELECT 1 FROM review_decisions WHERE review_decision_id = ?",
                    (prior_decision_id,),
                ).fetchone()
                if prior is None:
                    raise NotFoundError("prior review decision was not found")
            case_id = _new_id()
            occurred_at = _timestamp(data.get("occurred_at"))
            response = {
                "review_case_id": case_id,
                "subject_type": _required_text(data, "subject_type"),
                "subject_ref": _required_text(data, "subject_ref"),
                "review_kind": _required_text(data, "review_kind"),
                "review_stage": review_stage,
                "prior_review_decision_id": prior_decision_id,
                "risk_level": risk_level,
                "requested_authority_role": _required_text(data, "requested_authority_role"),
                "opened_by_ref": _required_text(data, "opened_by_ref"),
                "occurred_at": occurred_at,
            }
            connection.execute(
                "INSERT INTO review_cases "
                "(review_case_id, subject_type, subject_ref, review_kind, "
                "requested_authority_role, opened_by_ref, occurred_at, review_stage, "
                "prior_review_decision_id, risk_level) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    case_id,
                    response["subject_type"],
                    response["subject_ref"],
                    response["review_kind"],
                    response["requested_authority_role"],
                    response["opened_by_ref"],
                    occurred_at,
                    review_stage,
                    prior_decision_id,
                    risk_level,
                ),
            )
            reasons = ["UNRESOLVED"]
            if risk_level == "HIGH":
                reasons.append("HIGH_RISK")
            outbox_event_id = self._enqueue_oversight(
                connection,
                "REVIEW_UNRESOLVED",
                case_id,
                None,
                reasons,
                {
                    "review_stage": review_stage,
                    "review_kind": response["review_kind"],
                    "subject_type": response["subject_type"],
                    "subject_ref": response["subject_ref"],
                },
                occurred_at,
            )
            response["oversight_outbox_event_id"] = outbox_event_id
            self._emit_event(
                connection,
                "core.review.opened",
                "review_case",
                case_id,
                actor_type,
                actor_ref,
                occurred_at,
                {
                    "review_kind": response["review_kind"],
                    "review_stage": review_stage,
                    "risk_level": risk_level,
                    "oversight_outbox_event_id": outbox_event_id,
                },
            )
            self._audit(
                connection,
                "review.open",
                actor_type,
                actor_ref,
                "review_case",
                case_id,
                response,
                occurred_at,
            )
            return "review_case", case_id, response

        return self._write("review.open", idempotency_key, data, create)

    def decide_review(self, data: Dict[str, Any], idempotency_key: str) -> Dict[str, Any]:
        _keys(
            data,
            required=(
                "review_case_id",
                "outcome",
                "reviewer_agent_id",
                "reviewer_model",
                "reviewer_version",
                "actor_type",
                "actor_ref",
            ),
            optional=("rationale", "evidence_ref", "occurred_at"),
        )
        outcome = _required_text(data, "outcome").upper()
        if outcome not in ("APPROVED", "REJECTED", "CHANGES_REQUESTED"):
            raise ValidationError("outcome must be APPROVED, REJECTED, or CHANGES_REQUESTED")
        actor_type, actor_ref = self._actor(data)
        reviewer_agent_id = _required_text(data, "reviewer_agent_id")
        reviewer_model = _required_text(data, "reviewer_model")
        reviewer_version = _required_text(data, "reviewer_version")
        if actor_type.upper() != "AI_AGENT":
            raise ValidationError("AI-first review decisions require actor_type AI_AGENT")
        if actor_ref != reviewer_agent_id:
            raise ValidationError("actor_ref must match reviewer_agent_id")
        case_id = _required_text(data, "review_case_id")

        def create(connection: sqlite3.Connection):
            review_case = connection.execute(
                "SELECT review_stage, prior_review_decision_id FROM review_cases "
                "WHERE review_case_id = ?",
                (case_id,),
            ).fetchone()
            if review_case is None:
                raise NotFoundError("review case was not found")
            if review_case["review_stage"] != "PRIMARY":
                prior = connection.execute(
                    "SELECT reviewer_agent_id FROM review_decisions "
                    "WHERE review_decision_id = ?",
                    (review_case["prior_review_decision_id"],),
                ).fetchone()
                if prior is None:
                    raise NotFoundError("prior review decision was not found")
                if prior["reviewer_agent_id"] == reviewer_agent_id:
                    raise ValidationError(
                        "independent review or appeal must use a different reviewer_agent_id"
                    )
            decision_id = _new_id()
            occurred_at = _timestamp(data.get("occurred_at"))
            response = {
                "review_decision_id": decision_id,
                "review_case_id": case_id,
                "outcome": outcome,
                "reviewer_agent_id": reviewer_agent_id,
                "reviewer_model": reviewer_model,
                "reviewer_version": reviewer_version,
                "rationale": _optional_text(data, "rationale"),
                "evidence_ref": _optional_text(data, "evidence_ref"),
                "occurred_at": occurred_at,
            }
            connection.execute(
                "INSERT INTO review_decisions "
                "(review_decision_id, review_case_id, outcome, decided_by_ref, "
                "rationale, evidence_ref, occurred_at, reviewer_agent_id, reviewer_model, "
                "reviewer_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    decision_id,
                    case_id,
                    outcome,
                    reviewer_agent_id,
                    response["rationale"],
                    response["evidence_ref"],
                    occurred_at,
                    reviewer_agent_id,
                    reviewer_model,
                    reviewer_version,
                ),
            )
            outbox_event_id = None
            if outcome in ADVERSE_OUTCOMES:
                outbox_event_id = self._enqueue_oversight(
                    connection,
                    "REVIEW_ADVERSE_DECISION",
                    case_id,
                    decision_id,
                    ["ADVERSE_DECISION", outcome],
                    {
                        "outcome": outcome,
                        "reviewer_agent_id": reviewer_agent_id,
                        "reviewer_model": reviewer_model,
                        "reviewer_version": reviewer_version,
                    },
                    occurred_at,
                )
            response["oversight_outbox_event_id"] = outbox_event_id
            self._emit_event(
                connection,
                "core.review.decided",
                "review_case",
                case_id,
                actor_type,
                actor_ref,
                occurred_at,
                {
                    "review_decision_id": decision_id,
                    "outcome": outcome,
                    "reviewer_agent_id": reviewer_agent_id,
                    "oversight_outbox_event_id": outbox_event_id,
                },
            )
            self._audit(
                connection,
                "review.decide",
                actor_type,
                actor_ref,
                "review_decision",
                decision_id,
                response,
                occurred_at,
            )
            return "review_decision", decision_id, response

        return self._write("review.decide", idempotency_key, data, create)

    def register_credential(self, data: Dict[str, Any], idempotency_key: str) -> Dict[str, Any]:
        _keys(
            data,
            required=(
                "abc4rd_id",
                "credential_type",
                "achievement_ref",
                "format",
                "payload_ref",
                "payload_sha256",
                "approval_decision_id",
                "actor_type",
                "actor_ref",
            ),
            optional=("issued_at",),
        )
        abc4rd_id = _required_text(data, "abc4rd_id")
        approval_id = _required_text(data, "approval_decision_id")
        payload_sha256 = _required_text(data, "payload_sha256").lower()
        if SHA256_RE.fullmatch(payload_sha256) is None:
            raise ValidationError("payload_sha256 must be 64 lowercase hexadecimal characters")
        actor_type, actor_ref = self._actor(data)

        def create(connection: sqlite3.Connection):
            self._identity_exists(connection, abc4rd_id)
            approval = connection.execute(
                "SELECT outcome, reviewer_agent_id, reviewer_model, reviewer_version "
                "FROM review_decisions WHERE review_decision_id = ?",
                (approval_id,),
            ).fetchone()
            if approval is None:
                raise NotFoundError("approval review decision was not found")
            if approval["outcome"] != "APPROVED":
                raise ValidationError("credential registration requires an APPROVED AI decision")
            if not all(
                approval[name]
                for name in ("reviewer_agent_id", "reviewer_model", "reviewer_version")
            ):
                raise ValidationError(
                    "credential registration requires AI reviewer identity, model, and version"
                )
            credential_id = _new_id()
            issued_at = _timestamp(data.get("issued_at"))
            response = {
                "credential_id": credential_id,
                "abc4rd_id": abc4rd_id,
                "credential_type": _required_text(data, "credential_type"),
                "achievement_ref": _required_text(data, "achievement_ref"),
                "format": _required_text(data, "format"),
                "payload_ref": _required_text(data, "payload_ref"),
                "payload_sha256": payload_sha256,
                "approval_decision_id": approval_id,
                "issued_at": issued_at,
            }
            connection.execute(
                "INSERT INTO credential_records "
                "(credential_id, abc4rd_id, credential_type, achievement_ref, format, "
                "payload_ref, payload_sha256, approval_decision_id, issued_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(response[name] for name in response),
            )
            self._emit_event(
                connection,
                "core.credential.registered",
                "credential",
                credential_id,
                actor_type,
                actor_ref,
                issued_at,
                {"abc4rd_id": abc4rd_id, "approval_decision_id": approval_id},
            )
            self._audit(
                connection,
                "credential.register",
                actor_type,
                actor_ref,
                "credential",
                credential_id,
                response,
                issued_at,
            )
            return "credential", credential_id, response

        return self._write("credential.register", idempotency_key, data, create)

    def list_oversight_outbox(self, limit: int = 100) -> Dict[str, Any]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValidationError("limit must be an integer from 1 to 500")
        connection = connect(self.database)
        try:
            rows = connection.execute(
                "SELECT outbox_event_id, event_type, review_case_id, review_decision_id, "
                "destination_ref, delivery_status, reasons_json, payload_json, created_at "
                "FROM oversight_outbox_events ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            events = []
            for row in rows:
                item = dict(row)
                item["reasons"] = json.loads(item.pop("reasons_json"))
                item["payload"] = json.loads(item.pop("payload_json"))
                events.append(item)
            return {"events": events, "count": len(events), "sending_implemented": False}
        finally:
            connection.close()

    def list_audit(self, limit: int = 100) -> Dict[str, Any]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValidationError("limit must be an integer from 1 to 500")
        connection = connect(self.database)
        try:
            rows = connection.execute(
                "SELECT sequence, audit_id, operation, actor_type, actor_ref, object_type, "
                "object_ref, details_json, occurred_at, previous_hash, entry_hash "
                "FROM audit_entries ORDER BY sequence DESC LIMIT ?",
                (limit,),
            ).fetchall()
            entries = []
            for row in rows:
                item = dict(row)
                item["details"] = json.loads(item.pop("details_json"))
                entries.append(item)
            return {"entries": entries, "count": len(entries)}
        finally:
            connection.close()

    def verify_audit_chain(self) -> Dict[str, Any]:
        connection = connect(self.database)
        try:
            rows = connection.execute(
                "SELECT sequence, audit_id, operation, actor_type, actor_ref, object_type, "
                "object_ref, details_json, occurred_at, previous_hash, entry_hash "
                "FROM audit_entries ORDER BY sequence"
            ).fetchall()
            expected_previous = ZERO_HASH
            for row in rows:
                if row["previous_hash"] != expected_previous:
                    return {"valid": False, "failed_sequence": row["sequence"]}
                material = {
                    "audit_id": row["audit_id"],
                    "operation": row["operation"],
                    "actor_type": row["actor_type"],
                    "actor_ref": row["actor_ref"],
                    "object_type": row["object_type"],
                    "object_ref": row["object_ref"],
                    "details": json.loads(row["details_json"]),
                    "occurred_at": row["occurred_at"],
                    "previous_hash": row["previous_hash"],
                }
                calculated = hashlib.sha256(_canonical(material).encode("utf-8")).hexdigest()
                if calculated != row["entry_hash"]:
                    return {"valid": False, "failed_sequence": row["sequence"]}
                expected_previous = row["entry_hash"]
            return {"valid": True, "entries": len(rows), "head_hash": expected_previous}
        finally:
            connection.close()
