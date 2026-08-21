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

"""`scripts/gen_harbor_tasks.py` -- the generated Harbor task battery.

Real end-to-end grading (does `solve.sh` -> the shim -> `verify_run.py`
actually produce reward=1 for a correct answer) is exercised directly, not
mocked -- see `TestSolveScriptsEndToEnd`. Full trial execution (an actual
Harbor `harbor run`, which needs Docker) is out of scope for the fast unit
lane; see `agent-evals/skills/harbor/CLAUDE.md` for what that still leaves
unverified.

`TestHarborSchemaValidation` is the one class in this file that needs the
real `harbor` package (`>=3.12`, not a repository dependency) -- it
self-skips via `pytest.importorskip` rather than a new pytest marker
(tests/CLAUDE.md: "don't change the marker scheme"), and CI installs
`harbor` and runs it as a dedicated, best-effort step precisely because it
is not otherwise reachable from any existing marker lane.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import tomllib

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "agent-evals" / "skills"
TASKS_DIR = EVAL_DIR / "harbor" / "tasks"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(EVAL_DIR))

import gen_harbor_tasks as gen  # noqa: E402

pytestmark = pytest.mark.skipif(
    not TASKS_DIR.is_dir(), reason="agent-evals/skills/harbor/tasks/ not generated"
)


def _task_dirs() -> list[Path]:
    return sorted(p for p in TASKS_DIR.iterdir() if p.is_dir())


class TestGeneratorCheck:
    def test_check_passes_against_the_committed_tree(self):
        assert gen.generate(check=True) is True

    def test_check_fails_on_drift(self, tmp_path, monkeypatch):
        stray = TASKS_DIR / "removed-export" / "task.toml"
        original = stray.read_text(encoding="utf-8")
        try:
            stray.write_text(original + "\n# hand-edited\n", encoding="utf-8")
            assert gen.generate(check=True) is False
        finally:
            stray.write_text(original, encoding="utf-8")

    def test_check_survives_the_pinned_ref_moving(self, monkeypatch):
        """A commit boundary always moves `git rev-parse HEAD` past whatever
        was baked into the Dockerfile at generation time (the SHA is
        necessarily computed *before* the commit that carries it exists) --
        `--check` must not treat that expected drift as real drift. Real
        regression: this failed on every commit after the tree was first
        committed, caught by literally committing and re-running `--check`,
        not by reasoning about it."""
        monkeypatch.setattr(gen, "_abicheck_ref", lambda: "f" * 40)
        assert gen.generate(check=True) is True

    def test_ref_drift_tolerance_requires_no_runtime_relevant_change(self):
        """`_ref_drift_is_tolerable` must answer from a real `git diff`, not
        blindly trust any ref difference -- the gap a first version of the
        `--check` normalization had (Codex review, fresh evidence, second
        round): a real change to `graders/`/the shim/`verify_run.py` with
        no accompanying regeneration would otherwise pass `--check` while
        every committed task kept cloning the stale revision. The repo's
        own root commit (verified above to predate `graders/` entirely)
        is real, reachable history, not a synthetic fixture."""
        root_commit = "af2f40acaee9a28fa55b058e65b01d9999fddb2c"
        assert gen._ref_drift_is_tolerable(root_commit) is False
        # The tolerable case, from the same real function: diffing a ref
        # against itself is always empty.
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert gen._ref_drift_is_tolerable(head) is True
        assert gen._ref_drift_is_tolerable(None) is True

    def test_check_catches_a_stale_ref_with_real_grader_drift_behind_it(self):
        """End-to-end proof, not just the unit-level check above: pinning
        every committed Dockerfile to a real, historical ref that predates
        `graders/` makes `--check` fail rather than silently normalizing
        the difference away. Every Dockerfile, not just one -- `_extract_
        committed_ref` reads whichever sorts first, so patching a single
        file the scan doesn't happen to pick would leave it invisible."""
        import re as _re

        dockerfiles = sorted(TASKS_DIR.rglob("environment/Dockerfile"))
        originals = {p: p.read_text(encoding="utf-8") for p in dockerfiles}
        try:
            for p, text in originals.items():
                p.write_text(
                    _re.sub(
                        r"ARG ABICHECK_REF=[0-9a-f]{40}",
                        "ARG ABICHECK_REF=af2f40acaee9a28fa55b058e65b01d9999fddb2c",
                        text,
                    ),
                    encoding="utf-8",
                )
            assert gen.generate(check=True) is False
        finally:
            for p, text in originals.items():
                p.write_text(text, encoding="utf-8")

    def test_check_still_catches_a_real_dockerfile_edit(self):
        """The normalization above must not swallow a genuine content
        change -- only the one line it's scoped to."""
        stray = TASKS_DIR / "removed-export" / "environment" / "Dockerfile"
        original = stray.read_text(encoding="utf-8")
        try:
            stray.write_text(
                original.replace("castxml", "castxml-evil"), encoding="utf-8"
            )
            assert gen.generate(check=True) is False
        finally:
            stray.write_text(original, encoding="utf-8")

    def test_check_catches_a_lost_executable_bit(self):
        """A byte-identical `tests/test.sh` that lost its executable bit
        (e.g. a manual re-save) is unusable to Harbor, which executes it
        directly -- a bytes-only comparison would silently pass this
        (Codex review)."""
        stray = TASKS_DIR / "removed-export" / "tests" / "test.sh"
        original_mode = stray.stat().st_mode
        try:
            stray.chmod(original_mode & ~0o111)
            assert gen.generate(check=True) is False
        finally:
            stray.chmod(original_mode)

    def test_regeneration_is_idempotent(self):
        """Same normalization `--check` applies, and for the identical
        reason: the tree on disk (a checkout of the *last* commit that ran
        this generator) necessarily pins the SHA of *that commit's own
        parent* -- the SHA is computed before the commit that carries it
        exists -- while a live `generate()` call right now pins the
        *current* `HEAD`, which is that later commit itself. Comparing raw
        bytes therefore fails on every fresh checkout whose tip commit
        touched the generator or its output, which is every commit that
        ever regenerates this tree -- caught by actually running this test
        immediately after a real commit, not by re-reading the assertion."""
        before = {
            p.relative_to(TASKS_DIR): gen._normalize_pinned_ref(p.read_bytes())
            for p in TASKS_DIR.rglob("*")
            if p.is_file()
        }
        gen.generate(check=False)
        after = {
            p.relative_to(TASKS_DIR): gen._normalize_pinned_ref(p.read_bytes())
            for p in TASKS_DIR.rglob("*")
            if p.is_file()
        }
        assert before == after


