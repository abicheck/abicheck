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

import os
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
from abicheck.errors import HeaderToolchainError, SnapshotError

_FLOATN_STDERR = (
    "/usr/include/bits/floatn-common.h:214:14: error: unknown type name '_Float32'"
)
_ASSUME_STDERR = (
    "/usr/include/c++/13/bits/stl_algobase.h:2070: error: '__assume__' was not declared"
)


def _completed(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess:
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
        raw, clang = _parse_castxml_version(
            "castxml version 0.6.8\nLLVM version 18.1.8\n"
        )
        assert raw == "0.6.8"
        assert clang == (18, 1)

    def test_missing_fields_are_none(self) -> None:
        assert _parse_castxml_version("") == (None, None)
        assert _parse_castxml_version("some unrelated output")[1] is None


class TestVersionNote:
    def test_probes_the_selected_castxml_path(self) -> None:
        with patch(
            "abicheck.dumper.deadline.run_bounded",
            return_value=_completed(stdout="castxml version 0.6.8\n"),
        ) as run:
            _castxml_version_note("/selected/wrapper/castxml")
        assert run.call_args.args[0] == ["/selected/wrapper/castxml", "--version"]

    def test_old_clang_recommends_upgrade(self) -> None:
        with patch(
            "abicheck.dumper.deadline.run_bounded",
            return_value=_completed(
                stdout="castxml version 0.5.1\nclang version 14.0.0\n"
            ),
        ):
            note = _castxml_version_note()
        assert "clang 14" in note
        assert ">= 18" in note
        assert "upgrade" in note.lower()

    def test_new_clang_gives_no_note(self) -> None:
        with patch(
            "abicheck.dumper.deadline.run_bounded",
            return_value=_completed(
                stdout="castxml version 0.6.8\nclang version 18.1.8\n"
            ),
        ):
            assert _castxml_version_note() == ""

    def test_castxml_version_without_clang_line(self) -> None:
        # castxml version is reported but no parseable clang line — still nudge.
        with patch(
            "abicheck.dumper.deadline.run_bounded",
            return_value=_completed(stdout="castxml version 0.4.5\n"),
        ):
            note = _castxml_version_note()
        assert "Detected castxml 0.4.5" in note
        assert ">= 18" in note

    def test_no_version_info_is_silent(self) -> None:
        with patch(
            "abicheck.dumper.deadline.run_bounded",
            return_value=_completed(stdout="unrelated output\n"),
        ):
            assert _castxml_version_note() == ""

    def test_probe_failure_is_silent(self) -> None:
        with patch(
            "abicheck.dumper.deadline.run_bounded", side_effect=OSError("not found")
        ):
            assert _castxml_version_note() == ""

    def test_probe_deadline_exceeded_propagates(self) -> None:
        # Second-round Codex review (PR #591): a DeadlineExceeded here is
        # NOT an ordinary probe failure (missing tool, etc.) — this probe
        # sits on the authoritative L2 castxml path. Silently degrading it
        # to "" (as an earlier fix did) let a budget overflow during the
        # probe masquerade as a normal HeaderToolchainError/SnapshotError
        # (CLI exit 1) instead of the documented budget-overflow exit 5, so
        # it must propagate uncaught like the castxml/clang subprocess
        # calls around it.
        # Round-3 note: DeadlineExceeded propagates only when the *outer scan*
        # deadline (not this probe's own 15s local cap) is what's binding —
        # an active deadline_scope tighter than 15s makes that the case here
        # (Codex review, PR #591, round 3).
        from abicheck import deadline

        with (
            patch(
                "abicheck.dumper.deadline.run_bounded",
                side_effect=deadline.DeadlineExceeded(-1.0),
            ),
            deadline.deadline_scope(5.0),
            pytest.raises(deadline.DeadlineExceeded),
        ):
            _castxml_version_note()

    def test_probe_local_cap_hit_with_generous_scan_budget_is_silent(self) -> None:
        # Codex review (PR #591), round 3: hitting this probe's OWN 15s cap
        # is an ordinary probe failure -- even with an active outer --budget,
        # as long as that outer budget still had *more* than 15s left when
        # the probe started (so the local cap, not the scan deadline, was
        # what actually bound the nested scope).
        from abicheck import deadline

        with (
            patch(
                "abicheck.dumper.deadline.run_bounded",
                side_effect=deadline.DeadlineExceeded(-1.0),
            ),
            deadline.deadline_scope(1800.0),  # generous 30-minute --budget
        ):
            assert _castxml_version_note() == ""

    def test_probe_bounded_by_local_cap_not_full_scan_budget(self) -> None:
        # Codex review (PR #591), round 3: deadline.run_bounded() honors an
        # active outer deadline verbatim (not min(timeout, left)), so a bare
        # timeout=15 alone did nothing once a generous --budget was active --
        # a hung `castxml --version` could consume the whole remaining scan
        # budget instead of this probe's own 15s cap.
        from abicheck import deadline

        seen_remaining: list[float | None] = []

        def fake_run(*_a, **_k):
            seen_remaining.append(deadline.remaining())
            return _completed(stdout="unrelated output\n")

        with (
            patch("abicheck.dumper.deadline.run_bounded", side_effect=fake_run),
            deadline.deadline_scope(1800.0),  # generous 30-minute --budget
        ):
            _castxml_version_note()

        assert seen_remaining
        assert seen_remaining[0] is not None and seen_remaining[0] <= 15.5

    def test_probe_classified_as_local_cap_at_entry_still_propagates_if_outer_expires_by_return(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Codex review (PR #591, round 3): run_bounded's own escalation
        # (SIGTERM -> grace -> SIGKILL, plus a fixed 5s pipe-drain) can push
        # real elapsed time past what scan_remaining showed when this probe
        # decided whether the local 15s cap or the outer scan deadline was
        # binding. A probe correctly classified "local-cap-only" at entry
        # (outer had *just over* 15s left) must still propagate if the
        # outer deadline is exhausted by the time the except clause runs --
        # trusting a snapshot taken before the call would silently swallow
        # a genuine budget overflow as an ordinary probe failure.
        from abicheck import deadline

        clock = {"t": 1000.0}
        monkeypatch.setattr(deadline.time, "monotonic", lambda: clock["t"])

        def fake_run(*_a, **_k):
            clock["t"] += 20.0  # simulate run_bounded's real escalation cost
            raise deadline.DeadlineExceeded(-1.0)

        with (
            patch("abicheck.dumper.deadline.run_bounded", side_effect=fake_run),
            deadline.deadline_scope(15.5),  # just over the 15s local cap
            pytest.raises(deadline.DeadlineExceeded),
        ):
            _castxml_version_note()


class TestFailureHint:
    def test_floatn_hint_points_at_newer_castxml(self) -> None:
        hint = _castxml_failure_hint(_FLOATN_STDERR, force_cpp=True, headers=[])
        assert "_Float" in hint
        assert "newer castxml" in hint
        # no brittle -D shim is advertised any more
        assert "-D_Float" not in hint

    def test_floatn_hint_includes_version_note(self) -> None:
        hint = _castxml_failure_hint(
            _FLOATN_STDERR,
            force_cpp=True,
            headers=[],
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
                return _completed(
                    stdout="castxml version 0.5.1\nclang version 14.0.0\n"
                )
            return _completed(returncode=1, stderr=_FLOATN_STDERR)

        with (
            patch("abicheck.dumper._resolve_selected_tool", return_value="castxml"),
            # Both the main castxml dump and the `castxml --version` probe
            # (_castxml_version_note) now go through deadline.run_bounded
            # (Codex review, PR #591) — patched to the same fake so either
            # call routes here.
            patch("abicheck.dumper.deadline.run_bounded", side_effect=fake_run),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "cache.xml"),
        ):
            header = tmp_path / "api.hpp"
            header.write_text("int f();\n", encoding="utf-8")
            with pytest.raises(RuntimeError) as exc:
                _castxml_dump([header], [])

        msg = str(exc.value)
        assert "newer castxml" in msg  # base sized-float hint
        assert "Detected castxml 0.5.1" in msg  # folded-in version note
        assert any("--version" in c for c in calls)  # probe happened

    def test_version_probe_deadline_exceeded_propagates_end_to_end(
        self, tmp_path: Path
    ) -> None:
        # Second-round Codex review (PR #591): when the scan budget expires
        # during the `--version` probe (triggered by a frontend-too-old
        # castxml failure), the DeadlineExceeded must escape _castxml_dump
        # uncaught -- not get folded into a HeaderToolchainError/SnapshotError
        # (CLI exit 1) the way an earlier fix incorrectly did.
        from abicheck import deadline

        def fake_run(cmd, **kwargs):  # noqa: ANN001
            if "--version" in cmd:
                raise deadline.DeadlineExceeded(-1.0)
            return _completed(returncode=1, stderr=_FLOATN_STDERR)

        with (
            patch("abicheck.dumper._resolve_selected_tool", return_value="castxml"),
            patch("abicheck.dumper.deadline.run_bounded", side_effect=fake_run),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "cache.xml"),
            # Round-3 note: propagation requires the *outer scan* deadline to
            # be the binding constraint, not just this probe's own 15s local
            # cap (Codex review, PR #591, round 3).
            deadline.deadline_scope(5.0),
            pytest.raises(deadline.DeadlineExceeded),
        ):
            header = tmp_path / "api.hpp"
            header.write_text("int f();\n", encoding="utf-8")
            _castxml_dump([header], [])

    def test_unrelated_failure_skips_version_probe(self, tmp_path: Path) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):  # noqa: ANN001
            calls.append(list(cmd))
            return _completed(
                returncode=1, stderr="fatal error: missing.h: No such file"
            )

        with (
            patch("abicheck.dumper._resolve_selected_tool", return_value="castxml"),
            patch("abicheck.dumper.deadline.run_bounded", side_effect=fake_run),
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
            patch("abicheck.dumper._resolve_selected_tool", return_value="castxml"),
            patch("abicheck.dumper.deadline.run_bounded", side_effect=fake_run),
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
            return _completed(
                returncode=1, stderr="fatal error: 'cfg.h' file not found"
            )

        header = tmp_path / "zlib.h"
        header.write_text(
            '#ifndef __cplusplus\n#include "cfg.h"\n#endif\n'
            '#ifdef __cplusplus\nextern "C" {\n#endif\nint f(void);\n'
            "#ifdef __cplusplus\n}\n#endif\n",
            encoding="utf-8",
        )
        with (
            patch("abicheck.dumper._resolve_selected_tool", return_value="castxml"),
            patch("abicheck.dumper.deadline.run_bounded", side_effect=fake_run),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "cache.xml"),
            pytest.raises(SnapshotError) as exc,
        ):
            _castxml_dump([header], [], compiler="cc", lang="c")

        assert modes == [True]  # only C mode ran; no C++ retry
        # The real failure (a missing C-only include) is an ordinary header/
        # input problem, not a language-mode mismatch: it must not be
        # misclassified as HeaderToolchainError with a "--lang c++" hint that
        # wouldn't even fix it — _castxml_failure_hint's own C++-detection
        # here must use _CPP_ONLY_PATTERNS (excluding the guarded extern "C"),
        # the same predicate the retry gate above already uses (Codex review).
        assert not isinstance(exc.value, HeaderToolchainError)
        assert "--lang" not in str(exc.value)
        assert "cfg.h" in str(exc.value)

    def test_both_modes_fail_surfaces_requested_c_error(self, tmp_path: Path) -> None:
        def fake_run(cmd, **kwargs):  # noqa: ANN001
            if "--version" in cmd:
                return _completed(
                    stdout="castxml version 0.6.8\nclang version 18.1.8\n"
                )
            return _completed(returncode=1, stderr="error: expected ';'")

        # A genuine C++-only construct triggers the retry; both modes fail here.
        header = tmp_path / "api.h"
        header.write_text("class Widget { int x; };\n", encoding="utf-8")
        with (
            patch("abicheck.dumper._resolve_selected_tool", return_value="castxml"),
            patch("abicheck.dumper.deadline.run_bounded", side_effect=fake_run),
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
            return _completed(
                returncode=1, stderr="fatal error: missing.h: No such file"
            )

        header = tmp_path / "api.h"
        header.write_text("int plain_c(void);\n", encoding="utf-8")
        with (
            patch("abicheck.dumper._resolve_selected_tool", return_value="castxml"),
            patch("abicheck.dumper.deadline.run_bounded", side_effect=fake_run),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "cache.xml"),
            pytest.raises(SnapshotError),
        ):
            _castxml_dump([header], [], compiler="cc", lang="c")

        # No C++ retry: a header with no C++ constructs failing in C mode is a
        # real error, not a language-mode mismatch.
        assert modes == [True]


class TestHeaderToolchainErrorClass:
    """G16: a recognised host-toolchain signature raises the dedicated
    ``HeaderToolchainError`` (still an ``except SnapshotError``-catchable
    subclass) so a caller can branch on "this failure carries an actionable
    remediation"; an unrecognised castxml failure stays a plain
    ``SnapshotError``."""

    def test_known_signature_raises_header_toolchain_error(
        self, tmp_path: Path
    ) -> None:
        def fake_run(cmd, **kwargs):  # noqa: ANN001
            return _completed(returncode=1, stderr=_FLOATN_STDERR)

        header = tmp_path / "api.h"
        header.write_text("int f(void);\n", encoding="utf-8")
        with (
            patch("abicheck.dumper._resolve_selected_tool", return_value="castxml"),
            patch("abicheck.dumper.deadline.run_bounded", side_effect=fake_run),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "cache.xml"),
            pytest.raises(HeaderToolchainError) as exc,
        ):
            _castxml_dump([header], [], compiler="cc")
        # It is still catchable as the base SnapshotError (back-compat).
        assert isinstance(exc.value, SnapshotError)
        assert "_Float32" in str(exc.value) or "sized-float" in str(exc.value)

    def test_unrecognised_failure_stays_plain_snapshot_error(
        self, tmp_path: Path
    ) -> None:
        def fake_run(cmd, **kwargs):  # noqa: ANN001
            return _completed(returncode=1, stderr="internal compiler error: segfault")

        header = tmp_path / "api.h"
        header.write_text("int f(void);\n", encoding="utf-8")
        with (
            patch("abicheck.dumper._resolve_selected_tool", return_value="castxml"),
            patch("abicheck.dumper.deadline.run_bounded", side_effect=fake_run),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "cache.xml"),
            pytest.raises(SnapshotError) as exc,
        ):
            _castxml_dump([header], [], compiler="cc")
        assert not isinstance(exc.value, HeaderToolchainError)

    def test_generic_header_hint_stays_plain_snapshot_error(
        self, tmp_path: Path
    ) -> None:
        # A missing-include failure gets a generic diagnose_header_compile_
        # failure() hint (case 4 of _castxml_failure_hint) — a non-empty hint,
        # but an ordinary project header/input problem, not a G16
        # host-toolchain-mismatch signature. Must NOT be classified as
        # HeaderToolchainError: a caller branching on that class to retry
        # with a different castxml/sysroot must not fire on this (Codex
        # review).
        def fake_run(cmd, **kwargs):  # noqa: ANN001
            return _completed(
                returncode=1,
                stderr="foo.h:1:10: fatal error: missing_dep.h: No such file or directory",
            )

        header = tmp_path / "api.h"
        header.write_text("int f(void);\n", encoding="utf-8")
        with (
            patch("abicheck.dumper._resolve_selected_tool", return_value="castxml"),
            patch("abicheck.dumper.deadline.run_bounded", side_effect=fake_run),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "cache.xml"),
            pytest.raises(SnapshotError) as exc,
        ):
            _castxml_dump([header], [], compiler="cc")
        assert not isinstance(exc.value, HeaderToolchainError)
        # The generic hint text is still present in the message — only the
        # exception *class* changes, not the diagnostic content.
        assert "missing_dep.h" in str(exc.value)


