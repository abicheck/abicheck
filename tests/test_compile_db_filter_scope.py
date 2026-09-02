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
        from abicheck.serialization import load_snapshot_document

        snapshot = load_snapshot_document(out)
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
        resolved = resolve_dump_request(request.replace(depth="build"))
        assert resolved.collect_mode == "build"

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


def test_input_spec_of_forwards_compile_db_filter() -> None:
    """Codex review, P2: the documented convenience factory
    (``InputSpec.of``) silently dropped ``compile_db_filter`` -- constructing
    through it (rather than the dataclass directly) raised ``TypeError`` for
    an unrecognized keyword, so the field was reachable only via the
    dataclass constructor despite being advertised on the public type."""
    from abicheck.api_types import InputSpec

    spec = InputSpec.of(path="lib.so", compile_db_filter="src/**")
    assert spec.compile_db_filter == "src/**"
    # And the default is unaffected -- every existing `InputSpec.of(...)`
    # call site stays a no-op for this field.
    assert InputSpec.of(path="lib.so").compile_db_filter is None


@pytest.mark.integration
@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="ELF/Linux-scoped repro (real g++-compiled .so + compile_commands.json)",
)
@pytest.mark.skipif(
    not (_HAVE_GXX and _HAVE_CLANG), reason="needs a real g++ and clang toolchain"
)
class TestCompareRequestAppliesTheSameScopeGuard:
    """Codex review, P1: the L2-filtered/L3-unfiltered scope guard was wired
    into ``resolve_dump_request`` only -- a typed ``CompareRequest`` side
    reaches the identical ``_seeded_includes_and_compile_context`` fold /
    ``embed_side_build_source`` split (``resolve_side_snapshot`` is the one
    primitive both pipelines share), so the same refusal must fire there
    too. Both pipelines now call the shared
    ``service_compare_evidence.reject_compile_db_filter_scope_mismatch``.
    """

    def test_a_side_with_the_scope_mismatch_is_refused(self, tmp_path: Path) -> None:
        from abicheck.api_types import CompareRequest, InputSpec
        from abicheck.errors import ValidationError
        from abicheck.service_compare_pipeline import resolve_compare_request

        so_path, header, compile_db = TestDumpCliHonorsTheFilterInTheFold._project(
            tmp_path
        )
        side = InputSpec(
            path=so_path,
            headers=(header,),
            sources=header.parent,
            build_info=compile_db,
            compile_db_filter="a.cpp",
        )
        request = CompareRequest(old=side, new=side, frontend="clang", depth="build")
        with pytest.raises(ValidationError, match="old: .*L2 header parse only"):
            resolve_compare_request(request)

    def test_a_scope_matched_filter_is_unaffected_by_the_guard(
        self, tmp_path: Path
    ) -> None:
        """Not "no filter" -- a bare, unfiltered compare of this exact project
        would still fail closed on the real ambiguity
        (`TestDumpCliHonorsTheFilterInTheFold.
        test_unfiltered_still_fails_closed_on_the_real_ambiguity`), which
        would make a `resolve_compare_request` call here indistinguishable
        from the guard actually firing. Isolating the guard itself needs a
        filter that resolves that ambiguity (as it does on the CLI/`dump`
        paths) *and* a collect mode (`"headers"` -> `"off"`) the guard
        permits regardless of the filter -- so this pins "the guard doesn't
        misfire on a legitimate filtered compare", not "no filter changes
        nothing"."""
        from abicheck.api_types import CompareRequest, InputSpec
        from abicheck.service_compare_pipeline import resolve_compare_request

        so_path, header, compile_db = TestDumpCliHonorsTheFilterInTheFold._project(
            tmp_path
        )
        side = InputSpec(
            path=so_path,
            headers=(header,),
            sources=header.parent,
            build_info=compile_db,
            compile_db_filter="a.cpp",
        )
        request = CompareRequest(old=side, new=side, frontend="clang", depth="headers")
        pair = resolve_compare_request(request)
        assert pair.old is not None and pair.new is not None


