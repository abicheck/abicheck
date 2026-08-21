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

"""End-to-end regression coverage for the "dump-vs-scan compile-context
divergence" bug report (a real ``abicheck dump`` baseline compared against
an unchanged codebase via ``abicheck scan --against`` reported
``NOT_COMPARABLE``/``profile_fingerprint`` mismatch, because ``dump``'s own
CLI path did not fold real L3 build evidence into its L2 header-AST parse
the way ``compare``'s implicit-dump operand and ``scan``'s candidate
resolution already did).

AGENTS.md's "Known gaps" section documents this class of bug at length (the
"The native ELF `abicheck dump` path never applies L3 build context to its
own L2 header parse" entry) and records it closed via the P0.3 L3->L2 fold
(``buildsource/l2_seed.py``'s ``seed_includes_and_fold_compile_context``),
wired into both ``perform_elf_dump`` and ``handle_non_elf_dump``. The
existing regression coverage for that fold
(``tests/test_cli_dump_helpers_coverage.py::
test_perform_elf_dump_folds_l3_compile_context_into_header_parse`` and
siblings) verifies the mechanism with a stubbed ``dump()`` call, at the unit
level. Per this file's own "Third-party-boundary tests must exercise the
real public API at realistic scale" convention, this module adds the
missing piece: a real ``abicheck dump`` CLI invocation, over a real
g++-compiled library and a real ``compile_commands.json``, whose JSON
output is then fed to a real ``abicheck scan --against`` CLI invocation on
the same (unchanged) sources -- confirming end to end that the reported
symptom (``ast_resolved_standard``/``ast_compile_args`` empty on the
``dump`` side, ``NOT_COMPARABLE`` on the ``scan`` side) does not reproduce
on the current fold, rather than trusting the mocked unit coverage alone.

Marked ``integration`` (real compiler-backed, so it must stay out of the
default fast lane -- CLAUDE.md's "Default fast command excludes all
external-tool markers").

Parametrized over ``--ast-frontend`` (``clang`` and ``castxml``, the
default header backend): the original bug report was reproduced against
castxml specifically, and the underlying fix (the P0.3 L3->L2 fold, the
legacy-match-overlap removal, ``scan``'s candidate resolver sharing
``dump``'s own primitive) operates purely on ``CompileContext``/token
data below where the two header backends diverge, so it is expected to
hold for both -- but until this module actually exercised castxml, that
was only an untested assumption, not a verified fact, and the Linux
``integration`` CI lane installs both toolchains
(``.github/workflows/ci.yml``) without either ever being exercised by
this module. ``clang`` was used exclusively in earlier revisions purely
because this module's own local development/verification environment
had no castxml install; that is no longer a reason to leave the
default backend unverified in CI, which does have one.

Running this module's ``scan``-comparison tests under castxml for the
first time surfaced a real, but *different*, pre-existing and already
self-documented gap -- not the ``NOT_COMPARABLE`` bug this module
exists to regression-test. ``scan_engine.run_scan_core``'s own comment
records that ``scan``'s candidate-side L4 source-ABI replay always uses
``source_extractor="auto"`` (which ``buildsource.inline.
_make_source_extractor`` resolves to clang), never the
``--ast-frontend`` the caller actually passed, "a real behaviour change
for real users, unverifiable without a castxml-capable lane" -- while
``dump``'s own L4 replay *does* follow ``--ast-frontend`` via
``service_compare_evidence.effective_frontend``. Under
``--ast-frontend castxml`` this means the ``dump`` baseline's L4 replay
runs through castxml while ``scan``'s own candidate replay still runs
through clang -- two different tools independently replaying the same
translation unit, which do not always resolve one exported symbol the
same way; the result is a spurious ``COMPATIBLE_WITH_RISK`` verdict
(``source_fact_coverage_incomplete``/``source_binary_provenance_
mismatch``) on completely unchanged source, never ``NOT_COMPARABLE``.
Since a castxml-capable lane is exactly what was previously missing to
verify this, it is now pinned as a known-divergent, exact signature
(mirroring ``tests/test_dump_cli_typed_api_parity.py``'s own
``_SCAN_KNOWN_DIVERGENT_SHAPES`` pattern) rather than silently
weakened or left to fail CI outright -- fixing the extractor selection
itself is the real behaviour change ``run_scan_core``'s own comment
already declines to make in this same PR, since it would need its own
dedicated verification against real castxml/clang divergence in
production usage, not a side effect of hardening this module's test
coverage.
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
    pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="ELF/Linux-scoped repro (real g++-compiled .so + compile_commands.json)",
    ),
]

_HAVE_GXX = shutil.which("g++") is not None
_HAVE_CLANG = shutil.which("clang") is not None
_HAVE_CASTXML = shutil.which("castxml") is not None

# Both header backends are exercised: the fix operates on shared
# CompileContext/token data below the point where the two backends
# diverge, so a regression specific to one of them must not hide behind
# only ever testing the other.
_AST_FRONTENDS = pytest.mark.parametrize("ast_frontend", ["clang", "castxml"])


def _have_frontend(ast_frontend: str) -> bool:
    return _HAVE_CASTXML if ast_frontend == "castxml" else _HAVE_CLANG


# scan's candidate-side L4 replay always uses clang (see module docstring) --
# only "castxml" is known-divergent for the two scan-comparison tests below.
# Not "dump-produces-a-different-baseline": the dump-side test above this
# constant has no scan candidate at all, so it is unaffected and has no
# xfail list of its own -- a future regression narrowing *that* path's
# coverage should fail loudly rather than being masked here.
_SCAN_KNOWN_DIVERGENT_FRONTENDS: frozenset[str] = frozenset({"castxml"})


def _is_known_l4_extractor_divergence(output: str) -> bool:
    """The exact, already-diagnosed signature: a RISK verdict from the two
    L4-replay-specific reason codes ``run_scan_core``'s own comment already
    attributes to the clang-vs-castxml candidate/baseline extractor
    mismatch -- never a broader "any non-NO_CHANGE verdict" check, so an
    unrelated regression still surfaces as a genuine, uncaught failure."""
    return (
        "COMPATIBLE_WITH_RISK" in output
        and "source_fact_coverage_incomplete" in output
        and "source_binary_provenance_mismatch" in output
    )


def _build_library(
    tmp_path: Path, *, extra_include_dir: str | None = None
) -> tuple[Path, Path, Path]:
    """Compile a tiny real C++17 library + a matching compile_commands.json.

    Mirrors the bug report's repro shape: a library genuinely built with an
    explicit ``-std=`` the header itself depends on (so a dump that failed
    to fold the real standard would produce headers parsed under the wrong
    dialect, not merely empty metadata).

    ``extra_include_dir``, when given, adds a real dependency header under a
    separate ``-I``-supplied directory (mirroring
    ``tests/test_dump_cli_typed_api_parity.py``'s own ``"extra-include-dir"``
    shape) -- the specific build-evidence shape the AGENTS.md "Known gaps"
    entry's "Two real mechanisms found and fixed" paragraph diagnoses as
    what actually reproduces the reported ``include_sequence`` divergence; a
    compile database with no extra ``-I`` at all does not exercise the
    legacy-match-overlap/derived-include-routing bug this module regression-
    tests, on either the buggy or the fixed commit (verified directly: see
    the module docstring's own note against re-adding one without this
    parameter).
    """
    include_dirs = [tmp_path]
    dep_header_snippet = ""
    dep_field = ""
    if extra_include_dir is not None:
        dep_dir = tmp_path / extra_include_dir
        dep_dir.mkdir(parents=True, exist_ok=True)
        (dep_dir / "dep.h").write_text(
            "#pragma once\nstruct Dep { int tag; };\n", encoding="utf-8"
        )
        include_dirs.append(dep_dir)
        dep_header_snippet = '#include "dep.h"\n'
        dep_field = "    Dep dep;\n"

    header = tmp_path / "widget.h"
    header.write_text(
        "#pragma once\n"
        f"{dep_header_snippet}"
        "#if __cplusplus < 201703L\n"
        '#error "needs c++17"\n'
        "#endif\n"
        "struct Widget {\n"
        "    int x;\n"
        "    int y;\n"
        f"{dep_field}"
        "    int sum() const { return x + y; }\n"
        "};\n",
        encoding="utf-8",
    )
    src = tmp_path / "widget.cpp"
    src.write_text(
        '#include "widget.h"\nint compute(const Widget& w) { return w.sum(); }\n',
        encoding="utf-8",
    )
    so_path = tmp_path / "libwidget.so"
    include_flags = [f"-I{d}" for d in include_dirs[1:]]
    subprocess.run(
        [
            "g++",
            "-std=c++17",
            *include_flags,
            "-shared",
            "-fPIC",
            "-o",
            str(so_path),
            str(src),
        ],
        check=True,
        capture_output=True,
    )
    compile_db = tmp_path / "compile_commands.json"
    compile_db.write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "arguments": [
                        "g++",
                        "-std=c++17",
                        *include_flags,
                        "-shared",
                        "-fPIC",
                        "-c",
                        str(src),
                        "-o",
                        "widget.o",
                    ],
                    "file": str(src),
                }
            ]
        ),
        encoding="utf-8",
    )
    return so_path, header, compile_db


@_AST_FRONTENDS
def test_dump_folds_real_l3_evidence_into_ast_compile_context(
    tmp_path: Path, ast_frontend: str
) -> None:
    """A real ``dump --build-info`` baseline carries the real ``-std=``.

    Direct assertion on the reported symptom: ``ast_resolved_standard``/
    ``ast_compile_args`` must not be empty when real L3 build evidence
    (a compile database recording ``-std=c++17``) was supplied.
    """
    if not (_HAVE_GXX and _have_frontend(ast_frontend)):
        pytest.skip(f"needs a real g++ and {ast_frontend} toolchain")
    so_path, header, compile_db = _build_library(tmp_path)
    baseline = tmp_path / "baseline.json"

    result = CliRunner().invoke(
        main,
        [
            "dump",
            str(so_path),
            "-H",
            str(header),
            "--sources",
            str(tmp_path),
            "--build-info",
            str(compile_db),
            "--depth",
            "source",
            "--ast-frontend",
            ast_frontend,
            "-o",
            str(baseline),
        ],
    )
    assert result.exit_code == 0, result.output

    snap = json.loads(baseline.read_text(encoding="utf-8"))
    assert snap.get("ast_resolved_standard") == "c++17", snap.get(
        "ast_resolved_standard"
    )
    assert snap.get("ast_compile_args"), "ast_compile_args must not be empty"
    assert snap.get("parsed_with_build_context") is True


@_AST_FRONTENDS
def test_scan_against_real_dump_baseline_is_comparable_on_unchanged_source(
    tmp_path: Path, ast_frontend: str
) -> None:
    """The full repro from the bug report: dump a baseline, then ``scan
    --against`` it on the identical, unchanged codebase. Must resolve as
    comparable (``NO_CHANGE``/exit 0), never ``NOT_COMPARABLE`` (exit 6)."""
    if not (_HAVE_GXX and _have_frontend(ast_frontend)):
        pytest.skip(f"needs a real g++ and {ast_frontend} toolchain")
    so_path, header, compile_db = _build_library(tmp_path)
    baseline = tmp_path / "baseline.json"

    dump_result = CliRunner().invoke(
        main,
        [
            "dump",
            str(so_path),
            "-H",
            str(header),
            "--sources",
            str(tmp_path),
            "--build-info",
            str(compile_db),
            "--depth",
            "source",
            "--ast-frontend",
            ast_frontend,
            "-o",
            str(baseline),
        ],
    )
    assert dump_result.exit_code == 0, dump_result.output

    scan_result = CliRunner().invoke(
        main,
        [
            "scan",
            str(so_path),
            "-H",
            str(header),
            "--sources",
            str(tmp_path),
            "--build-info",
            str(compile_db),
            "--depth",
            "source",
            "--ast-frontend",
            ast_frontend,
            "--against",
            str(baseline),
        ],
    )
    assert "NOT_COMPARABLE" not in scan_result.output, scan_result.output
    assert "profile_fingerprint mismatch" not in scan_result.output, scan_result.output
    assert scan_result.exit_code == 0, scan_result.output

    if ast_frontend in _SCAN_KNOWN_DIVERGENT_FRONTENDS:
        if _is_known_l4_extractor_divergence(scan_result.output):
            pytest.xfail(
                f"{ast_frontend}: known scan-candidate-L4-extractor-always-"
                "clang divergence (see this module's own docstring)"
            )
        pytest.fail(
            f"{ast_frontend} is listed in _SCAN_KNOWN_DIVERGENT_FRONTENDS, "
            "but the previously-diagnosed failure signature "
            "(COMPATIBLE_WITH_RISK naming source_fact_coverage_incomplete "
            "and source_binary_provenance_mismatch) did not reproduce. "
            "Either the underlying gap has closed -- remove this frontend "
            "from _SCAN_KNOWN_DIVERGENT_FRONTENDS and update this module's "
            "docstring -- or a different failure occurred and needs its "
            f"own investigation. output={scan_result.output!r}"
        )
    assert "Verdict: NO_CHANGE" in scan_result.output, scan_result.output


@_AST_FRONTENDS
def test_scan_against_real_dump_baseline_matches_reported_cli_invocation(
    tmp_path: Path, ast_frontend: str
) -> None:
    """The exact CLI invocation shape from the bug report: a side-prefixed
    ``-H new=PATH`` header, an explicit ``--lang c++``, an explicit
    ``--policy strict_abi``, and JSON output -- not just the bare
    ``-H PATH``/default-policy shape the sibling test above already covers.

    None of those extra flags should matter to whether the P0.3 L3->L2 fold
    reconciles ``dump``'s and ``scan``'s compile contexts, but the original
    report used exactly this combination, so it is pinned directly rather
    than trusted to be equivalent to the simpler sibling test.

    Uses ``extra_include_dir`` (a real dependency header under its own
    ``-I``-supplied directory) rather than the sibling test's plain,
    no-extra-``-I`` build: a compile database with no extra include
    directory at all does not exercise the legacy-match-overlap/derived-
    include-routing bug this module regression-tests -- confirmed directly
    against the reported buggy commit (``6fb8536``), which resolves this
    same invocation as ``NO_CHANGE`` even *without* the fix when no extra
    ``-I`` is involved. See ``_build_library``'s own docstring.
    """
    if not (_HAVE_GXX and _have_frontend(ast_frontend)):
        pytest.skip(f"needs a real g++ and {ast_frontend} toolchain")
    so_path, header, compile_db = _build_library(tmp_path, extra_include_dir="dep")
    baseline = tmp_path / "baseline.json"

    dump_result = CliRunner().invoke(
        main,
        [
            "dump",
            str(so_path),
            "-H",
            str(header),
            "--sources",
            str(tmp_path),
            "--build-info",
            str(compile_db),
            "--depth",
            "source",
            "--ast-frontend",
            ast_frontend,
            "-o",
            str(baseline),
        ],
    )
    assert dump_result.exit_code == 0, dump_result.output

    scan_report = tmp_path / "scan-report.json"
    scan_result = CliRunner().invoke(
        main,
        [
            "scan",
            str(so_path),
            "-H",
            f"new={header}",
            "--sources",
            str(tmp_path),
            "--build-info",
            str(compile_db),
            "--against",
            str(baseline),
            "--lang",
            "c++",
            "--depth",
            "source",
            "--ast-frontend",
            ast_frontend,
            "--policy",
            "strict_abi",
            "--format",
            "json",
            "-o",
            str(scan_report),
        ],
    )
    assert scan_result.exit_code == 0, scan_result.output

    report = json.loads(scan_report.read_text(encoding="utf-8"))
    diff = report.get("diff") or {}
    assert diff.get("reason") is None, diff.get("reason")

    if ast_frontend in _SCAN_KNOWN_DIVERGENT_FRONTENDS:
        if _is_known_l4_extractor_divergence(json.dumps(report)):
            pytest.xfail(
                f"{ast_frontend}: known scan-candidate-L4-extractor-always-"
                "clang divergence (see this module's own docstring)"
            )
        pytest.fail(
            f"{ast_frontend} is listed in _SCAN_KNOWN_DIVERGENT_FRONTENDS, "
            "but the previously-diagnosed failure signature "
            "(COMPATIBLE_WITH_RISK naming source_fact_coverage_incomplete "
            "and source_binary_provenance_mismatch) did not reproduce. "
            "Either the underlying gap has closed -- remove this frontend "
            "from _SCAN_KNOWN_DIVERGENT_FRONTENDS and update this module's "
            f"docstring -- or a different failure occurred. report={report!r}"
        )
    assert report.get("verdict") == "NO_CHANGE", report
