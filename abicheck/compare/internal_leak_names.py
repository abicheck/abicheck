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

"""Internal-namespace subject classification for the leak detectors.

Decides whether a type spelling lives in an internal namespace and how its
pointer and template structure reaches it. Owns the ``internal_namespace``
and ``internal_type_leak`` name heuristics, whose owner module is this one.
"""

from __future__ import annotations

import collections
import re
from collections.abc import Iterable
from typing import TYPE_CHECKING

from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.name_heuristics import (
    NameHeuristicEffect,
    StructuralFact,
    register_name_heuristic,
    register_severity_raising_heuristic,
)
from .internal_namespaces import (
    DEFAULT_INTERNAL_NAMESPACES,
    TEMPLATE_ARG_RE,
    is_internal_type,
    name_segments,
    strip_template_args as _strip_template_args,
)
from .layout_visibility import layout_proven_invisible

if TYPE_CHECKING:
    from ..model import AbiSnapshot, RecordType


def _strip_signature_params(name: str) -> str:
    """Truncate a demangled C++ function signature at its own parameter list.

    ``cxxfilt``/``c++filt`` demangle a mangled function name to the full
    signature, e.g. ``ns::api::foo(ns::detail::T*)`` — not just the bare
    qualified name. Tracks paren depth and cuts at the first depth-0 ``(``
    (the function's own parameter list opening; a deeper ``(`` is a nested
    parameter type, e.g. a function-pointer parameter, and must not be
    mistaken for it). A name with no top-level ``(`` at all (already a bare
    qualified name, or not a function) is returned unchanged.
    """
    depth = 0
    for i, ch in enumerate(name):
        if ch == "(":
            if depth == 0:
                return name[:i]
            depth += 1
        elif ch == ")":
            depth -= 1
    return name


# Strip type decorators — copy of the helper used in dwarf_snapshot. Kept
# local so that this module has no circular import with dwarf_snapshot.
_DECORATOR_RE = re.compile(r"(\*|&{1,2}|\[\d*\]|\bconst\b|\bvolatile\b)")


def _strip_decorators(typename: str) -> str:
    """Strip pointer/reference/const/volatile/array suffixes from *typename*.

    Returns the bare type name (or template) suitable for lookup in the
    types map.
    """
    s = _DECORATOR_RE.sub("", typename or "").strip()
    # Collapse multiple spaces.
    return re.sub(r"\s+", " ", s)


def _is_known_pointer_wrapper(outer: str) -> bool:
    """Return True only for pointer-owning wrapper spellings we know.

    This deliberately avoids substring matches such as
    ``acme::unique_ptr_value<T>``: a user-defined type can embed ``T`` by
    value while having a smart-pointer-like name.  In ambiguous cases, keep the
    path value-propagating so the ABI break is visible.
    """
    clean = _strip_template_args(_strip_decorators(outer)).strip()
    if not clean:
        return False
    leaf = clean.rsplit("::", 1)[-1]
    leaf_l = leaf.lower()
    if leaf_l in {"unique_ptr", "shared_ptr", "weak_ptr", "__uniq_ptr_impl"}:
        return True
    # oneDAL exposes detail::pimpl<T> as its public pimpl alias.  Keep this
    # project-specific alias narrow; a bare ``acme::pimpl<T>`` may be an
    # arbitrary by-value template and must not suppress layout leaks.
    return clean == "oneapi::dal::detail::pimpl"