@pytest.mark.integration
@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="ELF/Linux-scoped repro (real g++-compiled .so + compile_commands.json)",
)
@pytest.mark.skipif(
    not (_HAVE_GXX and _HAVE_CLANG), reason="needs a real g++ and clang toolchain"
)
class TestScopeGuardCoversSourcesOnlyAutoDiscovery:
    """Codex review, P1 (a fresh round on the same slice): the scope guard
    resolved the checked compile database via
    ``compile_db_from_build_info`` alone -- ``--build-info``-only -- so a
    request giving only ``sources`` (no ``build_info`` at all) could still
    reach the exact filtered-L2/unfiltered-L3 mismatch uncaught: the P0.3
    fold and ``embed_build_source``'s L3 collection both independently
    auto-discover the same ``compile_commands.json`` under ``sources``
    (``buildsource.inline._autodiscover_compile_db``, the same strategy
    both already use with no explicit ``--build-info``), but only the fold
    honors ``compile_db_filter``. Reproduced directly before the fix: a
    real two-TU project resolved with ``sources`` only, filtered to one TU
    at L2, still embedded *both* TUs' compile units as L3 evidence
    (`BuildEvidence.compile_units` had length 2).

    This is not a regression introduced by the typed-API slice -- the
    native CLI's own pre-existing `compile_db_path = compile_db_from_
    build_info(build_info, headers)` had the identical gap: before this
    fix, the CLI-path test below silently succeeded (exit 0) instead of
    rejecting the mismatch, since the CLI's own scope check used the same
    narrow, `--build-info`-only resolution. Fixed once, in the shared
    `header_conditionals.compile_db_for_filter_scope_check`, consumed by
    both the CLI and the typed pipelines' guard.
    """

    def test_cli_rejects_a_sources_only_scope_mismatch(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main

        so_path, header, _compile_db = TestDumpCliHonorsTheFilterInTheFold._project(
            tmp_path
        )
        result = CliRunner().invoke(
            main,
            [
                "dump",
                str(so_path),
                "-H",
                str(header),
                "--sources",
                str(header.parent),
                "--depth",
                "build",
                "--ast-frontend",
                "clang",
                "--compile-db-filter",
                "a.cpp",
                "-o",
                str(tmp_path / "out.json"),
            ],
        )
        assert result.exit_code == 64, result.output
        assert "L2 header parse only" in result.output

    def test_dump_request_rejects_a_sources_only_scope_mismatch(
        self, tmp_path: Path
    ) -> None:
        from abicheck.api_types import DumpRequest, InputSpec
        from abicheck.errors import ValidationError
        from abicheck.service_dump_pipeline import resolve_dump_request

        so_path, header, _compile_db = TestDumpCliHonorsTheFilterInTheFold._project(
            tmp_path
        )
        request = DumpRequest(
            input=InputSpec(
                path=so_path,
                headers=(header,),
                sources=header.parent,
                compile_db_filter="a.cpp",
            ),
            frontend="clang",
            depth="build",
        )
        with pytest.raises(ValidationError, match="input: .*L2 header parse only"):
            resolve_dump_request(request)

    def test_compare_request_rejects_a_sources_only_scope_mismatch(
        self, tmp_path: Path
    ) -> None:
        from abicheck.api_types import CompareRequest, InputSpec
        from abicheck.errors import ValidationError
        from abicheck.service_compare_pipeline import resolve_compare_request

        so_path, header, _compile_db = TestDumpCliHonorsTheFilterInTheFold._project(
            tmp_path
        )
        side = InputSpec(
            path=so_path,
            headers=(header,),
            sources=header.parent,
            compile_db_filter="a.cpp",
        )
        request = CompareRequest(old=side, new=side, frontend="clang", depth="build")
        with pytest.raises(ValidationError, match="old: .*L2 header parse only"):
            resolve_compare_request(request)

    def test_no_filter_sources_only_is_unaffected_by_the_guard(
        self, tmp_path: Path
    ) -> None:
        """No filter -> the guard itself never fires for this shape either
        (a downstream real ambiguity error from the same project's two
        conflicting TUs is a separate, pre-existing failure mode -- see
        `test_unfiltered_still_fails_closed_on_the_real_ambiguity` -- and
        `resolve_dump_request` never reaches the fold at all, only
        `execute_dump_request` does, so it cannot surface here)."""
        from abicheck.api_types import DumpRequest, InputSpec
        from abicheck.service_dump_pipeline import resolve_dump_request

        so_path, header, _compile_db = TestDumpCliHonorsTheFilterInTheFold._project(
            tmp_path
        )
        request = DumpRequest(
            input=InputSpec(path=so_path, headers=(header,), sources=header.parent),
            frontend="clang",
            depth="build",
        )
        resolved = resolve_dump_request(request)
        assert resolved.collect_mode == "build"


@pytest.mark.integration
@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="ELF/Linux-scoped repro (real g++-compiled .so + compile_commands.json)",
)
@pytest.mark.skipif(
    not (_HAVE_GXX and _HAVE_CLANG), reason="needs a real g++ and clang toolchain"
)
class TestScopeGuardCoversNestedBuildInfoDatabases:
    """Codex review, P1 (a fourth finding on the same slice): the scope
    guard's ``build_info`` resolution (``compile_db_from_build_info``) only
    ever checks ``<build_info>/compile_commands.json`` directly -- it has
    no notion of a conventional out-of-tree build directory. The real
    fold's own ``--build-info`` resolution
    (``buildsource.inline._compile_db_at``, for a directory delegating to
    ``_find_compile_db_in_dir``) searches immediate subdirectories too
    (``build/``, ``cmake-build-debug/``, ...) -- explicitly documented as
    matching ``--sources`` auto-discovery's own contract. So
    ``--build-info <project-root>`` whose database actually lives at
    ``<project-root>/build/compile_commands.json`` resolved for the fold
    but not for the scope guard, reproducing the identical filtered-L2/
    unfiltered-L3 mismatch. Fixed by having
    ``compile_db_for_filter_scope_check`` search the same immediate-
    subdirectory strategy for a directory ``build_info`` before falling
    back to ``sources`` auto-discovery.
    """

    @staticmethod
    def _project_with_nested_build_info(tmp_path: Path) -> tuple[Path, Path, Path]:
        so_path, header, compile_db = TestDumpCliHonorsTheFilterInTheFold._project(
            tmp_path
        )
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        compile_db.rename(build_dir / "compile_commands.json")
        return so_path, header, tmp_path  # build_info = the project root

    def test_cli_rejects_a_nested_build_info_scope_mismatch(
        self, tmp_path: Path
    ) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main

        so_path, header, build_info = self._project_with_nested_build_info(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "dump",
                str(so_path),
                "-H",
                str(header),
                "--build-info",
                str(build_info),
                "--depth",
                "build",
                "--ast-frontend",
                "clang",
                "--compile-db-filter",
                "a.cpp",
                "-o",
                str(tmp_path / "out.json"),
            ],
        )
        assert result.exit_code == 64, result.output
        assert "L2 header parse only" in result.output

    def test_dump_request_rejects_a_nested_build_info_scope_mismatch(
        self, tmp_path: Path
    ) -> None:
        from abicheck.api_types import DumpRequest, InputSpec
        from abicheck.errors import ValidationError
        from abicheck.service_dump_pipeline import resolve_dump_request

        so_path, header, build_info = self._project_with_nested_build_info(tmp_path)
        request = DumpRequest(
            input=InputSpec(
                path=so_path,
                headers=(header,),
                build_info=build_info,
                compile_db_filter="a.cpp",
            ),
            frontend="clang",
            depth="build",
        )
        with pytest.raises(ValidationError, match="input: .*L2 header parse only"):
            resolve_dump_request(request)

    def test_compare_request_rejects_a_nested_build_info_scope_mismatch(
        self, tmp_path: Path
    ) -> None:
        from abicheck.api_types import CompareRequest, InputSpec
        from abicheck.errors import ValidationError
        from abicheck.service_compare_pipeline import resolve_compare_request

        so_path, header, build_info = self._project_with_nested_build_info(tmp_path)
        side = InputSpec(
            path=so_path,
            headers=(header,),
            build_info=build_info,
            compile_db_filter="a.cpp",
        )
        request = CompareRequest(old=side, new=side, frontend="clang", depth="build")
        with pytest.raises(ValidationError, match="old: .*L2 header parse only"):
            resolve_compare_request(request)

    def test_the_nested_database_still_narrows_the_header_parse(
        self, tmp_path: Path
    ) -> None:
        """Positive control: the nested database really is what the fold
        resolves and filters by -- not merely a guard-level assumption."""
        from abicheck.api_types import DumpRequest, InputSpec
        from abicheck.service_dump_pipeline import (
            execute_dump_request,
            resolve_dump_request,
        )

        so_path, header, build_info = self._project_with_nested_build_info(tmp_path)
        request = DumpRequest(
            input=InputSpec(
                path=so_path,
                headers=(header,),
                build_info=build_info,
                compile_db_filter="a.cpp",
            ),
            frontend="clang",
            depth="headers",  # collect_mode "off" -- the guard doesn't fire
        )
        result = execute_dump_request(resolve_dump_request(request))
        fields = [
            f.name for t in result.snapshot.types if t.name == "S" for f in t.fields
        ]
        assert "b" in fields, fields  # a.cpp's -DWIDE field, confirms narrowing


