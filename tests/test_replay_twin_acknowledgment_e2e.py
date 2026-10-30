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

"""An acknowledged header-only inline removal is one observation with its replay.

Through the real CLI on compiled libraries: ``clamp_it`` is a header-only inline in
v1 and gone in v2. The header tier and the L4 source replay both report the
removal under the same ``ChangeKind``, so one ``symbol:`` + ``change_kind:``
record covers both copies. The dedup pass drops the replay copy, which stays on
the ledger as ``deduplicated``. The run must complete and acknowledge the one
removal, not raise ``AmbiguousAcknowledgmentError`` as if two findings matched.

Before the fix the C spelling (both copies share the kind) made the matcher count
two observations: it raised and wrote no report. The C++ spelling names the header
copy by its mangled symbol, so the record matched the replay copy alone and never
raised; it is kept as the C++ completion check. Which copy carries the
acknowledgment is not part of the report JSON, so the routing itself is pinned by
``tests/test_replay_twin_acknowledgment.py``.

Linux only: the libraries are built with gcc and parsed with castxml and clang.
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
from abicheck.policy.acknowledgment import AmbiguousAcknowledgmentError

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="builds an ELF .so"),
    pytest.mark.skipif(
        any(shutil.which(t) is None for t in ("gcc", "g++", "castxml", "clang")),
        reason="needs gcc, g++, castxml and clang",
    ),
]

_HEADER_V1 = {
    "c": "#pragma once\ninline int clamp_it(int x) { return x < 0 ? 0 : x; }\n"
    "int keep(int x);\n",
    "c++": "#pragma once\nnamespace demo {\n"
    "inline int clamp_it(int x) { return x < 0 ? 0 : x; }\n}\nint keep(int x);\n",
}
_HEADER_V2 = "#pragma once\nint keep(int x);\n"
# The C++ symbol is qualified in the record and demangled by the matcher.
_ACK_SYMBOL = {"c": "clamp_it", "c++": "demo::clamp_it"}


def _side(root: Path, version: str, language: str, header: str) -> Path:
    side = root / version
    side.mkdir()
    (side / "widget.h").write_text(header, encoding="utf-8")
    ext = "c" if language == "c" else "cpp"
    source = side / f"widget.{ext}"
    source.write_text(
        '#include "widget.h"\nint keep(int x) { return x + 1; }\n', encoding="utf-8"
    )
    driver = "gcc" if language == "c" else "g++"
    subprocess.run(
        [
            driver,
            "-shared",
            "-fPIC",
            f"-I{side}",
            str(source),
            "-o",
            str(side / "libwidget.so"),
        ],
        check=True,
    )
    (side / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(side),
                    "arguments": [
                        driver,
                        f"-I{side}",
                        "-c",
                        source.name,
                        "-o",
                        "widget.o",
                    ],
                    "file": str(source),
                }
            ]
        ),
        encoding="utf-8",
    )
    return side


@pytest.mark.parametrize("language", ["c", "c++"])
def test_acknowledged_inline_removal_completes_with_its_replay_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
    old = _side(tmp_path, "v1", language, _HEADER_V1[language])
    new = _side(tmp_path, "v2", language, _HEADER_V2)
    ack = tmp_path / "ack.yml"
    ack.write_text(
        "version: 1\nacknowledgments:\n"
        f"  - symbol: {_ACK_SYMBOL[language]}\n"
        "    change_kind: inline_function_removed\n"
        "    reason: deliberate removal\n",
        encoding="utf-8",
    )
    args = [
        "compare",
        str(old / "libwidget.so"),
        str(new / "libwidget.so"),
        "--header",
        f"old={old / 'widget.h'}",
        "--header",
        f"new={new / 'widget.h'}",
        "--sources",
        f"old={old}",
        "--sources",
        f"new={new}",
        "--build-info",
        f"old={old / 'compile_commands.json'}",
        "--build-info",
        f"new={new / 'compile_commands.json'}",
        "--depth",
        "source",
        "--acknowledgments",
        str(ack),
        "-o",
        "json=report.json",
    ]

    result = CliRunner().invoke(main, args)

    assert not isinstance(result.exception, AmbiguousAcknowledgmentError), result.output
    # The acknowledged break still gates (breaks: gate is the default).
    assert result.exit_code == 2, result.output
    report = json.loads(Path("report.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "API_BREAK"
    removed = [c for c in report["changes"] if c["kind"] == "inline_function_removed"]
    assert len(removed) == 1, [c["symbol"] for c in removed]
    audit = report["disposition_audit"]
    # The replay copy is still on the ledger as deduplicated, and the record covers
    # one observation: the surviving removal, not the folded copy as well.
    assert audit["counts"]["deduplicated"] == 1, audit["counts"]
    assert audit["acknowledged_total"] == 1, audit["acknowledged_total"]
    [entry] = audit["acknowledgments"]
    assert entry["matched_count"] == 1, entry
