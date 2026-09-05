#!/usr/bin/env python3
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Generate Harbor-format task directories from the G37 scenario pack.

ADR-058's Harbor-migration amendment is the design record for *why* this
exists; this docstring covers only what the generator itself does.

**One task per scenario, not per (scenario, arm).** Whether the skill is
installed is not a property of the *question* — it is a property of which
agent configuration answers it. Harbor's own `--skill owner/repo:path`
mechanism already models this split, confirmed by reading
`harbor.trial.trial.Trial._upload_injected_skills()`'s source directly (not
guessed from docs): it clones/resolves the skill on the orchestrator host
and uploads it into the *running* container after the image has already
started, entirely outside the Docker build, so an unrequested arm's image
never carries the skill's content at all — the generated task never bakes a
skill in. A baseline trial is `harbor run -a claude-code ...`; a skill
trial is the same command with `--skill abicheck/abicheck:.claude/skills/
check-abi-compatibility`. See `agent-evals/skills/harbor/CLAUDE.md`.

**Reuses, never re-derives, three things this repo already has right:**

1. `skill-eval-pack.json["scenarios"]` — the resolved prompt/expected/
   fixture-path triple, exactly as `runners/claude_code.py`'s own harness
   consumes it. A task's ground truth is one JSON blob copied into
   `tests/scenario.json`, not re-typed.
