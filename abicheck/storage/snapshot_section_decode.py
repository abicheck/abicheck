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

"""Per-section snapshot decoders and the join steps between them (ADR-062
D18, first step: the decode split that lazy per-section loading needs).

``snapshot_codec.decode_snapshot`` used to read the whole flat document in
one function, so no section could be decoded without the others. Here each
section kind of ``legacy_sections._SECTION_FIELDS`` has its own decoder,
and every step that needs more than one section is a named *join* that
declares what it reads:

* ``reads`` -- the section kinds whose raw keys the step may look at;
* ``after`` -- the steps whose *decoded* output it consumes.

Every step is handed a :class:`SectionView` of the flat document holding
only its declared sections' keys; reading any other section's key raises
:class:`UndeclaredSectionRead` instead of silently reading as absent. The
declarations in :data:`DECODE_STEPS` are therefore enforced on every load,
not only documented. The eager path composes the steps in
:data:`DECODE_STEPS` order and produces exactly the snapshot the
single-function decoder did.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

from ..errors import SnapshotError
from ..model import DependencyInfo
from ..model.header_exclusion_record import normalize_matching
from ..snapshot_platform_blocks import (
    dwarf_advanced_from_dict,
    dwarf_from_dict,
    elf_from_dict,
    kabi_from_dict,
    macho_from_dict,
    numpy_capi_from_dict,
    pe_from_dict,
    python_api_from_dict,
    python_ext_from_dict,
    sycl_from_dict,
)
from .fact_codec import decode_snapshot_facts
from .fact_decode import decode_abi_slot_fact
from .legacy_sections import SCHEMA_VERSION_KEY, section_of_field
from .snapshot_decode_declarations import decode_declaration_lists, decode_record_types
from .snapshot_load_normalization import (
    build_mode_from_dict,
    extraction_contract_from_dict,
)

_T = TypeVar("_T")

_MISSING = object()


class UndeclaredSectionRead(AssertionError):
    """A decode step read a key outside the sections it declared -- a
    programming error in the step, never a property of the document."""


class SectionView(dict[str, Any]):
    """The keys of a flat snapshot document that *sections* own, plus
    ``schema_version``. A read of a key another section (or no section)
    owns raises :class:`UndeclaredSectionRead`; a read of an own-section key
    the document lacks behaves like a plain dict's."""

    __slots__ = ("_sections", "_step")

    def __init__(
        self, document: Mapping[str, Any], sections: frozenset[str], step: str
    ):
        super().__init__(
            (key, value) for key, value in document.items() if _readable(key, sections)
        )
        self._sections = sections
        self._step = step

    def _check(self, key: object) -> None:
        if not (isinstance(key, str) and _readable(key, self._sections)):
            raise UndeclaredSectionRead(
                f"snapshot decode step {self._step!r} read {key!r}, owned by "
                f"section {section_of_field(key) if isinstance(key, str) else None!r}, "
                f"outside its declared sections {sorted(self._sections)}"
            )

    def __getitem__(self, key: str) -> Any:
        if not dict.__contains__(self, key):
            self._check(key)
        return dict.__getitem__(self, key)

    def __contains__(self, key: object) -> bool:
        if dict.__contains__(self, key):
            return True
        self._check(key)
        return False

    def get(self, key: Any, default: Any = None) -> Any:
        value = dict.get(self, key, _MISSING)
        if value is _MISSING:
            self._check(key)
            return default
        return value

    def __reduce__(self) -> Any:  # a view is never persisted or copied
        raise TypeError("SectionView is a decode-time view, not a value")


def _readable(key: str, sections: frozenset[str]) -> bool:
    return key == SCHEMA_VERSION_KEY or section_of_field(key) in sections


@dataclass(frozen=True)
class DecodeStep:
    """One step of the eager decode: the raw sections it reads and the
    earlier steps whose decoded output it consumes."""

    name: str
    reads: frozenset[str]
    after: tuple[str, ...] = ()


