# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""A scalar compare is a release with one member, at any cardinality.

ADR-063/065/067 "one model, any cardinality": comparing a pair of operands on
its own and comparing the same pair as one member of an N-member directory
release must yield the same applicable findings, dispositions (the
``disposition_audit`` block) and verdict, and the release's exit code must be
the fold of its members' exit codes. Checked for every cardinality 1..4 over
member assignments drawn from the F2 route-parity corpus, so a member's result
can never depend on how many siblings it was compared with, nor on which.

The oracle is independent of the release machinery: each member's reference
is a plain scalar ``compare`` of that member's own two files, and the exit
fold is the documented one (``docs/reference/exit-codes.md``: the worst
member wins).
"""

from __future__ import annotations

import dataclasses
import itertools
import json
from pathlib import Path

import pytest
from _family_f2_routes import (
    AXES_BY_NAME,
    CORPUS,
    Outcome,
    _invoke,
    parity_violations,
)

from abicheck.serialization import snapshot_to_json

#: Cases that compare (the comparability refusal has its own exit axis and
#: its own parity cell in ``test_family_f2_route_parity.py``).
_COMPARABLE = sorted(c for c in CORPUS if c != "scope_mismatch")

#: Member assignments: every cardinality 1..4, each as several rotations of
#: the corpus so every case appears at every position.
_ASSIGNMENTS = [
    tuple(_COMPARABLE[(start + k) % len(_COMPARABLE)] for k in range(n))
    for n in range(1, 5)
    for start in range(0, len(_COMPARABLE), 2)
]


def _write_member(root: Path, index: int, case: str) -> tuple[Path, Path]:
    old_snap, new_snap = CORPUS[case]
    lib = f"libm{index}.so"
    paths = []
    for side, snap in (("old", old_snap), ("new", new_snap)):
        d = root / side
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"libm{index}.json"
        p.write_text(
            snapshot_to_json(dataclasses.replace(snap, library=lib)),
            encoding="utf-8",
        )
        paths.append(p)
    return paths[0], paths[1]


def _scalar(old: Path, new: Path, axis_args: list[str]) -> Outcome:
    r = _invoke(["compare", str(old), str(new), *axis_args, "-o", "json=-"])
    return Outcome("scalar", json.loads(r.stdout), r.exit_code)


def _release(root: Path, axis_args: list[str]) -> tuple[dict, dict[str, Outcome], int]:
    summary, reports = root / "summary.json", root / "reports"
    r = _invoke(
        [
            "compare",
            str(root / "old"),
            str(root / "new"),
            *axis_args,
            "-o",
            f"json={summary}",
            "-o",
            f"json={reports}/",
        ]
    )
    doc = json.loads(summary.read_text(encoding="utf-8"))
    members = {
        lib["library"]: Outcome(
            "release",
            json.loads(Path(lib["complete_report"]).read_text(encoding="utf-8")),
            r.exit_code,
        )
        for lib in doc["libraries"]
    }
    return doc, members, r.exit_code


@pytest.mark.parametrize("axis", ["default", "policy_sdk_vendor", "suppress"])
@pytest.mark.parametrize(
    "assignment", _ASSIGNMENTS, ids=lambda a: f"n{len(a)}-" + "+".join(a)
)
def test_member_result_is_independent_of_cardinality(
    assignment: tuple[str, ...], axis: str, tmp_path: Path
) -> None:
    axis_args = AXES_BY_NAME[axis].cli(tmp_path)
    scalars: dict[str, Outcome] = {}
    for i, case in enumerate(assignment):
        old, new = _write_member(tmp_path / "rel", i, case)
        scalars[f"libm{i}.json"] = _scalar(old, new, axis_args)

    _doc, members, release_exit = _release(tmp_path / "rel", axis_args)
    assert sorted(members) == sorted(scalars)

    problems: list[str] = []
    for name, scalar in scalars.items():
        member = members[name]
        # Exit codes are folded below; compare one member's report only.
        member = dataclasses.replace(member, exit_code=scalar.exit_code)
        problems += [f"{name}: {p}" for p in parity_violations(scalar, member)]
    assert not problems, "\n".join(problems)

    assert release_exit == max(s.exit_code for s in scalars.values())


def test_assignments_cover_every_cardinality_and_verdict() -> None:
    """Guard against a vacuous parametrization."""
    assert {len(a) for a in _ASSIGNMENTS} == {1, 2, 3, 4}
    assert set(itertools.chain.from_iterable(_ASSIGNMENTS)) == set(_COMPARABLE)


def test_mutant_member_dropping_a_setting_is_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: a release member that silently drops ``--suppress``
    must break the invariant."""
    import abicheck.workflows.member_compare as member_compare

    real = member_compare.run_compare

    def dropping(*args: object, **kwargs: object) -> object:
        kwargs["suppress"] = None
        return real(*args, **kwargs)

    monkeypatch.setattr(member_compare, "run_compare", dropping)
    with pytest.raises(AssertionError):
        test_member_result_is_independent_of_cardinality(
            ("removal_and_addition", "addition_only"), "suppress", tmp_path
        )
