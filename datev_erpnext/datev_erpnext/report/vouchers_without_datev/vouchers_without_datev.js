frappe.query_reports["Vouchers without DATEV"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
		},
		{
			fieldname: "voucher_type",
			label: __("Voucher Type"),
			fieldtype: "Select",
			options: ["", "Sales Invoice", "Purchase Invoice", "Payment Entry", "Journal Entry"],
		},
	],
};
