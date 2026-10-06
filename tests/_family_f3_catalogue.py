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

"""Harness H3 (defect family F3, "identity is semantic") -- the transform
catalogue and its oracles, as data.

``docs/contribute/plans/defect-family-harnesses.md`` § H3 asks for ONE
catalogue that every environment-metamorphic identity test is an instance
of. This module is that catalogue; ``tests/test_family_f3_identity.py``
drives it. Two transform kinds:

* ``must_not_change`` -- an environment change (checkout relocation,
  symlinked root, ``./``/``..`` spellings, Windows separators, reversed
  collection order, ``PYTHONHASHSEED``) under which every identity key must
  be equal, and a comparison must read ``NO_CHANGE`` with zero findings.
* ``codec`` -- a platform decoration scheme (Mach-O ``_``, x86 PE
  cdecl/stdcall/fastcall/vectorcall, Itanium ctor/dtor ``C1/C2/C3`` and
  ``D0/D1/D2`` variants). The property is a *join*: decorated and
  undecorated spellings of one entity decode to one identity, and two
  distinct entities never decode to the same one (negative control).

Every oracle looks production functions up through their *module
attribute* at call time (never a captured reference), so the seeded-mutant
self-check can monkeypatch a historical bug back in and prove the oracle
reports it.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import abicheck.checker as checker_mod
import abicheck.extract.export_symbol_identity as export_ident_mod
import abicheck.extract.headers.clang.context as clang_ctx_mod
import abicheck.finding_identity as finding_mod
import abicheck.model.export_index as export_index_mod
import abicheck.model.graph_entity_identity as gei_mod
import abicheck.model.graph_identity as gid_mod
import abicheck.model.identity as ident_mod
import abicheck.model.name_decoration.elf_version as elf_codec
import abicheck.model.name_decoration.macho as macho_codec
import abicheck.model.name_decoration.pe_x86 as pe_codec
import abicheck.model.source_graph as sg_mod
import abicheck.model.special_member_identity as smi_mod
import abicheck.name_classification as nc_mod
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
)
from abicheck.model.pe_facts import PeExport, PeMetadata

# ---------------------------------------------------------------------------
# must_not_change: path-spelling transforms (string level)
# ---------------------------------------------------------------------------

BASE_ROOT = "/work/checkout_a"
BASE_HEADER = f"{BASE_ROOT}/inc/api.h"


@dataclass(frozen=True)
class Transform:
    name: str
    kind: str  # "must_not_change" | "codec"
    fn: Callable[[str], str]
    bugs: tuple[int, ...]


def _dir_base(path: str) -> tuple[str, str]:
    head, _, tail = path.rpartition("/")
    return head, tail


PATH_TRANSFORMS: tuple[Transform, ...] = (
    Transform(
        "relocate_checkout",
        "must_not_change",
        lambda p: p.replace(BASE_ROOT, "/srv/ci/an/unrelated/deeper/checkout_b"),
        (1343, 1355, 1383),
    ),
    Transform(
        # A symlinked root reaches identity code as just another absolute
        # prefix spelling of the same tree (#1330).
        "symlinked_root_spelling",
        "must_not_change",
        lambda p: p.replace(BASE_ROOT, "/home/dev/link_to_checkout"),
        (1330,),
    ),
    Transform(
        "relocate_checkout_with_space",
        "must_not_change",
        lambda p: p.replace(BASE_ROOT, "/home/dev/my projects/checkout b"),
        (1343, 1383),
    ),
    Transform(
        "redundant_dot_segment",
        "must_not_change",
        lambda p: "{}/./{}".format(*_dir_base(p)),
        (1330,),
    ),
    Transform(
        "redundant_dotdot_segment",
        "must_not_change",
        lambda p: (lambda d, b: f"{d}/../{d.rpartition('/')[2]}/{b}")(*_dir_base(p)),
        (1330,),
    ),
    Transform(
        "windows_separators",
        "must_not_change",
        lambda p: "C:" + p.replace("/", "\\"),
        (1383,),
    ),
    Transform(
        "relative_spelling",
        "must_not_change",
        lambda p: p.replace(BASE_ROOT + "/", ""),
        (1383,),
    ),
)

TRANSFORMS_BY_NAME = {t.name: t for t in PATH_TRANSFORMS}

# Spellings that embed a declaring-header path, the way clang/castxml spell
# a closure or anonymous tag. ``{p}`` is the header path.
LAMBDA_A = "lib::Guard<(lambda at {p}:4:37)>"
LAMBDA_B = "lib::Guard<(lambda at {p}:40:3)>"  # distinct lambda, same header
LAMBDA_OTHER_NS = "other::Guard<(lambda at {p}:4:37)>"  # same leaf, other scope
UNNAMED = "lib::Holder<(unnamed struct at {p}:9:5)>"
SPELLING_TEMPLATES = (LAMBDA_A, UNNAMED)


def spell(template: str, path: str) -> str:
    return template.format(p=path)


# ---------------------------------------------------------------------------
# Identity probes: spelling -> key, via module attributes (mutant-patchable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Probe:
    """One production identity function reduced to ``spelling -> key``.

    *distinguishes_same_header_lambdas* is False for the primitives that
    deliberately drop the ``:line:col`` discriminator (comparison-only or
    rename-classification keys), so the negative control does not demand a
    distinction the primitive's own contract rules out.
    """

    qualname: str
    key: Callable[[str], str]
    distinguishes_same_header_lambdas: bool = True


def _fn_identity(spelling: str) -> str:
    f = Function(
        name="lib::f",
        mangled="",
        return_type="int",
        params=[Param(name="g", type=spelling)],
    )
    return finding_mod.resolve_function_identity(f).primary_id


STRING_PROBES: tuple[Probe, ...] = (
    Probe(
        "abicheck.name_classification:strip_anonymous_type_location",
        lambda s: nc_mod.strip_anonymous_type_location(s),
    ),
    Probe(
        "abicheck.name_classification:canonicalize_type_name",
        lambda s: nc_mod.canonicalize_type_name(s),
        distinguishes_same_header_lambdas=False,
    ),
    Probe(
        "abicheck.model.graph_identity:checkout_stable_spelling",
        lambda s: gid_mod.checkout_stable_spelling(s),
    ),
    Probe(
        "abicheck.model.graph_identity:closure_location_free_identity",
        lambda s: gid_mod.closure_location_free_identity(s),
        distinguishes_same_header_lambdas=False,
    ),
    Probe(
        "abicheck.model.graph_entity_identity:signature_key",
        lambda s: gei_mod.signature_key(f"int ({s})"),
    ),
    Probe(
        "abicheck.model.graph_entity_identity:type_identity",
        lambda s: gei_mod.type_identity(s).node_id,
    ),
    Probe(
        "abicheck.model.graph_entity_identity:unresolved_identity",
        lambda s: gei_mod.unresolved_identity("decl", s).node_id,
    ),
    Probe(
        "abicheck.model.graph_entity_identity:declaration_identity",
        lambda s: (
            gei_mod.declaration_identity(
                qualified_name=f"{s}::run", signature=gei_mod.signature_key("int ()")
            ).node_id
        ),
    ),
    Probe(
        "abicheck.model.source_graph:function_decl_identity",
        lambda s: sg_mod.function_decl_identity("", "run", "lib::run", f"int ({s})"),
    ),
    Probe(
        # resolve_function_identity folds each parameter spelling through
        # canonicalize_type_name, which drops :line:col by design.
        "abicheck.finding_identity:resolve_function_identity",
        _fn_identity,
        distinguishes_same_header_lambdas=False,
    ),
)

PROBES_BY_NAME = {p.qualname: p for p in STRING_PROBES}


def string_violations(probe: Probe, transform: Transform) -> list[str]:
    """Every (template) where *transform* changed *probe*'s key."""
    out = []
    for template in SPELLING_TEMPLATES:
        before = probe.key(spell(template, BASE_HEADER))
        after = probe.key(spell(template, transform.fn(BASE_HEADER)))
        if before != after:
            out.append(f"{template!r}: {before!r} != {after!r}")
    return out


