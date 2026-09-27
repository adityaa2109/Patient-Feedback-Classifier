"""
core/tool_backends.py

The ACTUAL tool logic, implemented once. Every adapter (raw, LangChain,
CrewAI) wires its framework-specific tool wrapper to these same functions.
This is what makes cross-framework equivalence testable: if two adapters
call the same backend with the same args, the tool_call trace is
byte-for-byte comparable regardless of which framework produced it.

Deterministic on purpose (no randomness, no wall-clock) so verification
runs are reproducible.
"""

from __future__ import annotations


def search_flights(origin: str, destination: str, date: str) -> dict:
    origin = origin.strip().upper()
    destination = destination.strip().upper()
    base = (sum(map(ord, origin)) + sum(map(ord, destination))) % 400 + 120
    return {
        "flights": [
            {
                "flight_number": f"{origin[:2]}{destination[:2]}{100 + (base % 50)}",
                "price_usd": float(base),
                "departure_time": "09:15",
            },
            {
                "flight_number": f"{origin[:2]}{destination[:2]}{200 + (base % 50)}",
                "price_usd": float(base + 65),
                "departure_time": "18:40",
            },
        ]
    }


def search_hotels(city: str, check_in: str, check_out: str) -> dict:
    city_key = city.strip().lower()
    base = (sum(map(ord, city_key)) % 150) + 80
    return {
        "hotels": [
            {"name": f"{city.title()} Grand Hotel", "price_per_night_usd": float(base), "rating": 4.2},
            {"name": f"{city.title()} Budget Inn", "price_per_night_usd": float(base - 40 if base > 60 else 40), "rating": 3.6},
        ]
    }


# --- Inventory Ops Agent backends (second example agent) -------------------
# Deterministic warehouse simulation: fixed per-SKU stock/threshold/unit
# price table, so every runtime and every verification run sees identical
# results for identical inputs (no randomness, no wall-clock).

_WAREHOUSE_TABLE = {
    "SKU-WIDGET-001": {"current_stock": 4, "reorder_threshold": 20, "unit_price_usd": 12.50},
    "SKU-GADGET-002": {"current_stock": 150, "reorder_threshold": 30, "unit_price_usd": 4.00},
    "SKU-GIZMO-003": {"current_stock": 2, "reorder_threshold": 10, "unit_price_usd": 899.00},
}


def check_stock_level(item_sku: str) -> dict:
    record = _WAREHOUSE_TABLE.get(item_sku.strip().upper(), {"current_stock": 0, "reorder_threshold": 10, "unit_price_usd": 10.0})
    return {
        "item_sku": item_sku.strip().upper(),
        "current_stock": record["current_stock"],
        "reorder_threshold": record["reorder_threshold"],
    }


def notify_ops_channel(message: str) -> dict:
    # Deterministic no-op "post": in a real deployment this would call a
    # Slack/Teams webhook. Returns a fixed acknowledgement so runs are
    # reproducible without network access.
    return {"posted": True, "message_length": len(message)}


def place_restock_order(item_sku: str, quantity: int, max_order_value_usd: float) -> dict:
    sku = item_sku.strip().upper()
    unit_price = _WAREHOUSE_TABLE.get(sku, {"unit_price_usd": 10.0})["unit_price_usd"]
    total_cost = round(unit_price * quantity, 2)
    if total_cost > max_order_value_usd:
        return {"order_id": "", "total_cost_usd": total_cost, "status": "rejected_over_budget"}
    order_id = f"PO-{sku}-{quantity}"
    return {"order_id": order_id, "total_cost_usd": total_cost, "status": "placed"}


TOOL_REGISTRY = {
    "search_flights": search_flights,
    "search_hotels": search_hotels,
    "check_stock_level": check_stock_level,
    "notify_ops_channel": notify_ops_channel,
    "place_restock_order": place_restock_order,
}
