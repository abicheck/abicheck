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

"""Machinery for harness H1 (defect family F1, "Unknown != value").

See ``tests/test_family_f1_evidence_ablation.py`` for the contract; this
module holds the three moving parts so the test module stays readable:

* :func:`fact_site_inventory` / :func:`container_site_inventory` -- the
  *mechanical* inventory of evidence inputs, derived from the model's own
  dataclass annotations (never a hand-kept list);
* :data:`CORPUS` and :func:`ablate` -- in-process snapshot pairs and the
  evidence ablations applied to them;
* :func:`oracle_violations` -- the family oracles, written against the raw
  ``DiffResult`` fields (verdict, change kinds, coverage warnings,
  confidence, analysis assurance) rather than any predicate the
  implementation itself uses to reach its decision.
"""

from __future__ import annotations

import dataclasses
import importlib
import pkgutil
import re
import sys
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from abicheck.buildsource.header_graph import build_header_only_graph
from abicheck.checker import Verdict, compare
from abicheck.checker_policy import BREAKING_KINDS
from abicheck.dwarf_metadata import DwarfMetadata, FieldInfo, StructLayout
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.extract.export_table_read import finish_binary_snapshot
from abicheck.extract.semantic_normalizer import normalize_header_ast
from abicheck.extract.surface_fact_producers import header_ast_surface_facts
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    EnumMember,
    EnumType,
    Fact,
    Function,
    Param,
    ParamKind,
    RecordType,
    ScopeOrigin,
    TypeField,
    Variable,
    Visibility,
)
from abicheck.model.declaration_store import DECLARATION_KINDS
from abicheck.model.extraction_scope import EntityOwnership
from abicheck.model.identity import (
    entity_id_for_enum,
    entity_id_for_function,
    entity_id_for_type,
    entity_id_for_variable,
)
from abicheck.model.macho_facts import MachoMetadata
from abicheck.model.pe_facts import PeMetadata
from abicheck.model.semantic_ir import SemanticIR
from abicheck.policy.contract_coverage_exit import coverage_exit_floor

from _family_f1_containers import (  # isort: skip
    BREAK_CASE_CONTAINERS,
    PLATFORM_CASES,
    base_containers,
    on_platform,
)

# --------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------

_FACT_ANNOTATION = re.compile(r"\bFact\[")
_IDENT = re.compile(r"[A-Za-z_]\w*")


def _dataclass_name_index() -> dict[str, type]:
    """Every dataclass defined in an already-imported ``abicheck`` module,
    keyed by class name -- the resolver for string annotations (the model
    uses ``from __future__ import annotations``, so ``get_type_hints`` cannot
    resolve its forward references without each module's own globals)."""
    import abicheck.model as model_pkg

    for info in pkgutil.walk_packages(model_pkg.__path__, "abicheck.model."):
        importlib.import_module(info.name)
    index: dict[str, type] = {}
    for mod in list(sys.modules.values()):
        if not getattr(mod, "__name__", "").startswith("abicheck"):
            continue
        for name, obj in list(vars(mod).items()):
            if isinstance(obj, type) and dataclasses.is_dataclass(obj):
                index.setdefault(name, obj)
    return index


def reachable_dataclasses(root: type = AbiSnapshot) -> list[type]:
    """Dataclasses reachable from *root* through field annotations."""
    index = _dataclass_name_index()
    seen: list[type] = []

    def walk(cls: type) -> None:
        if cls in seen:
            return
        seen.append(cls)
        for f in dataclasses.fields(cls):
            for tok in _IDENT.findall(str(f.type)):
                if tok in index and tok != "Fact":
                    walk(index[tok])

    walk(root)
    return seen


def fact_site_inventory() -> dict[str, tuple[type, str]]:
    """``"Class.field" -> (class, field)`` for every ``Fact[...]``-annotated
    dataclass field reachable from :class:`AbiSnapshot`."""
    out: dict[str, tuple[type, str]] = {}
    for cls in reachable_dataclasses():
        for f in dataclasses.fields(cls):
            if _FACT_ANNOTATION.search(str(f.type)):
                out[f"{cls.__name__}.{f.name}"] = (cls, f.name)
    return out


