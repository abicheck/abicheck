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

"""Idiom & anti-pattern recognition over a :class:`SurfaceGraph` (ADR-025 A2).

Pure, deterministic recognisers that map a declaration to an :class:`IdiomTag`
carrying the idiom, a confidence, the *evidence* that matched, and the
idiom-specific proof fields the A4 anti-hiding guards need
(``layout_signature``, ``hidden_pointee``, ``definition_hidden``).

Recognition is computed on demand from the (already-persisted) declaration
graph, so the evidence the guards rely on is always available — there is no
lossy "tag-name only" persistence (ADR-025 D2.4 concern).

Crucially, ``OPAQUE_POINTER`` requires the type's *definition* to be hidden
from callers — incomplete in the public include closure — not merely that the
exported functions happen to pass it by pointer (ADR-025 D2.1). A complete
public type with private fields is observable (``sizeof``) and is **not**
opaque.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .checker_policy import ChangeKind, Confidence
from .model import ParamKind, RecordType, ScopeOrigin, Visibility
from .surface_graph import SurfaceGraph

# Provenance origins that are NOT part of the public ABI surface — a type defined
# only in one of these is the library's private implementation (ADR-024/027).
_NON_PUBLIC_ORIGINS = frozenset(
    {ScopeOrigin.PRIVATE_HEADER, ScopeOrigin.SYSTEM_HEADER, ScopeOrigin.GENERATED}
)


class Idiom(str, Enum):
    """API idioms recognised from the declaration graph (ADR-025 D2.1)."""

    OPAQUE_POINTER = "opaque_pointer"  # definition hidden; only ever crossed by pointer
    PIMPL = "pimpl"  # complete wrapper holding one pointer to a hidden impl
    HANDLE = "handle"  # typedef of void* / fwd-declared struct ptr used as a token
    FACTORY = "factory"  # exported fn returning a pointer to a polymorphic/base type
    CREATE_DESTROY = "create_destroy"  # paired create_X / destroy_X lifecycle fns
    CALLBACK_ABI = "callback_abi"  # function-pointer-typed parameter (ABI-sensitive)


@dataclass(frozen=True)
class IdiomTag:
    """A recognised idiom plus the evidence that justifies it.

    ``evidence`` lists the matched edges/reasons (→ the A4 ledger's
    ``edges_matched``). The proof fields support the A4 both-snapshots guards:

    - ``definition_hidden`` — the type is incomplete in the public include
      closure (OPAQUE_POINTER load-bearing condition, D2.1).
    - ``layout_signature`` — the PIMPL/opaque wrapper's *own* layout, so a
      change to the wrapper itself is never demoted (D4.1 PIMPL guard).
    - ``hidden_pointee`` — identity of the PIMPL impl pointee.
    """

    idiom: Idiom
    confidence: Confidence
    evidence: list[str] = field(default_factory=list)
    layout_signature: str | None = None
    hidden_pointee: str | None = None
    definition_hidden: bool = False


_POINTER_RE = re.compile(r"[*&]")
_FUNC_PTR_RE = re.compile(r"\(\s*\*\s*\)|\(\s*\*[^)]*\)\s*\(")


def _strip_ptr(type_str: str) -> str:
    """Drop pointer/reference/cv tokens, yielding the pointee type name."""
    s = _POINTER_RE.sub("", type_str)
    for kw in ("const", "volatile", "struct", "class", "union", "enum"):
        s = re.sub(rf"\b{kw}\b", "", s)
    return s.strip()


def _is_pointer(type_str: str) -> bool:
    return "*" in type_str


# Primitive/builtin type words; a pointer to one of these is not an opaque
# handle token. ``_strip_ptr`` already removes struct/class/union/cv keywords,
# so the pointee is just the core type spelling here.
_PRIMITIVE_WORDS = frozenset(
    {
        "void",
        "bool",
        "char",
        "wchar_t",
        "char8_t",
        "char16_t",
        "char32_t",
        "short",
        "int",
        "long",
        "float",
        "double",
        "signed",
        "unsigned",
        "_Bool",
        "__int128",
    }
)
_STDINT_RE = re.compile(
    r"^(u?int(_least|_fast)?(8|16|32|64|max|ptr)_t|size_t|ssize_t|ptrdiff_t)$"
)


def _is_builtin_type(pointee: str) -> bool:
    """True when *pointee* names a builtin/primitive (not an opaque user type).

    A typedef like ``int *`` / ``int32_t *`` is a pointer to a primitive, not an
    opaque handle token — so it must not be tagged HANDLE (ADR-027 review).
    """
    name = pointee.strip()
    if not name:
        return False
    if _STDINT_RE.match(name):
        return True
    words = name.split()
    return bool(words) and all(w in _PRIMITIVE_WORDS for w in words)


def _record_is_incomplete(rec: RecordType | None) -> bool:
    """True when *rec* is unknown or an incomplete (forward-declared) type.

    A type the parser never completed in the public translation unit is hidden
    from callers — they cannot ``sizeof`` or embed it (ADR-025 D2.1 cond. 1).
    An unknown name (no record at all) is likewise not observable here.
    """
    return rec is None or rec.is_opaque


def _layout_signature(rec: RecordType) -> str:
    """A stable string capturing the record's *own* observable layout."""
    fields = ";".join(f"{f.name}:{f.type}@{f.offset_bits}" for f in rec.fields)
    return f"size={rec.size_bits};align={rec.alignment_bits};fields={fields}"


