# GENERATED FILE -- do not hand-edit. Source: scripts/gen_harbor_tasks.py + agent-evals/skills/skill-eval-pack.json. Regenerate with `python scripts/gen_harbor_tasks.py`.
FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc g++ clang castxml git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Pinned to the commit this task pack was generated from -- see
# `_abicheck_ref()` in scripts/gen_harbor_tasks.py. Falls back to `main`
# if the pinned commit is unreachable: this repo's own PRs squash-merge
# (confirmed from `main`'s own log shape -- one commit per PR, titled
# "<title> (#N)"), so the exact development-branch commit this generator
# pinned does not survive past this PR's own merge -- a plain `git clone`
# (no `refs/pull/*`) will not have that object once the source branch is
# gone. Regenerating immediately after every merge would keep the pin
# exact, but nothing enforces that happening, so every already-published
# task would otherwise fail its build forever the moment the object is
# pruned (Codex review, fresh evidence: confirmed via `git merge-base
# --is-ancestor` that the previously-committed pin already isn't an
# ancestor of a representative squashed commit). Falling back to `main`
# keeps the image buildable at the cost of pinning precision once that
# happens -- strictly better than a permanently broken image, though a
# real fix (vendoring the runtime sources directly, or a published
# release tag) is a bigger, cross-cutting change than this generator's
# own scope; see agent-evals/skills/harbor/CLAUDE.md.
ARG ABICHECK_REF=dcfce7aa4334c011e159703ca56703e78183e520
RUN git clone https://github.com/abicheck/abicheck.git /opt/abicheck-src \
    && cd /opt/abicheck-src \
    && (git checkout "$ABICHECK_REF" 2>/dev/null \
        || (echo "WARNING: pinned ref $ABICHECK_REF unreachable (likely pruned after a squash-merge) -- falling back to main" >&2 \
            && git checkout main)) \
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
# `SKILL_EVAL_REAL_ABICHECK`/`SKILL_EVAL_SHIM` below are literal
# `ENV` values (Dockerfile `ENV` can't be assigned from a `RUN` step's own
# runtime output), so they assume `command -v abicheck` resolves to
# `/usr/local/bin/abicheck` -- true for a plain `pip install -e ".[dev]"`
# console-script install on this exact base image, verified rather than
# merely assumed: the `[ "$real" = /usr/local/bin/abicheck ]` guard fails
# the build loudly instead of silently shipping a shim at the wrong path
# with `ENV` pointing at a real binary that was never moved there (Codex
# review, fresh evidence).
RUN real="$(command -v abicheck)" \
    && [ "$real" = /usr/local/bin/abicheck ] \
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
#
# No separate `cp .../python3 .../python` step: `python:3.13-slim` already
# ships `/usr/local/bin/python` as a symlink to `python3`, so once `python3`
# is replaced with the interposer, `python` resolves through to it
# automatically -- a `cp` there would try to copy the interposer onto
# itself (`cp` resolves the destination symlink first) and fail the whole
# build with "are the same file" (Codex review, fresh evidence: confirmed
# against the base image's real layout, not assumed).
RUN mv /usr/local/bin/python3 /usr/local/bin/python3-real \
    && echo 'IyEvYmluL3NoCnNraXA9MApzZWVuPTAKZm9yIGFyZyBpbiAiJEAiOyBkbwogIGlmIFsgIiRza2lwIiA9IDEgXTsgdGhlbiBza2lwPTA7IHNlZW49JCgoc2VlbisxKSk7IGNvbnRpbnVlOyBmaQogIGNhc2UgIiRhcmciIGluCiAgICAtbSkKICAgICAgaWYgWyAiJChldmFsIGVjaG8gXCJcJHskKChzZWVuKzIpKX1cIikiID0gImFiaWNoZWNrIiBdOyB0aGVuCiAgICAgICAgc2hpZnQgJCgoc2VlbisyKSkKICAgICAgICBleGVjICIkU0tJTExfRVZBTF9TSElNIiAiJEAiCiAgICAgIGZpCiAgICAgIGJyZWFrCiAgICAgIDs7CiAgICAtbWFiaWNoZWNrKQogICAgICBzaGlmdCAkKChzZWVuKzEpKQogICAgICBleGVjICIkU0tJTExfRVZBTF9TSElNIiAiJEAiCiAgICAgIDs7CiAgICAtWHwtV3wtLWNoZWNrLWhhc2gtYmFzZWQtcHljcykgc2tpcD0xIDs7CiAgICAtYykgYnJlYWsgOzsKICAgIC0qKSA7OwogICAgKikgYnJlYWsgOzsKICBlc2FjCiAgc2Vlbj0kKChzZWVuKzEpKQpkb25lCmV4ZWMgIiRTS0lMTF9FVkFMX1JFQUxfUFlUSE9OIiAiJEAiCg==' | base64 -d > /usr/local/bin/python3 \
    && chmod +x /usr/local/bin/python3
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
