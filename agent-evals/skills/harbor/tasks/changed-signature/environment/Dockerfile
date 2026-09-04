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
ARG ABICHECK_REF=527e78b746a792f365a13cf7d2b62e963bfc9299
# ABICHECK_RUNTIME_DIGEST=180d964c18abac98965be45c8ab7f5b8072c62b80f979802ae9180062eca2ed2
# ^ content digest of `_RUNTIME_RELEVANT_PATHS` at generation time -- what
# `--check` (scripts/gen_harbor_tasks.py) actually compares to detect real
# staleness, independent of whether ABICHECK_REF above is still a
# resolvable commit. Not consumed at build time.
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
# `python:3.13-slim` ships `/usr/local/bin/python3` and `/usr/local/bin/
# python` as symlinks (`python -> python3 -> python3.13`) to the one real
# interpreter binary, `/usr/local/bin/python3.13` -- confirmed via
# `readlink -f`, not assumed. An earlier version of this step renamed only
# the `python3` symlink itself (leaving the real `python3.13` binary
# untouched at its own path), which correctly intercepted `python3`/
# `python` through the new interposer file it wrote, but left a direct
# `python3.13 -m abicheck` invocation -- a real, unmodified CPython
# spelling, not a hypothetical one -- reaching the untouched real binary
# and going completely unrecorded (Codex review, fresh evidence). Fixed by
# renaming and replacing the *real* binary file `python3`/`python` resolve
# through, rather than either symlink: `python3` and `python` then
# automatically resolve through to the same interposer with no separate
# write, since neither symlink itself was touched. The
# `[ "$resolved" = /usr/local/bin/python3.13 ]` guard fails the build
# loudly instead of silently shipping an interposer nothing resolves
# through, matching the identical guard style the abicheck shim install
# above already uses.
RUN resolved="$(readlink -f "$(command -v python3)")" \
    && [ "$resolved" = /usr/local/bin/python3.13 ] \
    && mv "$resolved" "$resolved-real" \
    && echo 'IyEvYmluL3NoCnNraXA9MApzZWVuPTAKZm9yIGFyZyBpbiAiJEAiOyBkbwogIGlmIFsgIiRza2lwIiA9IDEgXTsgdGhlbiBza2lwPTA7IHNlZW49JCgoc2VlbisxKSk7IGNvbnRpbnVlOyBmaQogIGNhc2UgIiRhcmciIGluCiAgICAtbSkKICAgICAgaWYgWyAiJChldmFsIGVjaG8gXCJcJHskKChzZWVuKzIpKX1cIikiID0gImFiaWNoZWNrIiBdOyB0aGVuCiAgICAgICAgc2hpZnQgJCgoc2VlbisyKSkKICAgICAgICBleGVjICIkU0tJTExfRVZBTF9TSElNIiAiJEAiCiAgICAgIGZpCiAgICAgIGJyZWFrCiAgICAgIDs7CiAgICAtbWFiaWNoZWNrKQogICAgICBzaGlmdCAkKChzZWVuKzEpKQogICAgICBleGVjICIkU0tJTExfRVZBTF9TSElNIiAiJEAiCiAgICAgIDs7CiAgICAtWHwtV3wtLWNoZWNrLWhhc2gtYmFzZWQtcHljcykgc2tpcD0xIDs7CiAgICAtYykgYnJlYWsgOzsKICAgIC0qKSA7OwogICAgKikgYnJlYWsgOzsKICBlc2FjCiAgc2Vlbj0kKChzZWVuKzEpKQpkb25lCmV4ZWMgIiRTS0lMTF9FVkFMX1JFQUxfUFlUSE9OIiAiJEAiCg==' | base64 -d > "$resolved" \
    && chmod +x "$resolved"
ENV SKILL_EVAL_REAL_PYTHON=/usr/local/bin/python3.13-real

# This sandbox's own castxml is routinely below abicheck's policy floor
# (agent-evals/skills/CLAUDE.md's "Environment prerequisites" section);
# degrade to direct-clang rather than hard-erroring, matching the existing
# harness's own documented workaround.
ENV ABICHECK_ALLOW_AST_FALLBACK=1

