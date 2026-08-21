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
agent configuration answers it. Harbor's own `claude-code` agent adapter
already models this split (`[environment].skills_dir`, copied into the
agent's own skills directory at trial start, confirmed by reading
`harbor.agents.installed.claude_code`'s source directly, not guessed from
docs) — so the generated task never bakes a skill in. A baseline trial is
`harbor run -a claude-code ...`; a skill trial is the same command with
`--ak skills_dir=/opt/skills`. See `agent-evals/skills/harbor/CLAUDE.md`.

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
    demo_app_sources,
    strip_comments,
)

PACK = EVAL_DIR / "skill-eval-pack.json"
TASKS_DIR = EVAL_DIR / "harbor" / "tasks"

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


def _dockerfile(ref: str, version_floor: str) -> str:
    return f"""{_MARKER}FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \\
        build-essential gcc g++ castxml git ca-certificates \\
    && rm -rf /var/lib/apt/lists/*

# Pinned to the commit this task pack was generated from -- see
# `_abicheck_ref()` in scripts/gen_harbor_tasks.py.
ARG ABICHECK_REF={ref}
RUN git clone https://github.com/abicheck/abicheck.git /opt/abicheck-src \\
    && cd /opt/abicheck-src && git checkout "$ABICHECK_REF" \\
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
RUN real="$(command -v abicheck)" \\
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
RUN mv /usr/local/bin/python3 /usr/local/bin/python3-real \\
    && echo '{base64.b64encode(_PYTHON_INTERPOSER.encode()).decode()}' | base64 -d > /usr/local/bin/python3 \\
    && cp /usr/local/bin/python3 /usr/local/bin/python \\
    && chmod +x /usr/local/bin/python3 /usr/local/bin/python
ENV SKILL_EVAL_REAL_PYTHON=/usr/local/bin/python3-real

# This sandbox's own castxml is routinely below abicheck's policy floor
# (agent-evals/skills/CLAUDE.md's "Environment prerequisites" section);
# degrade to direct-clang rather than hard-erroring, matching the existing
# harness's own documented workaround.
ENV ABICHECK_ALLOW_AST_FALLBACK=1

# Opt-in only -- see this file's own module docstring. A skill-arm trial
# requests it explicitly: `harbor run ... --ak skills_dir=/opt/skills`.
RUN mkdir -p /opt/skills \\
    && cp -r /opt/abicheck-src/.claude/skills/check-abi-compatibility /opt/skills/

WORKDIR /workspace
COPY workspace/ /workspace/
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


def _readme_abicheck_command(fixture: Path) -> tuple[str, str, str] | None:
    """The case README's own build+compare recipe, split for reuse.

    Category A scenarios are drawn straight from `examples/case*/`, whose
    README is the *validated* source of the case's own ground truth
    (`ground_truth.json` is checked against exactly this command, per
    `tests/validate_examples.py`) -- reusing it verbatim is strictly more
    trustworthy than this generator re-deriving a compile/compare
    invocation from the fixture's file names.

    Returns `(full_block, old_operand, new_operand)` -- the two operands
    parsed out of the block's own `abicheck compare <old> <new>` line so
    `solve.sh` can re-run that exact comparison with `--format json`
    appended, rather than re-parsing prose inside the generated shell
    script itself. `None` when the block is missing or carries no bare
    `abicheck compare` line (e.g. a case documented only via `abicheck
    scan`), in which case `_solve_sh` falls back to its unimplemented stub
    rather than guessing.
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
    compare_match = re.search(
        r"^abicheck compare\s+(\S+)\s+(\S+)\s*$", block, re.MULTILINE
    )
    if not compare_match:
        return None
    return block, compare_match.group(1), compare_match.group(2)


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
            block, old, new = parsed
            # Only the build lines -- the block's own bare `abicheck compare`
            # line is skipped here and re-run below with --format json
            # instead of run twice; a BREAKING case's plain-text run also
            # exits non-zero (exit 4), which `set -e` would otherwise treat
            # as this script failing before it ever reaches the JSON rerun.
            build_lines = "\n".join(
                line
                for line in block.splitlines()
                if not line.strip().startswith("abicheck compare")
            )
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
# on disk is what this script actually reads.
abicheck compare {old} {new} --format json -o /tmp/report.json || true
verdict=$(python3 -c "import json; print(json.load(open('/tmp/report.json'))['verdict'])")
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
        if not path.is_file() or path.suffix.lower() not in (
            ".c",
            ".h",
            ".cc",
            ".cpp",
            ".cxx",
            ".hpp",
            ".hh",
        ):
            continue
        stripped = strip_comments(path.read_text(encoding="utf-8"))
        if stripped is not None:
            path.write_text(stripped, encoding="utf-8")


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

    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / "tasks"
        staging.mkdir()
        for scenario_id, scenario in sorted(scenarios.items()):
            task_dir = staging / scenario_id
            (task_dir / "environment").mkdir(parents=True)
            (task_dir / "tests").mkdir()
            (task_dir / "solution").mkdir()

            (task_dir / "task.toml").write_text(
                _task_toml(scenario_id, scenario), encoding="utf-8"
            )
            (task_dir / "instruction.md").write_text(
                _instruction_md(scenario), encoding="utf-8"
            )
            (task_dir / "README.md").write_text(
                _readme(scenario_id, scenario), encoding="utf-8"
            )
            (task_dir / "environment" / "Dockerfile").write_text(
                _dockerfile(ref, version_floor), encoding="utf-8"
            )
            _prepared_library(
                ROOT / scenario["inputs"],
                task_dir / "environment" / "workspace" / "library",
            )
            (task_dir / "tests" / "test.sh").write_text(
                _test_sh(scenario), encoding="utf-8"
            )
            (task_dir / "tests" / "scenario.json").write_text(
                json.dumps(scenario, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            solve = task_dir / "solution" / "solve.sh"
            solve.write_text(_solve_sh(scenario_id, scenario), encoding="utf-8")
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

#: Files the built Docker image actually depends on *at runtime* --
#: cloned fresh from git at `ARG ABICHECK_REF`, never copied into the
#: static template text `_diff_trees` otherwise compares byte-for-byte.
#: Scoped narrowly: `abicheck/` itself is deliberately excluded (the ref
#: exists precisely to pin *which abicheck version* gets built, so that
#: axis drifting is the whole point, not something to flag), and
#: `scripts/gen_harbor_tasks.py`'s own logic changing is already caught by
#: the ordinary byte comparison (it changes what task.toml/instruction.md/
#: etc. actually contain).
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

    Only ever applied when :func:`_ref_drift_is_tolerable` says the drift
    is the benign, self-referential kind -- see that function's docstring
    for the real correctness gap a blanket application of this one opened
    (Codex review, second round).
    """
    return _REF_LINE.sub(b"ARG ABICHECK_REF=<normalized-for-diff>", data)


def _extract_committed_ref(committed: Path) -> str | None:
    """The `ABICHECK_REF` pinned in an already-committed task tree.

    Read from the first Dockerfile found -- every task in one generation
    run shares the identical ref (one `_abicheck_ref()` call per
    `generate()`), so any one is representative. `None` for a tree with no
    Dockerfile yet (nothing to compare against).
    """
    for dockerfile in sorted(committed.rglob("environment/Dockerfile")):
        match = _REF_LINE.search(dockerfile.read_bytes())
        if match:
            return match.group(1).decode()
    return None


def _ref_drift_is_tolerable(committed_ref: str | None) -> bool:
    """Whether the committed ref differing from a fresh regeneration's ref
    is the benign, self-referential kind -- HEAD moving is unavoidable and
    expected -- rather than real staleness.

    The distinction that matters: HEAD moving *at all* is not itself a
    problem (that's the self-referential drift `_normalize_pinned_ref`
    exists to tolerate) -- what *is* a problem is HEAD moving because
    something the running container actually depends on changed
    underneath the pinned ref without anyone regenerating. A first version
    of this check normalized away *any* ref difference unconditionally,
    which meant a real change to `graders/`/the shim/`verify_run.py` with
    no accompanying regeneration would pass `--check` while every
    committed task kept cloning the stale revision and grading trials with
    outdated logic (Codex review, fresh evidence, second round). Checked
    with a real `git diff --name-only`, not assumed.
    """
    if committed_ref is None:
        return True
    verify = subprocess.run(
        ["git", "rev-parse", "--verify", "-q", committed_ref],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if verify.returncode != 0:
        # An unresolvable ref (e.g. a hand-edited/corrupted line) can't be
        # reasoned about -- treat as real drift rather than silently
        # trusting it.
        return False
    diff = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            f"{committed_ref}..HEAD",
            "--",
            *_RUNTIME_RELEVANT_PATHS,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return not diff.stdout.strip()


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
    tolerate_ref_drift = _ref_drift_is_tolerable(_extract_committed_ref(committed))
    for rel in sorted(committed_files & generated_files):
        committed_path = committed / rel
        generated_path = generated / rel
        a = committed_path.read_bytes()
        b = generated_path.read_bytes()
        if tolerate_ref_drift:
            a = _normalize_pinned_ref(a)
            b = _normalize_pinned_ref(b)
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
