# Explainability

Inventory Ops Agent decides whether warehouse items need restocking and
places budget-limited orders. This report explains how it decides, what data
it uses, and where it should not be trusted. Every claim points to real code
in `inventory_ops/policy.py` or `inventory_ops/warehouse.py`.

# Decision and Reasoning: How It Decides

This section explains how the agent decides whether to order, do nothing, or
hand the case to a human. The decision is made by fixed rules, not by a
model, so the same inputs always give the same result. Every decision comes
back with a plain-language reason and an audit log of the tool calls made.

## The rules

The function `plan_restock` in `inventory_ops/policy.py` applies these rules
in order:

1. If the SKU is not in the warehouse table, the agent escalates to a human.
2. If `current_stock >= reorder_threshold`, the agent takes no action.
3. Otherwise it computes the quantity to order (formula below).
4. If that quantity is below 1, the agent escalates because the budget cannot
   buy a single unit.
5. Otherwise it notifies the ops channel and then places the order.

## The formula

`quantity = min(target_stock - current_stock, 500, floor(budget / unit_price))`

The first term refills toward the target, the second is the hard order cap,
and the third is the most the budget can afford. For example, SKU-WIDGET-001
has stock 4, target 120, and unit price $12.50. With a $2,000 budget the
quantity is `min(116, 500, 160) = 116`, which costs $1,450.00.

## Safeguards in the order of calls

The agent always calls `check_stock_level` first, then `notify_ops_channel`,
then `place_restock_order`. It never orders the same SKU twice in one task and
never makes more than six tool calls. A test in `tests/test_inventory_ops.py`
checks each of these rules.

# Inputs and Data Sources: Data Used

This section lists what the agent takes in and where its data comes from.
The inputs are a list of SKUs and a per-order budget in US dollars. The data
used is a fixed in-memory warehouse table, with no network calls and no
personal data.

## Inputs

A task is a list of SKU strings and one number, `budget_usd`. SKUs are
trimmed and upper-cased before use. No free text is interpreted, so there is
no prompt to inject into.

## Data sources

The warehouse table in `inventory_ops/warehouse.py` holds, for each of three
SKUs, the current stock, reorder threshold, unit price, and target stock. The
values are fixed example data, not a live inventory feed. A real deployment
would replace the three tool functions with calls to an inventory system and
a chat webhook, and the policy code would not change.

## Data lineage

Data moves in one direction: task input → `check_stock_level` → `plan_restock`
→ optional `notify_ops_channel` → optional `place_restock_order` → a
`Decision` with its audit log. Nothing is stored between tasks.

# Limitations, Constraints and Known Issues

This section states where the agent should not be trusted. It uses fixed
example data, so its stock numbers are not real. The rules are simple on
purpose, which makes them easy to audit but means they miss anything outside
the rules.

## Known limitations

- The warehouse data is a fixed example table, not a live system, so the
  agent shows decision logic and not real inventory accuracy.
- The agent does not forecast demand, lead times, or seasonality. It reacts
  only to the reorder threshold.
- The order cap of 500 units and the six-call limit are fixed constants, not
  tuned to any real warehouse.
- Prices are fixed. A real price change would not be noticed.
- Unknown SKUs are escalated, but the agent cannot tell a typo from a new
  product.

## Constraints that are enforced

The agent never exceeds the budget, never orders without a prior stock check
and notification, never double-orders a SKU in one task, and never makes more
than six tool calls. These are checked by automated tests.

## Safety and human oversight

The agent is meant to support a human, not replace one. It tells the ops
channel before every order and escalates instead of guessing whenever a case
falls outside its rules. It should not be used for high-value purchasing
without a human approval step.