class TestScopeGuardCoversPackAndBazelBuildInfo:
    """Codex review, P1 (a sixth finding on the same guard): the fold's own
    ``resolve_header_compile_context``/``filter_units_by_source`` narrows
    *whatever* ``BuildEvidence.compile_units`` a ``--build-info`` resolves
    to -- not only a literal ``compile_commands.json``. A ``--build-info``
    naming a pre-captured ``collect`` pack directory, or a Bazel
    ``aquery``/``cquery`` jsonproto, resolves compile units the identical
    way and is embedded at L3 with no filter either way -- so the guard's
    original ``compile_db_from_build_info``-only check (which deliberately
    returns ``None`` for both, since neither is a literal compile database)
    silently let the exact filtered-L2/unfiltered-L3 mismatch through for
    both shapes. These tests exercise the pure predicate directly -- no
    compiler needed, since the guard is a read-only classification over
    ``--build-info``'s own path shape.
    """

    @staticmethod
    def _pack_dir(tmp_path: Path) -> Path:
        pack = tmp_path / "pack"
        pack.mkdir()
        (pack / "manifest.json").write_text(
            json.dumps({"build_source_pack_version": 1}), encoding="utf-8"
        )
        return pack

    @staticmethod
    def _bazel_aquery_file(tmp_path: Path) -> Path:
        path = tmp_path / "aquery.json"
        path.write_text(json.dumps({"actions": [], "artifacts": []}), encoding="utf-8")
        return path

    @staticmethod
    def _bazel_cquery_file(tmp_path: Path) -> Path:
        path = tmp_path / "cquery.json"
        path.write_text(json.dumps({"results": []}), encoding="utf-8")
        return path

    @staticmethod
    def _inputs_pack(tmp_path: Path) -> Path:
        pack = tmp_path / "inputs_pack"
        pack.mkdir()
        (pack / "manifest.json").write_text(
            json.dumps({"kind": "abicheck_inputs"}), encoding="utf-8"
        )
        return pack

    def test_pack_directory_is_recognized(self, tmp_path: Path) -> None:
        from abicheck.header_conditionals import compile_db_for_filter_scope_check

        pack = self._pack_dir(tmp_path)
        resolved = compile_db_for_filter_scope_check(pack, None, (tmp_path / "api.h",))
        assert resolved == pack

    def test_flow2_inputs_pack_named_by_build_info_is_recognized(
        self, tmp_path: Path
    ) -> None:
        """Codex review, P1 (a ninth finding): the original pack recognition
        here only checked ``is_pack_dir`` (classic ``BuildSourcePack``),
        missing the identical Flow-2 ``abicheck_inputs/`` shape its own
        ``--sources`` sibling (``TestScopeGuardCoversSourcesPacks``) already
        covers -- even though ``_l2_seed_pack_inputs`` recognizes both
        shapes for ``build_info`` too."""
        from abicheck.header_conditionals import (
            compile_db_filter_scope_error,
            compile_db_for_filter_scope_check,
        )

        pack = self._inputs_pack(tmp_path)
        resolved = compile_db_for_filter_scope_check(pack, None, (tmp_path / "api.h",))
        assert resolved == pack
        assert compile_db_filter_scope_error("foo.cpp", resolved, "build") is not None

    def test_bazel_aquery_file_is_recognized(self, tmp_path: Path) -> None:
        from abicheck.header_conditionals import compile_db_for_filter_scope_check

        aquery = self._bazel_aquery_file(tmp_path)
        resolved = compile_db_for_filter_scope_check(
            aquery, None, (tmp_path / "api.h",)
        )
        assert resolved == aquery

    def test_bazel_cquery_file_is_recognized(self, tmp_path: Path) -> None:
        from abicheck.header_conditionals import compile_db_for_filter_scope_check

        cquery = self._bazel_cquery_file(tmp_path)
        resolved = compile_db_for_filter_scope_check(
            cquery, None, (tmp_path / "api.h",)
        )
        assert resolved == cquery

    def test_pack_directory_triggers_the_scope_error(self, tmp_path: Path) -> None:
        from abicheck.header_conditionals import (
            compile_db_filter_scope_error,
            compile_db_for_filter_scope_check,
        )

        pack = self._pack_dir(tmp_path)
        resolved = compile_db_for_filter_scope_check(pack, None, (tmp_path / "api.h",))
        error = compile_db_filter_scope_error("foo.cpp", resolved, "build")
        assert error is not None
        assert "--compile-db-filter" in error

    def test_bazel_jsonproto_triggers_the_scope_error(self, tmp_path: Path) -> None:
        from abicheck.header_conditionals import (
            compile_db_filter_scope_error,
            compile_db_for_filter_scope_check,
        )

        aquery = self._bazel_aquery_file(tmp_path)
        resolved = compile_db_for_filter_scope_check(
            aquery, None, (tmp_path / "api.h",)
        )
        error = compile_db_filter_scope_error("foo.cpp", resolved, "build")
        assert error is not None

    def test_no_filter_pack_directory_is_unaffected(self, tmp_path: Path) -> None:
        """Control: the guard only fires when a filter is actually set."""
        from abicheck.header_conditionals import (
            compile_db_filter_scope_error,
            compile_db_for_filter_scope_check,
        )

        pack = self._pack_dir(tmp_path)
        resolved = compile_db_for_filter_scope_check(pack, None, (tmp_path / "api.h",))
        assert compile_db_filter_scope_error(None, resolved, "build") is None

    def test_collect_mode_off_pack_directory_is_unaffected(
        self, tmp_path: Path
    ) -> None:
        """Control: no L3 embed at ``collect_mode == "off"``, nothing to
        mismatch against."""
        from abicheck.header_conditionals import (
            compile_db_filter_scope_error,
            compile_db_for_filter_scope_check,
        )

        pack = self._pack_dir(tmp_path)
        resolved = compile_db_for_filter_scope_check(pack, None, (tmp_path / "api.h",))
        assert compile_db_filter_scope_error("foo.cpp", resolved, "off") is None

    def test_a_plain_non_pack_directory_is_still_a_build_dir_search(
        self, tmp_path: Path
    ) -> None:
        """A directory that is not a pack (no manifest.json, or one lacking
        the BuildSourcePack marker) must still fall through to the ordinary
        nested-compile-db search / sources auto-discovery, not be
        misclassified as a pack."""
        from abicheck.header_conditionals import compile_db_for_filter_scope_check

        plain_dir = tmp_path / "not_a_pack"
        plain_dir.mkdir()
        resolved = compile_db_for_filter_scope_check(
            plain_dir, None, (tmp_path / "api.h",)
        )
        assert resolved is None

    def test_a_non_bazel_json_object_file_is_unrecognized(self, tmp_path: Path) -> None:
        """An ordinary JSON-object build-info file (neither a compile-DB
        array nor a Bazel aquery/cquery shape) must not be mistaken for
        filterable build evidence."""
        from abicheck.header_conditionals import compile_db_for_filter_scope_check

        odd = tmp_path / "odd.json"
        odd.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
        resolved = compile_db_for_filter_scope_check(odd, None, (tmp_path / "api.h",))
        assert resolved is None