_SCALAR_TOKENS = {
    "None",
    "str",
    "int",
    "float",
    "bool",
    "Fact",
    "dict",
    "list",
    "tuple",
}


def container_site_inventory() -> list[str]:
    """``AbiSnapshot`` fields that hold a whole optional evidence object
    (``X | None`` where ``X`` is a class, not a scalar or a ``Fact``): the
    snapshot-level evidence containers an extractor may fail to capture."""
    out = []
    for f in dataclasses.fields(AbiSnapshot):
        if f.name.startswith("_") or f.default is not None:
            continue
        toks = set(_IDENT.findall(str(f.type)))
        if "None" in toks and toks - _SCALAR_TOKENS and "Fact" not in toks:
            out.append(f"AbiSnapshot.{f.name}")
    return out


# --------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------


#: What the real header-AST producer stamps on a declaration it parsed from
#: a public header and found in the export table, so the full-evidence
#: baseline carries these facts *present* -- ablating an already-unknown fact
#: would test nothing (CodeRabbit review on PR #1428).
_EXPORTED_DECL_FACTS: dict[str, Any] = header_ast_surface_facts(
    exported=True, producer="castxml"
)

#: A real producer's classification (ADR-075), so every declaration's
#: ``ownership_fact`` is PRESENT in the full-evidence baseline and H1 ablates it.
_OWNED = Fact.present(EntityOwnership("target", "public", "corpus"))

#: Header-AST facts a castxml/clang producer captures for every function, set
#: to ordinary "captured, nothing special" values so each is PRESENT in the
#: baseline and H1's ablation of it is not a swap of one unknown for another.
_CAPTURED_FUNCTION_FACTS: dict[str, Any] = {
    "contract_attributes": [],
    "exception_spec": "",
    "is_override": False,
    "is_hidden_friend": False,
    "hidden_friend_owner_fact": Fact.present(None),
    "is_compiler_generated": False,
    "ownership_fact": _OWNED,
}


#: Every record/enum the corpus declares. A castxml producer records which of
#: them each type slot resolves to (``*.type_identities_fact``, schema v54).
_CORPUS_TYPE_NAMES = ("Point", "Base", "Derived", "Handle", "Color")


def _ids(type_: str) -> Fact[tuple[str, ...]]:
    bare = type_.replace("*", "").replace("const", "").strip()
    return Fact.present((bare,) if bare in _CORPUS_TYPE_NAMES else ())


def _param(name: str, type_: str = "int") -> Param:
    return Param(
        name=name,
        type=type_,
        type_identities_fact=_ids(type_),
        kind=ParamKind.VALUE,
        is_va_list=False,
        is_restrict=False,
    )


def _fn(
    name: str, ret: str = "int", params: tuple[str, ...] = ("int",), **kw: Any
) -> Function:
    return Function(
        name=name,
        mangled=f"_Z{len(name)}{name}{'i' * len(params) or 'v'}",
        return_type=ret,
        return_type_identities_fact=_ids(ret),
        params=[_param(f"a{i}", t) for i, t in enumerate(params)],
        visibility=Visibility.PUBLIC,
        is_variadic=False,
        is_explicit=False,
        deprecated=None,
        source_header="/inc/x.h",
        elf_binding_fact=Fact.present(SymbolBinding.GLOBAL),
        **{**_EXPORTED_DECL_FACTS, **_CAPTURED_FUNCTION_FACTS, **kw},
    )


def _var(name: str, type_: str = "int") -> Variable:
    return Variable(
        name=name,
        mangled=f"_ZL{len(name)}{name}",
        type=type_,
        type_identities_fact=_ids(type_),
        visibility=Visibility.PUBLIC,
        access=AccessLevel.PUBLIC,
        source_header="/inc/x.h",
        deprecated=None,
        elf_binding_fact=Fact.present(SymbolBinding.GLOBAL),
        alignment_bits=32,
        ownership_fact=_OWNED,
        **_EXPORTED_DECL_FACTS,
    )


def _field(name: str, type_: str, off: int) -> TypeField:
    return TypeField(
        name=name,
        type=type_,
        type_identities_fact=_ids(type_),
        offset_bits=off,
        is_const=False,
        is_volatile=False,
        is_mutable=False,
        default=None,
        deprecated=None,
    )


