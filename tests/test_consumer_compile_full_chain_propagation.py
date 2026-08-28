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

"""One real sentinel value, threaded through every real hop of the
``consumer_compile`` propagation chain (bug-class-regression-testing.md
Phase 6) -- not just a substring/exact-text assertion at each hop in
isolation.

A third Codex review round (PR #906) found the sentinel chain covered only
``consumer_compile``'s ``binding``/``standard``/``stdlib`` fields (the
compiler path/options), never its ``frontend`` field -- even though the
fixture never set ``consumer_compile.frontend`` at all, so
``consumer-ast-frontend`` was only ever evaluated as an empty string and
never carried to hop 4's ``--ast-frontend`` argument or checked against the
root-action env mapping. A regression in ``consumer-ast-frontend``'s own
expression, or a swap of ``INPUT_AST_FRONTEND`` with another declared
input, could therefore have gone undetected by this file even though
``frontend`` is part of the concern the registry now declares closed. Fixed
by threading a second, distinct sentinel for ``frontend`` through the
identical hops the gcc-path/gcc-options sentinel already traverses.

Codex review (PR #906) on the sibling tests in `test_reusable_workflows_
project_evidence.py`/`test_run_plan_consumer_compile_active.py`/
`test_project_targets_consumer_compile.py`: those prove each hop's
expression *names* the right upstream field (or, for `generate_run_plan`
and `run.sh`, exercise their own real logic directly) but never prove a
*single value* actually survives being evaluated through the unmodified
expression text at every hop in sequence. A refactor that swapped `&&`/
`||`, inverted a guard, or reordered a ternary's branches could keep every
substring/exact-text match those tests check while silently changing
which value reaches the next hop.

This test closes that gap using `_gha_expr.eval_gha_expression` (a real,
if deliberately narrow, GHA-expression evaluator -- see that module's own
docstring) to evaluate the REAL, unmodified expression text pulled live
from `check-project.yml`/`check-target/action.yml`, chained:

    .abicheck.yml config
      -> generate_run_plan() [real Python call]
      -> check-project.yml's "Run check-target" step [real expression, evaluated]
      -> check-target/action.yml's "Extract candidate consumer context" step's
         own SCHEDULING GUARD (its `if:`, not just its `with:`) [real
         expression, evaluated]
      -> that same step's `with:` [real expression, evaluated]
      -> root action.yml's own declared `gcc-path`/`gcc-options` inputs,
         resolved to their real INPUT_* env var names via the real
         "Run abicheck" step's own, isolated env block
         (test_action_run_contract.py's `_step_env_mapping`) -- never a
         hardcoded `INPUT_GCC_PATH` guess
      -> root action.yml's run.sh [real bash execution, via the existing
         test_action_compile_context_parity.py harness]

with one sentinel binding path/options string surviving, unaltered, end
to end. Every real `uses:` edge between the loaded files is also asserted
(`_run_check_step`/`_consumer_context_step`), not just assumed from their
hardcoded paths.

Two further Codex review rounds on this same file, both fixed here: (1)
the first cut evaluated only the consumer-context step's `with:` values,
never its own `if:` scheduling guard -- a `||`-to-`&&` regression in that
guard would still leave every `with:`-level assertion green while the
step (and therefore the whole consumer-context dump) silently never runs
at all. (2) the first cut also hardcoded `INPUT_GCC_PATH`/`INPUT_GCC_
OPTIONS` as the env var names fed to `run.sh`, disconnecting hop 3 (the
resolved `inputs.gcc-path`/`inputs.gcc-options` values) from hop 4 (which
env vars `run.sh` actually reads) -- a swap of those two mappings in root
action.yml's own env block would have gone undetected.

A fifth Codex review round found two more real gaps, both fixed here: (3)
hop 4 read env var names from `_action_yml_env_mapping()`, which
deliberately scans the *whole* action.yml file (by design, for its own
different purpose -- see that function's docstring); action.yml's earlier
"Validate mode/input combination" step duplicates some of the same
INPUT_* names in its own, differently-isolated env block, so removing an
entry from the "Run abicheck" step specifically -- the step this hop
actually executes -- could still resolve via the validation step's
leftover entry, leaving this test green while production silently dropped
the value at this hop. Fixed by reading the step-scoped
`_step_env_mapping("Run abicheck")` instead. (4) neither
`_run_check_step`/`_consumer_context_step` ever checked that the step's
own `uses:` edge still pointed at the file being loaded and evaluated --
repointing "Run check-target"'s `uses:` away from `actions/check-target`,
or the consumer-context step's `uses:` away from the checked-out
repository root, would have left this test evaluating stale files nothing
in production actually invokes anymore. Both helpers now assert their
step's real `uses:` value before returning it.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from _gha_expr import eval_gha_expression
from test_action_compile_context_parity import _DUMP_MODE_MARKER, _run_region
from test_action_run_contract import ACTION_YML, _step_env_mapping
from test_reusable_workflows_project_evidence import (
    CHECK_PROJECT,
    CHECK_TARGET,
    _load,
    _steps,
)
from test_run_plan import TestConsumerCompileOverlayProjection, _bo, _parsed

from abicheck.buildsource.run_plan import generate_run_plan

_SENTINEL_CONSUMER_GCC_PATH = "/opt/SENTINEL-consumer-toolchain/bin/clang++"
_SENTINEL_PRODUCER_GCC_PATH = "/opt/SENTINEL-producer-toolchain/bin/g++"

# Distinct from the values above -- a workflow-global fallback the chain
# must NOT leak through, proving the "consumer_compile_active gates the
# fallback too" rule (test_consumer_dump_activates_from_the_overlay_
# marker_alone's own subject) holds under real evaluation, not just text
# matching.
_WORKFLOW_GLOBAL_GCC_PATH = "/opt/SENTINEL-workflow-global/bin/gcc"
_WORKFLOW_GLOBAL_GCC_OPTIONS = "-DWORKFLOW_GLOBAL_SENTINEL=1"

# The `frontend` field of the same `consumer_compile` concern, threaded
# through the identical hops via its own sentinel/fallback pair -- must be
# one of the real, validated `ast-frontend` values (auto/castxml/clang/
# hybrid; action/run.sh's own validate-inputs guard), so "clang" and
# "hybrid" stand in for the sentinel/workflow-global roles respectively.
_SENTINEL_CONSUMER_AST_FRONTEND = "clang"
_WORKFLOW_GLOBAL_AST_FRONTEND = "hybrid"

# Distinct sentinels for the fallback-branch test below: an ACTIVE overlay
# that only sets `frontend:` leaves gcc-path/gcc-options unset, which must
# fall back to these nonempty workflow-global values -- not to "" (the
# marker-only test's scenario) and not leaked when inactive (the bundle/
# no-overlay test's scenario). Distinct from every other sentinel in this
# module so a wrong-source value is unambiguous.
_FALLBACK_GCC_PATH = "/opt/SENTINEL-fallback-toolchain/bin/gcc"
_FALLBACK_GCC_OPTIONS = "-DFALLBACK_SENTINEL=1"


def _run_check_step(name: str) -> dict[str, Any]:
    """A named step from `check-project.yml`'s `check` job. For "Run
    check-target" specifically, also asserts its real `uses:` edge still
    targets `actions/check-target` -- the file this module loads directly
    as `CHECK_TARGET` and evaluates hop 3's expressions from -- so
    repointing that edge to a different action (Codex review, PR #906)
    would fail here rather than leave this test silently evaluating a file
    production no longer actually invokes at this step."""
    project = _load(CHECK_PROJECT)
    step = next(
        step for step in _steps(project["jobs"]["check"]) if step.get("name") == name
    )
    if name == "Run check-target":
        uses = step.get("uses", "")
        # The exact real value, not a mere suffix check (Codex review,
        # PR #906) -- a suffix match would also accept a repointed
        # `./some-other/actions/check-target`, which names a completely
        # different checked-out tree while still ending in the same two
        # path segments.
        assert uses == "./.check-project-src/actions/check-target", (
            f"the {name!r} step's own `uses:` ({uses!r}) no longer targets "
            f"./.check-project-src/actions/check-target -- this test loads "
            f"that file directly (CHECK_TARGET) and would silently keep "
            f"evaluating it even if production repointed this edge "
            f"elsewhere"
        )
    return step


def _assert_run_check_target_step_guard_fires(run_step: dict[str, Any]) -> None:
    """The parent workflow's own scheduling guard on the "Run check-target"
    step itself (`if: steps.candidate.outcome == 'success'`), evaluated
    against a realistic successful-candidate `steps.*` context -- mirrors
    the consumer-context step's own guard check below (Codex review,
    PR #906): an inverted or renamed guard here would skip the whole step
    in production while this test still manually forwards every sentinel
    into its `with:` values and passes regardless."""
    guard = eval_gha_expression(run_step["if"], steps={"candidate.outcome": "success"})
    assert guard is True, (
        "the 'Run check-target' step's own scheduling guard (`if:`) does "
        "not evaluate truthy for a successful candidate -- the step would "
        "never run in a real workflow, silently skipping this whole "
        "propagation chain regardless of what its `with:` values resolve to"
    )


def _consumer_context_step() -> dict[str, Any]:
    """The "Extract candidate consumer context" step from `actions/check-
    target/action.yml`. Also asserts its real `uses:` edge still targets
    the checked-out repository root (`./.abicheck-check-target-src`, no
    subdirectory) -- the same tree root `action.yml` lives in, which hop 4
    below assumes this step invokes directly (Codex review, PR #906):
    repointing it to a subdirectory or a different action entirely would
    otherwise go unnoticed."""
    target = _load(CHECK_TARGET)
    step = next(
        step
        for step in _steps(target["runs"])
        if step.get("name") == "Extract candidate consumer context"
    )
    assert step.get("uses") == "./.abicheck-check-target-src", (
        f"the 'Extract candidate consumer context' step's own `uses:` "
        f"({step.get('uses')!r}) no longer targets the checked-out "
        f"repository root -- hop 4 assumes this step invokes root "
        f"action.yml directly"
    )
    return step


def _hop3_inputs_from(
    *,
    kind: Any,
    baseline_channel: Any,
    consumer_compile_active: Any,
    consumer_ast_frontend: Any,
    consumer_gcc_path: Any,
    consumer_gcc_options: Any,
) -> dict[str, Any]:
    """Hop-3 `inputs.*` context, built from hop-2's own evaluated values
    verbatim -- no Python-truthiness re-coercion of `consumer-compile-
    active` (Codex review, PR #906). The real expression (`... &&
    'true' || 'false'`) always resolves to the literal string `'true'` or
    `'false'`, exactly what GHA itself would forward as an action input --
    but re-wrapping it in `"true" if consumer_compile_active else "false"`
    was a real bug: both strings are non-empty and therefore Python-truthy,
    so that re-coercion silently collapsed EVERY evaluated result to
    `"true"`, masking a regression that made the real expression resolve
    to anything else (a bare boolean, or a typo like `"yes"`)."""
    return {
        "kind": kind,
        "baseline-channel": baseline_channel,
        "consumer-compile-active": consumer_compile_active,
        "consumer-ast-frontend": consumer_ast_frontend,
        "consumer-gcc-path": consumer_gcc_path,
        "consumer-gcc-options": consumer_gcc_options,
    }


def test_sentinel_consumer_gcc_path_survives_config_to_check_target_input() -> None:
    """Hops 1-3: config -> generate_run_plan() -> check-project.yml ->
    check-target/action.yml's scheduling guard AND its `with:`, evaluated
    for real at each of the latter two."""
    raw: dict[str, Any] = deepcopy(TestConsumerCompileOverlayProjection._RAW)
    raw["profiles"]["gcc14-build-clang20-client"]["consumer_compile"]["frontend"] = (
        _SENTINEL_CONSUMER_AST_FRONTEND
    )
    config = _parsed(raw)
    plan, report = generate_run_plan(
        config,
        {
            "gcc14-build-clang20-client": _bo("libfoo"),
            "plain": _bo("libfoo"),
        },
        resolved_bindings={
            "gcc14": _SENTINEL_PRODUCER_GCC_PATH,
            "clang20": _SENTINEL_CONSUMER_GCC_PATH,
        },
    )
    assert report.ok
    [check] = [c for c in plan.checks if c.profile_id == "gcc14-build-clang20-client"]
    matrix = check.to_dict()
    # Sanity: the fixture actually produced the sentinel, and it's distinct
    # from the producer's own -- otherwise a bug swapping the two fields
    # could go unnoticed by the assertions below.
    assert matrix["consumer_compile_gcc_path"] == _SENTINEL_CONSUMER_GCC_PATH
    assert matrix["compile_gcc_path"] == _SENTINEL_PRODUCER_GCC_PATH
    assert matrix["consumer_compile_gcc_path"] != matrix["compile_gcc_path"]
    assert matrix["consumer_compile_ast_frontend"] == _SENTINEL_CONSUMER_AST_FRONTEND

    run_step = _run_check_step("Run check-target")
    _assert_run_check_target_step_guard_fires(run_step)
    # Workflow-global inputs deliberately set to a THIRD, distinct
    # sentinel: if the real expression's own `matrix.consumer_compile_
    # active` gate were dropped, this would leak through instead of the
    # real consumer_compile_gcc_path, and the assertion below would catch
    # it (this sentinel, not the expected one, would appear). Same for
    # `ast-frontend`, with its own distinct workflow-global value.
    workflow_inputs = {
        "gcc-path": _WORKFLOW_GLOBAL_GCC_PATH,
        "gcc-options": "",
        "ast-frontend": _WORKFLOW_GLOBAL_AST_FRONTEND,
    }
    consumer_gcc_path = eval_gha_expression(
        run_step["with"]["consumer-gcc-path"], matrix=matrix, inputs=workflow_inputs
    )
    consumer_gcc_options = eval_gha_expression(
        run_step["with"]["consumer-gcc-options"], matrix=matrix, inputs=workflow_inputs
    )
    consumer_ast_frontend = eval_gha_expression(
        run_step["with"]["consumer-ast-frontend"], matrix=matrix, inputs=workflow_inputs
    )
    consumer_compile_active = eval_gha_expression(
        run_step["with"]["consumer-compile-active"], matrix=matrix, inputs={}
    )
    kind = eval_gha_expression(run_step["with"]["kind"], matrix=matrix)
    baseline_channel = eval_gha_expression(
        run_step["with"]["baseline-channel"], matrix=matrix
    )
    assert consumer_gcc_path == _SENTINEL_CONSUMER_GCC_PATH
    assert consumer_gcc_options == "-std=gnu++20 -stdlib=libc++"
    assert consumer_ast_frontend == _SENTINEL_CONSUMER_AST_FRONTEND

    consumer_step = _consumer_context_step()
    hop3_inputs = _hop3_inputs_from(
        kind=kind,
        baseline_channel=baseline_channel,
        consumer_compile_active=consumer_compile_active,
        consumer_ast_frontend=consumer_ast_frontend,
        consumer_gcc_path=consumer_gcc_path,
        consumer_gcc_options=consumer_gcc_options,
    )
    # Every real `steps.*` reference the guard makes, standing in for a
    # baseline that already resolved and preceding collect-facts steps that
    # both succeeded -- the ordinary "everything's fine" path this cell
    # takes in practice, which is exactly the path a dropped forwarding
    # edge would silently skip.
    steps_context = {
        "resolve.outputs.outcome": "resolved",
        "collect_verify.outcome": "success",
        "collect_replay.outcome": "success",
    }
    guard = eval_gha_expression(
        consumer_step["if"], inputs=hop3_inputs, steps=steps_context
    )
    assert guard is True, (
        "the consumer-context step's own scheduling guard (`if:`) does not "
        "evaluate truthy for this scenario -- the step would never run in "
        "a real workflow, silently skipping the consumer dump regardless "
        "of what its `with:` values resolve to"
    )

    final_gcc_path = eval_gha_expression(
        consumer_step["with"]["gcc-path"], inputs=hop3_inputs
    )
    final_gcc_options = eval_gha_expression(
        consumer_step["with"]["gcc-options"], inputs=hop3_inputs
    )
    final_ast_frontend = eval_gha_expression(
        consumer_step["with"]["ast-frontend"], inputs=hop3_inputs
    )
    assert final_gcc_path == _SENTINEL_CONSUMER_GCC_PATH
    assert final_gcc_options == "-std=gnu++20 -stdlib=libc++"
    assert final_ast_frontend == _SENTINEL_CONSUMER_AST_FRONTEND

    _assert_reaches_real_dump_cli_invocation(
        final_gcc_path, final_gcc_options, final_ast_frontend
    )


def test_marker_only_overlay_activates_the_consumer_step_without_any_field() -> None:
    """Codex review (PR #906): the pre-existing coverage for 'an overlay
    declaring only a presence marker, with every field unresolvable' was
    text-only (test_consumer_dump_activates_from_the_overlay_marker_alone,
    a substring match against the real hop-2 expression). This proves the
    same invariant by actually evaluating the chain: the consumer-context
    step's own scheduling `if:` guard must still fire from the overlay's
    presence alone, with `consumer-ast-frontend`/`consumer-gcc-path`/
    `consumer-gcc-options` all genuinely empty -- exactly the scenario
    `_hop3_inputs_from`'s fix above targets, since a `"true" if ... else
    "false"` re-coercion bug would have masked a real regression here too
    (every string is Python-truthy, so it could never actually observe
    `consumer-compile-active` resolving to anything but the always-true
    branch)."""
    raw = {
        "targets": TestConsumerCompileOverlayProjection._RAW["targets"],
        "profiles": {
            "marker-only": {
                "contract": True,
                # A `binding:` naming nothing in `resolved_bindings` (there
                # is none here) -- the overlay is genuinely declared, not
                # empty/omitted, but every one of its fields resolves to "".
                "consumer_compile": {"binding": "unresolvable-binding"},
            },
        },
        "baseline": TestConsumerCompileOverlayProjection._RAW["baseline"],
    }
    config = _parsed(raw)
    plan, report = generate_run_plan(config, {"marker-only": _bo("libfoo")})
    assert report.ok
    [check] = plan.checks
    matrix = check.to_dict()
    assert matrix["consumer_compile_active"] is True
    assert "consumer_compile_gcc_path" not in matrix
    assert "consumer_compile_gcc_options" not in matrix
    assert "consumer_compile_ast_frontend" not in matrix

    run_step = _run_check_step("Run check-target")
    _assert_run_check_target_step_guard_fires(run_step)
    workflow_inputs = {"gcc-path": "", "gcc-options": "", "ast-frontend": ""}
    consumer_gcc_path = eval_gha_expression(
        run_step["with"]["consumer-gcc-path"], matrix=matrix, inputs=workflow_inputs
    )
    consumer_gcc_options = eval_gha_expression(
        run_step["with"]["consumer-gcc-options"], matrix=matrix, inputs=workflow_inputs
    )
    consumer_ast_frontend = eval_gha_expression(
        run_step["with"]["consumer-ast-frontend"], matrix=matrix, inputs=workflow_inputs
    )
    consumer_compile_active = eval_gha_expression(
        run_step["with"]["consumer-compile-active"], matrix=matrix, inputs={}
    )
    kind = eval_gha_expression(run_step["with"]["kind"], matrix=matrix)
    baseline_channel = eval_gha_expression(
        run_step["with"]["baseline-channel"], matrix=matrix
    )
    assert consumer_gcc_path == ""
    assert consumer_gcc_options == ""
    assert consumer_ast_frontend == ""
    assert consumer_compile_active == "true", (
        "the real hop-2 consumer-compile-active expression must resolve "
        "the literal string 'true' from the overlay's own presence marker "
        "alone, with no resolvable field behind it"
    )

    consumer_step = _consumer_context_step()
    hop3_inputs = _hop3_inputs_from(
        kind=kind,
        baseline_channel=baseline_channel,
        consumer_compile_active=consumer_compile_active,
        consumer_ast_frontend=consumer_ast_frontend,
        consumer_gcc_path=consumer_gcc_path,
        consumer_gcc_options=consumer_gcc_options,
    )
    steps_context = {
        "resolve.outputs.outcome": "resolved",
        "collect_verify.outcome": "success",
        "collect_replay.outcome": "success",
    }
    guard = eval_gha_expression(
        consumer_step["if"], inputs=hop3_inputs, steps=steps_context
    )
    assert guard is True, (
        "a marker-only overlay (no resolvable ast-frontend/gcc-path/"
        "gcc-options) must still activate the consumer-context step's own "
        "scheduling guard from its presence marker alone"
    )


def test_partial_overlay_falls_back_to_workflow_global_per_field() -> None:
    """Codex review (PR #906): a real third state neither prior test
    exercises. The main test's overlay resolves every field itself; the
    marker-only test's overlay resolves NO field and pairs it with empty
    workflow-globals. Neither can catch a regression in the real per-field
    fallback (`matrix.consumer_compile_gcc_path || inputs.gcc-path`, not a
    blanket `|| ''`): an ACTIVE overlay that sets only `frontend:` -- a
    real, common shape (pin the consumer's header frontend, reuse the
    caller's own workflow-global compiler for the rest) -- must fall back
    to the workflow-global `gcc-path`/`gcc-options` for the two fields it
    left unset, while still keeping its own `frontend` value rather than
    the workflow-global's. Deleting `inputs.gcc-path`/`inputs.gcc-options`/
    `inputs.ast-frontend` from any of the three real fallback expressions
    would leave every other test in this file green while this exact
    scenario silently loses its compiler context."""
    raw = {
        "targets": TestConsumerCompileOverlayProjection._RAW["targets"],
        "profiles": {
            "partial-overlay": {
                "contract": True,
                # Only `frontend:` -- gcc_path/gcc_options resolve empty
                # from the overlay itself (no `binding`/`standard`/
                # `stdlib`), but the overlay is still genuinely active.
                "consumer_compile": {"frontend": _SENTINEL_CONSUMER_AST_FRONTEND},
            },
        },
        "baseline": TestConsumerCompileOverlayProjection._RAW["baseline"],
    }
    config = _parsed(raw)
    plan, report = generate_run_plan(config, {"partial-overlay": _bo("libfoo")})
    assert report.ok
    [check] = plan.checks
    matrix = check.to_dict()
    assert matrix["consumer_compile_active"] is True
    assert matrix["consumer_compile_ast_frontend"] == _SENTINEL_CONSUMER_AST_FRONTEND
    assert "consumer_compile_gcc_path" not in matrix
    assert "consumer_compile_gcc_options" not in matrix

    run_step = _run_check_step("Run check-target")
    _assert_run_check_target_step_guard_fires(run_step)
    # Nonempty workflow-global sentinels, distinct from the consumer's own
    # frontend sentinel and from every other sentinel in this module, so a
    # leaked or wrongly-sourced value is unambiguous either way.
    workflow_inputs = {
        "gcc-path": _FALLBACK_GCC_PATH,
        "gcc-options": _FALLBACK_GCC_OPTIONS,
        "ast-frontend": _WORKFLOW_GLOBAL_AST_FRONTEND,
    }
    consumer_gcc_path = eval_gha_expression(
        run_step["with"]["consumer-gcc-path"], matrix=matrix, inputs=workflow_inputs
    )
    consumer_gcc_options = eval_gha_expression(
        run_step["with"]["consumer-gcc-options"], matrix=matrix, inputs=workflow_inputs
    )
    consumer_ast_frontend = eval_gha_expression(
        run_step["with"]["consumer-ast-frontend"], matrix=matrix, inputs=workflow_inputs
    )
    consumer_compile_active = eval_gha_expression(
        run_step["with"]["consumer-compile-active"], matrix=matrix, inputs={}
    )
    kind = eval_gha_expression(run_step["with"]["kind"], matrix=matrix)
    baseline_channel = eval_gha_expression(
        run_step["with"]["baseline-channel"], matrix=matrix
    )
    # The two unset overlay fields fall back to the workflow-global input...
    assert consumer_gcc_path == _FALLBACK_GCC_PATH
    assert consumer_gcc_options == _FALLBACK_GCC_OPTIONS
    # ...while the one the overlay DID set keeps its own value, not the
    # (here, deliberately different) workflow-global's.
    assert consumer_ast_frontend == _SENTINEL_CONSUMER_AST_FRONTEND

    consumer_step = _consumer_context_step()
    hop3_inputs = _hop3_inputs_from(
        kind=kind,
        baseline_channel=baseline_channel,
        consumer_compile_active=consumer_compile_active,
        consumer_ast_frontend=consumer_ast_frontend,
        consumer_gcc_path=consumer_gcc_path,
        consumer_gcc_options=consumer_gcc_options,
    )
    steps_context = {
        "resolve.outputs.outcome": "resolved",
        "collect_verify.outcome": "success",
        "collect_replay.outcome": "success",
    }
    guard = eval_gha_expression(
        consumer_step["if"], inputs=hop3_inputs, steps=steps_context
    )
    assert guard is True

    final_gcc_path = eval_gha_expression(
        consumer_step["with"]["gcc-path"], inputs=hop3_inputs
    )
    final_gcc_options = eval_gha_expression(
        consumer_step["with"]["gcc-options"], inputs=hop3_inputs
    )
    final_ast_frontend = eval_gha_expression(
        consumer_step["with"]["ast-frontend"], inputs=hop3_inputs
    )
    assert final_gcc_path == _FALLBACK_GCC_PATH
    assert final_gcc_options == _FALLBACK_GCC_OPTIONS
    assert final_ast_frontend == _SENTINEL_CONSUMER_AST_FRONTEND

    _assert_reaches_real_dump_cli_invocation(
        final_gcc_path, final_gcc_options, final_ast_frontend
    )


def test_partial_overlay_without_frontend_falls_back_to_workflow_global_frontend() -> (
    None
):
    """Codex review (PR #906): the sibling test above only omits `frontend:`
    from the overlay, so its own `matrix.consumer_compile_ast_frontend ||
    inputs.ast-frontend` fallback is never reached -- every scenario in
    this file up to now either sets the overlay's own `frontend` or pairs
    an empty overlay with an empty workflow-global. This exercises the
    fallback in the OTHER direction: an active overlay setting `binding`/
    `standard` (so gcc-path/gcc-options resolve from the overlay itself)
    but no `frontend`, paired with a nonempty workflow-global `ast-
    frontend` -- the consumer-ast-frontend expression's own `||
    inputs.ast-frontend` arm must carry it through, while gcc-path/
    gcc-options keep the overlay's own values rather than a distinct
    workflow-global that must NOT leak through for those two fields."""
    raw = {
        "targets": TestConsumerCompileOverlayProjection._RAW["targets"],
        "profiles": {
            "partial-overlay-no-frontend": {
                "contract": True,
                "consumer_compile": {"binding": "clang20", "standard": "gnu++20"},
            },
        },
        "baseline": TestConsumerCompileOverlayProjection._RAW["baseline"],
    }
    config = _parsed(raw)
    plan, report = generate_run_plan(
        config,
        {"partial-overlay-no-frontend": _bo("libfoo")},
        resolved_bindings={"clang20": _SENTINEL_CONSUMER_GCC_PATH},
    )
    assert report.ok
    [check] = plan.checks
    matrix = check.to_dict()
    assert matrix["consumer_compile_active"] is True
    assert matrix["consumer_compile_gcc_path"] == _SENTINEL_CONSUMER_GCC_PATH
    assert "consumer_compile_ast_frontend" not in matrix

    run_step = _run_check_step("Run check-target")
    _assert_run_check_target_step_guard_fires(run_step)
    # gcc-path/gcc-options globals here are the "must NOT leak" sentinels
    # (the overlay already set its own); ast-frontend is the "must fall
    # back" sentinel (the overlay set no frontend of its own).
    workflow_inputs = {
        "gcc-path": _WORKFLOW_GLOBAL_GCC_PATH,
        "gcc-options": _WORKFLOW_GLOBAL_GCC_OPTIONS,
        "ast-frontend": _WORKFLOW_GLOBAL_AST_FRONTEND,
    }
    consumer_gcc_path = eval_gha_expression(
        run_step["with"]["consumer-gcc-path"], matrix=matrix, inputs=workflow_inputs
    )
    consumer_gcc_options = eval_gha_expression(
        run_step["with"]["consumer-gcc-options"], matrix=matrix, inputs=workflow_inputs
    )
    consumer_ast_frontend = eval_gha_expression(
        run_step["with"]["consumer-ast-frontend"], matrix=matrix, inputs=workflow_inputs
    )
    consumer_compile_active = eval_gha_expression(
        run_step["with"]["consumer-compile-active"], matrix=matrix, inputs={}
    )
    kind = eval_gha_expression(run_step["with"]["kind"], matrix=matrix)
    baseline_channel = eval_gha_expression(
        run_step["with"]["baseline-channel"], matrix=matrix
    )
    # The overlay's own gcc-path/gcc-options survive, unaffected by the
    # distinct workflow-globals supplied for them...
    assert consumer_gcc_path == _SENTINEL_CONSUMER_GCC_PATH
    assert consumer_gcc_options == "-std=gnu++20"
    # ...while the field the overlay left unset falls back to the
    # workflow-global.
    assert consumer_ast_frontend == _WORKFLOW_GLOBAL_AST_FRONTEND

    consumer_step = _consumer_context_step()
    hop3_inputs = _hop3_inputs_from(
        kind=kind,
        baseline_channel=baseline_channel,
        consumer_compile_active=consumer_compile_active,
        consumer_ast_frontend=consumer_ast_frontend,
        consumer_gcc_path=consumer_gcc_path,
        consumer_gcc_options=consumer_gcc_options,
    )
    steps_context = {
        "resolve.outputs.outcome": "resolved",
        "collect_verify.outcome": "success",
        "collect_replay.outcome": "success",
    }
    guard = eval_gha_expression(
        consumer_step["if"], inputs=hop3_inputs, steps=steps_context
    )
    assert guard is True

    final_gcc_path = eval_gha_expression(
        consumer_step["with"]["gcc-path"], inputs=hop3_inputs
    )
    final_gcc_options = eval_gha_expression(
        consumer_step["with"]["gcc-options"], inputs=hop3_inputs
    )
    final_ast_frontend = eval_gha_expression(
        consumer_step["with"]["ast-frontend"], inputs=hop3_inputs
    )
    assert final_gcc_path == _SENTINEL_CONSUMER_GCC_PATH
    assert final_gcc_options == "-std=gnu++20"
    assert final_ast_frontend == _WORKFLOW_GLOBAL_AST_FRONTEND

    _assert_reaches_real_dump_cli_invocation(
        final_gcc_path, final_gcc_options, final_ast_frontend
    )


def test_no_baseline_channel_activates_the_consumer_step_via_its_own_arm() -> None:
    """Codex review (PR #906): every scenario above supplies `baseline-
    channel: release` alongside `steps.resolve.outputs.outcome: 'resolved'`
    in its `steps_context`, so none of them requires the consumer-context
    step's own guard's `inputs.baseline-channel == 'none'` arm at all --
    the guard's `(inputs.baseline-channel == 'none' || steps.resolve.
    outputs.outcome == 'resolved')` clause is always satisfied through its
    SECOND disjunct in every prior test, so deleting or inverting the first
    would still leave every other test in this file green while a real
    `channel: none` check (no baseline needed -- `actions/check-target/
    action.yml`'s own "Resolve baseline" step's `if: inputs.baseline-
    channel != 'none'` means it never runs and never sets that output)
    silently loses its consumer-context dump in production.

    Exercises that arm directly: a `channel: "none"` target check, and a
    `steps` context with NO `resolve.outputs.outcome` key at all -- mirrors
    the real workflow, where that step never running means the property is
    genuinely undefined (evaluates to `None`, not the string `'resolved'`),
    so this scenario's guard must carry itself via the first disjunct
    alone."""
    raw = {
        "targets": {
            "libfoo": {
                "kind": "library",
                "binary_pattern": "build/libfoo*.so",
                "checks": [
                    {"channel": "none", "depth": "headers", "required": True},
                ],
            },
        },
        "profiles": {
            "no-baseline": {
                "contract": True,
                "consumer_compile": {"frontend": _SENTINEL_CONSUMER_AST_FRONTEND},
            },
        },
        "baseline": TestConsumerCompileOverlayProjection._RAW["baseline"],
    }
    config = _parsed(raw)
    plan, report = generate_run_plan(config, {"no-baseline": _bo("libfoo")})
    assert report.ok
    [check] = plan.checks
    matrix = check.to_dict()
    assert matrix["baseline_channel"] == "none"
    assert matrix["consumer_compile_active"] is True

    run_step = _run_check_step("Run check-target")
    _assert_run_check_target_step_guard_fires(run_step)
    workflow_inputs = {"gcc-path": "", "gcc-options": "", "ast-frontend": ""}
    consumer_gcc_path = eval_gha_expression(
        run_step["with"]["consumer-gcc-path"], matrix=matrix, inputs=workflow_inputs
    )
    consumer_gcc_options = eval_gha_expression(
        run_step["with"]["consumer-gcc-options"], matrix=matrix, inputs=workflow_inputs
    )
    consumer_ast_frontend = eval_gha_expression(
        run_step["with"]["consumer-ast-frontend"], matrix=matrix, inputs=workflow_inputs
    )
    consumer_compile_active = eval_gha_expression(
        run_step["with"]["consumer-compile-active"], matrix=matrix, inputs={}
    )
    kind = eval_gha_expression(run_step["with"]["kind"], matrix=matrix)
    baseline_channel = eval_gha_expression(
        run_step["with"]["baseline-channel"], matrix=matrix
    )
    assert baseline_channel == "none"
    assert consumer_ast_frontend == _SENTINEL_CONSUMER_AST_FRONTEND

    consumer_step = _consumer_context_step()
    hop3_inputs = _hop3_inputs_from(
        kind=kind,
        baseline_channel=baseline_channel,
        consumer_compile_active=consumer_compile_active,
        consumer_ast_frontend=consumer_ast_frontend,
        consumer_gcc_path=consumer_gcc_path,
        consumer_gcc_options=consumer_gcc_options,
    )
    # Deliberately no "resolve.outputs.outcome" key at all -- the real
    # "Resolve baseline" step never runs for a channel: none check, so this
    # property is genuinely undefined, not the string 'resolved'.
    steps_context = {
        "collect_verify.outcome": "success",
        "collect_replay.outcome": "success",
    }
    guard = eval_gha_expression(
        consumer_step["if"], inputs=hop3_inputs, steps=steps_context
    )
    assert guard is True, (
        "a channel: none check (no baseline) must still activate the "
        "consumer-context step's own scheduling guard via its "
        "`inputs.baseline-channel == 'none'` arm alone, with no "
        "`steps.resolve.outputs.outcome` present at all"
    )

    final_gcc_path = eval_gha_expression(
        consumer_step["with"]["gcc-path"], inputs=hop3_inputs
    )
    final_gcc_options = eval_gha_expression(
        consumer_step["with"]["gcc-options"], inputs=hop3_inputs
    )
    final_ast_frontend = eval_gha_expression(
        consumer_step["with"]["ast-frontend"], inputs=hop3_inputs
    )
    # Not carried through hop 4 (`_assert_reaches_real_dump_cli_invocation`
    # unconditionally asserts `--compiler`/`--compiler-option` are present,
    # which `add_single_flag`/`add_flag_shlex_split` in run.sh correctly
    # omit for an empty value -- the marker-only test above has the
    # identical shape and is likewise not carried through hop 4). This
    # test's own subject -- the guard's `baseline-channel == 'none'` arm --
    # is already fully exercised by the `guard is True` assertion.
    assert final_gcc_path == ""
    assert final_gcc_options == ""
    assert final_ast_frontend == _SENTINEL_CONSUMER_AST_FRONTEND


def _assert_run_abicheck_step_invokes_run_sh() -> None:
    """Root action.yml's own "Run abicheck" step's real `run:` command must
    still invoke `action/run.sh` -- the same file `test_action_compile_
    context_parity.py`'s `RUN_SH` constant (and therefore `_run_region`,
    which hop 4 calls below) hardcodes. Without this check, repointing that
    step's `run:` to a different script would disconnect production from
    the compiler flags this test verifies while `_run_region` kept
    executing the stale `action/run.sh` and passing regardless (Codex
    review, PR #906)."""
    action = _load(ACTION_YML)
    step = next(
        step for step in _steps(action["runs"]) if step.get("name") == "Run abicheck"
    )
    run_cmd = step.get("run", "")
    # The exact real invocation, not a substring check (Codex review,
    # PR #906) -- a substring match would also accept e.g.
    # `bash .../action/run.sh.backup` or a comment merely mentioning
    # `action/run.sh`, either of which means production no longer invokes
    # the file this test executes directly via `RUN_SH`.
    assert run_cmd == 'bash "${{ github.action_path }}/action/run.sh"', (
        f"root action.yml's 'Run abicheck' step's `run:` command no "
        f"longer exactly matches the expected invocation of action/run.sh "
        f"(got {run_cmd!r}) -- hop 4 below executes that file directly via "
        f"test_action_compile_context_parity.py's RUN_SH constant and "
        f"would silently keep testing a script production no longer runs"
    )


def _assert_reaches_real_dump_cli_invocation(
    gcc_path: str, gcc_options: str, ast_frontend: str
) -> None:
    """Hop 4: the values resolved at the end of hops 1-3, mapped to their REAL
    INPUT_* env var names via root action.yml's own "Run abicheck" step's
    OWN, isolated `env:` block (`_step_env_mapping("Run abicheck")`, from
    test_action_run_contract.py -- never a hardcoded `INPUT_GCC_PATH`/
    `INPUT_AST_FRONTEND` guess, so a swap of that mapping would be caught
    here). Deliberately the step-scoped mapping, not the whole-file
    `_action_yml_env_mapping()`: action.yml's earlier "Validate mode/input
    combination" step duplicates some of these same INPUT_* names in its
    OWN, differently-isolated env block (see `_step_env_mapping`'s own
    docstring), and scanning the whole file would let that duplicate mask
    a real removal from the "Run abicheck" step specifically -- the one
    step this hop actually executes (Codex review, PR #906). Fed into
    run.sh's own real dump-mode region (the existing
    test_action_compile_context_parity.py harness -- no new execution
    machinery, but its own `RUN_SH` edge is asserted first via
    `_assert_run_abicheck_step_invokes_run_sh`), producing the real
    --ast-frontend/--compiler/--compiler-option CLI flags."""
    _assert_run_abicheck_step_invokes_run_sh()
    env_by_input = {inp: var for var, inp in _step_env_mapping("Run abicheck").items()}
    for name in ("gcc-path", "gcc-options", "ast-frontend", "gcc-prefix", "sysroot"):
        assert name in env_by_input, (
            f"root action.yml's env block no longer maps input {name!r} to "
            f"an INPUT_* var -- test_action_run_contract.py's own contract "
            f"tests should already have failed"
        )
    env = {
        env_by_input["ast-frontend"]: ast_frontend,
        env_by_input["gcc-path"]: gcc_path,
        env_by_input["gcc-prefix"]: "",
        env_by_input["gcc-options"]: gcc_options,
        env_by_input["sysroot"]: "",
        "INPUT_NOSTDINC": "false",
    }
    cmd, _ = _run_region(_DUMP_MODE_MARKER, env)
    assert "--ast-frontend" in cmd
    assert cmd[cmd.index("--ast-frontend") + 1] == ast_frontend
    assert "--compiler" in cmd
    assert cmd[cmd.index("--compiler") + 1] == gcc_path
    assert "--compiler-option" in cmd
    options = [cmd[i + 1] for i, tok in enumerate(cmd) if tok == "--compiler-option"]
    assert options == gcc_options.split()


def test_bundle_kind_and_no_overlay_never_leak_the_workflow_global_path() -> None:
    """A companion to the first test's "distinct sentinel" trick, checked
    directly against the real hop-2 expressions -- gcc-path, gcc-options
    (since a further Codex review round), and ast-frontend -- for the two
    cases that must resolve empty regardless of what the workflow-global
    input carries: a bundle-kind cell (never gets consumer fields at all),
    and a non-overlay profile (`consumer_compile_active` false).
    `consumer-gcc-options` was previously left unchecked here even though
    its own expression has the identical `matrix.consumer_compile_active
    && ... || inputs.gcc-options || ''` shape -- a regression there could
    have let an inactive/bundle cell's workflow-global option leak through
    while every other assertion in this test stayed green.

    A further Codex review round found this test also never evaluated
    `consumer-compile-active` itself for either inactive case -- only the
    three compiler-field expressions. Changing that expression's own
    `matrix.kind != 'bundle' && matrix.consumer_compile_active` gate to
    `||` would preserve every substring this test (and the mutation-check
    test) assert against while forwarding `"true"` for a bundle/no-overlay
    cell, activating the child step's separate consumer dump with an
    empty/default compiler context -- exactly the failure mode this test
    exists to rule out, just one hop earlier than the three fields it was
    already checking. The same review round also asked that the evaluated
    marker be carried through the child step's own scheduling guard (hop
    3) for these inactive cases, confirming the step actually stays
    disabled end to end, not just that hop 2's own marker resolves
    falsy."""
    run_step = _run_check_step("Run check-target")
    _assert_run_check_target_step_guard_fires(run_step)
    gcc_path_expr = run_step["with"]["consumer-gcc-path"]
    gcc_options_expr = run_step["with"]["consumer-gcc-options"]
    ast_frontend_expr = run_step["with"]["consumer-ast-frontend"]
    active_expr = run_step["with"]["consumer-compile-active"]
    kind_expr = run_step["with"]["kind"]
    baseline_channel_expr = run_step["with"]["baseline-channel"]
    consumer_step = _consumer_context_step()
    steps_context = {
        "resolve.outputs.outcome": "resolved",
        "collect_verify.outcome": "success",
        "collect_replay.outcome": "success",
    }

    bundle_matrix = {
        "kind": "bundle",
        "consumer_compile_active": True,
        "consumer_compile_gcc_path": _SENTINEL_CONSUMER_GCC_PATH,
        "consumer_compile_gcc_options": _FALLBACK_GCC_OPTIONS,
        "consumer_compile_ast_frontend": _SENTINEL_CONSUMER_AST_FRONTEND,
    }
    bundle_inputs = {
        "gcc-path": _WORKFLOW_GLOBAL_GCC_PATH,
        "gcc-options": _WORKFLOW_GLOBAL_GCC_OPTIONS,
        "ast-frontend": _WORKFLOW_GLOBAL_AST_FRONTEND,
    }
    assert (
        eval_gha_expression(gcc_path_expr, matrix=bundle_matrix, inputs=bundle_inputs)
        == ""
    )
    assert (
        eval_gha_expression(
            gcc_options_expr, matrix=bundle_matrix, inputs=bundle_inputs
        )
        == ""
    )
    assert (
        eval_gha_expression(
            ast_frontend_expr, matrix=bundle_matrix, inputs=bundle_inputs
        )
        == ""
    )
    bundle_active = eval_gha_expression(active_expr, matrix=bundle_matrix, inputs={})
    assert bundle_active == "false", (
        "a bundle-kind cell must never report consumer-compile-active as "
        "true, regardless of what the (unused for bundles) consumer_"
        "compile_active matrix field says"
    )
    bundle_hop3_inputs = _hop3_inputs_from(
        kind=eval_gha_expression(kind_expr, matrix=bundle_matrix),
        baseline_channel=eval_gha_expression(
            baseline_channel_expr, matrix=bundle_matrix
        ),
        consumer_compile_active=bundle_active,
        consumer_ast_frontend="",
        consumer_gcc_path="",
        consumer_gcc_options="",
    )
    assert not eval_gha_expression(
        consumer_step["if"], inputs=bundle_hop3_inputs, steps=steps_context
    ), "a bundle-kind cell must leave the consumer-context step disabled end to end"

    no_overlay_matrix = {"kind": "target", "consumer_compile_active": False}
    no_overlay_inputs = {
        "gcc-path": _WORKFLOW_GLOBAL_GCC_PATH,
        "gcc-options": _WORKFLOW_GLOBAL_GCC_OPTIONS,
        "ast-frontend": _WORKFLOW_GLOBAL_AST_FRONTEND,
    }
    assert (
        eval_gha_expression(
            gcc_path_expr, matrix=no_overlay_matrix, inputs=no_overlay_inputs
        )
        == ""
    )
    assert (
        eval_gha_expression(
            gcc_options_expr, matrix=no_overlay_matrix, inputs=no_overlay_inputs
        )
        == ""
    )
    assert (
        eval_gha_expression(
            ast_frontend_expr, matrix=no_overlay_matrix, inputs=no_overlay_inputs
        )
        == ""
    )
    no_overlay_active = eval_gha_expression(
        active_expr, matrix=no_overlay_matrix, inputs={}
    )
    assert no_overlay_active == "false", (
        "a profile with no consumer_compile overlay must never report "
        "consumer-compile-active as true"
    )
    no_overlay_hop3_inputs = _hop3_inputs_from(
        kind=eval_gha_expression(kind_expr, matrix=no_overlay_matrix),
        baseline_channel=eval_gha_expression(
            baseline_channel_expr, matrix=no_overlay_matrix
        ),
        consumer_compile_active=no_overlay_active,
        consumer_ast_frontend="",
        consumer_gcc_path="",
        consumer_gcc_options="",
    )
    assert not eval_gha_expression(
        consumer_step["if"], inputs=no_overlay_hop3_inputs, steps=steps_context
    ), (
        "a profile with no consumer_compile overlay must leave the "
        "consumer-context step disabled end to end"
    )


def test_mutating_the_real_hop2_gate_leaks_the_workflow_global_path() -> None:
    """Phase 6's own mutation-check requirement (bug-class-regression-
    testing.md: "deliberately removing one forwarding edge... must make
    the suite fail and name the missing path"), applied to the REAL
    evaluator-based chain this file builds -- not just the substring-
    matching `TestConsumerCompilePropagationChainMutationCheck` class in
    test_reusable_workflows_project_evidence.py (Codex review, PR #906):
    that class proves only that `_assert_step_input_forwards`'s own exact-
    string comparison rejects a hand-modified `with:` dict -- a helper this
    file's own tests never call at all. It never showed that
    `eval_gha_expression` itself -- the primitive every test in this file
    is built on -- produces a different, WRONG answer when a real hop-2
    forwarding expression is mutated the way #860/#883's own root causes
    actually broke (dropping/inverting a gate).

    This constructs the identical single-character class of mutation
    (`&&` -> `||` in the activation gate) against the REAL, unmodified
    `consumer-gcc-path` expression text pulled live from check-project.yml
    -- the same text every other test in this file evaluates -- and proves
    the mutated text evaluates DIFFERENTLY from the real text for an
    inactive, no-overlay cell paired with a nonempty workflow-global path:
    the real expression correctly resolves empty (matching this file's own
    `test_bundle_kind_and_no_overlay_never_leak_the_workflow_global_path`),
    while the mutated one leaks the workflow-global value. This is the
    proof the harness would have caught #860/#883 before merge -- not
    merely that a hand-written comparison helper works in isolation."""
    run_step = _run_check_step("Run check-target")
    real_expr = run_step["with"]["consumer-gcc-path"]
    gate = "matrix.kind != 'bundle' && matrix.consumer_compile_active"
    assert gate in real_expr, (
        "the real consumer-gcc-path expression no longer contains the "
        "exact activation gate this mutation targets -- update the "
        "mutation to match the real expression's current text"
    )
    mutated_expr = real_expr.replace(
        gate, "matrix.kind != 'bundle' || matrix.consumer_compile_active", 1
    )
    assert mutated_expr != real_expr

    # An inactive, no-overlay target cell -- the exact scenario
    # test_bundle_kind_and_no_overlay_never_leak_the_workflow_global_path
    # already pins the REAL expression's correct ("") answer for.
    matrix = {"kind": "target", "consumer_compile_active": False}
    inputs = {"gcc-path": _WORKFLOW_GLOBAL_GCC_PATH}

    real_result = eval_gha_expression(real_expr, matrix=matrix, inputs=inputs)
    mutated_result = eval_gha_expression(mutated_expr, matrix=matrix, inputs=inputs)

    assert real_result == "", (
        "sanity check: the real, unmodified expression must still resolve "
        "empty for this inactive, no-overlay cell"
    )
    assert mutated_result == _WORKFLOW_GLOBAL_GCC_PATH, (
        "the deliberately mutated expression must leak the workflow-global "
        "path for this same inactive cell -- if it doesn't, eval_gha_"
        "expression (and therefore every full-chain test built on it) "
        "would NOT actually have caught this class of regression, "
        "contradicting the mutation-check claim this test exists to prove"
    )


def test_collect_facts_failure_independently_disables_the_consumer_step() -> None:
    """Codex review (PR #906): every `steps_context` used elsewhere in this
    file sets `collect_verify.outcome` and `collect_replay.outcome` to the
    identical value ("success") -- but `actions/check-target/action.yml`'s
    own "Collect source facts (verify)"/"(replay)" steps have mutually
    exclusive `if:` conditions (gated on `evidence-producer` being
    wrapper/clang-plugin vs. replay), so in a real run at most one of them
    ever actually executes; the other reads "skipped". Because every
    scenario elsewhere in this file supplies the same value for both keys,
    a mutation collapsing `steps.collect_replay.outcome != 'failure'` into
    a second check of `steps.collect_verify.outcome` would have left every
    other test in this file green, while a real replay-collection failure
    alongside a skipped verify step would incorrectly still let the
    consumer dump run.

    Proves each `!= 'failure'` clause is independently load-bearing: a
    genuine failure on either collection step disables the guard even
    when the OTHER, mutually-exclusive step was merely skipped (never a
    forged "success") -- and that a realistic skipped/succeeded pairing
    still activates it, so the failing cases aren't just an always-false
    guard."""
    consumer_step = _consumer_context_step()
    hop3_inputs = _hop3_inputs_from(
        kind="target",
        baseline_channel="none",
        consumer_compile_active="true",
        consumer_ast_frontend=_SENTINEL_CONSUMER_AST_FRONTEND,
        consumer_gcc_path=_SENTINEL_CONSUMER_GCC_PATH,
        consumer_gcc_options="",
    )

    # A real "evidence-producer: replay" run: verify never ran (skipped),
    # replay genuinely failed. The guard must still refuse to run.
    assert not eval_gha_expression(
        consumer_step["if"],
        inputs=hop3_inputs,
        steps={
            "collect_verify.outcome": "skipped",
            "collect_replay.outcome": "failure",
        },
    ), (
        "a failed replay collection must disable the consumer step even "
        "when verify was merely skipped, not run as a forged success"
    )

    # A real "evidence-producer: wrapper" run: replay never ran (skipped),
    # verify genuinely failed. The guard must still refuse to run.
    assert not eval_gha_expression(
        consumer_step["if"],
        inputs=hop3_inputs,
        steps={
            "collect_verify.outcome": "failure",
            "collect_replay.outcome": "skipped",
        },
    ), (
        "a failed verify collection must disable the consumer step even "
        "when replay was merely skipped, not run as a forged success"
    )

    # Sanity: the realistic, mutually-exclusive success pairing (one
    # skipped, one succeeded) still activates it -- confirming the two
    # failing cases above aren't just an always-false guard.
    assert eval_gha_expression(
        consumer_step["if"],
        inputs=hop3_inputs,
        steps={
            "collect_verify.outcome": "skipped",
            "collect_replay.outcome": "success",
        },
    ), (
        "a realistic skipped-verify/succeeded-replay pairing must still "
        "activate the consumer step"
    )
