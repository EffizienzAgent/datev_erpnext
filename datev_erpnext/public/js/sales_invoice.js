const DATEV_COLORS = {
	Transferred: "green",
	Exported: "green",
	Pending: "orange",
	Batched: "orange",
	Stuck: "red",
	Failed: "red",
};

function datev_indicator(status) {
	if (!status.document) return [__("DATEV: not transferred"), "grey"];
	const statuses = [status.document.status, ...status.bookings.map((booking) => booking.status)];
	for (const color of ["red", "orange"]) {
		const match = statuses.find((value) => DATEV_COLORS[value] === color);
		if (match) return [__("DATEV: {0}", [__(match)]), color];
	}
	const done = status.bookings.length && status.bookings.every((b) => DATEV_COLORS[b.status] === "green");
	if (done) return [__("DATEV: {0}", [__(status.bookings[0].status)]), "green"];
	return [__("DATEV: {0}", [__(status.document.status)]), "grey"];
}

frappe.ui.form.on("Sales Invoice", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1) return;
		frappe
			.call("datev_erpnext.sync.datev_status", { name: frm.doc.name })
			.then(({ message }) => frm.dashboard.add_indicator(...datev_indicator(message || {})));
	},
});
