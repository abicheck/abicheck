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

"""Product-gaps audit §3's "real effect" half, now a **retirement +
config-overlay replacement** pin (rulings.py deferred-option followup, later
closed by a Codex review finding on the require-complete-analysis retirement
PR itself).

The ``require-complete-analysis`` forwarding chain this file used to assert
(``check-project.yml``'s ``matrix.analysis_assurance`` -> ``check-target``'s
own ``require-complete-analysis`` input -> the nested root-Action analysis
step) is gone for good: the CLI's own ``compare --require-complete-analysis``
flag it terminated at was demoted to a config-only ``.abicheck.yml``
``assurance.require_complete: true`` with no CLI or Action-input override, so
a boolean *flag* has nothing left to forward to. But
``checks[].analysis.assurance: complete`` (RunPlanCheck.analysis_assurance,
still validated at run-plan generation time by
``analysis_assurance_gate.py``) is enforced again through a **different**
chain -- ``check-project.yml``'s ``matrix.analysis_assurance`` ->
``check-target``'s own ``analysis-assurance-complete`` input -> a
"Generate assurance-overlay config" step that merges
``assurance: {require_complete: true}`` into whatever ``build-config`` the
internal analysis step reads. See ``actions/check-target/action.yml``'s own
``analysis-assurance-complete`` input docstring and
``abicheck/buildsource/analysis_assurance_gate.py``'s module docstring for
the full account.

Split out of ``tests/test_reusable_workflows.py`` purely to respect that
file's ``architecture/debt.yaml`` ``no_growth`` baseline -- see this repo's
own "move responsibility, don't trim to fit" convention (root
``AGENTS.md``).

Structural tests over the parsed YAML, mirroring ``test_reusable_workflows.
py``'s own precedent: a real GitHub Actions runner is needed to exercise the
nested composite `uses:` chain end to end.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from _workflow_exec import make_workspace, outside_is_intact, run_step

_REPO_ROOT = Path(__file__).resolve().parents[1]
CHECK_PROJECT = _REPO_ROOT / ".github" / "workflows" / "check-project.yml"
CHECK_TARGET_ACTION = _REPO_ROOT / "actions" / "check-target" / "action.yml"


def _overlay_step() -> dict[str, Any]:
    data = _load(CHECK_TARGET_ACTION)
    return next(s for s in data["runs"]["steps"] if s.get("id") == "assurance_overlay")


def _written_overlay(result: Any) -> Any:
    """Load the YAML the overlay step actually wrote.

    Security (Codex review, PR #1222): the step no longer writes to a fixed,
    in-checkout-relative name -- it writes to a private, `mktemp`-created
    file under `$RUNNER_TEMP` and reports that ABSOLUTE path as its own
    `config-path` $GITHUB_OUTPUT record, exactly what the real "Run
    analysis" step reads to build its `--config`. Reading the file back via
    that same output (never a path this test re-derives on its own) is what
    proves the two agree.
    """
    config_path = Path(result.outputs["config-path"])
    return yaml.safe_load(config_path.read_text(encoding="utf-8"))


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class TestRequireCompleteAnalysisRetired:
    def test_check_project_no_longer_forwards_the_retired_flag(self) -> None:
        data = _load(CHECK_PROJECT)
        steps = data["jobs"]["check"]["steps"]
        run_step = next(s for s in steps if s.get("name") == "Run check-target")
        assert "require-complete-analysis" not in run_step["with"]

    def test_check_target_action_still_declares_the_retired_input(self) -> None:
        """Still declared (as a tombstone), so a workflow that sets it gets
        an explicit ::error:: instead of a silently-dropped, undeclared
        key -- see actions/check-target/validate-inputs.sh."""
        data = _load(CHECK_TARGET_ACTION)
        assert "require-complete-analysis" in data["inputs"]
        assert data["inputs"]["require-complete-analysis"]["default"] == "false"
        description = data["inputs"]["require-complete-analysis"]["description"]
        assert "RETIRED" in description

    def test_check_target_action_no_longer_forwards_the_retired_flag(self) -> None:
        """The nested root-Action analysis step has nothing left to forward
        the retired flag to (its own require-complete-analysis input is
        retired too), so this cell's own conditional forward stays gone."""
        data = _load(CHECK_TARGET_ACTION)
        analysis_step = next(
            s for s in data["runs"]["steps"] if s.get("name") == "Run analysis"
        )
        assert "require-complete-analysis" not in analysis_step["with"]


class TestAnalysisAssuranceCompleteConfigOverlay:
    """The config-overlay replacement that closed the tracked gap the module
    docstring above (and this file's own former revision) used to describe:
    ``checks[].analysis.assurance: complete`` is enforced again, through
    ``analysis-assurance-complete`` rather than a boolean CLI/Action flag.
    """

    def test_check_project_forwards_matrix_analysis_assurance(self) -> None:
        data = _load(CHECK_PROJECT)
        steps = data["jobs"]["check"]["steps"]
        run_step = next(s for s in steps if s.get("name") == "Run check-target")
        forwarded = run_step["with"]["analysis-assurance-complete"]
        assert "matrix.analysis_assurance" in forwarded
        assert "'complete'" in forwarded

    def test_check_target_action_declares_the_new_input(self) -> None:
        data = _load(CHECK_TARGET_ACTION)
        assert "analysis-assurance-complete" in data["inputs"]
        assert data["inputs"]["analysis-assurance-complete"]["default"] == "false"

    def test_check_target_action_has_overlay_generation_step(self) -> None:
        data = _load(CHECK_TARGET_ACTION)
        steps = data["runs"]["steps"]
        overlay_step = next(s for s in steps if s.get("id") == "assurance_overlay")
        assert overlay_step["if"] == "inputs.analysis-assurance-complete == 'true'"
        # Never allowed to silently swallow a real failure (e.g. a
        # build-config that doesn't parse to a YAML mapping) -- it must run
        # with continue-on-error so the always-run finalize step still
        # executes, and "Run analysis" must be gated on its outcome instead.
        assert overlay_step["continue-on-error"] is True

    def test_run_analysis_uses_the_generated_overlay_as_build_config(self) -> None:
        data = _load(CHECK_TARGET_ACTION)
        analysis_step = next(
            s for s in data["runs"]["steps"] if s.get("name") == "Run analysis"
        )
        build_config = analysis_step["with"]["build-config"]
        assert "steps.assurance_overlay.outcome" in build_config
        # Security (Codex review, PR #1222): the forwarded value must be the
        # overlay step's own `config-path` OUTPUT -- a private, freshly
        # `mktemp`-created file under `$RUNNER_TEMP` -- never a fixed,
        # in-checkout-relative name a malicious PR could pre-plant a symlink
        # at (see TestAssuranceOverlayOutputPathIsPrivate below).
        assert "steps.assurance_overlay.outputs.config-path" in build_config
        assert "check-target-assurance-config.yml" not in build_config
        assert "inputs.build-config" in build_config

    def test_run_analysis_gated_on_overlay_not_failing(self) -> None:
        data = _load(CHECK_TARGET_ACTION)
        analysis_step = next(
            s for s in data["runs"]["steps"] if s.get("name") == "Run analysis"
        )
        assert "steps.assurance_overlay.outcome != 'failure'" in analysis_step["if"]

    def test_finalize_step_reads_overlay_outcome_for_operational_error(
        self,
    ) -> None:
        data = _load(CHECK_TARGET_ACTION)
        finalize_step = next(
            s
            for s in data["runs"]["steps"]
            if s.get("name") == "Write report envelope and finalize"
        )
        assert "ASSURANCE_OVERLAY_OUTCOME" in finalize_step["env"]
        assert (
            finalize_step["env"]["ASSURANCE_OVERLAY_OUTCOME"]
            == "${{ steps.assurance_overlay.outcome }}"
        )

    def test_run_sh_surfaces_overlay_failure_as_operational_error(self) -> None:
        run_sh = (_REPO_ROOT / "actions" / "check-target" / "run.sh").read_text(
            encoding="utf-8"
        )
        assert 'ASSURANCE_OVERLAY_OUTCOME" == "failure"' in run_sh


class TestAssuranceOverlayGenerationExecuted:
    """Executes the "Generate assurance-overlay config" step's real Python
    body (not just its text) against real ``build-config`` YAML files, per
    this repo's own "asserting text proves nothing about behavior" rule
    (``tests/_workflow_exec.py``'s module docstring, #705 -> #758).

    Codex review (PR #1222, fresh evidence after the earlier receipt fix):
    the merge logic used to do ``assurance = data.get("assurance"); if not
    isinstance(assurance, dict): assurance = {}`` -- which *silently
    replaces* a malformed ``assurance:`` value (a bool, a bare string, a
    list) with ``{}`` instead of erroring, even though normal ``BuildConfig``
    ingestion rejects every one of those shapes as a schema violation. Only
    a missing key or an explicit ``null`` should be treated as "safe to
    overlay onto"; anything else must fail the step loudly.
    """

    def _run(self, tmp_path: Path, base_config_yaml: str | None) -> Any:
        workspace = make_workspace(tmp_path)
        env = {}
        if base_config_yaml is not None:
            config_path = workspace / "build-config.yml"
            config_path.write_text(base_config_yaml, encoding="utf-8")
            env["BASE_CONFIG"] = str(config_path)
        else:
            env["BASE_CONFIG"] = ""
        return run_step(_overlay_step(), workspace=workspace, env=env)

    def test_no_base_config_produces_a_clean_overlay(self, tmp_path: Path) -> None:
        result = self._run(tmp_path, None)
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written == {"assurance": {"require_complete": True}}

    def test_missing_assurance_key_is_overlaid(self, tmp_path: Path) -> None:
        result = self._run(tmp_path, "targets: {}\n")
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["assurance"] == {"require_complete": True}
        assert written["targets"] == {}

    def test_explicit_null_assurance_is_overlaid(self, tmp_path: Path) -> None:
        result = self._run(tmp_path, "assurance: null\n")
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["assurance"] == {"require_complete": True}

    def test_existing_mapping_assurance_is_merged_additively(
        self, tmp_path: Path
    ) -> None:
        result = self._run(tmp_path, "assurance:\n  some_other_field: true\n")
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["assurance"] == {
            "some_other_field": True,
            "require_complete": True,
        }

    @pytest.mark.parametrize(
        "malformed_yaml",
        [
            pytest.param("assurance: false\n", id="bool"),
            pytest.param("assurance: complete\n", id="bare-string"),
            pytest.param("assurance:\n  - one\n  - two\n", id="list"),
            pytest.param("assurance: 3\n", id="number"),
        ],
    )
    def test_malformed_assurance_block_fails_loud_not_silently_discarded(
        self, tmp_path: Path, malformed_yaml: str
    ) -> None:
        """The bug this finding names: none of these shapes may be silently
        replaced with ``{}`` -- every one must fail the step instead."""
        result = self._run(tmp_path, malformed_yaml)
        assert result.returncode != 0
        assert "::error::" in result.stderr
        assert "assurance" in result.stderr
        assert "config-path" not in result.outputs

    def test_non_mapping_whole_document_still_fails_loud(self, tmp_path: Path) -> None:
        """Negative control for the pre-existing top-level guard, exercised
        the same executing way as the new assurance-shape guard above."""
        result = self._run(tmp_path, "- just\n- a\n- list\n")
        assert result.returncode != 0
        assert "::error::" in result.stderr


class TestAssuranceOverlayGenerationIsIsolated:
    """Finding 1 (P1, SECURITY, PR #1222 Codex review): the "Generate
    assurance-overlay config" step used to launch Python directly against
    the workspace checkout, with no CWD/PYTHONPATH isolation. Python's own
    ``site`` processing auto-imports a discoverable ``sitecustomize.py``
    during interpreter STARTUP -- before this step's own heredoc body ever
    runs a single line -- so a PR that plants a top-level
    ``sitecustomize.py`` and declares ``checks[].analysis.assurance:
    complete`` in its own project config got arbitrary code execution in
    this Action's context. Fixed by mirroring ``action/run.sh``'s own
    ``$_PY_SAFE_DIR``/cleared-``PYTHONPATH`` mitigation exactly (see
    ``tests/test_action_run_sh_py_safe_path.py`` for the same
    sitecustomize-does-not-execute pattern proven against that script's own
    inline-Python invocations)."""

    def _run(self, tmp_path: Path) -> Any:
        workspace = make_workspace(tmp_path)
        (workspace / "sitecustomize.py").write_text(
            "import pathlib\n"
            "pathlib.Path(__file__).with_name('PWNED').write_text('pwned')\n",
            encoding="utf-8",
        )
        return run_step(_overlay_step(), workspace=workspace, env={"BASE_CONFIG": ""})

    def test_a_checkout_planted_sitecustomize_does_not_execute(
        self, tmp_path: Path
    ) -> None:
        result = self._run(tmp_path)
        assert result.returncode == 0, result.stderr
        assert not (result.workspace / "PWNED").exists()

    def test_the_step_still_produces_its_real_output_despite_isolation(
        self, tmp_path: Path
    ) -> None:
        """Negative control: isolation must not be a silent no-op that
        also breaks the step's own real job."""
        result = self._run(tmp_path)
        written = _written_overlay(result)
        assert written == {"assurance": {"require_complete": True}}


class TestAssuranceOverlayOutputPathIsPrivate:
    """SECURITY finding (P2-labeled but a real vulnerability, PR #1222 Codex
    review, second finding on this step): the overlay's OUTPUT file used to
    be written via a plain ``open(path, "w")`` at a PREDICTABLE, in-checkout
    path (``check-target-assurance-config.yml``, resolved relative to the
    checkout root). A malicious PR could commit a SYMLINK at that exact path
    pointing anywhere the runner user can write -- a dotfile, a sibling job's
    workspace -- and ``open(..., "w")`` follows a symlink, so the step would
    overwrite whatever the attacker's link pointed at with the merged YAML.

    Distinct from ``TestAssuranceOverlayGenerationIsIsolated`` above: that
    finding was about what code executes during Python interpreter STARTUP
    (a planted ``sitecustomize.py``); this one is purely about where the
    step's own WRITE lands, regardless of how safely the interpreter itself
    was launched.

    Fixed the same way ``action/run.sh`` already handles a config overlay it
    doesn't trust the destination of (see ``_COMPILE_CONTEXT_CONFIG_OVERLAY``/
    ``_RELEASE_TOPOLOGY_CONFIG_OVERLAY``): the output path is a fresh
    ``mktemp``-created file under ``$RUNNER_TEMP`` -- a runtime-random name
    nothing in the untrusted checkout could have pre-planted a symlink at --
    forwarded to "Run analysis" as this step's own ``config-path`` output
    rather than a fixed name the caller re-derives.
    """

    def _plant_symlink_and_run(self, tmp_path: Path) -> tuple[Any, Path]:
        workspace = make_workspace(tmp_path)
        victim = tmp_path / "outside" / "VICTIM.txt"
        victim.write_text("do not overwrite me", encoding="utf-8")
        try:
            (workspace / "check-target-assurance-config.yml").symlink_to(victim)
        except OSError as exc:  # Windows without the symlink privilege
            pytest.skip(f"cannot create a symlink here: {exc}")
        result = run_step(_overlay_step(), workspace=workspace, env={"BASE_CONFIG": ""})
        return result, victim

    def test_a_symlink_at_the_legacy_path_is_never_followed(
        self, tmp_path: Path
    ) -> None:
        result, victim = self._plant_symlink_and_run(tmp_path)
        assert result.returncode == 0, result.stderr
        assert victim.read_text(encoding="utf-8") == "do not overwrite me"
        assert outside_is_intact(tmp_path)

    def test_the_planted_symlink_itself_is_left_untouched(
        self, tmp_path: Path
    ) -> None:
        result, _victim = self._plant_symlink_and_run(tmp_path)
        assert result.returncode == 0, result.stderr
        planted = result.workspace / "check-target-assurance-config.yml"
        assert planted.is_symlink()

    def test_the_real_output_is_a_fresh_randomly_named_path(
        self, tmp_path: Path
    ) -> None:
        """The output path must not be the fixed, guessable legacy name a
        malicious PR could pre-plant a symlink at -- it must be an absolute,
        `mktemp`-randomized path distinct from the planted symlink."""
        result, _victim = self._plant_symlink_and_run(tmp_path)
        assert result.returncode == 0, result.stderr
        config_path = Path(result.outputs["config-path"])
        planted = result.workspace / "check-target-assurance-config.yml"
        assert config_path.is_absolute()
        assert config_path != planted
        assert config_path.name != "check-target-assurance-config.yml"
        written = _written_overlay(result)
        assert written == {"assurance": {"require_complete": True}}


class TestAssuranceOverlayDiscoversProjectConfig:
    """Finding 2 (P1, PR #1222 Codex review): with
    ``analysis-assurance-complete: true`` and no explicit ``build-config``,
    the overlay used to start from an EMPTY mapping and pass the generated
    overlay as an explicit ``--config``/``build-config`` to the nested root
    Action -- which DISABLES that Action's/CLI's normal ``.abicheck.yml``
    auto-discovery, silently dropping every other auto-discovered project
    setting (scope/severity/policy/compile/...) the moment the assurance
    gate is enabled. Fixed by discovering the project's real
    ``.abicheck.yml`` (the same way ``discover_project_config()`` does) and
    merging the overlay into THAT document instead of an empty one."""

    def test_no_explicit_build_config_still_preserves_auto_discovered_settings(
        self, tmp_path: Path
    ) -> None:
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "severity:\n  preset: strict\n"
            "policy:\n  overrides:\n    FUNCTION_REMOVED: error\n",
            encoding="utf-8",
        )
        result = run_step(_overlay_step(), workspace=workspace, env={"BASE_CONFIG": ""})
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        # Not just the bare assurance block: every auto-discovered setting
        # must survive the merge.
        assert written["severity"] == {"preset": "strict"}
        assert written["policy"] == {"overrides": {"FUNCTION_REMOVED": "error"}}
        assert written["assurance"] == {"require_complete": True}

    def test_no_discoverable_config_still_produces_a_clean_overlay(
        self, tmp_path: Path
    ) -> None:
        """Negative control: no ``.abicheck.yml`` anywhere above the
        workspace must behave exactly as before (an empty base)."""
        result = self._run_no_config(tmp_path)
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written == {"assurance": {"require_complete": True}}

    def _run_no_config(self, tmp_path: Path) -> Any:
        workspace = make_workspace(tmp_path)
        return run_step(_overlay_step(), workspace=workspace, env={"BASE_CONFIG": ""})

    def test_explicit_build_config_still_wins_over_discovery(
        self, tmp_path: Path
    ) -> None:
        """An explicit build-config is unaffected by discovery -- it stays
        the base document, matching pre-existing behavior exactly."""
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "severity:\n  preset: strict\n", encoding="utf-8"
        )
        explicit_config = workspace / "explicit.yml"
        explicit_config.write_text("targets: {}\n", encoding="utf-8")
        result = run_step(
            _overlay_step(),
            workspace=workspace,
            env={"BASE_CONFIG": str(explicit_config)},
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written == {
            "targets": {},
            "assurance": {"require_complete": True},
        }
        assert "severity" not in written
