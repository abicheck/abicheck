# Copyright 2026 Nikolay Petrov
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

"""Tests for ADR-027 A4 pattern-aware verdict modulation.

Focus is the anti-hiding contract: a modulation may demote-with-reason or
raise, never silently delete a real break. Includes the cross-output
completeness regression guard (a demoted finding must read compatible in every
sink and contribute to neither exit-code path).
"""

from __future__ import annotations

import json

import pytest

from abicheck import checker
from abicheck.checker_policy import ChangeKind, EvidenceTier, Verdict
from abicheck.checker_types import Change
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    Function,
    Param,
    RecordType,
    TypeField,
    Visibility,
)
from abicheck.pattern_verdicts import apply_pattern_verdicts
from abicheck.suppression import Suppression, SuppressionList

# ---------------------------------------------------------------------------
# Snapshot builders
# ---------------------------------------------------------------------------


def _opaque_snapshot(
    *, opaque: bool, size: int | None, by_value: bool = False
) -> AbiSnapshot:
    fields = (
        []
        if opaque
        else [TypeField(name="x", type="int", offset_bits=0, access=AccessLevel.PUBLIC)]
    )
    use_param = (
        Param(name="c", type="Ctx", pointer_depth=0)
        if by_value
        else Param(name="c", type="Ctx*", pointer_depth=1)
    )
    return AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="open",
                mangled="open",
                return_type="Ctx*",
                params=[],
                visibility=Visibility.PUBLIC,
                return_pointer_depth=1,
            ),
            Function(
                name="use",
                mangled="use",
                return_type="void",
                params=[use_param],
                visibility=Visibility.PUBLIC,
            ),
        ],
        types=[
            RecordType(
                name="Ctx",
                kind="struct",
                is_opaque=opaque,
                size_bits=size,
                fields=fields,
            )
        ],
    )


def _layout_change(symbol: str = "Ctx") -> Change:
    return Change(
        kind=ChangeKind.TYPE_SIZE_CHANGED,
        symbol=symbol,
        description="size changed",
        old_value="64",
        new_value="128",
    )


# ---------------------------------------------------------------------------
# Opaque-pointer demote (D4.1)
# ---------------------------------------------------------------------------


def test_opaque_layout_demoted_at_header_tier() -> None:
    old = _opaque_snapshot(opaque=True, size=None)
    new = _opaque_snapshot(opaque=True, size=None)
    changes = [_layout_change()]
    ledger = apply_pattern_verdicts(
        changes, old, new, evidence_tier=EvidenceTier.HEADER_AWARE
    )
    assert changes[0].effective_verdict == Verdict.COMPATIBLE
    assert changes[0].modulation_reason == "opaque-by-construction"
    assert changes[0].modulation_rule == "opaque-pointer-layout"
    assert any(m["rule_id"] == "opaque-pointer-layout" for m in ledger)
    assert ledger[0]["original_category"] == "breaking"
    assert ledger[0]["new_category"] == "compatible"


def test_policy_override_protects_kind_from_demotion() -> None:
    # ADR-027 review: an explicit user policy override on a kind is
    # authoritative and must win over an automated pattern demotion, so the
    # aggregate verdict (which applies the override) stays consistent with
    # per-finding classification. A demote never fires on a protected kind.
    old = _opaque_snapshot(opaque=True, size=None)
    new = _opaque_snapshot(opaque=True, size=None)
    changes = [_layout_change()]
    ledger = apply_pattern_verdicts(
        changes,
        old,
        new,
        evidence_tier=EvidenceTier.HEADER_AWARE,
        protected_kinds=frozenset({ChangeKind.TYPE_SIZE_CHANGED}),
    )
    assert changes[0].effective_verdict is None  # not demoted
    assert not any(m["rule_id"] == "opaque-pointer-layout" for m in ledger)


@pytest.mark.parametrize("tier", [EvidenceTier.ELF_ONLY, EvidenceTier.DWARF_AWARE])
def test_demotion_refused_below_header_tier(tier: EvidenceTier) -> None:
    # Confidence floor by tier (D4.1): idiom demotion needs the AST.
    old = _opaque_snapshot(opaque=True, size=None)
    new = _opaque_snapshot(opaque=True, size=None)
    changes = [_layout_change()]
    ledger = apply_pattern_verdicts(changes, old, new, evidence_tier=tier)
    assert changes[0].effective_verdict is None
    assert not any(m["rule_id"] == "opaque-pointer-layout" for m in ledger)


