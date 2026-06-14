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

import pytest

from abicheck.dumper import (
    _castxml_dump,
    _castxml_failure_hint,
    _castxml_version_note,
    _is_toolchain_version_failure,
    _parse_castxml_version,
)
from abicheck.errors import SnapshotError

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

    def test_parses_llvm_version_spelling(self) -> None:
        # castxml builds that print "LLVM version" rather than "clang version".
        raw, clang = _parse_castxml_version("castxml version 0.6.8\nLLVM version 18.1.8\n")
        assert raw == "0.6.8"
        assert clang == (18, 1)

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

    def test_castxml_version_without_clang_line(self) -> None:
        # castxml version is reported but no parseable clang line — still nudge.
        with patch(
            "abicheck.dumper.subprocess.run",
            return_value=_completed(stdout="castxml version 0.4.5\n"),
        ):
            note = _castxml_version_note()
        assert "Detected castxml 0.4.5" in note
        assert ">= 18" in note

    def test_no_version_info_is_silent(self) -> None:
        with patch(
            "abicheck.dumper.subprocess.run",
            return_value=_completed(stdout="unrelated output\n"),
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


class TestProbeGating:
    """The `castxml --version` probe is only triggered by frontend-too-old
    signatures, and is wired end-to-end into the raised error."""

    def test_signature_classification(self) -> None:
        assert _is_toolchain_version_failure(_FLOATN_STDERR)
        assert _is_toolchain_version_failure(_ASSUME_STDERR)
        assert not _is_toolchain_version_failure("fatal error: missing.h: No such file")
        assert not _is_toolchain_version_failure("")

    def test_floatn_failure_probes_version_and_folds_note(self, tmp_path: Path) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):  # noqa: ANN001
            calls.append(list(cmd))
            if "--version" in cmd:
                return _completed(stdout="castxml version 0.5.1\nclang version 14.0.0\n")
            return _completed(returncode=1, stderr=_FLOATN_STDERR)

        with (
            patch("abicheck.dumper._castxml_available", return_value=True),
            patch("abicheck.dumper.subprocess.run", side_effect=fake_run),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "cache.xml"),
        ):
            header = tmp_path / "api.hpp"
            header.write_text("int f();\n", encoding="utf-8")
            with pytest.raises(RuntimeError) as exc:
                _castxml_dump([header], [])

        msg = str(exc.value)
        assert "newer castxml" in msg          # base sized-float hint
        assert "Detected castxml 0.5.1" in msg  # folded-in version note
        assert any("--version" in c for c in calls)  # probe happened

    def test_unrelated_failure_skips_version_probe(self, tmp_path: Path) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):  # noqa: ANN001
            calls.append(list(cmd))
            return _completed(returncode=1, stderr="fatal error: missing.h: No such file")

        with (
            patch("abicheck.dumper._castxml_available", return_value=True),
            patch("abicheck.dumper.subprocess.run", side_effect=fake_run),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "cache.xml"),
        ):
            header = tmp_path / "api.hpp"
            header.write_text("int f();\n", encoding="utf-8")
            with pytest.raises(RuntimeError):
                _castxml_dump([header], [])

        assert not any("--version" in c for c in calls)  # no needless probe


def _in_c_mode(cmd: list[str]) -> bool:
    """True if the castxml command was assembled for C (``-x c``) parsing."""
    return "-x" in cmd and cmd[cmd.index("-x") + 1] == "c"


def _write_min_xml(cmd: list[str]) -> None:
    """Write a minimal valid castxml document to the command's ``-o`` target."""
    out = Path(cmd[cmd.index("-o") + 1])
    out.write_text("<GCC_XML><Namespace/></GCC_XML>", encoding="utf-8")