def string_collisions(probe: Probe, transform: Transform) -> list[str]:
    """Negative control: distinct entities that collapsed under *transform*."""
    path = transform.fn(BASE_HEADER)
    pairs = [(LAMBDA_A, LAMBDA_OTHER_NS)]
    if probe.distinguishes_same_header_lambdas:
        pairs.append((LAMBDA_A, LAMBDA_B))
    out = []
    for x, y in pairs:
        kx, ky = probe.key(spell(x, path)), probe.key(spell(y, path))
        if kx == ky:
            out.append(f"{x!r} and {y!r} both keyed {kx!r}")
    return out


# ---------------------------------------------------------------------------
# Path probes: a declaring path -> key (design-hardening Phase 3)
# ---------------------------------------------------------------------------
#
# Each probe takes the *recorded* path string (the transform's output) the
# way production receives it, so the probe covers the root-relative
# constructor (``RootRelativePath``) together with the identity function.
# Every probe is keyed so the path really reaches its result (REDUCED tier
# or the alias itself): a probe that ignored the path would pass vacuously,
# which the negative control (a different header stays distinct) rules out.

OTHER_HEADER = f"{BASE_ROOT}/inc/other.h"


def _report_entry(path: str) -> dict[str, Any]:
    return {
        "kind": "func_removed",
        "symbol": "",
        "description": "removed",
        "source_location": f"{path}:3",
    }