def _rec(
    name: str,
    fields: tuple[tuple[str, str], ...] = (("x", "int"), ("y", "int")),
    *,
    bases: tuple[str, ...] = (),
    vtable: tuple[str, ...] = (),
) -> RecordType:
    return RecordType(
        name=name,
        kind="struct",
        size_bits=32 * len(fields) + (64 if vtable else 0),
        alignment_bits=32,
        fields=[_field(n, t, 32 * i) for i, (n, t) in enumerate(fields)],
        bases=list(bases),
        virtual_bases=[],
        vtable=list(vtable),
        vptr_offset_bits=0 if vtable else None,
        is_final=False,
        is_abstract=False,
        data_size_bits=32 * len(fields),
        is_standard_layout=not vtable,
        is_trivially_copyable=not vtable,
        qualified_name=name,
        source_header="/inc/x.h",
        deprecated=None,
        ownership_fact=_OWNED,
    )


def _enum(name: str, members: tuple[tuple[str, int], ...]) -> EnumType:
    return EnumType(
        name=name,
        members=[EnumMember(n, v) for n, v in members],
        underlying_type="int",
        is_scoped=False,
        qualified_name=name,
        source_header="/inc/x.h",
        deprecated=None,
        ownership_fact=_OWNED,
    )


def _elf(snap_funcs: list[Function], snap_vars: list[Variable]) -> ElfMetadata:
    syms = [
        ElfSymbol(
            name=f.mangled,
            binding=SymbolBinding.GLOBAL,
            sym_type=SymbolType.FUNC,
            size=16,
        )
        for f in snap_funcs
    ]
    syms += [
        ElfSymbol(
            name=v.mangled,
            binding=SymbolBinding.GLOBAL,
            sym_type=SymbolType.OBJECT,
            size=4,
        )
        for v in snap_vars
    ]
    return ElfMetadata(
        soname="libx.so.1",
        symbols=syms,
        dynamic_flags_fact=Fact.present(frozenset()),
        has_init_fact=Fact.present(False),
        has_fini_fact=Fact.present(False),
    )


def _dwarf(types: list[RecordType]) -> DwarfMetadata:
    structs = {
        t.name: StructLayout(
            name=t.name,
            byte_size=(t.size_bits or 0) // 8,
            alignment=4,
            fields=[
                FieldInfo(
                    name=f.name,
                    type_name=f.type,
                    byte_offset=f.offset_bits // 8,
                    byte_size=4,
                )
                for f in t.fields
            ],
        )
        for t in types
    }
    return DwarfMetadata(structs=structs, has_dwarf=True)


@dataclass(frozen=True)
class Side:
    functions: tuple[Function, ...] = ()
    variables: tuple[Variable, ...] = ()
    types: tuple[RecordType, ...] = ()
    enums: tuple[EnumType, ...] = ()
    #: Overrides of ``_family_f1_containers.base_containers()`` for this side.
    containers: tuple[tuple[str, Any], ...] = ()


def _with_entity_ids(side: Side) -> Side:
    """Each declaration's ``entity_id`` as a header-AST backend assigns it, so
    the snapshot can carry the normalizer's ``SemanticIR`` (ADR-063 Phase 6)."""
    return dataclasses.replace(
        side,
        functions=tuple(
            dataclasses.replace(
                f,
                entity_id=entity_id_for_function(
                    (),
                    f.name,
                    mangled_name=f.mangled,
                    param_types=tuple(p.type for p in f.params),
                ),
            )
            for f in side.functions
        ),
        variables=tuple(
            dataclasses.replace(
                v, entity_id=entity_id_for_variable((), v.name, mangled_name=v.mangled)
            )
            for v in side.variables
        ),
        types=tuple(
            dataclasses.replace(t, entity_id=entity_id_for_type((), t.name))
            for t in side.types
        ),
        enums=tuple(
            dataclasses.replace(e, entity_id=entity_id_for_enum((), e.name))
            for e in side.enums
        ),
    )


def _semantic_ir(side: Side) -> SemanticIR:
    return normalize_header_ast(
        types=side.types,
        enums=side.enums,
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
        functions=side.functions,
        variables=side.variables,
    )


