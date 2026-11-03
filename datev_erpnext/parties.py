import frappe
from datev.intake import ensure_party_account
from frappe import _

from datev_erpnext.clients import client_for

PARTIES = {
	"Customer": ("Debtor", "customer_name", "customer_number_field"),
	"Supplier": ("Creditor", "supplier_name", "supplier_number_field"),
}


def _preferred_number(party_type: str, party: str) -> int | None:
	setting = PARTIES[party_type][2]
	field = frappe.db.get_single_value("DATEV ERPNext Settings", setting)
	value = frappe.db.get_value(party_type, party, field) if field else None
	try:
		number = int(str(value).strip())
	except TypeError, ValueError:
		return None
	return number if number > 0 else None


def party_account_for(client, party: str, party_type: str = "Customer") -> str:
	kind, name_field, _setting = PARTIES[party_type]
	frappe.db.get_value(party_type, party, "name", for_update=True)
	name, tax_id = frappe.db.get_value(party_type, party, [name_field, "tax_id"])
	return ensure_party_account(
		client.name,
		kind,
		party_type=party_type,
		party=party,
		party_name=name,
		vat_id=tax_id,
		iban=None,
		preferred_number=_preferred_number(party_type, party),
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
