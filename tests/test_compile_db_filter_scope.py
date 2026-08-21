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

"""``--compile-db-filter`` must mean the same thing in every layer that
narrows a compile database by it.

Three layers do: ``build_context`` (the legacy ``-p`` auto-match feeding the
L2 flags), ``header_conditionals`` (the ADR-039 build-context collector), and
-- since CLI cleanup phase two's PR 3A investigation -- ``buildsource.
header_compile_context`` (the P0.3 L3→L2 fold). The third was the gap, and it
was not a silent one: the fold's own ambiguity error names
``--compile-db-filter`` as a way to narrow the input, so a user who followed
that advice got the identical error back. Reproduced end to end before the
fix (see ``TestDumpCliHonorsTheFilterInTheFold``), which is why the guard
here is a real dump rather than only a unit test on the predicate.

Two lessons from this repo's own history shape what is tested and how. First,
"a second copy of an existing primitive drifts from it silently, and the drift
shows up as the copy lacking a property the original was deliberately given"
(the MSVC-driver vocabulary, third finding on the root ``AGENTS.md``'s
forced-include entry) -- so the matching rules live in one function,
``build_context.source_matches_filter``, and ``TestOneSharedDefinition``
checks the layers actually agree rather than trusting that they were written
the same way. Second, a reusable narrowing primitive gets its contract stated
as invariants, not only exercised through its highest-level caller
(``AGENTS.md``, "Primitive-level property tests") -- hence
``TestFilterUnitsBySourceContract``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.build_context import source_matches_filter
from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.header_compile_context import (
    filter_units_by_source,
    resolve_header_compile_context,
)

_HAVE_GXX = shutil.which("g++") is not None
_HAVE_CLANG = shutil.which("clang") is not None


def _cu(source: str, directory: str = "", **kwargs: object) -> CompileUnit:
    defaults: dict[str, object] = dict(
        id=f"cu://{source}",
        source=source,
        directory=directory,
        language="CXX",
    )
    defaults.update(kwargs)
    return CompileUnit(**defaults)  # type: ignore[arg-type]


class TestFilterUnitsBySourceContract:
    """The narrowing primitive's contract, stated as invariants."""

    def test_no_filter_is_identity(self) -> None:
        units = [_cu("/w/a.cpp"), _cu("/w/b.cpp")]
        assert filter_units_by_source(units, None) is units
        assert filter_units_by_source(units, "") is units

    def test_narrows_to_the_matching_units_only(self) -> None:
        a, b = _cu("/w/a.cpp"), _cu("/w/b.cpp")
        assert list(filter_units_by_source([a, b], "*/a.cpp")) == [a]

    def test_result_is_order_preserving_and_a_subsequence(self) -> None:
        units = [_cu(f"/w/{n}.cpp") for n in ("a", "b", "c", "d")]
        got = list(filter_units_by_source(units, "*/[abc].cpp"))
        assert got == [u for u in units if u in got]

    def test_a_filter_matching_nothing_keeps_every_unit(self) -> None:
        """The conservative fallback ``build_context._filter_entries_by_glob``
        and ``header_conditionals.collect_build_context`` already apply: a
        glob matching no TU is far likelier a typo than a request to discard
        all real build evidence, and the caller reads "no units" as "no L3
        evidence at all"."""
        units = [_cu("/w/a.cpp"), _cu("/w/b.cpp")]
        assert list(filter_units_by_source(units, "*/nothing-here.cpp")) == units

    def test_a_unit_with_no_recorded_source_never_matches(self) -> None:
        """An adapter that recorded no ``source`` cannot be *selected* by a
        filter -- but, per the fallback above, a filter that then matches
        nothing at all still keeps it."""
        named, unnamed = _cu("/w/a.cpp"), _cu("")
        assert list(filter_units_by_source([named, unnamed], "*/a.cpp")) == [named]

    def test_relative_source_resolves_against_the_units_directory(self) -> None:
        unit = _cu("a.cpp", directory="/w/src")
        assert list(filter_units_by_source([unit], "/w/src/a.cpp")) == [unit]
        assert list(filter_units_by_source([unit], "a.cpp")) == [unit]

    def test_a_redacted_unit_still_matches_its_own_filename(self) -> None:
        """Codex review, P1: ``CompileDbAdapter`` (ADR-032 D7) redacts both
        ``source`` and ``directory`` to the identical ``~/...`` placeholder
        before either ever reaches this predicate. Treating the redacted
        ``source`` as an ordinary relative name and joining it onto the
        redacted ``directory`` produced ``~/proj/~/proj/a.cpp`` -- matching
        nothing, so every unit fell back into the result instead of the
        filter narrowing to the one named unit.
        """
        redacted = _cu("~/proj/a.cpp", directory="~/proj")
        other = _cu("~/proj/b.cpp", directory="~/proj")
        assert list(filter_units_by_source([redacted, other], "a.cpp")) == [redacted]
        assert list(filter_units_by_source([redacted, other], "*/a.cpp")) == [redacted]
        assert list(filter_units_by_source([redacted, other], "~/proj/a.cpp")) == [
            redacted
        ]

    def test_an_ordinary_relative_file_sharing_the_directory_name_still_joins(
        self,
    ) -> None:
        """Codex review, P2: a real ``CompileEntry`` semantics case, not a
        redaction one -- ``directory="build"``, ``file="build/a.cpp"`` (no
        redaction anywhere) is lexically indistinguishable from the
        redacted ``directory="~/proj"``, ``file="~/proj/a.cpp"`` shape the
        previous ``is_relative_to`` check was written for, but
        ``CompileEntry.from_dict()`` still joins it unconditionally --
        ``build/build/a.cpp``, not ``build/a.cpp``. Treating the coincidental
        prefix as "already anchored" (skip the join) silently diverged from
        that real semantics: a filter naming the correctly-joined spelling
        matched nothing under the old code, only the un-joined one.
        """
        coincidence = _cu("build/a.cpp", directory="build")
        other = _cu("build/b.cpp", directory="build")
        # The join CompileEntry.from_dict() actually performs.
        assert list(
            filter_units_by_source([coincidence, other], "build/build/a.cpp")
        ) == [coincidence]
        assert list(filter_units_by_source([coincidence, other], "*/build/a.cpp")) == [
            coincidence
        ]
        # The un-joined spelling still matches too (both readings are
        # tested, since neither can be ruled out from the strings alone --
        # see source_matches_filter's own docstring).
        assert list(filter_units_by_source([coincidence, other], "build/a.cpp")) == [
            coincidence
        ]

    def test_a_redacted_unit_matches_an_unredacted_absolute_filter(self) -> None:
        """Codex review, P1 (fresh evidence beyond the relative-filter case):
        an absolute ``--compile-db-filter`` (what a user would actually type
        -- their own real home path, never the redaction placeholder) shares
        no path segments with a redacted unit at all, so the
        ``is_relative_to`` fix above can't help -- both are anchors, neither
        a prefix of the other. Closed by expanding a leading ``~`` on *both*
        sides before comparing.
        """
        import os

        home = os.path.expanduser("~")
        redacted = _cu("~/proj/a.cpp", directory="~/proj")
        other = _cu("~/proj/b.cpp", directory="~/proj")
        absolute_filter = f"{home}/proj/a.cpp"
        assert list(filter_units_by_source([redacted, other], absolute_filter)) == [
            redacted
        ]

    def test_a_relative_directory_still_matches_an_absolute_filter(self) -> None:
        """Codex review, P1, fresh evidence: a relative ``directory`` (real
        ``compile_commands.json`` entries always give it absolute per the
        Clang compilation-database spec, but this scan doesn't enforce
        that) left the joined candidate relative too, so it could never
        match an absolute ``--compile-db-filter`` -- the spelling a user
        would naturally type, and the one ``CompileEntry.from_dict()``
        itself resolves to before this raw scan runs. Closed by also
        testing the CWD-resolved absolute form of a relative candidate.
        """
        cwd = Path.cwd()
        target = _cu("src/a.cpp", directory="build")
        other = _cu("src/b.cpp", directory="build")
        absolute_filter = str(cwd / "build" / "src" / "a.cpp")
        assert list(filter_units_by_source([target, other], absolute_filter)) == [
            target
        ]