def _snapshot(version: str, side: Side) -> AbiSnapshot:
    side = _with_entity_ids(side)
    funcs, vars_ = list(side.functions), list(side.variables)
    # The builders' real shared tail stamps the export-read facts
    # (binary_exported / elf_binding) from the ELF table, so the full-evidence
    # baseline carries them present rather than unknown.
    snap = finish_binary_snapshot(
        AbiSnapshot(
            library="libx.so.1",
            version=version,
            functions=funcs,
            variables=vars_,
            types=list(side.types),
            enums=list(side.enums),
            elf=_elf(funcs, vars_),
            dwarf=_dwarf(list(side.types)),
            from_headers=True,
            platform="elf",
            ast_resolved_standard_fact=Fact.present("c++17"),
            public_header_identifiers=_header_identifiers(side),
            semantic_ir=_semantic_ir(side),
            **{**base_containers(), **dict(side.containers)},
        )
    )
    # The header-only L5 graph is derived from the snapshot itself.
    return dataclasses.replace(
        snap,
        surface_graph=build_header_only_graph(
            snap, public_header_paths=["/inc/x.h"], header_paths=["/inc/x.h"]
        ),
    )


def _header_identifiers(side: Side) -> frozenset[str]:
    """Every identifier the side's public header would spell -- what
    ``extract/public_header_identifiers`` records from the raw header text."""
    names: set[str] = set()
    for decl in (*side.functions, *side.variables):
        names.add(decl.name)
    for rec in side.types:
        names.add(rec.name)
        names.update(f.name for f in rec.fields)
    for en in side.enums:
        names.add(en.name)
        names.update(m.name for m in en.members)
    return frozenset(names)


def _handle(
    fields: tuple[tuple[str, str], ...] = (("x", "int"), ("y", "int")),
) -> RecordType:
    """A record declared in a public header that no signature references:
    public only through its header origin (the ``discard_iterator`` shape)."""
    return dataclasses.replace(_rec("Handle", fields), origin=ScopeOrigin.PUBLIC_HEADER)


def _base() -> Side:
    return Side(
        functions=(
            _fn("foo"),
            _fn("bar", "void", ("int", "char*")),
            _fn("baz", "long", ()),
            _fn("move", "void", ("Point",)),
            _fn("use", "void", ("Derived*",)),
        ),
        variables=(_var("g_count"),),
        types=(
            _rec("Point"),
            _rec("Base", (("v", "int"),), vtable=("_ZN4Base1fEv",)),
            _rec("Derived", (("d", "int"),), bases=("Base",), vtable=("_ZN4Base1fEv",)),
            _handle(),
        ),
        enums=(_enum("Color", (("RED", 0), ("GREEN", 1))),),
    )


def _replace_named(
    items: tuple[Any, ...], name: str, new: Any | None
) -> tuple[Any, ...]:
    out = []
    for it in items:
        if it.name == name:
            if new is not None:
                out.append(new)
        else:
            out.append(it)
    return tuple(out)


