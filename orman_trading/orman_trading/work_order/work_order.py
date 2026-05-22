import frappe


def _logger():
	return frappe.logger("orman_reserved_qty", allow_site=True, max_size=1, file_count=1)


@frappe.whitelist()
def get_reserved_qty_for_work_order(sales_order, production_item, sales_order_item=None, work_order_qty=None):
	"""
	Return stock_reserved_qty for the Sales Order Item that matches this Work Order.

	Match priority:
	  1. Exact SO Item row by sales_order_item name — used when WO is created via Production Plan.
	  2. item_code + qty match — distinguishes duplicate items in the SO with different quantities.
	  3. item_code only — fallback when qty differs or is unavailable.
	"""
	_logger().info(
		f"[ReservedQty] called | sales_order={sales_order} | production_item={production_item}"
		f" | sales_order_item={sales_order_item} | work_order_qty={work_order_qty}"
	)

	# Priority 1: exact SO Item row
	if sales_order_item:
		result = frappe.db.get_value(
			"Sales Order Item",
			sales_order_item,
			["reserve_stock", "stock_reserved_qty"],
			as_dict=True,
		)
		_logger().info(f"[ReservedQty] direct SO Item lookup → {result}")
		if result and result.reserve_stock:
			_logger().info(f"[ReservedQty] returning via direct match: {result.stock_reserved_qty}")
			return result.stock_reserved_qty or 0

	if not (sales_order and production_item):
		_logger().info("[ReservedQty] no match found — returning 0")
		return 0

	# Priority 2: item_code + qty — handles the same item appearing multiple times in the SO
	if work_order_qty:
		rows = frappe.get_all(
			"Sales Order Item",
			filters={
				"parent": sales_order,
				"item_code": production_item,
				"qty": float(work_order_qty),
				"reserve_stock": 1,
			},
			fields=["name", "qty", "stock_reserved_qty"],
			limit=1,
		)
		_logger().info(f"[ReservedQty] item_code+qty match → {rows}")
		if rows:
			_logger().info(f"[ReservedQty] returning via qty match: {rows[0].stock_reserved_qty}")
			return rows[0].stock_reserved_qty or 0

	# Priority 3: item_code only fallback
	rows = frappe.get_all(
		"Sales Order Item",
		filters={"parent": sales_order, "item_code": production_item, "reserve_stock": 1},
		fields=["name", "qty", "stock_reserved_qty"],
		limit=1,
	)
	_logger().info(f"[ReservedQty] item_code-only fallback → {rows}")
	if rows:
		_logger().info(f"[ReservedQty] returning via item_code fallback: {rows[0].stock_reserved_qty}")
		return rows[0].stock_reserved_qty or 0

	_logger().info("[ReservedQty] no match found — returning 0")
	return 0


@frappe.whitelist()
def get_so_items_for_item(sales_order, production_item, current_work_order=None):
	"""
	Return SO Item rows not yet precisely linked to another Work Order.
	Only rows whose exact name is stored on another WO's sales_order_item are excluded.
	Old WOs (sales_order_item = NULL) are not filtered out here — the before_save hook
	progressively sets sales_order_item so this filter improves over time.
	"""
	all_rows = frappe.get_all(
		"Sales Order Item",
		filters={"parent": sales_order, "item_code": production_item},
		fields=["name", "idx", "qty", "stock_reserved_qty", "reserve_stock", "custom_location", "delivery_date"],
		order_by="idx asc",
	)

	if not all_rows:
		return []

	row_names = [r.name for r in all_rows]

	wo_filters = {
		"sales_order_item": ["in", row_names],
		"sales_order": sales_order,
		"docstatus": ["!=", 2],
	}
	if current_work_order:
		wo_filters["name"] = ["!=", current_work_order]

	already_linked = frappe.get_all("Work Order", filters=wo_filters, fields=["sales_order_item"])
	linked_row_names = {wo.sales_order_item for wo in already_linked}

	_logger().info(f"[ReservedQty] all rows: {row_names} | precisely linked: {linked_row_names}")
	return [r for r in all_rows if r.name not in linked_row_names]


def update_work_order_reserved_qty(doc, method=None):
	"""Populate sales_order_item (if missing) and custom_stock_reserved_qty on save."""
	_logger().info(f"[ReservedQty] before_save triggered for Work Order: {doc.name}")

	# Auto-assign sales_order_item server-side when it wasn't set by the client.
	# This makes Pass 1 filtering progressively more accurate with each save.
	if doc.sales_order and doc.production_item and not doc.sales_order_item:
		_auto_assign_so_item(doc)

	doc.custom_stock_reserved_qty = get_reserved_qty_for_work_order(
		doc.sales_order, doc.production_item, doc.sales_order_item, doc.qty
	)
	_logger().info(f"[ReservedQty] sales_order_item={doc.sales_order_item} | custom_stock_reserved_qty={doc.custom_stock_reserved_qty}")


def _auto_assign_so_item(doc):
	"""Best-effort: link sales_order_item from SO rows not yet taken by another WO."""
	all_rows = frappe.get_all(
		"Sales Order Item",
		filters={"parent": doc.sales_order, "item_code": doc.production_item},
		fields=["name", "qty"],
		order_by="idx asc",
	)
	if not all_rows:
		return

	row_names = [r.name for r in all_rows]

	taken = {
		wo.sales_order_item
		for wo in frappe.get_all(
			"Work Order",
			filters={
				"sales_order_item": ["in", row_names],
				"sales_order": doc.sales_order,
				"docstatus": ["!=", 2],
				"name": ["!=", doc.name],
			},
			fields=["sales_order_item"],
		)
	}

	available = [r for r in all_rows if r.name not in taken]
	if not available:
		return

	# Prefer an exact qty match; otherwise take the first available row
	match = next((r for r in available if abs(float(r.qty or 0) - float(doc.qty or 0)) < 0.001), available[0])
	doc.sales_order_item = match.name
	_logger().info(f"[ReservedQty] auto-assigned sales_order_item={doc.sales_order_item}")


def backfill_reserved_qty():
	"""One-time fix: update custom_stock_reserved_qty on all Work Orders that have a Sales Order."""
	work_orders = frappe.get_all(
		"Work Order",
		filters={"sales_order": ["!=", ""]},
		fields=["name", "sales_order", "production_item", "sales_order_item", "qty"],
	)

	updated = 0
	for wo in work_orders:
		qty = get_reserved_qty_for_work_order(
			wo.sales_order, wo.production_item, wo.sales_order_item, wo.qty
		)
		frappe.db.set_value("Work Order", wo.name, "custom_stock_reserved_qty", qty)
		updated += 1

	frappe.db.commit()
	print(f"Updated {updated} Work Orders.")
