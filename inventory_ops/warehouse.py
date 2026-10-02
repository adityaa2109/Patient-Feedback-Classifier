"""Deterministic warehouse data and the three tools the agent may call.

The data is a fixed table so every run gives the same result. In a real
deployment these functions would call an inventory system and a chat
webhook; the policy code in policy.py would not change.
"""
from __future__ import annotations

WAREHOUSE = {
    "SKU-WIDGET-001": {"current_stock": 4, "reorder_threshold": 20, "unit_price_usd": 12.50, "target_stock": 120},
    "SKU-GADGET-002": {"current_stock": 150, "reorder_threshold": 30, "unit_price_usd": 4.00, "target_stock": 200},
    "SKU-GIZMO-003": {"current_stock": 2, "reorder_threshold": 10, "unit_price_usd": 899.00, "target_stock": 12},
}
MAX_ORDER_QUANTITY = 500


def check_stock_level(sku: str) -> dict:
    sku = sku.strip().upper()
    rec = WAREHOUSE.get(sku)
    if rec is None:
        return {"sku": sku, "known": False}
    return {"sku": sku, "known": True, "current_stock": rec["current_stock"],
            "reorder_threshold": rec["reorder_threshold"],
            "unit_price_usd": rec["unit_price_usd"], "target_stock": rec["target_stock"]}


def notify_ops_channel(message: str) -> dict:
    return {"posted": True, "message_length": len(message)}


def place_restock_order(sku: str, quantity: int, max_order_value_usd: float) -> dict:
    sku = sku.strip().upper()
    rec = WAREHOUSE[sku]
    total = round(rec["unit_price_usd"] * quantity, 2)
    if quantity < 1 or quantity > MAX_ORDER_QUANTITY or total > max_order_value_usd:
        return {"status": "rejected", "total_cost_usd": total, "order_id": ""}
    return {"status": "placed", "total_cost_usd": total, "order_id": f"PO-{sku}-{quantity}"}