def _build_corpus() -> dict[str, tuple[AbiSnapshot, AbiSnapshot]]:
    b = _base()
    cases: dict[str, Side] = {
        "identical": b,
        "func_removed": dataclasses.replace(
            b, functions=_replace_named(b.functions, "baz", None)
        ),
        "func_added": dataclasses.replace(b, functions=(*b.functions, _fn("qux"))),
        "return_changed": dataclasses.replace(
            b, functions=_replace_named(b.functions, "foo", _fn("foo", "double"))
        ),
        "param_changed": dataclasses.replace(
            b,
            functions=_replace_named(
                b.functions, "bar", _fn("bar", "void", ("long", "char*"))
            ),
        ),
        "var_type_changed": dataclasses.replace(
            b, variables=(_var("g_count", "long"),)
        ),
        "var_removed": dataclasses.replace(b, variables=()),
        "struct_field_removed": dataclasses.replace(
            b, types=_replace_named(b.types, "Point", _rec("Point", (("x", "int"),)))
        ),
        "struct_field_type": dataclasses.replace(
            b,
            types=_replace_named(
                b.types, "Point", _rec("Point", (("x", "int"), ("y", "long")))
            ),
        ),
        "base_removed": dataclasses.replace(
            b,
            types=_replace_named(b.types, "Derived", _rec("Derived", (("d", "int"),))),
        ),
        "header_record_changed": dataclasses.replace(
            b,
            types=_replace_named(
                b.types, "Handle", _handle((("x", "int"), ("y", "long")))
            ),
        ),
        "enum_value_changed": dataclasses.replace(
            b, enums=(_enum("Color", (("RED", 0), ("GREEN", 2))),)
        ),
        "enum_member_added": dataclasses.replace(
            b, enums=(_enum("Color", (("RED", 0), ("GREEN", 1), ("BLUE", 2))),)
        ),
    }
    cases.update(
        {
            case: dataclasses.replace(b, containers=tuple(override.items()))
            for case, override in BREAK_CASE_CONTAINERS.items()
        }
    )
    corpus = {k: (_snapshot("1.0", b), _snapshot("2.0", v)) for k, v in cases.items()}
    corpus.update(_dedicated_pairs(b))
    for platform in ("pe", "macho"):
        for case in PLATFORM_CASES:
            old, new = corpus[case]
            corpus[f"{platform}:{case}"] = (
                on_platform(old, platform),
                on_platform(new, platform),
            )
    return corpus


def _pimpl_side(b: Side, member: str) -> Side:
    """The case89 shape: a public class holding a pimpl to a ``detail::``
    record, with public inline accessors whose bodies name *member*."""
    impl = dataclasses.replace(
        _rec("descriptor_impl", ((member, "int"), ("max_iter_", "int"))),
        qualified_name="mylib::detail::descriptor_impl",
        qualified_name_fact=Fact.present("mylib::detail::descriptor_impl"),
    )
    holder = _rec(
        "descriptor", (("impl_", "shared_ptr<mylib::detail::descriptor_impl>"),)
    )
    accessor = dataclasses.replace(
        _fn("get_class_count", "int", ()),
        mangled="_ZNK5mylib10descriptor15get_class_countEv",
        is_inline=True,
        access=AccessLevel.PUBLIC,
    )
    return dataclasses.replace(
        b,
        types=(*b.types, impl, holder),
        functions=(*b.functions, accessor),
    )


def _dedicated_pairs(b: Side) -> dict[str, tuple[AbiSnapshot, AbiSnapshot]]:
    """Pairs whose OLD side is not the shared base.

    * ``pimpl_inline_body_renamed`` (bug class
      ``evidence.optional_layer_prerequisite_for_a_stated_fact``): the
      ``detail::`` namespace is stated by the headers' ``qualified_name``, so
      the BREAKING finding must survive every ablation -- DWARF included.
    """
    return {
        "pimpl_inline_body_renamed": (
            _snapshot("1.0", _pimpl_side(b, "class_count_")),
            _snapshot("2.0", _pimpl_side(b, "iteration_cap_")),
        ),
    }


CORPUS: dict[str, tuple[AbiSnapshot, AbiSnapshot]] = _build_corpus()


# --------------------------------------------------------------------------
# Ablations
# --------------------------------------------------------------------------

#: Every way a producer may leave a ``Fact`` short of an observation.
UNKNOWN_FACTS: dict[str, Callable[[], Fact[Any]]] = {
    "not_collected": lambda: Fact.not_collected("f1-ablation"),
    "unsupported": lambda: Fact.unsupported("f1-ablation"),
    "failed": lambda: Fact.failed("f1-ablation"),
}

SIDES = ("old", "new", "both")