def _path_probes() -> dict[str, Callable[[str], str]]:
    from abicheck.checker_types import Change
    from abicheck.model.change_catalog.kinds import ChangeKind
    from abicheck.model.entity_identity import (
        resolve_identity_for_node,
        source_relative_identity,
    )
    from abicheck.model.graph_facts import GraphNode
    from abicheck.model.root_relative_path import RootRelativePath
    from abicheck.workflows.aggregate import reconcile as agg

    def rel(path: str) -> RootRelativePath | None:
        return RootRelativePath.from_project_layout(path)

    return {
        "abicheck.model.entity_identity:source_relative_identity": (
            lambda p: source_relative_identity(rel(p), "lib", "run")
        ),
        # Production boundary: an L5 graph node's recorded ``def_file``.
        "abicheck.model.entity_identity:resolve_canonical_identity": (
            lambda p: (
                resolve_identity_for_node(
                    GraphNode(
                        id="n", kind="record_type", label="", attrs={"def_file": p}
                    )
                ).primary_id
            )
        ),
        "abicheck.finding_identity:resolve_symbol_identity": (
            lambda p: (
                finding_mod.resolve_symbol_identity(
                    kind="function", source_location=rel(f"{p}:3")
                ).primary_id
            )
        ),
        "abicheck.finding_identity:resolve_change_identity": (
            lambda p: (
                finding_mod.resolve_change_identity(
                    Change(
                        kind=ChangeKind.FUNC_REMOVED,
                        symbol="",
                        description="removed",
                        source_location=f"{p}:3",
                    )
                ).primary_id
            )
        ),
        "abicheck.workflows.aggregate.reconcile:resolve_report_change_identity": (
            lambda p: agg.resolve_report_change_identity(_report_entry(p)).primary_id
        ),
    }


def path_violations(probe_name: str, transform: Transform) -> list[str]:
    key = _path_probes()[probe_name]
    out = []
    before, after = key(BASE_HEADER), key(transform.fn(BASE_HEADER))
    if before != after:
        out.append(f"{BASE_HEADER!r}: {before!r} != {after!r}")
    other = key(transform.fn(OTHER_HEADER))
    if other == after:
        out.append(f"negative control: api.h and other.h both keyed {after!r}")
    return out