class TestTaskStructure:
    @pytest.mark.parametrize("task_dir", _task_dirs(), ids=lambda p: p.name)
    def test_every_task_has_the_five_required_entries(self, task_dir):
        assert (task_dir / "task.toml").is_file()
        assert (task_dir / "instruction.md").is_file()
        assert (task_dir / "environment" / "Dockerfile").is_file()
        assert (task_dir / "tests" / "test.sh").is_file()
        assert (task_dir / "solution" / "solve.sh").is_file()

    @pytest.mark.parametrize("task_dir", _task_dirs(), ids=lambda p: p.name)
    def test_task_toml_parses_and_names_the_task(self, task_dir):
        parsed = tomllib.loads((task_dir / "task.toml").read_text(encoding="utf-8"))
        assert parsed["task"]["name"] == f"abicheck/{task_dir.name}"
        assert parsed["schema_version"] == "1.4"
        # An agent-arm run must be able to opt out of runtime network --
        # confirms the generator's own no-network default landed, not a
        # public-network fallback nobody would notice went missing.
        assert parsed["environment"]["network_mode"] == "no-network"

    @pytest.mark.parametrize("task_dir", _task_dirs(), ids=lambda p: p.name)
    def test_generated_files_all_carry_the_ownership_marker(self, task_dir):
        # instruction.md is deliberately excluded: it is the agent's own
        # prompt text, and a "do not hand-edit" comment as its first line
        # would be part of what the agent reads, not a marker for a human
        # editor.
        for rel in ("task.toml", "README.md"):
            text = (task_dir / rel).read_text(encoding="utf-8")
            assert "GENERATED FILE" in text.splitlines()[0], rel
        assert (
            "GENERATED FILE"
            in (task_dir / "environment" / "Dockerfile")
            .read_text(encoding="utf-8")
            .splitlines()[0]
        )

    @pytest.mark.parametrize("task_dir", _task_dirs(), ids=lambda p: p.name)
    def test_scenario_json_is_scoped_to_tests_not_environment(self, task_dir):
        """The ground truth must never land where the agent can read it."""
        assert (task_dir / "tests" / "scenario.json").is_file()
        assert not (task_dir / "environment" / "scenario.json").exists()
        assert not (task_dir / "environment" / "workspace" / "scenario.json").exists()

    def test_no_task_leaks_the_tool_name_or_a_verdict_word(self):
        """The exact `workspace_leaks` scan the existing harness runs before
        any model call -- reused, not re-implemented, against every
        generated task's agent-visible workspace."""
        from runners.claude_code import workspace_leaks

        leaky = {
            task_dir.name: workspace_leaks(task_dir / "environment" / "workspace")
            for task_dir in _task_dirs()
        }
        leaky = {k: v for k, v in leaky.items() if v}
        assert leaky == {}


