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

"""Namespace-shape pattern detectors (header-only / template library follow-up).

These detectors handle ABI/API events that are best described at the
*namespace* level rather than the symbol level. They are generic — they
apply to any C++ library that uses experimental namespaces or std
re-exports — and are not tied to a particular library.

Detectors emitted here:

* ``EXPERIMENTAL_GRADUATED`` — a name in ``experimental::`` (or
  ``preview::``, ``v0::``) is now also present at a stable name in the
  new headers while the experimental alias is kept.

* ``EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT`` — a name in
  ``experimental::`` was removed and no declaration with the same leaf
  name exists at a stable location in the new headers.

* ``STD_REEXPORT_REMOVED`` — a public function whose declaration is just
  a ``using std::X;`` re-export was deleted. Detection works on
  qualified declared names alone, no DWARF body required.

All three are deliberately *source-level* findings; they fire whether or
not the underlying mangled symbol disappears, because the consumer
break is at compile time.
"""

from __future__ import annotations

import re as _re
from typing import TYPE_CHECKING

from .checker_policy import ChangeKind
from .checker_types import Change

if TYPE_CHECKING:
    from .model import AbiSnapshot

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

# Namespace segments that mark a declaration as "not yet promised stable".
# Matched as a whole segment between ``::``; substring matches inside
# identifiers like ``ExperimentalView`` are intentionally not flagged.
DEFAULT_EXPERIMENTAL_NAMESPACES: tuple[str, ...] = (
    "experimental",
    "preview",
    "v0",
)


def _segments(qualified: str) -> list[str]:
    """Split a qualified C++ name into namespace segments.

    Template arguments are stripped before splitting so that
    ``ns::experimental::sort<int>`` → ``["ns", "experimental", "sort"]``.
    Operator names containing ``::`` (extremely rare in declared form)
    are not handled specially; this is acceptable because the detectors
    care only about the segment ordering for namespace identification.
    """
    if not qualified:
        return []
    out: list[str] = []
    depth = 0
    buf: list[str] = []
    i = 0
    n = len(qualified)
    while i < n:
        ch = qualified[i]
        if ch == "<":
            depth += 1
            i += 1
            continue
        if ch == ">":
            if depth > 0:
                depth -= 1
            i += 1
            continue
        if depth == 0 and ch == ":" and i + 1 < n and qualified[i + 1] == ":":
            if buf:
                out.append("".join(buf).strip())
                buf = []
            i += 2
            continue
        if depth == 0:
            buf.append(ch)
        i += 1
    if buf:
        out.append("".join(buf).strip())
    return [s for s in out if s]


def _strip_experimental(
    qualified: str,
    experimental_namespaces: tuple[str, ...] = DEFAULT_EXPERIMENTAL_NAMESPACES,
) -> tuple[str, str | None]:
    """Return ``(stable_name, matched_segment)``.

    If any segment of ``qualified`` is an experimental namespace, that
    single segment is removed and the rest is rejoined. The first
    matching segment is returned so callers can name it in the
    description. When no experimental segment is present, returns
    ``(qualified, None)`` unchanged.

    Removes only the first matched segment to keep the transformation
    invertible for nested ``experimental::ranges::`` cases — callers can
    re-run the helper to peel additional layers if needed.
    """
    segs = _segments(qualified)
    for i, s in enumerate(segs):
        if s in experimental_namespaces:
            return "::".join(segs[:i] + segs[i + 1:]), s
    return qualified, None


