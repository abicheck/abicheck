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

"""ADR-085 Remaining item 3: non-type and template-template defaults on clang.

The clang walk now records the spelled default of a non-type or template-
template parameter as well as a type parameter's. A header-only change to any
of them is therefore ``TEMPLATE_PARAM_DEFAULT_CHANGED`` (``API_BREAK``), and an
unchanged header reads ``NO_CHANGE`` with no kind. Each case goes through the
public ``abicheck dump`` / ``abicheck compare`` workflow with the clang
frontend.

Marked ``integration``: it shells out to clang.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration

_KIND = "template_param_default_changed"

_NONTYPE = """#pragma once
namespace mylib {{
template <typename T, int N = {n}>
struct Buf {{
    T t[N];
}};
}}  // namespace mylib
"""

_ENUM = """#pragma once
namespace mylib {{
enum class Mode {{ A, B }};
template <typename T, Mode M = Mode::{m}>
struct Tag {{
    T t;
}};
}}  // namespace mylib
"""

_TEMPLATE_TEMPLATE = """#pragma once
namespace mylib {{
template <typename> struct Box2 {{}};
template <typename> struct Box3 {{}};
template <typename T, template <typename> class C = {c}>
struct Holder {{
    C<T> c;
}};
}}  // namespace mylib
"""

# (template, old header, new header): the default changes and nothing else.
_CHANGED = {
    "non_type_int": (
        _NONTYPE.format(n="4"),
        _NONTYPE.format(n="8"),
    ),
    "enum": (
        _ENUM.format(m="B"),
        _ENUM.format(m="A"),
    ),
    "template_template": (
        _TEMPLATE_TEMPLATE.format(c="Box2"),
        _TEMPLATE_TEMPLATE.format(c="Box3"),
    ),
}


def _require_clang() -> None:
    if shutil.which("clang++") is None:
        pytest.skip("clang++ not installed")


def _run_abicheck(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "abicheck", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _dump_clang(tmp: Path, header: Path, tag: str) -> Path:
    cfg = tmp / "clang.yml"
    cfg.write_text("compile:\n  frontend: clang\n  lang: c++\n")
    out = tmp / f"{tag}.json"
    result = _run_abicheck(
        "dump",
        "-H",
        str(header),
        "--config",
        str(cfg),
        "--depth",
        "headers",
        "-o",
        str(out),
    )
    assert result.returncode == 0, result.stderr
    return out


def _compare(old: Path, new: Path, tmp: Path) -> dict[str, Any]:
    out = tmp / "cmp.json"
    _run_abicheck("compare", str(old), str(new), "-o", f"json={out}")
    return json.loads(out.read_text())


def _kinds(report: dict[str, Any]) -> set[str]:
    return {c["kind"] for c in report.get("changes", [])}


@pytest.mark.parametrize("case", sorted(_CHANGED))
def test_header_only_default_change_is_api_break_on_clang(
    tmp_path: Path, case: str
) -> None:
    _require_clang()
    old_text, new_text = _CHANGED[case]
    old_h = tmp_path / "old.h"
    new_h = tmp_path / "new.h"
    old_h.write_text(old_text)
    new_h.write_text(new_text)

    report = _compare(
        _dump_clang(tmp_path, old_h, "old"),
        _dump_clang(tmp_path, new_h, "new"),
        tmp_path,
    )

    assert report["verdict"] == "API_BREAK"
    assert _kinds(report) == {_KIND}


def test_unchanged_non_type_and_template_template_defaults_are_no_change(
    tmp_path: Path,
) -> None:
    _require_clang()
    header = tmp_path / "same.h"
    header.write_text(_CHANGED["non_type_int"][0])
    same = _dump_clang(tmp_path, header, "old")
    again = _dump_clang(tmp_path, header, "new")

    report = _compare(same, again, tmp_path)

    assert report["verdict"] == "NO_CHANGE"
    assert _KIND not in _kinds(report)