#: Every step, in the order the eager path runs them. A ``reads`` of one
#: section and an empty ``after`` is a per-section decoder; anything else is
#: a join. ``tests/unit/storage/test_snapshot_section_decode.py`` checks the
#: order respects ``after`` and that each per-section decoder succeeds with
#: every other section removed.
DECODE_STEPS: tuple[DecodeStep, ...] = (
    DecodeStep("declarations", frozenset({"declarations"})),
    DecodeStep("types", frozenset({"types"})),
    DecodeStep("binary", frozenset({"binary"})),
    DecodeStep("debug", frozenset({"debug"})),
    DecodeStep("build", frozenset({"build"})),
    # `from_headers`: recorded in `debug`; absent (a legacy document),
    # inferred from the decoded surface and `binary`'s `elf_only_mode`.
    DecodeStep(
        "from_headers",
        frozenset({"debug"}),
        after=("binary", "declarations", "types"),
    ),
    # The `*_facts_reliable` flags: recorded in `debug`, else derived from
    # schema_version + producer + `from_headers`.
    DecodeStep("reliability_flags", frozenset({"debug"}), after=("from_headers",)),
    # The ADR-063 legacy fact backfill rewrites decoded declarations/types
    # in place, judged by `debug`'s flags/provenance and `binary`'s platform;
    # it pairs decoded entities with their raw dicts by position.
    DecodeStep(
        "legacy_fact_backfill",
        frozenset({"declarations", "types", "debug", "binary"}),
        after=("declarations", "types", "from_headers", "reliability_flags"),
    ),
    DecodeStep("layout", frozenset({"layout"})),
    DecodeStep("provenance", frozenset({"provenance"})),
    # Post-construction joins over the assembled snapshot.
    DecodeStep("graph", frozenset({"graph", "build"}), after=("build",)),
    DecodeStep("semantic_ir", frozenset({"semantic_ir"})),
    DecodeStep(
        "extraction_scope",
        frozenset({"layout"}),
        after=("declarations", "types"),
    ),
    DecodeStep(
        "finalize_semantic_ir",
        frozenset(),
        after=("declarations", "types", "debug", "semantic_ir"),
    ),
)

STEPS_BY_NAME: Mapping[str, DecodeStep] = {step.name: step for step in DECODE_STEPS}


def view_for(document: Mapping[str, Any], step: str) -> SectionView:
    """*document* restricted to the sections step *step* declares."""
    return SectionView(document, STEPS_BY_NAME[step].reads, step)


def _sub_block(parser: Callable[[dict[str, Any]], _T], raw: Any) -> _T | None:
    """One optional sub-block, or ``None`` when absent or not an object --
    ignored rather than fatal, per the codec's forward-compatibility rule."""
    return parser(raw) if isinstance(raw, dict) else None


def _str_or_none(raw: Any) -> str | None:
    return raw if isinstance(raw, str) else None


def _bool_or_none(raw: Any) -> bool | None:
    return raw if isinstance(raw, bool) else None


def decode_declarations_section(
    d: dict[str, Any], schema_version: int
) -> dict[str, Any]:
    """``declarations``: the function/variable/enum lists, typedefs and
    their sidecars, constants, and the language/platform blocks the
    section carries. Keys are ``AbiSnapshot`` field names, except the raw
    ``sidecar_entity_ids`` mapping."""
    decoded = decode_declaration_lists(d, schema_version)
    return {
        "functions": decoded.functions,
        "variables": decoded.variables,
        "enums": decoded.enums,
        "typedefs": decoded.typedefs,
        "typedefs_qualified": decoded.typedefs_qualified,
        "typedef_entity_ids": decoded.sidecar_entity_ids["typedef_entity_ids"],
        "constant_entity_ids": decoded.sidecar_entity_ids["constant_entity_ids"],
        "sycl": _sub_block(sycl_from_dict, d.get("sycl")),
        "numpy_capi": _sub_block(numpy_capi_from_dict, d.get("numpy_capi")),
        "python_ext": _sub_block(python_ext_from_dict, d.get("python_ext")),
        "python_api": _sub_block(python_api_from_dict, d.get("python_api")),
        "constants": d.get("constants", {}),
    }


def decode_typedef_abi_facts(
    d: dict[str, Any], schema_version: int
) -> dict[str, Any] | None:
    """``declarations``' ``typedef_abi_facts`` (schema v69), or ``None`` when
    the producer captured none -- what every pre-v69 document reads as."""
    raw = d.get("typedef_abi_facts")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise SnapshotError("snapshot document's typedef_abi_facts must be a mapping")
    facts = {str(k): decode_abi_slot_fact(v, schema_version) for k, v in raw.items()}
    return {k: f for k, f in facts.items() if f is not None}


def decode_types_section(d: dict[str, Any], schema_version: int) -> dict[str, Any]:
    """``types``: the record list."""
    return {"types": decode_record_types(d, schema_version)}


def decode_binary_section(d: dict[str, Any], schema_version: int) -> dict[str, Any]:
    """``binary``: the platform metadata blocks and the artifact's identity."""
    return {
        "elf": _sub_block(lambda e: elf_from_dict(e, schema_version), d.get("elf")),
        "pe": _sub_block(lambda e: pe_from_dict(e, schema_version), d.get("pe")),
        "macho": _sub_block(
            lambda e: macho_from_dict(e, schema_version), d.get("macho")
        ),
        "kabi": _sub_block(kabi_from_dict, d.get("kabi")),
        # Rehydrate BuildMode (schema v5). Missing key = older snapshot ->
        # None, so build-mode-aware detectors fall back to "unknown".
        "build_mode": build_mode_from_dict(d.get("build_mode")),
        "elf_only_mode": bool(d.get("elf_only_mode", False)),
        "platform": d.get("platform"),
        "build_id": d.get("build_id"),
        "source_path": d.get("source_path"),
        "source_mtime": d.get("source_mtime"),
        "source_mtime_epoch": d.get("source_mtime_epoch", False),
        "source_size": d.get("source_size"),
    }