# The skill is deliberately never baked into this image -- an earlier
# version of this step did (`cp -r .../check-abi-compatibility /opt/skills/`,
# unconditionally, in every image regardless of arm), which a baseline
# trial's own agent -- with the same ordinary shell access to this
# container everything else in this file's comments already document --
# could discover and manually follow, contaminating the one arm this whole
# corpus exists to keep clean of the treatment (Codex review, fresh
# evidence). Confirmed against the real, installed `harbor` package's own
# source, not assumed: `Trial._upload_injected_skills()`
# (`harbor.trial.trial`) uploads a `--skill owner/repo:path[@ref]`-resolved
# skill directly into the *running* container, after the image has already
# started, entirely outside the Docker build -- a complete no-op when
# `--skill` isn't passed. A skill-arm trial therefore requests it via
# `harbor run ... --skill abicheck/abicheck:.claude/skills/check-abi-
# compatibility` (see `agent-evals/skills/harbor/CLAUDE.md`), which the
# real `harbor` package's own `--ak skills_dir=...`-driven `claude_code`
# adapter (`_build_register_skills_command`) then copies from
# Harbor's own resolved skills directory into Claude's config at trial
# start -- never from anything this Dockerfile wrote. A baseline trial
# never receives that upload at all, and the image itself now carries no
# skill content for a baseline agent to stumble onto.

# The agent has ordinary shell access to this whole clone for the entire
# agent phase -- so anything answer-bearing left under `/opt/abicheck-src`
# is readable by the very agent being evaluated on it (Codex review, fresh
# evidence). `git clone` above pulls the *whole* repository -- source,
# history, and every test file -- and this repo's ground truth for these
# 12 scenarios turns out not to live in one place: `agent-evals/skills/
# scenarios.yaml`/`skill-eval-pack.json` and every sibling task's own
# generated `harbor/tasks/<id>/tests/scenario.json` carry it directly,
# `examples/ground_truth.json` derives a Category A scenario's outcome,
# the raw `fixtures/` sources are sometimes comment-annotated with the
# answer, `agent-evals/skills/graders/` (`dimensions.py`/`evidence.py`)
# itself contains scenario-identifying comments naming specific
# fixtures/consumers, and `tests/test_skill_eval_graders_consumer_
# scoping.py`'s own hand-written unit-test fixtures independently restate
# real scenario data (a real consumer name, its real verdict/full_verdict
# pair) as example input for testing the grader.
#
# The graders and `verify_run.py` are therefore no longer part of this
# allowlist at all -- an earlier version of this fix staged them here
# reasoning "the agent's shell access is unavoidable, so just don't name
# the paths that reveal the answer," which is exactly backwards: no
# in-container obfuscation (a denylist of named paths, a different
# location, restrictive permissions) stops a process with ordinary shell
# access to its own filesystem from reading a file that is present on it
# -- `graders/dimensions.py`'s own scenario-identifying comments make this
# concrete, not hypothetical (Codex review, fresh evidence). The verifier
# now runs in a genuinely separate container the agent never has
# filesystem access to at all, built from `tests/Dockerfile`
# (`_verifier_dockerfile()`) via Harbor's native
# `[verifier].environment_mode = "separate"` (confirmed against the real,
# installed `harbor` package: `Task._validate_tests`/
# `resolve_effective_verifier_env_config` in `harbor.models.task.
# verifier_mode`, and `Trial._verifier_env_build_context()` in
# `harbor.trial.trial`, which builds the separate verifier's image from
# the task's own `tests/` directory, not `environment/`). This allowlist
# now keeps only what the *agent's* image still needs after this point --
# `abicheck/` itself (the installed package is editable, so its source
# must stay on disk at this exact path for imports to keep resolving) --
# moved into a staging directory, with the entire original clone (`.git`
# included -- see below) discarded and the staging directory taking its
# place. Nothing outside that allowlist can survive this step by
# construction, regardless of what it's named or which future PR adds it.
#
# `.git` needs the identical treatment for a distinct reason: deleting a
# tracked file's working-tree copy does not remove it from history, and
# `/opt/abicheck-src/.git` stayed present and agent-visible for the whole
# trial in an earlier version of this same fix -- `git -C /opt/abicheck-
# src show HEAD:agent-evals/skills/scenarios.yaml` (or `git log`/`git
# cat-file` against the same blob) recovered the exact answer straight
# out of history even after the working-tree file was gone (Codex review,
# fresh evidence). The allowlist above already excludes `.git` by
# omission -- nothing here moves it into staging -- so this is one
# mechanism closing both leaks, not two. Nothing after this point needs
# git any further: the version was already patched directly in the
# installed package metadata (no `setuptools_scm`/git-tag-derived
# versioning in this repo), and no later step runs a `git` command.
RUN set -eux; \
    cd /opt/abicheck-src; \
    mkdir -p /tmp/rt-keep; \
    mv abicheck /tmp/rt-keep/abicheck; \
    cd /; \
    rm -rf /opt/abicheck-src; \
    mv /tmp/rt-keep /opt/abicheck-src

WORKDIR /workspace
COPY workspace/ /workspace/
