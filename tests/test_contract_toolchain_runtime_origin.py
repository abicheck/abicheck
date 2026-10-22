"""C++ runtime / toolchain symbols are outside the contract (field report A).

A C-API contract check returned exit 1 because six removed libstdc++
function-local statics (``std::__detail::__to_chars...::__digits``, weak /
unique objects instantiated inline from the runtime's headers) were left
``UNKNOWN_UNRESOLVED``: no header declares them, so the contract could not
place them, and an unresolved finding gates. The runtime owns those symbols,
so they are excluded with a reason and do not gate.

The expected values below are written by hand from the symbol shapes, not
read back from the classifier, so a wrong classification fails here. The
controls are the library's own symbols (``extern "C"``, a public C++ function,
the library's own inline function's local static, a user ``std::hash``
specialization, a std template instantiated over the library's own type).
Those must keep gating.
"""

from __future__ import annotations

import pytest

from abicheck.buildsource.export_accounting import toolchain_runtime_symbol_names
from abicheck.checker_policy import ChangeKind
from abicheck.contract_evaluation import (
    ContractEvaluationDecision,
    evaluate_change_contract_relevance,
)
from abicheck.contract_relevance_types import (
    NON_ENTITY_RELEVANCE,
    ContractAssurance,
    ContractMode,
    ContractRelevance,
)
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import AbiSnapshot
from abicheck.model.change import Change
from abicheck.policy.contract_toolchain_origin import (
    TOOLCHAIN_RUNTIME_ORIGIN_REASON,
    exclude_toolchain_runtime_findings,
)
from abicheck.policy.public_surface_index import PublicSurface

#: Runtime-owned symbol shapes the field report and the libstdc++/libc++ ABI
#: produce: a function-local static, its guard, or a std static member.
RUNTIME_SYMBOLS = {
    "to_chars_digits_unsigned": "_ZZNSt8__detail15__to_chars_lenIjEEiT_E8__digits",
    "to_chars_digits_unsigned_long": (
        "_ZZNSt8__detail15__to_chars_lenImEEiT_E8__digits"
    ),
    "to_chars_digits_guard": "_ZGVZNSt8__detail15__to_chars_lenIjEEiT_E8__digits",
    "use_facet_local_static": "_ZZSt9use_facetIcEERKT_RKSt6localeE5cache",
    "use_facet_local_guard": "_ZGVZSt9use_facetIcEERKT_RKSt6localeE5cache",
    "locale_ctype_id_guard": "_ZGVNSt5ctypeIcE2idE",
    "locale_ctype_id_object": "_ZNSt5ctypeIcE2idE",
    "gnu_cxx_allocator_local": "_ZZN9__gnu_cxx13new_allocatorIcE8allocateEmE1x",
    "libcxx_inline_ns_local": "_ZZNSt3__110__to_charsE1x",
}

#: Symbols the library under test owns. None may be classified as runtime.
USER_SYMBOLS = {
    "extern_c_api": "mylib_init",
    "public_cxx_function": "_Z3foov",
    "own_inline_local_static": "_ZZN5mylib3fooEvE1x",
    "own_inline_local_guard": "_ZGVZN5mylib3fooEvE1x",
    "std_hash_specialization_local": "_ZZNKSt4hashI6MyTypeEclERKS0_E4salt",
    "std_hash_specialization_guard": "_ZGVZNKSt4hashI6MyTypeEclERKS0_E4salt",
    "std_hash_specialization_member": "_ZNKSt4hashI6MyTypeEclERKS0_",
    "std_template_over_own_type_local": "_ZZNSt6vectorI6MyTypeSaIS0_EE1fEvE1x",
}


