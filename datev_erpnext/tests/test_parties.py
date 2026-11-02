import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field
from frappe.tests import IntegrationTestCase

from datev_erpnext.parties import party_account_for, register_customers
from datev_erpnext.tests.factories import CUSTOMER_NAME, TAX_ID, make_client, make_company, make_invoice

NUMBER_FIELD = "datev_test_number"


class TestParties(IntegrationTestCase):
	def setUp(self):
		self.company = make_company()
		frappe.db.savepoint("parties")
		self.addCleanup(frappe.db.rollback, save_point="parties")
		self.client = make_client(self.company)
		self.customer = frappe.db.get_value("Customer", {"customer_name": CUSTOMER_NAME})
		if not frappe.get_meta("Customer").has_field(NUMBER_FIELD):
			create_custom_field(
				"Customer", {"fieldname": NUMBER_FIELD, "fieldtype": "Data", "label": "Test Number"}
			)
		settings = frappe.get_single("DATEV ERPNext Settings")
		self.addCleanup(self._restore_settings, settings.customer_number_field)
		settings.customer_number_field = NUMBER_FIELD
		settings.save()

	def _restore_settings(self, value):
		settings = frappe.get_single("DATEV ERPNext Settings")
		settings.customer_number_field = value
		settings.save()

	def _set_number(self, value):
		frappe.db.set_value("Customer", self.customer, NUMBER_FIELD, value)

	def test_party_account_uses_number_field(self):
		self._set_number("10001")
		name = party_account_for(self.client, self.customer)
		account = frappe.get_doc("DATEV Party Account", name)
		self.assertEqual(account.preferred_number, 10001)
		self.assertEqual(account.party_type, "Customer")
		self.assertEqual(account.party, self.customer)
		self.assertEqual(account.party_name, CUSTOMER_NAME)
		self.assertEqual(account.vat_id, TAX_ID)

	def test_missing_number_field_value(self):
		for value in ("", None, "abc", "0", "-5"):
			self._set_number(value)
			name = party_account_for(self.client, self.customer)
			self.assertFalse(frappe.db.get_value("DATEV Party Account", name, "preferred_number"))

	def test_idempotent(self):
		self._set_number("10001")
		first = party_account_for(self.client, self.customer)
		self.assertEqual(party_account_for(self.client, self.customer), first)
		self.assertEqual(frappe.db.count("DATEV Party Account", {"client": self.client.name}), 1)

	def test_register_customers(self):
		self.assertEqual(register_customers(self.company), 0)
		self._set_number("10001")
		self.assertEqual(register_customers(self.company), 1)
		self._set_number("")
		frappe.db.delete("DATEV Party Account", {"client": self.client.name})
		make_invoice(self.company, [("MIETE", 1, 100)]).submit()
		self.assertEqual(register_customers(self.company), 1)
