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
GAP_UNIQUE_O2_DWARF = "A GNU-unique static whose inline owner was inlined away is not compared at DWARF depth"
GAP_FLAG_INVISIBLE_LAYOUT = (
    "A config-level layout flag a public header cannot carry: stripped {B,H} reads the "
    "layout without it (reduced-assurance signal pending the WS-2 ADR, plan §11.2)"
)
GAP_CASTXML_GCC_BARRIER = "castxml lays out a gcc `-fpack-struct=1` record with a zero-width bit-field by clang's rule"


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
)


def _gcc_packed_headers(c: Config) -> bool:
    """gcc build, -fpack-struct=1, header dump with the flag (DWARF in the cell)."""
    return (
        c.producer.family == "gcc"
        and c.flags.pack == 1
        and c.evidence == "headers"
        and not c.stripped
        and c.header_backend == "castxml"
    )


def _castxml_gcc_barrier(op: str, cls: str, issues: set[str]) -> Known:
    return Known(
        op,
        frozenset({"c_layout"}),
        cls,
        _gcc_packed_headers,
        "castxml computes the record with clang's rule (a :0 barrier rounds to "
        "the 32-bit unit) and that layout replaces the gcc binary's DWARF, "
        "where gcc -fpack-struct=1 rounds the barrier to the next byte: the "
        "verdict is right, the reported offsets and sizes are not",
        GAP_CASTXML_GCC_BARRIER,
        issues=frozenset(issues),
        verdicts=frozenset({"BREAKING"}),
    )


