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

"""``_EQUIVALENT_CHANGE_CATEGORIES`` -- one logical event, several kinds.

A pure data table, lifted out of ``finding_identity.py`` (which owns the
*resolution* that reads it, not the table itself) so that module stays at
its ``architecture/debt.yaml`` no-growth baseline, and so a new entry --
which is a taxonomy edit, not an identity-algorithm change -- lands in a
file whose whole content is that taxonomy. ``finding_identity`` imports
it under its historical private name, so every reader of
``finding_identity._EQUIVALENT_CHANGE_CATEGORIES`` is unaffected.
"""

from __future__ import annotations

__all__ = ["EQUIVALENT_CHANGE_CATEGORIES"]


#: Change kinds ``diff_filtering`` already treats as one logical event
#: reported by two different detectors -- mirrored here (this leaf module
#: still doesn't import ``diff_filtering``, so the dependency direction stays
#: one-way: ``diff_filtering`` depends on this module for its
#: ``_deduplicate_cross_detector`` dedup key, ADR-049 Phase 2 wiring, not the
#: reverse). Keep in sync with the two mappings this generalizes:
#: ``_deduplicate_cross_detector``'s local ``_DEDUP_CATEGORIES``
#: (rich-vs-L0 function/variable add/remove, symbol-version-node pairs --
#: wired) and module-level ``_DWARF_TO_AST_EQUIV``'s two *whole-type* pairs
#: (``STRUCT_SIZE_CHANGED``/``TYPE_SIZE_CHANGED``,
#: ``STRUCT_ALIGNMENT_CHANGED``/``TYPE_ALIGNMENT_CHANGED``) -- safe to
#: collapse because ``symbol`` names the whole type on both sides, with no
#: field-level substructure to lose.
#:
#: Deliberately excludes ``_DWARF_TO_AST_EQUIV``'s three *field-level*
#: pairs (``STRUCT_FIELD_OFFSET_CHANGED``/``STRUCT_FIELD_REMOVED``/
#: ``STRUCT_FIELD_TYPE_CHANGED`` vs. their ``TYPE_FIELD_*`` counterparts):
#: the DWARF side field-qualifies ``symbol`` (``"Point::x"``,
#: ``diff_platform.py``), but the AST side does not (``symbol="Point"``,
#: the field name only in ``description`` via ``detail=fname``,
#: ``diff_types.py``) -- collapsing these to a bare category would make
#: two *different* AST-side field findings on the same struct (``Point::x``
#: vs. ``Point::y``) collide with each other, not just with their DWARF
#: counterpart (Codex review: caught exactly this for
#: ``TYPE_FIELD_OFFSET_CHANGED``). Safely fixing this needs a real
#: per-field discriminator this module doesn't
#: have a reliable source for (the two detectors don't encode field
#: identity in a common field) -- left as the conservative default
#: (full kind/old/new/description discriminator, no cross-detector
#: collision for these three kinds) rather than a fix that risks losing a
#: distinct field-level fact, matching this module's ambiguity-safe bias.
#:
#: The three enum entries (self-mapped: same kind on both sides, not a
#: kind pair) mirror ``diff_filtering._dedup_enum_same_kind``'s own
#: ``(kind, symbol)`` dedup key exactly (Codex review): its AST detector
#: (``diff_types.py``) uses the registry description template with no
#: embedded values, while its DWARF detector (``diff_platform.py``) passes
#: a bespoke ``description`` embedding ``"({old} → {new})"`` -- the same
#: "one logical event, two detectors' own wording" shape as the kind-pair
#: entries above, just sharing one kind slug instead of two. Both
#: detectors populate ``old_value``/``new_value`` identically
#: (``str(old_val)``/``str(new_val)``), so only ``description`` actually
#: needed dropping, but collapsing the full discriminator (like every
#: other entry here) matches ``_dedup_enum_same_kind``'s own key precisely
#: rather than assuming every current and future producer of these kinds
#: populates old/new consistently.
EQUIVALENT_CHANGE_CATEGORIES = {
    "func_removed": "func_removal",
    "func_removed_elf_only": "func_removal",
    # ADR-069 follow-up: the same underlying event as func_removed/
    # var_removed, observed with one more fact -- the declaration is still
    # in the headers, only the export went away. Mapped onto the same
    # category so a second detector reporting the plain removal of that
    # very symbol dedupes against it rather than double-reporting.
    "func_export_removed_still_declared": "func_removal",
    "var_export_removed_still_declared": "var_removal",
    "func_added": "func_addition",
    "var_removed": "var_removal",
    "var_added": "var_addition",
    "symbol_version_node_removed": "version_def_removal",
    "symbol_version_defined_removed": "version_def_removal",
    "struct_size_changed": "type_size_change",
    "type_size_changed": "type_size_change",
    "struct_alignment_changed": "type_alignment_change",
    "type_alignment_changed": "type_alignment_change",
    "enum_member_value_changed": "enum_member_value_changed",
    "enum_member_removed": "enum_member_removed",
    "enum_last_member_value_changed": "enum_last_member_value_changed",
    # diff_types._diff_enums (L2 header tier, bare EnumType.name) and
    # diff_platform._diff_enum_layouts (L1 DWARF tier, fully-qualified DWARF
    # dict key) both emit this same kind for the same enum's underlying-type
    # size change — self-mapped so its discriminator collapses across
    # producers the same way the three sibling enum-member kinds above
    # already do; see diff_filtering._deduplicate_cross_detector's own
    # docstring for the qualified-name bridge this also depends on.
    "enum_underlying_size_changed": "enum_underlying_size_changed",
    # diff_symbols._detect_newly_deleted_functions emits FUNC_DELETED
    # (castxml is_deleted attribute) or FUNC_DELETED_DWARF (DWARF
    # DW_AT_deleted) for the same symbol/callable->deleted transition --
    # which kind you get depends only on which evidence source (header
    # analysis vs. binary DWARF) observed the deletion, the same
    # evidence-tier-producer variance every other pair in this map
    # normalizes (Codex review).
    "func_deleted": "func_deletion",
    "func_deleted_dwarf": "func_deletion",
}
