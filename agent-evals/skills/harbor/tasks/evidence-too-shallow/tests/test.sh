#!/bin/bash
# GENERATED FILE -- do not hand-edit. Source: scripts/gen_harbor_tasks.py + agent-evals/skills/skill-eval-pack.json. Regenerate with `python scripts/gen_harbor_tasks.py`.

set -euo pipefail

host_arch="$(uname -m)"
case "$host_arch" in
    aarch64) host_arch=arm64 ;;
esac
case " x86_64 " in
    *" $host_arch "*) ;;
    *)
        python3 -c "import json,sys; print('architecture_mismatch: host is ' + sys.argv[1] + ', task requires one of ' + repr(json.loads(sys.argv[2])), file=sys.stderr)" \
            "$host_arch" '["x86_64"]'
        exit 1
        ;;
esac

mkdir -p /logs/verifier
python3 /opt/abicheck-src/agent-evals/skills/harbor/verify_run.py \
    --workspace /workspace \
    --scenario /tests/scenario.json \
    --reward-txt /logs/verifier/reward.txt \
    --reward-json /logs/verifier/reward.json
