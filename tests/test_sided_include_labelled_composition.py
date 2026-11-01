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

"""A *labelled* side-scoped ``--include`` keeps the unlabelled shared roots.

Known gap "A labelled side-scoped ``--include`` appeared to suppress
unlabelled global include roots" (PVXS): ``--include old:public=OLD_INC
--include new:public=NEW_INC --include EPICS_INC --include
EPICS_INC/os/Linux ...`` failed with ``'epicsTime.h' file not found``.
The labelled form (ADR-050 D1, ``SidedIncludePathParam``) is a separate
parser from the unlabelled ``old=``/``new=`` one, so the bug class
``config.sided_shared_input_dropped``'s existing castxml fixture
(``tests/test_sided_include_composition.py``, unlabelled per-side roots
only) did not reach it.

This drives each per-side spelling (unlabelled, labelled) crossed with
every shared spelling (bare, ``both=``, ``both:LABEL=``),
through the real CLI and castxml, on the PVXS shape: each side's public
header includes a dependency that lives only in a shared root, and that
dependency includes a second one living only in a *nested* shared root.

Oracle: the compiler-visible fact that NEW's ``sic_api`` gained a
parameter with an identical C symbol name. Only a run that parsed both
sides' headers -- which needs both shared roots on castxml's search path
-- can report it as ``func_params_changed``. The negative control drops
the shared roots and must lose that finding, so the assertion cannot pass
on a run that ignored the headers.
"""

from __future__ import annotations

import itertools
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from abicheck.cli import main

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform == "win32", reason="builds an ELF .so pair, linux/macos only"
    ),
    pytest.mark.skipif(
        not (shutil.which("gcc") and shutil.which("castxml")),
        reason="needs gcc and castxml",
    ),
]

#: Lives only in ``shared/os/linux`` -- a nested shared root, as PVXS's
#: ``EPICS_INC/os/Linux``.
_OS_HEADER = "typedef long sic_os_t;\n"
#: Lives only in ``shared`` and itself needs the nested root.
_DEP_HEADER = "#include <sic_os.h>\nstruct sic_dep { int a; sic_os_t t; };\n"
_API_HEADER = "#include <sic_dep.h>\nint sic_api(struct sic_dep *d{extra});\n"
_SOURCE = (
    '#include "sic_api.h"\nint sic_api(struct sic_dep *d{extra}) {{ return d->a; }}\n'
)

#: How one side names its own public root.
_SIDE_SPELLINGS = {
    "unlabelled": "{side}={path}",
    "labelled": "{side}:public={path}",
}
#: How the two shared roots are named for both sides.
_SHARED_SPELLINGS = {
    "bare": "{path}",
    "both_prefixed": "both={path}",
    "both_labelled": "both:support={path}",
}


def _shared_roots(tmp: Path) -> tuple[Path, Path]:
    shared = tmp / "shared"
    nested = shared / "os" / "linux"
    nested.mkdir(parents=True)
    (nested / "sic_os.h").write_text(_OS_HEADER, encoding="utf-8")
    (shared / "sic_dep.h").write_text(_DEP_HEADER, encoding="utf-8")
    return shared, nested


