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
wired into both ``perform_elf_dump`` and ``handle_non_elf_dump`` at the time
(both since retired -- ADR-063 Phase 1 moved ``dump``'s real run onto the
shared typed executor, which does the identical fold in
``service_input_resolution._resolve_side_snapshot_impl``, and Track 1 then
deleted them). The existing regression coverage for that fold
(``tests/test_header_compile_context.py``'s ``resolve_side_snapshot``
stamp/context tests and ``tests/test_typed_dump_request.py``'s seed/fold
class) verifies the mechanism with a stubbed parse, at the unit level. Per this file's own "Third-party-boundary tests must exercise the
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
first time surfaced a real, but *different*, pre-existing gap -- not the
``NOT_COMPARABLE`` bug this module exists to regression-test.
``scan``'s candidate-side L4 source-ABI replay passed a hardcoded
``source_extractor="auto"`` (which ``buildsource.inline.
_make_source_extractor`` resolves to clang) regardless of the
``--ast-frontend`` the caller actually passed, while ``dump``'s own L4
replay follows it via ``service_compare_evidence.effective_frontend``. So
under ``--ast-frontend castxml`` the ``dump`` baseline's L4 replay ran
through castxml and ``scan``'s candidate replay through clang -- two
different tools independently replaying the same translation unit, which
do not always resolve one exported symbol the same way -- producing a
spurious ``COMPATIBLE_WITH_RISK``
(``source_fact_coverage_incomplete``) verdict on completely unchanged
source. It was pinned here as an xfailed, exact known-divergent signature
rather than silently weakened.

That pin is now gone, because the gap it described is closed:
``scan_engine``'s call site resolves an *explicitly* requested frontend
through ``service_compare_evidence.explicit_source_extractor``,
which delegates to the same ``effective_frontend`` ``dump``/``compare``
use, so an explicit ``--ast-frontend`` selects the same L4 backend on both
sides by construction. Both parametrizations must now come out completely
clean, asserted against the same exhaustive bucket set the xfail predicate
used to inspect (``_assert_scan_text_is_clean``/
``_assert_scan_report_is_clean``).