def _qualified_function_name(
    name: str, mangled: str, demangled: dict[str, str] | None = None
) -> str:
    """Return the best-effort qualified declaration name for a function.

    Header-derived snapshots populate ``Function.name`` with the
    qualified declaration name (``acme::lib::sort``). ELF-only mode
    leaves ``Function.name`` set to the mangled string; in that case we
    fall back to demangling of the mangled name.

    When iterating all functions of a snapshot, pass a *demangled* map
    (from :func:`_batch_demangle_public`) so the whole snapshot is demangled in
    a single batched ``c++filt`` call instead of one subprocess per symbol —
    the per-symbol path is what makes namespace detection explode on large
    stripped libraries. The lazy single-symbol fallback is kept for callers
    that have no batch (and is itself memoised in ``demangle_batch``).
    """
    if "::" in name or "<" in name:
        return name
    if mangled.startswith("_Z"):
        if demangled is not None:
            return demangled.get(mangled, name)
        from .demangle import demangle_batch
        return demangle_batch([mangled]).get(mangled, name)
    return name


# ---------------------------------------------------------------------------
# Detector: experimental → stable graduation / removal
# ---------------------------------------------------------------------------


def _split_experimental(
    qnames: list[str],
    experimental_namespaces: tuple[str, ...],
) -> tuple[list[str], list[str]]:
    """Split *qnames* into ``(experimental, stable)`` by namespace match."""
    exp = [
        q for q in qnames
        if any(s in experimental_namespaces for s in _segments(q))
    ]
    stable = [q for q in qnames if q not in exp]
    return exp, stable


def _index_funcs_by_stable_key(
    snap: AbiSnapshot,
    experimental_namespaces: tuple[str, ...],
) -> dict[tuple[str, str], list[str]]:
    """Index public functions by ``(stripped_qualified_name, leaf)``.

    Only public functions are indexed so internal helpers in
    ``experimental::`` don't get reported.
    """
    from .model import Visibility
    demangled = _batch_demangle_public(snap)
    out: dict[tuple[str, str], list[str]] = {}
    for f in snap.functions:
        if f.visibility != Visibility.PUBLIC:
            continue
        qname = _qualified_function_name(f.name, f.mangled, demangled)
        segs = _segments(qname)
        if not segs:
            continue
        leaf = segs[-1]
        stripped, _ = _strip_experimental(qname, experimental_namespaces)
        out.setdefault((stripped, leaf), []).append(qname)
    return out


def _index_types_by_stable_key(
    snap: AbiSnapshot,
    experimental_namespaces: tuple[str, ...],
) -> dict[tuple[str, str], list[str]]:
    """Index types by ``(stripped_qualified_name, leaf)``."""
    out: dict[tuple[str, str], list[str]] = {}
    for t in snap.types:
        qname = t.name
        segs = _segments(qname)
        if not segs:
            continue
        leaf = segs[-1]
        stripped, _ = _strip_experimental(qname, experimental_namespaces)
        out.setdefault((stripped, leaf), []).append(qname)
    return out


def _classify_experimental_event(
    old_exp: list[str],
    old_stable: list[str],
    new_exp: list[str],
    new_stable: list[str],
) -> str | None:
    """Return ``"graduated"``, ``"removed"``, or ``None`` for a key pair.

    Graduation requires an experimental presence in old AND a new stable
    twin that did not exist before. Removal requires no replacement on
    either side. Everything else is silent.
    """
    if not old_exp:
        return None
    if new_exp and new_stable and not old_stable:
        return "graduated"
    if not new_exp and not new_stable and not old_stable:
        return "removed"
    return None


def _emit_experimental_change(
    event: str,
    leaf: str,
    old_exp: list[str],
    new_stable: list[str],
    kind_label: str,
) -> Change:
    """Build the ``Change`` record for one classified event."""
    old_q = old_exp[0]
    if event == "graduated":
        new_q = new_stable[0]
        return Change(
            kind=ChangeKind.EXPERIMENTAL_GRADUATED,
            symbol=new_q,
            description=(
                f"Experimental {kind_label} '{old_q}' graduated to stable "
                f"name '{new_q}'; experimental alias retained."
            ),
            old_value=old_q,
            new_value=new_q,
        )
    return Change(
        kind=ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT,
        symbol=old_q,
        description=(
            f"Experimental {kind_label} '{old_q}' was removed and no "
            f"{kind_label} with leaf '{leaf}' was published at a stable "
            f"namespace in the new headers."
        ),
        old_value=old_q,
        new_value=None,
    )


