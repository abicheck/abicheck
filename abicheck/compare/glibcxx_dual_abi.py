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

"""A libstdc++ dual-ABI flip as a compatibility finding (ADR-050 amendment
2026-11-09): one ``GLIBCXX_DUAL_ABI_CHANGED`` (BREAKING) root cause when the
two builds select different ``_GLIBCXX_USE_CXX11_ABI`` values and the public
surface uses a type the selection changes.

Evidence, per side, for the *selection*: the header parse's own ``-D``
(``profile_fields["macro_ops"]``), else a ``-D_GLIBCXX_USE_CXX11_ABI=N`` the
compiler recorded in ``DW_AT_producer``, else the build mode inferred from the
binary's mangled names (``BuildMode.glibcxx_dual_abi``). Evidence for the
*affected surface*: an export removed whose dual-ABI twin was added (the two
demangle to one declaration once the ``__cxx11``/``[abi:cxx11]`` spelling is
removed), or, when the selection flip is known, an exported variable or a
public record that spells a string/list type.

* A re-mangled export pair proves the break on its own: a consumer's
  reference to the old name no longer resolves.
* Known, differing selections on both sides plus an affected variable or
  record prove it too: the type the consumer was compiled against is not the
  library's.
* Header parses whose selector operations differ while one side stayed at
  the compiler default (which may itself be ``=0``) count a record only when
  its size or field offsets moved, the observed effect of the flip.
* A selector difference in the header parses with nothing affected is
  recorded as ``TOOLCHAIN_FLAG_DRIFT`` (COMPATIBLE): the comparability gate
  waived that difference, and the waiver stays visible (ADR-067).
* Otherwise nothing. The ordinary symbol and layout findings still decide
  the verdict.

The per-entity findings (``func_removed``, ``type_size_changed`` …) stay in
the result; this finding names their common cause.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..detector_registry import registry
from ..diff_helpers import make_change
from ..model.build_mode_facts import GlibcxxDualAbi, StdlibFamily
from ..model.change_catalog.kinds import ChangeKind
from ..model.glibcxx_dual_abi import (
    DualAbiSelection,
    normalize_dual_abi_spelling,
    runtime_abi_ops,
    selection_from_macro_ops,
    selection_from_producer_flags,
    spells_dual_abi_type,
)
from ..model.vocabulary import NON_PUBLIC_ORIGINS
from .exported_functions import (
    exported_functions_by_mangled,
    exported_variables_by_mangled,
)

if TYPE_CHECKING:
    from ..model import AbiSnapshot, RecordType
    from ..model.change import Change

__all__ = ["dual_abi_selection_of", "remangled_export_pairs"]

#: How many affected entities the description names before "and N more".
_NAMED_LIMIT = 5

_INFERRED = "inferred from mangled names"


def dual_abi_selection_of(snapshot: AbiSnapshot) -> DualAbiSelection | None:
    """The side's libstdc++ dual-ABI selection, strongest evidence first."""
    contract = snapshot.contract
    if contract is not None:
        selected = selection_from_macro_ops(contract.profile_fields.get("macro_ops"))
        if selected is not None:
            return selected
    mode = snapshot.build_mode
    if mode is None:
        return None
    producer = mode.provenance.raw_producer or ""
    selected = selection_from_producer_flags(producer.split())
    if selected is not None:
        return selected
    if mode.stdlib is StdlibFamily.LIBSTDCXX and mode.glibcxx_dual_abi in (
        GlibcxxDualAbi.CXX11,
        GlibcxxDualAbi.OLD,
    ):
        return DualAbiSelection(mode.glibcxx_dual_abi, _INFERRED)
    return None


def _exports(snapshot: AbiSnapshot) -> dict[str, str]:
    """Exported function and variable mangled names -> declared name."""
    out = {m: f.name for m, f in exported_functions_by_mangled(snapshot).items() if m}
    out.update((m, v.name) for m, v in exported_variables_by_mangled(snapshot).items())
    return out


def remangled_export_pairs(old: AbiSnapshot, new: AbiSnapshot) -> list[tuple[str, str]]:
    """``(old_mangled, new_mangled)`` for each export the new build no longer
    has whose dual-ABI twin it does: different mangled names, one declaration
    once the dual-ABI spelling is normalized away."""
    old_exports, new_exports = _exports(old), _exports(new)
    removed = sorted(set(old_exports) - set(new_exports))
    added = sorted(set(new_exports) - set(old_exports))
    if not removed or not added:
        return []
    from ..demangle import demangle_batch

    # demangle_batch leaves non-C++ names out of its result.
    demangled = demangle_batch([*removed, *added])
    added_by_norm: dict[str, list[str]] = {}
    for m in added:
        if m in demangled:
            added_by_norm.setdefault(
                normalize_dual_abi_spelling(demangled[m]), []
            ).append(m)
    pairs: list[tuple[str, str]] = []
    for m in removed:
        text = demangled.get(m)
        if text is None:
            continue
        twins = added_by_norm.get(normalize_dual_abi_spelling(text), [])
        # Exactly one twin: an overload set that collapses to one normalized
        # spelling is ambiguous, not a pair.
        if len(twins) == 1 and demangled[twins[0]] != text:
            pairs.append((m, twins[0]))
    return pairs


def _is_library_record(t: RecordType) -> bool:
    """A record the library declares: not from a private, system or
    generated header (the structural origin fact), and not itself one of the
    runtime types the selection changes."""
    return t.origin not in NON_PUBLIC_ORIGINS and not spells_dual_abi_type(t.name)


def _layout_of(t: RecordType) -> tuple[object, ...]:
    return (t.size_bits, tuple((f.name, f.offset_bits) for f in t.fields))


