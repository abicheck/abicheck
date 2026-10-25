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

"""No-toolchain twin of ``test_template_export_loss_declaration_presence.py``.

The same decision (a template instance the new header no longer enumerates) on
constructed snapshots, so it runs in the fast lane without g++ or castxml. The
oracle here is the export table the test writes down for each transition,
instead of readelf; the invariants are the integration test's.
"""

from __future__ import annotations

import pytest

from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary
from abicheck.buildsource.template_graph_fold import NODE_SOURCE_DECL
from abicheck.compare.template_instance_absence import (
    TemplateInstanceAbsence,
    _primary_stem_of,
)
from abicheck.diff_symbols import _diff_functions
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot
from abicheck.model.declarations import Function, Visibility
from abicheck.model.elf_facts import SymbolBinding, SymbolType
from abicheck.model.export_entity_name import entity_name_components

_DECLARATION_REMOVAL_KINDS = {"func_removed", "inline_function_removed"}
_EXPORT_KINDS = {"func_visibility_changed", "func_removed_elf_only"}

#: kind -> (mangled symbol, header spelling of its primary template)
_KINDS = {
    "free_fn_template": ("_ZN2ns5ftmplIdEEvPT_", "ns::ftmpl"),
    "member_fn_template": ("_ZN2ns3CSR9setArraysIdEEvPT_", "ns::CSR::setArrays"),
    "class_template_member": ("_ZN2ns3BoxIdE3setEd", "ns::Box::set"),
}

#: The new export table the fixture is built with, per (kind, transition). Free
#: function templates are not inline-hidden, so they stay exported under
#: ``-fvisibility-inlines-hidden``; member templates become LOCAL there.
_STILL_EXPORTED = {
    ("free_fn_template", "to_local"): True,
    ("free_fn_template", "to_absent"): False,
    ("free_fn_template", "to_hidden"): False,
    ("member_fn_template", "to_local"): False,
    ("member_fn_template", "to_absent"): False,
    ("member_fn_template", "to_hidden"): False,
    ("class_template_member", "to_local"): False,
    ("class_template_member", "to_absent"): False,
    ("class_template_member", "to_hidden"): False,
}
_TRANSITIONS = ("to_local", "to_absent", "to_hidden")


def _function(
    mangled: str, name: str, *, binding: SymbolBinding | None, visibility: Visibility
) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        visibility=visibility,
        elf_binding=binding,
    )


def _elf(*exported: str) -> ElfMetadata:
    return ElfMetadata(
        machine="x86-64",
        symbols=[
            ElfSymbol(name=name, binding=SymbolBinding.WEAK, sym_type=SymbolType.FUNC)
            for name in exported
        ],
    )


def _graph(*stems: str | tuple[str, str]) -> SourceGraphSummary:
    """A header graph with one ``decl://`` node per entry.

    An entry is a qualified name, or a (name, sha) pair when the same name must
    appear as two distinct declarations.
    """
    graph = SourceGraphSummary()
    for index, entry in enumerate(stems):
        name, sha = (entry, f"{index:064x}") if isinstance(entry, str) else entry
        graph.add_node(
            GraphNode(id=f"decl://{name}#sha256:{sha}", kind=NODE_SOURCE_DECL)
        )
    return graph


def _snap(
    functions: list[Function],
    *,
    elf: ElfMetadata | None,
    graph: SourceGraphSummary | None,
    version: str,
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libx.so",
        version=version,
        functions=functions,
        elf=elf,
        from_headers=True,
        surface_graph=graph,
    )


def _detect(
    mangled: str,
    *,
    old_visibility: Visibility = Visibility.PUBLIC,
    old_binding: SymbolBinding | None = SymbolBinding.WEAK,
    new_exports: tuple[str, ...] = (),
    new_elf: bool = True,
    stems: tuple[str | tuple[str, str], ...] = (),
) -> list:
    """Run the functions detector over an old/new pair whose only difference is
    the one template instance, and return the changes that name its symbol."""
    first = stems[0] if stems else "f"
    name = (first[0] if isinstance(first, tuple) else first).rsplit("::", 1)[-1]
    old = _snap(
        [
            _function(
                mangled,
                name,
                binding=old_binding,
                visibility=old_visibility,
            )
        ],
        elf=_elf(mangled) if old_binding is not None else None,
        graph=_graph(*stems),
        version="1",
    )
    new = _snap(
        [],
        elf=_elf(*new_exports) if new_elf else None,
        graph=_graph(*stems),
        version="2",
    )
    return [c for c in _diff_functions(old, new) if c.symbol == mangled]


@pytest.mark.parametrize(
    ("mangled", "stem"),
    [
        ("_ZN2ns5ftmplIdEEvPT_", "ns::ftmpl"),
        ("_ZN2ns3CSR9setArraysIdEEvPT_", "ns::CSR::setArrays"),
        ("_ZN2ns3BoxIdE3setEd", "ns::Box::set"),
    ],
)
def test_primary_stem_names_the_declared_template(mangled: str, stem: str) -> None:
    assert _primary_stem_of(entity_name_components(mangled)) == stem


@pytest.mark.parametrize(
    "mangled",
    [
        "_ZN2ns5use_kEv",  # not a template instance
        "_ZN2ns3BoxIdEaSERKS1_",  # an operator: no header spelling is matched
        "_ZN2ns3BoxIdEC1Ev",  # a constructor: no header spelling is matched
        "__abicheck_ctor__ns::Box<double>()",  # synthetic key, no mangled name
    ],
)
def test_primary_stem_makes_no_claim_outside_named_template_members(
    mangled: str,
) -> None:
    assert _primary_stem_of(entity_name_components(mangled)) is None


