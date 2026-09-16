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

"""A shared ``--include``/``--header`` survives a side-specific one.

The bug class (``tests/regressions/manifest.py``: ``sided-shared-input-
dropped``): a repeatable option modelled as "both-sides value + per-side
*additions*" whose resolver implemented *replacement*, so naming anything
on one side silently discarded everything the caller declared for both.
Reported independently from a PVXS run and an Intel MKL run; the MKL
descriptor's shared MPI/OpenMP include roots vanished the moment either
side named its own.

Two layers, deliberately:

* :class:`TestComposeSidedPathsProperties` states the *primitive's*
  contract as invariants over generated inputs (this repository's
  "Primitive-level property tests" guidance) -- it is a reusable merge
  primitive, and the resolvers that call it are not the only thing that
  can be wrong about it.
* :class:`TestSharedIncludeRootIsSearched` is the real two-root fixture:
  a side-specific public header that ``#include``\\ s a dependency living
  only in the *shared* root. Under replacement this cannot compile at
  all, so the assertion falsifies the bug rather than restating the
  helper's arithmetic.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.cli_compare_release import _resolve_release_headers
from abicheck.model.sided_inputs import compose_sided_paths, resolve_per_side_inputs


def _p(*names: str) -> list[Path]:
    return [Path(n) for n in names]


class TestComposeSidedPathsProperties:
    """Invariants that hold for *any* pair of inputs, not one example."""

    @pytest.mark.parametrize(
        ("shared", "side"),
        [
            ([], []),
            (_p("a"), []),
            ([], _p("a")),
            (_p("a", "b"), _p("c")),
            (_p("a", "b"), _p("b")),
            (_p("a"), _p("a")),
            (_p("a", "a"), _p("b", "b")),
            (_p("x/y", "x/z"), _p("x/z", "q")),
            (_p("a", "b", "c"), _p("c", "b", "a")),
        ],
    )
    def test_invariants(self, shared: list[Path], side: list[Path]) -> None:
        out = compose_sided_paths(shared, side)
        # 1. Nothing the caller declared is lost -- the whole point.
        assert set(out) == set(shared) | set(side)
        # 2. No duplicates: a repeated -I changes no search, and a receipt
        #    listing one twice reads as a mistake.
        assert len(out) == len(set(out))
        # 3. Side-specific entries keep search precedence over shared ones.
        for s in side:
            for sh in shared:
                if sh != s:
                    assert out.index(s) < out.index(sh) or sh in side
        # 4. Each input's own relative order is preserved.
        assert [q for q in out if q in set(side)] == list(dict.fromkeys(side))

    def test_disjoint_paths_are_still_expressible(self) -> None:
        """The capability replacement had and addition must not lose: a
        caller naming *only* per-side values still gets disjoint lists."""
        assert compose_sided_paths([], _p("old")) == _p("old")
        assert compose_sided_paths([], _p("new")) == _p("new")

    def test_inputs_are_not_mutated(self) -> None:
        shared, side = _p("a"), _p("b")
        compose_sided_paths(shared, side)
        assert shared == _p("a")
        assert side == _p("b")

    def test_accepts_any_iterable(self) -> None:
        """Callers pass tuples, lists and Click's own tuples alike."""
        assert compose_sided_paths(tuple(_p("a")), tuple(_p("b"))) == _p("b", "a")


class TestResolvePerSideOptionsIsAdditive:
    """The compare-CLI resolver, which is what the reported runs hit."""

    def test_shared_survives_on_both_sides(self) -> None:
        old_h, new_h, old_inc, new_inc = resolve_per_side_inputs(
            tuple(_p("shared.h")),
            tuple(_p("extra")),
            tuple(_p("old.h")),
            tuple(_p("new.h")),
            tuple(_p("old")),
            tuple(_p("new")),
        )
        assert old_h == _p("old.h", "shared.h")
        assert new_h == _p("new.h", "shared.h")
        assert old_inc == _p("old", "extra")
        assert new_inc == _p("new", "extra")


class TestResolveReleaseHeaders:
    """The release/directory fan-out applies the identical composition."""

    def test_header_dir_used_when_no_per_side(self, tmp_path: Path) -> None:
        hd_old = tmp_path / "old-hdr"
        hd_new = tmp_path / "new-hdr"
        old_h, new_h = _resolve_release_headers(
            (),
            (),
            (),
            hd_old,
            hd_new,
        )
        assert old_h == [hd_old]
        assert new_h == [hd_new]

    def test_per_side_composes_with_header_dir(self, tmp_path: Path) -> None:
        """A `--header old=` entry is searched before the package's own
        extracted header tree, which stays available underneath."""
        oh = (tmp_path / "old.h",)
        hd_old = tmp_path / "old-hdr"
        old_h, new_h = _resolve_release_headers(
            (),
            oh,
            (),
            hd_old,
            None,
        )
        assert old_h == [*oh, hd_old]

    def test_shared_header_survives_a_side_specific_one(self, tmp_path: Path) -> None:
        """The defect this composition exists to close: a shared `--header`
        was dropped entirely once either side named one of its own."""
        shared = (tmp_path / "shared.h",)
        oh = (tmp_path / "old.h",)
        nh = (tmp_path / "new.h",)
        old_h, new_h = _resolve_release_headers(shared, oh, nh, None, None)
        assert old_h == [*oh, *shared]
        assert new_h == [*nh, *shared]


