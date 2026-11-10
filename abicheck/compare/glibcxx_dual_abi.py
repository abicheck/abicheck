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
compiler recorded in ``DW_AT_producer``, else what the side's exported names
(demangled) and debug-info record names spell. Evidence for the
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

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..detector_registry import registry
from ..diff_helpers import make_change
from ..model.build_mode_facts import GlibcxxDualAbi
from ..model.change_catalog.kinds import ChangeKind
from ..model.glibcxx_dual_abi import (
    DualAbiSelection,
    SelectionSource,
    dual_abi_from_spellings,
    parse_macro_ops,
    runtime_abi_ops,
    selection_from_macro_ops,
    selection_from_producer_flags,
    spells_dual_abi_type,
)
from ..model.vocabulary import NON_PUBLIC_ORIGINS
from .exported_by_mangled import (
    exported_functions_by_mangled,
    exported_variables_by_mangled,
)
from .type_map_pair import Side, TypeMap, TypeMapPair, type_map_key

if TYPE_CHECKING:
    from ..model import AbiSnapshot, RecordType
    from ..model.change import Change

__all__ = [
    "ExportNames",
    "dual_abi_selection_of",
    "export_names",
    "normalize_dual_abi_spelling",
    "pair_remangled_exports",
    "runtime_abi_ops_of",
]

#: How many affected entities the description names before "and N more".
_NAMED_LIMIT = 5

_CHAR_TYPES = ("char", "wchar_t", "char8_t", "char16_t", "char32_t")
_STRING_ALIAS = {
    "char": "std::string",
    "wchar_t": "std::wstring",
    "char8_t": "std::u8string",
    "char16_t": "std::u16string",
    "char32_t": "std::u32string",
}
_FULL_STRING_RE = re.compile(
    r"std::basic_string<\s*(?P<ch>"
    + "|".join(_CHAR_TYPES)
    + r")\s*,\s*std::char_traits<\s*(?P=ch)\s*>\s*,\s*std::allocator<\s*(?P=ch)\s*>\s*>"
)
_SHORT_STRING_RE = re.compile(
    r"std::basic_string<\s*(?P<ch>" + "|".join(_CHAR_TYPES) + r")\s*>"
)


def normalize_dual_abi_spelling(spelling: str) -> str:
    """``spelling`` (a demangled name or type) with the dual-ABI distinction
    removed: no ``__cxx11::``, no ``[abi:cxx11]``, and every
    ``std::basic_string<C, std::char_traits<C>, std::allocator<C> >`` written
    as its ``std::string`` alias, the form the old ABI demangles to.

    The matching key of :func:`pair_remangled_exports`: two exports whose
    spellings differ but normalize equal are one declaration built under
    the two ABIs.
    """
    out = spelling.replace("[abi:cxx11]", "").replace("__cxx11::", "")
    out = _FULL_STRING_RE.sub(lambda m: _STRING_ALIAS[m.group("ch")], out)
    out = _SHORT_STRING_RE.sub(lambda m: _STRING_ALIAS[m.group("ch")], out)
    return re.sub(r"\s+", " ", out).strip()


@dataclass(frozen=True)
class ExportNames:
    """One side's exports (mangled -> declared name) and their demangled
    spellings, built once per comparison side and shared by the selection
    and the pairing."""

    exports: Mapping[str, str]
    demangled: Mapping[str, str]


def export_names(snapshot: AbiSnapshot) -> ExportNames:
    """The side's exports and their demangled spellings (one pass)."""
    from ..demangle import demangle_batch

    exports = {
        m: f.name for m, f in exported_functions_by_mangled(snapshot).items() if m
    }
    exports.update(
        (m, v.name) for m, v in exported_variables_by_mangled(snapshot).items()
    )
    # demangle_batch leaves non-C++ names out of its result.
    return ExportNames(exports, demangle_batch(sorted(exports)))


def runtime_abi_ops_of(snapshot: AbiSnapshot) -> list[tuple[str, str]]:
    """The side's header-parse operations on a runtime-ABI macro (parsed
    once per side; ``[]`` for a side with no header parse)."""
    contract = snapshot.contract
    if contract is None:
        return []
    return runtime_abi_ops(parse_macro_ops(contract.profile_fields.get("macro_ops")))


