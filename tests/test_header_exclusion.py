# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""``--exclude-header``: the shared path filter and its cache-correctness.

Before this flag existed there was no way -- flag, config key, or descriptor
element -- to exclude one header from a parse. A library whose public include
tree contains two headers that cannot be parsed in the same translation unit
(two vendored copies of a third-party API declaring conflicting typedefs; the
FFTW2/FFTW3 clash in Intel MKL's ``include/`` is the reported case) therefore
made ``-H <dir>`` fail outright, with no way to rescue it short of naming
every other header individually.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.extract.header_exclusions import (
    apply_header_exclusions,
    apply_header_exclusions_to_inputs,
)
from abicheck.serialization import load_snapshot


class TestApplyHeaderExclusions:
    """The matching rule, as a property over spellings.

    A user should not have to know which spelling of a header path this
    codebase happens to carry, so all three are tried. These are stated as
    equivalences rather than as one example per spelling: the failure mode is
    a pattern that works in one spelling and silently matches nothing in
    another, which a single-example test per spelling cannot see.
    """

    HEADERS = [
        Path("/opt/inc/mkl.h"),
        Path("/opt/inc/fftw3.h"),
        Path("/opt/inc/fftw/fftw2.h"),
        Path("/opt/inc/detail/impl.hpp"),
    ]

    def _kept(self, *patterns: str) -> list[str]:
        return [p.name for p in apply_header_exclusions(self.HEADERS, list(patterns))]

    def test_bare_name_full_path_and_glob_select_the_same_header(self) -> None:
        by_name = self._kept("fftw3.h")
        by_path = self._kept("/opt/inc/fftw3.h")
        by_glob = self._kept("*/fftw3.h")
        assert by_name == by_path == by_glob
        assert "fftw3.h" not in by_name

    def test_directory_glob_excludes_a_whole_subtree(self) -> None:
        assert "impl.hpp" not in self._kept("**/detail/*")
        assert "impl.hpp" not in self._kept("*/detail/*")

    def test_no_patterns_is_an_exact_pass_through(self) -> None:
        """Identity, not merely "same contents": every run that does not use
        the flag must be byte-for-byte what it was, since this list is hashed
        into two cache keys."""
        assert apply_header_exclusions(self.HEADERS, []) == self.HEADERS

    def test_a_pattern_matching_nothing_removes_nothing(self) -> None:
        assert apply_header_exclusions(self.HEADERS, ["nosuch.h"]) == self.HEADERS

    def test_patterns_compose(self) -> None:
        kept = self._kept("fftw3.h", "**/fftw/*")
        assert kept == ["mkl.h", "impl.hpp"]

    def test_order_is_preserved(self) -> None:
        """The header list's order is semantically real -- it is the order the
        aggregate translation unit ``#include``s them in, and it is hashed
        unsorted into the whole-snapshot cache key (ADR-050 D1/D2). A filter
        must not reorder it."""
        kept = apply_header_exclusions(self.HEADERS, ["fftw3.h"])
        assert kept == [h for h in self.HEADERS if h.name != "fftw3.h"]

    def test_exclusion_is_order_insensitive_in_the_patterns(self) -> None:
        assert self._kept("fftw3.h", "mkl.h") == self._kept("mkl.h", "fftw3.h")


class TestResolveInputExclusionWiring:
    """``resolve_input``'s own wrapper: directory expansion, and the
    pass-through that keeps every existing warm cache entry valid."""

    def test_directories_expand_only_when_a_pattern_is_given(
        self, tmp_path: Path
    ) -> None:
        """The pass-through is not an optimization, it is a compatibility
        requirement: the resolved header list is hashed into both the AST
        cache key and the whole-snapshot cache key, so expanding a directory
        unconditionally would invalidate every warm cache entry in every
        existing checkout while changing no behaviour.
        """
        inc = tmp_path / "inc"
        inc.mkdir()
        (inc / "a.h").write_text("int a(void);", encoding="utf-8")
        (inc / "b.h").write_text("int b(void);", encoding="utf-8")

        # No patterns: the directory entry survives, unexpanded.
        assert apply_header_exclusions_to_inputs([inc], ()) == [inc]

        # With a pattern: expanded, then filtered.
        got = apply_header_exclusions_to_inputs([inc], ("b.h",))
        assert [p.name for p in got] == ["a.h"]

    def test_excluding_a_header_changes_the_parse_list_and_so_the_cache_key(
        self, tmp_path: Path
    ) -> None:
        """Cache-correctness, stated where it is actually guaranteed.

        The exclusion touches neither cache-key function. It is correct only
        because both keys hash the *resolved header list*, and excluding a
        header changes that list -- so a filtered parse cannot reuse an
        unfiltered entry. Asserting the two keys differ is what pins that
        chain; asserting the filter "works" would not.
        """
        from abicheck.dumper_ast_config import _cache_key

        inc = tmp_path / "inc"
        inc.mkdir()
        (inc / "a.h").write_text("int a(void);", encoding="utf-8")
        (inc / "b.h").write_text("int b(void);", encoding="utf-8")

        unfiltered = apply_header_exclusions_to_inputs([inc], ("nosuch.h",))
        filtered = apply_header_exclusions_to_inputs([inc], ("b.h",))
        assert unfiltered != filtered

        assert _cache_key(unfiltered, [], "g++") != _cache_key(filtered, [], "g++")

    def test_a_file_entry_is_filtered_too(self, tmp_path: Path) -> None:
        """Not only directory operands: an explicitly named header is
        excludable as well, so a caller does not have to restructure how they
        pass headers to use the flag."""
        a = tmp_path / "a.h"
        b = tmp_path / "b.h"
        a.write_text("int a(void);", encoding="utf-8")
        b.write_text("int b(void);", encoding="utf-8")
        assert apply_header_exclusions_to_inputs([a, b], ("b.h",)) == [a]


class TestExcludeHeaderReachesTheRequest:
    def test_input_spec_carries_the_patterns(self) -> None:
        from abicheck.workflows.request_inputs import InputSpec

        assert InputSpec.of("x.so", exclude_headers=["a.h"]).exclude_headers == ("a.h",)

    def test_both_commands_expose_the_flag(self) -> None:
        """One spelling, on both commands -- a shared concept with two
        spellings is what ``tests/test_cli_contract.py`` exists to prevent."""
        from abicheck.cli import main

        for cmd in ("dump", "compare"):
            params = main.commands[cmd].params
            names = {p.name for p in params}
            assert "exclude_headers" in names, cmd
            opt = next(p for p in params if p.name == "exclude_headers")
            assert "--exclude-header" in opt.opts
            assert opt.multiple
            assert opt.help


@pytest.mark.integration
@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason=(
        "the negative control this test depends on does not hold off ELF: "
        "on Mach-O/PE a header-AST failure degrades to export-table mode "
        "with a warning rather than failing the dump, so the clashing "
        "directory 'succeeds' without the flag and there is no condition "
        "left for the flag to rescue. The test's own guard detects that "
        "and fails loudly rather than passing vacuously -- which is how "
        "this was found -- so it is scoped rather than weakened. The "
        "filter itself is covered platform-independently above."
    ),
)
class TestExcludeHeaderEndToEnd:
    """The reported blocker itself, against a real compiler and header AST.

    The unit tests above prove the filter filters. Only this proves the thing
    the flag exists for: that a header directory which *cannot be parsed as a
    whole* becomes usable once the offending header is excluded. A
    unit-level test cannot show that, because the failure it rescues happens
    inside the header AST backend.
    """

    @staticmethod
    def _fixture(tmp_path: Path) -> Path:
        inc = tmp_path / "inc"
        inc.mkdir()
        (inc / "good.h").write_text(
            'extern "C" { int good_fn(int x); }'
            if False
            else '#ifdef __cplusplus\nextern "C" {\n#endif\n'
            "int good_fn(int x);\n"
            "#ifdef __cplusplus\n}\n#endif\n",
            encoding="utf-8",
        )
        # Two headers that cannot coexist in one translation unit -- the
        # shape of MKL's FFTW2/FFTW3 clash, reduced to its essence.
        (inc / "clash_a.h").write_text("typedef int clashing_t;\n", encoding="utf-8")
        (inc / "clash_b.h").write_text("typedef double clashing_t;\n", encoding="utf-8")
        (tmp_path / "lib.c").write_text("int good_fn(int x){return x+1;}\n", "utf-8")
        subprocess.run(
            [
                "gcc",
                "-shared",
                "-fPIC",
                "-g",
                "-o",
                str(tmp_path / "libgood.so"),
                str(tmp_path / "lib.c"),
            ],
            check=True,
        )
        return inc

    def test_clashing_directory_fails_without_and_parses_with_the_flag(
        self, tmp_path: Path
    ) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main

        inc = self._fixture(tmp_path)
        so = str(tmp_path / "libgood.so")
        runner = CliRunner()

        # Negative control first. Without it, the test below could pass
        # against a directory that parsed fine all along, proving nothing.
        without = runner.invoke(
            main, ["dump", so, "-H", str(inc), "-o", str(tmp_path / "all.json")]
        )
        assert not (tmp_path / "all.json").exists(), (
            "the clashing header directory parsed without the flag -- this "
            "fixture no longer reproduces the condition the flag rescues, so "
            "the assertion below would be vacuous"
        )

        with_flag = runner.invoke(
            main,
            [
                "dump",
                so,
                "-H",
                str(inc),
                "--exclude-header",
                "clash_b.h",
                "-o",
                str(tmp_path / "excluded.json"),
            ],
        )
        assert with_flag.exit_code == 0, with_flag.output
        assert (tmp_path / "excluded.json").exists()

        snap = load_snapshot(tmp_path / "excluded.json")
        # The surviving headers really were parsed: the excluded one's
        # absence must not have silently emptied the surface.
        assert any(f.name == "good_fn" for f in snap.functions), (
            "excluding one header must not drop the rest of the parse"
        )
        assert without.exit_code != 0 or not without.output.strip() == ""


