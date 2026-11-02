import frappe
from datev.intake import ensure_party_account
from frappe import _

from datev_erpnext.clients import client_for


def _preferred_number(customer: str) -> int | None:
	field = frappe.db.get_single_value("DATEV ERPNext Settings", "customer_number_field")
	value = frappe.db.get_value("Customer", customer, field) if field else None
	try:
		number = int(str(value).strip())
	except TypeError, ValueError:
		return None
	return number if number > 0 else None


def party_account_for(client, customer: str) -> str:
	frappe.db.get_value("Customer", customer, "name", for_update=True)
	name, tax_id = frappe.db.get_value("Customer", customer, ["customer_name", "tax_id"])
	return ensure_party_account(
		client.name,
		"Debtor",
		party_type="Customer",
		party=customer,
		party_name=name,
		vat_id=tax_id,
		iban=None,
		preferred_number=_preferred_number(customer),
	)


@frappe.whitelist(methods=["POST"])
def register_customers(company: str) -> int:
	frappe.only_for("DATEV Manager")
	client = client_for(company)
	if not client:
		frappe.throw(_("No enabled DATEV Client is assigned to company {0}.").format(company))

	customers = set(
		frappe.get_all(
			"Sales Invoice", filters={"company": company, "docstatus": 1}, pluck="customer", distinct=True
		)
	)
	field = frappe.db.get_single_value("DATEV ERPNext Settings", "customer_number_field")
	if field:
		customers.update(frappe.get_all("Customer", filters={field: ("is", "set")}, pluck="name"))

	enabled = frappe.get_all(
		"Customer", filters={"name": ("in", list(customers)), "disabled": 0}, pluck="name"
	)
	for customer in enabled:
		party_account_for(client, customer)
	return len(enabled)
