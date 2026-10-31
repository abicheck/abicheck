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

"""Route runners, operand corpus and oracle for harness H2 (family F2).

See ``docs/contribute/plans/defect-family-harnesses.md`` § "H2: route-parity
matrix". One *semantic* compare request is expressed through several routes
(native ``compare`` CLI, typed ``CompareRequest`` API, a one-member directory
release fan-out with the member projected out); every route must produce the
same canonical JSON report once :data:`ROUTE_SPECIFIC_FIELDS` -- the only
place a divergence may be *declared* -- has been removed.

:data:`KNOWN_DIVERGENCES` is deliberately separate: it lists divergences this
harness *found* and that are not (yet) justified as route-specific. The main
matrix strips them so the rest of the report is still checked, and each one
has its own ``xfail(strict=True)`` cell in the test module, so fixing one
turns that cell XPASS -> failure and forces the entry's removal.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from _legacy_scope import no_scope_config_args
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.comparability import _MISMATCH_ERRORS
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    ScopeOrigin,
    Variable,
    Visibility,
)
from abicheck.report.diff_report import to_json
from abicheck.serialization import snapshot_to_json
from abicheck.service import CompareRequest, InputSpec
from abicheck.workflows.compare.execute import run_compare_request

LIB = "libfoo.so"

#: Top-level report keys that legitimately differ by route. Each carries its
#: reason; nothing else may be dropped by the oracle.
ROUTE_SPECIFIC_FIELDS: dict[str, str] = {
    "old_file": "the operand path spelling: a release member is addressed "
    "as <dir>/<member>, a scalar operand by its own path",
    "new_file": "the operand path spelling, as for old_file: a release member "
    "is addressed as <dir>/<member>, a scalar operand by its own path",
}

#: Nested report paths (dotted) that legitimately differ by route.
ROUTE_SPECIFIC_PATHS: dict[tuple[str, ...], str] = {
    ("evidence_metrics", "extractor.duration_seconds"): "wall-clock timing of "
    "the extractor; differs between any two runs, whatever the route",
    ("contract_context", "evaluation_context", "field_provenance"): "records *which "
    "front end* stated each value (explicit_cli vs api_request layer); "
    "ADR-049's cross_front_end_differences() is explicitly 'modulo which "
    "front end stated a value' -- the resolved values are compared elsewhere "
    "in contract_context",
}

#: Divergences between routes the harness found that are *not* justified as
#: route-specific. ``key -> (routes that differ from the CLI, explanation)``.
#: Each is asserted to still diverge by an xfail(strict) cell.
#: Empty since the four entries this harness first found were fixed at their
#: owner: ``old_evidence_depth``/``new_evidence_depth`` and
#: ``suppression_audit`` moved from the native CLI into the shared Tier-2
#: pipeline (``workflows.analysis_assurance_attach.attach_evidence_depths``,
#: ``workflows.suppression_audit_attach``), and ``effective_config_fields``/
#: ``effective_config_digest`` agree because the typed API now resolves a
#: ``CompatibilityEvaluationConfig`` exactly when the CLI does (contract or
#: pack), leaving a plain run on the documented baseline tier.
KNOWN_DIVERGENCES: dict[str, tuple[frozenset[str], str]] = {}


# ── operand corpus ───────────────────────────────────────────────────────


def _fn(name: str, ret: str = "int", params: tuple[str, ...] = ()) -> Function:
    return Function(
        name=name,
        mangled=name,
        return_type=ret,
        params=[Param(name=f"p{i}", type=t) for i, t in enumerate(params)],
        visibility=Visibility.PUBLIC,
    )


def _var(name: str, type_: str = "int") -> Variable:
    return Variable(name=name, mangled=name, type=type_, visibility=Visibility.PUBLIC)


def _snap(
    version: str, funcs: list[Function], vars_: list[Variable] = ()
) -> AbiSnapshot:  # type: ignore[assignment]
    return AbiSnapshot(
        library=LIB,
        version=version,
        functions=list(funcs),
        variables=list(vars_),
        from_headers=True,
    )


#: Exports no public header declares (manifest class
#: ``report.cross_producer_disagreement_on_one_symbol``): one appears, one
#: disappears, one persists. Each export event must reach a report as ONE
#: existence finding on every route, with the hygiene twin folded into it.
UNDECLARED_INIT = "_ZN3foo2v14initEv"
UNDECLARED_ADDED = "_ZN3foo2v15leakyEv"
UNDECLARED_REMOVED = "_ZN3foo6detail6helperEv"
UNDECLARED_PERSISTENT = "_ZN3foo6detail4keepEv"


def _undeclared_side(version: str, exports: tuple[str, ...]) -> AbiSnapshot:
    init = Function(
        name="init",
        mangled=UNDECLARED_INIT,
        return_type="void",
        visibility=Visibility.PUBLIC,
        origin=ScopeOrigin.PUBLIC_HEADER,
        source_header="include/foo.h",
    )
    snap = AbiSnapshot(
        library=LIB, version=version, from_headers=True, functions=[init]
    )
    snap.elf = ElfMetadata(
        machine="EM_X86_64",
        symbols=[
            ElfSymbol(name=n, binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC)
            for n in (UNDECLARED_INIT, *exports)
        ],
    )
    return snap


def _corpus() -> dict[str, tuple[AbiSnapshot, AbiSnapshot]]:
    base = [_fn("foo"), _fn("bar", params=("int",))]
    return {
        "identical": (_snap("1.0", base), _snap("2.0", base)),
        "removal_and_addition": (
            _snap("1.0", base),
            _snap("2.0", [_fn("bar", params=("int",)), _fn("baz")]),
        ),
        "addition_only": (_snap("1.0", base), _snap("2.0", [*base, _fn("qux")])),
        "signature_change": (
            _snap("1.0", base),
            _snap("2.0", [_fn("foo", ret="long"), _fn("bar", params=("int", "int"))]),
        ),
        "variable_removed": (
            _snap("1.0", base, [_var("gv"), _var("keep")]),
            _snap("2.0", base, [_var("keep")]),
        ),
        "undeclared_export_churn": (
            _undeclared_side("1.0", (UNDECLARED_REMOVED, UNDECLARED_PERSISTENT)),
            _undeclared_side("2.0", (UNDECLARED_ADDED, UNDECLARED_PERSISTENT)),
        ),
        # Not comparable (filtered vs full dependency scope): exercises the
        # comparability gate and --diagnostic-comparison's escape hatch.
        "scope_mismatch": (
            dataclasses.replace(_snap("1.0", base), dependency_scope="filtered"),
            dataclasses.replace(_snap("2.0", [_fn("baz")]), dependency_scope="full"),
        ),
    }


CORPUS = _corpus()


@dataclass(frozen=True)
class Operands:
    old: Path
    new: Path
    old_dir: Path
    new_dir: Path
    #: Live sides only (a ``.so`` operand): the public header parsed with the
    #: binary, and the version the dump was stamped with. A stored snapshot
    #: side carries both inside the JSON, so it leaves them unset.
    old_header: Path | None = None
    new_header: Path | None = None
    old_version: str = ""
    new_version: str = ""

    def side_args(self) -> list[str]:
        """The per-side CLI spelling of a live side's header and version."""
        args: list[str] = []
        for label, header, version in (
            ("old", self.old_header, self.old_version),
            ("new", self.new_header, self.new_version),
        ):
            if header is not None:
                args += ["-H", f"{label}={header}"]
            if version:
                args += ["--label", f"{label}={version}"]
        return args


