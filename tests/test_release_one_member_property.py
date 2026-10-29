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

import pytest
from _family_f2_routes import Outcome, _invoke, parity_violations
from hypothesis import (
    HealthCheck,
    Phase,
    Verbosity,
    find,
    given,
    settings,
    strategies as st,
)

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


#: A side is drawn as plain data -- ``(functions, variables)`` with each
#: function ``(name, return type, param types)`` and each variable
#: ``(name, type)`` -- and only built into an :class:`AbiSnapshot` by
#: :func:`_snapshot`, so Hypothesis can print any example it reports.
_FuncSpec = tuple[str, str, tuple[str, ...]]
_SideSpec = tuple[tuple[_FuncSpec, ...], tuple[tuple[str, str], ...]]


def _function(name: str) -> st.SearchStrategy[_FuncSpec]:
    return st.tuples(
        st.just(name),
        st.sampled_from(_TYPES),
        st.lists(st.sampled_from(_TYPES), max_size=2).map(tuple),
    )


@st.composite
def _side(draw: Any) -> _SideSpec:
    funcs = tuple(draw(_function(n)) for n in _FUNC_NAMES if draw(st.booleans()))
    vars_ = tuple(
        (n, draw(st.sampled_from(_TYPES))) for n in _VAR_NAMES if draw(st.booleans())
    )
    return funcs, vars_


@st.composite
def _pair(draw: Any) -> tuple[_SideSpec, _SideSpec]:
    old = draw(_side())
    # Three shapes, a third each: an unrelated NEW (add/remove), OLD with
    # members perturbed (modifications), and OLD plus members it lacks
    # (pure additions -- an unrelated draw is almost never a superset, so
    # without this shape a COMPATIBLE pair is only reached by luck).
    shape = draw(st.integers(0, 2))
    if shape == 0:
        return old, draw(_side())
    if shape == 1:
        funcs = tuple(
            draw(_function(f[0])) if draw(st.booleans()) else f for f in old[0]
        )
        return old, (funcs, old[1])
    have_f = {f[0] for f in old[0]}
    have_v = {v[0] for v in old[1]}
    extra_f = tuple(
        draw(_function(n))
        for n in _FUNC_NAMES
        if n not in have_f and draw(st.booleans())
    )
    extra_v = tuple(
        (n, draw(st.sampled_from(_TYPES)))
        for n in _VAR_NAMES
        if n not in have_v and draw(st.booleans())
    )
    return old, (old[0] + extra_f, old[1] + extra_v)


def _snapshot(spec: _SideSpec, version: str) -> AbiSnapshot:
    funcs, vars_ = spec
    return AbiSnapshot(
        library="libone.so",
        version=version,
        functions=[
            Function(
                name=name,
                mangled=name,
                return_type=ret,
                params=[Param(name=f"p{i}", type=t) for i, t in enumerate(params)],
                visibility=Visibility.PUBLIC,
            )
            for name, ret, params in funcs
        ],
        variables=[
            Variable(name=n, mangled=n, type=t, visibility=Visibility.PUBLIC)
            for n, t in vars_
        ],
        from_headers=True,
    )


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
    pair: tuple[_SideSpec, _SideSpec],
) -> None:
    old, new = _snapshot(pair[0], "1.0"), _snapshot(pair[1], "2.0")
    with tempfile.TemporaryDirectory() as tmp:
        problems = one_member_violations(Path(tmp), old, new)
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("verdict", ["NO_CHANGE", "COMPATIBLE", "BREAKING"])
def test_property_draws_every_verdict(verdict: str) -> None:
    """Guard against a vacuous strategy: ``_pair()`` itself must generate a
    pair whose scalar compare reaches each verdict, so a strategy change
    that loses one fails here rather than silently narrowing the property."""

    def reaches(pair: tuple[_SideSpec, _SideSpec]) -> bool:
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for side, spec, version in (
                ("old", pair[0], "1.0"),
                ("new", pair[1], "2.0"),
            ):
                path = Path(tmp) / f"{side}.json"
                path.write_text(
                    snapshot_to_json(_snapshot(spec, version)), encoding="utf-8"
                )
                paths.append(path)
            r = _invoke(["compare", str(paths[0]), str(paths[1]), "-o", "json=-"])
            return json.loads(r.stdout)["verdict"] == verdict

    old, new = find(
        _pair(),
        reaches,
        settings=settings(
            max_examples=300,
            deadline=None,
            database=None,
            verbosity=Verbosity.quiet,
            phases=[Phase.generate],
        ),
    )
    with tempfile.TemporaryDirectory() as tmp:
        assert not one_member_violations(
            Path(tmp), _snapshot(old, "1.0"), _snapshot(new, "2.0")
        )


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
