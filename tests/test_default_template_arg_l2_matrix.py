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
    for tool in tools:
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not installed")


def _run_abicheck(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "abicheck", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _write_config(tmp: Path, backend: str) -> Path:
    cfg = tmp / f"{backend}.yml"
    cfg.write_text(f"compile:\n  frontend: {backend}\n  lang: c++\n")
    return cfg


def _dump_header(tmp: Path, header: Path, backend: str, tag: str) -> Path:
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
    out = tmp / f"cmp_{backend}.json"
    _run_abicheck("compare", str(old), str(new), "-o", f"json={out}")
    report = json.loads(out.read_text())
    return report


def _kinds(report: dict[str, Any]) -> set[str]:
    return {c["kind"] for c in report.get("changes", [])}


@pytest.fixture()
def header_pair(tmp_path: Path) -> tuple[Path, Path]:
    old = tmp_path / "old.h"
    new = tmp_path / "new.h"
    old.write_text(_BOX_HEADER.format(default=_ONLY_OLD, api=_ONLY_API))
    new.write_text(_BOX_HEADER.format(default=_ONLY_NEW, api=_ONLY_API))
    return old, new


@pytest.fixture()
def public_api_pair(tmp_path: Path) -> tuple[Path, Path]:
    old = tmp_path / "old_api.h"
    new = tmp_path / "new_api.h"
    old.write_text(_BOX_HEADER.format(default=_ONLY_OLD, api=_API))
    new.write_text(_BOX_HEADER.format(default=_ONLY_NEW, api=_API))
    return old, new


@pytest.mark.parametrize("backend", BACKENDS)
def test_header_only_default_change_is_no_l2_change(
    tmp_path: Path, header_pair: tuple[Path, Path], backend: str
) -> None:
    """No public surface and no export depends on Box's default: no ABI effect."""
    _require_tools(backend)
    old_h, new_h = header_pair
    old = _dump_header(tmp_path, old_h, backend, "old")
    new = _dump_header(tmp_path, new_h, backend, "new")
    report = _compare_kinds(old, new, tmp_path, backend)
    assert report["verdict"] == "NO_CHANGE"
    assert DEFAULT_KIND not in _kinds(report)


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
    """The clang AST carries the default; abicheck's clang dumper does not record it.

    Pins the producer-side fact that makes a clang-only extraction possible
    later, so the header-capability note stays accurate.
    """
    _require_tools("clang")
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