def _findings_for(
    old_index: dict[tuple[str, str], list[str]],
    new_index: dict[tuple[str, str], list[str]],
    experimental_namespaces: tuple[str, ...],
    kind_label: str,
) -> list[Change]:
    """Walk old/new indices, emitting one finding per classified event."""
    out: list[Change] = []
    for (stable_key, leaf), qnames in old_index.items():
        old_exp, old_stable = _split_experimental(qnames, experimental_namespaces)
        if not old_exp:
            continue
        new_qnames = new_index.get((stable_key, leaf), [])
        new_exp, new_stable = _split_experimental(
            new_qnames, experimental_namespaces,
        )
        event = _classify_experimental_event(
            old_exp, old_stable, new_exp, new_stable,
        )
        if event is None:
            continue
        out.append(_emit_experimental_change(
            event, leaf, old_exp, new_stable, kind_label,
        ))
    return out


def detect_experimental_namespace_changes(
    old: AbiSnapshot,
    new: AbiSnapshot,
    experimental_namespaces: tuple[str, ...] = DEFAULT_EXPERIMENTAL_NAMESPACES,
) -> list[Change]:
    """Report graduations and silent removals from experimental namespaces.

    For every public declaration in ``old`` whose qualified name contains
    an experimental segment, look up the corresponding ``leaf``-named
    declaration in ``new``:

    * If the experimental name is still present *and* a stable-namespace
      twin now exists → ``EXPERIMENTAL_GRADUATED`` (compatible).
    * If the experimental name is gone and no stable twin exists →
      ``EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT`` (API break).

    Functions and types are handled independently; a graduated *type*
    and graduated *function* with the same leaf are reported as two
    separate findings (they really are two separate API events).

    No finding is emitted when the experimental name is gone but a
    stable twin exists *and* the stable twin already existed in
    ``old`` — that's just deletion of a redundant alias, not a
    graduation event.
    """
    out: list[Change] = []
    out.extend(_findings_for(
        _index_funcs_by_stable_key(old, experimental_namespaces),
        _index_funcs_by_stable_key(new, experimental_namespaces),
        experimental_namespaces,
        "declaration",
    ))
    out.extend(_findings_for(
        _index_types_by_stable_key(old, experimental_namespaces),
        _index_types_by_stable_key(new, experimental_namespaces),
        experimental_namespaces,
        "type",
    ))
    return out


# ---------------------------------------------------------------------------
# Detector: std re-export removed
# ---------------------------------------------------------------------------

# Heuristic: a function whose declared qualified name resolves to a
# library namespace AND whose mangled name resolves to a name in
# ``std::`` is a re-export (the library names it via ``using std::X``).
#
# Concrete forms we accept:
#   - Function.name == "lib::ns::par"         (declared in library headers)
#   - Function.mangled demangles to a name beginning with "std::"
#     (the underlying definition belongs to the standard library).
#
# We DO NOT use libstdc++/libc++ internal-namespace heuristics here —
# false positives on real library functions would be worse than missing
# the occasional re-export. The detector therefore requires both halves
# of the signal to fire.

_STD_PREFIX = "std::"


def _looks_like_std_reexport(
    declared_qualified: str,
    underlying_qualified: str,
) -> bool:
    """Return True when declared_qualified is a non-std alias for underlying_qualified.

    Both names must be fully qualified. The underlying name must live in
    ``std::``; the declared name must live somewhere else (any library
    namespace). Identical names — i.e. the function genuinely lives in
    ``std::`` — are not re-exports.
    """
    if not declared_qualified or not underlying_qualified:
        return False
    declared_segs = _segments(declared_qualified)
    underlying_segs = _segments(underlying_qualified)
    if not declared_segs or not underlying_segs:
        return False
    # Declared must NOT be in std::; underlying MUST be in std::.
    if declared_segs[0] == "std":
        return False
    if underlying_segs[0] != "std":
        return False
    # Same leaf name on both sides — a using-declaration preserves the leaf.
    return declared_segs[-1] == underlying_segs[-1]