def write_operands(tmp: Path, case: str) -> Operands:
    """Stored-snapshot operands for *case*, laid out so the same files are
    both scalar operands and the sole member of a directory operand."""
    old_snap, new_snap = CORPUS[case]
    old_dir, new_dir = tmp / "old", tmp / "new"
    old_dir.mkdir(parents=True, exist_ok=True)
    new_dir.mkdir(parents=True, exist_ok=True)
    old = old_dir / "libfoo.json"
    new = new_dir / "libfoo.json"
    old.write_text(snapshot_to_json(old_snap), encoding="utf-8")
    new.write_text(snapshot_to_json(new_snap), encoding="utf-8")
    return Operands(old, new, old_dir, new_dir)


# ── axes: one semantic setting, spelled once per route ──────────────────


@dataclass(frozen=True)
class Axis:
    """A non-default setting expressed for each route.

    ``cli`` builds extra CLI args (also used by the release route, which is
    the same command with directory operands); ``api`` builds extra
    ``CompareRequest`` kwargs. ``click_params``/``request_fields`` name what
    the axis exercises; the completeness test cross-checks them against
    ``PARAM_ROUTING``.
    """

    name: str
    cli: Callable[[Path], list[str]]
    api: Callable[[Path], dict[str, Any]]
    click_params: tuple[str, ...]
    request_fields: tuple[str, ...]
    render: Mapping[str, Any] = field(default_factory=dict)
    #: route -> reason the route explicitly rejects this setting (exit 64).
    unsupported: Mapping[str, str] = field(default_factory=dict)
    #: route -> where that route reports this setting's effect instead of the
    #: projected per-member report (checked by its own dedicated test).
    reported_elsewhere: Mapping[str, str] = field(default_factory=dict)
    #: ``.abicheck.yml`` keys the CLI half sets instead of a Click parameter
    #: -- for a setting whose flag was retired into config (e.g.
    #: ``scope.public``, one-comparison-product Phase 9b).
    config_keys: tuple[str, ...] = ()


