"""ADR-084: a binary-only run may not keep a source-level (API_BREAK) claim it cannot back.

Three layers are pinned here:

* the registry: :data:`BINARY_GATED`, :data:`HEADER_OR_SOURCE_GATED` and
  :data:`OUT_OF_AXIS` partition every ``API_BREAK`` kind (a new kind fails the
  partition test until it is placed);
* the gate, generalized over the registry: for each binary-gated kind, a
  binary-only comparison is not ``API_BREAK``, the same comparison with
  header evidence keeps ``API_BREAK`` (the positive control, which stops the
  gate from over-demoting), and the demotion leaves a disposition record;
* the evidence predicate and the two detectors whose hard-coded reachability
  was replaced by it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.change_registry import API_BREAK_KINDS
from abicheck.checker import compare
from abicheck.compare.namespace_shape_detectors import _build_std_reexport_change
from abicheck.diff_cpp_patterns import _find_tag_rename_for_removed
from abicheck.diff_helpers import make_change
from abicheck.model import AbiSnapshot, Function, RecordType, Visibility
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.change_catalog.registry import Verdict
from abicheck.model.evidence_status import EvidenceStatus, ReachabilityState
from abicheck.model.fact import Fact
from abicheck.model.source_contract_evidence import (
    has_observed_source_contract_evidence,
)
from abicheck.policy.binary_gated_contract import (
    BINARY_GATE_RULE_ID,
    BINARY_GATED,
    HEADER_OR_SOURCE_GATED,
    NON_BINARY_REASONS,
    OUT_OF_AXIS,
    OUT_OF_AXIS_REASONS,
    demote_unproven_binary_api_breaks,
)
from abicheck.policy.classification import evidence_status_for_result

SUBJECT = "lib::subject"
SUBJECT_MANGLED = "_ZN3lib7subjectEv"
OTHER = "lib::other"
OTHER_MANGLED = "_ZN3lib5otherEv"

_BINARY_GATED_SORTED = sorted(BINARY_GATED, key=lambda k: k.value)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _binary_only_function(name: str, mangled: str) -> Function:
    """The worst binary-only shape: exported, with debug info, no headers.

    A DWARF export makes (b) ``PRESENT(True)`` -- the export is real, but it
    is not header or source evidence, and the predicate must not read it as
    such.
    """
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        params=[],
        visibility=Visibility.PUBLIC,
        binary_exported_fact=Fact.present(True, producer="dwarf"),
        in_public_contract_fact=Fact.present(True, producer="dwarf"),
    )


def _header_backed_function(name: str, mangled: str) -> Function:
    """A declaration a header parse of this run declared (fact (a))."""
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        params=[],
        visibility=Visibility.PUBLIC,
        declared_in_headers_fact=Fact.present(True, producer="castxml"),
        in_public_contract_fact=Fact.present(True, producer="castxml"),
        binary_exported_fact=Fact.present(True, producer="castxml"),
    )


def _snapshot(*functions: Function, from_headers: bool) -> AbiSnapshot:
    return AbiSnapshot(
        library="libsubject.so",
        version="1",
        functions=list(functions),
        from_headers=from_headers,
    )


def _claim(kind: ChangeKind, symbol: str = SUBJECT) -> Change:
    """The producer's worst-case claim: public, proven reachable, direct."""
    return make_change(
        kind,
        symbol=symbol,
        description=f"injected {kind.value} claim for {symbol}",
        public_reachable=True,
        reachability_state=ReachabilityState.PROVEN_REACHABLE,
        reachability_kind="direct_public_symbol",
    )


def _finding(result, kind: ChangeKind) -> Change:
    matches = [c for c in result.changes if c.kind is kind]
    assert matches, f"{kind.value} did not survive to the result (vacuous check)"
    return matches[0]


def _ledger_rule_ids(result, kind: ChangeKind) -> set[str | None]:
    return {
        r.rule.rule_id if r.rule is not None else None
        for r in result.disposition_ledger.records
        if r.kind == kind.value
    }


