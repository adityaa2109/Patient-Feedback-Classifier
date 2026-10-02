# Explainability

Expense Approval Agent reviews one expense claim at a time and returns an
approve, reject, or escalate decision. This report explains how it decides,
what data it uses, and where it should not be trusted. Every claim points to
real code in `expense_approval/policy.py`.

# Decision and Reasoning: How It Decides

This section explains how the agent reaches a decision. It uses fixed rules
and no model, so the same claim and history always produce the same result.
Every decision returns the rule identifiers that triggered it and a sentence
of reasoning, so nothing is a black box.

## The rules in order

The function `review_claim` checks these rules in order, and the first reject
or escalate rule that fires decides the outcome:

1. **R1** Reject if the amount is zero or negative.
2. **R2** Reject if the category is prohibited (alcohol, personal, fines).
3. **R3** Reject if the same employee, vendor, amount, and date already exist
   in the history.
4. **R4** Reject if the amount is above $25 and there is no receipt.
5. **R5** Escalate to a manager if the category is unknown.
6. **R6** Escalate to finance if the amount is above the category limit.
7. **R7** Escalate to finance if this claim would push the employee's total
   for that month above $3,000.
8. **R8** Approve if the amount is $100 or less and no rule above fired.
9. **R9** Otherwise escalate to a manager.

## Why the order matters

Hard failures such as prohibited items and duplicates come first, so an
escalation is never wasted on a claim that should simply be rejected. Limits
come before auto-approval, so a small claim cannot slip past a monthly cap.

## Worked example

A $300 software claim has a valid category and receipt, is under the $500
software limit, and does not break the monthly cap. It is above $100, so R9
applies and the claim goes to a manager. The output is `escalate`, route
`manager`, rule `R9`.

# Inputs and Data Sources: Data Used

This section lists what the agent takes in and where its data comes from.
Each review takes one claim and an optional list of earlier claims. The data
used is only what is passed in, plus fixed policy numbers stored in the
code.

## Inputs

A claim has an employee name, category, amount in US dollars, an ISO date, a
vendor, and a flag for whether a receipt exists. The history is a list of
earlier claims in the same format. Categories are trimmed and lower-cased
before they are compared.

## Data sources

The policy limits (category limits, the $25 receipt threshold, the $100
auto-approval range, and the $3,000 monthly cap) are constants at the top of
`expense_approval/policy.py`. There is no database, no network call, and no
external service. The history comes from whoever calls the function.

## Privacy

The agent sees employee names and spending amounts only for the duration of
one call. It stores nothing and sends nothing anywhere else. A real
deployment should pass employee identifiers instead of names.

## Data lineage

Data moves one way: the claim and history go into `review_claim`, and a
`Decision` comes out. Nothing is written back, and no state is kept between
calls.

# Limitations, Constraints and Known Issues

This section states where the agent should not be trusted. It checks claims
against simple rules, so it cannot judge whether a purchase was truly
necessary. The limits are example values and have not been tuned for any real
company.

## Known limitations

- It cannot read or verify a receipt. The `has_receipt` flag is trusted as
  given.
- Duplicate detection only matches exact vendor, amount, date, and employee,
  so a near-duplicate with a different amount is not caught.
- The category list is small and fixed. A new valid category is escalated
  until someone adds it.
- It has no currency handling. All amounts are assumed to be US dollars.
- The monthly cap counts only the history it is given, so a missing history
  hides earlier spending.

## Constraints that are enforced

The agent never approves prohibited categories, duplicates, large claims
without receipts, or claims that break the monthly cap. Automated tests in
`tests/test_expense_approval.py` check each of these rules, including the
boundaries at $25 and $100.

## Safety and human oversight

The agent supports a finance team and does not replace it. It never approves
anything above $100, and it routes every unclear case to a named person. It
should not be the only control on real money, and a person should sample its
approvals regularly.
