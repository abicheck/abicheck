# GENERATED FILE -- do not hand-edit. Source: scripts/gen_harbor_tasks.py + agent-evals/skills/skill-eval-pack.json. Regenerate with `python scripts/gen_harbor_tasks.py`.
FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Same pin, same fallback-to-main rationale, and the same runtime digest as
# `_dockerfile()`'s agent image -- see that function's own comments for why
# each exists. `_normalize_pinned_ref`/`_DIGEST_LINE` in this generator are
# file-path-agnostic, so the identical two-line convention here is picked up
# by `--check` without any further changes to that comparison machinery.
ARG ABICHECK_REF=cf30f28b0687161d4766620f6ae8e17844d6c6c5
# ABICHECK_RUNTIME_DIGEST=69bb57a4965c8cee8d1986604e5912a7020bb8fedc7b8f143e6a97fd6703ac35
RUN git clone https://github.com/abicheck/abicheck.git /opt/abicheck-src \
    && cd /opt/abicheck-src \
    && (git checkout "$ABICHECK_REF" 2>/dev/null \
        || (echo "WARNING: pinned ref $ABICHECK_REF unreachable (likely pruned after a squash-merge) -- falling back to main" >&2 \
            && git checkout main))

# The verifier's own allowlist: only what `verify_run.py` actually needs at
# runtime -- itself, and the `graders/` package it imports -- survives.
# Everything else (including `.git`, for the identical history-leak reason
# `_dockerfile()`'s own comment gives -- though there is no agent in this
# container to read it from, keeping the discipline identical avoids this
# image quietly becoming the one place that reasoning doesn't hold) is
# discarded. This container is never agent-visible, but the allowlist
# convention is kept anyway rather than trusted-by-isolation-alone: a
# correct architecture and a minimal attack surface are not substitutes for
# each other.
RUN set -eux; \
    cd /opt/abicheck-src; \
    mkdir -p /tmp/rt-keep/agent-evals/skills/harbor; \
    mv agent-evals/skills/graders /tmp/rt-keep/agent-evals/skills/graders; \
    mv agent-evals/skills/harbor/verify_run.py /tmp/rt-keep/agent-evals/skills/harbor/verify_run.py; \
    cd /; \
    rm -rf /opt/abicheck-src; \
    mv /tmp/rt-keep /opt/abicheck-src
