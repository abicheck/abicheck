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

from ..checker_policy import ChangeKind
from ..checker_types import Change
from .source_abi import EVIDENCE_TIER_L4, SourceAbiSurface, SourceEntity


def _loc(entity: SourceEntity) -> str:
    """Render a source location stamped with the L4 evidence-tier boundary (D10)."""
    base = ""
    if entity.source_location and entity.source_location.path:
        base = entity.source_location.path
        if entity.source_location.line:
            base += f":{entity.source_location.line}"
    return f"{base} [{EVIDENCE_TIER_L4}]" if base else f"[{EVIDENCE_TIER_L4}]"


def _by_name(entities: list[SourceEntity]) -> dict[str, SourceEntity]:
    """Index entities by qualified name (last write wins; ODR is detected at link)."""
    return {e.qualified_name: e for e in entities if e.qualified_name}


def diff_source_abi(old: SourceAbiSurface, new: SourceAbiSurface) -> list[Change]:
    """Return source-replay findings for the old→new source-surface transition.

    The result is an ordinary list of :class:`Change` objects ready to fold into
    a ``DiffResult`` and run through the existing verdict/policy pipeline.
    """
    changes: list[Change] = []
    changes.extend(_diff_macros(old, new))
    changes.extend(_diff_declarations(old, new))
    changes.extend(_diff_inline_bodies(old, new))
    changes.extend(_diff_templates(old, new))
    changes.extend(_diff_mappings(old, new))
    changes.extend(_diff_odr(old, new))
    return changes


# -- macros ------------------------------------------------------------------


def _diff_macros(old: SourceAbiSurface, new: SourceAbiSurface) -> list[Change]:
    old_m = _by_name(old.reachable_macros)
    new_m = _by_name(new.reachable_macros)
    changes: list[Change] = []
    for name in sorted(set(old_m) & set(new_m)):
        ov, nv = old_m[name], new_m[name]
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
    old_d = _by_name(old.reachable_declarations)
    new_d = _by_name(new.reachable_declarations)
    changes: list[Change] = []
    for name in sorted(set(old_d) & set(new_d)):
        ov, nv = old_d[name], new_d[name]

        # Generated public header content changed (any hash drift in a generated
        # entity). Reported once per declaration; policy may escalate (D6).
        if _is_generated(nv) and _entity_changed(ov, nv):
            changes.append(
                Change(
                    kind=ChangeKind.GENERATED_HEADER_CHANGED,
                    symbol=name,
                    description=(
                        f"Generated public declaration {name!r} changed; the "
                        "generated header content differs between versions. "
                        "Verify the generated configuration is intended."
                    ),
                    old_value=ov.value or ov.signature_hash,
                    new_value=nv.value or nv.signature_hash,
                    source_location=_loc(nv),
                )
            )
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
        # default-argument string (ADR-030 D6).
        if ov.signature_hash == nv.signature_hash and ov.value != nv.value:
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
    old_i = _by_name(old.reachable_inline_bodies)
    new_i = _by_name(new.reachable_inline_bodies)
    changes: list[Change] = []
    for name in sorted(set(old_i) & set(new_i)):
        ov, nv = old_i[name], new_i[name]
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
    old_t = _by_name(old.reachable_templates)
    new_t = _by_name(new.reachable_templates)
    changes: list[Change] = []
    for name in sorted(set(old_t) - set(new_t)):
        ov = old_t[name]
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
    for name in sorted(set(old_t) & set(new_t)):
        ov, nv = old_t[name], new_t[name]
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
    changes: list[Change] = []
    for name in sorted(set(old_map) & set(new_map)):
        had = bool(old_map.get(name))
        has = bool(new_map.get(name))
        if had and not has:
            changes.append(
                Change(
                    kind=ChangeKind.SOURCE_DECL_BINARY_SYMBOL_MISMATCH,
                    symbol=name,
                    description=(
                        f"Public declaration {name!r} no longer maps to an "
                        "exported symbol. If the export was removed, the artifact "
                        "diff (L0) emits the authoritative breaking finding."
                    ),
                    old_value=str(old_map.get(name)),
                    new_value="",
                    source_location=f"[{EVIDENCE_TIER_L4}]",
                )
            )
    return changes


# -- ODR conflicts -----------------------------------------------------------


def _diff_odr(old: SourceAbiSurface, new: SourceAbiSurface) -> list[Change]:
    """Flag ODR conflicts newly introduced on the new side (D6)."""
    old_names = {c.get("qualified_name", "") for c in old.odr_conflicts}
    changes: list[Change] = []
    for conflict in new.odr_conflicts:
        name = conflict.get("qualified_name", "")
        if name and name not in old_names:
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
