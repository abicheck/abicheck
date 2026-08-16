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

"""PR D (plan "PR 3B") — build-context completeness for the P0.3 L3->L2 fold.

Two gaps, both previously recorded in the root ``AGENTS.md``, both closed
here. Each has three layers of coverage: the primitive on its own, the
derived L2 context it feeds, and the real ``collect_inline_pack``-backed
entry point every ``dump``/``scan``/``compare`` resolver actually calls
(``l2_seed.seed_includes_and_fold_compile_context``).

1. **Forced pre-includes** (``-include``/``-imacros``/``/FI``). A build that
   forces a macro-controlling header in parses its own headers with that
   header's macros already defined; the derived L2 context never carried it,
   so the header parse saw a materially different translation unit while
   still reporting a real match and stamping ``parsed_with_build_context``.

   ``TestReplayStillEmitsForcedIncludesExactlyOnce`` is the load-bearing
   negative half: it pins why the fix that ``ABI_RELEVANT_FLAG_PREFIXES``'s
   own comment used to propose — capture forced includes into
   ``CompileUnit.abi_relevant_flags`` — is wrong. ``replay_extra_flags``
   both carries that list *and* independently re-scans raw argv for the same
   tokens, so L4 replay would have emitted every forced include twice.

2. **Matched compile-unit selection.** The L2 include-dir seed gathered dirs
   from every ``CompileUnit`` in the build rather than the one(s) that
   actually compile the headers being parsed, so an unrelated TU's own
   generated-header directory could shadow the matched TU's own colliding
   header.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.header_compile_context import resolve_header_compile_context
from abicheck.errors import HeaderCompileContextAmbiguousError
from abicheck.header_utils import forced_include_operands


def _cu(**kwargs: object) -> CompileUnit:
    defaults: dict[str, object] = dict(
        id=f"cu://{kwargs.get('source', 'x')}",
        source="",
        directory="",
        target_id="",
        compiler="",
        language="CXX",
        standard="",
        defines={},
        undefines=[],
        include_paths=[],
        system_include_paths=[],
        sysroot=None,
        target_triple="",
        abi_relevant_flags=[],
        argv=[],
    )
    defaults.update(kwargs)
    return CompileUnit(**defaults)  # type: ignore[arg-type]


def _tokens(result: Any) -> list[str]:
    assert result.context is not None
    return list(result.context.gcc_option_tokens)


def _seed_and_fold(**overrides: Any) -> Any:
    from abicheck.buildsource.l2_seed import seed_includes_and_fold_compile_context

    kwargs: dict[str, Any] = dict(
        headers=[],
        includes=[],
        sources=None,
        build_info=None,
        build_config=None,
        build_query=None,
        build_compile_db=None,
        collect_mode="source-target",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options=None,
        gcc_option_tokens=(),
        sysroot=None,
        nostdinc=False,
        frontend="auto",
        frontend_context="host",
        lang="c++",
        lang_explicit=False,
        pending_cleanups=[],
    )
    kwargs.update(overrides)
    return seed_includes_and_fold_compile_context(**kwargs)


def _write_compile_db(tmp_path: Path, entries: list[tuple[Path, list[str]]]) -> None:
    (tmp_path / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": str(src),
                    "arguments": ["c++", "-c", str(src), "-o", f"{src.stem}.o", *args],
                }
                for src, args in entries
            ]
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 1a. The shared recognizer
# ---------------------------------------------------------------------------


class TestForcedIncludeRecognizer:
    """``header_utils.forced_include_operands`` — one vocabulary, two views.

    The same matchers the L4 replay path reaches through
    ``source_extractors._argv``, so the set L2 renders can never drift from
    the set L4 replays.
    """

    def test_gnu_separate_and_joined_spellings(self) -> None:
        argv = ["c++", "-include", "cfg.h", "-imacrosdefs.h", "-c", "a.cpp"]
        assert forced_include_operands(argv, msvc=False) == [
            ("-include", "cfg.h"),
            ("-imacros", "defs.h"),
        ]

    def test_include_pch_is_recognized_only_in_its_separate_form(self) -> None:
        # clang has no joined ``-include-pchfile`` spelling, so the joined
        # branch must not read one -- ``-include-pchfoo`` reaches the joined
        # ``-include`` branch with an option-shaped "operand" (``-pchfoo``),
        # which is not a real file and is dropped.
        assert forced_include_operands(
            ["c++", "-include-pch", "p.pch"], msvc=False
        ) == [("-include-pch", "p.pch")]
        assert forced_include_operands(["c++", "-include-pchfoo"], msvc=False) == []

    def test_msvc_family_is_gated_on_the_resolved_dialect(self) -> None:
        argv = ["clang-cl", "/FIcfg.h", "/FI", "other.h", "-FIthird.h"]
        assert forced_include_operands(argv, msvc=True) == [
            ("/FI", "cfg.h"),
            ("/FI", "other.h"),
            ("/FI", "third.h"),
        ]
        # A GNU command's ``-F``-family flag is never read as a forced include.
        assert forced_include_operands(argv, msvc=False) == []

    def test_a_dangling_separate_option_consumes_nothing(self) -> None:
        assert (
            forced_include_operands(["c++", "-c", "a.cpp", "-include"], msvc=False)
            == []
        )


class TestReplayStillEmitsForcedIncludesExactlyOnce:
    """Why forced includes deliberately never enter ``abi_relevant_flags``.

    ``replay_extra_flags`` carries ``abi_relevant_flags`` through *and*
    independently re-scans raw ``argv`` for forced-include tokens, without
    consulting the ``seen`` set the first pass builds. Capturing a forced
    include into that list — the fix ``ABI_RELEVANT_FLAG_PREFIXES``'s comment
    used to propose — would therefore double-emit it on every L4 replay
    command: a silent double inclusion that a header without include guards
    turns into a hard redefinition error.
    """

    def test_argv_sourced_forced_include_is_emitted_once(self) -> None:
        from abicheck.buildsource.source_extractors._argv import replay_extra_flags

        cu = _cu(argv=["c++", "-include", "cfg.h", "-c", "a.cpp"])
        assert replay_extra_flags(cu, [], "gnu").count("-include") == 1

    def test_forced_includes_are_absent_from_abi_relevant_flags(self) -> None:
        from abicheck.buildsource.adapters.base import extract_abi_relevant_flags

        argv = ["c++", "-include", "cfg.h", "-imacros", "defs.h", "/FIw.h", "-fPIC"]
        assert extract_abi_relevant_flags(argv) == ["-fPIC"]


# ---------------------------------------------------------------------------
# 1b. Forced includes reach the derived L2 context
# ---------------------------------------------------------------------------


class TestForcedIncludesReachTheDerivedContext:
    def _matched_unit(self, tmp_path: Path, args: list[str]) -> BuildEvidence:
        header = tmp_path / "widget.h"
        header.write_text("struct Widget { int x; };\n", encoding="utf-8")
        src = tmp_path / "widget.cpp"
        src.write_text('#include "widget.h"\n', encoding="utf-8")
        return BuildEvidence(
            compile_units=[
                _cu(
                    source=str(src),
                    directory=str(tmp_path),
                    argv=["c++", "-c", str(src), *args],
                )
            ]
        )

    def test_relative_operand_resolves_against_the_compile_units_directory(
        self, tmp_path: Path
    ) -> None:
        # The compile command's own cwd is the build's, never abicheck's, so a
        # relative operand naming a real file must be pinned to it.
        (tmp_path / "cfg").mkdir()
        forced = tmp_path / "cfg" / "config.h"
        forced.write_text("#define BUILD_ONLY 1\n", encoding="utf-8")
        ev = self._matched_unit(tmp_path, ["-include", "cfg/config.h"])

        toks = _tokens(resolve_header_compile_context(ev, [tmp_path / "widget.h"]))
        assert toks[-2:] == ["-include", forced.as_posix()]

    def test_bare_operand_with_no_file_on_disk_stays_relative(
        self, tmp_path: Path
    ) -> None:
        # GCC documents a two-stage lookup for ``-include``: the preprocessor's
        # working directory first, then the -iquote/-I chain. Pinning a
        # generated header the build finds through its -I chain to a
        # non-existent ``<directory>/config.h`` would turn a header that would
        # have been found into a hard "file not found".
        ev = self._matched_unit(tmp_path, ["-include", "generated_config.h"])

        toks = _tokens(resolve_header_compile_context(ev, [tmp_path / "widget.h"]))
        assert toks[-2:] == ["-include", "generated_config.h"]

    def test_imacros_is_rendered_under_its_own_spelling(self, tmp_path: Path) -> None:
        # -imacros takes only the macros, not the declarations, so it must not
        # be flattened into -include.
        ev = self._matched_unit(tmp_path, ["-imacros", "defs.h"])

        assert _tokens(resolve_header_compile_context(ev, [tmp_path / "widget.h"]))[
            -2:
        ] == ["-imacros", "defs.h"]

    def test_msvc_forced_include_renders_as_the_gnu_spelling(
        self, tmp_path: Path
    ) -> None:
        # Everything this module renders is GNU-style regardless of the
        # recorded command's dialect (-D/-I/--sysroot= already are), because
        # the consumer is always a GNU-driver castxml/clang invocation.
        header = tmp_path / "widget.h"
        header.write_text("struct Widget { int x; };\n", encoding="utf-8")
        src = tmp_path / "widget.cpp"
        src.write_text('#include "widget.h"\n', encoding="utf-8")
        ev = BuildEvidence(
            compile_units=[
                _cu(
                    source=str(src),
                    directory=str(tmp_path),
                    argv=["clang-cl", "/c", str(src), "/FIconfig.h"],
                )
            ]
        )

        assert _tokens(resolve_header_compile_context(ev, [header]))[-2:] == [
            "-include",
            "config.h",
        ]

    @pytest.mark.parametrize("driver", ["dpcpp-cl", "clang-cl-20", "clang-cl-20.1.exe"])
    def test_cl_mode_driver_spelled_with_gnu_dash_c_still_renders_fi(
        self, tmp_path: Path, driver: str
    ) -> None:
        """Codex review: the dialect vocabulary had drifted between the layers.

        `adapters.base._is_msvc_command` listed only `cl`/`clang-cl` while the
        L4 replay path also knew `dpcpp-cl` and version-suffixed drivers. A
        CL-mode command spelled with GNU `-c` rather than `/c` therefore read
        as GNU dialect here — silently dropping its `/FI` from the derived L2
        context while L4 replayed it correctly. Both now share one vocabulary.
        """
        header = tmp_path / "widget.h"
        header.write_text("struct Widget { int x; };\n", encoding="utf-8")
        src = tmp_path / "widget.cpp"
        src.write_text('#include "widget.h"\n', encoding="utf-8")
        ev = BuildEvidence(
            compile_units=[
                _cu(
                    source=str(src),
                    directory=str(tmp_path),
                    argv=[driver, "-c", str(src), "/FIconfig.h"],
                )
            ]
        )

        assert _tokens(resolve_header_compile_context(ev, [header]))[-2:] == [
            "-include",
            "config.h",
        ]

    def test_include_pch_is_not_rendered(self, tmp_path: Path) -> None:
        # A .pch is locked to the exact compiler build that produced it, and
        # L2 parses with castxml's bundled clang or the host clang -- replaying
        # one is a hard parse failure far more often than a fidelity win. L4
        # replay, which uses the real compiler, still carries it.
        ev = self._matched_unit(tmp_path, ["-include-pch", "everything.pch"])

        assert "-include-pch" not in _tokens(
            resolve_header_compile_context(ev, [tmp_path / "widget.h"])
        )

    def test_forced_include_lands_after_the_include_search_it_resolves_through(
        self, tmp_path: Path
    ) -> None:
        ev = self._matched_unit(tmp_path, ["-include", "generated_config.h", "-Igen"])
        ev.compile_units[0].include_paths = ["gen"]

        toks = _tokens(resolve_header_compile_context(ev, [tmp_path / "widget.h"]))
        assert toks.index("-include") > toks.index("-I")


class TestForcedIncludeAmbiguity:
    """A forced include was invisible to the ambiguity signature entirely, so
    two units forcing *different* macro-controlling headers in silently
    applied whichever grouped first."""

    def _two_units(self, tmp_path: Path, a: list[str], b: list[str]) -> BuildEvidence:
        header = tmp_path / "widget.h"
        header.write_text("struct Widget { int x; };\n", encoding="utf-8")
        units = []
        for name, args in (("a", a), ("b", b)):
            src = tmp_path / f"{name}.cpp"
            src.write_text('#include "widget.h"\n', encoding="utf-8")
            units.append(
                _cu(
                    id=f"cu://{name}",
                    source=str(src),
                    directory=str(tmp_path),
                    argv=["c++", "-c", str(src), *args],
                )
            )
        return BuildEvidence(compile_units=units)

    def test_different_forced_headers_fail_closed(self, tmp_path: Path) -> None:
        ev = self._two_units(
            tmp_path, ["-include", "debug_config.h"], ["-include", "release_config.h"]
        )
        with pytest.raises(HeaderCompileContextAmbiguousError) as excinfo:
            resolve_header_compile_context(ev, [tmp_path / "widget.h"])
        # The message must name the dimension that actually differs: every
        # other field it prints is identical here, so without this the user
        # sees two apparently-equal rows and no reason for the error.
        message = str(excinfo.value)
        assert "debug_config.h" in message
        assert "release_config.h" in message

    def test_one_unit_forcing_and_one_not_is_also_a_disagreement(
        self, tmp_path: Path
    ) -> None:
        # "This one forces nothing" is half of the disagreement, so the
        # forced-include field is shown for both rows once either has one --
        # an omitted field would read as agreement.
        ev = self._two_units(tmp_path, ["-include", "debug_config.h"], [])
        with pytest.raises(HeaderCompileContextAmbiguousError) as excinfo:
            resolve_header_compile_context(ev, [tmp_path / "widget.h"])
        assert str(excinfo.value).count("forced_includes=") == 2

    def test_equivalent_spellings_of_the_same_forced_header_still_agree(
        self, tmp_path: Path
    ) -> None:
        # The signature compares the *rendered* tokens, so a separate and a
        # joined spelling of the same header are not a disagreement.
        ev = self._two_units(tmp_path, ["-include", "config.h"], ["-includeconfig.h"])
        result = resolve_header_compile_context(ev, [tmp_path / "widget.h"])
        assert result.matched_unit_count == 2
        assert _tokens(result)[-2:] == ["-include", "config.h"]


class TestForcedIncludeCacheKey:
    """A forced-include header reaches the parse only as an opaque
    ``gcc_option_tokens`` string, so the AST cache key's directory-mtime
    hashing never covered it — editing the one macro-controlling input the
    parse depends on most would reuse a stale AST."""

    def test_union_covers_both_include_search_and_forced_include_paths(self) -> None:
        from abicheck.header_utils import (
            cache_relevant_operand_paths,
            include_operand_dirs,
        )

        toks = ("-I", "/proj/include", "-include", "/proj/gen/config.h")
        assert include_operand_dirs(toks) == (Path("/proj/include"),)
        assert set(cache_relevant_operand_paths(toks)) == {
            Path("/proj/include"),
            # The forced header itself, *and* its directory: the file because
            # the cache key's directory walk is suffix-filtered and would miss
            # it outright for a non-header suffix; the directory so a
            # neighbouring header it pulls in relatively is covered too, since
            # that directory need not be on the include search path.
            Path("/proj/gen/config.h"),
            Path("/proj/gen"),
        }

    def test_bare_forced_operand_contributes_only_the_file(self) -> None:
        from abicheck.header_utils import forced_include_operand_paths

        # No directory part to add: its resolution depends on the include
        # search, whose dirs include_operand_dirs already covers.
        assert forced_include_operand_paths(("-include", "config.h")) == (
            Path("config.h"),
        )

    def test_non_header_suffixed_forced_file_is_itself_hashed(
        self, tmp_path: Path
    ) -> None:
        """Codex review: the cache key's directory walk is suffix-filtered.

        `-imacros settings.def` (and an extensionless `-include generated/config`)
        carry suffixes `CACHE_HEADER_SUFFIXES` does not list, so hashing only the
        parent directory left an edit to the one macro-controlling input the
        parse depends on most invisible to the key.
        """
        from abicheck.dumper_ast_config import _cache_key
        from abicheck.header_utils import cache_relevant_operand_paths

        macros = tmp_path / "settings.def"
        macros.write_text("#define WIDTH 32\n", encoding="utf-8")
        header = tmp_path / "widget.h"
        header.write_text("struct Widget { int x; };\n", encoding="utf-8")
        hash_paths = list(cache_relevant_operand_paths(("-imacros", str(macros))))
        assert macros in hash_paths

        def key() -> str:
            return _cache_key([header], [], "c++", extra_hash_dirs=tuple(hash_paths))

        before = key()
        # A real edit to the forced file, with its mtime advanced the way a
        # rewrite does.
        macros.write_text("#define WIDTH 64\n", encoding="utf-8")
        os.utime(macros, (0, 0))
        assert key() != before

    def test_an_unresolvable_forced_include_degrades_instead_of_raising(
        self, tmp_path: Path
    ) -> None:
        # Not hypothetical: `_forced_include_flags` deliberately leaves an
        # operand relative when it does not resolve against the compile unit's
        # own directory (the include search is expected to find it), so the
        # cache key routinely sees a path that does not exist from here. It
        # must contribute its path string and move on, not raise.
        from abicheck.dumper_ast_config import _cache_key

        header = tmp_path / "widget.h"
        header.write_text("struct Widget { int x; };\n", encoding="utf-8")
        missing = tmp_path / "nope" / "generated_config.h"

        assert _cache_key(
            [header], [], "c++", extra_hash_dirs=(missing,)
        ) != _cache_key([header], [], "c++", extra_hash_dirs=())

    def test_pe_macho_parse_derives_the_same_hash_paths_from_its_own_tokens(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex review: PE/Mach-O must not disagree with ELF about staleness.

        `dump_native_binary`/`service.run_dump` exposes no `extra_hash_dirs`
        hook, so the caller's own derived-dirs return is discarded on that
        path. It does not need one: the merged L3 context arrives here as
        `compile=`, so the parse derives the identical set from the very
        tokens those dirs came from — the same fold `service._dump_elf` and
        `service._attach_header_graph` already apply.
        """
        from abicheck import service_header_scoped
        from abicheck.service_scan import CompileContext

        gen = tmp_path / "gen"
        gen.mkdir()
        forced = gen / "config.h"
        forced.write_text("#define BUILD_ONLY 1\n", encoding="utf-8")
        header = tmp_path / "widget.h"
        header.write_text("struct Widget { int x; };\n", encoding="utf-8")

        seen: dict[str, Any] = {}

        def fake_dump_pe(*args: Any, **kwargs: Any) -> Any:
            seen["extra_hash_dirs"] = kwargs.get("extra_hash_dirs")
            raise RuntimeError("stop after the cache-key inputs are known")

        monkeypatch.setattr("abicheck.dumper._dump_pe", fake_dump_pe, raising=False)
        service_header_scoped._try_header_scoped_dump(
            "pe",
            tmp_path / "lib.dll",
            [header],
            [],
            "1.0",
            "c++",
            compile=CompileContext(
                gcc_option_tokens=("-I", str(gen), "-include", str(forced))
            ),
        )
        hashed = set(seen["extra_hash_dirs"])
        assert forced in hashed  # the forced file itself
        assert gen in hashed  # and the include-search dir

    def test_derived_forced_include_dir_reaches_the_callers_hash_dirs(
        self, tmp_path: Path
    ) -> None:
        gen = tmp_path / "gen"
        gen.mkdir()
        forced = gen / "config.h"
        forced.write_text("#define BUILD_ONLY 1\n", encoding="utf-8")
        header = tmp_path / "widget.h"
        header.write_text("struct Widget { int x; };\n", encoding="utf-8")
        src = tmp_path / "widget.cpp"
        src.write_text('#include "widget.h"\n', encoding="utf-8")
        _write_compile_db(tmp_path, [(src, ["-include", str(forced)])])

        _incs, applied, ctx, dirs = _seed_and_fold(
            headers=[header], sources=tmp_path, pending_cleanups=[]
        )
        assert applied is True
        assert "-include" in ctx.gcc_option_tokens
        assert gen in dirs


