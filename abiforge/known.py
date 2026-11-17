"""Recorded corpus mismatches: strict xfails, each tied to a known-gaps entry.

A ``Known`` entry says: for this operator on these seeds, under configs where
``when`` holds, the cell reconciles to exactly ``cls``. The corpus test
xfails such a cell only while that class reproduces; any other class, a pass
included, fails, so a fixed detector forces the entry (and its
``docs/contribute/known-gaps.md`` row, named in ``gap``) to be removed.

Nothing here is fixed in abicheck: entries record detector findings, they do
not excuse harness bugs (``oracle-conflict`` is never allowed).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from abiforge.model.config import Config

_CLASSES = frozenset({"cxx_classes"})

GAP_CLANG_CHAR = (
    "A clang-built plain-`char` signedness flip reads `NO_CHANGE` at DWARF depth"
)
GAP_CASTXML_CONTRACT = "castxml records no GNU contract attribute on a function"
GAP_VA_LIST = "Only the clang header backend marks a `va_list` parameter"
GAP_GCC_THROW = (
    "The native gcc header backend records no dynamic exception specification"
)
GAP_O2_STD = (
    "An `-O2` rebuild of a C++ library that uses `std::string` reads as a risk on gcc, "
    "or a break on MinGW"
)
GAP_NEW_LIB_TAGS = "A version requirement on a newly needed library reads as compatible, whatever its version"
GAP_CLANG_INLINE_STATIC = "An inline function's static local, exported by clang, reads as an undeclared export"
GAP_UNIQUE_O2_DWARF = "A GNU-unique static whose inline owner was inlined away is not compared at DWARF depth"
GAP_DUAL_ABI_COW_CHURN = "A C++20 dual-ABI flip is not recognized when the old-ABI string's members are exported"
GAP_FLAG_INVISIBLE_LAYOUT = (
    "A config-level layout flag a public header cannot carry: stripped {B,H} reads the "
    "layout without it (no signal yet for reduced assurance)"
)
GAP_HEADER_TRAIT_STRIPPED = (
    "A value-ABI trait, a packed record or a struct return convention is not emitted from "
    "stripped public headers"
)


def _always(_: Config) -> bool:
    return True


def _ev(*levels: str) -> Callable[[Config], bool]:
    return lambda c: c.evidence in levels


@dataclass(frozen=True)
class Known:
    op: str
    seeds: frozenset[str]
    cls: str
    when: Callable[[Config], bool]
    reason: str
    gap: str
    # The exact finding: ``abiforge.model.expect.issue_keys`` of the problems,
    # and the abicheck verdicts it was observed with. A cell xfails only when
    # class, issues and verdict all match; anything else fails.
    issues: frozenset[str]
    verdicts: frozenset[str]
    # Another class the same finding reconciles to under some consumers: an
    # over-strong verdict is ``false-positive`` where every consumer runs and
    # compiles, ``wrong-verdict`` where one observes a source break (a
    # compiler that rejects an implicit declaration) -- the finding is the same.
    also_cls: frozenset[str] = frozenset()

    def expected_cls(self, execution_available: bool) -> str:
        """The class this finding reconciles to on a host with or without execution.

        ``false-clean`` and ``false-positive`` need the execution oracle (to
        show the break, or that consumers still run); where it is unavailable
        (a non-ELF host) the same issues read as the plain verdict or kind
        mismatch ``reconcile`` gives them.
        """
        if execution_available or self.cls not in ("false-clean", "false-positive"):
            return self.cls
        verdict_issue = any(
            i.startswith(("weaker-than:", "stronger-than:")) for i in self.issues
        )
        return "wrong-verdict" if verdict_issue else "wrong-kind"

    def matches(
        self,
        cls: str,
        issues: frozenset[str],
        verdict: str,
        execution_available: bool = True,
    ) -> bool:
        return (
            (cls == self.expected_cls(execution_available) or cls in self.also_cls)
            and issues == self.issues
            and verdict in self.verdicts
        )


def _hb(backend: str) -> Callable[[Config], bool]:
    """Cells whose headers the named backend dumps (castxml is the reference)."""
    return lambda c: c.evidence == "headers" and c.header_backend == backend


def _backend_gap(
    op: str,
    seeds: frozenset[str],
    backend: str,
    cls: str,
    reason: str,
    gap: str,
    issues: frozenset[str],
    verdict: str,
    also: Callable[[Config], bool] = _always,
) -> Known:
    hb = _hb(backend)
    return Known(
        op,
        seeds,
        cls,
        lambda c: hb(c) and also(c),
        reason,
        gap,
        issues=issues,
        verdicts=frozenset({verdict}),
    )


_MORE = frozenset({"cxx_more"})

_ELF = frozenset({"c_elf"})


def _contract_gap(op: str, kind: str) -> Known:
    return _backend_gap(
        op,
        _ELF,
        "castxml",
        "wrong-verdict",
        "castxml writes no nonnull/returns_nonnull on a <Function>: the "
        "contract change reads NO_CHANGE",
        GAP_CASTXML_CONTRACT,
        frozenset({f"missing:{kind}", "weaker-than:COMPATIBLE_WITH_RISK"}),
        "NO_CHANGE",
    )


def _va_list_gap(op: str, kind: str, backend: str) -> Known:
    return _backend_gap(
        op,
        _ELF,
        backend,
        "wrong-kind",
        f"{backend} never establishes Param.is_va_list: only func_params_changed",
        GAP_VA_LIST,
        frozenset({f"missing:{kind}"}),
        "BREAKING",
    )


# Header-backend parity gaps the corpus found (tests/
# test_header_backend_fact_gaps_integration.py holds their canaries).
_BACKEND_GAPS: tuple[Known, ...] = (
    _contract_gap("func_contract_attribute_added", "FUNC_CONTRACT_ATTRIBUTE_ADDED"),
    _contract_gap("func_contract_attribute_removed", "FUNC_CONTRACT_ATTRIBUTE_REMOVED"),
    *(
        _va_list_gap(op, kind, backend)
        for op, kind in (
            ("param_became_va_list", "PARAM_BECAME_VA_LIST"),
            ("param_lost_va_list", "PARAM_LOST_VA_LIST"),
        )
        for backend in ("castxml", "gcc")
    ),
    _backend_gap(
        "func_exception_spec_widened",
        _MORE,
        "gcc",
        "wrong-verdict",
        "the gcc plugin emits no dynamic exception specification",
        GAP_GCC_THROW,
        frozenset(
            {"missing:FUNC_EXCEPTION_SPEC_CHANGED", "weaker-than:COMPATIBLE_WITH_RISK"}
        ),
        "COMPATIBLE",
    ),
)


def _headers_vis_default(c: Config) -> bool:
    return c.evidence == "headers" and c.flags.visibility == "default"


_ELF = frozenset({"c_elf"})
_ALT = frozenset({"c_elf_alt"})
_XA = frozenset({"cxx_alt"})
_BREAKING = frozenset({"BREAKING"})

# Platform-level operators (``abiforge.operators.platform``).
_PLATFORM: tuple[Known, ...] = (
    Known(
        "glibc_version_required_added",
        _ELF,
        "wrong-kind",
        lambda c: c.producer.family == "gcc" and c.flags.opt == "O2",
        "the -O2 gcc build needed nothing from libc before, so GLIBC_2.25 comes "
        "with a newly needed libc.so.6 and reads symbol_version_required_added_compat",
        GAP_NEW_LIB_TAGS,
        issues=frozenset({"missing:SYMBOL_VERSION_REQUIRED_ADDED"}),
        verdicts=frozenset({"COMPATIBLE_WITH_RISK"}),
    ),
    Known(
        "std_string_abi_flipped",
        _XA,
        "wrong-kind",
        lambda c: c.std.year >= 2020 and c.producer.family == "gcc",
        "from C++20 the new-ABI std::string is constexpr and exports nothing, "
        "while the old COW string exports ~37 weak members (_ZNSs...): they "
        "dilute the __cxx11 marker share below the flip threshold",
        GAP_DUAL_ABI_COW_CHURN,
        issues=frozenset({"missing:GLIBCXX_DUAL_ABI_FLIP_DETECTED"}),
        verdicts=_BREAKING,
    ),
    Known(
        "gnu_unique_static_added",
        frozenset({"cxx_more"}),
        "wrong-kind",
        lambda c: (
            c.producer.family == "gcc" and c.flags.opt == "O2" and c.evidence == "dwarf"
        ),
        "-O2 inlines edit_ctr away; its STB_GNU_UNIQUE static is still exported, "
        "but the DWARF-depth compare reports only func_added",
        GAP_UNIQUE_O2_DWARF,
        issues=frozenset({"missing:SYMBOL_BINDING_BECAME_UNIQUE"}),
        verdicts=frozenset({"COMPATIBLE"}),
    ),
    Known(
        "gnu_unique_disabled",
        _XA,
        "wrong-verdict",
        lambda c: (
            c.producer.family == "gcc" and c.flags.opt == "O2" and c.evidence == "dwarf"
        ),
        "-O2 inlines uniq_counter away; its static loses STB_GNU_UNIQUE in the "
        "export table, but the DWARF-depth compare reads NO_CHANGE",
        GAP_UNIQUE_O2_DWARF,
        issues=frozenset(
            {"missing:SYMBOL_BINDING_LOST_UNIQUE", "weaker-than:COMPATIBLE_WITH_RISK"}
        ),
        verdicts=frozenset({"NO_CHANGE"}),
    ),
    Known(
        "gnu_unique_static_added",
        frozenset({"cxx_more"}),
        "false-positive",
        lambda c: c.producer.family == "clang" and c.evidence == "headers",
        "clang exports edit_ctr's static local weak; against the header it "
        "reads as an undeclared export (RISK) though no source can name it",
        GAP_CLANG_INLINE_STATIC,
        issues=frozenset({"stronger-than:COMPATIBLE"}),
        verdicts=frozenset({"COMPATIBLE_WITH_RISK"}),
    ),
)


KNOWN: tuple[Known, ...] = (
    # Stripped {B,H}: a config-level layout flag the public header cannot carry
    # (plan §11.2 WS-6 A3; the reduced-assurance signal is an ADR question).
    Known(
        "field_removed_without_shrink",
        frozenset({"cxx_more"}),
        "wrong-kind",
        lambda c: c.stripped and c.header_backend == "clang" and c.flags.pack == 1,
        "-fpack-struct=1 leaves no hole: the header dump lays out the record without "
        "the flag, so the offset and size changes read as the field removal alone",
        GAP_FLAG_INVISIBLE_LAYOUT,
        issues=frozenset({"none-of:TYPE_FIELD_OFFSET_CHANGED", "none-of:TYPE_SIZE_CHANGED"}),
        verdicts=frozenset({"BREAKING"}),
    ),
    Known(
        "array_grown_into_padding",
        frozenset({"cxx_more"}),
        "wrong-kind",
        lambda c: c.stripped and c.flags.pack == 1,
        "-fpack-struct=1 leaves no padding: the grown array reads as a type change, not "
        "an offset or size change, without the layout flag",
        GAP_FLAG_INVISIBLE_LAYOUT,
        issues=frozenset({"none-of:TYPE_FIELD_OFFSET_CHANGED", "none-of:TYPE_SIZE_CHANGED"}),
        verdicts=frozenset({"BREAKING"}),
    ),
    Known(
        "field_added_in_tail_padding",
        frozenset({"cxx_more"}),
        "false-clean",
        lambda c: c.stripped and c.flags.pack == 1,
        "-fpack-struct=1 leaves no tail padding: the header dump keeps the padding, so "
        "the grown record reads COMPATIBLE while the binary breaks (sizeof 5 -> 6)",
        GAP_FLAG_INVISIBLE_LAYOUT,
        issues=frozenset(
            {"missing:SONAME_BUMP_RECOMMENDED", "none-of:TYPE_SIZE_CHANGED", "weaker-than:BREAKING"}
        ),
        verdicts=frozenset({"COMPATIBLE"}),
    ),
    Known(
        "field_inserted_in_padding",
        frozenset({"cxx_more"}),
        "false-clean",
        lambda c: c.stripped and c.flags.pack == 1,
        "-fpack-struct=1 leaves no hole: an inserted field moves the offset of the field "
        "after it, which the header dump (no flag) does not show, so the change reads COMPATIBLE",
        GAP_FLAG_INVISIBLE_LAYOUT,
        issues=frozenset(
            {
                "missing:SONAME_BUMP_RECOMMENDED",
                "none-of:TYPE_FIELD_OFFSET_CHANGED",
                "none-of:TYPE_SIZE_CHANGED",
                "weaker-than:BREAKING",
            }
        ),
        verdicts=frozenset({"COMPATIBLE"}),
    ),
    Known(
        "enum_append_wide_value",
        frozenset({"c_core"}),
        "false-clean",
        lambda c: c.stripped and c.header_backend == "clang" and c.flags.short_enums,
        "-fshort-enums: appending a wide value grows the enum from 1 to 2 bytes; the header "
        "dump without the flag sees an enum of int size, so the change reads COMPATIBLE",
        GAP_FLAG_INVISIBLE_LAYOUT,
        issues=frozenset({"missing:SONAME_BUMP_RECOMMENDED", "weaker-than:BREAKING"}),
        verdicts=frozenset({"COMPATIBLE"}),
    ),
    Known(
        "enum_underlying_narrow",
        frozenset({"cxx_classes"}),
        "false-positive",
        lambda c: c.stripped and c.flags.short_enums,
        "-fshort-enums already sizes the enum to its values, so narrowing the underlying "
        "type leaves the size unchanged (both consumers run); the header dump without the "
        "flag reads ENUM_UNDERLYING_SIZE_CHANGED (BREAKING)",
        GAP_FLAG_INVISIBLE_LAYOUT,
        issues=frozenset({"stronger-than:COMPATIBLE_WITH_RISK"}),
        verdicts=frozenset({"BREAKING"}),
    ),
    # Stripped {B,H}: a header-visible fact the dump does not emit (plan §11.2 WS-2).
    Known(
        "by_value_struct_became_nontrivial",
        frozenset({"cxx_alt"}),
        "wrong-kind",
        lambda c: c.stripped and c.header_backend == "clang",
        "the clang header dump reports TRIVIALLY_COPYABLE_LOST for a class that loses "
        "triviality but not VALUE_ABI_TRAIT_CHANGED, which the edit's ABI meaning requires",
        GAP_HEADER_TRAIT_STRIPPED,
        issues=frozenset({"missing:VALUE_ABI_TRAIT_CHANGED"}),
        verdicts=frozenset({"BREAKING"}),
    ),
    Known(
        "record_lost_trivially_copyable",
        frozenset({"cxx_more"}),
        "wrong-kind",
        lambda c: c.stripped and c.producer.family == "clang",
        "a clang-built class that loses triviality reads TRIVIALLY_COPYABLE_LOST without "
        "VALUE_ABI_TRAIT_CHANGED from stripped headers",
        GAP_HEADER_TRAIT_STRIPPED,
        issues=frozenset({"missing:VALUE_ABI_TRAIT_CHANGED"}),
        verdicts=frozenset({"BREAKING"}),
    ),
    Known(
        "returned_struct_grew_past_registers",
        frozenset({"c_elf_alt"}),
        "wrong-kind",
        lambda c: c.stripped and c.header_backend == "clang",
        "a struct that grows past the return registers changes its return convention, which "
        "the stripped header dump does not report as STRUCT_RETURN_CONVENTION_CHANGED",
        GAP_HEADER_TRAIT_STRIPPED,
        issues=frozenset({"missing:STRUCT_RETURN_CONVENTION_CHANGED"}),
        verdicts=frozenset({"BREAKING"}),
    ),
    Known(
        "record_became_packed",
        frozenset({"c_elf"}),
        "wrong-kind",
        lambda c: c.stripped and c.header_backend == "castxml" and c.flags.short_enums,
        "a record that becomes __attribute__((packed)) is a header fact, but the castxml "
        "dump does not report STRUCT_PACKING_CHANGED alongside the layout kinds",
        GAP_HEADER_TRAIT_STRIPPED,
        issues=frozenset({"missing:STRUCT_PACKING_CHANGED"}),
        verdicts=frozenset({"BREAKING"}),
    ),
    Known(
        "char_made_unsigned",
        frozenset({"c_elf"}),
        "wrong-verdict",
        # clang writes no -f switches into DW_AT_producer and nothing else in
        # DWARF or the headers carries plain-char signedness; gcc records
        # -funsigned-char and reads char_signedness_changed (#1980).
        lambda c: (
            c.objfmt == "elf"
            and c.producer.family == "clang"
            and c.evidence in ("dwarf", "headers")
            and not c.stripped  # {B,H} reads NO_CHANGE clean: see GAP_FLAG_INVISIBLE_LAYOUT
        ),
        "a clang -funsigned-char rebuild reads NO_CHANGE: clang's DW_AT_producer "
        "records no switches, so the flip is invisible without build data",
        GAP_CLANG_CHAR,
        issues=frozenset(
            {"missing:CHAR_SIGNEDNESS_CHANGED", "weaker-than:COMPATIBLE_WITH_RISK"}
        ),
        verdicts=frozenset({"NO_CHANGE"}),
    ),
    Known(
        "rebuild_optimized",
        frozenset({"c_core"}),
        "false-positive",
        # i386 -O0 guards fill_rec's frame and calls __stack_chk_fail; -O2 needs no
        # canary. With DWARF the per-unit census shows the protector unchanged; the
        # g_counter placement drop (0x4010 -> 0x4008) is demoted at every level.
        lambda c: (
            c.objfmt == "elf"
            and c.producer.family == "gcc"
            and c.flags.bits == 32
            and c.evidence == "binary"
        ),
        "-O2 leaves no frame that needs a stack canary; without debug info no unit "
        "records -fstack-protector-strong, so stack_canary_removed keeps its RISK "
        "(marked as weaker evidence) while every consumer still runs",
        GAP_O2_STD,
        issues=frozenset({"stronger-than:COMPATIBLE"}),
        verdicts=frozenset({"COMPATIBLE_WITH_RISK"}),
    ),
    Known(
        "rebuild_optimized",
        frozenset({"cxx_stdlib"}),
        "false-positive",
        # PE records no binding, so the coalesced-copy rule has no signal there
        # (ADR-089's PE limit); against libc++ (macOS) the rebuild reads NO_CHANGE.
        # gcc (MinGW) is libstdc++; the old probe of the installed driver made the
        # record depend on the runner. A pinned family is the same condition here.
        lambda c: c.objfmt == "pe" and c.producer.family == "gcc",
        "MinGW: -O2 inlines the std::string instantiations the -O0 build exported; a PE "
        "export carries no WEAK binding, so their loss reads func_removed / "
        "func_visibility_changed (BREAKING) while every consumer still runs",
        GAP_O2_STD,
        issues=frozenset({"stronger-than:COMPATIBLE"}),
        verdicts=frozenset({"BREAKING", "COMPATIBLE_WITH_RISK"}),
    ),
    *_BACKEND_GAPS,
    *_PLATFORM,
)


def known_mismatch(op: str, seed: str, config: Config) -> Known | None:
    for k in KNOWN:
        if k.op == op and seed in k.seeds and k.when(config):
            return k
    return None
