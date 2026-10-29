# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""A one-member release concludes exactly as the scalar compare of its pair.

Root ``AGENTS.md`` "one model, any cardinality": a one-member package and the
scalar path must yield the same applicable findings. The sibling tests
(``test_compare_cardinality_invariance.py``,
``test_release_compare_workflow.py``) check this over the fixed F2 corpus
(seven hand-picked cases). This one states it as a property over *generated*
operand pairs -- arbitrary function/variable surfaces drawn on each side --
so a release-only step that drops, adds or re-scores a finding for some
shape of change the corpus does not contain still fails.

The release path is exercised end to end through the CLI (directory
``compare``), and every release-level projection a reader sees at N=1 is
held to the scalar report, not to anything the release machinery computes:

* the member's complete report equals the scalar report (F2 normalization);
* the release process exit code equals the scalar exit code -- exactly, not
  a fold -- and the release ``exit`` block equals the scalar one;
* the release verdict, the member row's verdict and per-category counts,
  the release ``change_inventory`` compatibility counts, the release
  ``disposition_audit``, and the release ``surface_changes`` lists all equal
  their scalar counterparts.

This is the regression oracle for the "release via pair sessions" refactor
(``docs/contribute/plans/release-via-pair-sessions.md``): moving a release
step onto its pair owner must keep it green.
"""

from __future__ import annotations

import dataclasses
import json
import tempfile
from pathlib import Path
from typing import Any

from _family_f2_routes import Outcome, _invoke, parity_violations
from hypothesis import HealthCheck, given, settings, strategies as st

from abicheck.model import AbiSnapshot, Function, Param, Variable, Visibility
from abicheck.serialization import snapshot_to_json

#: The member row's per-category counts, named as the scalar ``summary``.
_ROW_COUNTS = (
    "breaking",
    "source_breaks",
    "risk_changes",
    "compatible_additions",
    "quality_issues",
    "informational",
)

_TYPES = ("int", "long", "double", "char*")
_FUNC_NAMES = ("foo", "bar", "baz", "qux", "quux")
_VAR_NAMES = ("gv", "gw", "gx")


@st.composite
def _function(draw: Any, name: str) -> Function:
    params = draw(st.lists(st.sampled_from(_TYPES), max_size=2))
    return Function(
        name=name,
        mangled=name,
        return_type=draw(st.sampled_from(_TYPES)),
        params=[Param(name=f"p{i}", type=t) for i, t in enumerate(params)],
        visibility=Visibility.PUBLIC,
    )


@st.composite
def _side(draw: Any, version: str) -> AbiSnapshot:
    funcs = [draw(_function(name)) for name in _FUNC_NAMES if draw(st.booleans())]
    vars_ = [
        Variable(
            name=name,
            mangled=name,
            type=draw(st.sampled_from(_TYPES)),
            visibility=Visibility.PUBLIC,
        )
        for name in _VAR_NAMES
        if draw(st.booleans())
    ]
    return AbiSnapshot(
        library="libone.so",
        version=version,
        functions=funcs,
        variables=vars_,
        from_headers=True,
    )


@st.composite
def _pair(draw: Any) -> tuple[AbiSnapshot, AbiSnapshot]:
    old = draw(_side("1.0"))
    # Bias towards related sides: half the time NEW is OLD with some
    # members perturbed, so modifications (not only add/remove) are drawn.
    if draw(st.booleans()):
        return old, draw(_side("2.0"))
    funcs = [
        draw(_function(f.name)) if draw(st.booleans()) else f
        for f in old.declarations.functions
    ]
    return old, dataclasses.replace(old, version="2.0", functions=funcs)


def _strip_library(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Release surface entries carry the member name; a scalar's do not."""
    return [{k: v for k, v in e.items() if k != "library"} for e in entries]