def _candidate_type_names_indirect(typename: str) -> list[tuple[str, bool]]:
    """Yield ``(candidate_type_name, reached_through_pointer)`` pairs for *typename*.

    The per-hop path model: indirection is computed **per template argument**, so
    the pointer in ``std::_Tuple_impl<0, proxy*, deleter>`` is attributed to
    ``proxy`` (the pointee) and not to ``deleter`` — and a by-value argument in
    ``std::pair<ns::detail::Impl, int*>`` (``Impl``) is correctly *not* indirect
    even though the spelling contains a ``*``. A smart-pointer wrapper
    (``unique_ptr``/``shared_ptr``/``weak_ptr``) makes its argument the pointee.

    For ``std::unique_ptr<acme::lib::detail::impl>`` we surface both the outer
    template and the inner ``detail::impl`` (what users see leaking); the inner
    one carries ``reached_through_pointer=True``.
    """
    out: list[tuple[str, bool]] = []
    base = _strip_decorators(typename)
    if not base:
        return out
    # Base candidate: indirect iff the whole spelling is a top-level pointer/ref
    # (collapse template args so a pointer buried in an argument doesn't count).
    top = _strip_template_args(typename)
    top_ptr = "*" in top or "&" in top
    out.append((base, top_ptr))
    # Smart-pointer wrapper is decided on the OUTER type only (template args
    # already collapsed in `top`), so a nested ``pimpl<Other>`` / ``unique_ptr``
    # in an unrelated argument of a by-value template does not mark its siblings
    # indirect (Codex review).
    outer = _strip_decorators(top)
    smart = _is_known_pointer_wrapper(outer)
    # Walk the outermost <...> of the ORIGINAL spelling (keeps inner */&).
    depth = 0
    start = -1
    for i, ch in enumerate(typename):
        if ch == "<":
            if depth == 0:
                start = i + 1
            depth += 1
        elif ch == ">":
            depth -= 1
            if depth == 0 and start >= 0:
                for p in _split_top_level_commas(typename[start:i]):
                    p_top = _strip_template_args(p)
                    # A top-level pointer/ref on the enclosing template
                    # (``pair<Impl, int>*``) puts every argument behind it.
                    arg_ptr = top_ptr or smart or "*" in p_top or "&" in p_top
                    sub = _strip_decorators(p)
                    if sub:
                        out.append((sub, arg_ptr))
                        for c2, ind2 in _candidate_type_names_indirect(sub):
                            out.append((c2, ind2 or arg_ptr))
                start = -1
    return out


def _split_top_level_commas(s: str) -> list[str]:
    """Split *s* on commas that are not nested inside ``<...>``."""
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in s:
        if ch == "<":
            depth += 1
            buf.append(ch)
        elif ch == ">":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def _build_qualified_index(types: Iterable[RecordType]) -> dict[str, set[str | None]]:
    """Bare ``RecordType.name`` -> ``{distinct qualified_name}`` over *types*.

    Built directly from the source type list (before any bare-name dict
    collapses duplicates), so an ambiguous bare name shared by two distinct
    types (e.g. both ``api::Foo`` and ``api::detail::Foo``) stays detectable
    — a plain ``{t.name: t for t in types}`` map (:func:`_build_type_map`)
    would silently keep only whichever record was inserted last.
    """
    idx: dict[str, set[str | None]] = collections.defaultdict(set)
    for t in types:
        idx[t.name].add(t.qualified_name)
    return idx


def _typename_is_internal(
    typename: str,
    qualified_index: dict[str, set[str | None]],
    internal_namespaces: Iterable[str],
) -> bool:
    """Like :func:`is_internal_type`, but also consults the matching
    ``RecordType.qualified_name`` when the bare *typename* itself carries no
    namespace segment.

    ``RecordType.name`` (and every bare spelling derived from it — bases,
    field types, param/return types) is deliberately unqualified so it keeps
    matching the DWARF backend's equally-bare struct names (see
    ``RecordType.qualified_name``'s docstring in model.py); castxml is the
    only source that can currently recover the real namespace path, and only
    via that separate field. Without this fallback, a type genuinely declared
    in an internal namespace (``mylib::detail::descriptor_base``) is invisible
    to :func:`is_internal_type` once reduced to its bare spelling
    (``descriptor_base``), silently disabling the leak check for it.

    Only applies the qualified-name fallback when *typename* unambiguously
    names one distinct qualified type: if two records share this bare name
    (e.g. a public ``api::Foo`` and an internal ``api::detail::Foo``), which
    one is "the" match can't be resolved from the bare name alone, so this
    returns ``False`` (no fallback) rather than guessing via whichever
    record happened to be indexed.
    """
    if is_internal_type(typename, internal_namespaces):
        return True
    qnames = qualified_index.get(typename)
    if not qnames or len(qnames) != 1:
        return False
    (qname,) = qnames
    if not qname:
        return False
    return is_internal_type(qname, internal_namespaces)