class TestAnExclusionIsRecordedAsReducedEvidence:
    """``--exclude-header``'s help promises the omission is "reported as
    reduced evidence rather than as a removal". Both halves, as tests.

    The *removal* half held by construction and still does: one invocation
    applies the patterns to both sides, so a symmetrically excluded
    declaration is absent from both and nothing is manufactured. The
    *reported* half was promised and unimplemented -- the patterns were
    applied before extraction and assurance saw the input, so a clean parse
    of what remained reported full header-aware assurance and a clean verdict
    over a surface nobody had looked at (Codex review).
    """

    @staticmethod
    def _snap(patterns=()):
        from abicheck.model.snapshot import AbiSnapshot

        return AbiSnapshot(
            library="libfoo.so", version="1.0", excluded_header_patterns=tuple(patterns)
        )

    def test_a_symmetric_exclusion_is_disclosed(self) -> None:
        from abicheck.checker import compare
        from abicheck.confidence import HEADER_EXCLUSION_WARNING_MARKER

        result = compare(self._snap(("fftw3.h",)), self._snap(("fftw3.h",)))
        disclosed = [
            w for w in result.coverage_warnings if HEADER_EXCLUSION_WARNING_MARKER in w
        ]
        assert len(disclosed) == 1
        assert "fftw3.h" in disclosed[0]

    def test_a_run_without_exclusions_says_nothing(self) -> None:
        """The negative control, and the compatibility claim: every run that
        does not use the flag must be unchanged, warning for warning."""
        from abicheck.checker import compare
        from abicheck.confidence import HEADER_EXCLUSION_WARNING_MARKER

        result = compare(self._snap(), self._snap())
        assert not [
            w for w in result.coverage_warnings if HEADER_EXCLUSION_WARNING_MARKER in w
        ]

    def test_differing_exclusions_are_named_as_an_asymmetry(self) -> None:
        """Reachable only with a *stored* baseline, since one invocation
        applies one set of patterns to both sides. There the asymmetry itself
        can manufacture a finding -- a declaration excluded from OLD alone
        reads as newly added -- so the warning has to say which side.

        ADR-050 comparability independently refuses such a pair on
        ``scope_fingerprint`` (the resolved header lists differ), which is the
        stronger guarantee; this states what a caller that reaches the diff
        anyway is told.
        """
        from abicheck.checker import compare
        from abicheck.confidence import HEADER_EXCLUSION_WARNING_MARKER

        result = compare(self._snap(), self._snap(("fftw3.h",)))
        disclosed = [
            w for w in result.coverage_warnings if HEADER_EXCLUSION_WARNING_MARKER in w
        ]
        assert len(disclosed) == 1
        assert "differ between the two sides" in disclosed[0]

    def test_the_patterns_survive_a_snapshot_round_trip(self) -> None:
        """Persistence is the half that matters for a *stored* baseline: a
        snapshot that forgot its own exclusions claims a complete surface, and
        the comparability check that refuses an asymmetric pair has nothing to
        refuse on."""
        from abicheck.serialization import snapshot_from_dict
        from abicheck.storage.snapshot_encode import snapshot_to_dict

        restored = snapshot_from_dict(snapshot_to_dict(self._snap(("a.h", "b.h"))))
        assert restored.excluded_header_patterns == ("a.h", "b.h")

    def test_a_pre_v47_snapshot_loads_as_no_exclusions(self) -> None:
        """Which is correct for every such snapshot: the flag did not exist."""
        from abicheck.serialization import snapshot_from_dict
        from abicheck.storage.snapshot_encode import snapshot_to_dict

        d = snapshot_to_dict(self._snap())
        d.pop("excluded_header_patterns", None)
        assert snapshot_from_dict(d).excluded_header_patterns == ()