def test_disabled_is_noop() -> None:
    old = _opaque_snapshot(opaque=True, size=None)
    new = _opaque_snapshot(opaque=True, size=None)
    changes = [_layout_change()]
    ledger = apply_pattern_verdicts(
        changes, old, new, evidence_tier=EvidenceTier.HEADER_AWARE, enabled=False
    )
    assert ledger == []
    assert changes[0].effective_verdict is None


def test_frozen_namespace_never_demoted() -> None:
    old = _opaque_snapshot(opaque=True, size=None)
    new = _opaque_snapshot(opaque=True, size=None)
    c = _layout_change()
    c.frozen_namespace_violation = "myns::*"
    apply_pattern_verdicts([c], old, new, evidence_tier=EvidenceTier.HEADER_AWARE)
    assert c.effective_verdict is None


def test_ambiguous_short_name_not_demoted() -> None:
    # Codex P2: ns1::Ctx has a real size change; only ns2::Ctx is opaque. The
    # unqualified short name "Ctx" is ambiguous across namespaces, so ns2::Ctx's
    # opaque evidence must NOT demote the ns1::Ctx break.
    def snap() -> AbiSnapshot:
        return AbiSnapshot(
            library="l",
            version="1",
            from_headers=True,
            functions=[
                Function(  # ns2::Ctx is opaque (incomplete, pointer-only)
                    name="ns2_use",
                    mangled="ns2_use",
                    return_type="void",
                    params=[Param(name="c", type="ns2::Ctx*", pointer_depth=1)],
                    visibility=Visibility.PUBLIC,
                ),
                Function(  # ns1::Ctx is a complete, by-value public type
                    name="ns1_use",
                    mangled="ns1_use",
                    return_type="void",
                    params=[Param(name="c", type="ns1::Ctx", pointer_depth=0)],
                    visibility=Visibility.PUBLIC,
                ),
            ],
            types=[
                RecordType(name="ns2::Ctx", kind="struct", is_opaque=True),
                RecordType(
                    name="ns1::Ctx",
                    kind="struct",
                    is_opaque=False,
                    size_bits=64,
                    fields=[
                        TypeField(
                            name="x",
                            type="int",
                            offset_bits=0,
                            access=AccessLevel.PUBLIC,
                        )
                    ],
                ),
            ],
        )

    change = Change(
        kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="ns1::Ctx", description="grew"
    )
    apply_pattern_verdicts(
        [change], snap(), snap(), evidence_tier=EvidenceTier.HEADER_AWARE
    )
    # ns1::Ctx is not opaque; its break must stand despite ns2::Ctx being opaque.
    assert change.effective_verdict is None


# ---------------------------------------------------------------------------
# Lost opaque invariant → OPAQUE_INVARIANT_BROKEN (D2.2, never silent)
# ---------------------------------------------------------------------------


def test_lost_opaqueness_emits_break_not_silent_demotion() -> None:
    old = _opaque_snapshot(opaque=True, size=None)
    new = _opaque_snapshot(opaque=False, size=128)  # definition now visible
    changes: list[Change] = [_layout_change()]
    ledger = apply_pattern_verdicts(
        changes, old, new, evidence_tier=EvidenceTier.HEADER_AWARE
    )
    kinds = {c.kind for c in changes}
    assert ChangeKind.OPAQUE_INVARIANT_BROKEN in kinds
    broken = next(c for c in changes if c.kind == ChangeKind.OPAQUE_INVARIANT_BROKEN)
    assert broken.effective_verdict is None  # a real BREAKING kind, not demoted
    # The layout change itself must NOT have been silently demoted.
    layout = next(c for c in changes if c.kind == ChangeKind.TYPE_SIZE_CHANGED)
    assert layout.effective_verdict is None
    assert any(m["rule_id"] == "lost-opaque-invariant" for m in ledger)


