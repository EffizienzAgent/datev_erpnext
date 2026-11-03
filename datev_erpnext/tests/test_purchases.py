from decimal import Decimal

import frappe
from frappe.tests import IntegrationTestCase

from datev_erpnext.lines import Line, check_tax_keys, sales_invoice_lines
from datev_erpnext.purchases import document_field, purchase_invoice_lines
from datev_erpnext.tests.factories import (
	INPUT_TAX,
	INPUT_TAX_FREE,
	REVERSE_CHARGE,
	make_client,
	make_company,
	make_invoice,
	make_purchase_invoice,
)


class TestPurchases(IntegrationTestCase):
	def setUp(self):
		self.company = make_company()
		self.abbr = frappe.db.get_value("Company", self.company, "abbr")
		frappe.db.savepoint("purchases")
		self.addCleanup(frappe.db.rollback, save_point="purchases")

	def _code(self, title):
		return f"{title} - {self.abbr}"

	def _reverse_charge(self, items=(("FREMD", 1, 1000),), **overrides):
		return make_purchase_invoice(
			self.company,
			list(items),
			taxes_and_charges=self._code(REVERSE_CHARGE),
			**overrides,
		)

	def test_purchase_single_line(self):
		invoice = make_purchase_invoice(self.company, [("BUERO", 1, 100)])
		code = self._code(INPUT_TAX)
		self.assertEqual(
			purchase_invoice_lines(invoice), [Line(f"6815:{code}:19", 6815, Decimal("119.00"), 19, code, "H")]
		)

	def test_purchase_mixed_tax(self):
		invoice = make_purchase_invoice(self.company, [("BUERO", 1, 100), ("PORTO", 2, 10)])
		vat, free = self._code(INPUT_TAX), self._code(INPUT_TAX_FREE)
		self.assertEqual(
			purchase_invoice_lines(invoice),
			[
				Line(f"6800:{free}:0", 6800, Decimal("20.00"), 0, free, "H"),
				Line(f"6815:{vat}:19", 6815, Decimal("119.00"), 19, vat, "H"),
			],
		)

	def test_reverse_charge_net_with_bu_key(self):
		invoice = self._reverse_charge()
		self.assertEqual(invoice.base_grand_total, 1000)
		code = self._code(REVERSE_CHARGE)
		lines = purchase_invoice_lines(invoice)
		self.assertEqual(lines, [Line(f"6815:{code}:0", 6815, Decimal("1000.00"), 0, code, "H", True)])
		check_tax_keys(make_client(self.company), lines)

	def test_reverse_charge_without_bu_key_rejected(self):
		client = make_client(self.company)
		for row in client.tax_keys:
			if row.tax_code == self._code(REVERSE_CHARGE):
				row.bu_key = ""
		client.save()
		lines = purchase_invoice_lines(self._reverse_charge())
		with self.assertRaises(frappe.ValidationError) as raised:
			check_tax_keys(client, lines)
		self.assertIn("needs a BU key", str(raised.exception))

	def test_single_deduct_row_rejected(self):
		invoice = make_purchase_invoice(self.company, [("BUERO", 1, 100)])
		invoice.taxes[0].add_deduct_tax = "Deduct"
		with self.assertRaises(frappe.ValidationError) as raised:
			purchase_invoice_lines(invoice)
		self.assertIn("reverse charge pairs", str(raised.exception))

	def test_valuation_category_rejected(self):
		invoice = make_purchase_invoice(self.company, [("BUERO", 1, 100)])
		invoice.taxes[0].category = "Valuation and Total"
		with self.assertRaises(frappe.ValidationError):
			purchase_invoice_lines(invoice)

	def test_debit_note_sides(self):
		original = make_purchase_invoice(self.company, [("BUERO", 1, 100)])
		original.submit()
		debit_note = make_purchase_invoice(
			self.company,
			[("BUERO", -1, 100)],
			is_return=1,
			return_against=original.name,
			bill_no=None,
		)
		code = self._code(INPUT_TAX)
		self.assertEqual(
			purchase_invoice_lines(debit_note),
			[Line(f"6815:{code}:19", 6815, Decimal("119.00"), 19, code, "S")],
		)
		self.assertEqual(document_field(debit_note), original.bill_no)

	def test_srbnb_rejected(self):
		invoice = make_purchase_invoice(self.company, [("BUERO", 1, 100)])
		invoice.items[0].expense_account = frappe.get_cached_value(
			"Company", self.company, "stock_received_but_not_billed"
		)
		with self.assertRaises(frappe.ValidationError) as raised:
			purchase_invoice_lines(invoice)
		self.assertIn("goods receipts are not supported yet", str(raised.exception))

	def test_is_paid_rejected(self):
		invoice = make_purchase_invoice(self.company, [("BUERO", 1, 100)])
		invoice.is_paid = 1
		with self.assertRaises(frappe.ValidationError):
			purchase_invoice_lines(invoice)

	def test_deferred_expense_rejected(self):
		invoice = make_purchase_invoice(self.company, [("BUERO", 1, 100)])
		invoice.items[0].enable_deferred_expense = 1
		with self.assertRaises(frappe.ValidationError):
			purchase_invoice_lines(invoice)

	def test_missing_bill_no_rejected(self):
		invoice = make_purchase_invoice(self.company, [("BUERO", 1, 100)], bill_no=None)
		with self.assertRaises(frappe.ValidationError):
			document_field(invoice)
		invoice.bill_no = "LR-7"
		self.assertEqual(document_field(invoice), "LR-7")

	def test_sales_lines_unchanged(self):
		invoice = make_invoice(self.company, [("MIETE", 1, 100), ("VERS", 1, 29.75)])
		vat, free = f"DE 19 % - {self.abbr}", f"DE steuerfrei - {self.abbr}"
		self.assertEqual(
			sales_invoice_lines(invoice),
			[
				Line(f"4100:{free}:0", 4100, Decimal("29.75"), 0, free, "S"),
				Line(f"4400:{vat}:19", 4400, Decimal("119.00"), 19, vat, "S"),
			],
		)
