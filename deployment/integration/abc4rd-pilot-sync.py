#!/usr/bin/env python3
"""Idempotent pilot projection: Open edX/Keycloak -> Core -> ERPNext/Portal."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LMS_CONTAINER = os.environ.get("ABC4RD_LMS_CONTAINER", "tutor_local-lms-1")
CORE_CONTAINER = os.environ.get("ABC4RD_CORE_CONTAINER", "abc4rd-academy-core")
ERP_CONTAINER = os.environ.get("ABC4RD_ERP_CONTAINER", "abc4rd-erpnext-backend-1")
ERP_SITE = os.environ.get("ABC4RD_ERP_SITE", "crm.abc4rd.org")
USERNAME_PREFIX = os.environ.get("ABC4RD_SYNC_USERNAME_PREFIX", "pilot_")
STATE_PATH = Path(os.environ.get("ABC4RD_PORTAL_STATE", "/opt/abc4rd/portal/data/state.json"))
ACTOR_REF = "abc4rd-pilot-sync:v1"
MARKER = "ABC4RD_SYNC_JSON="


CORE_BRIDGE = r"""
import json, sys, urllib.error, urllib.request
message = json.load(sys.stdin)
url = "http://127.0.0.1:8080" + message["path"]
headers = {"Accept": "application/json"}
data = None
method = message.get("method", "GET")
if method == "POST":
    data = json.dumps(message["body"], separators=(",", ":")).encode("utf-8")
    headers["Content-Type"] = "application/json"
    headers["Idempotency-Key"] = message["key"]
request = urllib.request.Request(url, data=data, method=method, headers=headers)
try:
    with urllib.request.urlopen(request, timeout=15) as response:
        print(response.read().decode("utf-8"))
except urllib.error.HTTPError as error:
    sys.stderr.write(error.read().decode("utf-8"))
    raise
""".strip()


def run(command: list[str], *, input_text: str | None = None, timeout: int = 90) -> str:
    result = subprocess.run(
        command,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        stderr = result.stderr.strip().splitlines()
        detail = stderr[-1] if stderr else f"exit {result.returncode}"
        raise RuntimeError(f"{command[0]} failed: {detail}")
    return result.stdout.strip()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def frappe_time(value: str) -> str:
    return value.replace("T", " ").removesuffix("Z")


def lms_snapshot() -> list[dict[str, Any]]:
    prefix = json.dumps(USERNAME_PREFIX)
    code = f"""
import json
from social_django.models import UserSocialAuth
from common.djangoapps.student.models import CourseEnrollment
from lms.djangoapps.grades.course_grade_factory import CourseGradeFactory
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
rows=[]
for social in UserSocialAuth.objects.filter(provider='identityServer3', user__username__startswith={prefix}).select_related('user').order_by('user__username'):
    user=social.user
    courses=[]
    for enrollment in CourseEnrollment.objects.filter(user=user,is_active=True).order_by('course_id'):
        try:
            grade=CourseGradeFactory().read(user,course_key=enrollment.course_id)
            percent=round(float(grade.percent)*100,2)
            passed=bool(grade.passed)
        except Exception:
            percent=0.0
            passed=False
        try:
            title=CourseOverview.get_from_id(enrollment.course_id).display_name
        except Exception:
            title=str(enrollment.course_id)
        courses.append({{'course_ref':str(enrollment.course_id),'title':title,'grade_percent':percent,'passed':passed}})
    rows.append({{'lms_user_id':user.id,'username':user.username,'keycloak_subject':social.uid,'courses':courses}})
