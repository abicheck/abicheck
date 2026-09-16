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

"""`compat dump` must read every descriptor `compat check` reads.

`compat check`'s loading path expands a `<headers>`/`<libs>` *directory*
operand at the boundary (`run_inputs._load_descriptor_or_dump`), which is
ABICC's ordinary usage. `compat_dump_cmd` called `parse_descriptor` alone,
so the same descriptor failed there -- the library directory reached the
binary parser as "Unrecognised binary format" and the header directory
reached the header parser as `#include "<dir>"` (Codex review).

Bug class: one documented capability wired into one of two public consumers.
The tests below are written against the *command*, not the helper, since
the helper was already correct and already tested -- what was missing was
the call.
"""

from __future__ import annotations

import struct
from pathlib import Path
from unittest import mock

import pytest
from _descriptor_paths import descriptor_include_flag
from click.testing import CliRunner

from abicheck._compiler_options import split_gcc_options
from abicheck.compat.cli import compat_group


def _et_dyn_elf_header() -> bytes:
    """A minimal, genuinely well-formed 64-bit ``ET_DYN`` ELF header.

    Enough for `package.discover_shared_libraries`, which is what
    `expand_descriptor_libs` delegates to, and which reads `e_type` rather
    than trusting the magic alone -- a bare `b"\x7fELF"` buffer is rejected
    (confirmed: the first version of this fixture was, which is the
    discovery check doing its job). The dump itself is stubbed, so no
    toolchain is needed and this module stays in the default fast lane.
    """
    header = bytearray(64)
    header[0:4] = b"\x7fELF"
    header[4] = 2  # ELFCLASS64
    header[5] = 1  # ELFDATA2LSB
    header[6] = 1  # EV_CURRENT
    struct.pack_into("<H", header, 16, 3)  # e_type = ET_DYN
    struct.pack_into("<H", header, 18, 62)  # e_machine = EM_X86_64
    struct.pack_into("<I", header, 20, 1)  # e_version
    struct.pack_into("<H", header, 52, 64)  # e_ehsize
    struct.pack_into("<H", header, 54, 56)  # e_phentsize
    return bytes(header)


def _descriptor(tmp_path: Path, headers: str, libs: str) -> Path:
    desc = tmp_path / "d.xml"
    desc.write_text(
        f"<version>1.0</version>\n<headers>\n  {headers}\n</headers>\n"
        f"<libs>\n  {libs}\n</libs>\n"
    )
    return desc


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "include").mkdir()
    (tmp_path / "include" / "a.h").write_text("int keep_me(int x);\n")
    (tmp_path / "include" / "b.h").write_text("int also_me(int x);\n")
    libs = tmp_path / "libs"
    libs.mkdir()
    (libs / "libfoo.so").write_bytes(_et_dyn_elf_header())
    return tmp_path


class TestDirectoryOperandsReachTheDumper:
    def _run(self, tree: Path, captured: dict):
        def _fake_dump(path, **kwargs):
            captured["path"] = path
            captured["headers"] = list(kwargs.get("headers") or [])
            from abicheck.model import AbiSnapshot

            return AbiSnapshot(library="libfoo.so", version="1.0")

        desc = _descriptor(tree, "include", "libs")
        with mock.patch("abicheck.compat.cli.dump", side_effect=_fake_dump):
            return CliRunner().invoke(
                compat_group,
                [
                    "dump",
                    "-lib",
                    "foo",
                    "-dump",
                    str(desc),
                    "-dump-path",
                    str(tree / "out.json"),
                ],
            )

    def test_a_libs_directory_resolves_to_the_library_inside_it(self, tree):
        captured: dict = {}
        result = self._run(tree, captured)
        assert result.exit_code == 0, result.output
        # Not the directory, which is what reached the binary parser before.
        assert captured["path"].name == "libfoo.so"
        assert captured["path"].is_file()

    def test_a_headers_directory_resolves_to_the_headers_inside_it(self, tree):
        captured: dict = {}
        result = self._run(tree, captured)
        assert result.exit_code == 0, result.output
        assert sorted(h.name for h in captured["headers"]) == ["a.h", "b.h"]
        assert all(h.is_file() for h in captured["headers"])

    def test_a_plain_file_descriptor_is_unchanged(self, tree):
        """The negative control: expansion must be a no-op for the form that
        already worked, or this fix trades one broken descriptor for another."""
        captured: dict = {}

        def _fake_dump(path, **kwargs):
            captured["path"] = path
            captured["headers"] = list(kwargs.get("headers") or [])
            from abicheck.model import AbiSnapshot

            return AbiSnapshot(library="libfoo.so", version="1.0")

        desc = _descriptor(
            tree, str(tree / "include" / "a.h"), str(tree / "libs" / "libfoo.so")
        )
        with mock.patch("abicheck.compat.cli.dump", side_effect=_fake_dump):
            result = CliRunner().invoke(
                compat_group,
                [
                    "dump",
                    "-lib",
                    "foo",
                    "-dump",
                    str(desc),
                    "-dump-path",
                    str(tree / "out.json"),
                ],
            )
        assert result.exit_code == 0, result.output
        assert captured["path"] == tree / "libs" / "libfoo.so"
        assert [h.name for h in captured["headers"]] == ["a.h"]