class TestOneSharedDefinition:
    """The three layers must select the same units for the same filter.

    Checked by driving each layer's own entry point, not by asserting they
    call the same function -- an implementation detail a future refactor may
    legitimately change, unlike the property that matters.
    """

    @pytest.mark.parametrize(
        ("file", "directory", "pattern", "expected"),
        [
            ("/w/src/a.cpp", "/w", "/w/src/a.cpp", True),  # absolute
            ("/w/src/a.cpp", "/w", "src/*.cpp", True),  # directory-relative
            ("src/a.cpp", "/w", "/w/src/a.cpp", True),  # relative file, abs glob
            ("/w/src/a.cpp", "/w", "src/b.cpp", False),
            ("/w/src/a.cpp", None, "src/a.cpp", False),  # no dir to relativize
            ("~/proj/a.cpp", "~/proj", "a.cpp", True),  # redacted (ADR-032 D7)
            ("~/proj/a.cpp", "~/proj", "~/proj/a.cpp", True),  # redacted, full
            ("~/proj/a.cpp", "~/proj", "b.cpp", False),  # redacted, no match
        ],
    )
    def test_compile_unit_layer_matches_the_shared_predicate(
        self, file: str, directory: str | None, pattern: str, expected: bool
    ) -> None:
        unit = _cu(file, directory=directory or "")
        selected = list(filter_units_by_source([unit, _cu("/w/other.cpp")], pattern))
        assert (selected == [unit]) is expected
        assert source_matches_filter(file, directory, pattern) is expected

    def test_adr039_collector_layer_matches_the_shared_predicate(self) -> None:
        from abicheck.header_conditionals import _compile_entry_matches

        entry = {"file": "src/a.cpp", "directory": "/w"}
        assert _compile_entry_matches(entry, "/w/src/a.cpp") is True
        assert _compile_entry_matches(entry, "src/*.cpp") is True
        assert _compile_entry_matches(entry, "src/b.cpp") is False

    def test_collector_layer_rejects_a_non_string_file(self) -> None:
        from abicheck.header_conditionals import _compile_entry_matches

        assert _compile_entry_matches({"file": 7, "directory": "/w"}, "*") is False