def _suppress_file(tmp: Path) -> Path:
    p = tmp / "suppress.yaml"
    p.write_text(
        "version: 1\nsuppressions:\n  - symbol: foo\n    reason: parity harness\n  - symbol: gv\n    reason: parity harness\n",
        encoding="utf-8",
    )
    return p


def _strict_suppression_args(tmp: Path) -> list[str]:
    """``--suppress`` plus a config turning on ``suppression.strict`` and
    ``suppression.require_justification`` (every rule above has a reason and
    no expiry, so both are satisfied and the findings must be identical)."""
    cfg = tmp / "strict-suppression.abicheck.yml"
    cfg.write_text(
        "suppression:\n  strict: true\n  require_justification: true\n",
        encoding="utf-8",
    )
    return ["--suppress", str(_suppress_file(tmp)), "--config", str(cfg)]


def _probe_matrices(tmp: Path) -> tuple[Path, Path]:
    """Two build-configuration matrices whose only delta is a raised C++
    standard floor (17 -> 20): a ``cxx_standard_floor_raised`` finding."""
    out = []
    for name, version, stds in (
        ("pm_old.json", "1.0", {"a": 17, "b": 20}),
        ("pm_new.json", "2.0", {"b": 20, "c": 23}),
    ):
        path = tmp / name
        path.write_text(
            json.dumps(
                {
                    "library": "libfoo",
                    "version": version,
                    "spec_name": "libfoo",
                    "cxx_stds": stds,
                    "defaults": {"backend": "tbb"},
                    "results": [],
                }
            ),
            encoding="utf-8",
        )
        out.append(path)
    return out[0], out[1]


def _probe_matrix_args(tmp: Path) -> list[str]:
    old, new = _probe_matrices(tmp)
    return ["--build-info", f"old={old}", "--build-info", f"new={new}"]


def _probe_matrix_fields(tmp: Path) -> dict[str, Any]:
    old, new = _probe_matrices(tmp)
    return {"old_probe_matrix": old, "new_probe_matrix": new}


def _policy_file(tmp: Path) -> Path:
    p = tmp / "policy.yaml"
    p.write_text(
        "base_policy: strict_abi\noverrides:\n  func_removed: risk\n  var_removed: risk\n",
        encoding="utf-8",
    )
    return p


