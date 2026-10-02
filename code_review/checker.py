"""Rule-based diff reviewer. Only ADDED lines are inspected for content
rules, so old problems in untouched code are never blamed on the author."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

LARGE_CHANGE_LINES = 400
SENSITIVE_PATHS = (".github/workflows/", "dockerfile", "docker-compose", "requirements",
                   "package.json", "pyproject.toml", ".env")
SOURCE_EXT = (".py", ".js", ".ts", ".java", ".go", ".rb")

# (rule_id, severity, regex, message). Messages never include the matched text.
CONTENT_RULES = [
    ("C1", "high", re.compile(r"AKIA[0-9A-Z]{16}"), "Possible AWS access key committed."),
    ("C1", "high", re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"), "Private key committed."),
    ("C1", "high", re.compile(r"(?i)\b(password|passwd|secret|api_key|token)\s*=\s*[\"'][^\"']{4,}[\"']"),
     "Possible hardcoded credential."),
    ("C2", "high", re.compile(r"\beval\s*\("), "Use of eval() runs arbitrary code."),
    ("C2", "high", re.compile(r"\bexec\s*\("), "Use of exec() runs arbitrary code."),
    ("C2", "high", re.compile(r"os\.system\s*\("), "os.system() can allow command injection."),
    ("C2", "high", re.compile(r"shell\s*=\s*True"), "shell=True can allow command injection."),
    ("C2", "high", re.compile(r"pickle\.loads?\s*\("), "pickle can execute code when loading untrusted data."),
    ("C3", "medium", re.compile(r"^\s*except\s*:"), "Bare 'except:' hides errors."),
    ("C4", "medium", re.compile(r"@pytest\.mark\.skip|\bxit\s*\(|\.skip\s*\(|@unittest\.skip"), "A test is being skipped."),
    ("C5", "low", re.compile(r"\bbreakpoint\s*\(|import pdb|console\.log\s*\(|^\s*print\s*\("), "Debug output left in code."),
    ("C6", "low", re.compile(r"\b(TODO|FIXME|HACK)\b"), "TODO or FIXME added."),
]


@dataclass
class Finding:
    rule: str
    severity: str     # high | medium | low
    path: str
    line: int
    message: str


@dataclass
class Review:
    verdict: str                      # approve | request_changes | needs_human
    findings: list = field(default_factory=list)
    summary: str = ""
    added_lines: int = 0
    files: list = field(default_factory=list)


def parse_diff(diff: str):
    """Yield (path, new_line_number, text) for every added line, and return
    the list of changed paths."""
    path, new_line, added, files = None, 0, [], []
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            p = raw[4:].strip()
            path = p[2:] if p.startswith("b/") else p
            if path != "/dev/null" and path not in files:
                files.append(path)
        elif raw.startswith("--- "):
            continue
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            new_line = int(m.group(1)) if m else 0
        elif raw.startswith("+") and path:
            added.append((path, new_line, raw[1:]))
            new_line += 1
        elif raw.startswith("-"):
            continue
        else:
            new_line += 1
    return added, files


def _is_test(path: str) -> bool:
    low = path.lower()
    return "/tests/" in "/" + low or low.startswith("test_") or "/test_" in low or low.endswith("_test.py") \
        or ".test." in low or ".spec." in low


def review_diff(diff: str) -> Review:
    added, files = parse_diff(diff)
    findings: list[Finding] = []

    for path, line_no, text in added:
        for rule, sev, rx, msg in CONTENT_RULES:
            if rx.search(text):
                findings.append(Finding(rule, sev, path, line_no, msg))
                break          # one finding per line, most serious rule first

    src = [f for f in files if f.lower().endswith(SOURCE_EXT) and not _is_test(f)]
    tests = [f for f in files if _is_test(f)]
    if src and not tests:
        findings.append(Finding("C7", "medium", src[0], 0, "Source files changed but no test files changed."))

    sensitive = [f for f in files if any(s in f.lower() for s in SENSITIVE_PATHS)]
    large = len(added) > LARGE_CHANGE_LINES

    sev = {f.severity for f in findings}
    if "high" in sev:
        verdict, why = "request_changes", "High-severity findings must be fixed before merge."
    elif large or sensitive:
        verdict = "needs_human"
        why = ("Change is too large to review reliably." if large
               else "Change touches sensitive files: " + ", ".join(sensitive) + ".")
    elif "medium" in sev:
        verdict, why = "request_changes", "Medium-severity findings should be addressed."
    else:
        verdict, why = "approve", "No blocking findings."
        if findings:
            why += " Low-severity notes are listed for the author."

    return Review(verdict, findings, why, len(added), files)
