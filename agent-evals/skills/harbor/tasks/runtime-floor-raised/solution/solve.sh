#!/bin/bash
# GENERATED FILE -- do not hand-edit. Source: scripts/gen_harbor_tasks.py + agent-evals/skills/skill-eval-pack.json. Regenerate with `python scripts/gen_harbor_tasks.py`.

set -euo pipefail
cd /workspace/library

# The case's own documented build recipe, verbatim:
g++ -shared -fPIC -std=c++17 -g v1.cpp -o libv1.so
g++ -shared -fPIC -std=c++17 -g v2.cpp -o libv2.so

# The case's own documented comparison, re-run with --format json so the
# verdict can be read back programmatically. `compare`'s own exit code
# encodes the verdict (e.g. 4 = BREAKING) -- non-zero is a real result,
# not a failure, and `set -e` must not treat it as one; the report file
# on disk is what this script actually reads. Any env-var prefix and
# trailing flags (--header, --contract, ...) the README's own command
# carries are preserved -- some cases (e.g. a header-only-visible source
# break) only produce their documented verdict with those flags present.
# A fresh `mktemp` path, not a fixed name: a real trial's own container is
# isolated, so a shared name would be harmless there, but this exact script
# is also executed directly (not through a container) by tests/
# test_gen_harbor_tasks.py's TestSolveScriptsEndToEnd, once per scenario --
# a fixed `/tmp/report.json` is one shared file across every one of those
# invocations, and pytest-xdist is free to schedule two of them onto
# different workers at the same time, so one scenario's write can race
# another's read of the identical path (reproduced as a real, host/
# scheduling-dependent CI failure, not a hypothetical). `mktemp` gives every
# invocation -- test or real trial -- its own path, closing the race at its
# actual cause instead of only in the test harness that happened to expose it.
report_json="$(mktemp)"
abicheck compare libv1.so libv2.so --format json -o "$report_json" || true
verdict=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['verdict'])" "$report_json")
cat > /workspace/final.md <<EOF
Reference solution -- the documented command for this case:

\`\`\`
g++ -shared -fPIC -std=c++17 -g v1.cpp -o libv1.so
g++ -shared -fPIC -std=c++17 -g v2.cpp -o libv2.so
abicheck compare libv1.so libv2.so
\`\`\`

reported $verdict.

\`\`\`json
{"verdict": "$verdict", "evidence": [0], "confident": true}
\`\`\`
EOF
