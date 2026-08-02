#!/usr/bin/env python3
"""Static contract checks that do not import Frappe or touch a site."""

from __future__ import annotations

import json
import pathlib
import re
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCTYPE_ROOT = ROOT / "apps" / "abc4rd_crm" / "abc4rd_crm" / "abc4rd_crm" / "doctype"


def load_doctype(slug: str) -> dict:
    path = DOCTYPE_ROOT / slug / f"{slug}.json"
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def fields_by_name(document: dict) -> dict[str, dict]:
    return {field["fieldname"]: field for field in document["fields"]}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def require_field(
    fields: dict[str, dict],
    name: str,
    fieldtype: str,
    *,
    reqd: bool = False,
    read_only: bool = False,
    unique: bool = False,
    options: str | None = None,
    length: int | None = None,
) -> None:
    require(name in fields, f"missing field: {name}")
    field = fields[name]
    require(field.get("fieldtype") == fieldtype, f"invalid fieldtype: {name}")
    require(bool(field.get("reqd", 0)) is reqd, f"invalid required flag: {name}")
    require(bool(field.get("read_only", 0)) is read_only, f"invalid read_only flag: {name}")
    require(bool(field.get("unique", 0)) is unique, f"invalid unique flag: {name}")
    if options is not None:
        require(field.get("options") == options, f"invalid options: {name}")
    if length is not None:
        require(field.get("length") == length, f"invalid length: {name}")


def forbid_extra_data_fields(fields: dict[str, dict], expected: set[str], doctype: str) -> None:
    layout_types = {"Section Break", "Column Break"}
    extras = sorted(
        name
        for name, field in fields.items()
        if name not in expected and field.get("fieldtype") not in layout_types
    )
    require(not extras, f"unlisted data fields in {doctype}: {', '.join(extras)}")


