"""ADR-084: no BINARY_GATED finding claims proven public reachability from export data.

Bug class: a detector for a ``BINARY_GATED`` kind tags its finding
``public_reachable=True`` / ``PROVEN_REACHABLE`` because its input population
(exported, or ``Visibility.PUBLIC``) "already proves" the subject public. The
export table proves linkability, not a source contract, so a consumer reading
the tags of a binary-only run is told something no evidence established.

Two layers close the class:

* a structural ratchet over ``abicheck/`` -- every call that builds a finding
  for a ``BINARY_GATED`` kind, and every call in a function that references
  one, must derive its reachability tags from
  ``model.source_contract_evidence`` (``**public_reachability(...)`` or
  ``**reachability_of(...)``), never pass ``public_reachable``/
  ``reachability_state``/``reachability_kind`` directly. A new detector that
  hard-codes the claim fails here.
* behavioural checks on the detectors themselves: binary-only snapshots
  (export + DWARF facts, no header parse) never yield a proven-public tag;
  the same subject observed by a header parse does.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from abicheck.diff_namespaces import detect_experimental_namespace_changes
from abicheck.diff_templates import (
    detect_mandatory_template_param_added,
    detect_unspecified_return_now_named,
)
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.evidence_status import ReachabilityState
from abicheck.model.fact import Fact
from abicheck.policy.binary_gated_contract import BINARY_GATED
from abicheck.policy.finding_disposition import DispositionRules, dispose_findings
from abicheck.policy.suppression.rules import Suppression, SuppressionList

ROOT = Path(__file__).resolve().parent.parent / "abicheck"

_REACHABILITY_KWARGS = frozenset(
    {"public_reachable", "reachability_state", "reachability_kind"}
)

#: (relative path, function name) -> justification. Must stay empty unless a
#: producer genuinely has header/source evidence in hand at that call site
#: and cannot route it through ``public_reachability``.
EXEMPT: dict[tuple[str, str], str] = {}


# ---------------------------------------------------------------------------
# Structural ratchet
# ---------------------------------------------------------------------------


def _gated_kind_refs(node: ast.AST, gated: frozenset[str]) -> set[str]:
    return {
        n.attr
        for n in ast.walk(node)
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id == "ChangeKind"
        and n.attr in gated
    }


def _direct_reachability_kwargs(call: ast.Call) -> list[str]:
    """Reachability keywords passed literally rather than via ``**public_reachability(...)``."""
    return [kw.arg for kw in call.keywords if kw.arg in _REACHABILITY_KWARGS]


def violations_in_source(
    source: str, rel: str, gated: frozenset[str]
) -> list[tuple[str, str, int, list[str]]]:
    """Calls in a BINARY_GATED-producing scope that set reachability tags directly."""
    tree = ast.parse(source)
    found: list[tuple[str, str, int, list[str]]] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        kinds = _gated_kind_refs(fn, gated)
        if not kinds:
            continue
        for call in ast.walk(fn):
            if not isinstance(call, ast.Call):
                continue
            bad = _direct_reachability_kwargs(call)
            if bad and (rel, fn.name) not in EXEMPT:
                found.append((rel, fn.name, call.lineno, bad))
    return found


def _gated_names() -> frozenset[str]:
    return frozenset(k.name for k in BINARY_GATED)


def _scan_tree() -> list[tuple[str, str, int, list[str]]]:
    gated = _gated_names()
    out: list[tuple[str, str, int, list[str]]] = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT.parent).as_posix()
        out.extend(violations_in_source(path.read_text(encoding="utf-8"), rel, gated))
    return out


def test_no_binary_gated_producer_sets_reachability_tags_directly() -> None:
    violations = _scan_tree()
    assert not violations, (
        "BINARY_GATED findings must derive reachability from observed "
        "header/source evidence (ADR-084): use "
        "**public_reachability(has_observed_source_contract_evidence(...)). "
        f"Offenders: {violations}"
    )


def test_ratchet_scans_the_known_producers() -> None:
    """Positive control: the scan reaches the modules that build these kinds."""
    gated = _gated_names()
    producers = {
        path.relative_to(ROOT.parent).as_posix()
        for path in ROOT.rglob("*.py")
        if any(
            _gated_kind_refs(fn, gated)
            for fn in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(fn, ast.FunctionDef)
        )
    }
    assert {
        "abicheck/diff_templates.py",
        "abicheck/diff_namespaces.py",
        "abicheck/compare/namespace_shape_detectors.py",
    } <= producers


def test_exemptions_point_at_real_functions() -> None:
    for rel, func in EXEMPT:
        tree = ast.parse((ROOT.parent / rel).read_text(encoding="utf-8"))
        assert any(
            isinstance(n, ast.FunctionDef) and n.name == func for n in ast.walk(tree)
        ), f"stale exemption {rel}:{func}"


_HARDCODED = """
def detect(old, new):
    return [make_change(ChangeKind.TYPE_REMOVED, symbol="x",
                        public_reachable=True,
                        reachability_state=ReachabilityState.PROVEN_REACHABLE)]
