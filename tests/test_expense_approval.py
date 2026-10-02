import os, re
from expense_approval import Claim, review_claim
from expense_approval.policy import MONTHLY_CAP_USD

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
C = lambda cat="meals", amt=50.0, **kw: Claim(kw.get("emp", "asha"), cat, amt, kw.get("date", "2026-10-02"),
                                              kw.get("vendor", "Cafe"), kw.get("receipt", True))

def test_small_valid_claim_is_approved():
    d = review_claim(C(amt=50.0))
    assert (d.action, d.rule_ids) == ("approve", ["R8"])

def test_zero_or_negative_amount_rejected():
    assert review_claim(C(amt=0)).rule_ids == ["R1"]
    assert review_claim(C(amt=-5)).rule_ids == ["R1"]

def test_prohibited_category_rejected():
    assert review_claim(C(cat="alcohol")).rule_ids == ["R2"]

def test_duplicate_rejected():
    prior = [C(amt=40.0)]
    assert review_claim(C(amt=40.0), prior).rule_ids == ["R3"]

def test_missing_receipt_above_threshold_rejected():
    assert review_claim(C(amt=30.0, receipt=False)).rule_ids == ["R4"]

def test_missing_receipt_at_or_below_threshold_allowed():
    assert review_claim(C(amt=25.0, receipt=False)).action == "approve"

def test_unknown_category_escalates_to_manager():
    d = review_claim(C(cat="spaceship"))
    assert (d.action, d.route, d.rule_ids) == ("escalate", "manager", ["R5"])

def test_over_category_limit_escalates_to_finance():
    d = review_claim(C(cat="meals", amt=76.0))
    assert (d.action, d.route, d.rule_ids) == ("escalate", "finance", ["R6"])

def test_monthly_cap_escalates_to_finance():
    prior = [C(cat="travel", amt=1400.0, date="2026-10-01", vendor="A"),
             C(cat="travel", amt=1400.0, date="2026-10-01", vendor="B")]
    d = review_claim(C(cat="travel", amt=500.0, vendor="C"), prior)
    assert (d.action, d.route, d.rule_ids) == ("escalate", "finance", ["R7"])

def test_monthly_cap_ignores_other_months_and_people():
    prior = [C(cat="travel", amt=1400.0, date="2026-09-01", vendor="A"),
             C(cat="travel", amt=1400.0, emp="ravi", vendor="B")]
    assert review_claim(C(cat="travel", amt=500.0, vendor="C"), prior).action == "escalate"  # R9 manager, not R7
    assert review_claim(C(cat="travel", amt=500.0, vendor="C"), prior).rule_ids == ["R9"]

def test_mid_size_claim_needs_manager():
    d = review_claim(C(cat="software", amt=300.0))
    assert (d.action, d.route, d.rule_ids) == ("escalate", "manager", ["R9"])

def test_boundary_100_is_auto_approved_and_101_is_not():
    assert review_claim(C(cat="office", amt=100.0)).action == "approve"
    assert review_claim(C(cat="office", amt=101.0)).rule_ids == ["R9"]

def test_every_decision_has_a_reason():
    for amt in (0, 20, 100, 300, 5000):
        assert review_claim(C(cat="travel", amt=amt)).reasons

def test_results_are_deterministic():
    a = review_claim(C(amt=300.0, cat="software"))
    b = review_claim(C(amt=300.0, cat="software"))
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