def decode_debug_section(d: dict[str, Any], schema_version: int) -> dict[str, Any]:
    """``debug``: the debug-info blocks and the header-AST producer's record.

    ``from_headers`` and the reliability flags are joins
    (:func:`infer_from_headers`, ``snapshot_reliability_flags``), not here."""
    raw_ast_toolchain = d.get("ast_toolchain")
    raw_unsupported = d.get("ast_toolchain_unsupported_reasons")
    raw_compile_args = d.get("ast_compile_args")
    return {
        "dwarf": _sub_block(dwarf_from_dict, d.get("dwarf")),
        "dwarf_advanced": _sub_block(dwarf_advanced_from_dict, d.get("dwarf_advanced")),
        "ast_producer": d.get("ast_producer"),
        "ast_toolchain": (
            {
                str(key): str(value)
                for key, value in raw_ast_toolchain.items()
                if isinstance(key, str) and isinstance(value, str)
            }
            if isinstance(raw_ast_toolchain, dict)
            else {}
        ),
        "ast_fallback_reason": _str_or_none(d.get("ast_fallback_reason")),
        "ast_toolchain_supported": _bool_or_none(d.get("ast_toolchain_supported")),
        "template_default_header_scan": _bool_or_none(
            d.get("template_default_header_scan")
        ),
        "ast_toolchain_unsupported_reasons": (
            [str(r) for r in raw_unsupported]
            if isinstance(raw_unsupported, list)
            else []
        ),
        # ADR-050 D5 (G32 Phase D) -- resolved SYCL/DPC++ "host"/"device".
        "frontend_context_kind": _str_or_none(d.get("frontend_context_kind")),
        "ast_resolved_standard": _str_or_none(d.get("ast_resolved_standard")),
        "ast_resolved_standard_fact": decode_snapshot_facts(
            {"ast_resolved_standard_fact": d.get("ast_resolved_standard_fact")},
            schema_version,
        )["ast_resolved_standard_fact"],
        "ast_cplusplus_macro": _str_or_none(d.get("ast_cplusplus_macro")),
        "ast_compile_args": (
            tuple(str(a) for a in raw_compile_args)
            if isinstance(raw_compile_args, (list, tuple))
            else ()
        ),
        "ast_sysroot": _str_or_none(d.get("ast_sysroot")),
        # G28 Phase 3 -- per-fact provenance of a hybrid snapshot.
        "fact_provenance": dict(d.get("fact_provenance", {})),
        # Build-context parse provenance (v7, ADR-029) and the active
        # build-time define set (ADR-039).
        "parsed_with_build_context": bool(d.get("parsed_with_build_context", False)),
        "build_context_defines": set(d.get("build_context_defines", [])),
    }


def infer_from_headers(
    d: dict[str, Any],
    *,
    elf_only_mode: bool,
    declarations: Mapping[str, Any],
    types: Mapping[str, Any],
) -> tuple[bool, bool]:
    """The ``from_headers`` join: ``(from_headers, from_headers_inferred)``.

    A present key -- including a legitimate False for DWARF-only/
    symbols-only dumps -- is honored verbatim. An absent key means a legacy
    snapshot dumped before the field existed: header provenance is inferred
    from a populated, non-elf-only surface so saved baselines do not
    silently downgrade from HEADER_AWARE, and marked inferred, because a
    legacy DWARF-only dump populates the same lists, so source-level
    detectors that demand genuine header evidence stay quiet."""
    if "from_headers" in d:
        return bool(d["from_headers"]), False
    from_headers = (not elf_only_mode) and bool(
        declarations["functions"]
        or declarations["variables"]
        or types["types"]
        or declarations["enums"]
        or declarations["typedefs"]
    )
    return from_headers, from_headers