def _rewrite(obj: Any, cls: type, fname: str, value: Any, hits: list[int]) -> Any:
    """Return *obj* with ``fname`` replaced on every nested *cls* instance.

    Rebuilt through ``dataclasses.replace`` (never attribute assignment), so
    each owning dataclass's legacy/``Fact`` bridge re-runs exactly as it
    would for an extractor constructing the object with that fact."""
    if isinstance(obj, list):
        return [_rewrite(x, cls, fname, value, hits) for x in obj]
    if isinstance(obj, tuple):
        return tuple(_rewrite(x, cls, fname, value, hits) for x in obj)
    if isinstance(obj, Mapping):
        # Any mapping, not just ``dict``: ``SemanticIR.occurrences`` is a
        # ``FrozenMapping``. Rebuilt in its own type only when a value changed.
        items = {k: _rewrite(v, cls, fname, value, hits) for k, v in obj.items()}
        if all(items[k] is v for k, v in obj.items()):
            return obj
        return items if isinstance(obj, dict) else type(obj)(items)
    if not (dataclasses.is_dataclass(obj) and not isinstance(obj, type)) or isinstance(
        obj, Fact
    ):
        return obj
    updates: dict[str, Any] = {}
    for f in dataclasses.fields(obj):
        if not f.init:
            continue
        cur = getattr(obj, f.name)
        new = _rewrite(cur, cls, fname, value, hits)
        if new is not cur:
            updates[f.name] = new
    if type(obj) is cls:
        hits.append(1)
        updates[fname] = value
    ir = updates.get("semantic_ir")
    if isinstance(obj, AbiSnapshot) and ir is not None and ir.declarations is not None:
        # ``replace`` forwards the snapshot's *current* declarations as
        # builder inputs, which would win over the rewritten store the new
        # IR carries -- so state the rewritten kinds explicitly.
        for kind in DECLARATION_KINDS:
            updates[kind] = getattr(ir.declarations, kind)
    return dataclasses.replace(obj, **updates) if updates else obj


def ablate_fact(
    snap: AbiSnapshot, site: tuple[type, str], fact: Fact[Any]
) -> tuple[AbiSnapshot, int]:
    """Set the ``Fact`` at *site* to *fact* on every instance; return count."""
    hits: list[int] = []
    out = _rewrite(snap, site[0], site[1], fact, hits)
    return out, len(hits)


def present_fact_count(snap: AbiSnapshot, site: tuple[type, str]) -> int:
    """Instances of ``site`` whose baseline ``Fact`` is present -- a site
    whose facts are all unknown already cannot be ablated meaningfully."""
    count = 0
    seen: set[int] = set()

    def walk(obj: Any) -> None:
        nonlocal count
        if isinstance(obj, list | tuple):
            for x in obj:
                walk(x)
            return
        if isinstance(obj, Mapping):
            for x in obj.values():
                walk(x)
            return
        if isinstance(obj, Fact) or not (
            dataclasses.is_dataclass(obj) and not isinstance(obj, type)
        ):
            return
        if id(obj) in seen:
            return
        seen.add(id(obj))
        value = getattr(obj, site[1], None) if type(obj) is site[0] else None
        if isinstance(value, Fact) and value.is_present:
            count += 1
        for f in dataclasses.fields(obj):
            walk(getattr(obj, f.name))

    walk(snap)
    return count


def _drop(field: str) -> Callable[[AbiSnapshot], AbiSnapshot | None]:
    def op(s: AbiSnapshot) -> AbiSnapshot | None:
        return (
            dataclasses.replace(s, **{field: None})
            if getattr(s, field) is not None
            else None
        )

    return op


def _empty_export_table(s: AbiSnapshot) -> AbiSnapshot | None:
    if s.elf is None or not s.elf.symbols:
        return None
    return dataclasses.replace(s, elf=dataclasses.replace(s.elf, symbols=[]))


def _unread_export_table(s: AbiSnapshot) -> AbiSnapshot | None:
    """The export table was never read (a default/parse-failed platform
    block), and every lookup against it answered ``ABSENT`` -- then the
    builders' real shared tail runs, which is what must withdraw those
    absences (the #1384/#1385 chain)."""
    if s.elf is None:
        return None
    s = dataclasses.replace(
        s,
        elf=ElfMetadata(),
        functions=[
            dataclasses.replace(f, binary_exported_fact=Fact.present(False))
            for f in s.declarations.functions
        ],
        variables=[
            dataclasses.replace(v, binary_exported_fact=Fact.present(False))
            for v in s.declarations.variables
        ],
    )
    return finish_binary_snapshot(s)


