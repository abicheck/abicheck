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

"""Export-obligation linkage: the four ``public_not_exported``/``func_added``
defects a oneDNN/oneCCL audit found, stated as invariants.

1. **Inline on any redeclaration makes the function inline** ([dcl.inline]/6).
   ``struct P { void run() const; }; inline void P::run() const {}`` owes no
   export. clang dumps two records (fold: ``fold_inline_across_redeclarations``),
   castxml dumps one record whose ``inline`` attribute reflects only the first
   declaration (recovered from the header text: ``out_of_line_inline``), and
   the obligation check folds redeclarations itself for stored snapshots.
2. **An internal-namespace declaration owes no export** -- the same
   ``detail``/``impl``/``internal``/anonymous convention ``exported_not_public``
   classifies with.
3. **A ``static`` member function keeps its obligation**; only internal
   linkage (a namespace-scope ``static``) exempts. Every static member used to
   be skipped, so an unexported one was never reported.
4. **An addition the NEW export table confirms absent is a header-only API
   addition**, not an added export: ``FUNC_ADDED``/``VAR_ADDED`` say so and
   carry ``surface_facts``.

Oracles are independent of the implementation: manglings are *built* from
their parts here, never parsed by the helper under test.
"""

from __future__ import annotations

import itertools
import random

import pytest

from abicheck.buildsource.cross_source_checks import (
    _has_export_obligation,
    _var_has_export_obligation,
    run_crosschecks,
)
from abicheck.buildsource.export_obligation_linkage import (
    inline_declared_symbols,
    is_static_member_symbol,
    owner_in_internal_namespace,
)
from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.extract.headers.castxml.out_of_line_inline import (
    index_out_of_line_inline,
)
from abicheck.extract.headers.clang.inline_semantics import (
    fold_inline_across_redeclarations,
)
from abicheck.extract.surface_fact_producers import header_ast_surface_facts
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    Function,
    ScopeOrigin,
    Variable,
    Visibility,
)

_SCOPE_NAMES = ["ns", "ccl", "dnnl", "communicator", "W", "Outer", "v1"]
_LEAVES = ["create", "split", "execute", "get", "L2", "make"]
_PARAMS = ["v", "i", "RKS_", "PKc", "N2ns1XE"]


def _src(name: str) -> str:
    return f"{len(name)}{name}"


def _nested(scopes: list[str], leaf: str, params: str, *, internal: bool) -> str:
    marker = "L" if internal else ""
    return "_ZN" + "".join(_src(s) for s in scopes) + marker + _src(leaf) + "E" + params


def _fn(name: str, mangled: str, **kw) -> Function:
    kw.setdefault("origin", ScopeOrigin.PUBLIC_HEADER)
    kw.setdefault("access", AccessLevel.PUBLIC)
    return Function(name=name, mangled=mangled, return_type="void", **kw)


# --------------------------------------------------------------------------- #
# is_static_member_symbol
# --------------------------------------------------------------------------- #


def test_static_member_vs_internal_linkage_exhaustive_small_domain() -> None:
    """Every (scope path, leaf, params) combination: a nested name without the
    internal-linkage marker is a member; with it, or un-nested, it is not."""
    cases = 0
    for depth in (1, 2, 3):
        for scopes in itertools.permutations(_SCOPE_NAMES, depth):
            for leaf, params in itertools.product(_LEAVES, _PARAMS):
                member = _nested(list(scopes), leaf, params, internal=False)
                internal = _nested(list(scopes), leaf, params, internal=True)
                assert is_static_member_symbol(member), member
                assert not is_static_member_symbol(internal), internal
                assert not is_static_member_symbol("_ZL" + _src(leaf) + params)
                assert not is_static_member_symbol("_Z" + _src(leaf) + params)
                # Darwin's extra leading underscore changes nothing.
                assert is_static_member_symbol("_" + member)
                cases += 1
    assert cases > 1000


@pytest.mark.parametrize("name", ["helper", "split_communicator", ""])
def test_unmangled_static_is_never_a_member(name: str) -> None:
    # castxml leaves an internal-linkage free function unmangled; C has no
    # members at all.
    assert not is_static_member_symbol(name)


