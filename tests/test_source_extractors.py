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
        stderr = ""

    def _fake_run(cmd: list[str], **kw: object) -> _Result:
        captured["cwd"] = kw.get("cwd")
        out = cmd[cmd.index("-o") + 1]
        Path(out).write_text('<GCC_XML><File id="f1" name="foo.h"/></GCC_XML>')
        return _Result()

    monkeypatch.setattr(castxml_mod.subprocess, "run", _fake_run)
    cu = _cu(source="src/foo.cpp", directory=str(tmp_path))
    tu = extractor.extract(cu, public_header_roots=["foo.h"], target_id="target://x")
    assert captured["cwd"] == str(tmp_path)
    assert tu.extractor["name"] == "castxml-source"


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
        stderr = ""

    def _fake_run(cmd: list[str], **kw: object) -> _Result:
        captured["cmd"] = cmd
        captured["cwd"] = kw.get("cwd")
        out = cmd[cmd.index("-o") + 1]
        Path(out).write_text('<GCC_XML><File id="f1" name="foo.h"/></GCC_XML>')
        return _Result()

    monkeypatch.setattr(castxml_mod.subprocess, "run", _fake_run)
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
        stderr = ""

    def _fake_run(cmd: list[str], **kw: object) -> _Result:
        captured["cmd"] = cmd
        out = cmd[cmd.index("-o") + 1]
        Path(out).write_text('<GCC_XML><File id="f1" name="foo.h"/></GCC_XML>')
        return _Result()

    monkeypatch.setattr(castxml_mod.subprocess, "run", _fake_run)
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
            name="Widget", mangled="Widget", return_type="void",
            params=[Param("x", "int")],
        )
    )
    ctor_dbl = entity_from_function(
        Function(
            name="Widget", mangled="Widget", return_type="void",
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


def test_entity_from_typedef_carries_provenance() -> None:
    from abicheck.buildsource.source_extractors.base import entity_from_typedef

    plain = entity_from_typedef("handle_t", "int32_t", source_header="include/foo.h")
    assert plain.kind == "typedef" and plain.value == "int32_t"
    assert plain.source_location.path == "include/foo.h"
    assert plain.source_location.origin == "PUBLIC_HEADER"
    assert plain.visibility == "public_header"

    gen = entity_from_typedef("cfg_t", "long", source_header="gen/cfg.h", generated=True)
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