def test_removed_opaque_with_same_short_name_not_flagged() -> None:
    # Codex P2: old ns1::Ctx is opaque and removed; new has an unrelated
    # ns2::Ctx (single, same short name). The lost-invariant transition must
    # require exact qualified identity, so no OPAQUE_INVARIANT_BROKEN fires for
    # ns1::Ctx (the removal is covered by normal type-removed handling).
    old = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="open",
                mangled="open",
                return_type="ns1::Ctx*",
                params=[],
                visibility=Visibility.PUBLIC,
                return_pointer_depth=1,
            ),
        ],
        types=[RecordType(name="ns1::Ctx", kind="struct", is_opaque=True)],
    )
    new = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="other",
                mangled="other",
                return_type="void",
                params=[Param(name="c", type="ns2::Ctx", pointer_depth=0)],
                visibility=Visibility.PUBLIC,
            ),
        ],
        types=[
            RecordType(
                name="ns2::Ctx",
                kind="struct",
                is_opaque=False,
                size_bits=64,
                fields=[
                    TypeField(
                        name="x", type="int", offset_bits=0, access=AccessLevel.PUBLIC
                    )
                ],
            )
        ],
    )
    changes: list[Change] = []
    apply_pattern_verdicts(changes, old, new, evidence_tier=EvidenceTier.HEADER_AWARE)
    assert not any(c.kind == ChangeKind.OPAQUE_INVARIANT_BROKEN for c in changes)


def test_lost_opaqueness_by_value_use() -> None:
    old = _opaque_snapshot(opaque=True, size=None)
    # Still incomplete (opaque=True), but now crossed by value publicly → opacity
    # lost. Keeping it incomplete isolates the *by-value* detection path: the
    # break must fire even though the definition is still hidden (Codex/CodeRabbit
    # review — opaque=False would let it pass via visibility instead).
    new = _opaque_snapshot(opaque=True, size=None, by_value=True)
    changes: list[Change] = []
    apply_pattern_verdicts(changes, old, new, evidence_tier=EvidenceTier.HEADER_AWARE)
    assert any(c.kind == ChangeKind.OPAQUE_INVARIANT_BROKEN for c in changes)


def test_still_hidden_opaque_not_flagged() -> None:
    # Codex P2: the type stays incomplete in new but its last public Ctx* use
    # was removed (so it's no longer *recognised* as an opaque idiom). Opacity
    # is intact — the forward declaration is still all callers can see — so this
    # must NOT emit a false OPAQUE_INVARIANT_BROKEN.
    old = _opaque_snapshot(opaque=True, size=None)
    new = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            # No more public function references Ctx; only the forward decl
            # remains. Ctx is still incomplete (is_opaque=True).
            Function(
                name="unrelated",
                mangled="unrelated",
                return_type="void",
                params=[],
                visibility=Visibility.PUBLIC,
            ),
        ],
        types=[RecordType(name="Ctx", kind="struct", is_opaque=True)],
    )
    changes: list[Change] = []
    ledger = apply_pattern_verdicts(
        changes, old, new, evidence_tier=EvidenceTier.HEADER_AWARE
    )
    assert not any(c.kind == ChangeKind.OPAQUE_INVARIANT_BROKEN for c in changes)
    assert not any(m["rule_id"] == "lost-opaque-invariant" for m in ledger)


# ---------------------------------------------------------------------------
# PIMPL pointee-only vs wrapper layout (D4.1 guard)
# ---------------------------------------------------------------------------


def _pimpl_snapshot(*, wrapper_size: int, impl_size: int | None) -> AbiSnapshot:
    wrapper = RecordType(
        name="Widget",
        kind="class",
        is_opaque=False,
        size_bits=wrapper_size,
        alignment_bits=64,
        fields=[
            TypeField(
                name="impl", type="Impl*", offset_bits=0, access=AccessLevel.PRIVATE
            )
        ],
    )
    impl = RecordType(name="Impl", kind="class", is_opaque=True, size_bits=impl_size)
    return AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="make",
                mangled="make",
                return_type="Widget*",
                params=[],
                visibility=Visibility.PUBLIC,
                return_pointer_depth=1,
            )
        ],
        types=[wrapper, impl],
    )


