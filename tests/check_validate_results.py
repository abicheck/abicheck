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

# Strict-kinds tier: a case can PASS/XFAIL on the top-level verdict string
# while its ground_truth.json expected_kinds/expected_absent_kinds are
# violated — the right severity for the wrong detector reason. Surface every
# mismatch, and fail the gate only when ABICHECK_STRICT_KINDS=1 (promote from
# reported-only to blocking once the catalog is verified clean).
kinds_mismatch = [
    r for r in data.get("results", [])
    if r.get("kinds_strict") == "mismatch"
]
if kinds_mismatch:
    print(f"\nexpected_kinds/expected_absent_kinds mismatches: {len(kinds_mismatch)}")
    for r in kinds_mismatch:
        print(f"  - {r.get('case_id', r.get('name'))} [{r.get('mode')}]: "
              f"{r.get('kinds_strict_detail', '')}")

kinds_strict_env = os.environ.get("ABICHECK_STRICT_KINDS") == "1"
if kinds_mismatch and kinds_strict_env:
    print("ERROR: ABICHECK_STRICT_KINDS=1 and expected_kinds mismatches present", file=sys.stderr)
    fails += len(kinds_mismatch)

if fails:
    sys.exit(1)