def _public_pointer_only(graph: SurfaceGraph, type_name: str) -> tuple[bool, bool]:
    """Return (referenced_by_public, only_ever_by_pointer) for *type_name*.

    Walks every public function: if it names *type_name* by value in a
    parameter or return position, the type is observable by value.
    """
    referenced = False
    only_pointer = True
    short = type_name.rsplit("::", 1)[-1]
    for fn in graph.snapshot.functions:
        # Use the function's own visibility, not demangled-name membership in
        # public_roots(): a *hidden* C++ overload sharing a public overload's
        # name must not contribute its by-value parameter as "public" evidence.
        if fn.visibility != Visibility.PUBLIC:
            continue
        sites: list[tuple[str, int]] = [(fn.return_type, fn.return_pointer_depth)]
        for p in fn.params:
            sites.append((getattr(p, "type", "") or "", getattr(p, "pointer_depth", 0)))
        for type_str, depth in sites:
            names = {type_str.rsplit("::", 1)[-1]} | set(_strip_ptr(type_str).split())
            if short in names or type_name in (type_str, _strip_ptr(type_str)):
                referenced = True
                if depth < 1 and not _is_pointer(type_str):
                    only_pointer = False
    return referenced, only_pointer


def _recognise_opaque(graph: SurfaceGraph, rec: RecordType) -> IdiomTag | None:
    referenced, only_pointer = _public_pointer_only(graph, rec.name)
    if not referenced or not only_pointer:
        return None
    # Load-bearing: the definition must be hidden in the public include closure.
    if not rec.is_opaque:
        return None
    if any(f.access.name == "PUBLIC" for f in rec.fields):
        return None
    return IdiomTag(
        idiom=Idiom.OPAQUE_POINTER,
        confidence=Confidence.HIGH,
        evidence=[
            f"{rec.name} is incomplete in the public surface",
            f"{rec.name} crossed only by pointer in public API",
        ],
        definition_hidden=True,
    )


def _recognise_pimpl(graph: SurfaceGraph, rec: RecordType) -> IdiomTag | None:
    # A complete wrapper with exactly one data member, a pointer to a hidden impl.
    if rec.is_opaque or len(rec.fields) != 1:
        return None
    field0 = rec.fields[0]
    if not _is_pointer(field0.type):
        return None
    pointee_name = _strip_ptr(field0.type)
    pointee = graph.types_by_name.get(pointee_name) or graph.types_by_name.get(
        pointee_name.rsplit("::", 1)[-1]
    )
    if not _record_is_incomplete(pointee):
        return None
    return IdiomTag(
        idiom=Idiom.PIMPL,
        confidence=Confidence.HIGH,
        evidence=[f"{rec.name} holds a single pointer to hidden impl {pointee_name}"],
        layout_signature=_layout_signature(rec),
        hidden_pointee=pointee_name,
    )


