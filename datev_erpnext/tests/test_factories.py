import frappe
from frappe.tests import IntegrationTestCase

from datev_erpnext.tests.factories import make_company, make_invoice


class TestFactories(IntegrationTestCase):
	def test_make_company_is_idempotent(self):
		self.assertEqual(make_company(), make_company())

	def test_mixed_tax_invoice(self):
		company = make_company()
		invoice = make_invoice(company, [("MIETE", 1, 100), ("VERS", 2, 10)])
		invoice.submit()
		self.assertEqual(invoice.base_grand_total, 139)
		self.assertEqual(invoice.total_taxes_and_charges, 19)
		self.assertEqual([row.income_account.split(" - ")[0] for row in invoice.items], ["4400", "4100"])
		self.assertEqual(
			frappe.db.get_value("Company", company, "default_receivable_account"), invoice.debit_to
		)