# ---------------------------------------------------------------------------
# Registry: three buckets partition every API_BREAK kind
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_buckets_cover_every_api_break_kind(self) -> None:
        """A new API_BREAK kind fails here until it is placed in a bucket."""
        covered = BINARY_GATED | HEADER_OR_SOURCE_GATED | OUT_OF_AXIS
        unclassified = sorted(k.value for k in API_BREAK_KINDS - covered)
        assert unclassified == []

    def test_buckets_name_only_api_break_kinds(self) -> None:
        assert BINARY_GATED <= API_BREAK_KINDS
        assert HEADER_OR_SOURCE_GATED <= API_BREAK_KINDS
        assert OUT_OF_AXIS <= API_BREAK_KINDS

    def test_buckets_are_pairwise_disjoint(self) -> None:
        assert not (BINARY_GATED & HEADER_OR_SOURCE_GATED)
        assert not (BINARY_GATED & OUT_OF_AXIS)
        assert not (HEADER_OR_SOURCE_GATED & OUT_OF_AXIS)

    def test_every_non_binary_kind_states_why_it_is_not_gated(self) -> None:
        assert set(NON_BINARY_REASONS) == HEADER_OR_SOURCE_GATED
        assert set(OUT_OF_AXIS_REASONS) == OUT_OF_AXIS
        for reason in (*NON_BINARY_REASONS.values(), *OUT_OF_AXIS_REASONS.values()):
            assert reason.strip()

    def test_the_probed_binary_producers_are_binary_gated(self) -> None:
        """The kinds the ADR-084 probes reached from binary-only input."""
        for kind in (
            ChangeKind.MANDATORY_TEMPLATE_PARAM_ADDED,
            ChangeKind.INLINE_FUNCTION_REMOVED,
            ChangeKind.TYPE_REMOVED,
            ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT,
            ChangeKind.UNSPECIFIED_RETURN_NOW_NAMED,
        ):
            assert kind in BINARY_GATED, kind.value


# ---------------------------------------------------------------------------
# The gate, generalized over BINARY_GATED
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", _BINARY_GATED_SORTED, ids=lambda k: k.value)
def test_binary_only_run_does_not_keep_an_unbacked_api_break(kind: ChangeKind) -> None:
    old = _snapshot(_binary_only_function(SUBJECT, SUBJECT_MANGLED), from_headers=False)
    new = _snapshot(_binary_only_function(SUBJECT, SUBJECT_MANGLED), from_headers=False)

    result = compare(old, new, extra_changes=[_claim(kind)])

    finding = _finding(result, kind)
    assert result.verdict != Verdict.API_BREAK
    assert finding.effective_verdict == Verdict.COMPATIBLE_WITH_RISK
    assert finding.modulation_rule == BINARY_GATE_RULE_ID
    assert finding.modulation_reason
    # ADR-067: the demotion is recorded with its rule, not silently applied.
    assert _ledger_rule_ids(result, kind) == {BINARY_GATE_RULE_ID}


@pytest.mark.parametrize("kind", _BINARY_GATED_SORTED, ids=lambda k: k.value)
def test_header_evidence_keeps_the_api_break(kind: ChangeKind) -> None:
    """Positive control: the gate must not over-demote a backed claim."""
    old = _snapshot(
        _header_backed_function(SUBJECT, SUBJECT_MANGLED), from_headers=True
    )
    new = _snapshot(
        _header_backed_function(SUBJECT, SUBJECT_MANGLED), from_headers=True
    )

    result = compare(old, new, extra_changes=[_claim(kind)])

    finding = _finding(result, kind)
    assert finding.effective_verdict is None
    assert finding.modulation_rule is None
    assert result.verdict == Verdict.API_BREAK


# ---------------------------------------------------------------------------
# Subject resolution
# ---------------------------------------------------------------------------


def test_unbacked_subject_is_demoted_even_when_another_declaration_is_backed() -> None:
    """Resolution is per entity: evidence on a neighbour does not back this subject."""
    old = _snapshot(
        _binary_only_function(SUBJECT, SUBJECT_MANGLED),
        _header_backed_function(OTHER, OTHER_MANGLED),
        from_headers=True,
    )
    new = _snapshot(
        _binary_only_function(SUBJECT, SUBJECT_MANGLED),
        _header_backed_function(OTHER, OTHER_MANGLED),
        from_headers=True,
    )

    result = compare(
        old, new, extra_changes=[_claim(ChangeKind.INLINE_FUNCTION_REMOVED)]
    )

    finding = _finding(result, ChangeKind.INLINE_FUNCTION_REMOVED)
    assert finding.effective_verdict == Verdict.COMPATIBLE_WITH_RISK


def test_a_header_observed_contract_without_an_export_keeps_the_api_break() -> None:
    """The converse of the export-only case: an observed (a) needs no export."""
    unexported = Function(
        name=SUBJECT,
        mangled=SUBJECT_MANGLED,
        return_type="void",
        params=[],
        visibility=Visibility.PUBLIC,
        declared_in_headers_fact=Fact.present(True, producer="castxml"),
        binary_exported_fact=Fact.present(False, producer="elf"),
    )
    old = _snapshot(unexported, from_headers=True)
    new = _snapshot(unexported, from_headers=True)

    result = compare(old, new, extra_changes=[_claim(ChangeKind.TYPE_REMOVED)])

    assert _finding(result, ChangeKind.TYPE_REMOVED).effective_verdict is None
    assert result.verdict == Verdict.API_BREAK