def dual_abi_selection_of(
    snapshot: AbiSnapshot,
    names: ExportNames | None = None,
    abi_ops: list[tuple[str, str]] | None = None,
) -> DualAbiSelection | None:
    """The side's libstdc++ dual-ABI selection, strongest evidence first.
    *names* and *abi_ops* (:func:`runtime_abi_ops_of`) reuse what the
    caller already read from the side."""
    if abi_ops is None:
        abi_ops = runtime_abi_ops_of(snapshot)
    selected = selection_from_macro_ops(abi_ops)
    if selected is not None:
        return selected
    mode = snapshot.build_mode
    if mode is not None:
        producer = mode.provenance.raw_producer or ""
        selected = selection_from_producer_flags(producer.split())
        if selected is not None:
            return selected
    return _selection_from_names(snapshot, names or export_names(snapshot))


def _selection_from_names(
    snapshot: AbiSnapshot, names: ExportNames
) -> DualAbiSelection | None:
    """The selection the side's own names spell: its exports, demangled, and
    the debug-info names of its record types. Read here, not from the stored
    ``BuildMode.glibcxx_dual_abi``: that dump-time inference reads mangled
    names only, so an old-ABI ``std::string`` (the ``Ss`` substitution) and a
    library whose only use of one is a record field never reach it."""
    spellings = [*names.demangled.values()]
    spellings += [t.name for t in snapshot.declarations.types if t.name]
    abi = dual_abi_from_spellings(spellings)
    return DualAbiSelection(abi, SelectionSource.NAMES) if abi is not None else None


def pair_remangled_exports(o: ExportNames, n: ExportNames) -> list[tuple[str, str]]:
    """``(old_mangled, new_mangled)`` for each export the new side no longer
    has whose dual-ABI twin it does: different mangled names, one declaration
    once the dual-ABI spelling is normalized away. Pure over its two inputs:
    the result does not depend on their order."""
    removed = sorted(set(o.exports) - set(n.exports))
    added = sorted(set(n.exports) - set(o.exports))
    added_by_norm: dict[str, list[str]] = {}
    for m in added:
        if m in n.demangled:
            added_by_norm.setdefault(
                normalize_dual_abi_spelling(n.demangled[m]), []
            ).append(m)
    pairs: list[tuple[str, str]] = []
    for m in removed:
        text = o.demangled.get(m)
        if text is None:
            continue
        twins = added_by_norm.get(normalize_dual_abi_spelling(text), [])
        # Exactly one twin: an overload set that collapses to one normalized
        # spelling is ambiguous, not a pair. And the two spellings must name
        # different ABIs (``make[abi:cxx11]()`` vs ``make()``): a difference
        # the normalizer merely ignores, such as spacing, is not a flip.
        if len(twins) == 1 and dual_abi_from_spellings(
            [text]
        ) is not dual_abi_from_spellings([n.demangled[twins[0]]]):
            pairs.append((m, twins[0]))
    return pairs


def _is_library_record(t: RecordType) -> bool:
    """A record the library declares: not from a private, system or
    generated header (the structural origin fact), and not itself one of the
    runtime types the selection changes."""
    return t.origin not in NON_PUBLIC_ORIGINS and not spells_dual_abi_type(t.name)


def _layout_of(t: RecordType) -> tuple[object, ...]:
    """A record's size and field offsets, the layout a flip moves."""
    return (t.size_bits, tuple((f.name, f.offset_bits) for f in t.fields))


def _affected_records(
    old: AbiSnapshot, new: AbiSnapshot, *, layout_must_differ: bool
) -> list[str]:
    """Public records on both sides with a field spelling a dual-ABI type;
    with ``layout_must_differ``, only those whose size or field offsets
    differ (the observed effect of a flip the evidence does not name on
    both sides). Old and new records are paired by qualified identity
    (:class:`TypeMapPair`), never by a bare name two namespaces can share."""
    pairs = TypeMapPair(
        old=TypeMap(old.declarations.types), new=TypeMap(new.declarations.types)
    )
    out: set[str] = set()
    for t in old.declarations.types:
        twin = pairs.counterpart(Side.OLD, t)
        if (
            twin is not None
            and not t.is_template_pattern
            and _is_library_record(t)
            and any(spells_dual_abi_type(f.type) for f in t.fields)
            and (not layout_must_differ or _layout_of(t) != _layout_of(twin))
        ):
            out.add(type_map_key(t))
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
    """The first few *items* for a description, then a count of the rest."""
    head = ", ".join(items[:_NAMED_LIMIT])
    rest = len(items) - _NAMED_LIMIT
    return f"{head} and {rest} more" if rest > 0 else head


