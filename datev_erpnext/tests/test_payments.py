from decimal import Decimal
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from datev_erpnext import sync
from datev_erpnext.payments import PaymentLine, Waiting, voucher_lines
from datev_erpnext.tests.factories import (
	CUSTOMER_NAME,
	SUPPLIER_NAME,
	account,
	customer,
	make_client,
	make_company,
	make_invoice,
	make_journal,
	make_payment,
	make_purchase_invoice,
	supplier,
)
from datev_erpnext.tests.test_images import minimal_pdf


class TestPayments(IntegrationTestCase):
	def setUp(self):
		self.company = make_company()
		frappe.db.savepoint("payments")
		self.addCleanup(frappe.db.rollback, save_point="payments")
		for target, value in (("frappe.get_print", minimal_pdf()), ("frappe.enqueue", None)):
			patcher = patch(target, return_value=value)
			patcher.start()
			self.addCleanup(patcher.stop)
		frappe.db.set_single_value("DATEV ERPNext Settings", "sync_from_date", "2000-01-01")
		make_client(self.company)

	def _invoice(self, rate=100):
		invoice = make_invoice(self.company, [("MIETE", 1, rate)])
		invoice.submit()
		return invoice, sync.sync_sales_invoice(invoice.name)

	def _line(self, key, amount, side, document_field, **values):
		defaults = {
			"party_type": None,
			"party": None,
			"account_number": None,
			"contra_account_number": 1800,
			"depends_on": None,
			"posting_text": CUSTOMER_NAME,
		}
		defaults.update(values)
		return PaymentLine(
			line_key=key,
			amount=Decimal(amount),
			debit_credit=side,
			document_field=document_field,
			**defaults,
		)

	def _debtor(self, against, amount, document_field=None, depends_on=None):
		return self._line(
			f"1200:{customer()}:{against}",
			amount,
			"H",
			document_field or against,
			party_type="Customer",
			party=customer(),
			depends_on=depends_on,
		)

	def test_customer_payment_single_invoice(self):
		invoice, document = self._invoice()
		payment = make_payment(self.company, [(invoice.name, 119)])
		payment.submit()
		self.assertEqual(
			voucher_lines("Payment Entry", payment.name),
			[self._debtor(invoice.name, "119.00", depends_on=document)],
		)

	def test_multi_invoice_with_deduction(self):
		first, first_document = self._invoice()
		second, second_document = self._invoice()
		payment = make_payment(
			self.company, [(first.name, 119), (second.name, 119)], deductions=[("6855", 2.5)]
		)
		payment.submit()
		self.assertEqual(payment.base_received_amount, 235.5)
		fee = self._line(
			f"6855::{payment.name}",
			"2.50",
			"S",
			payment.name,
			account_number=6855,
			posting_text="Nebenkosten des Geldverkehrs",
		)
		self.assertEqual(
			sorted(voucher_lines("Payment Entry", payment.name), key=lambda line: line.line_key),
			sorted(
				[
					self._debtor(first.name, "119.00", depends_on=first_document),
					self._debtor(second.name, "119.00", depends_on=second_document),
					fee,
				],
				key=lambda line: line.line_key,
			),
		)

	def test_unallocated_remainder(self):
		invoice, document = self._invoice()
		payment = make_payment(self.company, [(invoice.name, 119)], amount=200)
		payment.submit()
		self.assertEqual(
			voucher_lines("Payment Entry", payment.name),
			sorted(
				[
					self._debtor(invoice.name, "119.00", depends_on=document),
					self._debtor(payment.name, "81.00"),
				],
				key=lambda line: line.line_key,
			),
		)

	def test_invoice_before_sync_from_date_has_no_dependency(self):
		invoice = make_invoice(self.company, [("MIETE", 1, 100)])
		invoice.submit()
		frappe.db.set_single_value("DATEV ERPNext Settings", "sync_from_date", "2999-01-01")
		payment = make_payment(self.company, [(invoice.name, 119)])
		payment.submit()
		self.assertEqual(voucher_lines("Payment Entry", payment.name), [self._debtor(invoice.name, "119.00")])

	def test_invoice_not_yet_in_datev_holds(self):
		invoice = make_invoice(self.company, [("MIETE", 1, 100)])
		invoice.submit()
		payment = make_payment(self.company, [(invoice.name, 119)])
		payment.submit()
		with self.assertRaises(frappe.ValidationError) as raised:
			voucher_lines("Payment Entry", payment.name)
		self.assertIn(invoice.name, str(raised.exception))

	def test_supplier_advance_on_advance_account_books_gl(self):
		advance = account(self.company, "1186")
		payment = make_payment(
			self.company,
			amount=500,
			payment_type="Pay",
			paid_to=advance,
			book_advance_payments_in_separate_party_account=1,
		)
		payment.submit()
		self.assertEqual(
			voucher_lines("Payment Entry", payment.name),
			[
				self._line(
					f"1186:{supplier()}:{payment.name}",
					"500.00",
					"S",
					payment.name,
					account_number=1186,
					posting_text=SUPPLIER_NAME,
				)
			],
		)

	def test_supplier_payment_without_invoice_books_creditor(self):
		payment = make_payment(self.company, amount=300, payment_type="Pay")
		payment.submit()
		self.assertEqual(
			voucher_lines("Payment Entry", payment.name),
			[
				self._line(
					f"3300:{supplier()}:{payment.name}",
					"300.00",
					"S",
					payment.name,
					party_type="Supplier",
					party=supplier(),
					posting_text=SUPPLIER_NAME,
				)
			],
		)

	def test_bank_fee_journal_entry(self):
		journal = make_journal(self.company, [("6855", 5, 0), ("1800", 0, 5)])
		journal.submit()
		self.assertEqual(
			voucher_lines("Journal Entry", journal.name),
			[
				self._line(
					f"6855::{journal.name}",
					"5.00",
					"S",
					journal.name,
					account_number=6855,
					posting_text="Nebenkosten des Geldverkehrs",
				)
			],
		)

	def test_internal_transfer(self):
		payment = make_payment(
			self.company,
			amount=1000,
			payment_type="Internal Transfer",
			party_type=None,
			party=None,
			paid_from=account(self.company, "1800"),
			paid_to=account(self.company, "1810"),
		)
		payment.submit()
		self.assertEqual(
			voucher_lines("Payment Entry", payment.name),
			[
				self._line(
					f"1810::{payment.name}",
					"1000.00",
					"S",
					payment.name,
					account_number=1810,
					posting_text="Sparkasse",
				)
			],
		)

	def test_journal_without_bank_ignored(self):
		journal = make_journal(self.company, [("6855", 5, 0), ("6815", 0, 5)])
		journal.submit()
		self.assertEqual(voucher_lines("Journal Entry", journal.name), [])

	def test_account_without_number_rejected(self):
		abbr = frappe.db.get_value("Company", self.company, "abbr")
		unnumbered = frappe.get_doc(
			{
				"doctype": "Account",
				"account_name": "Gebühren ohne Nummer",
				"company": self.company,
				"parent_account": f"Indirect Expenses - {abbr}",
				"account_type": "Expense Account",
				"is_group": 0,
			}
		).insert()
		journal = make_journal(self.company, [("1800", 0, 5)])
		journal.append(
			"accounts",
			{
				"account": unnumbered.name,
				"debit_in_account_currency": 5,
				"cost_center": frappe.get_cached_value("Company", self.company, "cost_center"),
			},
		)
		journal.save()
		journal.submit()
		with self.assertRaises(frappe.ValidationError) as raised:
			voucher_lines("Journal Entry", journal.name)
		self.assertIn(unnumbered.name, str(raised.exception))

	def _purchase(self, attach=True):
		invoice = make_purchase_invoice(self.company, [("BUERO", 1, 100)])
		if attach:
			frappe.get_doc(
				{
					"doctype": "File",
					"file_name": "lieferant.pdf",
					"content": minimal_pdf(),
					"is_private": 1,
					"attached_to_doctype": "Purchase Invoice",
					"attached_to_name": invoice.name,
				}
			).insert(ignore_permissions=True)
		invoice.submit()
		return invoice

	def _creditor(self, invoice, depends_on):
		return self._line(
			f"3300:{supplier()}:{invoice.name}",
			"119.00",
			"S",
			invoice.bill_no,
			party_type="Supplier",
			party=supplier(),
			depends_on=depends_on,
			posting_text=SUPPLIER_NAME,
		)

	def test_supplier_payment_uses_bill_no_and_waits(self):
		frappe.db.set_single_value("DATEV ERPNext Settings", "sync_purchase_invoices", 1)
		invoice = self._purchase()
		payment = make_payment(self.company, [(invoice.name, 119)], payment_type="Pay")
		payment.submit()
		with self.assertRaises(Waiting) as raised:
			voucher_lines("Payment Entry", payment.name)
		self.assertIn(invoice.name, str(raised.exception))

		document = sync.sync_purchase_invoice(invoice.name)
		self.assertTrue(document)
		self.assertEqual(voucher_lines("Payment Entry", payment.name), [self._creditor(invoice, document)])

	def test_supplier_payment_held_when_purchases_off(self):
		invoice = self._purchase()
		payment = make_payment(self.company, [(invoice.name, 119)], payment_type="Pay")
		payment.submit()
		with self.assertRaises(Waiting) as raised:
			voucher_lines("Payment Entry", payment.name)
		self.assertIn("waiting for purchase invoices to be synced", str(raised.exception))

		frappe.db.set_single_value("DATEV ERPNext Settings", "sync_from_date", "2999-01-01")
		self.assertEqual(voucher_lines("Payment Entry", payment.name), [self._creditor(invoice, None)])