def test_an_unresolved_subject_in_a_header_run_is_not_demoted() -> None:
    """A spelling the index cannot match is not proof that nothing was observed.

    In a run that parsed headers the unresolved name keeps its category; the
    run-level answer is the conservative one for a finding the run cannot place.
    """
    old = _snapshot(_header_backed_function(OTHER, OTHER_MANGLED), from_headers=True)
    new = _snapshot(_header_backed_function(OTHER, OTHER_MANGLED), from_headers=True)

    result = compare(
        old,
        new,
        extra_changes=[
            _claim(ChangeKind.MANDATORY_TEMPLATE_PARAM_ADDED, "lib::unplaced<int>")
        ],
    )

    finding = _finding(result, ChangeKind.MANDATORY_TEMPLATE_PARAM_ADDED)
    assert finding.effective_verdict is None
    assert result.verdict == Verdict.API_BREAK


def _export_only_function(name: str, mangled: str) -> Function:
    """Exported in the binary, and nothing else: no header or contract fact."""
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        params=[],
        visibility=Visibility.PUBLIC,
        binary_exported_fact=Fact.present(True, producer="elf"),
    )


@pytest.mark.parametrize("kind", _BINARY_GATED_SORTED, ids=lambda k: k.value)
def test_a_binary_export_alone_does_not_keep_an_api_break(kind: ChangeKind) -> None:
    """The export table says a consumer can link; it does not say the name was promised.

    This pins the distinction ``is_abi_visible`` blurs: an exported symbol
    with no (a) or (b) header observation must not satisfy the gate.
    """
    old = _snapshot(_export_only_function(SUBJECT, SUBJECT_MANGLED), from_headers=False)
    new = _snapshot(_export_only_function(SUBJECT, SUBJECT_MANGLED), from_headers=False)

    result = compare(old, new, extra_changes=[_claim(kind)])

    assert _finding(result, kind).effective_verdict == Verdict.COMPATIBLE_WITH_RISK
    assert result.verdict != Verdict.API_BREAK


@pytest.mark.parametrize("kind", _BINARY_GATED_SORTED, ids=lambda k: k.value)
def test_a_demoted_finding_reads_as_contextual_risk(kind: ChangeKind) -> None:
    """The evidence label must not restate a source-contract claim the run could not back."""
    old = _snapshot(_binary_only_function(SUBJECT, SUBJECT_MANGLED), from_headers=False)
    new = _snapshot(_binary_only_function(SUBJECT, SUBJECT_MANGLED), from_headers=False)

    result = compare(old, new, extra_changes=[_claim(kind)])

    finding = _finding(result, kind)
    assert evidence_status_for_result(finding) is EvidenceStatus.CONTEXTUAL_RISK


def test_a_header_backed_api_break_keeps_its_source_contract_label() -> None:
    old = _snapshot(
        _header_backed_function(SUBJECT, SUBJECT_MANGLED), from_headers=True
    )
    new = _snapshot(
        _header_backed_function(SUBJECT, SUBJECT_MANGLED), from_headers=True
    )

    result = compare(old, new, extra_changes=[_claim(ChangeKind.TYPE_REMOVED)])

    finding = _finding(result, ChangeKind.TYPE_REMOVED)
    assert evidence_status_for_result(finding) is EvidenceStatus.SOURCE_CONTRACT


def test_a_non_binary_kind_is_never_demoted() -> None:
    """The gate is for BINARY_GATED kinds only; a header-gated kind passes through."""
    old = _snapshot(_binary_only_function(SUBJECT, SUBJECT_MANGLED), from_headers=False)
    new = _snapshot(_binary_only_function(SUBJECT, SUBJECT_MANGLED), from_headers=False)

    result = compare(old, new, extra_changes=[_claim(ChangeKind.CONSTANT_REMOVED)])

    finding = _finding(result, ChangeKind.CONSTANT_REMOVED)
    assert finding.effective_verdict is None
    assert result.verdict == Verdict.API_BREAK


def test_the_gate_mutates_verdict_only_and_never_drops_a_finding() -> None:
    old = _snapshot(_binary_only_function(SUBJECT, SUBJECT_MANGLED), from_headers=False)
    new = _snapshot(_binary_only_function(SUBJECT, SUBJECT_MANGLED), from_headers=False)
    claim = _claim(ChangeKind.TYPE_REMOVED)
    changes = [claim]

    demoted = demote_unproven_binary_api_breaks(changes, old, new)

    assert changes == [claim]
    assert demoted == [claim]