KNOWN: tuple[Known, ...] = (
    _castxml_gcc_barrier(
        "bitfield_width_16_to_17_behind_barrier",
        "wrong-kind",
        {
            "exact-missing:TYPE_FIELD_OFFSET_CHANGED Bits_bw16_before::b 16bit->24bit",
            "exact-missing:TYPE_FIELD_OFFSET_CHANGED Bits_bw16_before::c 32bit->40bit",
            "exact-missing:TYPE_FIELD_OFFSET_CHANGED Bits_bw16_before::tail 40bit->48bit",
            "exact-missing:TYPE_SIZE_CHANGED Bits_bw16_before 48bit->56bit",
            "missing:TYPE_FIELD_OFFSET_CHANGED",
            "missing:TYPE_SIZE_CHANGED",
        },
    ),
    _castxml_gcc_barrier(
        "bitfield_barrier_inserted_before_7",
        "wrong-detail",
        {
            "exact-missing:TYPE_FIELD_OFFSET_CHANGED Bits_bins7_before::b 7bit->8bit",
            "exact-missing:TYPE_FIELD_OFFSET_CHANGED Bits_bins7_before::c 23bit->24bit",
            "exact-unexpected:TYPE_FIELD_OFFSET_CHANGED Bits_bins7_before::b 7bit->32bit",
            "exact-unexpected:TYPE_FIELD_OFFSET_CHANGED Bits_bins7_before::c 23bit->48bit",
            "exact-unexpected:TYPE_FIELD_OFFSET_CHANGED Bits_bins7_before::tail 32bit->56bit",
            "exact-unexpected:TYPE_SIZE_CHANGED Bits_bins7_before 40bit->64bit",
        },
    ),
    _castxml_gcc_barrier(
        "bitfield_barrier_inserted_before_17",
        "wrong-detail",
        {
            "exact-missing:TYPE_FIELD_OFFSET_CHANGED Bits_bins17_before::b 17bit->24bit",
            "exact-missing:TYPE_FIELD_OFFSET_CHANGED Bits_bins17_before::c 33bit->40bit",
            "exact-missing:TYPE_FIELD_OFFSET_CHANGED Bits_bins17_before::tail 40bit->48bit",
            "exact-missing:TYPE_SIZE_CHANGED Bits_bins17_before 48bit->56bit",
            "exact-unexpected:TYPE_FIELD_OFFSET_CHANGED Bits_bins17_before::b 17bit->32bit",
            "exact-unexpected:TYPE_FIELD_OFFSET_CHANGED Bits_bins17_before::c 33bit->48bit",
            "exact-unexpected:TYPE_FIELD_OFFSET_CHANGED Bits_bins17_before::tail 40bit->56bit",
            "exact-unexpected:TYPE_SIZE_CHANGED Bits_bins17_before 48bit->64bit",
        },
    ),
    _castxml_gcc_barrier(
        "bitfield_barrier_inserted_after_17",
        "wrong-detail",
        {
            "exact-missing:TYPE_FIELD_OFFSET_CHANGED Bits_bins17_after::c 33bit->40bit",
            "exact-missing:TYPE_FIELD_OFFSET_CHANGED Bits_bins17_after::tail 40bit->48bit",
            "exact-missing:TYPE_SIZE_CHANGED Bits_bins17_after 48bit->56bit",
            "exact-unexpected:TYPE_FIELD_OFFSET_CHANGED Bits_bins17_after::c 33bit->64bit",
            "exact-unexpected:TYPE_FIELD_OFFSET_CHANGED Bits_bins17_after::tail 40bit->72bit",
            "exact-unexpected:TYPE_SIZE_CHANGED Bits_bins17_after 48bit->80bit",
        },
    ),
    _castxml_gcc_barrier(
        "bitfield_barrier_removed_after_17",
        "wrong-detail",
        {
            "exact-missing:TYPE_FIELD_OFFSET_CHANGED Bits_brem17_after::c 40bit->33bit",
            "exact-missing:TYPE_FIELD_OFFSET_CHANGED Bits_brem17_after::tail 48bit->40bit",
            "exact-missing:TYPE_SIZE_CHANGED Bits_brem17_after 56bit->48bit",
            "exact-unexpected:TYPE_FIELD_OFFSET_CHANGED Bits_brem17_after::c 64bit->33bit",
            "exact-unexpected:TYPE_FIELD_OFFSET_CHANGED Bits_brem17_after::tail 72bit->40bit",
            "exact-unexpected:TYPE_SIZE_CHANGED Bits_brem17_after 80bit->48bit",
        },
    ),
    _castxml_gcc_barrier(
        "bitfield_barrier_removed_before_7",
        "wrong-detail",
        {
            "exact-missing:TYPE_FIELD_OFFSET_CHANGED Bits_brem7_before::b 8bit->7bit",
            "exact-missing:TYPE_FIELD_OFFSET_CHANGED Bits_brem7_before::c 24bit->23bit",
            "exact-unexpected:TYPE_FIELD_OFFSET_CHANGED Bits_brem7_before::b 32bit->7bit",
            "exact-unexpected:TYPE_FIELD_OFFSET_CHANGED Bits_brem7_before::c 48bit->23bit",
            "exact-unexpected:TYPE_FIELD_OFFSET_CHANGED Bits_brem7_before::tail 56bit->32bit",
            "exact-unexpected:TYPE_SIZE_CHANGED Bits_brem7_before 64bit->40bit",
        },
    ),
    _castxml_gcc_barrier(
        "bitfield_barrier_removed_after_6",
        "wrong-detail",
        {
            "exact-missing:TYPE_FIELD_OFFSET_CHANGED Bits_brem6_after::c 24bit->22bit",
            "exact-unexpected:TYPE_FIELD_OFFSET_CHANGED Bits_brem6_after::c 32bit->22bit",
            "exact-unexpected:TYPE_FIELD_OFFSET_CHANGED Bits_brem6_after::tail 40bit->32bit",
            "exact-unexpected:TYPE_SIZE_CHANGED Bits_brem6_after 48bit->40bit",
        },
    ),
    _castxml_gcc_barrier(
        "bitfield_barrier_removed_before_1",
        "wrong-detail",
        {
            "exact-missing:TYPE_FIELD_OFFSET_CHANGED Bits_brem1_before::b 8bit->1bit",
            "exact-missing:TYPE_FIELD_OFFSET_CHANGED Bits_brem1_before::c 24bit->17bit",
            "exact-missing:TYPE_FIELD_OFFSET_CHANGED Bits_brem1_before::tail 32bit->24bit",
            "exact-missing:TYPE_SIZE_CHANGED Bits_brem1_before 40bit->32bit",
            "exact-unexpected:TYPE_FIELD_OFFSET_CHANGED Bits_brem1_before::b 32bit->1bit",
            "exact-unexpected:TYPE_FIELD_OFFSET_CHANGED Bits_brem1_before::c 48bit->17bit",
            "exact-unexpected:TYPE_FIELD_OFFSET_CHANGED Bits_brem1_before::tail 56bit->24bit",
            "exact-unexpected:TYPE_SIZE_CHANGED Bits_brem1_before 64bit->32bit",
        },
    ),
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
        issues=frozenset(
            {"none-of:TYPE_FIELD_OFFSET_CHANGED", "none-of:TYPE_SIZE_CHANGED"}
        ),
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
        issues=frozenset(
            {"none-of:TYPE_FIELD_OFFSET_CHANGED", "none-of:TYPE_SIZE_CHANGED"}
        ),
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
            {
                "missing:SONAME_BUMP_RECOMMENDED",
                "none-of:TYPE_SIZE_CHANGED",
                "weaker-than:BREAKING",
            }
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
