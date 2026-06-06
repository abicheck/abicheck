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

from .checker_policy import Confidence
from .model import RecordType, Visibility
from .surface_graph import SurfaceGraph


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


def _recognise_handle(graph: SurfaceGraph) -> dict[str, IdiomTag]:
    out: dict[str, IdiomTag] = {}
    for alias, target in sorted(graph.snapshot.typedefs.items()):
        t = target.strip()
        if not _is_pointer(t):
            continue
        pointee = _strip_ptr(t)
        # void* token, or a pointer to a forward-declared / unknown struct.
        rec = graph.types_by_name.get(pointee) or graph.types_by_name.get(
            pointee.rsplit("::", 1)[-1]
        )
        if pointee in ("void", "") or _record_is_incomplete(rec):
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
