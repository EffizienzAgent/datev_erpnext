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
		self.vat = f"DE 19 % - {self.abbr}"
		self.free = f"DE steuerfrei - {self.abbr}"
		frappe.db.savepoint("booking_lines")
		self.addCleanup(frappe.db.rollback, save_point="booking_lines")

	def _rent(self, amount, side):
		return Line(f"4400:{self.vat}:19", 4400, Decimal(amount), 19, self.vat, side)

	def _insurance(self, amount, side):
		return Line(f"4100:{self.free}:0", 4100, Decimal(amount), 0, self.free, side)

	def _round_to_five_cents(self):
		fraction = frappe.db.get_value("Currency", "EUR", "smallest_currency_fraction_value")
		self.addCleanup(frappe.db.set_value, "Currency", "EUR", "smallest_currency_fraction_value", fraction)
		frappe.db.set_value("Currency", "EUR", "smallest_currency_fraction_value", 0.05)

	def test_single_line(self):
		invoice = make_invoice(self.company, [("MIETE", 1, 100)])
		self.assertEqual(sales_invoice_lines(invoice), [self._rent("119.00", "S")])
		self.assertEqual(debit_credit(invoice), "S")

	def test_mixed_tax_lines(self):
		invoice = make_invoice(self.company, MIXED)
		lines = sales_invoice_lines(invoice)
		self.assertEqual(lines, [self._insurance("29.75", "S"), self._rent("119.00", "S")])
		self.assertEqual(sum(line.amount for line in lines), Decimal(str(invoice.base_grand_total)))

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
		self.assertEqual(
			sales_invoice_lines(invoice), [self._insurance("26.77", "S"), self._rent("107.08", "S")]
		)

	def test_zero_amount_line_is_dropped(self):
		invoice = make_invoice(self.company, [*MIXED, ("ZUB", 1, 0)])
		self.assertEqual(
			sales_invoice_lines(invoice), [self._insurance("29.75", "S"), self._rent("119.00", "S")]
		)

	def test_zero_total_has_no_lines(self):
		invoice = make_invoice(self.company, [("ZUB", 1, 0)])
		self.assertEqual(sales_invoice_lines(invoice), [])

	def test_negative_row(self):
		invoice = make_invoice(self.company, [("MIETE", 1, 100), ("VERS", -1, 10)])
		self.assertEqual(
			sales_invoice_lines(invoice), [self._insurance("10.00", "H"), self._rent("119.00", "S")]
		)

	def test_return_invoice(self):
		invoice = make_invoice(self.company, [(code, -qty, rate) for code, qty, rate in MIXED], is_return=1)
		self.assertEqual(
			sales_invoice_lines(invoice), [self._insurance("29.75", "H"), self._rent("119.00", "H")]
		)
		self.assertEqual(debit_credit(invoice), "H")

	def test_return_with_positive_row(self):
		setting = ("Selling Settings", "allow_negative_rates_for_items")
		self.addCleanup(frappe.db.set_single_value, *setting, frappe.db.get_single_value(*setting))
		frappe.db.set_single_value(*setting, 1)
		invoice = make_invoice(self.company, [("MIETE", -1, 100), ("VERS", -1, -10)], is_return=1)
		self.assertEqual(
			sales_invoice_lines(invoice), [self._insurance("10.00", "S"), self._rent("119.00", "H")]
		)

	def test_non_tax_charge_is_rejected(self):
		account = frappe.db.get_value(
			"Account", {"company": self.company, "is_group": 0, "account_type": "Chargeable"}
		)
		invoice = make_invoice(self.company, [("MIETE", 1, 100)])
		invoice.append(
			"taxes",
			{"charge_type": "Actual", "account_head": account, "description": "Freight", "tax_amount": 5},
		)
		invoice.save()
		with self.assertRaisesRegex(frappe.ValidationError, account):
			sales_invoice_lines(invoice)

	def test_oversized_difference_is_rejected(self):
		invoice = make_invoice(self.company, [("MIETE", 1, 100)])
		invoice.base_grand_total += 5
		invoice.base_rounded_total += 5
		with self.assertRaisesRegex(frappe.ValidationError, invoice.name):
			sales_invoice_lines(invoice)

	def test_missing_account_number(self):
		invoice = make_invoice(self.company, [("MIETE", 1, 100)])
		account = invoice.items[0].income_account
		self.addCleanup(frappe.clear_document_cache, "Account", account)
		frappe.db.set_value("Account", account, "account_number", "")
		frappe.clear_document_cache("Account", account)
		with self.assertRaisesRegex(frappe.ValidationError, account):
			sales_invoice_lines(invoice)
