# GENERATED FILE -- do not hand-edit. Source: scripts/gen_harbor_tasks.py + agent-evals/skills/skill-eval-pack.json. Regenerate with `python scripts/gen_harbor_tasks.py`.
FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc g++ castxml git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Pinned to the commit this task pack was generated from -- see
# `_abicheck_ref()` in scripts/gen_harbor_tasks.py.
ARG ABICHECK_REF=4e399f2095bbe389fd77654cd2128046e8bea5fe
RUN git clone https://github.com/abicheck/abicheck.git /opt/abicheck-src \
    && cd /opt/abicheck-src && git checkout "$ABICHECK_REF" \
    && pip install --no-cache-dir -e ".[dev]"

# Evaluation-only version-metadata patch (mirrors
# agent-evals/skills/CLAUDE.md's "Environment prerequisites for a real run"
# section, never a real release decision): `pyproject.toml`'s own `version`
# field lags the working tree's actual CLI surface between releases
# (skills-src/CLAUDE.md rule 7), and the published skill's own preflight
# refuses to proceed on a version outside its declared
# `abicheck-version-range` -- without this, every skill-arm trial would
# dead-end at that preflight while the baseline arm proceeds normally,
# which reads as "the skill hurts" when the actual cause is environment
# metadata, not skill quality. Both metadata sources `importlib.metadata`
# can resolve (the editable install's own egg-info, and the site-packages
# dist-info it also registers) are patched, since which one wins is
# resolution-order dependent.
RUN set -eux; \
    EGG=/opt/abicheck-src/abicheck.egg-info/PKG-INFO; \
    DIST=$(python3 -c "import importlib.metadata as m; print(next(str(d._path) for d in m.distributions() if d.metadata['Name']=='abicheck'))")/METADATA; \
    sed -i "s/^Version: .*/Version: 0.6.0/" "$EGG" "$DIST"

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
ENV SKILL_EVAL_SHIM=/usr/local/bin/abicheck

# `python -m abicheck ...` is a documented, supported entry point that does
# not go through the PATH shim above -- an agent using it would record no
# calls at all, biasing the baseline arm in particular (it is not handed
# the skill's own spelling of the command). Same interposer the existing
# harness installs (`runners.claude_code._PYTHON_INTERPOSER`), reused
# verbatim rather than re-derived, so a fix to its module-selector parsing
# there is not something this image can silently drift from. base64-encoded
# rather than a heredoc: a heredoc body inside a single `RUN` needs
# BuildKit's Dockerfile 1.4+ heredoc support (undeclared here) to survive
# the classic line-oriented parser intact, while base64 has no shell
# metacharacters at all and needs nothing beyond `base64`, present in every
# Debian-based image by default.
RUN mv /usr/local/bin/python3 /usr/local/bin/python3-real \
    && echo 'IyEvYmluL3NoCnNraXA9MApzZWVuPTAKZm9yIGFyZyBpbiAiJEAiOyBkbwogIGlmIFsgIiRza2lwIiA9IDEgXTsgdGhlbiBza2lwPTA7IHNlZW49JCgoc2VlbisxKSk7IGNvbnRpbnVlOyBmaQogIGNhc2UgIiRhcmciIGluCiAgICAtbSkKICAgICAgaWYgWyAiJChldmFsIGVjaG8gXCJcJHskKChzZWVuKzIpKX1cIikiID0gImFiaWNoZWNrIiBdOyB0aGVuCiAgICAgICAgc2hpZnQgJCgoc2VlbisyKSkKICAgICAgICBleGVjICIkU0tJTExfRVZBTF9TSElNIiAiJEAiCiAgICAgIGZpCiAgICAgIGJyZWFrCiAgICAgIDs7CiAgICAtbWFiaWNoZWNrKQogICAgICBzaGlmdCAkKChzZWVuKzEpKQogICAgICBleGVjICIkU0tJTExfRVZBTF9TSElNIiAiJEAiCiAgICAgIDs7CiAgICAtWHwtV3wtLWNoZWNrLWhhc2gtYmFzZWQtcHljcykgc2tpcD0xIDs7CiAgICAtYykgYnJlYWsgOzsKICAgIC0qKSA7OwogICAgKikgYnJlYWsgOzsKICBlc2FjCiAgc2Vlbj0kKChzZWVuKzEpKQpkb25lCmV4ZWMgIiRTS0lMTF9FVkFMX1JFQUxfUFlUSE9OIiAiJEAiCg==' | base64 -d > /usr/local/bin/python3 \
    && cp /usr/local/bin/python3 /usr/local/bin/python \
    && chmod +x /usr/local/bin/python3 /usr/local/bin/python
ENV SKILL_EVAL_REAL_PYTHON=/usr/local/bin/python3-real

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
