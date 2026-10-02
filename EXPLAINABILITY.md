# Explainability

Code Review Checker reads a unified diff and returns a verdict with itemized
findings. This report explains how it decides, what it reads, and where it
should not be trusted. Every claim points to real code in
`code_review/checker.py`.

# Decision and Reasoning: How It Decides

This section explains how the agent reaches a verdict. It uses fixed text
patterns and counts, with no model, so the same diff always gives the same
answer. Each finding carries a rule identifier, a severity, a file, and a
line number, so the reasoning behind a verdict can be checked by hand.

## Step 1: find the added lines

`parse_diff` walks the diff, tracks the current file from the `+++` line and
the new line number from each `@@` hunk header, and collects only the lines
that begin with `+`. Removed and unchanged lines are ignored, so old problems
are not blamed on the author.

## Step 2: apply the content rules

Each added line is tested against the rules in `CONTENT_RULES`, and the first
match on a line becomes its single finding:

| Rule | Severity | What it flags |
|---|---|---|
| C1 | high | Likely AWS keys, private key headers, hardcoded passwords or tokens |
| C2 | high | `eval`, `exec`, `os.system`, `shell=True`, `pickle.load(s)` |
| C3 | medium | Bare `except:` |
| C4 | medium | A skipped test |
| C5 | low | Debug output such as `print(`, `breakpoint(`, `console.log(` |
| C6 | low | `TODO`, `FIXME`, `HACK` |

## Step 3: apply the whole-change rules

Rule C7 (medium) fires when source files changed but no test file did. A
change is also marked large if it adds more than 400 lines, and sensitive if
it touches CI workflows, Docker files, dependency files, or `.env` files.

## Step 4: choose the verdict

The verdict is chosen in this order:

1. Any high finding gives **request changes**.
2. Otherwise a large or sensitive change gives **needs human**.
3. Otherwise any medium finding gives **request changes**.
4. Otherwise the result is **approve**, with low findings listed as notes.

## Worked example

A diff adds `result = eval(user_input)` to `app/config.py` and changes no
tests. Rule C2 fires as high and rule C7 fires as medium. Because a high
finding exists, the verdict is request changes, with both findings listed.

# Inputs and Data Sources: Data Used

This section lists what the agent reads and where the data comes from. The
only input is the text of a unified diff passed to `review_diff`. The data
used is that text plus fixed rule patterns stored in the code, with no
network access and no stored history.

## Inputs

The input is one string in the standard unified diff format produced by
`git diff`. The agent reads file names from `+++` lines, line numbers from
`@@` headers, and the content of lines that start with `+`.

## Data sources

The rule patterns, the 400-line threshold, and the list of sensitive paths
are constants at the top of `code_review/checker.py`. There is no database,
no external service, and no model call.

## Privacy and secrets

The agent may see real secrets inside a diff. It never copies a matched
value into a finding, a summary, or any output, and it does not store the
diff after returning the result. Its messages say only that a possible
secret was found and where.

## Data lineage

Data moves one way: the diff text goes into `parse_diff`, the added lines go
through the rules, and a `Review` object comes out with a verdict and its
findings. Nothing is written back and no state is kept between calls.

# Limitations, Constraints and Known Issues

This section states where the agent should not be trusted. It matches text
patterns, so it can both miss real problems and flag harmless code. It
cannot understand what a change is meant to do, so a clean verdict is not a
guarantee that the code is correct or secure.

## Known limitations

- Pattern matching produces false positives, such as a harmless `eval(` in a
  comment or a test fixture, and false negatives, such as a secret split
  across two lines or encoded.
- The secret rules cover a few common formats and miss many others.
- It treats a file as a test only by name, so a test stored under an unusual
  name counts as source.
- It reads only added lines, so a problem created by removing a safeguard is
  not seen.
- It checks Python-style and JavaScript-style patterns best and covers other
  languages weakly.
- The 400-line threshold and the sensitive path list are example values, not
  tuned to any real team.

## Constraints that are enforced

The agent never prints a suspected secret, never approves a change with a
high-severity finding, and never approves a large or sensitive change without
a human. Automated tests in `tests/test_code_review.py` check each of these
rules, including the 400-line boundary and line numbering.

## Safety and human oversight

The agent supports reviewers and does not replace them. It should be one
check among several, alongside real secret scanners, static analysis, and a
human reading the change. A person should make the final merge decision.
