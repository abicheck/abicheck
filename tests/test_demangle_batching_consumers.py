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

"""Per-name demangling must not fork one ``c++filt`` per finding.

`demangle()` forks `c++filt` for every name no earlier batch warmed when the
`cxxfilt` binding is absent. Two consumers called it once per name -- the
long-double pairing detector and the public-surface fallback for mangled
type-level findings -- and on Linux an unrelated earlier batch happened to
warm their names, so the cost only showed on macOS (Mach-O spellings, where
nothing else does). The invariant is stated per consumer as a count of child
processes that does not grow with the number of names.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from _compare_workloads import _stamp_header_facts  # noqa: E402
from audit_repeated_calls import count_subprocess_spawns  # noqa: E402

from abicheck.checker import compare  # noqa: E402
from abicheck.model.declarations import Function, Visibility  # noqa: E402
from abicheck.model.elf_facts import ElfMetadata, ElfSymbol  # noqa: E402
from abicheck.model.snapshot import AbiSnapshot  # noqa: E402

pytestmark = [
    pytest.mark.skipif(shutil.which("c++filt") is None, reason="needs c++filt"),
    pytest.mark.skipif(
        importlib.util.find_spec("cxxfilt") is not None,
        reason="the in-process binding never forks, so the property is vacuous",
    ),
]


def _long_double_symbols(tag: str, i: int, side: str):
    # Distinct removed/added C++ exports: the long-double pairing demangles each.
    name = f"f{tag}{i}"
    return [ElfSymbol(name=f"_Z{len(name)}{name}{'e' if side == 'old' else 'd'}")], []


def _snapshot(version: str, n: int, tag: str, side: str, make) -> AbiSnapshot:
    symbols = [ElfSymbol(name="_Z6anchorv")]
    functions = [
        Function(
            name="anchor",
            mangled="_Z6anchorv",
            return_type="void",
            visibility=Visibility.PUBLIC,
        )
    ]
    for i in range(n):
        syms, funcs = make(tag, i, side)
        symbols += syms
        functions += funcs
    return _stamp_header_facts(
        AbiSnapshot(
            library="libx.so",
            version=version,
            functions=functions,
            elf=ElfMetadata(symbols=symbols),
        )
    )


def test_long_double_pairing_forks_at_most_one_demangler() -> None:
    make = _long_double_symbols
    spawned = {}
    for n in (4, 16, 48):
        tag = (
            f"{make.__name__[1:4]}{n}x"  # fresh names per size: caches are process-wide
        )
        old, new = (
            _snapshot("1", n, tag, "old", make),
            _snapshot("2", n, tag, "new", make),
        )
        spawned[n] = count_subprocess_spawns(lambda o=old, w=new: compare(o, w))
    # One batch per consumer at most, whatever the size; per-name forking
    # would make this at least n.
    assert max(spawned.values()) <= 2, spawned


def test_first_need_batch_runs_once_and_only_when_needed(monkeypatch) -> None:
    # The surface fallback's contract: nothing is demangled unless a finding
    # reaches the fallback; the first that does warms every mangled symbol of
    # the pass in one batch; later ones reuse it. (The wiring into the real
    # scoping pass is exercised on a dumped Mach-O-spelled library in
    # `test_extract_call_complexity.py`, the only input that reaches it.)
    import abicheck.surface as surface
    from abicheck.model.change import Change
    from abicheck.model.change_catalog.kinds import ChangeKind

    batches: list[list[str]] = []
    monkeypatch.setattr(
        surface, "demangle_batch", lambda names: batches.append(list(names)) or {}
    )
    for n in (0, 1, 5, 40):
        batches.clear()
        changes = [
            Change(kind=ChangeKind.FUNC_ADDED, symbol=f"_Z{i}fv", description="")
            for i in range(n)
        ]
        changes += [
            Change(kind=ChangeKind.FUNC_ADDED, symbol=f"plain{i}", description="")
            for i in range(n)
        ]
        warm = surface.demangle_batch_on_first_need(changes)
        assert batches == []  # building the callback demangles nothing
        for _ in range(3):
            warm()
        # Every symbol is handed over; `demangle_batch` drops non-mangled ones.
        expected = sorted(
            [*(f"_Z{i}fv" for i in range(n)), *(f"plain{i}" for i in range(n))]
        )
        assert batches == ([expected] if n else []), (n, batches)
