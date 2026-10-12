# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The release compare owner, driven without Click.

``workflows.release_compare.execute_release_compare`` is the one module that
runs a directory/package release: pairing (through the resolved plan), every
member's comparison, the sibling passes and the release folds. These tests
call it the way a typed-API caller would -- ``ReleaseCompareRequest`` ->
``resolve_release_compare_plan`` -> ``execute_release_compare`` -- with no
``CliRunner`` anywhere.

The invariant (root ``AGENTS.md``, "one model, any cardinality"): a member's
result equals the scalar comparison of its own pair, for every member of a
release of any size. The oracle is the typed scalar pipeline
(``service_compare_pipeline.run_compare_request``) over the member's two
files, not anything the release machinery computes, and the comparison is
over the complete rendered member report (``to_json``) with the F2 route
oracle's normalization -- so a setting a member silently dropped shows up as
a report difference. ``tests/test_compare_cardinality_invariance.py`` states
the same invariant through the CLI.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from _family_f2_routes import (
    AXES_BY_NAME,
    CORPUS,
    Outcome,
    parity_violations,
)

from abicheck.reporter import to_json
from abicheck.serialization import snapshot_to_json
from abicheck.service import CompareRequest, InputSpec
from abicheck.service_compare_pipeline import run_compare_request
from abicheck.workflows.release_compare import (
    ReleaseCompareOptions,
    ReleaseCompareOutcome,
    execute_release_compare,
)
from abicheck.workflows.release_compare_reporter import SilentReleaseReporter
from abicheck.workflows.release_request import (
    ReleaseCompareRequest,
    cleanup_release_compare_plan,
    resolve_release_compare_plan,
)

#: The comparability refusal has its own exit axis (F2 route parity).
_COMPARABLE = sorted(c for c in CORPUS if c != "scope_mismatch")

#: The axes a release states through its typed inputs: the option fields the
#: member request forwards, or (severity) the plan's own gate.
_AXES = ("default", "policy_sdk_vendor", "policy_file", "suppress", "severity_strict")
_REQUEST_FIELDS = {"severity_preset"}

#: Divergences of the *typed* release route from the typed scalar route,
#: each asserted to still diverge (strict xfail) and recorded in
#: docs/contribute/known-gaps.md, so a fix turns its cell red until removed.
_KNOWN_DIVERGENCES = {
    "severity_strict": "a member records the gate on its contract context "
    "only from the release's resolved PackApplication, which only the CLI "
    "builds (cli_compare_receipt.resolve_release_pack_application); the "
    "typed scalar API resolves it from the request itself -- known gap "
    "'A typed release compare does not record the gate on its members'",
}

#: Every cardinality 1..3, each a rotation of the corpus so every case
#: appears at every position.
_ASSIGNMENTS = [
    tuple(_COMPARABLE[(start + k) % len(_COMPARABLE)] for k in range(n))
    for n in (1, 2, 3)
    for start in range(0, len(_COMPARABLE), 3)
]


def _write_release(root: Path, assignment: tuple[str, ...]) -> list[tuple[Path, Path]]:
    pairs = []
    for index, case in enumerate(assignment):
        old_snap, new_snap = CORPUS[case]
        lib = f"libm{index}.so"
        paths = []
        for side, snap in (("old", old_snap), ("new", new_snap)):
            directory = root / side
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"libm{index}.json"
            path.write_text(
                snapshot_to_json(dataclasses.replace(snap, library=lib)),
                encoding="utf-8",
            )
            paths.append(path)
        pairs.append((paths[0], paths[1]))
    return pairs


def _run_release(root: Path, axis_fields: dict[str, Any]) -> ReleaseCompareOutcome:
    request_fields = {k: v for k, v in axis_fields.items() if k in _REQUEST_FIELDS}
    option_fields = {k: v for k, v in axis_fields.items() if k not in _REQUEST_FIELDS}
    plan = resolve_release_compare_plan(
        ReleaseCompareRequest(
            old_dir=root / "old", new_dir=root / "new", **request_fields
        )
    )
    try:
        return execute_release_compare(
            plan,
            ReleaseCompareOptions(**option_fields),
            reporter=SilentReleaseReporter(),
        )
    finally:
        cleanup_release_compare_plan(plan)