def _resolve_pointee(
    graph: SurfaceGraph, pointee: str
) -> tuple[RecordType | None, bool]:
    """Resolve a pointee type name to a record, flagging short-name ambiguity.

    Exact qualified identity wins. Otherwise the unqualified short name binds
    only when exactly one snapshot type carries it; if several types share the
    short name (e.g. ``ns1::Ctx`` + ``ns2::Ctx``), the reference is ambiguous —
    return ``(None, True)`` so callers never bind to whichever record
    ``types_by_name`` happened to store first (a typedef ``Ctx *`` might point
    at the complete ``ns2::Ctx``, not the incomplete ``ns1::Ctx`` — ADR-027
    review). A name no snapshot type carries is unknown / forward-declared:
    ``(None, False)``.
    """
    exact = graph.types_by_name.get(pointee)
    if exact is not None and exact.name == pointee:
        return exact, False
    short = pointee.rsplit("::", 1)[-1]
    matches = [r for r in graph.snapshot.types if r.name.rsplit("::", 1)[-1] == short]
    if len(matches) == 1:
        return matches[0], False
    return (None, True) if matches else (None, False)


def _recognise_handle(graph: SurfaceGraph) -> dict[str, IdiomTag]:
    out: dict[str, IdiomTag] = {}
    for alias, target in sorted(graph.snapshot.typedefs.items()):
        t = target.strip()
        if not _is_pointer(t):
            continue
        pointee = _strip_ptr(t)
        # void* token, or a pointer to a forward-declared / unknown struct.
        # Resolve by qualified identity; an ambiguous short name does not bind.
        rec, ambiguous = _resolve_pointee(graph, pointee)
        # A handle is an opaque *token*: void*, or a pointer to a type that is
        # genuinely incomplete (a known opaque record, or an unknown user type —
        # a forward-declared struct). A pointer to a builtin/primitive (e.g.
        # ``int *``, ``int32_t *``) is NOT an opaque handle, so excluding it
        # avoids a false ``handle_type_changed`` when its pointee width changes
        # (ADR-027 review). A *known but complete* record is likewise not a token,
        # and an ambiguous short-name pointee is not provably opaque so it is not
        # tagged (it may resolve to a complete type — ADR-027 review).
        is_token = pointee in ("void", "") or (
            not ambiguous
            and _record_is_incomplete(rec)
            and not _is_builtin_type(pointee)
        )
        if is_token:
            out[alias] = IdiomTag(
                idiom=Idiom.HANDLE,
                confidence=Confidence.MEDIUM if pointee != "void" else Confidence.HIGH,
                evidence=[f"typedef {alias} = {target} (opaque token)"],
                hidden_pointee=pointee or None,
                definition_hidden=True,
            )
    return out


def _recognise_factory(graph: SurfaceGraph) -> dict[str, IdiomTag]:
    out: dict[str, IdiomTag] = {}
    for fn in graph.snapshot.functions:
        if fn.visibility != Visibility.PUBLIC:
            continue
        if fn.return_pointer_depth < 1 and not _is_pointer(fn.return_type):
            continue
        pointee = _strip_ptr(fn.return_type)
        rec = graph.types_by_name.get(pointee) or graph.types_by_name.get(
            pointee.rsplit("::", 1)[-1]
        )
        if rec is not None and rec.vtable:
            out[fn.name] = IdiomTag(
                idiom=Idiom.FACTORY,
                confidence=Confidence.MEDIUM,
                evidence=[
                    f"{fn.name} returns {fn.return_type}* to polymorphic {pointee}"
                ],
                hidden_pointee=pointee,
            )
    return out


