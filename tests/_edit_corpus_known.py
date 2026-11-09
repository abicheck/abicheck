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

GAP_NOEXCEPT = "castxml output carries no `noexcept` exception specification"
GAP_TEMPLATE_DEFAULT = (
    "A header-only default template-argument change reads `NO_CHANGE` at L2 on castxml"
)
GAP_CONCEPT = (
    "A new `requires`-clause on a header-only template reads `NO_CHANGE` under castxml"
)
GAP_DWARF_PURE = "A GCC-built DWARF snapshot cannot tell that a virtual became pure"
GAP_CXX11_ABI = "A `_GLIBCXX_USE_CXX11_ABI` rebuild is refused as a profile mismatch, not reported as a break"
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
        "method_noexcept_add",
        _CLASSES,
        "false-clean",
        _ev("headers"),
        "castxml writes no exception specification: NO_CHANGE while an overrider without "
        "noexcept no longer compiles",
        GAP_NOEXCEPT,
        issues=frozenset({"missing:FUNC_NOEXCEPT_ADDED", "weaker-than:API_BREAK"}),
        verdicts=frozenset({"NO_CHANGE"}),
    ),
    Known(
        "template_default_arg_change",
        _CLASSES,
        "wrong-verdict",
        _ev("headers"),
        "castxml reads a header-only template default change as NO_CHANGE",
        GAP_TEMPLATE_DEFAULT,
        issues=frozenset(
            {
                "none-of:DEFAULT_TEMPLATE_ARG_CHANGED|TEMPLATE_PARAM_DEFAULT_CHANGED",
                "weaker-than:API_BREAK",
            }
        ),
        verdicts=frozenset({"NO_CHANGE"}),
    ),
    Known(
        "concept_constraint_added",
        _CLASSES,
        "false-clean",
        _ev("headers"),
        "a requires-clause added to a header-only function template reads NO_CHANGE while a "
        "consumer call stops compiling",
        GAP_CONCEPT,
        issues=frozenset(
            {
                "none-of:CONCEPT_CONSTRAINT_CHANGED|CONCEPT_TIGHTENED",
                "weaker-than:API_BREAK",
            }
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
        "rebuild_cxx11_abi_off",
        frozenset({"cxx_stdlib"}),
        "refused",
        _ev("headers"),
        "the new side's -D_GLIBCXX_USE_CXX11_ABI=0 makes compare raise ProfileMismatchError "
        "(ADR-050's documented refusal; the reason names the macro); consumers fail with "
        "undefined std::__cxx11 symbols. Open: whether a runtime-ABI macro should gate instead",
        GAP_CXX11_ABI,
        issues=frozenset({"refused:ProfileMismatchError"}),
        verdicts=frozenset({"REFUSED"}),
    ),
    Known(
        "rebuild_optimized",
        frozenset({"cxx_stdlib"}),
        "false-positive",
        # ELF: the weak std:: copies and covered imports are demoted (ADR-089
        # A7, #1960); gcc still drops __stack_chk_fail, which no binary fact
        # shows was not a hardening drop. clang on ELF reads COMPATIBLE.
        lambda c: c.objfmt == "elf" and c.producer.family == "gcc",
        "-O2 leaves no frame that needs a stack canary, so stack_canary_removed (RISK) is "
        "reported though -fstack-protector is still on; every consumer still runs",
        GAP_O2_STD,
        issues=frozenset({"stronger-than:COMPATIBLE"}),
        verdicts=frozenset({"COMPATIBLE_WITH_RISK"}),
    ),
    Known(
        "rebuild_optimized",
        frozenset({"c_core"}),
        "false-positive",
        # i386 -O0 guards fill_rec's frame and calls __stack_chk_fail; -O2 needs no
        # canary, and moves g_counter to a less-aligned address in a 4-aligned .bss.
        lambda c: (
            c.objfmt == "elf" and c.producer.family == "gcc" and c.flags.bits == 32
        ),
        "-O2 leaves no frame that needs a stack canary (stack_canary_removed, though "
        "-fstack-protector-strong is still on) and places g_counter at 0x4008 instead of "
        "0x4010 (exported_object_alignment_reduced 16 -> 8 from the address alone, binary "
        "and DWARF); both RISK, while every consumer still runs",
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