def main() -> int:
    participant = load_doctype("abc4rd_participant")
    inquiry = load_doctype("abc4rd_inquiry")
    audit = load_doctype("abc4rd_audit_reference")

    participant_fields = fields_by_name(participant)
    inquiry_fields = fields_by_name(inquiry)
    audit_fields = fields_by_name(audit)

    participant_contract = {
        "abc4rd_id": ("Data", True, False, True, None),
        "pilot_ref": ("Data", False, False, False, None),
        "display_label": ("Data", True, False, False, None),
        "full_name": ("Data", False, False, False, None),
        "email": ("Data", False, False, False, "Email"),
        "phone": ("Data", False, False, False, "Phone"),
        "locale": ("Data", False, False, False, None),
        "timezone": ("Data", False, False, False, None),
        "country_code": ("Data", False, False, False, None),
        "lifecycle_state": ("Select", True, True, False, "candidate\nlearner\nintern\ngraduate\nresident"),
        "lifecycle_source_ref": ("Data", True, True, False, None),
        "lifecycle_synced_at": ("Datetime", True, True, False, None),
        "lifecycle_audit_ref": ("Data", True, True, False, None),
        "responsible_actor_type": ("Select", True, False, False, "AI_AGENT\nSERVICE"),
        "responsible_actor_ref": ("Data", True, False, False, None),
        "financial_state": (
            "Select",
            True,
            True,
            False,
            "NO_VERIFIED_FACT\nSANDBOX_PAYMENT_RECONCILED\nSANDBOX_REFUND_RECONCILED\nSCHOLARSHIP_APPROVED",
        ),
        "financial_source_ref": ("Data", False, True, False, None),
        "financial_verified_at": ("Datetime", False, True, False, None),
        "financial_audit_ref": ("Data", False, True, False, None),
        "course_ref": ("Data", False, True, False, None),
        "course_synced_at": ("Datetime", False, True, False, None),
        "course_audit_ref": ("Data", False, True, False, None),
        "graduation_state": (
            "Select",
            True,
            True,
            False,
            "NO_VERIFIED_FACT\nRELEASE_ELIGIBLE\nRELEASE_APPROVED\nRELEASE_DENIED\nRELEASED",
        ),
        "graduation_source_ref": ("Data", False, True, False, None),
        "graduation_verified_at": ("Datetime", False, True, False, None),
        "graduation_audit_ref": ("Data", False, True, False, None),
        "audit_references": ("Table", False, True, False, "ABC4RD Audit Reference"),
    }
    for name, (fieldtype, reqd, read_only, unique, options) in participant_contract.items():
        require_field(
            participant_fields,
            name,
            fieldtype,
            reqd=reqd,
            read_only=read_only,
            unique=unique,
            options=options,
        )
    forbid_extra_data_fields(participant_fields, set(participant_contract), "ABC4RD Participant")
    require_field(participant_fields, "country_code", "Data", length=2)
    require(participant["autoname"] == "field:abc4rd_id", "participant name must be ABC4RD ID")
    require(
        participant_fields["lifecycle_state"]["options"].splitlines()
        == ["candidate", "learner", "intern", "graduate", "resident"],
        "lifecycle order changed",
    )
    require(participant_fields["lifecycle_state"].get("default") == "candidate", "new lifecycle must start as candidate")

    participant_controller = (
        DOCTYPE_ROOT / "abc4rd_participant" / "abc4rd_participant.py"
    ).read_text(encoding="utf-8")
    validators_source = (ROOT / "apps" / "abc4rd_crm" / "abc4rd_crm" / "validators.py").read_text(
        encoding="utf-8"
    )
    require(
        'not previous and self.lifecycle_state != "candidate"' in participant_controller,
        "controller must reject non-candidate lifecycle inserts",
    )
    require("str(parsed) != value" in validators_source, "UUID validator must reject noncanonical case")
    require(
        participant_fields["financial_state"]["options"].splitlines()
        == [
            "NO_VERIFIED_FACT",
            "SANDBOX_PAYMENT_RECONCILED",
            "SANDBOX_REFUND_RECONCILED",
            "SCHOLARSHIP_APPROVED",
        ],
        "financial state must contain verified terminal facts only",
    )
    require(
        participant_fields["graduation_state"]["options"].splitlines()
        == [
            "NO_VERIFIED_FACT",
            "RELEASE_ELIGIBLE",
            "RELEASE_APPROVED",
            "RELEASE_DENIED",
            "RELEASED",
        ],
        "graduation state contract changed",
    )

    forbidden_learning_fragments = ("grade", "score", "progress", "attempt", "submission", "lesson")
    for fieldname in participant_fields:
        require(
            not any(fragment in fieldname.lower() for fragment in forbidden_learning_fragments),
            f"Open edX learning data must not be copied into CRM: {fieldname}",
        )

    inquiry_contract = {
        "participant": ("Link", True, "ABC4RD Participant"),
        "inquiry_kind": (
            "Select",
            True,
            "ADMISSIONS\nACCOUNT_ACCESS\nCOURSE_ACCESS\nFINANCIAL_ACCESS\nRESULT_REVIEW\nGENERAL_SUPPORT",
        ),
        "status": (
            "Select",
            True,
            "OPEN\nIN_PROGRESS\nWAITING_FOR_PARTICIPANT\nWAITING_FOR_SERVICE\nRESOLVED\nCLOSED",
        ),
        "priority": ("Select", True, "LOW\nNORMAL\nHIGH\nURGENT"),
        "subject": ("Data", True, None),
        "description": ("Text Editor", False, None),
        "responsible_actor_type": ("Select", True, "AI_AGENT\nSERVICE"),
        "responsible_actor_ref": ("Data", True, None),
        "source_ref": ("Data", False, None),
        "audit_references": ("Table", False, "ABC4RD Audit Reference"),
    }
    for name, (fieldtype, reqd, options) in inquiry_contract.items():
        require_field(
            inquiry_fields,
            name,
            fieldtype,
            reqd=reqd,
            read_only=name == "audit_references",
            options=options,
        )
    forbid_extra_data_fields(inquiry_fields, set(inquiry_contract), "ABC4RD Inquiry")

    audit_contract = {
        "source_system": (
            "Select",
            True,
            "ACADEMY_CORE\nKEYCLOAK\nOPEN_EDX\nPAYMENT_PROVIDER\nS3\nMATRIX",
        ),
        "event_type": ("Data", True, None),
        "external_ref": ("Data", True, None),
        "occurred_at": ("Datetime", True, None),
        "verified_at": ("Datetime", True, None),
        "evidence_sha256": ("Data", False, None),
    }
    for name, (fieldtype, reqd, options) in audit_contract.items():
        require_field(audit_fields, name, fieldtype, reqd=reqd, options=options)
    forbid_extra_data_fields(audit_fields, set(audit_contract), "ABC4RD Audit Reference")
    require_field(audit_fields, "evidence_sha256", "Data", length=64)
    require(audit.get("istable") == 1, "audit reference must remain a child table")

    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    require("ports:" not in compose, "ERPNext must not publish a host port before reverse-proxy review")
    require(compose.count("@sha256:") >= 3, "runtime images must be pinned by digest")
    require("tutor_local_default" in compose, "Tutor network contract is missing")
    require("abc4rd-erpnext-frontend" in compose, "stable reverse-proxy network alias is missing")

    containerfile = (ROOT / "Containerfile").read_text(encoding="utf-8")
    require("abc4rd_crm.pth" in containerfile, "custom app must use deterministic path registration")
    require("pip install" not in containerfile, "production image must not resolve Python build dependencies")

    restore_override = (ROOT / "docker-compose.restore-drill.yml").read_text(encoding="utf-8")
    require("restore-drill-frontend-disabled" in restore_override, "restore frontend isolation is missing")
    require(
        restore_override.count("restore-drill-workers-disabled") == 3,
        "restore scheduler and workers must be disabled",
    )

    secret_assignment = re.compile(r"(?im)^(?!.*_FILE=).*(PASSWORD|TOKEN|SECRET|KEY)=(?!$|replace-|/).+$")
    for env_name in (".env.example", ".env.restore-drill.example"):
        env_text = (ROOT / env_name).read_text(encoding="utf-8")
        require(not secret_assignment.search(env_text), f"{env_name} appears to contain a secret value")

    print("PASS: full field contracts for 3 DocTypes and deployment invariants validated")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
