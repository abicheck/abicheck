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

"""Source ABI replay schemas (ADR-030 D4, D5, D10).

Two abicheck-owned normalized models:

- :class:`SourceAbiTu` — the per-translation-unit dump (``tu_source_abi/*.json``,
  D4) an extractor (castxml/Clang/Android) emits after parsing one TU/header
  under its real per-TU build context. External tool formats are raw provenance
  only; this normalized schema is the stable contract (ADR-028 D4).
- :class:`SourceAbiSurface` — the per-library linked surface
  (``source/source_abi.json``, D5) the linker produces by folding TU dumps
  against the library's exported symbols and public-header set.

Every source-only finding carries the explicit evidence boundary of D10 so a
source/API risk is never confused with an artifact-proven shipped-ABI break.
Nothing here parses binaries or runs external tools — the model is pure data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .model import LayerConfidence

#: Source-ABI schema version, independent of the pack/build/snapshot versions
#: (ADR-030 D8 cache key; ADR-028 D8 versioning). Bumped on any breaking change
#: to ``SourceAbiTu`` or ``SourceAbiSurface``.
SOURCE_ABI_VERSION: int = 1

#: Evidence-boundary labels stamped on every source-only finding (ADR-030 D10).
#: They keep a source/API compatibility risk distinct from an artifact-proven
#: shipped-binary ABI break and feed the evidence-coverage report (ADR-028 D7).
EVIDENCE_TIER_L4 = "L4_SOURCE_ABI"


def _confidence(raw: Any) -> LayerConfidence:
    try:
        return LayerConfidence(raw if raw is not None else "unknown")
    except ValueError:
        return LayerConfidence.UNKNOWN


def _as_bool(raw: Any, default: bool) -> bool:
    """Forward-compat boolean parse: a hand-edited pack may carry the string
    ``"false"``, which ``bool(...)`` would misread as True (evidence/CLAUDE.md
    "never abort/​misload a hand-edited pack")."""
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        token = raw.strip().lower()
        if token in ("true", "1", "yes", "on"):
            return True
        if token in ("false", "0", "no", "off", ""):
            return False
    return default


@dataclass
class SourceLocation:
    """Where a source entity was declared, with provenance origin (ADR-030 D4)."""

    path: str = ""
    line: int = 0
    #: PUBLIC_HEADER | PRIVATE_HEADER | SYSTEM_HEADER | GENERATED | SOURCE | UNKNOWN
    origin: str = "UNKNOWN"

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "line": self.line, "origin": self.origin}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SourceLocation:
        return cls(
            path=str(d.get("path", "")),
            line=int(d.get("line", 0) or 0),
            origin=str(d.get("origin", "UNKNOWN")),
        )


@dataclass
class SourceEntity:
    """One normalized source declaration/definition (ADR-030 D4 entity schema).

    ``signature_hash`` captures the type-level signature (params/return), so a
    pure default-argument change leaves it stable while ``value`` differs.
    ``body_hash`` captures inline/template bodies. ``value`` carries the
    normalized value for macros and ``constexpr`` constants and the normalized
    default-argument string for functions.
    """

    id: str
    #: function|method|record|enum|typedef|union|variable|macro|template|inline|constexpr
    kind: str = ""
    qualified_name: str = ""
    mangled_name: str = ""
    signature_hash: str = ""
    body_hash: str = ""
    type_hash: str = ""
    value: str = ""
    source_location: SourceLocation | None = None
    #: public_header|private_header|system_header|generated|unknown
    visibility: str = "unknown"
    api_relevant: bool = True
    confidence: LayerConfidence = LayerConfidence.UNKNOWN

    def identity(self) -> str:
        """Stable cross-version identity that keeps C++ overloads distinct.

        The mangled name when available — it encodes the full signature, so
        overloads sharing one ``qualified_name`` (``f(int)`` vs ``f(double)``)
        get distinct keys, and it stays stable across body/default-argument
        changes (which the diff detects via ``body_hash``/``value``).

        When no mangled name is present, fold in the type-level
        ``signature_hash`` so unmangled overloads stay distinct. castxml omits a
        mangled name for some declarations — notably constructors — so
        overloaded public constructors (``Widget(int)`` and ``Widget(double)``,
        both bare-named ``Widget``) would otherwise collapse onto one key and
        silently drop an overload from the linked surface / diff. The signature
        hash is stable across body/default-argument edits, so those
        modifications are still matched (and detected via ``body_hash``/
        ``value``); a true signature change reads as add+remove, exactly as a
        mangled-name change would. Entities with no signature (macros,
        ``constexpr`` constants, types) fall back to the bare qualified name.

        The identity deliberately does **not** include the declaring header
        path: castxml reports an absolute build path, which differs between two
        checkout/build roots (old vs new) even for an unchanged declaration, so
        baking it in would make every unmangled identity build-root-dependent
        and emit false ``source_decl_binary_symbol_mismatch`` findings. The
        residual cost is that two unmangled, same-signature declarations sharing
        a bare name in different scopes (``a::Widget(int)`` vs ``b::Widget(int)``)
        collapse onto one key — a rare case, accepted until a backend that
        carries fully-qualified names (Clang, phase 5) replaces the bare name.
        The symbol-mapping diff reconciles by exported symbol (not identity), so
        it stays correct across build roots regardless.
        """
        if self.mangled_name:
            return self.mangled_name
        if self.signature_hash:
            return f"{self.qualified_name}#{self.signature_hash}"
        return self.qualified_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "qualified_name": self.qualified_name,
            "mangled_name": self.mangled_name,
            "signature_hash": self.signature_hash,
            "body_hash": self.body_hash,
            "type_hash": self.type_hash,
            "value": self.value,
            "source_location": (
                self.source_location.to_dict() if self.source_location else None
            ),
            "visibility": self.visibility,
            "api_relevant": self.api_relevant,
            "confidence": self.confidence.value,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SourceEntity:
        loc = d.get("source_location")
        return cls(
            id=str(d["id"]),
            kind=str(d.get("kind", "")),
            qualified_name=str(d.get("qualified_name", "")),
            mangled_name=str(d.get("mangled_name", "")),
            signature_hash=str(d.get("signature_hash", "")),
            body_hash=str(d.get("body_hash", "")),
            type_hash=str(d.get("type_hash", "")),
            value=str(d.get("value", "")),
            source_location=SourceLocation.from_dict(loc)
            if isinstance(loc, dict)
            else None,
            visibility=str(d.get("visibility", "unknown")),
            api_relevant=_as_bool(d.get("api_relevant"), True),
            confidence=_confidence(d.get("confidence")),
        )


@dataclass
class SourceAbiTu:
    """Per-TU source ABI dump produced by an extractor (ADR-030 D4).

    The normalized schema is authoritative; the raw extractor output (castxml
    XML, Clang ``.sdump``, Android ``.lsdump``) is preserved under ``raw/`` for
    provenance only (ADR-028 D4, ADR-030 D9).
    """

    schema_version: int = SOURCE_ABI_VERSION
    tu_id: str = ""  # "cu://src/foo.cpp#cfg:abc123"
    target_id: str = ""  # "target://libfoo"
    extractor: dict[str, str] = field(
        default_factory=dict
    )  # {"name": .., "version": ..}
    compile_context_hash: str = ""  # "sha256:..." (ADR-030 D8 cache key input)
    source: str = ""
    public_header_roots: list[str] = field(default_factory=list)
    declarations: list[SourceEntity] = field(default_factory=list)
    types: list[SourceEntity] = field(default_factory=list)
    functions: list[SourceEntity] = field(default_factory=list)
    variables: list[SourceEntity] = field(default_factory=list)
    macros: list[SourceEntity] = field(default_factory=list)
    templates: list[SourceEntity] = field(default_factory=list)
    inline_bodies: list[SourceEntity] = field(default_factory=list)
    constexpr_values: list[SourceEntity] = field(default_factory=list)
    source_edges: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    #: Every file the extractor actually read for this TU — the source plus all
    #: transitively included headers (public, private, generated, forced). The
    #: per-TU cache (ADR-030 D8) hashes these so an edit to *any* included header
    #: invalidates a stale dump, not just an edit to the configured public roots.
    read_files: list[str] = field(default_factory=list)

    def all_entities(self) -> list[SourceEntity]:
        """Flatten every entity list, preserving the per-kind grouping order."""
        out: list[SourceEntity] = []
        for bucket in (
            self.declarations,
            self.types,
            self.functions,
            self.variables,
            self.macros,
            self.templates,
            self.inline_bodies,
            self.constexpr_values,
        ):
            out.extend(bucket)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "tu_id": self.tu_id,
            "target_id": self.target_id,
            "extractor": dict(self.extractor),
            "compile_context_hash": self.compile_context_hash,
            "source": self.source,
            "public_header_roots": list(self.public_header_roots),
            "declarations": [e.to_dict() for e in self.declarations],
            "types": [e.to_dict() for e in self.types],
            "functions": [e.to_dict() for e in self.functions],
            "variables": [e.to_dict() for e in self.variables],
            "macros": [e.to_dict() for e in self.macros],
            "templates": [e.to_dict() for e in self.templates],
            "inline_bodies": [e.to_dict() for e in self.inline_bodies],
            "constexpr_values": [e.to_dict() for e in self.constexpr_values],
            "source_edges": list(self.source_edges),
            "diagnostics": list(self.diagnostics),
            "read_files": list(self.read_files),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SourceAbiTu:
        def _ents(key: str) -> list[SourceEntity]:
            return [SourceEntity.from_dict(e) for e in d.get(key, [])]

        # Defensive: a newer/hand-edited pack may carry ``read_files: null`` or a
        # stray string; ``list(...)`` on those raises or yields a char list, which
        # would poison cache dependency tracking (CodeRabbit review). Coerce to a
        # clean list of non-empty strings.
        rf_raw = d.get("read_files")
        read_files = (
            [str(p) for p in rf_raw if p] if isinstance(rf_raw, list) else []
        )

        return cls(
            schema_version=int(d.get("schema_version", SOURCE_ABI_VERSION)),
            tu_id=str(d.get("tu_id", "")),
            target_id=str(d.get("target_id", "")),
            extractor=dict(d.get("extractor", {})),
            compile_context_hash=str(d.get("compile_context_hash", "")),
            source=str(d.get("source", "")),
            public_header_roots=list(d.get("public_header_roots", [])),
            declarations=_ents("declarations"),
            types=_ents("types"),
            functions=_ents("functions"),
            variables=_ents("variables"),
            macros=_ents("macros"),
            templates=_ents("templates"),
            inline_bodies=_ents("inline_bodies"),
            constexpr_values=_ents("constexpr_values"),
            source_edges=list(d.get("source_edges", [])),
            diagnostics=list(d.get("diagnostics", [])),
            read_files=read_files,
        )


@dataclass
class SourceAbiSurface:
    """Linked per-library source ABI surface — ``source/source_abi.json`` (D5).

    The linker (``source_link.link_source_abi``) merges per-TU facts against the
    library's exported binary symbols and public-header set. The diff
    (``source_diff.diff_source_abi``) compares two of these surfaces to produce
    source/API findings (D6). ``reachable_source_surface`` keeps the D5 buckets;
    ``constexpr`` entities ride in ``declarations`` and the diff dispatches on
    ``SourceEntity.kind``.
    """

    schema_version: int = SOURCE_ABI_VERSION
    library: str = ""
    target_id: str = ""
    roots: dict[str, list[str]] = field(
        default_factory=lambda: {
            "exported_symbols": [],
            "public_header_declarations": [],
            "forced_public": [],
        }
    )
    reachable_declarations: list[SourceEntity] = field(default_factory=list)
    reachable_types: list[SourceEntity] = field(default_factory=list)
    reachable_macros: list[SourceEntity] = field(default_factory=list)
    reachable_templates: list[SourceEntity] = field(default_factory=list)
    reachable_inline_bodies: list[SourceEntity] = field(default_factory=list)
    #: decl_to_binary_symbol maps qualified_name -> exported symbol ("" when the
    #: declaration has no exported symbol, i.e. a public decl that is not shipped)
    mappings: dict[str, Any] = field(
        default_factory=lambda: {
            "source_decl_to_binary_symbol": {},
            "source_type_to_debug_type": {},
            "public_header_to_target": {},
        }
    )
    odr_conflicts: list[dict[str, Any]] = field(default_factory=list)
    unmatched: dict[str, list[str]] = field(
        default_factory=lambda: {"symbols_without_decl": [], "decls_without_symbol": []}
    )
    coverage: dict[str, Any] = field(default_factory=dict)

    def reachable_buckets(self) -> dict[str, list[SourceEntity]]:
        return {
            "declarations": self.reachable_declarations,
            "types": self.reachable_types,
            "macros": self.reachable_macros,
            "templates": self.reachable_templates,
            "inline_bodies": self.reachable_inline_bodies,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "library": self.library,
            "target_id": self.target_id,
            "roots": {k: list(v) for k, v in self.roots.items()},
            "reachable_source_surface": {
                name: [e.to_dict() for e in bucket]
                for name, bucket in self.reachable_buckets().items()
            },
            "mappings": {
                "source_decl_to_binary_symbol": dict(
                    self.mappings.get("source_decl_to_binary_symbol", {})
                ),
                "source_type_to_debug_type": dict(
                    self.mappings.get("source_type_to_debug_type", {})
                ),
                "public_header_to_target": dict(
                    self.mappings.get("public_header_to_target", {})
                ),
            },
            "odr_conflicts": list(self.odr_conflicts),
            "unmatched": {k: list(v) for k, v in self.unmatched.items()},
            "coverage": dict(self.coverage),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SourceAbiSurface:
        surface = d.get("reachable_source_surface", {})

        def _ents(key: str) -> list[SourceEntity]:
            return [SourceEntity.from_dict(e) for e in surface.get(key, [])]

        roots_raw = d.get("roots", {})
        roots = {
            "exported_symbols": list(roots_raw.get("exported_symbols", [])),
            "public_header_declarations": list(
                roots_raw.get("public_header_declarations", [])
            ),
            "forced_public": list(roots_raw.get("forced_public", [])),
        }
        mappings_raw = d.get("mappings", {})
        mappings = {
            "source_decl_to_binary_symbol": dict(
                mappings_raw.get("source_decl_to_binary_symbol", {})
            ),
            "source_type_to_debug_type": dict(
                mappings_raw.get("source_type_to_debug_type", {})
            ),
            "public_header_to_target": dict(
                mappings_raw.get("public_header_to_target", {})
            ),
        }
        unmatched_raw = d.get("unmatched", {})
        unmatched = {
            "symbols_without_decl": list(unmatched_raw.get("symbols_without_decl", [])),
            "decls_without_symbol": list(unmatched_raw.get("decls_without_symbol", [])),
        }
        return cls(
            schema_version=int(d.get("schema_version", SOURCE_ABI_VERSION)),
            library=str(d.get("library", "")),
            target_id=str(d.get("target_id", "")),
            roots=roots,
            reachable_declarations=_ents("declarations"),
            reachable_types=_ents("types"),
            reachable_macros=_ents("macros"),
            reachable_templates=_ents("templates"),
            reachable_inline_bodies=_ents("inline_bodies"),
            mappings=mappings,
            odr_conflicts=list(d.get("odr_conflicts", [])),
            unmatched=unmatched,
            coverage=dict(d.get("coverage", {})),
        )