AXES: tuple[Axis, ...] = (
    Axis("default", lambda t: [], lambda t: {}, (), ()),
    Axis(
        "policy_sdk_vendor",
        lambda t: ["--policy", "sdk_vendor"],
        lambda t: {"policy": "sdk_vendor"},
        ("policy",),
        ("policy",),
    ),
    Axis(
        "policy_file",
        lambda t: ["--policy", str(_policy_file(t))],
        lambda t: {"policy": _policy_file(t)},
        ("policy",),
        ("policy",),
    ),
    Axis(
        "suppress",
        lambda t: ["--suppress", str(_suppress_file(t))],
        lambda t: {"suppress": _suppress_file(t)},
        ("suppress",),
        ("suppress",),
    ),
    Axis(
        "suppress_strict",
        _strict_suppression_args,
        lambda t: {
            "suppress": _suppress_file(t),
            "strict_suppressions": True,
            "require_justification": True,
        },
        ("suppress",),
        ("suppress", "strict_suppressions", "require_justification"),
        config_keys=("suppression.strict", "suppression.require_justification"),
    ),
    Axis(
        "probe_matrix",
        _probe_matrix_args,
        _probe_matrix_fields,
        ("build_info",),
        ("old_probe_matrix", "new_probe_matrix"),
        reported_elsewhere={
            "release": "a matrix describes the release, not one member: the "
            "release fan-out reports it in the summary's release-level "
            "matrix_findings/matrix_verdict, not in a member's report"
        },
    ),
    Axis(
        "no_scope_public",
        # --no-scope-public-headers was retired (Phase 9b); scope.public:
        # false is the setting it overrode.
        no_scope_config_args,
        lambda t: {"scope_public": False},
        (),
        ("scope_public",),
        config_keys=("scope.public",),
    ),
    Axis(
        "severity_strict",
        lambda t: ["--set", "severity.preset=strict"],
        lambda t: {"severity_preset": "strict"},
        ("severity_preset",),
        ("severity_preset",),
    ),
    Axis(
        "diagnostic_comparison",
        lambda t: ["--diagnostic-comparison"],
        lambda t: {"diagnostic_comparison": True},
        ("diagnostic_comparison",),
        ("diagnostic_comparison",),
        unsupported={
            "release": "the per-library fan-out rejects --diagnostic-comparison "
            "with a usage error (capability guard, #1233 family)"
        },
    ),
    Axis(
        "contract_exports",
        lambda t: ["--contract", "exports"],
        lambda t: {"contract": "exports"},
        ("contract_mode",),
        ("contract",),
        render={"contract_evaluation": True},
    ),
)
AXES_BY_NAME = {a.name: a for a in AXES}


# ── routes ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Outcome:
    route: str
    report: dict[str, Any]
    exit_code: int
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def verdict(self) -> str:
        return str(self.report.get("verdict"))

    @property
    def not_comparable(self) -> str | None:
        """The comparability refusal's reason, however the route spells it:
        the scalar report's ``reason`` + ``operational: not_comparable``, a
        release member's ``verdict: not_comparable`` entry, or the typed API's
        documented comparability exception."""
        if NOT_COMPARABLE_KEY in self.report:
            return str(self.report[NOT_COMPARABLE_KEY])
        if (self.report.get("run_outcome") or {}).get(
            "operational"
        ) == "not_comparable":
            reason = self.report.get("reason")
            return str(reason.get("message") if isinstance(reason, dict) else reason)
        return None


#: Marker key for a route whose native spelling of "not comparable" is not a
#: full report (a raised exception, a release summary entry).
NOT_COMPARABLE_KEY = "__not_comparable__"

#: The verdict a release run reports when it paired no member at all.
NO_COMPARISON_COMPLETED = "no_comparison_completed"
_COMPARABILITY_ERRORS = tuple(set(_MISMATCH_ERRORS.values()))


def _invoke(args: list[str]) -> Any:
    return CliRunner().invoke(main, args, catch_exceptions=False)


def run_cli(ops: Operands, axis: Axis, tmp: Path) -> Outcome:
    r = _invoke(
        [
            "compare",
            str(ops.old),
            str(ops.new),
            *ops.side_args(),
            *axis.cli(tmp),
            "-o",
            "json=-",
        ]
    )
    return Outcome("cli", json.loads(r.stdout), r.exit_code)


def _input_spec(path: Path, header: Path | None, version: str) -> InputSpec:
    return InputSpec(
        path=path, headers=(header,) if header is not None else (), version=version
    )


def run_api(
    ops: Operands,
    axis: Axis,
    tmp: Path,
    *,
    request_factory: Callable[..., CompareRequest] = CompareRequest,
) -> Outcome:
    # The native CLI hard-codes pattern_verdicts=True (see the xfail default
    # cell in the test module); the *semantic* request the CLI makes is
    # therefore pattern_verdicts=True, and the API cell states it.
    kwargs: dict[str, Any] = {"pattern_verdicts": True, **axis.api(tmp)}
    req = request_factory(
        old=_input_spec(ops.old, ops.old_header, ops.old_version),
        new=_input_spec(ops.new, ops.new_header, ops.new_version),
        **kwargs,
    )
    try:
        res = run_compare_request(req)
    except _COMPARABILITY_ERRORS as exc:
        # The typed API's documented spelling of "not comparable": it has no
        # exit code, so the oracle compares the reason only (exit_code=-1).
        return Outcome("api", {NOT_COMPARABLE_KEY: str(exc)}, -1)
    report = json.loads(
        to_json(res.diff, severity_config=res.severity_config, **dict(axis.render))
    )
    code = (
        res.exit_decision.code
        if res.exit_decision is not None
        else report["exit"]["code"]
    )
    return Outcome("api", report, code)


