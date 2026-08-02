from frappe import _
from frappe.model.document import Document

from abc4rd_crm.validators import validate_opaque_ref, validate_sha256


class ABC4RDAuditReference(Document):
    def validate(self) -> None:
        validate_opaque_ref(self.event_type, _("Event type"), required=True)
        validate_opaque_ref(self.external_ref, _("External reference"), required=True)
        validate_sha256(self.evidence_sha256, _("Evidence SHA-256"))
