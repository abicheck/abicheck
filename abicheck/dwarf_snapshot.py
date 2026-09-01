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

"""DwarfSnapshotBuilder — build a complete AbiSnapshot from DWARF alone.

ADR-003: When no headers are provided but DWARF debug info is present,
this module builds a full AbiSnapshot from DWARF .debug_info, enabling
type-aware artifact checks that are unavailable in symbol-only mode.

DWARF provides: function signatures, struct/class layouts, enum definitions,
variables, typedefs, inheritance, vtable entries, templates.
DWARF does NOT provide: #define constants, default parameter values.

Visibility filtering: only types/functions reachable from ELF exported
symbols are included (DWARF × ELF intersection).
"""

from __future__ import annotations

import collections
import logging
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

from .dwarf_snapshot_datasources import show_data_sources as show_data_sources
from .dwarf_utils import (
    attr_bool as _attr_bool,
    attr_int as _attr_int,
    attr_str as _attr_str,
    decode_member_location as _decode_member_location,
    dwarf_low_memory_mode,
    free_cu_die_cache,
    has_real_dwarf_info,
    resolve_die_ref as _resolve_ref,
    resolve_type_die as _resolve_type_die,
)
from .elf_symbol_filter import is_abi_relevant_elf_symbol
from .model import (
    AbiSnapshot,
    AccessLevel,
    EnumMember,
    EnumType,
    Fact,
    Function,
    Param,
    ParamKind,
    RecordType,
    TypeField,
    Variable,
    Visibility,
    is_compiler_internal_type as _is_compiler_internal,
    is_cxx_runtime_library,
    record_layout_facts,
    resolve_vptr_offset_bits,
)
from .model.mangled_name import itanium_scope_components

if TYPE_CHECKING:
    from .dwarf_advanced import AdvancedDwarfMetadata
    from .dwarf_metadata import DwarfMetadata
    from .dwarf_unified import DwarfSession
    from .elf_metadata import ElfMetadata

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_snapshot_from_dwarf(
    elf_path: Path,
    elf_meta: ElfMetadata,
    dwarf_meta: DwarfMetadata,
    dwarf_adv: AdvancedDwarfMetadata,
    *,
    version: str = "unknown",
    language_profile: str | None = None,
    session: DwarfSession | None = None,
) -> AbiSnapshot:
    """Build a complete AbiSnapshot from DWARF, no headers required.

    Args:
        elf_path: Path to the ELF binary.
        elf_meta: Pre-parsed ELF metadata (for exported symbol set).
        dwarf_meta: Pre-parsed DWARF basic metadata (structs, enums).
        dwarf_adv: Pre-parsed DWARF advanced metadata.
        version: Version label for the snapshot.
        language_profile: "c" | "cpp" | None.
        session: Optional pre-opened :class:`~abicheck.dwarf_unified.DwarfSession`
            (as produced while parsing ``dwarf_meta``/``dwarf_adv``). When given,
            the DIE walk reuses that open ``DWARFInfo`` — hitting the cache the
            metadata passes warmed — instead of opening ``elf_path`` a second
            time. Output is byte-for-byte identical either way; the caller owns
            the session's lifetime.

    Returns:
        AbiSnapshot with functions, variables, types, enums, and typedefs
        populated from DWARF. elf_only_mode=False (full type info available).
    """
    builder = _DwarfSnapshotBuilder(elf_path, elf_meta)
    builder.extract(session=session)

    snapshot = AbiSnapshot(
        library=elf_path.name,
        version=version,
        functions=builder.functions,
        variables=builder.variables,
        types=builder.types,
        enums=builder.enums,
        typedefs=builder.typedefs,
        elf=elf_meta,
        dwarf=dwarf_meta,
        dwarf_advanced=dwarf_adv,
        elf_only_mode=False,
        platform="elf",
        language_profile=language_profile,
    )
    return snapshot


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _record_kind_from_tag(tag: str) -> str:
    """Return the ABI kind string for a record-type DWARF tag.

    Maps ``DW_TAG_union_type`` → ``"union"``, ``DW_TAG_class_type`` →
    ``"class"``, and everything else (``DW_TAG_structure_type``) → ``"struct"``.
    """
    if tag == "DW_TAG_union_type":
        return "union"
    if tag == "DW_TAG_class_type":
        return "class"
    return "struct"


def _default_member_access_for_tag(tag: str) -> AccessLevel:
    """C++'s default member access for a record-type DWARF tag.

    A compiler only emits ``DW_AT_accessibility`` on a member when it
    *differs* from the enclosing record's language default — ``class``
    defaults to private, ``struct``/``union`` to public (matching the C++
    access-specifier rules) — so an absent attribute must resolve to this,
    not unconditionally to public (Codex review: a `class`'s first,
    unlabelled member, or an anonymous aggregate declared before any access
    label, both carry no ``DW_AT_accessibility`` at all and were previously
    misread as public).
    """
    return AccessLevel.PRIVATE if tag == "DW_TAG_class_type" else AccessLevel.PUBLIC


def _local_vptr_member_offset_bits(child: Any) -> int | None:
    """Bit offset of *child* if it is the compiler's artificial vptr member.

    GCC names it ``_vptr.<Class>``, Clang ``_vptr$<Class>`` — both emit it as
    an ``DW_TAG_member`` with ``DW_AT_artificial: true`` and a real
    ``DW_AT_data_member_location`` (verified against real GCC 13/Clang 18
    ``-g`` output). Returns ``None`` for any other member, including a
    non-artificial member and an artificial member that isn't the vptr (e.g.
    a compiler-synthesized default argument holder) — the ``_vptr`` name
    prefix is the identifying signal, not artificiality alone.
    """
    if child.tag != "DW_TAG_member" or not _attr_bool(child, "DW_AT_artificial"):
        return None
    member_name = _attr_str(child, "DW_AT_name") or ""
    if not member_name.startswith("_vptr"):
        return None
    loc = child.attributes.get("DW_AT_data_member_location")
    return _decode_member_location(loc.value if loc is not None else None) * 8


# DIE tags whose children can hold ABI-relevant *top-level* declarations the
# main traversal dispatches on (nested types, member functions, namespace/CU
# members). Only these are descended into: every other tag — variables,
# typedefs, enums (their enumerators are consumed in place), members, base/
# pointer/qualifier type-chain DIEs, parameters — has no such descendant, so
# pushing its children onto the work stack is pure overhead. Restricting the
# descent this way, plus reusing one materialized child list per container for
# both field-collection and descent, makes the walk single-pass over the DIE
# tree (previously records' children were iterated twice — once to build fields,
# once by the generic descent — and every leaf DIE's empty child list was still
# materialized). ``iter_children()`` is the dominant DWARF-parse cost, so halving
# the calls is the primary saving.
_DESCEND_TAGS: frozenset[str] = frozenset(
    {
        # Compilation-unit roots (top DIE) — their children are the CU's decls.
        "DW_TAG_compile_unit",
        "DW_TAG_partial_unit",
        "DW_TAG_type_unit",
        # Namespaces and records nest further ABI-relevant declarations.
        "DW_TAG_namespace",
        "DW_TAG_structure_type",
        "DW_TAG_class_type",
        "DW_TAG_union_type",
    }
)


# ---------------------------------------------------------------------------
# Internal builder
# ---------------------------------------------------------------------------