def test_pimpl_pointee_change_demoted() -> None:
    old = _pimpl_snapshot(wrapper_size=64, impl_size=None)
    new = _pimpl_snapshot(wrapper_size=64, impl_size=None)
    change = Change(
        kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Impl", description="impl grew"
    )
    apply_pattern_verdicts([change], old, new, evidence_tier=EvidenceTier.HEADER_AWARE)
    assert change.effective_verdict == Verdict.COMPATIBLE
    assert change.modulation_rule == "pimpl-pointee-only"


def test_pimpl_ambiguous_pointee_short_name_not_demoted() -> None:
    # Codex P2: a wrapper records an unqualified hidden_pointee "Impl", but the
    # snapshot has two Impl types (ns1::Impl, ns2::Impl). A real break on
    # ns1::Impl must NOT be demoted via the wrapper whose Impl* is ambiguous.
    def snap() -> AbiSnapshot:
        wrapper = RecordType(
            name="Widget",
            kind="class",
            is_opaque=False,
            size_bits=64,
            alignment_bits=64,
            fields=[
                TypeField(
                    name="impl", type="Impl*", offset_bits=0, access=AccessLevel.PRIVATE
                )
            ],
        )
        return AbiSnapshot(
            library="l",
            version="1",
            from_headers=True,
            functions=[
                Function(
                    name="make",
                    mangled="make",
                    return_type="Widget*",
                    params=[],
                    visibility=Visibility.PUBLIC,
                    return_pointer_depth=1,
                )
            ],
            types=[
                wrapper,
                RecordType(name="ns1::Impl", kind="class", is_opaque=True),
                RecordType(name="ns2::Impl", kind="class", is_opaque=True),
            ],
        )

    change = Change(
        kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="ns1::Impl", description="grew"
    )
    apply_pattern_verdicts(
        [change], snap(), snap(), evidence_tier=EvidenceTier.HEADER_AWARE
    )
    assert change.effective_verdict is None  # ambiguous pointee → not demoted


def test_pimpl_wrapper_layout_change_not_demoted() -> None:
    # The wrapper's own layout changed (64 -> 128): callers can sizeof it, so a
    # change to the wrapper is a real break and must NOT be demoted.
    old = _pimpl_snapshot(wrapper_size=64, impl_size=None)
    new = _pimpl_snapshot(wrapper_size=128, impl_size=None)
    change = Change(
        kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Widget", description="wrapper grew"
    )
    apply_pattern_verdicts([change], old, new, evidence_tier=EvidenceTier.HEADER_AWARE)
    assert change.effective_verdict is None


# ---------------------------------------------------------------------------
# Handle token change → HANDLE_TYPE_CHANGED
# ---------------------------------------------------------------------------


def _handle_snapshot(target: str) -> AbiSnapshot:
    return AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="use",
                mangled="use",
                return_type="void",
                params=[Param(name="h", type="my_handle_t", pointer_depth=0)],
                visibility=Visibility.PUBLIC,
            )
        ],
        typedefs={"my_handle_t": target},
    )


def test_new_antipattern_emitted_as_risk() -> None:
    # Old has no by-value std param; new introduces one → RISK finding emitted.
    def snap(by_value: bool) -> AbiSnapshot:
        ptype = "std::string" if by_value else "std::string*"
        depth = 0 if by_value else 1
        return AbiSnapshot(
            library="l",
            version="1",
            from_headers=True,
            functions=[
                Function(
                    name="sink",
                    mangled="sink",
                    return_type="void",
                    params=[Param(name="s", type=ptype, pointer_depth=depth)],
                    visibility=Visibility.PUBLIC,
                )
            ],
        )

    changes: list[Change] = []
    ledger = apply_pattern_verdicts(
        changes, snap(False), snap(True), evidence_tier=EvidenceTier.HEADER_AWARE
    )
    assert any(c.kind == ChangeKind.PUBLIC_API_EXPOSES_STL_BY_VALUE for c in changes)
    assert any(m["rule_id"] == "new-anti-pattern" for m in ledger)


