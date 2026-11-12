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

from tests._edit_corpus_config import Config
from tests._edit_corpus_toolchains import uses_libstdcxx

_CLASSES = frozenset({"cxx_classes"})

GAP_DWARF_PURE = "A GCC-built DWARF snapshot cannot tell that a virtual became pure"
GAP_CLANG_CHAR = (
    "A clang-built plain-`char` signedness flip reads `NO_CHANGE` at DWARF depth"
)
GAP_CASTXML_VAR_ALIGN = (
    "castxml headers read a variable's alignment specifier only as a lower bound"
)
GAP_CASTXML_CONTRACT = "castxml records no GNU contract attribute on a function"
GAP_VA_LIST = "Only the clang header backend marks a `va_list` parameter"
GAP_GCC_THROW = (
    "The native gcc header backend records no dynamic exception specification"
)
GAP_GCC_UNEXPORTED = (
    "The native gcc header backend keeps an unexported declaration PUBLIC"
)
GAP_CASTXML_TRAITS = "castxml records no standard-layout trait or data size"
GAP_TAG_DECL = (
    "clang and gcc header snapshots of the corpus seed drop a renamed tag's "
    "instantiation declaration"
)
GAP_TYPEDEF_RETURN = (
    "A return type re-spelled through a renamed typedef of the same type reads "
    "`func_return_changed`"
)
GAP_GCC_TYPEDEF_RETURN = (
    "The native gcc header backend spells a typedef'd return type canonically"
)
GAP_CASTXML_STD_LEAF = (
    "castxml's bare `std` leaf hides a std type passed by value from the idiom check"
)
GAP_CLOSURE_RETURN = (
    "The unspecified-return check does not know castxml's or clang's closure spelling"
)
GAP_CPU_DISPATCH = (
    "The CPU-dispatch roll-up reads the removal of declared ISA kernels as a risk"
)
GAP_PIMPL_BARE_HOLDER = (
    "The inline-body rename check misses a pimpl holder castxml and clang name by "
    "its bare leaf"
)
GAP_O2_STD = (
    "An `-O2` rebuild of a C++ library that uses `std::string` reads as a risk on gcc, "
    "or a break on MinGW"
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
    # The exact finding: ``_edit_corpus_expect.issue_keys`` of the problems,
    # and the abicheck verdicts it was observed with. A cell xfails only when
    # class, issues and verdict all match; anything else fails.
    issues: frozenset[str]
    verdicts: frozenset[str]

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
            cls == self.expected_cls(execution_available)
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


def _unexported_gap(op: str, seeds: frozenset[str], kind: str) -> Known:
    return _backend_gap(
        op,
        seeds,
        "gcc",
        "wrong-kind",
        "the gcc backend records the unexported declaration PUBLIC, so the "
        "pair does not match and the change reads func_added",
        GAP_GCC_UNEXPORTED,
        frozenset({f"missing:{kind}"}),
        "COMPATIBLE",
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
    _unexported_gap("declared_exports_defined", _ELF, "FUNC_EXPORT_ADDED"),
    _unexported_gap("inline_function_outlined", _MORE, "FUNC_LOST_INLINE"),
    *(
        _backend_gap(
            op,
            _MORE,
            "castxml",
            "wrong-verdict",
            "castxml records no standard-layout trait or data size",
            GAP_CASTXML_TRAITS,
            frozenset({f"missing:{kind}", "weaker-than:COMPATIBLE_WITH_RISK"}),
            "NO_CHANGE",
        )
        for op, kind in (
            ("standard_layout_lost", "STANDARD_LAYOUT_LOST"),
            ("record_became_pod_tail_padding", "TAIL_PADDING_REUSE_CHANGED"),
        )
    ),
    *(
        _backend_gap(
            "tag_type_renamed",
            _MORE,
            backend,
            "wrong-kind",
            "the new run<quick_tag> declaration is missing from the snapshot, so "
            "the rename has no added symbol to pair",
            GAP_TAG_DECL,
            frozenset({"missing:TAG_TYPE_RENAMED"}),
            "BREAKING",
        )
        for backend in ("clang", "gcc")
    ),
    _backend_gap(
        "version_stamp_typedef_bumped",
        _ELF,
        "gcc",
        "wrong-verdict",
        "the gcc plugin spells the typedef'd return canonically: no change seen",
        GAP_GCC_TYPEDEF_RETURN,
        frozenset({"missing:TYPEDEF_VERSION_SENTINEL", "weaker-than:COMPATIBLE"}),
        "NO_CHANGE",
    ),
    _backend_gap(
        "stl_by_value_function_added",
        frozenset({"cxx_stdlib"}),
        "castxml",
        "wrong-kind",
        "castxml spells std::string as `string`; the std-by-value check misses it",
        GAP_CASTXML_STD_LEAF,
        frozenset({"missing:PUBLIC_API_EXPOSES_STL_BY_VALUE"}),
        "COMPATIBLE_WITH_RISK",
    ),
)

# Detector gaps that hold on every header backend (and DWARF where noted).
_DETECTOR_GAPS: tuple[Known, ...] = (
    Known(
        "cpu_dispatch_isa_dropped",
        _ELF,
        "wrong-verdict",
        _ev("headers", "dwarf"),
        "the dropped tier's k*_avx2 exports are rolled up into a RISK finding "
        "although the header declares them and a pinned consumer fails to load",
        GAP_CPU_DISPATCH,
        issues=frozenset({"missing:SONAME_BUMP_RECOMMENDED", "weaker-than:BREAKING"}),
        verdicts=frozenset({"COMPATIBLE_WITH_RISK"}),
    ),
    *(
        _backend_gap(
            "pimpl_member_renamed_under_inline_accessor",
            _MORE,
            backend,
            "wrong-kind",
            "the holder is recorded as a bare `Desc`; the accessor's scope is lib::Desc",
            GAP_PIMPL_BARE_HOLDER,
            frozenset({"missing:INLINE_BODY_REFERENCES_RENAMED_MEMBER"}),
            "BREAKING",
        )
        for backend in ("castxml", "clang")
    ),
    _backend_gap(
        "unspecified_return_now_named",
        _MORE,
        "castxml",
        "wrong-verdict",
        "castxml spells the closure return `?`, which the check does not read",
        GAP_CLOSURE_RETURN,
        frozenset({"missing:UNSPECIFIED_RETURN_NOW_NAMED", "weaker-than:API_BREAK"}),
        "COMPATIBLE",
    ),
    _backend_gap(
        "unspecified_return_now_named",
        _MORE,
        "clang",
        "false-positive",
        "clang spells the closure return `(lambda:...)`: func_return_changed instead",
        GAP_CLOSURE_RETURN,
        frozenset({"missing:UNSPECIFIED_RETURN_NOW_NAMED", "stronger-than:API_BREAK"}),
        "BREAKING",
    ),
    Known(
        "version_stamp_typedef_bumped",
        _ELF,
        "false-positive",
        lambda c: (
            c.evidence == "dwarf"
            or (c.evidence == "headers" and c.header_backend in ("castxml", "clang"))
        ),
        "the return re-spelled through the renamed typedef reads "
        "func_return_changed (BREAKING) though the call is unchanged",
        GAP_TYPEDEF_RETURN,
        issues=frozenset({"stronger-than:API_BREAK"}),
        verdicts=frozenset({"BREAKING"}),
    ),
)

KNOWN: tuple[Known, ...] = (
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
        "virtual_become_pure",
        _CLASSES,
        "wrong-kind",
        # clang marks the in-class declaration DW_VIRTUALITY_pure_virtual; GCC
        # writes DW_VIRTUALITY_virtual, so only a GCC build misses the kind.
        lambda c: c.evidence == "dwarf" and c.producer.family == "gcc",
        "from a GCC-built DWARF snapshot the now-pure virtual reads func_removed; "
        "neither func_virtual_became_pure nor type_became_abstract",
        GAP_DWARF_PURE,
        issues=frozenset({"none-of:FUNC_VIRTUAL_BECAME_PURE|TYPE_BECAME_ABSTRACT"}),
        verdicts=frozenset({"BREAKING"}),
    ),
    Known(
        "type_lost_abstract",
        frozenset({"cxx_more"}),
        "false-positive",
        # The reverse of virtual_become_pure: GCC's DWARF marks Shape::area
        # DW_VIRTUALITY_virtual while it is still pure, so the old snapshot
        # has no exported area and defining it reads as a new virtual; clang
        # marks it pure and reads FUNC_ADDED (COMPATIBLE). Consumers still run.
        lambda c: c.evidence == "dwarf" and c.producer.family == "gcc",
        "from a GCC-built DWARF snapshot defining a pure virtual reads "
        "virtual_method_added (BREAKING); the vtable keeps its slot count",
        GAP_DWARF_PURE,
        issues=frozenset({"stronger-than:COMPATIBLE_WITH_RISK"}),
        verdicts=frozenset({"BREAKING"}),
    ),
    Known(
        "rebuild_optimized",
        frozenset({"cxx_stdlib"}),
        "false-positive",
        # ELF: the weak std:: copies and covered imports are demoted (ADR-089
        # A7, #1960); with DWARF every unit's DW_AT_producer shows
        # -fstack-protector-strong still on, so the lost canary is demoted too.
        # Binary only, nothing records the flag. clang on ELF reads COMPATIBLE.
        # Under the pre-C++11 string ABI (-D_GLIBCXX_USE_CXX11_ABI=0) the -O0
        # build has no protected frame either, so no canary is lost and the
        # rebuild reads COMPATIBLE: correct, not part of this gap.
        lambda c: (
            c.objfmt == "elf"
            and c.producer.family == "gcc"
            and c.evidence == "binary"
            and c.flags.cxx11_abi != 0
        ),
        "-O2 leaves no frame that needs a stack canary; without debug info no unit "
        "records -fstack-protector, so stack_canary_removed keeps its RISK (marked as "
        "weaker evidence); every consumer still runs",
        GAP_O2_STD,
        issues=frozenset({"stronger-than:COMPATIBLE"}),
        verdicts=frozenset({"COMPATIBLE_WITH_RISK"}),
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
        lambda c: c.objfmt == "pe" and uses_libstdcxx(c.producer),
        "MinGW: -O2 inlines the std::string instantiations the -O0 build exported; a PE "
        "export carries no WEAK binding, so their loss reads func_removed / "
        "func_visibility_changed (BREAKING) while every consumer still runs",
        GAP_O2_STD,
        issues=frozenset({"stronger-than:COMPATIBLE"}),
        verdicts=frozenset({"BREAKING", "COMPATIBLE_WITH_RISK"}),
    ),
    Known(
        "var_alignment_raised",
        frozenset({"c_elf"}),
        "wrong-verdict",
        # castxml 0.7 writes no align on a <Variable>: both sides record int's
        # natural alignment as a lower bound, and the raised specifier leaves
        # the address bounds overlapping. DWARF and the clang/gcc backends read it.
        _hb("castxml"),
        "castxml records no alignment specifier on a variable, so a raised "
        "aligned(16) -> aligned(64) reads NO_CHANGE at castxml headers depth",
        GAP_CASTXML_VAR_ALIGN,
        issues=frozenset(
            {
                "missing:SONAME_BUMP_RECOMMENDED",
                "missing:VAR_ALIGNMENT_CHANGED",
                "weaker-than:BREAKING",
            }
        ),
        verdicts=frozenset({"NO_CHANGE"}),
    ),
    *_BACKEND_GAPS,
    *_DETECTOR_GAPS,
)


def known_mismatch(op: str, seed: str, config: Config) -> Known | None:
    for k in KNOWN:
        if k.op == op and seed in k.seeds and k.when(config):
            return k
    return None
