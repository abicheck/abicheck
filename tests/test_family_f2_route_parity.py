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

"""Harness H2 -- the route-parity matrix for defect family F2.

``docs/contribute/plans/defect-family-harnesses.md`` § "H2". The existing
parity tests (``test_front_end_default_parity.py``,
``test_cli_compare_release_single_pair_parity.py``,
``test_one_comparison_product_parity.py``, ...) are each *one cell* of this
matrix: one option, one pair of routes. Every September-2026 F2 escape
(#1391 release member dropping a stated setting, #1258/#1264 typed-API
defaults, #1321 directory vs single library, #1233/#1195 capability guards,
#1176/#1326 release finding entries) was a cell nobody had written yet. So:

* **Matrix** -- corpus pair x setting axis, each run through the native CLI,
  the typed ``CompareRequest`` API and a one-member directory release (the
  member's complete report projected out). The oracle is equality of the
  whole canonical JSON report, minus only ``ROUTE_SPECIFIC_FIELDS``/
  ``ROUTE_SPECIFIC_PATHS`` (each with its reason), plus equal verdict and
  exit code.
* **Completeness by introspection** -- every ``compare`` Click parameter and
  every ``CompareRequest`` field must have a ``PARAM_ROUTING`` entry; a new
  option or field with none fails, and so does a stale entry.
* **Default agreement** -- every Click parameter maps to its request/input
  field (or states why it has none) and the two defaults must agree.
* **Seeded mutants** -- the oracle is shown to catch a #1391-style dropped
  member field and a #1258-style flipped typed-API default.
* **Known divergences** -- what this harness found and is not justified as
  route-specific is listed in ``KNOWN_DIVERGENCES`` and pinned by
  ``xfail(strict=True)`` cells, never silently normalized away.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner

import abicheck.service as service_mod
from abicheck.cli import main
from abicheck.service import CompareRequest, InputSpec

from _family_f2_routes import (  # isort: skip
    UNDECLARED_ADDED,
    AXES,
    AXES_BY_NAME,
    CORPUS,
    KNOWN_DIVERGENCES,
    ROUTE_SPECIFIC_FIELDS,
    ROUTE_SPECIFIC_PATHS,
    ROUTES,
    STORED_VS_LIVE_PATHS,
    Axis,
    Outcome,
    _invoke,
    export_event_violations,
    normalize,
    parity_violations,
    release_finding_entry_violations,
    run_api,
    run_cli,
    run_release_member,
    write_operands,
)

COMPARE = main.commands["compare"]

# ── PARAM_ROUTING: the inventory ─────────────────────────────────────────
# ("covered", (axis, ...))       exercised with a non-default value by those
#                                AXES, through every route not listed in the
#                                axis's own ``unsupported`` map
# ("route_specific", reason)     meaningful on one route only
# ("uncovered", reason)          shared, but not yet a matrix cell -- backlog

_RS = "route_specific"
_UC = "uncovered"

CLICK_ROUTING: dict[str, tuple[str, Any]] = {
    "policy": ("covered", ("policy_sdk_vendor", "policy_file")),
    "suppress": ("covered", ("suppress", "suppress_strict")),
    "acknowledgments": (
        _UC,
        "--acknowledgments is an operator-named records file; CLI/release/API parity is test_acknowledgment_cli_surfaces.py",
    ),
    "severity_preset": ("covered", ("severity_strict",)),
    "diagnostic_comparison": ("covered", ("diagnostic_comparison",)),
    "contract_mode": ("covered", ("contract_exports",)),
    "exports": (
        "covered",
        ("default",),
    ),  # -o json=... is the CLI/release oracle channel itself
    "help": (_RS, "Click help flag; no analysis meaning"),
    "help_all": (_RS, "Click help flag; no analysis meaning"),
    "old_input": ("covered", ("default",)),
    "new_input": ("covered", ("default",)),
    "no_baseline": (
        _RS,
        "single-candidate audit mode; the typed API has no operand-less request",
    ),
    "select": (
        _RS,
        "release member selection (ADR-065); the scalar/API routes have one member by construction",
    ),
    "select_required": (_RS, "release member selection (ADR-065)"),
    "variant": (_RS, "release variant selection (ADR-065)"),
    "bundle_facts_out": (_RS, "release-only artifact export"),
    "bundle_facts_library_manifest": (_RS, "release-only artifact export"),
    "manifest_path": (
        _UC,
        "instantiation manifest needs a real template-bearing library; live-only",
    ),
    "exclude_headers": (
        _UC,
        "header-side input: needs live header parsing (castxml) -- integration lane",
    ),
    "header": ("covered", ("live_integration",)),
    "include": (_UC, "header-side input: needs live header parsing (castxml)"),
    "version": ("covered", ("live_integration",)),
    "dump_manifest": (_UC, "L2 dump manifest needs a live build tree"),
    "view": (_RS, "CLI presentation of the human render; JSON document is unaffected"),
    "used_by_apps": (_UC, "consumer-scoped promotion needs a consumer binary (live)"),
    "required_symbols_opt": (
        _UC,
        "consumer-scoped promotion; CompareRequest has no field -- applied post-compare by the CLI",
    ),
    "used_by_manifests": (_UC, "consumer manifest; CompareRequest has no field"),
    "config": (
        _UC,
        ".abicheck.yml folding is a CLI-only resolution tier (tests/CLAUDE.md: test at the resolved request)",
    ),
    "follow_deps": (_UC, "dependency walk needs real DT_NEEDED binaries"),
    "include_dependencies": (
        _UC,
        "system-declaration filter only acts on a live header dump",
    ),
    "defines": (_UC, "preprocessor defines only act on a live header dump"),
    "search_paths": (_UC, "dependency search paths need real binaries"),
    "ld_library_path": (_UC, "dependency search paths need real binaries"),
    "debug_info": (_UC, "external debug roots need real stripped binaries"),
    # Also the probe-matrix spelling (--build-info old=/new=<matrix>), which
    # is covered; a build-evidence pack still needs a real build tree.
    "build_info": ("covered", ("probe_matrix",)),
    "sources": (_UC, "L4/L5 source evidence needs a source tree"),
    "depth": (
        _UC,
        "stored snapshots carry fixed evidence; --depth floor needs live extraction",
    ),
    "since": (_RS, "git-ref baseline resolution; CLI-only operand sugar"),
    "changed_paths_opt": (_UC, "changed-path relevance; not yet a cell"),
    "abi3": (_UC, "needs a CPython extension module fixture"),
    "budget": (_UC, "wall-clock budget; nondeterministic by nature"),
    "dry_run": (
        _UC,
        "dry-run plan vs execution parity (#1089/#977/#1013) is a planned H2 extension",
    ),
    "pack_paths": (
        _UC,
        "CLI/API parity is test_compare_request_configuration_parity.py",
    ),
    "use_cases_manifest": (_RS, "use-case manifest; CLI-only project integration"),
    "performance_profile": (
        _UC,
        "performance profile only changes resource use, not the report",
    ),
    "verbose": (_RS, "stderr progress only"),
    "require_same_generation": (
        _UC,
        "every corpus pair shares one extractor generation, so the refusal "
        "never fires; CLI/API refusal is test_generation_drift_demotion.py",
    ),
}

_SEVERITY_OVERRIDE = (
    _UC,
    "the .abicheck.yml severity override; CLI/typed parity is test_compare_exit_gate_unified.py",
)

REQUEST_ROUTING: dict[str, tuple[str, Any]] = {
    "old": ("covered", ("default",)),
    "new": ("covered", ("default",)),
    "policy": ("covered", ("policy_sdk_vendor",)),
    "policy_file_path": ("covered", ("policy_file",)),
    "suppress": ("covered", ("suppress", "suppress_strict")),
    "strict_suppressions": ("covered", ("suppress_strict",)),
    "old_probe_matrix": ("covered", ("probe_matrix",)),
    "new_probe_matrix": ("covered", ("probe_matrix",)),
    "require_justification": ("covered", ("suppress_strict",)),
    "scope_public": ("covered", ("no_scope_public",)),
    "severity_preset": ("covered", ("severity_strict",)),
    "severity_abi_breaking": _SEVERITY_OVERRIDE,
    "severity_potential_breaking": _SEVERITY_OVERRIDE,
    "severity_quality_issues": _SEVERITY_OVERRIDE,
    "severity_addition": _SEVERITY_OVERRIDE,
    "require_complete_analysis": (
        _UC,
        "the assurance.require_complete exit needs an incomplete-analysis fixture; gate-level parity is test_compare_exit_gate_unified.py",
    ),
    "diagnostic_comparison": ("covered", ("diagnostic_comparison",)),
    "contract_evaluation": ("covered", ("contract_exports",)),
    "contract_mode": ("covered", ("contract_exports",)),
    "pattern_verdicts": (
        "covered",
        ("default",),
    ),  # every API cell passes True, mirroring the CLI
    "lang": (_UC, "only reaches a live header parse"),
    "lang_explicit": (
        _UC,
        "only reaches a live header parse (#1391's field; covered at request-build level by the mutant below)",
    ),
    "frontend": (_UC, "only reaches a live header parse"),
    "frontend_context": (_RS, "records which front end built the request"),
    "has_sources": (_UC, "L4 source evidence"),
    "force_public_symbols": (
        _UC,
        "no CLI spelling any more; config-only scope.public_symbols",
    ),
    "public_surface_allowlist": (
        _UC,
        "CLI reaches it through .abicheck.yml contract.overlays.post_manifest; not yet a cell",
    ),
    "enable_debuginfod": (_UC, "network"),
    "debuginfod_url": (_UC, "network"),
    "env_matrix": (_UC, ".abicheck.yml deployment: block only"),
    "acknowledgments_path": (
        _UC,
        ".abicheck.yml acknowledgment.file (explicit --config only); CLI/release/API parity is test_acknowledgment_release_and_api.py",
    ),
    "acknowledgment_breaks": (
        _UC,
        ".abicheck.yml acknowledgment.breaks (explicit --config or a --policy block); CLI/release/API parity is test_acknowledgment_breaks.py",
    ),
    "acknowledgment_unacknowledged_additions": (
        _UC,
        ".abicheck.yml acknowledgment.unacknowledged_additions; CLI/release/API parity is test_acknowledgment_release_and_api.py",
    ),
    "depth": (_UC, "needs live extraction"),
    "budget_s": (_UC, "nondeterministic"),
    "dwarf_only": (_UC, "needs a live binary"),
    "debug_format": (_UC, "needs a live binary"),
    "include_labels": (_UC, "needs live header parsing"),
    "follow_dependencies": (_UC, "needs real DT_NEEDED binaries"),
    "dependency_search_paths": (_UC, "needs real binaries"),
    "ld_library_path": (_UC, "needs real binaries"),
    "pack_policy_overrides": (_UC, "pack projection; CLI spelling is --pack"),
    "project_policy_overrides": (_UC, "config projection; CLI spelling is --config"),
    "pack_internal_namespaces": (_UC, "pack projection"),
    "pack_paths": (
        _UC,
        "CLI/API parity is test_compare_request_configuration_parity.py",
    ),
    "stated_settings": (_UC, "receipt provenance; parity in the same module"),
    "changed_paths": (_UC, "not yet a cell"),
    "abi3_floor": (_UC, "needs a CPython extension module"),
    "collapse_versioned_symbols": (_UC, "config-only; needs versioned ELF symbols"),
    "allow_build_query": (_UC, "needs a build tree"),
    "performance_profile": (_UC, "resource use only"),
    "require_same_generation": (
        _UC,
        "corpus pairs share one extractor generation; refusal parity is "
        "test_generation_drift_demotion.py",
    ),
    "resolved_collect_mode": (
        _UC,
        "needs a source tree; the CLI resolves it from source.method/--since",
    ),
}


def _cli_built_in_config() -> Any:
    from abicheck.workflows.compare_configuration import resolve_compare_config

    return resolve_compare_config(None, cli_severity_preset=None)


def _cli_built_in_scope_public() -> bool:
    return _cli_built_in_config().scope_public


#: ``.abicheck.yml`` key -> (typed-API field, the CLI's built-in default) for a
#: setting with no Click parameter. ``scope.public`` lost its flag in
#: one-comparison-product Phase 9b; ``suppression.*`` never had one. Each
#: typed-API default must agree with what a no-flag, no-config CLI run resolves.
CONFIG_DEFAULT_MAP: dict[str, tuple[tuple[str, str], Callable[[], Any]]] = {
    "scope.public": (("CompareRequest", "scope_public"), _cli_built_in_scope_public),
    "suppression.strict": (
        ("CompareRequest", "strict_suppressions"),
        lambda: _cli_built_in_config().strict_suppressions,
    ),
    "suppression.require_justification": (
        ("CompareRequest", "require_justification"),
        lambda: _cli_built_in_config().require_justification,
    ),
}

#: Click dest -> ("CompareRequest"|"InputSpec", field) whose default must match.
DEFAULT_MAP: dict[str, tuple[str, str]] = {
    "policy": ("CompareRequest", "policy"),
    "suppress": ("CompareRequest", "suppress"),
    "acknowledgments": ("CompareRequest", "acknowledgments_path"),
    "severity_preset": ("CompareRequest", "severity_preset"),
    "diagnostic_comparison": ("CompareRequest", "diagnostic_comparison"),
    "contract_mode": ("CompareRequest", "contract_mode"),
    "follow_deps": ("CompareRequest", "follow_dependencies"),
    "search_paths": ("CompareRequest", "dependency_search_paths"),
    "ld_library_path": ("CompareRequest", "ld_library_path"),
    "depth": ("CompareRequest", "depth"),
    "budget": ("CompareRequest", "budget_s"),
    "abi3": ("CompareRequest", "abi3_floor"),
    "changed_paths_opt": ("CompareRequest", "changed_paths"),
    "performance_profile": ("CompareRequest", "performance_profile"),
    "require_same_generation": ("CompareRequest", "require_same_generation"),
    "pack_paths": ("CompareRequest", "pack_paths"),
    "include_dependencies": ("InputSpec", "include_dependencies"),
    "header": ("InputSpec", "headers"),
    "include": ("InputSpec", "includes"),
    "exclude_headers": ("InputSpec", "exclude_headers"),
    "sources": ("InputSpec", "sources"),
    "build_info": ("InputSpec", "build_info"),
    "dump_manifest": ("InputSpec", "dump_manifest"),
    "debug_info": ("InputSpec", "debug_roots"),
    "version": ("InputSpec", "version"),
}

#: Click dests with no request/input counterpart whose default could be
#: compared, each with the reason.
DEFAULT_UNMAPPED: dict[str, str] = {
    "help": "Click help flag",
    "help_all": "Click help flag",
    "old_input": "required operand, no default",
    "new_input": "operand; InputSpec.path is required",
    "no_baseline": "no typed-API equivalent (audit mode)",
    "select": "release-only selection",
    "select_required": "release-only selection",
    "variant": "release-only selection",
    "bundle_facts_out": "release-only export",
    "bundle_facts_library_manifest": "release-only export",
    "manifest_path": "resolved into InputSpec.compile context, not a scalar field",
    "exports": "output routing; the typed API renders explicitly",
    "view": "presentation only",
    "used_by_apps": "applied after compare() by the CLI; no request field",
    "required_symbols_opt": "applied after compare() by the CLI; no request field",
    "used_by_manifests": "applied after compare() by the CLI; no request field",
    "config": ".abicheck.yml is folded into several request fields",
    "defines": "folded into InputSpec.compile (a context object)",
    "since": "git-ref operand sugar",
    "dry_run": "CLI-only planning mode",
    "use_cases_manifest": "CLI-only project integration",
    "verbose": "stderr only",
}


# ── introspection helpers ───────────────────────────────────────────────


def _click_params() -> dict[str, click.Parameter]:
    # Hidden legacy aliases (ADR-091 D7) never reach the callback, so they have no route.
    return {
        p.name: p
        for p in COMPARE.params
        if p.name and not p.name.startswith("_legacy_")
    }


def _request_fields() -> set[str]:
    return {f.name for f in dataclasses.fields(CompareRequest)}


def _norm_default(value: Any, *, multiple: bool = False) -> Any:
    if value is None or value == "" or (isinstance(value, (list, tuple)) and not value):
        return None
    if type(value).__name__ == "Sentinel" or repr(value).startswith("Sentinel."):
        return None
    if isinstance(value, list):
        return tuple(value)
    return value


def _dataclass_default(cls: type, name: str) -> Any:
    f = cls.__dataclass_fields__[name]  # type: ignore[attr-defined]
    if f.default is not dataclasses.MISSING:
        return f.default
    if f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
        return f.default_factory()  # type: ignore[misc]
    return dataclasses.MISSING


def default_agreement_violations() -> list[str]:
    params = _click_params()
    owners = {"CompareRequest": CompareRequest, "InputSpec": InputSpec}
    out = []
    for dest, (owner, fname) in DEFAULT_MAP.items():
        p = params[dest]
        cli_d = _norm_default(p.default, multiple=getattr(p, "multiple", False))
        api_d = _norm_default(_dataclass_default(owners[owner], fname))
        if cli_d != api_d:
            out.append(
                f"--{dest} defaults to {cli_d!r} but {owner}.{fname} to {api_d!r}"
            )
    for key, ((owner, fname), cli_default) in CONFIG_DEFAULT_MAP.items():
        api_d = _norm_default(_dataclass_default(owners[owner], fname))
        if cli_default() != api_d:
            out.append(
                f"{key} defaults to {cli_default()!r} on the CLI but "
                f"{owner}.{fname} to {api_d!r}"
            )
    return out


# ── completeness ─────────────────────────────────────────────────────────


def _routing_problems(
    routing: dict[str, tuple[str, Any]], live: set[str], kind: str
) -> list[str]:
    problems = [
        f"{kind} {n!r} has no PARAM_ROUTING entry" for n in sorted(live - set(routing))
    ]
    problems += [
        f"stale PARAM_ROUTING entry for {kind} {n!r}"
        for n in sorted(set(routing) - live)
    ]
    for name, (status, detail) in routing.items():
        if status == "covered":
            for axis in detail:
                if axis not in AXES_BY_NAME and axis != "live_integration":
                    problems.append(f"{kind} {name!r} names unknown axis {axis!r}")
        elif status in (_RS, _UC):
            if not (isinstance(detail, str) and detail.strip()):
                problems.append(f"{kind} {name!r} is {status} without a reason")
        else:
            problems.append(f"{kind} {name!r} has unknown status {status!r}")
    return problems


def test_every_click_parameter_is_routed() -> None:
    problems = _routing_problems(CLICK_ROUTING, set(_click_params()), "click param")
    assert not problems, "\n".join(problems)


def test_every_request_field_is_routed() -> None:
    problems = _routing_problems(
        REQUEST_ROUTING, _request_fields(), "CompareRequest field"
    )
    assert not problems, "\n".join(problems)


def test_axes_agree_with_the_routing_table() -> None:
    """An axis claiming to exercise a parameter must be what the table says
    covers it, and every non-default axis must name at least one parameter
    -- so a 'covered' claim cannot rest on an axis that sets nothing."""
    problems = []
    for axis in AXES:
        if axis.name == "default":
            continue
        assert (axis.click_params or axis.config_keys) and axis.request_fields, (
            axis.name
        )
        for key in axis.config_keys:
            assert key in CONFIG_DEFAULT_MAP, (axis.name, key)
        for p in axis.click_params:
            status, detail = CLICK_ROUTING[p]
            if status != "covered" or axis.name not in detail:
                problems.append(
                    f"axis {axis.name} sets --{p} but CLICK_ROUTING does not credit it"
                )
        for f in axis.request_fields:
            status, detail = REQUEST_ROUTING[f]
            if status != "covered" or axis.name not in detail:
                problems.append(
                    f"axis {axis.name} sets {f} but REQUEST_ROUTING does not credit it"
                )
        # covered through >= 2 routes
        assert len(set(ROUTES) - set(axis.unsupported)) >= 2, axis.name
    assert not problems, "\n".join(problems)


def test_routing_detects_a_new_option_and_a_stale_entry() -> None:
    """Guard the guard: the completeness check is not vacuous."""
    live = set(_click_params())
    assert _routing_problems(CLICK_ROUTING, live | {"brand_new_flag"}, "click param")
    assert _routing_problems(CLICK_ROUTING, live - {"policy"}, "click param")


def test_every_click_parameter_has_a_default_mapping_or_a_reason() -> None:
    """Each Click parameter either maps to the request/input field whose
    default must agree, or states why it has none; stale entries fail."""
    live = set(_click_params())
    assert set(DEFAULT_MAP).isdisjoint(DEFAULT_UNMAPPED)
    assert set(DEFAULT_MAP) | set(DEFAULT_UNMAPPED) == live, sorted(
        live ^ (set(DEFAULT_MAP) | set(DEFAULT_UNMAPPED))
    )
    assert all(r.strip() for r in DEFAULT_UNMAPPED.values())
    for owner, fname in DEFAULT_MAP.values():
        cls = CompareRequest if owner == "CompareRequest" else InputSpec
        assert fname in cls.__dataclass_fields__, (owner, fname)  # type: ignore[attr-defined]


def test_shared_defaults_agree() -> None:
    violations = default_agreement_violations()
    assert not violations, "\n".join(violations)


def test_pattern_verdicts_default_matches_cli(tmp_path: Path) -> None:
    ops = write_operands(tmp_path, "removal_and_addition")
    cli = run_cli(ops, AXES_BY_NAME["default"], tmp_path)
    req = CompareRequest(old=InputSpec(path=ops.old), new=InputSpec(path=ops.new))
    fields = cli.report.get("effective_config_fields") or {}
    assert fields.get("policy.pattern_verdicts") == "True"
    assert req.pattern_verdicts is True


# ── the matrix ──────────────────────────────────────────────────────────


def _run_all(
    case: str, axis: Axis, tmp: Path, routes: dict[str, Callable[..., Outcome]] = ROUTES
) -> dict[str, Outcome]:
    ops = write_operands(tmp, case)
    return {
        name: fn(ops, axis, tmp)
        for name, fn in routes.items()
        if name not in axis.unsupported and name not in axis.reported_elsewhere
    }


def matrix_violations(outs: dict[str, Outcome]) -> list[str]:
    ref = outs["cli"]
    v: list[str] = []
    for name, out in outs.items():
        if name != "cli":
            v += parity_violations(ref, out)
    if "release" in outs:
        v += release_finding_entry_violations(ref, outs["release"])
    return v


@pytest.mark.parametrize("axis", [a.name for a in AXES])
@pytest.mark.parametrize("case", sorted(CORPUS))
def test_route_parity(case: str, axis: str, tmp_path: Path) -> None:
    outs = _run_all(case, AXES_BY_NAME[axis], tmp_path)
    assert len(outs) >= 2
    violations = matrix_violations(outs)
    assert not violations, "\n".join(violations)


def test_the_axes_are_not_vacuous(tmp_path: Path) -> None:
    """Each non-default axis must change the CLI report for some corpus pair
    -- otherwise its parity cells compare two default documents."""
    baseline = {
        c: normalize(
            _run_all(c, AXES_BY_NAME["default"], tmp_path / c, {"cli": run_cli})[
                "cli"
            ].report
        )
        for c in CORPUS
    }
    inert = []
    for axis in AXES:
        if axis.name == "default":
            continue
        moved = any(
            normalize(
                _run_all(c, axis, tmp_path / f"{axis.name}-{c}", {"cli": run_cli})[
                    "cli"
                ].report
            )
            != baseline[c]
            for c in ("removal_and_addition", "variable_removed", "scope_mismatch")
        )
        if not moved:
            inert.append(axis.name)
    assert not inert, inert


def test_corpus_spans_verdicts(tmp_path: Path) -> None:
    verdicts = {
        run_cli(
            write_operands(tmp_path / c, c), AXES_BY_NAME["default"], tmp_path / c
        ).verdict
        for c in CORPUS
    }
    assert {"NO_CHANGE", "COMPATIBLE", "BREAKING"} <= verdicts, verdicts


@pytest.mark.parametrize("axis", [a.name for a in AXES])
def test_undeclared_export_event_is_one_finding_on_every_route(
    axis: str, tmp_path: Path
) -> None:
    outs = _run_all("undeclared_export_churn", AXES_BY_NAME[axis], tmp_path)
    assert {"cli", "api", "release"} & set(outs), outs.keys()
    violations = [v for out in outs.values() for v in export_event_violations(out)]
    assert not violations, "\n".join(violations)


def test_mutant_unfolded_hygiene_twin_is_caught(tmp_path: Path) -> None:
    """Seeded mutant: a report carrying the hygiene twin beside the existence
    finding (the pre-#1492 double report) must be flagged."""
    out = _run_all(
        "undeclared_export_churn", AXES_BY_NAME["default"], tmp_path, {"cli": run_cli}
    )["cli"]
    assert export_event_violations(out) == []
    twin = {"kind": "exported_not_public", "symbol": UNDECLARED_ADDED}
    doubled = dataclasses.replace(
        out, report={**out.report, "changes": [*out.report["changes"], twin]}
    )
    assert export_event_violations(doubled) != []


@pytest.mark.parametrize(
    ("axis", "route"),
    [(a.name, r) for a in AXES for r in a.unsupported],
)
def test_unsupported_route_rejects_explicitly(
    axis: str, route: str, tmp_path: Path
) -> None:
    """A route that cannot honor a setting must refuse it (usage error), never
    run and silently ignore it (#1233/#1195 capability-guard class)."""
    ops = write_operands(tmp_path, "removal_and_addition")
    a = AXES_BY_NAME[axis]
    assert route == "release"
    r = CliRunner().invoke(
        main,
        [
            "compare",
            str(ops.old_dir),
            str(ops.new_dir),
            *a.cli(tmp_path),
            "-o",
            "json=-",
        ],
    )
    assert r.exit_code == 64, r.output
    assert "not supported" in r.output


def test_release_reports_the_probe_matrix_at_release_level(tmp_path: Path) -> None:
    """The ``probe_matrix`` axis's release placement: the same finding the
    CLI reports, in the summary's release-level ``matrix_findings``, and the
    same process exit code."""
    from _family_f2_routes import _probe_matrix_args

    ops = write_operands(tmp_path, "identical")
    cli = run_cli(ops, AXES_BY_NAME["probe_matrix"], tmp_path)
    summary = tmp_path / "release_summary.json"
    r = _invoke(
        [
            "compare",
            str(ops.old_dir),
            str(ops.new_dir),
            *_probe_matrix_args(tmp_path),
            "-o",
            f"json={summary}",
        ]
    )
    doc = json.loads(summary.read_text(encoding="utf-8"))
    cli_kinds = sorted(c["kind"] for c in cli.report["changes"])
    release_kinds = sorted(f["kind"] for f in doc["matrix_findings"])
    assert release_kinds == cli_kinds == ["cxx_standard_floor_raised"]
    assert r.exit_code == cli.exit_code


# ── known divergences: pinned, not normalized away ───────────────────────


def _divergence_axis(key: str) -> str:
    return "suppress" if key == "suppression_audit" else "default"


@pytest.mark.parametrize(
    ("key", "route"),
    [
        (k, r)
        for k, (routes, _) in sorted(KNOWN_DIVERGENCES.items())
        for r in sorted(routes)
    ],
)
def test_known_divergence_still_diverges(
    key: str, route: str, tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    request.applymarker(
        pytest.mark.xfail(
            strict=True,
            raises=AssertionError,
            reason=f"REAL DIVERGENCE {key} ({route}): {KNOWN_DIVERGENCES[key][1]}",
        )
    )
    outs = _run_all(
        "removal_and_addition",
        AXES_BY_NAME[_divergence_axis(key)],
        tmp_path,
        {"cli": run_cli, route: ROUTES[route]},
    )
    assert outs["cli"].report.get(key) == outs[route].report.get(key)


def test_route_specific_tables_carry_reasons() -> None:
    for table in (ROUTE_SPECIFIC_FIELDS, ROUTE_SPECIFIC_PATHS, STORED_VS_LIVE_PATHS):
        assert table and all(isinstance(r, str) and len(r) > 20 for r in table.values())
    assert all(
        len(reason) > 20 and routes for routes, reason in KNOWN_DIVERGENCES.values()
    )
    assert not set(KNOWN_DIVERGENCES) & set(ROUTE_SPECIFIC_FIELDS)


def test_strict_oracle_sees_the_known_divergences(tmp_path: Path) -> None:
    """Stripping KNOWN_DIVERGENCES is what makes the matrix pass -- the
    unstripped oracle must report them, so the table is not decorative."""
    outs = _run_all("removal_and_addition", AXES_BY_NAME["default"], tmp_path)
    strict = parity_violations(outs["cli"], outs["api"], strip_known=False)
    if not KNOWN_DIVERGENCES:
        # Nothing is stripped, so the strict oracle is the enforcing one.
        assert strict == []
        return
    assert strict and all(
        any(f"[{k!r}]" in line for k in KNOWN_DIVERGENCES) for line in strict
    )


# ── seeded mutants: the oracle must catch historical bugs ────────────────


def test_mutant_release_member_drops_stated_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1391 shape: the release fan-out builds its per-member request and
    drops a field the caller stated. Here: suppress and policy."""
    import abicheck.workflows.member_compare as member_compare

    real = member_compare.run_compare_request

    def dropping(request: Any, *args: Any, **kwargs: Any) -> Any:
        dropped = dataclasses.replace(request, suppress=None, policy="strict_abi")
        return real(dropped, *args, **kwargs)

    monkeypatch.setattr(member_compare, "run_compare_request", dropping)
    caught = []
    for axis in ("suppress", "policy_sdk_vendor"):
        outs = _run_all(
            "removal_and_addition",
            AXES_BY_NAME[axis],
            tmp_path / axis,
            {"cli": run_cli, "release": run_release_member},
        )
        caught.append(bool(matrix_violations(outs)))
    assert all(caught), caught


def test_mutant_typed_api_default_flipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1258/#1264 shape: a typed-API default diverges from the CLI's. Caught
    both declaratively (default agreement) and behaviorally (matrix)."""
    for owner, name, flipped in (
        (CompareRequest, "scope_public", False),
        (InputSpec, "include_dependencies", True),
    ):
        with monkeypatch.context() as m:
            m.setattr(owner.__dataclass_fields__[name], "default", flipped)  # type: ignore[attr-defined]
            assert default_agreement_violations(), name

    def flipped_request(**kw: Any) -> CompareRequest:
        kw.setdefault("scope_public", False)
        return CompareRequest(**kw)

    ops = write_operands(tmp_path, "removal_and_addition")
    axis = AXES_BY_NAME["default"]
    cli = run_cli(ops, axis, tmp_path)
    api = run_api(ops, axis, tmp_path, request_factory=flipped_request)
    assert parity_violations(cli, api)


def test_mutant_release_finding_entry_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1176/#1326 shape: a release summary entry disagrees with the scalar
    finding on a shared field."""
    ops = write_operands(tmp_path, "removal_and_addition")
    axis = AXES_BY_NAME["default"]
    cli = run_cli(ops, axis, tmp_path)
    rel = run_release_member(ops, axis, tmp_path)
    assert not release_finding_entry_violations(cli, rel)
    lib = json.loads(json.dumps(rel.extra["library"]))
    lib["findings"][0]["description"] = "drifted"
    drifted = Outcome("release", rel.report, rel.exit_code, {"library": lib})
    assert release_finding_entry_violations(cli, drifted)


# ── stored vs live operands (needs gcc + castxml) ───────────────────────


def _build(tmp: Path, side: str, header: str, source: str) -> tuple[Path, Path]:
    d = tmp / side
    d.mkdir()
    (d / "foo.h").write_text(header, encoding="utf-8")
    (d / "foo.c").write_text(source, encoding="utf-8")
    so = d / "libfoo.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-g", "-o", str(so), str(d / "foo.c")], check=True
    )
    return so, d / "foo.h"


@pytest.mark.integration
def test_stored_snapshot_vs_live_binary(tmp_path: Path) -> None:
    """#1236 class: comparing dumped snapshots must decide exactly what
    comparing the live binaries with the same headers decides."""
    if not (shutil.which("gcc") and shutil.which("castxml")):
        pytest.skip("gcc/castxml not available")
    old_so, old_h = _build(
        tmp_path,
        "old",
        "int foo(void);\nint bar(int);\n",
        "int foo(void){return 1;}\nint bar(int x){return x;}\n",
    )
    new_so, new_h = _build(
        tmp_path,
        "new",
        "long foo(void);\nint baz(void);\n",
        "long foo(void){return 1;}\nint baz(void){return 2;}\n",
    )
    runner = CliRunner()
    for so, h, v, out in (
        (old_so, old_h, "1.0", tmp_path / "old.json"),
        (new_so, new_h, "2.0", tmp_path / "new.json"),
    ):
        r = runner.invoke(
            main, ["dump", str(so), "-H", str(h), "--label", v, "-o", str(out)]
        )
        assert r.exit_code == 0, r.output
    live = runner.invoke(
        main,
        [
            "compare",
            str(old_so),
            str(new_so),
            "-H",
            f"old={old_h}",
            "-H",
            f"new={new_h}",
            "--version",
            "old=1.0",
            "--version",
            "new=2.0",
            "-o",
            "json=-",
        ],
    )
    stored = runner.invoke(
        main,
        [
            "compare",
            str(tmp_path / "old.json"),
            str(tmp_path / "new.json"),
            "-o",
            "json=-",
        ],
    )
    a = Outcome("cli", json.loads(live.stdout), live.exit_code)
    b = Outcome("stored", json.loads(stored.stdout), stored.exit_code)
    assert a.verdict == "BREAKING"
    violations = parity_violations(a, b, extra_paths=STORED_VS_LIVE_PATHS)
    assert not violations, "\n".join(violations)


# ── stored vs live: every persisted ELF symbol fact survives the round trip ──


def _elf_snapshot(version: str, symbols: list[Any]) -> Any:
    from abicheck.model import AbiSnapshot
    from abicheck.model.elf_facts import ElfMetadata

    return AbiSnapshot(
        library="libown.so.1",
        version=version,
        elf=ElfMetadata(soname="libown.so.1", symbols=symbols),
        elf_only_mode=True,
    )


@pytest.mark.parametrize(
    "origins",
    [
        ("libstdc++.so.6", None),
        (None, "libstdc++.so.6"),
        ("libgcc_s.so.1", "libc.so.6"),
    ],
)
def test_stored_snapshot_keeps_dependency_origin_findings(
    origins: tuple[str | None, str | None],
) -> None:
    """serialization.persisted_field_not_decoded: a snapshot read back from
    storage (a cache hit, a saved baseline) must decide what the freshly
    parsed one decides. ``origin_lib`` was written but never decoded, so the
    stored route silently lost every dependency-leak finding."""
    from abicheck.checker import compare
    from abicheck.model.elf_facts import ElfSymbol
    from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

    old_origin, new_origin = origins
    old = _elf_snapshot(
        "1", [ElfSymbol(name="keep"), ElfSymbol(name="gone", origin_lib=old_origin)]
    )
    new = _elf_snapshot(
        "2", [ElfSymbol(name="keep"), ElfSymbol(name="added", origin_lib=new_origin)]
    )

    def kinds(o: Any, n: Any) -> list[tuple[str, str]]:
        return sorted((c.kind.value, c.symbol) for c in compare(o, n).changes)

    live = kinds(old, new)
    stored = kinds(
        snapshot_from_dict(snapshot_to_dict(old)),
        snapshot_from_dict(snapshot_to_dict(new)),
    )
    assert stored == live
    leaked = {s for k, s in live if k == "symbol_leaked_from_dependency_changed"}
    expected = {s for s, o in (("gone", old_origin), ("added", new_origin)) if o}
    assert leaked == expected


# --------------------------------------------------------------------------
# Hybrid dump routes: `service.run_dump` and `dumper.dump` legs see one scope
# --------------------------------------------------------------------------


def test_hybrid_dump_routes_parse_their_legs_alike(tmp_path: Path) -> None:
    """Bug class ``extraction.recorded_scope_matches_parse_skip``: the CLI's
    hybrid route (``service.run_dump``) and ``dumper.dump``'s hybrid route
    must hand their two backend legs the same parse-time state under the same
    scoped request. The CLI route once kept the enclosing dependency skip
    that ``dumper.dump``'s route turned off."""
    from unittest.mock import patch

    from _dump_format_fakes import fake_format_adapter

    from abicheck.extract.dependency_exclusion import (
        active_dependency_predicate,
        dependency_exclusion_scope,
    )
    from abicheck.extract.headers.clang.streaming import streaming_prune_suppressed
    from abicheck.model import AbiSnapshot
    from abicheck.workflows.dump import hybrid as dumper_hybrid

    def _state() -> tuple[bool, bool]:
        return (active_dependency_predicate() is None, streaming_prune_suppressed())

    so = tmp_path / "lib.so"
    so.write_bytes(b"\x7fELF" + b"\x00" * 100)
    header = tmp_path / "api.h"
    header.write_text("int f(void);\n", encoding="utf-8")

    cli_legs: dict[str, tuple[bool, bool]] = {}

    def _fake_dump_elf(request: Any) -> AbiSnapshot:
        frontend = request.compile.frontend
        cli_legs[frontend] = _state()
        return AbiSnapshot(
            library="l", version="1", from_headers=True, ast_producer=frontend
        )

    with (
        fake_format_adapter("elf", side_effect=_fake_dump_elf),
        patch(
            "abicheck.workflows.dump.native._attach_header_graph",
            side_effect=lambda snap, *_a, **_k: snap,
        ),
    ):
        service_mod.run_dump(
            so, "elf", headers=[header], header_backend="hybrid"
        )  # the default, scoped request

    dumper_legs: dict[str, tuple[bool, bool]] = {}

    def _fake_leg(_so: Path, _headers: list[Path], *, header_backend: str) -> Any:
        dumper_legs[header_backend] = (active_dependency_predicate() is None, True)
        return AbiSnapshot(library="l", version="1")

    with (
        patch.object(dumper_hybrid, "merge_snapshots", lambda a, _b: a),
        dependency_exclusion_scope([str(tmp_path)]),
    ):
        dumper_hybrid.run_hybrid_dump(_fake_leg, so, [header])

    assert set(cli_legs) == set(dumper_legs) == {"castxml", "clang"}
    for backend in cli_legs:
        # Same oracle on both routes: no dependency skip reaches either leg.
        assert cli_legs[backend][0] is dumper_legs[backend][0] is True, backend
    # The CLI legs are full-surface requests, so the streaming pruner is off.
    assert all(prune_off for _skip_off, prune_off in cli_legs.values())


# ── evidence.narrowed_conclusion_not_carried_across_cardinality ─────────────


@pytest.mark.parametrize("entity", ["function", "variable"])
@pytest.mark.parametrize("narrowed", [True, False])
def test_member_and_release_routes_agree_on_obligation_confidence(
    entity: str, narrowed: bool
) -> None:
    """Scalar member check vs release reconciliation: one declaration under
    one recorded scope gets one confidence on both routes (full domain in
    tests/test_release_obligation_standing.py)."""
    from test_release_obligation_standing import _release_findings, _snapshot

    from abicheck.buildsource.cross_source_checks import (
        CrosscheckConfig,
        _check_public_not_exported,
    )
    from abicheck.model.evidence_status import Confidence
    from abicheck.workflows.release_surface_acquisition import (
        surface_from_snapshot,
    )

    rule = "no_root" if narrowed else "target_root:include/pvxs/iochooks.h"
    snap = _snapshot(("include/pvxs/iochooks.h",), "unresolved", rule, entity)
    member = [
        c.confidence
        for c in _check_public_not_exported(snap, CrosscheckConfig()).findings
    ]
    surface = surface_from_snapshot(snap, acquisition_key="k", side="new")
    release = [c.confidence for c in _release_findings(surface).findings]
    assert member == release == [Confidence.LOW if narrowed else Confidence.HIGH]


# ── evidence.producer_drops_facts_its_input_holds ───────────────────────────

_SEMANTIC_HEADER = """\
#pragma once
namespace cell {
struct Impl { int v; };
template <typename T> struct Box { T value; T get() const { return value; } };
struct Base { virtual ~Base(); virtual int f(int) const; };
struct Derived : Base { int f(int) const override; };
typedef void (*handler_t)(int);
void reg(handler_t h);
void on_event(int);
struct Api { Box<Impl> held; };
inline int call(const Base& b) { return b.f(1); }
inline void setup() { reg(&on_event); }
}
"""


def _semantic_route_edges(tmp_path: Path) -> tuple[frozenset, frozenset]:
    """One header through the default L2 header route and the L5 route;
    each route's override/template/callback/dispatch edges (full domain in
    tests/test_header_graph_semantic_facts.py)."""
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.header_graph import build_header_only_graph
    from abicheck.buildsource.l5_ast_pass import fold_semantic_graphs
    from abicheck.extract.headers.sysinc import _resolve_clang_system_includes
    from abicheck.model import AbiSnapshot
    from abicheck.model.source_graph import SourceGraphSummary
    from abicheck.model.source_graph_coverage import SEMANTIC_FAMILIES
    from abicheck.service_header_graph_attach import acquire_header_graph_ast

    kinds = frozenset().union(*(f.edge_kinds for f in SEMANTIC_FAMILIES))
    header = tmp_path / "api.h"
    header.write_text(_SEMANTIC_HEADER, encoding="utf-8")
    acquired = acquire_header_graph_ast([header], [], None, None)
    assert acquired.ast_failure is None, acquired.ast_failure
    l2 = build_header_only_graph(
        AbiSnapshot(library="l", version="1"), ast_projection=acquired.projection
    )
    tu = tmp_path / "tu.cpp"
    tu.write_text('#include "api.h"\n', encoding="utf-8")
    system_dirs = _resolve_clang_system_includes(
        "c++", gcc_path=None, gcc_prefix=None, sysroot=None, nostdinc=False,
        force_cpp=True,
    )  # fmt: skip
    l5 = SourceGraphSummary()
    unit = CompileUnit(
        id="cu://tu", source=str(tu), directory=str(tmp_path), language="CXX",
        include_paths=[str(tmp_path)], system_include_paths=list(system_dirs),
    )  # fmt: skip
    fold_semantic_graphs(l5, BuildEvidence(compile_units=[unit]), "clang++", [])

    def edges(g: SourceGraphSummary) -> frozenset:
        return frozenset((e.kind, e.src, e.dst) for e in g.edges if e.kind in kinds)

    return edges(l2), edges(l5)


@pytest.mark.skipif(shutil.which("clang++") is None, reason="needs clang++")
def test_header_and_l5_routes_derive_the_same_semantic_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    l2, l5 = _semantic_route_edges(tmp_path)
    assert {k for k, _s, _d in l5} >= {
        "METHOD_POSSIBLE_OVERRIDE",
        "DECL_INSTANTIATES_TEMPLATE",
        "DECL_REGISTERS_CALLBACK",
    }
    assert l2 == l5, (sorted(l2 - l5)[:10], sorted(l5 - l2)[:10])


@pytest.mark.skipif(shutil.which("clang++") is None, reason="needs clang++")
def test_mutant_header_route_drops_the_semantic_folds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pre-fix shape: the header route keeps type/call edges only."""
    from abicheck.buildsource import header_graph_ast_projection

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(
        header_graph_ast_projection, "fold_header_semantic_facts", lambda *_a: None
    )
    l2, l5 = _semantic_route_edges(tmp_path)
    assert l5 and not l2
    assert l2 != l5


@pytest.mark.parametrize("header_backend", ["auto", "castxml", "clang"])
def test_dump_write_and_resolution_routes_replay_l4_alike(
    header_backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bug class ``extraction.default_extractor_lacks_the_promised_fact_family``:
    ``dump``'s write-time embed (``_make_source_extractor(header_backend)``)
    and the shared resolution's (``source_replay_extractor``) instantiate the
    same L4 extractor. Under ``auto`` the first was clang and the second
    castxml, so a C tree's macros reached ``dump`` and not the typed API."""
    from abicheck.buildsource.inline import _make_source_extractor
    from abicheck.service_compare_evidence import source_replay_extractor

    monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
    written, _ = _make_source_extractor(header_backend, "clang")
    resolved, _ = _make_source_extractor(
        source_replay_extractor(None, header_backend), "clang"
    )
    assert type(written) is type(resolved)


@pytest.fixture(autouse=True)
def _castxml_preflight_off(monkeypatch):
    """These tests stub the parse itself; castxml need not be on PATH (#1710)."""
    monkeypatch.setattr(
        "abicheck.workflows.header_frontend_preflight.require_castxml_if_needed",
        lambda *a, **k: None,
    )
