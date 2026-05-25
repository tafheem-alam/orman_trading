frappe.ui.form.on('Work Order', {
	refresh(frm) {
		console.log('[ReservedQty] refresh triggered', {
			sales_order: frm.doc.sales_order,
			production_item: frm.doc.production_item,
			sales_order_item: frm.doc.sales_order_item,
		});
		// On load of an existing doc just show the saved value — don't mutate sales_order_item
		set_reserved_qty(frm, 'refresh');
	},

	sales_order(frm) {
		console.log('[ReservedQty] sales_order changed →', frm.doc.sales_order);
		// SO changed — clear the previous SO item link, then re-resolve
		frappe.model.set_value(frm.doctype, frm.docname, 'sales_order_item', null);
		resolve_so_item_and_set_qty(frm);
	},

	production_item(frm) {
		console.log('[ReservedQty] production_item changed →', frm.doc.production_item);
		frappe.model.set_value(frm.doctype, frm.docname, 'sales_order_item', null);
		resolve_so_item_and_set_qty(frm);
	},

	sales_order_item(frm) {
		console.log('[ReservedQty] sales_order_item changed →', frm.doc.sales_order_item);
		set_reserved_qty(frm, 'sales_order_item');
	},

	qty(frm) {
		console.log('[ReservedQty] qty changed →', frm.doc.qty);
		// Qty changed — try to re-resolve SO item automatically
		if (frm.doc.sales_order && frm.doc.production_item) {
			resolve_so_item_and_set_qty(frm);
		}
	},
});

/**
 * Fetch all SO item rows for this item, then:
 *   1 row   → auto-link it
 *   multiple rows, qtys differ → auto-link the one whose qty matches frm.doc.qty
 *   multiple rows, qtys same  → show a selection dialog
 */
function resolve_so_item_and_set_qty(frm) {
	if (!frm.doc.sales_order || !frm.doc.production_item) {
		display_reserved_qty(frm, 0);
		return;
	}

	frappe.call({
		method: 'orman_trading.orman_trading.work_order.work_order.get_so_items_for_item',
		args: {
			sales_order: frm.doc.sales_order,
			production_item: frm.doc.production_item,
			current_work_order: frm.doc.name || null,
		},
		callback(r) {
			const rows = r.message || [];
			console.log('[ReservedQty] SO item rows:', rows);

			if (rows.length === 0) {
				// All rows are precisely linked to other WOs — fall back to qty-based lookup
				// so the reserved qty is still shown (handles old WOs without sales_order_item)
				set_reserved_qty(frm, 'fallback');
				return;
			}

			if (rows.length === 1) {
				link_so_item(frm, rows[0]);
				return;
			}

			// Multiple rows — try to auto-match by WO qty (handles different qty per row)
			if (frm.doc.qty) {
				const qty_matches = rows.filter(row => Math.abs(row.qty - frm.doc.qty) < 0.001);
				console.log('[ReservedQty] qty-based matches:', qty_matches);
				if (qty_matches.length === 1) {
					link_so_item(frm, qty_matches[0]);
					return;
				}
			}

			// Still ambiguous (same qty on multiple rows) — ask the user
			prompt_so_item_selection(frm, rows);
		},
	});
}

/** Properly link the SO item row and update the display. */
function link_so_item(frm, row) {
	console.log('[ReservedQty] linking SO item row:', row.name, '| reserved:', row.stock_reserved_qty);
	// Use frappe.model.set_value so the field is saved when the form is submitted
	frappe.model.set_value(frm.doctype, frm.docname, 'sales_order_item', row.name);
	display_reserved_qty(frm, row.reserve_stock ? (row.stock_reserved_qty || 0) : 0);
}

/** Show a dialog when multiple rows with the same qty exist. */
function prompt_so_item_selection(frm, rows) {
	const options = rows.map((row) => {
		const location = row.location || '—';
		const parts = [
			`Row ${row.idx}`,
			`Qty: ${row.qty}`,
			`Location: ${location}`,
			`Reserved: ${row.stock_reserved_qty || 0}`,
		];
		if (row.delivery_date) parts.push(`Delivery: ${row.delivery_date}`);
		return { label: parts.join(' | '), value: row.name, row };
	});

	frappe.prompt(
		[{
			fieldtype: 'Select',
			fieldname: 'so_item',
			label: __('Select Sales Order Item Row'),
			options: options.map(o => o.label).join('\n'),
			reqd: 1,
		}],
		(values) => {
			const selected = options.find(o => o.label === values.so_item);
			if (!selected) return;
			console.log('[ReservedQty] user selected:', selected.value);
			link_so_item(frm, selected.row);
		},
		__('Multiple rows found — select the correct Sales Order item'),
		__('Confirm')
	);
}

/** Plain reserved qty lookup — used on refresh (no SO item mutation). */
function set_reserved_qty(frm, trigger) {
	if (!frm.doc.sales_order || !frm.doc.production_item) {
		console.log('[ReservedQty] skipped — missing fields', { trigger });
		display_reserved_qty(frm, 0);
		return;
	}

	const args = {
		sales_order: frm.doc.sales_order,
		production_item: frm.doc.production_item,
		sales_order_item: frm.doc.sales_order_item || null,
		work_order_qty: frm.doc.qty || null,
	};
	console.log('[ReservedQty] calling API', { trigger, args });

	frappe.call({
		method: 'orman_trading.orman_trading.work_order.work_order.get_reserved_qty_for_work_order',
		args,
		callback(r) {
			console.log('[ReservedQty] API response', { trigger, raw: r.message });
			display_reserved_qty(frm, r.message || 0);
		},
	});
}

function display_reserved_qty(frm, value) {
	console.log('[ReservedQty] setting display value →', value);
	frm.doc.custom_stock_reserved_qty = value;
	frm.refresh_field('custom_stock_reserved_qty');
}