_CREATE_RE = re.compile(
    r"^(create|new|make|alloc)_?(?P<base>.+)$|^(?P<base2>.+?)_(new|create|alloc)$"
)
_DESTROY_RE = re.compile(
    r"^(destroy|free|delete|release)_?(?P<base>.+)$|^(?P<base2>.+?)_(free|destroy|delete|release)$"
)


def _lifecycle_base(name: str, pattern: re.Pattern[str]) -> str | None:
    m = pattern.match(name)
    if not m:
        return None
    return (m.group("base") or m.group("base2") or "").strip("_").lower() or None


def _recognise_create_destroy(graph: SurfaceGraph) -> dict[str, IdiomTag]:
    creators: dict[str, str] = {}
    destroyers: dict[str, str] = {}
    for name in graph.public_roots():
        base = _lifecycle_base(name, _CREATE_RE)
        if base:
            creators.setdefault(base, name)
        base = _lifecycle_base(name, _DESTROY_RE)
        if base:
            destroyers.setdefault(base, name)
    out: dict[str, IdiomTag] = {}
    for base, cname in creators.items():
        dname = destroyers.get(base)
        if dname:
            for who, partner in ((cname, dname), (dname, cname)):
                out[who] = IdiomTag(
                    idiom=Idiom.CREATE_DESTROY,
                    confidence=Confidence.MEDIUM,
                    evidence=[f"lifecycle pair: {cname} / {dname}"],
                    hidden_pointee=partner,
                )
    return out


def _is_callback_type(type_str: str, typedefs: dict[str, str]) -> bool:
    """True when *type_str* is (or resolves to) a function-pointer parameter.

    Handles three encodings:
    - C declarator text ``void (*)(int)`` (hand-written / non-castxml dumpers),
    - castxml's ``FunctionType*`` rendering (``dumper_castxml._type_name`` emits
      the bare ``FunctionType`` tag for an unnamed function type behind a
      pointer), and
    - a typedef'd callback, where the parameter names an alias whose target is
      itself a function pointer (resolved through the typedef map).
    """
    if _FUNC_PTR_RE.search(type_str) or "FunctionType" in type_str:
        return True
    base = _strip_ptr(type_str)
    seen: set[str] = set()
    while base in typedefs and base not in seen:
        seen.add(base)
        target = typedefs[base]
        if _FUNC_PTR_RE.search(target) or "FunctionType" in target:
            return True
        base = _strip_ptr(target)
    return False


def _recognise_callbacks(graph: SurfaceGraph) -> dict[str, IdiomTag]:
    out: dict[str, IdiomTag] = {}
    typedefs = graph.snapshot.typedefs
    for fn in graph.snapshot.functions:
        if fn.visibility != Visibility.PUBLIC:
            continue
        for p in fn.params:
            ptype = getattr(p, "type", "") or ""
            if _is_callback_type(ptype, typedefs):
                out.setdefault(
                    fn.name,
                    IdiomTag(
                        idiom=Idiom.CALLBACK_ABI,
                        confidence=Confidence.HIGH,
                        evidence=[
                            f"{fn.name} takes function-pointer parameter '{ptype}'"
                        ],
                    ),
                )
    return out


@dataclass(frozen=True)
class AntiPattern:
    """A single-snapshot anti-pattern finding (ADR-027 D2.2).

    ``kind`` is the :class:`ChangeKind` the finding maps to; ``evidence`` lists
    the graph edges that matched (for ``surface-report`` and the A4 ledger).
    """

    symbol: str
    kind: ChangeKind
    description: str
    evidence: list[str] = field(default_factory=list)