def one_member_violations(root: Path, old: AbiSnapshot, new: AbiSnapshot) -> list[str]:
    """Every way the one-member release of (*old*, *new*) disagrees with the
    scalar compare of the same pair; empty means the invariant holds."""
    paths = []
    for side, snap in (("old", old), ("new", new)):
        (root / side).mkdir(parents=True)
        path = root / side / "libone.json"
        path.write_text(snapshot_to_json(snap), encoding="utf-8")
        paths.append(path)

    s = _invoke(["compare", str(paths[0]), str(paths[1]), "-o", "json=-"])
    scalar = json.loads(s.stdout)

    summary_path, reports = root / "summary.json", root / "reports"
    r = _invoke(
        [
            "compare",
            str(root / "old"),
            str(root / "new"),
            "-o",
            f"json={summary_path}",
            "-o",
            f"json={reports}/",
        ]
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    (row,) = summary["libraries"]
    member = json.loads(Path(row["complete_report"]).read_text(encoding="utf-8"))

    out = parity_violations(
        Outcome("scalar", scalar, s.exit_code),
        Outcome("release-member", member, r.exit_code),
    )

    def check(what: str, release_value: Any, scalar_value: Any) -> None:
        if release_value != scalar_value:
            out.append(
                f"{what}: release {json.dumps(release_value, sort_keys=True)[:300]}"
                f" != scalar {json.dumps(scalar_value, sort_keys=True)[:300]}"
            )

    check("exit block", summary["exit"], scalar["exit"])
    check("release verdict", summary["verdict"], scalar["verdict"])
    check("member row verdict", row["verdict"], scalar["verdict"])
    for key in _ROW_COUNTS:
        check(f"member row {key}", row[key], scalar["summary"][key])
    for key, value in scalar["summary"]["change_inventory"].items():
        check(f"change_inventory.{key}", summary["change_inventory"].get(key), value)
    check(
        "disposition_audit", summary["disposition_audit"], scalar["disposition_audit"]
    )
    for key in ("additions", "removals", "modifications"):
        check(
            f"surface_changes.{key}",
            _strip_library(summary["surface_changes"][key]),
            scalar["surface_changes"][key],
        )
    check(
        "surface_changes.total",
        summary["surface_changes"]["total"],
        scalar["surface_changes"]["total"],
    )
    return out


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
@given(pair=_pair())
def test_one_member_release_equals_scalar_compare(
    pair: tuple[AbiSnapshot, AbiSnapshot],
) -> None:
    old, new = pair
    with tempfile.TemporaryDirectory() as tmp:
        problems = one_member_violations(Path(tmp), old, new)
    assert not problems, "\n".join(problems)


def test_property_draws_every_verdict() -> None:
    """Guard against a vacuous strategy: the generator reaches a breaking, a
    compatible-addition and a no-change pair (examples pinned, not drawn)."""
    base = [
        Function(
            name="foo", mangled="foo", return_type="int", visibility=Visibility.PUBLIC
        )
    ]
    snap = AbiSnapshot(
        library="libone.so", version="1.0", functions=base, from_headers=True
    )
    added = dataclasses.replace(
        snap,
        version="2.0",
        functions=[
            *base,
            Function(
                name="bar",
                mangled="bar",
                return_type="int",
                visibility=Visibility.PUBLIC,
            ),
        ],
    )
    removed = dataclasses.replace(snap, version="2.0", functions=[])
    verdicts = set()
    for i, new in enumerate((snap, added, removed)):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assert not one_member_violations(root, snap, new)
            verdicts.add(json.loads((root / "summary.json").read_text())["verdict"])
    assert verdicts == {"NO_CHANGE", "COMPATIBLE", "BREAKING"}


def test_mutant_release_rescoring_a_member_is_caught(monkeypatch: Any) -> None:
    """Negative control: a release step that drops one finding from a
    member's result after the pair compare ran must break the invariant."""
    import abicheck.workflows.member_compare as member_compare

    real = member_compare.run_compare_request

    def dropping(request: Any, *args: Any, **kwargs: Any) -> Any:
        result = real(request, *args, **kwargs)
        result.diff.changes[:] = result.diff.changes[1:]
        return result

    monkeypatch.setattr(member_compare, "run_compare_request", dropping)
    base = [
        Function(
            name="foo", mangled="foo", return_type="int", visibility=Visibility.PUBLIC
        )
    ]
    old = AbiSnapshot(
        library="libone.so", version="1.0", functions=base, from_headers=True
    )
    new = dataclasses.replace(old, version="2.0", functions=[])
    with tempfile.TemporaryDirectory() as tmp:
        assert one_member_violations(Path(tmp), old, new)
