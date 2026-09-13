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

"""The fail-closed contract behind every RETIRED input still declared in
``action.yml``.

A retired input is deliberately kept *declared* rather than deleted: a
composite action silently ignores an input it does not declare, so deleting
the declaration would turn "this workflow asks for something that no longer
exists" into "this workflow quietly runs a narrower check than it asked
for" -- a misleadingly successful run, which is strictly worse than the
loud failure the tombstone produces. Every one of these inputs therefore
owes the same guarantee: **setting it fails the run, before any analysis**.

That guarantee had no executable, exhaustive owner. The pre-existing
coverage was per-input tests naming three of the eight, and
``test_action_run_contract.py``'s ordering check asserts the *text* of
``run.sh`` rather than running it -- the exact shape AGENTS.md names as a
repeat escape in this repository (#705 -> #758: a workflow-injection defense
that asserted file text instead of executing the attack). A tombstone
deleted, or a new one added with no rejection, would have failed nothing.

So this module derives the retired set from ``action.yml`` itself -- the
single place a tombstone is declared -- and *executes* both gates against
every member. The oracle is the declaration, not a hand-maintained list
here, so the set cannot drift out of step with the product: adding a
RETIRED input with no rejection fails, and so does removing a rejection for
one still declared.
"""

from __future__ import annotations

import itertools
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from _workflow_exec import bash_executable, require_bash

# The one registry saying which declared inputs the "Run abicheck" step
# deliberately never forwards. Imported rather than restated: a second copy
# is exactly the drift this module exists to catch, and that file's own
# `test_every_action_yml_input_is_wired_to_run_sh` already keeps it honest
# in the other direction.
from test_action_run_contract import _NON_RUN_SH_INPUTS

REPO_ROOT = Path(__file__).resolve().parents[1]
ACTION_YML = REPO_ROOT / "action.yml"
ACTION_DIR = REPO_ROOT / "action"
VALIDATE_SH = ACTION_DIR / "validate-inputs.sh"
RUN_SH = ACTION_DIR / "run.sh"

pytestmark = pytest.mark.skipif(
    os.name == "nt" or not ACTION_YML.is_file() or not VALIDATE_SH.is_file(),
    reason="needs action.yml + action/validate-inputs.sh and a POSIX shell",
)


def _input_env_var(name: str) -> str:
    """``new-library-set`` -> ``INPUT_NEW_LIBRARY_SET``, GitHub's own rule."""
    return "INPUT_" + name.upper().replace("-", "_")


def _retired_inputs() -> dict[str, dict]:
    """Every ``action.yml`` input whose description declares it RETIRED.

    Deliberately read from the shipped manifest rather than restated here:
    a list in this file would be one more hand-maintained copy to drift, and
    the drift is precisely what this module exists to catch.
    """
    document = yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))
    return {
        name: spec
        for name, spec in (document.get("inputs") or {}).items()
        if "RETIRED" in (spec.get("description") or "")
    }


RETIRED_INPUTS = _retired_inputs()


#: Tombstones that must stay declared. Every test below is parametrized over
#: whatever ``action.yml`` currently marks RETIRED, which makes *deletion* the
#: one change they cannot catch on their own -- delete an input and it simply
#: stops being covered, silently, which is the removal this whole contract
#: exists to gate.
#:
#: So the names are frozen here as well. This is a deliberate hand-maintained
#: registry, the same shape as ``_NON_RUN_SH_INPUTS`` above and
#: ``IMPORT_CYCLE_ALLOWLIST`` in ``scripts/check_ai_readiness.py``: dropping an
#: entry means editing this list and saying why in the PR, rather than a
#: one-line deletion in ``action.yml`` that no test notices. Removing a
#: tombstone is safe only once no supported workflow can still set it -- which
#: is a judgement about the released Action's users, not something the tree can
#: check.
#:
#: Adding a tombstone needs no edit here (the derived set covers it
#: immediately); the completeness direction is asserted below.
_TOMBSTONES_THAT_MUST_STAY_DECLARED = frozenset(
    {
        "against",
        "audit",
        "build-target",
        "bundle-system-providers",
        "crosscheck",
        "estimate",
        "new-library-set",
        "require-complete-analysis",
        "risk-rules",
    }
)