#: Identity functions celled in ``tests/test_family_f3_identity_cells.py``
#: (one ``test_cell_<function>`` each) -- configuration, storage and
#: report keys whose environment contract is not a declaring path.
ENVIRONMENT_CELLS: tuple[str, ...] = (
    "abicheck.model.identity_tiers:snapshot_local_identity",
    "abicheck.model.extraction_scope:extraction_scope_identity",
    "abicheck.model.extraction_scope:snapshot_scope_identity",
    "abicheck.model.header_exclusion_record:canonical_exclusion_identity",
    "abicheck.model.header_exclusion_record:comparison_exclusion_identity",
    "abicheck.model.header_exclusion_record:release_exclusion_identity",
    "abicheck.policy.rule_identity:rule_identity",
    "abicheck.compatibility_evaluation_frontend:builtin_policy_identity",
    "abicheck.compatibility_evaluation_frontend:severity_preset_identity",
    "abicheck.frontends.action.library_selection:read_elf_identity",
    "abicheck.workflows.release_public_surface:build_side_identity",
    "abicheck.bundle:stored_capture_identity",
    "abicheck.workflows.aggregate.reconcile:resolve_cross_abi_identity",
    "abicheck.compare.template_surface:alias_identity",
    "abicheck.compare.template_surface:cpo_identity",
    "abicheck.extract.ownership_stamp:recorded_rules",
)


# ---------------------------------------------------------------------------
# codec: Mach-O leading underscore
# ---------------------------------------------------------------------------

DARWIN_TRIPLE = "arm64-apple-darwin23.0.0"
ITANIUM_NAMES = (
    "_ZN3lib3addEii",
    "_ZN3lib3addEdd",  # overload of the above
    "_ZN1a1fEv",
    "_ZN1b1fEv",  # same leaf, other namespace
    "_Z1fv",
)
C_NAMES = ("f", "g", "_f", "add")  # "f" vs "_f": two real, distinct C names


def macho_encode(name: str) -> str:
    return "_" + name


def macho_decoders() -> dict[str, Callable[[str, str, bool], str]]:
    """decoder(linker_spelling, plain_name, is_extern_c) -> identity key."""
    return {
        "abicheck.model.name_decoration.macho:decode_itanium": (
            lambda s, _plain, c: s if c else macho_codec.decode_itanium(s)
        ),
        "abicheck.model.graph_entity_identity:declaration_identity": (
            lambda s, plain, _c: (
                gei_mod.declaration_identity(linker_name=s, plain_name=plain).node_id
            )
        ),
        "abicheck.extract.headers.clang.context:strip_darwin_itanium_decoration": (
            lambda s, plain, c: clang_ctx_mod.strip_darwin_itanium_decoration(
                s, s, DARWIN_TRIPLE, name=plain, is_extern_c=c
            )
        ),
    }


def _macho_corpus() -> list[tuple[str, str, bool]]:
    """(undecorated linker name, plain name, is_extern_c)."""
    # A C++ producer hands over the *source* leaf name as the plain name,
    # never the mangling itself.
    leaves = ("add", "add", "f", "f", "f")
    itanium = [(n, leaf, False) for n, leaf in zip(ITANIUM_NAMES, leaves, strict=True)]
    return itanium + [(n, n, True) for n in C_NAMES]


def macho_violations(decoder_name: str) -> list[str]:
    """Join + negative control for one Mach-O decoder.

    The strip-only primitive does not claim the C-linkage case (it cannot
    tell ``_foo`` from a real ``asm("_foo")`` label without the caller's
    extern-C fact), so it is held to the Itanium half only.
    """
    dec = macho_decoders()[decoder_name]
    corpus = _macho_corpus()
    if decoder_name.endswith("decode_itanium"):
        corpus = [c for c in corpus if not c[2]]
    out: list[str] = []
    decoded: dict[str, str] = {}
    for name, plain, is_c in corpus:
        plain_key = dec(name, plain, is_c)
        enc_key = dec(macho_encode(name), plain, is_c)
        if plain_key != enc_key:
            out.append(f"join: {name!r} -> {plain_key!r} but decorated -> {enc_key!r}")
        if enc_key in decoded:
            out.append(f"collision: {name!r} and {decoded[enc_key]!r} -> {enc_key!r}")
        decoded[enc_key] = name
    return out


