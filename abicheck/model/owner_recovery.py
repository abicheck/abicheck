# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""Which class *owns* a mangled symbol -- a constructor/destructor, or a
vtable/typeinfo/VTT special name.

A sibling of :mod:`abicheck.model.mangled_name` rather than another function
inside it: that module is at the architecture gate's production file ceiling,
and this repository's rule for a file at its ceiling is to move
responsibility out to an owned module rather than trim it to fit. The
component grammar stays there (including the ``CI<n>`` inheriting-constructor
production, which is a parser production); recovering an *owner* from those
components -- with the two facts a consumer needs alongside it, whether the
path carries template arguments and whether the constructor is inheriting --
is this module's one job.
"""

from __future__ import annotations

from dataclasses import dataclass

from .mangled_name import (
    _itanium_strip_prefix,
    _step_next_component,
    _strip_abi_tag_suffixes,
    itanium_scope_components_with_template_positions,
)
from .mangled_name_template_args import (
    collect_type_candidate_identifiers as _collect_type_candidate_identifiers,
)

__all__ = [
    "SpecialMemberOwner",
    "itanium_special_member_owner",
    "itanium_special_name_owner_identifiers",
    "itanium_special_name_owner_scope_components",
]


#: The three Itanium ``<special-name>`` productions
#: ``diff_elf_layout.py``'s ELF-layout-only detectors synthesize a symbol
#: for (``TV`` vtable, ``TI`` typeinfo, ``TT`` VTT/construction-vtable-table)
#: -- each is ``_Z`` + this 2-character code + the SAME ``<class-enc>``/
#: ``<name>`` production an ordinary member mangling's nested-name prefix
#: already uses, so rewriting the code away and re-parsing through the
#: existing structural parser needs no new grammar.
_SPECIAL_NAME_OWNER_CODES = ("TV", "TI", "TT")

#: Itanium ABI Section 5.9 "Abbreviations" -- the six standard substitutions
#: that abbreviate a complete standard-library *type* (unlike ``St``, which
#: abbreviates only the ``std::`` scope *prefix* and can have further
#: components appended -- see ``itanium_scope_components``'s own docstring).
#: Maps each 2-character code to the scope-path a fully-spelled owner would
#: produce, so a bare-substitution owner (e.g. ``_ZTVSs``, a vtable for
#: ``std::string``) resolves exactly like the fully-spelled equivalent would
#: through the general parser below -- never through the optional external
#: ``demangle()`` fallback.
#:
#: Round 9/10 finding (Codex review, fresh evidence, macOS CI): before this,
#: a bare-substitution owner fell all the way through to ``surface.py``'s
#: ``demangle()`` fallback, and that fallback's output text is demangler-
#: implementation-dependent for exactly these six codes -- GNU's demangler
#: (the ``cxxfilt`` PyPI package's ``__cxa_demangle`` binding, or binutils
#: ``c++filt``) renders ``Ss`` as the fully-spelled
#: ``std::basic_string<char, std::char_traits<char>, std::allocator<char> >``,
#: while LLVM's demangler (macOS's system ``c++filt``, the only backend
#: reachable there once the libstdc++-only ``cxxfilt`` binding fails to
#: load) renders the identical substitution using a shorthand alias instead.
#: The *same* comparison, run on two demangler-equipped hosts, therefore
#: produced two different type-candidate identifier sets purely from that
#: spelling difference -- on the LLVM-demangled host the (still valid,
#: demangler-equipped) spelling shared no identifier with a model record
#: named ``basic_string``, silently falling through to "unknown, keep" as
#: if no demangler were installed at all, which is exactly the
#: reproducibility defect this whole structural-parser family exists to
#: avoid for every *other* shape. Resolving these six codes structurally
#: closes the gap the same way ``St`` already closes it for the scope-prefix
#: case.
_STANDARD_SUBSTITUTION_OWNER_SCOPE: dict[str, tuple[str, ...]] = {
    "Sa": ("std", "allocator"),
    "Sb": ("std", "basic_string"),
    "Ss": ("std", "basic_string"),
    "Si": ("std", "basic_istream"),
    "So": ("std", "basic_ostream"),
    "Sd": ("std", "basic_iostream"),
}


def itanium_special_name_owner_identifiers(mangled: str) -> frozenset[str] | None:
    """Type-candidate identifiers for a ``_ZTV``/``_ZTI``/``_ZTT`` special
    name's owning-class production: the fully-qualified owner name, its own
    bare (rightmost) component, and every class/namespace name embedded in
    any template-argument list the owner's path carries, at any nesting
    depth.

    Purely structural (no ``c++filt``/``cxxfilt``), like
    :func:`itanium_special_name_owner_scope_components`. This function
    exists specifically so public-surface scoping's *type-candidate*
    resolution (``surface.py``'s ``_resolve_type_candidates``) can match a
    **templated** vtable/RTTI/VTT owner (e.g. a libstdc++ container
    instantiation, or any user template) against the model's own canonical
    type names without falling back to the optional external demangler for
    that shape — a policy-affecting classification (public-surface scoping,
    and anything downstream that depends on it, such as contract-coverage
    classification) must not silently vary by whether that tool happens to
    be installed on the host. Compare
    :func:`itanium_special_name_owner_scope_components`'s own docstring,
    which established this rule for the non-templated case; this closes the
    templated case a fallback to :func:`~abicheck.demangle.demangle`
    previously (and host-dependently) handled.

    Deliberately **does not** return a bare *namespace-path* component (the
    ``"ns"`` in ``ns::Wrapper<int>``) as its own standalone candidate (Codex
    review, fresh evidence): an earlier version of this function flattened
    every length-prefixed name anywhere in the owner's scope path and its
    template-argument lists into one undifferentiated set, so
    ``_ZTVN2ns7WrapperIiEE`` produced ``{"ns", "Wrapper"}`` with "ns" fully
    interchangeable with the real owner "Wrapper". Since
    ``surface.py``'s ``_classify_type_level`` demotes a finding when *every*
    resolvable candidate is confidently private/unreachable, an unrelated
    modeled type that happens to share the bare namespace's own name (e.g. a
    record literally named ``ns`` with no ``ns::Wrapper<int>`` reachable)
    could stand in for the real, unresolvable owner and wrongly demote a
    genuine break. The fix mirrors how ``surface.py``'s own
    ``_type_identifiers`` already treats an ordinary (non-mangled) qualified
    type string — a namespace qualifier is only ever part of the fused
    qualified token or its own trailing ``::`` segment, never emitted
    standalone — by building the qualified owner name and its bare tail from
    each component's own *bare* (template-stripped) spelling
    (:func:`_step_next_component`'s ``bare_name``) rather than from every
    length-prefixed name found anywhere in the owner's whole span, and
    collecting template-argument identifiers only from the exact substring
    each component's own directly-attached ``I…E`` block occupies.

    Returns ``None`` under the identical conditions
    :func:`itanium_special_name_owner_scope_components` does (unrecognized
    special-name code, or a remainder that fails to parse at all). A
    substitution-encoded template argument (``S_``, ``Sa``, ``St``, …) is a
    documented, accepted limitation: this parser does not resolve a
    substitution back to the type it abbreviates, so a name reachable only
    through one is absent from the result — this can only ever *narrow* the
    candidate set (never fabricate a wrong one), so a match this parser
    misses still falls back to the existing conservative "unknown, keep"
    default the caller already applies, not a new failure mode; deterministic
    and host-independent either way, which is the property this function
    exists to guarantee.
    """
    if not mangled.startswith("_Z"):
        return None
    code, rest = mangled[2:4], mangled[4:]
    if code not in _SPECIAL_NAME_OWNER_CODES or not rest:
        return None
    std_scope = _STANDARD_SUBSTITUTION_OWNER_SCOPE.get(rest)
    if std_scope is not None:
        return frozenset({"::".join(std_scope), std_scope[-1]})
    prefix = _itanium_strip_prefix("_Z" + rest)
    if prefix is None:
        return None
    s, nested = prefix
    i = 0
    n = len(s)
    bare_components: list[str] = []
    template_identifiers: set[str] = set()
    if s[i : i + 2] == "St":
        # The canonical Itanium `St` substitution for the `std::` scope
        # prefix (see `itanium_scope_components`'s own docstring for the
        # empirically-confirmed encoding). Codex review, fresh evidence,
        # findings-analysis-fixes review round 4, finding 3: this branch
        # used to advance `i` past `St` without ever appending `"std"` to
        # `bare_components`, so `_ZTVSt6vectorIiE` (`std::vector<int>`)
        # produced only `{"vector"}` -- never `{"std::vector", "vector"}`
        # the way the ordinary, fully-spelled `_ZTVN3std6vectorIiEE` form
        # already does (`itanium_scope_components_with_template_positions`
        # appends `"std"` explicitly for that shape). A snapshot modeling an
        # internal `std::vector` record but no bare `vector` would then read
        # this owner as unknown and keep its vtable churn in-surface instead
        # of demoting it, purely because of which of the two equivalent
        # mangled spellings the compiler happened to emit.
        bare_components.append("std")
        i += 2
    while i < n:
        step = _step_next_component(s, i, nested)
        if step is None:
            return None
        label, i, done, template_attached, bare_name = step
        if label is not None and bare_name is not None:
            bare_components.append(bare_name)
            if template_attached:
                # `label` is `bare_name` followed by the raw, directly-
                # attached `I...E` template-argument text verbatim (see
                # `_parse_source_name_component`'s own docstring), so the
                # suffix after `bare_name` is exactly that template span --
                # every length-prefixed name inside it is genuinely
                # "embedded in a template-argument list", never a
                # namespace-path component of an *enclosing* scope.
                template_identifiers |= _collect_type_candidate_identifiers(
                    label, len(bare_name), len(label)
                )
        if done:
            break
    if not bare_components:
        return None
    # GNU ABI tags (see `_parse_source_name_component`) are stripped from
    # every candidate here -- but nowhere upstream -- because this function
    # feeds `surface.py`'s *type-candidate* matching against the model's own
    # (untagged) record names specifically; `itanium_special_name_owner_scope_components`
    # and the shared parser above keep tags intact for identity purposes.
    # Codex review, fresh evidence, findings-analysis-fixes review round 5,
    # finding 2: an ABI-tagged internal class (`_ZTV1CB3tag`, i.e.
    # `C[[gnu::abi_tag("tag")]]`) previously produced only the tagged
    # candidate `"C[abi:tag]"`, which could never match the model's own
    # untagged record name `"C"` -- so a tagged internal class's vtable/RTTI
    # churn was conservatively kept in the public surface instead of being
    # demoted, purely because of the tag.
    result = {_strip_abi_tag_suffixes(x) for x in template_identifiers}
    result.add(_strip_abi_tag_suffixes("::".join(bare_components)))
    result.add(_strip_abi_tag_suffixes(bare_components[-1]))
    return frozenset(result)


def itanium_special_name_owner_scope_components(
    mangled: str,
) -> tuple[list[str], frozenset[int]] | None:
    """Scope components of the class a vtable/typeinfo/VTT special-name
    belongs to, e.g. ``_ZTVN2ns3FooE`` -> ``(["ns", "Foo"], frozenset())``.

    Purely structural, like :func:`itanium_scope_components_with_template_
    positions` itself -- no dependency on an external demangler
    (``c++filt``/``cxxfilt``), which is not installed on every platform.
    Without this, a caller that fell back to :func:`~abicheck.demangle.
    demangle` for these three symbol shapes specifically would get a
    policy-affecting classification (public-surface scoping) that silently
    varies by whether that optional tool happens to be present on the host
    (Codex review, fresh evidence). Returns ``None`` for any other prefix,
    or when the remainder fails to parse.
    """
    if not mangled.startswith("_Z"):
        return None
    code, rest = mangled[2:4], mangled[4:]
    if code not in _SPECIAL_NAME_OWNER_CODES or not rest:
        return None
    std_scope = _STANDARD_SUBSTITUTION_OWNER_SCOPE.get(rest)
    if std_scope is not None:
        return list(std_scope), frozenset()
    return itanium_scope_components_with_template_positions("_Z" + rest)


@dataclass(frozen=True)
class SpecialMemberOwner:
    """The owning scope of a constructor/destructor export, parsed structurally.

    ``member`` is ``"{ctor}"`` or ``"{dtor}"``. ``owner_components`` are the
    enclosing nested-name components with each one's directly-attached
    template-argument text stripped back off, so ``_ZN3api3BoxIiEC1Ev``
    yields ``("api", "Box")`` and ``owner_carries_template_arguments`` is
    ``True``. The two are reported separately on purpose: the bare path is
    what a declaration join can match against a model that spells template
    arguments in C++ (``api::Box<int>``) rather than in Itanium encoding
    (``BoxIiE``), while the flag records that the path alone does **not**
    identify the entity -- a caller must not treat a primary template's
    declaration as evidence about one of its specializations.

    ``inherited`` marks a C++11 inheriting constructor (``CI<n>``), whose
    owner is the derived class.
    """

    member: str
    owner_components: tuple[str, ...]
    owner_carries_template_arguments: bool
    inherited: bool

    @property
    def qualified_owner(self) -> str:
        """The owner path, template-argument text stripped (``api::Box``)."""
        return "::".join(self.owner_components)


def itanium_special_member_owner(mangled: str) -> SpecialMemberOwner | None:
    """Structural owner of a ctor/dtor mangling, or ``None``.

    ``None`` means "this parser reached no verdict" -- a non-Itanium name, an
    unmodelled production, or a name whose leaf is not a ctor/dtor. It never
    means "no owner": a caller must treat ``None`` as unresolved evidence,
    not as a licence to conclude anything about the symbol.

    Exists so a consumer joining an export against declarations asks one
    owner for the answer instead of re-deriving scope recovery from
    :func:`itanium_scope_components`' template-inclusive component text, which
    cannot be compared against a model type name at all (``BoxIiE`` is not
    ``Box<int>``) and silently produced a never-matching owner spelling.
    """
    prefix = _itanium_strip_prefix(mangled)
    if prefix is None:
        return None
    s, nested = prefix
    if not nested:
        return None  # a free function's single component is never a ctor/dtor
    bare_components: list[str] = []
    templated = False
    i = 0
    n = len(s)
    if s[i : i + 2] == "St":
        bare_components.append("std")
        i += 2
    inherited = False
    member: str | None = None
    while i < n:
        before = i
        step = _step_next_component(s, i, nested)
        if step is None:
            return None
        label, i, done, template_attached, bare_name = step
        if label in ("{ctor}", "{dtor}"):
            member = label
            # An inheriting constructor is the only ctor form this parser
            # stops on (``done``) while still inside the ``N…E`` wrapper.
            inherited = label == "{ctor}" and done
            break
        if label is not None and bare_name is not None:
            bare_components.append(bare_name)
            templated = templated or template_attached
        elif label is None:
            return None  # nested name closed before any ctor/dtor component
        if done or i == before:
            return None
    if member is None or not bare_components:
        return None
    return SpecialMemberOwner(
        member=member,
        owner_components=tuple(bare_components),
        owner_carries_template_arguments=templated,
        inherited=inherited,
    )