class TestReadmeCommandParsing:
    def test_extracts_the_documented_compare_operands(self, tmp_path):
        case = tmp_path / "case"
        case.mkdir()
        (case / "README.md").write_text(
            "# Case\n\n## abicheck command\n\n```bash\n"
            "gcc -shared -fPIC -g v1.c -o libfoo_v1.so\n"
            "gcc -shared -fPIC -g v2.c -o libfoo_v2.so\n"
            "abicheck compare libfoo_v1.so libfoo_v2.so\n"
            "```\n",
            encoding="utf-8",
        )
        result = gen._readme_abicheck_command(case)
        assert result is not None
        block, old, new = result
        assert old == "libfoo_v1.so"
        assert new == "libfoo_v2.so"
        assert "abicheck compare" in block

    def test_none_when_the_section_is_absent(self, tmp_path):
        case = tmp_path / "case"
        case.mkdir()
        (case / "README.md").write_text(
            "# Case\n\nNo section here.\n", encoding="utf-8"
        )
        assert gen._readme_abicheck_command(case) is None

    def test_none_when_the_block_has_no_bare_compare_line(self, tmp_path):
        case = tmp_path / "case"
        case.mkdir()
        (case / "README.md").write_text(
            "# Case\n\n## abicheck command\n\n```bash\nabicheck scan --against x.so\n```\n",
            encoding="utf-8",
        )
        assert gen._readme_abicheck_command(case) is None


