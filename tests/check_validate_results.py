import json
import os
import sys

data = json.load(open("results/validate_examples.json"))
s = data["summary"]
print("Results:", json.dumps(s))
fails = s.get("FAIL", 0) + s.get("ERROR", 0)

# Strict-category tier (#4): when full (L2+) evidence is present an
# API_BREAK→COMPATIBLE collapse is masked by verdict normalization and still
# counts as PASS. Surface every collapse so the boundary blur is visible, and
# fail the gate only when ABICHECK_STRICT_CATEGORY=1 (so it can be promoted
# from reported-only to blocking once the catalog is clean).
collapsed = [
    r for r in data.get("results", [])
    if r.get("category_strict") == "collapsed"
]
if collapsed:
    print(f"\nCategory collapses (API_BREAK→COMPATIBLE with full evidence): {len(collapsed)}")
    for r in collapsed:
        print(f"  - {r.get('case_id', r.get('name'))} [{r.get('mode')}]: "
              f"expected={r.get('expected')!r} got={r.get('got')!r}")

strict = os.environ.get("ABICHECK_STRICT_CATEGORY") == "1"
if collapsed and strict:
    print("ERROR: ABICHECK_STRICT_CATEGORY=1 and category collapses present", file=sys.stderr)
    fails += len(collapsed)

if fails:
    sys.exit(1)