class TestScopeGuardCoversSourcesPacks:
    """Codex review, P1 (a seventh finding on the same guard): the sixth
    finding's fix only recognized a pack named by ``--build-info``, but
    ``buildsource.l2_seed._l2_seed_pack_inputs`` folds a ``--sources`` pack
    (a classic ``BuildSourcePack`` or a Flow-2 ``abicheck_inputs/``
    directory) into L2 seeding the identical way -- whenever no
    ``--build-info`` was given at all -- so a ``--sources`` naming such a
    pack reproduced the same filtered-L2/unfiltered-L3 mismatch with no
    error, since a pack directory carries no literal ``compile_commands.
    json`` at its root for ``_autodiscover_compile_db`` to find. Pure
    predicate tests, no compiler needed.
    """

    @staticmethod
    def _classic_pack(tmp_path: Path) -> Path:
        pack = tmp_path / "classic_pack"
        pack.mkdir()
        (pack / "manifest.json").write_text(
            json.dumps({"build_source_pack_version": 1}), encoding="utf-8"
        )
        return pack

    @staticmethod
    def _inputs_pack(tmp_path: Path) -> Path:
        pack = tmp_path / "inputs_pack"
        pack.mkdir()
        (pack / "manifest.json").write_text(
            json.dumps({"kind": "abicheck_inputs"}), encoding="utf-8"
        )
        return pack

    def test_classic_pack_named_by_sources_is_recognized(self, tmp_path: Path) -> None:
        from abicheck.header_conditionals import compile_db_for_filter_scope_check

        pack = self._classic_pack(tmp_path)
        resolved = compile_db_for_filter_scope_check(None, pack, (tmp_path / "api.h",))
        assert resolved == pack

    def test_inputs_pack_named_by_sources_is_recognized(self, tmp_path: Path) -> None:
        from abicheck.header_conditionals import compile_db_for_filter_scope_check

        pack = self._inputs_pack(tmp_path)
        resolved = compile_db_for_filter_scope_check(None, pack, (tmp_path / "api.h",))
        assert resolved == pack

    def test_sources_pack_triggers_the_scope_error(self, tmp_path: Path) -> None:
        from abicheck.header_conditionals import (
            compile_db_filter_scope_error,
            compile_db_for_filter_scope_check,
        )

        pack = self._classic_pack(tmp_path)
        resolved = compile_db_for_filter_scope_check(None, pack, (tmp_path / "api.h",))
        error = compile_db_filter_scope_error("foo.cpp", resolved, "build")
        assert error is not None

    def test_an_explicit_build_info_takes_precedence_over_a_sources_pack(
        self, tmp_path: Path
    ) -> None:
        """Mirrors ``_l2_seed_pack_inputs``'s own ``if build_info is None:``
        gate: an explicit ``--build-info`` (even one that itself resolves to
        nothing recognizable) means the sources pack's evidence is never
        folded into ``base_build``, so the guard must not treat the sources
        pack as filterable evidence in that combination either."""
        from abicheck.header_conditionals import compile_db_for_filter_scope_check

        pack = self._classic_pack(tmp_path)
        unrelated_build_info = tmp_path / "not_a_pack_or_db"
        unrelated_build_info.mkdir()
        resolved = compile_db_for_filter_scope_check(
            unrelated_build_info, pack, (tmp_path / "api.h",)
        )
        assert resolved is None

    def test_no_filter_sources_pack_is_unaffected(self, tmp_path: Path) -> None:
        """Control: the guard only fires when a filter is actually set."""
        from abicheck.header_conditionals import (
            compile_db_filter_scope_error,
            compile_db_for_filter_scope_check,
        )

        pack = self._classic_pack(tmp_path)
        resolved = compile_db_for_filter_scope_check(None, pack, (tmp_path / "api.h",))
        assert compile_db_filter_scope_error(None, resolved, "build") is None

    def test_a_plain_sources_directory_is_still_auto_discovery(
        self, tmp_path: Path
    ) -> None:
        """A ``sources`` directory that is not a pack (no manifest.json, or
        one lacking either pack's own marker) must still fall through to
        ordinary auto-discovery, not be misclassified as a pack."""
        from abicheck.header_conditionals import compile_db_for_filter_scope_check

        plain_tree = tmp_path / "plain_source_tree"
        plain_tree.mkdir()
        resolved = compile_db_for_filter_scope_check(
            None, plain_tree, (tmp_path / "api.h",)
        )
        assert resolved is None


