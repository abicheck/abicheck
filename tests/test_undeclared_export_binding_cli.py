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

"""A ``binding:`` reclassify rule reaches an undeclared WEAK inline/template
export through the real CLI, on real compiled binaries.

The failure this pins: a header-depth compare of two snapshots, with no
baseline ``.so`` on disk, reported the WEAK ``-fvisibility-inlines-hidden``
casualty ``func_removed_elf_only`` with no ``symbol_binding``. The rule's
``binding: weak`` selector therefore never matched, and the finding stayed
BREAKING. A variant removing a GLOBAL undeclared export of the same kind must
still gate, because the binding selector is what separates the two.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="ELF tests require Linux"),
    pytest.mark.skipif(shutil.which("g++") is None, reason="needs g++"),
]

_HEADER = (
    "namespace ns{namespace detail{template<class T>struct P{virtual ~P();"
    "int f(){return 1;}};template<class T>P<T>::~P(){}} "
    "struct Pub{detail::P<int> p;int g();detail::P<float>* q();}; "
    "int strong_fn();}\n"
)

_SOURCE = (
    '#include "a.h"\n'
    "namespace ns {\n"
    "int Pub::g() { return 2; }\n"
    "detail::P<float>* Pub::q() { return new detail::P<float>(); }\n"
    "int strong_fn() { return 3; }\n"
    "}\n"
)

#: An undeclared, default-visibility, non-inline export. Not hidden by
#: -fvisibility-inlines-hidden, so its removal is a GLOBAL one, not a WEAK one.
_STRONG_UNDECLARED = 'extern "C" int strong_undeclared() { return 4; }\n'

# `binding:` is conjunctive-only (policy_file rejects it standing alone), so
# the rule pairs it with a symbol_pattern that scopes it to the library's ns.
_POLICY_WEAK_RISK = (
    "reclassify:\n"
    "  - kind: func_removed_elf_only\n"
    "    symbol_pattern: '_ZN2ns.*'\n"
    "    binding: weak\n"
    "    to: risk\n"
    "    reason: WEAK inline/template export, consumers embed their own copy\n"
)

_WEAK_CTOR = "_ZN2ns6detail1PIfEC1Ev"


def _build(directory: Path, name: str, source: str, *flags: str) -> Path:
    (directory / "a.cpp").write_text(source, encoding="utf-8")
    so = directory / f"lib{name}.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", *flags, "a.cpp", "-o", str(so)],
        cwd=directory,
        check=True,
    )
    return so


def _run_scenario(tmp_path: Path, *, strong_removed: bool) -> tuple[int, dict]:
    """Build OLD and NEW, dump both from headers, delete OLD's binary, then
    compare the two snapshots under the WEAK-risk policy. Returns the exit
    code and the parsed JSON report.

    ``strong_removed`` adds one undeclared GLOBAL export to OLD only, so NEW
    loses it. Both sides otherwise match, so the only added finding is that
    removal."""
    (tmp_path / "a.h").write_text(_HEADER, encoding="utf-8")
    old_source = _SOURCE + (_STRONG_UNDECLARED if strong_removed else "")
    old_so = _build(tmp_path, "old", old_source)
    new_so = _build(tmp_path, "new", _SOURCE, "-fvisibility-inlines-hidden")
    old_json = tmp_path / "old.json"
    new_json = tmp_path / "new.json"
    header = tmp_path / "a.h"
    for so, out in ((old_so, old_json), (new_so, new_json)):
        dumped = CliRunner().invoke(
            main, ["dump", str(so), "-H", str(header), "-o", str(out)]
        )
        assert dumped.exit_code == 0, dumped.output
    # No baseline binary on disk: the header-depth path, not the L0 fold.
    old_so.unlink()
    policy = tmp_path / "pol.yaml"
    policy.write_text(_POLICY_WEAK_RISK, encoding="utf-8")
    out_json = tmp_path / "out.json"
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(old_json),
            str(new_json),
            "-o",
            f"json={out_json}",
            "--policy",
            str(policy),
        ],
    )
    return result.exit_code, json.loads(out_json.read_text(encoding="utf-8"))


def _by_symbol(report: dict, symbol: str) -> dict:
    matches = [c for c in report["changes"] if c["symbol"] == symbol]
    assert matches, [c["symbol"] for c in report["changes"]]
    return matches[0]


def test_weak_undeclared_removal_is_demoted_by_binding_rule(tmp_path: Path) -> None:
    exit_code, report = _run_scenario(tmp_path, strong_removed=False)
    weak = _by_symbol(report, _WEAK_CTOR)
    assert weak["kind"] == "func_removed_elf_only"
    assert weak["symbol_binding"] == "weak"
    assert exit_code == 0, report["verdict"]


def test_strong_undeclared_removal_still_gates(tmp_path: Path) -> None:
    exit_code, report = _run_scenario(tmp_path, strong_removed=True)
    strong = _by_symbol(report, "strong_undeclared")
    assert strong["kind"] == "func_removed_elf_only"
    assert strong["symbol_binding"] == "global"
    # The WEAK ctor is still demoted; the GLOBAL export keeps the run at 4.
    assert _by_symbol(report, _WEAK_CTOR)["symbol_binding"] == "weak"
    assert exit_code == 4, report["verdict"]
