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

"""Header-scoped source-mode toolchain robustness (plan G16).

Header-scoped scans drive an internal clang frontend (via castxml) while
emulating the host GCC. In the real-world scan campaign these aborted before any
ABI comparison for a small family of host-toolchain parse failures — always the
same three signatures, never an abicheck logic bug:

* glibc sized-float keywords ``_Float32``/``_Float64``/``_Float128`` the bundled
  clang frontend rejects (the dominant case);
* the GCC 13+ libstdc++ ``__assume__`` attribute;
* explicit ``--lang c`` on headers that need C++ or guard ``extern "C"``.

The durable fix for the first two is a castxml built against a newer Clang (the
``-D_FloatN`` shim was rejected — it rewrites glibc's own ``typedef float
_Float32;`` fallback into ``typedef float float;``). So abicheck diagnoses
precisely: it classifies the signature and, on a real failure, probes
``castxml --version`` and folds in an upgrade recommendation. These tests pin the
pure parser, the version note, and the per-signature remediation text — fully
mocked, so they run in the default fast lane with no castxml present.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from abicheck.dumper import (
    _castxml_failure_hint,
    _castxml_version_note,
    _parse_castxml_version,
)

_FLOATN_STDERR = (
    "/usr/include/bits/floatn-common.h:214:14: error: unknown type name '_Float32'"
)
_ASSUME_STDERR = (
    "/usr/include/c++/13/bits/stl_algobase.h:2070: error: '__assume__' was not declared"
)


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    result: subprocess.CompletedProcess = MagicMock(spec=subprocess.CompletedProcess)
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


class TestParseCastxmlVersion:
    def test_parses_castxml_and_clang(self) -> None:
        out = "castxml version 0.6.8\nclang version 17.0.6\n"
        raw, clang = _parse_castxml_version(out)
        assert raw == "0.6.8"
        assert clang == (17, 0)

    def test_clang_major_only(self) -> None:
        raw, clang = _parse_castxml_version("castxml version 0.5.1\nclang version 14\n")
        assert raw == "0.5.1"
        assert clang == (14, 0)

    def test_missing_fields_are_none(self) -> None:
        assert _parse_castxml_version("") == (None, None)
        assert _parse_castxml_version("some unrelated output")[1] is None


class TestVersionNote:
    def test_old_clang_recommends_upgrade(self) -> None:
        with patch(
            "abicheck.dumper.subprocess.run",
            return_value=_completed(stdout="castxml version 0.5.1\nclang version 14.0.0\n"),
        ):
            note = _castxml_version_note()
        assert "clang 14" in note
        assert ">= 18" in note
        assert "upgrade" in note.lower()

    def test_new_clang_gives_no_note(self) -> None:
        with patch(
            "abicheck.dumper.subprocess.run",
            return_value=_completed(stdout="castxml version 0.6.8\nclang version 18.1.8\n"),
        ):
            assert _castxml_version_note() == ""

    def test_probe_failure_is_silent(self) -> None:
        with patch("abicheck.dumper.subprocess.run", side_effect=OSError("not found")):
            assert _castxml_version_note() == ""


class TestFailureHint:
    def test_floatn_hint_points_at_newer_castxml(self) -> None:
        hint = _castxml_failure_hint(_FLOATN_STDERR, force_cpp=True, headers=[])
        assert "_Float" in hint
        assert "newer castxml" in hint
        # no brittle -D shim is advertised any more
        assert "-D_Float" not in hint

    def test_floatn_hint_includes_version_note(self) -> None:
        hint = _castxml_failure_hint(
            _FLOATN_STDERR, force_cpp=True, headers=[],
            version_note=" Detected castxml 0.5.1 (clang 14.0); upgrade.",
        )
        assert "Detected castxml 0.5.1" in hint

    def test_assume_attribute_hint(self) -> None:
        hint = _castxml_failure_hint(_ASSUME_STDERR, force_cpp=True, headers=[])
        assert "__assume__" in hint
        assert "libstdc++" in hint

    def test_lang_c_on_cpp_headers_hint(self, tmp_path: Path) -> None:
        header = tmp_path / "api.h"
        header.write_text("namespace ns { class C {}; }\n", encoding="utf-8")
        hint = _castxml_failure_hint(
            "error: expected ';'", force_cpp=False, headers=[header]
        )
        assert "--lang" in hint

    def test_no_hint_for_unknown_failure(self, tmp_path: Path) -> None:
        header = tmp_path / "api.h"
        header.write_text("int f(void);\n", encoding="utf-8")
        hint = _castxml_failure_hint(
            "fatal error: missing.h: No such file", force_cpp=False, headers=[header]
        )
        assert hint == ""