def test_no_tombstone_has_been_silently_undeclared() -> None:
    """A retired input's declaration is load-bearing: GitHub drops an
    *undeclared* input before the composite action runs, so the workflow
    that still sets it sees no annotation and its setting silently stops
    applying -- a narrower analysis reported as a clean one. Deleting the
    declaration is therefore a product decision, not a tidy-up.
    """
    missing = sorted(_TOMBSTONES_THAT_MUST_STAY_DECLARED - set(RETIRED_INPUTS))
    assert not missing, (
        f"action.yml no longer declares these as RETIRED: {missing}. A "
        f"workflow that still sets one now gets no error and a silently "
        f"narrower run. If the removal is intended, drop the name from "
        f"_TOMBSTONES_THAT_MUST_STAY_DECLARED here and say in the PR why no "
        f"supported workflow can still set it."
    )


def test_the_frozen_tombstone_list_has_not_gone_stale() -> None:
    """The other direction: a newly retired input is covered by the derived
    set the moment it is marked, but the frozen list above should name it
    too, or the next deletion of *that* one goes unnoticed.
    """
    unlisted = sorted(set(RETIRED_INPUTS) - _TOMBSTONES_THAT_MUST_STAY_DECLARED)
    assert not unlisted, (
        f"action.yml marks {unlisted} RETIRED but they are absent from "
        f"_TOMBSTONES_THAT_MUST_STAY_DECLARED, so deleting one later would "
        f"fail nothing. Add them."
    )


#: Spellings a workflow can plausibly give a boolean-shaped input. Composite
#: inputs are untyped strings, so `audit: yes` and `estimate: 1` are as much
#: an explicit request for the retired behaviour as `true` is -- and guards
#: matching only the literal "true" let every other spelling through into a
#: silently narrower run, which this suite missed by defining the one
#: meaningful probe as "true" (Codex review).
_BOOLEAN_TRUTHY_SPELLINGS = ("true", "yes", "1", "TRUE", "on")


def _is_boolean_shaped(spec: dict) -> bool:
    """A `default: "false"` input is boolean-shaped: it is "set" at any
    value other than that default, not only at `true`."""
    return str(spec.get("default", "")).lower() == "false"


def _meaningful_values(spec: dict) -> tuple[str, ...]:
    """Every value that counts as *setting* this input.

    A boolean-shaped input is probed with several independently chosen
    truthy spellings rather than one, so a guard that happens to match the
    first cannot stand in for the contract. Everything else takes a plain
    non-empty string.
    """
    if _is_boolean_shaped(spec):
        return _BOOLEAN_TRUTHY_SPELLINGS
    return ("probe-value",)


def _meaningful_value(spec: dict) -> str:
    """One representative value, for the checks that need only a single
    invocation (`run.sh`'s own copy of each guard)."""
    return _meaningful_values(spec)[0]


