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
from _assurance_overlay_exec import (
    CHECK_PROJECT,
    CHECK_TARGET_ACTION,
    _load,
    _run_overlay,
    _written_overlay,
)
from _workflow_exec import make_workspace, outside_is_intact

_REPO_ROOT = Path(__file__).resolve().parents[1]


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

    def test_sources_pairwise_env_matches_the_ambiguous_bundle_plus_baseline_case(
        self,
    ) -> None:
        """P1 finding (Codex review, fresh evidence, PR #1222 fourth
        round): the overlay step's own ``SOURCES_PAIRWISE`` env value must
        be 'pairwise' in EXACTLY the one combination run.sh's own
        ``_compile_context_sources_pairwise`` treats as genuinely
        ambiguous for this step's nested "Run analysis" invocation --
        kind: bundle with a real baseline-channel (mode: compare against a
        live binaries-dir old-library) -- and empty (single-sided) for
        every other kind/baseline-channel combination (kind: target
        against either a stored snapshot or no baseline at all, and
        kind: bundle with baseline-channel: none, which routes to mode:
        scan -- always single-sided regardless of kind)."""
        data = _load(CHECK_TARGET_ACTION)
        overlay_step = next(
            s for s in data["runs"]["steps"] if s.get("id") == "assurance_overlay"
        )
        expr = overlay_step["env"]["SOURCES_PAIRWISE"]
        assert "inputs.kind == 'bundle'" in expr
        assert "inputs.baseline-channel != 'none'" in expr
        assert "'pairwise'" in expr

    def test_sources_merge_compile_env_matches_mode_compare(self) -> None:
        """P1 finding (Codex review, fresh evidence, PR #1222 fourth round,
        second finding on this same fix): within the single-sided bucket
        above, ``compile:`` does not always resolve the same way -- the
        overlay step's own ``SOURCES_MERGE_COMPILE`` env value must be
        'true' in EXACTLY the combination whose nested "Run analysis"
        invocation runs ``mode: compare`` (``inputs.baseline-channel !=
        'none'``) and empty otherwise (``mode: scan``, where ``compile:``
        genuinely is a single-document-exclusive selection, like
        ``build:``/``sources:``/``source:``/``debug:``)."""
        data = _load(CHECK_TARGET_ACTION)
        overlay_step = next(
            s for s in data["runs"]["steps"] if s.get("id") == "assurance_overlay"
        )
        expr = overlay_step["env"]["SOURCES_MERGE_COMPILE"]
        assert "inputs.baseline-channel != 'none'" in expr
        assert "'true'" in expr


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
        return _run_overlay(workspace, env)

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
        """Regression note (PR #1222 Codex review, second finding):
        `some_other_field` used to stand in for "any pre-existing key" here,
        but `assurance:` has exactly one recognized subkey
        (`require_complete`) -- once the base document is validated against
        the real `BuildConfig` schema (this finding's own fix), that
        fixture is itself schema-invalid and the step now correctly refuses
        it before ever reaching the merge this test means to exercise. Use
        `assurance.require_complete: false` instead (schema-valid, and a
        stronger assertion of "additive": the overlay must not just add the
        key, it must override an existing FALSE value to True) alongside an
        unrelated top-level block, to keep proving both halves of
        "additive" -- an unrelated top-level key survives untouched, and an
        existing assurance value is overridden rather than merely added
        alongside."""
        result = self._run(
            tmp_path,
            "severity:\n  preset: strict\nassurance:\n  require_complete: false\n",
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["assurance"] == {"require_complete": True}
        assert written["severity"] == {"preset": "strict"}

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

    # Codex review (P1/P2, fresh evidence, PR #1222): the explicit-YAML
    # parse-failure-escaping and non-mapping-document handling tests moved
    # to their own sibling module,
    # ``test_reusable_workflows_assurance_overlay_parse_safety.py``, so this
    # already-1199-line file doesn't cross the ``architecture/debt.yaml``
    # test-file cap -- see that module's own docstring for the two findings
    # it covers.


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
        return _run_overlay(workspace, {"BASE_CONFIG": ""})

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
        result = _run_overlay(workspace, {"BASE_CONFIG": ""})
        return result, victim

    def test_a_symlink_at_the_legacy_path_is_never_followed(
        self, tmp_path: Path
    ) -> None:
        result, victim = self._plant_symlink_and_run(tmp_path)
        assert result.returncode == 0, result.stderr
        assert victim.read_text(encoding="utf-8") == "do not overwrite me"
        assert outside_is_intact(tmp_path)

    def test_the_planted_symlink_itself_is_left_untouched(self, tmp_path: Path) -> None:
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
        result = _run_overlay(workspace, {"BASE_CONFIG": ""})
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
        return _run_overlay(workspace, {"BASE_CONFIG": ""})

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
        result = _run_overlay(workspace, {"BASE_CONFIG": str(explicit_config)})
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written == {
            "targets": {},
            "assurance": {"require_complete": True},
        }
        assert "severity" not in written


class TestAssuranceOverlayStripsExecutableKeysFromDiscoveredConfig:
    """Finding 1 (P1, SECURITY, PR #1222 Codex review, commit 148c624be):

    With ``analysis-assurance-complete: true`` and NO explicit
    ``build-config``, the step used to copy the PR-controlled, auto-
    discovered ``.abicheck.yml`` into the overlay VERBATIM -- including
    ``build.query``/``compile.compiler``, both of which select code or an
    executable to run. That merged overlay is then handed to the nested
    root Action's own analysis step as an EXPLICIT ``--config``/
    ``build-config`` -- and ``cli_options.py``'s ``compile.compiler`` gate
    and ADR-032 D5's ``build.query`` gate treat "explicit --config" as
    operator authorization to run either. So a PR that merely adds a
    ``.abicheck.yml`` with a ``build.query``/``compile.compiler`` AND
    declares ``checks[].analysis.assurance: complete`` (which any project
    using ``analysis-assurance-complete: true`` without naming its own
    build-config does automatically) gets those keys promoted from
    untrusted, auto-discovered content to trusted-and-executable -- a
    privilege-escalation / command-injection path.

    Fixed by routing the discovered document through
    ``abicheck.action_config_overlay.strip_untrusted_execution_keys`` --
    the identical, shared implementation ``action/run.sh``'s own equivalent
    merge (``_merge_config_overlay_with_discovered_project_config``) already
    used for its own compile-context/release-topology overlays, so the two
    call sites can't independently drift on this trust boundary again.
    """

    def test_discovered_build_query_is_stripped(self, tmp_path: Path) -> None:
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "build:\n  query: cmake --build . --target print-abi-flags\n",
            encoding="utf-8",
        )
        result = _run_overlay(workspace, {"BASE_CONFIG": ""})
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        # The bug this finding names: build.query must never survive into
        # the overlay this step hands to the nested Action as an explicit
        # --config.
        assert "query" not in written.get("build", {})
        assert "build.query" in result.stderr

    def test_discovered_compile_compiler_is_stripped(self, tmp_path: Path) -> None:
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "compile:\n  compiler: /tmp/evil-compiler.sh\n",
            encoding="utf-8",
        )
        result = _run_overlay(workspace, {"BASE_CONFIG": ""})
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert "compiler" not in written.get("compile", {})
        assert "compile.compiler" in result.stderr

    def test_discovered_resource_limits_are_capped_not_raised(
        self, tmp_path: Path
    ) -> None:
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "resource_limits:\n  max_bundle_facts_decode_nodes: 999999999\n",
            encoding="utf-8",
        )
        result = _run_overlay(workspace, {"BASE_CONFIG": ""})
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["resource_limits"]["max_bundle_facts_decode_nodes"] < 999999999

    def test_discovered_build_compile_db_is_stripped(self, tmp_path: Path) -> None:
        """No ``SOURCES_ROOT`` given (this class's default, via
        ``_run_overlay``) means this call site has no ``--sources`` root to
        validate the glob against -- it strips, same as ``action/run.sh``'s
        own equivalent handling does when it has no known ``--sources``
        root either. See ``TestAssuranceOverlayPreservesUsableDiscoveredCompileDb``
        below for the PR #1222 Finding 2 fix: when this step's own
        effective ``--sources`` root IS known (mirroring its ``sources``
        Action input), a demonstrably-resolving discovered ``compile_db``
        now survives instead of always being stripped."""
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "build:\n  compile_db: compile_commands.json\n", encoding="utf-8"
        )
        (workspace / "compile_commands.json").write_text("[]", encoding="utf-8")
        result = _run_overlay(workspace, {"BASE_CONFIG": ""})
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert "compile_db" not in written.get("build", {})

    def test_other_build_and_compile_settings_survive_the_strip(
        self, tmp_path: Path
    ) -> None:
        """Negative control: the strip must be scoped to exactly the four
        execution/resource-ceiling keys -- every other build:/compile:
        setting must reach the overlay unchanged."""
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "build:\n"
            "  query: cmake --build . --target print-abi-flags\n"
            "  system: cmake\n"
            "compile:\n"
            "  compiler: /tmp/evil-compiler.sh\n"
            "  std: c++17\n",
            encoding="utf-8",
        )
        result = _run_overlay(workspace, {"BASE_CONFIG": ""})
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["build"] == {"system": "cmake"}
        assert written["compile"] == {"std": "c++17"}

    def test_explicit_build_config_query_and_compiler_are_not_stripped(
        self, tmp_path: Path
    ) -> None:
        """An operator's own explicit build-config is the trusted case
        these same gates exist to allow (``cli_options.py``'s
        ``explicit_config = build_config is not None``) -- naming the file
        is itself the deliberate authorization, so neither key is stripped
        from it, unchanged from before this fix."""
        workspace = make_workspace(tmp_path)
        explicit_config = workspace / "explicit.yml"
        explicit_config.write_text(
            "build:\n  query: cmake --build . --target print-abi-flags\n"
            "compile:\n  compiler: /usr/bin/g++-custom\n",
            encoding="utf-8",
        )
        result = _run_overlay(workspace, {"BASE_CONFIG": str(explicit_config)})
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["build"]["query"] == "cmake --build . --target print-abi-flags"
        assert written["compile"]["compiler"] == "/usr/bin/g++-custom"
        assert "build.query" not in result.stderr
        assert "compile.compiler" not in result.stderr


