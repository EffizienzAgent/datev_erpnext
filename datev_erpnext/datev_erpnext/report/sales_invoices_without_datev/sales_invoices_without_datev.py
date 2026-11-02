import frappe
from frappe import _

from datev_erpnext.sync import SETTINGS, latest_comments, missing_invoices


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return _columns(), _rows(filters)


def _columns():
	return [
		{
			"fieldname": "name",
			"label": _("Sales Invoice"),
			"fieldtype": "Link",
			"options": "Sales Invoice",
			"width": 160,
		},
		{
			"fieldname": "company",
			"label": _("Company"),
			"fieldtype": "Link",
			"options": "Company",
			"width": 160,
		},
		{"fieldname": "posting_date", "label": _("Posting Date"), "fieldtype": "Date", "width": 110},
		{
			"fieldname": "customer",
			"label": _("Customer"),
			"fieldtype": "Link",
			"options": "Customer",
			"width": 140,
		},
		{"fieldname": "customer_name", "label": _("Customer Name"), "fieldtype": "Data", "width": 180},
		{"fieldname": "base_grand_total", "label": _("Grand Total"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "reason", "label": _("Reason"), "fieldtype": "Small Text", "width": 400},
	]


def _rows(filters):
	sync_from_date = frappe.db.get_single_value(SETTINGS, "sync_from_date")
	if not sync_from_date:
		return []
	client_filters = {"company": filters.company} if filters.company else {}
	companies = frappe.get_all("DATEV Client", filters=client_filters, pluck="company", distinct=True)
	rows = missing_invoices(companies, sync_from_date)
	comments = latest_comments([row.name for row in rows])
	for row in rows:
		comment = comments.get(row.name)
		row.reason = comment.content if comment else ""
	return rows
