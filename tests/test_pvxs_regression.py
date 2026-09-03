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

"""End-to-end regression for the PVXS-style C++20 false-positive dialect bug.

PVXS's ``json.h``-style headers guard a required base type with a plain
preprocessor diagnostic, e.g.::

    #ifndef HAVE_BASE
    #error Foo requires Base
    #endif

abicheck's C++20 ``requires``/``concept`` auto-detection heuristic used to
scan raw header text for the word "requires" without skipping preprocessor
directive lines, so this ``#error`` line alone was enough to make it believe
the header needed C++20, silently forcing ``-std=gnu++20`` onto a project
that only ever declared C++11 support. That is now fixed at two levels
(``dumper_ast_config_cpp20._detect_cpp20_headers`` skips directive lines
entirely; ``cli_helpers_compare._pair_wide_dialect_override`` resolves the
dialect once over the union of both sides so old/new can never disagree) —
both already have direct unit coverage. This test is the missing outer
layer: a real compile + real ``abicheck dump``/``compare`` invocations
against actual binaries and headers containing exactly this pattern, on a
project that only ever declared C++11 support and never passes an explicit
``-std=``. The decisive assertion dumps each side and checks the schema v15
``ast_resolved_standard`` provenance field directly to prove the dialect was
not force-resolved to ``"gnu++20"`` -- checking only the ABI verdict/SONAME
advisory (as an earlier version of this test did) would not actually
falsify the bug here, since ``int pvxs_op(int x)`` mangles identically under
gnu++11 and a wrongly-forced gnu++20 (CodeRabbit review). The ``compare``
step and its verdict/SONAME assertions remain as a secondary full-pipeline
smoke check.

Marked ``integration`` (needs a real compiler); uses ``--ast-frontend clang``
so it also runs on hosts with clang but no castxml (unlike most other
integration tests here, which default to castxml).
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
from abicheck.model import AbiSnapshot
from abicheck.serialization import snapshot_from_dict

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform == "win32", reason="builds an ELF .so pair, linux/macos only"
    ),
    pytest.mark.skipif(
        shutil.which("g++") is None, reason="g++ required to build fixtures"
    ),
    pytest.mark.skipif(
        shutil.which("clang") is None, reason="clang required for --ast-frontend clang"
    ),
]

#: The exact PVXS json.h pattern: a plain "requires" inside a preprocessor
#: diagnostic, not a real C++20 requires-clause. HAVE_BASE is always defined
#: by the (real) build below, so the #error never actually fires -- exactly
#: like a real project's config header satisfying its own guard -- but the
#: raw source text still contains the string that used to trip the heuristic.
_GUARDED_HEADER = """\
#ifndef HAVE_BASE
#error Foo requires Base
#endif
int pvxs_op(int x);
"""

_SOURCE = """\
#include "pvxs.h"
int pvxs_op(int x) { return x; }
"""


def _build_lib(src_dir: Path, out_so: Path) -> None:
    header = src_dir / "pvxs.h"
    header.write_text(_GUARDED_HEADER, encoding="utf-8")
    source = src_dir / "pvxs.cpp"
    source.write_text(_SOURCE, encoding="utf-8")
    subprocess.run(
        [
            "g++",
            "-DHAVE_BASE=1",
            "-std=gnu++11",
            "-fPIC",
            "-shared",
            "-o",
            str(out_so),
            str(source),
        ],
        cwd=src_dir,
        check=True,
        capture_output=True,
        text=True,
    )


def _dump_snapshot(so_path: Path, header: Path, out_json: Path) -> AbiSnapshot:
    result = CliRunner().invoke(
        main,
        [
            "dump",
            str(so_path),
            "-H",
            str(header),
            "--ast-frontend",
            "clang",
            "--compiler-option",
            "-DHAVE_BASE=1",
            "-o",
            str(out_json),
        ],
    )
    assert result.exit_code == 0, result.output
    # `dump -o` writes the D8-sectioned on-disk shape (ADR-062/ADR-063 Phase
    # 8), not a flat top-level dict -- ``ast_resolved_standard`` lives under
    # ``sections.debug.payload`` there. ``snapshot_from_dict`` is the one
    # maintained compatibility surface that reads either that shape or a
    # legacy flat one, so go through it (and the real ``AbiSnapshot``
    # attribute) rather than indexing the raw JSON directly.
    return snapshot_from_dict(json.loads(out_json.read_text(encoding="utf-8")))


def test_pvxs_error_requires_guard_does_not_force_cxx20(tmp_path: Path) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()

    old_so = old_dir / "libpvxs.so"
    new_so = new_dir / "libpvxs.so"
    _build_lib(old_dir, old_so)
    _build_lib(new_dir, new_so)

    # Decisive check first: dump each side on its own (no explicit -std=) and
    # confirm the compile-context provenance (schema v15) shows the dialect
    # was NOT forced to gnu++20 by the misleading "#error ... requires ..."
    # text. This is the assertion that actually falsifies the bug --
    # int pvxs_op(int x) mangles identically under gnu++11 and a
    # wrongly-forced gnu++20, so neither the verdict nor SONAME-advisory
    # assertions below would ever fail even with the detection bug
    # reintroduced (CodeRabbit review: the original version of this test
    # only checked those indirect, dialect-insensitive signals).
    #
    # abicheck-internal-bugs finding 2 follow-up: with no explicit -std=,
    # this no longer resolves to bare None -- dumper_toolchain._probe_
    # default_language_standard now fills in the resolved compiler's own
    # *probed* default dialect (host-clang-version-dependent), so "not
    # forced to gnu++20" is asserted directly instead of "stayed None".
    old_snap = _dump_snapshot(old_so, old_dir / "pvxs.h", tmp_path / "old.abi.json")
    new_snap = _dump_snapshot(new_so, new_dir / "pvxs.h", tmp_path / "new.abi.json")
    assert old_snap.ast_resolved_standard != "gnu++20", old_snap.ast_resolved_standard
    assert new_snap.ast_resolved_standard != "gnu++20", new_snap.ast_resolved_standard
    # Both sides were built identically and dumped under the identical
    # resolved clang -- their (possibly probed) dialects must still agree.
    assert old_snap.ast_resolved_standard == new_snap.ast_resolved_standard

    out_json = tmp_path / "report.json"
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(old_so),
            str(new_so),
            "--header",
            f"old={old_dir / 'pvxs.h'}",
            "--header",
            f"new={new_dir / 'pvxs.h'}",
            "--ast-frontend",
            "clang",
            "--compiler-option",
            "-DHAVE_BASE=1",
            "--format",
            "json",
            "-o",
            str(out_json),
        ],
    )
    assert result.exit_code == 0, result.output

    report = json.loads(out_json.read_text(encoding="utf-8"))
    # NO_CHANGE/COMPATIBLE is the common case. On a platform where header <->
    # export-table symbol matching itself degrades for an unrelated reason
    # (observed on macOS Mach-O: --scope-public-headers falls back to the
    # full export table, reducing confidence and tripping an unrelated
    # runtime/toolchain-floor RISK finding) the verdict can widen to
    # COMPATIBLE_WITH_RISK, which correctly earns its own PATCH/MINOR release
    # recommendation -- that degradation is orthogonal to the C++20 dialect
    # bug this test guards against, so it isn't asserted away. These are a
    # secondary, full-pipeline smoke check (does `compare` blow up on this
    # pair at all) -- the decisive dialect-forcing guard is the
    # ast_resolved_standard assertion above, since a forced C++20 dialect
    # would not itself perturb these ABI-level signals for this pair.
    assert report["verdict"] not in ("BREAKING", "API_BREAK")
    assert report["summary"]["breaking"] == 0
    assert report["summary"]["source_breaks"] == 0
    kinds = {c.get("kind") for c in report.get("changes", [])}
    assert "soname_bump_recommended" not in kinds
    release = report.get("release_recommendation") or {}
    assert release.get("soname_action") not in ("bump_required", "bump_missing")
    assert release.get("version_bump") != "major"


def test_pvxs_explicit_gnu11_dialect_resolves_and_matches_on_both_sides(
    tmp_path: Path,
) -> None:
    """Companion to the test above (status-review follow-up): that test only
    proves the C++20 auto-detection heuristic did NOT fire (``ast_resolved_
    standard is None`` -- no explicit dialect was ever requested, so nothing
    to resolve). It does not prove old/new were actually parsed under
    gnu++11 -- ``int pvxs_op(int x)`` mangles identically under gnu++11 and a
    silently-forced gnu++20, so a scanner that quietly ignored the profile's
    dialect and defaulted to some other standard entirely would pass that
    test too. This test closes that gap: both sides are dumped with an
    *explicit* ``--compiler-option -std=gnu++11``, and the decisive assertion is
    that both snapshots' own ``ast_resolved_standard`` provenance field
    reads back exactly ``"gnu++11"`` -- proving the requested dialect was
    the one actually used, not merely that C++20 wasn't force-applied."""
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()

    old_so = old_dir / "libpvxs.so"
    new_so = new_dir / "libpvxs.so"
    _build_lib(old_dir, old_so)
    _build_lib(new_dir, new_so)

    def _dump_with_explicit_gnu11(
        so_path: Path, header: Path, out_json: Path
    ) -> AbiSnapshot:
        result = CliRunner().invoke(
            main,
            [
                "dump",
                str(so_path),
                "-H",
                str(header),
                "--ast-frontend",
                "clang",
                "--compiler-option",
                "-DHAVE_BASE=1",
                "--compiler-option",
                "-std=gnu++11",
                "-o",
                str(out_json),
            ],
        )
        assert result.exit_code == 0, result.output
        # See `_dump_snapshot`'s own comment: `dump -o` writes the
        # D8-sectioned on-disk shape, so read it back through
        # `snapshot_from_dict` rather than indexing the raw JSON.
        return snapshot_from_dict(json.loads(out_json.read_text(encoding="utf-8")))

    old_snap = _dump_with_explicit_gnu11(
        old_so, old_dir / "pvxs.h", tmp_path / "old-gnu11.abi.json"
    )
    new_snap = _dump_with_explicit_gnu11(
        new_so, new_dir / "pvxs.h", tmp_path / "new-gnu11.abi.json"
    )
    # The decisive assertion: both sides actually resolved to gnu++11, not
    # merely "not gnu++20" -- and they agree with each other.
    assert old_snap.ast_resolved_standard == "gnu++11", old_snap.ast_resolved_standard
    assert new_snap.ast_resolved_standard == "gnu++11", new_snap.ast_resolved_standard
    assert old_snap.ast_resolved_standard == new_snap.ast_resolved_standard

    out_json = tmp_path / "report-gnu11.json"
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(old_so),
            str(new_so),
            "--header",
            f"old={old_dir / 'pvxs.h'}",
            "--header",
            f"new={new_dir / 'pvxs.h'}",
            "--ast-frontend",
            "clang",
            "--compiler-option",
            "-DHAVE_BASE=1",
            "--compiler-option",
            "-std=gnu++11",
            "--format",
            "json",
            "-o",
            str(out_json),
        ],
    )
    assert result.exit_code == 0, result.output
    report = json.loads(out_json.read_text(encoding="utf-8"))
    # Same tolerance as the sibling test above: NO_CHANGE/COMPATIBLE is the
    # common case, but a platform-specific header<->export-table matching
    # degradation (observed on macOS) can widen this to
    # COMPATIBLE_WITH_RISK -- orthogonal to what this test guards (the
    # requested dialect actually took effect), so only BREAKING/API_BREAK
    # are asserted away.
    assert report["verdict"] not in ("BREAKING", "API_BREAK")
    assert report["summary"]["breaking"] == 0
    assert report["summary"]["source_breaks"] == 0
