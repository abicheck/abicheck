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

from tests._abicheck_subprocess import abicheck_command

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("castxml") is None or shutil.which("g++") is None,
        reason="needs castxml and g++",
    ),
]


@pytest.fixture(params=["castxml", "clang"], autouse=True)
def selected_backend(request, monkeypatch):
    monkeypatch.setenv("ABICHECK_AST_FRONTEND", request.param)


#: ``-g0`` is the L2 path the user relies on most: public headers plus a
#: binary with no debug info, so every record layout comes from the header
#: backend itself (on Mach-O, ``-g`` leaves DWARF in the objects, so the
#: dylib is this case too). A clang layout pass that re-derived C from the
#: ``.h`` suffix instead of following the primary parse's C++ decision
#: dumped nothing there, and the reached record's break read exit 0.
@pytest.fixture(params=["-g", "-g0"], autouse=True)
def debug_flag(request):
    return request.param


_API_H = """#include "../priv/private.h"
using namespace ns1;
Cache *api();
"""

_SOURCE = """#include "inc/api.h"
Cache *api() { return 0; }
namespace ns2 { void touch(Cache *) {} }
"""


def _build(root: Path, ns1_extra: str, ns2_extra: str, debug: str) -> Path:
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
        debug,
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


#: ``long long``, not ``long``: on an LLP64 target (Windows) ``long`` is 4
#: bytes, so ``int; long`` would change only the size -- and a pointer-only
#: ``Cache`` (no implicit special members on clang, unlike castxml) has its
#: size change lowered by the case62 opaque-handle rule, leaving nothing that
#: gates. ``long long`` raises size *and* alignment on LP64 and LLP64 alike,
#: so the record's break is observable on every platform this test runs on.
_GROWTH = "long long x;"


def _run_compare(
    tmp_path: Path, grown: str, *, operand: str, debug: str
) -> tuple[int, dict]:
    """``compare --contract public`` of v1 against a v2 where *grown*'s
    ``Cache`` gained a field; *operand* is ``file`` (one library) or ``dir``
    (the release fan-out over a directory of one)."""
    old = _build(tmp_path / "v1", "", "", debug)
    new = _build(
        tmp_path / "v2",
        _GROWTH if grown == "ns1" else "",
        _GROWTH if grown == "ns2" else "",
        debug,
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
        abicheck_command(
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
        ),
        capture_output=True,
        check=False,
        env=env,
    )
    assert report.exists(), proc.stderr.decode(errors="replace")
    return proc.returncode, json.loads(report.read_text())


#: The record-layout findings a grown ``Cache`` can produce. Which of them
#: survive is evidence-dependent, not what this file tests: castxml adds
#: implicit special members taking ``const Cache&``, so its ``Cache`` reads
#: as used by value; clang records none, ``Cache`` is reached only through
#: the ``Cache *api()`` factory, and the case62 opaque-handle rule
#: (``diff_filtering._filter_opaque_size_changes``) lowers the size change
#: while the alignment change remains.
_LAYOUT_KINDS = frozenset(
    {
        "type_size_changed",
        "struct_size_changed",
        "type_alignment_changed",
        "struct_alignment_changed",
    }
)


def _layout_relevances(
    tmp_path: Path, grown: str, debug: str
) -> tuple[int, list[str | None]]:
    code, doc = _run_compare(tmp_path, grown, operand="file", debug=debug)
    record = f"{grown}::Cache"
    relevances = [
        c.get("contract_relevance")
        for c in doc["changes"]
        if c["kind"] in _LAYOUT_KINDS
        and record in (c.get("symbol"), c.get("qualified_name"))
    ]
    # Never vacuous: the grown record's layout change must be observed
    # (on both backends, with and without DWARF) before its relevance means
    # anything.
    assert relevances, [(c["kind"], c.get("symbol")) for c in doc["changes"]]
    return code, relevances


def test_the_reached_records_break_is_in_contract_and_gates(
    tmp_path: Path, debug_flag: str
) -> None:
    code, relevances = _layout_relevances(tmp_path, "ns1", debug_flag)
    assert set(relevances) == {"IN_CONTRACT"}
    assert code == 4


def test_the_unreached_siblings_break_is_not_confirmed(
    tmp_path: Path, debug_flag: str
) -> None:
    code, relevances = _layout_relevances(tmp_path, "ns2", debug_flag)
    assert "IN_CONTRACT" not in relevances
    assert code != 4


@pytest.mark.parametrize("grown", ["ns1", "ns2"])
def test_the_directory_fan_out_agrees_with_the_single_pair(
    tmp_path: Path, grown: str, debug_flag: str
) -> None:
    # The package lane: one library per directory must reach the decision a
    # single-pair compare of that library does (cardinality invariance).
    # Compared against the single pair on the *same* platform rather than a
    # fixed verdict: Mach-O adds its own risk findings (macOS reports the
    # unreached case as COMPATIBLE_WITH_RISK, Linux as NO_CHANGE), which is
    # not what this test is about.
    pair_code, pair = _run_compare(
        tmp_path / "pair", grown, operand="file", debug=debug_flag
    )
    dir_code, release = _run_compare(
        tmp_path / "dir", grown, operand="dir", debug=debug_flag
    )
    assert release["verdict"] == pair["verdict"]
    assert dir_code == pair_code
    if grown == "ns1":
        # The reached record's break gates as an ABI break...
        assert dir_code == 4
    else:
        # ...and the unreached sibling's never does: at most the coverage
        # floor, whatever risk findings the platform adds.
        assert dir_code != 4
        assert release["verdict"] != "BREAKING"


@pytest.mark.known_gap_canary
def test_canary_opaque_handle_size_lowering_depends_on_frontend(
    tmp_path: Path, selected_backend: None, debug_flag: str, monkeypatch
) -> None:
    """Known gap (docs/contribute/known-gaps.md, "Opaque-handle size lowering
    depends on the header frontend"): a size-only growth of the pointer-only
    ``ns1::Cache`` gates on castxml (its implicit members read as a by-value
    use) but is lowered on clang without DWARF. Fails -- and the gap entry
    should be closed -- once both frontends agree."""
    if not sys.platform.startswith("linux"):
        pytest.skip("pinned where the divergence was measured (Linux ELF)")
    if debug_flag != "-g0":
        pytest.skip("the divergence needs a header-only layout (no DWARF)")
    monkeypatch.setattr(sys.modules[__name__], "_GROWTH", "int x;")
    codes = {}
    for frontend in ("castxml", "clang"):
        monkeypatch.setenv("ABICHECK_AST_FRONTEND", frontend)
        codes[frontend], _ = _run_compare(
            tmp_path / frontend, "ns1", operand="file", debug=debug_flag
        )
    assert codes == {"castxml": 4, "clang": 0}
