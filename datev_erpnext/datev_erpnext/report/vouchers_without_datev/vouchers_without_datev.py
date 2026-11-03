import frappe
from frappe import _

from datev_erpnext.sync import SETTINGS, latest_comments, missing_invoices, synced_doctypes


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return _columns(), _rows(filters)


def _columns():
	return [
		{"fieldname": "voucher_type", "label": _("Voucher Type"), "fieldtype": "Data", "width": 130},
		{
			"fieldname": "name",
			"label": _("Voucher"),
			"fieldtype": "Dynamic Link",
			"options": "voucher_type",
			"width": 170,
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
			"fieldname": "party",
			"label": _("Party"),
			"fieldtype": "Dynamic Link",
			"options": "party_type",
			"width": 140,
		},
		{"fieldname": "party_name", "label": _("Party Name"), "fieldtype": "Data", "width": 180},
		{"fieldname": "amount", "label": _("Amount"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "reason", "label": _("Reason"), "fieldtype": "Small Text", "width": 400},
	]


def _rows(filters):
	settings = frappe.get_single(SETTINGS)
	if not settings.sync_from_date:
		return []
	client_filters = {"company": filters.company} if filters.company else {}
	companies = frappe.get_all("DATEV Client", filters=client_filters, pluck="company", distinct=True)
	rows = []
	for doctype in synced_doctypes(settings):
		if filters.voucher_type and doctype != filters.voucher_type:
			continue
		vouchers = missing_invoices(companies, settings.sync_from_date, doctype=doctype)
		comments = latest_comments([row.name for row in vouchers], doctype)
		for row in vouchers:
			comment = comments.get(row.name)
			row.reason = comment.content if comment else ""
		rows.extend(vouchers)
	return rows