_DEP_HEADER = "struct sic_dep { int a; };\n"
#: The NEW side gains a second parameter. Deliberately a *header-only*
#: difference: the C symbol name is identical on both sides, so the change
#: is invisible to the export table and can only be reported when both
#: sides' headers were really parsed -- which in turn requires the shared
#: include root that supplies ``sic_dep.h``.
_API_HEADER = "#include <sic_dep.h>\nint sic_api(struct sic_dep *d{extra});\n"
_SOURCE = (
    '#include "sic_api.h"\nint sic_api(struct sic_dep *d{extra}) {{ return d->a; }}\n'
)


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform == "win32", reason="builds an ELF .so pair, linux/macos only"
)
@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc required")
class TestSharedIncludeRootIsSearched:
    """The real two-root fixture named in the report.

    ``sic_api.h`` lives in each side's *own* header root and includes
    ``sic_dep.h``, which exists only in the root passed as the shared
    ``--include``. Resolving the shared root away makes this header
    unparseable, so a green run is direct evidence the composition kept it.
    """

    @staticmethod
    def _build(root: Path, dep: Path, extra: str = "") -> tuple[Path, Path]:
        root.mkdir(parents=True, exist_ok=True)
        (root / "sic_api.h").write_text(
            _API_HEADER.format(extra=extra), encoding="utf-8"
        )
        src = root / "sic_api.c"
        src.write_text(_SOURCE.format(extra=extra), encoding="utf-8")
        so = root / "libsic.so"
        subprocess.run(
            [
                "gcc",
                f"-I{dep}",
                f"-I{root}",
                "-fPIC",
                "-shared",
                "-o",
                str(so),
                str(src),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return so, root / "sic_api.h"

    def test_compare_parses_with_only_the_shared_root_supplying_the_dep(
        self, tmp_path: Path
    ) -> None:
        dep = tmp_path / "shared"
        dep.mkdir()
        (dep / "sic_dep.h").write_text(_DEP_HEADER, encoding="utf-8")
        old_so, old_h = self._build(tmp_path / "old", dep)
        new_so, new_h = self._build(tmp_path / "new", dep, extra=", int n")

        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_so),
                str(new_so),
                "--include",
                str(dep),  # shared: the only place sic_dep.h exists
                # Side-specific include roots too: under replacement these
                # are what evicted the shared root above.
                "--include",
                f"old={old_h.parent}",
                "--include",
                f"new={new_h.parent}",
                "--header",
                f"old={old_h}",
                "--header",
                f"new={new_h}",
                "-o",
                "json=-",
            ],
        )
        # A parameter was added, which is an ABI break -- but only a run
        # that actually parsed both sides' headers can see it, and a header
        # is only parseable when the shared --include root that holds
        # ``sic_dep.h`` survived the two side-specific ones.
        assert result.exit_code == 4, result.output
        assert "sic_api" in result.output

    def test_dry_run_receipt_reports_each_side_effective_headers(
        self, tmp_path: Path
    ) -> None:
        dep = tmp_path / "shared"
        dep.mkdir()
        (dep / "sic_dep.h").write_text(_DEP_HEADER, encoding="utf-8")
        old_so, old_h = self._build(tmp_path / "old", dep)
        new_so, new_h = self._build(tmp_path / "new", dep)
        shared_h = tmp_path / "shared" / "sic_dep.h"

        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_so),
                str(new_so),
                "--dry-run",
                "--header",
                str(shared_h),
                "--header",
                f"old={old_h}",
                "--header",
                f"new={new_h}",
            ],
        )
        assert result.exit_code == 0, result.output
        # Both sides list their own header *and* the shared one -- the
        # receipt must describe what will really be parsed.
        assert "headers (old):" in result.output
        assert "headers (new):" in result.output
        for line in result.output.splitlines():
            if line.strip().startswith("headers (old):"):
                assert str(old_h) in line and str(shared_h) in line
            if line.strip().startswith("headers (new):"):
                assert str(new_h) in line and str(shared_h) in line
