import frappe


def execute():
	if frappe.db.exists("Report", "Sales Invoices without DATEV"):
		frappe.delete_doc("Report", "Sales Invoices without DATEV", force=True, ignore_permissions=True)