def run_release_member(ops: Operands, axis: Axis, tmp: Path) -> Outcome:
    """Directory operands holding exactly one member; the member's complete
    per-library report is projected out (``-o json=<dir>/``)."""
    summary = tmp / "release_summary.json"
    reports = tmp / "release_reports"
    r = _invoke(
        [
            "compare",
            str(ops.old_dir),
            str(ops.new_dir),
            *axis.cli(tmp),
            *ops.side_args(),
            "-o",
            f"json={summary}",
            "-o",
            f"json={reports}/",
        ]
    )
    doc = json.loads(summary.read_text(encoding="utf-8"))
    if not doc["libraries"]:
        # No member paired on the two sides (e.g. a stored .json directory
        # against a live .so directory): the release run completed zero
        # comparisons. Its own outcome, never an assertion failure.
        return Outcome(
            "release",
            {"verdict": NO_COMPARISON_COMPLETED, "run_outcome": doc.get("run_outcome")},
            r.exit_code,
            {"summary": doc},
        )
    assert len(doc["libraries"]) == 1, doc
    lib = doc["libraries"][0]
    if lib.get("verdict") == "not_comparable":
        return Outcome(
            "release",
            {NOT_COMPARABLE_KEY: lib["reason"]},
            r.exit_code,
            {"summary": doc, "library": lib},
        )
    member = json.loads(Path(lib["complete_report"]).read_text(encoding="utf-8"))
    return Outcome("release", member, r.exit_code, {"summary": doc, "library": lib})


# ── operand forms and the derived route matrix ───────────────────────────
#
# A *route* is one entry point (``ENTRY_POINTS``) reading one operand form
# (``OPERAND_FORMS``). The matrix is the product minus ``ROUTE_EXCEPTIONS``,
# so adding an entry point or a form adds its routes without touching the
# matrix tests, and a cell a route cannot take is declared with its reason
# rather than silently absent.

#: Operand forms. ``stored``: both sides ``abicheck dump`` snapshots. ``live``:
#: both sides the binary plus its header. ``mixed_old``/``mixed_new``: one side
#: stored, the other live (the stored/live split the #1236/#1715 classes
#: broke on, one side at a time).
OPERAND_FORMS: tuple[str, ...] = ("stored", "live", "mixed_old", "mixed_new")

#: Entry point name -> the runner that takes :class:`Operands`.
ENTRY_POINTS: dict[str, Callable[[Operands, Axis, Path], Outcome]] = {
    "cli": run_cli,
    "api": run_api,
    "release": run_release_member,
}

#: ``(entry point, operand form)`` cells that entry point cannot take, each
#: with the reason. Empty: every entry point accepts every form.
ROUTE_EXCEPTIONS: dict[tuple[str, str], str] = {}


@dataclass(frozen=True)
class Route:
    entry: str
    form: str
    run: Callable[[Operands, Axis, Path], Outcome]

    @property
    def name(self) -> str:
        return self.entry if self.form == "stored" else f"{self.entry}@{self.form}"


#: Every route, keyed by its name. ``cli``/``api``/``release`` (the stored
#: form) keep their historical names.
ROUTE_MATRIX: dict[str, Route] = {
    route.name: route
    for route in (
        Route(entry, form, run)
        for entry, run in ENTRY_POINTS.items()
        for form in OPERAND_FORMS
        if (entry, form) not in ROUTE_EXCEPTIONS
    )
}

#: Route cells that diverge from the stored CLI reference and are not fixed:
#: ``route name -> explanation``. Each is left out of the live matrix and
#: pinned by its own ``xfail(strict=True)`` cell in the test module, so a fix
#: turns that cell XPASS -> failure and forces the entry's removal.
KNOWN_ROUTE_DIVERGENCES: dict[str, str] = {
    "release@mixed_old": "the release fan-out pairs directory members by file "
    "name, so a stored libfoo.json in OLD never pairs with a live libfoo.so in "
    "NEW: the run completes zero comparisons and exits 1 "
    "(no_comparison_completed), where the scalar route compares the same bytes "
    "(known-gaps: release-fan-out-cannot-pair-a-stored-snapshot-directory-with-a-live-one)",
    "release@mixed_new": "the mirror of release@mixed_old: a live libfoo.so "
    "directory in OLD against a stored libfoo.json directory in NEW pairs no "
    "member, exits 1 (no_comparison_completed), and the scalar route compares "
    "the same bytes (known-gaps: release-fan-out-cannot-pair-a-stored-snapshot-directory-with-a-live-one)",
}