def test_template_args_with_literals_do_not_fake_internal_linkage() -> None:
    # `W<5>::make(int)` -- `Li5E` is a literal inside the template arguments,
    # not the depth-0 internal-linkage marker.
    assert is_static_member_symbol("_ZN2ns1WILi5EE4makeEi")
    assert is_static_member_symbol("_ZN2ns1WIiE4makeEv")


_TEMPLATE_ARG_ATOMS = [
    "i",
    "Li5E",  # integer literal
    "LN2ns4kindE0E",  # namespaced-enumerator literal
    "N2ns1XE",  # nested name
    "N6detail3EnvE",  # a nested name spelling `E`-heavy identifiers
    "S_",
    "S0_",
    "S12_",  # seq-id that must not be read as a length
    "St",
    "T_",
    "JiiE",  # argument pack
]


def _template_args(rng: random.Random, depth: int = 0) -> str:
    """Generate a well-formed ``I...E`` list from the atoms above, nesting
    further template argument lists up to three levels deep."""
    parts = []
    for _ in range(rng.randint(1, 4)):
        if depth < 3 and rng.random() < 0.3:
            parts.append(
                _src(rng.choice(_SCOPE_NAMES)) + _template_args(rng, depth + 1)
            )
        else:
            parts.append(rng.choice(_TEMPLATE_ARG_ATOMS))
    return "I" + "".join(parts) + "E"


def test_generated_template_arguments_never_decide_linkage() -> None:
    """Generated manglings with templated scope components: the oracle is the
    internal flag the generator itself placed at depth 0. Whatever literals,
    nested names, substitutions or packs sit inside the template arguments,
    the answer must follow that flag alone."""
    rng = random.Random(20260929)
    for _ in range(3000):
        scopes = [
            _src(rng.choice(_SCOPE_NAMES))
            + (_template_args(rng) if rng.random() < 0.6 else "")
            for _ in range(rng.randint(1, 3))
        ]
        internal = rng.random() < 0.5
        leaf = _src(rng.choice(_LEAVES))
        mangled = (
            "_ZN"
            + "".join(scopes)
            + ("L" if internal else "")
            + leaf
            + "E"
            + rng.choice(_PARAMS)
        )
        assert is_static_member_symbol(mangled) is (not internal), mangled


@pytest.mark.parametrize(
    ("code", "member"),
    [(c, True) for c in "CDKLST"] + [(c, False) for c in "YZAEIMQU"],
)
def test_msvc_access_code_decides_static_member(code: str, member: bool) -> None:
    assert is_static_member_symbol(f"?make@W@ns@@{code}AHH@Z") is member


# --------------------------------------------------------------------------- #
# owner_in_internal_namespace
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("internal", ["detail", "impl", "internal", "_GLOBAL__N_1"])
def test_internal_namespace_owner_at_any_depth(internal: str) -> None:
    rng = random.Random(internal)
    for _ in range(50):
        outer = rng.sample(_SCOPE_NAMES, rng.randint(0, 2))
        inner = rng.sample(_SCOPE_NAMES, rng.randint(0, 2))
        leaf = rng.choice(_LEAVES)
        mangled = _nested([*outer, internal, *inner], leaf, "v", internal=False)
        assert owner_in_internal_namespace(mangled), mangled
        # The internal name as the entity itself, or only in a parameter
        # type, is not an internal owner.
        assert not owner_in_internal_namespace(
            _nested([*outer, *inner] or ["ns"], internal, "v", internal=False)
        )
        param = "RKN" + _src("ns") + _src(internal) + _src("T") + "E"
        assert not owner_in_internal_namespace(
            _nested([*outer, *inner] or ["ns"], leaf, param, internal=False)
        )


# --------------------------------------------------------------------------- #
# _has_export_obligation invariants
# --------------------------------------------------------------------------- #