def _is_std_by_value(type_str: str, pointer_depth: int, kind: ParamKind) -> bool:
    """True when *type_str* names a ``std::`` type crossed **by value**.

    A pointer/reference to a ``std::`` type is fine (only the pointer crosses
    the boundary); it is passing/returning the container itself that is fragile.
    """
    if "std::" not in type_str:
        return False
    if pointer_depth >= 1 or _is_pointer(type_str) or "&" in type_str:
        return False
    if kind in (ParamKind.POINTER, ParamKind.REFERENCE, ParamKind.RVALUE_REF):
        return False
    return True


def _has_virtual_destructor(rec: RecordType) -> bool:
    """Heuristic: does *rec*'s vtable carry a destructor slot?

    Itanium mangles destructors with ``D0``/``D1``/``D2`` suffixes; MSVC uses
    ``??1`` / ``vector deleting destructor``. A polymorphic type whose vtable
    has no destructor slot has a non-virtual destructor — deleting through a
    base pointer is UB.
    """
    for entry in rec.vtable:
        if re.search(r"D[012]\b|D[012]Ev|\?\?1|deleting destructor", entry):
            return True
    return False


def _detect_stl_by_value(graph: SurfaceGraph) -> list[AntiPattern]:
    """Collect PUBLIC_API_EXPOSES_STL_BY_VALUE findings for every public function."""
    found: list[AntiPattern] = []
    for fn in graph.snapshot.functions:
        if fn.visibility != Visibility.PUBLIC:
            continue
        hits: list[str] = []
        if _is_std_by_value(fn.return_type, fn.return_pointer_depth, ParamKind.VALUE):
            hits.append(f"returns {fn.return_type} by value")
        for p in fn.params:
            ptype = getattr(p, "type", "") or ""
            if _is_std_by_value(ptype, getattr(p, "pointer_depth", 0), p.kind):
                hits.append(f"parameter {p.name!r} is {ptype} by value")
        if hits:
            found.append(
                AntiPattern(
                    symbol=fn.name,
                    kind=ChangeKind.PUBLIC_API_EXPOSES_STL_BY_VALUE,
                    description=(
                        f"public function {fn.name} crosses the ABI boundary with "
                        f"a std:: type by value ({'; '.join(hits)})"
                    ),
                    evidence=hits,
                )
            )
    return found


def _build_type_name_index(
    graph: SurfaceGraph,
) -> tuple[set[str], dict[str, list[str]]]:
    """Return (all_type_names, by_short) for unambiguous type-name resolution.

    Resolve every base/factory target spelling to a *specific* snapshot type
    (exact qualified name, or short name only when unambiguous), so a factory
    returning ns1::Base* never tags an unrelated ns2::Base that merely shares a
    short name (ADR-027 review). Unresolvable / ambiguous targets are dropped.
    """
    all_type_names = {rec.name for rec in graph.snapshot.types}
    by_short: dict[str, list[str]] = {}
    for n in all_type_names:
        by_short.setdefault(n.rsplit("::", 1)[-1], []).append(n)
    return all_type_names, by_short


def _resolve_type_name(
    spelling: str,
    all_type_names: set[str],
    by_short: dict[str, list[str]],
) -> str | None:
    """Return the unique qualified name for *spelling*, or ``None`` if ambiguous/unknown."""
    if spelling in all_type_names:
        return spelling
    cands = by_short.get(spelling.rsplit("::", 1)[-1], [])
    return cands[0] if len(cands) == 1 else None


def _collect_base_targets(
    graph: SurfaceGraph,
    all_type_names: set[str],
    by_short: dict[str, list[str]],
) -> set[str]:
    """Gather fully-qualified names of types used as a base by public-surface records.

    Only count a base when the *deriving* type is on the public surface
    (ADR-027 review). A polymorphic Base inherited solely by a private /
    system / generated record is not a public ABI risk; counting it would
    also let that private-inheritance evidence pre-exist in old and wrongly
    suppress a *newly introduced* public factory risk for the same Base in
    _emit_new_antipatterns(). When no public-header set was supplied every
    record is UNKNOWN, which is treated as in-surface (no behaviour change).
    """
    base_targets: set[str] = set()
    for rec in graph.snapshot.types:
        if rec.origin in _NON_PUBLIC_ORIGINS:
            continue
        for b in rec.bases:
            resolved = _resolve_type_name(b, all_type_names, by_short)
            if resolved is not None:
                base_targets.add(resolved)
    return base_targets


