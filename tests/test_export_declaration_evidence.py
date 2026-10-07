"""Undeclared-export accounting against real template/namespace shapes.

Bug class (oneCCL/oneDNN validation): ``exported_not_public`` and
``func_added_elf_only`` judged an export from a *mis-parsed* entity name and
from the absence of a concrete parsed declaration alone. Four symptoms, one
class each:

* a hand-rolled ``I``/``E`` depth counter unbalanced on a ``N…E``/``L…E``
  inside template arguments, so a ``detail::`` namespace of a template
  argument or return type was read as the entity's own scope (internal);
* an instantiation of a publicly declared template was advised "hide it";
* a ``std::`` template instantiated over the library's own types was
  attributed to a statically linked libstdc++;
* a declaration the run could not see (``#ifdef`` region, excluded header)
  was reported as "declared in no public header".

The invariants are exercised over *generated* encodings whose boundaries and
scopes the generator knows independently of the parser (the oracle), plus
real g++ manglings captured from compiled oneDNN/oneCCL-shaped sources.
"""

from __future__ import annotations

import random

import pytest

from abicheck.buildsource.cross_source_checks import (
    CHECK_EXPORTED_NOT_PUBLIC,
    CrosscheckConfig,
    run_crosschecks,
)
from abicheck.buildsource.export_accounting import (
    _entity_owner_is_internal,
    _external_dependency_origin,
    _has_template_args,
)
from abicheck.buildsource.export_declaration_evidence import (
    build_export_declaration_evidence,
    instantiated_over_owned_types,
    textual_declaration_hint,
)
from abicheck.checker_policy import ChangeKind, Confidence
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, RecordType, ScopeOrigin
from abicheck.model.evidence_status import CrossSourceEvolution
from abicheck.model.export_entity_name import (
    PublicTemplateScopes,
    entity_name_components,
    public_template_for_symbol,
    public_template_scopes,
)
from abicheck.model.mangled_name_template_args import skip_template_args
from abicheck.report.change_operation import operation_for_change

# --------------------------------------------------------------------------- #
# Generator: Itanium fragments whose extent the generator knows by construction
# --------------------------------------------------------------------------- #

_NAMES = ("dnnl", "impl", "detail", "ccl", "v1", "stream", "attr_id", "Box", "E", "INE")


def _src(name: str) -> str:
    return f"{len(name)}{name}"


def _type(rng: random.Random, depth: int) -> str:
    choice = rng.randrange(8 if depth < 3 else 4)
    if choice == 0:
        return rng.choice("ibcdfjlmv")
    if choice == 1:
        return _src(rng.choice(_NAMES))
    if choice == 2:
        return f"S{rng.randrange(40)}_" if rng.random() < 0.7 else "S_"
    if choice == 3:
        return f"T{rng.randrange(12)}_" if rng.random() < 0.7 else "T_"
    if choice == 4:
        comps = "".join(_src(rng.choice(_NAMES)) for _ in range(rng.randrange(1, 4)))
        return "N" + comps + "E"
    if choice == 5:
        return "P" + _type(rng, depth + 1)
    if choice == 6:
        return _src(rng.choice(_NAMES)) + _args(rng, depth + 1)
    return (
        "N"
        + _src(rng.choice(_NAMES))
        + _src(rng.choice(_NAMES))
        + _args(rng, depth + 1)
        + "E"
    )


def _arg(rng: random.Random, depth: int) -> str:
    kind = rng.randrange(5)
    if kind == 0:
        return f"Li{rng.randrange(100)}E"
    if kind == 1:  # enumerator of a namespaced enum: L <nested-type> <value> E
        return (
            "LN"
            + _src(rng.choice(_NAMES))
            + _src("attr_id")
            + "E"
            + str(rng.randrange(9))
            + "E"
        )
    if kind == 2 and depth < 3:  # argument pack
        return (
            "J"
            + "".join(_type(rng, depth + 1) for _ in range(rng.randrange(0, 3)))
            + "E"
        )
    if kind == 3 and depth < 3:  # expression
        return "XcviT_E"
    return _type(rng, depth)


def _args(rng: random.Random, depth: int = 0) -> str:
    return "I" + "".join(_arg(rng, depth) for _ in range(rng.randrange(1, 4))) + "E"


@pytest.mark.parametrize("seed", range(400))
def test_skip_template_args_finds_the_generated_closer(seed: int) -> None:
    rng = random.Random(seed)
    args = _args(rng)
    tail = rng.choice(["", "Ev", "E3fooEv", "ES_", "9trailing"])
    assert skip_template_args(args + tail, 0) == len(args), args


