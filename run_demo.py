from expense_approval import Claim, review_claim

history = [Claim("asha", "meals", 40.0, "2026-10-01", "Cafe Nero")]
claims = [
    Claim("asha", "meals", 40.0, "2026-10-01", "Cafe Nero"),       # duplicate
    Claim("ravi", "software", 60.0, "2026-10-02", "Notion"),       # auto-approve
    Claim("ravi", "travel", 800.0, "2026-10-02", "IndiGo"),        # manager
    Claim("ravi", "travel", 2400.0, "2026-10-02", "IndiGo"),       # over limit
    Claim("meera", "alcohol", 50.0, "2026-10-02", "Pub"),          # prohibited
    Claim("meera", "office", 80.0, "2026-10-02", "Stationery", has_receipt=False),  # no receipt
]
for c in claims:
    d = review_claim(c, history)
    print(f"{c.employee:6} {c.category:9} ${c.amount_usd:>7.2f} -> {d.action.upper():8} "
          f"{d.route:8} [{', '.join(d.rule_ids)}] {d.reasons[0][1]}")
