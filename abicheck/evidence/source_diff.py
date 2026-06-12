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

"""Source ABI replay diff and findings (ADR-030 D6, D10).

Compares two linked :class:`SourceAbiSurface` objects and classifies
source/API drift that final binary/debug artifacts under-represent: macro
constants, default arguments, inline/template bodies, constexpr values,
uninstantiated templates, source/symbol mapping loss, ODR conflicts, and
generated-header changes.

Per ADR-028 D3 / ADR-030 D6 these findings are never ``BREAKING`` on their own
(the partition is fixed in ``change_registry.py``: each is ``API_BREAK`` or
``RISK``). Each finding's ``source_location`` carries the explicit L4 evidence
boundary (ADR-030 D10) so a source/API risk is never read as a proven shipped
binary break.
"""

from __future__ import annotations

import re

from ..checker_policy import ChangeKind
from ..checker_types import Change
from .source_abi import EVIDENCE_TIER_L4, SourceAbiSurface, SourceEntity


def _header_basename(path: str) -> str:
    """Build-root-stable form of a declaring-header path for cross-surface keys.

    The linker keys ODR conflicts by ``(qualified_name, declaring_header)`` where
    the header is an absolute ``source_location.path``. When the old and new
    packs are produced from different checkout/build roots
    (``/old/include/api.h`` vs ``/new/include/api.h``), comparing those raw paths
    makes a *pre-existing* conflict look new. Reduce the path to its basename
    (splitting on both separators so a Windows path is handled off-Windows) so
    the discriminator stays stable across roots while still telling
    ``a.h``/``b.h`` apart — the disambiguation the header key exists for. This
    mirrors the build-root independence of ``SourceEntity.identity()``; the rare
    same-basename-different-directory collision is an accepted L4 limitation
    (ADR-030 D6; L4 is never sole BREAKING authority).
    """
    return re.split(r"[\\/]", path)[-1] if path else ""


def _loc(entity: SourceEntity) -> str:
    """Render a source location stamped with the L4 evidence-tier boundary (D10)."""
    base = ""
    if entity.source_location and entity.source_location.path:
        base = entity.source_location.path
        if entity.source_location.line:
            base += f":{entity.source_location.line}"
    return f"{base} [{EVIDENCE_TIER_L4}]" if base else f"[{EVIDENCE_TIER_L4}]"


#: Entity kinds that can carry a default argument (so a ``value`` change is a
#: default-argument change, not a variable-initializer or constant change).
_FUNCTION_KINDS = frozenset({"function", "method"})


def _by_identity(entities: list[SourceEntity]) -> dict[str, SourceEntity]:
    """Index entities by stable identity so C++ overloads stay distinct.

    Keying by ``qualified_name`` alone would collapse overloads (``f(int)`` and
    ``f(double)`` share a name), dropping all but the last and risking comparing
    two different overloads across versions. ``SourceEntity.identity()`` keys by
    mangled name when present, falling back to ``qualified_name#signature_hash``
    (or the bare qualified name when there is no signature), so each overload —
    including unmangled ones like castxml's constructors — is matched to its own
    counterpart.

    When two entities share one identity — clang emits both the in-class
    *declaration* (which carries the default argument) and the out-of-line inline
    *definition* (no default) of the same constructor/method — prefer the one
    that carries the richer value/body/type information, so a default-argument
    change on an inline-defined member is not masked by the value-less definition
    overwriting it (Codex review #339, P2).
    """
    out: dict[str, SourceEntity] = {}
    for e in entities:
        key = e.identity()
        if not key:
            continue
        prev = out.get(key)
        if prev is None or _richer(e, prev):
            out[key] = e
    return out


def _richer(candidate: SourceEntity, current: SourceEntity) -> bool:
    """Whether ``candidate`` carries more comparable detail than ``current``.

    Breaks identity collisions toward the entity that actually bears the
    value/body/type fingerprint the diff compares, rather than keeping the last
    one seen.
    """
    def _score(e: SourceEntity) -> int:
        return sum(bool(v) for v in (e.value, e.body_hash, e.type_hash))

    return _score(candidate) > _score(current)


def diff_source_abi(old: SourceAbiSurface, new: SourceAbiSurface) -> list[Change]:
    """Return source-replay findings for the old→new source-surface transition.

    The result is an ordinary list of :class:`Change` objects ready to fold into
    a ``DiffResult`` and run through the existing verdict/policy pipeline.
    """
    changes: list[Change] = []
    changes.extend(_diff_generated(old, new))
    changes.extend(_diff_typedefs(old, new))
    changes.extend(_diff_macros(old, new))
    changes.extend(_diff_declarations(old, new))
    changes.extend(_diff_inline_bodies(old, new))
    changes.extend(_diff_templates(old, new))
    changes.extend(_diff_mappings(old, new))
    changes.extend(_diff_odr(old, new))
    return changes