# ---------------------------------------------------------------------------
# 2. Matched compile-unit selection for the include-dir seed
# ---------------------------------------------------------------------------


class TestIncludeSeedIsRestrictedToMatchedUnits:
    def _project(self, tmp_path: Path, *, matching: bool) -> Path:
        """A two-TU build: one TU compiles the public header, one does not.

        Each carries its own private include dir, and both hold a colliding
        ``config.h`` — the shape where seeding from every unit lets an
        unrelated TU's generated header shadow the matched TU's own.
        """
        for name in ("matched_inc", "unrelated_inc"):
            d = tmp_path / name
            d.mkdir()
            (d / "config.h").write_text(f"/* {name} */\n", encoding="utf-8")
        header = tmp_path / "widget.h"
        header.write_text("struct Widget { int x; };\n", encoding="utf-8")

        matched = tmp_path / "widget.cpp"
        matched.write_text(
            '#include "widget.h"\n' if matching else "int f() { return 0; }\n",
            encoding="utf-8",
        )
        unrelated = tmp_path / "other.cpp"
        unrelated.write_text("int g() { return 0; }\n", encoding="utf-8")
        _write_compile_db(
            tmp_path,
            [
                (matched, ["-I", str(tmp_path / "matched_inc")]),
                (unrelated, ["-I", str(tmp_path / "unrelated_inc")]),
            ],
        )
        return header

    def test_only_the_matched_units_include_dirs_are_seeded(
        self, tmp_path: Path
    ) -> None:
        header = self._project(tmp_path, matching=True)

        incs, applied, _ctx, _dirs = _seed_and_fold(
            headers=[header], sources=tmp_path, pending_cleanups=[]
        )
        seeded = {str(p) for p in incs}
        assert applied is True
        assert str(tmp_path / "matched_inc") in seeded
        assert str(tmp_path / "unrelated_inc") not in seeded

    def test_seed_falls_back_to_every_unit_when_nothing_matched(
        self, tmp_path: Path
    ) -> None:
        # The case this seed was built for: a public header the compile DB
        # does not cover, reaching into a dependency SDK. There is no narrower
        # set to prefer, so restricting must not silently empty the seed.
        header = self._project(tmp_path, matching=False)

        incs, applied, _ctx, _dirs = _seed_and_fold(
            headers=[header], sources=tmp_path, pending_cleanups=[]
        )
        seeded = {str(p) for p in incs}
        assert applied is False
        assert str(tmp_path / "matched_inc") in seeded
        assert str(tmp_path / "unrelated_inc") in seeded

    def test_resolution_exposes_the_units_not_only_their_count(
        self, tmp_path: Path
    ) -> None:
        header = tmp_path / "widget.h"
        header.write_text("struct Widget { int x; };\n", encoding="utf-8")
        src = tmp_path / "widget.cpp"
        src.write_text('#include "widget.h"\n', encoding="utf-8")
        other = tmp_path / "other.cpp"
        other.write_text("int g() { return 0; }\n", encoding="utf-8")
        ev = BuildEvidence(
            compile_units=[
                _cu(id="cu://widget", source=str(src), directory=str(tmp_path)),
                _cu(id="cu://other", source=str(other), directory=str(tmp_path)),
            ]
        )

        result = resolve_header_compile_context(ev, [header])
        assert [cu.id for cu in result.matched_units] == ["cu://widget"]
        # The historical read view stays exactly consistent with it.
        assert result.matched_unit_count == len(result.matched_units) == 1
