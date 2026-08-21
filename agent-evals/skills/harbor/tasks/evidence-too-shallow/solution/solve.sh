#!/bin/bash
# GENERATED FILE -- do not hand-edit. Source: scripts/gen_harbor_tasks.py + agent-evals/skills/skill-eval-pack.json. Regenerate with `python scripts/gen_harbor_tasks.py`.

set -euo pipefail
# No generic reference solution for evidence-too-shallow: this Category B
# fixture's correct invocation (a specific --used-by/--required-symbol/
# --contract choice, per tests/scenario.json's own "invocation" block) was
# verified by hand when the scenario was promoted to `ready` but is not
# recorded anywhere this generator can read it back from. Left
# unimplemented rather than guessing a command that could silently produce
# a confidently wrong reference answer.
exit 1