class TestTheDescriptorsCompileAndSkipFieldsAreApplied:
    """Expanding the directories was half the wiring.

    `compat dump` still passed the CLI's `gcc_options` and the *unfiltered*
    header list, so a descriptor relying on `<include_paths>`, `<defines>`,
    `<gcc_options>`, `<skip_headers>` or `<skip_including>` dumped a
    different surface than the identical descriptor under `compat check` --
    which breaks the documented dump-then-compare workflow at its root,
    since the two sides are then not the same contract (Codex review).

    Bug class: the same one the directory expansion had, one layer down --
    a descriptor capability wired into one of two public consumers.
    """

    def _run(self, tree: Path, body: str, captured: dict):
        desc = tree / "d.xml"
        desc.write_text(body)

        def _fake_dump(path, **kwargs):
            captured["headers"] = list(kwargs.get("headers") or [])
            captured["gcc_options"] = kwargs.get("gcc_options")
            from abicheck.model import AbiSnapshot

            return AbiSnapshot(library="libfoo.so", version="1.0")

        with mock.patch("abicheck.compat.cli.dump", side_effect=_fake_dump):
            return CliRunner().invoke(
                compat_group,
                [
                    "dump",
                    "-lib",
                    "foo",
                    "-dump",
                    str(desc),
                    "-dump-path",
                    str(tree / "out.json"),
                ],
            )

    def test_skip_headers_removes_the_named_header(self, tree):
        captured: dict = {}
        result = self._run(
            tree,
            "<version>1.0</version>\n<headers>\n  include\n</headers>\n"
            "<libs>\n  libs\n</libs>\n<skip_headers>\n  b.h\n</skip_headers>\n",
            captured,
        )
        assert result.exit_code == 0, result.output
        assert [h.name for h in captured["headers"]] == ["a.h"]

    def test_skip_including_is_honoured_too(self, tree):
        """The sibling element, which the parser unions into the same set --
        covering only `<skip_headers>` would leave half the claim untested."""
        captured: dict = {}
        result = self._run(
            tree,
            "<version>1.0</version>\n<headers>\n  include\n</headers>\n"
            "<libs>\n  libs\n</libs>\n<skip_including>\n  a.h\n</skip_including>\n",
            captured,
        )
        assert result.exit_code == 0, result.output
        assert [h.name for h in captured["headers"]] == ["b.h"]

    def test_include_paths_and_defines_reach_the_dumper(self, tree):
        captured: dict = {}
        result = self._run(
            tree,
            "<version>1.0</version>\n<headers>\n  include\n</headers>\n"
            "<libs>\n  libs\n</libs>\n"
            "<include_paths>\n  /opt/inc\n</include_paths>\n"
            "<defines>\n  FOO=1\n</defines>\n",
            captured,
        )
        assert result.exit_code == 0, result.output
        tokens = split_gcc_options(captured["gcc_options"] or "")
        assert descriptor_include_flag("/opt/inc") in tokens
        assert "-DFOO=1" in tokens

    def test_a_descriptor_without_those_elements_passes_none(self, tree):
        """The negative control: an unconditional empty string would make
        every plain descriptor's dump differ from what it was."""
        captured: dict = {}
        result = self._run(
            tree,
            "<version>1.0</version>\n<headers>\n  include\n</headers>\n"
            "<libs>\n  libs\n</libs>\n",
            captured,
        )
        assert result.exit_code == 0, result.output
        # A descriptor stating no <include_paths> is in ABICC's *automatic*
        # include-path mode, so its own <headers> root is searched -- which
        # is the whole point of that mode, and what an umbrella header
        # including its sibling by <angle> name needs.
        #
        # Spelled from the real directory, not through
        # `descriptor_include_flag`: that helper answers "where does a
        # descriptor's POSIX *literal* land on this host" and re-adds the
        # drive on Windows, which double-prefixed an already-host-spelled
        # `tmp_path` into `-ID:D:\a\...` (real Windows CI failure).
        assert captured["gcc_options"] == f"-I{tree / 'include'}"
        assert sorted(h.name for h in captured["headers"]) == ["a.h", "b.h"]