def decode_build_section(d: dict[str, Any], schema_version: int) -> dict[str, Any]:
    """``build``: the build/source pack reference (schema v7, ADR-028; the
    pre-rename ``evidence_pack`` spelling as fallback) and the inline
    embedded build-info/source facts. Malformed values load as ``None``."""
    del schema_version
    ep_raw = d.get("build_source_pack")
    if ep_raw is None:
        ep_raw = d.get("evidence_pack")
    build_source_pack = None
    if isinstance(ep_raw, dict):
        from ..buildsource.model import BuildSourceRef

        build_source_pack = BuildSourceRef.from_dict(ep_raw)
    bs_raw = d.get("build_source")
    build_source = None
    if isinstance(bs_raw, dict):
        from ..buildsource.pack import BuildSourcePack

        build_source = BuildSourcePack.from_embedded_dict(bs_raw)
    return {"build_source_pack": build_source_pack, "build_source": build_source}


def decode_layout_section(d: dict[str, Any], schema_version: int) -> dict[str, Any]:
    """``layout``: layout coherence, extraction contract and header scope.
    ``extraction_scope`` attaches to decoded entities, so it is a join
    (``extraction_scope_codec.decode_extraction_scope``)."""
    raw_mismatches = d.get("dwarf_layout_coherence_mismatches")
    # Schema v18 -- a present, non-null value a producer cannot write is
    # refused: the comparability gate lets a None side through unchecked,
    # so a corrupt value must not be downgraded to None.
    raw_dependency_scope = d.get("dependency_scope")
    if raw_dependency_scope is not None and raw_dependency_scope not in (
        "filtered",
        "full",
    ):
        raise SnapshotError(
            f"invalid dependency_scope {raw_dependency_scope!r} in snapshot "
            "-- expected 'filtered', 'full', or the key to be absent/null; "
            "this snapshot is corrupt or was hand-edited."
        )
    return {
        "dwarf_layout_coherence": _str_or_none(d.get("dwarf_layout_coherence")),
        "dwarf_layout_coherence_mismatches": (
            tuple(str(m) for m in raw_mismatches)
            if isinstance(raw_mismatches, (list, tuple))
            else ()
        ),
        "contract": extraction_contract_from_dict(d.get("contract")),
        "dependency_scope": raw_dependency_scope,
        "public_header_identifiers_fact": decode_snapshot_facts(
            {"public_header_identifiers_fact": d.get("public_header_identifiers_fact")},
            schema_version,
        )["public_header_identifiers_fact"],
        "header_only": bool(d.get("header_only", False)),
        "excluded_header_patterns": tuple(d.get("excluded_header_patterns") or ()),
        # Schema v48; absent on v47 reads as "unknown", not "glob".
        "excluded_header_matching": normalize_matching(
            d.get("excluded_header_matching")
        ),
        "scope_fallback": d.get("scope_fallback"),
        # ADR-039 -- {type: {field: {guard, type, is_bitfield, bitfield_bits}}}.
        "conditional_fields": {
            str(t): {str(fn): dict(decl) for fn, decl in fields.items()}
            for t, fields in dict(d.get("conditional_fields", {})).items()
        },
    }


def decode_preprocessor_hidden(d: dict[str, Any]) -> dict[str, tuple[str, ...]] | None:
    """``layout``'s ``live_preprocessor_hidden`` scan (schema v67), or
    ``None`` when no scan ran. Refused, not coerced, when malformed."""
    value = d.get("live_preprocessor_hidden")
    if value is None:
        return None
    if not isinstance(value, dict) or not all(
        isinstance(names, list) and all(isinstance(n, str) for n in names)
        for names in value.values()
    ):
        raise SnapshotError(
            "snapshot document's live_preprocessor_hidden must map guard macros "
            "to lists of function names"
        )
    return {str(macro): tuple(names) for macro, names in value.items()}


def decode_provenance_section(d: dict[str, Any], schema_version: int) -> dict[str, Any]:
    """``provenance``: identity and capture metadata. ``library``/``version``
    are required; a missing one raises ``KeyError`` as it always did."""
    del schema_version
    dep_data = d.get("dependency_info")
    return {
        "library": d["library"],
        "version": d["version"],
        "language_profile": d.get("language_profile"),
        "dependency_info": (
            DependencyInfo(
                nodes=dep_data.get("nodes", []),
                edges=dep_data.get("edges", []),
                unresolved=dep_data.get("unresolved", []),
                bindings_summary=dep_data.get("bindings_summary", {}),
                missing_symbols=dep_data.get("missing_symbols", []),
            )
            if isinstance(dep_data, dict)
            else None
        ),
        "git_commit": d.get("git_commit"),
        "git_tag": d.get("git_tag"),
        "created_at": d.get("created_at"),
    }


def decode_dump_provenance(d: dict[str, Any]) -> dict[str, object] | None:
    """``provenance``'s ``dump_provenance`` block, or ``None`` when absent.
    Refused, not coerced, when present but not an object."""
    value = d.get("dump_provenance")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise SnapshotError(
            "snapshot document's dump_provenance must be a JSON object, got "
            f"{type(value).__name__}"
        )
    return value
