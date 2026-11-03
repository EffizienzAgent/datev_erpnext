import frappe
from frappe import _
from frappe.model.document import Document


class DATEVERPNextSettings(Document):
	def validate(self):
		for field, doctype in (("customer_number_field", "Customer"), ("supplier_number_field", "Supplier")):
			value = self.get(field)
			if value and not frappe.get_meta(doctype).has_field(value):
				frappe.throw(_("{0} is not a field of {1}.").format(value, _(doctype)))