def test_static_member_owes_export_static_free_does_not() -> None:
    member = _fn("create", "_ZN3ccl12communicator6createEv", is_static=True)
    free_ns = _fn("helper", "_ZN3cclL6helperEv", is_static=True)
    free_c = _fn("helper", "helper", is_static=True, is_extern_c=True)
    assert _has_export_obligation(member) is True
    assert _has_export_obligation(free_ns) is False
    assert _has_export_obligation(free_c) is False


def test_internal_namespace_declarations_owe_no_export() -> None:
    fn = _fn("get", "_ZNK3ccl6detail11environment3getEv")
    var = Variable(
        name="counter",
        mangled="_ZN3ccl6detail7counterE",
        type="int",
        origin=ScopeOrigin.PUBLIC_HEADER,
        access=AccessLevel.PUBLIC,
    )
    assert _has_export_obligation(fn) is False
    assert _var_has_export_obligation(var) is False
    # Control: the same shapes outside `detail` do owe an export.
    assert _has_export_obligation(_fn("get", "_ZNK3ccl11environment3getEv"))


def test_inline_on_any_redeclaration_removes_obligation_in_any_order() -> None:
    sym = "_ZNK4dnnl9primitive7executeEv"
    decl = _fn("execute", sym)
    definition = _fn("execute", sym, is_inline=True)
    for records in itertools.permutations([decl, definition, _fn("other", "_Z1fv")]):
        inline_symbols = inline_declared_symbols(records)
        assert _has_export_obligation(decl, inline_symbols) is False
        assert _has_export_obligation(records[-1], inline_symbols) is (
            records[-1].mangled != sym and not records[-1].is_inline
        )


# --------------------------------------------------------------------------- #
# run_crosschecks end to end (hand-built snapshots)
# --------------------------------------------------------------------------- #


def _snap(functions: list[Function], exports: list[str]) -> AbiSnapshot:
    snap = AbiSnapshot(library="libx.so", version="1", from_headers=True)
    snap.elf = ElfMetadata(symbols=[ElfSymbol(name=n) for n in exports])
    snap.functions = functions
    return snap


def test_crosscheck_reports_exactly_the_owed_missing_exports() -> None:
    owed_missing = [
        _fn("split_communicator", "_ZNK3ccl12communicator18split_communicatorEi"),
        _fn("split", "_ZN3ccl12communicator5splitEi", is_static=True),
    ]
    exempt = [
        # declared in class, defined `inline` out of line
        _fn("execute", "_ZNK4dnnl9primitive7executeEv"),
        _fn("execute", "_ZNK4dnnl9primitive7executeEv", is_inline=True),
        # internal namespace
        _fn("get", "_ZNK3ccl6detail11environment3getEv"),
        _fn("instance", "_ZN3ccl6detail11environment8instanceEv", is_static=True),
        # namespace-scope static
        _fn("helper", "_ZN3cclL6helperEv", is_static=True),
        # pure virtual
        _fn("get_id", "_ZNK3ccl13kvs_interface6get_idEv", is_pure_virtual=True),
    ]
    exported = _fn("create", "_ZN3ccl12communicator6createEv", is_static=True)
    snap = _snap([*owed_missing, *exempt, exported], [exported.mangled])
    hits = [
        c.symbol
        for c in run_crosschecks(snap).findings
        if c.kind == ChangeKind.PUBLIC_NOT_EXPORTED
    ]
    assert sorted(hits) == sorted(f.mangled for f in owed_missing)


# --------------------------------------------------------------------------- #
# clang: fold inline across redeclarations
# --------------------------------------------------------------------------- #


def test_clang_fold_is_order_independent_and_keyed_by_real_mangling() -> None:
    base = [
        _fn("execute", "_ZNK4dnnl9primitive7executeEv"),
        _fn("execute", "_ZNK4dnnl9primitive7executeEv", is_inline=True),
        _fn("run", "_ZNK4dnnl9primitive3runEv"),
        # Two uninstantiated templates falling back to the same bare name
        # must not be folded together.
        _fn("f", "f", is_inline=True),
        _fn("f", "f"),
    ]
    for order in itertools.permutations(range(len(base))):
        funcs = [
            _fn(base[i].name, base[i].mangled, is_inline=base[i].is_inline)
            for i in order
        ]
        fold_inline_across_redeclarations(funcs)
        by = {(f.mangled, f.is_inline) for f in funcs}
        assert ("_ZNK4dnnl9primitive7executeEv", False) not in by
        assert ("_ZNK4dnnl9primitive3runEv", False) in by
        assert ("f", False) in by and ("f", True) in by


