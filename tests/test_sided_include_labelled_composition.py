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
import warnings
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
#: Lives only under the side's own include root, never beside the API header,
#: so only the side-specific ``--include`` can resolve it.
_SIDE_HEADER = "typedef int sic_side_t;\n"
_API_HEADER = (
    "#include <side_only/sic_side.h>\n"
    "#include <sic_dep.h>\n"
    "int sic_api(struct sic_dep *d{extra});\n"
)
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
    """``root`` is the side's include root (``side_only/sic_side.h``); the
    public header sits apart in ``root/api``, whose own directory the header
    parser searches, so it cannot stand in for the side root."""
    (root / "api").mkdir(parents=True)
    (root / "side_only").mkdir()
    (root / "api" / "sic_api.h").write_text(
        _API_HEADER.format(extra=extra), encoding="utf-8"
    )
    (root / "side_only" / "sic_side.h").write_text(_SIDE_HEADER, encoding="utf-8")
    src = root / "sic_api.c"
    src.write_text(_SOURCE.format(extra=extra), encoding="utf-8")
    so = root / "libsic.so"
    subprocess.run(
        [
            "gcc",
            f"-I{nested}",
            f"-I{shared}",
            f"-I{root / 'api'}",
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
        f"old={old_root / 'api' / 'sic_api.h'}",
        "--header",
        f"new={new_root / 'api' / 'sic_api.h'}",
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


#: The header-scoped dump of a PE/Mach-O binary falls back to export-table
#: mode, with this warning, when its header parse fails; ELF fails the run
#: (known gap: a-failed-header-parse-on-pe-or-mach-o-falls-back-to-export-table-mode).
_EXPORT_TABLE_FALLBACK = "Falling back to export-table mode"


@pytest.mark.parametrize("dropped", ["shared", "side"])
@pytest.mark.parametrize("side_spelling", list(_SIDE_SPELLINGS))
def test_negative_control_without_a_root_the_headers_do_not_parse(
    fixture_tree: dict[str, Path], side_spelling: str, dropped: str
) -> None:
    """Drop the shared roots (what the PVXS run lost) or the side roots: the
    header parse cannot find ``sic_dep.h`` / ``side_only/sic_side.h``, so the
    header-only parameter change is never reported. The matrix above passing
    is therefore evidence both kinds of root reached castxml, not that the
    headers were never needed."""
    sided = _sided(fixture_tree, side_spelling, side_spelling)
    shared = [str(fixture_tree["shared"]), str(fixture_tree["nested"])]
    include_args = sided if dropped == "shared" else shared
    missing = "sic_dep.h" if dropped == "shared" else "sic_side.h"

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        exit_code, output = _run(fixture_tree, include_args)

    assert "func_params_changed" not in output
    if sys.platform == "darwin":
        # Mach-O: the failed parse is a warned export-table fallback, so the
        # run reads binary evidence only.
        assert any(_EXPORT_TABLE_FALLBACK in str(w.message) for w in caught), output
        assert '"new_evidence_depth": "binary"' in output
    else:
        assert exit_code not in (0, 4), output
        assert missing in output