Deliberately still open, and *not* claimed closed by any test here: the
**unflagged** default still differs -- ``scan`` resolves an unstated
``auto`` to clang, ``dump``/``compare`` resolve it to castxml. Matching
those would newly require castxml for a plain ``scan --depth source`` that
works with clang today, a real behaviour change tracked as item 2 of the
plan's PR 3A section (``docs/contribute/plans/cli-cleanup-phase-two.md``).
Every test in this module passes an explicit ``--ast-frontend``, so none
of them exercised that half before or after.
"""

from __future__ import annotations

import json
import re
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


# Both frontends must now come out completely clean. `scan`'s candidate-side
# L4 replay used to ignore `--ast-frontend` entirely and always replay through
# clang, so a castxml run compared a castxml `dump` baseline against a clang
# `scan` candidate -- two different tools independently replaying the same
# translation unit -- and produced a spurious COMPATIBLE_WITH_RISK
# (`source_fact_coverage_incomplete`) on unchanged source. That was pinned here
# as an xfailed known-divergent signature; `scan_engine`'s call site now honors
# an explicit request via `service_compare_evidence.
# explicit_source_extractor`, so there is no divergent frontend
# left for these tests to except and the pin is gone rather than weakened.
#
# What is deliberately NOT claimed by its removal: the *unflagged* default
# still differs (`scan` resolves an unstated "auto" to clang, `dump`/`compare`
# to castxml) -- see the plan's PR 3A item 2. Every test here passes an
# explicit `--ast-frontend`, so none of them exercised that half either before
# or after.
_CLEAN_TOTALS = (0, 0, 0, 0)  # breaking, api_break, risk, compatible

_RISK_LINE_RE = re.compile(r"^\s*\[risk\]\s+(\S+):", re.MULTILINE)
#: ``cli_scan_helpers.render_baseline_lines``'s exact counts-line format:
#: "  breaking=N api_break=N risk=N compatible=N". Checked in addition to
#: the risk-finding multiset above -- Codex review, round 3: an unrelated
#: *compatible*-category false positive (``diff.compatible``, itemized
#: under ``summary["additions"]``/``["quality"]``, never
#: ``summary["findings"]``) leaves ``verdict``/``[risk] ...`` lines
#: completely untouched, so neither prior check could ever see it. Only
#: the aggregate ``compatible=0`` (and the ``breaking``/``api_break``
#: counts, for the same reason) can.
_COUNTS_LINE_RE = re.compile(
    r"breaking=(\d+)\s+api_break=(\d+)\s+risk=(\d+)\s+compatible=(\d+)"
)
#: ``render_crosscheck_lines`` only emits this block at all when
#: ``out.crosscheck["counts_by_check"]`` is non-empty -- an entirely
#: separate, always-on block from the baseline ``diff`` this module
#: otherwise inspects (``ScanOutcome.crosscheck``, not
#: ``ScanOutcome.diff_summary``), rendered as its own
#: ``[<severity>] <kind>: <n>`` lines under one of these two headings
#: depending on ``--audit``. Neither heading appeared in the real
#: CI-captured failure text this module's docstring quotes, but nothing
#: before this checked for its *absence* -- Codex review, round 4: an
#: unrelated cross-source regression would leave the verdict and every
#: baseline count this module already checks completely untouched.
_CROSSCHECK_HEADINGS = (
    "Cross-source findings (advisory)",
    "ABI-hygiene catalog (intra-version, advisory)",
)

#: The remaining two advisory blocks (``render_pattern_lines``/
#: ``render_preprocessor_lines``) with the identical shape as crosscheck
#: above: each is rendered only when its own source has something to
#: report, entirely independent of ``diff``/``crosscheck``. Closed
#: proactively alongside the crosscheck fix rather than waiting for a
#: fifth review round to name each one individually -- the three
#: advisory blocks are ``ScanOutcome``'s complete, enumerable set (see
#: ``cli_scan_helpers.py``'s own ``render_*_lines`` functions), so this is
#: the general fix ("no advisory finding of any kind"), not another
#: one-off patch.
_ADVISORY_HEADINGS = _CROSSCHECK_HEADINGS + (
    "Pattern pre-scan facts (advisory)",
    "Preprocessor pre-scan facts (S2, advisory)",
)


def _assert_scan_text_is_clean(output: str) -> None:
    """The text-mode scan output must report *nothing at all*: every baseline
    count zero, no ``[risk] <kind>:`` line, and none of the three advisory
    blocks (cross-check, pattern, preprocessor) with anything to report.

    This is the positive form of what used to be an
    "is this the known L4-extractor divergence?" predicate. The buckets it
    inspects are unchanged and deliberately exhaustive -- each was added by
    its own review round after an earlier, narrower check was shown to be
    blind to a whole finding category: the aggregate counts line catches a
    *compatible*-category finding (itemized under ``summary["additions"]``/
    ``["quality"]``, which never touches ``verdict`` or any ``[risk]`` line),
    and the three advisory headings are each a wholly separate, always-on
    ``ScanOutcome`` field the ``verdict``/``diff`` never reflect. Asserting
    "clean" against the same exhaustive bucket set is strictly stronger than
    asserting ``Verdict: NO_CHANGE`` alone, which is why the bucket knowledge
    is kept rather than deleted along with the xfail it used to gate.

    Text mode cannot go finer than kind granularity: ``cli_scan_helpers.
    render_baseline_lines`` renders each finding as only
    ``[<bucket>] <kind>: <symbol><location>``, never the ``description``
    that distinguishes *which* cause produced a given kind. Prefer the JSON
    variant below in any new test that needs cause-level distinction.
    """
    advisory = [h for h in _ADVISORY_HEADINGS if h in output]
    assert not advisory, f"unexpected advisory block(s) {advisory}: {output}"
    counts_match = _COUNTS_LINE_RE.search(output)
    assert counts_match is not None, f"no counts line in output: {output}"
    assert tuple(int(g) for g in counts_match.groups()) == _CLEAN_TOTALS, output
    assert not _RISK_LINE_RE.findall(output), output
    assert "COMPATIBLE_WITH_RISK" not in output, output


def _assert_scan_report_is_clean(report: dict) -> None:
    """JSON-report form of :func:`_assert_scan_text_is_clean`: every baseline
    count zero, no baseline finding of any kind, and none of the three
    advisory blocks with anything to report.

    Several buckets live entirely outside ``report["diff"]`` and are checked
    separately, each added by the review round that found the previous check
    blind to it: an unrelated *compatible*-category finding (itemized under
    ``diff.additions``/``diff.quality``, which ``verdict``/``findings`` never
    reflect, so only the aggregate ``compatible`` count can see it), and the
    three advisory blocks -- ``report["crosscheck"]["counts_by_check"]``,
    ``report["pattern_scan"]["counts_by_kind"]``, and
    ``report["preprocessor_scan"]["divergences"/"leaks"]`` -- each a wholly
    separate, always-on ``ScanOutcome`` field, non-empty only when that
    source actually found something.

    ``scan --format json``'s ``ScanOutcome.to_dict()`` has no top-level
    ``changes`` array (that is ``compare``'s report shape) -- its baseline
    findings are nested under ``report["diff"]["findings"]``
    (``cli_scan_baseline._baseline_summary``'s ``summary["findings"]``).
    """
    assert not (report.get("crosscheck") or {}).get("counts_by_check"), report
    assert not (report.get("pattern_scan") or {}).get("counts_by_kind"), report
    preprocessor_scan = report.get("preprocessor_scan") or {}
    assert not preprocessor_scan.get("divergences"), report
    assert not preprocessor_scan.get("leaks"), report
    diff = report.get("diff") or {}
    totals = (
        diff.get("breaking"),
        diff.get("api_break"),
        diff.get("risk"),
        diff.get("compatible"),
    )
    assert totals == _CLEAN_TOTALS, report
    assert not (diff.get("findings") or []), report


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

    from abicheck.serialization import load_snapshot_document

    snap = load_snapshot_document(baseline)
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

    _assert_scan_text_is_clean(scan_result.output)
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

    _assert_scan_report_is_clean(report)
    assert report.get("verdict") == "NO_CHANGE", report
