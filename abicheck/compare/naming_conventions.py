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

"""Naming-convention classifiers used by the flat ``diff_*`` detectors.

The owner of the spelling half of each heuristic below, and of its
registration with ``model.name_heuristics`` (design-hardening Phase 5,
family F4). Each was an inline string check in a legacy detector module;
moving it here is what lets those modules route through the registered
handle without growing past their ADR-061 debt baselines. The detectors
import the handles (and, for their existing tests, the matcher names) back.

A name here may only lower confidence or route a finding to review, except
through a severity-raising registration, which names the structural fact
that decides it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING

from ..diff_symbols_renames import find_prefix_rename_pairs
from ..model.name_heuristics import (
    NameHeuristicEffect,
    StructuralFact,
    register_name_heuristic,
    register_severity_raising_heuristic,
)
from .internal_namespaces import is_internal_type
from .template_surface import strip_template_args as _strip_template_args

if TYPE_CHECKING:
    from ..model import Function


# -------------------------------------------------------------------------
# From diff_platform_elf_dynamic.py
# -------------------------------------------------------------------------

_INTERNAL_NAME_PATTERNS = (
    "internal",
    "helper",
    "_impl",
    "detail",
    "private",
    "__",
    "_priv",
    "_int_",
    "_do_",
    "_handle_",
)


def _looks_internal(name: str) -> bool:
    """Heuristic: True if symbol name looks like internal implementation detail."""
    lower = name.lower()
    return any(pat in lower for pat in _INTERNAL_NAME_PATTERNS)


#: Registered name heuristic (design-hardening Phase 5): an internal-looking
#: export name only *routes to review* -- it raises the informational,
#: COMPATIBLE ``visibility_leak`` on the OLD library, never a break.
ELF_INTERNAL_SYMBOL_NAME = register_name_heuristic(
    "elf_internal_symbol_name",
    owner=__name__,
    effect=NameHeuristicEffect.ROUTE_TO_REVIEW,
    description="an exported ELF-only symbol named *_impl/detail/__... looks internal",
    matcher=_looks_internal,
    vocabularies=("_INTERNAL_NAME_PATTERNS",),
)


# -------------------------------------------------------------------------
# From diff_platform.py
# -------------------------------------------------------------------------

# Matches Itanium-style ::v1::, ::__v2:: AND libc++-style ::__1::, ::__2::
# Anchored to :: on both sides to avoid matching inside identifiers.
_INLINE_NS_RE = re.compile(r"::(?:__)?(?:v)?\d+::")


def _strip_inline_ns(name: str) -> str:
    return _INLINE_NS_RE.sub("::", name)


def _differ_only_by_inline_namespace(names: tuple[str, str]) -> bool:
    """Both spellings reduce to one name, and at least one carried a
    versioned inline-namespace segment."""
    old_name, new_name = names
    stripped = _strip_inline_ns(new_name)
    return _strip_inline_ns(old_name) == stripped and (
        stripped != new_name or stripped != old_name
    )


def _symbol_replaced_in_export_table(
    fact_input: tuple[str, str, Mapping[str, object], Mapping[str, object]],
) -> bool:
    old_m, new_m, old_exports, new_exports = fact_input
    return old_m not in new_exports and new_m not in old_exports


#: Registered severity-raising heuristic (design-hardening Phase 5): a
#: ``vN``/``__N`` segment only nominates a pair; ``inline_namespace_moved``
#: (BREAKING) counts it only when the old symbol left the export table and the
#: new one entered it.
INLINE_NAMESPACE_MOVE = register_severity_raising_heuristic(
    "inline_namespace_move",
    owner=__name__,
    description="two exports whose names differ only by a ::vN::/::__N:: segment moved",
    matcher=_differ_only_by_inline_namespace,
    helpers=(_strip_inline_ns,),
    fact=StructuralFact(
        "compare.export_table.symbol_replaced", _symbol_replaced_in_export_table
    ),
    patterns=(_INLINE_NS_RE,),
)


# -------------------------------------------------------------------------
# From diff_templates.py
# -------------------------------------------------------------------------


def _is_internal_segment(name: str, internal_segments: tuple[str, ...]) -> bool:
    """Return True if any ``::`` segment of ``name`` (template args
    stripped) matches one of ``internal_segments`` exactly."""
    bare = _strip_template_args(name)
    return any(s in bare.split("::") for s in internal_segments)


_INTERNAL_TEMPLATE_NAMESPACES: tuple[str, ...] = (
    "detail",
    "impl",
    "internal",
    "__detail",
    "_impl",
    "__internal",
)


def _internal_stem(
    stem: str,
    *,
    internal_namespaces: tuple[str, ...] = _INTERNAL_TEMPLATE_NAMESPACES,
) -> bool:
    return _is_internal_segment(stem, internal_namespaces)


def _public_instantiation_removed(
    fact_input: tuple[
        set[tuple[str, tuple[str, int, str]]], set[tuple[str, tuple[str, int, str]]]
    ],
) -> bool:
    # Direction matters: a (name, signature) pair that existed in OLD but is
    # absent from NEW means a consumer TU that already resolved and linked
    # against that mangled instantiation now has nothing to link against.
    # A pair present only in NEW cannot break an already-linked consumer.
    old_sigs, new_sigs = fact_input
    return bool(old_sigs - new_sigs)


#: Registered severity-raising heuristic (design-hardening Phase 5): the
#: internal namespace only nominates a template stem;
#: ``internal_template_leaks_via_public_api`` (BREAKING) is emitted only when a
#: public, exported instantiation of it was removed.
INTERNAL_TEMPLATE_LEAK = register_severity_raising_heuristic(
    "internal_template_leak",
    owner=__name__,
    description=(
        "a template in a detail::/impl::/internal:: namespace instantiated in "
        "the public export table"
    ),
    matcher=_internal_stem,
    helpers=(_is_internal_segment,),
    fact=StructuralFact(
        "compare.templates.public_instantiation_removed",
        _public_instantiation_removed,
    ),
    vocabularies=("_INTERNAL_TEMPLATE_NAMESPACES",),
)


# -------------------------------------------------------------------------
# From diff_cpp_patterns.py
# -------------------------------------------------------------------------

_SYCL_QUEUE_PARAM_RE = re.compile(r"\bsycl\s*::\s*queue\b")


def _strip_param_decorators(type_str: str) -> str:
    """Reduce a param type spelling to its bare base identifier for a
    type-map lookup: drop ``const``/``volatile`` and trailing pointer/
    reference markers (``queue&`` -> ``queue``)."""
    s = re.sub(r"\bconst\b|\bvolatile\b", "", type_str or "")
    return s.strip().rstrip("*&").strip()


def _has_sycl_queue_first_param(
    fn: Function, type_qualified_index: Mapping[str, set[str | None]] | None = None
) -> bool:
    if not fn.params:
        return False
    first = fn.params[0]
    type_str = first.type or ""
    if _SYCL_QUEUE_PARAM_RE.search(type_str):
        return True
    if type_qualified_index is None:
        return False
    # castxml never namespace-qualifies a Struct/Class/Union spelling in a
    # param type (RecordType.name itself stays bare — see its docstring in
    # model.py), so a real ``sycl::queue&`` param shows up here as the bare
    # ``queue&``. Fall back to the RecordType's qualified_name (when the
    # dumper recovered one) before giving up (case82) — but only when the
    # bare name unambiguously names one distinct type across both snapshots;
    # an ambiguous bare name (multiple distinct qualified names, e.g. both
    # ``mylib::queue`` and ``sycl::queue``) can't be resolved from the type
    # alone and must not guess.
    qnames = type_qualified_index.get(_strip_param_decorators(type_str))
    if not qnames or len(qnames) != 1:
        return False
    (qname,) = qnames
    return bool(qname and _SYCL_QUEUE_PARAM_RE.search(qname))


#: Registered name heuristic (design-hardening Phase 5): the ``sycl::queue``
#: spelling only *routes to review* -- it groups per-symbol removals the
#: symbol detector already reports (each still BREAKING) into one family
#: finding of the same severity.
SYCL_QUEUE_OVERLOAD = register_name_heuristic(
    "sycl_queue_overload",
    owner=__name__,
    effect=NameHeuristicEffect.ROUTE_TO_REVIEW,
    description="a first parameter spelled sycl::queue marks a DPC++ overload family",
    matcher=_has_sycl_queue_first_param,
    patterns=(_SYCL_QUEUE_PARAM_RE,),
)


# Ordered most-specific to least-specific so that ``avx512`` wins over
# ``avx`` and ``sse42`` over ``sse``.
_ISA_TOKENS: tuple[str, ...] = (
    "avx512",
    "avx2",
    "avx",
    "sse42",
    "sse41",
    "sse2",
    "sse",
    "neon",
    "sve",
    "scalar",
    "generic",
)


def _isa_token_in_symbol(symbol_name: str) -> str | None:
    """Find the most specific ISA token in *symbol_name*.

    Looks for ``_<token>_``, trailing ``_<token>``, or ``_<token>@`` (the
    identifier/signature boundary in a raw MSVC-decorated export string, e.g.
    ``?kmeans_compute_avx512@mylib@@YAHH@Z`` — needed when matching directly
    against a PE/Mach-O export table rather than a demangled ``Function.name``;
    see :func:`_build_removed_by_isa_from_raw_exports`). Case-insensitive.
    Returns the canonical lowercase token or ``None``.
    """
    if not symbol_name:
        return None
    lowered = symbol_name.lower()
    for token in _ISA_TOKENS:
        if (
            f"_{token}_" in lowered
            or f"_{token}@" in lowered
            or lowered.endswith(f"_{token}")
        ):
            return token
    return None


#: Registered name heuristic (design-hardening Phase 5): an ISA token in a
#: removed symbol's name only *lowers* the removal to
#: ``cpu_dispatch_isa_dropped`` (a risk), and only when a sibling with the
#: same algorithm stem survives under another ISA.
CPU_DISPATCH_ISA = register_name_heuristic(
    "cpu_dispatch_isa",
    owner=__name__,
    effect=NameHeuristicEffect.LOWER_CONFIDENCE,
    description="an _avx512/_sse42/... token marks a per-ISA dispatch variant",
    matcher=_isa_token_in_symbol,
    vocabularies=("_ISA_TOKENS",),
)


# -------------------------------------------------------------------------
# From diff_versioning.py
# -------------------------------------------------------------------------

# Tokens that mark an ELF symbol-version node as implementation-internal rather
# than public ABI. This is a widespread upstream convention: implementation-only
# exports are bound to a version node whose name carries one of these markers —
# glibc's ``GLIBC_PRIVATE``, nettle's ``NETTLE_INTERNAL_8_1`` /
# ``HOGWEED_INTERNAL_6_1``. Symbols on such a node are dynamically exported but
# are *not* part of the public ABI contract, so changes confined to them are a
# deployment risk (a consumer who illegally linked them rebuilds), not a break.
_INTERNAL_VERSION_NODE_TOKENS = ("PRIVATE", "INTERNAL")


def is_internal_version_node(version: str) -> bool:
    """True if an ELF version-node name marks it implementation-internal/private.

    Matches the ``GLIBC_PRIVATE`` / ``*_INTERNAL_*`` convention (see
    :data:`_INTERNAL_VERSION_NODE_TOKENS`). The check is on the *version-node*
    name only — never an arbitrary symbol name — so a public function that merely
    has ``internal`` in its identifier is unaffected.
    """
    upper = (version or "").upper()
    return any(token in upper for token in _INTERNAL_VERSION_NODE_TOKENS)


#: Registered name heuristic (design-hardening Phase 5): a ``*_PRIVATE``/
#: ``*_INTERNAL`` version node only *lowers* a change confined to it to a
#: deployment risk, and only for names with no public binding.
INTERNAL_VERSION_NODE = register_name_heuristic(
    "internal_version_node",
    owner=__name__,
    effect=NameHeuristicEffect.LOWER_CONFIDENCE,
    description="a GLIBC_PRIVATE-style version node is outside the public contract",
    matcher=is_internal_version_node,
    vocabularies=("_INTERNAL_VERSION_NODE_TOKENS",),
)


# -------------------------------------------------------------------------
# From typedefs.py
# -------------------------------------------------------------------------

_VERSION_STAMPED_TYPEDEF_RE = re.compile(r"^(.*?)_version_\d+_\d+_\d+$", re.IGNORECASE)
"""Pattern for version-stamped compile-time sentinel typedefs.