class TestAStoredSnapshotKeepsItsOwnExclusions:
    """A stored operand's recorded patterns are provenance, not a slot.

    Loading a snapshot parses no headers, so *this* run's patterns say
    nothing about it -- and it already carries the patterns it was really
    built under. Stamping anyway overwrote that: a snapshot dumped under
    `original.h` and loaded under `--exclude-header current.h` came back
    claiming `current.h` (Codex review). Worse than a wrong label, it can
    make an asymmetric pair look symmetric, which is the comparison the
    recorded patterns exist to expose.
    """

    @staticmethod
    def _stored(tmp_path, patterns):
        from abicheck.model.snapshot import AbiSnapshot
        from abicheck.serialization import save_snapshot

        path = tmp_path / "stored.abi.json"
        save_snapshot(
            AbiSnapshot(
                library="libfoo.so",
                version="1.0",
                excluded_header_patterns=tuple(patterns),
            ),
            path,
        )
        return path

    def test_a_different_request_does_not_overwrite_them(self, tmp_path) -> None:
        from abicheck.workflows.input_resolution import resolve_input

        stored = self._stored(tmp_path, ("original.h",))
        loaded = resolve_input(stored, exclude_headers=("current.h",))
        assert loaded.excluded_header_patterns == ("original.h",)

    def test_a_request_does_not_invent_them_either(self, tmp_path) -> None:
        """The empty-provenance case: a snapshot dumped with no exclusions
        must not acquire this run's."""
        from abicheck.workflows.input_resolution import resolve_input

        stored = self._stored(tmp_path, ())
        loaded = resolve_input(stored, exclude_headers=("current.h",))
        assert loaded.excluded_header_patterns == ()

    def test_a_live_operand_is_still_stamped(self, tmp_path) -> None:
        """The negative control. Skipping the stamp for *everything* would
        satisfy both assertions above and silently undo the recording this
        field exists for."""
        from abicheck.extract.header_exclusions import record_header_exclusions
        from abicheck.model.snapshot import AbiSnapshot

        fresh = AbiSnapshot(library="libfoo.so", version="1.0")
        stamped = record_header_exclusions(fresh, ("a.h",), extracted_now=True)
        assert stamped.excluded_header_patterns == ("a.h",)

        # And the rule itself, stated directly rather than only through
        # `resolve_input`: the same call for a loaded operand records nothing.
        loaded = AbiSnapshot(library="libfoo.so", version="1.0")
        assert (
            record_header_exclusions(
                loaded, ("a.h",), extracted_now=False
            ).excluded_header_patterns
            == ()
        )