class TestDescriptorSkipsAreRecordedOnTheSnapshot:
    """`<skip_headers>`/`<skip_including>` narrow the parsed surface, so they
    must be recorded like `--exclude-header`.

    Filtering the live side's headers without recording the patterns left the
    comparability check seeing two empty exclusion sets. Against a stored
    operand carrying no contract -- an older snapshot, or an imported ABICC
    dump, where `scope_fingerprint` cannot refuse either -- the comparison
    proceeded and declarations omitted from NEW came back as removals with a
    breaking verdict (Codex review).

    Bug class: the same asymmetric-scope hole closed for the native
    `--exclude-header` path, left open on the descriptor path. Exactly why
    the fix belongs at the recording step both share.
    """

    def _dump_with_skip(self, tree: Path, skip_element: str) -> object:
        from abicheck.model import AbiSnapshot
        from abicheck.serialization import load_snapshot

        desc = tree / "d.xml"
        desc.write_text(
            "<version>1.0</version>\n<headers>\n  include\n</headers>\n"
            f"<libs>\n  libs\n</libs>\n{skip_element}"
        )
        out = tree / "out.json"

        def _fake_dump(path, **kwargs):
            return AbiSnapshot(library="libfoo.so", version="1.0")

        with mock.patch("abicheck.compat.cli.dump", side_effect=_fake_dump):
            result = CliRunner().invoke(
                compat_group,
                ["dump", "-lib", "foo", "-dump", str(desc), "-dump-path", str(out)],
            )
        assert result.exit_code == 0, result.output
        return load_snapshot(out)

    def test_skip_headers_is_recorded(self, tree):
        snap = self._dump_with_skip(tree, "<skip_headers>\n  b.h\n</skip_headers>\n")
        assert snap.excluded_header_patterns == ("b.h",)

    def test_skip_including_is_not_recorded_as_a_narrowing(self, tree):
        """The two elements are different rules (ABICC's own semantics).

        ``<skip_including>`` says "do not include this directly"; the
        declarations it reaches are still part of the contract. Recording it
        as an exclusion made the snapshot claim a reduced surface, which the
        coverage warning then reported and the comparability gate then
        refused an identical unexcluded operand against.
        """
        snap = self._dump_with_skip(
            tree, "<skip_including>\n  b.h\n</skip_including>\n"
        )
        assert snap.excluded_header_patterns == ()

    def test_the_record_is_order_stable(self, tree):
        """Built from a set, so it is sorted -- otherwise two identical runs
        could record different tuples and compare as asymmetric."""
        snap = self._dump_with_skip(
            tree, "<skip_headers>\n  b.h\n  a.h\n</skip_headers>\n"
        )
        assert snap.excluded_header_patterns == ("a.h", "b.h")

    def test_a_descriptor_with_no_skips_records_nothing(self, tree):
        """The negative control and the compatibility claim: every ordinary
        descriptor's snapshot is unchanged."""
        snap = self._dump_with_skip(tree, "")
        assert snap.excluded_header_patterns == ()

    def test_the_asymmetry_is_then_refused_against_a_contract_less_operand(self, tree):
        """The consequence that makes the recording worth anything, stated
        through the real comparability gate."""
        from abicheck.comparability import check_contracts_comparable
        from abicheck.errors import ScopeMismatchError
        from abicheck.model import AbiSnapshot

        narrowed = self._dump_with_skip(
            tree, "<skip_headers>\n  b.h\n</skip_headers>\n"
        )
        stored = AbiSnapshot(library="libfoo.so", version="0.9")
        with pytest.raises(ScopeMismatchError):
            check_contracts_comparable(stored, narrowed)


