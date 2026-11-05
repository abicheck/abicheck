import json
import sys

data = json.load(open("results/validate_examples.json"))
s = data["summary"]
print("Results:", json.dumps(s))
fails = s.get("FAIL", 0) + s.get("ERROR", 0)

# Strict-kinds tier: in the native lanes (debug-headers, build-source) a
# missing expected_kinds entry, a present expected_absent_kinds entry, or a
# stale expected_kinds_gaps declaration already FAILs its row
# (tests/expected_kinds_gate.py), so it is counted above. List them here so
# the log names the kinds, not just the case.
kinds_failures = [
    r
    for r in data.get("results", [])
    if r.get("kinds_strict") in ("mismatch", "stale-gap")
]
if kinds_failures:
    print(f"\nexpected_kinds gate violations: {len(kinds_failures)}")
    for r in kinds_failures:
        print(
            f"  - {r.get('case_id', r.get('name'))} [{r.get('mode')}] "
            f"{r.get('status')}: {r.get('kinds_strict_detail', '')}"
        )

if fails:
    sys.exit(1)
