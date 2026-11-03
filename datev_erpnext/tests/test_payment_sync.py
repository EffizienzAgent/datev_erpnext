from decimal import Decimal
from unittest.mock import patch

import frappe
from datev.protect import internal
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from datev_erpnext import payment_sync, sync
from datev_erpnext.tests.factories import (
	CUSTOMER_NAME,
	account,
	customer,
	make_client,
	make_company,
	make_invoice,
	make_journal,
	make_payment,
)
from datev_erpnext.tests.test_images import minimal_pdf

PAYMENT_JOB = "datev_erpnext.payment_sync.sync_payment"


def bookings(document):
	return frappe.get_all(
		"DATEV Booking",
		filters={"document": document},
		fields=[
			"name",
			"line_key",
			"kind",
			"party_account",
			"account",
			"contra_account",
			"amount",
			"debit_credit",
			"document_field_1",
			"depends_on_document",
			"general_reversal",
			"posting_text",
			"status",
		],
		order_by="line_key",
	)


class TestPaymentSync(IntegrationTestCase):
	def setUp(self):
		self.company = make_company()
		frappe.db.savepoint("payment_sync")
		self.addCleanup(frappe.db.rollback, save_point="payment_sync")
		renderer = patch("frappe.get_print", return_value=minimal_pdf())
		renderer.start()
		self.addCleanup(renderer.stop)
		enqueue = patch("frappe.enqueue")
		self.enqueue = enqueue.start()
		self.addCleanup(enqueue.stop)
		frappe.db.set_single_value("DATEV ERPNext Settings", "sync_from_date", "2000-01-01")
		frappe.db.set_single_value("DATEV ERPNext Settings", "sync_payments", 1)
		self.client = make_client(self.company)

	def _jobs(self):
		return [
			call.kwargs for call in self.enqueue.call_args_list if call.args and call.args[0] == PAYMENT_JOB
		]

	def _invoice(self):
		invoice = make_invoice(self.company, [("MIETE", 1, 100)])
		invoice.submit()
		return invoice, sync.sync_sales_invoice(invoice.name)

	def _synced_payment(self, references=None, **overrides):
		if references is None:
			invoice, _document = self._invoice()
			references = [(invoice.name, 119)]
		payment = make_payment(self.company, references, **overrides)
		payment.submit()
		return payment, payment_sync.sync_payment("Payment Entry", payment.name)

	def _transfer(self, document):
		for row in bookings(document):
			frappe.db.set_value("DATEV Booking", row.name, "status", "Transferred")

	def _comments(self, doc):
		return frappe.get_all(
			"Comment",
			filters={"reference_doctype": doc.doctype, "reference_name": doc.name},
			pluck="content",
		)

	def test_payment_synced_on_submit(self):
		invoice, invoice_document = self._invoice()
		payment = make_payment(self.company, [(invoice.name, 119)])
		payment.submit()
		self.assertEqual(
			self._jobs(),
			[
				{
					"doctype": "Payment Entry",
					"name": payment.name,
					"job_id": f"datev-sync-Payment Entry:{payment.name}",
					"deduplicate": True,
					"queue": "short",
					"enqueue_after_commit": True,
				}
			],
		)

		name = payment_sync.sync_payment("Payment Entry", payment.name)

		document = frappe.get_doc("DATEV Document", name)
		self.assertEqual(document.source_key, f"Payment Entry:{payment.name}")
		self.assertEqual((document.direction, document.status), ("Other", "Not Required"))
		self.assertEqual(document.document_number, payment.name)
		self.assertEqual(Decimal(str(document.amount)), Decimal("119"))
		party = frappe.db.get_value("DATEV Party Account", {"party": customer(), "kind": "Debtor"}, "name")
		[row] = bookings(name)
		self.assertEqual(
			(
				row.kind,
				row.party_account,
				row.account,
				row.contra_account,
				Decimal(str(row.amount)),
				row.debit_credit,
				row.document_field_1,
				row.depends_on_document,
				row.posting_text,
			),
			("Payment", party, 0, 1800, Decimal("119"), "H", invoice.name, invoice_document, CUSTOMER_NAME),
		)
		self.assertEqual(payment_sync.sync_payment("Payment Entry", payment.name), name)
		self.assertEqual(len(bookings(name)), 1)

	def test_payment_off_by_default(self):
		frappe.db.set_single_value("DATEV ERPNext Settings", "sync_payments", 0)
		invoice, _document = self._invoice()
		payment = make_payment(self.company, [(invoice.name, 119)])
		payment.submit()
		self.assertEqual(self._jobs(), [])
		self.assertIsNone(payment_sync.sync_payment("Payment Entry", payment.name))
		self.assertFalse(frappe.db.exists("DATEV Document", {"source_name": payment.name}))
		self.assertEqual(sync.datev_status(payment.name, "Payment Entry"), {"hidden": True})

	def test_bank_journal_synced_and_other_journal_ignored(self):
		fee = make_journal(self.company, [("6855", 5, 0), ("1800", 0, 5)])
		fee.submit()
		other = make_journal(self.company, [("6855", 5, 0), ("6815", 0, 5)])
		other.submit()
		self.assertEqual([job["name"] for job in self._jobs()], [fee.name])
		document = payment_sync.sync_payment("Journal Entry", fee.name)
		[row] = bookings(document)
		self.assertEqual((row.account, row.party_account, row.debit_credit), (6855, None, "S"))
		self.assertIsNone(payment_sync.sync_payment("Journal Entry", other.name))
		self.assertEqual(sync.datev_status(other.name, "Journal Entry"), {"hidden": True})

	def test_waiting_for_invoice_notes(self):
		invoice = make_invoice(self.company, [("MIETE", 1, 100)])
		invoice.submit()
		payment = make_payment(self.company, [(invoice.name, 119)])
		payment.submit()
		self.assertIsNone(payment_sync.sync_payment("Payment Entry", payment.name))
		self.assertFalse(frappe.db.exists("DATEV Document", {"source_name": payment.name}))
		[comment] = self._comments(payment)
		self.assertIn(invoice.name, comment)
		self.assertEqual(sync.datev_status(payment.name, "Payment Entry")["notice"], "waiting")

	def test_cancel_before_transfer_discards(self):
		payment, document = self._synced_payment()
		payment.cancel()
		self.assertEqual(frappe.db.get_value("DATEV Document", document, "status"), "Discarded")
		self.assertEqual(bookings(document), [])

	def test_cancel_after_transfer_reverses(self):
		payment, document = self._synced_payment(deductions=[("6855", 2.5)])
		self._transfer(document)
		sent = {row.line_key: row for row in bookings(document)}
		payment.cancel()

		rows = {row.line_key: row for row in bookings(document)}
		self.assertEqual(set(rows), {*sent, *(f"storno:{key}" for key in sent)})
		for key, original in sent.items():
			reversal = rows[f"storno:{key}"]
			self.assertEqual(
				(
					reversal.party_account,
					reversal.account,
					reversal.contra_account,
					reversal.amount,
					reversal.debit_credit,
					reversal.document_field_1,
					reversal.general_reversal,
					reversal.status,
				),
				(
					original.party_account,
					original.account,
					original.contra_account,
					original.amount,
					original.debit_credit,
					original.document_field_1,
					1,
					"Pending",
				),
			)
			self.assertEqual(reversal.posting_text, f"Storno {original.posting_text}")
		self.assertEqual(frappe.db.get_value("DATEV Document", document, "status"), "Not Required")

	def test_cancel_blocked_while_batched(self):
		payment, document = self._synced_payment()
		batch = internal(
			frappe.get_doc(
				{
					"doctype": "DATEV Batch",
					"client": self.client.name,
					"export_connector": "EXTF download",
					"status": "Approved",
				}
			)
		).insert(ignore_permissions=True)
		for row in bookings(document):
			frappe.db.set_value("DATEV Booking", row.name, {"status": "Batched", "batch": batch.name})
		with self.assertRaises(frappe.ValidationError):
			payment.cancel()
		self.assertEqual(frappe.db.get_value("Payment Entry", payment.name, "docstatus"), 1)

	def test_cancel_while_awaiting_approval_discards(self):
		payment, document = self._synced_payment()
		batch = internal(
			frappe.get_doc(
				{
					"doctype": "DATEV Batch",
					"client": self.client.name,
					"export_connector": "EXTF download",
					"status": "Awaiting Approval",
				}
			)
		).insert(ignore_permissions=True)
		for row in bookings(document):
			frappe.db.set_value("DATEV Booking", row.name, {"status": "Batched", "batch": batch.name})
		payment.cancel()
		self.assertEqual(frappe.db.get_value("DATEV Document", document, "status"), "Discarded")
		self.assertFalse(frappe.db.exists("DATEV Batch", batch.name))

	def test_reallocation_after_transfer(self):
		first, _first_document = self._invoice()
		second, second_document = self._invoice()
		payment, document = self._synced_payment([(first.name, 119)], amount=238, deductions=())
		self._transfer(document)
		sent = {row.line_key: row for row in bookings(document)}
		advance_key = f"1200:{customer()}:{payment.name}"
		self.assertEqual(set(sent), {f"1200:{customer()}:{first.name}", advance_key})
		self.enqueue.reset_mock()

		reconciliation = frappe.new_doc("Payment Reconciliation")
		reconciliation.update(
			{
				"company": self.company,
				"party_type": "Customer",
				"party": customer(),
				"receivable_payable_account": account(self.company, "1200"),
			}
		)
		reconciliation.get_unreconciled_entries()
		invoices = [row.as_dict() for row in reconciliation.invoices if row.invoice_number == second.name]
		payments = [row.as_dict() for row in reconciliation.payments if row.reference_name == payment.name]
		reconciliation.allocate_entries(frappe._dict({"invoices": invoices, "payments": payments}))
		reconciliation.reconcile()
		self.assertIn(payment.name, [job["name"] for job in self._jobs()])

		payment_sync.sync_payment("Payment Entry", payment.name)

		rows = {row.line_key: row for row in bookings(document)}
		new_key = f"1200:{customer()}:{second.name}"
		self.assertEqual(set(rows), {*sent, f"storno:{advance_key}", new_key})
		self.assertEqual(rows[f"storno:{advance_key}"].general_reversal, 1)
		self.assertEqual(rows[f"storno:{advance_key}"].document_field_1, payment.name)
		self.assertEqual(
			(rows[new_key].document_field_1, rows[new_key].depends_on_document, rows[new_key].debit_credit),
			(second.name, second_document, "H"),
		)
		for key in sent:
			self.assertEqual(rows[key].status, "Transferred")

		payment_sync.sync_payment("Payment Entry", payment.name)
		self.assertEqual(len(bookings(document)), 4)

	def test_reallocation_before_transfer_updates_open_lines(self):
		first, _first_document = self._invoice()
		payment, document = self._synced_payment([], amount=119, deductions=())
		reconciliation = frappe.new_doc("Payment Reconciliation")
		reconciliation.update(
			{
				"company": self.company,
				"party_type": "Customer",
				"party": customer(),
				"receivable_payable_account": account(self.company, "1200"),
			}
		)
		reconciliation.get_unreconciled_entries()
		invoices = [row.as_dict() for row in reconciliation.invoices if row.invoice_number == first.name]
		payments = [row.as_dict() for row in reconciliation.payments if row.reference_name == payment.name]
		reconciliation.allocate_entries(frappe._dict({"invoices": invoices, "payments": payments}))
		reconciliation.reconcile()

		payment_sync.sync_payment("Payment Entry", payment.name)
		self.assertEqual(
			[(row.line_key, row.document_field_1) for row in bookings(document)],
			[(f"1200:{customer()}:{first.name}", first.name)],
		)

	def test_unreconcile_queues_recompute(self):
		invoice, _invoice_document = self._invoice()
		payment, _document = self._synced_payment([(invoice.name, 119)])
		self.enqueue.reset_mock()
		unreconcile = frappe.get_doc(
			{
				"doctype": "Unreconcile Payment",
				"company": self.company,
				"voucher_type": "Payment Entry",
				"voucher_no": payment.name,
			}
		)
		unreconcile.add_references()
		unreconcile.save()
		unreconcile.submit()
		self.assertIn(payment.name, [job["name"] for job in self._jobs()])

	def test_catch_up_includes_payments(self):
		missing = make_payment(self.company, [], amount=50)
		missing.submit()
		synced, document = self._synced_payment([], amount=70)
		self.enqueue.reset_mock()
		frappe.db.set_value(
			"DATEV Booking", {"document": document}, "modified", "2000-01-01 00:00:00", update_modified=False
		)
		frappe.db.set_value(
			"DATEV Document", document, "creation", "2000-01-01 00:00:00", update_modified=False
		)

		sync.catch_up()

		queued = {(job["doctype"], job["name"]): job for job in self._jobs()}
		self.assertEqual(queued[("Payment Entry", missing.name)]["queue"], "long")
		self.assertIn(("Payment Entry", synced.name), queued)

		self.enqueue.reset_mock()
		frappe.db.set_value(
			"DATEV Booking", {"document": document}, "modified", "2999-01-01 00:00:00", update_modified=False
		)
		sync.catch_up()
		self.assertNotIn(
			("Payment Entry", synced.name), {(job["doctype"], job["name"]) for job in self._jobs()}
		)

	def test_payment_report_lists_missing(self):
		from frappe.desk.query_report import run

		missing = make_payment(self.company, [], amount=50, posting_date=nowdate())
		missing.submit()
		report = run(
			"Vouchers without DATEV", filters={"company": self.company, "voucher_type": "Payment Entry"}
		)
		self.assertIn(missing.name, [row["name"] for row in report["result"]])