def _collect_factory_targets(
    graph: SurfaceGraph,
    all_type_names: set[str],
    by_short: dict[str, list[str]],
) -> set[str]:
    """Gather fully-qualified names of types returned by pointer from public functions."""
    factory_targets: set[str] = set()
    for fn in graph.snapshot.functions:
        if fn.visibility != Visibility.PUBLIC:
            continue
        if fn.return_pointer_depth >= 1 or _is_pointer(fn.return_type):
            resolved = _resolve_type_name(
                _strip_ptr(fn.return_type), all_type_names, by_short
            )
            if resolved is not None:
                factory_targets.add(resolved)
    return factory_targets


def _detect_non_virtual_dtor(
    graph: SurfaceGraph,
    base_targets: set[str],
    factory_targets: set[str],
) -> list[AntiPattern]:
    """Collect POLYMORPHIC_TYPE_NON_VIRTUAL_DTOR findings."""
    found: list[AntiPattern] = []
    for rec in graph.snapshot.types:
        if not rec.vtable:
            continue
        used_as_base = rec.name in base_targets
        used_as_factory = rec.name in factory_targets
        if not (used_as_base or used_as_factory):
            continue
        if _has_virtual_destructor(rec):
            continue
        role = "base class" if used_as_base else "factory return"
        found.append(
            AntiPattern(
                symbol=rec.name,
                kind=ChangeKind.POLYMORPHIC_TYPE_NON_VIRTUAL_DTOR,
                description=(
                    f"polymorphic type {rec.name} (used as {role}) has a vtable "
                    f"but no virtual destructor — delete through base is UB"
                ),
                evidence=[f"{rec.name} has vtable, no destructor slot; role={role}"],
            )
        )
    return found


def detect_antipatterns(graph: SurfaceGraph) -> list[AntiPattern]:
    """Recognise single-snapshot anti-patterns over *graph* (ADR-027 D2.2).

    Returns the deterministic, order-stable list of RISK-level anti-patterns:
    public functions exposing ``std::`` types by value, and polymorphic types
    (used as a base or factory return) lacking a virtual destructor.
    """
    found: list[AntiPattern] = _detect_stl_by_value(graph)

    # POLYMORPHIC_TYPE_NON_VIRTUAL_DTOR — build name indexes, collect targets,
    # then flag vtable types with no virtual destructor.
    all_type_names, by_short = _build_type_name_index(graph)
    base_targets = _collect_base_targets(graph, all_type_names, by_short)
    factory_targets = _collect_factory_targets(graph, all_type_names, by_short)
    found += _detect_non_virtual_dtor(graph, base_targets, factory_targets)

    return sorted(found, key=lambda a: (a.kind.value, a.symbol))


def recognise_idioms(graph: SurfaceGraph) -> dict[str, list[IdiomTag]]:
    """Recognise all idioms in *graph*, keyed by declaration name.

    Deterministic and order-stable: the returned dict and every tag list are
    sorted, so identical snapshots always produce identical results.
    """
    tags: dict[str, list[IdiomTag]] = {}

    def add(name: str, tag: IdiomTag | None) -> None:
        if tag is not None:
            tags.setdefault(name, []).append(tag)

    for rec in graph.snapshot.types:
        add(rec.name, _recognise_opaque(graph, rec))
        add(rec.name, _recognise_pimpl(graph, rec))

    for collector in (
        _recognise_handle,
        _recognise_factory,
        _recognise_create_destroy,
        _recognise_callbacks,
    ):
        for name, tag in collector(graph).items():
            add(name, tag)

    return {
        name: sorted(tag_list, key=lambda t: t.idiom.value)
        for name, tag_list in sorted(tags.items())
    }
