import os, re
from code_review import review_diff

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKE_KEY = "AKIA" + "Z" * 16


def diff(path, *added, start=1, extra_files=()):
    body = "".join(f"--- a/{path}\n+++ b/{path}\n@@ -0,0 +{start},{len(added)} @@\n" + "".join(f"+{a}\n" for a in added))
    for f in extra_files:
        body += f"--- a/{f}\n+++ b/{f}\n@@ -0,0 +1,1 @@\n+def test_x():\n"
    return body


def rules(r):
    return [f.rule for f in r.findings]


def test_clean_change_with_test_is_approved():
    r = review_diff(diff("app/a.py", "x = 1", extra_files=["tests/test_a.py"]))
    assert r.verdict == "approve" and r.findings == []


def test_aws_key_is_high_and_blocks():
    r = review_diff(diff("app/a.py", f'KEY = "{FAKE_KEY}"', extra_files=["tests/test_a.py"]))
    assert r.verdict == "request_changes" and rules(r) == ["C1"]


def test_secret_value_is_never_printed():
    r = review_diff(diff("app/a.py", f'KEY = "{FAKE_KEY}"', extra_files=["tests/test_a.py"]))
    assert all(FAKE_KEY not in f.message for f in r.findings) and FAKE_KEY not in r.summary


def test_hardcoded_password_detected():
    r = review_diff(diff("app/a.py", 'password = "hunter22"', extra_files=["tests/test_a.py"]))
    assert rules(r) == ["C1"]


def test_private_key_header_detected():
    r = review_diff(diff("keys.txt", "-----BEGIN RSA PRIVATE KEY-----"))
    assert "C1" in rules(r) and r.verdict == "request_changes"


def test_eval_exec_system_shell_pickle_detected():
    lines = ["eval(x)", "exec(x)", "os.system(x)", "subprocess.run(x, shell=True)", "pickle.loads(x)"]
    r = review_diff(diff("app/a.py", *lines, extra_files=["tests/test_a.py"]))
    assert rules(r) == ["C2"] * 5 and r.verdict == "request_changes"


def test_bare_except_is_medium():
    r = review_diff(diff("app/a.py", "try:", "    f()", "except:", "    pass", extra_files=["tests/test_a.py"]))
    assert rules(r) == ["C3"] and r.verdict == "request_changes"


def test_skipped_test_is_flagged():
    r = review_diff(diff("tests/test_a.py", "@pytest.mark.skip"))
    assert "C4" in rules(r)


def test_debug_print_and_todo_are_low_and_still_approve():
    r = review_diff(diff("app/a.py", "print(x)", "# TODO later", extra_files=["tests/test_a.py"]))
    assert sorted(rules(r)) == ["C5", "C6"] and r.verdict == "approve"


def test_source_change_without_tests_is_flagged():
    r = review_diff(diff("app/a.py", "x = 1"))
    assert rules(r) == ["C7"] and r.verdict == "request_changes"


def test_docs_only_change_does_not_need_tests():
    r = review_diff(diff("README.md", "hello"))
    assert r.verdict == "approve"


def test_sensitive_file_needs_human():
    r = review_diff(diff(".github/workflows/test.yml", "- run: ls"))
    assert r.verdict == "needs_human"


def test_large_change_needs_human():
    r = review_diff(diff("README.md", *["line"] * 401))
    assert r.verdict == "needs_human" and r.added_lines == 401


def test_high_finding_beats_needs_human():
    r = review_diff(diff(".github/workflows/test.yml", "- run: eval(x)"))
    assert r.verdict == "request_changes"


def test_removed_lines_are_not_blamed():
    d = "--- a/app/a.py\n+++ b/app/a.py\n@@ -1,1 +1,1 @@\n-eval(x)\n+x = 1\n"
    r = review_diff(d + "--- a/tests/test_a.py\n+++ b/tests/test_a.py\n@@ -0,0 +1,1 @@\n+def test_x():\n")
    assert r.findings == []


def test_line_numbers_follow_hunk_header():
    r = review_diff(diff("app/a.py", "a = 1", "eval(x)", start=10, extra_files=["tests/test_a.py"]))
    assert [(f.rule, f.line) for f in r.findings] == [("C2", 11)]


def test_empty_diff_is_approved_with_no_findings():
    r = review_diff("")
    assert r.verdict == "approve" and r.findings == [] and r.added_lines == 0


def test_results_are_deterministic():
    d = diff("app/a.py", "eval(x)")
    assert review_diff(d) == review_diff(d)


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
