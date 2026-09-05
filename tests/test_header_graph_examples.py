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

"""Live proof that ``dump`` (with ``--depth headers`` evidence) builds the L2
header-only semantic graph against real compiled headers for
case187/188/189/191, and reproduces the ``public_api_internal_dependency_added``
finding each case's README documents.

G29 Phase A: the graph is now always attempted whenever headers are parsed —
no ``--header-graph`` flag is passed below any more (that hidden, deprecated
no-op shim has since been removed outright, CLI cleanup H1; see
``test_cli_coverage_extra.py`` / ``test_compare_dispatch.py`` for coverage of
the removal).

These four cases ship real ``v1``/``v2`` sources (not hand-built graph
fixtures): a real field/base/parameter-type change to an internal type is
never invisible to the plain binary+header lane (that lane alone already
proves BREAKING via a structural ``ChangeKind`` — see
``tests/test_example_autodiscovery.py``), so the L5 risk finding these cases
are named after needs its own dedicated live check that actually reproduces
it — the exact reproduction commands documented in each README's "How to
reproduce" section, executed for real rather than only described. See
``validation/scripts/collect_full_example_matrix.py``'s
``HEADER_GRAPH_PROOF_CASES`` for how these are excluded from the
build-integrated (``--sources``/``--build-info``) proof lane, which this
family deliberately does not use.

Marked ``integration`` — needs a C++ compiler and clang (the header-only
Clang AST type-graph pass silently degrades to no type/call edges without
clang; see ``header_graph.py``).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_DIR = Path(__file__).resolve().parent.parent

# Phase 3 resolver (scripts/CLAUDE.md, docs/contribute/plans/examples-catalog-split.md).
if str(REPO_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_DIR / "scripts"))
import example_catalog  # noqa: E402

EXAMPLES_DIR = example_catalog.EXAMPLES_DIR

#: (case dir, expected verdict, expected changed kinds beyond the L5 risk
#: finding all four share). An entry may itself be a ``frozenset`` of
#: alternative kinds, meaning "at least one of these", rather than a bare
#: string meaning "exactly this one".
#:
#: case187/191 name a DWARF-sourced kind (``struct_field_type_changed``/
#: ``struct_size_changed``) that is genuinely unreachable on macOS: abicheck
#: has no Mach-O debug-map/N_OSO resolution, so ``_dump_macho`` never attempts
#: DWARF at all (headers + export table only, unlike ``_dump_elf``). On Linux
#: the same ``.so`` used to additionally fire the DWARF-sourced kind alongside
#: the header-sourced one -- but the abicheck code-review report's item 5 fix
#: (cross-tier struct/type dedup bridging bare-vs-qualified spellings) now
#: correctly collapses the two into whichever one the pipeline resolves first
#: for this case's exact field/type shape, so which single kind survives is
#: no longer reliably platform-determined (Codex review, fresh CI evidence:
#: `struct_field_type_changed` went missing on Linux once that fix landed).
#: Both kinds are independently sufficient for BREAKING per each case's
#: README, so accept either rather than pinning one.
_STRUCT_FIELD_KINDS = frozenset({"type_field_type_changed", "struct_field_type_changed"})
_STRUCT_SIZE_KINDS = frozenset({"type_size_changed", "struct_size_changed"})
CASES = [
    (
        "case187_public_struct_private_field_type",
        "BREAKING",
        {_STRUCT_FIELD_KINDS},
    ),
    (
        "case188_public_class_private_base_class",
        "BREAKING",
        {"type_base_changed"},
    ),
    (
        "case189_public_function_private_parameter_type",
        "BREAKING",
        {"func_removed"},
    ),
    (
        "case191_header_only_graph_field_type",
        "BREAKING",
        {_STRUCT_SIZE_KINDS},
    ),
]


def _find_cxx_compiler() -> str | None:
    for cc in ("g++", "clang++"):
        if shutil.which(cc):
            return cc
    return None


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform == "win32", reason="cases build ELF .so pairs, linux/macos only"
    ),
    pytest.mark.skipif(_find_cxx_compiler() is None, reason="no C++ compiler on PATH"),
    pytest.mark.skipif(
        shutil.which("clang") is None,
        reason="clang required for the header-only type-graph pass",
    ),
    # The `dump` call below never passes --ast-frontend clang
    # (nor sets ABICHECK_AST_FRONTEND), so the L2 header parse resolves to
    # "auto" -> castxml (dumper.py: "auto resolves to castxml and never
    # silently falls back to clang on castxml-less hosts" -- the automatic
    # clang fallback only covers a toolchain-version mismatch or a
    # direct-include #error guard, not castxml being absent outright).
    # Without this, a host with g++/clang++ but no castxml would fail with a
    # raw SnapshotError instead of skipping cleanly, unlike every sibling
    # integration test that does a real --header dump (e.g.
    # test_example_autodiscovery.py).
    pytest.mark.skipif(
        shutil.which("castxml") is None, reason="castxml required for header parsing"
    ),
]


@pytest.mark.parametrize(
    "case_name,expected_verdict,expected_extra_kinds", CASES, ids=[c[0] for c in CASES]
)
def test_header_graph_reproduces_documented_finding(
    case_name: str,
    expected_verdict: str,
    expected_extra_kinds: set[str],
    tmp_path: Path,
) -> None:
    case_dir = example_catalog.case_dir(case_name)
    cxx = _find_cxx_compiler()
    assert cxx is not None

    libv1 = tmp_path / "libv1.so"
    libv2 = tmp_path / "libv2.so"
    # macOS: without a pinned -install_name, clang derives LC_ID_DYLIB from
    # the (differing) output path, which the Mach-O diff reports as a
    # spurious SONAME_CHANGED (same gotcha examples/CMakeLists.txt's APPLE
    # branch and test_cross_platform_integration.py's
    # test_native_identical_dylib_is_compatible already document/pin around)
    # -- give both builds the identical install name.
    install_name_flags = (
        ["-Wl,-install_name,@rpath/lib.dylib"] if sys.platform == "darwin" else []
    )
    for src, out in ((case_dir / "v1.cpp", libv1), (case_dir / "v2.cpp", libv2)):
        # Compile and link as two steps (not one -shared invocation): harmless,
        # equivalent to one-shot compilation on every platform this test runs
        # on (abicheck never reads DWARF/a debug map for Mach-O at all --
        # _dump_macho is headers + export table only, unlike _dump_elf -- so
        # there is no debug-map indirection here to preserve). Kept for
        # parity with every other example case, whose CMake build tree
        # naturally keeps its .o files around.
        obj = out.with_suffix(".o")
        compile_result = subprocess.run(
            [cxx, "-std=c++17", "-fPIC", "-g", "-c", str(src), "-o", str(obj)],
            cwd=case_dir,
            capture_output=True,
            text=True,
        )
        assert compile_result.returncode == 0, compile_result.stderr
        link_result = subprocess.run(
            [cxx, "-shared", "-g", str(obj), "-o", str(out), *install_name_flags],
            cwd=case_dir,
            capture_output=True,
            text=True,
        )
        assert link_result.returncode == 0, link_result.stderr

    old_json = tmp_path / "old.json"
    new_json = tmp_path / "new.json"
    for lib, header, out in (
        (libv1, "v1.h", old_json),
        (libv2, "v2.h", new_json),
    ):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "abicheck.cli",
                "dump",
                str(lib),
                "--header",
                str(case_dir / header),
                "-o",
                str(out),
            ],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    report_path = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "abicheck.cli",
            "compare",
            str(old_json),
            str(new_json),
            "--format",
            "json",
            "-o",
            str(report_path),
        ],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    # compare exits non-zero for a BREAKING/API_BREAK verdict by design.
    assert report_path.is_file(), result.stderr

    payload = json.loads(report_path.read_text())
    assert payload["verdict"] == expected_verdict, payload["verdict"]
    got_kinds = {c["kind"] for c in payload.get("changes", [])}
    expected_kinds = expected_extra_kinds | {"public_api_internal_dependency_added"}
    # A requirement is either a bare kind (must be present) or a frozenset of
    # alternatives (at least one must be present) -- see CASES's own comment.
    missing = {
        req
        for req in expected_kinds
        if not (got_kinds & req if isinstance(req, frozenset) else req in got_kinds)
    }
    assert not missing, f"{case_name}: missing kinds {missing}; got {got_kinds}"