class TestAssuranceOverlayPreservesUsableDiscoveredCompileDb:
    """Finding 2 (P1, PR #1222 Codex review, commit d332fffa8, fresh
    evidence): this step used to strip a discovered ``build.compile_db``
    UNCONDITIONALLY, believing it had no ``--sources`` root to validate the
    glob against -- but this step's own ``sources`` Action input (mirrored
    into ``SOURCES_ROOT``/``ABICHECK_SOURCES_ROOT`` by the overlay step's
    ``env:`` block, see ``actions/check-target/action.yml``'s own comment
    there) IS that root, exactly the same evidence ``action/run.sh``'s own
    compile-context overlay already uses to keep a demonstrably-resolving
    discovered ``compile_db`` (``TestCompileContextPreservesUsableDiscoveredCompileDb``
    in ``tests/test_action_compile_context_parity.py``, which this class
    mirrors). Both callers now share the identical resolution primitive
    (``abicheck.action_config_overlay.discovered_compile_db_resolves``) so
    they cannot independently drift on what counts as "resolves".
    """

    def test_resolving_compile_db_survives_the_merge(self, tmp_path: Path) -> None:
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "build:\n  compile_db: compile_commands.json\n  system: cmake\n",
            encoding="utf-8",
        )
        (workspace / "compile_commands.json").write_text("[]", encoding="utf-8")
        result = _run_overlay(
            workspace, {"BASE_CONFIG": "", "SOURCES_ROOT": str(workspace)}
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["build"] == {
            "compile_db": "compile_commands.json",
            "system": "cmake",
        }
        assert "build.compile_db" not in result.stderr

    def test_nonresolving_compile_db_is_still_stripped(self, tmp_path: Path) -> None:
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "build:\n  compile_db: nonexistent_compile_commands.json\n"
            "  system: cmake\n",
            encoding="utf-8",
        )
        result = _run_overlay(
            workspace, {"BASE_CONFIG": "", "SOURCES_ROOT": str(workspace)}
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert "compile_db" not in written.get("build", {})
        assert written["build"] == {"system": "cmake"}
        assert "build.compile_db" in result.stderr

    def test_relative_sources_root_still_resolves(self, tmp_path: Path) -> None:
        """``sources`` is normally a checkout-relative path too (e.g.
        ``sources: src``) -- the overlay step absolutizes ``SOURCES_ROOT``
        against ``$_real_pwd`` the same way it already does for
        ``BASE_CONFIG``. PR #1222 ninth round: ``build.compile_db`` now
        lives in the sources-root's own doc -- ``build:`` is exclusive."""
        workspace = make_workspace(tmp_path)
        src_dir = workspace / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "build:\n  compile_db: compile_commands.json\n", encoding="utf-8"
        )
        (src_dir / "compile_commands.json").write_text("[]", encoding="utf-8")
        result = _run_overlay(workspace, {"BASE_CONFIG": "", "SOURCES_ROOT": "src"})
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["build"] == {"compile_db": "compile_commands.json"}

    def test_path_traversal_outside_sources_root_is_still_stripped(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence: a discovered ``build.compile_db``
        naming a path that escapes ``SOURCES_ROOT`` via ``..`` (or a
        symlink -- see ``TestDiscoveredCompileDbResolves`` in
        ``tests/test_action_config_overlay.py`` for the symlink-escape
        case, tested at the primitive level) must never be treated as
        "resolves", matching the containment check
        ``discovered_compile_db_resolves`` performs. PR #1222 ninth round:
        the traversing values now live in the sources-root's own doc."""
        workspace = make_workspace(tmp_path)
        sources_dir = workspace / "sources"
        sources_dir.mkdir()
        outside_dir = workspace / "outside"
        outside_dir.mkdir()
        (outside_dir / "secret_compile_commands.json").write_text(
            "[]", encoding="utf-8"
        )
        (sources_dir / ".abicheck.yml").write_text(
            "build:\n"
            "  compile_db: ../outside/secret_compile_commands.json\n"
            "  system: cmake\n",
            encoding="utf-8",
        )
        result = _run_overlay(
            workspace, {"BASE_CONFIG": "", "SOURCES_ROOT": str(sources_dir)}
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert "compile_db" not in written.get("build", {})
        assert written["build"] == {"system": "cmake"}
        assert "build.compile_db" in result.stderr

    def test_consumer_context_success_means_no_sources_root_still_strips(
        self, tmp_path: Path
    ) -> None:
        """A blank ``SOURCES_ROOT`` (the app-consumer path's own effective
        --sources -- see the overlay step's ``env:`` comment: a successful
        ``consumer_context`` step means "Run analysis" forwards no
        --sources at all) must still strip, exactly like the no-``sources``-
        input case above -- it must never be confused with "any root is
        fine"."""
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "build:\n  compile_db: compile_commands.json\n", encoding="utf-8"
        )
        (workspace / "compile_commands.json").write_text("[]", encoding="utf-8")
        result = _run_overlay(workspace, {"BASE_CONFIG": "", "SOURCES_ROOT": ""})
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert "compile_db" not in written.get("build", {})


class TestAssuranceOverlayPromotesSourcesRootBuildAndSourcesBlocks:
    """P1 finding (Codex review, fresh evidence, PR #1222 third round,
    commit 04afc779c): with ``analysis-assurance-complete: true``, no
    explicit ``build-config``, and ``inputs.sources`` naming a tree that
    carries its OWN ``.abicheck.yml``, this step used to forward its
    synthesized overlay as an explicit ``--build-config`` without ever
    consulting that sources-root document at all -- ``discover_project_config``
    (used for the CHECKOUT-root document) only walks UP from
    ``ABICHECK_DISCOVER_START``, so it can never find a config that lives
    below it, at ``inputs.sources`` itself. Since every downstream
    ``--build-config`` from this step is explicit (``cli_options.py``'s
    ``explicit_config = build_config is not None``), that permanently
    short-circuited the nested compare/scan's own single-sided
    ``build_config or discover_build_config(sources)`` resolution
    (``embed_build_source()``, ``cli_options.py``'s compile-context
    resolution) -- silently dropping the sources root's own ``build:``/
    ``sources:`` settings (compile-DB selection, build-system targets,
    graph-detail settings) purely because this overlay-generation step
    shadowed them, not because the caller asked for that.

    Fixed by looking up ``inputs.sources``' own ``.abicheck.yml`` via the
    identical ``config_paths.discover_build_config`` (non-recursive,
    anchored at the sources root) ``embed_build_source()`` itself uses, and
    -- when it names a document distinct from whatever the checkout-root
    walk found -- REPLACING (never merging) ``build:``/``sources:`` from
    that document, via the same shared primitive
    (``abicheck.action_config_overlay.apply_sources_root_config_blocks``)
    ``action/run.sh``'s own compile-context overlay already uses for the
    identical promotion, so the two callers cannot independently drift.
    """

    def test_sources_root_build_and_sources_blocks_are_promoted_with_no_checkout_root_config(
        self, tmp_path: Path
    ) -> None:
        """No checkout-root ``.abicheck.yml`` at all -- the sources-root
        document alone supplies ``build:``/``sources:``, merged with the
        synthesized ``assurance.require_complete: true``."""
        workspace = make_workspace(tmp_path)
        src_dir = workspace / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "build:\n  system: cmake\n  targets: [mylib]\nsources:\n  graph: full\n",
            encoding="utf-8",
        )
        result = _run_overlay(
            workspace, {"BASE_CONFIG": "", "SOURCES_ROOT": str(src_dir)}
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["build"] == {"system": "cmake", "targets": ["mylib"]}
        assert written["sources"] == {"graph": "full"}
        assert written["assurance"] == {"require_complete": True}

    def test_sources_root_blocks_replace_a_conflicting_checkout_root_document(
        self, tmp_path: Path
    ) -> None:
        """Both a checkout-root AND a distinct sources-root config exist --
        the sources-root's own ``build:``/``sources:`` REPLACE (not merge
        into) the checkout-root document's, exactly as the native CLI's own
        single-sided ``build_config or discover_build_config(sources)``
        selection would have picked when no explicit ``--config`` was
        given; every OTHER checkout-root setting (severity, here) survives
        untouched."""
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "severity:\n  preset: strict\n"
            "build:\n  system: bazel\n"
            "sources:\n  graph: summary\n",
            encoding="utf-8",
        )
        src_dir = workspace / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "build:\n  system: cmake\n", encoding="utf-8"
        )
        result = _run_overlay(
            workspace, {"BASE_CONFIG": "", "SOURCES_ROOT": str(src_dir)}
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["build"] == {"system": "cmake"}
        # The sources-root document defines no "sources" block at all --
        # REPLACE-or-remove semantics clear the checkout-root's own one
        # rather than leaving it in place (apply_sources_root_config_blocks'
        # own documented behavior).
        assert "sources" not in written
        assert written["severity"] == {"preset": "strict"}

    def test_sources_root_promotion_is_still_subject_to_execution_key_stripping(
        self, tmp_path: Path
    ) -> None:
        """The promoted ``build:`` block is untrusted, auto-discovered
        content exactly like the checkout-root one -- ``build.query`` must
        still be stripped from it (Codex review's own trust-boundary
        concern applies uniformly, regardless of which document
        ``build:`` actually came from)."""
        workspace = make_workspace(tmp_path)
        src_dir = workspace / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "build:\n  query: cmake --build .\n  system: cmake\n",
            encoding="utf-8",
        )
        result = _run_overlay(
            workspace, {"BASE_CONFIG": "", "SOURCES_ROOT": str(src_dir)}
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["build"] == {"system": "cmake"}
        assert "build.query" in result.stderr

    def test_same_config_at_both_roots_is_not_double_loaded(
        self, tmp_path: Path
    ) -> None:
        """Negative control: when the sources root resolves to the SAME
        file the checkout-root walk already found (e.g. ``sources: .``),
        promotion must be a no-op -- it must never re-read/re-validate the
        same document a second time under a different label."""
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "build:\n  system: cmake\n", encoding="utf-8"
        )
        result = _run_overlay(
            workspace, {"BASE_CONFIG": "", "SOURCES_ROOT": str(workspace)}
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["build"] == {"system": "cmake"}

    def test_empty_sources_root_config_clears_the_checkout_root_blocks(
        self, tmp_path: Path
    ) -> None:
        """An existing-but-empty sources-root ``.abicheck.yml`` is not "no
        config found" -- ``discover_build_config``'s selection is
        exclusive, so it must CLEAR the checkout-root's own ``build:``/
        ``sources:`` rather than leaving them in place, matching
        ``load_build_config``'s own empty-``BuildConfig`` outcome."""
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "build:\n  system: bazel\n", encoding="utf-8"
        )
        src_dir = workspace / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text("", encoding="utf-8")
        result = _run_overlay(
            workspace, {"BASE_CONFIG": "", "SOURCES_ROOT": str(src_dir)}
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert "build" not in written

    def test_malformed_sources_root_config_fails_loud(self, tmp_path: Path) -> None:
        """A schema-invalid sources-root document must fail the same loud
        way an invalid checkout-root/explicit one already does -- not be
        silently stripped/replaced before the nested CLI ever sees it."""
        workspace = make_workspace(tmp_path)
        src_dir = workspace / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text("build:\n  query: 7\n", encoding="utf-8")
        result = _run_overlay(
            workspace, {"BASE_CONFIG": "", "SOURCES_ROOT": str(src_dir)}
        )
        assert result.returncode != 0
        assert "::error::" in result.stderr

    def test_malformed_sources_root_yaml_fails_loud(self, tmp_path: Path) -> None:
        """Unparsable YAML at the sources root must fail loud too, not
        raise an unhandled traceback."""
        workspace = make_workspace(tmp_path)
        src_dir = workspace / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text("build: [unterminated", encoding="utf-8")
        result = _run_overlay(
            workspace, {"BASE_CONFIG": "", "SOURCES_ROOT": str(src_dir)}
        )
        assert result.returncode != 0
        assert "::error::" in result.stderr
        assert "failed to parse the discovered sources-root config" in result.stderr


class TestAssuranceOverlayRebasesRelativeIncludeDirs:
    """Finding 2 (P1, SECURITY-ADJACENT CORRECTNESS, PR #1222 Codex review,
    commit 148c624be):

    The overlay step writes its merged document to a FRESH path under
    ``$RUNNER_TEMP`` (see ``TestAssuranceOverlayOutputPathIsPrivate`` above
    for why). A ``compile.include_dirs`` entry in the base config that is
    relative (e.g. ``[include]``) is documented to resolve against the
    config file's own project root (``config_paths.project_root_for_config``)
    -- but the nested root Action's CLI resolves it against wherever
    ``--config`` actually points, which after this step runs is the
    ``$RUNNER_TEMP`` overlay file's own directory, not the real project.
    Left unrebased, this silently parses the wrong (or a nonexistent)
    header surface with no diagnostic.

    Fixed by rewriting every ``compile.include_dirs`` entry to an absolute
    path anchored at the ORIGINAL config's own project root before writing
    the overlay -- via the identical shared
    ``abicheck.action_config_overlay.rebase_relative_config_paths``
    ``action/run.sh``'s own compile-context/release-topology overlays
    already use (see ``tests/test_action_release_topology_config.py``'s own
    ``test_relative_include_dir_resolves_against_project_root_not_tmp`` for
    that call site's identical regression test). Applies to BOTH a
    discovered and an explicit base document -- this is a pure correctness
    concern, independent of the trust question Finding 1 addresses.
    """

    def test_discovered_relative_include_dir_resolves_against_project_root(
        self, tmp_path: Path
    ) -> None:
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "compile:\n  include_dirs: [include]\n", encoding="utf-8"
        )
        result = _run_overlay(workspace, {"BASE_CONFIG": ""})
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        resolved = written["compile"]["include_dirs"]
        # The bug this finding names: an unrebased relative entry would
        # instead resolve against the overlay file's own $RUNNER_TEMP-style
        # scratch directory (never a real ancestor of `workspace`) once the
        # nested Action's CLI reads it back from `config-path`.
        assert resolved == [str((workspace / "include").resolve())]
        config_path = Path(result.outputs["config-path"])
        assert not resolved[0].startswith(str(config_path.parent))

    def test_discovered_multiple_relative_include_dirs_all_resolve(
        self, tmp_path: Path
    ) -> None:
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "compile:\n  include_dirs: [a, b]\n", encoding="utf-8"
        )
        result = _run_overlay(workspace, {"BASE_CONFIG": ""})
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["compile"]["include_dirs"] == [
            str((workspace / "a").resolve()),
            str((workspace / "b").resolve()),
        ]

    def test_discovered_absolute_include_dir_is_left_unchanged(
        self, tmp_path: Path
    ) -> None:
        abs_dir = str(tmp_path / "somewhere-else")
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            f"compile:\n  include_dirs: [{abs_dir}]\n", encoding="utf-8"
        )
        result = _run_overlay(workspace, {"BASE_CONFIG": ""})
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["compile"]["include_dirs"] == [abs_dir]

    def test_explicit_build_config_relative_include_dir_also_resolves(
        self, tmp_path: Path
    ) -> None:
        """Finding 2 applies to an explicit build-config too -- an explicit
        --config with a relative compile.include_dirs breaks exactly the
        same way a discovered one does the moment its document moves to
        this step's own overlay file."""
        workspace = make_workspace(tmp_path)
        explicit_dir = workspace / "explicit-project"
        explicit_dir.mkdir()
        explicit_config = explicit_dir / "explicit.yml"
        explicit_config.write_text(
            "compile:\n  include_dirs: [include]\n", encoding="utf-8"
        )
        result = _run_overlay(workspace, {"BASE_CONFIG": str(explicit_config)})
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["compile"]["include_dirs"] == [
            str((explicit_dir / "include").resolve())
        ]


