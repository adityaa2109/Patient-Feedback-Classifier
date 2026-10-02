# Explainability

Support Ticket Router reads one ticket and returns a queue, an urgency, a
response deadline, and a flag for human handling. This report explains how it
decides, what it reads, and where it should not be trusted. Every claim
points to real code in `ticket_router/router.py`.

# Decision and Reasoning: How It Decides

This section explains how the agent reaches a routing decision. It uses
fixed keyword rules and no model, so the same ticket always gets the same
result. Each result lists the rule identifiers that produced it, so a person
can see exactly why a ticket went where it did.

## The rules in order

The function `route_ticket` applies these rules in order:

1. **T1** If the text contains safety, legal, or privacy-sensitive phrases
   (for example a threat of legal action), route to `human-triage` as
   critical with a one-hour deadline and stop.
2. **T2** If the text contains security phrases (for example "hacked" or
   "suspicious login"), the category is security.
3. **T3** Otherwise score billing, technical, and account keywords. The
   category with the highest total weight wins.
4. **T4** If no keyword matched, or two categories tie, route to
   `human-triage` for a person to decide.
5. **T5** Set urgency from wording: question wording lowers it to low, blocked
   work or deadlines raise it to high, and outage or data-loss wording raises
   it to critical. Security tickets are at least high.
6. **T6** A premium customer raises urgency by one level.
7. **T7** A third or later contact about the same issue raises urgency by
   one level.

## Urgency and deadlines

Urgency maps to a response deadline: critical is 1 hour, high is 4 hours,
normal is 24 hours, and low is 72 hours. Urgency can never go above
critical.

## Worked example

A premium customer writes that the app crashes on login, the matter is
urgent, and production is blocked. The word "crashes" scores 3 for technical
and "login" scores 2 for account, so technical wins (T3). The words "urgent"
and "production" raise urgency to high (T5), and the premium tier raises it
to critical (T6). The ticket goes to `tech-support` with a one-hour deadline.

# Inputs and Data Sources: Data Used

This section lists what the agent reads and where its data comes from. The
only inputs are the ticket text and two customer facts. The data used is
those inputs plus keyword lists stored in the code, with no network access
and no stored history.

## Inputs

A ticket has a subject, a body, a customer tier (standard or premium), and a
count of earlier contacts about the same issue. The subject and body are
joined and lower-cased, and keywords are matched as whole words so that a
word like "pressed" does not match "press".

## Data sources

The keyword lists, weights, queue names, and deadlines are constants at the
top of `ticket_router/router.py`. There is no database, no customer record
lookup, and no external service. The tier and contact count come from
whoever calls the function.

## Privacy

Tickets may contain personal details. The agent keeps nothing after a call
and sends nothing to any other system. Its reasons describe the rule that
fired and do not repeat the customer's words.

## Data lineage

Data moves one way: the ticket goes into `route_ticket`, the rules run in
order, and a `Routing` object comes out. Nothing is written back and no state
is kept between calls.

# Limitations, Constraints and Known Issues

This section states where the agent should not be trusted. It matches
keywords, so it can misread sarcasm, negation, and any wording it has not
been given. A routing result is a suggestion for a queue, not a judgment of
what the customer really needs.

## Known limitations

- It does not understand meaning. "This is not a refund request" still scores
  as billing because the word "refund" appears.
- It reads English only, and misspellings are not matched.
- Keyword weights and queue names are example values and have not been tuned
  on real tickets.
- The safety and legal phrase list is short. A distressed customer who uses
  other words will not be sent to a person by rule T1.
- A ticket that mixes two problems is routed by the higher score only, so the
  second problem is not flagged.
- The tier and contact count are trusted as given and are not checked
  against any customer record.

## Constraints that are enforced

The agent always routes sensitive tickets to a human with a one-hour
deadline, never lets any rule override that, treats security as at least
high urgency, never exceeds critical, and sends unclear or tied tickets to a
person. Automated tests in `tests/test_ticket_router.py` check each of these
rules.

## Safety and human oversight

The agent supports a support team and does not replace it. Because it cannot
read tone or context, a person should sample its routing results regularly,
and anyone who may be in distress should be handled by a trained person, not
by a keyword list. The phrase list should be reviewed by that team before any
real use.