def _unread_platform_table(
    platform: str,
) -> Callable[[AbiSnapshot], AbiSnapshot | None]:
    """The PE/Mach-O block a failed or skipped parse leaves: no header
    fields, no exports -- then the builders' shared tail runs."""

    def op(s: AbiSnapshot) -> AbiSnapshot | None:
        if getattr(s, platform) is None:
            return None
        blank = PeMetadata() if platform == "pe" else MachoMetadata()
        return finish_binary_snapshot(dataclasses.replace(s, **{platform: blank}))

    return op


def _no_canonical_ir(s: AbiSnapshot) -> AbiSnapshot | None:
    if s.semantic_ir is None or not s.semantic_ir.canonical:
        return None
    return dataclasses.replace(s, semantic_ir=None)


def _unscanned_identifiers(
    fact: Fact[frozenset[str] | None],
) -> Callable[[AbiSnapshot], AbiSnapshot | None]:
    def op(s: AbiSnapshot) -> AbiSnapshot | None:
        if s.public_header_identifiers is None:
            return None
        return dataclasses.replace(
            s, public_header_identifiers=None, public_header_identifiers_fact=fact
        )

    return op


#: Container ablations: "missing" (container never captured) plus, for the
#: export table, "returns empty" and "read failed". Keyed by inventory site.
#: A silently *truncated* table is deliberately absent: the model carries no
#: completeness marker on ``ElfMetadata.symbols``, so a table holding half its
#: entries is observationally identical to a release that removed the other
#: half -- reporting those removals is correct, not an F1 violation. F1 is
#: about *signalled* unknowns (a status, a missing/failed block).
CONTAINER_ABLATIONS: dict[
    str, dict[str, Callable[[AbiSnapshot], AbiSnapshot | None]]
] = {
    "AbiSnapshot.elf": {
        "missing": _drop("elf"),
        "empty_export_table": _empty_export_table,
        "unread_export_table": _unread_export_table,
    },
    "AbiSnapshot.dwarf": {"missing": _drop("dwarf")},
    # PE / Mach-O: never captured, or captured but the table never parsed.
    "AbiSnapshot.pe": {
        "missing": _drop("pe"),
        "unread_export_table": _unread_platform_table("pe"),
    },
    "AbiSnapshot.macho": {
        "missing": _drop("macho"),
        "unread_export_table": _unread_platform_table("macho"),
    },
    **{
        f"AbiSnapshot.{name}": {"missing": _drop(name)}
        for name in (
            "dwarf_advanced",
            "sycl",
            "kabi",
            "python_api",
            "python_ext",
            "numpy_capi",
            "extraction_scope",
            "dependency_info",
            "build_mode",
            "build_source_pack",
            "build_source",
            "surface_graph",
            "contract",
        )
    },
    # The canonical IR was never built (a DWARF-only/PE-only extraction, a
    # pre-v38 snapshot): the declarations stay, the occurrences go.
    "AbiSnapshot.semantic_ir": {"missing": _no_canonical_ir},
    # The header-identifier index (schema v55): never scanned, or the scan
    # failed. Either must leave an undeclared export UNKNOWN_UNRESOLVED, never
    # "searched completely, no commitment".
    "AbiSnapshot.public_header_identifiers": {
        "missing": _unscanned_identifiers(Fact.not_collected()),
        "read_failed": _unscanned_identifiers(Fact.failed("header unreadable")),
    },
}


def apply_sided(
    old: AbiSnapshot,
    new: AbiSnapshot,
    side: str,
    op: Callable[[AbiSnapshot], AbiSnapshot],
) -> tuple[AbiSnapshot, AbiSnapshot]:
    return (
        op(old) if side in ("old", "both") else old,
        op(new) if side in ("new", "both") else new,
    )


# --------------------------------------------------------------------------
# Oracles
# --------------------------------------------------------------------------

_CLEAN = {Verdict.NO_CHANGE, Verdict.COMPATIBLE, Verdict.COMPATIBLE_WITH_RISK}
_BROKEN = {Verdict.BREAKING, Verdict.API_BREAK}
#: Independent restatement of the documented confidence order.
_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}


@dataclass(frozen=True)
class Outcome:
    """The oracle-relevant projection of one ``compare()`` result."""

    verdict: Verdict
    breaking: frozenset[tuple[str, str]]
    confidence: int
    warnings: frozenset[str]
    assurance: tuple[Any, ...]
    coverage_floor: int