def _build(root: Path, shared: Path, nested: Path, extra: str = "") -> Path:
    root.mkdir(parents=True)
    (root / "sic_api.h").write_text(_API_HEADER.format(extra=extra), encoding="utf-8")
    src = root / "sic_api.c"
    src.write_text(_SOURCE.format(extra=extra), encoding="utf-8")
    so = root / "libsic.so"
    subprocess.run(
        [
            "gcc",
            f"-I{nested}",
            f"-I{shared}",
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
    return so


@pytest.fixture(scope="module")
def fixture_tree(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    tmp = tmp_path_factory.mktemp("labelled_include")
    shared, nested = _shared_roots(tmp)
    return {
        "shared": shared,
        "nested": nested,
        "old_so": _build(tmp / "old", shared, nested),
        "new_so": _build(tmp / "new", shared, nested, extra=", int n"),
    }


def _invoke(tree: dict[str, Path], include_args: list[str]) -> Result:
    old_root = tree["old_so"].parent
    new_root = tree["new_so"].parent
    argv = [
        "compare",
        str(tree["old_so"]),
        str(tree["new_so"]),
        "--header",
        f"old={old_root / 'sic_api.h'}",
        "--header",
        f"new={new_root / 'sic_api.h'}",
        "-o",
        "json=-",
    ]
    for value in include_args:
        argv += ["--include", value]
    return CliRunner().invoke(main, argv)


def _run(tree: dict[str, Path], include_args: list[str]) -> tuple[int, str]:
    """Exit code and the combined stdout+stderr text, for diagnostics."""
    result = _invoke(tree, include_args)
    return result.exit_code, result.output


def _compare(tree: dict[str, Path], include_args: list[str]) -> tuple[int, dict]:
    """Exit code and the JSON report, read from stdout alone."""
    result = _invoke(tree, include_args)
    assert result.stdout.strip(), result.output
    return result.exit_code, json.loads(result.stdout)


def _changed_kinds(report: dict, name: str) -> set[str]:
    return {
        c["kind"] for c in report.get("changes", []) if name in str(c.get("symbol", ""))
    }


def _sided(tree: dict[str, Path], old_spelling: str, new_spelling: str) -> list[str]:
    return [
        _SIDE_SPELLINGS[old_spelling].format(side="old", path=tree["old_so"].parent),
        _SIDE_SPELLINGS[new_spelling].format(side="new", path=tree["new_so"].parent),
    ]


@pytest.mark.parametrize(
    ("side_spelling", "shared_spelling"),
    list(itertools.product(_SIDE_SPELLINGS, _SHARED_SPELLINGS)),
)
@pytest.mark.parametrize(
    "shared_first", [True, False], ids=["shared_first", "sided_first"]
)
def test_shared_roots_survive_every_side_spelling(
    fixture_tree: dict[str, Path],
    side_spelling: str,
    shared_spelling: str,
    shared_first: bool,
) -> None:
    sided = _sided(fixture_tree, side_spelling, side_spelling)
    shared = [
        _SHARED_SPELLINGS[shared_spelling].format(path=fixture_tree[key])
        for key in ("shared", "nested")
    ]
    exit_code, report = _compare(
        fixture_tree, [*shared, *sided] if shared_first else [*sided, *shared]
    )

    assert "func_params_changed" in _changed_kinds(report, "sic_api"), report.get(
        "changes"
    )
    assert exit_code == 4
    assert report["analysis_assurance"]["header_context_status"] == "clean"


@pytest.mark.parametrize(
    ("old_spelling", "new_spelling"),
    [("labelled", "unlabelled"), ("unlabelled", "labelled")],
)
def test_asymmetric_side_labels_are_refused_as_not_comparable(
    fixture_tree: dict[str, Path], old_spelling: str, new_spelling: str
) -> None:
    """A label is part of the extraction contract (ADR-050 D1: it names the
    ``-I`` slot in ``profile_fingerprint``), so labelling one side's root and
    not the other's declares two different contracts. That is refused loudly
    (exit 16, ``include_sequence`` named), never a header-less clean run --
    and the shared roots are not what differs: both sides still parse."""
    shared = [str(fixture_tree["shared"]), str(fixture_tree["nested"])]
    exit_code, output = _run(
        fixture_tree, [*shared, *_sided(fixture_tree, old_spelling, new_spelling)]
    )

    assert exit_code == 16, output
    assert "not comparable" in output
    assert "include_sequence" in output
    assert "sic_dep.h" not in output  # the shared roots were searched


@pytest.mark.parametrize("side_spelling", list(_SIDE_SPELLINGS))
def test_negative_control_without_shared_roots_fails_on_the_dependency(
    fixture_tree: dict[str, Path], side_spelling: str
) -> None:
    """Same per-side roots, no shared roots: the header parse fails on the
    dependency, exactly what the PVXS run saw. The matrix above passing is
    therefore evidence the shared roots reached castxml, not that the
    headers were never needed."""
    exit_code, output = _run(
        fixture_tree, _sided(fixture_tree, side_spelling, side_spelling)
    )

    assert exit_code not in (0, 4), output
    assert "sic_dep.h" in output
    assert "func_params_changed" not in output