def _affected_records(
    old: AbiSnapshot, new: AbiSnapshot, *, layout_must_differ: bool
) -> list[str]:
    """Public records on both sides with a field spelling a dual-ABI type;
    with ``layout_must_differ``, only those whose size or field offsets
    differ (the observed effect of a selection only one side named)."""
    new_by_name = {t.name: t for t in new.declarations.types}
    out: set[str] = set()
    for t in old.declarations.types:
        twin = new_by_name.get(t.name)
        if (
            twin is not None
            and not t.is_template_pattern
            and _is_library_record(t)
            and any(spells_dual_abi_type(f.type) for f in t.fields)
            and (not layout_must_differ or _layout_of(t) != _layout_of(twin))
        ):
            out.add(t.name)
    return sorted(out)


def _affected_variables(old: AbiSnapshot, new: AbiSnapshot) -> list[str]:
    """Exported variables on both sides whose type spells a dual-ABI type
    (a variable's mangled name does not encode its type, so it survives)."""
    new_exported = exported_variables_by_mangled(new)
    return sorted(
        {
            v.name
            for m, v in exported_variables_by_mangled(old).items()
            if m in new_exported and spells_dual_abi_type(v.type)
        }
    )


def _named(items: list[str]) -> str:
    head = ", ".join(items[:_NAMED_LIMIT])
    rest = len(items) - _NAMED_LIMIT
    return f"{head} and {rest} more" if rest > 0 else head


def _pair_direction(pairs: list[tuple[str, str]]) -> tuple[str, str]:
    old_tagged = sum(1 for o, _ in pairs if "cxx11" in o)
    new_tagged = sum(1 for _, n in pairs if "cxx11" in n)
    if old_tagged == new_tagged:
        return "one libstdc++ ABI", "the other"
    old_value, new_value = ("1", "0") if old_tagged > new_tagged else ("0", "1")
    return (
        f"_GLIBCXX_USE_CXX11_ABI={old_value} ({_INFERRED})",
        f"_GLIBCXX_USE_CXX11_ABI={new_value} ({_INFERRED})",
    )


def _ops_text(snapshot: AbiSnapshot) -> str:
    contract = snapshot.contract
    raw = contract.profile_fields.get("macro_ops") if contract is not None else None
    ops = [f"-{op}{arg}" for op, arg in runtime_abi_ops(raw)]
    return " ".join(ops) or "compiler default"


def _side(
    selection: DualAbiSelection | None, snapshot: AbiSnapshot, macro_flip: bool
) -> str | None:
    if selection is not None:
        return selection.describe()
    if macro_flip:
        return f"{_ops_text(snapshot)} (header parse)"
    return None


@registry.detector("glibcxx_dual_abi_selection")
def _diff_glibcxx_dual_abi_selection(
    old: AbiSnapshot, new: AbiSnapshot
) -> list[Change]:
    """Report a libstdc++ dual-ABI flip whose effect reaches the public surface."""
    o = dual_abi_selection_of(old)
    n = dual_abi_selection_of(new)
    flipped = o is not None and n is not None and o.abi is not n.abi
    # The two header parses ran different operations on the selector macro:
    # a flip unless the side left at the compiler default already had the
    # value the other named, so it needs an observed effect to count.
    macro_flip = _runtime_abi_ops_of(old) != _runtime_abi_ops_of(new)
    pairs = remangled_export_pairs(old, new)
    records = (
        _affected_records(old, new, layout_must_differ=not flipped)
        if flipped or macro_flip
        else []
    )
    variables = _affected_variables(old, new) if flipped else []
    old_desc = _side(o, old, macro_flip)
    new_desc = _side(n, new, macro_flip)

    if pairs or records or variables:
        parts: list[str] = []
        if pairs:
            parts.append(
                f"{len(pairs)} export(s) re-mangled: "
                + _named([f"{a} -> {b}" for a, b in pairs])
            )
        if variables:
            parts.append(f"exported variable(s) of a changed type: {_named(variables)}")
        if records:
            parts.append(f"public record(s) holding a changed type: {_named(records)}")
        if old_desc is None or new_desc is None or old_desc == new_desc:
            old_desc, new_desc = _pair_direction(pairs)
        return [
            make_change(
                ChangeKind.GLIBCXX_DUAL_ABI_CHANGED,
                symbol="<glibcxx-dual-abi>",
                old=old_desc,
                new=new_desc,
                detail="; ".join(parts),
                affected_symbols=[a for a, _ in pairs] + variables + records,
            )
        ]

    # The comparability gate waived a selector-only macro_ops difference
    # (ADR-050 amendment 2026-11-09); with nothing on the surface affected,
    # record that waiver instead of letting it vanish (ADR-067). A flip read
    # only from DW_AT_producer is already the advanced-DWARF toolchain-flag
    # diff's finding, and one inferred from mangled names, with nothing
    # affected, is no fact to record.
    if macro_flip and old_desc is not None and new_desc is not None:
        return [
            make_change(
                ChangeKind.TOOLCHAIN_FLAG_DRIFT,
                symbol="<toolchain>",
                description=(
                    "libstdc++ dual ABI selector differs between the header "
                    f"parses: {old_desc} → {new_desc}; no exported symbol, "
                    "exported variable or public record shows a type it changes"
                ),
                old_value=old_desc,
                new_value=new_desc,
            )
        ]
    return []


def _runtime_abi_ops_of(snapshot: AbiSnapshot) -> list[tuple[str, str]]:
    contract = snapshot.contract
    if contract is None:
        return []
    return runtime_abi_ops(contract.profile_fields.get("macro_ops"))