class TestAssuranceOverlayValidatesBaseConfigBeforeStripping:
    """P2 finding (PR #1222 Codex review, this commit): the base config
    document (discovered OR explicit) was never validated against the real
    ``BuildConfig`` schema before this step's own stripping/overlay logic
    ran. A schema-invalid value in a field this step goes on to STRIP
    (``build.query: 7``, ``compile.compiler: []``) was previously silently
    deleted as part of ordinary stripping -- before the nested "Run
    analysis" step's own CLI ever got a chance to parse and reject it --
    turning a config a direct ``compare --config <file>`` invocation would
    refuse outright into a silently-accepted run purely because
    ``analysis-assurance-complete`` happened to be enabled.

    Fixed by validating the loaded document via the shared
    ``abicheck.action_config_overlay.validate_base_config`` (the same
    ``BuildConfig.from_dict`` check ``action/run.sh``'s own equivalent merge
    already applies, see
    ``tests/test_action_run_sh_config_validation.py``'s
    ``TestMergeConfigOverlayValidatesBaseConfigBeforeStripping`` for that
    call site's own regression coverage, and
    ``tests/test_action_config_overlay.py``'s ``TestValidateBaseConfig``
    for the shared function's own direct, harness-independent tests)
    BEFORE any stripping or overlay merge runs.
    """

    def test_discovered_invalid_build_query_type_fails_loud(
        self, tmp_path: Path
    ) -> None:
        """The exact scenario the finding names: build.query: 7 is a field
        this step strips unconditionally -- validating AFTER stripping (the
        bug) would never see the invalid value at all."""
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "build:\n  query: 7\n", encoding="utf-8"
        )
        result = _run_overlay(workspace, {"BASE_CONFIG": ""})
        assert result.returncode != 0
        assert "::error::" in result.stderr
        assert "build.query" in result.stderr
        assert "config-path" not in result.outputs

    def test_discovered_invalid_compile_compiler_type_fails_loud(
        self, tmp_path: Path
    ) -> None:
        """compile.compiler: [] -- also stripped unconditionally, also must
        be validated first."""
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "compile:\n  compiler: []\n", encoding="utf-8"
        )
        result = _run_overlay(workspace, {"BASE_CONFIG": ""})
        assert result.returncode != 0
        assert "::error::" in result.stderr
        assert "compile.compiler" in result.stderr
        assert "config-path" not in result.outputs

    def test_discovered_invalid_build_compile_db_type_fails_loud(
        self, tmp_path: Path
    ) -> None:
        """build.compile_db: false -- this call site strips it
        unconditionally too (no --sources root to validate a resolving
        glob against)."""
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "build:\n  compile_db: false\n", encoding="utf-8"
        )
        result = _run_overlay(workspace, {"BASE_CONFIG": ""})
        assert result.returncode != 0
        assert "::error::" in result.stderr
        assert "compile_db" in result.stderr
        assert "config-path" not in result.outputs

    def test_explicit_build_config_invalid_document_also_fails_loud(
        self, tmp_path: Path
    ) -> None:
        """Schema validity is independent of the trust question -- an
        explicit build-config is trusted to run build.query/
        compile.compiler, but a direct `compare --config <file>` against it
        would still reject a structurally invalid document just as loudly
        as a discovered one."""
        workspace = make_workspace(tmp_path)
        explicit_config = workspace / "explicit.yml"
        explicit_config.write_text("build:\n  query: 7\n", encoding="utf-8")
        result = _run_overlay(workspace, {"BASE_CONFIG": str(explicit_config)})
        assert result.returncode != 0
        assert "::error::" in result.stderr
        assert "config-path" not in result.outputs

    def test_valid_discovered_document_is_unaffected(self, tmp_path: Path) -> None:
        """Negative control: a schema-valid discovered document must still
        produce a normal, successful overlay -- this fix must not reject
        anything it didn't reject before."""
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "build:\n  system: cmake\ncompile:\n  std: c++17\n", encoding="utf-8"
        )
        result = _run_overlay(workspace, {"BASE_CONFIG": ""})
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["build"] == {"system": "cmake"}
        assert written["compile"] == {"std": "c++17"}
        assert written["assurance"] == {"require_complete": True}