"""

_EXPORT_DERIVED = """
def detect(old, new):
    is_public = old.exported
    return [make_change(ChangeKind.FIELD_RENAMED, symbol="x",
                        public_reachable=is_public)]
"""

_EVIDENCE_DERIVED = """
def detect(old, new):
    return [make_change(ChangeKind.TYPE_REMOVED, symbol="x",
                        **public_reachability(has_observed_source_contract_evidence(d)))]
"""

_UNGATED_KIND = """
def detect(old, new):
    return [make_change(ChangeKind.FUNC_REMOVED, symbol="x", public_reachable=True)]
"""


@pytest.mark.parametrize(
    "src", [_HARDCODED, _EXPORT_DERIVED], ids=["literal", "export"]
)
def test_ratchet_flags_a_direct_claim(src: str) -> None:
    """Negative control: a hard-coded or export-derived claim is caught."""
    assert violations_in_source(src, "x.py", _gated_names())


@pytest.mark.parametrize(
    "src", [_EVIDENCE_DERIVED, _UNGATED_KIND], ids=["evidence", "ungated"]
)
def test_ratchet_accepts_evidence_and_ungated_kinds(src: str) -> None:
    assert not violations_in_source(src, "x.py", _gated_names())


# ---------------------------------------------------------------------------
# Behaviour: binary-only vs header-backed runs
# ---------------------------------------------------------------------------


def _fn(name: str, mangled: str, ret: str, *, header: bool) -> Function:
    if header:
        facts = {
            "declared_in_headers_fact": Fact.present(True, producer="castxml"),
            "in_public_contract_fact": Fact.present(True, producer="castxml"),
            "binary_exported_fact": Fact.present(True, producer="castxml"),
        }
    else:
        # Worst binary-only shape: exported, DWARF-confirmed contract (b).
        facts = {
            "binary_exported_fact": Fact.present(True, producer="dwarf"),
            "in_public_contract_fact": Fact.present(True, producer="dwarf"),
        }
    return Function(
        name=name,
        mangled=mangled,
        return_type=ret,
        params=[],
        visibility=Visibility.PUBLIC,
        **facts,
    )


def _snap(*funcs: Function, header: bool) -> AbiSnapshot:
    return AbiSnapshot(
        library="libx.so", version="1", functions=list(funcs), from_headers=header
    )


def _unspecified_return(header: bool):
    old = _snap(
        _fn("lib::make", "_ZN3lib4makeEv", "auto", header=header), header=header
    )
    new = _snap(
        _fn("lib::make", "_ZN3lib4makeEv", "lib::Widget", header=header), header=header
    )
    return detect_unspecified_return_now_named(old, new)


def _experimental_removed(header: bool):
    old = _snap(
        _fn(
            "lib::experimental::gadget",
            "_ZN3lib12experimental6gadgetEv",
            "void",
            header=header,
        ),
        _fn("lib::keep", "_ZN3lib4keepEv", "void", header=header),
        header=header,
    )
    new = _snap(
        _fn("lib::keep", "_ZN3lib4keepEv", "void", header=header), header=header
    )
    return detect_experimental_namespace_changes(old, new)


def _mandatory_template_param(header: bool):
    old = _snap(
        _fn("lib::box<int>", "_ZN3lib3boxIiEEv", "void", header=header), header=header
    )
    new = _snap(
        _fn("lib::box<int, int>", "_ZN3lib3boxIiiEEv", "void", header=header),
        header=header,
    )
    return detect_mandatory_template_param_added(old, new)


_DETECTORS = {
    ChangeKind.MANDATORY_TEMPLATE_PARAM_ADDED: _mandatory_template_param,
    ChangeKind.UNSPECIFIED_RETURN_NOW_NAMED: _unspecified_return,
    ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT: _experimental_removed,
}


def test_behavioural_cases_are_binary_gated() -> None:
    assert set(_DETECTORS) <= BINARY_GATED


@pytest.mark.parametrize("kind", list(_DETECTORS), ids=lambda k: k.value)
def test_binary_only_run_claims_no_proven_reachability(kind: ChangeKind) -> None:
    findings = [c for c in _DETECTORS[kind](False) if c.kind is kind]
    assert findings, "fixture did not produce the finding (vacuous)"
    for c in findings:
        assert c.public_reachable is not True
        assert c.reachability_state is not ReachabilityState.PROVEN_REACHABLE
        assert c.reachability_kind is None


@pytest.mark.parametrize("kind", list(_DETECTORS), ids=lambda k: k.value)
def test_header_backed_run_keeps_proven_reachability(kind: ChangeKind) -> None:
    findings = [c for c in _DETECTORS[kind](True) if c.kind is kind]
    assert findings, "fixture did not produce the finding (vacuous)"
    for c in findings:
        assert c.public_reachable is True
        assert c.reachability_state is ReachabilityState.PROVEN_REACHABLE
        assert c.reachability_kind == "direct_public_symbol"


# ---------------------------------------------------------------------------
# Moved from test_reachability_aware_suppression.py: a late detector's own
# tag decides whether a broad suppression may hide it.
# ---------------------------------------------------------------------------


def test_header_declared_experimental_removal_survives_broad_suppression() -> None:
    old = _snap(
        _fn("lib::experimental::foo", "lib::experimental::foo", "int", header=True),
        header=True,
    )
    new = _snap(header=True)
    suppression = SuppressionList(
        [Suppression(namespace="lib::experimental::*", reason="experimental churn")]
    )
    ctx = dispose_findings([], old, new, DispositionRules(suppression=suppression))
    kind = ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
    found = [c for c in ctx.kept if c.kind is kind]
    assert len(found) == 1
    assert found[0].public_reachable is True
    assert found[0] not in ctx.suppressed
    # The matched-but-withheld broad rule leaves its diagnostic.
    diag = [
        c for c in ctx.kept if c.kind is ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK
    ]
    assert len(diag) == 1
    assert "lib::experimental::foo" in diag[0].symbol


def test_binary_only_experimental_removal_is_suppressible_and_recorded() -> None:
    """Without evidence the subject is not proven public, so the rule applies,
    and the finding stays recorded as suppressed rather than vanishing."""
    old = _snap(
        _fn(
            "lib::experimental::foo", "_ZN3lib12experimental3fooEv", "int", header=False
        ),
        header=False,
    )
    new = _snap(header=False)
    suppression = SuppressionList(
        [Suppression(namespace="lib::experimental::*", reason="experimental churn")]
    )
    ctx = dispose_findings([], old, new, DispositionRules(suppression=suppression))
    kind = ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
    assert not [c for c in ctx.kept if c.kind is kind]
    assert [c for c in ctx.suppressed if c.kind is kind]
