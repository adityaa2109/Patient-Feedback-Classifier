# Support Ticket Router

A small, rule-based agent that reads a customer support ticket and returns a
**queue** (billing, tech support, account, security, or human triage), an
**urgency** (low, normal, high, critical), a **response deadline** (1, 4, 24,
or 72 hours), and a flag for human handling. Every result lists the rule
identifiers that produced it.

Safety, legal, and privacy-sensitive tickets always go to a person. Tickets
the rules cannot classify, or where two categories tie, also go to a person
instead of a guess.

## Run it

```
pip install -r requirements.txt
python run_demo.py
python -m pytest tests -v
```

## Files

- `agent.yaml`, `SOUL.md`, `EXPLAINABILITY.md`: the OpenGAP agent description.
- `ticket_router/router.py`: the rules, keyword lists, and the `route_ticket` function.
- `tests/`: automated tests for every rule, the boundaries, and the documents.

## Honest scope

This is keyword matching. It cannot understand tone, sarcasm, or negation,
and its keyword lists are examples. It supports a support team and does not
replace human judgment, especially for sensitive messages.