# -- generated headers -------------------------------------------------------


def _diff_generated(old: SourceAbiSurface, new: SourceAbiSurface) -> list[Change]:
    """Flag any generated public entity whose content changed (ADR-030 D6).

    Generated declarations land in ``reachable_declarations`` while generated
    public *types* (record/enum/typedef) land in ``reachable_types``; scanning
    both buckets here ensures a generated-config-header change is not silently
    missed for either. Handled before the per-bucket diffs so a generated entity
    is reported once, as ``generated_header_changed`` rather than (e.g.) a
    constexpr/default-arg change.

    Both *content changes* (entity in both surfaces, differing) and *removals*
    (a generated public entity present only in the old surface) are reported:
    the normal declaration diff intentionally skips generated entities and there
    is no removal diff for ``reachable_types``, so without the removal pass a
    generated config header dropping a public record/enum/typedef/decl would
    produce no L4 finding at all.
    """
    changes: list[Change] = []
    for old_bucket, new_bucket in (
        (old.reachable_declarations, new.reachable_declarations),
        (old.reachable_types, new.reachable_types),
    ):
        old_b = _by_identity(old_bucket)
        new_b = _by_identity(new_bucket)
        for key in sorted(set(old_b) & set(new_b)):
            ov, nv = old_b[key], new_b[key]
            if _is_generated(nv) and _entity_changed(ov, nv):
                name = nv.qualified_name
                changes.append(
                    Change(
                        kind=ChangeKind.GENERATED_HEADER_CHANGED,
                        symbol=name,
                        description=(
                            f"Generated public {nv.kind} {name!r} changed; the "
                            "generated header content differs between versions. "
                            "Verify the generated configuration is intended."
                        ),
                        old_value=ov.value or ov.type_hash or ov.signature_hash,
                        new_value=nv.value or nv.type_hash or nv.signature_hash,
                        source_location=_loc(nv),
                    )
                )
        for key in sorted(set(old_b) - set(new_b)):
            ov = old_b[key]
            if _is_generated(ov):
                name = ov.qualified_name
                changes.append(
                    Change(
                        kind=ChangeKind.GENERATED_HEADER_CHANGED,
                        symbol=name,
                        description=(
                            f"Generated public {ov.kind} {name!r} was removed; the "
                            "generated header no longer emits it. Verify the "
                            "generated configuration is intended."
                        ),
                        old_value=ov.value or ov.type_hash or ov.signature_hash,
                        new_value="",
                        source_location=_loc(ov),
                    )
                )
    return changes


# -- typedefs / aliases ------------------------------------------------------


def _diff_typedefs(old: SourceAbiSurface, new: SourceAbiSurface) -> list[Change]:
    """Flag a public typedef/alias whose underlying type changed (ADR-030 D6).

    Typedef entities ride in ``reachable_types`` (kind ``typedef``); a bare
    typedef has no exported symbol, so a target change is otherwise invisible to
    artifact comparison. Generated typedefs are reported as
    ``generated_header_changed`` by ``_diff_generated`` and skipped here so they
    are not double-counted.
    """
    old_t = {e.identity(): e for e in old.reachable_types if e.kind == "typedef"}
    new_t = {e.identity(): e for e in new.reachable_types if e.kind == "typedef"}
    changes: list[Change] = []
    for key in sorted(set(old_t) & set(new_t)):
        ov, nv = old_t[key], new_t[key]
        if _is_generated(nv):
            continue
        if ov.type_hash != nv.type_hash:
            name = nv.qualified_name
            changes.append(
                Change(
                    kind=ChangeKind.PUBLIC_TYPEDEF_TARGET_CHANGED,
                    symbol=name,
                    description=(
                        f"Public typedef {name!r} now resolves to a different "
                        f"underlying type: {ov.value!r} -> {nv.value!r}. Source "
                        "relying on the old aliased type may change meaning or "
                        "fail to compile; recompile consumers against the new "
                        "headers."
                    ),
                    old_value=ov.value or ov.type_hash,
                    new_value=nv.value or nv.type_hash,
                    source_location=_loc(nv),
                )
            )
    return changes


# -- macros ------------------------------------------------------------------


