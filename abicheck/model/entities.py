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

"""User-defined types: records (struct/class/union) and enumerations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from .fact import Fact, _Omitted, bridge_legacy_and_fact
from .identity import EntityId
from .vocabulary import AccessLevel, ScopeOrigin

# ADR-063 Phase 0: private omission sentinels for RecordType's Fact[T]-backed
# fields — see model/fact.py's _Omitted/bridge_legacy_and_fact docstrings.
# cast() to the field's own real type so the declared type never widens;
# the list-typed sentinels are routed through a default_factory returning
# this same singleton (a *direct* mutable-typed dataclass default is
# rejected outright by `dataclasses`), never a fresh object per instance.
_OMITTED_BASES: list[str] = cast("list[str]", _Omitted())
_OMITTED_VIRTUAL_BASES: list[str] = cast("list[str]", _Omitted())
_OMITTED_VTABLE: list[str] = cast("list[str]", _Omitted())
_OMITTED_VPTR_OFFSET_BITS: int | None = cast("int | None", _Omitted())


@dataclass
class TypeField:
    name: str
    type: str
    offset_bits: int | None = None
    is_bitfield: bool = False
    bitfield_bits: int | None = None
    is_const: bool = False
    is_volatile: bool = False
    is_mutable: bool = False
    access: AccessLevel = AccessLevel.PUBLIC
    # Default member initializer expression, verbatim (value not evaluated).
    # None = no initializer, or the dumper does not capture this (older
    # snapshots / non-castxml producers). As with Function.deprecated, None
    # is not unambiguously "unsupported" here — a real "gained/lost
    # initializer" transition has one side genuinely None by construction —
    # so the detector gates on header-tier confirmation at the *snapshot*
    # level (mirroring Param.default/param_defaults) rather than skipping
    # per-pair on either side being None.
    default: str | None = None
    # See Function.deprecated for the message-string convention.
    deprecated: str | None = None


@dataclass
class RecordType:
    """struct / class / union."""

    name: str
    kind: str  # "struct" | "class" | "union"
    size_bits: int | None = None
    alignment_bits: int | None = None
    fields: list[TypeField] = field(default_factory=list)
    # ADR-063 Phase 0: bases/virtual_bases/vtable default to a private
    # omission sentinel (identity-compared in __post_init__), not a plain
    # empty list — an omitted field and an explicitly-confirmed-empty one
    # must backfill their *_fact sibling differently (not_collected() vs.
    # present([])). See bases_fact/virtual_bases_fact/vtable_fact below.
    bases: list[str] = field(default_factory=lambda: _OMITTED_BASES)  # base class names
    virtual_bases: list[str] = field(default_factory=lambda: _OMITTED_VIRTUAL_BASES)
    vtable: list[str] = field(
        default_factory=lambda: _OMITTED_VTABLE
    )  # ordered vtable entries (mangled)
    source_location: str | None = None
    is_union: bool = False
    is_opaque: bool = (
        False  # incomplete type (forward-decl only; was complete → BREAKING)
    )
    # `final` class-key specifier. Tri-state to keep "unknown" distinct from
    # "not final":
    # - True  → declared `class C final { ... }` (castxml `final` attribute).
    # - False → declared without `final`.
    # - None  → dumper/loader could not determine (DWARF/symbols-only mode,
    #           which carries no `final` information; older snapshots). The
    #           diff skips the finality detector when either side is None to
    #           avoid false findings from schema evolution / tier downgrade.
    is_final: bool | None = None
    # ADR-063 Phase 5 (D7's first registered conversion): Fact[bool]
    # sibling. Unlike bases/virtual_bases/vtable/vptr_offset_bits above,
    # is_final needs no private omission sentinel and no snapshot-level
    # reliability flag — its own None already unambiguously means
    # "dumper/loader could not determine" (there is no separate
    # "confirmed no evidence" state distinct from the field simply being
    # unset), so __post_init__ bridges directly off the literal None.
    is_final_fact: Fact[bool | None] | None = field(default=None, kw_only=True)
    # True when this RecordType is a class/struct template's own pattern body
    # (e.g. the clang header backend's CXXRecordDecl nested inside a
    # ClassTemplateDecl) rather than a concrete, instantiable type. Its field
    # *names*/*types* are still real public surface, but it has no fixed
    # layout for any one instantiation — detectors that need real
    # size/offset data (e.g. DWARF layout backfill's name-based matching)
    # must not treat it as an ordinary type. False for every non-clang
    # producer (castxml/DWARF never emit an uninstantiated pattern this way).
    is_template_pattern: bool = False
    # True when *every* entry in `fields` was flattened up from an anonymous
    # struct/union member by the clang header backend (clang emits an
    # IndirectFieldDecl for each such member; see dumper_clang.py) -- not
    # merely "at least one was" (Codex review): a mixed record like
    # `struct Foo { union { int i; }; int tag; };` has an ordinary field
    # (`tag`) with no such provenance guarantee, so the flag must be False
    # for it too. DWARF's own record builder (dwarf_snapshot.py) now flattens
    # *supported* anonymous aggregates too, but an unsupported producer/shape
    # or a cached snapshot predating that flatten still legitimately leaves
    # an all-anonymous record's DWARF view fieldless even though it carries
    # the real size_bits — a structural signal the DWARF layout backfill
    # needs to trust a
    # bare-suffix (namespaced) match for this case without also trusting an
    # ordinary record's coincidental match to an unrelated, fieldless type
    # reached the same way. False for every non-clang producer (castxml
    # computes real layout itself and is never backfilled; DWARF-only
    # snapshots have no header view to flatten).
    has_anonymous_aggregate_fields: bool = False
    # Provenance (ADR-015, schema v6) — see Function.source_header.
    source_header: str | None = None
    origin: ScopeOrigin = ScopeOrigin.UNKNOWN
    # ── Fine-grained layout descriptor (layout-closure work) ─────────────────
    # All tri-state / optional so "unknown" (DWARF-only or symbols-only dumps,
    # older snapshots) stays distinct from a real value; the layout detectors
    # skip a comparison whenever either side is None/empty, avoiding false
    # findings from schema evolution or an evidence-tier downgrade.
    #
    # Itanium "data size" (a.k.a. dsize/nvsize): the size occupied by the
    # object's own members *excluding* trailing tail padding. A derived class
    # may reuse a base's tail padding, so a change here can shift a derived
    # layout even when ``size_bits`` (the padded sizeof) is unchanged.
    data_size_bits: int | None = None
    # C++ type traits that govern tail-padding reuse and how the type is passed
    # by value (in registers vs. on the stack / via hidden reference).
    is_standard_layout: bool | None = None
    is_trivially_copyable: bool | None = None
    # Bit offset of the vtable pointer within the object (0 for a simple
    # polymorphic class; nonzero with virtual bases). None when the type is
    # non-polymorphic or the dumper could not determine it. Introducing the
    # first virtual function makes this go from None → 0 and shifts every field.
    # ADR-063 Phase 0: defaults to a private omission sentinel, not the
    # literal `None` — `None` is already a real, meaningful value here
    # ("no vptr observed"), so `RecordType()` (omitted) and
    # `RecordType(vptr_offset_bits=None)` (explicit: confirmed no vptr)
    # must backfill vptr_offset_bits_fact differently. See __post_init__.
    vptr_offset_bits: int | None = _OMITTED_VPTR_OFFSET_BITS
    # Base-class subobject offsets: base name → bit offset within this object.
    # Distinct from ``bases`` (declaration order only): a base can *move* (e.g.
    # an empty-base-optimization is lost, or a member is inserted ahead of it)
    # without the name list reordering. Empty when unknown.
    base_offsets: dict[str, int] = field(default_factory=dict)
    # Namespace/enclosing-class-qualified spelling (e.g. "mylib::detail::Impl"),
    # set only when it differs from the bare ``name`` above. ``name`` itself
    # stays bare (matching the DWARF backend, which has no cheaper way to
    # qualify a struct name) so type-map lookups and DWARF/header merges keep
    # matching by the same key across both backends; this field exists solely
    # for namespace-aware checks (internal-leak detection, SYCL-queue param
    # matching) that need to see the real namespace path. None when the type
    # is at global scope or the dumper couldn't determine it (e.g. DWARF-only).
    qualified_name: str | None = None
    # Whether the class/struct declares at least one pure virtual function
    # (making it abstract — cannot be instantiated). Tri-state like
    # ``is_final``: True/False = captured (castxml's `abstract` attribute;
    # clang's `definitionData.isAbstract` since G31 Phase C); None =
    # dumper/loader could not determine (DWARF/symbols-only mode, older
    # snapshots). The diff skips comparison when either side is None.
    is_abstract: bool | None = None
    # See Function.deprecated for the message-string convention.
    deprecated: str | None = None

    # ── ADR-063 Phase 0: Fact[T] siblings for the fields AGENTS.md's
    # "Known gaps" names as actively causing fabricated findings from
    # absent evidence (type_vtable_changed, the accepted type_base_changed
    # gap). Default None means "caller supplied neither form" — distinct
    # from Fact.not_collected(), which is an explicit claim; __post_init__
    # backfills from whichever of the legacy field / Fact sibling the
    # caller actually supplied (see model/fact.py's bridge_legacy_and_fact).
    # A detector reads these, never the plain bases/virtual_bases/vtable/
    # vptr_offset_bits fields above, which stay for one release only for
    # asdict()-based external-consumer compatibility (kept in sync with
    # the Fact sibling at every construction site, never independently
    # assigned again after this phase).
    bases_fact: Fact[list[str]] | None = field(default=None, kw_only=True)
    virtual_bases_fact: Fact[list[str]] | None = field(default=None, kw_only=True)
    vtable_fact: Fact[list[str]] | None = field(default=None, kw_only=True)
    vptr_offset_bits_fact: Fact[int | None] | None = field(default=None, kw_only=True)

    # ── ADR-063 Phase 2 (third slice): the resolved identity carrier ────────
    # The plan's Phase 2 open design question ("option (a) a real carrier
    # field vs. option (b) defer every post-parse consumer to Phase 6") is
    # resolved as option (a): `EntityId` is computed ONCE, at parse time --
    # the only point the typed `ScopePath` exists at all -- and carried
    # forward here. See docs/contribute/plans/one-semantic-pipeline.md's
    # Phase 2 section for the full decision and its consequences.
    #
    # Four properties of this field are deliberate, not incidental:
    #
    # * `None` is a real, honest value: "no producer supplied one". Every
    #   external caller constructing a `RecordType` directly (this is a
    #   public API dataclass) gets `None` rather than a fabricated identity
    #   -- inventing one from the flattened `name`/`qualified_name` is
    #   exactly the structurally-insufficient reconstruction the plan found
    #   cannot work.
    # * `kw_only=True`, appended last: a public, non-keyword-only dataclass
    #   cannot take a positional insertion without silently rebinding an
    #   existing caller's arguments (the same reasoning
    #   `Function.hidden_friend_owner` already records).
    # * `compare=False`: identity is *derived* from the declaration, so
    #   folding it into `__eq__` would make two otherwise-identical model
    #   objects compare unequal purely on whether a producer wired it --
    #   the same identity-vs-payload discipline `identity.Record.access`
    #   already applies one level down.
    # * **Persisted (schema v28), but not yet readable.** `serialization.
    #   snapshot_to_dict`/`snapshot_from_dict` round-trip it through
    #   `storage/entity_id_codec.py`'s bridge onto `storage/entity_ids.py`'s
    #   `ScopePath`-preserving wire-schema-v2 `EntityId` encoding -- a
    #   reloaded snapshot's declarations carry the identical `entity_id` an
    #   in-memory one does (or `None`, honestly, when nothing resolved one).
    #   **No diff/report consumer may read this field yet regardless**: the
    #   `finding_identity.py` algorithm migration and the post-parse
    #   consumer migrations (ADR-063 Phase 2's remaining items) haven't
    #   landed, so nothing yet gives this field a defined meaning to a
    #   comparison -- reading it early would be acting on a value nothing
    #   has committed to the semantics of.
    entity_id: EntityId | None = field(default=None, kw_only=True, compare=False)

    def __post_init__(self) -> None:
        self.bases, self.bases_fact = bridge_legacy_and_fact(
            self.bases, self.bases_fact, _OMITTED_BASES, []
        )
        self.virtual_bases, self.virtual_bases_fact = bridge_legacy_and_fact(
            self.virtual_bases, self.virtual_bases_fact, _OMITTED_VIRTUAL_BASES, []
        )
        self.vtable, self.vtable_fact = bridge_legacy_and_fact(
            self.vtable, self.vtable_fact, _OMITTED_VTABLE, []
        )
        self.vptr_offset_bits, self.vptr_offset_bits_fact = bridge_legacy_and_fact(
            self.vptr_offset_bits,
            self.vptr_offset_bits_fact,
            _OMITTED_VPTR_OFFSET_BITS,
            None,
        )
        # `None` itself is the omission marker here — no private sentinel
        # needed, since a caller-supplied `is_final=None` and an omitted
        # `is_final` mean the identical thing (see the field's own comment).
        self.is_final, self.is_final_fact = bridge_legacy_and_fact(
            self.is_final, self.is_final_fact, None, None
        )


@dataclass
class EnumMember:
    name: str
    value: int


@dataclass
class EnumType:
    name: str
    members: list[EnumMember] = field(default_factory=list)
    underlying_type: str = "int"
    source_location: str | None = None
    # Provenance (ADR-015, schema v6) — see Function.source_header.
    source_header: str | None = None
    origin: ScopeOrigin = ScopeOrigin.UNKNOWN
    # `enum class` / `enum struct` (C++11 scoped enumeration) versus a plain
    # C-style enum. Tri-state like RecordType.is_final: True/False = captured
    # (castxml's `scoped` attribute); None = dumper/loader could not
    # determine (DWARF/symbols-only mode, older snapshots, non-castxml
    # header producers). The diff skips comparison when either side is None.
    is_scoped: bool | None = None
    # See Function.deprecated for the message-string convention.
    deprecated: str | None = None
    # Namespace/enclosing-class-qualified spelling, mirroring
    # ``RecordType.qualified_name`` (same bare-``name``-collision motivation:
    # PR #608 follow-up). ``name`` stays bare for the same DWARF-parity and
    # type-map-key reasons documented on ``RecordType.qualified_name``. None
    # when the enum is at global scope or the dumper couldn't determine it.
    qualified_name: str | None = None
    # ADR-063 Phase 2 identity carrier (persisted since schema v28) -- see
    # RecordType.entity_id above for the full rationale, including why this
    # is keyword-only, excluded from equality, and not yet readable by any
    # consumer.
    entity_id: EntityId | None = field(default=None, kw_only=True, compare=False)


def record_layout_facts(
    bases: list[str],
    virtual_bases: list[str],
    vtable: list[str],
    vptr_offset_bits: int | None,
) -> dict[str, Any]:
    """``Fact.present(...)`` for all four ``RecordType`` layout fields at once.

    ADR-063 Phase 0: a header-AST producer (castxml, direct-clang) that
    resolves these itself via real parse-time analysis states so
    explicitly, rather than leaving the omission bridge to infer
    ``Fact.present(...)`` from "a legacy value was supplied" — spread the
    result into the ``RecordType(...)`` call alongside the matching legacy
    keyword arguments, e.g. ``RecordType(bases=bases, vtable=vtable, ...,
    **record_layout_facts(bases, virtual_bases, vtable, vptr_offset_bits))``.
    Not used for DWARF's ``vptr_offset_bits``, which a post-construction
    fixed-point pass may still revise — see :func:`resolve_vptr_offset_bits`.
    """
    return {
        "bases_fact": Fact.present(bases),
        "virtual_bases_fact": Fact.present(virtual_bases),
        "vtable_fact": Fact.present(vtable),
        "vptr_offset_bits_fact": Fact.present(vptr_offset_bits),
    }


def resolve_vptr_offset_bits(rec: RecordType, value: int) -> None:
    """Set ``vptr_offset_bits`` AND its ``Fact[T]`` sibling together.

    ADR-063 Phase 0: a caller resolving a class's vptr offset *after* the
    ``RecordType`` was already constructed (a post-construction fixed-point
    pass, a DWARF-corroboration backfill) must update both representations
    together — ``__post_init__`` already backfilled ``vptr_offset_bits_fact``
    from the pre-resolution state (typically ``Fact.not_collected()``, since
    ``None`` is what put the record on such a pass's worklist in the first
    place), and leaving it stale while only the legacy scalar moves silently
    loses exactly the fact this bridge exists to make visible.
    """
    rec.vptr_offset_bits = value
    rec.vptr_offset_bits_fact = Fact.present(value)