print('{MARKER}'+json.dumps(rows,ensure_ascii=False,separators=(',',':')))
""".strip()
    shell = f"python manage.py lms shell -c {shlex.quote(code)} 2>/dev/null"
    output = run(["docker", "exec", LMS_CONTAINER, "bash", "-lc", shell], timeout=120)
    for line in reversed(output.splitlines()):
        if line.startswith(MARKER):
            data = json.loads(line[len(MARKER) :])
            if isinstance(data, list):
                return data
    raise RuntimeError("Open edX snapshot marker was not returned")


def core_request(path: str, *, body: dict[str, Any] | None = None, key: str | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"path": path, "method": "POST" if body is not None else "GET"}
    if body is not None:
        message.update(body=body, key=key)
    output = run(
        ["docker", "exec", "-i", CORE_CONTAINER, "python", "-c", CORE_BRIDGE],
        input_text=json.dumps(message, separators=(",", ":")),
    )
    data = json.loads(output)
    if not isinstance(data, dict):
        raise RuntimeError("Academy Core returned a non-object response")
    return data


def core_audit() -> list[dict[str, Any]]:
    return core_request("/v1/audit?limit=500").get("entries", [])


def find_identity(entries: list[dict[str, Any]], external_ref: str) -> tuple[str, str] | None:
    for entry in entries:
        if entry.get("operation") == "identity.create" and entry.get("details", {}).get("external_identity_ref") == external_ref:
            return entry["object_ref"], entry["audit_id"]
    return None


def find_entitlement(
    entries: list[dict[str, Any]], abc4rd_id: str, resource_ref: str
) -> tuple[str, str] | None:
    for entry in entries:
        details = entry.get("details", {})
        if (
            entry.get("operation") == "entitlement.record"
            and details.get("abc4rd_id") == abc4rd_id
            and details.get("resource_ref") == resource_ref
            and details.get("action") == "GRANTED"
        ):
            return entry["object_ref"], entry["audit_id"]
    return None


def ensure_core_projection(row: dict[str, Any], course: dict[str, Any]) -> dict[str, str]:
    external_ref = f"keycloak:{row['keycloak_subject']}"
    resource_ref = f"openedx:{course['course_ref']}"
    entries = core_audit()
    identity = find_identity(entries, external_ref)
    if identity is None:
        response = core_request(
            "/v1/identities",
            key=f"sync-identity-{row['keycloak_subject']}-v1",
            body={"external_identity_ref": external_ref, "actor_type": "SERVICE", "actor_ref": ACTOR_REF},
        )
        entries = core_audit()
        identity = find_identity(entries, external_ref)
        if identity is None or identity[0] != response["abc4rd_id"]:
            raise RuntimeError("identity audit record is missing")
    abc4rd_id, identity_audit_id = identity

    entitlement = find_entitlement(entries, abc4rd_id, resource_ref)
    if entitlement is None:
        response = core_request(
            "/v1/entitlements",
            key=f"sync-entitlement-{row['keycloak_subject']}-{hashlib.sha256(resource_ref.encode()).hexdigest()[:16]}-v1",
            body={
                "abc4rd_id": abc4rd_id,
                "resource_type": "course",
                "resource_ref": resource_ref,
                "action": "GRANTED",
                "authority_ref": f"openedx:active-enrollment:user-{row['lms_user_id']}",
                "evidence_ref": f"openedx:course-enrollment:user-{row['lms_user_id']}",
                "actor_type": "SERVICE",
                "actor_ref": ACTOR_REF,
            },
        )
        entries = core_audit()
        entitlement = find_entitlement(entries, abc4rd_id, resource_ref)
        if entitlement is None or entitlement[0] != response["entitlement_record_id"]:
            raise RuntimeError("entitlement audit record is missing")
    _, entitlement_audit_id = entitlement

    result_material = {
        "course_ref": resource_ref,
        "grade_percent": course["grade_percent"],
        "passed": course["passed"],
    }
    digest = hashlib.sha256(json.dumps(result_material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    core_request(
        "/v1/events",
        key=f"sync-result-{abc4rd_id}-{digest[:24]}",
        body={
            "event_type": "openedx.course.result.observed",
            "aggregate_type": "abc4rd_identity",
            "aggregate_ref": abc4rd_id,
            "source": "openedx:course-grade",
            "actor_type": "SERVICE",
            "actor_ref": ACTOR_REF,
            "payload": result_material,
        },
    )
    return {
        "abc4rd_id": abc4rd_id,
        "identity_audit_ref": f"academy-core:audit:{identity_audit_id}",
        "entitlement_audit_ref": f"academy-core:audit:{entitlement_audit_id}",
        "resource_ref": resource_ref,
    }


def crm_execute(function: str, kwargs: dict[str, Any]) -> str:
    return run(
        [
            "docker",
            "exec",
            ERP_CONTAINER,
            "bench",
            "--site",
            ERP_SITE,
            "execute",
            function,
            "--kwargs",
            json.dumps(kwargs, ensure_ascii=False, separators=(",", ":")),
        ],
        timeout=120,
    )


def parse_json_output(output: str) -> Any:
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        return None
    return json.loads(lines[-1])


def pilot_ref(username: str) -> str:
    suffix = username.removeprefix(USERNAME_PREFIX).replace("_", "-").upper()
    return f"PILOT-{suffix}"


def ensure_crm_participant(
    row: dict[str, Any], course: dict[str, Any], projection: dict[str, str], observed_at: str
) -> None:
    abc4rd_id = projection["abc4rd_id"]
    count_output = crm_execute(
        "frappe.client.get_count",
        {"doctype": "ABC4RD Participant", "filters": {"abc4rd_id": abc4rd_id}},
    )
    count = int(count_output or "0")
    if count == 0:
        same_pilot = parse_json_output(
            crm_execute(
                "frappe.client.get_list",
                {
                    "doctype": "ABC4RD Participant",
                    "filters": {"pilot_ref": pilot_ref(row["username"])},
                    "fields": ["abc4rd_id"],
                    "limit_page_length": 2,
                },
            )
        ) or []
        if same_pilot:
            raise RuntimeError("pilot reference already belongs to a different ABC4RD ID")
        crm_execute(
            "frappe.client.insert",
            {
                "doc": {
                    "doctype": "ABC4RD Participant",
                    "abc4rd_id": abc4rd_id,
                    "pilot_ref": pilot_ref(row["username"]),
                    "display_label": pilot_ref(row["username"]),
                    "lifecycle_state": "candidate",
                    "lifecycle_source_ref": f"academy-core:identity:{abc4rd_id}",
                    "lifecycle_synced_at": frappe_time(observed_at),
                    "lifecycle_audit_ref": projection["identity_audit_ref"],
                    "responsible_actor_type": "SERVICE",
                    "responsible_actor_ref": ACTOR_REF,
                    "financial_state": "NO_VERIFIED_FACT",
                    "course_ref": projection["resource_ref"],
                    "course_synced_at": frappe_time(observed_at),
                    "course_audit_ref": projection["entitlement_audit_ref"],
                    "graduation_state": "NO_VERIFIED_FACT",
                }
            },
        )

    document = parse_json_output(
        crm_execute("frappe.client.get", {"doctype": "ABC4RD Participant", "name": abc4rd_id})
    )
    if document.get("course_ref") not in (None, "", projection["resource_ref"]):
        raise RuntimeError("CRM participant is already bound to another course")
    updates: dict[str, Any] = {}
    if not document.get("course_ref"):
        updates.update(
            course_ref=projection["resource_ref"],
            course_synced_at=frappe_time(observed_at),
            course_audit_ref=projection["entitlement_audit_ref"],
        )
    if document.get("lifecycle_state") == "candidate":
        updates.update(
            lifecycle_state="learner",
            lifecycle_source_ref=f"openedx:course-enrollment:user-{row['lms_user_id']}",
            lifecycle_synced_at=frappe_time(observed_at),
            lifecycle_audit_ref=projection["entitlement_audit_ref"],
        )
    if updates:
        crm_execute(
            "frappe.client.set_value",
            {"doctype": "ABC4RD Participant", "name": abc4rd_id, "fieldname": updates},
        )


def previous_certificates() -> dict[str, str]:
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    result: dict[str, str] = {}
    for participant in state.get("participants", []):
        for course in participant.get("courses", []):
            certificate = course.get("certificate")
            if isinstance(certificate, dict) and certificate.get("id") and certificate.get("issued_at"):
                result[certificate["id"]] = certificate["issued_at"]
    return result


def certificate_for(
    abc4rd_id: str, course_ref: str, passed: bool, observed_at: str, previous: dict[str, str]
) -> dict[str, str] | None:
    if not passed:
        return None
    certificate_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"abc4rd:pilot-certificate:{abc4rd_id}:{course_ref}"))
    return {
        "id": certificate_id,
        "issued_at": previous.get(certificate_id, observed_at),
        "status": "PILOT_COMPLETION_CONFIRMED",
        "format": "PDF_QR",
    }


def write_state(participants: list[dict[str, Any]], generated_at: str) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "generated_at": generated_at, "participants": participants}
    fd, temporary = tempfile.mkstemp(prefix="state.", suffix=".json", dir=STATE_PATH.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, STATE_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def synchronize() -> dict[str, Any]:
    observed_at = utc_now()
    old_issued_at = previous_certificates()
    portal_participants: list[dict[str, Any]] = []
    rows = lms_snapshot()
    for row in rows:
        projected_courses = []
        participant_id = None
        for course in row["courses"]:
            projection = ensure_core_projection(row, course)
            participant_id = projection["abc4rd_id"]
            ensure_crm_participant(row, course, projection, observed_at)
            projected = dict(course)
            projected["core_resource_ref"] = projection["resource_ref"]
            projected["certificate"] = certificate_for(
                participant_id, course["course_ref"], bool(course["passed"]), observed_at, old_issued_at
            )
            projected_courses.append(projected)
        if participant_id is None:
            continue
        portal_participants.append(
            {
                "abc4rd_id": participant_id,
                "username": row["username"],
                "keycloak_subject": row["keycloak_subject"],
                "pilot_ref": pilot_ref(row["username"]),
                "display_label": pilot_ref(row["username"]),
                "lifecycle_state": "learner",
                "courses": projected_courses,
            }
        )
    write_state(portal_participants, observed_at)
    verification = core_request("/v1/audit/verify")
    if not verification.get("valid"):
        raise RuntimeError("Academy Core audit chain verification failed")
    return {
        "participants": len(portal_participants),
        "courses": sum(len(item["courses"]) for item in portal_participants),
        "certificates": sum(
            1 for item in portal_participants for course in item["courses"] if course.get("certificate")
        ),
        "audit_entries": verification.get("entries"),
    }


def main() -> int:
    with open("/tmp/abc4rd-pilot-sync.lock", "w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("ABC4RD_SYNC skipped: another run is active")
            return 0
        result = synchronize()
    print("ABC4RD_SYNC " + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ABC4RD_SYNC_ERROR {error}", file=sys.stderr)
        raise

