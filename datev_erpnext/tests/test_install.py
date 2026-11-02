import frappe
from frappe.tests import IntegrationTestCase


class TestInstall(IntegrationTestCase):
	def test_app_installed(self):
		self.assertIn("datev_erpnext", frappe.get_installed_apps())

	def test_core_installed(self):
		apps = frappe.get_installed_apps()
		self.assertIn("datev", apps)
		self.assertIn("erpnext", apps)
