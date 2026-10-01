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

"""The same-leaf contract case on real binaries: g++ + castxml + ``compare``.

Two records share the leaf ``Cache`` (``ns1::``/``ns2::``), both in a
*private* header -- so neither is a public seed on its own -- and the public
``api()`` spells its return type as the bare ``Cache *``. Before schema v54
the reached record's break was withheld from the gate under ``--contract
public`` (``UNKNOWN_UNRESOLVED``, exit 1 instead of 4). Now:

* the reached ``ns1::Cache`` growing is ``IN_CONTRACT`` and gates as an ABI
  break (exit 4);
* the unreached ``ns2::Cache`` growing is *not* confirmed -- the identity
  names one record, and its implicit members, which castxml synthesizes for
  every parsed record, must not launder it in.

The unit-level invariants live in ``test_contract_type_identities.py``; this
module exists because the claim "castxml records the identity, and it
matches the record's qualified name" is about castxml, not about a
hand-built XML document.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("castxml") is None or shutil.which("g++") is None,
        reason="needs castxml and g++",
    ),
]

_API_H = """#include "../priv/private.h"
using namespace ns1;
Cache *api();
"""

_SOURCE = """#include "inc/api.h"
Cache *api() { return 0; }
namespace ns2 { void touch(Cache *) {} }
"""


def _build(root: Path, ns1_extra: str, ns2_extra: str) -> Path:
    (root / "inc").mkdir(parents=True)
    (root / "priv").mkdir()
    (root / "priv" / "private.h").write_text(
        f"namespace ns1 {{ struct Cache {{ int slot; {ns1_extra} }}; }}\n"
        f"namespace ns2 {{ struct Cache {{ int slot; {ns2_extra} }}; }}\n"
    )
    (root / "inc" / "api.h").write_text(_API_H)
    (root / "a.cpp").write_text(_SOURCE)
    lib = root / "liba.so"
    cmd = [
        "g++",
        "-shared",
        "-fPIC",
        "-g",
        f"-I{root}",
        str(root / "a.cpp"),
        "-o",
        str(lib),
    ]
    result = subprocess.run(cmd, capture_output=True, check=False)
    if result.returncode != 0:
        # A configured compiler that ran and failed is a broken fixture, not
        # an absent capability (bug class guard.absent_capability_vs_real_failure).
        pytest.fail(
            f"g++ failed: {' '.join(cmd)}\n{result.stderr.decode(errors='replace')}"
        )
    return lib


def _run_compare(tmp_path: Path, grown: str, *, operand: str) -> tuple[int, dict]:
    """``compare --contract public`` of v1 against a v2 where *grown*'s
    ``Cache`` gained a field; *operand* is ``file`` (one library) or ``dir``
    (the release fan-out over a directory of one)."""
    old = _build(tmp_path / "v1", "", "")
    new = _build(
        tmp_path / "v2",
        "long x;" if grown == "ns1" else "",
        "long x;" if grown == "ns2" else "",
    )
    if operand == "dir":
        for side, lib in (("d1", old), ("d2", new)):
            (tmp_path / side).mkdir()
            shutil.copy(lib, tmp_path / side / lib.name)
        old_op, new_op = tmp_path / "d1", tmp_path / "d2"
    else:
        old_op, new_op = old, new
    report = tmp_path / "report.json"
    # A private cache: the user's own would serve a snapshot some other
    # abicheck revision dumped, which is how a stale v32 entry once hid this
    # very fix (`snapshot_cache._SNAPSHOT_CACHE_VERSION`'s v33 note).
    env = {**os.environ, "XDG_CACHE_HOME": str(tmp_path / "cache")}
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "abicheck",
            "compare",
            str(old_op),
            str(new_op),
            "--header",
            f"old={old.parent / 'inc' / 'api.h'}",
            "--header",
            f"new={new.parent / 'inc' / 'api.h'}",
            "--contract",
            "public",
            "-o",
            f"json={report}",
        ],
        capture_output=True,
        check=False,
        env=env,
    )
    assert report.exists(), proc.stderr.decode(errors="replace")
    return proc.returncode, json.loads(report.read_text())


def _size_change_relevance(tmp_path: Path, grown: str) -> tuple[int, str | None]:
    code, doc = _run_compare(tmp_path, grown, operand="file")
    (size,) = [c for c in doc["changes"] if c["kind"] == "type_size_changed"]
    return code, size.get("contract_relevance")


def test_the_reached_records_break_is_in_contract_and_gates(tmp_path: Path) -> None:
    code, relevance = _size_change_relevance(tmp_path, "ns1")
    assert relevance == "IN_CONTRACT"
    assert code == 4


def test_the_unreached_siblings_break_is_not_confirmed(tmp_path: Path) -> None:
    _code, relevance = _size_change_relevance(tmp_path, "ns2")
    assert relevance != "IN_CONTRACT"


@pytest.mark.parametrize(
    ("grown", "verdict", "code"), [("ns1", "BREAKING", 4), ("ns2", "NO_CHANGE", 1)]
)
def test_the_directory_fan_out_agrees_with_the_single_pair(
    tmp_path: Path, grown: str, verdict: str, code: int
) -> None:
    # The package lane: one library per directory must reach the decision a
    # single-pair compare of that library does (cardinality invariance).
    # `NO_CHANGE` + exit 1 is the unreached sibling's honest answer: its
    # break is withheld as unresolved and only the coverage floor fires.
    exit_code, doc = _run_compare(tmp_path, grown, operand="dir")
    assert doc["verdict"] == verdict
    assert exit_code == code
