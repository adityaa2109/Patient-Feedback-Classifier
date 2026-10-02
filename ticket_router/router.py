"""Rule-based support ticket router. No model; same ticket, same result."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

LEVELS = ["low", "normal", "high", "critical"]
SLA_HOURS = {"critical": 1, "high": 4, "normal": 24, "low": 72}
QUEUES = {"billing": "billing-team", "technical": "tech-support",
          "account": "account-team", "security": "security-team"}

# T1: always goes to a person, whatever else the ticket says.
HUMAN_ONLY = ["hurt myself", "kill myself", "suicide", "self harm", "self-harm",
              "lawyer", "attorney", "sue you", "legal action", "lawsuit",
              "data breach", "gdpr request", "press", "journalist"]

# T2: security signals.
SECURITY = ["hacked", "unauthorized", "unauthorised", "phishing", "stolen",
            "compromised", "someone logged in", "suspicious login", "fraud"]

# T3: category keyword weights.
CATEGORY_WORDS = {
    "billing": {"invoice": 2, "refund": 3, "charged": 3, "charge": 2, "payment": 2,
                "billing": 3, "subscription": 2, "receipt": 1, "price": 1, "overcharged": 3},
    "technical": {"error": 2, "bug": 2, "crash": 3, "crashes": 3, "not working": 3,
                  "broken": 2, "slow": 1, "timeout": 2, "outage": 3, "api": 1, "loading": 1},
    "account": {"password": 3, "login": 2, "log in": 2, "email address": 2, "username": 2,
                "reset": 2, "profile": 1, "delete my account": 3, "locked out": 3, "sign in": 2},
}

# T5: urgency signals.
RAISE_TO_HIGH = ["urgent", "asap", "immediately", "cannot access", "can't access",
                 "production", "down for", "blocked", "deadline"]
RAISE_TO_CRITICAL = ["outage", "all users", "everyone is", "data loss", "lost all"]
LOWER_TO_LOW = ["thank you", "thanks", "how do i", "just a question", "feature request", "suggestion"]


@dataclass
class Ticket:
    subject: str
    body: str
    customer_tier: str = "standard"      # "standard" | "premium"
    previous_contacts: int = 0           # earlier tickets on the same issue


@dataclass
class Routing:
    category: str
    queue: str
    urgency: str
    sla_hours: int
    needs_human: bool
    reasons: list = field(default_factory=list)   # list of (rule_id, text)

    @property
    def rule_ids(self):
        return [r for r, _ in self.reasons]


def _has(text: str, phrase: str) -> bool:
    return re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", text) is not None


def _bump(level: str, steps: int) -> str:
    return LEVELS[max(0, min(len(LEVELS) - 1, LEVELS.index(level) + steps))]


def route_ticket(ticket: Ticket) -> Routing:
    text = f"{ticket.subject} {ticket.body}".lower()
    reasons: list = []

    # T1: sensitive content -> human, critical, nothing else matters.
    if any(_has(text, p) for p in HUMAN_ONLY):
        return Routing("sensitive", "human-triage", "critical", SLA_HOURS["critical"], True,
                       [("T1", "Ticket contains safety, legal, or privacy-sensitive content; a person must handle it.")])

    # T2 / T3: category.
    if any(_has(text, p) for p in SECURITY):
        category = "security"
        reasons.append(("T2", "Ticket reports a possible account or payment security problem."))
    else:
        scores = {c: sum(w for p, w in words.items() if _has(text, p)) for c, words in CATEGORY_WORDS.items()}
        best = max(scores.values())
        winners = [c for c, s in scores.items() if s == best]
        if best == 0:
            return Routing("unclear", "human-triage", "normal", SLA_HOURS["normal"], True,
                           [("T4", "No category keywords matched; a person must triage it.")])
        if len(winners) > 1:
            return Routing("unclear", "human-triage", "normal", SLA_HOURS["normal"], True,
                           [("T4", f"Tie between {', '.join(sorted(winners))}; a person must triage it.")])
        category = winners[0]
        reasons.append(("T3", f"Best category is {category} with score {best}."))

    # T5: urgency from wording.
    urgency = "normal"
    if any(_has(text, p) for p in LOWER_TO_LOW):
        urgency = "low"
        reasons.append(("T5", "Wording suggests a question or feedback, not a problem."))
    if any(_has(text, p) for p in RAISE_TO_HIGH):
        urgency = "high"
        reasons.append(("T5", "Wording signals blocked work or a deadline."))
    if any(_has(text, p) for p in RAISE_TO_CRITICAL):
        urgency = "critical"
        reasons.append(("T5", "Wording signals an outage or data loss."))
    if category == "security" and LEVELS.index(urgency) < LEVELS.index("high"):
        urgency = "high"
        reasons.append(("T5", "Security tickets are at least high urgency."))

    # T6 / T7: customer-based boosts.
    if ticket.customer_tier.strip().lower() == "premium":
        urgency = _bump(urgency, 1)
        reasons.append(("T6", "Premium customer: urgency raised one level."))
    if ticket.previous_contacts >= 3:
        urgency = _bump(urgency, 1)
        reasons.append(("T7", "Third or later contact about this issue: urgency raised one level."))

    return Routing(category, QUEUES[category], urgency, SLA_HOURS[urgency], False, reasons)