def _snapshot(
    symbols: list[str],
    *,
    soname: str = "libmylib.so.1",
    needed: list[str] | None = None,
) -> AbiSnapshot:
    """A binary-only snapshot whose ELF table carries *symbols*.

    Every symbol is bound ``STB_GNU_UNIQUE``, the binding a runtime's inline
    local static carries, so the classification cannot be riding on binding.
    """
    elf = ElfMetadata(
        soname=soname,
        needed=["libstdc++.so.6"] if needed is None else needed,
        symbols=[
            ElfSymbol(name=s, binding=SymbolBinding.UNIQUE, sym_type=SymbolType.OBJECT)
            for s in symbols
        ],
    )
    return AbiSnapshot(library=soname, version="1.0", elf=elf)


@pytest.mark.parametrize(
    "symbol", list(RUNTIME_SYMBOLS.values()), ids=list(RUNTIME_SYMBOLS)
)
def test_runtime_symbol_families_are_toolchain_owned(symbol: str) -> None:
    assert symbol in toolchain_runtime_symbol_names(_snapshot([symbol]))


@pytest.mark.parametrize("symbol", list(USER_SYMBOLS.values()), ids=list(USER_SYMBOLS))
def test_library_owned_symbols_are_never_toolchain_owned(symbol: str) -> None:
    assert symbol not in toolchain_runtime_symbol_names(_snapshot([symbol]))


def test_only_the_runtime_symbols_are_selected_from_a_mixed_table() -> None:
    names = [*RUNTIME_SYMBOLS.values(), *USER_SYMBOLS.values()]
    selected = toolchain_runtime_symbol_names(_snapshot(names))
    assert selected == frozenset(RUNTIME_SYMBOLS.values())


def test_auditing_the_runtime_itself_keeps_its_exports_gating() -> None:
    # libstdc++'s own exports are its API, not a dependency's leak: when the
    # audited library is the runtime, its own symbols are not excluded.
    libstdcxx_family = [
        symbol
        for key, symbol in RUNTIME_SYMBOLS.items()
        if key != "libcxx_inline_ns_local"  # libc++'s, a real leak even here
    ]
    snap = _snapshot(libstdcxx_family, soname="libstdc++.so.6", needed=[])
    assert toolchain_runtime_symbol_names(snap) == frozenset()


def test_snapshot_without_an_elf_table_selects_nothing() -> None:
    assert toolchain_runtime_symbol_names(AbiSnapshot(library="x", version="1")) == (
        frozenset()
    )


# A header-resolvable surface that does not declare the removed symbol: the
# header domain can place it nowhere, which is what gated the field report's
# run under a stated contract.
_HEADERS = PublicSurface(
    resolvable=True,
    public_symbols={"mylib_init"},
    all_symbols={"mylib_init"},
)


def _removed(symbol: str) -> Change:
    return Change(kind=ChangeKind.FUNC_REMOVED, symbol=symbol, description="")


def _excluded(
    symbol: str,
    toolchain: frozenset[str],
    *,
    mode: ContractMode = ContractMode.PUBLIC,
    allowlist: frozenset[str] | None = None,
):
    """The stage's order: the domain decision, then the runtime exclusion."""
    change = _removed(symbol)
    decision = evaluate_change_contract_relevance(
        change,
        _HEADERS,
        _HEADERS,
        mode=mode,
        public_surface_allowlist=allowlist,
    )
    (out,) = exclude_toolchain_runtime_findings([change], [decision], toolchain)
    return decision, out


@pytest.mark.parametrize("mode", [ContractMode.PUBLIC, ContractMode.ALL])
@pytest.mark.parametrize(
    "symbol", list(RUNTIME_SYMBOLS.values()), ids=list(RUNTIME_SYMBOLS)
)
def test_removed_runtime_symbol_is_excluded_with_its_reason(
    symbol: str, mode: ContractMode
) -> None:
    toolchain = toolchain_runtime_symbol_names(_snapshot([symbol]))
    _, out = _excluded(symbol, toolchain, mode=mode)
    assert out.relevance is ContractRelevance.PROVEN_OUT_OF_CONTRACT
    assert (
        out.reason_code == TOOLCHAIN_RUNTIME_ORIGIN_REASON == "toolchain_runtime_origin"
    )