def _scalar(old: Path, new: Path, axis_fields: dict[str, Any]) -> tuple[Outcome, Any]:
    result = run_compare_request(
        CompareRequest(
            old=InputSpec(path=old),
            new=InputSpec(path=new),
            pattern_verdicts=True,
            **axis_fields,
        )
    )
    report = json.loads(to_json(result.diff, severity_config=result.severity_config))
    return Outcome("scalar", report, report["exit"]["code"]), result


def _member_outcome(
    outcome: ReleaseCompareOutcome, library: str, exit_code: int
) -> Outcome:
    (entry,) = [e for e in outcome.library_results if e["library"] == library]
    diff = entry["_diff_result"]
    report = json.loads(to_json(diff, severity_config=outcome.severity_config))
    # Exit codes are folded separately (below); one member's report only.
    return Outcome("release-workflow", report, exit_code)


@pytest.mark.parametrize(
    "axis",
    [
        pytest.param(
            a,
            marks=pytest.mark.xfail(strict=True, reason=_KNOWN_DIVERGENCES[a]),
        )
        if a in _KNOWN_DIVERGENCES
        else a
        for a in _AXES
    ],
)
@pytest.mark.parametrize(
    "assignment", _ASSIGNMENTS, ids=lambda a: f"n{len(a)}-" + "+".join(a)
)
def test_each_member_is_the_scalar_compare_of_its_pair(
    assignment: tuple[str, ...], axis: str, tmp_path: Path
) -> None:
    axis_fields = AXES_BY_NAME[axis].api(tmp_path)
    pairs = _write_release(tmp_path / "rel", assignment)
    outcome = _run_release(tmp_path / "rel", axis_fields)

    assert sorted(e["library"] for e in outcome.library_results) == sorted(
        old.name for old, _ in pairs
    )
    problems: list[str] = []
    for old, new in pairs:
        scalar, result = _scalar(old, new, axis_fields)
        member = _member_outcome(outcome, old.name, scalar.exit_code)
        problems += [f"{old.name}: {p}" for p in parity_violations(scalar, member)]
        (entry,) = [e for e in outcome.library_results if e["library"] == old.name]
        # The member folds the very decision the scalar compare made.
        assert entry["_compatibility_contribution"] == (
            result.exit_decision.compatibility_contribution
        ), old.name
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("case", _COMPARABLE)
def test_a_one_member_release_concludes_as_its_scalar_compare(
    case: str, tmp_path: Path
) -> None:
    """At N=1 nothing release-level may add to or subtract from the member:
    no sibling pass applies, so the release verdict is the scalar verdict."""
    ((old, new),) = _write_release(tmp_path / "rel", (case,))
    outcome = _run_release(tmp_path / "rel", {})
    scalar, result = _scalar(old, new, {})

    (entry,) = outcome.library_results
    assert entry["verdict"] == result.diff.verdict.value
    assert outcome.worst_verdict == result.diff.verdict.value
    assert outcome.removed_keys == outcome.added_keys == []
    assert not parity_violations(
        scalar, _member_outcome(outcome, old.name, scalar.exit_code)
    )


def test_assignments_cover_every_cardinality_and_case() -> None:
    """Guard against a vacuous parametrization."""
    assert {len(a) for a in _ASSIGNMENTS} == {1, 2, 3}
    assert {c for a in _ASSIGNMENTS for c in a} == set(_COMPARABLE)


def test_mutant_member_dropping_a_setting_is_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: a member that silently drops ``suppress`` must
    break the invariant, or the oracle above proves nothing."""
    import abicheck.workflows.member_compare as member_compare

    real = member_compare.run_compare

    def dropping(*args: object, **kwargs: object) -> object:
        kwargs["suppress"] = None
        return real(*args, **kwargs)

    monkeypatch.setattr(member_compare, "run_compare", dropping)
    with pytest.raises(AssertionError):
        test_each_member_is_the_scalar_compare_of_its_pair(
            ("removal_and_addition", "addition_only"), "suppress", tmp_path
        )
