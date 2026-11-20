"""A name-only runtime match is excluded only when a symbol table can bind it.

ADR-089 A8. The oracle is the evidence-availability rule, stated independently
of the helper:

* A symbol the library's ELF table lists, with its binding, is binding
  evidence. A runtime-owned copy listed there is out of contract (the weak
  copy case), and so is a field-report static no table lists, because on ELF
  the table's completeness is itself the evidence that the library does not
  emit it.
* A target with no symbol table (PE, Mach-O) has no binding records. A name
  that merely looks like a runtime symbol cannot prove the library does not
  export it, so the finding keeps gating, at partial assurance, with its
  reason recorded.

Each expected value is written from that rule, not read back from the
classifier. The decisions fed in are hand-built, so the assertions do not
depend on the domain stage's own answer.
"""

from __future__ import annotations

import pytest

from abicheck.buildsource.export_accounting import (
    toolchain_runtime_name_predicate,
    toolchain_runtime_symbol_names,
)
from abicheck.contract_evaluation import ContractEvaluationDecision
from abicheck.contract_relevance_types import ContractAssurance, ContractRelevance
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import AbiSnapshot
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.pe_metadata import PeMetadata
from abicheck.policy.contract_toolchain_origin import (
    TOOLCHAIN_NAME_UNBOUND_REASON,
    TOOLCHAIN_RUNTIME_ORIGIN_REASON,
    exclude_toolchain_runtime_findings,
)

RUNTIME_NAME = "_ZZNSt8__detail15__to_chars_lenIjEEiT_E8__digits"
LIBRARY_NAME = "mylib_init"


def _gating() -> ContractEvaluationDecision:
    """The domain decision the field-report shape starts from: unresolved, gating."""
    return ContractEvaluationDecision(
        relevance=ContractRelevance.UNKNOWN_UNRESOLVED,
        reason_code="required_evidence_incomplete",
        assurance=ContractAssurance.COMPLETE,
    )


def _elf(symbols: list[tuple[str, SymbolBinding]]) -> AbiSnapshot:
    elf = ElfMetadata(
        soname="libmylib.so.1",
        needed=["libstdc++.so.6"],
        symbols=[
            ElfSymbol(name=n, binding=b, sym_type=SymbolType.OBJECT) for n, b in symbols
        ],
    )
    return AbiSnapshot(library="libmylib.so.1", version="1.0", elf=elf)


def _pe() -> AbiSnapshot:
    return AbiSnapshot(library="mylib.dll", version="1.0", pe=PeMetadata())


def _run(
    symbol: str,
    snapshots: list[AbiSnapshot],
    *,
    symbol_table_evidence: bool,
) -> ContractEvaluationDecision:
    """The stage's order for one removed finding, with the pipeline's own facts."""
    toolchain = frozenset().union(
        *(toolchain_runtime_symbol_names(s) for s in snapshots if s.elf is not None)
    )
    change = Change(kind=ChangeKind.FUNC_REMOVED, symbol=symbol, description="")
    (out,) = exclude_toolchain_runtime_findings(
        [change],
        [_gating()],
        toolchain,
        is_runtime_name=toolchain_runtime_name_predicate(snapshots),
        symbol_table_evidence=symbol_table_evidence,
    )
    return out


# --- PE and Mach-O: no symbol table, so no binding evidence -----------------


def test_pe_name_only_runtime_match_keeps_gating() -> None:
    out = _run(RUNTIME_NAME, [_pe()], symbol_table_evidence=False)
    assert out.relevance is ContractRelevance.UNKNOWN_UNRESOLVED
    assert out.reason_code == TOOLCHAIN_NAME_UNBOUND_REASON
    assert out.assurance is ContractAssurance.PARTIAL


def test_pe_is_never_proven_out_of_contract_by_name_alone() -> None:
    out = _run(RUNTIME_NAME, [_pe()], symbol_table_evidence=False)
    assert out.relevance is not ContractRelevance.PROVEN_OUT_OF_CONTRACT
    assert out.assurance is not ContractAssurance.COMPLETE


def test_pe_library_symbol_is_untouched_by_the_rule() -> None:
    # A library's own name is not a runtime name, so the rule never reaches it.
    out = _run(LIBRARY_NAME, [_pe()], symbol_table_evidence=False)
    assert out == _gating()


def test_an_in_contract_decision_is_not_relabelled_without_evidence() -> None:
    # The surface already places this finding in contract: the rule adds no
    # label to a decision that is not in question.
    decision = ContractEvaluationDecision(
        relevance=ContractRelevance.IN_CONTRACT,
        reason_code="export_root_membership",
        assurance=ContractAssurance.COMPLETE,
    )
    change = Change(kind=ChangeKind.FUNC_REMOVED, symbol=RUNTIME_NAME, description="")
    (out,) = exclude_toolchain_runtime_findings(
        [change],
        [decision],
        frozenset(),
        is_runtime_name=toolchain_runtime_name_predicate([_pe()]),
        symbol_table_evidence=False,
    )
    assert out == decision


# --- ELF: a symbol table is binding evidence, field-report path unchanged ----


def test_elf_weak_runtime_copy_listed_in_the_table_is_excluded() -> None:
    snap = _elf([(RUNTIME_NAME, SymbolBinding.WEAK)])
    out = _run(RUNTIME_NAME, [snap], symbol_table_evidence=True)
    assert out.relevance is ContractRelevance.PROVEN_OUT_OF_CONTRACT
    assert out.reason_code == TOOLCHAIN_RUNTIME_ORIGIN_REASON
    assert out.assurance is ContractAssurance.COMPLETE


def test_elf_field_report_static_no_table_lists_is_still_excluded() -> None:
    # The field report's name-only path: the table is complete and does not
    # list the symbol, so its absence is the evidence. Must not regress.
    snap = _elf([(LIBRARY_NAME, SymbolBinding.GLOBAL)])
    out = _run(RUNTIME_NAME, [snap], symbol_table_evidence=True)
    assert out.relevance is ContractRelevance.PROVEN_OUT_OF_CONTRACT
    assert out.reason_code == TOOLCHAIN_RUNTIME_ORIGIN_REASON


def test_elf_library_symbol_keeps_gating() -> None:
    snap = _elf([(LIBRARY_NAME, SymbolBinding.GLOBAL)])
    out = _run(LIBRARY_NAME, [snap], symbol_table_evidence=True)
    assert out == _gating()


# --- the evidence fact decides, not the format of the snapshot --------------


@pytest.mark.parametrize(
    ("symbol_table_evidence", "expected"),
    [
        (True, ContractRelevance.PROVEN_OUT_OF_CONTRACT),
        (False, ContractRelevance.UNKNOWN_UNRESOLVED),
    ],
)
def test_the_same_name_follows_the_evidence_fact(
    symbol_table_evidence: bool, expected: ContractRelevance
) -> None:
    out = _run(
        RUNTIME_NAME,
        [_elf([(LIBRARY_NAME, SymbolBinding.GLOBAL)])],
        symbol_table_evidence=symbol_table_evidence,
    )
    assert out.relevance is expected
