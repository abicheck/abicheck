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

"""Template / overload-set pattern detectors.

Generic detectors for failure modes common to template-heavy libraries
(oneDPL, Boost, the C++ standard library implementation surface, …):

* ``INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API`` — function-template
  analogue of PR #238's type leak detector. An internal helper template
  signature changed and its instantiations participate in user symbol
  mangling.

* ``CPO_KIND_CHANGED`` — a public name flipped between function and
  function-object (variable of unspecified class type). Call syntax
  preserved; ``decltype`` and trait specializations broken.

* ``OVERLOAD_SET_REROUTED`` — overload set membership shifted enough
  that existing call sites resolve to a different overload.

* ``MANDATORY_TEMPLATE_PARAM_ADDED`` — heuristic for templates whose
  effective parameter count grew (defaulted/deduced → mandatory).

* ``UNSPECIFIED_RETURN_NOW_NAMED`` — a factory function's return-type
  spelling flipped between an unspecified placeholder (``auto``,
  ``__lambda``, ``(unnamed class)``) and a stable named type.

These detectors are deliberately *signature-based* and do not require
running a compiler — they consume the standard ``AbiSnapshot`` produced
by either the header dumper or the binary dumper.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import TYPE_CHECKING

from .checker_policy import ChangeKind
from .checker_types import Change
from .diff_helpers import make_change

if TYPE_CHECKING:
    from .model import AbiSnapshot, Function

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _strip_template_args(name: str) -> str:
    """Drop everything from the first top-level ``<`` to the matching ``>``."""
    if "<" not in name:
        return name
    depth = 0
    out: list[str] = []
    for ch in name:
        if ch == "<":
            depth += 1
            continue
        if ch == ">":
            if depth > 0:
                depth -= 1
            continue
        if depth == 0:
            out.append(ch)
    return "".join(out)


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


# A Function whose demangled name contains ``<...>`` is (in Itanium /
# MSVC C++ ABI terms) an instantiated template specialisation. The
# regex requires ``<`` to be followed by a non-bracket character so that
# ``operator<`` and ``operator<<`` don't get false-flagged as
# instantiations (their ``<`` is followed by space, ``=``, or another
# ``<``, none of which match ``[^<>]``).
_TEMPLATE_ARGS_RE = re.compile(r"<[^<>]")


def _looks_like_template_instantiation(name: str) -> bool:
    """A declared C++ name is a template instantiation iff it contains a
    top-level ``<`` followed by a non-bracket character."""
    return bool(name) and bool(_TEMPLATE_ARGS_RE.search(name))


def _qualified_function_name(name: str, mangled: str) -> str:
    if "::" in name or "<" in name:
        return name
    if mangled.startswith("_Z"):
        from .demangle import demangle_batch
        return demangle_batch([mangled]).get(mangled, name)
    return name


# ---------------------------------------------------------------------------
# INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API
# ---------------------------------------------------------------------------


def _public_functions(snap: AbiSnapshot) -> list[Function]:
    """Return the subset of public functions in *snap*."""
    from .model import Visibility
    return [f for f in snap.functions if f.visibility == Visibility.PUBLIC]


def _internal_template_stems(
    funcs: list[Function],
    internal_namespaces: tuple[str, ...],
) -> set[str]:
    """Return template stems that live in one of *internal_namespaces*."""
    out: set[str] = set()
    for f in funcs:
        qname = _qualified_function_name(f.name, f.mangled)
        if not _looks_like_template_instantiation(qname):
            continue
        stem = _strip_template_args(qname)
        if _is_internal_segment(stem, internal_namespaces):
            out.add(stem)
    return out


def _functions_by_stem(funcs: list[Function]) -> dict[str, list[Function]]:
    """Group *funcs* by template-args-stripped stem."""
    out: dict[str, list[Function]] = defaultdict(list)
    for f in funcs:
        qname = _qualified_function_name(f.name, f.mangled)
        out[_strip_template_args(qname)].append(f)
    return out


def _function_signature(f: Function) -> tuple[str, int, str]:
    """Return a comparable signature tuple for *f*."""
    return (
        f.return_type,
        len(f.params),
        "|".join(p.type for p in f.params),
    )


def _instantiation_set(funcs: list[Function]) -> set[tuple[str, tuple[str, int, str]]]:
    return {
        (_qualified_function_name(f.name, f.mangled), _function_signature(f))
        for f in funcs
    }


def _leak_change(
    stem: str,
    old_sigs: set[tuple[str, tuple[str, int, str]]],
    new_sigs: set[tuple[str, tuple[str, int, str]]],
) -> Change:
    """Build an INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API finding for *stem*."""
    removed_names = sorted({n for n, _ in old_sigs - new_sigs})[:3]
    added_names = sorted({n for n, _ in new_sigs - old_sigs})[:3]
    return make_change(
        ChangeKind.INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API,
        symbol=stem,
        name=stem,
        detail=f"removed={removed_names}, added={added_names}",
        old_value=str(sorted({n for n, _ in old_sigs})[:3]),
        new_value=str(sorted({n for n, _ in new_sigs})[:3]),
    )


def detect_internal_template_leaks(
    old: AbiSnapshot,
    new: AbiSnapshot,
    internal_namespaces: tuple[str, ...] = _INTERNAL_TEMPLATE_NAMESPACES,
) -> list[Change]:
    """Report internal-namespace function templates whose instantiations changed.

    Strategy:
      1. Index OLD functions by ``(stem_without_template_args,
         param_arity_signature)``. Stem includes the internal namespace
         path so "internal" status is preserved.
      2. Match against NEW functions by the same key.
      3. For internal-namespace stems whose instantiation set changed
         (instantiations removed, added, or signature-changed), emit a
         single ``INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API`` finding per
         stem.

    The detector is intentionally per-stem rather than per-instantiation
    so a reviewer sees one finding even when 30 instantiations shift.
    """
    old_funcs = _public_functions(old)
    new_funcs = _public_functions(new)
    internal_stems = (
        _internal_template_stems(old_funcs, internal_namespaces)
        | _internal_template_stems(new_funcs, internal_namespaces)
    )
    if not internal_stems:
        return []

    old_by_stem = _functions_by_stem(old_funcs)
    new_by_stem = _functions_by_stem(new_funcs)

    changes: list[Change] = []
    for stem in sorted(internal_stems):
        old_sigs = _instantiation_set(old_by_stem.get(stem, []))
        new_sigs = _instantiation_set(new_by_stem.get(stem, []))
        if old_sigs == new_sigs:
            continue
        changes.append(_leak_change(stem, old_sigs, new_sigs))
    return changes


# ---------------------------------------------------------------------------
# CPO_KIND_CHANGED
# ---------------------------------------------------------------------------


def detect_cpo_kind_changed(
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> list[Change]:
    """Report public names that flipped between function and function-object.

    A *customization point object* (CPO) — e.g. ``std::ranges::sort`` —
    can be authored either as a free function template or as an
    ``inline constexpr`` variable of an unspecified class type. The
    two forms have the same call syntax but different ``decltype`` and
    therefore different trait specializations.

    Detection compares the set of public functions vs public variables
    by qualified leaf name. A leaf present only in functions (old) and
    only in variables (new), or vice versa, triggers the finding.
    """
    from .model import Visibility

    def _func_names(snap: AbiSnapshot) -> set[str]:
        out: set[str] = set()
        for f in snap.functions:
            if f.visibility != Visibility.PUBLIC:
                continue
            qname = _qualified_function_name(f.name, f.mangled)
            if qname:
                out.add(_strip_template_args(qname))
        return out

    def _var_names(snap: AbiSnapshot) -> set[str]:
        out: set[str] = set()
        for v in snap.variables:
            if v.visibility != Visibility.PUBLIC:
                continue
            if v.name:
                out.add(v.name)
        return out

    old_funcs = _func_names(old)
    old_vars = _var_names(old)
    new_funcs = _func_names(new)
    new_vars = _var_names(new)

    changes: list[Change] = []

    # function → variable
    for name in sorted((old_funcs - old_vars) & (new_vars - new_funcs)):
        changes.append(make_change(
            ChangeKind.CPO_KIND_CHANGED,
            symbol=name,
            name=name,
            old="function",
            new="variable (function-object / CPO)",
            new_value="variable",
        ))

    # variable → function
    for name in sorted((old_vars - old_funcs) & (new_funcs - new_vars)):
        changes.append(make_change(
            ChangeKind.CPO_KIND_CHANGED,
            symbol=name,
            name=name,
            old="variable (function-object / CPO)",
            new="function",
            old_value="variable",
        ))

    return changes


# ---------------------------------------------------------------------------
# OVERLOAD_SET_REROUTED
# ---------------------------------------------------------------------------


def detect_overload_set_rerouted(
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> list[Change]:
    """Report overload sets whose membership shifted (additions *and* removals).

    Conservative: only fires when, at the same qualified name,
    at least one overload was removed AND at least one overload was
    added. A pure removal is already caught by ``func_removed``; a pure
    addition by ``func_added``. Simultaneous removal+addition is the
    signal that existing call sites may now resolve to a different
    overload (silent re-routing).
    """
    from .model import Visibility

    def _by_stem(snap: AbiSnapshot) -> dict[str, list[Function]]:
        out: dict[str, list[Function]] = defaultdict(list)
        for f in snap.functions:
            if f.visibility != Visibility.PUBLIC:
                continue
            qname = _qualified_function_name(f.name, f.mangled)
            stem = _strip_template_args(qname)
            out[stem].append(f)
        return out

    def _overload_key(f: Function) -> tuple[object, ...]:
        # An overload is distinguished by its parameter types *and* its
        # implicit-object cv/ref qualifiers. Two member functions f(int) and
        # f(int) const are separate overloads sharing a parameter-type tuple;
        # keying on params alone would hide the removal/addition of one of them
        # in a mixed change (e.g. {f(int), f(int) const} -> {f(int), f(long)}).
        return (
            tuple(p.type for p in f.params),
            f.is_const,
            f.is_volatile,
            f.ref_qualifier,
        )

    def _fmt_key(key: tuple[object, ...]) -> str:
        params, is_const, is_volatile, ref_qual = key
        sig = "(" + ", ".join(params) + ")"  # type: ignore[arg-type]
        if is_const:
            sig += " const"
        if is_volatile:
            sig += " volatile"
        if ref_qual:
            sig += f" {ref_qual}"
        return sig

    old_by_stem = _by_stem(old)
    new_by_stem = _by_stem(new)

    changes: list[Change] = []
    for stem in sorted(set(old_by_stem) & set(new_by_stem)):
        old_sigs = {_overload_key(f) for f in old_by_stem[stem]}
        new_sigs = {_overload_key(f) for f in new_by_stem[stem]}
        removed = old_sigs - new_sigs
        added = new_sigs - old_sigs
        if not (removed and added):
            continue
        # Re-routing is only possible within a genuine overload set — i.e. at
        # least one side carries two or more overloads under the same name, so a
        # call that bound to a removed overload can silently rebind to a
        # *different* surviving/added one. A name that maps to a single function
        # on both sides (1→1) is just a signature change (already reported as
        # FUNC_PARAMS_CHANGED); C has no overloading at all, so such a name can
        # never re-route. Skip it to avoid a spurious RISK finding that
        # double-counts the same change. Count actual overloads (Function
        # entries) so cv/ref-only overloads (which share a parameter-type tuple)
        # are not under-counted.
        if len(old_by_stem[stem]) < 2 and len(new_by_stem[stem]) < 2:
            continue
        changes.append(make_change(
            ChangeKind.OVERLOAD_SET_REROUTED,
            symbol=stem,
            name=stem,
            detail=f"{len(removed)} overload(s) removed and {len(added)} added in the same revision",
            old_value=str(sorted(_fmt_key(k) for k in old_sigs)),
            new_value=str(sorted(_fmt_key(k) for k in new_sigs)),
        ))

    return changes


# ---------------------------------------------------------------------------
# MANDATORY_TEMPLATE_PARAM_ADDED
# ---------------------------------------------------------------------------


def _count_top_level_template_args(name: str) -> int | None:
    """Count the comma-separated top-level template arguments in
    ``name<...>``. Returns ``None`` if there is no top-level ``<``.

    ``Foo<int, std::pair<int, char>>`` → 2.
    """
    if "<" not in name:
        return None
    depth = 0
    args = 0
    saw_any = False
    for ch in name:
        if ch == "<":
            depth += 1
            if depth == 1:
                saw_any = True
            continue
        if ch == ">":
            if depth > 0:
                depth -= 1
            continue
        if depth == 1 and ch == ",":
            args += 1
    return args + 1 if saw_any else None


def detect_mandatory_template_param_added(
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> list[Change]:
    """Heuristic: report templates whose minimum effective arg count grew.

    Without true parameter-pack metadata we cannot perfectly distinguish
    "added defaulted param" from "added mandatory param". The heuristic:
    for each stem, take the minimum arity observed across all
    instantiations of that stem in old vs new. If the new minimum is
    strictly greater than the old minimum, emit the finding.

    Conservative — does NOT fire when the minimum stays the same (so an
    added defaulted parameter that keeps at least one ``Foo<X>``
    instantiation visible is invisible to this detector). The opposite
    miss (false positive when the library drops the smallest-arity
    instantiation) is documented as a known limitation; the symbol
    removal is also caught by ``func_removed`` so the user does see a
    finding even when this detector misses.
    """
    from .model import Visibility

    def _arities(snap: AbiSnapshot) -> dict[str, set[int]]:
        out: dict[str, set[int]] = defaultdict(set)
        for f in snap.functions:
            if f.visibility != Visibility.PUBLIC:
                continue
            qname = _qualified_function_name(f.name, f.mangled)
            if "<" not in qname:
                continue
            stem = _strip_template_args(qname)
            arity = _count_top_level_template_args(qname)
            if arity is not None:
                out[stem].add(arity)
        # Types are also indexed.
        for t in snap.types:
            if "<" not in t.name:
                continue
            stem = _strip_template_args(t.name)
            arity = _count_top_level_template_args(t.name)
            if arity is not None:
                out[stem].add(arity)
        return out

    old_ar = _arities(old)
    new_ar = _arities(new)

    changes: list[Change] = []
    for stem in sorted(set(old_ar) & set(new_ar)):
        old_min = min(old_ar[stem])
        new_min = min(new_ar[stem])
        if new_min <= old_min:
            continue
        changes.append(make_change(
            ChangeKind.MANDATORY_TEMPLATE_PARAM_ADDED,
            symbol=stem,
            name=stem,
            old=str(old_min),
            new=str(new_min),
            old_value=f"min_arity={old_min}",
            new_value=f"min_arity={new_min}",
        ))

    return changes


# ---------------------------------------------------------------------------
# UNSPECIFIED_RETURN_NOW_NAMED
# ---------------------------------------------------------------------------


# Return-type spellings we treat as "unspecified". These all share the
# property that the consumer must use ``auto`` to capture the result.
_UNSPECIFIED_RETURN_MARKERS: tuple[str, ...] = (
    "auto",
    "__lambda",
    "(unnamed",
    "(anonymous",
    "{lambda",
    "<lambda",
    "decltype(",
)


def _return_is_unspecified(rt: str) -> bool:
    if not rt:
        return False
    rt = rt.strip()
    if rt == "auto":
        return True
    return any(m in rt for m in _UNSPECIFIED_RETURN_MARKERS)


def detect_unspecified_return_now_named(
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> list[Change]:
    """Report functions whose return type flipped between unspecified and named.

    Matches public functions across snapshots by ``(qualified_name,
    param-type-tuple)``. If the return type was unspecified in one
    snapshot and named in the other, emit a finding. The two directions
    are reported with different descriptions so reviewers see whether
    they gained or lost a deduced return.
    """
    from .model import Visibility

    def _index(snap: AbiSnapshot) -> dict[tuple[str, tuple[str, ...]], str]:
        out: dict[tuple[str, tuple[str, ...]], str] = {}
        for f in snap.functions:
            if f.visibility != Visibility.PUBLIC:
                continue
            qname = _qualified_function_name(f.name, f.mangled)
            key = (qname, tuple(p.type for p in f.params))
            out[key] = f.return_type
        return out

    old_idx = _index(old)
    new_idx = _index(new)

    changes: list[Change] = []
    for key in sorted(set(old_idx) & set(new_idx)):
        old_rt = old_idx[key]
        new_rt = new_idx[key]
        old_unspec = _return_is_unspecified(old_rt)
        new_unspec = _return_is_unspecified(new_rt)
        if old_unspec == new_unspec:
            continue
        qname, _params = key
        if old_unspec:
            desc = (
                f"Function '{qname}' return type changed from unspecified "
                f"('{old_rt}') to named ('{new_rt}'). Source that captured "
                f"the result with `auto` keeps compiling; source that wrote "
                f"out the deduced type no longer matches."
            )
        else:
            desc = (
                f"Function '{qname}' return type changed from named "
                f"('{old_rt}') to unspecified ('{new_rt}'). Source that "
                f"wrote out the type no longer compiles; only `auto` "
                f"captures it now."
            )
        changes.append(make_change(
            ChangeKind.UNSPECIFIED_RETURN_NOW_NAMED,
            symbol=qname,
            description=desc,
            old_value=old_rt,
            new_value=new_rt,
        ))

    return changes


# ---------------------------------------------------------------------------
# INSTANTIATION_MISSING_FROM_BINARY (moved from diff_cpp_patterns in PR-D)
# ---------------------------------------------------------------------------


def detect_missing_instantiations(
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> list[Change]:
    """Emit ``INSTANTIATION_MISSING_FROM_BINARY`` for template-instantiation
    symbols present in *old* that vanished in *new* but whose enclosing
    template still exists.

    Generalised from the library-family detector originally added in
    PR #239; the heuristic (instantiation = function name contains
    ``<`` at top level) is library-agnostic. Re-exported from
    :mod:`abicheck.diff_cpp_patterns` for backwards compatibility.
    """
    old.index()
    new.index()
    new_mangled = {f.mangled for f in new.functions}
    findings: list[Change] = []
    surviving_stems: set[str] = set()
    for fn in new.functions:
        if _looks_like_template_instantiation(fn.name):
            surviving_stems.add(_strip_template_args(fn.name))
    for fn in old.functions:
        if fn.mangled in new_mangled:
            continue
        if not _looks_like_template_instantiation(fn.name):
            continue
        stem = _strip_template_args(fn.name)
        if stem not in surviving_stems:
            continue
        findings.append(make_change(
            ChangeKind.INSTANTIATION_MISSING_FROM_BINARY,
            symbol=fn.mangled,
            name=fn.name,
            detail=stem,
            old_value=fn.mangled,
            new_value=None,
        ))
    return findings


# ---------------------------------------------------------------------------
# Combined entry point
# ---------------------------------------------------------------------------


def detect_template_patterns(
    old: AbiSnapshot,
    new: AbiSnapshot,
    internal_namespaces: tuple[str, ...] = _INTERNAL_TEMPLATE_NAMESPACES,
) -> list[Change]:
    out: list[Change] = []
    out.extend(detect_internal_template_leaks(
        old, new, internal_namespaces=internal_namespaces,
    ))
    out.extend(detect_cpo_kind_changed(old, new))
    out.extend(detect_overload_set_rerouted(old, new))
    out.extend(detect_mandatory_template_param_added(old, new))
    out.extend(detect_unspecified_return_now_named(old, new))
    return out
