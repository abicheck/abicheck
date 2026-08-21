#!/bin/bash
# GENERATED FILE -- do not hand-edit. Source: scripts/gen_harbor_tasks.py + agent-evals/skills/skill-eval-pack.json. Regenerate with `python scripts/gen_harbor_tasks.py`.

set -euo pipefail
cd /workspace/library

# The case's own documented build recipe, verbatim:
gcc -shared -fPIC -g v1.c -o libfoo_v1.so
gcc -shared -fPIC -g v2.c -o libfoo_v2.so

# The case's own documented comparison, re-run with --format json so the
# verdict can be read back programmatically. `compare`'s own exit code
# encodes the verdict (e.g. 4 = BREAKING) -- non-zero is a real result,
# not a failure, and `set -e` must not treat it as one; the report file
# on disk is what this script actually reads.
abicheck compare libfoo_v1.so libfoo_v2.so --format json -o /tmp/report.json || true
verdict=$(python3 -c "import json; print(json.load(open('/tmp/report.json'))['verdict'])")
cat > /workspace/final.md <<EOF
Reference solution -- the documented command for this case:

\`\`\`
gcc -shared -fPIC -g v1.c -o libfoo_v1.so
gcc -shared -fPIC -g v2.c -o libfoo_v2.so
abicheck compare libfoo_v1.so libfoo_v2.so
\`\`\`

reported $verdict.

\`\`\`json
{"verdict": "$verdict", "evidence": [0], "confident": true}
\`\`\`
EOF
