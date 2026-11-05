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

"""Pin ``_frontend_spelling``'s rules to what the real header frontends write.

One header exercises every rule the helper encodes (namespaced and nested
records, a base and a virtual base in another namespace, an alias-template
field, an explicitly instantiated template, typedefs, a scoped enum, builtins
both frontends spell differently, pointers, references, arrays, a function
pointer, a global-scope record referring into a namespace). It is dumped with
both the castxml and the clang backend, and each dump's spelling of the
declarations the logical fixture names must equal ``render(logical, frontend)``
exactly. A frontend that changes how it spells a name fails here first, so the
detector tests parametrized over ``FRONTENDS`` cannot keep passing against a
spelling no backend still produces.

Linux/ELF only and ``integration``: it builds a shared library with g++ and runs
castxml and clang (same gating as ``test_castxml_clang_parity_gate.py``).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _frontend_spelling import FRONTENDS, Frontend, render

from abicheck.dumper import dump
from abicheck.model import (
    AbiSnapshot,
    EnumType,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="g++-built ELF and clang/castxml mangling agree on Linux only",
    ),
]

_HEADER = """\
#pragma once
namespace lib {
namespace detail {
struct Base { int a; };
struct VBase { virtual ~VBase(); int b; };
struct Impl { int x; long y; };
enum class Mode { A, B };
template <class T> struct Box { T v; };
}
template <class T> using pimpl = detail::Box<T>;
template <class T> struct Wrap { T t; };
template struct detail::Box<int>;
template struct Wrap<detail::Impl>;
typedef detail::Impl ImplT;
typedef const detail::Impl* ImplPtr;
struct Outer { struct Inner { int q; }; Inner in; };
struct Pub : detail::Base, virtual detail::VBase {
  detail::Impl impl;
  pimpl<int> p;
  Wrap<detail::Impl> w;
  Outer::Inner oi;
  detail::Mode m;
  unsigned long long z;
  unsigned short us;
  const char* s;
  Pub* self;
  ImplT it;
  detail::Impl arr[2];
  void set(detail::Impl i, unsigned long n);
  detail::Impl get() const;
};
enum Big : unsigned long { X = 1 };
detail::Impl* make(detail::Mode m, const unsigned short* a, int (*cb)(int));
extern detail::Impl g_impl;
}
struct Global { lib::Pub* p; long l; };
void gfun(const Global& g, long long x);
"""

_SOURCE = """\
#include "api.h"
namespace lib {
detail::VBase::~VBase() {}
void Pub::set(detail::Impl, unsigned long) {}
detail::Impl Pub::get() const { return {}; }
detail::Impl* make(detail::Mode, const unsigned short*, int (*)(int)) { return 0; }
detail::Impl g_impl;
}
void gfun(const Global&, long long) {}
"""


def _rec(name: str, fields: list[tuple[str, str]], **kw: object) -> RecordType:
    return RecordType(
        name=name,
        kind="struct",
        fields=[TypeField(name=n, type=t) for n, t in fields],
        **kw,
    )


def _fn(name: str, mangled: str, ret: str, params: list[str]) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type=ret,
        params=[Param(name=f"p{i}", type=t) for i, t in enumerate(params)],
        visibility=Visibility.PUBLIC,
    )


# The header above in the logical spelling: every name fully qualified.
LOGICAL = AbiSnapshot(
    library="libapi.so",
    version="1",
    types=[
        _rec("lib::detail::Base", [("a", "int")]),
        _rec("lib::detail::VBase", [("b", "int")]),
        _rec("lib::detail::Impl", [("x", "int"), ("y", "long")]),
        _rec("lib::detail::Box<int>", [("v", "int")]),
        _rec("lib::Wrap<lib::detail::Impl>", [("t", "lib::detail::Impl")]),
        _rec("lib::Outer", [("in", "lib::Outer::Inner")]),
        _rec("lib::Outer::Inner", [("q", "int")]),
        _rec(
            "lib::Pub",
            [
                ("impl", "lib::detail::Impl"),
                ("p", "lib::detail::Box<int>"),
                ("w", "lib::Wrap<lib::detail::Impl>"),
                ("oi", "lib::Outer::Inner"),
                ("m", "lib::detail::Mode"),
                ("z", "unsigned long long"),
                ("us", "unsigned short"),
                ("s", "const char*"),
                ("self", "lib::Pub*"),
                ("it", "lib::ImplT"),
                ("arr", "lib::detail::Impl[2]"),
            ],
            bases=["lib::detail::Base"],
            virtual_bases=["lib::detail::VBase"],
        ),
        _rec("Global", [("p", "lib::Pub*"), ("l", "long")]),
    ],
    enums=[
        EnumType(name="lib::detail::Mode", members=[], underlying_type="int"),
        EnumType(name="lib::Big", members=[], underlying_type="unsigned long"),
    ],
    functions=[
        _fn(
            "lib::make",
            "_ZN3lib4makeENS_6detail4ModeEPKtPFiiE",
            "lib::detail::Impl*",
            ["lib::detail::Mode", "const unsigned short*", "int (*)(int)"],
        ),
        _fn(
            "lib::Pub::set",
            "_ZN3lib3Pub3setENS_6detail4ImplEm",
            "void",
            ["lib::detail::Impl", "unsigned long"],
        ),
        _fn("lib::Pub::get", "_ZNK3lib3Pub3getEv", "lib::detail::Impl", []),
        _fn("gfun", "_Z4gfunRK6Globalx", "void", ["const Global&", "long long"]),
    ],
    variables=[
        Variable(
            name="lib::g_impl",
            mangled="_ZN3lib6g_implE",
            type="lib::detail::Impl",
            visibility=Visibility.PUBLIC,
        )
    ],
    typedefs_qualified={
        "lib::ImplT": "lib::detail::Impl",
        "lib::ImplPtr": "const lib::detail::Impl*",
    },
)
CANONICAL_FIELDS = {("lib::Pub", "p")}
ALIAS_TEMPLATES = {"lib::pimpl": "lib::detail::Box<T>"}


def _fact(f: object) -> object:
    return None if f is None else (f.status.value, f.value)


def _project(snap: AbiSnapshot, frontend: Frontend) -> dict[str, object]:
    """The spelling-bearing facts of the declarations LOGICAL names."""
    decls = snap.declarations
    rendered = render(
        LOGICAL,
        frontend,
        canonical_fields=CANONICAL_FIELDS,
        alias_templates=ALIAS_TEMPLATES,
        leaf_decl_names=True,
    ).declarations
    want_types = {t.qualified_name or t.name for t in rendered.types}
    want_mangled = {f.mangled for f in rendered.functions}
    want_enums = {e.qualified_name or e.name for e in rendered.enums}
    records = {}
    for t in decls.types:
        key = t.qualified_name or t.name
        if key not in want_types:
            continue
        records[key] = (
            t.name,
            t.qualified_name,
            bool(t.is_template_pattern),
            list(t.bases),
            list(t.virtual_bases),
            # A pattern's fields are dependent (`T`); the helper does not model them.
            []
            if t.is_template_pattern
            else [(f.name, f.type, _fact(f.type_identities_fact)) for f in t.fields],
        )
    return {
        "records": records,
        "functions": {
            f.mangled: (
                f.name,
                f.return_type,
                _fact(f.return_type_identities_fact),
                [(p.type, _fact(p.type_identities_fact)) for p in f.params],
            )
            for f in decls.functions
            if f.mangled in want_mangled
        },
        "enums": {
            e.qualified_name or e.name: (e.name, e.qualified_name, e.underlying_type)
            for e in decls.enums
            if (e.qualified_name or e.name) in want_enums
        },
        "variables": {
            v.mangled: (v.name, v.type, _fact(v.type_identities_fact))
            for v in decls.variables
            if v.mangled in {w.mangled for w in rendered.variables}
        },
        "typedefs": dict(decls.typedefs_qualified or {}),
    }


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    required = ("g++", "clang", "castxml")
    missing = [t for t in required if shutil.which(t) is None]
    if missing:
        pytest.skip(f"frontend spelling check needs {', '.join(missing)}")
    root = tmp_path_factory.mktemp("frontend_spelling")
    header = root / "api.h"
    header.write_text(_HEADER)
    (root / "api.cpp").write_text(_SOURCE)
    so = root / "libapi.so"
    proc = subprocess.run(
        [
            "g++",
            "-shared",
            "-fPIC",
            "-g0",
            "-o",
            str(so),
            str(root / "api.cpp"),
            f"-I{root}",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return so, header


@pytest.mark.parametrize("frontend", FRONTENDS, ids=str)
def test_render_matches_real_frontend_spelling(
    built: tuple[Path, Path], frontend: Frontend
) -> None:
    so, header = built
    real = dump(so, [header], header_backend=str(frontend))
    assert real.ast_producer == str(frontend)
    rendered = render(
        LOGICAL,
        frontend,
        canonical_fields=CANONICAL_FIELDS,
        alias_templates=ALIAS_TEMPLATES,
        leaf_decl_names=True,
    )
    # The projection keys on render()'s own records, so a record it dropped
    # would leave both sides unseen: every logical record must be rendered.
    logical_keys = {t.qualified_name or t.name for t in LOGICAL.declarations.types}
    rendered_keys = {t.qualified_name or t.name for t in rendered.declarations.types}
    assert logical_keys <= rendered_keys, sorted(logical_keys - rendered_keys)
    got, want = _project(real, frontend), _project(rendered, frontend)
    assert set(got["records"]) == set(want["records"]), "records"
    for section in want:
        assert got[section] == want[section], section


def test_the_two_frontends_really_spell_the_fixture_differently(
    built: tuple[Path, Path],
) -> None:
    """Guard against a vacuous helper: the renderings must disagree somewhere
    that a detector reads (bases and field types), as the real dumps do."""
    so, header = built
    castxml = dump(so, [header], header_backend="castxml")
    clang = dump(so, [header], header_backend="clang")

    def pub(snap: AbiSnapshot) -> RecordType:
        return next(
            t for t in snap.declarations.types if t.qualified_name == "lib::Pub"
        )

    assert pub(castxml).bases == ["Base"]
    assert pub(clang).bases == ["detail::Base"]
    assert {f.name: f.type for f in pub(castxml).fields}["p"] == "Box<int>"
    assert {f.name: f.type for f in pub(clang).fields}["p"] == "lib::detail::Box<int>"
