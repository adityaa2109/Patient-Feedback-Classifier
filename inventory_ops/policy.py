"""The decision policy: rules only, no model, fully reproducible."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import warehouse

MAX_TOOL_CALLS = 6


@dataclass
class Decision:
    sku: str
    action: str              # "no_action" | "ordered" | "escalate"
    reason: str
    quantity: int = 0
    total_cost_usd: float = 0.0
    order_id: str = ""
    audit_log: list = field(default_factory=list)


def plan_restock(stock: dict, budget_usd: float) -> tuple[str, int, str]:
    """Pure function: returns (action, quantity, reason) from a stock record.

    quantity = min(target_stock - current_stock, MAX_ORDER_QUANTITY,
                   floor(budget / unit_price))
    """
    if not stock["known"]:
        return "escalate", 0, "SKU is not in the warehouse table; a human must review it."
    if stock["current_stock"] >= stock["reorder_threshold"]:
        return "no_action", 0, (f"Stock {stock['current_stock']} is at or above the reorder "
                                f"threshold {stock['reorder_threshold']}.")
    wanted = stock["target_stock"] - stock["current_stock"]
    affordable = math.floor(budget_usd / stock["unit_price_usd"])
    qty = min(wanted, warehouse.MAX_ORDER_QUANTITY, affordable)
    if qty < 1:
        return "escalate", 0, (f"Budget ${budget_usd:.2f} cannot buy one unit at "
                               f"${stock['unit_price_usd']:.2f}; a human must decide.")
    note = "full refill" if qty == wanted else "partial refill limited by budget or order cap"
    return "order", qty, (f"Stock {stock['current_stock']} is below threshold "
                          f"{stock['reorder_threshold']}; ordering {qty} units ({note}).")


def run_task(skus: list[str], budget_usd: float) -> list[Decision]:
    """Handle several SKUs in one task, enforcing every rule in SOUL.md."""
    log: list = []
    decisions: list[Decision] = []
    ordered: set = set()
    calls = 0

    def call(name, fn, *args):
        nonlocal calls
        if calls >= MAX_TOOL_CALLS:
            raise RuntimeError("max tool calls per task exceeded")
        calls += 1
        result = fn(*args)
        log.append({"tool": name, "args": list(args), "result": result})
        return result

    for sku in skus:
        sku = sku.strip().upper()
        if calls + 1 > MAX_TOOL_CALLS:
            decisions.append(Decision(sku, "escalate", "Tool-call limit reached; a human must continue.", audit_log=log))
            continue
        stock = call("check_stock_level", warehouse.check_stock_level, sku)
        action, qty, reason = plan_restock(stock, budget_usd)
        if action == "order" and sku in ordered:
            decisions.append(Decision(sku, "no_action", "Already ordered this SKU in this task.", audit_log=log))
            continue
        if action != "order":
            decisions.append(Decision(sku, action, reason, audit_log=log))
            continue
        if calls + 2 > MAX_TOOL_CALLS:
            decisions.append(Decision(sku, "escalate", "Not enough tool calls left to notify and order.", audit_log=log))
            continue
        call("notify_ops_channel", warehouse.notify_ops_channel, f"Restocking {sku}: {reason}")
        res = call("place_restock_order", warehouse.place_restock_order, sku, qty, budget_usd)
        if res["status"] == "placed":
            ordered.add(sku)
            decisions.append(Decision(sku, "ordered", reason, qty, res["total_cost_usd"], res["order_id"], log))
        else:
            decisions.append(Decision(sku, "escalate", "Order was rejected by the warehouse system.", audit_log=log))
    return decisions