# ---------------------------------------------------------------------------
# codec: x86 PE C-linkage calling-convention decoration
# ---------------------------------------------------------------------------

I386 = "IMAGE_FILE_MACHINE_I386"
AMD64 = "IMAGE_FILE_MACHINE_AMD64"
PE_C_NAMES = ("f", "g", "_f", "Add2")
PE_SCHEMES: dict[str, Callable[[str, int], str]] = {
    "cdecl": lambda n, _b: f"_{n}",
    "stdcall": lambda n, b: f"_{n}@{b}",
    "fastcall": lambda n, b: f"@{n}@{b}",
    "vectorcall": lambda n, b: f"{n}@@{b}",
}


def _pe_alias_decoder(spelling: str) -> str:
    """The join's alias table, read back as a decoder over one export."""
    snap = AbiSnapshot(
        library="libx.dll",
        version="1",
        pe=PeMetadata(machine=I386, exports=[PeExport(name=spelling)]),
    )
    table = export_index_mod.pe_decoration_aliases(snap, [spelling])
    return next((b for b, raw in table.items() if spelling in raw), "")


def pe_decoders() -> dict[str, Callable[[str], str]]:
    return {
        "abicheck.model.name_decoration.pe_x86:decode_c_name": (
            lambda s: pe_codec.decode_c_name(s, x86_32=True)
        ),
        "abicheck.model.export_index:pe_decoration_aliases": _pe_alias_decoder,
        "abicheck.extract.export_symbol_identity:msvc_export_function": (
            lambda s: (
                export_ident_mod.msvc_export_function(
                    s, is_x86_32=True
                ).entity_id.leaf_name
            )
        ),
    }


def pe_violations(decoder_name: str, scheme: str) -> list[str]:
    dec = pe_decoders()[decoder_name]
    enc = PE_SCHEMES[scheme]
    out: list[str] = []
    seen: dict[str, str] = {}
    for name in PE_C_NAMES:
        for arg_bytes in (0, 8):
            got = dec(enc(name, arg_bytes)) or enc(name, arg_bytes)
            if got != name:
                out.append(
                    f"join: {scheme} {enc(name, arg_bytes)!r} -> {got!r}, want {name!r}"
                )
        key = dec(enc(name, 8)) or enc(name, 8)
        if key in seen and seen[key] != name:
            out.append(f"collision: {name!r} and {seen[key]!r} -> {key!r}")
        seen[key] = name
    return out


def pe_x64_collisions() -> list[str]:
    """Off 32-bit x86 a leading ``_`` is part of the real name (PE has no
    cdecl/stdcall decoration there), so ``_f`` and ``f`` stay distinct
    under every decoration a non-x86 machine still applies."""
    out = []
    for enc in (lambda n: n, lambda n: f"{n}@@8"):
        a = export_ident_mod.msvc_export_function(enc("f"), is_x86_32=False).entity_id
        b = export_ident_mod.msvc_export_function(enc("_f"), is_x86_32=False).entity_id
        if a == b:
            out.append(f"msvc_export_function: {enc('f')!r} and {enc('_f')!r} -> {a}")
        pa, pb = (
            pe_codec.decode_c_name(enc("f"), x86_32=False) or enc("f"),
            pe_codec.decode_c_name(enc("_f"), x86_32=False) or enc("_f"),
        )
        if pa == pb:
            out.append(
                f"pe_x86.decode_c_name: {enc('f')!r} and {enc('_f')!r} -> {pa!r}"
            )
    return out


# ---------------------------------------------------------------------------
# codec: Itanium ctor/dtor variants
# ---------------------------------------------------------------------------

