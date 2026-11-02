import frappe
from frappe.tests import IntegrationTestCase

from datev_erpnext.clients import client_for
from datev_erpnext.tests.factories import make_client, make_company


class TestClients(IntegrationTestCase):
	def setUp(self):
		self.company = make_company()
		frappe.db.savepoint("clients")
		self.addCleanup(frappe.db.rollback, save_point="clients")

	def test_client_for_company(self):
		client = make_client(self.company)
		self.assertEqual(client_for(self.company).name, client.name)

	def test_no_client(self):
		self.assertIsNone(client_for(self.company))

	def test_disabled_client_is_ignored(self):
		make_client(self.company, enabled=0)
		self.assertIsNone(client_for(self.company))

	def test_two_clients_raise(self):
		make_client(self.company)
		make_client(self.company)
		with self.assertRaises(frappe.ValidationError):
			client_for(self.company)

	def test_settings_rejects_unknown_field(self):
		settings = frappe.get_single("DATEV ERPNext Settings")
		settings.customer_number_field = "no_such_field"
		with self.assertRaises(frappe.ValidationError):
			settings.validate()

	def test_settings_accepts_customer_field(self):
		settings = frappe.get_single("DATEV ERPNext Settings")
		settings.customer_number_field = "customer_name"
		settings.validate()
