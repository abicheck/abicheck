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
from abiforge.run.toolchains import uses_libstdcxx

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
GAP_O2_STD = (
    "An `-O2` rebuild of a C++ library that uses `std::string` reads as a risk on gcc, "
    "or a break on MinGW"
)
GAP_HEADER_DROPPED_EXPORT = "A function that leaves the public header but stays exported reads as a binary break"
GAP_NEW_LIB_TAGS = "A version requirement on a newly needed library reads as compatible, whatever its version"
GAP_HEADER_ONLY_POINTER = (
    "A pointer-level change of a never-exported header function reads as a binary break"
)
GAP_CASTXML_ALIGN = (
    "castxml headers read a variable's alignment specifier only as a lower bound"
)
GAP_BASE_UNVERSIONED = (
    "An export GNU ld binds to the base version is never reported as unversioned"
)
GAP_VECTOR_ABI = (
    "A `-mveclibabi` switch reads as a binary break when no call is vectorized"
)
GAP_CLANG_INLINE_STATIC = "An inline function's static local, exported by clang, reads as an undeclared export"
GAP_UNIQUE_O2_DWARF = "A GNU-unique static whose inline owner was inlined away is not compared at DWARF depth"
GAP_DUAL_ABI_COW_CHURN = "A C++20 dual-ABI flip is not recognized when the old-ABI string's members are exported"
GAP_GCC_HEADER_ONLY = (
    "The native gcc header backend records no function the library never emits"
)
GAP_DEFINE_PROFILE = (
    "A macro that changes the build's ABI makes the two header dumps incomparable"
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
        "exported_but_undeclared",
        _ELF,
        "false-positive",
        _headers_vis_default,
        "wget leaves the header but stays exported; built consumers still run, "
        "yet the contract removal reads func_removed (BREAKING)",
        GAP_HEADER_DROPPED_EXPORT,
        issues=frozenset({"stronger-than:API_BREAK"}),
        verdicts=_BREAKING,
        also_cls=frozenset({"wrong-verdict"}),
    ),
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
        "header_only_function_params_changed",
        _ALT,
        "false-clean",
        _hb("gcc"),
        "the native gcc backend records no hscale (static inline, never "
        "emitted), so the signature change reads as nothing",
        GAP_GCC_HEADER_ONLY,
        issues=frozenset(
            {"missing:HEADER_ONLY_FUNCTION_PARAMS_CHANGED", "weaker-than:API_BREAK"}
        ),
        verdicts=frozenset({"COMPATIBLE"}),
        # A gcc consumer only warns on the int -> int * conversion: no
        # source break, so the same finding reads wrong-verdict.
        also_cls=frozenset({"wrong-verdict"}),
    ),
    Known(
        "header_only_function_params_changed",
        _ALT,
        "false-positive",
        lambda c: c.evidence == "headers" and c.header_backend != "gcc",
        "hscale is static inline and never exported, yet its int -> int * "
        "parameter reads param_pointer_level_changed (BREAKING)",
        GAP_HEADER_ONLY_POINTER,
        issues=frozenset({"stronger-than:API_BREAK"}),
        verdicts=_BREAKING,
        also_cls=frozenset({"wrong-verdict"}),
    ),
    Known(
        "object_alignment_reduced",
        _ELF,
        "wrong-verdict",
        _hb("castxml"),
        "castxml cannot read g_al's old aligned(16), so the address-derived "
        "reduction is demoted to COMPATIBLE",
        GAP_CASTXML_ALIGN,
        issues=frozenset({"weaker-than:COMPATIBLE_WITH_RISK"}),
        verdicts=frozenset({"COMPATIBLE"}),
    ),
    Known(
        "unversioned_export_added",
        _ELF,
        "wrong-kind",
        _always,
        "GNU ld binds the unmatched edit_unv to the base version (index 1), "
        "which unversioned_exported_symbol deliberately never flags",
        GAP_BASE_UNVERSIONED,
        issues=frozenset({"missing:UNVERSIONED_EXPORTED_SYMBOL"}),
        verdicts=frozenset({"COMPATIBLE"}),
    ),
    Known(
        "vector_math_abi_changed",
        _ELF,
        "false-positive",
        lambda c: c.producer.family == "gcc" and c.evidence in ("dwarf", "headers"),
        "the seed makes no math call, so -mveclibabi=svml changes no code; "
        "vector_abi_changed keeps its BREAKING default",
        GAP_VECTOR_ABI,
        issues=frozenset({"stronger-than:COMPATIBLE_WITH_RISK"}),
        verdicts=_BREAKING,
    ),
    Known(
        "std_string_abi_flipped",
        _XA,
        "wrong-kind",
        lambda c: c.std.year >= 2020,
        "from C++20 the new-ABI std::string is constexpr and exports nothing, "
        "while the old COW string exports ~37 weak members (_ZNSs...): they "
        "dilute the __cxx11 marker share below the flip threshold",
        GAP_DUAL_ABI_COW_CHURN,
        issues=frozenset({"missing:GLIBCXX_DUAL_ABI_FLIP_DETECTED"}),
        verdicts=_BREAKING,
    ),
    Known(
        "header_inline_removed",
        _XA,
        "false-clean",
        _hb("gcc"),
        "the native gcc backend records no demo::clampi (header-only, never "
        "emitted), so its removal reads NO_CHANGE",
        GAP_GCC_HEADER_ONLY,
        issues=frozenset({"missing:INLINE_FUNCTION_REMOVED", "weaker-than:API_BREAK"}),
        verdicts=frozenset({"NO_CHANGE"}),
    ),
    Known(
        "time_bits_64",
        _ALT,
        "refused",
        _ev("headers"),
        "-D_TIME_BITS=64 reaches the new side's header dump only, so the two "
        "dumps' parse profiles differ and the compare refuses",
        GAP_DEFINE_PROFILE,
        issues=frozenset({"refused:ProfileMismatchError"}),
        verdicts=frozenset({"REFUSED"}),
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
        # From C++20 the old-ABI -O0 build guards a frame too (the nightly
        # drew c++20 + cxx11abi0 once the cxx_alt seed joined the array).
        lambda c: (
            c.objfmt == "elf"
            and c.producer.family == "gcc"
            and c.evidence == "binary"
            and (c.flags.cxx11_abi != 0 or c.std.year >= 2020)
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
    *_PLATFORM,
)


def known_mismatch(op: str, seed: str, config: Config) -> Known | None:
    for k in KNOWN:
        if k.op == op and seed in k.seeds and k.when(config):
            return k
    return None
