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

"""Tests for the ADR-030 phase-2 castxml source ABI extractor.

The context→argv builder and the model→entity mapping are pure and tested in
the default (fast) lane; the end-to-end castxml run is marked ``integration``.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from abicheck.buildsource.build_evidence import CompileUnit
from abicheck.buildsource.source_extractors import (
    CASTXML_EXTRACTOR_VERSION,
    CastxmlSourceExtractor,
    build_castxml_command,
)
from abicheck.buildsource.source_extractors.base import (
    assemble_source_tu,
    entity_from_constant,
    entity_from_enum,
    entity_from_function,
    entity_from_record,
    entity_from_typedef,
    entity_from_variable,
)
from abicheck.model import (
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    TypeField,
    Variable,
)


def _cu(**kw: object) -> CompileUnit:
    base: dict[str, object] = {
        "id": "cu://src/foo.cpp#cfg",
        "source": "src/foo.cpp",
        "language": "CXX",
        "standard": "c++20",
    }
    base.update(kw)
    return CompileUnit(**base)  # type: ignore[arg-type]


# -- build_castxml_command (pure, D2) ----------------------------------------


def test_build_command_reflects_compile_context() -> None:
    cu = _cu(
        directory="/proj",
        defines={"FOO": "1", "BARE": ""},
        undefines=["NDEBUG"],
        include_paths=["include"],
        system_include_paths=["/opt/sdk/include"],
        sysroot="/sysroot",
        target_triple="aarch64-linux-gnu",
    )
    out = Path("build/out.xml")
    src = Path("src/foo.cpp")
    cmd = build_castxml_command(cu, src, out)
    assert cmd[:4] == ["castxml", "--castxml-output=1", "--castxml-cc-gnu", "g++"]
    assert "-std=c++20" in cmd
    assert "-DFOO=1" in cmd
    assert "-DBARE" in cmd  # valueless define carries no '='
    assert "-UNDEBUG" in cmd
    assert cmd[cmd.index("-I") + 1] == "include"
    assert cmd[cmd.index("-isystem") + 1] == "/opt/sdk/include"
    assert "--sysroot=/sysroot" in cmd
    assert "--target=aarch64-linux-gnu" in cmd
    # Compare via str(Path(...)) so the separator is OS-native (Windows uses \).
    assert cmd[-3:] == ["-o", str(out), str(src)]


def test_build_command_c_uses_gcc_and_no_target_for_msvc() -> None:
    cmd = build_castxml_command(
        _cu(language="C", standard="c11"), Path("a.c"), Path("o.xml")
    )
    assert "--castxml-cc-gnu" in cmd and "gcc" in cmd
    assert "-std=c11" in cmd
    # MSVC path: /std: form and no --target flag.
    msvc = build_castxml_command(
        _cu(standard="c++20", target_triple="x64"),
        Path("a.cpp"),
        Path("o.xml"),
        compiler_binary="cl.exe",
    )
    assert "--castxml-cc-msvc" in msvc
    assert "/std:c++20" in msvc
    assert not any(a.startswith("--target=") for a in msvc)


def test_build_command_uses_build_action_compiler() -> None:
    # The compiler recorded in the build action (argv[0]) is preferred over the
    # g++/gcc fallback so clang TUs replay against clang's builtins (Codex #335).
    clang = build_castxml_command(
        _cu(argv=["clang++", "-c", "foo.cpp"]), Path("foo.cpp"), Path("o.xml")
    )
    assert "--castxml-cc-gnu" in clang
    assert "clang++" in clang
    # clang-cl is MSVC-mode.
    clang_cl = build_castxml_command(
        _cu(argv=["clang-cl", "/c", "foo.cpp"]), Path("foo.cpp"), Path("o.xml")
    )
    assert "--castxml-cc-msvc" in clang_cl
    assert "clang-cl" in clang_cl
    # An explicit override still wins over argv.
    override = build_castxml_command(
        _cu(argv=["clang++"]), Path("a.cpp"), Path("o.xml"), compiler_binary="g++"
    )
    assert "g++" in override and "clang++" not in override


def test_build_command_detects_msvc_for_windows_compiler_paths() -> None:
    # A Windows compiler path from a cross / off-Windows compile database must
    # still select MSVC mode: Path(...).name does not split on '\' on POSIX, so
    # the basename has to be extracted host-independently (Codex review #335).
    cl = build_castxml_command(
        _cu(argv=[r"C:\VS\bin\cl.exe", "/c", "a.cpp"]), Path("a.cpp"), Path("o.xml")
    )
    assert "--castxml-cc-msvc" in cl
    clang_cl = build_castxml_command(
        _cu(argv=[r"C:\LLVM\bin\clang-cl.exe", "/c", "a.cpp"]),
        Path("a.cpp"),
        Path("o.xml"),
    )
    assert "--castxml-cc-msvc" in clang_cl
    # A Windows g++ path stays GNU.
    gnu = build_castxml_command(
        _cu(argv=[r"C:\msys64\mingw64\bin\g++.exe", "-c", "a.cpp"]),
        Path("a.cpp"),
        Path("o.xml"),
    )
    assert "--castxml-cc-gnu" in gnu


def test_build_command_carries_msvc_forced_includes() -> None:
    # MSVC /FI forced includes (joined and separate) must be carried for cl /
    # clang-cl TUs (Codex review #335).
    cmd = build_castxml_command(
        _cu(argv=["clang-cl", "/FIjoined.h", "/FI", "sep.h", "/c", "a.cpp"]),
        Path("a.cpp"),
        Path("o.xml"),
    )
    assert "--castxml-cc-msvc" in cmd
    assert "/FIjoined.h" in cmd
    assert cmd[cmd.index("/FI") + 1] == "sep.h"
    # In GNU mode a stray /FI-looking token is not carried (avoids -F-family
    # false matches); only the GNU -include/-imacros forms are.
    gnu = build_castxml_command(
        _cu(argv=["g++", "/FInotmine.h", "-c", "a.cpp"]), Path("a.cpp"), Path("o.xml")
    )
    assert "--castxml-cc-gnu" in gnu
    assert "/FInotmine.h" not in gnu


def test_build_command_carries_argv_only_options() -> None:
    # ABI-relevant flags and forced includes that live only in argv must be
    # carried through so castxml parses the same TU as the build (Codex review).
    cu = _cu(
        abi_relevant_flags=["-fms-extensions", "-fabi-version=11"],
        argv=[
            "g++",
            "-include",
            "config.h",
            "-imacros",
            "m.h",
            "-includejoined.h",  # joined GNU forced-include spelling
            "-c",
            "foo.cpp",
        ],
    )
    cmd = build_castxml_command(cu, Path("foo.cpp"), Path("o.xml"))
    assert "-fms-extensions" in cmd
    assert "-fabi-version=11" in cmd
    assert cmd[cmd.index("-include") + 1] == "config.h"
    assert cmd[cmd.index("-imacros") + 1] == "m.h"
    assert "-includejoined.h" in cmd  # joined form carried verbatim
    # the build action's own -c/source/-o are NOT blindly forwarded
    assert "-c" not in cmd


def test_build_command_unwraps_compiler_launcher() -> None:
    # A build action recorded with a ccache/sccache launcher must emulate the
    # real compiler (argv after the launcher), not the launcher itself, which
    # castxml would invoke without its compiler operand (Codex review #335, P2).
    cmd = build_castxml_command(
        _cu(argv=["ccache", "clang++", "-c", "foo.cpp"]),
        Path("foo.cpp"),
        Path("o.xml"),
    )
    assert "--castxml-cc-gnu" in cmd
    assert "clang++" in cmd
    assert "ccache" not in cmd
    # A launcher in front of clang-cl still resolves to MSVC mode.
    msvc = build_castxml_command(
        _cu(argv=["sccache", "clang-cl", "/c", "a.cpp"]), Path("a.cpp"), Path("o.xml")
    )
    assert "--castxml-cc-msvc" in msvc
    assert "clang-cl" in msvc


def test_build_command_preserves_include_pch_operand() -> None:
    # clang's -include-pch <file> is separate-operand only; it must not be
    # treated as a joined -include (which would drop the pch.h operand and leave
    # a dangling option that fails castxml replay) (Codex review #335, P2).
    cmd = build_castxml_command(
        _cu(argv=["clang++", "-include-pch", "pch.h", "-c", "a.cpp"]),
        Path("a.cpp"),
        Path("o.xml"),
    )
    assert "-include-pch" in cmd
    assert cmd[cmd.index("-include-pch") + 1] == "pch.h"


def test_build_command_carries_gnu_include_search_flags() -> None:
    # GNU -iquote/-idirafter take a dir operand and are NOT normalized into
    # include_paths/system_include_paths, so the replay must carry them or
    # castxml searches a different set of directories (Codex review #335, P2).
    cmd = build_castxml_command(
        _cu(argv=["g++", "-iquote", "q/dir", "-idirafter", "late/dir", "-c", "a.cpp"]),
        Path("a.cpp"),
        Path("o.xml"),
    )
    assert cmd[cmd.index("-iquote") + 1] == "q/dir"
    assert cmd[cmd.index("-idirafter") + 1] == "late/dir"


def test_build_command_carries_joined_gnu_include_search_flags() -> None:
    # gcc/clang also accept the joined spelling (-iquote/dir, -idirafter/dir);
    # a compile DB recording that form must not drop the directory (Codex #338 P2).
    cmd = build_castxml_command(
        _cu(argv=["g++", "-iquoteq/dir", "-idirafterlate/dir", "-c", "a.cpp"]),
        Path("a.cpp"),
        Path("o.xml"),
    )
    assert "-iquoteq/dir" in cmd
    assert "-idirafterlate/dir" in cmd


def test_build_command_carries_msvc_include_search_flags() -> None:
    # MSVC /I dir (separate) and /Idir (joined) add #include search directories
    # and are not normalized; carry them through in MSVC mode (Codex review #335).
    cmd = build_castxml_command(
        _cu(argv=["cl.exe", "/I", "inc dir", "/Ijoined", "-c", "a.cpp"]),
        Path("a.cpp"),
        Path("o.xml"),
    )
    assert cmd[cmd.index("/I") + 1] == "inc dir"  # separate operand preserved
    assert "/Ijoined" in cmd  # joined form preserved


def test_build_command_ignores_msvc_include_search_in_gnu_mode() -> None:
    # In GNU mode a `/I`-looking token must not be mistaken for an MSVC include
    # flag (it isn't a GNU option), so it is not carried through.
    cmd = build_castxml_command(
        _cu(argv=["g++", "/Ijoined", "-c", "a.cpp"]),
        Path("a.cpp"),
        Path("o.xml"),
    )
    assert "/Ijoined" not in cmd


def test_build_command_drops_split_sysroot_flag_carried_without_operand() -> None:
    # A split `-isysroot /sdk` is normalized into compile_unit.sysroot (emitted
    # as --sysroot=/sdk), but extract_abi_relevant_flags records only the bare
    # `-isysroot` token (operand dropped). Carrying it through would dangle and
    # swallow the following `-o`, breaking castxml replay (Codex review #335, P2).
    cmd = build_castxml_command(
        _cu(
            sysroot="/sdk",
            target_triple="x86_64-linux-gnu",
            abi_relevant_flags=["-isysroot", "--target", "-fvisibility=hidden"],
        ),
        Path("a.cpp"),
        Path("o.xml"),
    )
    # Structured fields are emitted in combined form once...
    assert "--sysroot=/sdk" in cmd
    assert "--target=x86_64-linux-gnu" in cmd
    # ...and the dangling bare toolchain tokens are NOT re-appended.
    assert "-isysroot" not in cmd
    assert "--target" not in cmd
    # A genuine non-toolchain abi flag is still carried through.
    assert "-fvisibility=hidden" in cmd
    # The output option keeps its operand (nothing swallowed `-o`).
    assert cmd[cmd.index("-o") + 1] == "o.xml"


def test_extract_runs_in_compile_unit_directory(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Mock castxml so we can assert the subprocess runs with cwd=directory and
    # exercise the extract() success path without the tool installed.
    from abicheck.buildsource.source_extractors import castxml as castxml_mod

    extractor = CastxmlSourceExtractor()
    monkeypatch.setattr(extractor, "available", lambda: True)
    captured: dict[str, object] = {}

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd: list[str], **kw: object) -> _Result:
        if "-o" not in cmd:  # the --version compiler-identity probe
            return _Result()
        captured["cwd"] = kw.get("cwd")
        out = cmd[cmd.index("-o") + 1]
        Path(out).write_text('<GCC_XML><File id="f1" name="foo.h"/></GCC_XML>')
        return _Result()

    monkeypatch.setattr(castxml_mod.deadline, "run_bounded", _fake_run)
    cu = _cu(source="src/foo.cpp", directory=str(tmp_path))
    tu = extractor.extract(cu, public_header_roots=["foo.h"], target_id="target://x")
    assert captured["cwd"] == str(tmp_path)
    assert tu.extractor["name"] == "castxml-source"


def test_extract_uses_deadline_bounded_not_raw_subprocess(monkeypatch) -> None:
    # P0 follow-up: same fix family as the L2 header-AST subprocess
    # (abicheck/deadline.py) — the castxml L4 extractor must go through
    # deadline.run_bounded (shrinking --budget deadline + process-group kill
    # on timeout), not a bare subprocess.run(timeout=self.timeout).
    from abicheck.buildsource.source_extractors import castxml as castxml_mod

    extractor = CastxmlSourceExtractor()
    monkeypatch.setattr(extractor, "available", lambda: True)
    seen: dict[str, object] = {}

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **kw):  # type: ignore[no-untyped-def]
        if "-o" not in cmd:  # the --version compiler-identity probe
            return _Result()
        seen.update(kw)
        out = cmd[cmd.index("-o") + 1]
        Path(out).write_text('<GCC_XML><File id="f1" name="foo.h"/></GCC_XML>')
        return _Result()

    monkeypatch.setattr(castxml_mod.deadline, "run_bounded", _fake_run)
    extractor.extract(_cu(source="foo.cpp"), public_header_roots=["foo.h"])
    assert seen.get("timeout") == extractor.timeout


def test_extract_deadline_exceeded_degrades_like_timeout(monkeypatch) -> None:
    # A --budget deadline expiring mid-extraction must degrade to the same
    # SourceExtractionError contract as an ordinary subprocess timeout (this
    # extractor's failures already fold into partial per-TU coverage rather
    # than aborting the scan), not propagate as a distinct exception type.
    from abicheck import deadline
    from abicheck.buildsource.source_extractors import (
        SourceExtractionError,
        castxml as castxml_mod,
    )

    extractor = CastxmlSourceExtractor()
    monkeypatch.setattr(extractor, "available", lambda: True)

    def _raise(cmd, **kw):  # type: ignore[no-untyped-def]
        raise deadline.DeadlineExceeded(-1.0)

    monkeypatch.setattr(castxml_mod.deadline, "run_bounded", _raise)
    with pytest.raises(SourceExtractionError, match="timed out"):
        extractor.extract(_cu(source="foo.cpp"), public_header_roots=["foo.h"])


def test_extract_bounded_by_local_cap_not_full_scan_budget(monkeypatch) -> None:
    """Codex review (PR #591), round 7: deadline.run_bounded() honors an
    active outer deadline verbatim (not min(timeout, left)), so a bare
    timeout=self.timeout on the L4 castxml call alone did nothing once a scan
    --budget was active: the call stayed bound by the FULL remaining scan
    budget instead of this extractor's own ~120s local cap. A hung per-TU
    castxml replay under a generous --budget could therefore eat the whole
    remaining scan instead of degrading after self.timeout. Assert the
    ContextVar deadline observed inside run_bounded is capped at the local
    timeout, not the much larger outer scan budget."""
    from abicheck import deadline
    from abicheck.buildsource.source_extractors import (
        SourceExtractionError,
        castxml as castxml_mod,
    )

    extractor = CastxmlSourceExtractor(timeout=10)
    monkeypatch.setattr(extractor, "available", lambda: True)
    seen_remaining: list[float | None] = []

    def _raise(cmd, **kw):  # type: ignore[no-untyped-def]
        seen_remaining.append(deadline.remaining())
        raise deadline.DeadlineExceeded(-1.0)

    monkeypatch.setattr(castxml_mod.deadline, "run_bounded", _raise)
    with deadline.deadline_scope(1800.0):  # a generous 30-minute --budget
        with pytest.raises(SourceExtractionError):
            extractor.extract(_cu(source="foo.cpp"), public_header_roots=["foo.h"])

    assert seen_remaining
    # Bound by the extractor's own ~10s local cap, not the 1800s scan budget.
    assert seen_remaining[0] is not None and seen_remaining[0] <= 10.5


def test_extract_rechecks_deadline_before_parsing_xml(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Codex review follow-up (PR #591): the 'check deadline before loading
    cached/output data' gap fixed in the L2 dumper.py path and the L4 clang
    extractor also existed here — a budget that expires exactly as castxml
    exits successfully must not silently let the XML parse run past it, and
    must degrade to SourceExtractionError (L4 never aborts the scan), not a
    raw DeadlineExceeded."""
    import time

    from abicheck import deadline
    from abicheck.buildsource.source_extractors import (
        SourceExtractionError,
        castxml as castxml_mod,
    )

    extractor = CastxmlSourceExtractor()
    monkeypatch.setattr(extractor, "available", lambda: True)

    class _Result:
        returncode = 0
        stderr = ""

    def _fake_run(cmd, **kw):  # type: ignore[no-untyped-def]
        time.sleep(0.05)
        out = cmd[cmd.index("-o") + 1]
        Path(out).write_text('<GCC_XML><File id="f1" name="foo.h"/></GCC_XML>')
        return _Result()

    monkeypatch.setattr(castxml_mod.deadline, "run_bounded", _fake_run)
    with deadline.deadline_scope(0.01):
        with pytest.raises(SourceExtractionError, match="deadline exceeded"):
            extractor.extract(_cu(source="foo.cpp"), public_header_roots=["foo.h"])


def test_extract_rechecks_deadline_after_parsing_xml(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Codex review (PR #591, round 4): DefusedET.parse() on a large per-TU
    castxml XML file can itself consume the rest of the budget -- the
    existing pre-parse deadline.check() doesn't catch that; must re-check
    again after the parse, before walking the tree in _parse_root."""
    import time

    from abicheck import deadline
    from abicheck.buildsource.source_extractors import (
        SourceExtractionError,
        castxml as castxml_mod,
    )

    extractor = CastxmlSourceExtractor()
    monkeypatch.setattr(extractor, "available", lambda: True)

    class _Result:
        returncode = 0
        stderr = ""

    def _fake_run(cmd, **kw):  # type: ignore[no-untyped-def]
        out = cmd[cmd.index("-o") + 1]
        Path(out).write_text('<GCC_XML><File id="f1" name="foo.h"/></GCC_XML>')
        return _Result()

    monkeypatch.setattr(castxml_mod.deadline, "run_bounded", _fake_run)
    real_parse = castxml_mod.DefusedET.parse

    def _slow_parse(path):
        time.sleep(0.05)
        return real_parse(path)

    monkeypatch.setattr(castxml_mod.DefusedET, "parse", _slow_parse)
    with deadline.deadline_scope(0.03):
        with pytest.raises(SourceExtractionError, match="deadline exceeded"):
            extractor.extract(_cu(source="foo.cpp"), public_header_roots=["foo.h"])


def test_unredact_home_expands_tilde() -> None:
    # The evidence redaction policy rewrites the home prefix to `~`; the replay
    # must expand it back since subprocess does not (Codex review #335, P2).
    import os

    from abicheck.buildsource.source_extractors.castxml import _unredact_home

    home = os.path.expanduser("~")
    assert _unredact_home("~/build/foo.cpp") == f"{home}/build/foo.cpp"
    assert _unredact_home("-I~/include") == f"-I{home}/include"  # joined flag
    assert _unredact_home("~\\build\\foo.cpp") == f"{home}\\build\\foo.cpp"  # win sep
    assert _unredact_home("~") == home  # whole-token placeholder
    assert _unredact_home("-std=c++17") == "-std=c++17"  # no tilde → untouched


def test_unredact_home_leaves_embedded_short_name_tilde() -> None:
    # Only a `~` standing in for a home *directory* (whole token or followed by a
    # path separator) is expanded. A `~` embedded mid-component — e.g. a Windows
    # 8.3 short name like RUNNER~1 in a freshly created temp path — is NOT a
    # redaction placeholder and must be left intact, or the path is corrupted
    # into RUNNER<home>1 and the castxml output file cannot be opened
    # (Windows CI lane failure, #335).
    from abicheck.buildsource.source_extractors.castxml import _unredact_home

    temp = "C:\\Users\\RUNNER~1\\AppData\\Local\\Temp\\tmpabcd.xml"
    assert _unredact_home(temp) == temp
    assert _unredact_home("/home/foo/RUNNER~1bar") == "/home/foo/RUNNER~1bar"


def test_extract_unredacts_home_for_replay(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # A redacted CompileUnit (`~` placeholders) must be expanded to the real home
    # before castxml runs (subprocess does not expand `~`) (Codex P2). We assert
    # the cwd/flags equal the expanded values rather than "no tilde": on some
    # platforms the resolved home itself contains a tilde (Windows 8.3 short
    # names like C:\Users\RUNNER~1), which is legitimate.
    import os

    from abicheck.buildsource.source_extractors import castxml as castxml_mod

    extractor = CastxmlSourceExtractor()
    monkeypatch.setattr(extractor, "available", lambda: True)
    captured: dict[str, object] = {}

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd: list[str], **kw: object) -> _Result:
        if "-o" not in cmd:  # the --version compiler-identity probe
            return _Result()
        captured["cmd"] = cmd
        captured["cwd"] = kw.get("cwd")
        out = cmd[cmd.index("-o") + 1]
        Path(out).write_text('<GCC_XML><File id="f1" name="foo.h"/></GCC_XML>')
        return _Result()

    monkeypatch.setattr(castxml_mod.deadline, "run_bounded", _fake_run)
    # Absolute (redacted) source + a `~`-redacted include path in the build.
    cu = _cu(
        source="~/proj/src/foo.cpp",
        directory="~/proj",
        include_paths=["~/proj/include"],
    )
    extractor.extract(cu, public_header_roots=["foo.h"], target_id="target://x")
    home = os.path.expanduser("~")
    assert captured["cwd"] == f"{home}/proj"  # expanded, not the literal "~/proj"
    assert f"{home}/proj/include" in captured["cmd"]  # type: ignore[operator]


def test_extract_unredacts_home_in_macro_value(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # A redacted home prefix inside a macro value (e.g. -DCFG=~/build/cfg.h used
    # via `#include CFG`) is expanded for replay, like any other home-path
    # operand, so CastXML parses the same TU the real compile did (Codex review
    # #335). A `~` that is NOT a home-dir placeholder (mid-token, e.g. a `~1`
    # short name) is still left intact by _unredact_home.
    import os

    from abicheck.buildsource.source_extractors import castxml as castxml_mod

    extractor = CastxmlSourceExtractor()
    monkeypatch.setattr(extractor, "available", lambda: True)
    captured: dict[str, object] = {}

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd: list[str], **kw: object) -> _Result:
        if "-o" not in cmd:  # the --version compiler-identity probe
            return _Result()
        captured["cmd"] = cmd
        out = cmd[cmd.index("-o") + 1]
        Path(out).write_text('<GCC_XML><File id="f1" name="foo.h"/></GCC_XML>')
        return _Result()

    monkeypatch.setattr(castxml_mod.deadline, "run_bounded", _fake_run)
    cu = _cu(
        source="~/proj/src/foo.cpp",
        directory="~/proj",
        include_paths=["~/proj/include"],
        defines={"CFG": "~/build/cfg.h", "MODE": "fast~1"},
    )
    extractor.extract(cu, public_header_roots=["foo.h"], target_id="target://x")
    home = os.path.expanduser("~")
    cmd = captured["cmd"]
    # Home-prefix placeholder in the macro value is expanded...
    assert f"-DCFG={home}/build/cfg.h" in cmd  # type: ignore[operator]
    # ...the include path operand too...
    assert f"{home}/proj/include" in cmd  # type: ignore[operator]
    # ...but a mid-token `~` (not a home-dir placeholder) is left intact.
    assert "-DMODE=fast~1" in cmd  # type: ignore[operator]


# -- model → SourceEntity mapping (pure, D4) ---------------------------------


def test_entity_from_function_signature_stable_under_default_change() -> None:
    common = dict(
        name="ns::f",
        mangled="_ZN2ns1fEi",
        return_type="void",
        source_header="include/f.h",
        source_location="include/f.h:10",
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    no_default = entity_from_function(Function(params=[Param("x", "int")], **common))
    default_1 = entity_from_function(
        Function(params=[Param("x", "int", default="1")], **common)
    )
    default_2 = entity_from_function(
        Function(params=[Param("x", "int", default="2")], **common)
    )
    # Same type signature → signature_hash unchanged across all three.
    assert (
        no_default.signature_hash
        == default_1.signature_hash
        == default_2.signature_hash
    )
    # value carries the default-argument expression so add/remove AND value
    # changes are visible to default_argument_changed.
    assert no_default.value == ""
    assert default_1.value == "x=1"
    assert default_2.value == "x=2"
    assert default_1.value != default_2.value
    assert default_1.kind == "function"
    assert default_1.api_relevant is True
    assert default_1.source_location is not None
    assert default_1.source_location.origin == "PUBLIC_HEADER"


def test_entity_from_function_drops_bare_name_mangled_fallback() -> None:
    # The castxml parser stores Function.mangled as `el.get("mangled","") or
    # name`, so a constructor with no mangled attribute arrives as mangled==name.
    # entity_from_function must treat that as "no mangled name" so identity()
    # falls back to qualified_name#signature_hash and unmangled overloads stay
    # distinct (Codex review #335, P2).
    ctor_int = entity_from_function(
        Function(
            name="Widget",
            mangled="Widget",
            return_type="void",
            params=[Param("x", "int")],
        )
    )
    ctor_dbl = entity_from_function(
        Function(
            name="Widget",
            mangled="Widget",
            return_type="void",
            params=[Param("x", "double")],
        )
    )
    assert ctor_int.mangled_name == ""
    assert ctor_dbl.mangled_name == ""
    assert ctor_int.identity() != ctor_dbl.identity()
    # A real mangled name is preserved verbatim.
    real = entity_from_function(
        Function(name="ns::f", mangled="_ZN2ns1fEv", return_type="void")
    )
    assert real.mangled_name == "_ZN2ns1fEv"
    assert real.identity() == "_ZN2ns1fEv"


def test_entity_from_function_signature_changes_with_param_type() -> None:
    a = entity_from_function(
        Function(name="f", mangled="m", return_type="int", params=[Param("x", "int")])
    )
    b = entity_from_function(
        Function(name="f", mangled="m", return_type="int", params=[Param("x", "long")])
    )
    assert a.signature_hash != b.signature_hash


def test_entity_from_record_type_hash_tracks_layout() -> None:
    r1 = entity_from_record(
        RecordType(
            name="S", kind="struct", size_bits=64, fields=[TypeField("a", "int")]
        )
    )
    r2 = entity_from_record(
        RecordType(
            name="S", kind="struct", size_bits=128, fields=[TypeField("a", "int")]
        )
    )
    assert r1.kind == "record"
    assert r1.type_hash != r2.type_hash


def test_entity_from_enum_and_variable_and_constant_and_typedef() -> None:
    en = entity_from_enum(
        EnumType(name="E", members=[EnumMember("A", 0), EnumMember("B", 1)])
    )
    assert en.kind == "enum" and en.type_hash

    var = entity_from_variable(
        Variable(
            name="g",
            mangled="g",
            type="int",
            value="7",
            origin=ScopeOrigin.PUBLIC_HEADER,
        )
    )
    assert var.kind == "variable" and var.value == "7" and var.api_relevant is True

    const = entity_from_constant("kMax", "100")
    assert (
        const.kind == "constexpr"
        and const.value == "100"
        and const.api_relevant is True
    )
    # A plain public constant stays PUBLIC_HEADER and carries no generated marker.
    assert const.visibility == "public_header"
    assert const.source_location and const.source_location.origin == "PUBLIC_HEADER"

    td = entity_from_typedef("Handle", "void*")
    assert td.kind == "typedef" and td.value == "void*"


def test_entity_from_constant_marks_generated() -> None:
    # A constexpr from a generated public header is stamped GENERATED (visibility
    # + origin) and keeps its declaring-header path, so _diff_generated owns its
    # removal instead of it being silently dropped (Codex review #335, P2).
    gen = entity_from_constant(
        "cfg::KMax", "64", source_header="build/gen/config_generated.h", generated=True
    )
    assert gen.kind == "constexpr"
    assert gen.visibility == "generated"
    assert gen.source_location is not None
    assert gen.source_location.origin == "GENERATED"
    assert gen.source_location.path == "build/gen/config_generated.h"


def test_assemble_marks_generated_constants() -> None:
    # assemble_source_tu threads per-constant header + generated set onto the
    # constexpr entities (Codex review #335, P2).
    cu = _cu()
    tu = assemble_source_tu(
        cu,
        public_header_roots=["include/foo.h"],
        target_id="",
        extractor_name="castxml-source",
        extractor_version=CASTXML_EXTRACTOR_VERSION,
        functions=[],
        records=[],
        enums=[],
        variables=[],
        constants={"cfg::KMax": "64", "kPlain": "1"},
        typedefs={},
        constant_headers={
            "cfg::KMax": "build/gen/config_generated.h",
            "kPlain": "include/foo.h",
        },
        generated_constants={"cfg::KMax"},
    )
    by_name = {e.qualified_name: e for e in tu.constexpr_values}
    assert by_name["cfg::KMax"].visibility == "generated"
    assert by_name["kPlain"].visibility == "public_header"


def test_non_public_origin_is_not_api_relevant() -> None:
    fn = entity_from_function(
        Function(
            name="impl",
            mangled="i",
            return_type="void",
            origin=ScopeOrigin.PRIVATE_HEADER,
        )
    )
    assert fn.api_relevant is False
    assert fn.visibility == "private_header"


def test_private_member_of_public_class_is_not_api_relevant() -> None:
    # A private/protected member of a public class is in a public header but not
    # callable by consumers, so it must stay off the public source surface — a
    # private default-arg edit must not produce an L4 finding (Codex review #335,
    # P2). A public method (and a free function) stay api_relevant.
    from abicheck.model import AccessLevel

    private_method = entity_from_function(
        Function(
            name="Widget::impl",
            mangled="_ZN6Widget4implEv",
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
            access=AccessLevel.PRIVATE,
        )
    )
    assert private_method.api_relevant is False

    protected_method = entity_from_function(
        Function(
            name="Widget::hook",
            mangled="_ZN6Widget4hookEv",
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
            access=AccessLevel.PROTECTED,
        )
    )
    assert protected_method.api_relevant is False

    public_method = entity_from_function(
        Function(
            name="Widget::api",
            mangled="_ZN6Widget3apiEv",
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
            access=AccessLevel.PUBLIC,
        )
    )
    assert public_method.api_relevant is True


# -- assemble_source_tu (pure, D4) -------------------------------------------


def test_assemble_source_tu_routes_entities_to_buckets() -> None:
    cu = _cu(target_id="target://libfoo")
    tu = assemble_source_tu(
        cu,
        public_header_roots=["include/foo.h"],
        target_id="",
        extractor_name="castxml-source",
        extractor_version=CASTXML_EXTRACTOR_VERSION,
        functions=[Function(name="f", mangled="mf", return_type="void")],
        records=[RecordType(name="S", kind="struct")],
        enums=[EnumType(name="E")],
        variables=[Variable(name="g", mangled="mg", type="int")],
        constants={"kMax": "10"},
        typedefs={"Alias": "int"},
    )
    assert tu.tu_id == "cu://src/foo.cpp#cfg"
    assert tu.target_id == "target://libfoo"
    assert tu.extractor == {
        "name": "castxml-source",
        "version": CASTXML_EXTRACTOR_VERSION,
    }
    assert tu.compile_context_hash.startswith("sha256:")
    assert [e.qualified_name for e in tu.functions] == ["f"]
    # records + enums + typedefs all land in the types bucket
    assert {e.qualified_name for e in tu.types} == {"S", "E", "Alias"}
    assert [e.qualified_name for e in tu.variables] == ["g"]
    assert [e.qualified_name for e in tu.constexpr_values] == ["kMax"]
    # round-trips through the normalized schema
    from abicheck.buildsource.source_abi import SourceAbiTu

    assert SourceAbiTu.from_dict(tu.to_dict()).tu_id == tu.tu_id


# -- extractor orchestration (no real castxml) -------------------------------


def test_extract_raises_when_castxml_unavailable() -> None:
    from abicheck.buildsource.source_extractors import SourceExtractionError

    extractor = CastxmlSourceExtractor(castxml_bin="castxml-does-not-exist-xyz")
    assert extractor.available() is False
    with pytest.raises(SourceExtractionError):
        extractor.extract(_cu(), public_header_roots=["include/foo.h"])


def test_parse_root_maps_castxml_xml_without_running_castxml() -> None:
    # Drive the XML→SourceAbiTu path on a hand-built GCC_XML document, so the
    # parser/assembly is covered without castxml installed.
    from xml.etree.ElementTree import Element, SubElement

    root = Element("GCC_XML")
    SubElement(root, "File", id="f1", name="foo.h")
    SubElement(root, "FundamentalType", id="t_int", name="int")
    SubElement(root, "Location", id="loc1", file="f1", line="3")
    cls = SubElement(
        root, "Class", id="c1", name="Widget", size="64", align="64", location="loc1"
    )
    SubElement(cls, "Field", name="a", type="t_int", offset="0")
    fn = SubElement(
        root, "Function", id="fn1", name="add", returns="t_int", location="loc1"
    )
    SubElement(fn, "Argument", name="x", type="t_int")

    extractor = CastxmlSourceExtractor()
    tu = extractor._parse_root(
        root, _cu(), public_header_roots=["foo.h"], target_id="target://libfoo"
    )
    names = {e.qualified_name for e in tu.all_entities()}
    assert any("add" in n for n in names)
    assert any("Widget" in n for n in names)
    assert tu.extractor["name"] == "castxml-source"
    # Provenance is applied (P1 fix): public-header decls are api_relevant, not
    # left UNKNOWN — otherwise the linker would filter every declaration out.
    assert any(e.api_relevant for e in tu.functions)
    assert any(e.api_relevant for e in tu.types)
    # And they survive linking onto the public source surface.
    from abicheck.buildsource import link_source_abi

    surface = link_source_abi([tu], target_id="target://libfoo")
    assert any("add" in e.qualified_name for e in surface.reachable_declarations)
    assert any("Widget" in e.qualified_name for e in surface.reachable_types)


def test_parse_root_marks_generated_public_header_as_generated() -> None:
    # A header that is both public and generated must keep the GENERATED marker
    # so a generated public type change is caught by diff_source_abi's
    # generated-header check, not merged into the plain public surface (Codex).
    from xml.etree.ElementTree import Element, SubElement

    root = Element("GCC_XML")
    SubElement(root, "File", id="f1", name="generated/config_generated.h")
    SubElement(root, "FundamentalType", id="t_int", name="int")
    SubElement(root, "Location", id="loc1", file="f1", line="3")
    cls = SubElement(
        root, "Class", id="c1", name="Cfg", size="32", align="32", location="loc1"
    )
    SubElement(cls, "Field", name="flag", type="t_int", offset="0")

    extractor = CastxmlSourceExtractor()
    tu = extractor._parse_root(
        root,
        _cu(),
        public_header_roots=["generated/config_generated.h"],
        target_id="target://libfoo",
    )
    cfg = next(e for e in tu.types if "Cfg" in e.qualified_name)
    assert cfg.visibility == "generated"
    assert cfg.source_location is not None
    assert cfg.source_location.origin == "GENERATED"
    # It still survives linking onto the public surface (generated == public).
    from abicheck.buildsource import link_source_abi

    surface = link_source_abi([tu], target_id="target://libfoo")
    assert any("Cfg" in e.qualified_name for e in surface.reachable_types)


def test_parse_root_keeps_private_generated_header_off_public_surface() -> None:
    # A generated-looking header that is NOT in public_header_roots (e.g.
    # build/generated/internal_config.h) is classified GENERATED by
    # classify_origin (the public check runs first and fails). Since the L4
    # schema treats GENERATED as public, the extractor must demote it to a
    # private origin so internal generated decls/types never leak onto the
    # linked public surface (Codex review #335, P2).
    from xml.etree.ElementTree import Element, SubElement

    root = Element("GCC_XML")
    SubElement(root, "File", id="f1", name="build/generated/internal_config.h")
    SubElement(root, "FundamentalType", id="t_int", name="int")
    SubElement(root, "Location", id="loc1", file="f1", line="3")
    cls = SubElement(
        root, "Class", id="c1", name="Internal", size="32", align="32", location="loc1"
    )
    SubElement(cls, "Field", name="flag", type="t_int", offset="0")

    extractor = CastxmlSourceExtractor()
    # Public set is some *other* header; the generated one is private to the build.
    tu = extractor._parse_root(
        root, _cu(), public_header_roots=["api.h"], target_id="target://libfoo"
    )
    internal = next(e for e in tu.types if "Internal" in e.qualified_name)
    assert internal.visibility == "private_header"
    assert not internal.api_relevant
    # And it does not survive linking onto the public surface.
    from abicheck.buildsource import link_source_abi

    surface = link_source_abi([tu], target_id="target://libfoo")
    assert not any("Internal" in e.qualified_name for e in surface.reachable_types)


def test_parse_root_omits_unscoped_typedefs() -> None:
    # parse_typedefs() carries no provenance, so the extractor must not emit
    # typedefs (they would be falsely marked public and could create spurious
    # odr_source_conflict). Records/enums still come through (Codex review #335).
    from xml.etree.ElementTree import Element, SubElement

    root = Element("GCC_XML")
    SubElement(root, "File", id="f1", name="foo.h")
    SubElement(root, "FundamentalType", id="t_int", name="int")
    SubElement(root, "Location", id="loc1", file="f1", line="3")
    # A typedef (no provenance) and a record (has provenance).
    SubElement(root, "Typedef", id="td1", name="Handle", type="t_int")
    SubElement(
        root, "Class", id="c1", name="Rec", size="32", align="32", location="loc1"
    )

    extractor = CastxmlSourceExtractor()
    tu = extractor._parse_root(
        root, _cu(), public_header_roots=["foo.h"], target_id="target://libfoo"
    )
    kinds = {e.kind for e in tu.types}
    assert "typedef" not in kinds
    assert any(e.qualified_name == "Rec" for e in tu.types)


# -- fact_set / coverage stamping (Codex review, PR #719) --------------------


def test_parse_root_stamps_fact_set_and_coverage() -> None:
    """The castxml extractor previously never stamped ``fact_set``/``coverage``
    at all, so two castxml-produced TUs compared as if neither carried a
    fact_set identity -- silently exempting every castxml comparison from the
    producer/producer_version recipe-drift gating this ADR-038 C.8 apparatus
    exists to provide."""
    from xml.etree.ElementTree import Element, SubElement

    root = Element("GCC_XML")
    SubElement(root, "File", id="f1", name="foo.h")
    SubElement(root, "FundamentalType", id="t_int", name="int")
    SubElement(root, "Location", id="loc1", file="f1", line="3")
    SubElement(
        root, "Class", id="c1", name="Widget", size="64", align="64", location="loc1"
    )

    extractor = CastxmlSourceExtractor()
    tu = extractor._parse_root(
        root, _cu(), public_header_roots=["foo.h"], target_id="target://libfoo"
    )
    assert tu.fact_set["producer"] == "castxml-source"
    assert tu.fact_set["producer_version"] == CASTXML_EXTRACTOR_VERSION
    # castxml's own bundled Clang runs in gcc-emulation mode by default (no
    # MSVC compiler_binary override configured), never the direct
    # `clang -ast-dump=json` recipe default_fact_set's "clang" default names.
    assert tu.fact_set["compiler_family"] == "gnu"
    # Families this extractor genuinely collects.
    assert tu.coverage["types"] == "complete"
    # Families this extractor never attempts at all -- a permanent producer
    # limitation, not a collection failure.
    for family in ("macros", "templates", "inline_bodies", "source_edges"):
        assert tu.coverage[family] == "unsupported"


def test_parse_root_stamps_compiler_version_from_castxml_probe(monkeypatch) -> None:
    """Codex review, PR #719: an unstamped compiler_version means two runs on
    the same abicheck release but different castxml/bundled-Clang builds
    would be silently treated as recipe-agreeing -- deterministic coverage
    for the probe wiring, independent of whether a real castxml happens to
    be on this host's PATH."""
    from abicheck.buildsource.source_extractors import castxml as castxml_mod

    castxml_mod._castxml_tool_version.cache_clear()
    monkeypatch.setattr(
        castxml_mod, "_castxml_tool_version", lambda _bin, *_args: "0.6.20260105"
    )
    extractor = CastxmlSourceExtractor()
    tu = extractor._parse_root(
        _root_with_one_type(), _cu(), public_header_roots=["foo.h"], target_id=""
    )
    assert tu.fact_set["compiler_version"] == "0.6.20260105"


def test_castxml_tool_version_degrades_to_empty_on_missing_binary() -> None:
    from abicheck.buildsource.source_extractors.castxml import _castxml_tool_version

    _castxml_tool_version.cache_clear()
    assert _castxml_tool_version("definitely-not-a-real-binary-xyz") == ""


def test_castxml_tool_version_includes_bundled_clang_identity(monkeypatch) -> None:
    """Codex review, PR #719, second round: two castxml installs sharing the
    same castxml release but bundling a different Clang must not read as
    the same compiler_version -- the bundled Clang is what actually resolves
    a compiler-selected fact like an unfixed enum's underlying type."""
    import subprocess as subprocess_mod

    from abicheck.buildsource.source_extractors import castxml as castxml_mod

    castxml_mod._castxml_tool_version.cache_clear()

    def fake_run(*_args, **_kwargs):
        return subprocess_mod.CompletedProcess(
            args=["castxml", "--version"],
            returncode=0,
            stdout=(
                "castxml version 0.6.3\n\n"
                "CastXML project maintained and supported by Kitware "
                "(kitware.com).\n\n"
                "Ubuntu clang version 17.0.6 (9ubuntu1)\n"
                "Target: x86_64-pc-linux-gnu\n"
                "Thread model: posix\n"
                "InstalledDir:\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(castxml_mod.deadline, "run_bounded", fake_run)
    version = castxml_mod._castxml_tool_version("castxml")
    assert version.startswith("0.6.3")
    assert "clang version 17.0.6" in version
    castxml_mod._castxml_tool_version.cache_clear()


def test_castxml_tool_version_recognizes_llvm_spelled_banner(monkeypatch) -> None:
    """Codex review, PR #719, third round: some castxml/frontend builds spell
    the bundled-compiler banner line "LLVM version ..." rather than "clang
    version ..." (the same variance `dumper_castxml_probe._CLANG_VERSION_RE`
    already documents/handles) -- two installs differing only in that
    spelling must not collapse to the identical compiler_version."""
    import subprocess as subprocess_mod

    from abicheck.buildsource.source_extractors import castxml as castxml_mod

    castxml_mod._castxml_tool_version.cache_clear()

    def fake_run(*_args, **_kwargs):
        return subprocess_mod.CompletedProcess(
            args=["castxml", "--version"],
            returncode=0,
            stdout=(
                "castxml version 0.6.3\n\n"
                "CastXML project maintained and supported by Kitware "
                "(kitware.com).\n\n"
                "LLVM version 18.1.8\n"
                "Target: x86_64-pc-linux-gnu\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(castxml_mod.deadline, "run_bounded", fake_run)
    version = castxml_mod._castxml_tool_version("castxml")
    assert "LLVM version 18.1.8" in version
    castxml_mod._castxml_tool_version.cache_clear()


def test_castxml_tool_version_reads_stderr_banner(monkeypatch) -> None:
    """Codex review, PR #719, fourth round: some castxml wrapper/build
    combinations write the --version banner to stderr rather than stdout
    (dumper_castxml_probe.py's own combined-transcript read already handles
    this) -- the probe must not silently return "" for those installs."""
    import subprocess as subprocess_mod

    from abicheck.buildsource.source_extractors import castxml as castxml_mod

    castxml_mod._castxml_tool_version.cache_clear()

    def fake_run(*_args, **_kwargs):
        return subprocess_mod.CompletedProcess(
            args=["castxml", "--version"],
            returncode=0,
            stdout="",
            stderr="castxml version 0.6.3\nUbuntu clang version 17.0.6\n",
        )

    monkeypatch.setattr(castxml_mod.deadline, "run_bounded", fake_run)
    version = castxml_mod._castxml_tool_version("castxml")
    assert version.startswith("0.6.3")
    assert "clang version 17.0.6" in version
    castxml_mod._castxml_tool_version.cache_clear()


def test_castxml_tool_version_matches_banner_case_insensitively(monkeypatch) -> None:
    """Codex review, PR #719, fourth round: a build that capitalizes
    "CastXML version ..." must still be recognized -- mirrors
    dumper_castxml_probe's case-insensitive ``_CASTXML_VERSION_RE``."""
    import subprocess as subprocess_mod

    from abicheck.buildsource.source_extractors import castxml as castxml_mod

    castxml_mod._castxml_tool_version.cache_clear()

    def fake_run(*_args, **_kwargs):
        return subprocess_mod.CompletedProcess(
            args=["castxml", "--version"],
            returncode=0,
            stdout="CastXML Version 0.6.3\n",
            stderr="",
        )

    monkeypatch.setattr(castxml_mod.deadline, "run_bounded", fake_run)
    version = castxml_mod._castxml_tool_version("castxml")
    assert version.startswith("0.6.3")
    castxml_mod._castxml_tool_version.cache_clear()


def test_castxml_tool_version_is_bound_by_deadline_run_bounded(monkeypatch) -> None:
    """Codex review, PR #719, fifth round: the probe previously called plain
    ``subprocess.run(timeout=5)``, ignoring an active scan ``--budget``
    deadline shorter than 5s -- it must go through the same
    deadline-aware bounded path (``deadline.run_bounded``, via
    ``run_bounded_for_extraction``) as every other subprocess this
    extractor runs. Outside an active deadline scope, a local timeout
    still degrades to "" (not raise) -- see the next test for the
    genuinely-active-scan-deadline case, which does not degrade."""
    from abicheck import deadline
    from abicheck.buildsource.source_extractors import castxml as castxml_mod

    castxml_mod._castxml_tool_version.cache_clear()
    seen: dict[str, object] = {}

    def fake_run_bounded(cmd, **kw):  # type: ignore[no-untyped-def]
        seen["cmd"] = cmd
        seen["timeout"] = kw.get("timeout")
        raise deadline.DeadlineExceeded(-1.0)

    monkeypatch.setattr(castxml_mod.deadline, "run_bounded", fake_run_bounded)
    assert castxml_mod._castxml_tool_version("castxml") == ""
    assert seen["cmd"] == ["castxml", "--version"]
    assert seen["timeout"] == 5
    castxml_mod._castxml_tool_version.cache_clear()


def test_castxml_tool_version_reraises_deadline_exceeded_under_active_budget(
    monkeypatch,
) -> None:
    """Codex review, PR #719, sixth round: a genuinely exhausted scan
    --budget must stay observable, not be silently absorbed into an
    empty identity -- a cache lookup keyed on that "" could otherwise
    proceed past an expired deadline with no further check in between.
    Deliberately NOT wrapped into SourceExtractionError: both of this
    function's callers must see the same un-swallowed
    deadline.DeadlineExceeded that _replay_cache_lookup()'s own bare
    deadline.check() already lets propagate for this phase."""
    from abicheck import deadline
    from abicheck.buildsource.source_extractors import castxml as castxml_mod

    castxml_mod._castxml_tool_version.cache_clear()

    def fake_run_bounded(cmd, **kw):  # type: ignore[no-untyped-def]
        raise deadline.DeadlineExceeded(-1.0)

    monkeypatch.setattr(castxml_mod.deadline, "run_bounded", fake_run_bounded)
    with deadline.deadline_scope(-1.0):  # already-exhausted active budget
        with pytest.raises(deadline.DeadlineExceeded):
            castxml_mod._castxml_tool_version("castxml")
    castxml_mod._castxml_tool_version.cache_clear()


def test_castxml_tool_version_refreshes_when_executable_is_swapped(
    tmp_path: Path, monkeypatch
) -> None:
    """Codex review, PR #719, sixth round: caching the probe by binary PATH
    alone means a long-lived process keeps serving the OLD identity forever
    if the executable AT THAT SAME PATH is replaced in place (an in-place
    upgrade, or a swapped PATH entry) without the process restarting.
    ``_executable_stat_key()``'s dev/ino/mtime/size, folded into the
    lru_cache key by real callers, must make a same-path swap re-probe."""
    import os
    import subprocess as subprocess_mod

    from abicheck.buildsource.source_extractors import castxml as castxml_mod

    castxml_mod._castxml_tool_version.cache_clear()
    exe = tmp_path / "castxml"
    exe.write_text("v1")
    responses = iter(["castxml version 1.0.0\n", "castxml version 2.0.0\n"])

    def fake_run(*_args, **_kwargs):
        return subprocess_mod.CompletedProcess(
            args=["castxml", "--version"],
            returncode=0,
            stdout=next(responses),
            stderr="",
        )

    monkeypatch.setattr(castxml_mod.deadline, "run_bounded", fake_run)
    monkeypatch.setattr(castxml_mod.shutil, "which", lambda _bin: str(exe))

    key1 = castxml_mod._executable_stat_key(str(exe))
    assert castxml_mod._castxml_tool_version(str(exe), *key1) == "1.0.0"

    # "Swap" the executable at the same path -- content and mtime change.
    exe.write_text("v2, a different length")
    os.utime(exe, ns=(exe.stat().st_atime_ns + 10**9, exe.stat().st_mtime_ns + 10**9))
    key2 = castxml_mod._executable_stat_key(str(exe))
    assert key2 != key1  # the stat signature actually changed
    assert castxml_mod._castxml_tool_version(str(exe), *key2) == "2.0.0"
    castxml_mod._castxml_tool_version.cache_clear()


def test_castxml_extractor_cache_identity_extra_folds_probed_tool_version(
    monkeypatch,
) -> None:
    """Codex review, PR #719: without this hook a warm SourceAbiCache replays
    a stale SourceAbiTu after the castxml binary at the same path is
    upgraded/swapped, since CASTXML_EXTRACTOR_VERSION alone doesn't change.
    ``source_replay._extractor_version()`` folds ``cache_identity_extra()``
    into the D8 TU cache key when an extractor exposes one -- verify this
    extractor exposes exactly the probed castxml/bundled-Clang identity."""
    from abicheck.buildsource.source_extractors import castxml as castxml_mod

    castxml_mod._castxml_tool_version.cache_clear()
    monkeypatch.setattr(
        castxml_mod, "_castxml_tool_version", lambda _bin, *_args: "0.6.3; clang 17.0.6"
    )
    extractor = CastxmlSourceExtractor(castxml_bin="my-castxml")
    assert extractor.cache_identity_extra() == "0.6.3; clang 17.0.6"


def test_cache_identity_extra_falls_back_to_stat_when_version_unparseable(
    tmp_path: Path, monkeypatch
) -> None:
    """Codex review, PR #719, seventh round: two DIFFERENT broken/unparseable
    castxml installs at the same path must not both collapse to the same
    uninformative "" cache identity -- cache_identity_extra() falls back to
    the executable's own stat signature so a same-path swap between two
    such installs still changes the D8 TU cache key."""
    from abicheck.buildsource.source_extractors import castxml as castxml_mod

    castxml_mod._castxml_tool_version.cache_clear()
    monkeypatch.setattr(castxml_mod, "_castxml_tool_version", lambda *_a: "")
    exe = tmp_path / "castxml"
    exe.write_text("broken")
    monkeypatch.setattr(castxml_mod.shutil, "which", lambda _bin: str(exe))
    extractor = CastxmlSourceExtractor(castxml_bin=str(exe))

    identity1 = extractor.cache_identity_extra()
    assert identity1.startswith("stat:")
    assert identity1 != ""

    # A different broken executable at the SAME path must change identity.
    exe.write_text("a different broken binary, different size")
    identity2 = extractor.cache_identity_extra()
    assert identity2 != identity1


def test_cache_identity_extra_stays_empty_when_binary_unresolvable(
    monkeypatch,
) -> None:
    """No executable to stat at all (never found on PATH) -- no stat
    fallback is possible, so this stays "" rather than fabricating one."""
    from abicheck.buildsource.source_extractors import castxml as castxml_mod

    castxml_mod._castxml_tool_version.cache_clear()
    monkeypatch.setattr(castxml_mod, "_castxml_tool_version", lambda *_a: "")
    monkeypatch.setattr(castxml_mod.shutil, "which", lambda _bin: None)
    extractor = CastxmlSourceExtractor(castxml_bin="definitely-not-a-real-binary-xyz")
    assert extractor.cache_identity_extra() == ""


def test_parse_root_stamps_stat_fallback_compiler_version_when_probe_fails(
    tmp_path: Path, monkeypatch
) -> None:
    """Codex review, PR #719, eighth round: a failed/unparseable version probe
    must not persist a bare "" compiler_version into the fact_set -- two
    DIFFERENT broken castxml installs at the same path would then compare as
    recipe-agreeing (check_fact_compatibility sees identical producer/
    producer_version/compiler_version), letting an unchanged enum's
    underlying-type disagreement slip through as GENERATED_HEADER_CHANGED
    with no compiler_version_mismatch warning to explain it. Verify
    fact_set["compiler_version"] gets the SAME stat-based fallback identity
    cache_identity_extra() already uses, not the probe's bare ""."""
    from abicheck.buildsource.source_extractors import castxml as castxml_mod

    castxml_mod._castxml_tool_version.cache_clear()
    monkeypatch.setattr(castxml_mod, "_castxml_tool_version", lambda *_a: "")
    exe = tmp_path / "castxml"
    exe.write_text("broken")
    monkeypatch.setattr(castxml_mod.shutil, "which", lambda _bin: str(exe))

    extractor = CastxmlSourceExtractor(castxml_bin=str(exe))
    tu = extractor._parse_root(
        _root_with_one_type(), _cu(), public_header_roots=["foo.h"], target_id=""
    )
    assert tu.fact_set["compiler_version"].startswith("stat:")
    assert tu.fact_set["compiler_version"] == extractor.cache_identity_extra()

    # A different broken executable at the SAME path must persist a
    # different compiler_version, so two independently-collected baselines
    # against different bundled frontends aren't silently treated as
    # recipe-comparable.
    exe.write_text("a different broken binary, different size")
    tu2 = extractor._parse_root(
        _root_with_one_type(), _cu(), public_header_roots=["foo.h"], target_id=""
    )
    assert tu2.fact_set["compiler_version"] != tu.fact_set["compiler_version"]


def test_parse_root_stamps_msvc_compiler_family_for_msvc_compiler_binary() -> None:
    from xml.etree.ElementTree import Element, SubElement

    root = Element("GCC_XML")
    SubElement(root, "File", id="f1", name="foo.h")

    extractor = CastxmlSourceExtractor(compiler_binary="cl.exe")
    tu = extractor._parse_root(
        root, _cu(), public_header_roots=["foo.h"], target_id="target://libfoo"
    )
    assert tu.fact_set["compiler_family"] == "msvc"


def test_parse_root_compiler_version_ignores_resolved_emulated_compiler_path(
    tmp_path: Path, monkeypatch
) -> None:
    """Codex review, PR #719, follow-up round three: a prior round of this
    fix folded a stat signature of the resolved EMULATED compiler
    (`cc_bin`) into `compiler_version` -- a real regression, not a fix
    (Codex review caught it): those stat fields are filesystem-local, so
    two TUs in ONE surface resolved to DIFFERENT but same-toolchain drivers
    (`gcc` for a `.c` TU, `g++` for a `.cpp` TU -- an entirely ordinary
    mixed-language build) got different suffixes purely from being
    different files on disk, tripping the D8/fact_set-inconsistency gate
    for a perfectly healthy surface. Reverted; verify `compiler_version`
    depends ONLY on the castxml probe/identity, not on which compiler
    binary a given compile unit happens to resolve to."""
    from abicheck.buildsource.source_extractors import castxml as castxml_mod

    castxml_mod._castxml_tool_version.cache_clear()
    monkeypatch.setattr(castxml_mod, "_castxml_tool_version", lambda *_a: "0.6.3")
    gcc = tmp_path / "gcc"
    gpp = tmp_path / "g++"
    gcc.write_text("a fake gcc, different size than g++")
    gpp.write_text("a fake g++")

    extractor = CastxmlSourceExtractor()
    tu_c = extractor._parse_root(
        _root_with_one_type(),
        _cu(argv=[str(gcc), "-c", "foo.c"]),
        public_header_roots=["foo.h"],
        target_id="",
    )
    tu_cpp = extractor._parse_root(
        _root_with_one_type(),
        _cu(argv=[str(gpp), "-c", "foo.cpp"]),
        public_header_roots=["foo.h"],
        target_id="",
    )
    assert tu_c.fact_set["compiler_version"] == "0.6.3"
    assert tu_cpp.fact_set["compiler_version"] == "0.6.3"
    assert "cc:" not in tu_c.fact_set["compiler_version"]


def test_two_castxml_producer_versions_suppress_content_comparison() -> None:
    """End-to-end through fact_set.check_fact_compatibility: a castxml TU
    stamped under one CASTXML_EXTRACTOR_VERSION and one stamped under a
    different version are no longer treated as unconditionally comparable —
    the concrete gap this whole fix closes."""
    from abicheck.buildsource.fact_set import check_fact_compatibility

    old_fs = dict(
        CastxmlSourceExtractor()
        ._parse_root(
            _root_with_one_type(), _cu(), public_header_roots=["foo.h"], target_id=""
        )
        .fact_set
    )
    new_fs = dict(old_fs)
    new_fs["producer_version"] = "9.9"  # simulates an extractor upgrade
    compat = check_fact_compatibility(old_fs, new_fs)
    assert not compat.structured_content_comparable
    # Removal detection (existence, not content) is unaffected -- both sides
    # still agree on the same fact_set name/version contract.
    assert compat.structured_facts_comparable


def test_legacy_unstamped_castxml_baseline_suppresses_content_comparison() -> None:
    """The literal transition case (Codex review, PR #719, second round): an
    already-persisted L4 baseline predates this PR entirely, so it carries NO
    fact_set at all (``{}``) -- not merely an older CASTXML_EXTRACTOR_VERSION
    stamp. Compared against a freshly re-collected new side that DOES stamp
    one, content comparison must still be suppressed; the asymmetric-absence
    case is not the same as the "neither side ever stamped one" forward-compat
    case."""
    from abicheck.buildsource.fact_set import check_fact_compatibility

    new_fs = dict(
        CastxmlSourceExtractor()
        ._parse_root(
            _root_with_one_type(), _cu(), public_header_roots=["foo.h"], target_id=""
        )
        .fact_set
    )
    compat = check_fact_compatibility({}, new_fs)
    assert not compat.structured_content_comparable
    assert compat.structured_facts_comparable


def _root_with_one_type() -> object:
    from xml.etree.ElementTree import Element, SubElement

    root = Element("GCC_XML")
    SubElement(root, "File", id="f1", name="foo.h")
    SubElement(root, "FundamentalType", id="t_int", name="int")
    SubElement(root, "Location", id="loc1", file="f1", line="3")
    SubElement(
        root, "Class", id="c1", name="Widget", size="64", align="64", location="loc1"
    )
    return root


# -- end-to-end via real castxml (integration) -------------------------------


@pytest.mark.integration
def test_castxml_extractor_end_to_end(tmp_path: Path) -> None:
    extractor = CastxmlSourceExtractor()
    if not extractor.available():
        pytest.skip("castxml not installed")
    header = tmp_path / "foo.h"
    header.write_text(
        textwrap.dedent(
            """
            #ifndef FOO_H
            #define FOO_H
            struct Widget { int a; int b; };
            int add(int x, int y);
            const int kAnswer = 42;
            #endif
            """
        )
    )
    src = tmp_path / "foo.cpp"
    src.write_text('#include "foo.h"\n')
    cu = CompileUnit(
        id="cu://foo.cpp",
        source=str(src),
        language="CXX",
        standard="c++17",
    )
    tu = extractor.extract(
        cu, public_header_roots=[str(header)], target_id="target://libfoo"
    )
    names = {e.qualified_name for e in tu.all_entities()}
    assert any("add" in n for n in names)
    assert any("Widget" in n for n in names)
    # The public const is captured with its value (enables constexpr_value_changed).
    consts = {e.qualified_name: e.value for e in tu.constexpr_values}
    assert any("kAnswer" in k for k in consts)
    # Codex review, PR #719: a real castxml install resolves a non-empty
    # compiler_version, not the "" default a probe failure would leave.
    assert tu.fact_set["compiler_version"] != ""


def test_entity_from_typedef_carries_provenance() -> None:
    from abicheck.buildsource.source_extractors.base import entity_from_typedef

    plain = entity_from_typedef("handle_t", "int32_t", source_header="include/foo.h")
    assert plain.kind == "typedef" and plain.value == "int32_t"
    assert plain.source_location.path == "include/foo.h"
    assert plain.source_location.origin == "PUBLIC_HEADER"
    assert plain.visibility == "public_header"

    gen = entity_from_typedef(
        "cfg_t", "long", source_header="gen/cfg.h", generated=True
    )
    assert gen.source_location.origin == "GENERATED"
    assert gen.visibility == "generated"


def test_castxml_parse_public_typedefs_scopes_to_public_headers() -> None:
    from xml.etree.ElementTree import Element, SubElement

    from abicheck.dumper_castxml import _CastxmlParser

    def _set(parent, tag, **attrs):
        el = SubElement(parent, tag)
        for k, v in attrs.items():
            el.set(k, v)
        return el

    root = Element("CastXML")
    _set(root, "File", id="f1", name="/inc/api.h")
    _set(root, "File", id="f2", name="/src/detail.h")
    _set(root, "FundamentalType", id="_int", name="int")
    _set(root, "Typedef", id="_t1", name="handle_t", type="_int", file="f1", line="4")
    # Private header → filtered out of the public-typedef set.
    _set(root, "Typedef", id="_t2", name="secret_t", type="_int", file="f2", line="9")

    parser = _CastxmlParser(root, set(), set(), public_header_paths=["/inc/api.h"])
    tds = parser.parse_public_typedefs()
    assert tds == {"handle_t": "int"}
    assert parser.parse_public_typedef_headers()["handle_t"] == "/inc/api.h"


# -- strip_launchers (ADR-030 D2 shared argv helper) --------------------------


def test_strip_launchers_drops_bare_launcher() -> None:
    from abicheck.buildsource.source_extractors._argv import strip_launchers

    assert strip_launchers(["ccache", "clang++", "-c", "foo.cpp"]) == [
        "clang++", "-c", "foo.cpp",
    ]


def test_strip_launchers_skips_ccache_config_overrides() -> None:
    # ccache's own documented invocation form: `ccache KEY=VALUE ... compiler
    # [compiler options]` (ccache manual, "Configuration" section) — a bare
    # KEY=VALUE token here is a per-invocation config override, not the
    # compiler, and must be skipped too.
    from abicheck.buildsource.source_extractors._argv import strip_launchers

    assert strip_launchers(
        ["ccache", "compiler_check=content", "icpx", "-c", "foo.cpp"]
    ) == ["icpx", "-c", "foo.cpp"]
    assert strip_launchers(
        [
            "ccache",
            "debug=true",
            'compiler_check="%compiler% --version"',
            "gcc",
            "-c",
            "foo.c",
        ]
    ) == ["gcc", "-c", "foo.c"]


def test_strip_launchers_chained_launchers_with_config_overrides() -> None:
    from abicheck.buildsource.source_extractors._argv import strip_launchers

    assert strip_launchers(
        ["ccache", "compiler_check=content", "distcc", "gcc", "-c", "foo.c"]
    ) == ["gcc", "-c", "foo.c"]


def test_strip_launchers_no_config_overrides_for_non_launcher_command() -> None:
    # A KEY=VALUE-looking token that never follows a recognized launcher name
    # must not be treated as a config override.
    from abicheck.buildsource.source_extractors._argv import strip_launchers

    argv = ["FOO=bar", "gcc", "-c", "foo.c"]
    assert strip_launchers(argv) == argv
