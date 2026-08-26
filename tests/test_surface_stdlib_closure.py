"""Unit tests for the stdlib-internal-closure surface demotion (ADR-024).

A closure's Itanium mangling embeds per-translation-unit, compiler-ordering
facts (see `change_registry`'s own `unnamed_type_in_public_abi` entry), so a
stdlib/runtime symbol instantiated over a caller-supplied lambda can never be
part of any external consumer's ABI contract -- no consumer could have
written source code naming that exact template argument themselves.
`classify_change_surface` (`abicheck/surface.py`) demotes such a finding via
`demangle.is_stdlib_internal_closure_instantiation`, even when neither
side's header surface resolves at all (the ELF-only-mode case), which is
otherwise the one case every other rule conservatively keeps.

Kept in its own file (not `tests/test_surface.py`) because that file is an
ADR-061 no-growth-baselined legacy module already at its line-budget cap.
"""

from __future__ import annotations

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.model import AbiSnapshot, Function, Param, ScopeOrigin, Visibility
from abicheck.surface import (
    REASON_STDLIB_INTERNAL_CLOSURE,
    PublicSurface,
    classify_change_surface,
    compute_public_surface,
)

# A real mangled symbol produced by `g++ -std=c++17` for a `std::call_once`
# guard closing over a caller-supplied lambda declared inside
# `dnnl::impl::detail::Widget::run()` -- confirmed to demangle (via a real
# c++filt) to:
#   std::once_flag::_Prepare_execution::_Prepare_execution<
#       std::call_once<dnnl::impl::detail::Widget::run()::{lambda()#1}>(
#           std::once_flag&, dnnl::impl::detail::Widget::run()::{lambda()#1}&&
#       )::{lambda()#1}
#   >(dnnl::impl::detail::Widget::run()::{lambda()#1}&)
# Not a hand-mocked shortcut: this is the actual mangling GCC emits for this
# construct, not a guessed string standing in for it (AGENTS.md's
# real-dependency-testing convention).
_STDLIB_CLOSURE_SYMBOL = (
    "_ZNSt9once_flag18_Prepare_executionC1IZSt9call_onceIZN4dnnl4impl6detail"
    "6Widget3runEvEUlvE_JEEvRS_OT_DpOT0_EUlvE_EERS9_"
)


def _fn(name, ret="void", vis=Visibility.PUBLIC, mangled=None):
    return Function(
        name=name,
        mangled=mangled if mangled is not None else f"_Z{len(name)}{name}",
        return_type=ret,
        params=[Param(name="a0", type="int")],
        visibility=vis,
        origin=ScopeOrigin.UNKNOWN,
    )


def _unresolvable():
    return PublicSurface()  # resolvable defaults to False


class TestStdlibInternalClosureDemotion:
    def test_demoted_even_when_unresolvable(self):
        c = Change(
            kind=ChangeKind.FUNC_REMOVED_ELF_ONLY,
            symbol=_STDLIB_CLOSURE_SYMBOL,
            description="",
        )
        u = _unresolvable()
        assert classify_change_surface(c, u, u) == (
            False,
            REASON_STDLIB_INTERNAL_CLOSURE,
        )

    def test_demoted_even_when_resolvable(self):
        # Resolvability-independent: fires ahead of the ordinary
        # resolvable/type-reachability checks too, not only as a fallback
        # for the unresolvable case.
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        s = compute_public_surface(snap)
        c = Change(
            kind=ChangeKind.FUNC_PARAMS_CHANGED,
            symbol=_STDLIB_CLOSURE_SYMBOL,
            description="",
        )
        assert classify_change_surface(c, s, s) == (
            False,
            REASON_STDLIB_INTERNAL_CLOSURE,
        )

    def test_negative_control_stdlib_symbol_without_closure_is_not_demoted(self):
        # A real, non-lambda stdlib mangled name (std::vector<int>::size()
        # const) must NOT be demoted by this rule -- only a closure
        # instantiation is unconditionally unnameable by a consumer.
        u = _unresolvable()
        c = Change(
            kind=ChangeKind.FUNC_REMOVED_ELF_ONLY,
            symbol="_ZNKSt6vectorIiSaIiEE4sizeEv",
            description="",
        )
        assert classify_change_surface(c, u, u) == (True, None)

    def test_negative_control_non_stdlib_closure_is_not_demoted(self):
        # A library's own (non-stdlib-rooted) closure-parameterized symbol
        # is deliberately out of scope for this narrow rule (see
        # `is_stdlib_internal_closure_instantiation`'s own docstring):
        # dnnl::impl::detail::Widget::run()::{lambda()#1}::operator()()
        # const -- confirmed via a real demangler.
        own_closure_symbol = "_ZZN4dnnl4impl6detail6Widget3runEvENKUlvE_clEv"
        u = _unresolvable()
        c = Change(
            kind=ChangeKind.FUNC_REMOVED_ELF_ONLY,
            symbol=own_closure_symbol,
            description="",
        )
        assert classify_change_surface(c, u, u) == (True, None)

    def test_negative_control_no_symbol_is_not_demoted(self):
        u = _unresolvable()
        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="", description="")
        assert classify_change_surface(c, u, u) == (True, None)

    def test_negative_control_unmangled_symbol_is_not_demoted(self):
        # Not a valid Itanium mangled name at all -- demangle() returns
        # None, and the helper must fail closed (never guess) rather than
        # crash.
        u = _unresolvable()
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="NotMangled", description=""
        )
        assert classify_change_surface(c, u, u) == (True, None)