class TestScopeGuardDoesNotFallBackToSourcesWhenBuildInfoMisses:
    """Codex review, P2 (an eighth finding): the seventh finding's fix made
    every branch fall through unconditionally to the sources-based checks
    once none of the ``build_info`` branches matched -- but
    ``buildsource.inline._resolve_compile_db``'s own ``explicit_input_
    missed`` logic (the real function every one of these seeded resolvers
    ultimately calls) returns ``None`` as soon as a *given* ``build_info``
    misses, deliberately, per its own comment: "surface that miss rather
    than masking it with a stale auto-discovered DB ... checked BEFORE
    auto-discovery". So an explicit ``--build-info`` that doesn't resolve
    to anything recognizable means neither the real L2 fold nor the L3
    embed ever falls back to a ``sources``-discovered database -- falling
    back in the guard produced a false positive, rejecting a
    ``--compile-db-filter`` combination the real resolvers wouldn't
    actually apply to either side of. Pure predicate tests, no compiler
    needed for the unrecognized-build_info half; the auto-discoverable-
    sources half reuses the real g++/clang project fixture to prove the
    combination the guard now permits is genuinely one the real fold
    ignores ``sources`` for.
    """

    def test_unresolvable_build_info_does_not_fall_back_to_sources_auto_discovery(
        self, tmp_path: Path
    ) -> None:
        from abicheck.header_conditionals import compile_db_for_filter_scope_check

        unrelated_build_info = tmp_path / "not_a_pack_or_db"
        unrelated_build_info.mkdir()
        sources = tmp_path / "sources"
        sources.mkdir()
        (sources / "compile_commands.json").write_text("[]", encoding="utf-8")
        resolved = compile_db_for_filter_scope_check(
            unrelated_build_info, sources, (tmp_path / "api.h",)
        )
        assert resolved is None

    @pytest.mark.integration
    @pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="ELF/Linux-scoped repro (real g++-compiled .so + compile_commands.json)",
    )
    @pytest.mark.skipif(
        not (_HAVE_GXX and _HAVE_CLANG), reason="needs a real g++ and clang toolchain"
    )
    def test_an_unresolvable_build_info_alongside_a_real_sources_db_is_unaffected(
        self, tmp_path: Path
    ) -> None:
        """Positive control against the real g++/clang project: with a
        genuinely unresolvable ``--build-info`` alongside a ``--sources``
        tree that *does* auto-discover a real compile database, the guard
        must not reject the request -- confirming the fix isn't merely
        unit-testing the predicate in isolation."""
        from abicheck.header_conditionals import (
            compile_db_filter_scope_error,
            compile_db_for_filter_scope_check,
        )

        _so_path, header, _compile_db = TestDumpCliHonorsTheFilterInTheFold._project(
            tmp_path
        )
        unrelated_build_info = tmp_path / "not_a_pack_or_db"
        unrelated_build_info.mkdir()
        resolved = compile_db_for_filter_scope_check(
            unrelated_build_info, header.parent, (header,)
        )
        assert resolved is None
        assert compile_db_filter_scope_error("a.cpp", resolved, "build") is None