@pytest.mark.parametrize("seed", range(400))
def test_entity_components_ignore_names_inside_args_and_signature(seed: int) -> None:
    rng = random.Random(10_000 + seed)
    scope = [
        rng.choice(("dnnl", "ccl", "v1", "attr", "Box"))
        for _ in range(rng.randrange(1, 4))
    ]
    internal_scope = rng.random() < 0.3
    if internal_scope:
        scope.insert(
            rng.randrange(1, len(scope) + 1), rng.choice(("detail", "impl", "internal"))
        )
    leaf = rng.choice(("set", "get", "run", "detail"))
    templated = rng.random() < 0.6
    args = _args(rng) if templated else ""
    # The signature (and, for a template, the return type) routinely names a
    # ``detail::`` type: it must never become a scope component.
    signature = (
        "".join(_type(rng, 1) for _ in range(rng.randrange(1, 3)))
        + "NS_6detail6traitsE"
    )
    sym = "_ZN" + "".join(_src(s) for s in scope) + _src(leaf) + args + "E" + signature
    parsed = entity_name_components(sym)
    assert parsed is not None, sym
    assert list(parsed.components) == [*scope, leaf], sym
    assert bool(parsed.template_positions) == templated, sym
    assert _has_template_args(sym) == templated, sym
    assert _entity_owner_is_internal(sym) == internal_scope, sym


# --------------------------------------------------------------------------- #
# Real g++ manglings (oneCCL attr accessors, oneDNN shared_ptr deleters)
# --------------------------------------------------------------------------- #

_CCL_SET = "_ZN3ccl2v115allgatherv_attr3setILNS0_7attr_idE0EbEENS0_6detail6traitsIT0_XcviT_EE11return_typeES6_"
_CCL_GET = "_ZNK3ccl2v115allgatherv_attr3getILNS0_7attr_idE0EEENS0_6detail6traitsIXT_EE11return_typeEv"
_DNNL_DISPOSE = "_ZNSt19_Sp_counted_deleterIPN4dnnl4impl6streamESt14default_deleteIS2_ESaIvELN9__gnu_cxx12_Lock_policyE2EE10_M_disposeEv"
_DNNL_SHARED_COUNT = "_ZNSt14__shared_countILN9__gnu_cxx12_Lock_policyE2EEC1IPN4dnnl4impl6streamESt14default_deleteIS5_EvEET_T0_"
_DNNL_TI = "_ZTISt19_Sp_counted_deleterIPN4dnnl4impl6streamESt14default_deleteIS2_ESaIvELN9__gnu_cxx12_Lock_policyE2EE"
_STD_ONLY = "_ZNSt6vectorIiSaIiEE9push_backEOi"


@pytest.mark.parametrize("sym", [_CCL_SET, _CCL_GET, _DNNL_DISPOSE, _DNNL_SHARED_COUNT])
def test_real_accessors_are_not_internal(sym: str) -> None:
    assert _entity_owner_is_internal(sym) is False


@pytest.mark.parametrize("sym", [_DNNL_DISPOSE, _DNNL_SHARED_COUNT, _DNNL_TI])
def test_std_template_over_owned_types_is_the_librarys_own(sym: str) -> None:
    owned = frozenset({"dnnl"})
    assert instantiated_over_owned_types(sym, owned)
    # Attribution stays external when the arguments name nothing the library owns.
    assert not instantiated_over_owned_types(sym, frozenset({"ccl"}))
    assert _external_dependency_origin(sym, ["libstdc++.so.6"]) is not None


def test_std_template_over_foreign_types_stays_external() -> None:
    assert not instantiated_over_owned_types(_STD_ONLY, frozenset({"dnnl"}))


# --------------------------------------------------------------------------- #
# Public-template matching
# --------------------------------------------------------------------------- #


def _public_class(name: str) -> RecordType:
    return RecordType(name=name, kind="class", origin=ScopeOrigin.PUBLIC_HEADER)


def _snap(elf_names: tuple[str, ...], **kw: object) -> AbiSnapshot:
    snap = AbiSnapshot(library="libx.so", version="1", from_headers=True, **kw)  # type: ignore[arg-type]
    snap.elf = ElfMetadata(symbols=[ElfSymbol(name=n) for n in elf_names])
    return snap