def test_private_inheritance_does_not_suppress_new_public_factory_risk() -> None:
    # Codex P2: old has a polymorphic Base (no virtual dtor) inherited only by a
    # PRIVATE_HEADER record (not a public risk). New adds the first public factory
    # returning Base*. The new *public* risk must be emitted — the private-
    # inheritance evidence must not pre-seed old_aps and suppress it.
    from abicheck.model import RecordType as _RT
    from abicheck.model import ScopeOrigin

    base = _RT(name="Base", kind="class", vtable=["_ZN4Base3fooEv"])

    def old_snap() -> AbiSnapshot:
        return AbiSnapshot(
            library="l",
            version="1",
            from_headers=True,
            types=[
                base,
                _RT(
                    name="Impl",
                    kind="class",
                    bases=["Base"],
                    origin=ScopeOrigin.PRIVATE_HEADER,
                ),
            ],
        )

    def new_snap() -> AbiSnapshot:
        return AbiSnapshot(
            library="l",
            version="1",
            from_headers=True,
            functions=[
                Function(
                    name="make",
                    mangled="make",
                    return_type="Base*",
                    params=[],
                    visibility=Visibility.PUBLIC,
                    return_pointer_depth=1,
                )
            ],
            types=[
                base,
                _RT(
                    name="Impl",
                    kind="class",
                    bases=["Base"],
                    origin=ScopeOrigin.PRIVATE_HEADER,
                ),
            ],
        )

    changes: list[Change] = []
    apply_pattern_verdicts(
        changes, old_snap(), new_snap(), evidence_tier=EvidenceTier.HEADER_AWARE
    )
    assert any(
        c.kind == ChangeKind.POLYMORPHIC_TYPE_NON_VIRTUAL_DTOR and c.symbol == "Base"
        for c in changes
    )


def test_preexisting_antipattern_not_re_emitted() -> None:
    # Present in BOTH snapshots → pre-existing debt, not nagged about.
    def snap() -> AbiSnapshot:
        return AbiSnapshot(
            library="l",
            version="1",
            from_headers=True,
            functions=[
                Function(
                    name="sink",
                    mangled="sink",
                    return_type="void",
                    params=[Param(name="s", type="std::string", pointer_depth=0)],
                    visibility=Visibility.PUBLIC,
                )
            ],
        )

    changes: list[Change] = []
    apply_pattern_verdicts(
        changes, snap(), snap(), evidence_tier=EvidenceTier.HEADER_AWARE
    )
    assert not any(
        c.kind == ChangeKind.PUBLIC_API_EXPOSES_STL_BY_VALUE for c in changes
    )


def test_handle_token_change_emits_break() -> None:
    old = _handle_snapshot("struct Foo *")
    new = _handle_snapshot("struct Bar *")
    changes: list[Change] = []
    ledger = apply_pattern_verdicts(
        changes, old, new, evidence_tier=EvidenceTier.HEADER_AWARE
    )
    assert any(c.kind == ChangeKind.HANDLE_TYPE_CHANGED for c in changes)
    assert any(m["rule_id"] == "handle-token-changed" for m in ledger)


def test_builtin_pointer_typedef_is_not_a_handle() -> None:
    # Codex P2: typedef int *ptr_t is a pointer to a builtin, NOT an opaque
    # handle token. A width change int* -> long* must not emit handle_type_changed.
    old = _handle_snapshot("int *")
    new = _handle_snapshot("long *")
    changes: list[Change] = []
    apply_pattern_verdicts(changes, old, new, evidence_tier=EvidenceTier.HEADER_AWARE)
    assert not any(c.kind == ChangeKind.HANDLE_TYPE_CHANGED for c in changes)


def test_pattern_generated_change_respects_suppression() -> None:
    suppression = SuppressionList(
        [
            Suppression(symbol="my_handle_t", change_kind="typedef_base_changed"),
            Suppression(symbol="my_handle_t", change_kind="handle_type_changed"),
        ]
    )
    result = checker.compare(
        _handle_snapshot("struct Foo *"),
        _handle_snapshot("struct Bar *"),
        suppression=suppression,
        pattern_verdicts=True,
    )
    assert result.verdict == Verdict.NO_CHANGE
    assert ChangeKind.HANDLE_TYPE_CHANGED not in {c.kind for c in result.changes}
    assert ChangeKind.HANDLE_TYPE_CHANGED in {c.kind for c in result.suppressed_changes}


