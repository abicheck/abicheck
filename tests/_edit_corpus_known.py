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
GAP_O2_STD = (
    "An `-O2` rebuild of a C++ library that uses `std::string` reads as a risk on gcc, "
    "or a break on MinGW"
)


GAP_CLANG_ALIGN = "The clang header backend records no record alignment change"
GAP_CLANG_TYPEDEF = "The clang header backend reads a removed C typedef as BREAKING"
GAP_STATIC_ACCESS = (
    "The clang and native gcc header backends record no access for a static data member"
)
GAP_GCC_DEPRECATED = "The native gcc header backend records no deprecation attributes"
GAP_GCC_C_SHAPES = (
    "The native gcc header backend records no flexible-array, `_Atomic`, opaque-record "
    "or const-object facts"
)
GAP_GCC_FRIEND = "The native gcc header backend records no hidden friends"
GAP_GCC_RESTRICT = "The native gcc header backend records no `restrict` on a parameter"
GAP_GCC_CONCEPT = (
    "The native gcc header backend cannot tell a tightened concept constraint"
)
GAP_GCC_VTABLE = "The native gcc header backend records no vptr or pure-virtual facts"


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


_ELF = frozenset({"c_elf"})
_MORE = frozenset({"cxx_more"})
_LAYOUT_SEEDS = frozenset({"c_core", "cxx_classes"})
_ALIGN = frozenset({"none-of:STRUCT_ALIGNMENT_CHANGED|TYPE_ALIGNMENT_CHANGED"})