@pytest.mark.parametrize(
    ("public_names", "sym", "expected"),
    [
        (("ccl::v1::allgatherv_attr",), _CCL_SET, "ccl::v1::allgatherv_attr"),
        (("ccl::allgatherv_attr",), _CCL_GET, "ccl::v1::allgatherv_attr"),  # inline ns
        (
            ("lib::Box<int>",),
            "_ZN3lib3BoxIfE3getEv",
            "lib::Box",
        ),  # other specialization
        (("lib::Other",), "_ZN3lib3BoxIfE3getEv", None),
        (
            ("ccl::v1::allgatherv_attr",),
            "_ZN3ccl2v115allgatherv_attr3runEv",
            None,
        ),  # not a template
        ((), _CCL_SET, None),
    ],
)
def test_public_template_for_symbol(
    public_names: tuple[str, ...], sym: str, expected: str | None
) -> None:
    snap = _snap((), types=[_public_class(n) for n in public_names])
    assert public_template_for_symbol(sym, public_template_scopes(snap)) == expected


def test_private_class_member_template_is_not_public() -> None:
    snap = _snap(
        (),
        types=[
            RecordType(
                name="ccl::v1::allgatherv_attr",
                kind="class",
                origin=ScopeOrigin.PRIVATE_HEADER,
            )
        ],
    )
    assert public_template_for_symbol(_CCL_SET, public_template_scopes(snap)) is None
    assert public_template_for_symbol(_CCL_SET, PublicTemplateScopes()) is None


def _public_fn(
    name: str = "ccl::v1::init", mangled: str = "_ZN3ccl2v14initEv"
) -> Function:
    return Function(
        name=name, mangled=mangled, return_type="void", origin=ScopeOrigin.PUBLIC_HEADER
    )


def test_exported_not_public_accounts_public_template_instantiation() -> None:
    snap = _snap(
        ("_ZN3ccl2v14initEv", _CCL_SET, _CCL_GET),
        functions=[_public_fn()],
        types=[_public_class("ccl::v1::allgatherv_attr")],
    )
    res = run_crosschecks(snap, CrosscheckConfig(max_per_check=0))
    assert not [c for c in res.findings if c.kind == ChangeKind.EXPORTED_NOT_PUBLIC]
    row = next(
        r
        for r in res.coverage
        if r["layer"] == f"crosscheck:{CHECK_EXPORTED_NOT_PUBLIC}"
    )
    assert row["counters"]["public_template_instantiation"] == 2
    assert "internal_namespace" not in row["counters"]


def test_exported_not_public_does_not_blame_libstdcxx_for_own_instantiation() -> None:
    fn = _public_fn("dnnl::init", "_ZN4dnnl4initEv")
    snap = _snap(("_ZN4dnnl4initEv", _DNNL_DISPOSE, _STD_ONLY), functions=[fn])
    snap.elf.needed = ["libstdc++.so.6"]  # type: ignore[union-attr]
    res = run_crosschecks(snap, CrosscheckConfig(max_per_check=0))
    by_sym = {
        c.symbol: c for c in res.findings if c.kind == ChangeKind.EXPORTED_NOT_PUBLIC
    }
    assert by_sym[_DNNL_DISPOSE].old_value is None
    assert "external dependency" not in by_sym[_DNNL_DISPOSE].description
    assert "own types" in by_sym[_DNNL_DISPOSE].description
    assert by_sym[_STD_ONLY].old_value == "libstdc++.so.6"


# --------------------------------------------------------------------------- #
# Incomplete header evidence: #ifdef regions and excluded headers
# --------------------------------------------------------------------------- #


def _header_snap(
    tmp_path, exported: str, *, excluded: tuple[str, ...] = ()
) -> AbiSnapshot:
    inc = tmp_path / "include" / "dnnl"
    inc.mkdir(parents=True)
    (inc / "dnnl.hpp").write_text(
        "#ifndef DNNL_HPP\n#define DNNL_HPP\n"
        "namespace dnnl { void init();\n"
        "#ifdef DNNL_EXPERIMENTAL_PROFILING\n"
        "void reset_profiling(int stream);\n"
        "#endif\n"
        "void always();\n"
        "}\n#endif\n"
    )
    (inc / "dnnl_ocl.hpp").write_text(
        "namespace dnnl { namespace ocl_interop { int get_mem_kind(int m); } }\n"
    )
    fn = Function(
        name="dnnl::init",
        mangled="_ZN4dnnl4initEv",
        return_type="void",
        origin=ScopeOrigin.PUBLIC_HEADER,
        source_header=str(inc / "dnnl.hpp"),
    )
    return _snap(
        ("_ZN4dnnl4initEv", exported), functions=[fn], excluded_header_patterns=excluded
    )


