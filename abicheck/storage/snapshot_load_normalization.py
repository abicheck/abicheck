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

"""On-load snapshot migrations for legacy-format documents.

``serialization.snapshot_from_dict`` calls these right after a document is
parsed, before the result reaches any detector or index. Kept out of
``serialization.py`` itself (ADR-061 D1: ``storage/`` is the canonical owner
of a snapshot format's schemas and migrations; that file is debt-baselined
at its adoption ceiling, so new load-time migration logic belongs here, with
only a thin call left at the call site). ``abicheck.qualified_name_segments``
is a ``public_root_surfaces`` entry (its own docstring already frames it as
a stable, dependency-free leaf shared across detectors), the same exemption
``abicheck.serialization`` itself already uses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..model.build_mode_facts import (
    BuildMode,
    BuildModeProvenance,
    CompilerFamily,
    CxxStandard,
    GlibcxxDualAbi,
    StdlibFamily,
)
from ..model.extraction_contract import ExtractionContract
from ..model.fact import Fact
from ..name_classification import strip_anonymous_type_location
from ..qualified_name_segments import (
    _LAMBDA_IDENTITY_FIELDS,
    _lambda_identity_containers_and_strings,
    _walk_rewrite_strings,
)
from .guards import decision_key, identity_text, mapping as _mapping_guard, strict_int

if TYPE_CHECKING:
    from ..model.snapshot import AbiSnapshot


def normalize_anonymous_type_spellings_on_load(snapshot: AbiSnapshot) -> AbiSnapshot:
    """Strip a checkout-dependent path out of any closure/anonymous-type
    marker still in its raw ``(lambda at <path>:<line>:<col>)`` form,
    mutating *snapshot* in place. Returns *snapshot* unchanged (for
    chaining at the call site's own ``return``).

    A baseline written before ``strip_anonymous_type_location`` existed (or
    by a header-mode dumper build that never called it) still carries that
    raw spelling on disk -- the two header-mode dumpers only ever call it at
    extraction time, never on this load path. Without this step,
    ``qualified_name_segments.renumber_anonymous_closure_identities``'s own
    marker regex (which requires the already-stripped
    ``(lambda:<basename>:<line>:<col>)`` form) leaves such a baseline's
    closures completely unrenumbered, comparing them against a freshly
    dumped snapshot's ordinal-form spellings as if the two were unrelated
    declarations. Idempotent: ``strip_anonymous_type_location`` is a no-op
    on text with no ``" at "`` left to strip, so this is safe to call
    unconditionally on every load, including an already-normalized one.

    Known, accepted limitation shared with ``renumber_anonymous_closure_
    identities`` (Codex review; see ``docs/contribute/known-gaps.md``'s "The
    L5 source graph's own node identities are never renumbered..." entry,
    Codex review on PR #868): this only rewrites
    :data:`~abicheck.qualified_name_segments._LAMBDA_IDENTITY_FIELDS` on the
    flat snapshot. A schema-v29+ document's ``AbiSnapshot.surface_graph`` is
    decoded earlier in ``snapshot_from_dict`` (``decode_surface_graph``) and
    is not touched here, so a loaded raw-marker baseline's attached graph
    keeps its own un-stripped node/edge identities even after the flat
    fields are normalized -- the identical flat-vs-graph mismatch the
    existing entry documents for dump-time renumbering, now also reachable
    from this load-time path. Not fixed here for the same reason: closing it
    needs the graph's own node/edge strings folded into the same
    strip-and-renumber pass, verified against a case mixing flat-visible and
    graph-only closures -- a real, cross-cutting change, not a same-PR
    reactive patch.
    """
    collected = _lambda_identity_containers_and_strings(snapshot)
    if collected is None:
        return snapshot
    containers, _strings = collected
    for field_name, container in zip(_LAMBDA_IDENTITY_FIELDS, containers):
        new_container = _walk_rewrite_strings(container, strip_anonymous_type_location)
        if new_container is not container:
            setattr(snapshot, field_name, new_container)
    return snapshot


def backfill_missing_elf_binding(snap: AbiSnapshot) -> None:
    """Backfill Function/Variable.elf_binding from an already-loaded
    ``elf.symbols`` for a snapshot serialized before this field existed
    (Codex review, fresh evidence).

    A pre-this-PR snapshot's own ``elf`` block already carries the exact
    same fact ``dumper_elf_symbols._populate_elf_visibility`` reads it
    from at dump time -- only the newer per-declaration ``elf_binding``
    key was never written at serialization time, so loading it as ``None``
    (the ordinary missing-key convention) would make a fresh ``binding:``
    suppression selector fail closed against *every* already-archived
    baseline, not just genuinely-unknown-binding cases, until each one is
    regenerated -- which is not always possible for an archived release.
    Only fills a ``None``: an explicitly serialized value from a v16+
    writer is preserved untouched, never recomputed.

    Deliberately scoped to ``elf_binding`` alone -- ``elf_visibility`` has
    the identical legacy-backfill gap but predates this PR, is unrelated to
    the field this PR adds, and is left as it already was.
    """
    if snap.elf is None:
        return
    sym_map = snap.elf.symbol_map
    for func in snap.functions:
        if func.elf_binding is None:
            elf_sym = sym_map.get(func.mangled)
            if elf_sym is not None:
                func.elf_binding = elf_sym.binding
                # Plain attribute assignment never re-runs __post_init__, so
                # elf_binding_fact must be kept in sync explicitly here too
                # (ADR-063 Phase 5 -- same mutation trap already fixed at
                # dump time in dumper_elf_symbols._populate_elf_visibility).
                func.elf_binding_fact = Fact.present(elf_sym.binding)
    for var in snap.variables:
        if var.elf_binding is None:
            elf_sym = sym_map.get(var.mangled)
            if elf_sym is not None:
                var.elf_binding = elf_sym.binding
                var.elf_binding_fact = Fact.present(elf_sym.binding)


def _str_field_mapping(raw: Any, field_name: str) -> dict[str, str]:
    """A ``dict[str, str]`` extraction-contract field, rejected outright --
    not filtered down to its well-formed entries -- when the container, or
    an individual key/value pair inside it, is not already string-shaped
    (storage AGENTS.md invariant 6, ``guards``'s "reject rather than
    coerce" rule, reused here rather than restated).

    ``profile_fields``/``scope_fields`` feed ADR-050's comparability gate
    directly (``comparability.py``'s carve-out checks look values up in
    them by known keys), so two failure modes both had to close, not just
    the ``str()``-coercion one: a first fix (Codex review) stopped ``1``/
    ``"1"`` colliding onto one entry, but silently *dropping* the malformed
    pair instead of rejecting the field left a second, subtler one open
    (fresh Codex review) -- a malformed ``profile_fields``/``scope_fields``
    becomes indistinguishable from one that genuinely has fewer entries,
    so a carve-out that reads a still-present key never learns the
    document was corrupt. A field present but wrong-shaped is a malformed
    document, and this package's own convention (see ``dependency_scope``'s
    handling in ``serialization.snapshot_from_dict``) is to fail the load
    loudly rather than let a corrupt document masquerade as a smaller
    legitimate one. Only a genuinely *absent* field (the key missing, or an
    explicit ``null`` -- the same "no evidence" spelling every other
    optional field in this contract already accepts) degrades to ``{}``;
    every other shape raises.
    """
    if raw is None:
        return {}
    _mapping_guard(raw, field_name)
    result: dict[str, str] = {}
    for key, value in raw.items():
        str_key = decision_key(key, f"{field_name} key")
        result[str_key] = identity_text(value, f"{field_name}[{str_key!r}]")
    return result


def extraction_contract_from_dict(raw: Any) -> ExtractionContract | None:
    """Convert a serialized ExtractionContract dict (or None) back into the
    typed dataclass (ADR-050 D1). Returns None when the whole ``contract``
    field is missing (every snapshot predating schema v12) or not a dict.

    Raises ``TypeError`` -- does not degrade -- when a *present*
    ``profile_fields``/``scope_fields`` sub-field is the wrong shape (see
    ``_str_field_mapping``): those two feed the comparability gate
    directly, so a malformed one must fail the load rather than silently
    read as a smaller legitimate one.
    """
    if not isinstance(raw, dict):
        return None
    profile_fingerprint = raw.get("profile_fingerprint")
    scope_fingerprint = raw.get("scope_fingerprint")
    return ExtractionContract(
        profile_fingerprint=profile_fingerprint
        if isinstance(profile_fingerprint, str)
        else None,
        scope_fingerprint=scope_fingerprint
        if isinstance(scope_fingerprint, str)
        else None,
        profile_fields=_str_field_mapping(raw.get("profile_fields"), "profile_fields"),
        scope_fields=_str_field_mapping(raw.get("scope_fields"), "scope_fields"),
    )


def _enum_or(cls: type, value: Any, default: Any) -> Any:
    if value is None:
        return default
    try:
        return cls(value)
    except (ValueError, KeyError):
        return default


def build_mode_from_dict(raw: Any) -> BuildMode | None:
    """Convert a serialized BuildMode dict (or None) back into the
    typed dataclass. Returns None when the field is genuinely absent
    (missing key, or explicit ``null`` -- every snapshot predating this
    field, or a current one that never went through a build-mode-aware
    dumper). Raises ``TypeError`` for a *present* but wrong-shaped value:
    storage AGENTS.md invariant 6, the same "reject rather than coerce"
    rule ``extraction_contract_from_dict`` applies above -- a present,
    malformed ``build_mode``/``provenance``/``libcpp_abi_version`` must not
    read as "this snapshot predates the field", or ``_effective_build_mode``
    would infer weaker facts (or skip stdlib-ABI checks) from evidence that
    was never actually collected, silently, with no signal anything was
    wrong (Codex review).
    """
    if raw is None:
        return None
    _mapping_guard(raw, "build_mode")

    prov_raw = raw.get("provenance")
    if prov_raw is None:
        prov_raw = {}
    else:
        _mapping_guard(prov_raw, "build_mode.provenance")
    provenance = BuildModeProvenance(
        raw_producer=prov_raw.get("raw_producer"),
        raw_comment=prov_raw.get("raw_comment"),
        compiler_version=prov_raw.get("compiler_version"),
    )

    # libcpp_abi_version: an int passes through unchanged; anything else
    # present (a string -- even a numeric one like "1", since that is
    # exactly the 1/"1" collision invariant 6 exists to prevent -- a bool,
    # a float, a list) is rejected via strict_int rather than coerced.
    libcpp_raw = raw.get("libcpp_abi_version")
    libcpp_abi_version: int | None = (
        None
        if libcpp_raw is None
        else strict_int(libcpp_raw, "build_mode.libcpp_abi_version")
    )

    return BuildMode(
        compiler_family=_enum_or(
            CompilerFamily,
            raw.get("compiler_family"),
            CompilerFamily.UNKNOWN,
        ),
        language_std=_enum_or(
            CxxStandard,
            raw.get("language_std"),
            CxxStandard.UNKNOWN,
        ),
        stdlib=_enum_or(StdlibFamily, raw.get("stdlib"), StdlibFamily.UNKNOWN),
        glibcxx_dual_abi=_enum_or(
            GlibcxxDualAbi,
            raw.get("glibcxx_dual_abi"),
            GlibcxxDualAbi.NOT_APPLICABLE,
        ),
        libcpp_abi_version=libcpp_abi_version,
        provenance=provenance,
    )