_OTHER_ABI = {
    GlibcxxDualAbi.CXX11: GlibcxxDualAbi.OLD,
    GlibcxxDualAbi.OLD: GlibcxxDualAbi.CXX11,
}


def _pair_direction(
    pairs: list[tuple[str, str]], old_names: ExportNames, new_names: ExportNames
) -> tuple[str, str]:
    """Each side's selection read off the re-mangled pairs alone: what the
    pair's demangled spellings on that side spell. A re-mangled pair proves
    the two sides differ, so one side's spelling settles the other (the old
    ABI tags nothing: ``make[abi:cxx11]()`` pairs with a plain ``make()``)."""
    old_abi = dual_abi_from_spellings(old_names.demangled[a] for a, _ in pairs)
    new_abi = dual_abi_from_spellings(new_names.demangled[b] for _, b in pairs)
    if old_abi is None and new_abi is not None:
        old_abi = _OTHER_ABI[new_abi]
    elif new_abi is None and old_abi is not None:
        new_abi = _OTHER_ABI[old_abi]
    if old_abi is None or new_abi is None or old_abi is new_abi:
        return "one libstdc++ ABI", "the other"
    return (
        DualAbiSelection(old_abi, SelectionSource.NAMES).describe(),
        DualAbiSelection(new_abi, SelectionSource.NAMES).describe(),
    )


def _ops_text(abi_ops: list[tuple[str, str]]) -> str:
    """The header parse's selector operations, as ``-D``/``-U`` spellings."""
    ops = [f"-{op}{arg}" for op, arg in abi_ops]
    return " ".join(ops) or "compiler default"


def _side(
    selection: DualAbiSelection | None,
    abi_ops: list[tuple[str, str]],
    macro_flip: bool,
) -> str | None:
    """How a side's selection is described: its evidence, else (when the
    header parses' selector operations differ) the operations it ran."""
    if selection is not None:
        return selection.describe()
    if macro_flip:
        return f"{_ops_text(abi_ops)} (header parse)"
    return None


@registry.detector("glibcxx_dual_abi_selection")
def _diff_glibcxx_dual_abi_selection(
    old: AbiSnapshot, new: AbiSnapshot
) -> list[Change]:
    """Report a libstdc++ dual-ABI flip whose effect reaches the public surface."""
    old_names, new_names = export_names(old), export_names(new)
    old_ops, new_ops = runtime_abi_ops_of(old), runtime_abi_ops_of(new)
    o = dual_abi_selection_of(old, old_names, old_ops)
    n = dual_abi_selection_of(new, new_names, new_ops)
    flipped = o is not None and n is not None and o.abi is not n.abi
    # A flip both sides name outright (a header -D or a producer flag on
    # each). One read from names is weaker: a library that starts or stops
    # using a dual-ABI type changes what its names spell without a rebuild
    # under the other selection. For such a flip a record counts only when
    # its layout moved, and an exported variable (whose spelling the flip
    # does not change) is not attributed to it.
    named_flip = (
        flipped
        and o is not None
        and n is not None
        and o.source.names_the_selection
        and n.source.names_the_selection
    )
    # The two header parses ran different operations on the selector macro:
    # a flip unless the side left at the compiler default already had the
    # value the other named, so it needs an observed effect to count.
    macro_flip = old_ops != new_ops
    pairs = pair_remangled_exports(old_names, new_names)
    records = (
        _affected_records(old, new, layout_must_differ=not named_flip)
        if flipped or macro_flip
        else []
    )
    variables = _affected_variables(old, new) if named_flip else []
    old_desc = _side(o, old_ops, macro_flip)
    new_desc = _side(n, new_ops, macro_flip)

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
            old_desc, new_desc = _pair_direction(pairs, old_names, new_names)
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
    # diff's finding, and one inferred from names, with nothing
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