# ---------------------------------------------------------------------------
# Evidence predicate (model layer)
# ---------------------------------------------------------------------------


class TestSourceContractEvidence:
    def test_a_header_parse_declaration_is_evidence(self) -> None:
        assert has_observed_source_contract_evidence(
            _header_backed_function(SUBJECT, SUBJECT_MANGLED)
        )

    def test_a_debug_info_export_is_not_evidence(self) -> None:
        """(b) from a DWARF export is true of the binary, not of a header."""
        assert not has_observed_source_contract_evidence(
            _binary_only_function(SUBJECT, SUBJECT_MANGLED)
        )

    def test_a_legacy_public_visibility_is_not_evidence(self) -> None:
        legacy = Function(
            name=SUBJECT,
            mangled=SUBJECT_MANGLED,
            return_type="void",
            params=[],
            visibility=Visibility.PUBLIC,
        )
        assert not has_observed_source_contract_evidence(legacy)

    def test_a_non_binary_contract_judgement_is_evidence(self) -> None:
        """A public-header set or a source extractor placing (b) counts without (a)."""
        judged = Function(
            name=SUBJECT,
            mangled=SUBJECT_MANGLED,
            return_type="void",
            params=[],
            visibility=Visibility.PUBLIC,
            in_public_contract_fact=Fact.present(
                True, "declared in a supplied public header"
            ),
        )
        assert has_observed_source_contract_evidence(judged)

    def test_a_record_needs_a_header_parse_and_a_header_location(self) -> None:
        record = RecordType(
            name="lib::Gadget",
            kind="struct",
            source_header_fact=Fact.present("api.h"),
        )
        assert has_observed_source_contract_evidence(record, header_parsed=True)
        assert not has_observed_source_contract_evidence(record, header_parsed=False)

    def test_a_record_with_no_header_location_is_not_evidence(self) -> None:
        record = RecordType(name="lib::Helper", kind="class")
        assert not has_observed_source_contract_evidence(record, header_parsed=True)


# ---------------------------------------------------------------------------
# Detectors: hard-coded public_reachable replaced by observed evidence
# ---------------------------------------------------------------------------


class TestDetectorReachability:
    def test_std_reexport_without_evidence_is_unknown_not_proven(self) -> None:
        change = _build_std_reexport_change("std_x", "std::x", public_evidence=False)
        assert change.public_reachable is False
        assert change.reachability_state is ReachabilityState.UNKNOWN
        assert change.reachability_kind is None

    def test_std_reexport_with_evidence_is_proven_reachable(self) -> None:
        change = _build_std_reexport_change("std_x", "std::x", public_evidence=True)
        assert change.public_reachable is True
        assert change.reachability_state is ReachabilityState.PROVEN_REACHABLE
        assert change.reachability_kind == "direct_public_symbol"

    @staticmethod
    def _tag_pair(
        *, removed_fact: Fact[str | None] | None
    ) -> tuple[RecordType, RecordType]:
        removed = RecordType(
            name="ns::OldTag", kind="struct", source_header_fact=removed_fact
        )
        added = RecordType(name="ns::NewTag", kind="struct")
        return removed, added

    def test_tag_type_renamed_without_header_evidence_is_unknown(self) -> None:
        removed, added = self._tag_pair(removed_fact=None)
        change = _find_tag_rename_for_removed(
            removed,
            [added],
            {"_Z3fooI6OldTagEv"},
            {"_Z3fooI6NewTagEv"},
            header_parsed=False,
        )
        assert change is not None
        assert change.kind is ChangeKind.TAG_TYPE_RENAMED
        assert change.public_reachable is False
        assert change.reachability_state is ReachabilityState.UNKNOWN

    def test_tag_type_renamed_with_a_header_parse_is_proven_reachable(self) -> None:
        removed, added = self._tag_pair(removed_fact=Fact.present("api.h"))
        change = _find_tag_rename_for_removed(
            removed,
            [added],
            {"_Z3fooI6OldTagEv"},
            {"_Z3fooI6NewTagEv"},
            header_parsed=True,
        )
        assert change is not None
        assert change.public_reachable is True
        assert change.reachability_state is ReachabilityState.PROVEN_REACHABLE


# ---------------------------------------------------------------------------
# Real detectors, no injected findings (ADR-084 probes, binary-only input)
# ---------------------------------------------------------------------------