def test_antipattern_annotation_on_existing_change() -> None:
    # A change sitting on an STL-by-value surface gets an annotation (raise),
    # never a demotion: category unchanged, but disclosed in the ledger.
    def snap() -> AbiSnapshot:
        return AbiSnapshot(
            library="l",
            version="1",
            from_headers=True,
            functions=[
                Function(
                    name="sink",
                    mangled="sink",
                    return_type="void",
                    params=[Param(name="s", type="std::string", pointer_depth=0)],
                    visibility=Visibility.PUBLIC,
                )
            ],
        )

    change = Change(
        kind=ChangeKind.FUNC_PARAMS_CHANGED, symbol="sink", description="params changed"
    )
    ledger = apply_pattern_verdicts(
        [change], snap(), snap(), evidence_tier=EvidenceTier.HEADER_AWARE
    )
    assert change.effective_verdict is None  # raise never demotes
    assert change.modulation_reason == "anti-pattern-elevated-risk"
    assert any(m["rule_id"] == "anti-pattern-raise" for m in ledger)


def _save(snap: AbiSnapshot, path) -> None:
    from abicheck.serialization import save_snapshot

    save_snapshot(snap, path)


def test_suppressed_synthetic_finding_pruned_from_ledger() -> None:
    # Codex P2: when a synthetic pattern finding is suppressed, its
    # pattern_modulations ledger row must be pruned too — otherwise the JSON /
    # --explain-patterns ledger reports a raise that is absent from changes.
    from abicheck.suppression import Suppression, SuppressionList

    old = _handle_snapshot("struct Foo *")
    new = _handle_snapshot("struct Bar *")
    supp = SuppressionList(
        [
            Suppression(symbol="my_handle_t", change_kind="handle_type_changed"),
            Suppression(symbol="my_handle_t", change_kind="typedef_base_changed"),
        ]
    )
    result = checker.compare(
        old, new, suppression=supp, scope_to_public_surface=False, pattern_verdicts=True
    )
    assert not any(c.kind == ChangeKind.HANDLE_TYPE_CHANGED for c in result.changes)
    assert any(
        c.kind == ChangeKind.HANDLE_TYPE_CHANGED for c in result.suppressed_changes
    )
    # The ledger row for the suppressed handle transition must be gone.
    assert not any(
        m["rule_id"] == "handle-token-changed" for m in result.pattern_modulations
    )


def test_cli_explain_patterns(tmp_path) -> None:
    from click.testing import CliRunner

    from abicheck.cli import main

    old = _handle_snapshot("struct Foo *")
    new = _handle_snapshot("struct Bar *")
    op = tmp_path / "old.abi.json"
    np = tmp_path / "new.abi.json"
    out = tmp_path / "report.json"
    _save(old, op)
    _save(new, np)
    res = CliRunner().invoke(
        main,
        [
            "compare",
            str(op),
            str(np),
            "--pattern-verdicts",
            "--explain-patterns",
            "--format",
            "json",
            "-o",
            str(out),
        ],
    )
    assert res.exit_code in (0, 2, 4), res.output
    payload = json.loads(out.read_text())
    assert any(
        m["rule_id"] == "handle-token-changed" for m in payload["pattern_modulations"]
    )
    # --explain-patterns prints the ledger (with evidence edges).
    combined = res.output + (res.stderr if res.stderr_bytes else "")
    assert "Pattern-aware modulations" in combined
    assert "handle-token-changed" in combined


def test_cli_no_modulations_message(tmp_path) -> None:
    from click.testing import CliRunner

    from abicheck.cli import main

    # Identical snapshots → no changes, no modulations → the "none" branch.
    snap = _handle_snapshot("struct Foo *")
    p = tmp_path / "s.abi.json"
    _save(snap, p)
    res = CliRunner().invoke(main, ["compare", str(p), str(p), "--explain-patterns"])
    assert res.exit_code == 0, res.output
    combined = res.output + (res.stderr if res.stderr_bytes else "")
    assert "No pattern-aware modulations applied." in combined


# ---------------------------------------------------------------------------
# Cross-output completeness (validation matrix §2)
# ---------------------------------------------------------------------------


