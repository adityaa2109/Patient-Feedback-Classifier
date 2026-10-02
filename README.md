# Expense Approval Agent

A small, rule-based agent that reviews one employee expense claim and returns
**approve**, **reject**, or **escalate** (to a manager or to finance). Each
decision lists the rule identifiers that caused it and a plain-language reason.

It checks, in order: invalid amounts, prohibited categories, duplicates,
missing receipts, unknown categories, category limits, a monthly spending cap,
and then either auto-approves small claims or sends larger ones to a manager.

## Run it

```
pip install -r requirements.txt
python run_demo.py
python -m pytest tests -v
```

## Files

- `agent.yaml`, `SOUL.md`, `EXPLAINABILITY.md`: the OpenGAP agent description.
- `expense_approval/policy.py`: all rules, limits, and the `review_claim` function.
- `tests/`: 16 tests covering every rule, the boundaries, and the documents.

## Honest scope

The limits are example values and the agent cannot verify receipts. It is a
clear, auditable example of policy-based decisions, not a production finance
system.