class TestResolveHeaderCompileContextHonorsTheFilter:
    """The fold itself, at the level the CLI reaches it."""

    @staticmethod
    def _two_conflicting_units(tmp_path: Path) -> tuple[Path, BuildEvidence]:
        header = tmp_path / "api.h"
        header.write_text("struct S { int a; };\n", encoding="utf-8")
        units = []
        for name, macro in (("a", "WIDE"), ("b", "NARROW")):
            src = tmp_path / f"{name}.cpp"
            src.write_text('#include "api.h"\n', encoding="utf-8")
            units.append(
                _cu(
                    str(src),
                    directory=str(tmp_path),
                    defines={macro: "1"},
                    abi_relevant_flags=[f"-D{macro}=1"],
                )
            )
        return header, BuildEvidence(compile_units=units)

    def test_unfiltered_two_conflicting_units_stay_ambiguous(
        self, tmp_path: Path
    ) -> None:
        from abicheck.errors import HeaderCompileContextAmbiguousError

        header, evidence = self._two_conflicting_units(tmp_path)
        with pytest.raises(HeaderCompileContextAmbiguousError):
            resolve_header_compile_context(evidence, [header])

    @pytest.mark.parametrize(
        ("pick", "macro"), [("a.cpp", "WIDE"), ("b.cpp", "NARROW")]
    )
    def test_the_filter_resolves_the_ambiguity_to_the_named_unit(
        self, tmp_path: Path, pick: str, macro: str
    ) -> None:
        header, evidence = self._two_conflicting_units(tmp_path)
        result = resolve_header_compile_context(evidence, [header], source_filter=pick)
        assert result.context is not None, pick
        tokens = " ".join(result.context.gcc_option_tokens)
        # Not merely "one context was chosen" -- the derived flags must be
        # the *named* unit's, which is the whole point of the filter.
        assert f"-D{macro}=1" in tokens, (pick, tokens)
        assert len(result.matched_units) == 1, pick

    def test_a_filter_matching_no_unit_leaves_the_resolution_unchanged(
        self, tmp_path: Path
    ) -> None:
        """The conservative fallback again, at this level: a mistyped glob
        must not silently turn real L3 evidence into none."""
        from abicheck.errors import HeaderCompileContextAmbiguousError

        header, evidence = self._two_conflicting_units(tmp_path)
        with pytest.raises(HeaderCompileContextAmbiguousError):
            resolve_header_compile_context(
                evidence, [header], source_filter="*/typo-nothing-matches.cpp"
            )

    def test_no_filter_is_a_no_op_for_a_single_unit(self, tmp_path: Path) -> None:
        header = tmp_path / "api.h"
        header.write_text("struct S { int a; };\n", encoding="utf-8")
        src = tmp_path / "a.cpp"
        src.write_text('#include "api.h"\n', encoding="utf-8")
        evidence = BuildEvidence(
            compile_units=[
                _cu(str(src), directory=str(tmp_path), abi_relevant_flags=["-DWIDE=1"])
            ]
        )
        assert resolve_header_compile_context(
            evidence, [header]
        ) == resolve_header_compile_context(evidence, [header], source_filter=None)


