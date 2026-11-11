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
)


def known_mismatch(op: str, seed: str, config: Config) -> Known | None:
    for k in KNOWN:
        if k.op == op and seed in k.seeds and k.when(config):
            return k
    return None
