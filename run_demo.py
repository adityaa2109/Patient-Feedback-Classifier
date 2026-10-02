from inventory_ops import run_task


def show(title, skus, budget):
    print(f"\n== {title} (budget ${budget:,.2f}) ==")
    for d in run_task(skus, budget):
        extra = f" Order {d.order_id}, ${d.total_cost_usd:.2f}." if d.order_id else ""
        print(f"{d.sku}: {d.action.upper()} - {d.reason}{extra}")


show("Routine check", ["SKU-WIDGET-001", "SKU-GADGET-002"], 2000.0)
show("Expensive item, small budget", ["SKU-GIZMO-003"], 1000.0)
show("Budget too small for one unit", ["SKU-GIZMO-003"], 100.0)
