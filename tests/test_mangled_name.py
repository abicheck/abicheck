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


def test_itanium_scope_components_conversion_operator() -> None:
    """A conversion operator's own leaf component (``cv``, followed by the
    raw, unparsed target-type encoding -- see ``_parse_operator_component``'s
    own docstring) is a distinct terminal shape ``_step_next_component``
    handles separately from an ordinary source-name/ctor/dtor component.
    Real mangling for ``struct Foo { operator ns::Bar() {...} };``, verified
    against a real g++ (`nm` on a compiled TU): ``_ZN3FoocvN2ns3BarEEv``.
    """
    assert mangled_name.itanium_scope_components("_ZN3FoocvN2ns3BarEEv") == [
        "Foo",
        "{op:cv:N2ns3BarEEv}",
    ]


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
    #
    # Findings-analysis-fixes review round 3, finding 4: the *qualified*
    # owner (and its own bare tail) are the only namespace-carrying
    # candidates -- a bare namespace-*path* segment (e.g. "ns" out of
    # "ns::Foo") is never emitted standalone, mirroring how surface.py's own
    # `_type_identifiers` treats an ordinary (non-mangled) qualified type
    # string: a namespace qualifier is only ever part of the fused qualified
    # token or its own trailing "::" segment, never a free-standing token.
    fn = mangled_name.itanium_special_name_owner_identifiers
    # No template args: the qualified owner and its own bare tail.
    assert fn("_ZTV13InternalCache") == frozenset({"InternalCache"})
    assert fn("_ZTVN2ns3FooE") == frozenset({"ns::Foo", "Foo"})
    # A templated owner: the qualified owner/bare tail, plus every name
    # embedded in the owner's own template-argument list(s), at any nesting
    # depth -- but never a bare namespace-path segment.
    assert fn("_ZTV3BoxIiE") == frozenset({"Box"})
    assert fn("_ZTVN4dnnl4pool6vectorIiEE") == frozenset(
        {"dnnl::pool::vector", "vector"}
    )
    assert fn("_ZTIN3BoxIN2ab3BazEEE") == frozenset({"Box", "ab::Baz", "Baz"})
    assert fn("_ZTT3BoxIiE") == frozenset({"Box"})
    # Not a TV/TI/TT special name, or unparseable -- None, same contract as
    # the sibling scope-component function.
    assert fn("_ZN3Foo3barEv") is None
    assert fn("not a mangled name") is None
    assert fn("") is None
    # A `TV`/`TI`/`TT` code whose remainder opens a nested-name wrapper
    # (`N`) but never supplies a component before running out of string --
    # the owner-component loop never executes at all, leaving no
    # candidate to build a result from.
    assert fn("_ZTVN") is None
    # A malformed length-prefixed owner name: "9" claims a 9-char name
    # only 1 character ("x") can ever satisfy.
    assert fn("_ZTV9x") is None
    # A well-formed short owner name carrying a GNU ABI tag whose own
    # directly-attached template-argument list never closes.
    assert fn("_ZTV1CB3tagI") is None
    # The exact adversarial shape Codex named: a bare namespace segment must
    # never appear standalone, even though it legitimately contributes to
    # the qualified owner and would otherwise "just happen" to be a
    # plausible-looking candidate on its own.
    assert "ns" not in fn("_ZTVN2ns3FooE")
    assert "dnnl" not in fn("_ZTVN4dnnl4pool6vectorIiEE")
    assert "pool" not in fn("_ZTVN4dnnl4pool6vectorIiEE")
    assert "ab" not in fn("_ZTVN4dnnl4pool6vectorIiEE")  # not even in this one
    # Findings-analysis-fixes review round 4, finding 3: the canonical `St`
    # substitution for `std::` must produce the identical qualified-owner
    # candidate the fully-spelled `N3std...E` nested form already does --
    # `_ZTVSt6vectorIiE` (`std::vector<int>`, the substituted spelling) and
    # `_ZTVN3std6vectorIiEE` (the same type, spelled out) must agree.
    assert fn("_ZTVSt6vectorIiE") == frozenset({"std::vector", "vector"})
    assert fn("_ZTVN3std6vectorIiEE") == frozenset({"std::vector", "vector"})
    assert fn("_ZTVSt6vectorIiE") == fn("_ZTVN3std6vectorIiEE")
    # Findings-analysis-fixes review round 5, finding 2: a GNU
    # `__attribute__((abi_tag("tag")))`-carrying class's vtable mangles the
    # tag directly onto the owner's bare name (`_ZTV1CB3tag`, confirmed
    # against a real compiled `class __attribute__((abi_tag("tag"))) C {
    # virtual ~C(); };`), but CastXML/clang model the record itself under its
    # plain, untagged name `"C"`. The surface-candidate set here must strip
    # the tag so it can still match that untagged model name -- unlike
    # `itanium_special_name_owner_scope_components`, which must keep it (see
    # that function's own test above and its docstring).
    assert fn("_ZTV1CB3tag") == frozenset({"C"})
    assert fn("_ZTI1CB3tag") == frozenset({"C"})
    # A tag on a *templated* owner: tag stripped, template args untouched.
    assert fn("_ZTV1CB3tagIiE") == frozenset({"C"})
    # A tag nested inside a namespace-qualified owner.
    assert fn("_ZTVN2ns1CB3tagE") == frozenset({"ns::C", "C"})
    # A tag on a name embedded in a template-argument list.
    assert fn("_ZTV3BoxI1CB3tagE") == frozenset({"Box", "C"})