@pytest.mark.integration
@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="ELF/Linux-scoped repro (real g++-compiled .so + compile_commands.json)",
)
@pytest.mark.skipif(
    not (_HAVE_GXX and _HAVE_CLANG), reason="needs a real g++ and clang toolchain"
)
class TestDumpCliHonorsTheFilterInTheFold:
    """The end-to-end shape the fix exists for, through the real CLI.

    Two translation units include one public header under conflicting,
    ABI-relevant ``-D``s. Before the fix, ``--compile-db-filter`` reached only
    the legacy ``-p`` auto-match, so the fold still saw both units and failed
    closed with a message advising the very flag that was already given.
    """

    @staticmethod
    def _project(tmp_path: Path) -> tuple[Path, Path, Path]:
        header = tmp_path / "api.h"
        header.write_text(
            "#pragma once\n"
            "struct S {\n"
            "  int a;\n"
            "#ifdef WIDE\n"
            "  long long b;\n"
            "#endif\n"
            "};\n",
            encoding="utf-8",
        )
        for name in ("a", "b"):
            (tmp_path / f"{name}.cpp").write_text(
                f'#include "api.h"\nint f_{name}(S s) {{ return s.a; }}\n',
                encoding="utf-8",
            )
        so_path = tmp_path / "libapi.so"
        subprocess.run(
            [
                "g++",
                "-std=c++17",
                "-DWIDE=1",
                "-shared",
                "-fPIC",
                "-o",
                str(so_path),
                str(tmp_path / "a.cpp"),
                str(tmp_path / "b.cpp"),
            ],
            check=True,
            capture_output=True,
        )
        compile_db = tmp_path / "compile_commands.json"
        compile_db.write_text(
            json.dumps(
                [
                    {
                        "directory": str(tmp_path),
                        "arguments": [
                            "g++",
                            "-std=c++17",
                            f"-D{macro}",
                            "-fPIC",
                            "-c",
                            str(tmp_path / f"{name}.cpp"),
                            "-o",
                            f"{name}.o",
                        ],
                        "file": str(tmp_path / f"{name}.cpp"),
                    }
                    for name, macro in (("a", "WIDE=1"), ("b", "NARROW=1"))
                ]
            ),
            encoding="utf-8",
        )
        return so_path, header, compile_db

    @staticmethod
    def _dump(so_path: Path, header: Path, compile_db: Path, out: Path, *extra: str):
        from click.testing import CliRunner

        from abicheck.cli import main

        return CliRunner().invoke(
            main,
            [
                "dump",
                str(so_path),
                "-H",
                str(header),
                "--sources",
                str(header.parent),
                "--build-info",
                str(compile_db),
                # `--depth headers` keeps `collect_mode` "off", which is the
                # one depth at which `compile_db_filter_scope_error` permits
                # the flag alongside `--build-info` at all: no L3 evidence is
                # embedded, so there is no L2-filtered/L3-unfiltered snapshot
                # to be inconsistent. That refusal is unchanged by this fix.
                "--depth",
                "headers",
                "--ast-frontend",
                "clang",
                "-o",
                str(out),
                *extra,
            ],
        )

    def test_unfiltered_still_fails_closed_on_the_real_ambiguity(
        self, tmp_path: Path
    ) -> None:
        so_path, header, compile_db = self._project(tmp_path)
        result = self._dump(so_path, header, compile_db, tmp_path / "out.json")
        assert result.exit_code != 0
        assert "materially different" in result.output

    @pytest.mark.parametrize(
        ("pick", "expects_wide_field"), [("a.cpp", True), ("b.cpp", False)]
    )
    def test_the_filter_selects_that_units_context_for_the_header_parse(
        self, tmp_path: Path, pick: str, expects_wide_field: bool
    ) -> None:
        so_path, header, compile_db = self._project(tmp_path)
        out = tmp_path / f"{pick}.json"
        result = self._dump(
            so_path, header, compile_db, out, "--compile-db-filter", pick
        )
        assert result.exit_code == 0, result.output
        snapshot = json.loads(out.read_text(encoding="utf-8"))
        fields = [
            f.get("name")
            for t in snapshot.get("types", [])
            if t.get("name") == "S"
            for f in t.get("fields", [])
        ]
        assert fields, (pick, snapshot.get("types"))
        # Not merely "a context was chosen": the guarded field is present
        # only under the TU the filter named, so this pins *which* one.
        assert ("b" in fields) is expects_wide_field, (pick, fields)


