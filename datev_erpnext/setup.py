import frappe

SETTINGS = "DATEV ERPNext Settings"


def apply_defaults() -> None:
	if frappe.db.get_singles_dict(SETTINGS):
		return
	frappe.get_single(SETTINGS).save(ignore_permissions=True)