class TestLangCFallsBackToCpp:
    """G16/A3: an explicit ``--lang c`` on a header that *genuinely requires* C++
    (a stray class/namespace/template) degrades to a C++ retry rather than
    hard-fail. A valid C header — including a guarded ``extern "C"`` shim — that
    fails in C mode is a real error and must NOT be masked by a C++ retry (Codex
    review). Fully mocked — no castxml."""

    def test_cpp_only_header_retries_in_cpp_and_succeeds(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        modes: list[bool] = []

        def fake_run(cmd, **kwargs):  # noqa: ANN001
            c_mode = _in_c_mode(cmd)
            modes.append(c_mode)
            if c_mode:
                return _completed(returncode=1, stderr="error: expected ';'")
            _write_min_xml(cmd)
            return _completed(returncode=0)

        # A genuine C++-only construct (namespace) that cannot parse as C.
        header = tmp_path / "api.h"
        header.write_text("namespace ns { int f(int); }\n", encoding="utf-8")
        with (
            patch("abicheck.dumper._castxml_available", return_value=True),
            patch("abicheck.dumper.subprocess.run", side_effect=fake_run),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "cache.xml"),
            caplog.at_level("WARNING"),
        ):
            root = _castxml_dump([header], [], compiler="cc", lang="c")

        assert root.tag == "GCC_XML"
        # First attempt was C mode (failed), second was C++ mode (succeeded).
        assert modes == [True, False]
        assert any("retrying in C++" in r.message for r in caplog.records)

    def test_guarded_extern_c_failure_is_not_masked(self, tmp_path: Path) -> None:
        # A valid C header whose only "C++" token is a guarded extern "C": a
        # C-mode failure here is real (e.g. a missing include under
        # #ifndef __cplusplus). It must surface, NOT be retried as C++ — which
        # would skip the C-only branch and fabricate a snapshot (Codex review).
        modes: list[bool] = []

        def fake_run(cmd, **kwargs):  # noqa: ANN001
            modes.append(_in_c_mode(cmd))
            return _completed(returncode=1, stderr="fatal error: 'cfg.h' file not found")

        header = tmp_path / "zlib.h"
        header.write_text(
            "#ifndef __cplusplus\n#include \"cfg.h\"\n#endif\n"
            '#ifdef __cplusplus\nextern "C" {\n#endif\nint f(void);\n'
            "#ifdef __cplusplus\n}\n#endif\n",
            encoding="utf-8",
        )
        with (
            patch("abicheck.dumper._castxml_available", return_value=True),
            patch("abicheck.dumper.subprocess.run", side_effect=fake_run),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "cache.xml"),
            pytest.raises(SnapshotError),
        ):
            _castxml_dump([header], [], compiler="cc", lang="c")

        assert modes == [True]  # only C mode ran; no C++ retry

    def test_both_modes_fail_surfaces_requested_c_error(self, tmp_path: Path) -> None:
        def fake_run(cmd, **kwargs):  # noqa: ANN001
            if "--version" in cmd:
                return _completed(stdout="castxml version 0.6.8\nclang version 18.1.8\n")
            return _completed(returncode=1, stderr="error: expected ';'")

        # A genuine C++-only construct triggers the retry; both modes fail here.
        header = tmp_path / "api.h"
        header.write_text("class Widget { int x; };\n", encoding="utf-8")
        with (
            patch("abicheck.dumper._castxml_available", return_value=True),
            patch("abicheck.dumper.subprocess.run", side_effect=fake_run),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "cache.xml"),
            pytest.raises(SnapshotError) as exc,
        ):
            _castxml_dump([header], [], compiler="cc", lang="c")

        # The C-mode hint (suggesting --lang c++) is what the user sees, since
        # that matches the mode they explicitly requested.
        assert "--lang" in str(exc.value)

    def test_pure_c_header_does_not_retry(self, tmp_path: Path) -> None:
        modes: list[bool] = []

        def fake_run(cmd, **kwargs):  # noqa: ANN001
            modes.append(_in_c_mode(cmd))
            return _completed(returncode=1, stderr="fatal error: missing.h: No such file")

        header = tmp_path / "api.h"
        header.write_text("int plain_c(void);\n", encoding="utf-8")
        with (
            patch("abicheck.dumper._castxml_available", return_value=True),
            patch("abicheck.dumper.subprocess.run", side_effect=fake_run),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "cache.xml"),
            pytest.raises(SnapshotError),
        ):
            _castxml_dump([header], [], compiler="cc", lang="c")

        # No C++ retry: a header with no C++ constructs failing in C mode is a
        # real error, not a language-mode mismatch.
        assert modes == [True]
