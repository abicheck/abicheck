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

"""Source ABI extractor interface and the shared model→entity mapping (ADR-030 D3).

The extractor interface (ADR-032) is one normalized contract behind which several
backends sit — castxml replay now, Clang LibTooling / Android adapters later
(ADR-030 D3 table). Each backend produces a :class:`SourceAbiTu`; the
abicheck-owned schema is authoritative and external tool formats are raw
provenance only (ADR-028 D4).

This module holds the *pure*, tool-independent half: converting abicheck's own
parsed model objects (``Function``/``RecordType``/``EnumType``/``Variable`` plus
the constant/typedef dictionaries the castxml parser already produces) into
:class:`SourceEntity` records and assembling them into a :class:`SourceAbiTu`.
Keeping it free of any subprocess/tool call makes the mapping unit-testable
without castxml installed.
"""

from __future__ import annotations

import hashlib
from typing import Protocol

from ...model import (
    AccessLevel,
    EnumType,
    Function,
    RecordType,
    ScopeOrigin,
    Variable,
)
from ..build_evidence import CompileUnit
from ..model import LayerConfidence
from ..source_abi import SourceAbiTu, SourceEntity, SourceLocation

#: Map a model ``ScopeOrigin`` to the (visibility, location-origin) pair used by
#: the L4 schema. Only PUBLIC_HEADER/GENERATED land on the public source surface.
_ORIGIN_MAP: dict[ScopeOrigin, tuple[str, str]] = {
    ScopeOrigin.PUBLIC_HEADER: ("public_header", "PUBLIC_HEADER"),
    ScopeOrigin.PRIVATE_HEADER: ("private_header", "PRIVATE_HEADER"),
    ScopeOrigin.SYSTEM_HEADER: ("system_header", "SYSTEM_HEADER"),
    ScopeOrigin.GENERATED: ("generated", "GENERATED"),
    ScopeOrigin.EXPORT_ONLY: ("unknown", "UNKNOWN"),
    ScopeOrigin.UNKNOWN: ("unknown", "UNKNOWN"),
}

_PUBLIC_ORIGINS = frozenset({ScopeOrigin.PUBLIC_HEADER, ScopeOrigin.GENERATED})
#: Class-member access levels that keep a member *off* the public source surface:
#: a private/protected method or constructor of a public class is not callable by
#: consumers, so a private default-arg edit must not produce an L4 finding. Free
#: (namespace-scope) functions carry AccessLevel.PUBLIC, so they are unaffected.
_NON_PUBLIC_ACCESS = frozenset({AccessLevel.PRIVATE, AccessLevel.PROTECTED})


class SourceExtractionError(RuntimeError):
    """An extractor could not produce a dump (tool missing, parse/timeout error).

    Callers record the failure as partial L4 coverage (ADR-028 D7) rather than
    aborting the whole comparison — the artifact tiers remain authoritative.
    """


class SourceAbiExtractor(Protocol):
    """One backend that turns a build compile-unit into a per-TU source dump.

    Implementations parse the TU/headers under the compile unit's real build
    context (ADR-030 D2) and return a normalized :class:`SourceAbiTu`. The
    interface is intentionally tiny so castxml, Clang, and Android backends are
    interchangeable (ADR-032).
    """

    name: str

    def available(self) -> bool:
        """True when the backend's external tool is usable in this environment.

        Implementations probe for their front-end (clang/castxml/header-abi
        dumper); a ``False`` lets callers degrade L4 to partial coverage instead
        of attempting an extraction that would only raise (ADR-028 D3).
        """
        ...

    def extract(
        self,
        compile_unit: CompileUnit,
        *,
        public_header_roots: list[str],
        target_id: str = "",
    ) -> SourceAbiTu: ...


def _content_hash(*parts: str) -> str:
    """Stable ``sha256:`` digest over the joined parts (order-significant)."""
    blob = "\x00".join(parts).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def _line_of(source_location: str | None) -> int:
    """Pull the trailing ``:<line>`` off a ``path:line`` location string."""
    if not source_location or ":" not in source_location:
        return 0
    tail = source_location.rsplit(":", 1)[1]
    return int(tail) if tail.isdigit() else 0


def _location(
    source_header: str | None, source_location: str | None, origin: ScopeOrigin
) -> SourceLocation:
    _, origin_label = _ORIGIN_MAP.get(origin, ("unknown", "UNKNOWN"))
    return SourceLocation(
        path=source_header or "",
        line=_line_of(source_location),
        origin=origin_label,
    )


