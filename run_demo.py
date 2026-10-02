from code_review import review_diff

FAKE_KEY = "AKIA" + "X" * 16   # built at runtime so no real-looking key sits in the source

DIFFS = {
    "Clean change with a test": """\
--- a/app/math_utils.py
+++ b/app/math_utils.py
@@ -1,2 +1,4 @@
 def add(a, b):
-    return a+b
+    return a + b
--- a/tests/test_math_utils.py
+++ b/tests/test_math_utils.py
@@ -1,1 +1,3 @@
+def test_add():
+    assert 1 + 1 == 2
""",
    "Secret and eval": f"""\
--- a/app/config.py
+++ b/app/config.py
@@ -1,1 +1,3 @@
+KEY = "{FAKE_KEY}"
+result = eval(user_input)
""",
    "CI workflow edit": """\
--- a/.github/workflows/test.yml
+++ b/.github/workflows/test.yml
@@ -1,1 +1,2 @@
+      - run: echo hello
""",
}
for name, diff in DIFFS.items():
    r = review_diff(diff)
    print(f"\n== {name} -> {r.verdict.upper()} ({r.summary})")
    for f in r.findings:
        print(f"   [{f.rule}/{f.severity}] {f.path}:{f.line} {f.message}")
