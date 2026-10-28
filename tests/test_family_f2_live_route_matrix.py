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

"""Harness H2 extension -- the route matrix over live and mixed operands.

``tests/test_family_f2_route_parity.py`` runs one semantic compare through
the native CLI, the typed API and a one-member directory, over *stored*
snapshots. This module extends that matrix with the operand forms the
historical divergences lived in (see ``_family_f2_routes.OPERAND_FORMS``):

* ``live``: both sides a real gcc/g++ binary plus its header, dumped by the
  same extractor a stored snapshot came from -- the live-vs-stored claim;
* ``mixed_old`` / ``mixed_new``: one side stored, the other live.

Every route is an (entry point, operand form) cell of ``ROUTE_MATRIX``, so a
new entry point or form joins every matrix test without a new test. The
completeness check walks the Click command tree (``abicheck.cli.main``): a new
command, group or leaf must say how the harness treats it.

Cells that diverge and are not fixed are ``KNOWN_ROUTE_DIVERGENCES``; each
has a strict-xfail cell here, so a fix forces the entry's removal. Needs gcc
and castxml: the live matrix is marked ``integration``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from pathlib import Path

import click
import pytest

from abicheck.cli import main
from abicheck.service import InputSpec

from _family_f2_routes import (  # isort: skip
    AXES,
    AXES_BY_NAME,
    ENTRY_POINTS,
    KNOWN_ROUTE_DIVERGENCES,
    LIVE_SOURCES,
    LIVE_UNDECLARED_NAMES,
    OPERAND_FORMS,
    ROUTE_EXCEPTIONS,
    ROUTE_MATRIX,
    ROUTES,
    STORED_VS_LIVE_PATHS,
    Axis,
    LiveBuild,
    Outcome,
    export_event_violations,
    live_operands,
    parity_violations,
    release_finding_entry_violations,
    write_live_build,
)

# ── route matrix: entry points x operand forms (live and mixed operands) ──

#: Entry points that are not a Click command: the typed Python API.
NON_CLI_ENTRY_POINTS: dict[str, str] = {
    "api": "the typed Python API (CompareRequest through run_compare_request); "
    "it has no Click command of its own",
}

#: Every command and group in ``abicheck.cli.main``'s Click tree, by command
#: path. ``("entry", names)`` names the :data:`ENTRY_POINTS` it reaches (a
#: comma-separated list: ``compare`` is both the scalar and the directory
#: release entry point, a directory operand selecting the fan-out).
#: Any other kind carries the reason it is not a pair-compare route.
CLI_TREE_ROUTING: dict[str, tuple[str, str]] = {
    "aggregate": (
        "out_of_scope",
        "reads a directory of per-target reports and folds them into one "
        "gate verdict; it takes no OLD/NEW operand pair",
    ),
    "compare": ("entry", "cli, release"),
    "deps": ("group", "dependency-closure commands; see deps compare"),
    "deps compare": (
        "out_of_scope",
        "one binary against two dependency roots (old_root, new_root): the "
        "operand contract is a closure, not an OLD/NEW pair",
    ),
    "deps tree": ("out_of_scope", "prints one binary's dependency tree; no compare"),
    "dump": (
        "producer",
        "writes the stored snapshot every stored and mixed route reads; its "
        "output is the stored side, not a compare entry point",
    ),
    "project": ("group", "project-level commands; see the leaves below"),
    "project capture-variants": (
        "out_of_scope",
        "captures build variants for a project manifest; it runs no comparison",
    ),
    "project history": (
        "out_of_scope",
        "lists recorded runs from storage; it compares nothing new",
    ),
    "project plan": (
        "out_of_scope",
        "plans a project's dump/compare work without executing it",
    ),
    "project validate": (
        "out_of_scope",
        "validates a project manifest; no operand pair",
    ),
}


def _tree_paths(cmd: click.Command, prefix: tuple[str, ...] = ()) -> set[str]:
    out: set[str] = set()
    for name, sub in getattr(cmd, "commands", {}).items():
        path = " ".join((*prefix, name))
        out.add(path)
        out |= _tree_paths(sub, (*prefix, name))
    return out


def _tree_routing_problems(
    paths: set[str], table: Mapping[str, tuple[str, str]]
) -> list[str]:
    problems = [
        f"Click command {p!r} has no CLI_TREE_ROUTING entry"
        for p in sorted(paths - set(table))
    ]
    problems += [
        f"stale CLI_TREE_ROUTING entry {p!r}" for p in sorted(set(table) - paths)
    ]
    reached: set[str] = set()
    for path, (kind, detail) in table.items():
        if kind == "entry":
            names = [n.strip() for n in detail.split(",")]
            reached |= set(names)
            problems += [
                f"{path!r} names unknown entry point {n!r}"
                for n in names
                if n not in ENTRY_POINTS
            ]
        elif kind in ("out_of_scope", "producer", "group"):
            if not detail.strip():
                problems.append(f"{path!r} is {kind} without a reason")
        else:
            problems.append(f"{path!r} has unknown kind {kind!r}")
    problems += [
        f"entry point {n!r} is reached by no Click command and is not a "
        "declared non-CLI surface"
        for n in sorted(set(ENTRY_POINTS) - reached - set(NON_CLI_ENTRY_POINTS))
    ]
    return problems


def test_every_click_command_is_routed_or_declared() -> None:
    """Completeness over the entry-point registry: a new Click command, group
    or leaf must say how the harness treats it, and an entry point must be
    reached by some command (or be declared non-CLI)."""
    problems = _tree_routing_problems(_tree_paths(main), CLI_TREE_ROUTING)
    assert not problems, "\n".join(problems)


def test_tree_routing_detects_a_new_command_and_a_stale_or_bad_entry() -> None:
    """Guard the guard: the Click-tree completeness check is not vacuous."""
    paths = _tree_paths(main)
    assert _tree_routing_problems(paths | {"brand-new"}, CLI_TREE_ROUTING)
    assert _tree_routing_problems(paths - {"dump"}, CLI_TREE_ROUTING)
    bad = {**CLI_TREE_ROUTING, "compare": ("entry", "cli, no-such-route")}
    assert _tree_routing_problems(paths, bad)
    unreached = {**CLI_TREE_ROUTING, "compare": ("entry", "cli")}
    assert _tree_routing_problems(paths, unreached)


def test_route_matrix_is_the_product_minus_declared_exceptions() -> None:
    """The matrix is derived from the two registries; a declared exception is
    a real cell with a reason, and a known divergence names a real route."""
    cells = {(r.entry, r.form) for r in ROUTE_MATRIX.values()}
    product = {(e, f) for e in ENTRY_POINTS for f in OPERAND_FORMS}
    assert cells == product - set(ROUTE_EXCEPTIONS)
    assert set(ROUTE_EXCEPTIONS) <= product
    assert all(reason.strip() for reason in ROUTE_EXCEPTIONS.values())
    assert set(KNOWN_ROUTE_DIVERGENCES) <= set(ROUTE_MATRIX)
    assert all(len(r) > 40 for r in KNOWN_ROUTE_DIVERGENCES.values())
    assert set(ROUTES) == {n for n, r in ROUTE_MATRIX.items() if r.form == "stored"}


@pytest.fixture(scope="module")
def live_builds(tmp_path_factory: pytest.TempPathFactory) -> Callable[[str], LiveBuild]:
    """Each live case built and dumped once per module: the builds are inputs,
    never written to by a route, so every axis reuses them."""
    root = tmp_path_factory.mktemp("f2-live")
    built: dict[str, LiveBuild] = {}

    def get(case: str) -> LiveBuild:
        if case not in built:
            built[case] = write_live_build(root / case, case)
        return built[case]

    return get


def _run_live_matrix(build: LiveBuild, axis: Axis, run_dir: Path) -> dict[str, Outcome]:
    """Every live-matrix route on *build*, against one shared run directory:
    the axis's input files (suppress, policy) must sit at one path for every
    route, as in :func:`_run_all`."""
    run_dir.mkdir(parents=True, exist_ok=True)
    outs: dict[str, Outcome] = {}
    for name, route in ROUTE_MATRIX.items():
        if route.entry in axis.unsupported or route.entry in axis.reported_elsewhere:
            continue
        if name in KNOWN_ROUTE_DIVERGENCES:
            continue  # pinned by test_known_route_divergence_still_diverges
        out = route.run(live_operands(build, route.form), axis, run_dir)
        outs[name] = dataclasses.replace(out, route=name)
    return outs


def live_matrix_violations(outs: Mapping[str, Outcome], case: str) -> list[str]:
    """Every route against the stored CLI reference (``cli``). A live or mixed
    route may differ from it only in :data:`STORED_VS_LIVE_PATHS`."""
    ref = outs["cli"]
    v: list[str] = []
    for name, out in outs.items():
        if name == "cli":
            continue
        route = ROUTE_MATRIX[name]
        extra = STORED_VS_LIVE_PATHS if route.form != "stored" else None
        v += parity_violations(ref, out, extra_paths=extra)
        if route.entry == "release":
            v += release_finding_entry_violations(ref, out)
    if case == "undeclared_export_churn":
        for name, out in outs.items():
            v += [
                f"{name}: {x}"
                for x in export_event_violations(out, LIVE_UNDECLARED_NAMES)
            ]
    return v


@pytest.mark.integration
@pytest.mark.parametrize("axis", [a.name for a in AXES])
@pytest.mark.parametrize("case", sorted(LIVE_SOURCES))
def test_live_route_matrix(
    case: str,
    axis: str,
    tmp_path: Path,
    live_builds: Callable[[str], LiveBuild],
) -> None:
    """Routes (1)-(3) of the matrix: a live compare, its stored-dump
    equivalent, the one-member directory of each, and the two mixed
    (one-side-stored) shapes, all decide alike under every axis."""
    a = AXES_BY_NAME[axis]
    outs = _run_live_matrix(live_builds(case), a, tmp_path / "run")
    required = {"cli", "api", "cli@live", "cli@mixed_old", "cli@mixed_new"}
    if "release" not in a.unsupported and "release" not in a.reported_elsewhere:
        required |= {"release", "release@live"}
    assert required <= set(outs), sorted(outs)
    violations = live_matrix_violations(outs, case)
    assert not violations, "\n".join(violations)


@pytest.mark.integration
def test_mutant_live_side_losing_its_header_is_caught(
    tmp_path: Path,
    live_builds: Callable[[str], LiveBuild],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seeded mutant: the typed API's live side is built without its header
    (a live operand losing its build context while the stored side keeps it).
    The live matrix must see the typed route stop agreeing with the stored
    reference, or the live cells compare nothing they claim to."""
    import _family_f2_routes as routes_mod

    real = routes_mod._input_spec

    def headerless(path: Path, header: Path | None, version: str) -> InputSpec:
        return real(path, None if path.suffix == ".so" else header, version)

    build = live_builds("removal_and_addition")
    default = AXES_BY_NAME["default"]
    ref = ROUTE_MATRIX["cli"].run(live_operands(build, "stored"), default, tmp_path)
    monkeypatch.setattr(routes_mod, "_input_spec", headerless)
    got = ROUTE_MATRIX["api@live"].run(live_operands(build, "live"), default, tmp_path)
    assert parity_violations(
        ref,
        dataclasses.replace(got, route="api@live"),
        extra_paths=STORED_VS_LIVE_PATHS,
    )


@pytest.mark.integration
@pytest.mark.parametrize("route", sorted(KNOWN_ROUTE_DIVERGENCES))
def test_known_route_divergence_still_diverges(
    route: str,
    tmp_path: Path,
    live_builds: Callable[[str], LiveBuild],
    request: pytest.FixtureRequest,
) -> None:
    request.applymarker(
        pytest.mark.xfail(
            strict=True,
            raises=AssertionError,
            reason=f"REAL DIVERGENCE {route}: {KNOWN_ROUTE_DIVERGENCES[route]}",
        )
    )
    build = live_builds("removal_and_addition")
    default = AXES_BY_NAME["default"]
    ref = ROUTE_MATRIX["cli"].run(live_operands(build, "stored"), default, tmp_path)
    got = ROUTE_MATRIX[route].run(
        live_operands(build, ROUTE_MATRIX[route].form), default, tmp_path
    )
    violations = parity_violations(
        ref, dataclasses.replace(got, route=route), extra_paths=STORED_VS_LIVE_PATHS
    )
    assert not violations, "\n".join(violations)
