from decimal import Decimal

import frappe
from frappe.tests import IntegrationTestCase

from datev_erpnext.lines import Line, debit_credit, sales_invoice_lines
from datev_erpnext.tests.factories import make_company, make_invoice

MIXED = [("MIETE", 1, 100), ("VERS", 1, 29.75)]


class TestLines(IntegrationTestCase):
	def setUp(self):
		self.company = make_company()
		self.abbr = frappe.db.get_value("Company", self.company, "abbr")
		frappe.db.savepoint("booking_lines")
		self.addCleanup(frappe.db.rollback, save_point="booking_lines")

	def _total(self, invoice):
		return Decimal(str(abs(invoice.base_rounded_total or invoice.base_grand_total))).quantize(
			Decimal("0.01")
		)

	def test_single_line(self):
		invoice = make_invoice(self.company, [("MIETE", 1, 100)])
		self.assertEqual(
			sales_invoice_lines(invoice),
			[Line(f"4400:DE 19 % - {self.abbr}:19", 4400, Decimal("119.00"), 19, f"DE 19 % - {self.abbr}")],
		)
		self.assertEqual(debit_credit(invoice), "S")

	def test_mixed_tax_lines(self):
		invoice = make_invoice(self.company, MIXED)
		lines = sales_invoice_lines(invoice)
		self.assertEqual(
			lines,
			[
				Line(
					f"4100:DE steuerfrei - {self.abbr}:0",
					4100,
					Decimal("29.75"),
					0,
					f"DE steuerfrei - {self.abbr}",
				),
				Line(f"4400:DE 19 % - {self.abbr}:19", 4400, Decimal("119.00"), 19, f"DE 19 % - {self.abbr}"),
			],
		)
		self.assertEqual(sum(line.amount for line in lines), self._total(invoice))

	def _round_to_five_cents(self):
		fraction = frappe.db.get_value("Currency", "EUR", "smallest_currency_fraction_value")
		self.addCleanup(frappe.db.set_value, "Currency", "EUR", "smallest_currency_fraction_value", fraction)
		frappe.db.set_value("Currency", "EUR", "smallest_currency_fraction_value", 0.05)

	def test_discount_and_rounding(self):
		self._round_to_five_cents()
		invoice = make_invoice(
			self.company,
			MIXED,
			apply_discount_on="Net Total",
			additional_discount_percentage=10,
			disable_rounded_total=0,
		)
		self.assertEqual(invoice.base_grand_total, 133.87)
		self.assertEqual(invoice.base_rounded_total, 133.85)
		lines = sales_invoice_lines(invoice)
		self.assertEqual(sum(line.amount for line in lines), Decimal("133.85"))
		by_account = {line.contra_account: line.amount for line in lines}
		self.assertEqual(by_account[4100], Decimal("26.77"))
		self.assertEqual(by_account[4400], Decimal("107.08"))

	def test_return_invoice(self):
		invoice = make_invoice(self.company, [(code, -qty, rate) for code, qty, rate in MIXED], is_return=1)
		self.assertEqual(sales_invoice_lines(invoice), sales_invoice_lines(make_invoice(self.company, MIXED)))
		self.assertEqual(debit_credit(invoice), "H")

	def test_missing_account_number(self):
		invoice = make_invoice(self.company, [("MIETE", 1, 100)])
		account = invoice.items[0].income_account
		frappe.db.set_value("Account", account, "account_number", "")
		with self.assertRaisesRegex(frappe.ValidationError, account):
			sales_invoice_lines(invoice)
