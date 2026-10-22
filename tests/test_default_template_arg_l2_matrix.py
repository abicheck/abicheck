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

"""G28 Phase 5: where a default template-argument change is caught at L2.

A change to a default template argument (``template <class T, class U = T>``
becoming ``U = int``) is observable at L2 in three shapes. This module pins
what each header backend (CastXML and direct clang) reports for each shape,
through the public ``abicheck dump`` / ``abicheck compare`` workflow:

* **instantiated** — the library exports the default-substituted
  instantiation (case87). Both backends attribute it as
  ``default_template_arg_changed`` at L2.
* **in a public signature** — the default-substituted type appears in a public
  function parameter. Both backends catch the break at L2 as a removed and an
  added mangled function, but neither attributes it to the default, so the
  finding carries no ``default_template_arg_changed`` label.
* **header-only** — the template is never named by the public surface and
  nothing is exported. No ABI effect exists to report, so L2 is ``NO_CHANGE``
  on both backends. CastXML emits no record for an uninstantiated class
  template at all; the clang AST does carry the template pattern and the
  default argument (``TemplateTypeParmDecl.defaultArg``), which abicheck's clang
  dumper does not record. That asymmetry is why no clang-only detector was
  added: it would return a BREAKING verdict castxml cannot reproduce.

See ``docs/contribute/plans/g28-castxml-clang-l2-parity-hardening.md`` (Phase 5)
and ``docs/contribute/known-gaps.md`` for the decision record.

Marked ``integration``: it shells out to real castxml, clang and g++.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="castxml/clang L2 matrix is ELF/Linux-scoped (see module docstring)",
    ),
]

BACKENDS = ("castxml", "clang")
DEFAULT_KIND = "default_template_arg_changed"

_BOX_HEADER = """#pragma once
namespace mylib {{
template <typename T, typename U = {default}>
struct Box {{
    T t;
    U u;
}};
{api}
}}  // namespace mylib
"""

# Header-only: nothing public names Box, so nothing is exported that depends on
# the default.
_ONLY_OLD = "T"
_ONLY_NEW = "int"
_ONLY_API = ""

# Public signature: take(const Box<char>&) mangles against the substituted type.
_API = "void take(const Box<char>& b);"

_CASE87 = (
    Path(__file__).resolve().parent.parent
    / "catalog"
    / "cases"
    / ("case87_default_template_arg_changed")
)


def _require_tools(*tools: str) -> None:
    """Skip the calling test unless every named executable is on PATH."""
    for tool in tools:
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not installed")


def _run_abicheck(*args: str) -> subprocess.CompletedProcess[str]:
    """Run ``python -m abicheck`` with *args* and capture its output."""
    return subprocess.run(
        [sys.executable, "-m", "abicheck", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _write_config(tmp: Path, backend: str) -> Path:
    """Write a ``.abicheck.yml`` that pins *backend* as the header frontend."""
    cfg = tmp / f"{backend}.yml"
    cfg.write_text(f"compile:\n  frontend: {backend}\n  lang: c++\n")
    return cfg


def _dump_header(tmp: Path, header: Path, backend: str, tag: str) -> Path:
    """Dump *header* alone at ``--depth headers`` and return the snapshot path."""
    out = tmp / f"{tag}_{backend}.json"
    result = _run_abicheck(
        "dump",
        "-H",
        str(header),
        "--config",
        str(_write_config(tmp, backend)),
        "--depth",
        "headers",
        "-o",
        str(out),
    )
    assert result.returncode == 0, result.stderr
    return out


def _compare_kinds(old: Path, new: Path, tmp: Path, backend: str) -> dict[str, Any]:
    """Compare two snapshots and return the parsed JSON report."""
    out = tmp / f"cmp_{backend}.json"
    _run_abicheck("compare", str(old), str(new), "-o", f"json={out}")
    report = json.loads(out.read_text())
    return report


def _kinds(report: dict[str, Any]) -> set[str]:
    """Return the set of change kinds in a parsed report."""
    return {c["kind"] for c in report.get("changes", [])}


@pytest.fixture()
def header_pair(tmp_path: Path) -> tuple[Path, Path]:
    """Old and new headers with a header-only default change."""
    old = tmp_path / "old.h"
    new = tmp_path / "new.h"
    old.write_text(_BOX_HEADER.format(default=_ONLY_OLD, api=_ONLY_API))
    new.write_text(_BOX_HEADER.format(default=_ONLY_NEW, api=_ONLY_API))
    return old, new


@pytest.fixture()
def public_api_pair(tmp_path: Path) -> tuple[Path, Path]:
    """Old and new headers where the default-substituted type is in a public signature."""
    old = tmp_path / "old_api.h"
    new = tmp_path / "new_api.h"
    old.write_text(_BOX_HEADER.format(default=_ONLY_OLD, api=_API))
    new.write_text(_BOX_HEADER.format(default=_ONLY_NEW, api=_API))
    return old, new


HEADER_KIND = "template_param_default_changed"
_CASTXML_GAP = "template-parameter defaults (castxml cannot record them)"


def test_header_only_default_change_is_api_break_on_clang(
    tmp_path: Path, header_pair: tuple[Path, Path]
) -> None:
    """ADR-085 D2: clang records the defaults, so the header-only change is API_BREAK.

    No export or public signature depends on Box's default, so it is not a
    BREAKING finding, and it is not the exported-instantiation kind.
    """
    _require_tools("clang")
    old_h, new_h = header_pair
    old = _dump_header(tmp_path, old_h, "clang", "old")
    new = _dump_header(tmp_path, new_h, "clang", "new")
    report = _compare_kinds(old, new, tmp_path, "clang")
    assert report["verdict"] == "API_BREAK"
    assert _kinds(report) == {HEADER_KIND}
    assert DEFAULT_KIND not in _kinds(report)


def test_header_only_default_change_is_a_castxml_assurance_gap(
    tmp_path: Path, header_pair: tuple[Path, Path]
) -> None:
    """ADR-085 D3: castxml cannot record the default, so the run says so.

    castxml's compare verdict stays NO_CHANGE (it cannot see the change), but
    the header scan finds the template default on both sides, so the analysis
    is partial with an explicit gap note, not a silent clean comparison.
    """
    _require_tools("castxml")
    old_h, new_h = header_pair
    old = _dump_header(tmp_path, old_h, "castxml", "old")
    new = _dump_header(tmp_path, new_h, "castxml", "new")
    report = _compare_kinds(old, new, tmp_path, "castxml")
    assert report["verdict"] == "NO_CHANGE"
    assert HEADER_KIND not in _kinds(report)
    assurance = report["analysis_assurance"]
    assert assurance["status"] == "partial"
    gap = [n for n in assurance["notes"] if n.startswith(_CASTXML_GAP)]
    assert len(gap) == 2  # one per side


@pytest.mark.integration
def test_clang_reports_and_castxml_names_the_same_header_change(
    tmp_path: Path, header_pair: tuple[Path, Path]
) -> None:
    """Cross-backend parity (ADR-085 test plan): the same header pair is not
    a silent clean on either backend. clang reports the kind; castxml reports
    the explicit gap instead."""
    _require_tools("castxml", "clang")
    old_h, new_h = header_pair
    clang = _compare_kinds(
        _dump_header(tmp_path, old_h, "clang", "old"),
        _dump_header(tmp_path, new_h, "clang", "new"),
        tmp_path,
        "clang",
    )
    castxml = _compare_kinds(
        _dump_header(tmp_path, old_h, "castxml", "old"),
        _dump_header(tmp_path, new_h, "castxml", "new"),
        tmp_path,
        "castxml",
    )
    assert HEADER_KIND in _kinds(clang)
    assert HEADER_KIND not in _kinds(castxml)
    assert castxml["analysis_assurance"]["status"] == "partial"
    assert not any(
        n.startswith(_CASTXML_GAP) for n in clang["analysis_assurance"]["notes"]
    )


def test_castxml_cannot_expose_uninstantiated_template_defaults(
    tmp_path: Path, header_pair: tuple[Path, Path]
) -> None:
    """CastXML emits no record for an uninstantiated class template.

    The snapshot therefore has nothing to hang a default argument on. This is
    the castxml side of the capability gap recorded in
    ``docs/reference/header-backend-capabilities.md``.
    """
    _require_tools("castxml", "g++")
    old_h, _ = header_pair
    # castxml refuses a .h as C++ without a language flag, so give it a .hpp copy.
    probe = tmp_path / "probe.hpp"
    probe.write_text(old_h.read_text())
    xml = tmp_path / "probe.xml"
    subprocess.run(
        [
            "castxml",
            "--castxml-cc-gnu",
            "g++",
            "--castxml-output=1",
            "-std=c++17",
            "-o",
            str(xml),
            str(probe),
        ],
        check=True,
        capture_output=True,
    )
    # Assert on CastXML's own output, not the abicheck snapshot, so the claim is
    # about the producer. The snapshot check below pins the L2 result separately.
    assert 'name="Box"' not in xml.read_text()
    snap = _dump_header(tmp_path, old_h, "castxml", "probe").read_text()
    assert '"name": "Box"' not in snap


def test_clang_ast_exposes_template_default_argument(
    tmp_path: Path, header_pair: tuple[Path, Path]
) -> None:
    """The clang AST carries the default, and abicheck's clang dumper records it.

    Pins the producer-side fact the clang backend's recording (ADR-085 D1) is
    built on, so a change in the AST shape fails here first.
    """
    _require_tools("clang++")
    old_h, _ = header_pair
    ast = subprocess.run(
        [
            "clang++",
            "-std=c++17",
            "-fsyntax-only",
            "-x",
            "c++",
            "-Xclang",
            "-ast-dump=json",
            str(old_h),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    tree = json.loads(ast)

    def _params(node: Any) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        if isinstance(node, dict):
            if node.get("kind") == "TemplateTypeParmDecl":
                found.append(node)
            for value in node.values():
                found.extend(_params(value))
        elif isinstance(node, list):
            for item in node:
                found.extend(_params(item))
        return found

    u_param = next(p for p in _params(tree) if p.get("name") == "U")
    assert "defaultArg" in u_param


@pytest.mark.parametrize("backend", BACKENDS)
def test_default_substituted_into_public_signature_breaks_at_l2(
    tmp_path: Path, public_api_pair: tuple[Path, Path], backend: str
) -> None:
    """The break is caught at L2 on both backends, without the default label."""
    _require_tools(backend)
    old_h, new_h = public_api_pair
    old = _dump_header(tmp_path, old_h, backend, "old")
    new = _dump_header(tmp_path, new_h, backend, "new")
    report = _compare_kinds(old, new, tmp_path, backend)
    kinds = _kinds(report)
    assert report["verdict"] == "BREAKING"
    assert {"func_removed", "func_added"} <= kinds
    # Known attribution gap (Phase 5): the mangled-signature change is caught,
    # but the default-argument cause is not named at L2 for this shape.
    assert DEFAULT_KIND not in kinds
    # ADR-085 shape C: the reported signature change already is the break, so
    # the header-level default finding for Box is not counted a second time.
    assert HEADER_KIND not in kinds


@pytest.mark.parametrize("backend", BACKENDS)
def test_instantiated_default_change_is_attributed_at_l2(
    tmp_path: Path, backend: str
) -> None:
    """case87: the exported default-substituted instantiation is attributed at L2."""
    _require_tools("g++", backend)
    case_dir = tmp_path / "case87"
    shutil.copytree(_CASE87, case_dir, ignore=shutil.ignore_patterns("*.txt", "*.md"))
    libs: dict[str, Path] = {}
    for version in ("v1", "v2"):
        so = tmp_path / f"lib_{version}.so"
        subprocess.run(
            [
                "g++",
                "-shared",
                "-fPIC",
                "-g",
                "-std=c++17",
                f"-I{case_dir}",
                str(case_dir / f"{version}.cpp"),
                "-o",
                str(so),
            ],
            check=True,
            capture_output=True,
        )
        libs[version] = so
    dumps: dict[str, Path] = {}
    for version in ("v1", "v2"):
        out = tmp_path / f"{version}_{backend}.json"
        result = _run_abicheck(
            "dump",
            str(libs[version]),
            "-H",
            str(case_dir / f"{version}.h"),
            "--config",
            str(_write_config(tmp_path, backend)),
            "--depth",
            "headers",
            "-o",
            str(out),
        )
        assert result.returncode == 0, result.stderr
        dumps[version] = out
    report = _compare_kinds(dumps["v1"], dumps["v2"], tmp_path, backend)
    assert report["verdict"] == "BREAKING"
    assert DEFAULT_KIND in _kinds(report)
    # ADR-085 D2 shape-A suppression: the exported instantiation already carries
    # the change, so the header-level kind is not counted a second time.
    assert HEADER_KIND not in _kinds(report)