class TestOnlyTheAchievedNarrowingIsRecorded:
    """A descriptor skip and a native `--exclude-header` are matched by
    different rules, so the same text is not the same scope.

    A descriptor skip is matched by ABICC's three rule classes
    (`model.header_skip_rules`: basename, component-boundary path, compiled
    pattern); `extract.header_exclusions` matches `--exclude-header` with
    `fnmatch` plus a `*/<pattern>` try. The two agree on some inputs and not
    others -- `include/foo.h` excludes `/pkg/include/foo.h` natively and,
    under the descriptor rule, matches at a component boundary anywhere,
    including trees the native try would miss. Recording the raw text for
    both made the comparability gate treat those two snapshots as covering
    the same declared surface, and it could then report fabricated additions
    or removals (Codex review).

    Bug class: one field recording two producers' values under two different
    meanings -- the "request recorded as achieved" failure this field exists
    to prevent, arriving from the other side.
    """

    def _dump(self, tree: Path, skip: str):
        from abicheck.model import AbiSnapshot
        from abicheck.serialization import load_snapshot

        desc = tree / "d.xml"
        desc.write_text(
            "<version>1.0</version>\n<headers>\n  include\n</headers>\n"
            f"<libs>\n  libs\n</libs>\n<skip_headers>\n  {skip}\n</skip_headers>\n"
        )
        out = tree / "out.json"
        with mock.patch(
            "abicheck.compat.cli.dump",
            side_effect=lambda path, **kw: AbiSnapshot(
                library="libfoo.so", version="1.0"
            ),
        ):
            result = CliRunner().invoke(
                compat_group,
                ["dump", "-lib", "foo", "-dump", str(desc), "-dump-path", str(out)],
            )
        assert result.exit_code == 0, result.output
        return load_snapshot(out), result.output

    @pytest.mark.parametrize("skip", ["b.h", "include/b.h"])
    def test_the_matching_rule_is_recorded_with_the_patterns(self, skip, tree):
        """The fact, not a guess from the text.

        Two earlier attempts decided comparability from the pattern spelling
        and both were falsified: recording the raw text for both rules, then
        dropping only metacharacter-bearing patterns. The second is wrong for
        any pattern with a path separator -- native matching also tries
        ``*/<pattern>``, so ``include/foo.h`` excludes ``/pkg/include/foo.h``
        while exact membership keeps it (Codex review).
        """
        snap, _ = self._dump(tree, skip)
        assert snap.excluded_header_matching == "abicc"
        assert snap.excluded_header_patterns == (skip,)

    @pytest.mark.parametrize("skip", ["*.h", "?.h", "[ab].h"])
    def test_a_pattern_skip_now_really_narrows_and_is_recorded(self, skip, tree):
        """A metacharacter-bearing rule is a *pattern*, and patterns match.

        This asserted the opposite until descriptor skips gained ABICC's own
        three rule classes: under the previous exact-membership test a
        pattern could match nothing at all, so there was no achieved
        narrowing to record. It now matches real headers in the fixture
        tree, so it is a real narrowing and recording it is the honest
        answer.
        """
        snap, output = self._dump(tree, skip)
        assert snap.excluded_header_patterns == (skip,)
        assert snap.excluded_header_matching == "abicc"
        assert "matched no header" not in output

    def test_a_rule_matching_nothing_records_no_narrowing_and_is_reported(self, tree):
        """The surviving half of the old wildcard case, generalized.

        A rule can still match nothing -- it just takes a typo now rather
        than a metacharacter. Such a rule narrowed nothing, so recording it
        would make the snapshot claim a reduced surface it does not have,
        and it must still be reported rather than silently doing nothing.
        """
        from abicheck.comparability import check_contracts_comparable
        from abicheck.model import AbiSnapshot

        snap, output = self._dump(tree, "nosuch/typo.h")
        assert snap.excluded_header_patterns == ()
        assert "matched no header" in output
        assert "nosuch/typo.h" in output
        # And the consequence: it must not be refused against a snapshot
        # that never had a skip rule at all.
        plain = AbiSnapshot(library="libfoo.so", version="0.9")
        assert check_contracts_comparable(plain, snap) is None

    def test_a_plain_skip_is_still_recorded(self, tree):
        """The negative control: dropping every pattern would satisfy the
        claims above completely, and would undo the recording this PR's
        predecessor added."""
        snap, output = self._dump(tree, "b.h")
        assert snap.excluded_header_patterns == ("b.h",)
        assert "matched no header" not in output

    def test_a_descriptor_and_a_native_run_are_refused_across_rules(self, tree):
        """Including for a bare name, which is the cost this accepts.

        A descriptor `b.h` and a native `b.h` do achieve the same thing, and
        are refused anyway -- because nothing in the patterns alone proves
        which pairs are equivalent, as the `include/foo.h` counterexample
        shows. Refusing a comparable pair costs a run; accepting an
        incomparable one manufactures findings.
        """
        from abicheck.comparability import check_contracts_comparable
        from abicheck.errors import ScopeMismatchError
        from abicheck.model import AbiSnapshot

        snap, _ = self._dump(tree, "b.h")
        native = AbiSnapshot(
            library="libfoo.so", version="0.9", excluded_header_patterns=("b.h",)
        )
        assert native.excluded_header_matching == "glob"
        with pytest.raises(ScopeMismatchError):
            check_contracts_comparable(native, snap)

    def test_two_descriptor_runs_still_compare(self, tree):
        """The negative control: refusing everything would satisfy the claim
        above completely. Same rule, same patterns -- comparable."""
        from abicheck.comparability import check_contracts_comparable

        snap, _ = self._dump(tree, "b.h")
        other, _ = self._dump(tree, "b.h")
        assert check_contracts_comparable(other, snap) is None

    def test_a_relative_path_skip_is_refused_against_the_native_spelling(self, tree):
        """The counterexample that falsified the previous fix: `include/foo.h`
        excludes `/pkg/include/foo.h` natively (the `*/<pattern>` branch) and
        keeps it under exact membership -- with no metacharacter anywhere."""
        from abicheck.comparability import check_contracts_comparable
        from abicheck.errors import ScopeMismatchError
        from abicheck.model import AbiSnapshot

        snap, _ = self._dump(tree, "include/foo.h")
        native = AbiSnapshot(
            library="libfoo.so",
            version="0.9",
            excluded_header_patterns=("include/foo.h",),
        )
        with pytest.raises(ScopeMismatchError):
            check_contracts_comparable(native, snap)

    def test_the_reason_names_the_differing_rules(self, tree):
        """A bare "scope differs" leaves a user with nothing to act on when
        the patterns are textually identical."""
        from abicheck.comparability import check_contracts_comparable
        from abicheck.model import AbiSnapshot

        snap, _ = self._dump(tree, "b.h")
        native = AbiSnapshot(
            library="libfoo.so", version="0.9", excluded_header_patterns=("b.h",)
        )
        mismatch = check_contracts_comparable(native, snap, diagnostic=True)
        assert "different rules" in mismatch.reason
        assert "glob" in mismatch.reason and "abicc" in mismatch.reason