def test_itanium_special_name_owner_identifiers_is_host_independent_property() -> None:
    """Property: for every representative templated special-name owner, the
    identifier set never depends on anything but the mangled text itself --
    called twice on the same input always yields the same result (pure,
    deterministic), and it never touches an external demangler (the
    function has no such dependency to begin with -- this pins that by
    construction, not by mocking, since the function imports nothing
    optional). Also pins finding 4 (review round 3) as a property, not just
    a fixed example: no namespace-*path* component (every scope component
    before the trailing owner-class one) may ever appear as its own
    standalone candidate."""
    fn = mangled_name.itanium_special_name_owner_identifiers
    cases = [
        "_ZTVN4dnnl4pool6vectorIiEE",
        "_ZTVSt6vectorIN2ab3BarEE",
        "_ZTIN3BoxIN2ab3BazEEE",
        "_ZTT3BoxIiE",
        "_ZTVN2ns5OuterIN2ns5InnerIiEEEE",
        "_ZTI6Result",
        # Findings-analysis-fixes review round 4, finding 3: the `St`
        # substitution shape and its fully-spelled equivalent.
        "_ZTVSt6vectorIiE",
        "_ZTVN3std6vectorIiEE",
        # Review round 5, finding 2: ABI-tagged owners, plain and templated.
        "_ZTV1CB3tag",
        "_ZTV1CB3tagIiE",
        "_ZTVN2ns1CB3tagE",
        "_ZTV3BoxI1CB3tagE",
    ]
    for mangled in cases:
        first = fn(mangled)
        second = fn(mangled)
        assert first == second, mangled
        assert first is not None, mangled
        scope = mangled_name.itanium_special_name_owner_scope_components(mangled)
        assert scope is not None
        components, template_positions = scope
        # Every namespace-*path* component (every component strictly before
        # the trailing owner-class one) that carries no template args of its
        # own must never appear as a standalone candidate -- only the fully
        # qualified owner and its own bare tail may. Skip a component whose
        # own bare spelling coincidentally equals the tail's (e.g. an
        # `Outer::Outer`-shaped owner) since that overlap is legitimate, not
        # a namespace-token leak. `"std"` (the `St`-substitution's own
        # synthesized component, review round 4 finding 3) is no longer
        # exempted here: it must obey the identical rule as any other
        # namespace-path component now that it is fused into the qualified
        # owner rather than dropped.
        for idx, name in enumerate(components[:-1]):
            if idx not in template_positions and name != components[-1]:
                assert name not in first, (mangled, name, first)


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