def _visibility(origin: ScopeOrigin) -> str:
    return _ORIGIN_MAP.get(origin, ("unknown", "UNKNOWN"))[0]


def entity_from_function(fn: Function) -> SourceEntity:
    """Map a parsed :class:`Function` to a ``function`` source entity.

    ``signature_hash`` covers the type-level signature (return + parameter types
    + cv/ref qualifiers) so it is stable across a pure default-argument edit.
    ``value`` records each parameter's default-argument *expression* (castxml
    emits ``default="<expr>"``), so both adding/removing a default and changing
    its value (``x = 1`` → ``x = 2``) surface as a value change
    (``default_argument_changed``).

    The castxml parser stores ``Function.mangled`` as ``el.get("mangled","") or
    name``, so a public constructor (or any decl castxml emits without a mangled
    attribute) carries the *bare name* there. Treat ``mangled == name`` as "no
    distinguishing mangled name" and leave ``mangled_name`` empty so
    :meth:`SourceEntity.identity` falls back to ``qualified_name#signature_hash``
    and keeps unmangled overloads (``Widget(int)`` vs ``Widget(double)``)
    distinct — copying the bare-name fallback verbatim would collapse them.
    """
    qualifiers = []
    if fn.is_const:
        qualifiers.append("const")
    if fn.is_volatile:
        qualifiers.append("volatile")
    if fn.ref_qualifier:
        qualifiers.append(fn.ref_qualifier)
    sig = (
        f"{fn.return_type}({','.join(p.type for p in fn.params)}){''.join(qualifiers)}"
    )
    default_repr = ",".join(
        f"{p.name}={p.default}" for p in fn.params if p.default is not None
    )
    mangled = fn.mangled if fn.mangled and fn.mangled != fn.name else ""
    return SourceEntity(
        id=_content_hash("function", mangled or fn.name, sig),
        kind="function",
        qualified_name=fn.name,
        mangled_name=mangled,
        signature_hash=_content_hash("sig", sig),
        value=default_repr,
        source_location=_location(fn.source_header, fn.source_location, fn.origin),
        visibility=_visibility(fn.origin),
        # Private/protected members of a public class are not part of the callable
        # public surface, so keep them off it (Codex review #335, P2).
        api_relevant=fn.origin in _PUBLIC_ORIGINS and fn.access not in _NON_PUBLIC_ACCESS,
        confidence=LayerConfidence.HIGH,
    )


def entity_from_record(rec: RecordType) -> SourceEntity:
    """Map a parsed :class:`RecordType` (struct/class/union) to a ``record`` entity."""
    field_repr = ";".join(f"{f.name}:{f.type}@{f.offset_bits}" for f in rec.fields)
    type_repr = (
        f"{rec.kind}|size={rec.size_bits}|align={rec.alignment_bits}"
        f"|bases={','.join(rec.bases)}|vt={','.join(rec.vtable)}|{field_repr}"
    )
    return SourceEntity(
        id=_content_hash("record", rec.name, type_repr),
        kind="record",
        qualified_name=rec.name,
        type_hash=_content_hash("type", type_repr),
        source_location=_location(rec.source_header, rec.source_location, rec.origin),
        visibility=_visibility(rec.origin),
        api_relevant=rec.origin in _PUBLIC_ORIGINS,
        confidence=LayerConfidence.HIGH,
    )


def entity_from_enum(en: EnumType) -> SourceEntity:
    """Map a parsed :class:`EnumType` to an ``enum`` entity."""
    type_repr = f"{en.underlying_type}|" + ",".join(
        f"{m.name}={m.value}" for m in en.members
    )
    return SourceEntity(
        id=_content_hash("enum", en.name, type_repr),
        kind="enum",
        qualified_name=en.name,
        type_hash=_content_hash("type", type_repr),
        source_location=_location(en.source_header, en.source_location, en.origin),
        visibility=_visibility(en.origin),
        api_relevant=en.origin in _PUBLIC_ORIGINS,
        confidence=LayerConfidence.HIGH,
    )


def entity_from_variable(var: Variable) -> SourceEntity:
    """Map a parsed :class:`Variable` to a ``variable`` entity."""
    return SourceEntity(
        id=_content_hash("variable", var.mangled or var.name, var.type),
        kind="variable",
        qualified_name=var.name,
        mangled_name=var.mangled,
        type_hash=_content_hash("type", var.type),
        value=var.value or "",
        source_location=_location(var.source_header, var.source_location, var.origin),
        visibility=_visibility(var.origin),
        api_relevant=var.origin in _PUBLIC_ORIGINS,
        confidence=LayerConfidence.HIGH,
    )


