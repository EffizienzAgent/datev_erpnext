import frappe
from frappe import _
from frappe.model.document import Document


class DATEVERPNextSettings(Document):
	def validate(self):
		if self.customer_number_field and not frappe.get_meta("Customer").has_field(
			self.customer_number_field
		):
			frappe.throw(_("{0} is not a field of Customer.").format(self.customer_number_field))
