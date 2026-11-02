import frappe
from frappe.tests import IntegrationTestCase

from datev_erpnext.setup import apply_defaults


class TestInstall(IntegrationTestCase):
	def test_app_installed(self):
		self.assertIn("datev_erpnext", frappe.get_installed_apps())

	def test_core_installed(self):
		apps = frappe.get_installed_apps()
		self.assertIn("datev", apps)
		self.assertIn("erpnext", apps)


class TestDefaults(IntegrationTestCase):
	def test_defaults_stored_when_single_is_empty(self):
		frappe.db.delete("Singles", {"doctype": "DATEV ERPNext Settings"})
		frappe.clear_document_cache("DATEV ERPNext Settings", "DATEV ERPNext Settings")
		apply_defaults()
		values = frappe.db.get_singles_dict("DATEV ERPNext Settings")
		self.assertEqual(int(values.enabled), 1)
		self.assertEqual(values.document_image_source, "Print format")
		self.assertEqual(values.posting_text_template, "{customer_name}")

	def test_existing_values_are_kept(self):
		settings = frappe.get_single("DATEV ERPNext Settings")
		settings.update({"enabled": 0, "posting_text_template": "{name}"})
		settings.save()
		apply_defaults()
		values = frappe.db.get_singles_dict("DATEV ERPNext Settings")
		self.assertEqual(int(values.enabled), 0)
		self.assertEqual(values.posting_text_template, "{name}")
