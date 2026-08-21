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
lane; see `agent-evals/skills/harbor/CLAUDE.md` for how that was validated
manually against the real `harbor` package's own `Task`/`TaskConfig`
Pydantic models.
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

    def test_regeneration_is_idempotent(self):
        before = {
            p.relative_to(TASKS_DIR): p.read_bytes()
            for p in TASKS_DIR.rglob("*")
            if p.is_file()
        }
        gen.generate(check=False)
        after = {
            p.relative_to(TASKS_DIR): p.read_bytes()
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

    def test_mismatched_architecture_short_circuits_with_reward_zero(self, tmp_path):
        proc, logs = self._run_with_fake_uname(
            tmp_path, "evidence-too-shallow", "aarch64"
        )
        assert proc.returncode == 0, proc.stderr
        assert (logs / "reward.txt").read_text(encoding="utf-8").strip() == "0"
        payload = json.loads((logs / "reward.json").read_text(encoding="utf-8"))
        assert payload == {
            "reward": 0,
            "error": "architecture_mismatch",
            "host_architecture": "arm64",
            "required_architectures": ["x86_64"],
        }

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
