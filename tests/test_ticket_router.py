import os, re
from ticket_router import Ticket, route_ticket

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = lambda body, subject="Help", **kw: Ticket(subject, body, **kw)


def test_billing_ticket_goes_to_billing_team():
    r = route_ticket(T("I was charged twice, please refund me."))
    assert (r.category, r.queue, r.needs_human) == ("billing", "billing-team", False)


def test_technical_ticket_goes_to_tech_support():
    r = route_ticket(T("The app keeps showing an error and crashes."))
    assert (r.category, r.queue) == ("technical", "tech-support")


def test_account_ticket_goes_to_account_team():
    r = route_ticket(T("I am locked out and need a password reset."))
    assert (r.category, r.queue) == ("account", "account-team")


def test_security_wins_over_other_categories():
    r = route_ticket(T("Someone logged in to my account and changed my password."))
    assert (r.category, r.queue) == ("security", "security-team")
    assert "T2" in r.rule_ids


def test_security_is_at_least_high_urgency():
    r = route_ticket(T("My card was stolen and charged."))
    assert r.category == "security" and r.urgency in ("high", "critical")


def test_legal_threat_goes_to_human_and_is_critical():
    r = route_ticket(T("Fix this or I will take legal action."))
    assert (r.needs_human, r.queue, r.urgency, r.rule_ids) == (True, "human-triage", "critical", ["T1"])


def test_self_harm_language_goes_to_human():
    r = route_ticket(T("I want to hurt myself."))
    assert r.needs_human and r.queue == "human-triage" and r.sla_hours == 1


def test_human_rule_beats_everything_else():
    r = route_ticket(T("Refund my invoice or my lawyer will call.", customer_tier="premium"))
    assert r.rule_ids == ["T1"]


def test_no_keywords_goes_to_human_triage():
    r = route_ticket(T("Hello there."))
    assert (r.category, r.needs_human, r.rule_ids) == ("unclear", True, ["T4"])


def test_tie_goes_to_human_triage():
    r = route_ticket(T("refund and also an error"))        # billing 3, technical 2 -> no tie
    assert r.category == "billing"
    r2 = route_ticket(T("payment error"))                  # billing 2, technical 2 -> tie
    assert (r2.category, r2.needs_human) == ("unclear", True)


def test_outage_wording_is_critical():
    r = route_ticket(T("There is an outage and all users see an error."))
    assert (r.urgency, r.sla_hours) == ("critical", 1)


def test_urgent_wording_is_high():
    r = route_ticket(T("Urgent: the api returns an error and we are blocked."))
    assert (r.urgency, r.sla_hours) == ("high", 4)


def test_question_wording_is_low():
    r = route_ticket(T("How do I change my email address? Thanks"))
    assert (r.urgency, r.sla_hours) == ("low", 72)


def test_premium_raises_urgency_one_level():
    base = route_ticket(T("There is a bug in the report."))
    prem = route_ticket(T("There is a bug in the report.", customer_tier="premium"))
    assert (base.urgency, prem.urgency) == ("normal", "high") and "T6" in prem.rule_ids


def test_repeat_contact_raises_urgency():
    r = route_ticket(T("The report is still broken.", previous_contacts=3))
    assert r.urgency == "high" and "T7" in r.rule_ids
    assert route_ticket(T("The report is still broken.", previous_contacts=2)).urgency == "normal"


def test_urgency_never_exceeds_critical():
    r = route_ticket(T("outage, all users, production", customer_tier="premium", previous_contacts=5))
    assert (r.urgency, r.sla_hours) == ("critical", 1)


def test_whole_word_matching_avoids_false_hits():
    r = route_ticket(T("My pressed key is stuck on the keyboard error."))   # "press" is inside "pressed"
    assert r.category == "technical" and not r.needs_human


def test_case_does_not_matter():
    assert route_ticket(T("REFUND MY INVOICE")).category == "billing"


def test_every_routing_has_a_reason():
    for body in ["refund", "hello", "lawyer", "hacked", "password reset"]:
        assert route_ticket(T(body)).reasons


def test_results_are_deterministic():
    t = T("The api has an error, urgent.", customer_tier="premium")
    assert route_ticket(t) == route_ticket(t)


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