def _collect_public_declared_names(snap: AbiSnapshot) -> set[str]:
    """Return the set of qualified declared names of public functions in *snap*."""
    from .model import Visibility
    demangled = _batch_demangle_public(snap)
    out: set[str] = set()
    for f in snap.functions:
        if f.visibility != Visibility.PUBLIC:
            continue
        qname = _qualified_function_name(f.name, f.mangled, demangled)
        if qname:
            out.add(qname)
    return out


def _batch_demangle_public(snap: AbiSnapshot) -> dict[str, str]:
    """Demangle every public mangled name in *snap* in one batch call."""
    from .demangle import demangle_batch
    from .model import Visibility
    mangled = [
        f.mangled for f in snap.functions
        if f.mangled.startswith("_Z") and f.visibility == Visibility.PUBLIC
    ]
    return demangle_batch(mangled) if mangled else {}


def _build_std_reexport_change(declared: str, underlying: str) -> Change:
    """Build a single ``STD_REEXPORT_REMOVED`` finding."""
    return Change(
        kind=ChangeKind.STD_REEXPORT_REMOVED,
        symbol=declared,
        description=(
            f"Public re-export '{declared}' of standard-library entity "
            f"'{underlying}' was removed. Consumer code that named "
            f"'{declared}' no longer compiles; '{underlying}' is "
            f"still available under its std:: name."
        ),
        old_value=f"{declared} → {underlying}",
        new_value=None,
    )


