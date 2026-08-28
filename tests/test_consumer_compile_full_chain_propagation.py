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
         "Run abicheck" step's own env block (test_action_run_contract.py's
         `_action_yml_env_mapping`) -- never a hardcoded `INPUT_GCC_PATH`
         guess
      -> root action.yml's run.sh [real bash execution, via the existing
         test_action_compile_context_parity.py harness]

with one sentinel binding path/options string surviving, unaltered, end
to end.

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
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from _gha_expr import eval_gha_expression
from test_action_compile_context_parity import _DUMP_MODE_MARKER, _run_region
from test_action_run_contract import _action_yml_env_mapping
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

# The `frontend` field of the same `consumer_compile` concern, threaded
# through the identical hops via its own sentinel/fallback pair -- must be
# one of the real, validated `ast-frontend` values (auto/castxml/clang/
# hybrid; action/run.sh's own validate-inputs guard), so "clang" and
# "hybrid" stand in for the sentinel/workflow-global roles respectively.
_SENTINEL_CONSUMER_AST_FRONTEND = "clang"
_WORKFLOW_GLOBAL_AST_FRONTEND = "hybrid"


def _run_check_step(name: str) -> dict[str, Any]:
    project = _load(CHECK_PROJECT)
    return next(
        step for step in _steps(project["jobs"]["check"]) if step.get("name") == name
    )


def _consumer_context_step() -> dict[str, Any]:
    target = _load(CHECK_TARGET)
    return next(
        step
        for step in _steps(target["runs"])
        if step.get("name") == "Extract candidate consumer context"
    )


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


def _assert_reaches_real_dump_cli_invocation(
    gcc_path: str, gcc_options: str, ast_frontend: str
) -> None:
    """Hop 4: the values resolved at the end of hops 1-3, mapped to their REAL
    INPUT_* env var names via root action.yml's own "Run abicheck" step env
    block (`_action_yml_env_mapping`, from test_action_run_contract.py --
    never a hardcoded `INPUT_GCC_PATH`/`INPUT_AST_FRONTEND` guess, so a swap
    of that env block's own mappings would be caught here), fed into
    run.sh's own real dump-mode region (the existing
    test_action_compile_context_parity.py harness -- no new execution
    machinery), producing the real --ast-frontend/--compiler/
    --compiler-option CLI flags."""
    env_by_input = {inp: var for var, inp in _action_yml_env_mapping().items()}
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
    directly against the real hop-2 expressions -- gcc-path and, since the
    third Codex review round, ast-frontend too -- for the two cases that
    must resolve empty regardless of what the workflow-global input
    carries: a bundle-kind cell (never gets consumer fields at all), and a
    non-overlay profile (`consumer_compile_active` false)."""
    run_step = _run_check_step("Run check-target")
    gcc_path_expr = run_step["with"]["consumer-gcc-path"]
    ast_frontend_expr = run_step["with"]["consumer-ast-frontend"]

    bundle_matrix = {
        "kind": "bundle",
        "consumer_compile_active": True,
        "consumer_compile_gcc_path": _SENTINEL_CONSUMER_GCC_PATH,
        "consumer_compile_ast_frontend": _SENTINEL_CONSUMER_AST_FRONTEND,
    }
    bundle_inputs = {
        "gcc-path": _WORKFLOW_GLOBAL_GCC_PATH,
        "ast-frontend": _WORKFLOW_GLOBAL_AST_FRONTEND,
    }
    assert (
        eval_gha_expression(gcc_path_expr, matrix=bundle_matrix, inputs=bundle_inputs)
        == ""
    )
    assert (
        eval_gha_expression(
            ast_frontend_expr, matrix=bundle_matrix, inputs=bundle_inputs
        )
        == ""
    )

    no_overlay_matrix = {"kind": "target", "consumer_compile_active": False}
    no_overlay_inputs = {
        "gcc-path": _WORKFLOW_GLOBAL_GCC_PATH,
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
            ast_frontend_expr, matrix=no_overlay_matrix, inputs=no_overlay_inputs
        )
        == ""
    )