class TestArchitectureGuard:
    """`_test_sh`'s runtime `uname -m` guard for a scenario declaring
    `architectures` (e.g. `evidence-too-shallow`, which embeds an x86_64
    prebuilt artifact) -- exercised for real, not mocked, since a first
    version of this guard built its `python3 -c` payload with
    `json.dumps()` (double-quoted strings) inside an outer bash
    double-quoted string, which silently truncated the argument at the
    first unescaped `"` and fed the shell interpreter's own bareword back
    in as literal (unquoted) Python source -- `NameError: name 'x86_64' is
    not defined`, reproducible only by actually running the generated
    script, never by reading it.
    """

    def _run_with_fake_uname(self, tmp_path, task_id, reported_arch):
        task_dir = TASKS_DIR / task_id
        test_sh = (task_dir / "tests" / "test.sh").read_text(encoding="utf-8")
        logs = tmp_path / "logs"
        script = tmp_path / "test.sh"
        script.write_text(
            test_sh.replace("/logs/verifier", str(logs)), encoding="utf-8"
        )
        script.chmod(0o755)

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake_uname = bin_dir / "uname"
        fake_uname.write_text(f"#!/bin/sh\necho {reported_arch}\n", encoding="utf-8")
        fake_uname.chmod(0o755)

        import os

        env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
        proc = subprocess.run(  # noqa: S603
            ["bash", str(script)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return proc, logs

    def test_mismatched_architecture_exits_nonzero_without_writing_a_reward(
        self, tmp_path
    ):
        """No reward file at all -- verified against the real `harbor`
        package's own `Verifier.verify()` (not guessed): with neither
        `reward.txt` nor `reward.json` present, it raises
        `RewardFileNotFoundError`, which `TrialResult` records as
        `exception_info` on a trial whose `verifier_result` stays `None` --
        structurally distinct from a real, scored 0. A written `reward=0`
        (the guard's first version) would count an environment mismatch as
        a failed agent trial in every arm on a non-x86_64 host, depressing
        aggregate scores (Codex review, fresh evidence, second round)."""
        proc, logs = self._run_with_fake_uname(
            tmp_path, "evidence-too-shallow", "aarch64"
        )
        assert proc.returncode == 1
        assert not logs.exists() or not any(logs.iterdir())
        assert "architecture_mismatch" in proc.stderr
        assert "arm64" in proc.stderr
        assert "x86_64" in proc.stderr

    def test_matching_architecture_falls_through_to_the_real_verifier(self, tmp_path):
        # Falls through past the guard and fails only because
        # /opt/abicheck-src doesn't exist on this bare host -- confirming
        # the guard did NOT short-circuit, not that the full verifier ran.
        proc, logs = self._run_with_fake_uname(
            tmp_path, "evidence-too-shallow", "x86_64"
        )
        assert not (logs / "reward.json").is_file()
        assert "verify_run.py" in proc.stderr

    def test_a_scenario_with_no_declared_architectures_has_no_guard(self):
        test_sh = (TASKS_DIR / "removed-export" / "tests" / "test.sh").read_text(
            encoding="utf-8"
        )
        assert "architecture_mismatch" not in test_sh


class TestSolveScriptsEndToEnd:
    """Real execution: `solve.sh` -> the recording shim -> `verify_run.py`.

    Skipped without gcc/abicheck on PATH -- the same precondition the
    existing harness's own `missing_toolchain()` gates on, not a new one.
    """

    @pytest.fixture(autouse=True)
    def _require_toolchain(self):
        if shutil.which("gcc") is None or shutil.which("abicheck") is None:
            pytest.skip("gcc and/or abicheck not on PATH")

    @pytest.mark.parametrize(
        "scenario_id",
        [
            "removed-export",
            "compatible-addition",
            "changed-signature",
            "enum-value-change",
            "struct-layout-drift",
            "vtable-change",
        ],
    )
    def test_reference_solution_grades_correct(self, tmp_path, scenario_id):
        task_dir = TASKS_DIR / scenario_id
        if not (task_dir / "solution" / "solve.sh").is_file():
            pytest.skip(f"{scenario_id} has no generated solve.sh")
        solve_text = (task_dir / "solution" / "solve.sh").read_text(encoding="utf-8")
        if "exit 1" in solve_text and "abicheck compare" not in solve_text:
            pytest.skip(f"{scenario_id}'s solve.sh is an intentional stub")

        workspace = tmp_path / "workspace"
        shutil.copytree(
            task_dir / "environment" / "workspace" / "library", workspace / "library"
        )
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        real_abicheck = shutil.which("abicheck")
        shim = bin_dir / "abicheck"
        shutil.copy2(EVAL_DIR / "shim" / "abicheck", shim)
        shim.chmod(0o755)

        local_solve = tmp_path / "solve_local.sh"
        local_solve.write_text(
            solve_text.replace(
                "/workspace/library", str(workspace / "library")
            ).replace("/workspace/final.md", str(workspace / "final.md")),
            encoding="utf-8",
        )

        import os

        env = {
            **os.environ,
            "SKILL_EVAL_CALLS": str(workspace / "calls.jsonl"),
            "SKILL_EVAL_REAL_ABICHECK": real_abicheck,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        }
        proc = subprocess.run(  # noqa: S603
            ["bash", str(local_solve)],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert (workspace / "final.md").is_file()

        pack = json.loads(
            (EVAL_DIR / "skill-eval-pack.json").read_text(encoding="utf-8")
        )
        scenario_path = tmp_path / "scenario.json"
        scenario_path.write_text(
            json.dumps(pack["scenarios"][scenario_id]), encoding="utf-8"
        )

        reward_txt = tmp_path / "reward.txt"
        reward_json = tmp_path / "reward.json"
        verify_env = {**env, "HARBOR_GRADERS_ROOT": str(EVAL_DIR)}
        verify_proc = subprocess.run(  # noqa: S603
            [
                sys.executable,
                str(EVAL_DIR / "harbor" / "verify_run.py"),
                "--workspace",
                str(workspace),
                "--scenario",
                str(scenario_path),
                "--reward-txt",
                str(reward_txt),
                "--reward-json",
                str(reward_json),
            ],
            env=verify_env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert verify_proc.returncode == 0, verify_proc.stderr
        assert reward_txt.read_text(encoding="utf-8").strip() == "1", json.loads(
            reward_json.read_text(encoding="utf-8")
        )


class TestHarborSchemaValidation:
    """Every generated task validates against the real `harbor` package's
    own `Task`/`TaskConfig` Pydantic models -- not a hand-guessed schema,
    and not merely `task.toml` parsing as plain TOML (`TestTaskStructure`
    above already covers that): `Task.__init__` additionally runs Harbor's
    own `_validate_tests` (verifier/agent/solution shape, OS selection)
    against `TaskConfig.model_validate_toml`'s parsed result.

    `harbor` needs Python >=3.12 and is not a repository dependency (it
    exists to validate output *for* Harbor, not to be one), so this
    self-skips via `pytest.importorskip` when it isn't installed --
    deliberately not a new pytest marker, per tests/CLAUDE.md's "don't
    change the marker scheme". CI installs it and runs this class as one
    dedicated, best-effort step (`.github/workflows/ci.yml`'s
    `harbor-schema` step) rather than folding it into an existing marker
    lane that assumes a different toolchain (castxml/gcc, not a PyPI
    package) and a different Python floor (this repo's own canonical 3.13
    happens to satisfy `harbor`'s floor, but nothing pins that relationship
    -- an explicit, separate install keeps the two floors independent).
    """

    @pytest.fixture(autouse=True)
    def _harbor(self):
        # `agent-evals/skills/harbor/` (this repo's own generated-task tree)
        # is itself an implicit PEP 420 namespace package named `harbor`,
        # and this module's own `sys.path.insert(0, str(EVAL_DIR))` above
        # puts it ahead of site-packages -- `pytest.importorskip("harbor")`
        # alone resolves to *that* directory when the real package isn't
        # installed (bare `import harbor` succeeds either way) and only
        # fails later, as a genuine error rather than a clean skip, the
        # first time something reaches for a real submodule. Importing the
        # actual submodule this class needs sidesteps the ambiguity
        # entirely: a namespace package has no `models` submodule, so this
        # raises (and `importorskip` turns into a skip) exactly when the
        # real package is absent, and resolves correctly to the real,
        # installed `harbor` when it's present (PEP 420: a regular package
        # -- one with `__init__.py`, which the real one has and the local
        # shadow doesn't -- wins over a namespace package regardless of
        # which comes first on `sys.path`, confirmed directly against a
        # real installed `harbor`).
        return pytest.importorskip("harbor.models.task.task")

    @pytest.mark.parametrize("task_dir", _task_dirs(), ids=lambda p: p.name)
    def test_task_validates_against_the_real_harbor_schema(self, task_dir, _harbor):
        task = _harbor.Task(task_dir)
        assert task.name == f"abicheck/{task_dir.name}"
