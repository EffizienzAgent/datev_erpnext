from decimal import Decimal
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from datev_erpnext import sync
from datev_erpnext.tests.factories import make_client, make_company, make_invoice
from datev_erpnext.tests.test_images import minimal_pdf

MIXED = [("MIETE", 1, 100), ("VERS", 1, 29.75)]
SYNC_JOB = "datev_erpnext.sync.sync_sales_invoice"


def bookings(document):
	return frappe.get_all(
		"DATEV Booking",
		filters={"document": document},
		fields=[
			"line_key",
			"kind",
			"party_account",
			"contra_account",
			"amount",
			"debit_credit",
			"tax_rate",
			"bu_key",
			"posting_text",
		],
		order_by="line_key",
	)


class TestSync(IntegrationTestCase):
	def setUp(self):
		self.company = make_company()
		frappe.db.savepoint("sync")
		self.addCleanup(frappe.db.rollback, save_point="sync")
		renderer = patch("frappe.get_print", return_value=minimal_pdf())
		renderer.start()
		self.addCleanup(renderer.stop)
		enqueue = patch("frappe.enqueue")
		self.enqueue = enqueue.start()
		self.addCleanup(enqueue.stop)
		self._set_setting("sync_from_date", "2000-01-01")

	def _set_setting(self, field, value):
		frappe.db.set_single_value("DATEV ERPNext Settings", field, value)

	def _sync_failing(self, name):
		frappe.db.savepoint("before_sync")
		real_rollback = frappe.db.rollback
		with (
			patch(
				"frappe.db.rollback", side_effect=lambda: real_rollback(save_point="before_sync")
			) as rollback,
			patch("frappe.db.commit") as commit,
		):
			result = sync.sync_sales_invoice(name)
		rollback.assert_called_once_with()
		commit.assert_called_once_with()
		return result

	def _sync_jobs(self):
		return [call for call in self.enqueue.call_args_list if call.args and call.args[0] == SYNC_JOB]

	def _submitted(self, items=MIXED, **overrides):
		invoice = make_invoice(self.company, items, **overrides)
		invoice.submit()
		return invoice

	def _comments(self, invoice):
		return frappe.get_all(
			"Comment",
			filters={"reference_doctype": "Sales Invoice", "reference_name": invoice.name},
			pluck="content",
		)

	def test_submit_enqueues(self):
		make_client(self.company)
		invoice = self._submitted()
		self.assertEqual(len(self._sync_jobs()), 1)
		self.assertEqual(
			self._sync_jobs()[0].kwargs,
			{
				"name": invoice.name,
				"job_id": f"datev-sync-{invoice.name}",
				"deduplicate": True,
				"queue": "short",
				"enqueue_after_commit": True,
			},
		)

	def test_submit_skipped_when_disabled(self):
		make_client(self.company)
		self._set_setting("enabled", 0)
		self._submitted()
		self.assertEqual(self._sync_jobs(), [])

	def test_submit_never_fails_on_sync_errors(self):
		make_client(self.company)
		make_client(self.company)
		frappe.local.message_log = []
		invoice = self._submitted()
		self.assertEqual(invoice.docstatus, 1)
		self.assertEqual(self._sync_jobs(), [])
		self.assertNotIn("DATEV Client", str(frappe.local.message_log))
		comments = frappe.get_all(
			"Comment",
			filters={"reference_doctype": "Sales Invoice", "reference_name": invoice.name},
			pluck="content",
		)
		self.assertEqual(len(comments), 1)
		self.assertIn("DATEV: More than one enabled DATEV Client", comments[0])

	def test_failed_booking_rolls_back_document(self):
		make_client(self.company)
		invoice = self._submitted()
		self.enqueue.reset_mock()
		calls = []
		real_add_booking = sync.add_booking

		def add_booking(*args, **kwargs):
			calls.append(args)
			if len(calls) == 2:
				frappe.throw("booking rejected")
			return real_add_booking(*args, **kwargs)

		with patch.object(sync, "add_booking", side_effect=add_booking):
			self.assertIsNone(self._sync_failing(invoice.name))

		self.assertEqual(len(calls), 2)
		self.assertFalse(frappe.db.exists("DATEV Document", {"source_name": invoice.name}))
		self.assertFalse(frappe.db.count("DATEV Booking", {"document_field_1": invoice.name}))
		uploads = [call for call in self.enqueue.call_args_list if call.args[0] == "datev.documents.upload"]
		self.assertEqual(len(uploads), 1)
		self.assertTrue(uploads[0].kwargs["enqueue_after_commit"])
		comments = frappe.get_all(
			"Comment",
			filters={"reference_doctype": "Sales Invoice", "reference_name": invoice.name},
			pluck="content",
		)
		self.assertIn("DATEV: booking rejected", comments)

	def test_sync_creates_document_and_bookings(self):
		make_client(self.company)
		invoice = self._submitted(po_no="PO-7")
		self._set_setting("posting_text_template", "{customer_name} {name} {po_no}")
		name = sync.sync_sales_invoice(invoice.name)

		document = frappe.get_doc("DATEV Document", name)
		self.assertEqual(document.source_key, f"Sales Invoice:{invoice.name}")
		self.assertEqual(document.direction, "Outgoing")
		self.assertEqual(document.document_number, invoice.name)
		self.assertEqual(Decimal(str(document.amount)), Decimal("148.75"))
		self.assertEqual(document.currency, "EUR")
		self.assertEqual(document.source_doctype, "Sales Invoice")
		self.assertEqual(document.source_name, invoice.name)
		self.assertFalse(document.is_return)
		self.assertEqual(document.file_name, f"{invoice.name}.pdf")

		party = frappe.db.get_value("DATEV Party Account", {"party": invoice.customer}, "name")
		rows = bookings(name)
		self.assertEqual(
			[(row.contra_account, Decimal(str(row.amount)), row.tax_rate, row.debit_credit) for row in rows],
			[(4100, Decimal("29.75"), 0, "S"), (4400, Decimal("119.00"), 19, "S")],
		)
		for row in rows:
			self.assertEqual(row.kind, "Outgoing Invoice")
			self.assertEqual(row.party_account, party)
			self.assertEqual(row.posting_text, f"{invoice.customer_name} {invoice.name} PO-7")

	def test_sync_twice_is_idempotent(self):
		make_client(self.company)
		invoice = self._submitted()
		first = sync.sync_sales_invoice(invoice.name)
		second = sync.sync_sales_invoice(invoice.name)
		self.assertEqual(first, second)
		source_key = f"Sales Invoice:{invoice.name}"
		self.assertEqual(frappe.db.count("DATEV Document", {"source_key": source_key}), 1)
		self.assertEqual(frappe.db.count("DATEV Booking", {"document": first}), 2)

	def test_return_invoice(self):
		make_client(self.company)
		original = self._submitted()
		returned = self._submitted(
			[(code, -qty, rate) for code, qty, rate in MIXED], is_return=1, return_against=original.name
		)
		original_document = sync.sync_sales_invoice(original.name)
		return_document = sync.sync_sales_invoice(returned.name)

		self.assertNotEqual(original_document, return_document)
		document = frappe.get_doc("DATEV Document", return_document)
		self.assertTrue(document.is_return)
		self.assertEqual(Decimal(str(document.amount)), Decimal("148.75"))
		self.assertEqual({row.debit_credit for row in bookings(return_document)}, {"H"})
		self.assertEqual({row.posting_text for row in bookings(return_document)}, {returned.customer_name})

	def test_no_client_is_skipped(self):
		invoice = self._submitted()
		self.assertEqual(invoice.docstatus, 1)
		self.assertEqual(self._sync_jobs(), [])
		self.assertIsNone(sync.sync_sales_invoice(invoice.name))
		self.assertFalse(frappe.db.exists("DATEV Document", {"source_name": invoice.name}))

	def test_draft_is_skipped(self):
		make_client(self.company)
		invoice = make_invoice(self.company, MIXED)
		self.assertIsNone(sync.sync_sales_invoice(invoice.name))
		self.assertFalse(frappe.db.exists("DATEV Document", {"source_name": invoice.name}))

	def test_missing_account_number_comments(self):
		make_client(self.company)
		abbr = frappe.db.get_value("Company", self.company, "abbr")
		account = frappe.get_doc(
			{
				"doctype": "Account",
				"account_name": "Erlöse ohne Nummer",
				"company": self.company,
				"parent_account": f"Direct Income - {abbr}",
				"account_type": "Income Account",
				"is_group": 0,
			}
		).insert()
		invoice = make_invoice(self.company, [("MIETE", 1, 100)])
		invoice.items[0].income_account = account.name
		invoice.save()
		invoice.submit()

		self.assertIsNone(self._sync_failing(invoice.name))
		self.assertFalse(frappe.db.exists("DATEV Document", {"source_name": invoice.name}))
		comments = frappe.get_all(
			"Comment",
			filters={"reference_doctype": "Sales Invoice", "reference_name": invoice.name},
			pluck="content",
		)
		self.assertTrue(
			any(comment.startswith("DATEV: ") and account.name in comment for comment in comments)
		)

	def test_backfill_only_missing(self):
		make_client(self.company)
		synced = self._submitted()
		missing = self._submitted()
		make_invoice(self.company, MIXED)
		sync.sync_sales_invoice(synced.name)
		self.enqueue.reset_mock()

		count = sync.backfill(self.company, synced.posting_date)

		self.assertEqual(count, 1)
		self.assertEqual(len(self._sync_jobs()), 1)
		self.assertEqual(
			self._sync_jobs()[0].kwargs,
			{
				"name": missing.name,
				"job_id": f"datev-sync-{missing.name}",
				"deduplicate": True,
				"queue": "long",
				"from_backfill": True,
			},
		)
		self.assertEqual(sync.backfill(self.company, "2000-01-01", "2000-12-31"), 0)

	def test_backfill_requires_datev_manager(self):
		make_client(self.company)
		frappe.set_user("Guest")
		self.addCleanup(frappe.set_user, "Administrator")
		with self.assertRaises(frappe.PermissionError):
			sync.backfill(self.company, "2000-01-01")

	def test_datev_status(self):
		make_client(self.company)
		invoice = self._submitted()
		self.assertEqual(sync.datev_status(invoice.name), {"error": False, "notice": None})

		document = sync.sync_sales_invoice(invoice.name)
		status = sync.datev_status(invoice.name)
		self.assertEqual(status["document"], {"status": "Pending", "external_id": None})
		self.assertFalse(status["error"])
		self.assertEqual(
			[(row["line_key"], row["status"], row["batch"]) for row in status["bookings"]],
			[(row.line_key, "Pending", None) for row in bookings(document)],
		)

	def test_datev_status_requires_read_permission(self):
		invoice = self._submitted()
		frappe.set_user("Guest")
		self.addCleanup(frappe.set_user, "Administrator")
		with self.assertRaises(frappe.PermissionError):
			sync.datev_status(invoice.name)

	def test_sync_locks_invoice(self):
		make_client(self.company)
		invoice = self._submitted()
		with patch("frappe.get_doc", wraps=frappe.get_doc) as get_doc:
			sync.sync_sales_invoice(invoice.name)
		get_doc.assert_any_call("Sales Invoice", invoice.name, for_update=True)

	def test_queued_sync_after_cancel_is_skipped(self):
		make_client(self.company)
		invoice = self._submitted()
		invoice.cancel()
		self.assertIsNone(sync.sync_sales_invoice(invoice.name))
		self.assertFalse(frappe.db.exists("DATEV Document", {"source_name": invoice.name}))

	def test_zero_amount_line_is_dropped(self):
		make_client(self.company)
		invoice = self._submitted([*MIXED, ("ZUB", 1, 0)])
		document = sync.sync_sales_invoice(invoice.name)
		self.assertEqual([row.contra_account for row in bookings(document)], [4100, 4400])

	def test_zero_total_invoice_is_skipped(self):
		make_client(self.company)
		invoice = self._submitted([("ZUB", 1, 0)])
		self.assertEqual(invoice.base_grand_total, 0)
		self.assertIsNone(sync.sync_sales_invoice(invoice.name))
		self.assertFalse(frappe.db.exists("DATEV Document", {"source_name": invoice.name}))

	def test_opening_invoice_is_skipped(self):
		make_client(self.company)
		invoice = self._submitted()
		invoice.db_set("is_opening", "Yes")
		self.enqueue.reset_mock()

		sync.on_submit(invoice)
		self.assertEqual(self._sync_jobs(), [])
		self.assertIsNone(sync.sync_sales_invoice(invoice.name))
		self.assertIsNone(sync.sync_sales_invoice(invoice.name, from_backfill=True))
		self.assertFalse(frappe.db.exists("DATEV Document", {"source_name": invoice.name}))
		self.assertEqual(sync.backfill(self.company, invoice.posting_date), 0)

	def test_unexpected_error_rolls_back_and_comments(self):
		make_client(self.company)
		invoice = self._submitted()
		with patch.object(sync, "document_image", side_effect=RuntimeError("renderer down")):
			self.assertIsNone(self._sync_failing(invoice.name))
		self.assertFalse(frappe.db.exists("DATEV Document", {"source_name": invoice.name}))
		self.assertIn("DATEV: renderer down", self._comments(invoice))

	def test_bu_key_follows_outgoing_direction(self):
		vat, free = f"DE 19 % - {self._abbr()}", f"DE steuerfrei - {self._abbr()}"
		make_client(
			self.company,
			tax_keys=[
				{"tax_code": vat, "direction": "Incoming", "bu_key": "9"},
				{"tax_code": vat, "direction": "Outgoing", "bu_key": ""},
				{"tax_code": free, "bu_key": "1"},
			],
		)
		name = sync.sync_sales_invoice(self._submitted().name)
		self.assertEqual(
			sorted((row.tax_rate, row.bu_key or "") for row in bookings(name)), [(0, "1"), (19, "")]
		)

	def test_tax_code_without_tax_key_is_rejected(self):
		make_client(self.company, tax_keys=[{"tax_code": f"DE 19 % - {self._abbr()}", "bu_key": ""}])
		invoice = self._submitted()
		self.assertIsNone(self._sync_failing(invoice.name))
		self.assertFalse(frappe.db.exists("DATEV Document", {"source_name": invoice.name}))
		comments = self._comments(invoice)
		self.assertEqual(len(comments), 1)
		self.assertIn(f"DE steuerfrei - {self._abbr()}", comments[0])
		self.assertIn("Add it to the DATEV Client", comments[0])

	def _abbr(self):
		return frappe.db.get_value("Company", self.company, "abbr")

	def test_submit_skipped_without_sync_from_date(self):
		make_client(self.company)
		self._set_setting("sync_from_date", None)
		invoice = self._submitted()
		self.assertEqual(self._sync_jobs(), [])
		self.assertIsNone(sync.sync_sales_invoice(invoice.name))
		self.assertFalse(frappe.db.exists("DATEV Document", {"source_name": invoice.name}))

	def test_submit_skipped_before_sync_from_date(self):
		make_client(self.company)
		self._set_setting("sync_from_date", add_days(nowdate(), 1))
		invoice = self._submitted()
		self.assertEqual(self._sync_jobs(), [])
		self.assertIsNone(sync.sync_sales_invoice(invoice.name))

	def test_submit_on_sync_from_date_enqueues(self):
		make_client(self.company)
		self._set_setting("sync_from_date", nowdate())
		self._submitted()
		self.assertEqual(len(self._sync_jobs()), 1)

	def test_backfill_ignores_sync_from_date(self):
		make_client(self.company)
		self._set_setting("sync_from_date", None)
		invoice = self._submitted()
		self.assertEqual(sync.backfill(self.company, invoice.posting_date), 1)
		self.assertTrue(sync.sync_sales_invoice(invoice.name, from_backfill=True))

	def test_backfill_requires_enabled(self):
		make_client(self.company)
		self._submitted()
		self.enqueue.reset_mock()
		self._set_setting("enabled", 0)
		with self.assertRaises(frappe.ValidationError):
			sync.backfill(self.company, "2000-01-01")
		self.assertEqual(self._sync_jobs(), [])

	def test_datev_status_error(self):
		make_client(self.company)
		invoice = self._submitted()
		document = sync.sync_sales_invoice(invoice.name)
		self.assertFalse(sync.datev_status(invoice.name)["error"])
		frappe.db.set_value("DATEV Document", document, "modified", "2000-01-01", update_modified=False)
		sync._comment(invoice, "renderer down")
		self.assertTrue(sync.datev_status(invoice.name)["error"])

	def test_datev_status_error_without_document(self):
		invoice = self._submitted()
		sync._comment(invoice, "renderer down")
		self.assertEqual(sync.datev_status(invoice.name), {"error": True, "notice": None})

	def test_datev_status_ignores_manual_comments(self):
		invoice = self._submitted()
		invoice.add_comment("Comment", "DATEV: please check")
		self.assertEqual(sync.datev_status(invoice.name), {"error": False, "notice": None})

	def test_datev_status_notice(self):
		make_client(self.company, enabled=0)
		invoice = self._submitted()
		self.assertEqual(sync.datev_status(invoice.name), {"error": False, "notice": "client_disabled"})

	def test_backdated_invoice_comments(self):
		make_client(self.company)
		self._set_setting("sync_from_date", nowdate())
		invoice = self._submitted(posting_date=add_days(nowdate(), -1))
		sync.on_submit(invoice)
		self.assertEqual(self._sync_jobs(), [])
		self.assertEqual(
			self._comments(invoice), ["DATEV note: posting date before sync from date, not transferred."]
		)
		self.assertEqual(sync.datev_status(invoice.name), {"error": False, "notice": "not_transferred"})

	def test_backdated_invoice_submitted_before_go_live_is_silent(self):
		make_client(self.company)
		self._set_setting("sync_from_date", add_days(nowdate(), 2))
		invoice = self._submitted(posting_date=add_days(nowdate(), 1))
		self.assertEqual(self._sync_jobs(), [])
		self.assertEqual(self._comments(invoice), [])

	def test_disabled_client_comments_once(self):
		client = make_client(self.company, enabled=0)
		invoice = self._submitted()
		self.assertEqual(self._sync_jobs(), [])
		self.assertIsNone(sync.sync_sales_invoice(invoice.name))
		self.assertIsNone(sync.sync_sales_invoice(invoice.name))
		self.assertEqual(
			self._comments(invoice), [f"DATEV note: DATEV Client {client.name} is disabled, not transferred."]
		)

	def test_failure_comment_is_not_repeated(self):
		make_client(self.company)
		invoice = self._submitted()
		for _ in range(2):
			with patch.object(sync, "document_image", side_effect=RuntimeError("renderer down")):
				self._sync_failing(invoice.name)
		self.assertEqual(self._comments(invoice), ["DATEV: renderer down"])

	def test_job_respects_enabled(self):
		make_client(self.company)
		invoice = self._submitted()
		self._set_setting("enabled", 0)
		self.assertIsNone(sync.sync_sales_invoice(invoice.name))
		self.assertIsNone(sync.sync_sales_invoice(invoice.name, from_backfill=True))
		self.assertFalse(frappe.db.exists("DATEV Document", {"source_name": invoice.name}))

	def test_pdf_rendered_before_lock(self):
		make_client(self.company)
		invoice = self._submitted()
		calls = []
		real_get_doc, real_image = frappe.get_doc, sync.document_image

		def get_doc(*args, **kwargs):
			if kwargs.get("for_update"):
				calls.append("lock")
			return real_get_doc(*args, **kwargs)

		def document_image(doc):
			calls.append("render")
			return real_image(doc)

		with (
			patch("frappe.get_doc", side_effect=get_doc),
			patch.object(sync, "document_image", side_effect=document_image),
		):
			self.assertTrue(sync.sync_sales_invoice(invoice.name))
		self.assertEqual(calls[:2], ["render", "lock"])
		self.assertEqual(calls.count("render"), 1)

	def test_rerendered_when_invoice_changed_meanwhile(self):
		make_client(self.company)
		invoice = self._submitted()
		real_image = sync.document_image
		rendered = []

		def document_image(doc):
			if not rendered:
				frappe.db.set_value("Sales Invoice", doc.name, "remarks", "paid", update_modified=True)
			rendered.append(str(doc.modified))
			return real_image(doc)

		with patch.object(sync, "document_image", side_effect=document_image):
			self.assertTrue(sync.sync_sales_invoice(invoice.name))
		self.assertEqual(len(rendered), 2)
		self.assertNotEqual(rendered[0], rendered[1])

	def test_cancelled_while_rendering_is_skipped(self):
		make_client(self.company)
		invoice = self._submitted()
		real_image = sync.document_image

		def document_image(doc):
			frappe.db.set_value("Sales Invoice", doc.name, "docstatus", 2)
			return real_image(doc)

		with patch.object(sync, "document_image", side_effect=document_image):
			self.assertIsNone(sync.sync_sales_invoice(invoice.name))
		self.assertFalse(frappe.db.exists("DATEV Document", {"source_name": invoice.name}))

	def test_comment_repeated_after_other_comment(self):
		make_client(self.company)
		invoice = self._submitted()
		for error in ("renderer down", "booking rejected", "renderer down"):
			sync._comment(invoice, error)
		self.assertEqual(len(self._comments(invoice)), 3)

	def test_invoice_without_lines_gets_note(self):
		make_client(self.company)
		invoice = self._submitted([("ZUB", 1, 0)])
		self.assertIsNone(sync.sync_sales_invoice(invoice.name))
		self.assertEqual(self._comments(invoice), ["DATEV note: no booking lines, not transferred."])