class TestTheRecordedUniverseIncludesTheCliSuppliedHeaders:
    """A skip rule matching only a `-header`/`-headers-list` input is still
    an achieved narrowing.

    The universe the rules are *recorded* against must be the one they were
    *applied* to. It was the descriptor's `<headers>` alone, so a rule whose
    only match arrived via the CLI was reported as matching nothing and its
    narrowing went unrecorded (CodeRabbit review) -- and an unrecorded
    narrowing is exactly what lets the ADR-050 comparability gate accept an
    asymmetric pair, the failure `record_descriptor_skips` exists to
    prevent.

    Driven through `compat check`, because `-header` is only wired there.
    """

    def _run(self, tree: Path, skip: str, extra_header: Path):
        from abicheck.model import AbiSnapshot

        desc = tree / "d.xml"
        desc.write_text(
            "<version>1.0</version>\n<headers>\n  include\n</headers>\n"
            f"<libs>\n  libs\n</libs>\n<skip_headers>\n  {skip}\n</skip_headers>\n"
        )
        captured: dict = {}

        def _fake_dump(path, **kwargs):
            captured["headers"] = list(kwargs.get("headers") or [])
            return AbiSnapshot(library="libfoo.so", version="1.0")

        with mock.patch("abicheck.compat.cli.dump", side_effect=_fake_dump):
            result = CliRunner().invoke(
                compat_group,
                [
                    "check",
                    "-lib",
                    "foo",
                    "-old",
                    str(desc),
                    "-new",
                    str(desc),
                    "-header",
                    str(extra_header),
                ],
            )
        return captured, result.output

    def test_a_rule_matching_only_a_cli_header_is_not_called_unmatched(
        self, tree: Path
    ) -> None:
        extra = tree / "extra.h"
        extra.write_text("int extra_decl(int);\n")
        captured, output = self._run(tree, "extra.h", extra)
        # It really excluded the header ...
        assert "extra.h" not in [h.name for h in captured["headers"]]
        # ... so it must not be reported as having matched nothing.
        assert "matched no header" not in output

    def test_the_descriptors_own_headers_are_still_in_the_universe(
        self, tree: Path
    ) -> None:
        """The negative control: widening the universe must not stop a rule
        that matches a `<headers>` entry from being recognised."""
        extra = tree / "extra.h"
        extra.write_text("int extra_decl(int);\n")
        captured, output = self._run(tree, "b.h", extra)
        assert sorted(h.name for h in captured["headers"]) == ["a.h", "extra.h"]
        assert "matched no header" not in output

    def test_a_rule_matching_nothing_anywhere_is_still_reported(
        self, tree: Path
    ) -> None:
        """And the guard stays live: widening the universe must not silence
        a genuinely unreachable rule."""
        extra = tree / "extra.h"
        extra.write_text("int extra_decl(int);\n")
        _, output = self._run(tree, "nosuch.h", extra)
        assert "matched no header" in output
