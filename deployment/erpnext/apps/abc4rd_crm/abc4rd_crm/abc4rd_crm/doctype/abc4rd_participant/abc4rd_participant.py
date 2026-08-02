from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

from abc4rd_crm.validators import (
    require_changed_evidence,
    require_projection_evidence,
    validate_abc4rd_id,
    validate_opaque_ref,
)


LIFECYCLE_TRANSITIONS = {
    "candidate": {"learner"},
    "learner": {"intern"},
    "intern": {"graduate"},
    "graduate": {"resident"},
    "resident": set(),
}

FINANCIAL_TRANSITIONS = {
    "NO_VERIFIED_FACT": {"SANDBOX_PAYMENT_RECONCILED", "SCHOLARSHIP_APPROVED"},
    "SANDBOX_PAYMENT_RECONCILED": {"SANDBOX_REFUND_RECONCILED"},
    "SANDBOX_REFUND_RECONCILED": set(),
    "SCHOLARSHIP_APPROVED": set(),
}

GRADUATION_TRANSITIONS = {
    "NO_VERIFIED_FACT": {"RELEASE_ELIGIBLE", "RELEASE_DENIED"},
    "RELEASE_ELIGIBLE": {"RELEASE_APPROVED", "RELEASE_DENIED"},
    "RELEASE_APPROVED": {"RELEASED"},
    "RELEASE_DENIED": {"RELEASE_ELIGIBLE", "RELEASE_APPROVED"},
    "RELEASED": set(),
}


class ABC4RDParticipant(Document):
    def validate(self) -> None:
        validate_abc4rd_id(self.abc4rd_id)
        validate_opaque_ref(self.responsible_actor_ref, _("Responsible actor reference"), required=True)
        if self.pilot_ref:
            validate_opaque_ref(self.pilot_ref, _("Pilot reference"))

        previous = self.get_doc_before_save()
        if previous and previous.abc4rd_id != self.abc4rd_id:
            frappe.throw(_("ABC4RD ID is immutable"))

        self._validate_lifecycle(previous)
        self._validate_financial_fact(previous)
        self._validate_course_projection(previous)
        self._validate_graduation_projection(previous)

    def _validate_lifecycle(self, previous) -> None:
        require_projection_evidence(self, "lifecycle", "lifecycle_synced_at")
        if not previous and self.lifecycle_state != "candidate":
            frappe.throw(_("A new participant must enter the lifecycle as candidate"))
        if previous and previous.lifecycle_state != self.lifecycle_state:
            allowed = LIFECYCLE_TRANSITIONS.get(previous.lifecycle_state, set())
            if self.lifecycle_state not in allowed:
                frappe.throw(
                    _("Invalid lifecycle transition: {0} to {1}").format(
                        previous.lifecycle_state, self.lifecycle_state
                    )
                )
            require_changed_evidence(self, previous, "lifecycle", "lifecycle_synced_at")

    def _validate_financial_fact(self, previous) -> None:
        if self.financial_state != "NO_VERIFIED_FACT":
            require_projection_evidence(self, "financial", "financial_verified_at")
        elif self.financial_source_ref or self.financial_verified_at or self.financial_audit_ref:
            frappe.throw(_("NO_VERIFIED_FACT cannot carry financial evidence"))

        if previous and previous.financial_state != self.financial_state:
            allowed = FINANCIAL_TRANSITIONS.get(previous.financial_state, set())
            if self.financial_state not in allowed:
                frappe.throw(
                    _("Invalid financial fact transition: {0} to {1}").format(
                        previous.financial_state, self.financial_state
                    )
                )
            require_changed_evidence(self, previous, "financial", "financial_verified_at")

    def _validate_course_projection(self, previous) -> None:
        if self.course_ref:
            validate_opaque_ref(self.course_ref, _("Course reference"), required=True)
            if not self.course_synced_at:
                frappe.throw(_("Course synchronized time is required"))
            validate_opaque_ref(self.course_audit_ref, _("Course audit reference"), required=True)
        elif self.course_synced_at or self.course_audit_ref:
            frappe.throw(_("Course evidence requires a course reference"))

        if previous and previous.course_ref != self.course_ref:
            if previous.course_ref:
                frappe.throw(_("Course reference is immutable; add a separate enrollment model before multi-course use"))
            if not self.course_synced_at or self.course_audit_ref == previous.course_audit_ref:
                frappe.throw(_("A course reference requires new synchronization evidence"))

    def _validate_graduation_projection(self, previous) -> None:
        if self.graduation_state != "NO_VERIFIED_FACT":
            require_projection_evidence(self, "graduation", "graduation_verified_at")
        elif self.graduation_source_ref or self.graduation_verified_at or self.graduation_audit_ref:
            frappe.throw(_("NO_VERIFIED_FACT cannot carry graduation evidence"))

        if previous and previous.graduation_state != self.graduation_state:
            allowed = GRADUATION_TRANSITIONS.get(previous.graduation_state, set())
            if self.graduation_state not in allowed:
                frappe.throw(
                    _("Invalid graduation transition: {0} to {1}").format(
                        previous.graduation_state, self.graduation_state
                    )
                )
            require_changed_evidence(self, previous, "graduation", "graduation_verified_at")
