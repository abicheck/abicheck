# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for :mod:`abicheck.model.mangled_name` (ADR-061 D1, split out of
``diff_cxx_rules.py`` so ``extract``'s ``dumper_clang_expr.py``/
``dumper_hybrid.py`` can use ``itanium_scope_components`` without a
forbidden ``extract -> compare`` edge; ``msvc_scope_components`` joined it
here later, closing Phase 2's "fourth, pre-existing tension" so
``buildsource/ctor_export_match.py``/``virtual_dispatch_graph.py`` can use
either decoder the same way).

Full behavioral coverage of the Itanium/MSVC parsing chains already exists
across many call sites (``test_dumper_hybrid.py``, ``test_dumper_clang.py``,
``test_type_reachability_mangling.py``, ``test_kde_compat_detectors.py``,
...) and is not duplicated here -- this file only pins the split itself: the
new canonical import path resolves and behaves, and ``diff_cxx_rules.py``'s
back-compat re-export is the identical function object, not a copy that
could drift.
"""

from __future__ import annotations

from abicheck import diff_cxx_rules
from abicheck.model import mangled_name


def test_itanium_scope_components_basic() -> None:
    assert mangled_name.itanium_scope_components("_ZN1C3barEv") == ["C", "bar"]
    assert mangled_name.itanium_scope_components("_Z4drawi") == ["draw"]
    assert mangled_name.itanium_scope_components("not a mangled name") is None


def test_msvc_scope_components_basic() -> None:
    assert mangled_name.msvc_scope_components("?run@Foo@@QEAAXXZ") == ["Foo", "run"]
    assert mangled_name.msvc_scope_components("?instantiate@@YAXXZ") == ["instantiate"]
    assert mangled_name.msvc_scope_components("not a mangled name") is None


def test_itanium_special_name_owner_scope_components() -> None:
    # Codex review, fresh evidence: surface.py's vtable/RTTI/VTT owner-scope
    # resolution must not depend on an external demangler -- this is the
    # in-process structural parser it uses instead.
    fn = mangled_name.itanium_special_name_owner_scope_components
    assert fn("_ZTV13InternalCache") == (["InternalCache"], frozenset())
    assert fn("_ZTVN2ns3FooE") == (["ns", "Foo"], frozenset())
    assert fn("_ZTI6Result") == (["Result"], frozenset())
    assert fn("_ZTTN2ns3FooE") == (["ns", "Foo"], frozenset())
    # An ordinary member mangling (no TV/TI/TT special-name code) is not a
    # vtable/typeinfo/VTT symbol at all -- must not be misread as one.
    assert fn("_ZN3Foo3barEv") is None
    assert fn("not a mangled name") is None


def test_itanium_special_name_owner_identifiers() -> None:
    # New defect 1 fix: dependency-free identifier extraction for a
    # templated `_ZTV`/`_ZTI`/`_ZTT` owner -- the counterpart of
    # `itanium_special_name_owner_scope_components` used by surface.py's
    # *type-candidate* resolution instead of falling back to the optional
    # external demangler (host-dependent reproducibility defect).
    fn = mangled_name.itanium_special_name_owner_identifiers
    # No template args: identical to the plain scope-component names.
    assert fn("_ZTV13InternalCache") == frozenset({"InternalCache"})
    assert fn("_ZTVN2ns3FooE") == frozenset({"ns", "Foo"})
    # A templated owner: identifiers from the scope path AND every name
    # embedded in the template-argument list, at any nesting depth.
    assert fn("_ZTV3BoxIiE") == frozenset({"Box"})
    assert fn("_ZTVN4dnnl4pool6vectorIiEE") == frozenset({"dnnl", "pool", "vector"})
    assert fn("_ZTIN3BoxIN2ab3BazEEE") == frozenset({"Box", "ab", "Baz"})
    assert fn("_ZTT3BoxIiE") == frozenset({"Box"})
    # Not a TV/TI/TT special name, or unparseable -- None, same contract as
    # the sibling scope-component function.
    assert fn("_ZN3Foo3barEv") is None
    assert fn("not a mangled name") is None
    assert fn("") is None


def test_itanium_special_name_owner_identifiers_is_host_independent_property() -> None:
    """Property: for every representative templated special-name owner, the
    identifier set never depends on anything but the mangled text itself --
    called twice on the same input always yields the same result (pure,
    deterministic), and it never touches an external demangler (the
    function has no such dependency to begin with -- this pins that by
    construction, not by mocking, since the function imports nothing
    optional)."""
    fn = mangled_name.itanium_special_name_owner_identifiers
    cases = [
        "_ZTVN4dnnl4pool6vectorIiEE",
        "_ZTVSt6vectorIN2ab3BarEE",
        "_ZTIN3BoxIN2ab3BazEEE",
        "_ZTT3BoxIiE",
        "_ZTVN2ns5OuterIN2ns5InnerIiEEEE",
        "_ZTI6Result",
    ]
    for mangled in cases:
        first = fn(mangled)
        second = fn(mangled)
        assert first == second, mangled
        assert first is not None, mangled
        # The bare, non-templated scope components are always a subset of
        # the full identifier set (template args can only ever add names).
        scope = mangled_name.itanium_special_name_owner_scope_components(mangled)
        assert scope is not None
        # Every bare (non-template-suffixed) component name is present --
        # a component that had no template args attached at all keeps its
        # exact name; a templated one's un-suffixed prefix isn't
        # necessarily a real token on its own, so only assert this for the
        # untemplated indices.
        template_positions = scope[1]
        for idx, name in enumerate(scope[0]):
            # "std" is the `St` two-letter abbreviation, not a real
            # length-prefixed name token, so it is deliberately absent from
            # the structural identifier extraction (see the function's own
            # docstring) -- harmless for matching purposes, since no real
            # declared type is ever named literally "std".
            if idx not in template_positions and name != "std":
                assert name in first, (mangled, name, first)


def test_diff_cxx_rules_reexports_the_identical_function_object() -> None:
    assert (
        diff_cxx_rules.itanium_scope_components is mangled_name.itanium_scope_components
    )
    assert (
        diff_cxx_rules.itanium_scope_components_with_template_positions
        is mangled_name.itanium_scope_components_with_template_positions
    )
    assert diff_cxx_rules._itanium_strip_prefix is mangled_name._itanium_strip_prefix
    assert diff_cxx_rules.msvc_scope_components is mangled_name.msvc_scope_components