class _DwarfSnapshotBuilder:
    """Extract ABI snapshot fields from DWARF .debug_info.

    Walks all CUs, extracting functions, variables, types, enums, and
    typedefs. Filters to ABI-relevant items via ELF exported symbol
    intersection.
    """

    def __init__(self, elf_path: Path, elf_meta: ElfMetadata) -> None:
        elf_path = Path(elf_path)
        self._elf_path = elf_path
        self._elf_meta = elf_meta
        self._filter_transitive_runtime_symbols = not (
            is_cxx_runtime_library(elf_path.name)
            or is_cxx_runtime_library(elf_meta.soname)
        )

        # Build exported symbol sets from ELF metadata, excluding
        # hidden/internal visibility symbols (not part of the public ABI)
        _HIDDEN_VIS = {"hidden", "internal"}
        self._exported_names: set[str] = set()
        if elf_meta.symbols:
            for sym in elf_meta.symbols:
                if (
                    sym.name
                    and sym.visibility not in _HIDDEN_VIS
                    and self._is_abi_relevant_export(sym.name)
                ):
                    self._exported_names.add(sym.name)

        # Build demangled export index for C++ fallback matching (FIX-B).
        # This allows matching DWARF functions against ELF exports even when
        # the mangled names differ slightly (e.g. const qualifier variance).
        self._demangled_exports: set[str] = set()
        from .demangle import demangle_batch

        cpp_names = [s for s in self._exported_names if s.startswith("_Z")]
        if cpp_names:
            demangled_map = demangle_batch(cpp_names)
            self._demangled_exports = set(demangled_map.values())

        # Results
        self.functions: list[Function] = []
        self.variables: list[Variable] = []
        self.types: list[RecordType] = []
        self.enums: list[EnumType] = []
        self.typedefs: dict[str, str] = {}

        # Type resolution cache: (cu_offset, die_offset) -> (name, byte_size)
        self._type_cache: dict[tuple[int, int], tuple[str, int]] = {}

        # Track types referenced by exported functions/variables for
        # transitive reachability filtering
        self._referenced_type_names: set[str] = set()

        # Dedup: prevent double-registration of types/enums across CUs
        self._seen_type_names: set[str] = set()
        self._seen_enum_names: set[str] = set()
        self._seen_func_mangles: set[str] = set()
        self._seen_var_mangles: set[str] = set()

        # FIX-I: throttle duplicate-type/enum debug messages to first occurrence
        self._logged_type_dups: set[str] = set()
        self._logged_enum_dups: set[str] = set()

        # (CU offset, DIE offset) -> the RecordType built from that DIE.
        # Used only by _finalize_vptr_offsets to resolve a base class to its
        # own already-built RecordType by DIE identity rather than by name:
        # RecordType.bases stores each base's *bare* DW_AT_name (matching
        # this class's own long-standing behavior, unrelated to this vptr
        # fix), but self.types is keyed by *qualified* name once a namespace
        # or enclosing class is involved -- a name-only lookup would silently
        # fail to resolve `ns::N : ns::A`'s inherited vptr (Codex review,
        # reproduced against a real GCC-compiled namespaced example). Every
        # ODR-duplicate DIE of an already-registered type (the same header
        # parsed in a second CU) is ALSO registered here, aliased to the one
        # retained RecordType (see _check_and_register_type_name's caller) --
        # otherwise a base referenced from that second CU would resolve to a
        # DIE identity this dict never learned about at all (a second, real
        # gap Codex review also caught, reproduced with two TUs sharing one
        # header).
        self._record_die_index: dict[tuple[int, int], RecordType] = {}
        # qualified name -> the retained RecordType, kept in lockstep with
        # self.types -- consulted only from _finalize_vptr_offsets (via
        # _die_key_to_qualified_name below), not for the primary base-name
        # lookup (that stays the bare-name `by_name` fallback there).
        self._record_by_qualified_name: dict[str, RecordType] = {}
        # (CU offset, DIE offset) -> qualified name, for a DIE that is NOT
        # the retained definition -- either an ODR-duplicate (a second CU's
        # own copy of an already-seen type) or a declaration-only forward
        # reference (the shape GCC emits for a base class referenced from a
        # CU that never defines it itself). Populated unconditionally at the
        # point such a DIE is encountered, regardless of whether the real
        # definition has been walked yet -- DWARF's per-CU emission order
        # does not guarantee the real definition comes first (see
        # _finalize_vptr_offsets's own docstring for the general version of
        # this ordering problem), so resolving through
        # ``_record_by_qualified_name`` is deferred to _finalize_vptr_offsets
        # time, once every CU is known, rather than attempted eagerly here.
        self._die_key_to_qualified_name: dict[tuple[int, int], str] = {}
        # id(derived RecordType) -> ordered list of (base bare name, this
        # edge's own bit offset or None, (CU offset, base DIE offset) or
        # None), ONE ENTRY PER DW_TAG_inheritance CHILD -- not a dict keyed
        # by base name. A derived class can directly inherit two distinct
        # bases sharing the same bare name in different namespaces (e.g.
        # `D : one::A, two::A`); a bare-name-keyed dict (the shape this
        # started as) collapses the second edge onto the first, corrupting
        # both the offset and the DIE identity for whichever edge lost —
        # reproduced with exactly that shape (Codex review): a polymorphic
        # `one::A` at real offset 0 and a non-polymorphic `two::A` at a
        # non-zero offset, where the second write to a shared key made the
        # true offset-0 candidate unrecoverable. Only non-virtual edges are
        # appended (mirroring `base_offsets`'s own population condition — a
        # virtual base's offset is dynamic and can never be primary), one
        # entry per edge regardless of how many other edges share its bare
        # name. Captured at inheritance-edge-processing time (while the base
        # DIE is still directly at hand) since a base is not guaranteed to
        # already be in ``_record_die_index`` at that point -- DWARF's
        # per-CU DIE emission order does not correlate with declaration
        # order (see _finalize_vptr_offsets's own docstring).
        self._base_edges_by_record: dict[
            int, list[tuple[str, int | None, tuple[int, int] | None]]
        ] = {}
        # id(derived RecordType) -> ordered list of (virtual base bare name,
        # (CU offset, base DIE offset) or None) -- the virtual-base
        # counterpart of ``_base_edges_by_record`` above, minus an offset
        # field (a virtual base's own subobject offset is inherently
        # dynamic, never something this resolution derives). Exists so
        # _finalize_vptr_offsets's virtual-primary-base fallback tier can
        # resolve a namespaced virtual base by DIE identity the same way
        # the non-virtual primary-base walk already does, instead of a
        # bare-name lookup that silently fails once a namespace is involved
        # (`ns::E : virtual A` failing to resolve via `ns::A`'s already-known
        # offset -- Codex review, fresh evidence, the same class of gap as
        # the earlier namespace-ambiguity finding, just for the virtual-only
        # fallback tier that finding predates).
        self._virtual_base_edges_by_record: dict[
            int, list[tuple[str, tuple[int, int] | None]]
        ] = {}

    def extract(self, session: DwarfSession | None = None) -> None:
        """Walk DWARF and populate result lists.

        When *session* is provided, its already-open ``DWARFInfo`` is reused
        (the metadata passes warmed the DIE cache); otherwise the ELF is opened
        here. Both paths produce identical results.
        """
        if session is not None:
            self._walk_dwarf(session.dwarf)
            return

        try:
            with open(self._elf_path, "rb") as f:
                elf = ELFFile(f)  # type: ignore[no-untyped-call]
                if not has_real_dwarf_info(elf):
                    return
                self._walk_dwarf(elf.get_dwarf_info())  # type: ignore[no-untyped-call]

        except (ELFError, OSError, ValueError) as exc:
            log.warning(
                "dwarf_snapshot: failed to parse %s: %s",
                self._elf_path,
                exc,
            )

    def _walk_dwarf(self, dwarf: Any) -> None:
        """Walk every CU of *dwarf*, then filter to exported reachability.

        Shared by the open-here and reuse-session paths of :meth:`extract`.

        On a large binary (``dwarf_low_memory_mode``), each CU's DIE cache
        is freed as soon as this walk finishes with it -- this walk only
        needs the plain functions/variables/types/enums/typedefs already
        appended to ``self`` by ``_process_cu``, never the DIE objects
        themselves afterwards, so retaining pyelftools' cache past that
        point is pure waste on a binary large enough for it to matter (see
        ``dwarf_utils.free_cu_die_cache``'s docstring).
        """
        low_memory = dwarf_low_memory_mode(dwarf)
        # First pass: extract all functions, variables, types, enums, typedefs
        for CU in dwarf.iter_CUs():
            try:
                self._process_cu(CU)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "dwarf_snapshot: skipping CU in %s: %s",
                    self._elf_path,
                    exc,
                )
            if low_memory:
                free_cu_die_cache(CU)

        # Second pass: resolve vptr_offset_bits for types whose vtable is
        # entirely inherited — needs every record type in self.types, not
        # just this CU's (see _finalize_vptr_offsets's own docstring).
        self._finalize_vptr_offsets()

        # Third pass: filter types to only those reachable from
        # exported symbols (transitive closure)
        self._filter_types_by_reachability()

    def _process_cu(self, CU: Any) -> None:
        """Walk all DIEs in one Compilation Unit."""
        top_die = CU.get_top_DIE()
        stack: collections.deque[tuple[Any, str]] = collections.deque([(top_die, "")])

        while stack:
            die, scope = stack.pop()
            tag = die.tag

            # Skip function bodies / inlined frames — we handle
            # DW_TAG_subprogram at the top level only
            if tag in (
                "DW_TAG_inlined_subroutine",
                "DW_TAG_lexical_block",
                "DW_TAG_GNU_call_site",
            ):
                continue

            die_name = _attr_str(die, "DW_AT_name")
            next_scope = scope
            # Materialize a container's children at most once and reuse the list
            # for both field-collection (records/enums) and the descent push, so
            # ``iter_children()`` (the dominant DWARF-parse cost) runs once per DIE,
            # not twice. ``None`` until a branch that needs the children populates it.
            children: list[Any] | None = None

            if tag == "DW_TAG_namespace" and die_name:
                next_scope = f"{scope}::{die_name}" if scope else die_name
            elif tag == "DW_TAG_subprogram":
                self._process_subprogram(die, CU, scope)
                continue  # don't descend into function body
            elif tag == "DW_TAG_variable":
                self._process_variable(die, CU, scope)
            elif tag in (
                "DW_TAG_structure_type",
                "DW_TAG_class_type",
                "DW_TAG_union_type",
            ):
                qualified = f"{scope}::{die_name}" if (scope and die_name) else die_name
                children = list(die.iter_children())
                self._process_record_type(die, CU, scope, children)
                if die_name:
                    next_scope = qualified or scope
            elif tag == "DW_TAG_enumeration_type":
                # Enumerators are consumed here; the enum itself is not descended
                # (not in _DESCEND_TAGS), so this list is used exactly once.
                children = list(die.iter_children())
                self._process_enum(die, CU, scope, children)
            elif tag == "DW_TAG_typedef":
                self._process_typedef(die, CU, scope)

            # Only descend into containers that can hold ABI-relevant nested decls
            # (see _DESCEND_TAGS) — pushing leaf DIEs' (usually empty) child lists
            # was the redundant half of the old two-pass walk.
            if tag in _DESCEND_TAGS:
                if children is None:
                    children = list(die.iter_children())
                for child in reversed(children):
                    stack.append((child, next_scope))

    # -------------------------------------------------------------------
    # Subprogram (function) extraction
    # -------------------------------------------------------------------

    def _process_subprogram(self, die: Any, CU: Any, scope: str) -> None:
        """Extract a function from DW_TAG_subprogram."""
        name = _attr_str(die, "DW_AT_name")
        if not name:
            return

        # Get linkage name (mangled name for C++)
        linkage_name = _attr_str(die, "DW_AT_linkage_name")
        if not linkage_name:
            linkage_name = _attr_str(die, "DW_AT_MIPS_linkage_name")
        mangled = linkage_name or name
        qualified_name = f"{scope}::{name}" if scope else name

        # Deleted functions intentionally bypass the exported-symbol check below
        # so a public API that becomes ``= delete`` is still observable. Do not
        # let that bypass re-admit transitive stdlib/runtime subprograms into a
        # non-runtime library's public surface.
        if not self._is_abi_relevant_export(
            mangled
        ) or not self._is_abi_relevant_export(qualified_name):
            return

        # DWARF5 DW_AT_deleted: function marked as = delete by the compiler.
        # Currently only emitted by very recent compilers (not yet in GCC/Clang mainline).
        # Future-proofing: check for the attribute and set is_deleted on the Function.
        is_deleted = _attr_bool(die, "DW_AT_deleted")

        # Skip declarations without definitions (DW_AT_declaration=True)
        # BUT: if it's marked deleted, we still want to record it for cross-reference.
        if _attr_bool(die, "DW_AT_declaration") and not is_deleted:
            return

        # Visibility: must be in ELF exported symbols.
        # BUT: deleted functions won't have symbols in the new binary, so bypass
        # the export check — we need them in the snapshot for cross-reference.
        # A function that is present in the binary (has a real DWARF
        # definition, already established above — declarations without a
        # definition returned earlier) with external linkage (DW_AT_external,
        # i.e. not a C `static`) but absent from the dynamic export set has had
        # its ELF visibility hidden — recorded as Visibility.HIDDEN rather than
        # dropped, so it isn't indistinguishable from an outright removal (see
        # case06_visibility). A function with NO external linkage (a genuine
        # `static`) was never part of the ABI and is correctly dropped.
        visibility = Visibility.PUBLIC
        if not is_deleted and not self._is_exported(mangled, name):
            if _attr_bool(die, "DW_AT_external"):
                visibility = Visibility.HIDDEN
            else:
                return

        # Dedup
        if mangled in self._seen_func_mangles:
            return
        self._seen_func_mangles.add(mangled)

        self.functions.append(
            self._build_function(
                die,
                CU,
                scope,
                name,
                mangled,
                qualified_name,
                is_deleted,
                visibility=visibility,
            )
        )

    def _build_function(
        self,
        die: Any,
        CU: Any,
        scope: str,
        name: str,
        mangled: str,
        qualified_name: str,
        is_deleted: bool,
        visibility: Visibility = Visibility.PUBLIC,
    ) -> Function:
        """Construct a :class:`Function` from a (already surface-admitted)
        ``DW_TAG_subprogram`` DIE.

        Pure DWARF→model mapping (N-C): admission/dedup live in
        ``_process_subprogram``; this only reads DIE attributes and builds the
        model object, so the DWARF-to-``Function`` translation is testable
        without the surface-filtering path. It still records referenced type
        names on the builder so the second extraction pass can reach them.
        """
        # Return type
        ret_type = "void"
        ret_ptr_depth = 0
        if "DW_AT_type" in die.attributes:
            ret_type, _ = self._resolve_type(die, CU)
            ret_ptr_depth = self._count_pointer_depth(die, CU)
        self._referenced_type_names.add(ret_type)

        # Parameters
        params: list[Param] = []
        for child in die.iter_children():
            if child.tag == "DW_TAG_formal_parameter":
                p = self._process_param(child, CU)
                if p is not None:
                    params.append(p)

        # Qualifiers
        is_virtual = _attr_bool(die, "DW_AT_virtuality") or (
            _attr_int(die, "DW_AT_virtuality") > 0
        )
        is_pure_virtual = (
            _attr_int(die, "DW_AT_virtuality") == 2
        )  # DW_VIRTUALITY_pure_virtual
        is_extern_c = not mangled.startswith("_Z")
        is_static = not _attr_bool(die, "DW_AT_external")
        # DW_AT_explicit is emitted by g++/clang++ on ctors and conversion ops
        # whose source-level declaration carries the `explicit` specifier
        # (C++20 conditional `explicit(bool)` collapses to its resolved bool).
        # Tri-state: True/False are authoritative readings from DWARF; the
        # snapshot loader defaults to None for older snapshots that predate
        # this field, so the diff can distinguish "unknown" from "implicit".
        #
        # `_attr_bool` returns False (never None) for a missing attribute
        # (Codex review, fresh evidence, PR #982), and the compiler only
        # ever *emits* DW_AT_explicit on a ctor/conversion-operator DIE in
        # the first place -- so a bare `_attr_bool` read can't distinguish
        # "confirmed not explicit" (an implicit ctor/conversion op, real
        # evidence) from "explicit is conceptually inapplicable" (an
        # ordinary method/free function/destructor, no evidence at all).
        # Eligibility is derived from the mangled name's own Itanium ctor/
        # conversion-operator encoding (`{ctor}`/`{op:cv:...}`) -- DWARF
        # carries no equivalent of castxml/clang's own AST node kind here.
        _explicit_components = itanium_scope_components(mangled)
        _is_explicit_eligible = (
            _explicit_components is not None
            and bool(_explicit_components)
            and (
                _explicit_components[-1] == "{ctor}"
                or _explicit_components[-1].startswith("{op:cv:")
            )
        )
        is_explicit: bool | None = (
            _attr_bool(die, "DW_AT_explicit") if _is_explicit_eligible else None
        )

        # Access level
        access_val = _attr_int(die, "DW_AT_accessibility")
        access = self._access_from_dwarf(access_val)

        # Vtable index
        vtable_index: int | None = None
        if "DW_AT_vtable_elem_location" in die.attributes:
            vtable_index = _attr_int(die, "DW_AT_vtable_elem_location")

        return Function(
            name=qualified_name if scope else name,
            mangled=mangled,
            return_type=ret_type,
            params=params,
            visibility=visibility,
            is_virtual=is_virtual,
            is_extern_c=is_extern_c,
            vtable_index=vtable_index,
            is_static=is_static,
            is_pure_virtual=is_pure_virtual,
            access=access,
            return_pointer_depth=ret_ptr_depth,
            is_deleted=is_deleted,
            deleted_from_dwarf=is_deleted,
            is_explicit=is_explicit,
            is_explicit_fact=(
                Fact.present(is_explicit)
                if _is_explicit_eligible
                else Fact.not_applicable()
            ),
        )

    def _is_abi_relevant_export(self, name: str) -> bool:
        """Return True when *name* belongs to this DSO's own ABI surface."""
        return is_abi_relevant_elf_symbol(
            name,
            filter_transitive_runtime_symbols=self._filter_transitive_runtime_symbols,
        )

    def _process_param(self, die: Any, CU: Any) -> Param | None:
        """Extract a parameter from DW_TAG_formal_parameter."""
        name = _attr_str(die, "DW_AT_name")
        # DWARF carries no va_list-ness signal for either branch below -- same UNSUPPORTED stance as dumper_castxml.py's Param.
        if "DW_AT_type" not in die.attributes:
            return Param(name=name, type="?", is_va_list_fact=Fact.unsupported())

        type_name, _ = self._resolve_type(die, CU)
        self._referenced_type_names.add(type_name)

        ptr_depth = self._count_pointer_depth(die, CU)
        kind = ParamKind.VALUE
        if ptr_depth > 0:
            kind = ParamKind.POINTER

        # Detect reference types
        type_die = _resolve_type_die(die, CU)
        if type_die is not None:
            if type_die.tag == "DW_TAG_reference_type":
                kind = ParamKind.REFERENCE
            elif type_die.tag == "DW_TAG_rvalue_reference_type":
                kind = ParamKind.RVALUE_REF

        return Param(
            name=name,
            type=type_name,
            kind=kind,
            pointer_depth=ptr_depth,
            is_va_list_fact=Fact.unsupported(),
        )

    # -------------------------------------------------------------------
    # Variable extraction
    # -------------------------------------------------------------------

    def _process_variable(self, die: Any, CU: Any, scope: str) -> None:
        """Extract a variable from DW_TAG_variable."""
        # Only externally visible variables
        if not _attr_bool(die, "DW_AT_external"):
            return

        name = _attr_str(die, "DW_AT_name")
        if not name:
            return

        linkage_name = _attr_str(die, "DW_AT_linkage_name")
        if not linkage_name:
            linkage_name = _attr_str(die, "DW_AT_MIPS_linkage_name")
        mangled = linkage_name or name

        if not self._is_exported(mangled, name):
            return

        if mangled in self._seen_var_mangles:
            return
        self._seen_var_mangles.add(mangled)

        type_name = "?"
        is_const = False
        if "DW_AT_type" in die.attributes:
            type_name, _ = self._resolve_type(die, CU)
            self._referenced_type_names.add(type_name)
            # Check for const qualifier
            type_die = _resolve_type_die(die, CU)
            if type_die is not None and type_die.tag == "DW_TAG_const_type":
                is_const = True

        qualified_name = f"{scope}::{name}" if scope else name

        self.variables.append(
            Variable(
                name=qualified_name if scope else name,
                mangled=mangled,
                type=type_name,
                visibility=Visibility.PUBLIC,
                is_const=is_const,
            )
        )

    # -------------------------------------------------------------------
    # Record type (struct/class/union) extraction
    # -------------------------------------------------------------------

    def _process_record_type(
        self, die: Any, CU: Any, scope: str, children: list[Any] | None = None
    ) -> None:
        """Extract a struct/class/union from DWARF.

        *children*, when supplied by the main traversal, is the already-materialized
        child DIE list, reused so ``iter_children()`` is not called a second time.
        """
        name = _attr_str(die, "DW_AT_name")
        if not name:
            return  # anonymous — handled via typedef
        if _is_compiler_internal(name):
            return

        qualified = f"{scope}::{name}" if scope else name
        self._process_record_type_named(die, CU, qualified, children)

    def _process_record_type_named(
        self, die: Any, CU: Any, qualified: str, children: list[Any] | None = None
    ) -> None:
        """Extract a struct/class/union using a given qualified name.

        *children* is the pre-materialized child DIE list from the main traversal
        (reused to avoid a second ``iter_children()``); ``None`` on the anonymous-
        typedef path, where the target DIE's children are iterated on demand.
        """
        byte_size = _attr_int(die, "DW_AT_byte_size")
        if byte_size == 0 and _attr_bool(die, "DW_AT_declaration"):
            # Forward declaration only -- but an inheritance edge in another
            # CU can (and does — this is the exact shape GCC emits for a
            # base class referenced from a CU that doesn't itself define it:
            # a declaration-only stub DIE, distinct from the real
            # definition's own DIE elsewhere) reference exactly THIS DIE, so
            # it still needs to resolve to the real type's qualified name or
            # that edge's lookup finds nothing (Codex review, reproduced
            # with two TUs sharing one header, one of which only
            # forward-references the base).
            self._register_die_qualified_name(die, CU, qualified)
            return

        if not self._check_and_register_type_name(qualified):
            # ODR: first definition wins (duplicate logged inside) -- but
            # THIS DIE's own identity still needs to resolve to the
            # already-retained RecordType, or a base reference from a
            # *different* CU pointing at this exact (now-discarded) DIE
            # would find nothing in _record_die_index at all (Codex review,
            # reproduced with two TUs sharing one header).
            self._register_die_qualified_name(die, CU, qualified)
            return

        tag = die.tag
        is_union = tag == "DW_TAG_union_type"
        kind = _record_kind_from_tag(tag)

        (
            fields,
            bases,
            virtual_bases,
            vtable,
            base_offsets,
            own_vptr_offset_bits,
            base_edges,
            virtual_base_edges,
        ) = self._collect_record_type_children(die, CU, children)

        alignment = _attr_int(die, "DW_AT_alignment")
        is_opaque = byte_size == 0 and not fields
        source_loc = self._resolve_decl_file(die, CU)

        # Real vptr offset, read from the compiler's own artificial vptr member when this class introduces one. A class that only extends or overrides an already-inherited vtable (no local vptr member, even when every one of its own declared methods is virtual) is left None here and resolved in a later pass, once every record type in this binary is known — see _finalize_vptr_offsets's docstring for why that can't be done eagerly, here, per-type. Tri-state by design: None also covers genuinely non-polymorphic and "only reachable through a virtual base" (whose offset is dynamic, never a fixed one this model can express), so the diff can tell "gained a vptr" (None → a real offset) from "vptr stayed".
        vptr_offset_bits = own_vptr_offset_bits
        # is_standard_layout / is_trivially_copyable / data_size_bits are
        # deliberately *not* derived here. "not polymorphic and no virtual bases"
        # is NOT a sound standard-layout signal: a class can already be
        # non-standard-layout for reasons DWARF doesn't expose here (mixed access
        # control, member-bearing bases). Such a class would be wrongly marked
        # True, then flip to False when it gains a virtual function — emitting a
        # spurious STANDARD_LAYOUT_LOST for a trait that was never present (Codex
        # review #345). Likewise DWARF can't tell a defaulted special member from
        # a user-provided one. Without a real trait signal these stay None and the
        # detector stays quiet rather than guessing.

        rec = RecordType(
            name=qualified,
            kind=kind,
            size_bits=byte_size * 8 if byte_size > 0 else None,
            alignment_bits=alignment * 8 if alignment > 0 else None,
            fields=fields,
            bases=bases,
            virtual_bases=virtual_bases,
            vtable=vtable,
            is_union=is_union,
            is_opaque=is_opaque,
            source_location=source_loc,
            vptr_offset_bits=vptr_offset_bits,
            base_offsets=base_offsets,
            # Stated explicitly, matching the other producers -- vptr_offset_bits may still be None pending _finalize_vptr_offsets's own later pass, which already resyncs both via resolve_vptr_offset_bits() if it resolves a real value.
            **record_layout_facts(bases, virtual_bases, vtable, vptr_offset_bits),
        )
        self.types.append(rec)
        self._record_by_qualified_name[qualified] = rec
        # Registered unconditionally (not just when base_edges is non-empty)
        # so _finalize_vptr_offsets can resolve THIS type as somebody else's
        # base by DIE identity too, not just by name. CU is None only in a
        # handful of unit tests exercising the dedup-logging path directly
        # (not a real DWARF walk) — skip registration rather than fail,
        # since there is no real DIE identity to key on anyway.
        if CU is not None:
            self._record_die_index[(CU.cu_offset, die.offset)] = rec
        if base_edges:
            self._base_edges_by_record[id(rec)] = base_edges
        if virtual_base_edges:
            self._virtual_base_edges_by_record[id(rec)] = virtual_base_edges

    def _check_and_register_type_name(self, qualified: str) -> bool:
        """Return True and register *qualified* if it is new; False if already seen.

        When the name was seen before, logs a one-time debug message (ODR).
        """
        if qualified in self._seen_type_names:
            if qualified not in self._logged_type_dups:
                self._logged_type_dups.add(qualified)
                log.debug("Duplicate type skipped (first-wins): %s", qualified)
            return False
        self._seen_type_names.add(qualified)
        return True

    def _register_die_qualified_name(self, die: Any, CU: Any, qualified: str) -> None:
        """Record that *die* — not itself the retained definition — names
        *qualified*, for ``_finalize_vptr_offsets`` to resolve later.

        Covers both non-retained shapes a base-class reference can point at:
        an ODR-duplicate (a second CU's own copy of an already-seen type)
        and a declaration-only forward reference. CU is None only in a
        handful of unit tests exercising the dedup-logging path directly
        (not a real DWARF walk) — skip registration rather than fail, since
        there is no real DIE identity to key on anyway.
        """
        if CU is not None:
            self._die_key_to_qualified_name[(CU.cu_offset, die.offset)] = qualified

    def _collect_record_type_children(
        self,
        die: Any,
        CU: Any,
        children: list[Any] | None = None,
    ) -> tuple[
        list[TypeField],
        list[str],
        list[str],
        list[str],
        dict[str, int],
        int | None,
        list[tuple[str, int | None, tuple[int, int] | None]],
        list[tuple[str, tuple[int, int] | None]],
    ]:
        """Iterate over the children of a record-type DIE.

        *children*, when provided, is the already-materialized child DIE list
        (from the main traversal) and is reused instead of re-walking the DIE; it
        is ``None`` only on the anonymous-typedef path, which iterates on demand.

        Returns ``(fields, bases, virtual_bases, vtable, base_offsets,
        own_vptr_offset_bits, base_edges, virtual_base_edges)``.

        - *fields*: data member ``TypeField`` objects (in order).
        - *bases*: names of direct (non-virtual) base classes.
        - *virtual_bases*: names of virtual base classes.
        - *vtable*: mangled names of virtual member functions.
        - *base_offsets*: base-name → bit offset for direct bases that carry a
          ``DW_AT_data_member_location``; empty when none do. Pre-existing,
          bare-name-keyed field, unrelated to this vptr fix and left as-is —
          note it can itself collide when two direct bases share a bare name
          (see *base_edges* below, which does not have this problem).
        - *own_vptr_offset_bits*: bit offset of *this* class's own artificial
          vptr member (``_vptr.<Class>``/``_vptr$<Class>``), when this class
          introduces one; ``None`` when it has no local vptr member (either
          non-polymorphic, or its vtable is entirely inherited — see
          ``_finalize_vptr_offsets``, which resolves the inherited case).
        - *base_edges*: one ``(base bare name, this edge's own bit offset or
          None, (CU offset, base DIE offset) or None)`` tuple per direct
          (non-virtual) ``DW_TAG_inheritance`` child, in DIE order — NOT a
          dict keyed by name, so two distinct bases sharing a bare name
          (``D : one::A, two::A``) each keep their own offset/identity
          instead of the second overwriting the first. Only
          ``_finalize_vptr_offsets`` consumes this.
        - *virtual_base_edges*: the virtual-base counterpart of *base_edges*
          — one ``(virtual base bare name, (CU offset, base DIE offset) or
          None)`` tuple per virtual ``DW_TAG_inheritance`` child, no offset
          field (a virtual base's own subobject offset is inherently
          dynamic). Lets ``_finalize_vptr_offsets``'s virtual-primary-base
          fallback tier resolve a namespaced virtual base by DIE identity
          instead of a bare-name lookup that fails once a namespace is
          involved (``ns::E : virtual A`` — Codex review, fresh evidence).
        """
        fields: list[TypeField] = []
        bases: list[str] = []
        virtual_bases: list[str] = []
        vtable: list[str] = []
        # Best-effort layout descriptor (layout-closure work). base_offsets maps
        # base name → bit offset within this object (from the inheritance DIE's
        # DW_AT_data_member_location); empty when no such offset is present.
        base_offsets: dict[str, int] = {}
        base_edges: list[tuple[str, int | None, tuple[int, int] | None]] = []
        virtual_base_edges: list[tuple[str, tuple[int, int] | None]] = []
        own_vptr_offset_bits: int | None = None
        default_access = _default_member_access_for_tag(die.tag)

        kids = children if children is not None else die.iter_children()
        for child in kids:
            if child.tag == "DW_TAG_member":
                if own_vptr_offset_bits is None:
                    own_vptr_offset_bits = _local_vptr_member_offset_bits(child)
                fields.extend(self._process_field(child, CU, default_access))
            elif child.tag == "DW_TAG_inheritance":
                self._process_inheritance_child(
                    child,
                    CU,
                    bases,
                    virtual_bases,
                    base_offsets,
                    base_edges,
                    virtual_base_edges,
                )
            elif child.tag == "DW_TAG_subprogram":
                self._process_virtual_method_child(child, vtable)

        return (
            fields,
            bases,
            virtual_bases,
            vtable,
            base_offsets,
            own_vptr_offset_bits,
            base_edges,
            virtual_base_edges,
        )

    def _process_inheritance_child(
        self,
        child: Any,
        CU: Any,
        bases: list[str],
        virtual_bases: list[str],
        base_offsets: dict[str, int],
        base_edges: list[tuple[str, int | None, tuple[int, int] | None]],
        virtual_base_edges: list[tuple[str, tuple[int, int] | None]],
    ) -> None:
        """Process a single ``DW_TAG_inheritance`` child DIE in-place.

        Appends to *bases* or *virtual_bases* as appropriate, and records the
        static bit-offset in *base_offsets* for direct (non-virtual) bases
        (pre-existing, bare-name-keyed — see its own docstring note above on
        why *base_edges* exists as a non-colliding alternative for the one
        consumer that actually needs exact per-edge identity).
        """
        base_name, base_die_key = self._resolve_base_name_and_key(child, CU)
        if not base_name:
            return
        is_virtual_base = _attr_int(child, "DW_AT_virtuality") > 0
        if is_virtual_base:
            virtual_bases.append(base_name)
            virtual_base_edges.append((base_name, base_die_key))
            return
        bases.append(base_name)
        # Record the (non-virtual) base subobject offset when present.
        # A virtual base's location is dynamic, so only capture the
        # static, direct-base offset. Same condition gates both the
        # pre-existing base_offsets write and the new base_edges append —
        # a virtual base is never a primary-base candidate either way.
        if "DW_AT_data_member_location" in child.attributes:
            offset_bits = (
                _decode_member_location(
                    child.attributes["DW_AT_data_member_location"].value
                )
                * 8
            )
            base_offsets[base_name] = offset_bits
            base_edges.append((base_name, offset_bits, base_die_key))

    @staticmethod
    def _process_virtual_method_child(child: Any, vtable: list[str]) -> None:
        """Append the mangled name of a virtual ``DW_TAG_subprogram`` to *vtable*.

        No-op when the subprogram is not virtual or has no usable name.
        """
        if _attr_int(child, "DW_AT_virtuality") > 0:
            vt_mangled = (
                _attr_str(child, "DW_AT_linkage_name")
                or _attr_str(child, "DW_AT_MIPS_linkage_name")
                or _attr_str(child, "DW_AT_name")
            )
            if vt_mangled:
                vtable.append(vt_mangled)

    def _finalize_vptr_offsets(self) -> None:
        """Resolve ``vptr_offset_bits`` for types whose vtable is inherited.

        ``_process_record_type_named`` already set ``vptr_offset_bits`` from
        each class's own artificial vptr member
        (``_vptr.<Class>``/``_vptr$<Class>``, read directly off DWARF instead
        of assumed to be 0 — G31 Phase C's "vptr placement is only a
        0-if-polymorphic heuristic" gap; verified against real GCC 13/Clang
        18 ``-g`` output for plain, multiple-inheritance, and virtual-base
        cases before writing this). Left ``None`` there is a class that only
        *extends or overrides* an already-inherited vtable (adds a new
        virtual method, or overrides one, without introducing a new vtable
        slot of its own) — it has no local vptr member at all, not even when
        every one of its own declared methods is virtual, or, in the most
        extreme case, when it declares no virtual methods of its own
        whatsoever and is polymorphic purely by inheritance (confirmed
        empirically: a class that neither adds nor overrides anything still
        carries no local ``vtable`` entries).

        This runs as a separate, whole-binary pass — *after* every CU has
        been walked and every record type is in ``self.types`` — rather than
        resolving eagerly per-class during the walk, because DWARF's
        per-child-DIE order does not correlate with declaration order (a
        compiler may emit a type's full definition at its first point of
        *use* in codegen, not at its point of *declaration* in the source —
        confirmed empirically: a subclass with no local vtable slot can be
        emitted by GCC before its own base class is). An eager, walk-order
        lookup would therefore silently miss a base processed later and
        leave a real inherited vptr resolved as unknown.

        Per Itanium C++ ABI (and, empirically, this is also what MSVC's
        non-virtual-inheritance layout does), the primary base — the one
        non-virtual base whose vtable pointer a derived class shares — is
        always placed at absolute offset 0 within the derived object, when
        one exists; a virtual base is never primary (its offset is resolved
        dynamically through the vbase-offset table, never a static
        ``DW_AT_data_member_location``) and is therefore never a candidate —
        mirrored by ``base_offsets`` only ever holding non-virtual bases
        (``_process_inheritance_child``'s own population of it). Since the
        primary base sits at offset 0, its own absolute vptr offset *is* the
        derived class's absolute vptr offset — no arithmetic needed, just a
        lookup once the base's own offset is known.

        A fixed-point loop (bounded by the actually-unresolved count, not a
        fixed depth) over the whole-binary type list handles a
        derived-of-derived chain (``M : N : A``) regardless of which of the
        three DWARF happened to emit first, converging once no further
        progress is made in a full pass over what remains unresolved.

        Resolving *which* base a bare name refers to is itself two-tiered,
        and deliberately does NOT walk ``RecordType.bases``/``base_offsets``
        (both pre-existing, bare-name-keyed fields, unrelated to this fix)
        directly. Two independent gaps were found and fixed here (both
        Codex review, both reproduced against real compiled binaries before
        fixing):

        1. **Namespace ambiguity.** ``self.types`` is keyed by *qualified*
           name the moment a namespace or enclosing class is involved
           (``qualified = f"{scope}::{name}"``), so a bare-name lookup alone
           silently fails to resolve `ns::N : ns::A`'s inherited vptr even
           though `ns::A`'s own offset is already known. Fixed by resolving
           each edge via the base DIE's own identity
           (``_base_edges_by_record``/``_record_die_index``, captured while
           each inheritance edge was still directly at hand — see
           ``_resolve_base_name_and_key``'s docstring), falling back to a
           bare-name lookup only when a base DIE didn't resolve to a key at
           all. A DIE for the SAME logical type discarded by ODR dedup (the
           same header parsed in a second CU) is separately aliased into
           ``_record_die_index`` at dedup time — see
           ``_process_record_type_named`` — so a base referenced from that
           second CU still resolves instead of finding an unregistered key.
        2. **Same-bare-name collision across two DIRECT bases of one
           class.** `D : one::A, two::A` — two distinct bases sharing a bare
           name in different namespaces — cannot be represented by a dict
           keyed on that bare name at all: the second edge's offset/identity
           silently overwrites the first's. This is why the walk below
           iterates ``_base_edges_by_record``'s ordered *list* of `(name,
           offset, key)` tuples, one per DW_TAG_inheritance child, rather
           than ``rec.bases`` (a plain name list) paired with
           ``rec.base_offsets``/a name-keyed dict (either pairing reopens
           the same collision `base_edges` exists specifically to avoid).

        A base DIE key can point at three different shapes, tried in order:
        the retained definition itself (``_record_die_index``, the common
        case); an ODR-duplicate or declaration-only stub DIE that names a
        *different* qualified type than its own bare name would suggest
        (``_die_key_to_qualified_name`` → ``_record_by_qualified_name`` —
        the exact shape GCC emits for a base referenced from a CU that
        never defines it itself, resolved here rather than eagerly, since
        the real definition is not guaranteed to already be known at the
        point the stub DIE was encountered); and, only once both DIE-identity
        tiers miss, the original bare-name ``by_name`` fallback.

        A LAST, final pass (after the fixed-point loop below) restores the
        original blanket "0 if polymorphic" heuristic for whatever remains
        unresolved — see that pass's own comment for the real case requiring
        it (a virtual-only base sharing a "nearly empty" primary base's
        vptr, which never enters this loop at all since virtual bases are
        excluded from ``bases``/``base_edges`` for the same static-offset
        reason ``base_offsets`` excludes them). This makes the whole
        function strictly additive over the pre-fix behavior: every type the
        old code resolved to 0 still resolves to (at worst) 0, and several
        classes it could only guess at now resolve to a real, DWARF-derived
        offset instead.
        """
        by_name = {t.name: t for t in self.types}

        def _resolve_base_record(
            base_name: str, base_key: tuple[int, int] | None
        ) -> RecordType | None:
            """Resolve a base-edge (name, DIE key) to its RecordType.

            Three tiers, tried in order: the retained definition itself
            (``_record_die_index``, the common case); an ODR-duplicate or
            declaration-only stub DIE that names a *different* qualified
            type than its own bare name would suggest
            (``_die_key_to_qualified_name`` → ``_record_by_qualified_name``);
            and, only once both DIE-identity tiers miss (or *base_key* is
            ``None``), the bare-name ``by_name`` fallback. Shared by both
            the primary-base walk and the virtual-primary-base fallback
            tier below, so a namespaced base resolves identically in both.
            """
            base_rec = (
                self._record_die_index.get(base_key) if base_key is not None else None
            )
            if base_rec is None and base_key is not None:
                aliased_name = self._die_key_to_qualified_name.get(base_key)
                if aliased_name is not None:
                    base_rec = self._record_by_qualified_name.get(aliased_name)
            if base_rec is None:
                base_rec = by_name.get(base_name)
            return base_rec

        unresolved = [t for t in self.types if t.vptr_offset_bits is None and t.bases]
        progressed = True
        while progressed and unresolved:
            progressed = False
            still_unresolved = []
            for rec in unresolved:
                edges = self._base_edges_by_record.get(id(rec), [])
                resolved = None
                for base_name, offset_bits, base_key in edges:
                    if offset_bits != 0:
                        continue
                    base_rec = _resolve_base_record(base_name, base_key)
                    if base_rec is not None and base_rec.vptr_offset_bits is not None:
                        resolved = base_rec.vptr_offset_bits
                        break
                if resolved is not None:
                    resolve_vptr_offset_bits(rec, resolved)
                    progressed = True
                else:
                    still_unresolved.append(rec)
            unresolved = still_unresolved

        # Final fallback: restore the original "0 if polymorphic" heuristic
        # for whatever remains unresolved, rather than leaving it None.
        # Real gap (Codex review, reproduced against real GCC output):
        # `struct E : virtual A { virtual void e(); };` where A is "nearly
        # empty" (no data members beyond its own vtable pointer) can be laid
        # out under the Itanium ABI's virtual-primary-base rule so E and A
        # SHARE one vptr slot at offset 0 with no local `_vptr.E` member of
        # E's own — GCC emits no such member here, unlike every other
        # virtual-base case this fix already handles (`struct D : virtual A
        # { virtual void d(); int di; };`, which DOES get its own `_vptr.D`,
        # confirmed by DIE dump, since D has a real data member forcing a
        # non-degenerate layout). Because E's only base is virtual, it's
        # entirely absent from `bases`/`base_edges` (mirroring `base_offsets`'s
        # own long-standing exclusion of virtual bases — their offset is
        # dynamic, not a fixed one this resolution walks), so the loop above
        # never even considers it. Every class this applies to has
        # `own_vptr_offset_bits is None` (no local member) AND is unreachable
        # via the primary-base walk (no resolvable non-virtual base, or none
        # at all) — exactly the shape the original heuristic covered by
        # assuming 0 for any type with a non-empty `vtable`, so restoring it
        # here for the specific remaining unresolved set is a pure regression
        # fix, not a reintroduction of the original guess for cases this pass
        # already resolves more precisely (those are excluded here since
        # their `vptr_offset_bits` is already set).
        for rec in self.types:
            if rec.vptr_offset_bits is None and rec.vtable:
                resolve_vptr_offset_bits(rec, 0)

        # Second final-fallback tier: a class that is polymorphic ONLY
        # through a "nearly empty" virtual base, adding or overriding no
        # virtual method of its own at all, has neither a local vptr member
        # nor any entry in its own `vtable` list -- confirmed with
        # `struct A { virtual void a(); }; struct E : virtual A {};`: GCC
        # emits no local `_vptr.E` and `E.vtable` is empty, so the tier
        # above (gated on `rec.vtable`) never applies (Codex review, fresh
        # evidence). Unlike the first fallback tier, this one is a genuine
        # accuracy improvement, not a regression fix: the pre-fix heuristic
        # (`0 if vtable else None`) ALSO returned `None` for this exact
        # shape, since `vtable` was always empty here -- there was no prior
        # "0" answer for this case to preserve. Resolved by checking whether
        # any of this class's OWN virtual bases is itself already known to
        # be polymorphic. A polymorphic virtual base is positive evidence
        # *this* class shares that vtable pointer under the same
        # virtual-primary-base rule the first tier's own comment documents,
        # so 0 is used unconditionally here too -- not the base's own
        # (possibly different, if it isn't nearly-empty) offset.
        #
        # Resolution is DIE-identity-first, the same three-tier lookup the
        # non-virtual primary-base walk above uses, NOT a bare `by_name`
        # lookup on its own: a namespaced virtual base (`ns::E : virtual A`)
        # has its bare name `"A"` recorded in `rec.virtual_bases`, so a
        # pure-name lookup silently fails to find `ns::A`'s already-resolved
        # offset -- the identical namespace-ambiguity gap the non-virtual
        # walk's own docstring documents, reproduced here for the
        # virtual-only fallback tier specifically (Codex review, fresh
        # evidence). `_virtual_base_edges_by_record` carries only a name +
        # DIE key per edge, no offset field (a virtual base's own subobject
        # offset is inherently dynamic, unlike a non-virtual `base_edges`
        # entry) -- this tier only ever answers "0 or unknown," never a real
        # non-zero offset, so it has no arithmetic to do once a candidate
        # resolves.
        #
        # This must be a fixed-point loop, not a single pass, for the same
        # reason the primary-base walk above is one: a multi-level virtual
        # chain (`struct F : virtual E {}; struct E : virtual A {};`) needs
        # E resolved before F can resolve through it, and `self.types`
        # iteration order follows DWARF emission order, not dependency
        # order (Codex review, reproduced against real GCC AND Clang output
        # with exactly this A/E/F shape: `self.types` came out as
        # `[F, A, E]` under GCC, so a single pass checked F while E was
        # still `None`, set E to 0, and left F unresolved).
        virtual_unresolved = [
            t for t in self.types if t.vptr_offset_bits is None and t.virtual_bases
        ]
        progressed = True
        while progressed and virtual_unresolved:
            progressed = False
            still_unresolved = []
            for rec in virtual_unresolved:
                virtual_edges = self._virtual_base_edges_by_record.get(id(rec), [])
                if any(
                    (vbase := _resolve_base_record(vbase_name, vbase_key)) is not None
                    and vbase.vptr_offset_bits is not None
                    for vbase_name, vbase_key in virtual_edges
                ):
                    resolve_vptr_offset_bits(rec, 0)
                    progressed = True
                else:
                    still_unresolved.append(rec)
            virtual_unresolved = still_unresolved

    def _resolve_decl_file(self, die: Any, CU: Any) -> str | None:
        """Resolve DW_AT_decl_file to a source file path.

        Returns a string like ``"foo.c:42"`` or ``"foo.h"`` (without line if
        unavailable), or ``None`` if the attribute is absent.
        """
        if "DW_AT_decl_file" not in die.attributes:
            return None
        file_idx = die.attributes["DW_AT_decl_file"].value
        if file_idx == 0:
            return None  # no file
        try:
            line_prog = CU.dwarfinfo.line_program_for_CU(CU)
            if line_prog is None:
                return None
            # DWARF 4: file_entry is 1-indexed into line_prog['file_entry']
            # DWARF 5: file_entry is 0-indexed into line_prog.header['file_names']
            file_entries = line_prog.header.get("file_names") or []
            # pyelftools uses 1-based indexing for DWARF 4
            ver = CU.header.get("version", 4) if hasattr(CU, "header") else 4
            idx = file_idx - 1 if ver < 5 else file_idx
            if 0 <= idx < len(file_entries):
                entry = file_entries[idx]
                fname = (
                    entry.name
                    if isinstance(entry.name, str)
                    else entry.name.decode("utf-8", errors="replace")
                )
                line = _attr_int(die, "DW_AT_decl_line")
                return f"{fname}:{line}" if line else fname
        except Exception:  # noqa: BLE001
            log.debug("Failed to resolve source location for DIE")
        return None

    def _process_field(
        self,
        die: Any,
        CU: Any,
        default_access: AccessLevel = AccessLevel.PUBLIC,
    ) -> list[TypeField]:
        """Extract a struct/class/union field, or the flattened fields of an
        anonymous struct/union member.

        *default_access* is the enclosing record's language default (private
        for ``class``, public for ``struct``/``union``), used when this
        field's own ``DW_AT_accessibility`` is absent — a compiler only
        emits that attribute when it differs from the default (Codex
        review).

        Returns a list (0, 1, or more ``TypeField``\\ s): an unnamed
        ``DW_TAG_member`` is either padding (0 results) or an anonymous
        aggregate whose own members clang flattens into the enclosing
        record's public surface (``RecordType.has_anonymous_aggregate_fields``
        in ``dumper_clang.py``) — recursing into it here and adjusting each
        inner field's offset by this member's own offset keeps DWARF's field
        offsets in step with the header's flattened view, so a field
        reordered *within* the aggregate remains a detectable layout change
        under the clang+DWARF backend instead of always resolving to
        ``offset_bits=None`` (Codex review).
        """
        name = _attr_str(die, "DW_AT_name")
        if not name:
            return self._flatten_anonymous_member(die, CU, default_access)
        if _attr_bool(die, "DW_AT_artificial"):
            # Compiler-injected, not a real user-visible member — e.g. the
            # implicit vtable pointer, which gcc names "_vptr.Foo" and
            # clang spells differently, a documented cross-compiler false-
            # positive source (test_cross_compiler_fp.py). It's already
            # represented separately via RecordType.vtable/vptr_offset_bits,
            # so surfacing it again as an ordinary field is both redundant
            # and, for a fieldless-but-polymorphic class specifically,
            # actively harmful: the clang header parser never emits it (no
            # data members at all), so it made every such class's non-empty
            # dwarf.fields look "unrelated" to the header's empty one and
            # block backfill outright, even for an exact name match (Codex
            # review).
            return []

        type_name = "?"
        if "DW_AT_type" in die.attributes:
            type_name, _ = self._resolve_type(die, CU)

        # Byte offset
        byte_offset = 0
        if "DW_AT_data_member_location" in die.attributes:
            byte_offset = _decode_member_location(
                die.attributes["DW_AT_data_member_location"].value
            )

        offset_bits = byte_offset * 8

        # Bitfield
        bit_size = _attr_int(die, "DW_AT_bit_size")
        is_bitfield = bit_size > 0
        bitfield_bits = bit_size if is_bitfield else None

        if is_bitfield:
            if "DW_AT_data_bit_offset" in die.attributes:
                offset_bits = _attr_int(die, "DW_AT_data_bit_offset")
            elif "DW_AT_bit_offset" in die.attributes:
                offset_bits = byte_offset * 8 + _attr_int(die, "DW_AT_bit_offset")

        # Access level
        access_val = _attr_int(die, "DW_AT_accessibility")
        access = self._access_from_dwarf(access_val, default_access)

        # Const / volatile
        is_const = False
        is_volatile = False
        type_die = _resolve_type_die(die, CU)
        if type_die is not None:
            if type_die.tag == "DW_TAG_const_type":
                is_const = True
            elif type_die.tag == "DW_TAG_volatile_type":
                is_volatile = True

        return [
            TypeField(
                name=name,
                type=type_name,
                offset_bits=offset_bits,
                is_bitfield=is_bitfield,
                bitfield_bits=bitfield_bits,
                is_const=is_const,
                is_volatile=is_volatile,
                access=access,
            )
        ]

    def _flatten_anonymous_member(
        self,
        die: Any,
        CU: Any,
        default_access: AccessLevel = AccessLevel.PUBLIC,
    ) -> list[TypeField]:
        """Flatten an anonymous struct/union member's own fields.

        *die* is the unnamed ``DW_TAG_member`` slot for the aggregate itself
        (not its type). Only a genuinely anonymous member type is flattened —
        a named (typedef'd) anonymous-record type isn't flattened by the
        clang header parser either, so treating it the same way here would
        create fields DWARF has that the header side doesn't. Each inner
        field's offset is relative to the anonymous aggregate, not the
        enclosing record, so it is adjusted by this member's own offset
        before returning.

        Access is likewise resolved from *this* outer member, not each
        inner one, and overrides it (CodeRabbit review): a C++ access
        specifier applies to the anonymous member's *declaration*, not to
        its individual members, and a compiler only emits
        ``DW_AT_accessibility`` on the inner ``DW_TAG_member``\\ s when it
        differs from *their own* enclosing (inner, anonymous) aggregate's
        default — which is unrelated to the outer class's access section.
        Confirmed against real GCC DWARF: a `protected:` anonymous union's
        outer member DIE carries ``DW_AT_accessibility: 2``, while its
        inner members carry none at all (and would otherwise silently
        resolve to the wrong default via ``_access_from_dwarf``).

        *default_access* is *this* member's own enclosing record's language
        default (private for `class`, public for `struct`/`union`), used
        when the outer member's own accessibility is absent — e.g. a
        `class`'s first, unlabelled anonymous union carries no
        ``DW_AT_accessibility`` at all (its access matches the class
        default, so the compiler omits it) and must not resolve to public
        (Codex review).
        """
        type_die = _resolve_type_die(die, CU)
        if type_die is None or type_die.tag not in (
            "DW_TAG_structure_type",
            "DW_TAG_union_type",
        ):
            return []
        if _attr_str(type_die, "DW_AT_name"):
            return []
        outer_offset_bits = 0
        if "DW_AT_data_member_location" in die.attributes:
            outer_offset_bits = (
                _decode_member_location(
                    die.attributes["DW_AT_data_member_location"].value
                )
                * 8
            )
        outer_access = self._access_from_dwarf(
            _attr_int(die, "DW_AT_accessibility"), default_access
        )
        flattened: list[TypeField] = []
        for child in type_die.iter_children():
            if child.tag != "DW_TAG_member":
                continue  # e.g. a nested type declaration, not a data member
            for inner in self._process_field(child, CU):
                flattened.append(
                    replace(
                        inner,
                        offset_bits=(inner.offset_bits or 0) + outer_offset_bits,
                        access=outer_access,
                    )
                )
        return flattened

    def _resolve_base_name_and_key(
        self, die: Any, CU: Any
    ) -> tuple[str, tuple[int, int] | None]:
        """Resolve ``DW_TAG_inheritance`` → ``(base class bare name, DIE key)``.

        The name is bare (``DW_AT_name`` only, no namespace/enclosing-class
        qualification), matching this method's long-standing behavior — every
        other consumer of ``RecordType.bases``/``base_offsets`` already
        expects that. The DIE key (``(base_die.cu.cu_offset, base_die.offset)``,
        matching ``_type_cache``'s own convention) is the base DIE's own
        identity, returned so a caller that specifically needs to resolve
        *which* record type this base refers to (unambiguous even when two
        distinct bases share the same bare name in different namespaces) can
        do so without a second, name-based lookup — see
        ``_finalize_vptr_offsets``. ``None`` for the key when the base type
        DIE itself can't be resolved (only the name lookup then degrades).

        Deliberately keyed on ``base_die.cu`` -- the resolved DIE's OWN
        owning compilation unit -- rather than *CU* (the referencing
        ``DW_TAG_inheritance`` DIE's own CU), even though for a CU-relative
        form (``DW_FORM_ref1``/``ref2``/``ref4``/``ref8``/``ref_udata``) the
        two are always identical (`resolve_die_ref`'s own arithmetic derives
        the target from *CU*'s own header offset, so it cannot land outside
        *CU*). ``DW_FORM_ref_addr`` is the one form this is NOT true for:
        it is section-absolute by construction (DWARF's own definition), so
        it can in principle name a DIE genuinely owned by a *different* CU
        than the referencing one -- and pyelftools's ``DIE.cu`` always
        records a DIE's real owning unit regardless of which CU's
        ``get_DIE_from_refaddr`` happened to resolve it (Codex review).
        Every real GCC/Clang producer checked while investigating this
        (GCC plain, GCC with `-flto`, GCC with `-fdebug-types-section`,
        Clang) keeps an inheritance edge CU-local in practice -- each always
        emits its own declaration-only stub for an out-of-CU base rather
        than a genuine cross-CU `DW_FORM_ref_addr` -- so this fix has no
        empirical repro on real compiler output the way this section's
        other fixes do; it closes a real, pyelftools-confirmed risk in the
        general case at zero cost (identical result whenever the two CUs
        already agree, which is every case tested), not a reproduced bug.
        """
        if "DW_AT_type" not in die.attributes:
            return "", None
        try:
            base_die = _resolve_ref(die, "DW_AT_type", CU)
            name = _attr_str(base_die, "DW_AT_name") or ""
            return name, (base_die.cu.cu_offset, base_die.offset)
        except Exception:  # noqa: BLE001
            return "", None

    # -------------------------------------------------------------------
    # Enum extraction
    # -------------------------------------------------------------------

    def _process_enum(
        self, die: Any, CU: Any, scope: str, children: list[Any] | None = None
    ) -> None:
        """Extract an enum from DWARF.

        *children* is the pre-materialized child DIE list from the main traversal,
        reused to avoid a second ``iter_children()``.
        """
        name = _attr_str(die, "DW_AT_name")
        if not name:
            return
        if _is_compiler_internal(name):
            return

        qualified = f"{scope}::{name}" if scope else name
        self._process_enum_named(die, CU, qualified, children)

    def _process_enum_named(
        self, die: Any, CU: Any, qualified: str, children: list[Any] | None = None
    ) -> None:
        """Extract an enum using a given qualified name.

        *children* is the pre-materialized child DIE list (reused when supplied);
        ``None`` on the anonymous-typedef path, which iterates on demand.
        """
        if qualified in self._seen_enum_names:
            if qualified not in self._logged_enum_dups:
                self._logged_enum_dups.add(qualified)
                log.debug("Duplicate enum skipped (first-wins): %s", qualified)
            return
        self._seen_enum_names.add(qualified)

        byte_size = _attr_int(die, "DW_AT_byte_size")
        if byte_size == 0:
            return  # declaration-only

        # Underlying type
        underlying = "int"
        if "DW_AT_type" in die.attributes:
            underlying, _ = self._resolve_type(die, CU)

        members: list[EnumMember] = []
        kids = children if children is not None else die.iter_children()
        for child in kids:
            if child.tag == "DW_TAG_enumerator":
                m_name = _attr_str(child, "DW_AT_name")
                m_val = _attr_int(child, "DW_AT_const_value")
                if m_name:
                    members.append(EnumMember(name=m_name, value=m_val))

        self.enums.append(
            EnumType(
                name=qualified,
                members=members,
                underlying_type=underlying,
                source_location=self._resolve_decl_file(die, CU),
            )
        )

    # -------------------------------------------------------------------
    # Typedef extraction
    # -------------------------------------------------------------------

    def _process_typedef(self, die: Any, CU: Any, scope: str = "") -> None:
        """Extract a typedef from DWARF.

        Also registers anonymous structs/enums under the typedef name
        (e.g. ``typedef struct { int x; } Point;``).

        The typedef is keyed by its *namespace-qualified* name (consistent with
        records and enums) so that standard-library typedefs nested under ``std``
        (e.g. ``std::vector<…>::size_type``) carry their ``std::`` scope and the
        non-ABI-surface filter can recognise them (validation/REPORT.md FP-1).
        """
        name = _attr_str(die, "DW_AT_name")
        if not name:
            return
        if _is_compiler_internal(name):
            return
        qualified = f"{scope}::{name}" if scope else name

        # Check if this typedef points to an anonymous struct/enum
        if "DW_AT_type" in die.attributes:
            try:
                target = _resolve_ref(die, "DW_AT_type", CU)
                target_name = _attr_str(target, "DW_AT_name")
                target_tag = target.tag

                if target_tag in (
                    "DW_TAG_structure_type",
                    "DW_TAG_class_type",
                    "DW_TAG_union_type",
                ):
                    if not target_name and qualified not in self._seen_type_names:
                        # Anonymous struct/union — register under the qualified
                        # typedef name so same-named typedefs in different
                        # namespaces don't collide on the bare alias.
                        self._process_record_type_named(target, CU, qualified)
                elif target_tag == "DW_TAG_enumeration_type":
                    if not target_name and qualified not in self._seen_enum_names:
                        # Anonymous enum — register under the qualified typedef name
                        self._process_enum_named(target, CU, qualified)
            except Exception:  # noqa: BLE001
                log.debug("Failed to process typedef target for %s", qualified)

        if qualified in self.typedefs:
            return  # first wins

        underlying = "?"
        if "DW_AT_type" in die.attributes:
            underlying = self._resolve_underlying_type(die, CU, depth=0)

        self.typedefs[qualified] = underlying

    def _resolve_underlying_type(self, die: Any, CU: Any, depth: int) -> str:
        """Follow typedef chains to the concrete base type."""
        if depth > 20:
            return "?"
        type_die = _resolve_type_die(die, CU)
        if type_die is None:
            return "?"
        if type_die.tag == "DW_TAG_typedef":
            return self._resolve_underlying_type(type_die, CU, depth + 1)
        name, _ = self._die_to_type_name(type_die, CU, depth=0)
        return name

    # -------------------------------------------------------------------
    # Visibility filtering
    # -------------------------------------------------------------------

    def _is_exported(self, mangled: str, name: str) -> bool:
        """Check if a symbol is in the ELF exported symbol set.

        Three-tier matching (FIX-B):
        1. Exact mangled name match (fast path)
        2. Plain name match (C functions)
        3. Demangled fallback (C++ with mangling variance)
        """
        # Tier 1: exact mangled match
        if mangled and mangled in self._exported_names:
            return True
        # Tier 2: plain name match (C functions)
        if name and name in self._exported_names:
            return True
        # Tier 3: demangled fallback for C++ (FIX-B)
        if mangled and mangled.startswith("_Z") and self._demangled_exports:
            from .demangle import demangle as _demangle

            demangled = _demangle(mangled)
            if demangled and demangled in self._demangled_exports:
                return True
        return False

    def _filter_types_by_reachability(self) -> None:
        """Filter types and enums to only those reachable from exports.

        After functions and variables are extracted, _referenced_type_names
        contains the directly referenced types. We transitively include
        types referenced by fields of those types.
        """
        # Build type name → RecordType index
        type_map: dict[str, RecordType] = {t.name: t for t in self.types}

        # Transitive closure: BFS from referenced types
        queue = collections.deque(self._referenced_type_names)
        reachable: set[str] = set(self._referenced_type_names)

        while queue:
            type_name = queue.popleft()
            rec = type_map.get(type_name)
            if rec is None:
                continue
            for field in rec.fields:
                # Strip pointer/reference/const/volatile/array suffixes
                base = _strip_type_decorators(field.type)
                if base not in reachable:
                    reachable.add(base)
                    queue.append(base)
            # Fact[T]-bridged reads (ADR-063 Phase 0): `bases_fact`/
            # `virtual_bases_fact` and their legacy siblings are kept in
            # lockstep by `RecordType.__post_init__`, so resolving through
            # the sibling here is exactly value-preserving. Each `*_fact`
            # field is declared `Fact[list[str]] | None` (only
            # `__init__`-time callers may omit it); `__post_init__` always
            # backfills a real `Fact`, so the leading `is not None` check
            # never actually fails at runtime — it, and the trailing
            # `.value is not None` (mirroring `bridge_legacy_and_fact`'s
            # own resolution), exist to narrow the type for mypy.
            bases_fact = rec.bases_fact
            rec_bases = (
                bases_fact.value
                if bases_fact is not None
                and bases_fact.is_present
                and bases_fact.value is not None
                else []
            )
            virtual_bases_fact = rec.virtual_bases_fact
            rec_virtual_bases = (
                virtual_bases_fact.value
                if virtual_bases_fact is not None
                and virtual_bases_fact.is_present
                and virtual_bases_fact.value is not None
                else []
            )
            for base_name in rec_bases + rec_virtual_bases:
                if base_name not in reachable:
                    reachable.add(base_name)
                    queue.append(base_name)

        # Filter: keep all types for now (DWARF types from file-scope are
        # generally ABI-relevant). The reachability set is stored for
        # future stricter filtering if needed.
        # NOTE: We don't filter aggressively because DWARF file-scope types
        # (structs, enums visible in headers) are almost always ABI-relevant.
        # Over-filtering would miss important type changes.

    # -------------------------------------------------------------------
    # Type resolution helpers
    # -------------------------------------------------------------------

    def _resolve_type(self, die: Any, CU: Any) -> tuple[str, int]:
        """Return (type_name, byte_size) for the type referenced by die."""
        if "DW_AT_type" not in die.attributes:
            return ("void", 0)
        try:
            type_die = _resolve_ref(die, "DW_AT_type", CU)
            return self._die_to_type_name(type_die, CU, depth=0)
        except Exception:  # noqa: BLE001
            return ("?", 0)

    def _die_to_type_name(self, die: Any, CU: Any, depth: int) -> tuple[str, int]:
        """Resolve a type DIE to (name, byte_size) with caching."""
        if depth > 8:
            return ("...", 0)

        cache_key = (CU.cu_offset, die.offset)
        if cache_key in self._type_cache:
            return self._type_cache[cache_key]

        result = self._compute_type_name(die, CU, depth)
        self._type_cache[cache_key] = result
        return result

    def _compute_type_name(self, die: Any, CU: Any, depth: int) -> tuple[str, int]:
        """Compute type name from a DWARF type DIE."""
        tag = die.tag

        if tag == "DW_TAG_base_type":
            return (
                _attr_str(die, "DW_AT_name") or "base",
                _attr_int(die, "DW_AT_byte_size"),
            )

        if tag in ("DW_TAG_structure_type", "DW_TAG_class_type", "DW_TAG_union_type"):
            name = _attr_str(die, "DW_AT_name") or "<anon>"
            return (name, _attr_int(die, "DW_AT_byte_size"))

        if tag == "DW_TAG_enumeration_type":
            name = _attr_str(die, "DW_AT_name") or "<enum>"
            return (f"enum {name}", _attr_int(die, "DW_AT_byte_size"))

        if tag == "DW_TAG_pointer_type":
            inner = self._resolve_inner_name(die, CU, depth)
            size = _attr_int(die, "DW_AT_byte_size") or 0
            return (f"{inner} *" if inner else "void *", size)

        if tag == "DW_TAG_reference_type":
            inner = self._resolve_inner_name(die, CU, depth)
            size = _attr_int(die, "DW_AT_byte_size") or 0
            return (f"{inner} &" if inner else "? &", size)

        if tag == "DW_TAG_rvalue_reference_type":
            inner = self._resolve_inner_name(die, CU, depth)
            size = _attr_int(die, "DW_AT_byte_size") or 0
            return (f"{inner} &&" if inner else "? &&", size)

        if tag in ("DW_TAG_const_type", "DW_TAG_volatile_type", "DW_TAG_restrict_type"):
            qualifier = tag.split("_")[2].lower()
            inner_info = self._resolve_inner_info(die, CU, depth)
            if inner_info is None:
                return (qualifier, 0)
            return (f"{qualifier} {inner_info[0]}", inner_info[1])

        if tag == "DW_TAG_atomic_type":
            # Spelled "_Atomic" (not the generic tag.split() lowercase form) so
            # it matches the C11 keyword diff_atomic.py's _has_atomic() looks for.
            inner_info = self._resolve_inner_info(die, CU, depth)
            if inner_info is None:
                return ("_Atomic", 0)
            return (f"_Atomic {inner_info[0]}", inner_info[1])

        if tag == "DW_TAG_typedef":
            name = _attr_str(die, "DW_AT_name")
            inner_info = self._resolve_inner_info(die, CU, depth)
            if inner_info is None:
                return (name or "typedef", 0)
            return (name or inner_info[0], inner_info[1])

        if tag == "DW_TAG_array_type":
            size = _attr_int(die, "DW_AT_byte_size")
            inner = self._resolve_inner_name(die, CU, depth)
            return (
                f"{inner}[]" if inner else "array",
                size,
            )

        if tag == "DW_TAG_subroutine_type":
            return ("fn(...)", _attr_int(die, "DW_AT_byte_size"))

        # Fallback
        name = _attr_str(die, "DW_AT_name")
        return (name or tag or "unknown", _attr_int(die, "DW_AT_byte_size"))

    def _resolve_inner_name(self, die: Any, CU: Any, depth: int) -> str | None:
        """Resolve inner type name (for pointer/reference/array types)."""
        info = self._resolve_inner_info(die, CU, depth)
        return info[0] if info is not None else None

    def _resolve_inner_info(
        self, die: Any, CU: Any, depth: int
    ) -> tuple[str, int] | None:
        """Resolve inner type info (for qualified/typedef types)."""
        if "DW_AT_type" not in die.attributes:
            return None
        try:
            inner_die = _resolve_ref(die, "DW_AT_type", CU)
            return self._die_to_type_name(inner_die, CU, depth + 1)
        except Exception:  # noqa: BLE001
            return None

    def _count_pointer_depth(self, die: Any, CU: Any, depth: int = 0) -> int:
        """Count pointer nesting: T=0, T*=1, T**=2."""
        if depth > 10:
            return 0
        type_die = _resolve_type_die(die, CU)
        if type_die is None:
            return 0
        if type_die.tag == "DW_TAG_pointer_type":
            return 1 + self._count_pointer_depth(type_die, CU, depth + 1)
        if type_die.tag in (
            "DW_TAG_const_type",
            "DW_TAG_volatile_type",
            "DW_TAG_typedef",
        ):
            return self._count_pointer_depth(type_die, CU, depth + 1)
        return 0

    @staticmethod
    def _access_from_dwarf(
        val: int, default: AccessLevel = AccessLevel.PUBLIC
    ) -> AccessLevel:
        """Map DW_AT_accessibility value to AccessLevel.

        *default* is returned for an absent attribute (``val == 0``) — the
        caller passes the enclosing record's actual language default
        (private for ``class``, public for ``struct``/``union``) where that
        distinction matters (record fields); other call sites keep the
        historical unconditional-public default.
        """
        if val == 2:  # DW_ACCESS_protected
            return AccessLevel.PROTECTED
        if val == 3:  # DW_ACCESS_private
            return AccessLevel.PRIVATE
        if val == 1:  # DW_ACCESS_public
            return AccessLevel.PUBLIC
        return default  # 0 (absent)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _strip_type_decorators(type_name: str) -> str:
    """Strip pointer/reference/const/volatile/array suffixes from a type name.

    Used for reachability analysis — we need the base type name to follow
    type references transitively.
    """
    name = type_name.strip()
    # Remove trailing array brackets
    while name.endswith("[]"):
        name = name[:-2].strip()
    # Remove trailing pointer/reference markers
    while name.endswith(("*", "&", "&&")):
        if name.endswith("&&"):
            name = name[:-2].strip()
        else:
            name = name[:-1].strip()
    # Remove leading qualifiers (loop until none remain)
    _qualifiers = ("const ", "volatile ", "restrict ")
    changed = True
    while changed:
        changed = False
        for prefix in _qualifiers:
            if name.startswith(prefix):
                name = name[len(prefix) :]
                changed = True
    return name.strip()
