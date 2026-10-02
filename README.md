# Code Review Checker

A small, rule-based agent that reads a unified diff and returns **approve**,
**request changes**, or **needs human**, with each finding tied to a rule,
severity, file, and line number. It flags hardcoded secrets, dangerous calls
(`eval`, `exec`, `os.system`, `shell=True`, `pickle`), bare `except:`, skipped
tests, debug leftovers, source changes without tests, oversized changes, and
edits to sensitive files such as CI workflows. It never prints the value of a
suspected secret.

## Run it

```
pip install -r requirements.txt
python run_demo.py
python -m pytest tests -v
```

## Files

- `agent.yaml`, `SOUL.md`, `EXPLAINABILITY.md`: the OpenGAP agent description.
- `code_review/checker.py`: the diff parser, the rules, and the verdict logic.
- `tests/`: automated tests for every rule, the verdict order, and the documents.

## Honest scope

This is pattern matching on text. It can miss real problems and flag harmless
code, so it should be used next to real secret scanners, static analysis, and
human review, not instead of them.
