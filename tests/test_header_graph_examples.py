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

"""Live proof that ``dump --header-graph`` runs against real compiled headers
for case187/188/189/191, and reproduces the ``public_api_internal_dependency_added``
finding each case's README documents.

These four cases ship real ``v1``/``v2`` sources (not hand-built graph
fixtures): a real field/base/parameter-type change to an internal type is
never invisible to the plain binary+header lane (that lane alone already
proves BREAKING via a structural ``ChangeKind`` — see
``tests/test_example_autodiscovery.py``), so the L5 risk finding these cases
are named after needs its own dedicated live check that actually passes
``--header-graph`` — the exact reproduction commands documented in each
README's "How to reproduce" section, executed for real rather than only
described. See ``validation/scripts/collect_full_example_matrix.py``'s
``HEADER_GRAPH_PROOF_CASES`` for how these are excluded from the
build-integrated (``--sources``/``--build-info``) proof lane, which this
family deliberately does not use.

Marked ``integration`` — needs a C++ compiler and clang (the header-only
Clang AST type-graph pass silently degrades to no type/call edges without
clang; see ``header_graph.py``).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_DIR = Path(__file__).parent.parent
EXAMPLES_DIR = REPO_DIR / "examples"

#: (case dir, expected verdict, expected changed kinds beyond the L5 risk
#: finding all four share).
CASES = [
    (
        "case187_public_struct_private_field_type",
        "BREAKING",
        {"struct_field_type_changed"},
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
        {"struct_size_changed"},
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
    # The `dump --header-graph` call below never passes --ast-frontend clang
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
    case_dir = EXAMPLES_DIR / case_name
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
        # Compile and link as two steps (not one -shared invocation) so the
        # intermediate .o persists in tmp_path for the rest of the test: on
        # macOS, `-g` embeds only a debug *map* pointing at the compiler's
        # object file (ld64's N_OSO stabs), not self-contained DWARF, so an
        # ephemeral one-shot compile+link's temp .o vanishing before abicheck
        # reads it silently degrades every struct/base/parameter-type finding
        # to no DWARF evidence. Every other example case avoids this because
        # CMake's build tree naturally keeps its .o files around; harmless,
        # equivalent to one-shot compilation, on Linux (DWARF is embedded
        # directly in the ELF, no such indirection).
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
    dump_stderrs: dict[str, str] = {}
    # dumper_cache._cache_path's own layout (mirrored here, not imported, so
    # this stays a pure diagnostic with no production-code coupling): the raw
    # castxml XML for each dump is content-addressed under this directory.
    # Snapshotting its contents around each dump call recovers exactly what
    # castxml produced on the runner, without needing production code changes
    # to surface it.
    _xdg_cache = os.environ.get("XDG_CACHE_HOME")
    castxml_cache_dir = (
        (Path(_xdg_cache) if _xdg_cache else Path.home() / ".cache")
        / "abi_check"
        / "castxml"
    )
    castxml_xml: dict[str, str] = {}
    for lib, header, out in (
        (libv1, "v1.h", old_json),
        (libv2, "v2.h", new_json),
    ):
        before_xml = (
            set(castxml_cache_dir.glob("*.xml"))
            if castxml_cache_dir.is_dir()
            else set()
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "abicheck.cli",
                "dump",
                str(lib),
                "--header",
                str(case_dir / header),
                "--public-header",
                str(case_dir / header),
                "--header-graph",
                "-o",
                str(out),
            ],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        dump_stderrs[header] = result.stderr
        after_xml = (
            set(castxml_cache_dir.glob("*.xml"))
            if castxml_cache_dir.is_dir()
            else set()
        )
        for new_file in after_xml - before_xml:
            try:
                castxml_xml[header] = new_file.read_text(errors="replace")
            except OSError:
                pass

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
    got_kinds = {c["kind"] for c in payload.get("changes", [])}
    expected_kinds = expected_extra_kinds | {"public_api_internal_dependency_added"}
    missing = expected_kinds - got_kinds
    if payload["verdict"] != expected_verdict or missing:
        # TEMPORARY diagnostic (not gated on -s; pytest only shows captured
        # stdout for a failing test, so this is silent on green runs): dump
        # the full old/new snapshot `types` + the emitted `changes` so a
        # platform-specific miss (seen on macos-latest, never on
        # ubuntu-latest, for this exact test) can be root-caused from CI
        # log output alone, without local macOS/castxml access. Remove once
        # the macOS-specific gap this is chasing is understood and fixed.
        old_payload = json.loads(old_json.read_text())
        new_payload = json.loads(new_json.read_text())
        print(f"\n--- {case_name} diagnostic (sys.platform={sys.platform}) ---")
        print(
            "old ast_producer/from_headers:",
            old_payload.get("ast_producer"),
            old_payload.get("from_headers"),
        )
        print(
            "new ast_producer/from_headers:",
            new_payload.get("ast_producer"),
            new_payload.get("from_headers"),
        )
        # `dump`'s UserWarning (emitted by service._try_header_scoped_dump when
        # it discards a header-scoped snapshot and falls back to export-table
        # mode) lands on stderr but is only surfaced by pytest on a failing
        # test — capture both dump invocations' stderr here rather than only
        # the final `compare` step's, since the fallback happens at dump time.
        print("v1.h dump stderr:", dump_stderrs.get("v1.h", ""))
        print("v2.h dump stderr:", dump_stderrs.get("v2.h", ""))
        print("v1.h castxml XML:", castxml_xml.get("v1.h", "<not found in cache>"))
        print("v2.h castxml XML:", castxml_xml.get("v2.h", "<not found in cache>"))
        print("old macho exports:", json.dumps(old_payload.get("macho", {}), indent=2))
        print("new macho exports:", json.dumps(new_payload.get("macho", {}), indent=2))
        print("old types:", json.dumps(old_payload.get("types", []), indent=2))
        print("new types:", json.dumps(new_payload.get("types", []), indent=2))
        print("old functions:", json.dumps(old_payload.get("functions", []), indent=2))
        print("new functions:", json.dumps(new_payload.get("functions", []), indent=2))
        print("changes:", json.dumps(payload.get("changes", []), indent=2))
    assert payload["verdict"] == expected_verdict, payload["verdict"]
    assert not missing, f"{case_name}: missing kinds {missing}; got {got_kinds}"
