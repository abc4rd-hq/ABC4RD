from frappe import _
from frappe.model.document import Document

from abc4rd_crm.validators import validate_opaque_ref


class ABC4RDInquiry(Document):
    def validate(self) -> None:
        validate_opaque_ref(self.responsible_actor_ref, _("Responsible actor reference"), required=True)
        if self.source_ref:
            validate_opaque_ref(self.source_ref, _("Source reference"))