def _internal_subject(
    name: str,
    *,
    internal_namespaces: Iterable[str] = DEFAULT_INTERNAL_NAMESPACES,
    qualified_index: dict[str, set[str | None]] | None = None,
) -> bool:
    """The internal-namespace spelling check, with the qualified-name
    fallback of :func:`_typename_is_internal` when an index is given."""
    if qualified_index is None:
        return is_internal_type(name, internal_namespaces)
    return _typename_is_internal(name, qualified_index, internal_namespaces)


def _layout_proven_invisible(fact_input: tuple[str, AbiSnapshot, AbiSnapshot]) -> bool:
    return layout_proven_invisible(*fact_input)


_INTERNAL_VOCABULARY = "abicheck.model.symbol_ownership:DEFAULT_INTERNAL_NAMESPACES"

#: Design-hardening Phase 5: the name only nominates the pointer-only
#: leniency; proven-invisible layout confirms it (decision 2A).
INTERNAL_NAMESPACE = register_name_heuristic(
    "internal_namespace",
    owner=__name__,
    effect=NameHeuristicEffect.LOWER_CONFIDENCE,
    description=(
        "a detail::/impl::/internal:: segment marks a type as implementation "
        "detail, eligible for the pointer-only layout leniency"
    ),
    matcher=_internal_subject,
    helpers=(is_internal_type, name_segments, _typename_is_internal),
    confirmed_by=StructuralFact(
        "compare.layout_visibility.layout_proven_invisible", _layout_proven_invisible
    ),
    vocabularies=(_INTERNAL_VOCABULARY,),
    patterns=(TEMPLATE_ARG_RE,),
)

#: The name nominates a BREAKING leak finding; a public reachability path decides.
INTERNAL_TYPE_LEAK = register_severity_raising_heuristic(
    "internal_type_leak",
    owner=__name__,
    raises=("INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API",),
    description=(
        "a changed internal-namespace type is reported as leaking through "
        "the public API"
    ),
    matcher=_internal_subject,
    helpers=(is_internal_type, name_segments, _typename_is_internal),
    fact=StructuralFact("policy.internal_leak.public_signature_reaches_type", bool),
    vocabularies=(_INTERNAL_VOCABULARY,),
    patterns=(TEMPLATE_ARG_RE,),
)

# Change kinds whose ``symbol`` carries a ``Type::field`` form (i.e. the
# field name appended after the containing type). For these, the leading
# segment is the containing type and the trailing segment must be
# stripped.
#
# NOTE: ``TYPE_FIELD_*`` (emitted by ``diff_types``) and
# ``STRUCT_FIELD_*`` (emitted by ``diff_platform``) follow *different*
# symbol conventions:
#
#     diff_types:    symbol = "ns::Type"          (field name in description only)
#     diff_platform: symbol = "ns::Type::field"   (field name appended)
#
# Stripping the last segment for ``TYPE_FIELD_*`` would silently truncate
# legitimate namespaced type names like ``ns::detail::Impl`` into
# ``ns::detail``, breaking the reachability lookup. So only the
# ``STRUCT_FIELD_*`` kinds participate in stripping.
_FIELD_LEVEL_LEAK_KINDS: frozenset[ChangeKind] = frozenset(
    {
        ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
        ChangeKind.STRUCT_FIELD_REMOVED,
        ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
    }
)


def _root_type_name_for_change(c: Change) -> str:
    """Peel any "::field" suffix off *c*'s symbol to get the containing type.

    Only strips the final segment for change kinds where the emitter is
    known to put the field name into the symbol (``STRUCT_FIELD_*`` from
    ``diff_platform``). Other kinds — including the ``TYPE_FIELD_*``
    family from ``diff_types`` — carry the containing type name directly
    in ``symbol`` and must be returned as-is to preserve namespaced
    internal type names like ``ns::detail::Impl``.
    """
    sym = c.symbol or ""
    if "::" in sym and c.kind in _FIELD_LEVEL_LEAK_KINDS:
        return sym.rsplit("::", 1)[0]
    return sym