# Header-backend parity: castxml reads these facts, the named backend does
# not. Each is being fixed in the backend itself; the entry forces its own
# removal (a fixed cell no longer reproduces) when the fix lands.
_BACKEND_GAPS: tuple[Known, ...] = (
    *(
        _backend_gap(
            op,
            _LAYOUT_SEEDS,
            "clang",
            "wrong-kind",
            "the clang header backend reports the size and offsets but not the "
            "record's alignment change",
            GAP_CLANG_ALIGN,
            _ALIGN,
            "BREAKING",
            # Under -fpack-struct the operator owes no alignment change at all.
            also=lambda c: c.flags.pack is None,
        )
        for op in ("struct_field_widen", "rebuild_pack_struct")
    ),
    _backend_gap(
        "typedef_removed",
        _ELF,
        "clang",
        "false-positive",
        "removing an unused public typedef reads BREAKING under the clang header "
        "backend; castxml reads it as a source break only",
        GAP_CLANG_TYPEDEF,
        frozenset({"stronger-than:API_BREAK"}),
        "BREAKING",
    ),
    *(
        _backend_gap(
            op,
            _MORE,
            backend,
            "wrong-verdict",
            f"the {backend} header backend records no access on a static data "
            "member, so its narrowing or widening reads NO_CHANGE",
            GAP_STATIC_ACCESS,
            frozenset({f"missing:{kind}", f"weaker-than:{floor}"}),
            "NO_CHANGE",
        )
        for backend in ("clang", "gcc")
        for op, kind, floor in (
            ("static_member_access_narrowed", "VAR_ACCESS_CHANGED", "API_BREAK"),
            ("static_member_access_widened", "VAR_ACCESS_WIDENED", "COMPATIBLE"),
        )
    ),
    _backend_gap(
        "api_deprecated",
        _ELF,
        "gcc",
        "wrong-kind",
        "the native gcc header backend projects no [[deprecated]] attribute",
        GAP_GCC_DEPRECATED,
        frozenset(
            {
                "missing:ENUM_DEPRECATED_ADDED",
                "missing:FIELD_DEPRECATED_ADDED",
                "missing:TYPE_DEPRECATED_ADDED",
            }
        ),
        "COMPATIBLE",
    ),
    _backend_gap(
        "api_undeprecated",
        _ELF,
        "gcc",
        "wrong-kind",
        "the native gcc header backend projects no [[deprecated]] attribute",
        GAP_GCC_DEPRECATED,
        frozenset(
            {"missing:ENUM_DEPRECATED_REMOVED", "missing:TYPE_DEPRECATED_REMOVED"}
        ),
        "COMPATIBLE",
    ),
    _backend_gap(
        "field_deprecated_removed",
        _MORE,
        "gcc",
        "wrong-verdict",
        "the native gcc header backend projects no [[deprecated]] attribute",
        GAP_GCC_DEPRECATED,
        frozenset({"missing:FIELD_DEPRECATED_REMOVED", "weaker-than:COMPATIBLE"}),
        "NO_CHANGE",
    ),
    *(
        _backend_gap(
            op,
            _ELF,
            "gcc",
            cls,
            f"the native gcc header backend records no fact {kind} is read from",
            GAP_GCC_C_SHAPES,
            frozenset({f"missing:{kind}"}),
            verdict,
        )
        for op, kind, cls, verdict in (
            (
                "flexible_array_member",
                "FLEXIBLE_ARRAY_MEMBER_CHANGED",
                "wrong-kind",
                "BREAKING",
            ),
            (
                "const_string_object_grew",
                "SYMBOL_SIZE_CHANGED_CONST_OBJECT",
                "wrong-kind",
                "BREAKING",
            ),
            (
                "field_became_atomic",
                "ATOMIC_QUALIFIER_CHANGED",
                "wrong-kind",
                "COMPATIBLE_WITH_RISK",
            ),
            ("record_became_opaque", "TYPE_BECAME_OPAQUE", "wrong-kind", "BREAKING"),
        )
    ),
    *(
        _backend_gap(
            op,
            _MORE,
            "gcc",
            cls,
            "the native gcc header backend records no hidden (ADL-only) friend",
            GAP_GCC_FRIEND,
            frozenset({f"missing:{kind}", f"weaker-than:{floor}"}),
            "NO_CHANGE",
        )
        for op, kind, floor, cls in (
            (
                "hidden_friend_added",
                "HIDDEN_FRIEND_ADDED",
                "COMPATIBLE",
                "wrong-verdict",
            ),
            # Consumers calling e1 == e2 stop compiling: the oracle sees it.
            (
                "hidden_friend_removed",
                "HIDDEN_FRIEND_REMOVED",
                "API_BREAK",
                "false-clean",
            ),
        )
    ),
    _backend_gap(
        "param_restrict_removed",
        _MORE,
        "gcc",
        "wrong-verdict",
        "the native gcc header backend records no restrict qualifier on a parameter",
        GAP_GCC_RESTRICT,
        frozenset({"missing:PARAM_RESTRICT_CHANGED", "weaker-than:COMPATIBLE"}),
        "NO_CHANGE",
    ),
    _backend_gap(
        "concept_constraint_added",
        frozenset({"cxx_classes"}),
        "gcc",
        "false-clean",
        "the native gcc header backend reads a newly constrained template as "
        "concept_constraint_changed (a risk), not concept_tightened: callers "
        "with a now-excluded argument stop compiling",
        GAP_GCC_CONCEPT,
        frozenset({"weaker-than:API_BREAK"}),
        "COMPATIBLE_WITH_RISK",
    ),
    _backend_gap(
        "vptr_introduced",
        _MORE,
        "gcc",
        "wrong-kind",
        "the native gcc header backend records no vptr, so the first virtual reads "
        "as a layout change without vptr_introduced",
        GAP_GCC_VTABLE,
        frozenset({"missing:VPTR_INTRODUCED"}),
        "BREAKING",
    ),
    _backend_gap(
        "type_lost_abstract",
        _MORE,
        "gcc",
        "false-positive",
        "the native gcc header backend records no pure virtual, so defining one "
        "reads as a new virtual method (BREAKING)",
        GAP_GCC_VTABLE,
        frozenset({"stronger-than:COMPATIBLE_WITH_RISK"}),
        "BREAKING",
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
        lambda c: (
            c.objfmt == "elf" and c.producer.family == "gcc" and c.evidence == "binary"
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
    *_BACKEND_GAPS,
)


def known_mismatch(op: str, seed: str, config: Config) -> Known | None:
    for k in KNOWN:
        if k.op == op and seed in k.seeds and k.when(config):
            return k
    return None
