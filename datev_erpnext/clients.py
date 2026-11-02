import frappe
from frappe import _


def client_for(company: str):
	names = frappe.get_all("DATEV Client", filters={"company": company, "enabled": 1}, pluck="name")
	if len(names) > 1:
		frappe.throw(
			_("More than one enabled DATEV Client is assigned to company {0}.").format(company),
			frappe.ValidationError,
		)
	return frappe.get_doc("DATEV Client", names[0]) if names else None
