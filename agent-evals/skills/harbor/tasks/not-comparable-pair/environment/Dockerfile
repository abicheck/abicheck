# GENERATED FILE -- do not hand-edit. Source: scripts/gen_harbor_tasks.py + agent-evals/skills/skill-eval-pack.json. Regenerate with `python scripts/gen_harbor_tasks.py`.
FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc g++ castxml git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Pinned to the commit this task pack was generated from -- see
# `_abicheck_ref()` in scripts/gen_harbor_tasks.py.
ARG ABICHECK_REF=fb5528b35b437a84e35fb362aa224bde24e47538
RUN git clone https://github.com/abicheck/abicheck.git /opt/abicheck-src \
    && cd /opt/abicheck-src && git checkout "$ABICHECK_REF" \
    && pip install --no-cache-dir -e ".[dev]"

# Recording shim (agent-evals/skills/shim/abicheck): every `abicheck ...`
# call an agent makes is transparently recorded to $SKILL_EVAL_CALLS, then
# forwarded to the real binary. Present in every trial, skill-arm or not --
# both arms of the existing harness always have abicheck on PATH; only
# whether the skill points the agent at it differs.
RUN real="$(command -v abicheck)" \
    && mv "$real" "$real-real" \
    && cp /opt/abicheck-src/agent-evals/skills/shim/abicheck "$real" \
    && chmod +x "$real" "$real-real"
ENV SKILL_EVAL_REAL_ABICHECK=/usr/local/bin/abicheck-real
ENV SKILL_EVAL_CALLS=/workspace/calls.jsonl

# This sandbox's own castxml is routinely below abicheck's policy floor
# (agent-evals/skills/CLAUDE.md's "Environment prerequisites" section);
# degrade to direct-clang rather than hard-erroring, matching the existing
# harness's own documented workaround.
ENV ABICHECK_ALLOW_AST_FALLBACK=1

# Opt-in only -- see this file's own module docstring. A skill-arm trial
# requests it explicitly: `harbor run ... --ak skills_dir=/opt/skills`.
RUN mkdir -p /opt/skills \
    && cp -r /opt/abicheck-src/.claude/skills/check-abi-compatibility /opt/skills/

WORKDIR /workspace
COPY workspace/ /workspace/