Some libraries (e.g. libpng) define typedefs whose names encode the library
version, e.g. ``typedef char* png_libpng_version_1_6_46``.  The name changes
every release by design -- this is NOT a binary ABI break because the typedef
is never exported as an ELF symbol; it exists solely to produce a
compile-time error if headers from different versions are mixed.

When such a typedef disappears (``typedef_removed``), abicheck would
otherwise report BREAKING.  This guard downgrades the change to
TYPEDEF_VERSION_SENTINEL (COMPATIBLE) instead.

Moved here verbatim from ``diff_types.py`` with this cohort -- the same
pattern, not a re-derived one.
"""


def is_version_stamped_typedef(name: str) -> bool:
    """True if *name* looks like a version-stamped sentinel typedef.

    Moved here from ``diff_types.py`` with this cohort: it is typedef-family
    logic with no other caller, and leaving it behind would have meant the
    migrated detector importing back into the module it was split out of.
    """
    return bool(_VERSION_STAMPED_TYPEDEF_RE.match(name))


def _has_version_family_successor(name: str, new_aliases: frozenset[str]) -> bool:
    """True if *new_aliases* contains another version-stamped typedef with the
    same family prefix (e.g. ``png_libpng_version_``).

    Distinguishes a sentinel rotation (old version removed, new version
    added) from a genuine removal whose name merely matches the pattern.
    Takes the alias *key set* rather than the alias map, since only the keys
    were ever consulted -- the narrower input is what lets this run off the
    index's display names with no legacy dict in scope.
    """
    m = _VERSION_STAMPED_TYPEDEF_RE.match(name)
    if not m:
        return False
    prefix = m.group(1).lower()
    # Require a non-empty family prefix so an unrelated sentinel whose own
    # name starts with `_version_` (e.g. `_version_1_0_0`) doesn't match.
    if not prefix:
        return False
    prefix = prefix + "_version_"
    return any(k.lower().startswith(prefix) for k in new_aliases)


def _family_successor_added(fact_input: tuple[str, frozenset[str]]) -> bool:
    return _has_version_family_successor(*fact_input)


#: Registered name heuristic (design-hardening Phase 5): a version-stamped
#: typedef name only *lowers* its removal to ``typedef_version_sentinel``,
#: and only when the NEW side declares a successor in the same family.
TYPEDEF_VERSION_STAMP = register_name_heuristic(
    "typedef_version_stamp",
    owner=__name__,
    effect=NameHeuristicEffect.LOWER_CONFIDENCE,
    description=(
        "a *_version_N_N_N typedef is a compile-time version sentinel, not "
        "part of the binary ABI"
    ),
    matcher=is_version_stamped_typedef,
    helpers=(_has_version_family_successor,),
    confirmed_by=StructuralFact(
        "compare.typedefs.family_successor_added", _family_successor_added
    ),
    patterns=(_VERSION_STAMPED_TYPEDEF_RE,),
)


#: Registered name heuristic (design-hardening Phase 5): a shared name suffix
#: only *routes to review* -- it groups removals that are each still
#: reported into one ``symbol_renamed_batch`` finding of the same severity.
PREFIX_RENAME = register_name_heuristic(
    "prefix_rename",
    owner=__name__,
    effect=NameHeuristicEffect.ROUTE_TO_REVIEW,
    description="an added name equal to a removed one plus a prepended prefix is a rename",
    matcher=find_prefix_rename_pairs,
)


def _holder_with_inline_accessors(fact_input: tuple[object, object]) -> bool:
    holders, inline_funcs = fact_input
    return bool(holders) and bool(inline_funcs)


#: From diff_cpp_patterns. The internal namespace nominates a renamed member;
#: ``inline_body_references_renamed_member`` (BREAKING) needs a public record
#: holding a pimpl to the type *and* an inline accessor on that record.
PIMPL_RENAMED_MEMBER = register_severity_raising_heuristic(
    "pimpl_renamed_member",
    owner=__name__,
    description="a member renamed inside a detail::/impl:: type behind a public pimpl",
    matcher=is_internal_type,
    fact=StructuralFact(
        "compare.cpp_patterns.public_pimpl_holder_has_inline_accessor",
        _holder_with_inline_accessors,
    ),
)
