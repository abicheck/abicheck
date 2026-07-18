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
    for src, out in ((case_dir / "v1.cpp", libv1), (case_dir / "v2.cpp", libv2)):
        result = subprocess.run(
            [cxx, "-std=c++17", "-shared", "-fPIC", "-g", str(src), "-o", str(out)],
            cwd=case_dir,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

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
    missing = expected_kinds - got_kinds
    assert not missing, f"{case_name}: missing kinds {missing}; got {got_kinds}"
