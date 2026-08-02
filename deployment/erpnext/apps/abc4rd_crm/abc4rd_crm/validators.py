from __future__ import annotations

import re
import uuid

import frappe
from frappe import _


OPAQUE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,254}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def validate_abc4rd_id(value: str) -> None:
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError):
        frappe.throw(_("ABC4RD ID must be a canonical opaque UUID"))
    if str(parsed) != value:
        frappe.throw(_("ABC4RD ID must use canonical lowercase UUID form"))


def validate_opaque_ref(value: str | None, label: str, *, required: bool = False) -> None:
    if not value:
        if required:
            frappe.throw(_("{0} is required").format(label))
        return
    if not OPAQUE_REF.fullmatch(value):
        frappe.throw(
            _("{0} must be an opaque reference without whitespace, query parameters or PII markers").format(label)
        )


def validate_sha256(value: str | None, label: str) -> None:
    if value and not SHA256.fullmatch(value):
        frappe.throw(_("{0} must be a lowercase SHA-256 value").format(label))


def require_projection_evidence(document, prefix: str, time_field: str) -> None:
    validate_opaque_ref(document.get(f"{prefix}_source_ref"), _("Source reference"), required=True)
    if not document.get(time_field):
        frappe.throw(_("Verified or synchronized time is required"))
    validate_opaque_ref(document.get(f"{prefix}_audit_ref"), _("Audit reference"), required=True)


def require_changed_evidence(document, previous, prefix: str, time_field: str) -> None:
    if not previous:
        return
    if document.get(time_field) == previous.get(time_field):
        frappe.throw(_("A state change requires a new evidence timestamp"))
    if document.get(f"{prefix}_audit_ref") == previous.get(f"{prefix}_audit_ref"):
        frappe.throw(_("A state change requires a new audit reference"))