class TestAssuranceOverlayCopiesAliasedAssuranceMapping:
    """P2 finding (PR #1222 Codex review, this commit): ``yaml.safe_load()``
    resolves a YAML anchor/alias pair (``assurance: &shared {}`` /
    ``gate: *shared``) to the SAME dict object for both keys. The step's own
    merge used to do ``assurance["require_complete"] = True`` directly on
    whatever object ``data.get("assurance")`` returned -- when that object
    was shared via an alias, this ALSO inserted ``require_complete`` into
    the other key's mapping, and ``yaml.safe_dump()`` then preserved the
    alias relationship, writing ``require_complete`` under BOTH
    ``assurance`` and the unrelated aliased key in the generated overlay.
    The nested CLI then rejected the overlay outright (``gate.
    require_complete`` is not a recognized field there) even though the
    original, unmodified config was perfectly valid.

    Fixed by copying ``assurance`` (``dict(assurance)``) before adding
    ``require_complete`` to it, so the step never mutates an object the
    parsed document's OTHER top-level keys might still reference.
    """

    def test_aliased_gate_mapping_is_unaffected_by_the_overlay(
        self, tmp_path: Path
    ) -> None:
        """The exact scenario the finding names: `assurance` and `gate`
        share one aliased mapping in the base document."""
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "assurance: &shared {}\ngate: *shared\n", encoding="utf-8"
        )
        result = _run_overlay(workspace, {"BASE_CONFIG": ""})
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["assurance"] == {"require_complete": True}
        # The bug this finding names: `gate` must NOT pick up
        # `require_complete` just because it was aliased to the same
        # object `assurance` started out as.
        assert written["gate"] == {}

    def test_aliased_mapping_with_preexisting_content_is_also_unaffected(
        self, tmp_path: Path
    ) -> None:
        """A stronger variant: the shared mapping already carries a real
        field (`require_complete: false`) -- `require_complete` is the
        ONLY key `assurance:` recognizes, so the other end of the alias is
        `baseline:` here (a top-level block `BuildConfig`'s own structure
        check does not inspect at all, unlike `gate:`, which only
        recognizes `fail_on_removed_library` and would reject
        `baseline.require_complete` as an unrelated schema error having
        nothing to do with this finding). A naive fix that merely swaps in
        a *fresh* `{}` instead of a genuine `dict(assurance)` copy (losing
        the base document's own `assurance` content) would be caught here."""
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "assurance: &shared\n  require_complete: false\nbaseline: *shared\n",
            encoding="utf-8",
        )
        result = _run_overlay(workspace, {"BASE_CONFIG": ""})
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["assurance"] == {"require_complete": True}
        # The bug this finding names: `baseline` must keep the base
        # document's own (pre-overlay) value, not pick up the overlay's
        # `True` just because it was aliased to the same object.
        assert written["baseline"] == {"require_complete": False}

    def test_explicit_build_config_aliased_gate_mapping_is_also_unaffected(
        self, tmp_path: Path
    ) -> None:
        """Same hazard, explicit build-config branch -- the aliasing risk is
        in the shared merge code past the discovered/explicit fork, not
        specific to either loading path."""
        workspace = make_workspace(tmp_path)
        explicit_config = workspace / "explicit.yml"
        explicit_config.write_text(
            "assurance: &shared {}\ngate: *shared\n", encoding="utf-8"
        )
        result = _run_overlay(workspace, {"BASE_CONFIG": str(explicit_config)})
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["assurance"] == {"require_complete": True}
        assert written["gate"] == {}