2. `runners.claude_code.strip_comments`/`demo_app_sources`/
   `EXPLANATORY_FILES` — the exact fixture-to-workspace transform the
   existing harness already applies (answer-bearing comments stripped, the
   case's own demo consumer excluded), imported directly so a future change
   to that transform is not something this file can silently drift from.
3. `runners.claude_code.ANSWER_CONTRACT` — the same claim-envelope contract,
   with one addendum appended (see `_FILE_ADDENDUM` below) telling the agent
   to persist its answer to a file, since a Harbor verifier reads the
   trial's filesystem, not its chat transcript.

**What is NOT reused, and why.** `dimension_1`'s skill-activation check
(`arm` parameter) has no Harbor equivalent yet — see `verify_run.py`'s own
docstring for why `arm=None` is correct here, not a gap silently dropped.

Run `python scripts/gen_harbor_tasks.py --check` to verify the committed
tree matches (same contract as `gen_skill_eval_pack.py --check`).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "agent-evals" / "skills"
sys.path.insert(0, str(EVAL_DIR))

from runners.claude_code import (  # noqa: E402
    _PYTHON_INTERPOSER,
    ANSWER_CONTRACT,
    EXPLANATORY_FILES,
    SOURCE_SUFFIXES,
    demo_app_sources,
    strip_comments,
)

PACK = EVAL_DIR / "skill-eval-pack.json"
TASKS_DIR = EVAL_DIR / "harbor" / "tasks"


def _write_lf(path: Path, content: str) -> None:
    """`path.write_text(content, encoding="utf-8")`, forcing LF line endings
    regardless of the host platform.

    Plain `write_text()` uses `newline=None`, which on Windows translates
    every `\\n` in *content* to `\\r\\n` on write -- a pure Python I/O
    behavior, independent of git. Without this, running this generator on
    a Windows machine produces a byte-for-byte different tree than running
    it on Linux/macOS for the identical logical content, which
    `_diff_trees`' byte comparison (and the digest `--check` embeds) would
    then read as real drift. Paired with `.gitattributes` forcing these
    same paths to `text eol=lf` on checkout (so a committed LF tree never
    gets CRLF-translated back on a Windows checkout either) -- together
    these make every file this generator writes byte-identical no matter
    which platform produced or checked it out, which is what lets
    `_runtime_relevant_digest()` hash real content directly rather than
    normalizing away a line-ending artifact (Codex review, fresh
    evidence -- an earlier revision normalized CRLF at hash time, which
    also silently hid a *genuine* CRLF-introducing edit to a runtime file,
    e.g. one that would break a shebang; closing the platform-dependence
    at its source instead keeps the digest sensitive to that).
    """
    path.write_text(content, encoding="utf-8", newline="\n")


#: Marker every generated file carries — `check_ai_readiness.py`'s
#: `generated-file-ownership` check requires one on every file a generator
#: owns. `.toml`/`.sh`/`.md` all accept a `#`-comment first line.
_MARKER = (
    "# GENERATED FILE -- do not hand-edit. Source: scripts/gen_harbor_tasks.py "
    "+ agent-evals/skills/skill-eval-pack.json. Regenerate with "
    "`python scripts/gen_harbor_tasks.py`.\n"
)

#: Appended to the shared `ANSWER_CONTRACT` -- the one behavioral difference
#: this migration requires of the agent, and the only reason a Harbor task's
#: instruction.md is not byte-identical to the existing harness's prompt.
_FILE_ADDENDUM = """
This trial is graded from files, not from chat: before you finish, write
your reply's fenced ```json block above verbatim to the file
`/workspace/final.md` as your last action (a heredoc or your file-write tool
both work) -- nothing you only say in the conversation is checked.
"""

#: `[environment].network_mode` for the agent phase. The image build (which
#: needs network for `apt`/`git`/`pip`) is a separate phase Harbor times and
#: gates independently (`build_timeout_sec`) -- this only restricts the
#: *agent's* runtime network, which the compiled-in toolchain never needs.
_AGENT_NETWORK_MODE = "no-network"


def _abicheck_ref() -> str:
    """The commit this task pack was generated from -- pinned, not floating.

    A generated task frozen at a stale ref is a known, visible staleness
    (this generator's own `--check` catches it, same contract as
    `skill-eval-pack.json`'s content digests); a Dockerfile that always
    clones `HEAD` of a branch would silently drift the moment abicheck's own
    graders change underneath an already-published task.

    **This reads `git rev-parse HEAD`, i.e. the current commit — never the
    working tree.** A regeneration run before the commit that carries its
    own output (the ordinary flow: edit, regenerate, `git add -A`, commit)
    necessarily pins the *parent* commit, one behind the one the generated
    files end up committed into (Codex review, PR #819, fresh evidence: an
    open PR's Harbor images built from its own HEAD would clone one commit
    older than that HEAD, so a real trial run against the exact tip of an
    open branch — not yet the squash-merged `main` case the paragraph above
    already covers — could silently evaluate stale skill/grader content
    with no fallback firing, since the stale-but-reachable parent commit
    checks out cleanly). This is a genuine fixed point, not an oversight to
    patch here: the ref cannot name "the commit this file is part of"
    before that commit exists, and re-deriving it (regenerate, commit,
    regenerate again, commit again, ...) never converges — each pass is
    still one commit behind the one it lands in. The mitigation is
    procedural, not code: run this generator as the *last* step before a
    push, so the residual lag is the smallest an SHA-pinned scheme can
    have, and rely on `--check` (which compares generated *content*, not
    the embedded ref) to still catch every other kind of staleness. Once a
    branch merges this residual gap doesn't linger: the pinned parent
    commit is normally still reachable from `main` right after merge (it's
    an ancestor of the squash commit), so the fallback below won't even be
    the mechanism that saves it — the *content* is simply one ordinary
    commit's worth of eventual drift, the same as any other generated
    artifact's `--check` gate exists to catch on the next regeneration.
    """
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _skill_version_floor() -> str:
    """Lower bound of the published skill's own declared `abicheck-version-range`.

    Read once at generation time, from the skill's real checked-in
    declaration, so the Dockerfile's evaluation-only metadata patch below
    can never hand-drift from it the next time the range changes.
    """
    skill_md = ROOT / ".claude" / "skills" / "check-abi-compatibility" / "SKILL.md"
    match = re.search(
        r'abicheck-version-range:\s*">=([0-9.]+)', skill_md.read_text(encoding="utf-8")
    )
    if not match:
        raise RuntimeError(f"could not parse abicheck-version-range from {skill_md}")
    return match.group(1)


def _dockerfile(ref: str, version_floor: str, runtime_digest: str) -> str:
    return f"""{_MARKER}FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \\
        build-essential gcc g++ clang castxml git ca-certificates \\
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
ARG ABICHECK_REF={ref}
# ABICHECK_RUNTIME_DIGEST={runtime_digest}
# ^ content digest of `_RUNTIME_RELEVANT_PATHS` at generation time -- what
# `--check` (scripts/gen_harbor_tasks.py) actually compares to detect real
# staleness, independent of whether ABICHECK_REF above is still a
# resolvable commit. Not consumed at build time.
RUN git clone https://github.com/abicheck/abicheck.git /opt/abicheck-src \\
    && cd /opt/abicheck-src \\
    && (git checkout "$ABICHECK_REF" 2>/dev/null \\
        || (echo "WARNING: pinned ref $ABICHECK_REF unreachable (likely pruned after a squash-merge) -- falling back to main" >&2 \\
            && git checkout main)) \\
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
RUN set -eux; \\
    EGG=/opt/abicheck-src/abicheck.egg-info/PKG-INFO; \\
    DIST=$(python3 -c "import importlib.metadata as m; print(next(str(d._path) for d in m.distributions() if d.metadata['Name']=='abicheck'))")/METADATA; \\
    sed -i "s/^Version: .*/Version: {version_floor}/" "$EGG" "$DIST"

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
RUN real="$(command -v abicheck)" \\
    && [ "$real" = /usr/local/bin/abicheck ] \\
    && mv "$real" "$real-real" \\
    && cp /opt/abicheck-src/agent-evals/skills/shim/abicheck "$real" \\
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
RUN resolved="$(readlink -f "$(command -v python3)")" \\
    && [ "$resolved" = /usr/local/bin/python3.13 ] \\
    && mv "$resolved" "$resolved-real" \\
    && echo '{base64.b64encode(_PYTHON_INTERPOSER.encode()).decode()}' | base64 -d > "$resolved" \\
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
# `catalog/ground_truth.json` derives a Category A scenario's outcome,
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
RUN set -eux; \\
    cd /opt/abicheck-src; \\
    mkdir -p /tmp/rt-keep; \\
    mv abicheck /tmp/rt-keep/abicheck; \\
    cd /; \\
    rm -rf /opt/abicheck-src; \\
    mv /tmp/rt-keep /opt/abicheck-src

WORKDIR /workspace
COPY workspace/ /workspace/
"""


def _verifier_dockerfile(ref: str, runtime_digest: str) -> str:
    """`tests/Dockerfile` -- the separate verifier's own image (Codex review).

    Harbor builds a `[verifier].environment_mode = "separate"` verifier's
    image from the task's own `tests/` directory, not `environment/`
    (confirmed against the real, installed `harbor` package: `Trial.
    _verifier_env_build_context()` in `harbor.trial.trial`) -- so this is a
    genuinely distinct container from `_dockerfile()`'s agent image, sharing
    no filesystem with it. That separation is the actual fix for the
    ground-truth leak `_dockerfile()`'s own comment above describes: the
    agent process never has shell access to this container at all, so the
    answer-bearing `agent-evals/skills/graders/` and `verify_run.py` no
    longer need to be hidden from it by naming, obfuscation, or omission --
    they simply aren't reachable.
    `verify_run.py` itself imports only the standard library plus `graders/`
    -- but `graders/evidence.py`'s own `_option_tables()` has a lazy,
    try/except-guarded `import click; from abicheck.cli import main` used to
    read the *real* Click command tree (which options take a value, which
    are bare flags) so a call like `compare old.so --verbose old.so` isn't
    misread as a two-operand comparison. Neither `click` nor `abicheck`
    being importable in this image was missed in an earlier version of this
    function -- the caught `ImportError` silently degrades to the documented
    conservative fallback ("every option takes a value"), which can hide a
    real self-comparison from `dimension_2`/`dimension_6`'s zero-tolerance
    checks (Codex review, fresh evidence: `--verbose old.so` then reads as
    one operand consumed by the flag, not two identical ones). Fixed by
    giving this image the same `pip install -e .` `abicheck` has in the
    agent image -- deliberately the bare install, not `.[dev]`, since only
    the real Click command tree is needed here, not the dev toolchain --
    which needs no compiler (`click`/`rich-click`/`pyelftools` are all pure
    Python wheels), so `git`/`ca-certificates` alone are still enough apt
    packages.
    """
    return f"""{_MARKER}FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \\
        git ca-certificates \\
    && rm -rf /var/lib/apt/lists/*

# Same pin, same fallback-to-main rationale, and the same runtime digest as
# `_dockerfile()`'s agent image -- see that function's own comments for why
# each exists. `_normalize_pinned_ref`/`_DIGEST_LINE` in this generator are
# file-path-agnostic, so the identical two-line convention here is picked up
# by `--check` without any further changes to that comparison machinery.
ARG ABICHECK_REF={ref}
# ABICHECK_RUNTIME_DIGEST={runtime_digest}
RUN git clone https://github.com/abicheck/abicheck.git /opt/abicheck-src \\
    && cd /opt/abicheck-src \\
    && (git checkout "$ABICHECK_REF" 2>/dev/null \\
        || (echo "WARNING: pinned ref $ABICHECK_REF unreachable (likely pruned after a squash-merge) -- falling back to main" >&2 \\
            && git checkout main)) \\
    && pip install --no-cache-dir -e "."

# The verifier's own allowlist: only what `verify_run.py` actually needs at
# runtime -- itself, the `graders/` package it imports, and `abicheck/`
# itself (the editable install above needs its source to persist at this
# exact path for imports to keep resolving, same reason the agent image
# keeps it -- `graders/evidence.py`'s own `_option_tables()` is the actual
# consumer, see this function's own docstring) -- survives. Everything else
# (including `.git`, for the identical history-leak reason `_dockerfile()`'s
# own comment gives -- though there is no agent in this container to read it
# from, keeping the discipline identical avoids this image quietly becoming
# the one place that reasoning doesn't hold) is discarded. This container is
# never agent-visible, but the allowlist convention is kept anyway rather
# than trusted-by-isolation-alone: a correct architecture and a minimal
# attack surface are not substitutes for each other.
RUN set -eux; \\
    cd /opt/abicheck-src; \\
    mkdir -p /tmp/rt-keep/agent-evals/skills/harbor; \\
    mv abicheck /tmp/rt-keep/abicheck; \\
    mv agent-evals/skills/graders /tmp/rt-keep/agent-evals/skills/graders; \\
    mv agent-evals/skills/harbor/verify_run.py /tmp/rt-keep/agent-evals/skills/harbor/verify_run.py; \\
    cd /; \\
    rm -rf /opt/abicheck-src; \\
    mv /tmp/rt-keep /opt/abicheck-src
"""


def _task_toml(scenario_id: str, scenario: dict) -> str:
    category = scenario.get("category", "?")
    note = (scenario.get("expected") or {}).get("resolved_from") or scenario.get(
        "coverage_note", ""
    )
    description = scenario["prompt"].strip().splitlines()[0][:200]
    keywords = ["abi", "abicheck", f"category-{category.lower()}"]
    keywords_toml = ", ".join(json.dumps(k) for k in keywords)
    return f"""{_MARKER}schema_version = "1.4"
artifacts = []

[task]
name = "abicheck/{scenario_id}"
version = "1.0.0"
description = {json.dumps(description)}
keywords = [{keywords_toml}]
[[task.authors]]
name = "abicheck"

[metadata]
category = {json.dumps(category)}
coverage_note = {json.dumps(note)}
generated_from = "agent-evals/skills/scenarios.yaml"

[verifier]
timeout_sec = 180.0
collect = []
# The verifier runs in its own container, built from `tests/Dockerfile`
# (`_verifier_dockerfile()`), never the agent's -- confirmed against the
# real `harbor` package: `resolve_effective_verifier_env_config` (`harbor.
# models.task.verifier_mode`) treats a `None` `[verifier.environment]` under
# `environment_mode = "separate"` as "use a fresh copy of the top-level
# [environment]", so no explicit `[verifier.environment]` block is needed
# here. This is the actual fix for the ground-truth leak documented in
# `_dockerfile()`'s own comments: the agent process never has filesystem
# access to this container, so the graders it builds no longer need to be
# hidden from the agent by naming or omission.
environment_mode = "separate"

[verifier.env]

[agent]
timeout_sec = 1800.0

[environment]
network_mode = {json.dumps(_AGENT_NETWORK_MODE)}
build_timeout_sec = 900.0
os = "linux"
mcp_servers = []

[environment.env]

[solution.env]
"""


def _instruction_md(scenario: dict) -> str:
    return scenario["prompt"].strip() + "\n" + ANSWER_CONTRACT + _FILE_ADDENDUM


def _readme(scenario_id: str, scenario: dict) -> str:
    return (
        f"{_MARKER}\n# abicheck/{scenario_id}\n\n"
        f"Category {scenario.get('category', '?')} scenario from the G37 evaluation "
        f"corpus (`agent-evals/skills/scenarios.yaml`). Generated, not hand-authored "
        "-- see `scripts/gen_harbor_tasks.py`.\n"
    )


def _test_sh(scenario: dict) -> str:
    architectures = scenario.get("architectures") or []
    arch_guard = ""
    if architectures:
        # Mirrors `runners.claude_code.host_architecture()`'s canonicalization
        # (`aarch64`/`arm64` are the same architecture under two spellings) --
        # a fixture declaring `architectures` embeds an architecture-specific
        # prebuilt artifact (Codex review), so running it unguarded on a
        # mismatched host would silently grade a cross-architecture-break
        # artifact as if it were the intended shallow-evidence result. This
        # host and Docker's own build platform are the same one Harbor
        # scheduled the trial on -- no cross-platform Docker emulation is
        # assumed or handled here.
        allowed_shell = " ".join(architectures)  # plain identifiers, no quoting needed
        # The JSON list is passed as its own argv token (single-quoted on
        # the bash side, `json.loads()`'d on the python side), never spliced
        # into the double-quoted `python3 -c "..."` string itself -- an
        # earlier version of this guard did the latter and reproducibly
        # broke: the embedded list's own `"` characters prematurely closed
        # the outer bash double-quoted argument, leaving a bare `x86_64`
        # token concatenated back in as literal Python source
        # (`NameError: name 'x86_64' is not defined`).
        #
        # Deliberately writes NO reward file at all on a mismatch, rather
        # than a scored `reward=0` -- verified directly against the real
        # `harbor` package's own `Verifier.verify()`: when neither
        # `reward.txt` nor `reward.json` exists it raises
        # `RewardFileNotFoundError`, which `TrialResult`/`StepResult`
        # record as `exception_info` on a trial whose `verifier_result`
        # stays `None` -- structurally distinct from a real, scored 0. A
        # written `reward=0` (the first version of this guard) would have
        # counted an environment mismatch as a failed agent trial in every
        # arm on a non-x86_64 Harbor host, depressing aggregate scores and
        # eliminating this scenario's own intended measurement (Codex
        # review, fresh evidence, second round). `set -euo pipefail` above
        # means the plain `exit 1` here is enough on its own; the
        # diagnostic before it is for a human reading the trial's own
        # stdout, not for Harbor's own scoring.
        arch_guard = f"""
host_arch="$(uname -m)"
case "$host_arch" in
    aarch64) host_arch=arm64 ;;
esac
case " {allowed_shell} " in
    *" $host_arch "*) ;;
    *)
        python3 -c "import json,sys; print('architecture_mismatch: host is ' + sys.argv[1] + ', task requires one of ' + repr(json.loads(sys.argv[2])), file=sys.stderr)" \\
            "$host_arch" '{json.dumps(architectures)}'
        exit 1
        ;;
esac
"""
    return f"""#!/bin/bash
{_MARKER}
set -euo pipefail
{arch_guard}
mkdir -p /logs/verifier
python3 /opt/abicheck-src/agent-evals/skills/harbor/verify_run.py \\
    --workspace /workspace \\
    --scenario /tests/scenario.json \\
    --reward-txt /logs/verifier/reward.txt \\
    --reward-json /logs/verifier/reward.json
"""


def _readme_abicheck_command(fixture: Path) -> tuple[str, str] | None:
    """The case README's own build+compare recipe, split for reuse.

    Category A scenarios are drawn straight from `catalog/cases/case*/`, whose
    README is the *validated* source of the case's own ground truth
    (`ground_truth.json` is checked against exactly this command, per
    `tests/validate_examples.py`) -- reusing it verbatim is strictly more
    trustworthy than this generator re-deriving a compile/compare
    invocation from the fixture's file names.

    Returns `(full_block, run_line)`. `run_line` is the compare invocation
    reconstructed as one logical shell line -- a leading env-var prefix
    (e.g. `ABICHECK_AST_FRONTEND=clang `), `abicheck compare <old> <new>`,
    and any trailing flags the case's own documented command needs
    (`--header old=... --header new=...`, `--contract ...`) -- collapsed
    from however many physical, backslash-continued lines the README
    itself wraps it across. A case's own extra flags are not optional
    decoration: `case124`'s constant-value comparison is genuinely
    `NO_CHANGE` at binary depth and only `API_BREAK` with `--header`
    evidence, so a reference solution that dropped them would silently
    reproduce the wrong verdict rather than the documented one. `None`
    when the block is missing or carries no `abicheck compare` line (e.g.
    a case documented only via `abicheck scan`), in which case `_solve_sh`
    falls back to its unimplemented stub rather than guessing.
    """
    readme = fixture / "README.md"
    if not readme.is_file():
        return None
    text = readme.read_text(encoding="utf-8")
    section = text.split("## abicheck command", 1)
    if len(section) != 2:
        return None
    match = re.search(r"```bash\n(.*?)```", section[1], re.DOTALL)
    if not match:
        return None
    block = match.group(1).strip()
    # Collapse a `\`-continued command across physical lines into one
    # logical line before matching -- the README wraps a long invocation
    # (env prefix + compare + several `--header`/`--contract` flags) for
    # readability, but the shell (and this parser) must see it as one
    # statement.
    joined = re.sub(r"[ \t]*\\\n[ \t]*", " ", block)
    compare_match = re.search(
        r"^((?:\w+=\S+\s+)*abicheck compare\s+\S+\s+\S+(?:\s+\S+)*)\s*$",
        joined,
        re.MULTILINE,
    )
    if not compare_match:
        return None
    return block, compare_match.group(1)


def _solve_sh(scenario_id: str, scenario: dict) -> str:
    """A best-effort reference solution.

    Real for Category A: the exact, already-validated compile+compare
    command from the case's own README (`_readme_abicheck_command`), run
    from the workspace and its real verdict captured into `final.md` --
    not a heuristic re-derivation that could silently diverge from what
    actually produced `ground_truth.json`.

    Not attempted for Category B: each fixture's correct invocation was
    verified by hand when its scenario was promoted to `ready` (see the
    PR history for `agent-evals/skills/fixtures/`), but that command isn't
    recorded anywhere this generator can read it back from -- a stub
    explaining why beats silently guessing one for a corpus dominated by
    scoped/uncertain-outcome scenarios, where a wrong guess reads as a
    confidently wrong reference answer instead of an honest gap.
    """
    if scenario.get("category") == "A":
        parsed = _readme_abicheck_command(ROOT / scenario["inputs"])
        if parsed is not None:
            block, run_line = parsed
            # Only the build lines -- the block's own compare invocation
            # (its `abicheck compare ...` line, plus any backslash-continued
            # follow-on lines carrying its trailing flags) is skipped here
            # and re-run below with --format json instead of run twice; a
            # BREAKING case's plain-text run also exits non-zero (exit 4),
            # which `set -e` would otherwise treat as this script failing
            # before it ever reaches the JSON rerun.
            build_lines_list: list[str] = []
            in_compare_stmt = False
            compare_start_re = re.compile(r"^(?:\w+=\S+\s+)*abicheck compare\b")
            for line in block.splitlines():
                stripped = line.strip()
                if not in_compare_stmt and compare_start_re.match(stripped):
                    in_compare_stmt = True
                if in_compare_stmt:
                    if not stripped.endswith("\\"):
                        in_compare_stmt = False
                    continue
                build_lines_list.append(line)
            build_lines = "\n".join(build_lines_list)
            return f"""#!/bin/bash
{_MARKER}
set -euo pipefail
cd /workspace/library

# The case's own documented build recipe, verbatim:
{build_lines}

# The case's own documented comparison, re-run with --format json so the
# verdict can be read back programmatically. `compare`'s own exit code
# encodes the verdict (e.g. 4 = BREAKING) -- non-zero is a real result,
# not a failure, and `set -e` must not treat it as one; the report file
# on disk is what this script actually reads. Any env-var prefix and
# trailing flags (--header, --contract, ...) the README's own command
# carries are preserved -- some cases (e.g. a header-only-visible source
# break) only produce their documented verdict with those flags present.
# A fresh `mktemp` path, not a fixed name: a real trial's own container is
# isolated, so a shared name would be harmless there, but this exact script
# is also executed directly (not through a container) by tests/
# test_gen_harbor_tasks.py's TestSolveScriptsEndToEnd, once per scenario --
# a fixed `/tmp/report.json` is one shared file across every one of those
# invocations, and pytest-xdist is free to schedule two of them onto
# different workers at the same time, so one scenario's write can race
# another's read of the identical path (reproduced as a real, host/
# scheduling-dependent CI failure, not a hypothetical). `mktemp` gives every
# invocation -- test or real trial -- its own path, closing the race at its
# actual cause instead of only in the test harness that happened to expose it.
report_json="$(mktemp)"
{run_line} --format json -o "$report_json" || true
verdict=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['verdict'])" "$report_json")
cat > /workspace/final.md <<EOF
Reference solution -- the documented command for this case:

\\`\\`\\`
{block}
\\`\\`\\`

reported $verdict.

\\`\\`\\`json
{{"verdict": "$verdict", "evidence": [0], "confident": true}}
\\`\\`\\`
EOF
"""
    return f"""#!/bin/bash
{_MARKER}
set -euo pipefail
# No generic reference solution for {scenario_id}: this Category B
# fixture's correct invocation (a specific --used-by/--required-symbol/
# --contract choice, per tests/scenario.json's own "invocation" block) was
# verified by hand when the scenario was promoted to `ready` but is not
# recorded anywhere this generator can read it back from. Left
# unimplemented rather than guessing a command that could silently produce
# a confidently wrong reference answer.
exit 1
"""


def _prepared_library(fixture: Path, dest: Path) -> None:
    """The same fixture -> workspace transform `_prepare_workspace` applies.

    Duplicated in shape only because the destination differs (a task
    directory on disk here, a disposable run directory there) -- the actual
    transform (which files are excluded, which have comments stripped) is
    the imported functions, not a re-implementation of them.
    """
    import shutil

    apps = [Path(name).name for name in demo_app_sources(fixture)]
    excluded = (*EXPLANATORY_FILES, *apps)
    shutil.copytree(fixture, dest, ignore=shutil.ignore_patterns(*excluded))
    for path in sorted(dest.rglob("*")):
        # `SOURCE_SUFFIXES`, imported, not a re-typed literal -- this is
        # exactly the kind of drift this module's own docstring says
        # importing `runners.claude_code`'s transform is meant to prevent
        # (Codex review, fresh evidence): a hand-copied tuple here could
        # silently stop matching the real harness's own suffix list.
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        stripped = strip_comments(path.read_text(encoding="utf-8"))
        if stripped is not None:
            _write_lf(path, stripped)


def generate(check: bool = False) -> bool:
    """Write every ready scenario's Harbor task. Returns whether anything changed."""
    import shutil
    import tempfile

    pack = json.loads(PACK.read_text(encoding="utf-8"))
    scenarios = {
        sid: s for sid, s in pack["scenarios"].items() if s.get("status") == "ready"
    }
    ref = _abicheck_ref()
    version_floor = _skill_version_floor()
    runtime_digest = _runtime_relevant_digest()

    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / "tasks"
        staging.mkdir()
        for scenario_id, scenario in sorted(scenarios.items()):
            task_dir = staging / scenario_id
            (task_dir / "environment").mkdir(parents=True)
            (task_dir / "tests").mkdir()
            (task_dir / "solution").mkdir()

            _write_lf(task_dir / "task.toml", _task_toml(scenario_id, scenario))
            _write_lf(task_dir / "instruction.md", _instruction_md(scenario))
            _write_lf(task_dir / "README.md", _readme(scenario_id, scenario))
            _write_lf(
                task_dir / "environment" / "Dockerfile",
                _dockerfile(ref, version_floor, runtime_digest),
            )
            _prepared_library(
                ROOT / scenario["inputs"],
                task_dir / "environment" / "workspace" / "library",
            )
            _write_lf(
                task_dir / "tests" / "Dockerfile",
                _verifier_dockerfile(ref, runtime_digest),
            )
            _write_lf(task_dir / "tests" / "test.sh", _test_sh(scenario))
            _write_lf(
                task_dir / "tests" / "scenario.json",
                json.dumps(scenario, indent=2, sort_keys=True) + "\n",
            )
            solve = task_dir / "solution" / "solve.sh"
            _write_lf(solve, _solve_sh(scenario_id, scenario))
            solve.chmod(0o755)
            (task_dir / "tests" / "test.sh").chmod(0o755)

        if check:
            if not TASKS_DIR.is_dir():
                print(
                    f"ERROR: {TASKS_DIR} does not exist -- run without --check first."
                )
                return False
            changed = _diff_trees(TASKS_DIR, staging)
            if changed:
                print("ERROR: agent-evals/skills/harbor/tasks/ is out of date.")
                print(
                    "       Run `python scripts/gen_harbor_tasks.py` and commit the result."
                )
                for line in changed:
                    print(f"       {line}")
                return False
            print("agent-evals/skills/harbor/tasks/ is up to date")
            return True

        if TASKS_DIR.exists():
            shutil.rmtree(TASKS_DIR)
        TASKS_DIR.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(staging, TASKS_DIR)
        print(f"wrote {len(scenarios)} task(s) to {TASKS_DIR}")
        return True


_REF_LINE = re.compile(rb"^ARG ABICHECK_REF=([0-9a-f]{40})$", re.MULTILINE)
_DIGEST_LINE = re.compile(rb"^# ABICHECK_RUNTIME_DIGEST=([0-9a-f]{64})$", re.MULTILINE)

#: Files the built Docker image actually depends on *at runtime* --
#: cloned fresh from git at `ARG ABICHECK_REF`, never copied into the
#: static template text `_diff_trees` otherwise compares byte-for-byte.
#: Scoped narrowly: `abicheck/` itself is deliberately excluded (the ref
#: exists precisely to pin *which abicheck version* gets built, so that
#: axis drifting is the whole point, not something to flag), and
#: `scripts/gen_harbor_tasks.py`'s own logic changing is already caught by
#: the ordinary byte comparison (it changes what task.toml/instruction.md/
#: etc. actually contain).
#:
#: `.claude/skills/check-abi-compatibility` previously belonged here --
#: `_dockerfile()` used to `cp -r` this exact generated tree into every
#: image's `/opt/skills/` for a skill-arm trial, so a skill-content-only
#: edit needed tracking here for `--check` to catch it (a real gap this
#: same list once closed, Codex review). That bake-in step was removed
#: entirely in a later round (Codex review, fresh evidence): baking the
#: skill into *every* image regardless of arm meant a baseline trial's own
#: agent -- with the same ordinary shell access every other finding in
#: `_dockerfile()`'s own comments already documents -- could discover and
#: manually follow the treatment, contaminating the baseline arm. The
#: skill is now injected per-trial by Harbor's own `--skill` mechanism
#: (`Trial._upload_injected_skills()`, entirely outside the Docker build),
#: so no generated task file depends on the skill's content any more --
#: `.claude/skills/check-abi-compatibility` is correctly absent from this
#: list, not by oversight.
_RUNTIME_RELEVANT_PATHS = (
    "agent-evals/skills/graders",
    "agent-evals/skills/shim",
    "agent-evals/skills/harbor/verify_run.py",
)


def _normalize_pinned_ref(data: bytes) -> bytes:
    """Blank out the one line that is *expected* to differ between any two
    generations: the pinned commit SHA.

    `--check`'s job is catching genuine template/logic drift, not "time
    passed and HEAD moved" -- and it always will have moved by the time a
    freshly-generated tree is compared against one that was itself
    committed at an *earlier* HEAD (the committed tree's own commit
    necessarily postdates the SHA baked into it, since the SHA is computed
    before the commit that carries it exists). Without this normalization,
    `--check` could never pass on any commit after the one that generated
    the tree -- a real bug found by actually re-running `--check` after
    editing an unrelated file, not a hypothetical.

    Applied unconditionally, unlike an earlier revision of this function
    that only normalized when a companion "is this drift tolerable" check
    agreed. That companion check answered the question via `git rev-parse
    --verify`/`git diff --name-only` against the *pinned commit itself* --
    two distinct, both-real failure modes, not one:

    1. A pinned ref that is still a real, reachable commit (e.g. this
       branch's own previous commit) with a genuine, un-regenerated change
       to `graders/`/the shim/`verify_run.py` behind it: `git diff` finds
       the change and correctly reports every task stale -- this is the
       check doing its job, reproduced directly in this PR's own history
       (edit `graders/dimensions.py`, forget to regenerate, `--check`
       fails, exactly as intended).
    2. A pinned ref this checkout's local history has never heard of at
       all -- not "resolves but shows no diff", genuinely absent, which is
       what a squash-merge produces the moment a source branch's own
       commits stop existing anywhere reachable from `main`
       (`_dockerfile()`'s own "Falls back to `main`" comment already
       documents the runtime half of this same fact). `git rev-parse
       --verify -q <40-hex-chars>` does *not* reliably fail for this case
       (a syntactically valid SHA-shaped string can report success without
       the object actually existing); the `git diff --name-only
       <ref>..HEAD` call right after it does, but with `check=True` that
       raises `CalledProcessError` -- so `--check` didn't degrade to a
       wrong answer here, it **crashed** with an unhandled exception the
       instant it ran against a checkout that no longer had the pinned
       commit (Codex review, third round; confirmed directly by replaying
       the old function's body against a real fabricated 40-hex-char
       string that isn't any object in this repository).

    Fixed by moving the actual drift-relevant question -- "did
    `_RUNTIME_RELEVANT_PATHS` change since this was generated" -- onto
    `_DIGEST_LINE`, a content digest embedded in the file itself rather
    than a commit reference resolved through git history. The SHA line
    can therefore always be blanked before comparison: if nothing
    runtime-relevant changed, the digest line (unaffected by this
    normalization) already matches on its own -- case 1's real-drift
    detection survives unchanged, and case 2's crash cannot happen because
    nothing here ever asks git to resolve the pinned ref at all.
    """
    return _REF_LINE.sub(b"ARG ABICHECK_REF=<normalized-for-diff>", data)


#: Generated, environment-dependent noise that can appear under
#: `_RUNTIME_RELEVANT_PATHS` without any real source change -- excluded from
#: the digest so its presence/absence/content (all three vary by interpreter
#: version, import order, and whether anything has imported these modules
#: yet in this process) can never move the answer. Confirmed reproducible,
#: not hypothetical: running this repo's own test suite before regenerating
#: leaves `graders/__pycache__/*.pyc` and `shim/__pycache__/*.pyc` on disk,
#: and computing the digest with vs. without them present produces two
#: different hex strings for the identical source tree (Codex review, fresh
#: evidence) -- `--check` would then fail on a perfectly fresh checkout
#: purely because CI happened to import `graders.claim` (e.g. from an
#: earlier pytest step in the same job) before running this generator.
_DIGEST_IGNORED_DIR_NAMES = frozenset({"__pycache__"})
_DIGEST_IGNORED_SUFFIXES = frozenset({".pyc", ".pyo"})


def _runtime_relevant_files(root: Path = ROOT) -> list[Path]:
    """Every file under `_RUNTIME_RELEVANT_PATHS`, sorted for determinism.

    Skips `__pycache__` directories and `.pyc`/`.pyo` files -- see
    `_DIGEST_IGNORED_DIR_NAMES`'s own comment for why these must never
    reach `_runtime_relevant_digest`.
    """
    files: list[Path] = []
    for rel in _RUNTIME_RELEVANT_PATHS:
        path = root / rel
        if path.is_dir():
            for p in path.rglob("*"):
                if not p.is_file():
                    continue
                if _DIGEST_IGNORED_DIR_NAMES & set(p.relative_to(path).parts[:-1]):
                    continue
                if p.suffix in _DIGEST_IGNORED_SUFFIXES:
                    continue
                files.append(p)
        elif path.is_file():
            files.append(path)
    return sorted(files)


def _runtime_relevant_digest(root: Path = ROOT) -> str:
    """Content digest of everything the built image actually depends on at
    runtime (`_RUNTIME_RELEVANT_PATHS`).

    Embedded in every generated Dockerfile (`_DIGEST_LINE`) as the real
    signal `--check` uses to tell benign ref drift (HEAD moved, nothing
    relevant changed) from real staleness (a `graders/`/shim/`verify_run.py`
    edit landed with no regeneration) -- see `_normalize_pinned_ref`'s
    docstring for why a commit-reachability check (the previous design)
    cannot answer this reliably once the pinned commit is pruned. A
    content digest needs no git history at all: it is a pure function of
    the files on disk right now, so it produces the identical answer
    whether the pinned commit is three commits back, unreachable after a
    squash-merge, or this is a shallow clone with no history beyond `HEAD`.

    Hashes raw bytes with no line-ending normalization -- deliberately: an
    earlier revision collapsed `\\r\\n` to `\\n` before hashing to paper over
    a real platform-dependence bug (a checkout on a platform whose git
    defaults to CRLF translation, e.g. GitHub's windows-latest runners,
    handed these files back with every `\\n` already turned into `\\r\\n`,
    so a digest recomputed there could never match the one already
    committed). That normalization was itself wrong (Codex review, fresh
    evidence): it also silently hid a *genuine* CRLF-introducing edit to a
    runtime-relevant file -- e.g. one that would break a shebang in the
    real built image -- since such an edit would then hash identically to
    its LF original. The actual bug was platform-dependent I/O, not a
    digest that needed to tolerate it: `.gitattributes` now pins every
    path under `_RUNTIME_RELEVANT_PATHS` (and the generated task tree) to
    `text eol=lf`, so a checkout never CRLF-translates them regardless of
    platform, and `_write_lf()` (this module's own write helper) forces
    the identical LF bytes when *this generator itself* runs on Windows.
    Together those two close the platform-dependence at its source, so
    this function can hash raw bytes directly and stay sensitive to a
    real content change of any kind, line-ending changes included.
    """
    hasher = hashlib.sha256()
    for path in _runtime_relevant_files(root):
        # .as_posix(), not str(): str() renders a Windows PureWindowsPath
        # with backslashes, so hashing it would make the digest differ by
        # platform even once the content bytes themselves are LF-stable
        # (CodeRabbit review, fresh evidence).
        hasher.update(path.relative_to(root).as_posix().encode())
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
        hasher.update(b"\0")
    return hasher.hexdigest()


def _extract_committed_digest(committed: Path) -> str | None:
    """The `ABICHECK_RUNTIME_DIGEST` recorded in an already-committed task
    tree -- diagnostic/test use only, not read by `_diff_trees` itself
    (plain byte comparison already surfaces a differing digest line as
    ordinary content drift). Read from the first Dockerfile found, same
    convention as the pinned ref used to be read under. `None` for a tree
    with no Dockerfile yet.
    """
    for dockerfile in sorted(committed.rglob("environment/Dockerfile")):
        match = _DIGEST_LINE.search(dockerfile.read_bytes())
        if match:
            return match.group(1).decode()
    return None


def _diff_trees(committed: Path, generated: Path) -> list[str]:
    committed_files = {
        p.relative_to(committed) for p in committed.rglob("*") if p.is_file()
    }
    generated_files = {
        p.relative_to(generated) for p in generated.rglob("*") if p.is_file()
    }
    diffs = []
    for rel in sorted(committed_files - generated_files):
        diffs.append(f"only in committed tree: {rel}")
    for rel in sorted(generated_files - committed_files):
        diffs.append(f"only in generated tree: {rel}")
    for rel in sorted(committed_files & generated_files):
        committed_path = committed / rel
        generated_path = generated / rel
        a = _normalize_pinned_ref(committed_path.read_bytes())
        b = _normalize_pinned_ref(generated_path.read_bytes())
        if a != b:
            diffs.append(f"content differs: {rel}")
        # Bytes-only comparison would miss the executable bit -- `tests/
        # test.sh` and `solution/solve.sh` are explicitly chmod'd 0o755 at
        # generation time (Harbor executes them directly), and a committed
        # file that lost that bit (e.g. a manual re-save) would otherwise
        # pass `--check` as "current" while being unusable (Codex review).
        committed_executable = bool(committed_path.stat().st_mode & 0o111)
        generated_executable = bool(generated_path.stat().st_mode & 0o111)
        if committed_executable != generated_executable:
            diffs.append(f"executable bit differs: {rel}")
    return diffs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="verify without writing; exit 1 on drift"
    )
    args = parser.parse_args(argv)
    ok = generate(check=args.check)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