def test_cross_output_completeness_for_demoted_finding() -> None:
    from abicheck.junit_report import to_junit_xml
    from abicheck.reporter import to_json
    from abicheck.sarif import to_sarif_str
    from abicheck.severity import PRESET_DEFAULT, compute_exit_code

    old = _opaque_snapshot(opaque=True, size=None)
    new = _opaque_snapshot(opaque=True, size=None)
    result = checker.compare(
        old, new, scope_to_public_surface=False, pattern_verdicts=True
    )
    # Force a demoted layout finding into the result so every sink is exercised
    # even though the existing opaque filter also handles pure size drift.
    demoted = _layout_change()
    demoted.effective_verdict = Verdict.COMPATIBLE
    demoted.modulation_reason = "opaque-by-construction"
    demoted.modulation_rule = "opaque-pointer-layout"
    result.changes.append(demoted)
    result.pattern_modulations = [
        {
            "symbol": "Ctx",
            "original_category": "breaking",
            "new_category": "compatible",
            "rule_id": "opaque-pointer-layout",
            "reason": "opaque-by-construction",
            "evidence_tier": "header_aware",
            "edges_matched": [],
        }
    ]

    # 1. verdict / compute_verdict exit path
    assert demoted in result.compatible
    assert demoted not in result.breaking

    # 2. JSON `changes` + severity field
    payload = json.loads(to_json(result))
    entry = next(c for c in payload["changes"] if c["kind"] == "type_size_changed")
    assert entry["severity"] == "compatible"
    assert entry["effective_verdict"] == "COMPATIBLE"
    assert entry["modulation_reason"] == "opaque-by-construction"

    # 3. JSON filtered_summary (show_only path) — and the show-only *filter*
    # itself must exclude the demoted finding by its effective category (Codex
    # P2): a demoted type_size_changed must not leak into `changes` under
    # --show-only=breaking while filtered_summary reports breaking: 0.
    payload2 = json.loads(to_json(result, show_only="breaking"))
    assert payload2["filtered_summary"]["breaking"] == 0
    assert all(c["kind"] != "type_size_changed" for c in payload2["changes"])
    # Conversely it IS selected under --show-only=compatible.
    payload3 = json.loads(to_json(result, show_only="compatible"))
    assert any(c["kind"] == "type_size_changed" for c in payload3["changes"])

    # 4. severity-aware exit code path
    assert compute_exit_code([demoted], PRESET_DEFAULT) == 0

    # 5. SARIF level = note, not error
    sarif = json.loads(to_sarif_str(result))
    results = sarif["runs"][0]["results"]
    size_results = [r for r in results if r["ruleId"] == "type_size_changed"]
    assert size_results and all(r["level"] == "note" for r in size_results)

    # 5b. Leaf-mode JSON keeps the modulation audit trail (Codex P2): the
    # demoted root type change carries effective_verdict/modulation_reason and
    # the top-level pattern_modulations ledger is present.
    leaf = json.loads(to_json(result, report_mode="leaf"))
    leaf_entry = next(
        c for c in leaf["leaf_changes"] if c["kind"] == "type_size_changed"
    )
    assert leaf_entry["severity"] == "compatible"
    assert leaf_entry["effective_verdict"] == "COMPATIBLE"
    assert leaf_entry["modulation_reason"] == "opaque-by-construction"
    assert "pattern_modulations" in leaf

    # 5c. Element filter (--show-only) must not drop ADR-027 kinds that don't
    # match the prefix table (Codex P2). A type-level invariant break is kept
    # under --show-only=types.
    from abicheck.reporter import ShowOnlyFilter

    inv = Change(
        kind=ChangeKind.OPAQUE_INVARIANT_BROKEN, symbol="Ctx", description="lost"
    )
    assert ShowOnlyFilter.parse("types").matches(inv)
    stl = Change(
        kind=ChangeKind.PUBLIC_API_EXPOSES_STL_BY_VALUE, symbol="f", description="stl"
    )
    assert ShowOnlyFilter.parse("functions").matches(stl)

    # 6. JUnit: demoted finding is not a failure
    xml = to_junit_xml(result)
    assert (
        "type_size_changed" not in xml
        or "<failure" not in xml.split("type_size_changed")[1][:200]
    )