@pytest.mark.integration
@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="ELF/Linux-scoped repro (real g++-compiled .so + compile_commands.json)",
)
@pytest.mark.skipif(
    not (_HAVE_GXX and _HAVE_CLANG), reason="needs a real g++ and clang toolchain"
)
class TestTypedApiHonorsTheFilterInTheFold:
    """The identical shape as ``TestDumpCliHonorsTheFilterInTheFold``, through
    the typed ``DumpRequest``/``resolve_dump_request``/``execute_dump_request``
    path instead of the CLI (PR 3A investigation, 2026-08-21:
    ``InputSpec.compile_db_filter``).

    ``InputSpec`` deliberately had no ``compile_db_filter`` field until this
    slice — see its own docstring for the two prerequisites this closes:
    the filter narrows the shared L2 fold (already landed, exercised by the
    CLI class above), and the L2-filtered/L3-unfiltered refusal is mirrored
    into ``resolve_dump_request`` so a typed caller cannot reach the
    snapshot shape the CLI refuses outright.
    """

    @staticmethod
    def _request(so_path: Path, header: Path, compile_db: Path, *, compile_db_filter):
        from abicheck.api_types import DumpRequest, InputSpec

        return DumpRequest(
            input=InputSpec(
                path=so_path,
                headers=(header,),
                sources=header.parent,
                build_info=compile_db,
                compile_db_filter=compile_db_filter,
            ),
            frontend="clang",
            depth="headers",
        )

    def test_resolve_dump_request_refuses_the_l2_l3_scope_mismatch(
        self, tmp_path: Path
    ) -> None:
        from abicheck.errors import ValidationError
        from abicheck.service_dump_pipeline import resolve_dump_request

        so_path, header, compile_db = TestDumpCliHonorsTheFilterInTheFold._project(
            tmp_path
        )
        request = self._request(
            so_path, header, compile_db, compile_db_filter="a.cpp"
        ).replace(depth="build")
        with pytest.raises(ValidationError, match="materially different|L2 header"):
            resolve_dump_request(request)

    def test_no_filter_is_unaffected(self, tmp_path: Path) -> None:
        from abicheck.service_dump_pipeline import resolve_dump_request

        so_path, header, compile_db = TestDumpCliHonorsTheFilterInTheFold._project(
            tmp_path
        )
        request = self._request(so_path, header, compile_db, compile_db_filter=None)
        # No filter -> the scope-error guard never fires, regardless of depth;
        # unchanged behavior for every pre-existing caller.
        resolve_dump_request(request.replace(depth="build"))

    @pytest.mark.parametrize(
        ("pick", "expects_wide_field"), [("a.cpp", True), ("b.cpp", False)]
    )
    def test_the_filter_selects_that_units_context_for_the_header_parse(
        self, tmp_path: Path, pick: str, expects_wide_field: bool
    ) -> None:
        from abicheck.service_dump_pipeline import (
            execute_dump_request,
            resolve_dump_request,
        )

        so_path, header, compile_db = TestDumpCliHonorsTheFilterInTheFold._project(
            tmp_path
        )
        request = self._request(so_path, header, compile_db, compile_db_filter=pick)
        result = execute_dump_request(resolve_dump_request(request))
        fields = [
            f.name for t in result.snapshot.types if t.name == "S" for f in t.fields
        ]
        assert fields, result.snapshot.types
        assert ("b" in fields) is expects_wide_field, (pick, fields)
