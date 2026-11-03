const DATEV_COLORS = {
	Transferred: "green",
	Exported: "green",
	Uploaded: "blue",
	"No Link": "orange",
	Pending: "orange",
	Batched: "orange",
	Stuck: "red",
	Failed: "red",
};

function datev_indicator(status) {
	if (status.error) return [__("DATEV: error"), "red"];
	if (status.notice === "client_disabled") return [__("DATEV: client disabled"), "orange"];
	if (status.notice === "waiting") return [__("DATEV: waiting"), "orange"];
	if (!status.document) return [__("DATEV: not transferred"), "grey"];
	const statuses = [status.document.status, ...status.bookings.map((booking) => booking.status)];
	for (const color of ["red", "orange"]) {
		const match = statuses.find((value) => DATEV_COLORS[value] === color);
		if (match) return [__("DATEV: {0}", [__(match)]), color];
	}
	const done = status.bookings.length && status.bookings.every((b) => DATEV_COLORS[b.status] === "green");
	if (done) return [__("DATEV: {0}", [__(status.bookings[0].status)]), "green"];
	return [__("DATEV: {0}", [__(status.document.status)]), DATEV_COLORS[status.document.status] || "grey"];
}

function datev_refresh(frm) {
	if (frm.doc.docstatus !== 1) return;
	frappe.call({
		method: "datev_erpnext.sync.datev_status",
		args: { name: frm.doc.name, doctype: frm.doctype },
		error: () => {},
		callback: ({ message }) => {
			if (message && message.hidden) return;
			frm.dashboard.add_indicator(...datev_indicator(message || {}));
		},
	});
}

if (!window.datev_erpnext_status_loaded) {
	window.datev_erpnext_status_loaded = true;
	for (const doctype of ["Sales Invoice", "Purchase Invoice", "Payment Entry", "Journal Entry"]) {
		frappe.ui.form.on(doctype, { refresh: datev_refresh });
	}
}
