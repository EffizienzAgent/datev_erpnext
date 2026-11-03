from decimal import Decimal
from unittest.mock import patch

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field
from frappe.tests import IntegrationTestCase

from datev_erpnext import sync
from datev_erpnext.tests.factories import (
	INPUT_TAX,
	SUPPLIER_NAME,
	make_client,
	make_company,
	make_purchase_invoice,
	supplier,
)
from datev_erpnext.tests.test_images import minimal_pdf

PURCHASE_JOB = "datev_erpnext.sync.sync_purchase_invoice"
NUMBER_FIELD = "datev_test_creditor_number"
XML = b'<?xml version="1.0" encoding="UTF-8"?><Invoice/>'


class TestPurchaseSync(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.get_meta("Supplier").has_field(NUMBER_FIELD):
			create_custom_field(
				"Supplier", {"fieldname": NUMBER_FIELD, "fieldtype": "Data", "label": "Test Number"}
			)

	def setUp(self):
		self.company = make_company()
		frappe.db.savepoint("purchase_sync")
		self.addCleanup(frappe.db.rollback, save_point="purchase_sync")
		renderer = patch("frappe.get_print", return_value=minimal_pdf())
		self.get_print = renderer.start()
		self.addCleanup(renderer.stop)
		enqueue = patch("frappe.enqueue")
		self.enqueue = enqueue.start()
		self.addCleanup(enqueue.stop)
		frappe.db.set_single_value("DATEV ERPNext Settings", "sync_from_date", "2000-01-01")
		frappe.db.set_single_value("DATEV ERPNext Settings", "sync_purchase_invoices", 1)
		self.client = make_client(self.company)

	def _attach(self, doctype, name, file_name, content):
		return frappe.get_doc(
			{
				"doctype": "File",
				"file_name": file_name,
				"content": content,
				"is_private": 1,
				"attached_to_doctype": doctype,
				"attached_to_name": name,
			}
		).insert(ignore_permissions=True)

	def _invoice(self, attach=True, **overrides):
		invoice = make_purchase_invoice(self.company, [("BUERO", 1, 100)], **overrides)
		if attach:
			self._attach("Purchase Invoice", invoice.name, "lieferant.pdf", minimal_pdf())
		invoice.submit()
		return invoice

	def _jobs(self):
		return [
			call.kwargs for call in self.enqueue.call_args_list if call.args and call.args[0] == PURCHASE_JOB
		]

	def _file_name(self, document):
		return frappe.db.get_value("DATEV Document", document, "file_name")

	def test_purchase_synced(self):
		invoice = self._invoice()
		self.assertEqual([job["name"] for job in self._jobs()], [invoice.name])
		self.assertEqual(self._jobs()[0]["job_id"], f"datev-sync-Purchase Invoice:{invoice.name}")

		name = sync.sync_purchase_invoice(invoice.name)

		document = frappe.get_doc("DATEV Document", name)
		self.assertEqual(document.source_key, f"Purchase Invoice:{invoice.name}")
		self.assertEqual(document.direction, "Incoming")
		self.assertEqual(document.document_number, invoice.bill_no)
		self.assertEqual(
			document.file_name,
			frappe.db.get_value("File", {"attached_to_name": invoice.name}, "file_name"),
		)
		self.assertEqual(Decimal(str(document.amount)), Decimal("119"))
		party = frappe.get_doc(
			"DATEV Party Account", frappe.db.get_value("DATEV Party Account", {"party": supplier()})
		)
		self.assertEqual((party.kind, party.party_type), ("Creditor", "Supplier"))
		[row] = frappe.get_all(
			"DATEV Booking",
			filters={"document": name},
			fields=[
				"kind",
				"party_account",
				"contra_account",
				"amount",
				"debit_credit",
				"tax_rate",
				"bu_key",
				"posting_text",
				"document_field_1",
			],
		)
		self.assertEqual(
			(
				row.kind,
				row.party_account,
				row.contra_account,
				row.debit_credit,
				row.tax_rate,
				row.posting_text,
			),
			("Incoming Invoice", party.name, 6815, "H", 19, SUPPLIER_NAME),
		)
		self.assertEqual(row.document_field_1, invoice.bill_no)
		self.assertEqual(sync.datev_status(invoice.name, "Purchase Invoice")["document"]["status"], "Pending")

	def test_purchase_off_by_default(self):
		frappe.db.set_single_value("DATEV ERPNext Settings", "sync_purchase_invoices", 0)
		invoice = self._invoice()
		self.assertEqual(self._jobs(), [])
		self.assertIsNone(sync.sync_purchase_invoice(invoice.name))
		self.assertEqual(sync.datev_status(invoice.name, "Purchase Invoice"), {"hidden": True})

	def test_einvoice_import_file_used(self):
		if "eu_einvoice" not in frappe.get_installed_apps():
			self.skipTest("eu_einvoice is not installed")
		einvoice = frappe.new_doc("E Invoice Import")
		einvoice.update({"company": self.company, "supplier": supplier()})
		einvoice.db_insert()
		file = self._attach("E Invoice Import", einvoice.name, "xrechnung.xml", XML)
		self.assertTrue(file.file_name.endswith(".xml"))
		frappe.db.set_value("E Invoice Import", einvoice.name, "einvoice", file.file_url)
		invoice = make_purchase_invoice(self.company, [("BUERO", 1, 100)])
		self._attach("Purchase Invoice", invoice.name, "scan.pdf", minimal_pdf())
		invoice.db_set("e_invoice_import", einvoice.name)
		invoice.submit()

		document = sync.sync_purchase_invoice(invoice.name)
		self.assertEqual(self._file_name(document), file.file_name)

	def test_attached_pdf_used(self):
		invoice = make_purchase_invoice(self.company, [("BUERO", 1, 100)])
		self._attach("Purchase Invoice", invoice.name, "older.xml", XML)
		frappe.db.set_value("File", {"file_name": "older.xml"}, "creation", "2000-01-01 00:00:00")
		self._attach("Purchase Invoice", invoice.name, "notiz.txt", b"note")
		newest = self._attach("Purchase Invoice", invoice.name, "Rechnung.PDF", minimal_pdf())
		invoice.submit()
		document = sync.sync_purchase_invoice(invoice.name)
		self.assertEqual(self._file_name(document), newest.file_name)
		self.get_print.assert_not_called()

	def test_no_supplier_file_notice(self):
		invoice = self._invoice(attach=False)
		self.assertIsNone(sync.sync_purchase_invoice(invoice.name))
		self.assertFalse(frappe.db.exists("DATEV Document", {"source_name": invoice.name}))
		self.get_print.assert_not_called()
		comments = frappe.get_all(
			"Comment",
			filters={"reference_doctype": "Purchase Invoice", "reference_name": invoice.name},
			pluck="content",
		)
		self.assertEqual(comments, ["DATEV note: no supplier document attached, not transferred."])
		self.assertEqual(sync.datev_status(invoice.name, "Purchase Invoice")["notice"], "not_transferred")

	def test_debit_note_uses_print_pdf(self):
		original = self._invoice()
		debit_note = make_purchase_invoice(
			self.company, [("BUERO", -1, 100)], is_return=1, return_against=original.name, bill_no=None
		)
		debit_note.submit()
		document = sync.sync_purchase_invoice(debit_note.name)
		self.get_print.assert_called_once_with("Purchase Invoice", debit_note.name, as_pdf=True)
		values = frappe.db.get_value(
			"DATEV Document", document, ["file_name", "document_number", "is_return"], as_dict=True
		)
		self.assertEqual(
			(values.file_name, values.document_number, values.is_return),
			(f"{debit_note.name}.pdf", original.bill_no, 1),
		)
		sides = frappe.get_all("DATEV Booking", filters={"document": document}, pluck="debit_credit")
		self.assertEqual(sides, ["S"])

	def test_purchase_cancel_blocked_after_upload(self):
		invoice = self._invoice()
		document = sync.sync_purchase_invoice(invoice.name)
		frappe.db.set_value("DATEV Document", document, "status", "Uploaded")
		with self.assertRaises(frappe.ValidationError):
			invoice.cancel()
		self.assertEqual(frappe.db.get_value("Purchase Invoice", invoice.name, "docstatus"), 1)

	def test_purchase_cancel_before_upload_discards(self):
		invoice = self._invoice()
		document = sync.sync_purchase_invoice(invoice.name)
		invoice.cancel()
		self.assertEqual(frappe.db.get_value("DATEV Document", document, "status"), "Discarded")

	def test_supplier_party_account_number(self):
		settings = frappe.get_single("DATEV ERPNext Settings")
		self.addCleanup(frappe.db.set_single_value, "DATEV ERPNext Settings", "supplier_number_field", None)
		settings.supplier_number_field = NUMBER_FIELD
		settings.save()
		frappe.db.set_value("Supplier", supplier(), NUMBER_FIELD, "70001")
		invoice = self._invoice()
		sync.sync_purchase_invoice(invoice.name)
		self.assertEqual(
			frappe.db.get_value("DATEV Party Account", {"party": supplier()}, "preferred_number"), 70001
		)

	def test_reverse_charge_synced_with_bu_key(self):
		abbr = frappe.db.get_value("Company", self.company, "abbr")
		invoice = make_purchase_invoice(
			self.company, [("FREMD", 1, 1000)], taxes_and_charges=f"DE Reverse Charge 19 % - {abbr}"
		)
		self._attach("Purchase Invoice", invoice.name, "lieferant.pdf", minimal_pdf())
		invoice.submit()
		document = sync.sync_purchase_invoice(invoice.name)
		[row] = frappe.get_all(
			"DATEV Booking", filters={"document": document}, fields=["amount", "bu_key", "tax_rate"]
		)
		self.assertEqual((Decimal(str(row.amount)), row.bu_key, row.tax_rate), (Decimal("1000"), "94", 0))

	def test_tax_code_check_uses_purchase_template(self):
		abbr = frappe.db.get_value("Company", self.company, "abbr")
		self.client.tax_keys = [
			row for row in self.client.tax_keys if row.tax_code != f"{INPUT_TAX} - {abbr}"
		]
		self.client.save()
		invoice = self._invoice()
		frappe.db.savepoint("before_sync")
		real_rollback = frappe.db.rollback
		with (
			patch("frappe.db.rollback", side_effect=lambda: real_rollback(save_point="before_sync")),
			patch("frappe.db.commit"),
		):
			self.assertIsNone(sync.sync_purchase_invoice(invoice.name))
		self.assertFalse(frappe.db.exists("DATEV Document", {"source_name": invoice.name}))

	def test_catch_up_and_report_include_purchases(self):
		from frappe.desk.query_report import run

		invoice = self._invoice(attach=False)
		sync.sync_purchase_invoice(invoice.name)
		self.enqueue.reset_mock()
		sync.catch_up()
		self.assertIn(invoice.name, [job["name"] for job in self._jobs()])
		report = run(
			"Vouchers without DATEV", filters={"company": self.company, "voucher_type": "Purchase Invoice"}
		)
		reasons = {row["name"]: row["reason"] for row in report["result"]}
		self.assertEqual(reasons[invoice.name], "DATEV note: no supplier document attached, not transferred.")