def test_conditional_declaration_is_not_claimed_absent(tmp_path) -> None:
    sym = "_ZN4dnnl15reset_profilingEi"
    snap = _header_snap(tmp_path, sym)
    hint = textual_declaration_hint(sym, build_export_declaration_evidence(snap))
    assert hint is not None and hint.reason == "conditional"
    assert "DNNL_EXPERIMENTAL_PROFILING" in (hint.guard or "")
    res = run_crosschecks(snap, CrosscheckConfig(max_per_check=0))
    (finding,) = [c for c in res.findings if c.kind == ChangeKind.EXPORTED_NOT_PUBLIC]
    assert "declared in no public" not in finding.description
    assert "DNNL_EXPERIMENTAL_PROFILING" in finding.description
    assert finding.confidence == Confidence.LOW


def test_excluded_header_declaration_is_not_claimed_absent(tmp_path) -> None:
    sym = "_ZN4dnnl11ocl_interop12get_mem_kindEi"
    snap = _header_snap(tmp_path, sym, excluded=("dnnl_ocl.hpp",))
    res = run_crosschecks(snap, CrosscheckConfig(max_per_check=0))
    (finding,) = [c for c in res.findings if c.kind == ChangeKind.EXPORTED_NOT_PUBLIC]
    assert "excluded header" in finding.description
    assert finding.confidence == Confidence.LOW


def test_unconditional_undeclared_export_keeps_its_claim(tmp_path) -> None:
    sym = "_ZN4dnnl13hidden_helperEv"  # appears in no header text at all
    snap = _header_snap(tmp_path, sym)
    res = run_crosschecks(snap, CrosscheckConfig(max_per_check=0))
    (finding,) = [c for c in res.findings if c.kind == ChangeKind.EXPORTED_NOT_PUBLIC]
    assert "declared in no public header" in finding.description
    assert finding.confidence == Confidence.HIGH


def test_include_guard_is_not_a_condition(tmp_path) -> None:
    snap = _header_snap(tmp_path, "_ZN4dnnl6alwaysEv")
    assert (
        textual_declaration_hint(
            "_ZN4dnnl6alwaysEv", build_export_declaration_evidence(snap)
        )
        is None
    )


# --------------------------------------------------------------------------- #
# Report operation reflects cross-source evolution
# --------------------------------------------------------------------------- #


class _Finding:
    def __init__(self, evolution: CrossSourceEvolution | None) -> None:
        self.cross_source_evolution = evolution


@pytest.mark.parametrize(
    ("evolution", "expected"),
    [
        (CrossSourceEvolution.INTRODUCED, "added"),
        (CrossSourceEvolution.RESOLVED, "removed"),
        (CrossSourceEvolution.PERSISTENT, "unchanged"),
        (CrossSourceEvolution.NOT_EVALUATED, "modified"),
        (None, "modified"),
    ],
)
def test_operation_follows_cross_source_evolution(
    evolution: CrossSourceEvolution | None, expected: str
) -> None:
    assert operation_for_change(_Finding(evolution), "exported_not_public") == expected


def test_operation_for_unstamped_kind_is_the_catalog_answer() -> None:
    assert operation_for_change(_Finding(None), "func_added_elf_only") == "added"


# --------------------------------------------------------------------------- #
# func_added_elf_only: an instantiation of a public template is not "declared
# in no public header"
# --------------------------------------------------------------------------- #


def _elf_side(exports: tuple[str, ...], types: list[RecordType]) -> AbiSnapshot:
    from abicheck.elf_metadata import SymbolType

    snap = AbiSnapshot(library="libx.so", version="1", from_headers=True, types=types)
    snap.elf = ElfMetadata(
        machine="EM_X86_64",
        symbols=[
            ElfSymbol(name=n, binding="GLOBAL", sym_type=SymbolType.FUNC)
            for n in exports
        ],
    )
    return snap


@pytest.mark.parametrize("public", [True, False])
def test_func_added_elf_only_names_the_public_template(public: bool) -> None:
    from abicheck.compare.undeclared_exports import _diff_undeclared_exports

    origin = ScopeOrigin.PUBLIC_HEADER if public else ScopeOrigin.PRIVATE_HEADER
    types = [RecordType(name="ccl::v1::allgatherv_attr", kind="class", origin=origin)]
    old = _elf_side(("_ZN3ccl2v14initEv",), types)
    new = _elf_side(("_ZN3ccl2v14initEv", _CCL_SET), types)
    added = [
        c
        for c in _diff_undeclared_exports(old, new)
        if c.kind == ChangeKind.FUNC_ADDED_ELF_ONLY
    ]
    assert [c.symbol for c in added] == [_CCL_SET]
    if public:
        assert "public template ccl::v1::allgatherv_attr" in added[0].description
        assert "not declared in any public header" not in added[0].description
    else:
        assert "not declared in any public header" in added[0].description
