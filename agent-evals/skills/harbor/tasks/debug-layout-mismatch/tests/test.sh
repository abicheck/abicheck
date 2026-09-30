#!/bin/bash
# GENERATED FILE -- do not hand-edit. Source: scripts/gen_harbor_tasks.py + agent-evals/skills/skill-eval-pack.json. Regenerate with `python scripts/gen_harbor_tasks.py`.

set -euo pipefail

mkdir -p /logs/verifier
python3 /opt/abicheck-src/agent-evals/skills/harbor/verify_run.py \
    --workspace /workspace \
    --scenario /tests/scenario.json \
    --reward-txt /logs/verifier/reward.txt \
    --reward-json /logs/verifier/reward.json
