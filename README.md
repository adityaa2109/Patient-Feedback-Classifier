# Inventory Ops Agent

A small, rule-based warehouse agent. For each SKU it checks stock, and if
stock is below the reorder threshold it works out a quantity that stays
inside a per-order budget, notifies a human ops channel, and then places the
order. When the case falls outside its rules it escalates instead of guessing.

This is the standalone version of the "Inventory Ops" example agent from my
Agent Passport project (`adityaa2109/agent-passport`). That repository verifies
agent manifests across frameworks. This repository is the agent itself, with
its own policy code, tests, and documentation.

## Run it

```
pip install -r requirements.txt
python run_demo.py
python -m pytest tests -v
```

## Files

- `agent.yaml`, `SOUL.md`, `EXPLAINABILITY.md`: the OpenGAP agent description.
- `inventory_ops/warehouse.py`: the three tools and a fixed example warehouse.
- `inventory_ops/policy.py`: the decision rules and the audit log.
- `tests/`: 13 tests covering the rules, the budget, ordering, and docs.

## Honest scope

The warehouse data is fixed example data and there is no live inventory
connection. The agent demonstrates safe, auditable decision logic, not a
production purchasing system.
