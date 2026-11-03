from decimal import Decimal

import frappe
from eu_einvoice.european_e_invoice.doctype.e_invoice_import.e_invoice_import import (
	create_purchase_invoice,
)
from frappe.tests import IntegrationTestCase

from datev_erpnext.lines import Line, sales_invoice_lines
from datev_erpnext.purchases import purchase_invoice_lines
from datev_erpnext.tests.factories import (
	INPUT_TAX,
	account,
	make_company,
	make_invoice,
	supplier,
)


class TestEInvoice(IntegrationTestCase):
	def setUp(self):
		self.company = make_company()
		self.abbr = frappe.db.get_value("Company", self.company, "abbr")
		self.input_tax = account(self.company, "1406")
		frappe.db.savepoint("einvoice")
		self.addCleanup(frappe.db.rollback, save_point="einvoice")

	def _import(self, items, taxes, **overrides):
		einvoice = frappe.get_doc(
			{
				"doctype": "E Invoice Import",
				"supplier": supplier(),
				"company": self.company,
				"id": f"ER-{frappe.generate_hash(length=6)}",
				"issue_date": frappe.utils.nowdate(),
				"currency": "EUR",
				"items": [
					{"item": code, "billed_quantity": qty, "uom": "Nos", "net_rate": rate}
					for code, qty, rate in items
				],
				"taxes": [
					{
						"tax_account": other[0] if other else self.input_tax,
						"rate_applicable_percent": rate,
						"basis_amount": basis,
						"calculated_amount": amount,
					}
					for rate, basis, amount, *other in taxes
				],
			}
		).insert()
		einvoice.submit()
		invoice = create_purchase_invoice(einvoice.name)
		for tax in invoice.taxes:
			tax.description = "Vorsteuer"
		invoice.update(overrides)
		return invoice.insert()

	def _input_tax_line(self, amount):
		code = f"{INPUT_TAX} - {self.abbr}"
		return Line(f"6815:{code}:19", 6815, Decimal(amount), 19, code, "H")

	def test_import_sets_actual_rows_and_item_templates(self):
		invoice = self._import(
			[("BUERO", 3, 33.33), ("FREMD", 1, 10.01)],
			[(19, 110, 20.9)],
			taxes_and_charges=f"{INPUT_TAX} - {self.abbr}",
		)
		self.assertEqual([tax.charge_type for tax in invoice.taxes], ["Actual"])
		self.assertEqual(invoice.items[0].item_tax_template, f"{INPUT_TAX} - {self.abbr}")
		self.assertFalse(invoice.items[1].item_tax_template)
		self.assertEqual(invoice.base_grand_total, 130.9)
		self.assertEqual(purchase_invoice_lines(invoice), [self._input_tax_line("130.90")])

	def test_import_leaves_invoice_template_empty(self):
		invoice = self._import([("BUERO", 1, 100)], [(19, 100, 19)])
		self.assertFalse(invoice.taxes_and_charges)
		self.assertEqual(purchase_invoice_lines(invoice), [self._input_tax_line("119.00")])

	def test_rounded_tax_amount_accepted(self):
		invoice = self._import([("BUERO", 1, 100)], [(19, 100, 19.01)])
		self.assertEqual(purchase_invoice_lines(invoice), [self._input_tax_line("119.01")])

	def test_wrong_rate_rejected(self):
		invoice = self._import([("BUERO", 1, 100)], [(7, 100, 7)])
		with self.assertRaises(frappe.ValidationError) as raised:
			purchase_invoice_lines(invoice)
		self.assertIn("its tax amounts give 7.00%", str(raised.exception))

	def _reduced_rate_item(self):
		reduced = frappe.get_doc(
			{
				"doctype": "Account",
				"account_name": "Vorsteuer 7 %",
				"account_number": "1401",
				"company": self.company,
				"parent_account": f"Tax Assets - {self.abbr}",
				"account_type": "Tax",
				"tax_rate": 7,
			}
		).insert()
		template = frappe.get_doc(
			{
				"doctype": "Item Tax Template",
				"title": "DE Vorsteuer 7 %",
				"company": self.company,
				"taxes": [{"tax_type": reduced.name, "tax_rate": 7}],
			}
		).insert()
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "BUCH",
				"item_name": "BUCH",
				"item_group": "Services",
				"stock_uom": "Nos",
				"is_stock_item": 0,
				"item_defaults": [
					{"company": self.company, "expense_account": account(self.company, "6815")}
				],
				"taxes": [{"item_tax_template": template.name}],
			}
		).insert()
		return reduced.name

	def test_mixed_rates_spread_over_all_lines_rejected(self):
		reduced = self._reduced_rate_item()
		invoice = self._import([("BUERO", 1, 100), ("BUCH", 1, 100)], [(19, 100, 19), (7, 100, 7, reduced)])
		with self.assertRaises(frappe.ValidationError) as raised:
			purchase_invoice_lines(invoice)
		self.assertIn("its tax amounts give 13.00%", str(raised.exception))

	def test_without_template_rejected_until_invoice_template_set(self):
		invoice = self._import([("FREMD", 1, 100)], [(19, 100, 19)])
		with self.assertRaises(frappe.ValidationError) as raised:
			purchase_invoice_lines(invoice)
		self.assertIn("has no tax template", str(raised.exception))
		invoice.taxes_and_charges = f"{INPUT_TAX} - {self.abbr}"
		self.assertEqual(purchase_invoice_lines(invoice), [self._input_tax_line("119.00")])

	def test_actual_charge_on_non_tax_account_rejected(self):
		invoice = self._import([("BUERO", 1, 100)], [(19, 100, 19)])
		invoice.append(
			"taxes",
			{
				"charge_type": "Actual",
				"account_head": account(self.company, "6855"),
				"description": "Fracht",
				"tax_amount": 5,
			},
		)
		invoice.calculate_taxes_and_totals()
		with self.assertRaises(frappe.ValidationError) as raised:
			purchase_invoice_lines(invoice)
		self.assertIn("Charges on account", str(raised.exception))

	def test_sales_invoice_with_actual_tax_row(self):
		invoice = make_invoice(self.company, [("MIETE", 1, 60), ("ZUB", 1, 40)])
		invoice.taxes[0].charge_type = "Actual"
		invoice.taxes[0].tax_amount = 19
		invoice.save()
		code = f"DE 19 % - {self.abbr}"
		self.assertEqual(
			sales_invoice_lines(invoice),
			[
				Line(f"4200:{code}:19", 4200, Decimal("47.60"), 19, code, "S"),
				Line(f"4400:{code}:19", 4400, Decimal("71.40"), 19, code, "S"),
			],
		)