# --------------------------------------------------------------------------- #
# castxml: out-of-line inline recovery from header text
# --------------------------------------------------------------------------- #


_HEADER = """
namespace dnnl {
struct primitive {
    void execute(const stream &s, const std::unordered_map<int, memory> &args) const;
    void execute(int) const;
    void run() const;
    primitive(int a, int b);
    ~primitive();
    bool operator==(const primitive &o) const;
    static int count();
};
// inline void primitive::run() const {}   (commented out: not a definition)
/* inline void primitive::run() const; */
inline void dnnl::primitive::execute(
        const stream &s, const std::unordered_map<int, memory> &args) const {}
inline primitive::primitive(int a, int b) {}
inline primitive::~primitive() = default;
inline bool primitive::operator==(const primitive &o) const { return true; }
constexpr int primitive::count() { return 0; }
template <typename T> inline T helper::get(T v) { return v; }
inline std::vector<int> other::names(void) { return {}; }
}
"""


def test_out_of_line_inline_index_matches_declarations() -> None:
    index = index_out_of_line_inline(_HEADER)
    assert index[("primitive", "execute")] == {2}
    assert index[("primitive", "primitive")] == {2}
    assert index[("primitive", "~primitive")] == {0}
    assert index[("primitive", "operator==")] == {1}
    assert index[("primitive", "count")] == {0}
    assert index[("helper", "get")] == {1}
    assert index[("other", "names")] == {0}
    # Commented-out text and an unqualified in-class declaration never count.
    assert ("primitive", "run") not in index


@pytest.mark.parametrize("arity", [0, 1, 2, 3, 5])
def test_out_of_line_inline_arity_counts_top_level_params(arity: int) -> None:
    params = ", ".join(
        f"std::map<int, std::pair<A, B>> p{i}" if i % 2 else f"void (*cb{i})(int, int)"
        for i in range(arity)
    )
    text = f"inline void Cls::fn({params}) {{}}"
    assert index_out_of_line_inline(text) == {("Cls", "fn"): {arity}}


def test_castxml_dump_recovers_out_of_line_inline(tmp_path) -> None:
    """Through the real castxml element parser (a hand-written XML tree)."""
    from xml.etree.ElementTree import fromstring

    from abicheck.extract.headers.castxml.context import CastxmlParserContext
    from abicheck.extract.headers.castxml.out_of_line_inline import (
        declared_inline_out_of_line,
    )

    header = tmp_path / "api.hpp"
    header.write_text(
        "struct primitive { void execute(int) const; void run() const; };\n"
        "inline void primitive::execute(int s) const {}\n"
    )
    sibling = tmp_path / "api_impl.hpp"
    sibling.write_text("inline void primitive::run() const {}\n")
    xml = fromstring(
        f"""<CastXML>
  <Struct id="_2" name="primitive" context="_1"/>
  <Method id="_3" name="execute" context="_2" file="f1" line="1"><Argument type="_9"/></Method>
  <Method id="_4" name="run" context="_2" file="f1" line="1"/>
  <Method id="_5" name="execute" context="_2" file="f1" line="1"/>
  <File id="f1" name="{header}"/>
  <File id="f2" name="{sibling}"/>
</CastXML>"""
    )
    ctx = CastxmlParserContext(xml, set(), set())
    ctx.build_id_map()
    els = {el.get("id"): el for el in xml}
    assert declared_inline_out_of_line(ctx, els["_3"]) is True
    assert declared_inline_out_of_line(ctx, els["_4"]) is True  # sibling header
    assert declared_inline_out_of_line(ctx, els["_5"]) is False  # arity 0 overload