CTOR_FAMILIES: dict[str, tuple[str, ...]] = {
    "W(int)": ("_ZN3lib1WC1Ei", "_ZN3lib1WC2Ei", "_ZN3lib1WC3Ei"),
    "W(double)": ("_ZN3lib1WC1Ed", "_ZN3lib1WC2Ed"),  # overload
    "~W": ("_ZN3lib1WD0Ev", "_ZN3lib1WD1Ev", "_ZN3lib1WD2Ev"),
    "other::W(int)": ("_ZN5other1WC1Ei", "_ZN5other1WC2Ei"),  # same leaf
    # A class whose own name embeds "C1E": a substring search would find the
    # wrong marker (the historical template_graph false positive).
    "C1Evil<int>()": ("_ZN6C1EvilIiEC1Ev", "_ZN6C1EvilIiEC2Ev"),
    "C2Evil<int>()": ("_ZN6C2EvilIiEC1Ev", "_ZN6C2EvilIiEC2Ev"),
}


def ctor_dtor_violations() -> list[str]:
    exports = {s for fam in CTOR_FAMILIES.values() for s in fam}
    out = []
    for label, fam in CTOR_FAMILIES.items():
        for sym in fam:
            joined = {sym, *smi_mod.special_member_variant_aliases(sym, exports)}
            if joined != set(fam):
                out.append(
                    f"{label}: {sym!r} joined {sorted(joined)}, want {sorted(fam)}"
                )
    return out


# ---------------------------------------------------------------------------
# codec: ELF symbol-version suffixes
# ---------------------------------------------------------------------------

# Distinct leaves: the leaf parser deliberately joins overloads.
ELF_NAMES = ("inflate", "inflate_", "_Z3addii", "_ZN2ns3subEv")
ELF_SUFFIXES: dict[str, Callable[[str], str]] = {
    "unversioned": lambda n: n,
    "default": lambda n: f"{n}@@LIB_1.2",
    "hidden": lambda n: f"{n}@LIB_1.0",
}


def elf_version_decoders() -> dict[str, Callable[[str], str | None]]:
    from abicheck.model.symbol_leaf import symbol_leaf_identifier

    return {
        "abicheck.model.name_decoration.elf_version:unversioned_name": (
            elf_codec.unversioned_name
        ),
        # The leaf parser keys a header lookup off the unversioned name.
        "abicheck.model.symbol_leaf:symbol_leaf_identifier": symbol_leaf_identifier,
    }


def elf_version_violations(decoder_name: str) -> list[str]:
    dec = elf_version_decoders()[decoder_name]
    out: list[str] = []
    seen: dict[object, str] = {}
    for name in ELF_NAMES:
        keys = {label: dec(enc(name)) for label, enc in ELF_SUFFIXES.items()}
        if len(set(keys.values())) != 1:
            out.append(f"join: {name!r} -> {keys}")
        key = keys["default"]
        if key is not None and key in seen and seen[key] != name:
            out.append(f"collision: {name!r} and {seen[key]!r} -> {key!r}")
        seen[key] = name
    return out


# ---------------------------------------------------------------------------
# Snapshot level (default lane): the production load path is the normalizer
# ---------------------------------------------------------------------------