class TestExclusionsAreRefusedAgainstAManifest:
    """A pattern that cannot match anything is not silently recorded.

    A manifest dump parses the translation units and public headers the
    manifest declares, never the `-H` list this filter narrows -- so
    `--exclude-header` matched nothing while still being recorded on the
    snapshot and reported as an omission: a request recorded as achieved
    when it was not, which is the failure this whole area exists to stop
    (Codex review).
    """

    def test_the_combination_is_rejected(self) -> None:
        from abicheck.errors import ValidationError
        from abicheck.extract.header_exclusions import (
            reject_exclusions_against_a_manifest,
        )

        with pytest.raises(ValidationError, match="--dump-manifest"):
            reject_exclusions_against_a_manifest(("a.h",), object())

    @pytest.mark.parametrize(
        ("patterns", "manifest"),
        [
            ((), object()),  # a manifest dump with no exclusions
            (("a.h",), None),  # exclusions with no manifest
            ((), None),  # neither
        ],
    )
    def test_every_other_combination_is_allowed(self, patterns, manifest) -> None:
        """Only the pair is refused. Rejecting more would break a manifest
        dump that never asked for an exclusion."""
        from abicheck.extract.header_exclusions import (
            reject_exclusions_against_a_manifest,
        )

        reject_exclusions_against_a_manifest(patterns, manifest)