def test_field_report_shape_gates_without_the_exclusion() -> None:
    # Under a stated public contract, the removed runtime static is
    # UNKNOWN_UNRESOLVED (the gating state) until the exclusion applies.
    symbol = RUNTIME_SYMBOLS["to_chars_digits_unsigned"]
    decision, _ = _excluded(symbol, frozenset())
    assert decision.relevance is ContractRelevance.UNKNOWN_UNRESOLVED
    assert decision.reason_code == "required_evidence_incomplete"


#: Library symbols absent from the header surface: the contract cannot place
#: them, so they stay unresolved and gate. (``extern_c_api`` is in the surface;
#: see ``test_an_extern_c_symbol_in_the_public_surface_is_in_contract``.)
_UNDECLARED_USER_SYMBOLS = [v for k, v in USER_SYMBOLS.items() if k != "extern_c_api"]


@pytest.mark.parametrize("symbol", _UNDECLARED_USER_SYMBOLS)
def test_removed_library_symbol_keeps_gating_under_public(symbol: str) -> None:
    toolchain = toolchain_runtime_symbol_names(_snapshot(list(USER_SYMBOLS.values())))
    decision, out = _excluded(symbol, toolchain)
    assert out is decision  # the exclusion did not touch it
    assert decision.reason_code != TOOLCHAIN_RUNTIME_ORIGIN_REASON
    assert decision.relevance is ContractRelevance.UNKNOWN_UNRESOLVED


def test_an_extern_c_symbol_in_the_public_surface_is_in_contract() -> None:
    toolchain = toolchain_runtime_symbol_names(_snapshot(list(USER_SYMBOLS.values())))
    _, out = _excluded("mylib_init", toolchain)
    assert out.relevance is ContractRelevance.IN_CONTRACT
    assert out.reason_code == "public_root_membership"


def test_a_caller_declared_public_runtime_symbol_keeps_gating() -> None:
    # An explicit --public-symbol (or committed export) is the caller's promise:
    # the runtime exclusion must not override it.
    symbol = RUNTIME_SYMBOLS["to_chars_digits_unsigned"]
    toolchain = toolchain_runtime_symbol_names(_snapshot([symbol]))
    _, out = _excluded(symbol, toolchain, allowlist=frozenset({symbol}))
    assert out.relevance is ContractRelevance.IN_CONTRACT
    assert out.reason_code == "public_root_membership"


def test_not_applicable_findings_are_never_reclassified() -> None:
    # Cross-source hygiene findings are scored by policy as before; the
    # exclusion must not turn one into an out-of-contract entity.
    symbol = RUNTIME_SYMBOLS["to_chars_digits_unsigned"]
    change = _removed(symbol)
    not_applicable = ContractEvaluationDecision(
        relevance=NON_ENTITY_RELEVANCE,
        reason_code="non_entity_finding",
        assurance=ContractAssurance.COMPLETE,
    )
    (out,) = exclude_toolchain_runtime_findings(
        [change], [not_applicable], frozenset({symbol})
    )
    assert out is not_applicable


def test_exclusion_pairs_each_finding_with_its_own_decision() -> None:
    runtime = RUNTIME_SYMBOLS["to_chars_digits_unsigned"]
    user = USER_SYMBOLS["own_inline_local_static"]
    toolchain = toolchain_runtime_symbol_names(_snapshot([runtime, user]))
    changes = [_removed(runtime), _removed(user)]
    decisions = [
        evaluate_change_contract_relevance(
            c, _HEADERS, _HEADERS, mode=ContractMode.PUBLIC
        )
        for c in changes
    ]
    out = exclude_toolchain_runtime_findings(changes, decisions, toolchain)
    assert out[0].reason_code == TOOLCHAIN_RUNTIME_ORIGIN_REASON
    assert out[1] is decisions[1]
    assert out[1].relevance is ContractRelevance.UNKNOWN_UNRESOLVED