def _run_script(script: Path, env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    """Run one of the Action's shell scripts with a clean `INPUT_*` slate.

    Every `INPUT_*` the test process itself inherited is stripped, so an
    ambient value cannot stand in for the one under test nor mask a missing
    rejection by tripping an unrelated guard first.
    """
    require_bash()
    env = {
        k: v
        for k, v in os.environ.items()
        # Strip every INPUT_* the *test process* may have inherited, so an
        # ambient value cannot stand in for the one under test (nor mask a
        # missing rejection by tripping some unrelated guard first).
        if not k.startswith("INPUT_")
    }
    env.update(env_extra)
    return subprocess.run(
        [bash_executable(), str(script)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        cwd=REPO_ROOT,
    )


def test_action_yml_still_declares_retired_inputs() -> None:
    """Vacuity guard: the parametrized tests below iterate the retired set,
    so an empty set would make every one of them pass while asserting
    nothing at all -- the failure mode AGENTS.md's matrix-test rule names.
    """
    assert RETIRED_INPUTS, (
        "no action.yml input declares itself RETIRED -- either the tombstone "
        "convention changed (update this module's parser) or every tombstone "
        "was deleted, which is the removal this contract exists to gate."
    )


@pytest.mark.parametrize("name", sorted(RETIRED_INPUTS))
def test_setting_a_retired_input_fails_preflight_validation(name: str) -> None:
    """`validate-inputs.sh` is the first gate a real run hits -- before
    Python setup and the toolchain install -- so this is where a retired
    input must be refused. Executed, not text-matched.
    """
    for value in _meaningful_values(RETIRED_INPUTS[name]):
        result = _run_script(
            VALIDATE_SH,
            {
                "INPUT_MODE": "compare",
                "INPUT_NEW_LIBRARY": "libfoo.so",
                _input_env_var(name): value,
            },
        )
        combined = result.stdout + result.stderr
        assert result.returncode != 0, (
            f"action.yml declares {name!r} RETIRED, but setting it to "
            f"{value!r} passes validate-inputs.sh -- a workflow that still "
            f"sets it would run a narrower analysis than it asked for, with "
            f"no signal.\n{combined}"
        )
        assert "::error::" in combined, (
            f"{name}={value!r} fails the run but emits no ::error:: "
            f"annotation, so the reason is invisible in the GitHub Actions "
            f"UI.\n{combined}"
        )
        assert name in combined, (
            f"{name}={value!r}'s rejection message never names the input, so "
            f"a workflow author cannot tell which input to remove.\n"
            f"{combined}"
        )


@pytest.mark.parametrize("name", sorted(RETIRED_INPUTS))
def test_the_rejection_does_not_depend_on_the_mode(name: str) -> None:
    """Retirement is a property of the input, not of which branch happens
    to run: none of these inputs can do anything on any mode any more. A
    rejection reachable only from one mode's arm would let the same
    workflow pass by changing an unrelated field.

    This is the executable form of the ordering claim
    ``test_action_run_contract.py`` makes against ``run.sh``'s text -- here
    every real mode is actually run.
    """
    for mode in ("compare", "dump", "deps-tree", "deps-compare"):
        result = _run_script(
            VALIDATE_SH,
            {
                "INPUT_MODE": mode,
                "INPUT_NEW_LIBRARY": "libfoo.so",
                _input_env_var(name): _meaningful_value(RETIRED_INPUTS[name]),
            },
        )
        assert result.returncode != 0, (
            f"{name} is rejected on some modes but accepted on {mode!r} -- a "
            f"workflow keeps the retired input and silently gets a narrower "
            f"run.\n{result.stdout + result.stderr}"
        )


def test_the_composite_runs_preflight_validation_before_run_sh() -> None:
    """What makes a preflight-only rejection sufficient.

    Two tombstones (`jobs`, `bundle-system-providers`) are deliberately
    *not* forwarded to `run.sh` at all -- there is nothing left to forward
    them to -- so `validate-inputs.sh` is their only consumer. That is safe
    precisely because the composite always runs the validator first; if the
    two steps were ever reordered, or the validator step dropped, those
    inputs would go from "hard error" to "silently ignored" with no test
    noticing. So the ordering is asserted here, next to the guarantee that
    depends on it.
    """
    text = ACTION_YML.read_text(encoding="utf-8")
    validate_at = text.find('/action/validate-inputs.sh"')
    run_at = text.find('/action/run.sh"')
    assert validate_at != -1, "action.yml no longer runs action/validate-inputs.sh"
    assert run_at != -1, "action.yml no longer runs action/run.sh"
    assert validate_at < run_at, (
        "action.yml runs action/run.sh before action/validate-inputs.sh, so a "
        "retired input rejected only in preflight is now reachable: the "
        "analysis runs first and the input is silently ignored."
    )


@pytest.mark.parametrize("name", sorted(set(RETIRED_INPUTS) - set(_NON_RUN_SH_INPUTS)))
def test_run_sh_refuses_the_same_input_independently(name: str) -> None:
    """For every retired input `run.sh` *does* receive, it must refuse it
    itself rather than lean on the preflight step -- defense in depth, since
    the two scripts are separate entry points.

    Swept over every mode *and* every meaningful value, for the same
    reasons the preflight check is -- and the value axis specifically
    because widening only the preflight sweep left this entry point
    covered by a single `"true"` probe, so reverting `run.sh`'s own
    `audit`/`estimate` predicate to an exact-`"true"` match kept all 43
    cases green while a direct `audit=yes` run proceeded silently (Codex
    review). Defense in depth is not depth if only one layer is tested for
    the whole input class.

    A rejection sitting inside one mode's own branch is not this contract:
    `require-complete-analysis` was guarded only in the compare arm, so a
    direct `run.sh` run with `mode: dump` and the input set went on to
    analyse -- while a compare-only version of this test passed (Codex
    review). Retirement is a property of the input, not of which branch
    happens to run.

    The exemptions come from `_NON_RUN_SH_INPUTS`, the existing registry of
    inputs the "Run abicheck" step never forwards; for those, preflight is
    the whole contract and the ordering test above is what backs it.
    """
    for mode, value in itertools.product(
        ("compare", "dump", "deps-tree", "deps-compare"),
        _meaningful_values(RETIRED_INPUTS[name]),
    ):
        result = _run_script(
            RUN_SH,
            {
                "INPUT_MODE": mode,
                "INPUT_NEW_LIBRARY": "libfoo.so",
                _input_env_var(name): value,
                # run.sh would otherwise try to do real work; every
                # retired-input rejection precedes the mode dispatch, so it
                # never gets there.
                "GITHUB_STEP_SUMMARY": os.devnull,
                "GITHUB_OUTPUT": os.devnull,
            },
        )
        combined = result.stdout + result.stderr
        assert result.returncode != 0, (
            f"run.sh accepts retired input {name!r}={value!r} on mode "
            f"{mode!r}; only the preflight step rejects it, so an entry "
            f"point that skips preflight runs a narrower analysis "
            f"silently.\n{combined}"
        )
        assert name in combined, (
            f"run.sh exits non-zero for {name!r}={value!r} on mode {mode!r} "
            f"but never names it -- that is some other error, not this "
            f"input's rejection.\n{combined}"
        )


@pytest.mark.parametrize(
    "name", sorted(n for n, spec in RETIRED_INPUTS.items() if _is_boolean_shaped(spec))
)
def test_a_boolean_tombstone_at_its_documented_default_is_inert(name: str) -> None:
    """The other side of the truthy sweep: `audit: false` is not a request
    for the retired behaviour, it is the documented default written out, and
    a workflow that spells it must keep working. Without this, a guard
    rejecting the input unconditionally would satisfy every assertion above.
    """
    result = _run_script(
        VALIDATE_SH,
        {
            "INPUT_MODE": "compare",
            "INPUT_NEW_LIBRARY": "libfoo.so",
            _input_env_var(name): "false",
        },
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, (
        f"{name}=false is the input's own documented default, but the run "
        f"failed -- an existing workflow that writes the default explicitly "
        f"now breaks.\n{combined}"
    )


@pytest.mark.parametrize("name", sorted(RETIRED_INPUTS))
def test_a_retired_input_left_unset_is_never_itself_a_failure(name: str) -> None:
    """The negative control, and the reason the assertions above mean
    something: an unset tombstone must be inert. Without this, a validator
    that failed unconditionally would satisfy every test above.
    """
    result = _run_script(
        VALIDATE_SH, {"INPUT_MODE": "compare", "INPUT_NEW_LIBRARY": "libfoo.so"}
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert name not in combined, (
        f"{name} is mentioned on a run that never set it.\n{combined}"
    )


def test_every_declared_input_the_validator_rejects_outright_is_declared_retired() -> (
    None
):
    """The converse direction, which keeps the two documents honest with
    each other: an input the validator refuses on every mode is retired in
    fact, so ``action.yml`` must say so. Otherwise its description still
    advertises a capability the product refuses to run -- the documentation
    drift this contract is meant to make impossible.
    """
    document = yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))
    declared = document.get("inputs") or {}
    validator = VALIDATE_SH.read_text(encoding="utf-8")

    unmarked: list[str] = []
    for name, spec in declared.items():
        if "RETIRED" in (spec.get("description") or ""):
            continue
        env_var = _input_env_var(name)
        # Only probe inputs the validator even reads -- running the script
        # once per declared input is affordable, but an input it never
        # mentions cannot have a rejection to find.
        if env_var not in validator:
            continue
        result = _run_script(
            VALIDATE_SH,
            {
                "INPUT_MODE": "compare",
                "INPUT_NEW_LIBRARY": "libfoo.so",
                env_var: _meaningful_value(spec),
            },
        )
        message = result.stdout + result.stderr
        if result.returncode != 0 and (
            "no longer supported" in message
            or "is retired" in message
            or "was removed" in message
        ):
            unmarked.append(name)

    assert not unmarked, (
        "these inputs are refused as retired by validate-inputs.sh but their "
        "action.yml descriptions do not say RETIRED, so the documented "
        "surface still advertises them: " + ", ".join(sorted(unmarked))
    )