def build_snapshot(
    root: str = BASE_ROOT, *, version: str = "1", changed: bool = False
) -> AbiSnapshot:
    """A small library whose identity-bearing spellings embed *root*.

    *changed* produces a real, known set of findings so finding-id equality
    under a transform is not vacuous.
    """
    hdr = f"{root}/inc/api.h"
    lam_a, lam_b = spell(LAMBDA_A, hdr), spell(LAMBDA_B, hdr)
    fns = [
        Function(
            name="lib::add",
            mangled="_ZN3lib3addEii",
            return_type="int",
            params=[Param("a", "int"), Param("b", "int")],
            source_location=f"{hdr}:3",
        ),
        Function(
            name="lib::add",
            mangled="_ZN3lib3addEdd",
            return_type="double",
            params=[Param("a", "double"), Param("b", "double")],
            source_location=f"{hdr}:4",
        ),
        Function(
            name="other::add",
            mangled="_ZN5other3addEii",
            return_type="int",
            params=[Param("a", "int"), Param("b", "int")],
            source_location=f"{hdr}:5",
        ),
        Function(
            name="c_entry",
            mangled="c_entry",
            return_type="void",
            is_extern_c=True,
            source_location=f"{hdr}:6",
        ),
        Function(
            name=f"{lam_a}::run",
            mangled=f"__abicheck_ctor__{lam_a}()",
            return_type="int",
        ),
        Function(
            name=f"{lam_b}::run",
            mangled=f"__abicheck_ctor__{lam_b}()",
            return_type="int",
        ),
        Function(
            name="lib::use",
            mangled="_ZN3lib3useEv",
            return_type="int",
            params=[Param("g", f"{lam_a}&")],
        ),
    ]
    if changed:
        fns[0] = copy.deepcopy(fns[0])
        fns[0].return_type = "long"
        fns = [f for f in fns if f.name != "other::add"]
    point_fields = [TypeField("x", "int", 0), TypeField("y", "int", 32)]
    if changed:
        point_fields.append(TypeField("z", "int", 64))
    types = [
        RecordType(
            name="lib::Point",
            kind="struct",
            size_bits=96 if changed else 64,
            fields=point_fields,
            source_location=f"{hdr}:10",
        ),
        RecordType(
            name="other::Point",
            kind="struct",
            size_bits=32,
            fields=[TypeField("v", "int", 0)],
            source_location=f"{hdr}:11",
        ),
        RecordType(
            name=lam_a, kind="class", size_bits=8, fields=[TypeField("fn_", lam_a, 0)]
        ),
        RecordType(
            name=lam_b, kind="class", size_bits=8, fields=[TypeField("fn_", lam_b, 0)]
        ),
    ]
    variables = [
        Variable(
            name="lib::counter",
            mangled="_ZN3lib7counterE",
            type="int",
            source_location=f"{hdr}:20",
        ),
        Variable(
            name="other::counter",
            mangled="_ZN5other7counterE",
            type="int",
            source_location=f"{hdr}:21",
        ),
    ]
    enums = [
        EnumType(
            name="lib::Mode",
            members=[EnumMember("A", 0), EnumMember("B", 1)],
            source_location=f"{hdr}:30",
        )
    ]
    return AbiSnapshot(
        library="libapi.so.1",
        version=version,
        functions=fns,
        types=types,
        variables=variables,
        enums=enums,
    )


def _map_strings(obj: Any, fn: Callable[[str], str]) -> Any:
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, list):
        return [_map_strings(x, fn) for x in obj]
    if isinstance(obj, dict):
        return {k: _map_strings(v, fn) for k, v in obj.items()}
    return obj


_PATH_IN_TEXT = re.compile(re.escape(BASE_HEADER))


def snapshot_transform(snap: AbiSnapshot, transform_name: str) -> dict[str, Any]:
    """Apply one catalogue transform to *snap*'s stored document."""
    from abicheck.serialization import snapshot_to_dict

    doc = snapshot_to_dict(snap)
    if transform_name == "identity":
        return doc
    if transform_name == "reverse_collection_order":
        for key in ("functions", "variables", "types", "enums", "typedefs"):
            if isinstance(doc.get(key), list):
                doc[key] = list(reversed(doc[key]))
        return doc
    t = TRANSFORMS_BY_NAME[transform_name]
    new_header = t.fn(BASE_HEADER)
    return _map_strings(doc, lambda s: _PATH_IN_TEXT.sub(lambda _m: new_header, s))


SNAPSHOT_TRANSFORMS = (*(t.name for t in PATH_TRANSFORMS), "reverse_collection_order")


def load(doc: dict[str, Any]) -> AbiSnapshot:
    from abicheck.serialization import snapshot_from_dict

    return snapshot_from_dict(json.loads(json.dumps(doc)))


def compare(old: AbiSnapshot, new: AbiSnapshot) -> Any:
    return checker_mod.compare(old, new, cross_source_checks=False)


def finding_ids(result: Any) -> list[str]:
    """Report finding ids in emitted order."""
    return [finding_mod.report_canonical_finding_id(c) for c in result.changes]


