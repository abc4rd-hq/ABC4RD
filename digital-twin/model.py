"""Deterministic, network-free tokenomics model for the ABC4RD pilot.

All ledger amounts are integer simulation units (SIM). The canonical course-price
reference is USD 1.00 (100 minor units), but this module cannot move real money,
send notifications, mint tokens, or call an external service.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


COURSE_CURRENCY = "USD"
COURSE_PRICE_MINOR = 100
TBD_MAILBOX = "TBD_MAILBOX"

PAYMENT_GATES = (
    "legal_recipient_approved",
    "provider_verified",
    "sandbox_charge_passed",
    "sandbox_refund_passed",
    "webhook_idempotency_passed",
    "reconciliation_passed",
    "human_go_live_approved",
)

VALUE_EVENTS = {
    "sandbox_payment",
    "future_real_charge_simulation",
    "scholarship",
    "reward",
}


class ScenarioError(ValueError):
    """Raised when a scenario is structurally invalid."""


@dataclass
class Participant:
    participant_id: str
    access: bool = False
    frozen: bool = False
    paid_units: int = 0
    refunded_units: int = 0
    scholarship_units: int = 0
    reward_units: int = 0


@dataclass
class Treasury:
    initial_reserve: int
    available_units: int
    payment_receipts: int = 0
    refunds: int = 0
    scholarships: int = 0
    rewards: int = 0


@dataclass
class EventResult:
    event_id: str
    event_type: str
    status: str
    reason: str


@dataclass
class AIReview:
    review_id: str
    case_id: str
    role: str
    reviewer_id: str
    participant_id: str
    decision: str
    primary_review_id: Optional[str] = None


@dataclass
class NotificationRecord:
    notification_id: str
    case_id: str
    participant_id: str
    mailbox_id: str
    trigger: str
    delivery_status: str = "not_sent"


@dataclass
class DigitalTwin:
    participant_ids: List[str]
    initial_treasury: int
    payment_gates: Optional[Dict[str, bool]] = None
    participants: Dict[str, Participant] = field(init=False)
    treasury: Treasury = field(init=False)
    paused: bool = False
    pause_reason: Optional[str] = None
    results: List[EventResult] = field(default_factory=list)
    reviews: Dict[str, AIReview] = field(default_factory=dict)
    notifications: List[NotificationRecord] = field(default_factory=list)
    _processed: Dict[str, EventResult] = field(default_factory=dict)
    _event_payloads: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    _payments: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.participant_ids) != 3:
            raise ScenarioError("the minimal twin requires exactly three participants")
        if len(set(self.participant_ids)) != len(self.participant_ids):
            raise ScenarioError("participant ids must be unique")
        if any(not isinstance(item, str) or not item.strip() for item in self.participant_ids):
            raise ScenarioError("participant ids must be non-empty strings")
        if self.initial_treasury < 0:
            raise ScenarioError("initial treasury cannot be negative")

        supplied_gates = self.payment_gates or {}
        unknown_gates = sorted(set(supplied_gates) - set(PAYMENT_GATES))
        if unknown_gates:
            raise ScenarioError(f"unknown payment gates: {', '.join(unknown_gates)}")
        if any(not isinstance(value, bool) for value in supplied_gates.values()):
            raise ScenarioError("payment gate values must be booleans")
        self.payment_gates = {
            gate: supplied_gates.get(gate, False)
            for gate in PAYMENT_GATES
        }

        self.participants = {
            participant_id: Participant(participant_id)
            for participant_id in self.participant_ids
        }
        self.treasury = Treasury(
            initial_reserve=self.initial_treasury,
            available_units=self.initial_treasury,
        )

    def apply(self, event: Dict[str, Any]) -> EventResult:
        event_id = self._required_text(event, "id")
        event_type = self._required_text(event, "type")
        payload = {key: value for key, value in event.items() if key != "expect"}

        if event_id in self._processed:
            original = self._processed[event_id]
            if self._event_payloads[event_id] != payload:
                result = EventResult(
                    event_id,
                    event_type,
                    "blocked",
                    "event id reused with different payload",
                )
                self.results.append(result)
                return result
            result = EventResult(
                event_id,
                event_type,
                "duplicate",
                f"already processed as {original.status}",
            )
            self.results.append(result)
            return result

        participant = self._participant_for(event, event_type)

        if event_type in VALUE_EVENTS and self.paused:
            result = self._record(event_id, event_type, "blocked", "emergency pause active")
        elif event_type in VALUE_EVENTS and participant and participant.frozen:
            result = self._record(event_id, event_type, "blocked", "participant frozen")
        elif event_type == "sandbox_payment":
            result = self._payment(event_id, event, participant, event_type)
        elif event_type == "future_real_charge_simulation":
            result = self._future_charge(event_id, event, participant)
        elif event_type == "future_real_charge_readiness":
            result = self._future_charge_readiness(event_id, event_type, event)
        elif event_type == "payment_decline":
            mismatch = self._course_price_mismatch(event)
            if mismatch:
                result = self._record(event_id, event_type, "blocked", mismatch)
            else:
                result = self._record(
                    event_id,
                    event_type,
                    "declined",
                    "sandbox processor declined",
                )
        elif event_type == "refund":
            result = self._refund(event_id, event, participant)
        elif event_type == "reconciliation":
            result = self._reconciliation(event_id, event, participant)
        elif event_type == "scholarship":
            result = self._scholarship(event_id, event, participant)
        elif event_type == "reward":
            result = self._reward(event_id, event, participant)
        elif event_type == "ai_primary_review":
            result = self._ai_primary_review(event_id, event, participant)
        elif event_type == "ai_second_reconsideration":
            result = self._ai_second_reconsideration(event_id, event, participant)
        elif event_type == "notification":
            result = self._notification(event_id, event, participant)
        elif event_type == "abuse":
            result = self._abuse(event_id, event, participant)
        elif event_type == "emergency_pause":
            reason = self._required_text(event, "reason")
            self.paused = True
            self.pause_reason = reason
            result = self._record(event_id, event_type, "applied", reason)
        else:
            raise ScenarioError(f"unknown event type: {event_type}")

        self._event_payloads[event_id] = payload
        self._assert_treasury_invariant()
        return result

    def _payment(
        self,
        event_id: str,
        event: Dict[str, Any],
        participant: Participant,
        event_type: str,
    ) -> EventResult:
        mismatch = self._course_price_mismatch(event)
        if mismatch:
            return self._record(event_id, event_type, "blocked", mismatch)
        amount = self._positive_amount(event)
        participant.paid_units += amount
        participant.access = True
        self.treasury.payment_receipts += amount
        self.treasury.available_units += amount
        self._payments[event_id] = {
            "participant_id": participant.participant_id,
            "amount": amount,
            "currency": COURSE_CURRENCY,
            "kind": event_type,
            "refunded": False,
        }
        reason = (
            "sandbox payment accepted"
            if event_type == "sandbox_payment"
            else "future real charge simulated locally; no external charge made"
        )
        return self._record(event_id, event_type, "applied", reason)

    def _future_charge(
        self,
        event_id: str,
        event: Dict[str, Any],
        participant: Participant,
    ) -> EventResult:
        missing = self._missing_payment_gates()
        if missing:
            return self._record(
                event_id,
                "future_real_charge_simulation",
                "blocked",
                f"payment gates not satisfied: {', '.join(missing)}",
            )
        return self._payment(
            event_id,
            event,
            participant,
            "future_real_charge_simulation",
        )

    def _future_charge_readiness(
        self,
        event_id: str,
        event_type: str,
        event: Dict[str, Any],
    ) -> EventResult:
        mismatch = self._course_price_mismatch(event)
        if mismatch:
            return self._record(event_id, event_type, "blocked", mismatch)
        missing = self._missing_payment_gates()
        if missing:
            return self._record(
                event_id,
                event_type,
                "blocked",
                f"payment gates not satisfied: {', '.join(missing)}",
            )
        return self._record(
            event_id,
            event_type,
            "ready",
            "eligible for future human-authorized external charge; no charge made",
        )

    def _refund(
        self,
        event_id: str,
        event: Dict[str, Any],
        participant: Participant,
    ) -> EventResult:
        mismatch = self._course_price_mismatch(event)
        if mismatch:
            return self._record(event_id, "refund", "blocked", mismatch)
        payment_id = self._required_text(event, "payment_id")
        payment = self._payments.get(payment_id)
        if payment is None:
            return self._record(event_id, "refund", "blocked", "payment not found")
        if payment["participant_id"] != participant.participant_id:
            return self._record(event_id, "refund", "blocked", "payment owner mismatch")
        if payment["refunded"]:
            return self._record(event_id, "refund", "blocked", "payment already refunded")
        if self.treasury.available_units < COURSE_PRICE_MINOR:
            return self._record(event_id, "refund", "blocked", "insufficient treasury reserve")

        payment["refunded"] = True
        participant.refunded_units += COURSE_PRICE_MINOR
        participant.access = participant.scholarship_units > 0
        self.treasury.refunds += COURSE_PRICE_MINOR
        self.treasury.available_units -= COURSE_PRICE_MINOR
        return self._record(event_id, "refund", "applied", "protective refund completed")

    def _reconciliation(
        self,
        event_id: str,
        event: Dict[str, Any],
        participant: Participant,
    ) -> EventResult:
        payment_id = self._required_text(event, "payment_id")
        payment = self._payments.get(payment_id)
        if payment is None:
            return self._record(event_id, "reconciliation", "blocked", "payment not found")
        if payment["participant_id"] != participant.participant_id:
            return self._record(event_id, "reconciliation", "blocked", "payment owner mismatch")

        expected_gross = self._non_negative_integer(event, "expected_gross_minor")
        expected_refund = self._non_negative_integer(event, "expected_refund_minor")
        expected_net = self._non_negative_integer(event, "expected_net_minor")
        actual_gross = payment["amount"]
        actual_refund = payment["amount"] if payment["refunded"] else 0
        actual_net = actual_gross - actual_refund
        if (expected_gross, expected_refund, expected_net) != (
            actual_gross,
            actual_refund,
            actual_net,
        ):
            return self._record(
                event_id,
                "reconciliation",
                "blocked",
                "expected and simulated payment totals differ",
            )
        return self._record(
            event_id,
            "reconciliation",
            "reconciled",
            "simulated charge, refund, and net total agree",
        )

    def _scholarship(
        self,
        event_id: str,
        event: Dict[str, Any],
        participant: Participant,
    ) -> EventResult:
        amount = self._positive_amount(event)
        if self.treasury.available_units < amount:
            return self._record(event_id, "scholarship", "blocked", "insufficient treasury reserve")
        participant.scholarship_units += amount
        participant.access = True
        self.treasury.scholarships += amount
        self.treasury.available_units -= amount
        return self._record(event_id, "scholarship", "applied", "scholarship allocated")

    def _reward(
        self,
        event_id: str,
        event: Dict[str, Any],
        participant: Participant,
    ) -> EventResult:
        amount = self._positive_amount(event)
        if self.treasury.available_units < amount:
            return self._record(event_id, "reward", "blocked", "insufficient treasury reserve")
        participant.reward_units += amount
        self.treasury.rewards += amount
        self.treasury.available_units -= amount
        return self._record(event_id, "reward", "applied", "non-transferable reward recorded")

    def _ai_primary_review(
        self,
        event_id: str,
        event: Dict[str, Any],
        participant: Participant,
    ) -> EventResult:
        review_id = self._required_text(event, "review_id")
        case_id = self._required_text(event, "case_id")
        reviewer_id = self._required_text(event, "reviewer_id")
        decision = self._required_choice(event, "decision", {"low_risk", "high_risk"})
        if review_id in self.reviews:
            return self._record(event_id, "ai_primary_review", "blocked", "review id already used")
        self.reviews[review_id] = AIReview(
            review_id,
            case_id,
            "primary",
            reviewer_id,
            participant.participant_id,
            decision,
        )
        return self._record(event_id, "ai_primary_review", "applied", "primary AI review recorded")

    def _ai_second_reconsideration(
        self,
        event_id: str,
        event: Dict[str, Any],
        participant: Participant,
    ) -> EventResult:
        review_id = self._required_text(event, "review_id")
        primary_review_id = self._required_text(event, "primary_review_id")
        case_id = self._required_text(event, "case_id")
        reviewer_id = self._required_text(event, "reviewer_id")
        decision = self._required_choice(
            event,
            "decision",
            {"cleared", "confirmed_high_risk", "unresolved"},
        )
        if event.get("independent") is not True:
            raise ScenarioError("second AI reconsideration must be explicitly independent")
        if review_id in self.reviews:
            return self._record(
                event_id,
                "ai_second_reconsideration",
                "blocked",
                "review id already used",
            )
        primary = self.reviews.get(primary_review_id)
        if primary is None or primary.role != "primary":
            return self._record(
                event_id,
                "ai_second_reconsideration",
                "blocked",
                "primary AI review not found",
            )
        if primary.case_id != case_id or primary.participant_id != participant.participant_id:
            return self._record(
                event_id,
                "ai_second_reconsideration",
                "blocked",
                "primary review case mismatch",
            )
        if primary.reviewer_id == reviewer_id:
            return self._record(
                event_id,
                "ai_second_reconsideration",
                "blocked",
                "second AI reviewer must have a different identifier",
            )
        self.reviews[review_id] = AIReview(
            review_id,
            case_id,
            "independent_second",
            reviewer_id,
            participant.participant_id,
            decision,
            primary_review_id,
        )
        return self._record(
            event_id,
            "ai_second_reconsideration",
            "applied",
            "independent second-AI reconsideration recorded",
        )

    def _notification(
        self,
        event_id: str,
        event: Dict[str, Any],
        participant: Participant,
    ) -> EventResult:
        notification_id = self._required_text(event, "notification_id")
        case_id = self._required_text(event, "case_id")
        mailbox_id = self._required_text(event, "mailbox_id")
        delivery_mode = self._required_text(event, "delivery_mode")
        if mailbox_id != TBD_MAILBOX:
            raise ScenarioError(f"notification mailbox must remain {TBD_MAILBOX}")
        if delivery_mode != "record_only":
            raise ScenarioError("notification delivery mode must be record_only")
        if any(item.notification_id == notification_id for item in self.notifications):
            return self._record(event_id, "notification", "blocked", "notification id already used")

        second = self._second_review_for_case(case_id, participant.participant_id)
        if second is None or second.decision not in {"confirmed_high_risk", "unresolved"}:
            return self._record(
                event_id,
                "notification",
                "blocked",
                "case is not unresolved or independently confirmed high-risk",
            )
        trigger = (
            "unresolved_case"
            if second.decision == "unresolved"
            else "confirmed_high_risk_case"
        )
        self.notifications.append(
            NotificationRecord(
                notification_id,
                case_id,
                participant.participant_id,
                mailbox_id,
                trigger,
            )
        )
        return self._record(
            event_id,
            "notification",
            "applied",
            "notification event recorded locally; email not sent",
        )

    def _abuse(
        self,
        event_id: str,
        event: Dict[str, Any],
        participant: Participant,
    ) -> EventResult:
        reason = self._required_text(event, "reason")
        case_id = self._required_text(event, "case_id")
        second = self._second_review_for_case(case_id, participant.participant_id)
        notified = any(
            item.case_id == case_id and item.participant_id == participant.participant_id
            for item in self.notifications
        )
        if second is None or second.decision not in {"confirmed_high_risk", "unresolved"}:
            return self._record(event_id, "abuse", "blocked", "independent review chain incomplete")
        if not notified:
            return self._record(event_id, "abuse", "blocked", "required notification not recorded")
        participant.frozen = True
        return self._record(event_id, "abuse", "applied", reason)

    def _second_review_for_case(
        self,
        case_id: str,
        participant_id: str,
    ) -> Optional[AIReview]:
        for review in self.reviews.values():
            if (
                review.role == "independent_second"
                and review.case_id == case_id
                and review.participant_id == participant_id
            ):
                return review
        return None

    def _participant_for(
        self,
        event: Dict[str, Any],
        event_type: str,
    ) -> Optional[Participant]:
        if event_type == "emergency_pause":
            return None
        participant_id = self._required_text(event, "participant_id")
        try:
            return self.participants[participant_id]
        except KeyError as exc:
            raise ScenarioError(f"unknown participant: {participant_id}") from exc

    def _record(
        self,
        event_id: str,
        event_type: str,
        status: str,
        reason: str,
    ) -> EventResult:
        result = EventResult(event_id, event_type, status, reason)
        self._processed[event_id] = result
        self.results.append(result)
        return result

    def _missing_payment_gates(self) -> List[str]:
        return [gate for gate in PAYMENT_GATES if not self.payment_gates[gate]]

    def _course_price_mismatch(self, event: Dict[str, Any]) -> Optional[str]:
        amount = self._positive_amount(event)
        currency = self._required_text(event, "currency")
        if currency != COURSE_CURRENCY or amount != COURSE_PRICE_MINOR:
            return "canonical course price must be USD 1.00 (minor=100)"
        return None

    def _assert_treasury_invariant(self) -> None:
        expected = (
            self.treasury.initial_reserve
            + self.treasury.payment_receipts
            - self.treasury.refunds
            - self.treasury.scholarships
            - self.treasury.rewards
        )
        if self.treasury.available_units != expected or expected < 0:
            raise ScenarioError("treasury conservation invariant violated")

    @staticmethod
    def _required_text(event: Dict[str, Any], field_name: str) -> str:
        value = event.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ScenarioError(f"{field_name} must be a non-empty string")
        return value

    @staticmethod
    def _required_choice(
        event: Dict[str, Any],
        field_name: str,
        choices: set,
    ) -> str:
        value = DigitalTwin._required_text(event, field_name)
        if value not in choices:
            raise ScenarioError(f"{field_name} must be one of: {', '.join(sorted(choices))}")
        return value

    @staticmethod
    def _positive_amount(event: Dict[str, Any]) -> int:
        amount = event.get("amount")
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            raise ScenarioError("amount must be a positive integer")
        return amount

    @staticmethod
    def _non_negative_integer(event: Dict[str, Any], field_name: str) -> int:
        value = event.get(field_name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ScenarioError(f"{field_name} must be a non-negative integer")
        return value

    def summary(self) -> Dict[str, Any]:
        status_counts: Dict[str, int] = {}
        for result in self.results:
            status_counts[result.status] = status_counts.get(result.status, 0) + 1
        return {
            "simulation_only": True,
            "network_actions": 0,
            "real_money_transactions": 0,
            "unit": "SIM",
            "unit_disclaimer": "simulation-only; no monetary or token value",
            "course_price": {
                "currency": COURSE_CURRENCY,
                "minor": COURSE_PRICE_MINOR,
                "display": "USD 1.00",
            },
            "payment_gates": dict(self.payment_gates),
            "payment_gates_passed": not self._missing_payment_gates(),
            "paused": self.paused,
            "pause_reason": self.pause_reason,
            "participants": {
                participant_id: asdict(participant)
                for participant_id, participant in sorted(self.participants.items())
            },
            "treasury": asdict(self.treasury),
            "reviews": [
                asdict(review)
                for review in sorted(self.reviews.values(), key=lambda item: item.review_id)
            ],
            "notifications": [asdict(item) for item in self.notifications],
            "event_status_counts": dict(sorted(status_counts.items())),
            "results": [asdict(result) for result in self.results],
        }


def _validated_course_price(scenario: Dict[str, Any]) -> None:
    price = scenario.get("course_price")
    if not isinstance(price, dict):
        raise ScenarioError("course_price must be an object")
    if price.get("currency") != COURSE_CURRENCY or price.get("minor") != COURSE_PRICE_MINOR:
        raise ScenarioError("canonical course price must be USD 1.00 (minor=100)")


def run_scenario(scenario: Dict[str, Any]) -> Dict[str, Any]:
    if scenario.get("simulation_only") is not True:
        raise ScenarioError("scenario must explicitly set simulation_only=true")
    _validated_course_price(scenario)

    participants = scenario.get("participants")
    if not isinstance(participants, list) or not all(isinstance(item, str) for item in participants):
        raise ScenarioError("participants must be a list of ids")
    initial_treasury = scenario.get("initial_treasury")
    if not isinstance(initial_treasury, int) or isinstance(initial_treasury, bool):
        raise ScenarioError("initial_treasury must be an integer")
    payment_gates = scenario.get("payment_gates")
    if not isinstance(payment_gates, dict):
        raise ScenarioError("payment_gates must be an object")
    events = scenario.get("events")
    if not isinstance(events, list) or not all(isinstance(item, dict) for item in events):
        raise ScenarioError("events must be a list of objects")

    twin = DigitalTwin(participants, initial_treasury, payment_gates)
    for event in events:
        result = twin.apply(event)
        expected_status = event.get("expect")
        if expected_status is not None and result.status != expected_status:
            raise ScenarioError(
                f"event {result.event_id}: expected {expected_status}, got {result.status}"
            )
    return twin.summary()
