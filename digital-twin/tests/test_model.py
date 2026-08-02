import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from model import DigitalTwin, PAYMENT_GATES, ScenarioError, run_scenario  # noqa: E402


SCENARIO = ROOT / "scenarios" / "three-participant.json"
FUTURE_SCENARIO = ROOT / "scenarios" / "future-dollar-after-gates.json"


def load_summary(path):
    with path.open(encoding="utf-8") as source:
        return run_scenario(json.load(source))


def add_unresolved_case(twin, participant_id="A", case_id="CASE-1"):
    primary = twin.apply(
        {
            "id": f"{case_id}-primary-event",
            "type": "ai_primary_review",
            "participant_id": participant_id,
            "case_id": case_id,
            "review_id": f"{case_id}-primary-review",
            "reviewer_id": "AI-PRIMARY-V1",
            "decision": "high_risk",
        }
    )
    second = twin.apply(
        {
            "id": f"{case_id}-second-event",
            "type": "ai_second_reconsideration",
            "participant_id": participant_id,
            "case_id": case_id,
            "review_id": f"{case_id}-second-review",
            "reviewer_id": "AI-SECOND-V1",
            "primary_review_id": f"{case_id}-primary-review",
            "independent": True,
            "decision": "unresolved",
        }
    )
    notification = twin.apply(
        {
            "id": f"{case_id}-notification-event",
            "type": "notification",
            "participant_id": participant_id,
            "case_id": case_id,
            "notification_id": f"{case_id}-notification",
            "mailbox_id": "TBD_MAILBOX",
            "delivery_mode": "record_only",
        }
    )
    return primary, second, notification