@pytest.mark.parametrize("kind", list(_KINDS))
@pytest.mark.parametrize("transition", _TRANSITIONS)
def test_declared_template_instance_outcome_follows_the_export_table(
    kind: str, transition: str
) -> None:
    """The matrix's invariants on constructed snapshots: the header still
    declares the template, and the new export table is the oracle."""
    mangled, stem = _KINDS[kind]
    still_exported = _STILL_EXPORTED[(kind, transition)]
    exported_before = True  # the old build exports every matrix symbol
    changes = _detect(
        mangled,
        new_exports=(mangled,) if still_exported else (),
        stems=(stem,),
    )
    kinds = {c.kind.value for c in changes}
    assert not kinds & _DECLARATION_REMOVAL_KINDS, sorted(kinds)
    export_changed = exported_before and not still_exported
    if export_changed:
        assert kinds == {"func_visibility_changed"}, sorted(kinds)
        assert all(c.symbol_binding == "weak" for c in changes)
    else:
        assert not kinds & _EXPORT_KINDS, sorted(kinds)


def test_removed_declaration_without_primary_stays_a_removal() -> None:
    """Control: no declared primary means the declaration itself is gone."""
    mangled = "_ZN2ns5ftmplIdEEvPT_"
    changes = _detect(mangled, new_exports=(), stems=())
    assert [c.kind.value for c in changes] == ["func_removed"]


def test_still_exported_instance_without_declared_primary_stays_a_removal() -> None:
    """Control: a removed declaration is not excused by the binary still
    exporting the symbol, unless the primary is still declared."""
    mangled = "_ZN2ns5ftmplIdEEvPT_"
    changes = _detect(mangled, new_exports=(mangled,), stems=())
    assert [c.kind.value for c in changes] == ["func_removed"]


def test_plain_function_is_not_a_template_instance() -> None:
    """Control: a non-template still exported by the new binary, dropped from
    the header, is still a removal (the header is the declaration evidence)."""
    mangled = "_ZN2ns3barEi"
    changes = _detect(mangled, new_exports=(mangled,), stems=("ns::bar",))
    assert [c.kind.value for c in changes] == ["func_removed"]


def test_kept_overload_set_is_judged_by_the_export_table() -> None:
    """Two declarations share the primary's spelling (an overload set) and
    both are kept: the template is still declared, so the export table
    decides (H2b). The deleted-overload control lives in
    ``test_template_instance_absence_shapes.py``."""
    mangled, stem = _KINDS["free_fn_template"]
    changes = _detect(
        mangled,
        new_exports=(),
        stems=((stem, "a"), (stem, "b")),
    )
    assert [c.kind.value for c in changes] == ["func_visibility_changed"]


def test_unread_new_export_table_makes_no_claim() -> None:
    """Without a new export table the absence is unknown, not proven, so the
    existing evidence path decides; this rule must not invent a visibility loss."""
    mangled, stem = _KINDS["member_fn_template"]
    changes = _detect(mangled, new_elf=False, stems=(stem,))
    assert [c.kind.value for c in changes] == ["func_removed"]


def test_still_exported_instance_has_no_finding() -> None:
    mangled, stem = _KINDS["free_fn_template"]
    assert _detect(mangled, new_exports=(mangled,), stems=(stem,)) == []


def test_never_exported_instance_is_dropped_rather_than_removed() -> None:
    """The old build did not export the instance: there was no symbol for a
    consumer to bind to, and the declaration remains."""
    mangled, stem = _KINDS["class_template_member"]
    changes = _detect(
        mangled,
        old_visibility=Visibility.HIDDEN,
        old_binding=None,
        new_exports=(),
        stems=(stem,),
    )
    assert changes == []


def test_export_table_only_old_record_keeps_the_elf_only_path() -> None:
    """An export-table-only old record keeps its existing ELF-only removal
    path; this rule does not take it over."""
    mangled, stem = _KINDS["free_fn_template"]
    old_record = Function(
        name="?",
        mangled=mangled,
        return_type="?",
        visibility=Visibility.ELF_ONLY,
        elf_binding=SymbolBinding.WEAK,
    )
    rule = TemplateInstanceAbsence(
        _snap([], elf=_elf(), graph=_graph(stem), version="2")
    )
    assert rule.changes_for(mangled, old_record, elf_only_mode=True) is None


@pytest.mark.parametrize(
    "identity",
    ["ns::CSR::setArrays", "ns::Box<double>::set", "f", "a::b::c::d"],
)
def test_decl_node_identity_inverts_decl_node_id(identity: str) -> None:
    from abicheck.model.graph_identity import _decl_node_id, decl_node_identity

    assert decl_node_identity(_decl_node_id(identity)) == identity
    assert decl_node_identity(_decl_node_id(identity) + "#sha256:abc") == identity


@pytest.mark.parametrize(
    "node_id",
    [
        "type://ns::CSR",
        "template_decl://ns::CSR::setArrays",
        "header://a.h",
        "unresolved://decl/x",
    ],
)
def test_decl_node_identity_rejects_other_node_kinds(node_id: str) -> None:
    from abicheck.model.graph_identity import decl_node_identity

    assert decl_node_identity(node_id) is None
