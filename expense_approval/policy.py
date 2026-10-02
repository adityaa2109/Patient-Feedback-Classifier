"""Expense policy: fixed rules, no model, fully reproducible."""
from __future__ import annotations

from dataclasses import dataclass, field

CATEGORY_LIMITS_USD = {"meals": 75.0, "travel": 1500.0, "software": 500.0,
                       "office": 200.0, "training": 1000.0}
PROHIBITED = {"alcohol", "personal", "fines"}
RECEIPT_REQUIRED_ABOVE_USD = 25.0
AUTO_APPROVE_UP_TO_USD = 100.0
MONTHLY_CAP_USD = 3000.0


@dataclass
class Claim:
    employee: str
    category: str
    amount_usd: float
    date: str            # ISO date, e.g. "2026-10-02"
    vendor: str
    has_receipt: bool = True


@dataclass
class Decision:
    action: str          # "approve" | "reject" | "escalate"
    route: str           # "" | "manager" | "finance"
    reasons: list = field(default_factory=list)   # list of (rule_id, text)

    @property
    def rule_ids(self):
        return [r for r, _ in self.reasons]


def review_claim(claim: Claim, history: list[Claim] | None = None) -> Decision:
    """Rules run in this order; the first reject or escalate rule that fires
    decides the outcome, and the reasons list records every rule checked that
    fired. Order: R1 amount, R2 prohibited, R3 duplicate, R4 receipt,
    R5 unknown category, R6 category limit, R7 monthly cap, R8 auto-approve,
    R9 manager approval."""
    history = history or []
    cat = claim.category.strip().lower()
    amt = round(float(claim.amount_usd), 2)

    if amt <= 0:
        return Decision("reject", "", [("R1", "Amount must be greater than zero.")])
    if cat in PROHIBITED:
        return Decision("reject", "", [("R2", f"Category '{cat}' is not reimbursable.")])
    for past in history:
        if (past.employee == claim.employee and past.vendor.lower() == claim.vendor.lower()
                and round(past.amount_usd, 2) == amt and past.date == claim.date):
            return Decision("reject", "", [("R3", "Duplicate of an earlier claim (same employee, vendor, amount, date).")])
    if amt > RECEIPT_REQUIRED_ABOVE_USD and not claim.has_receipt:
        return Decision("reject", "", [("R4", f"Receipt required above ${RECEIPT_REQUIRED_ABOVE_USD:.2f}; resubmit with a receipt.")])
    if cat not in CATEGORY_LIMITS_USD:
        return Decision("escalate", "manager", [("R5", f"Unknown category '{cat}'; a person must classify it.")])

    limit = CATEGORY_LIMITS_USD[cat]
    if amt > limit:
        return Decision("escalate", "finance", [("R6", f"${amt:.2f} exceeds the {cat} limit of ${limit:.2f}.")])

    month = claim.date[:7]
    spent = sum(p.amount_usd for p in history if p.employee == claim.employee and p.date[:7] == month)
    if spent + amt > MONTHLY_CAP_USD:
        return Decision("escalate", "finance", [("R7", f"Monthly total would be ${spent + amt:.2f}, above the ${MONTHLY_CAP_USD:.2f} cap.")])

    if amt <= AUTO_APPROVE_UP_TO_USD:
        return Decision("approve", "", [("R8", f"${amt:.2f} is within the ${AUTO_APPROVE_UP_TO_USD:.2f} auto-approval range and passed every check.")])
    return Decision("escalate", "manager", [("R9", f"${amt:.2f} is above ${AUTO_APPROVE_UP_TO_USD:.2f}; a manager must approve.")])