class DigitalTwinScenarioTests(unittest.TestCase):
    def test_three_participant_scenario_has_expected_final_state(self):
        summary = load_summary(SCENARIO)

        self.assertTrue(summary["simulation_only"])
        self.assertEqual(summary["network_actions"], 0)
        self.assertEqual(summary["real_money_transactions"], 0)
        self.assertEqual(
            summary["course_price"],
            {"currency": "USD", "minor": 100, "display": "USD 1.00"},
        )
        self.assertFalse(summary["payment_gates_passed"])
        self.assertEqual(sum(summary["payment_gates"].values()), 0)
        self.assertTrue(summary["paused"])
        self.assertEqual(summary["pause_reason"], "synthetic incident drill")
        self.assertEqual(
            summary["event_status_counts"],
            {
                "applied": 12,
                "blocked": 3,
                "declined": 1,
                "duplicate": 1,
                "reconciled": 1,
            },
        )
        self.assertEqual(summary["treasury"]["available_units"], 380)
        self.assertEqual(summary["treasury"]["payment_receipts"], 200)
        self.assertEqual(summary["treasury"]["refunds"], 200)
        self.assertEqual(summary["treasury"]["scholarships"], 100)
        self.assertEqual(summary["treasury"]["rewards"], 20)

        self.assertFalse(summary["participants"]["SYNTH-001"]["access"])
        self.assertTrue(summary["participants"]["SYNTH-002"]["access"])
        self.assertTrue(summary["participants"]["SYNTH-003"]["frozen"])

        self.assertEqual(
            [item["review_id"] for item in summary["reviews"]],
            ["AI-PRIMARY-REVIEW-0001", "AI-SECOND-RECONSIDERATION-0001"],
        )
        self.assertNotEqual(
            summary["reviews"][0]["reviewer_id"],
            summary["reviews"][1]["reviewer_id"],
        )
        self.assertEqual(len(summary["notifications"]), 1)
        self.assertEqual(summary["notifications"][0]["mailbox_id"], "TBD_MAILBOX")
        self.assertEqual(summary["notifications"][0]["delivery_status"], "not_sent")

    def test_future_dollar_scenario_requires_all_gates_and_reconciles(self):
        summary = load_summary(FUTURE_SCENARIO)

        self.assertTrue(summary["payment_gates_passed"])
        self.assertEqual(sum(summary["payment_gates"].values()), len(PAYMENT_GATES))
        self.assertEqual(
            summary["event_status_counts"],
            {"applied": 2, "ready": 1, "reconciled": 1},
        )
        self.assertEqual(summary["treasury"]["payment_receipts"], 100)
        self.assertEqual(summary["treasury"]["refunds"], 100)
        self.assertEqual(summary["treasury"]["available_units"], 500)
        self.assertEqual(summary["network_actions"], 0)
        self.assertEqual(summary["real_money_transactions"], 0)

    def test_future_charge_is_blocked_while_payment_gates_are_missing(self):
        twin = DigitalTwin(["A", "B", "C"], 100)

        readiness = twin.apply(
            {
                "id": "readiness-1",
                "type": "future_real_charge_readiness",
                "participant_id": "A",
                "currency": "USD",
                "amount": 100,
            }
        )
        charge = twin.apply(
            {
                "id": "future-charge-1",
                "type": "future_real_charge_simulation",
                "participant_id": "A",
                "currency": "USD",
                "amount": 100,
            }
        )

        self.assertEqual(readiness.status, "blocked")
        self.assertEqual(charge.status, "blocked")
        self.assertEqual(twin.treasury.payment_receipts, 0)
        self.assertFalse(twin.participants["A"].access)

    def test_noncanonical_course_price_is_blocked(self):
        twin = DigitalTwin(["A", "B", "C"], 100)

        result = twin.apply(
            {
                "id": "payment-1",
                "type": "sandbox_payment",
                "participant_id": "A",
                "currency": "USD",
                "amount": 99,
            }
        )

        self.assertEqual(result.status, "blocked")
        self.assertEqual(twin.treasury.payment_receipts, 0)

    def test_duplicate_payment_is_idempotent(self):
        twin = DigitalTwin(["A", "B", "C"], 100)
        event = {
            "id": "payment-1",
            "type": "sandbox_payment",
            "participant_id": "A",
            "currency": "USD",
            "amount": 100,
        }

        first = twin.apply(event)
        second = twin.apply(event)

        self.assertEqual(first.status, "applied")
        self.assertEqual(second.status, "duplicate")
        self.assertEqual(twin.treasury.payment_receipts, 100)
        self.assertEqual(twin.participants["A"].paid_units, 100)

    def test_reused_event_id_with_different_payload_is_blocked(self):
        twin = DigitalTwin(["A", "B", "C"], 100)
        twin.apply(
            {
                "id": "payment-1",
                "type": "sandbox_payment",
                "participant_id": "A",
                "currency": "USD",
                "amount": 100,
            }
        )

        collision = twin.apply(
            {
                "id": "payment-1",
                "type": "sandbox_payment",
                "participant_id": "A",
                "currency": "USD",
                "amount": 200,
            }
        )

        self.assertEqual(collision.status, "blocked")
        self.assertEqual(twin.treasury.payment_receipts, 100)

    def test_decline_does_not_change_treasury_or_access(self):
        twin = DigitalTwin(["A", "B", "C"], 100)

        result = twin.apply(
            {
                "id": "decline-1",
                "type": "payment_decline",
                "participant_id": "A",
                "currency": "USD",
                "amount": 100,
            }
        )

        self.assertEqual(result.status, "declined")
        self.assertEqual(twin.treasury.available_units, 100)
        self.assertFalse(twin.participants["A"].access)

    def test_ai_review_chain_records_notification_without_sending(self):
        twin = DigitalTwin(["A", "B", "C"], 100)

        primary, second, notification = add_unresolved_case(twin)

        self.assertEqual(primary.status, "applied")
        self.assertEqual(second.status, "applied")
        self.assertEqual(notification.status, "applied")
        self.assertEqual(len(twin.reviews), 2)
        self.assertNotEqual(
            twin.reviews["CASE-1-primary-review"].reviewer_id,
            twin.reviews["CASE-1-second-review"].reviewer_id,
        )
        self.assertEqual(twin.notifications[0].mailbox_id, "TBD_MAILBOX")
        self.assertEqual(twin.notifications[0].delivery_status, "not_sent")

    def test_second_ai_reconsideration_must_use_different_identifier(self):
        twin = DigitalTwin(["A", "B", "C"], 100)
        twin.apply(
            {
                "id": "primary-event",
                "type": "ai_primary_review",
                "participant_id": "A",
                "case_id": "CASE-1",
                "review_id": "PRIMARY-1",
                "reviewer_id": "AI-SAME",
                "decision": "high_risk",
            }
        )

        second = twin.apply(
            {
                "id": "second-event",
                "type": "ai_second_reconsideration",
                "participant_id": "A",
                "case_id": "CASE-1",
                "review_id": "SECOND-1",
                "reviewer_id": "AI-SAME",
                "primary_review_id": "PRIMARY-1",
                "independent": True,
                "decision": "unresolved",
            }
        )

        self.assertEqual(second.status, "blocked")
        self.assertNotIn("SECOND-1", twin.reviews)

    def test_notification_cannot_enable_delivery(self):
        twin = DigitalTwin(["A", "B", "C"], 100)
        add_unresolved_case(twin)

        with self.assertRaises(ScenarioError):
            twin.apply(
                {
                    "id": "unsafe-notification",
                    "type": "notification",
                    "participant_id": "A",
                    "case_id": "CASE-1",
                    "notification_id": "UNSAFE-1",
                    "mailbox_id": "TBD_MAILBOX",
                    "delivery_mode": "send_email",
                }
            )

    def test_abuse_requires_review_and_notification_before_freeze(self):
        twin = DigitalTwin(["A", "B", "C"], 100)

        blocked = twin.apply(
            {
                "id": "abuse-before-review",
                "type": "abuse",
                "participant_id": "A",
                "case_id": "CASE-1",
                "reason": "synthetic signal",
            }
        )
        add_unresolved_case(twin)
        applied = twin.apply(
            {
                "id": "abuse-after-review",
                "type": "abuse",
                "participant_id": "A",
                "case_id": "CASE-1",
                "reason": "synthetic signal",
            }
        )

        self.assertEqual(blocked.status, "blocked")
        self.assertEqual(applied.status, "applied")
        self.assertTrue(twin.participants["A"].frozen)

    def test_abuse_freeze_blocks_reward(self):
        twin = DigitalTwin(["A", "B", "C"], 100)
        add_unresolved_case(twin)
        twin.apply(
            {
                "id": "abuse-1",
                "type": "abuse",
                "participant_id": "A",
                "case_id": "CASE-1",
                "reason": "synthetic signal",
            }
        )

        result = twin.apply(
            {
                "id": "reward-1",
                "type": "reward",
                "participant_id": "A",
                "amount": 20,
            }
        )

        self.assertEqual(result.status, "blocked")
        self.assertEqual(twin.participants["A"].reward_units, 0)

    def test_pause_blocks_value_events_but_allows_protective_refund(self):
        twin = DigitalTwin(["A", "B", "C"], 100)
        twin.apply(
            {
                "id": "payment-1",
                "type": "sandbox_payment",
                "participant_id": "A",
                "currency": "USD",
                "amount": 100,
            }
        )
        twin.apply({"id": "pause-1", "type": "emergency_pause", "reason": "drill"})

        blocked = twin.apply(
            {
                "id": "reward-1",
                "type": "reward",
                "participant_id": "A",
                "amount": 10,
            }
        )
        refunded = twin.apply(
            {
                "id": "refund-1",
                "type": "refund",
                "participant_id": "A",
                "payment_id": "payment-1",
                "currency": "USD",
                "amount": 100,
            }
        )

        self.assertEqual(blocked.status, "blocked")
        self.assertEqual(refunded.status, "applied")
        self.assertEqual(twin.treasury.available_units, 100)
        self.assertFalse(twin.participants["A"].access)

    def test_scholarship_and_reward_require_sufficient_reserve(self):
        twin = DigitalTwin(["A", "B", "C"], 10)

        scholarship = twin.apply(
            {
                "id": "scholarship-1",
                "type": "scholarship",
                "participant_id": "A",
                "amount": 11,
            }
        )
        reward = twin.apply(
            {
                "id": "reward-1",
                "type": "reward",
                "participant_id": "B",
                "amount": 11,
            }
        )

        self.assertEqual(scholarship.status, "blocked")
        self.assertEqual(reward.status, "blocked")
        self.assertEqual(twin.treasury.available_units, 10)

    def test_invalid_participant_and_amount_are_rejected(self):
        twin = DigitalTwin(["A", "B", "C"], 100)

        with self.assertRaises(ScenarioError):
            twin.apply(
                {
                    "id": "payment-1",
                    "type": "sandbox_payment",
                    "participant_id": "UNKNOWN",
                    "currency": "USD",
                    "amount": 100,
                }
            )
        with self.assertRaises(ScenarioError):
            twin.apply(
                {
                    "id": "payment-2",
                    "type": "sandbox_payment",
                    "participant_id": "A",
                    "currency": "USD",
                    "amount": -1,
                }
            )

    def test_invalid_control_events_do_not_partially_change_state(self):
        twin = DigitalTwin(["A", "B", "C"], 100)

        with self.assertRaises(ScenarioError):
            twin.apply({"id": "abuse-1", "type": "abuse", "participant_id": "A"})
        with self.assertRaises(ScenarioError):
            twin.apply({"id": "pause-1", "type": "emergency_pause"})

        self.assertFalse(twin.participants["A"].frozen)
        self.assertFalse(twin.paused)


if __name__ == "__main__":
    unittest.main()