#: Compare configurations: the default pipeline, and ADR-049 contract
#: evaluation over the export domain (the configuration #1384/#1385 broke).
CONFIGS: dict[str, dict[str, Any]] = {
    "default": {},
    "contract_exports": {"contract_evaluation": True, "contract_mode": "exports"},
    # The domain that reads the header-identifier index and the captured
    # type identities (closed-domain decisions, the exact closure walk).
    "contract_public": {"contract_evaluation": True, "contract_mode": "public"},
}


def outcome(old: AbiSnapshot, new: AbiSnapshot, config: str = "default") -> Outcome:
    r = compare(old, new, **CONFIGS[config])
    aa = r.analysis_assurance
    return Outcome(
        verdict=r.verdict,
        breaking=frozenset(
            (c.kind.value, c.symbol) for c in r.changes if c.kind in BREAKING_KINDS
        ),
        confidence=_CONFIDENCE_RANK.get(
            str(getattr(r.confidence, "value", r.confidence)).lower(), 0
        ),
        warnings=frozenset(r.coverage_warnings),
        assurance=(
            aa.status,
            aa.notes,
            aa.layout_unverified_detectors,
            aa.graph_completeness,
        )
        if aa is not None
        else (),
        coverage_floor=coverage_exit_floor(r),
    )


def gap_stated(full: Outcome, ablated: Outcome) -> bool:
    """The ablated report *states a gap* relative to the full one.

    Precisely: at least one of (1) its overall confidence is lower, (2) it
    carries a coverage warning the full-evidence run did not, or (3) its
    analysis-assurance record (status, notes, layout-unverified detectors,
    graph completeness) differs, or (4) its ADR-049 contract-coverage ledger
    contributes a higher exit floor (a required evidence domain incomplete). Any of these is a user-visible statement
    that the ablated run knew less; none of them is the verdict itself."""
    return (
        ablated.confidence < full.confidence
        or bool(ablated.warnings - full.warnings)
        or ablated.assurance != full.assurance
        or ablated.coverage_floor > full.coverage_floor
    )


#: The same layout observation reported by two layers: the DWARF layout
#: detector's kind and the header-layer kind for one record. With the header
#: layer's evidence unknown (e.g. ``CanonicalEntity.size_bits``) the header
#: detector stands down and the DWARF one reports the change instead -- the
#: break is kept, not fabricated. Spelled out here, not imported from the
#: pipeline's dedup table, so the oracle does not reuse what it checks.
_SAME_OBSERVATION: dict[str, str] = {
    "struct_size_changed": "type_size_changed",
    "struct_alignment_changed": "type_alignment_changed",
    "struct_field_offset_changed": "type_field_offset_changed",
    "struct_field_removed": "type_field_removed",
    "struct_field_type_changed": "type_field_type_changed",
}


def _fabricated(full: Outcome, ablated: Outcome) -> list[tuple[str, str]]:
    return sorted(
        (kind, symbol)
        for kind, symbol in ablated.breaking - full.breaking
        if (_SAME_OBSERVATION.get(kind), symbol) not in full.breaking
    )


def oracle_violations(case: str, full: Outcome, ablated: Outcome) -> list[str]:
    """Check the three F1 oracles; return human-readable violations."""
    out = []
    if (
        full.verdict in _BROKEN
        and ablated.verdict in _CLEAN
        and not gap_stated(full, ablated)
    ):
        out.append(
            f"(a) silent clean: {full.verdict.value} -> {ablated.verdict.value} with no stated gap"
        )
    extra = _fabricated(full, ablated)
    if extra:
        out.append(f"(b) fabricated BREAKING findings: {sorted(extra)}")
    if case == "identical" and ablated.verdict in _BROKEN:
        out.append(f"(c) identical pair became {ablated.verdict.value}")
    return out


def iter_fact_cells(
    sites: dict[str, tuple[type, str]], statuses: tuple[str, ...]
) -> Iterator[tuple[str, str, str, str]]:
    for case in CORPUS:
        for site in sites:
            for status in statuses:
                for side in SIDES:
                    yield case, site, status, side