#: The stored-form routes, as ``name -> runner`` (the historical view).
ROUTES: dict[str, Callable[[Operands, Axis, Path], Outcome]] = {
    name: route.run for name, route in ROUTE_MATRIX.items() if route.form == "stored"
}


# ── live operands: real gcc + castxml builds ─────────────────────────────

_BASE_H = "int foo(void);\nint bar(int);\n"
_BASE_C = "int foo(void){return 1;}\nint bar(int x){return x;}\n"

#: Live corpus: per corpus case, ``((old header, old source), (new header,
#: new source))``. Each pair is built with gcc into a real shared library and
#: dumped with castxml; the stored form is the ``abicheck dump`` of the same
#: binary, so a live cell and its stored cell share every input bit.
LIVE_SOURCES: dict[str, tuple[tuple[str, str], tuple[str, str]]] = {
    "identical": ((_BASE_H, _BASE_C), (_BASE_H, _BASE_C)),
    "removal_and_addition": (
        (_BASE_H, _BASE_C),
        (
            "int bar(int);\nint baz(void);\n",
            "int bar(int x){return x;}\nint baz(void){return 2;}\n",
        ),
    ),
    "addition_only": (
        (_BASE_H, _BASE_C),
        (
            _BASE_H + "int qux(void);\n",
            _BASE_C + "int qux(void){return 3;}\n",
        ),
    ),
    "signature_change": (
        (_BASE_H, _BASE_C),
        (
            "long foo(void);\nint bar(int, int);\n",
            "long foo(void){return 1;}\nint bar(int a, int b){return a+b;}\n",
        ),
    ),
    "variable_removed": (
        (
            _BASE_H + "extern int gv;\nextern int keep;\n",
            _BASE_C + "int gv;\nint keep;\n",
        ),
        (_BASE_H + "extern int keep;\n", _BASE_C + "int keep;\n"),
    ),
    # Exports no header declares: one appears, one disappears, one persists
    # (the live twin of the stored ``undeclared_export_churn`` case). C names
    # are used so castxml and the ELF table agree without mangling.
    "undeclared_export_churn": (
        (
            "void init_fn(void);\n",
            "void init_fn(void){}\nvoid removed_helper(void){}\nvoid persistent_helper(void){}\n",
        ),
        (
            "void init_fn(void);\n",
            "void init_fn(void){}\nvoid leaky_fn(void){}\nvoid persistent_helper(void){}\n",
        ),
    ),
    # A C++ header whose only C++ constructs are a non-const reference and a
    # default argument: the #1565 shape, where the header parse must follow the
    # binary's resolved language (C++) or the declaration-only graph is lost.
    "cxx_reference_header": (
        (
            "int foo(int& v);\nvoid bar(const char* s, int n = 3);\n",
            "int foo(int& v){return v + 1;}\nvoid bar(const char* s, int n){(void)s;(void)n;}\n",
        ),
        (
            "int foo(int& v);\nvoid bar(const char* s, long n = 3);\n",
            "int foo(int& v){return v + 2;}\nvoid bar(const char* s, long n){(void)s;(void)n;}\n",
        ),
    ),
}

#: Live cases built as C++ (g++); every other case is C (gcc).
LIVE_CXX_CASES = frozenset({"cxx_reference_header"})

#: The live undeclared-export case's names for :data:`UNDECLARED_*`.
LIVE_UNDECLARED_NAMES = {
    "added": "leaky_fn",
    "removed": "removed_helper",
    "persistent": "persistent_helper",
}


@dataclass(frozen=True)
class LiveBuild:
    """One live corpus case built and dumped under ``root``."""

    root: Path
    old_so: Path
    new_so: Path
    old_header: Path
    new_header: Path
    old_json: Path
    new_json: Path


