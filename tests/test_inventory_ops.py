import os, re
from inventory_ops import run_task
from inventory_ops.policy import plan_restock, MAX_TOOL_CALLS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ST = lambda cur, thr, price, tgt: {"known": True, "current_stock": cur, "reorder_threshold": thr,
                                   "unit_price_usd": price, "target_stock": tgt}

def test_healthy_stock_means_no_action():
    assert plan_restock(ST(150, 30, 4.0, 200), 1000)[0] == "no_action"

def test_low_stock_orders_full_refill_when_affordable():
    action, qty, _ = plan_restock(ST(4, 20, 12.5, 120), 5000)
    assert (action, qty) == ("order", 116)

def test_budget_limits_quantity():
    action, qty, _ = plan_restock(ST(4, 20, 12.5, 120), 500)
    assert (action, qty) == ("order", 40)

def test_budget_too_small_escalates():
    assert plan_restock(ST(2, 10, 899.0, 12), 100)[0] == "escalate"

def test_unknown_sku_escalates():
    assert plan_restock({"known": False}, 1000)[0] == "escalate"

def test_quantity_never_exceeds_order_cap():
    _, qty, _ = plan_restock(ST(0, 10, 1.0, 9999), 1e9)
    assert qty == 500

def test_order_is_always_preceded_by_check_and_notify():
    log = run_task(["SKU-WIDGET-001"], 2000.0)[0].audit_log
    assert [c["tool"] for c in log] == ["check_stock_level", "notify_ops_channel", "place_restock_order"]

def test_never_spends_over_budget():
    for d in run_task(["SKU-WIDGET-001", "SKU-GIZMO-003"], 2000.0):
        assert d.total_cost_usd <= 2000.0

def test_same_sku_not_ordered_twice():
    ds = run_task(["SKU-WIDGET-001", "SKU-WIDGET-001"], 2000.0)
    assert [d.action for d in ds] == ["ordered", "no_action"]

def test_tool_call_limit_respected():
    ds = run_task(["SKU-WIDGET-001", "SKU-GIZMO-003", "SKU-GADGET-002"], 5000.0)
    assert len(ds[-1].audit_log) <= MAX_TOOL_CALLS

def test_results_are_deterministic():
    a = [d.__dict__ for d in run_task(["SKU-WIDGET-001"], 2000.0)]
    b = [d.__dict__ for d in run_task(["SKU-WIDGET-001"], 2000.0)]
    assert a == b

def test_agent_yaml_and_soul_exist():
    text = open(os.path.join(ROOT, "agent.yaml")).read()
    assert 'spec_version: "0.1.0"' in text
    assert re.search(r"(?m)^name: [a-z][a-z0-9-]*$", text)
    assert os.path.getsize(os.path.join(ROOT, "SOUL.md")) > 500

def test_explainability_headings_and_sentences():
    text = open(os.path.join(ROOT, "EXPLAINABILITY.md")).read()
    parts = re.split(r"(?m)^(#{1,6})\s+(.*)$", text)
    groups = {"decision": ["decision", "reasoning", "how it decides"],
              "inputs": ["data source", "input", "data used"],
              "limits": ["limitation", "constraint", "known issue"]}
    found = {k: False for k in groups}
    for i in range(1, len(parts) - 2, 3):
        level, title, body = parts[i], parts[i + 1].lower(), parts[i + 2]
        if level != "#":
            continue
        for k, words in groups.items():
            if any(w in title for w in words):
                sents = [s for s in re.split(r"(?<=[.!?])\s+", body.strip()) if len(s.split()) > 3]
                assert len(sents) >= 2, title
                found[k] = True
    assert all(found.values()), found