def _diff_macros(old: SourceAbiSurface, new: SourceAbiSurface) -> list[Change]:
    old_m = _by_identity(old.reachable_macros)
    new_m = _by_identity(new.reachable_macros)
    changes: list[Change] = []
    for key in sorted(set(old_m) & set(new_m)):
        ov, nv = old_m[key], new_m[key]
        name = nv.qualified_name
        if ov.value != nv.value:
            changes.append(
                Change(
                    kind=ChangeKind.PUBLIC_MACRO_VALUE_CHANGED,
                    symbol=name,
                    description=(
                        f"Public macro {name!r} value changed: "
                        f"{ov.value!r} -> {nv.value!r}. Consumers that baked in "
                        "the old value must be recompiled against the new headers."
                    ),
                    old_value=ov.value,
                    new_value=nv.value,
                    source_location=_loc(nv),
                )
            )
    return changes


# -- declarations: default args, constexpr, generated headers ----------------


def _diff_declarations(old: SourceAbiSurface, new: SourceAbiSurface) -> list[Change]:
    old_d = _by_identity(old.reachable_declarations)
    new_d = _by_identity(new.reachable_declarations)
    changes: list[Change] = []
    for key in sorted(set(old_d) & set(new_d)):
        ov, nv = old_d[key], new_d[key]
        name = nv.qualified_name

        # Generated entities are reported as generated_header_changed by
        # _diff_generated (which also covers the reachable_types bucket); skip
        # them here so they are not double-reported as a constexpr/default-arg
        # change.
        if _is_generated(nv):
            continue

        if nv.kind == "constexpr":
            if ov.value != nv.value:
                changes.append(
                    Change(
                        kind=ChangeKind.CONSTEXPR_VALUE_CHANGED,
                        symbol=name,
                        description=(
                            f"Public constexpr {name!r} value changed: "
                            f"{ov.value!r} -> {nv.value!r}. Consumers that baked "
                            "in the old value must be recompiled."
                        ),
                        old_value=ov.value,
                        new_value=nv.value,
                        source_location=_loc(nv),
                    )
                )
            continue

        # Default-argument change: same type signature, different normalized
        # default-argument string (ADR-030 D6). Restricted to function/method
        # entities: a non-function decl (notably a `variable`) carries an empty
        # signature_hash on both sides and a `value` (its initializer), which
        # would otherwise spuriously fire default_argument_changed even though it
        # has no default argument.
        if (
            nv.kind in _FUNCTION_KINDS
            and ov.signature_hash == nv.signature_hash
            and ov.value != nv.value
        ):
            changes.append(
                Change(
                    kind=ChangeKind.DEFAULT_ARGUMENT_CHANGED,
                    symbol=name,
                    description=(
                        f"Default argument of {name!r} changed: "
                        f"{ov.value!r} -> {nv.value!r}. Newly compiled callers "
                        "that omit the argument get a different value."
                    ),
                    old_value=ov.value,
                    new_value=nv.value,
                    source_location=_loc(nv),
                )
            )
    return changes


# -- inline bodies -----------------------------------------------------------


def _diff_inline_bodies(old: SourceAbiSurface, new: SourceAbiSurface) -> list[Change]:
    old_i = _by_identity(old.reachable_inline_bodies)
    new_i = _by_identity(new.reachable_inline_bodies)
    changes: list[Change] = []
    for key in sorted(set(old_i) & set(new_i)):
        ov, nv = old_i[key], new_i[key]
        name = nv.qualified_name
        if ov.body_hash != nv.body_hash:
            changes.append(
                Change(
                    kind=ChangeKind.INLINE_BODY_CHANGED,
                    symbol=name,
                    description=(
                        f"Public inline function {name!r} body changed with no "
                        "exported symbol change. Callers that inlined the old "
                        "body keep it until recompiled — a mixed-build/ODR risk."
                    ),
                    old_value=ov.body_hash,
                    new_value=nv.body_hash,
                    source_location=_loc(nv),
                )
            )
    return changes


# -- templates ---------------------------------------------------------------


def _diff_templates(old: SourceAbiSurface, new: SourceAbiSurface) -> list[Change]:
    old_t = _by_identity(old.reachable_templates)
    new_t = _by_identity(new.reachable_templates)
    changes: list[Change] = []
    for key in sorted(set(old_t) - set(new_t)):
        ov = old_t[key]
        name = ov.qualified_name
        changes.append(
            Change(
                kind=ChangeKind.UNINSTANTIATED_TEMPLATE_REMOVED,
                symbol=name,
                description=(
                    f"Public template {name!r} was removed without any binary "
                    "presence. Source that instantiates it no longer compiles."
                ),
                old_value=ov.body_hash or ov.signature_hash,
                source_location=_loc(ov),
            )
        )
    for key in sorted(set(old_t) & set(new_t)):
        ov, nv = old_t[key], new_t[key]
        name = nv.qualified_name
        if ov.body_hash != nv.body_hash:
            changes.append(
                Change(
                    kind=ChangeKind.TEMPLATE_BODY_CHANGED,
                    symbol=name,
                    description=(
                        f"Uninstantiated public template {name!r} implementation "
                        "changed. Invisible to artifact comparison; consumers pick "
                        "up the new body on recompile."
                    ),
                    old_value=ov.body_hash,
                    new_value=nv.body_hash,
                    source_location=_loc(nv),
                )
            )
    return changes