def write_live_build(root: Path, case: str) -> LiveBuild:
    """Build both sides of *case* with gcc and dump each with ``abicheck dump``.

    A dump failure fails the build: an uncompilable or unparseable fixture
    is a broken corpus, never a route divergence.
    """
    paths: dict[str, Path] = {}
    for side, version, (header_text, source_text) in zip(
        ("old", "new"), ("1.0", "2.0"), LIVE_SOURCES[case]
    ):
        # The header sits beside its binary: the include root the dump
        # derives is then the side's own directory, the layout a real build
        # tree has (a header tree elsewhere changes ``include_sequence``).
        so_dir = root / side
        so_dir.mkdir(parents=True, exist_ok=True)
        header = so_dir / "foo.h"
        header.write_text(header_text, encoding="utf-8")
        cxx = case in LIVE_CXX_CASES
        source = root / f"{side}.{'cpp' if cxx else 'c'}"
        source.write_text(source_text, encoding="utf-8")
        so = so_dir / "libfoo.so"
        subprocess.run(
            [
                "g++" if cxx else "gcc",
                "-shared",
                "-fPIC",
                "-g",
                "-o",
                str(so),
                str(source),
            ],
            check=True,
        )
        stored_dir = root / f"{side}-stored"
        stored_dir.mkdir(parents=True, exist_ok=True)
        snapshot = stored_dir / "libfoo.json"
        dumped = _invoke(
            [
                "dump",
                str(so),
                "-H",
                str(header),
                "--label",
                version,
                "-o",
                str(snapshot),
            ]
        )
        assert dumped.exit_code == 0, dumped.output
        paths.update(
            {f"{side}_so": so, f"{side}_header": header, f"{side}_json": snapshot}
        )
    return LiveBuild(
        root=root,
        old_so=paths["old_so"],
        new_so=paths["new_so"],
        old_header=paths["old_header"],
        new_header=paths["new_header"],
        old_json=paths["old_json"],
        new_json=paths["new_json"],
    )


#: form -> (old side is live, new side is live).
_LIVENESS: dict[str, tuple[bool, bool]] = {
    "stored": (False, False),
    "live": (True, True),
    "mixed_old": (False, True),
    "mixed_new": (True, False),
}


def live_operands(build: LiveBuild, form: str) -> Operands:
    """The operands of *build* in operand *form*. Each side's directory holds
    only its own operand, so a directory operand is a one-member release."""
    old_live, new_live = _LIVENESS[form]
    root = build.root
    return Operands(
        old=build.old_so if old_live else build.old_json,
        new=build.new_so if new_live else build.new_json,
        old_dir=root / ("old" if old_live else "old-stored"),
        new_dir=root / ("new" if new_live else "new-stored"),
        old_header=build.old_header if old_live else None,
        new_header=build.new_header if new_live else None,
        old_version="1.0" if old_live else "",
        new_version="2.0" if new_live else "",
    )


# ── oracle ───────────────────────────────────────────────────────────────


#: Additional paths that legitimately differ between a *live* binary operand
#: and a *stored* snapshot of it (the stored-vs-live cell only).
STORED_VS_LIVE_PATHS: dict[tuple[str, ...], str] = {
    ("pattern_preprocessor_scan",): "the evidence licence: a live extraction "
    "may pre-scan the headers it just parsed, a stored snapshot must not "
    "re-read the filesystem for historical facts (#1236); the record says "
    "so explicitly instead of the findings changing",
}


def _drop_path(doc: dict[str, Any], path: tuple[str, ...]) -> None:
    *parents, leaf = path
    node: Any = doc
    for part in parents:
        if not isinstance(node, dict) or part not in node:
            return
        node[part] = dict(node[part]) if isinstance(node[part], dict) else node[part]
        node = node[part]
    if isinstance(node, dict):
        node.pop(leaf, None)


def normalize(
    report: Mapping[str, Any],
    *,
    strip_known: bool = True,
    extra_paths: Mapping[tuple[str, ...], str] | None = None,
) -> dict[str, Any]:
    drop = set(ROUTE_SPECIFIC_FIELDS)
    if strip_known:
        drop |= set(KNOWN_DIVERGENCES)
    out = {k: v for k, v in report.items() if k not in drop}
    for path in [*ROUTE_SPECIFIC_PATHS, *(extra_paths or {})]:
        _drop_path(out, path)
    return out


