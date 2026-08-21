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
        mkdir -p /logs/verifier
        echo 0 > /logs/verifier/reward.txt
        python3 -c "import json; json.dump({'reward': 0, 'error': 'architecture_mismatch', 'host_architecture': '$host_arch', 'required_architectures': ['x86_64']}, open('/logs/verifier/reward.json', 'w'))"
        exit 0
        ;;
esac

mkdir -p /logs/verifier
python3 /opt/abicheck-src/agent-evals/skills/harbor/verify_run.py \
    --workspace /workspace \
    --scenario /tests/scenario.json \
    --reward-txt /logs/verifier/reward.txt \
    --reward-json /logs/verifier/reward.json