# --------------------------------------------------------------------------- #
# FUNC_ADDED / VAR_ADDED on a binary-backed comparison
# --------------------------------------------------------------------------- #


def _parsed(exported: bool) -> dict:
    """Surface facts as a header-AST backend records them for a public decl."""
    return header_ast_surface_facts(
        exported=exported, judged_public=True, producer="castxml"
    )


def test_header_only_additions_do_not_read_as_added_exports() -> None:
    old = AbiSnapshot(library="libk.so", version="1", from_headers=True)
    old.elf = ElfMetadata(symbols=[ElfSymbol(name="_ZN2ns3kvs4sizeEv")])
    old.functions = [
        _fn("size", "_ZN2ns3kvs4sizeEv", visibility=Visibility.PUBLIC, **_parsed(True))
    ]
    new = AbiSnapshot(library="libk.so", version="2", from_headers=True)
    new.elf = ElfMetadata(
        symbols=[ElfSymbol(name="_ZN2ns3kvs4sizeEv"), ElfSymbol(name="_ZN2ns3newEv")]
    )
    new.functions = [
        _fn("size", "_ZN2ns3kvs4sizeEv", visibility=Visibility.PUBLIC, **_parsed(True)),
        _fn("fresh", "_ZN2ns3newEv", visibility=Visibility.PUBLIC, **_parsed(True)),
        _fn(
            "get_id",
            "_ZNK2ns13kvs_interface6get_idEv",
            is_pure_virtual=True,
            is_virtual=True,
            visibility=Visibility.HIDDEN,
            **_parsed(False),
        ),
        _fn(
            "twice",
            "_ZN2ns5twiceEi",
            is_inline=True,
            visibility=Visibility.HIDDEN,
            **_parsed(False),
        ),
    ]
    new.variables = [
        Variable(
            name="invalid_kvs_id",
            mangled="_ZN2nsL14invalid_kvs_idE",
            type="const int",
            is_const=True,
            origin=ScopeOrigin.PUBLIC_HEADER,
            visibility=Visibility.HIDDEN,
            **_parsed(False),
        )
    ]
    result = compare(old, new)
    added = {
        c.symbol: c
        for c in result.changes
        if c.kind in (ChangeKind.FUNC_ADDED, ChangeKind.VAR_ADDED)
    }
    header_only = {
        "_ZNK2ns13kvs_interface6get_idEv",
        "_ZN2ns5twiceEi",
        "_ZN2nsL14invalid_kvs_idE",
    }
    assert header_only | {"_ZN2ns3newEv"} <= set(added)
    for sym in header_only:
        assert "header-only" in added[sym].description, added[sym].description
        assert added[sym].surface_facts["binary_exported"] == "false"
    exported = added["_ZN2ns3newEv"]
    assert "header-only" not in exported.description
    assert exported.description == "New public function: fresh"
    assert exported.surface_facts["binary_exported"] == "true"


_PARAM_TYPES = [
    "int",
    "const char *s",
    "std::map<int, long> m",
    "void (*cb)(int, int)",
    "char c",
]
_DEFAULTS = [
    "",
    ' = "a,b"',
    " = ','",
    ' = "x)(,"',
    " = '\\''",
    ' = "say \\"hi, there\\""',
    " = 1'000'000",
    " = 0xFF'FF",
    " = f(1, 2)",
    " = u','",
]


def test_generated_parameter_lists_count_only_real_separators() -> None:
    """Parameter lists assembled from parts whose defaults put commas and
    brackets inside string, character and numeric literals: the oracle is the
    number of parameters the generator joined, never the text's comma count."""
    from abicheck.extract.headers.castxml.out_of_line_inline import _arity

    rng = random.Random(1411)
    for _ in range(2000):
        n = rng.randint(1, 5)
        params = [rng.choice(_PARAM_TYPES) + rng.choice(_DEFAULTS) for _ in range(n)]
        text = "inline void C::f(" + ", ".join(params) + ") const {}"
        assert _arity(text, text.index("(")) == n, text
    for empty in ("f()", "f(void)", "f( )"):
        assert _arity(empty, empty.index("(")) == 0