class TestG16ClangFallbackRespectsConfiguredDriver:
    """The G16 recoverable-fallback guard must probe the exact clang driver
    _run_clang() would actually invoke (_resolve_clang_bin honors
    --gcc-path/--gcc-prefix), not a bare "clang" on PATH -- a caller-
    configured or prefixed clang may be available and should let the
    fallback recover even when bare "clang" isn't on PATH at all (Codex
    review). Fully mocked -- no castxml/clang needed."""

    _KWARGS = dict(
        backend="auto",
        gcc_options=None,
        sysroot=None,
        nostdinc=False,
        lang=None,
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=[],
        public_dir_paths=[],
    )

    def test_fallback_recovers_with_configured_gcc_path(self, tmp_path: Path) -> None:
        from abicheck.dumper import _header_ast_parser

        header = tmp_path / "api.h"
        header.write_text("int f(void);\n", encoding="utf-8")
        configured = "/opt/llvm/bin/clang++"

        def fake_which(name):  # noqa: ANN001
            # Bare "clang"/"clang++" is NOT on PATH -- only the exact
            # --gcc-path driver is.
            return configured if name == configured else None

        sentinel = MagicMock()
        with (
            patch.dict(os.environ, {"ABICHECK_ALLOW_AST_FALLBACK": "1"}),
            patch(
                "abicheck.dumper._castxml_dump",
                side_effect=SnapshotError(_ASSUME_STDERR),
            ),
            patch("abicheck.dumper.shutil.which", side_effect=fake_which),
            patch("abicheck.dumper._clang_header_dump", return_value=MagicMock()),
            patch("abicheck.dumper._ClangAstParser", return_value=sentinel),
        ):
            result = _header_ast_parser(
                [header],
                [],
                compiler="c++",
                gcc_path=configured,
                gcc_prefix=None,
                **self._KWARGS,
            )
        assert result is sentinel
        assert result._abicheck_ast_fallback_reason == (
            "castxml-toolchain-version-mismatch"
        )

    def test_fallback_recovers_from_castxml_version_gate_failure(
        self, tmp_path: Path
    ) -> None:
        """Regression (Codex review): UnsupportedCastxmlVersionError (the
        proactive version-gate check, raised before castxml even runs) is
        exactly the same "this castxml can't be trusted" signal as the two
        string-matched stderr signatures this fallback already recognizes
        -- excluding it defeated the opt-in fallback's whole purpose for
        the one new reason a castxml can now be untrusted."""
        from abicheck.dumper import _header_ast_parser
        from abicheck.errors import UnsupportedCastxmlVersionError

        header = tmp_path / "api.h"
        header.write_text("int f(void);\n", encoding="utf-8")

        sentinel = MagicMock()
        with (
            patch.dict(os.environ, {"ABICHECK_ALLOW_AST_FALLBACK": "1"}),
            patch(
                "abicheck.dumper._castxml_dump",
                side_effect=UnsupportedCastxmlVersionError(
                    "CastXML 0.6.20260105 is not supported."
                ),
            ),
            patch("abicheck.dumper.shutil.which", return_value="/usr/bin/clang++"),
            patch("abicheck.dumper._clang_header_dump", return_value=MagicMock()),
            patch("abicheck.dumper._ClangAstParser", return_value=sentinel),
        ):
            result = _header_ast_parser(
                [header],
                [],
                compiler="c++",
                gcc_path=None,
                gcc_prefix=None,
                **self._KWARGS,
            )
        assert result is sentinel
        assert result._abicheck_ast_fallback_reason == "castxml-unsupported-version"

    def test_auto_fallback_is_fail_closed_by_default(self, tmp_path: Path) -> None:
        from abicheck.dumper import _header_ast_parser

        header = tmp_path / "api.h"
        header.write_text("int f(void);\n", encoding="utf-8")
        with (
            patch.dict(os.environ, {}, clear=False),
            patch(
                "abicheck.dumper._castxml_dump",
                side_effect=SnapshotError(_ASSUME_STDERR),
            ),
            patch("abicheck.dumper.shutil.which", return_value="/usr/bin/clang++"),
            pytest.raises(SnapshotError, match="fallback is disabled"),
        ):
            os.environ.pop("ABICHECK_ALLOW_AST_FALLBACK", None)
            _header_ast_parser(
                [header],
                [],
                compiler="c++",
                gcc_path=None,
                gcc_prefix=None,
                **self._KWARGS,
            )

    def test_no_fallback_when_no_clang_driver_is_available(
        self, tmp_path: Path
    ) -> None:
        from abicheck.dumper import _header_ast_parser

        header = tmp_path / "api.h"
        header.write_text("int f(void);\n", encoding="utf-8")
        with (
            patch(
                "abicheck.dumper._castxml_dump",
                side_effect=SnapshotError(_ASSUME_STDERR),
            ),
            patch("abicheck.dumper.shutil.which", return_value=None),
            pytest.raises(SnapshotError),
        ):
            _header_ast_parser(
                [header],
                [],
                compiler="c++",
                gcc_path=None,
                gcc_prefix=None,
                **self._KWARGS,
            )