def detect_std_reexport_removed(
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> list[Change]:
    """Report ``using std::X;`` re-exports that disappeared from public headers.

    A re-export is detected when the OLD snapshot has a public function
    whose declared qualified name lives in a library namespace but whose
    mangled name demangles to ``std::``. If the same declared qualified
    name is absent from the NEW snapshot's function set, we emit one
    ``STD_REEXPORT_REMOVED`` per missing declaration.

    The detector is intentionally narrow — it never fires when the
    declared name and the underlying name are identical, when the
    declared name is in ``std::``, or when the mangled name does not
    demangle to ``std::``.
    """
    from .model import Visibility

    demangled = _batch_demangle_public(old)
    new_declared = _collect_public_declared_names(new)

    changes: list[Change] = []
    seen: set[str] = set()
    for f in old.functions:
        if f.visibility != Visibility.PUBLIC:
            continue
        declared = _qualified_function_name(f.name, f.mangled, demangled)
        if not declared or declared in seen or declared in new_declared:
            continue
        underlying = demangled.get(f.mangled, "")
        if not _looks_like_std_reexport(declared, underlying):
            continue
        seen.add(declared)
        changes.append(_build_std_reexport_change(declared, underlying))

    return changes


# ---------------------------------------------------------------------------
# Detector: versioned inline namespace bumped (header-declared)
# ---------------------------------------------------------------------------

# Matches segment-name shapes commonly used as a versioned inline
# namespace: ``_V1``, ``__v2``, ``v3``, ``__1``. Anchored to whole
# segment match (caller passes a single segment string). Captures the
# integer suffix for ordering checks.
_VERSION_NS_RE = _re.compile(r"^_{0,2}[Vv]?(\d+)$")


def _version_suffix(segment: str) -> int | None:
    """Return the integer suffix if ``segment`` looks like a versioned
    inline namespace tag (``_V1``, ``__1``, ``v2``, …); else ``None``.
    """
    m = _VERSION_NS_RE.match(segment)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _version_strip_segments(segs: list[str]) -> tuple[tuple[str, ...], int | None]:
    """Strip any one versioned-namespace segment and return
    ``(stripped_segments, version_int)``.

    Returns ``(tuple(segs), None)`` unchanged when no versioned segment
    is present. Only the first matching segment is stripped — nested
    versioned namespaces are vanishingly rare in practice and the simple
    rule keeps the matching key stable.
    """
    for i, s in enumerate(segs):
        v = _version_suffix(s)
        if v is not None:
            return tuple(segs[:i] + segs[i + 1:]), v
    return tuple(segs), None


def detect_inline_namespace_version_bump(
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> list[Change]:
    """Detect declarations whose versioned inline-namespace segment shifted.

    Complementary to the existing symbol-level ``INLINE_NAMESPACE_MOVED``
    detector (``diff_platform._diff_inline_namespace``): that one needs
    ≥2 mangled-symbol moves and works only on built shared libraries;
    this one fires from declared qualified names so it works for header-
    only / template-library snapshots and on a single declaration.

    The detector matches old and new declarations by the *version-
    stripped* qualified name. If both sides have versioned segments AND
    the integer suffix changed, emit one finding per moved declaration.
    """
    old_idx = _index_versioned(_collect_versioned_entries(old))
    new_idx = _index_versioned(_collect_versioned_entries(new))
    return _emit_version_bumps(old_idx, new_idx)


def _index_versioned(
    items: list[tuple[str, str]],
) -> dict[tuple[str, ...], list[tuple[str, int, str]]]:
    """Map version-stripped segments → list of ``(qualified, version_int, kind)``."""
    out: dict[tuple[str, ...], list[tuple[str, int, str]]] = {}
    for qname, kind in items:
        segs = _segments(qname)
        stripped, ver = _version_strip_segments(segs)
        if ver is None:
            continue
        out.setdefault(stripped, []).append((qname, ver, kind))
    return out


def _collect_versioned_entries(snap: AbiSnapshot) -> list[tuple[str, str]]:
    """Return ``[(qualified_name, "function"|"type"), …]`` for *snap*."""
    from .model import Visibility
    demangled = _batch_demangle_public(snap)
    items: list[tuple[str, str]] = []
    for f in snap.functions:
        if f.visibility != Visibility.PUBLIC:
            continue
        qname = _qualified_function_name(f.name, f.mangled, demangled)
        if qname:
            items.append((qname, "function"))
    for t in snap.types:
        if t.name:
            items.append((t.name, "type"))
    return items


def _emit_version_bumps(
    old_idx: dict[tuple[str, ...], list[tuple[str, int, str]]],
    new_idx: dict[tuple[str, ...], list[tuple[str, int, str]]],
) -> list[Change]:
    changes: list[Change] = []
    for stripped, old_list in old_idx.items():
        new_list = new_idx.get(stripped, [])
        if not new_list:
            continue
        old_versions = {v for _, v, _ in old_list}
        new_versions = {v for _, v, _ in new_list}
        if old_versions == new_versions:
            continue
        if max(new_versions) <= max(old_versions):
            continue
        old_q = old_list[0][0]
        new_q = new_list[0][0]
        changes.append(Change(
            kind=ChangeKind.INLINE_NAMESPACE_VERSION_BUMPED,
            symbol=new_q,
            description=(
                f"Inline namespace version bumped: '{old_q}' → '{new_q}' "
                f"(version segment changed from {sorted(old_versions)} to "
                f"{sorted(new_versions)}); mangled names change so old "
                f"and new TUs of the same program ODR-violate."
            ),
            old_value=old_q,
            new_value=new_q,
        ))
    return changes


# ---------------------------------------------------------------------------
# Combined entry point used by the post-processing pipeline.
# ---------------------------------------------------------------------------


def detect_namespace_patterns(
    old: AbiSnapshot,
    new: AbiSnapshot,
    experimental_namespaces: tuple[str, ...] = DEFAULT_EXPERIMENTAL_NAMESPACES,
) -> list[Change]:
    """Run all namespace-shape detectors and return their concatenated findings."""
    out: list[Change] = []
    out.extend(detect_experimental_namespace_changes(
        old, new, experimental_namespaces=experimental_namespaces,
    ))
    out.extend(detect_std_reexport_removed(old, new))
    out.extend(detect_inline_namespace_version_bump(old, new))
    return out