def entity_from_constant(
    name: str, value: str, *, source_header: str = "", generated: bool = False
) -> SourceEntity:
    """Map a public ``const``/``constexpr`` constant (name→value) to a ``constexpr``
    entity, so a value change surfaces as ``constexpr_value_changed`` (ADR-030 D6).

    castxml resolves these from the public-header surface only (the parser scopes
    ``parse_constants`` by provenance), so they are always API-relevant. When the
    declaring header is itself a *generated* public header the caller passes
    ``generated=True`` (and the ``source_header`` path), which marks the entity
    ``GENERATED`` so ``_diff_generated`` owns it — without this a constant
    *removed* from a generated config header produces no L4 finding (the
    value-change case is already covered for plain public constants).
    """
    origin = "GENERATED" if generated else "PUBLIC_HEADER"
    return SourceEntity(
        id=_content_hash("constexpr", name, value),
        kind="constexpr",
        qualified_name=name,
        value=value,
        source_location=SourceLocation(path=source_header, origin=origin),
        visibility="generated" if generated else "public_header",
        api_relevant=True,
        confidence=LayerConfidence.HIGH,
    )


def entity_from_typedef(
    name: str, target: str, *, source_header: str = "", generated: bool = False
) -> SourceEntity:
    """Map a typedef alias (name→underlying type) to a ``typedef`` type entity.

    The **caller must pre-scope the typedefs to the public surface** (the castxml
    parser's ``parse_public_typedefs`` does this by provenance, like
    ``parse_constants`` for constants) so private/system aliases are not pulled
    onto the surface. ``source_header`` carries the declaring-header path so the
    linker's ODR detection — keyed on ``(qualified_name, header)`` — does not
    collide two same-named public typedefs from different headers into a false
    ``odr_source_conflict``. A typedef in a *generated* public header is marked
    ``GENERATED`` so ``_diff_generated`` owns its add/remove/change.
    """
    origin = "GENERATED" if generated else "PUBLIC_HEADER"
    return SourceEntity(
        id=_content_hash("typedef", name, target),
        kind="typedef",
        qualified_name=name,
        type_hash=_content_hash("type", target),
        value=target,
        source_location=SourceLocation(path=source_header, origin=origin),
        visibility="generated" if generated else "public_header",
        api_relevant=True,
        confidence=LayerConfidence.HIGH,
    )


def assemble_source_tu(
    compile_unit: CompileUnit,
    *,
    public_header_roots: list[str],
    target_id: str,
    extractor_name: str,
    extractor_version: str,
    functions: list[Function],
    records: list[RecordType],
    enums: list[EnumType],
    variables: list[Variable],
    constants: dict[str, str],
    typedefs: dict[str, str],
    constant_headers: dict[str, str] | None = None,
    generated_constants: set[str] | None = None,
    typedef_headers: dict[str, str] | None = None,
    generated_typedefs: set[str] | None = None,
    diagnostics: list[str] | None = None,
) -> SourceAbiTu:
    """Assemble parsed model objects into a normalized :class:`SourceAbiTu` (D4).

    Pure and tool-independent: any extractor that can produce these model lists
    (castxml today; others later) reuses this to emit the normalized dump.
    """
    return SourceAbiTu(
        tu_id=compile_unit.id,
        target_id=target_id or compile_unit.target_id,
        extractor={"name": extractor_name, "version": extractor_version},
        compile_context_hash=_content_hash(
            "ctx",
            compile_unit.standard,
            compile_unit.target_triple,
            compile_unit.sysroot or "",
            ",".join(f"{k}={v}" for k, v in sorted(compile_unit.defines.items())),
            ",".join(compile_unit.include_paths),
        ),
        source=compile_unit.source,
        public_header_roots=list(public_header_roots),
        functions=[entity_from_function(fn) for fn in functions],
        types=(
            [entity_from_record(r) for r in records]
            + [entity_from_enum(e) for e in enums]
            + [
                entity_from_typedef(
                    name,
                    target,
                    source_header=(typedef_headers or {}).get(name, ""),
                    generated=name in (generated_typedefs or set()),
                )
                for name, target in sorted(typedefs.items())
            ]
        ),
        variables=[entity_from_variable(v) for v in variables],
        constexpr_values=[
            entity_from_constant(
                name,
                value,
                source_header=(constant_headers or {}).get(name, ""),
                generated=name in (generated_constants or set()),
            )
            for name, value in sorted(constants.items())
        ],
        diagnostics=list(diagnostics or []),
    )