def snapshot_identity_keys(snap: AbiSnapshot) -> list[str]:
    keys = [
        finding_mod.resolve_function_identity(f).primary_id
        for f in snap.declarations.functions
    ]
    keys += [
        finding_mod.resolve_variable_identity(v).primary_id
        for v in snap.declarations.variables
    ]
    keys += [
        f"type:{nc_mod.strip_anonymous_type_location(t.name)}"
        for t in snap.declarations.types
    ]
    keys += [f"enum:{e.name}" for e in snap.declarations.enums]
    return sorted(keys)


def entity_count(snap: AbiSnapshot) -> int:
    return (
        len(snap.declarations.functions)
        + len(snap.declarations.variables)
        + len(snap.declarations.types)
        + len(snap.declarations.enums)
    )


def hashseed_fingerprint() -> str:
    """Everything identity-shaped this harness observes, as one JSON string
    -- run under several ``PYTHONHASHSEED`` values by a subprocess cell."""
    old = load(snapshot_transform(build_snapshot(), "identity"))
    new = load(
        snapshot_transform(build_snapshot(changed=True, version="2"), "identity")
    )
    result = compare(old, new)
    return json.dumps(
        {
            "finding_ids": finding_ids(result),
            "old_keys": [
                finding_mod.resolve_function_identity(f).primary_id
                for f in old.declarations.functions
            ],
            "entity_ids": [
                repr(ident_mod.entity_id_for_function((), n, mangled_name=n))
                for n in ITANIUM_NAMES
            ],
            "ctor_aliases": [
                smi_mod.special_member_variant_aliases(s, set(fam))
                for fam in CTOR_FAMILIES.values()
                for s in fam
            ],
            "types": [t.name for t in old.declarations.types],
        },
        sort_keys=True,
    )


# ---------------------------------------------------------------------------
# Negative control over EntityId constructors
# ---------------------------------------------------------------------------


def entity_id_collisions() -> list[str]:
    """Distinct entities with similar shapes must get distinct EntityIds."""
    s_lib, s_other = (ident_mod.Namespace("lib"),), (ident_mod.Namespace("other"),)
    cases = {
        "function overloads": (
            ident_mod.entity_id_for_function(
                s_lib, "add", mangled_name="_ZN3lib3addEii"
            ),
            ident_mod.entity_id_for_function(
                s_lib, "add", mangled_name="_ZN3lib3addEdd"
            ),
        ),
        "unmangled overloads": (
            ident_mod.entity_id_for_function(s_lib, "add", param_types=("int",)),
            ident_mod.entity_id_for_function(s_lib, "add", param_types=("double",)),
        ),
        "function same leaf other namespace": (
            ident_mod.entity_id_for_function(
                s_lib, "add", mangled_name="_ZN3lib3addEii"
            ),
            ident_mod.entity_id_for_function(
                s_other, "add", mangled_name="_ZN5other3addEii"
            ),
        ),
        "variable same leaf other namespace": (
            ident_mod.entity_id_for_variable(
                s_lib, "counter", mangled_name="_ZN3lib7counterE"
            ),
            ident_mod.entity_id_for_variable(
                s_other, "counter", mangled_name="_ZN5other7counterE"
            ),
        ),
        "type same leaf other namespace": (
            ident_mod.entity_id_for_type(s_lib, "Point"),
            ident_mod.entity_id_for_type(s_other, "Point"),
        ),
        "enum same leaf other namespace": (
            ident_mod.entity_id_for_enum(s_lib, "Mode"),
            ident_mod.entity_id_for_enum(s_other, "Mode"),
        ),
        "typedef same leaf other namespace": (
            ident_mod.entity_id_for_typedef(s_lib, "handle_t"),
            ident_mod.entity_id_for_typedef(s_other, "handle_t"),
        ),
        "constant same leaf other namespace": (
            ident_mod.entity_id_for_constant(s_lib, "MAX"),
            ident_mod.entity_id_for_constant(s_other, "MAX"),
        ),
        "type vs typedef same spelling": (
            ident_mod.entity_id_for_type(s_lib, "stat"),
            ident_mod.entity_id_for_typedef(s_lib, "stat"),
        ),
    }
    return [f"{label}: {a}" for label, (a, b) in cases.items() if a == b]
