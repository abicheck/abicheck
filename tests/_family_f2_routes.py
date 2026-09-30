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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from abicheck.cli import main
from abicheck.comparability import _MISMATCH_ERRORS
from abicheck.model import AbiSnapshot, Function, Param, Variable, Visibility
from abicheck.reporter import to_json
from abicheck.serialization import snapshot_to_json
from abicheck.service import CompareRequest, InputSpec
from abicheck.service_compare_pipeline import run_compare_request

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


def _suppress_file(tmp: Path) -> Path:
    p = tmp / "suppress.yaml"
    p.write_text(
        "version: 1\nsuppressions:\n  - symbol: foo\n    reason: parity harness\n  - symbol: gv\n    reason: parity harness\n",
        encoding="utf-8",
    )
    return p


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
        lambda t: {"policy_file_path": _policy_file(t)},
        ("policy",),
        ("policy_file_path",),
    ),
    Axis(
        "suppress",
        lambda t: ["--suppress", str(_suppress_file(t))],
        lambda t: {"suppress": _suppress_file(t)},
        ("suppress",),
        ("suppress",),
    ),
    Axis(
        "no_scope_public",
        lambda t: ["--no-scope-public-headers"],
        lambda t: {"scope_public": False},
        ("scope_public_headers",),
        ("scope_public",),
    ),
    Axis(
        "severity_strict",
        lambda t: ["--severity-preset", "strict"],
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
        lambda t: {"contract_evaluation": True, "contract_mode": "exports"},
        ("contract_mode",),
        ("contract_evaluation", "contract_mode"),
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
_COMPARABILITY_ERRORS = tuple(set(_MISMATCH_ERRORS.values()))


def _invoke(args: list[str]) -> Any:
    return CliRunner().invoke(main, args, catch_exceptions=False)


def run_cli(ops: Operands, axis: Axis, tmp: Path) -> Outcome:
    r = _invoke(["compare", str(ops.old), str(ops.new), *axis.cli(tmp), "-o", "json=-"])
    return Outcome("cli", json.loads(r.stdout), r.exit_code)


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
        old=InputSpec(path=ops.old), new=InputSpec(path=ops.new), **kwargs
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
            "-o",
            f"json={summary}",
            "-o",
            f"json={reports}/",
        ]
    )
    doc = json.loads(summary.read_text(encoding="utf-8"))
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


ROUTES: dict[str, Callable[[Operands, Axis, Path], Outcome]] = {
    "cli": run_cli,
    "api": run_api,
    "release": run_release_member,
}


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
    if cli.not_comparable is not None:
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
