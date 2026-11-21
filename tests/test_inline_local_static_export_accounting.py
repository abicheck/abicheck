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

"""A public inline function's local entities are documented by their owner.

clang exports a header inline function's static local (``_ZZ8edit_ctrvE1n``)
weak, gcc as ``STB_GNU_UNIQUE``; with its guard (``_ZGVZ...``) it exists only
to keep the owner's one-definition rule, and no source can name it. Accounting
it as an undeclared export (``exported_not_public``) made adding a header
inline function with a static read as a risk. The oracle here builds each
local-entity name from the Itanium grammar (``Z <encoding> E <entity>
[<discriminator>]``) around an owner chosen independently, so it does not
reuse the scanner; the integration half compiles real libraries and asserts
that a genuinely undeclared export (a plain function, and a static local of a
function no public header declares) is still reported.
"""

from __future__ import annotations

import itertools
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.buildsource.cross_source_checks import (
    CHECK_EXPORTED_NOT_PUBLIC,
    CrosscheckConfig,
    run_crosschecks,
)
from abicheck.buildsource.export_account_decision import public_local_entity_owner
from abicheck.cli import main
from abicheck.serialization import load_snapshot

#: Owner manglings with nested names, templates and parameters that contain
#: ``E`` markers of their own, so an early ``E`` is a plausible wrong cut.
_OWNERS = (
    "_Z8edit_ctrv",
    "_ZN2ns3ctrEv",
    "_ZN2ns5inner3getEi",
    "_ZN3Foo3barEv",
    "_ZNK3Foo5valueEv",
    "_Z3sumIiEiT_",
    "_ZN2ns3mapINS_4pairEEEvRKT_",
)
#: Local entity spellings: a named static, a discriminated one, a string literal.
_ENTITIES = ("1n", "5cache", "1n_0", "s", "s_1")
_PREFIXES = ("_ZZ", "_ZGVZ")


def _local(prefix: str, owner: str, entity: str) -> str:
    """Itanium ``<local-name>`` of *entity* inside *owner* (grammar, not scan)."""
    return f"{prefix}{owner[2:]}E{entity}"


@pytest.mark.parametrize(
    ("prefix", "owner", "entity"),
    list(itertools.product(_PREFIXES, _OWNERS, _ENTITIES)),
)
def test_local_entity_of_a_public_owner_names_that_owner(
    prefix: str, owner: str, entity: str
) -> None:
    others = frozenset(o for o in _OWNERS if o != owner)
    sym = _local(prefix, owner, entity)
    assert public_local_entity_owner(sym, others | {owner}) == owner
    # The same local with its owner absent from the public set stays undocumented.
    assert public_local_entity_owner(sym, others) is None


@pytest.mark.parametrize("sym", [*_OWNERS, "_ZTV3Foo", "plain_c_func", "_ZGVN2ns1gE"])
def test_a_non_local_export_is_never_owned(sym: str) -> None:
    assert public_local_entity_owner(sym, frozenset(_OWNERS)) is None


# --------------------------------------------------------------------------- #
# Real compiled libraries, through the public compare CLI.
# --------------------------------------------------------------------------- #

_HEADER_V1 = "int api_f(int x);\n"
_HEADER_V2 = """\
int api_f(int x);
inline int edit_ctr() { static int n = 0; return ++n; }
namespace ns {
inline int counter() { static int c = 0; return ++c; }
struct Box { int get() { static int g = 0; return ++g; } };
}
"""
_SOURCE_V1 = '#include "api.h"\nint api_f(int x) { return x; }\n'
_SOURCE_V2 = """\
#include "api.h"
inline int private_ctr() { static int hidden = 0; return ++hidden; }
int undeclared_fn(int x) { return x + private_ctr(); }
int api_f(int x) { ns::Box b; return x + edit_ctr() + ns::counter() + b.get(); }
"""


_PUBLIC_LOCALS = ("_ZZ8edit_ctrvE1n", "_ZZN2ns7counterEvE1c", "_ZZN2ns3Box3getEvE1g")


def _compilers() -> list[object]:
    return [
        pytest.param(
            cxx,
            marks=pytest.mark.skipif(
                shutil.which(cxx) is None, reason=f"{cxx} required"
            ),
        )
        for cxx in ("clang++", "g++")
    ]


def _build(cxx: str, d: Path, header: str, source: str) -> Path:
    d.mkdir()
    (d / "api.h").write_text(header, encoding="utf-8")
    (d / "api.cpp").write_text(source, encoding="utf-8")
    so = d / "libapi.so"
    subprocess.run(
        [cxx, "-std=c++17", "-O0", "-fPIC", "-shared", "-o", str(so), "api.cpp"],
        cwd=d,
        check=True,
        capture_output=True,
        text=True,
    )
    return so


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="builds ELF libraries")
@pytest.mark.parametrize("cxx", _compilers())
def test_inline_statics_are_not_undeclared_but_real_leaks_are(
    tmp_path: Path, cxx: str
) -> None:
    old = _build(cxx, tmp_path / "v1", _HEADER_V1, _SOURCE_V1)
    new = _build(cxx, tmp_path / "v2", _HEADER_V2, _SOURCE_V2)
    snap_path = tmp_path / "new.json"
    dumped = CliRunner().invoke(
        main,
        [
            "dump",
            str(new),
            "-H",
            str(tmp_path / "v2" / "api.h"),
            "-o",
            str(snap_path),
        ],
    )
    assert snap_path.exists(), dumped.output
    result = run_crosschecks(
        load_snapshot(snap_path),
        CrosscheckConfig(enabled=frozenset({CHECK_EXPORTED_NOT_PUBLIC})),
    )
    flagged = {c.symbol for c in result.findings}
    for public_local in _PUBLIC_LOCALS:
        assert public_local not in flagged, sorted(flagged)
    # A genuinely undeclared export, and a static local of an undeclared owner.
    assert {"_Z13undeclared_fni", "_ZZ11private_ctrvE6hidden"} <= flagged

    # The OLD->NEW existence findings word the same decision the same way.
    out = tmp_path / "r.json"
    CliRunner().invoke(
        main,
        [
            "compare",
            str(old),
            str(new),
            "--header",
            f"old={tmp_path / 'v1' / 'api.h'}",
            "--header",
            f"new={tmp_path / 'v2' / 'api.h'}",
            "-o",
            f"json={out}",
        ],
    )
    report = json.loads(out.read_text(encoding="utf-8"))
    words = {c.get("symbol"): c.get("description", "") for c in report["changes"]}
    for public_local in _PUBLIC_LOCALS:
        assert "function-local entity of public function" in words[public_local]
    assert "not declared in any public header" in words["_ZZ11private_ctrvE6hidden"]
