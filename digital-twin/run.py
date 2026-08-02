#!/usr/bin/env python3
"""Run an ABC4RD tokenomics digital-twin scenario."""

import argparse
import json
from pathlib import Path
from typing import Any, Dict

from model import ScenarioError, run_scenario


DEFAULT_SCENARIO = Path(__file__).with_name("scenarios") / "three-participant.json"


def load_scenario(path: Path) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as source:
        value = json.load(source)
    if not isinstance(value, dict):
        raise ScenarioError("scenario root must be an object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local ABC4RD tokenomics twin")
    parser.add_argument("scenario", nargs="?", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = parser.parse_args()

    try:
        summary = run_scenario(load_scenario(args.scenario))
    except (OSError, json.JSONDecodeError, ScenarioError) as exc:
        parser.exit(1, f"scenario failed: {exc}\n")

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    treasury = summary["treasury"]
    counts = summary["event_status_counts"]
    price = summary["course_price"]
    gates = summary["payment_gates"]
    passed_gates = sum(1 for value in gates.values() if value)
    print("ABC4RD tokenomics digital twin: OK")
    print("Unit: SIM (simulation-only; no monetary or token value)")
    print(f"Canonical course price: {price['display']} (minor={price['minor']})")
    print(f"Payment gates: {passed_gates}/{len(gates)} satisfied in this scenario")
    print(f"Participants: {len(summary['participants'])}")
    status_order = ("applied", "ready", "reconciled", "declined", "blocked", "duplicate")
    print("Events: " + ", ".join(f"{key}={counts.get(key, 0)}" for key in status_order))
    print(
        "Treasury: "
        f"initial={treasury['initial_reserve']}, "
        f"available={treasury['available_units']}, "
        f"receipts={treasury['payment_receipts']}, "
        f"refunds={treasury['refunds']}, "
        f"scholarships={treasury['scholarships']}, "
        f"rewards={treasury['rewards']}"
    )
    print(f"Emergency pause: {'active' if summary['paused'] else 'inactive'}")
    print(
        "AI controls: "
        f"reviews={len(summary['reviews'])}, "
        f"notification events={len(summary['notifications'])}, "
        "emails sent=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