def parity_violations(
    reference: Outcome,
    other: Outcome,
    *,
    strip_known: bool = True,
    extra_paths: Mapping[tuple[str, ...], str] | None = None,
) -> list[str]:
    """Every way *other* disagrees with *reference*; empty means parity."""
    out: list[str] = []
    if reference.not_comparable is not None or other.not_comparable is not None:
        if reference.not_comparable != other.not_comparable:
            out.append(
                f"{other.route} vs {reference.route}: not-comparable {other.not_comparable!r} != {reference.not_comparable!r}"
            )
        if (
            -1 not in (reference.exit_code, other.exit_code)
            and reference.exit_code != other.exit_code
        ):
            out.append(
                f"{other.route} vs {reference.route}: exit {other.exit_code} != {reference.exit_code}"
            )
        return out
    a = normalize(reference.report, strip_known=strip_known, extra_paths=extra_paths)
    b = normalize(other.report, strip_known=strip_known, extra_paths=extra_paths)
    for key in sorted(set(a) | set(b)):
        if a.get(key) != b.get(key):
            out.append(
                f"{other.route} vs {reference.route}: report[{key!r}] "
                f"{json.dumps(b.get(key), sort_keys=True)[:300]} != "
                f"{json.dumps(a.get(key), sort_keys=True)[:300]}"
            )
    if reference.exit_code != other.exit_code:
        out.append(
            f"{other.route} vs {reference.route}: exit {other.exit_code} != {reference.exit_code}"
        )
    if reference.verdict != other.verdict:
        out.append(
            f"{other.route} vs {reference.route}: verdict {other.verdict} != {reference.verdict}"
        )
    return out


def release_finding_entry_violations(cli: Outcome, release: Outcome) -> list[str]:
    """#1176/#1326: the release summary's per-library ``findings`` entries
    must name the same findings as the scalar report's ``changes`` and must
    agree on every field the two entry shapes share."""
    out: list[str] = []
    if cli.not_comparable is not None or "library" not in release.extra:
        # A release run that paired no member has no per-library entry to
        # compare; the report-level parity check reports that run instead.
        return out
    summary_findings = release.extra["library"].get("findings", [])
    changes = cli.report["changes"]
    key = lambda e: (e.get("kind"), e.get("symbol"))  # noqa: E731
    if sorted(map(key, summary_findings)) != sorted(map(key, changes)):
        out.append(
            f"finding set differs: {sorted(map(key, summary_findings))} vs {sorted(map(key, changes))}"
        )
        return out
    by_key = {key(c): c for c in changes}
    for entry in summary_findings:
        scalar = by_key[key(entry)]
        for f in set(entry) & set(scalar):
            if entry[f] != scalar[f]:
                out.append(f"{key(entry)}.{f}: {entry[f]!r} != {scalar[f]!r}")
    return out


# ── undeclared-export event oracle ───────────────────────────────────────

_EXISTENCE_KINDS = {
    "func_added_elf_only": "added",
    "func_removed_elf_only": "removed",
    "var_added_elf_only": "added",
    "var_removed_elf_only": "removed",
}


#: The stored corpus's names for the three undeclared exports.
_STORED_UNDECLARED_NAMES = {
    "added": UNDECLARED_ADDED,
    "removed": UNDECLARED_REMOVED,
    "persistent": UNDECLARED_PERSISTENT,
}


def export_event_violations(
    out: Outcome, names: Mapping[str, str] = _STORED_UNDECLARED_NAMES
) -> list[str]:
    """Manifest class ``report.cross_producer_disagreement_on_one_symbol``:
    on every route, an undeclared export that appeared or disappeared is ONE
    existence finding in the right direction, and its ``exported_not_public``
    twin is not reported beside it. The oracle is the corpus construction
    (which symbol was added/removed/kept), not the shared fold under test.
    ``names`` maps ``added``/``removed``/``persistent`` to the case's symbols."""
    expected = {names["added"]: "added", names["removed"]: "removed"}
    persistent = names["persistent"]
    changes = out.report.get("changes", [])
    v: list[str] = []
    for sym, direction in expected.items():
        existence = [
            c
            for c in changes
            if c.get("symbol") == sym and c.get("kind") in _EXISTENCE_KINDS
        ]
        if [_EXISTENCE_KINDS[c["kind"]] for c in existence] != [direction]:
            v.append(f"{out.route}: {sym} existence findings {existence!r}")
        if any(
            c.get("symbol") == sym and c.get("kind") == "exported_not_public"
            for c in changes
        ):
            v.append(f"{out.route}: {sym} reported twice (hygiene twin kept)")
    if any(
        c.get("symbol") == persistent and c.get("kind") in _EXISTENCE_KINDS
        for c in changes
    ):
        v.append(f"{out.route}: persistent export {persistent} read as an event")
    return v