def _template_snapshot(
    arity_spelling: str, mangled: str, *, exported: bool
) -> AbiSnapshot:
    fn = Function(
        name=f"oneapi::dal::backend::primitives::sort<{arity_spelling}>",
        mangled=mangled,
        return_type="void",
        params=[],
        visibility=Visibility.PUBLIC,
        binary_exported_fact=Fact.present(exported, producer="dwarf"),
    )
    return AbiSnapshot(
        library="libdemo.so", version="1", functions=[fn], from_headers=False
    )


def test_a_binary_only_template_arity_change_is_not_api_break() -> None:
    """The reported shape: an exported instantiation gains a mandatory parameter."""
    old = _template_snapshot("int", "_ZN5sort3intEv", exported=True)
    new = _template_snapshot("int, bool", "_ZN5sort4boolEv", exported=True)

    result = compare(old, new)

    assert result.verdict != Verdict.API_BREAK


def test_a_binary_only_private_type_removal_is_not_api_break() -> None:
    old = AbiSnapshot(
        library="libdemo.so",
        version="1",
        types=[RecordType(name="lib::Helper", kind="class")],
        from_headers=False,
    )
    new = AbiSnapshot(library="libdemo.so", version="2", from_headers=False)

    result = compare(old, new)

    assert result.verdict != Verdict.API_BREAK


def test_an_exported_function_removal_stays_breaking() -> None:
    """Control: binary-proven breaks are not touched by the gate."""
    old = _snapshot(
        _binary_only_function("lib::api::run", "_ZN3lib3api3runEv"), from_headers=False
    )
    new = _snapshot(from_headers=False)

    result = compare(old, new)

    assert result.verdict == Verdict.BREAKING


# ---------------------------------------------------------------------------
# Real header-backed pipeline (castxml + gcc)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_castxml_records_carry_the_header_observation(tmp_path: Path) -> None:
    """The header-backed path the predicate is written against, end to end.

    A castxml dump stamps a located record and its functions as header
    observations; the same library dumped binary-only (debug info, no headers)
    stamps none. The gate therefore keeps a header-backed type removal and
    demotes the binary-only one.
    """
    import subprocess

    from abicheck.dumper import dump

    # `Gadget` is part of an exported signature in v1, so it is on the public
    # surface and GCC keeps its DWARF; v2 drops the type and its use.
    header_v1 = (
        "namespace lib {\nstruct Gadget { int a; };\n"
        "int use(const Gadget& g);\nint api(int x);\n}\n"
    )
    header_v2 = "namespace lib {\nint use(int g);\nint api(int x);\n}\n"
    source_v1 = (
        '#include "api.h"\nnamespace lib {\n'
        "int use(const Gadget& g) { return g.a; }\n"
        "int api(int x) { return x + 1; }\n}\n"
    )
    source_v2 = (
        "namespace lib {\nint use(int g) { return g + 1; }\n"
        "int api(int x) { return x + 1; }\n}\n"
    )

    def build(tag: str, header: str, source: str) -> tuple[Path, Path]:
        d = tmp_path / tag
        d.mkdir()
        (d / "api.h").write_text(header)
        (d / "lib.cpp").write_text(source)
        so = d / "libdemo.so"
        subprocess.run(
            [
                "g++",
                "-g",
                "-shared",
                "-fPIC",
                "-I",
                str(d),
                str(d / "lib.cpp"),
                "-o",
                str(so),
            ],
            check=True,
        )
        return so, d / "api.h"

    so1, hdr1 = build("v1", header_v1, source_v1)
    so2, hdr2 = build("v2", header_v2, source_v2)

    old_h = dump(so1, [hdr1], version="1")
    new_h = dump(so2, [hdr2], version="2")
    gadget = next(t for t in old_h.declarations.types if t.name.endswith("Gadget"))
    assert has_observed_source_contract_evidence(
        gadget, header_parsed=old_h.from_headers
    )
    header_removal = _finding(compare(old_h, new_h), ChangeKind.TYPE_REMOVED)
    assert header_removal.effective_verdict is None
    assert header_removal.modulation_rule is None

    old_b = dump(so1, [], version="1")
    new_b = dump(so2, [], version="2")
    assert not any(
        has_observed_source_contract_evidence(d, header_parsed=old_b.from_headers)
        for d in (*old_b.declarations.functions, *old_b.declarations.types)
    )
    binary_removal = _finding(compare(old_b, new_b), ChangeKind.TYPE_REMOVED)
    assert binary_removal.effective_verdict == Verdict.COMPATIBLE_WITH_RISK
    assert binary_removal.modulation_rule == BINARY_GATE_RULE_ID
