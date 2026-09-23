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

"""A directory operand honors ``--exclude-header`` exactly as a file pair does.

Bug class: *an option accepted at the front end and dropped at one
dispatch branch* -- the failure never announces itself, because the flag
parses, the run proceeds, and the only symptom is that the thing the flag
was asked to prevent happens anyway.

Concretely: ``cli_compare_helpers.run_compare``'s directory/package branch
forwarded every other header input to ``_dispatch_release_compare`` and not
``exclude_headers``. A one-library *directory* pair therefore failed the
CastXML parse (Intel MKL's ``include/`` ships FFTW2 and FFTW3 headers whose
typedefs conflict) under the exact arguments that made the identical
*file* pair exit 0, and a full 28-library scan failed on all 28.

These tests are written against the *parity* claim rather than against the
MKL tree: a file pair and a one-library directory pair given the same
arguments must receive the same exclusion rules, and must both avoid the
same conflicting header. That is the invariant; the MKL tree is one input
satisfying it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model.header_exclusion_record import canonical_exclusion_identity

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="compiles an ELF .so; Linux-scoped",
)

#: The two conflicting patterns, both spelled the way a real user would.
_CONFLICTING_HEADER = "conflict2.h"


def _require_toolchain() -> None:
    """Skip unless this host can build *and parse* the fixture.

    Both halves matter. ``gcc`` builds the ``.so``; a header-AST backend
    (castxml, or clang) is what turns the ``-H`` operand into the
    declarations these tests are about. The unit-test CI lane installs
    neither backend, which is what the ``integration`` marker on the
    compiling classes below is for (AGENTS.md: "if a test needs castxml,
    mark it ``@pytest.mark.integration``"); without it these failed there
    on a bare ``castxml not found in PATH``. This guard is the local
    belt-and-braces for a run outside that lane.
    """
    if shutil.which("gcc") is None:
        pytest.skip("gcc required")
    if shutil.which("castxml") is None and shutil.which("clang") is None:
        pytest.skip("a header-AST backend (castxml or clang) is required")


def _build_conflicting_tree(root: Path, *, libname: str = "libfoo.so") -> Path:
    """A header directory that cannot be parsed whole.

    ``conflict1.h`` and ``conflict2.h`` declare the same typedef name with
    different underlying types -- the FFTW2/FFTW3 shape, reduced. Parsing
    both in one translation unit is an error; excluding either one makes
    the directory usable. Nothing here is MKL- or FFTW-specific.
    """
    inc = root / "include"
    inc.mkdir(parents=True)
    (inc / "conflict1.h").write_text(
        "#pragma once\ntypedef struct { double re, im; } clash_t;\n", encoding="utf-8"
    )
    (inc / "conflict2.h").write_text(
        "#pragma once\ntypedef struct { float re, im; } clash_t;\n", encoding="utf-8"
    )
    (inc / "api.h").write_text("#pragma once\nvoid api(void);\n", encoding="utf-8")
    src = root / "lib.c"
    src.write_text("void api(void) {}\n", encoding="utf-8")
    libdir = root / "lib"
    libdir.mkdir()
    so = libdir / libname
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-g", "-o", str(so), str(src)],
        check=True,
        capture_output=True,
    )
    return so


def _invoke(args: list[str]) -> Any:
    return CliRunner().invoke(main, args)


@pytest.mark.integration
class TestFilePairAndOneLibraryDirectoryPairAgree:
    """Claims 1-3: the same arguments, the same rules, the same outcome."""

    @staticmethod
    def _tree(tmp_path: Path, side: str) -> tuple[Path, Path]:
        root = tmp_path / side
        so = _build_conflicting_tree(root)
        return so, root / "include"

    def test_a_directory_pair_avoids_the_header_a_file_pair_avoids(
        self, tmp_path: Path
    ) -> None:
        """Claims 2 and 3 together, as one differential run.

        Both configurations are executed in this same test -- a sibling test
        proving the file pair works says nothing about whether the directory
        pair received the rules.
        """
        _require_toolchain()
        old_so, old_inc = self._tree(tmp_path, "old")
        new_so, new_inc = self._tree(tmp_path, "new")
        common = [
            "-H",
            f"old={old_inc}",
            "-H",
            f"new={new_inc}",
            "--exclude-header",
            _CONFLICTING_HEADER,
            "--depth",
            "headers",
        ]
        file_result = _invoke(["compare", str(old_so), str(new_so), *common])
        dir_result = _invoke(
            ["compare", str(old_so.parent), str(new_so.parent), *common]
        )
        # A parse of the conflicting pair fails with a redeclaration
        # diagnostic naming the clashing typedef; a run that honored the
        # exclusion never reaches it.
        for label, result in (("file", file_result), ("directory", dir_result)):
            assert "clash_t" not in result.output, (
                f"the {label} operand still parsed the excluded header:\n"
                f"{result.output}"
            )
        assert file_result.exit_code == dir_result.exit_code, (
            "a one-library directory pair must reach the same outcome as the "
            f"identical file pair (file={file_result.exit_code} "
            f"dir={dir_result.exit_code})\n"
            f"--- file ---\n{file_result.output}\n--- dir ---\n{dir_result.output}"
        )

    def test_the_dry_run_receipt_states_the_rules_for_both_operands(
        self, tmp_path: Path
    ) -> None:
        """Claim 3, observed on the *mechanism* rather than on the outcome.

        An outcome-only assertion cannot distinguish "the rules were
        applied" from "this tree happened to parse anyway". The dry-run
        receipt is the run's own statement of what it resolved, so a
        directory operand that silently discarded the rules cannot print
        them.
        """
        _require_toolchain()
        old_so, old_inc = self._tree(tmp_path, "old")
        new_so, new_inc = self._tree(tmp_path, "new")
        expected = canonical_exclusion_identity([_CONFLICTING_HEADER])
        for old, new in ((old_so, new_so), (old_so.parent, new_so.parent)):
            result = _invoke(
                [
                    "compare",
                    str(old),
                    str(new),
                    "-H",
                    f"old={old_inc}",
                    "-H",
                    f"new={new_inc}",
                    "--exclude-header",
                    _CONFLICTING_HEADER,
                    "--dry-run",
                ]
            )
            assert expected in result.output, (
                f"operand {old} did not state its exclusion rules:\n{result.output}"
            )


class TestExclusionRulesAreThreadedToEveryReleaseConsumer:
    """Claims 1 and 6, checked structurally rather than through a parse.

    The parse-level test above proves the rules reach the member
    comparison. These prove they reach the two *other* places the fan-out
    reads headers -- the per-pair service call and the stranded/one-sided
    capture -- which no successful-parse assertion can distinguish, since
    both of those are reached only on inputs that never fail.
    """

    def test_the_pairwise_primitive_forwards_them_to_the_service(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.cli_compare_release_pairwise as pairwise
        import abicheck.service as service

        seen: dict[str, Any] = {}

        class _Stop(Exception):
            pass

        def _fake_run_compare(*_a: Any, **kw: Any) -> Any:
            seen.update(kw)
            raise _Stop()

        monkeypatch.setattr(service, "run_compare", _fake_run_compare)
        if True:
            with pytest.raises(_Stop):
                pairwise._run_compare_pair(
                    Path("old.so"),
                    Path("new.so"),
                    [],
                    [],
                    [],
                    [],
                    "1",
                    "2",
                    "c",
                    None,
                    "strict_abi",
                    None,
                    None,
                    None,
                    exclude_headers=("fftw3.h", "fftw.h"),
                )
        assert seen["exclude_headers"] == ("fftw3.h", "fftw.h")

    def test_the_service_shim_writes_them_onto_both_sides(self) -> None:
        """Claim 1 at the point the rules become per-side inputs: old and
        new must receive the identical set. An asymmetric pair is refused
        outright by the comparability gate, so writing them to one side
        only would not be a partial fix -- it would break every run."""
        from abicheck import service_compare_pipeline as scp

        captured: dict[str, Any] = {}

        def _capture(request: Any) -> Any:
            captured["request"] = request
            return None

        original = scp.run_compare_request
        scp.run_compare_request = _capture  # type: ignore[assignment]
        try:
            scp.run_compare(
                Path("old.so"),
                Path("new.so"),
                exclude_headers=("a.h", "b.h"),
            )
        finally:
            scp.run_compare_request = original  # type: ignore[assignment]
        request = captured["request"]
        assert request.old.exclude_headers == ("a.h", "b.h")
        assert request.new.exclude_headers == ("a.h", "b.h")
        assert request.old.exclude_headers == request.new.exclude_headers


class TestExclusionRulesChangeConfigurationIdentity:
    """Claim 4. Two otherwise identical runs with different rules must not
    share a configuration digest."""

    @staticmethod
    def _digest(identity: str) -> str:
        from types import SimpleNamespace

        from abicheck.effective_config_digest import (
            effective_config_digest,
            effective_config_fields,
        )
        from abicheck.workflows.gate import EffectiveGate

        result = SimpleNamespace(
            policy="strict_abi",
            policy_file=None,
            excluded_header_patterns=identity,
        )
        return effective_config_digest(
            effective_config_fields(result, gate=EffectiveGate.from_severity(None))
        )

    def test_different_rule_sets_produce_different_digests(self) -> None:
        a = self._digest(canonical_exclusion_identity(["fftw3.h"]))
        b = self._digest(canonical_exclusion_identity(["fftw.h"]))
        none = self._digest(canonical_exclusion_identity([]))
        assert len({a, b, none}) == 3

    def test_equivalent_rule_sets_share_one_identity(self) -> None:
        """Canonicalization, stated as the property it is: the rules are a
        set, so order and repetition cannot change the identity -- while
        membership must. Several independently-chosen spellings of the same
        set, not just the one reordering an author would think of."""
        canonical = canonical_exclusion_identity(["a.h", "b.h", "c.h"])
        for equivalent in (
            ["c.h", "b.h", "a.h"],
            ["a.h", "a.h", "b.h", "c.h"],
            ["b.h", "c.h", "a.h", "b.h"],
            ["c.h", "a.h", "c.h", "b.h", "a.h"],
        ):
            assert canonical_exclusion_identity(equivalent) == canonical
            assert self._digest(
                canonical_exclusion_identity(equivalent)
            ) == self._digest(canonical)
        # Vacuity guard: a canonicalizer that collapsed everything to one
        # value would pass every line above.
        assert canonical_exclusion_identity(["a.h", "b.h"]) != canonical

    def test_the_matching_rule_is_part_of_the_identity(self) -> None:
        """The same pattern text does not name the same headers under
        ``glob`` as under a descriptor's rules, so it cannot share an
        identity with it."""
        from abicheck.model.header_exclusion_record import (
            DESCRIPTOR_MATCHING,
            GLOB_MATCHING,
        )

        assert canonical_exclusion_identity(
            ["include/foo.h"], GLOB_MATCHING
        ) != canonical_exclusion_identity(["include/foo.h"], DESCRIPTOR_MATCHING)


@pytest.mark.integration
class TestAnUnmatchedRuleWarnsOnceForTheWholeRun:
    """Claim 5. The rules are release-wide, so an unmatched one is a
    release-wide fact. Reporting it per library would repeat one line 28
    times for a 28-library MKL tree and bury the actual message."""

    def test_one_warning_for_a_multi_library_directory_pair(
        self, tmp_path: Path
    ) -> None:
        _require_toolchain()
        # Three libraries, so "once" is distinguishable from "once each".
        for side in ("old", "new"):
            root = tmp_path / side
            _build_conflicting_tree(root, libname="libfoo.so")
            for extra in ("libbar.so", "libbaz.so"):
                shutil.copy(root / "lib" / "libfoo.so", root / "lib" / extra)
        result = _invoke(
            [
                "compare",
                str(tmp_path / "old" / "lib"),
                str(tmp_path / "new" / "lib"),
                "-H",
                f"old={tmp_path / 'old' / 'include'}",
                "-H",
                f"new={tmp_path / 'new' / 'include'}",
                "--exclude-header",
                _CONFLICTING_HEADER,
                "--exclude-header",
                "no_such_header_anywhere.h",
            ]
        )
        occurrences = result.output.count("matched no header under -H for")
        assert occurrences == 1, (
            "a release-wide unmatched rule must be reported once, not once "
            f"per library (seen {occurrences} times):\n{result.output}"
        )
        assert "no_such_header_anywhere.h" in result.output
        # And the one warning must be the *global* one, not one library's:
        # the per-library coverage note ("Headers matching ... were
        # excluded") is a different, legitimate per-library statement about
        # that library's own narrowed surface, and its presence for all
        # three libraries is what proves the rules reached each of them.
        assert result.output.count("were excluded from the parsed surface") == 3

    def test_a_matched_rule_produces_no_warning(self, tmp_path: Path) -> None:
        """The negative control: warning about every rule would satisfy the
        claim above."""
        _require_toolchain()
        for side in ("old", "new"):
            _build_conflicting_tree(tmp_path / side)
        result = _invoke(
            [
                "compare",
                str(tmp_path / "old" / "lib"),
                str(tmp_path / "new" / "lib"),
                "-H",
                f"old={tmp_path / 'old' / 'include'}",
                "-H",
                f"new={tmp_path / 'new' / 'include'}",
                "--exclude-header",
                _CONFLICTING_HEADER,
            ]
        )
        assert "matched no header" not in result.output, result.output


class TestHeaderExclusionConfigKeyIsDistinctFromSourceCollection:
    """The config-parity half: ``scope.exclude_headers`` exists and is not
    ``sources.exclude``. Overloading one key would mean a project taking a
    vendored header out of the *parse* silently also dropped it from
    *source collection*, and the reverse."""

    def test_the_two_keys_are_parsed_independently(self) -> None:
        from abicheck.buildsource.build_config import BuildConfig

        cfg = BuildConfig.from_dict(
            {
                "scope": {"exclude_headers": ["fftw3.h"]},
                "sources": {"exclude": ["vendor/**"]},
            }
        )
        assert cfg.exclude_headers == ["fftw3.h"]
        assert cfg.exclude == ["vendor/**"]
        assert "fftw3.h" not in cfg.exclude
        assert "vendor/**" not in cfg.exclude_headers

    def test_the_key_round_trips(self) -> None:
        from abicheck.buildsource.build_config import BuildConfig

        cfg = BuildConfig.from_dict({"scope": {"exclude_headers": ["a.h", "b.h"]}})
        assert BuildConfig.from_dict(cfg.to_dict()).exclude_headers == ["a.h", "b.h"]


@pytest.mark.integration
class TestTheConfigKeyReachesDumpAndCompareAlike:
    """A config-only rule must reach both commands, or a project cannot
    compare its own baseline.

    The rules are stamped on the snapshot and an asymmetrically-narrowed
    pair is refused outright, so a key honored by `compare` and ignored by
    `dump` would produce a narrowed candidate against an unnarrowed
    baseline -- the same front-end-default asymmetry `include_dependencies`
    was fixed for, in a new place.
    """

    @staticmethod
    def _config(root: Path) -> Path:
        cfg = root / ".abicheck.yml"
        cfg.write_text(
            '{"scope": {"exclude_headers": ["' + _CONFLICTING_HEADER + '"]}}',
            encoding="utf-8",
        )
        return cfg

    def test_dump_honors_the_config_key(self, tmp_path: Path) -> None:
        _require_toolchain()
        so = _build_conflicting_tree(tmp_path)
        out = tmp_path / "base.json"
        result = _invoke(
            [
                "dump",
                str(so),
                "-H",
                str(tmp_path / "include"),
                "--config",
                str(self._config(tmp_path)),
                "-o",
                str(out),
            ]
        )
        assert result.exit_code == 0, result.output
        assert "clash_t" not in result.output, result.output

    def test_a_config_narrowed_baseline_compares_against_a_live_candidate(
        self, tmp_path: Path
    ) -> None:
        """The end the parity exists for: dump under the config key, then
        compare that baseline under the same config, and get a verdict --
        not a scope refusal and not a parse failure."""
        _require_toolchain()
        old_so = _build_conflicting_tree(tmp_path / "old")
        new_so = _build_conflicting_tree(tmp_path / "new")
        cfg = self._config(tmp_path)
        base = tmp_path / "base.json"
        dumped = _invoke(
            [
                "dump",
                str(old_so),
                "-H",
                str(tmp_path / "old" / "include"),
                "--config",
                str(cfg),
                "-o",
                str(base),
            ]
        )
        assert dumped.exit_code == 0, dumped.output
        compared = _invoke(
            [
                "compare",
                str(base),
                str(new_so),
                "-H",
                str(tmp_path / "new" / "include"),
                "--config",
                str(cfg),
            ]
        )
        assert compared.exit_code == 0, compared.output
        assert "clash_t" not in compared.output, compared.output

    def test_an_explicit_flag_takes_the_whole_decision(self, tmp_path: Path) -> None:
        """Weaker than the flag and never unioned with it -- so a run
        stating `--exclude-header` for a header the config does not name
        keeps the config's own header in the parse, and fails on it. The
        negative control that stops the wiring from quietly becoming a
        union, which would make every stated rule set wider than stated."""
        _require_toolchain()
        so = _build_conflicting_tree(tmp_path)
        result = _invoke(
            [
                "dump",
                str(so),
                "-H",
                str(tmp_path / "include"),
                "--config",
                str(self._config(tmp_path)),
                "--exclude-header",
                "no_such_header_anywhere.h",
                "-o",
                str(tmp_path / "base.json"),
            ]
        )
        assert result.exit_code != 0, (
            "an explicit --exclude-header must replace the config key, not "
            f"union with it:\n{result.output}"
        )
        assert "clash_t" in result.output


class TestTheReleaseIdentityIsReadFromItsMembers:
    """The release's exclusion identity states what was observed.

    A stored snapshot is never restamped with the current
    ``--exclude-header``, so deriving the release identity from the request
    reported "excluded nothing" for two stored packages that had in truth
    been narrowed differently -- one configuration digest for two different
    compared surfaces (CodeRabbit review), which is the exact collision
    this identity exists to prevent.
    """

    @staticmethod
    def _entry(identity: str | None) -> dict[str, object]:
        if identity is None:
            return {"library": "x"}
        return {
            "library": "x",
            "_diff_result": SimpleNamespace(excluded_header_patterns=identity),
        }

    def test_it_reads_each_completed_member(self) -> None:
        from abicheck.workflows.header_exclusion_audit import (
            observed_member_exclusion_identities,
        )

        entries = [self._entry('glob:["a.h"]'), self._entry('glob:["b.h"]')]
        assert observed_member_exclusion_identities(entries) == [
            'glob:["a.h"]',
            'glob:["b.h"]',
        ]

    def test_a_member_that_never_compared_contributes_no_observation(self) -> None:
        """Not an empty one. A failed, unmatched or uncompared member did
        not observe "excluded nothing" -- counting it as such would turn a
        mixed release into an agreeing one, which is the same
        absence-as-evidence error in a new place."""
        from abicheck.workflows.header_exclusion_audit import (
            observed_member_exclusion_identities,
        )

        entries = [self._entry('glob:["a.h"]'), self._entry(None)]
        assert observed_member_exclusion_identities(entries) == ['glob:["a.h"]']

    def test_the_capture_precedes_the_strip(self) -> None:
        """An ordering guard, because the evidence is destroyed in place.

        ``_strip_diff_results_and_adjust_verdict`` removes the very
        ``_diff_result`` the capture reads, so a capture that drifted below
        it would silently observe nothing at all and fall back to the
        request -- restoring the defect with every unit test still passing.
        """
        import inspect

        from abicheck import cli_compare_release

        src = inspect.getsource(cli_compare_release)
        capture = src.index("observed_member_exclusion_identities(")
        strip = src.index("_strip_diff_results_and_adjust_verdict(\n")
        assert capture < strip, (
            "the observed-member capture must run before the DiffResults "
            "it reads are stripped"
        )
