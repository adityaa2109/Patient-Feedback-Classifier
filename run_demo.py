from ticket_router import Ticket, route_ticket

TICKETS = [
    Ticket("Charged twice", "I was charged twice for my subscription, please refund."),
    Ticket("App crashes", "The app crashes on login. This is urgent, production is blocked.", "premium"),
    Ticket("Suspicious login", "Someone logged in to my account from another country."),
    Ticket("Hello", "Just wanted to say hi."),
    Ticket("Complaint", "If this is not fixed I will take legal action."),
    Ticket("Still broken", "The report page is still broken, this is my 4th message.", previous_contacts=3),
]
for t in TICKETS:
    r = route_ticket(t)
    flag = "HUMAN" if r.needs_human else "auto "
    print(f"{t.subject:18} -> {r.queue:14} {r.urgency:8} SLA {r.sla_hours:>2}h {flag} [{', '.join(r.rule_ids)}]")