# -- source/symbol mapping ----------------------------------------------------


def _diff_mappings(old: SourceAbiSurface, new: SourceAbiSurface) -> list[Change]:
    old_map = old.mappings.get("source_decl_to_binary_symbol", {})
    new_map = new.mappings.get("source_decl_to_binary_symbol", {})
    new_exports = set(new.roots.get("exported_symbols", []))
    # The set of symbols still provided by *some* new declaration. Reconciling
    # by symbol (not by decl identity) keeps this diff correct across differing
    # build roots: an unmangled decl's identity can change between old and new
    # checkouts even when the API and its exported symbol are unchanged, so a
    # disappeared identity key alone must not imply a lost mapping.
    new_mapped_symbols = {sym for sym in new_map.values() if sym}
    changes: list[Change] = []

    def _emit(name: str, old_sym: str) -> None:
        changes.append(
            Change(
                kind=ChangeKind.SOURCE_DECL_BINARY_SYMBOL_MISMATCH,
                symbol=name,
                description=(
                    f"Public declaration {name!r} no longer maps to an exported "
                    "symbol. If the export was removed, the artifact diff (L0) "
                    "emits the authoritative breaking finding."
                ),
                old_value=old_sym,
                new_value="",
                source_location=f"[{EVIDENCE_TIER_L4}]",
            )
        )

    # Declaration still present (same identity) but its mapping was lost
    # (declared, not exported) — and no other new decl picked the symbol up.
    for name in sorted(set(old_map) & set(new_map)):
        old_sym = str(old_map.get(name) or "")
        if old_sym and not bool(new_map.get(name)) and old_sym not in new_mapped_symbols:
            _emit(name, old_sym)

    # Declaration's identity gone from the new surface while its symbol is still
    # exported (stale export): L0 sees no removed symbol, so without this the
    # source/API regression would be missed. Guarded by ``not in
    # new_mapped_symbols`` so a decl that merely changed identity (e.g. a
    # build-root path shift) but still provides the symbol is not falsely
    # flagged.
    for name in sorted(set(old_map) - set(new_map)):
        sym = old_map.get(name) or ""
        if sym and sym in new_exports and sym not in new_mapped_symbols:
            _emit(name, sym)
    return changes


# -- ODR conflicts -----------------------------------------------------------


def _diff_odr(old: SourceAbiSurface, new: SourceAbiSurface) -> list[Change]:
    """Flag ODR conflicts newly introduced on the new side (D6).

    Keyed by ``(qualified_name, header_basename)`` — the same discriminator the
    linker uses, but with the header reduced to a build-root-stable basename
    (see ``_header_basename``) so a pre-existing conflict whose header path only
    differs by checkout/build root is not re-reported as new, while a genuine new
    conflict for a same-named type in a *different* header is still surfaced.
    """
    old_keys = {
        (c.get("qualified_name", ""), _header_basename(str(c.get("header", ""))))
        for c in old.odr_conflicts
    }
    changes: list[Change] = []
    for conflict in new.odr_conflicts:
        name = conflict.get("qualified_name", "")
        key = (name, _header_basename(str(conflict.get("header", ""))))
        if name and key not in old_keys:
            changes.append(
                Change(
                    kind=ChangeKind.ODR_SOURCE_CONFLICT,
                    symbol=name,
                    description=(
                        f"Type {name!r} has conflicting definitions across "
                        "translation units (ODR conflict). Mixing them is "
                        "undefined behavior."
                    ),
                    old_value=str(conflict.get("old_type_hash", "")),
                    new_value=str(conflict.get("new_type_hash", "")),
                    source_location=f"[{EVIDENCE_TIER_L4}]",
                )
            )
    return changes


# -- helpers -----------------------------------------------------------------


def _is_generated(entity: SourceEntity) -> bool:
    if entity.visibility == "generated":
        return True
    loc = entity.source_location
    return bool(loc and loc.origin == "GENERATED")


def _entity_changed(old: SourceEntity, new: SourceEntity) -> bool:
    return (
        old.signature_hash != new.signature_hash
        or old.body_hash != new.body_hash
        or old.type_hash != new.type_hash
        or old.value != new.value
    )
