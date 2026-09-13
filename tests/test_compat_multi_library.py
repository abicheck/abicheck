# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Multi-library ``compat check``: descriptor ``<libs>`` pairing and the
per-library result merge (``abicheck.compat.multi_library``).

Before this module existed, ``compat check`` compared ``desc.libs[0]`` and
printed a warning naming the rest, so a descriptor covering a 28-library
release was answered by comparing one library and reporting the verdict as
though it described the release. The tests here state the two properties
that has to satisfy: nothing the descriptor named is silently uncompared,
and nothing absent on one side is manufactured into a removal.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change, DiffResult
from abicheck.compat.descriptor import CompatDescriptor
from abicheck.compat.multi_library import (
    _FIELD_POLICY,
    _WORST_SCALES,
    _soname_stem,
    merge_results,
    pair_libraries,
)


def _result(
    library: str, *, verdict: Verdict = Verdict.NO_CHANGE, **kw: object
) -> DiffResult:
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library=library,
        verdict=verdict,
        **kw,  # type: ignore[arg-type]
    )


class TestPairing:
    def test_exact_names_pair(self) -> None:
        pairs, old_only, new_only = pair_libraries(
            [Path("o/libа.so"), Path("o/libb.so")],
            [Path("n/libb.so"), Path("n/libа.so")],
        )
        assert [(o.name, n.name) for o, n in pairs] == [
            ("libb.so", "libb.so"),
            ("libа.so", "libа.so"),
        ]
        assert old_only == [] and new_only == []

    def test_soname_bump_still_pairs(self) -> None:
        """A major SONAME bump is the ordinary case a name-only join gets
        wrong -- it reads as one library removed and a different one added,
        which is the single worst way to report a release that bumped."""
        pairs, old_only, new_only = pair_libraries(
            [Path("o/libfoo.so.1.2.3")], [Path("n/libfoo.so.2.0.0")]
        )
        assert [(o.name, n.name) for o, n in pairs] == [
            ("libfoo.so.1.2.3", "libfoo.so.2.0.0")
        ]
        assert old_only == [] and new_only == []

    def test_ambiguous_stem_is_left_unpaired_rather_than_guessed(self) -> None:
        """Two libraries sharing a stem on one side cannot be matched without
        guessing, and a wrong guess attributes one library's findings to
        another. Both sides stay unpaired and are reported."""
        pairs, old_only, new_only = pair_libraries(
            [Path("o/libfoo.so.1")],
            [Path("n/libfoo.so.2"), Path("n/libfoo.so.3")],
        )
        assert pairs == []
        assert [p.name for p in old_only] == ["libfoo.so.1"]
        assert [p.name for p in new_only] == ["libfoo.so.2", "libfoo.so.3"]

    def test_unmatched_entries_are_returned_not_dropped(self) -> None:
        pairs, old_only, new_only = pair_libraries(
            [Path("o/libkeep.so"), Path("o/libgone.so")],
            [Path("n/libkeep.so"), Path("n/libnew.so")],
        )
        assert [(o.name, n.name) for o, n in pairs] == [("libkeep.so", "libkeep.so")]
        assert [p.name for p in old_only] == ["libgone.so"]
        assert [p.name for p in new_only] == ["libnew.so"]

    def test_pairing_is_order_insensitive(self) -> None:
        """The result must not depend on the order the descriptor happened to
        list its libraries in."""
        old = [Path("o/liba.so"), Path("o/libb.so"), Path("o/libc.so")]
        new = [Path("n/libc.so"), Path("n/liba.so"), Path("n/libd.so")]
        forward = pair_libraries(old, new)
        reverse = pair_libraries(list(reversed(old)), list(reversed(new)))
        assert forward == reverse

    def test_every_input_is_accounted_for(self) -> None:
        """The partition property: every library given is either in a pair or
        in exactly one unmatched list. This is what "nothing is silently
        uncompared" means mechanically."""
        old = [Path(f"o/lib{c}.so") for c in "abcdef"]
        new = [Path(f"n/lib{c}.so") for c in "cdefgh"]
        pairs, old_only, new_only = pair_libraries(old, new)
        assert sorted(
            [p.name for p, _ in pairs] + [p.name for p in old_only]
        ) == sorted(p.name for p in old)
        assert sorted(
            [n.name for _, n in pairs] + [p.name for p in new_only]
        ) == sorted(p.name for p in new)


class TestMergePolicyIsExhaustive:
    """``DiffResult`` carries 62 fields and grows. A hand-written merge drops
    every field added after it was written -- silently, and in the direction
    that reads as success (an empty ledger, a ``None`` assurance, a missing
    contract context all look like "nothing to report"). The policy table is
    what turns that into a hard error, and this class is what keeps the table
    honest.
    """

    def test_every_field_has_a_policy(self) -> None:
        missing = [
            f.name
            for f in dataclasses.fields(DiffResult)
            if f.name not in _FIELD_POLICY
        ]
        assert not missing, (
            "DiffResult fields with no multi-library merge policy: "
            f"{missing}. Add each to compat.multi_library._FIELD_POLICY."
        )

    def test_no_stale_policy_entries(self) -> None:
        """The other direction: a policy for a field that no longer exists is
        dead configuration that outlives its subject, the same way a deleted
        module's mypy override does (see the ``mypy-override-targets``
        gate)."""
        real = {f.name for f in dataclasses.fields(DiffResult)}
        stale = sorted(set(_FIELD_POLICY) - real)
        assert not stale, f"policy entries for nonexistent fields: {stale}"

    def test_policy_values_are_known(self) -> None:
        known = {
            "first",
            "concat",
            "union",
            "sum",
            "worst",
            "all",
            "any",
            "drop",
            "assurance_block",
        }
        unknown = {v for v in _FIELD_POLICY.values()} - known
        assert not unknown, f"unknown merge policies: {sorted(unknown)}"

    def test_every_worst_field_declares_a_scale(self) -> None:
        """``_worst`` falls back to the first library's value for a field with
        no declared ordinal, which is exactly the silent-wrong-answer the
        table exists to prevent -- so every ``"worst"`` field must have one."""
        worst_fields = {k for k, v in _FIELD_POLICY.items() if v == "worst"}
        assert worst_fields <= set(_WORST_SCALES), (
            "fields merged as 'worst' with no ordinal in _WORST_SCALES: "
            f"{sorted(worst_fields - set(_WORST_SCALES))}"
        )

    def test_enum_scales_match_their_enum_exactly(self) -> None:
        """Vacuity guard on the ordinals themselves.

        ``_rank`` treats a value missing from its scale as worst, so a scale
        written against the wrong spelling ranks *every* value equally and
        ``max`` silently returns the first library's -- which is what a
        lowercase ``verdict`` scale did here, against an enum whose values are
        uppercase, until this test was written. Checking membership rather
        than trusting the literals is the only thing that catches it, since
        the wrong answer is a plausible one.
        """
        from abicheck.checker_types import Confidence
        from abicheck.policy.evidence_status import EvidenceTier

        for field, enum in (
            ("verdict", Verdict),
            ("confidence", Confidence),
            ("evidence_tier", EvidenceTier),
        ):
            assert set(_WORST_SCALES[field]) == {m.value for m in enum}, field
            assert len(_WORST_SCALES[field]) == len(list(enum)), field

    def test_unlisted_field_raises_rather_than_defaulting(self) -> None:
        """The enforcement itself, exercised rather than assumed: with a
        policy entry removed, the merge must fail loudly."""
        saved = _FIELD_POLICY.pop("changes")
        try:
            with pytest.raises(AssertionError, match="no multi-library merge policy"):
                merge_results([_result("liba.so")], label="x")
        finally:
            _FIELD_POLICY["changes"] = saved


class TestMergeSemantics:
    def test_worst_verdict_wins(self) -> None:
        merged = merge_results(
            [
                _result("liba.so", verdict=Verdict.COMPATIBLE),
                _result("libb.so", verdict=Verdict.BREAKING),
                _result("libc.so", verdict=Verdict.COMPATIBLE_WITH_RISK),
            ],
            label="3 libraries",
        )
        assert merged.verdict == Verdict.BREAKING

    def test_worst_verdict_is_order_insensitive(self) -> None:
        a = _result("liba.so", verdict=Verdict.COMPATIBLE)
        b = _result("libb.so", verdict=Verdict.API_BREAK)
        assert (
            merge_results([a, b], label="x").verdict
            == merge_results([b, a], label="x").verdict
            == Verdict.API_BREAK
        )

    def test_findings_from_every_library_survive(self) -> None:
        merged = merge_results(
            [
                _result(
                    "liba.so",
                    changes=[
                        Change(kind=ChangeKind.FUNC_ADDED, symbol="a", description="")
                    ],
                ),
                _result(
                    "libb.so",
                    changes=[
                        Change(kind=ChangeKind.FUNC_ADDED, symbol="b", description="")
                    ],
                ),
            ],
            label="2 libraries",
        )
        assert {c.symbol for c in merged.changes} == {"a", "b"}

    def test_counts_sum(self) -> None:
        merged = merge_results(
            [
                _result("liba.so", old_symbol_count=10, suppressed_count=1),
                _result("libb.so", old_symbol_count=32, suppressed_count=2),
            ],
            label="2 libraries",
        )
        assert merged.old_symbol_count == 42
        assert merged.suppressed_count == 3

    def test_dropped_fields_reset_to_their_declared_default(self) -> None:
        """A dropped per-library field must be indistinguishable from one the
        run never populated -- not ``None`` written into a non-optional field,
        which type-checks nowhere and breaks the first renderer to iterate
        it."""
        merged = merge_results(
            [_result("liba.so"), _result("libb.so")], label="2 libraries"
        )
        assert merged.evidence_metrics == {}
        assert merged.old_metadata is None
        assert merged.library == "2 libraries"

    def test_evidence_tiers_union_without_duplicates(self) -> None:
        merged = merge_results(
            [
                _result("liba.so", evidence_tiers=["elf", "dwarf"]),
                _result("libb.so", evidence_tiers=["dwarf", "header"]),
            ],
            label="2 libraries",
        )
        assert merged.evidence_tiers == ["dwarf", "elf", "header"]

    def test_scope_resolved_requires_every_library(self) -> None:
        """``all``, not ``any``: one library whose public surface could not be
        resolved means the release's compatibility is unconfirmed."""
        merged = merge_results(
            [
                _result("liba.so", scope_resolved=True),
                _result("libb.so", scope_resolved=False),
            ],
            label="2 libraries",
        )
        assert merged.scope_resolved is False

    def test_single_result_merges_to_an_equivalent_result(self) -> None:
        """The degenerate case has to be a no-op on everything the merge does
        not deliberately reset -- otherwise the fan-out would change the
        answer for one-library descriptors, which are the overwhelming
        majority of real ones."""
        one = _result(
            "liba.so",
            verdict=Verdict.COMPATIBLE,
            changes=[Change(kind=ChangeKind.FUNC_ADDED, symbol="a", description="")],
            old_symbol_count=7,
            evidence_tiers=["elf"],
        )
        merged = merge_results([one], label="liba.so")
        for field in ("verdict", "old_symbol_count", "evidence_tiers", "library"):
            assert getattr(merged, field) == getattr(one, field), field
        assert [c.symbol for c in merged.changes] == ["a"]

    def test_empty_input_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="at least one result"):
            merge_results([], label="x")


class TestUnpairedLibrariesAreRecorded:
    """A library on only one side is a coverage warning, never a finding.

    ADR-065's "absent is not removed": a descriptor's ``<libs>`` list is a
    *selection*, not an inventory, so an entry with no counterpart on the
    other side is "not supplied there" -- which is a different fact from
    "removed from the release", and proving the latter needs inventory
    evidence a descriptor does not carry. Manufacturing a removal from it
    would be the same false break this whole change set exists to stop;
    dropping it silently would hide that the run covered less than the
    descriptor named.
    """

    def test_both_directions_become_coverage_warnings(self) -> None:
        from abicheck.compat.cli import _record_unpaired_libraries

        out = _record_unpaired_libraries(
            _result("liba.so"),
            [Path("old/libgone.so")],
            [Path("new/libnew.so")],
            True,
        )
        joined = " ".join(out.coverage_warnings)
        assert "libgone.so" in joined
        assert "libnew.so" in joined
        # Not findings, and not a verdict change.
        assert out.changes == []
        assert out.verdict == Verdict.NO_CHANGE

    def test_the_old_side_warning_says_it_is_not_a_removal(self) -> None:
        """The wording is the disposition. A reader who sees a library listed
        and nothing else will reasonably assume it was deleted."""
        from abicheck.compat.cli import _record_unpaired_libraries

        out = _record_unpaired_libraries(
            _result("liba.so"), [Path("old/libgone.so")], [], True
        )
        assert "not evidence of removal" in out.coverage_warnings[0]

    def test_nothing_unpaired_is_an_exact_no_op(self) -> None:
        from abicheck.compat.cli import _record_unpaired_libraries

        r = _result("liba.so")
        assert _record_unpaired_libraries(r, [], [], True) is r


class TestLibsDirectoryDiscoveryIsPlatformAware:
    """``<libs>`` directory expansion must work on every container format.

    Found by the macOS integration lane. The first version delegated to
    ``package.discover_shared_libraries``, which recognises ELF only, so a
    ``<libs>`` directory expanded to nothing off Linux and the caller then
    hard-errored -- making the whole capability silently Linux-only while
    every Linux test passed.

    Written against synthetic magic bytes rather than compiled artifacts so
    it exercises all three formats *on any host*: a test that could only
    build the host's own format is exactly what let the gap through.
    """

    #: Minimal leading magic for each container format.
    MAGIC = {
        "elf": b"\x7fELF",
        "macho": b"\xcf\xfa\xed\xfe",
        "pe": b"MZ\x90\x00",
    }

    def _write(self, directory: Path, name: str, fmt: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        f = directory / name
        f.write_bytes(self.MAGIC[fmt] + b"\x00" * 64)
        return f

    def test_macho_and_pe_libraries_are_discovered(self, tmp_path: Path) -> None:
        from abicheck.compat.descriptor_expansion import expand_descriptor_libs

        d = tmp_path / "libs"
        self._write(d, "libfoo.dylib", "macho")
        self._write(d, "libbar.1.dylib", "macho")
        self._write(d, "baz.dll", "pe")
        found = {p.name for p in expand_descriptor_libs([d])}
        assert found == {"libfoo.dylib", "libbar.1.dylib", "baz.dll"}

    def test_non_binaries_are_not_discovered(self, tmp_path: Path) -> None:
        """Vacuity guard: a rule that accepted everything would pass the
        test above and quietly feed a README to the binary parser."""
        from abicheck.compat.descriptor_expansion import expand_descriptor_libs

        d = tmp_path / "libs"
        self._write(d, "libfoo.dylib", "macho")
        (d / "README.txt").write_text("not a binary", encoding="utf-8")
        (d / "notes.json").write_text("{}", encoding="utf-8")
        assert {p.name for p in expand_descriptor_libs([d])} == {"libfoo.dylib"}

    def test_an_empty_directory_still_raises(self, tmp_path: Path) -> None:
        """Expanding to nothing would let a descriptor pointing at the wrong
        tree produce a confident verdict off an empty surface."""
        from abicheck.compat.descriptor_expansion import expand_descriptor_libs
        from abicheck.errors import ValidationError

        d = tmp_path / "libs"
        d.mkdir()
        (d / "README.txt").write_text("nothing here", encoding="utf-8")
        with pytest.raises(ValidationError, match="no shared libraries"):
            expand_descriptor_libs([d])

    def test_a_file_operand_passes_through_untouched(self, tmp_path: Path) -> None:
        from abicheck.compat.descriptor_expansion import expand_descriptor_libs

        f = self._write(tmp_path, "libfoo.dylib", "macho")
        assert expand_descriptor_libs([f]) == [f]

    def test_nested_directories_are_searched(self, tmp_path: Path) -> None:
        from abicheck.compat.descriptor_expansion import expand_descriptor_libs

        self._write(tmp_path / "libs" / "sub", "libnested.dylib", "macho")
        assert [p.name for p in expand_descriptor_libs([tmp_path / "libs"])] == [
            "libnested.dylib"
        ]


@pytest.mark.integration
@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason=(
        "asserts concrete verdict/exit semantics for a removed symbol, "
        "which this environment can only verify for ELF. Directory "
        "discovery itself -- the part that was Linux-only and is now not "
        "-- is covered on every platform by "
        "TestLibsDirectoryDiscoveryIsPlatformAware above."
    ),
)
class TestCompatCheckMultiLibraryEndToEnd:
    """The descriptor path itself, against real binaries.

    The unit tests above cover pairing and merging in isolation. This is what
    proves the wiring: that a descriptor naming a `<libs>` *directory* of
    several libraries actually compares all of them, rather than comparing
    the first and warning about the rest.
    """

    @staticmethod
    def _release(root: Path, *, with_break: bool) -> Path:
        """Two libraries; in the 'new' release one of them drops a symbol."""
        import subprocess

        root.mkdir(parents=True, exist_ok=True)
        (root / "a.c").write_text("int a_kept(void){return 1;}\n", encoding="utf-8")
        b_src = "int b_kept(void){return 2;}\n"
        if not with_break:
            b_src += "int b_dropped(void){return 3;}\n"
        (root / "b.c").write_text(b_src, encoding="utf-8")
        libs = root / "lib"
        libs.mkdir(exist_ok=True)
        for name in ("a", "b"):
            subprocess.run(
                [
                    "gcc",
                    "-shared",
                    "-fPIC",
                    "-g",
                    "-o",
                    str(libs / f"lib{name}.so"),
                    str(root / f"{name}.c"),
                ],
                check=True,
            )
        return libs

    @staticmethod
    def _descriptor(path: Path, version: str, libs: Path) -> Path:
        # Deliberately the rootless ABICC *fragment* form with a *directory*
        # <libs> value -- the two shapes real ABICC pipelines use, and the
        # two this command could not read at all before.
        path.write_text(
            f"<version>{version}</version>\n<libs>{libs}</libs>\n", encoding="utf-8"
        )
        return path

    def test_a_break_in_the_second_library_is_not_missed(self, tmp_path: Path) -> None:
        """The failure the fan-out exists to prevent, stated as the case that
        actually loses information: the break is in the library that is *not*
        first, so comparing only ``libs[0]`` reports a clean release.
        """
        from click.testing import CliRunner

        from abicheck.cli import main

        old_libs = self._release(tmp_path / "old", with_break=False)
        new_libs = self._release(tmp_path / "new", with_break=True)
        old_desc = self._descriptor(tmp_path / "old.xml", "1.0", old_libs)
        new_desc = self._descriptor(tmp_path / "new.xml", "2.0", new_libs)

        result = CliRunner().invoke(
            main,
            [
                "compat",
                "check",
                "-lib",
                "demo",
                "-old",
                str(old_desc),
                "-new",
                str(new_desc),
            ],
        )
        # `liba.so` sorts first and is unchanged; the removal is in
        # `libb.so`, so comparing only `libs[0]` reports a clean release.
        assert "Compared 2 libraries" in result.output, result.output
        assert "Verdict: BREAKING" in result.output, result.output
        # compat's own ABICC exit scheme: 1 == BREAKING.
        assert result.exit_code == 1, result.output
        # And the merged result really carries the second library's finding,
        # not merely a verdict that happens to be right.
        assert "problems: 1" in result.output, result.output

    def test_an_unchanged_release_stays_clean(self, tmp_path: Path) -> None:
        """Negative control. Without it the assertion above could pass on a
        fan-out that reports a break for every release."""
        from click.testing import CliRunner

        from abicheck.cli import main

        old_libs = self._release(tmp_path / "old", with_break=False)
        new_libs = self._release(tmp_path / "new", with_break=False)
        old_desc = self._descriptor(tmp_path / "old.xml", "1.0", old_libs)
        new_desc = self._descriptor(tmp_path / "new.xml", "2.0", new_libs)

        result = CliRunner().invoke(
            main,
            [
                "compat",
                "check",
                "-lib",
                "demo",
                "-old",
                str(old_desc),
                "-new",
                str(new_desc),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "b_dropped" not in result.output


class TestZeroPairPlanRefusesToInventAComparison:
    """A multi-library descriptor pair in which nothing pairs must not
    produce a verdict.

    Found in review. ``_plan_library_pairs`` returned ``([], [], [])`` for
    two different situations -- "not a multi-library comparison" and "a
    multi-library comparison that paired nothing" -- and the caller's
    ``or [(None, None)]`` fallback turned the second into "compare
    ``libs[0]`` against ``libs[0]``": two *unrelated* libraries, yielding a
    real BREAKING-or-clean verdict that the unpaired-library warnings
    appended afterwards could not undo. ADR-065: a run that completed zero
    comparisons never reads as a clean pass.
    """

    @staticmethod
    def _desc(libs: list[str]) -> CompatDescriptor:
        return CompatDescriptor(
            version="1.0", headers=[], libs=[Path(f"/fake/{n}") for n in libs]
        )

    def test_a_zero_pair_plan_is_distinguishable_from_no_plan(self) -> None:
        """The distinction the bug turned on, stated directly: ``None``
        means "no fan-out applies", an empty pair list means "a fan-out
        that matched nothing". Collapsing them is what let a verdict be
        invented."""
        from abicheck.compat.cli import _plan_library_pairs

        # Not a multi-library comparison at all -> None.
        assert (
            _plan_library_pairs(self._desc(["liba.so"]), self._desc(["liba.so"]))
            is None
        )

        # Multi-library, nothing pairs -> a real plan with zero pairs.
        plan = _plan_library_pairs(
            self._desc(["libalpha1.so", "libalpha2.so"]),
            self._desc(["libbeta1.so", "libbeta2.so"]),
        )
        assert plan is not None
        pairs, old_only, new_only = plan
        assert pairs == []
        assert len(old_only) == 2 and len(new_only) == 2

    def test_a_snapshot_against_one_library_is_not_a_fan_out(self) -> None:
        """A JSON/Perl dump names no library list, so there is nothing to fan
        out over -- and that is ``None``, not a zero-pair plan. The ordinary
        single-library case must stay exactly as it was."""
        from abicheck.compat.cli import _plan_library_pairs
        from abicheck.model import AbiSnapshot

        snap = AbiSnapshot(library="libfoo.so", version="1.0")
        single = self._desc(["libfoo.so"])
        assert _plan_library_pairs(snap, single) is None
        assert _plan_library_pairs(single, snap) is None

    def test_a_snapshot_against_many_libraries_is_refused(self) -> None:
        """One snapshot is one library. Against a descriptor naming several,
        nothing in the inputs says which entry it corresponds to.

        The old behaviour took ``libs[0]`` and warned about the rest -- a real
        verdict about whichever library the descriptor listed first, against a
        snapshot that may be a different library entirely (Codex review). Both
        operand orders are asserted, because the ambiguity does not depend on
        which side holds the snapshot.
        """
        from abicheck.compat.cli import _plan_library_pairs
        from abicheck.errors import ScopeMismatchError
        from abicheck.model import AbiSnapshot

        snap = AbiSnapshot(library="libfoo.so", version="1.0")
        multi = self._desc(["liba.so", "libb.so"])
        for args in ((snap, multi), (multi, snap)):
            with pytest.raises(ScopeMismatchError) as excinfo:
                _plan_library_pairs(*args)
            # The entries have to be named: an error that only says
            # "ambiguous" leaves the caller guessing at what to narrow.
            assert "liba.so" in str(excinfo.value)
            assert "libb.so" in str(excinfo.value)

    def test_two_snapshots_are_still_not_a_fan_out(self) -> None:
        """Neither side carries a ``<libs>`` list, so there is no ambiguity to
        refuse -- only one library on each side, as before."""
        from abicheck.compat.cli import _plan_library_pairs
        from abicheck.model import AbiSnapshot

        a = AbiSnapshot(library="libfoo.so", version="1.0")
        b = AbiSnapshot(library="libfoo.so", version="2.0")
        assert _plan_library_pairs(a, b) is None

    def test_the_error_names_every_unpaired_library(self) -> None:
        """A user has to be able to see *why* nothing paired; an error that
        only says "no pairs" leaves them guessing at names."""
        from abicheck.compat.cli import _no_library_pair_error

        exc = _no_library_pair_error(
            [Path("old/libalpha1.so"), Path("old/libalpha2.so")],
            [Path("new/libbeta1.so")],
        )
        text = str(exc)
        for name in ("libalpha1.so", "libalpha2.so", "libbeta1.so"):
            assert name in text

    def test_one_pair_plus_unpaired_still_compares(self) -> None:
        """The boundary in the other direction: a plan that pairs *some*
        libraries is a real comparison, and must not be refused because
        others went unpaired."""
        from abicheck.compat.cli import _plan_library_pairs

        plan = _plan_library_pairs(
            self._desc(["libshared.so", "libalpha.so"]),
            self._desc(["libshared.so", "libbeta.so"]),
        )
        assert plan is not None
        pairs, old_only, new_only = plan
        assert [(o.name, n.name) for o, n in pairs] == [
            ("libshared.so", "libshared.so")
        ]
        assert [p.name for p in old_only] == ["libalpha.so"]
        assert [p.name for p in new_only] == ["libbeta.so"]


class TestWorstScalesAreOrderedWorstLast:
    """Every ``"worst"`` ordinal must actually rank the *worst* value last.

    Found in review. The depth and ``evidence_tier`` scales were written in
    their natural reading order (shallow -> deep), so ``max`` selected the
    *strongest* member: a two-library release with one binary-only member
    and one reaching source evidence reported ``effective_depth='source'``
    and ``HEADER_AWARE`` release-wide — presenting the best member's
    assurance as the release's, the exact inversion of "weaker evidence
    narrows conclusions".

    The membership tests that were here first passed throughout, because a
    reversed scale contains exactly the right values. Only direction catches
    it, so direction is what this class asserts.
    """

    #: (field, better value, worse value). Spelled out rather than derived
    #: from the scale under test — an oracle read off the same table would
    #: agree with it however it is ordered.
    CASES = (
        ("old_evidence_depth", "source", "binary"),
        ("new_evidence_depth", "headers", "debug"),
        ("effective_depth", "build", "binary"),
        ("evidence_tier", "header_aware", "elf_only"),
        ("confidence", "high", "low"),
        ("surface_scope_confidence", "high", "reduced"),
        ("verdict", "COMPATIBLE", "BREAKING"),
    )

    def test_the_worse_value_wins_in_both_argument_orders(self) -> None:
        from abicheck.compat.multi_library import _worst

        offenders = []
        for field, better, worse in self.CASES:
            for values in ([better, worse], [worse, better]):
                if _worst(field, list(values)) != worse:
                    offenders.append(f"{field}: {values} -> {_worst(field, values)}")
        assert not offenders, (
            "a 'worst' field selected the better value (scale ordered "
            "best-last, or max/min inverted): " + "; ".join(offenders)
        )

    def test_every_worst_field_is_covered_by_a_direction_case(self) -> None:
        """Vacuity guard on the table above: a new ``"worst"`` field with no
        direction case would silently inherit the bug this class exists to
        catch."""
        from abicheck.compat.multi_library import _FIELD_POLICY

        worst_fields = {k for k, v in _FIELD_POLICY.items() if v == "worst"}
        covered = {field for field, _b, _w in self.CASES}
        # Single-valued scales have no direction to test.
        single = {"contract_coverage", "assurance"}
        assert worst_fields - covered - single == set()

    def test_an_unknown_value_ranks_worst(self) -> None:
        """Missing evidence is not evidence of good evidence: a value absent
        from its scale must not be beaten by a known-good one."""
        from abicheck.compat.multi_library import _worst

        assert _worst("effective_depth", ["source", "some-future-depth"]) == (
            "some-future-depth"
        )

    def test_merge_reports_the_weakest_members_evidence(self) -> None:
        """The property end-to-end, through the real merge rather than the
        ranking helper: a release is only as well-evidenced as its least
        well-evidenced member."""
        from abicheck.policy.evidence_status import EvidenceTier

        merged = merge_results(
            [
                _result(
                    "libdeep.so",
                    effective_depth="source",
                    evidence_tier=EvidenceTier.HEADER_AWARE,
                ),
                _result(
                    "libshallow.so",
                    effective_depth="binary",
                    evidence_tier=EvidenceTier.ELF_ONLY,
                ),
            ],
            label="2 libraries",
        )
        assert merged.effective_depth == "binary"
        assert merged.evidence_tier == EvidenceTier.ELF_ONLY


class TestPairingIsAmbiguitySafeOnBothSides:
    """No pair is emitted unless *both* sides resolve the key uniquely.

    A recursive ``<libs>`` expansion routinely finds one basename under
    several directories -- an architecture split is the ordinary case for a
    release like Intel MKL, not a corner one. Keying a dictionary by basename
    silently kept the last insertion, so the run compared one architecture's
    OLD against another's NEW and reported the rest as merely unpaired: a
    wrong verdict presented as a complete one (Codex review).

    Stated as the primitive's contract rather than as one repro, per
    ``AGENTS.md``'s primitive-level property guidance -- the original defect
    is invisible in every non-duplicated case, which is most of them.
    """

    def test_no_emitted_pair_ever_spans_an_ambiguous_key(self) -> None:
        """The invariant, swept over every duplication shape on either side.

        The oracle is independent of the implementation: count how many
        candidates each side has for the paired key and require both to be 1.
        """
        shapes = {
            "dup-new": (["a/libfoo.so"], ["x/libfoo.so", "y/libfoo.so"]),
            "dup-old": (["x/libfoo.so", "y/libfoo.so"], ["a/libfoo.so"]),
            "dup-both": (
                ["x/libfoo.so", "y/libfoo.so"],
                ["a/libfoo.so", "b/libfoo.so"],
            ),
            "dup-stem-new": (["a/libfoo.so.1"], ["x/libfoo.so.2", "y/libfoo.so.3"]),
            "dup-stem-old": (["x/libfoo.so.1", "y/libfoo.so.2"], ["a/libfoo.so.3"]),
            "clean": (["x/libfoo.so", "y/libbar.so"], ["a/libfoo.so", "b/libbar.so"]),
        }
        offenders: list[str] = []
        for label, (olds, news) in shapes.items():
            old_paths = [Path(p) for p in olds]
            new_paths = [Path(p) for p in news]
            pairs, old_only, new_only = pair_libraries(old_paths, new_paths)
            for o, n in pairs:
                n_old = sum(1 for p in old_paths if p.name == o.name)
                n_new = sum(1 for p in new_paths if p.name == n.name)
                if o.name == n.name and not (n_old == 1 and n_new == 1):
                    offenders.append(f"{label}: paired {o} with {n}")
            # Nothing is ever lost: every input appears exactly once in the
            # union of pairs and unpaired lists.
            seen_old = [o for o, _n in pairs] + list(old_only)
            seen_new = [n for _o, n in pairs] + list(new_only)
            if sorted(seen_old) != sorted(old_paths):
                offenders.append(f"{label}: OLD not accounted for")
            if sorted(seen_new) != sorted(new_paths):
                offenders.append(f"{label}: NEW not accounted for")
        assert not offenders, offenders

    def test_duplicate_basenames_are_left_unpaired_not_guessed(self) -> None:
        """The concrete architecture-split case, and its negative control.

        Without the second half, an implementation that paired *nothing* would
        satisfy the first half completely.
        """
        old = [Path("lib/intel64/libfoo.so"), Path("lib/ia32/libfoo.so")]
        new = [Path("lib/intel64/libfoo.so"), Path("lib/ia32/libfoo.so")]
        pairs, old_only, new_only = pair_libraries(old, new)
        assert pairs == []
        assert len(old_only) == 2 and len(new_only) == 2

        # Negative control: an unambiguous release still pairs.
        pairs, old_only, new_only = pair_libraries(
            [Path("lib/libfoo.so")], [Path("lib/libfoo.so")]
        )
        assert [(o.name, n.name) for o, n in pairs] == [("libfoo.so", "libfoo.so")]
        assert old_only == [] and new_only == []


class TestSonameStemNormalisesEveryPlatformsVersionConvention:
    """ELF puts the version after the extension, macOS/Windows before it.

    Truncating at the extension marker handles only the first, so
    ``libfoo.1.dylib``/``libfoo.2.dylib`` keyed apart and a SONAME bump read
    as one library removed and another added (Codex review).
    """

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            # ELF: version follows the extension.
            ("libfoo.so", "libfoo"),
            ("libfoo.so.1", "libfoo"),
            ("libfoo.so.1.2.3", "libfoo"),
            # macOS: version precedes it.
            ("libfoo.dylib", "libfoo"),
            ("libfoo.1.dylib", "libfoo"),
            ("libfoo.1.2.3.dylib", "libfoo"),
            # Windows.
            ("foo.dll", "foo"),
            ("foo-1.dll", "foo"),
            ("foo_2.dll", "foo"),
            # A name whose identity ends in a word keeps it.
            ("libfoo_debug.so.1", "libfoo_debug"),
            ("libfoo-static.1.dylib", "libfoo-static"),
            # A name that is nothing but a version is not stripped away
            # (``Path.stem`` drops the last component, the version strip then
            # leaves the rest standing) -- collapsing it to ``""`` would key
            # every such file together.
            ("1.2", "1"),
        ],
    )
    def test_stem(self, name: str, expected: str) -> None:
        assert _soname_stem(Path("some/dir") / name) == expected

    def test_a_stem_is_never_empty(self) -> None:
        """The guarantee that matters: an empty key would collapse unrelated
        libraries into one group. Swept over the degenerate shapes rather
        than asserted for one."""
        for name in ("1", "1.2", "1.2.3", ".so", ".dylib", "lib.so.1", "-1", "_1"):
            assert _soname_stem(Path(name)) != "", name

    def test_every_version_of_one_library_shares_one_stem(self) -> None:
        """The property the pairing pass actually depends on, stated over
        generated version shapes rather than the two spellings that were
        reported."""
        for template in ("lib{}.so{}", "lib{}{}.dylib", "{}{}.dll"):
            for base in ("foo", "bar_baz", "qux-quux"):
                stems = {
                    _soname_stem(Path(template.format(base, suffix)))
                    for suffix in ("", ".1", ".2", ".10", ".1.2", ".2.0.1")
                }
                assert len(stems) == 1, (template, base, stems)

    def test_a_soname_bump_pairs_on_every_platform(self) -> None:
        """End to end through ``pair_libraries``, with the must-not-pair
        control beside it: two genuinely different libraries must not be
        collapsed by the same normalisation."""
        for old_name, new_name in [
            ("libfoo.so.1", "libfoo.so.2"),
            ("libfoo.1.dylib", "libfoo.2.dylib"),
            ("foo-1.dll", "foo-2.dll"),
        ]:
            pairs, old_only, new_only = pair_libraries(
                [Path(old_name)], [Path(new_name)]
            )
            assert [(o.name, n.name) for o, n in pairs] == [(old_name, new_name)]
            assert old_only == [] and new_only == []

        pairs, old_only, new_only = pair_libraries(
            [Path("libfoo.1.dylib")], [Path("libbar.1.dylib")]
        )
        assert pairs == []
        assert len(old_only) == 1 and len(new_only) == 1


class TestDescriptorDirectoryExpansionRefusesToSayNothing:
    """An expansion that finds nothing is an error, never an empty surface.

    ``vision.md``'s "weaker evidence narrows conclusions": a ``<headers>``
    directory naming no parseable header, or a ``<libs>`` directory holding
    no library, must say so. Returning an empty list would let the run
    continue and report a confident verdict over a surface nobody supplied --
    which is the failure mode this whole capability was added to fix, in a
    new place.
    """

    def test_a_headers_directory_with_no_headers_raises(self, tmp_path) -> None:
        from abicheck.compat.descriptor_expansion import expand_descriptor_headers
        from abicheck.errors import ValidationError

        empty = tmp_path / "include"
        empty.mkdir()
        (empty / "README.txt").write_text("not a header", encoding="utf-8")
        with pytest.raises(ValidationError, match="no supported header files"):
            expand_descriptor_headers([empty])

    def test_a_headers_directory_with_one_header_does_not(self, tmp_path) -> None:
        """The negative control: an implementation that raised unconditionally
        would satisfy the claim above completely."""
        from abicheck.compat.descriptor_expansion import expand_descriptor_headers

        d = tmp_path / "include"
        d.mkdir()
        (d / "api.h").write_text("int f(void);\n", encoding="utf-8")
        assert [p.name for p in expand_descriptor_headers([d])] == ["api.h"]

    def test_a_non_binary_file_is_not_taken_for_a_library(self, tmp_path) -> None:
        """Format detection runs before the name rule, so a text file named
        ``libfoo.so`` is still not a library."""
        from abicheck.compat.descriptor_expansion import expand_descriptor_libs
        from abicheck.errors import ValidationError

        d = tmp_path / "lib"
        d.mkdir()
        (d / "libfoo.so").write_text(
            "#!/bin/sh\necho not a library\n", encoding="utf-8"
        )
        (d / "notes.md").write_text("hello", encoding="utf-8")
        with pytest.raises(ValidationError):
            expand_descriptor_libs([d])


class TestDescriptorOptionsDoNotOverrideTheCommandLine:
    """A descriptor states the project's default; the command line overrides it.

    GCC is last-wins for a repeated order-sensitive flag, so the order the
    two are concatenated in *is* the precedence rule. The first version
    appended the descriptor last and documented the opposite outcome, which
    made `-gcc-options -DNAME=cli` silently lose to the descriptor's own
    `<gcc_options>` (Codex review).
    """

    @staticmethod
    def _combined(desc_opts: str, cli_opts: str) -> str:
        # The same one-line expression `_snapshot_from_compat_input` builds.
        return " ".join(opt for opt in (desc_opts, cli_opts) if opt)

    def test_the_command_lines_value_comes_last(self) -> None:
        from abicheck.compat.descriptor import CompatDescriptor
        from abicheck.compat.multi_library_run import _descriptor_compile_options

        desc = CompatDescriptor(
            version="1.0",
            headers=[],
            libs=[],
            defines=["NAME=descriptor"],
            gcc_options=["-std=c++14"],
        )
        combined = self._combined(_descriptor_compile_options(desc), "-DNAME=cli")
        assert combined.index("-DNAME=descriptor") < combined.index("-DNAME=cli")

    def test_a_descriptor_only_run_still_carries_its_flags(self) -> None:
        """Negative control: the swap must not drop the descriptor's own
        options when no CLI options were given."""
        from abicheck.compat.descriptor import CompatDescriptor
        from abicheck.compat.multi_library_run import _descriptor_compile_options

        desc = CompatDescriptor(
            version="1.0",
            headers=[],
            libs=[],
            include_paths=[Path("/opt/inc")],
            defines=["ONLY=1"],
        )
        combined = self._combined(_descriptor_compile_options(desc), "")
        assert "-I/opt/inc" in combined
        assert "-DONLY=1" in combined


class TestMergedFindingsKeepTheirLibrary:
    """Which DSO produced a finding survives the merge.

    Merging N results concatenates bare ``Change`` objects and replaces the
    one library identifier with a release-wide label (``"2 libraries"``), so
    without attribution a reader cannot tell which library a removal came
    from -- and the *same symbol* removed from two of them collapses into two
    identical lines (Codex review). That is the multi-library counterpart of
    the `libs[0]` problem this module exists to fix: a real answer that does
    not say what it is an answer about.
    """

    @staticmethod
    def _removal(symbol: str) -> Change:
        return Change(ChangeKind.FUNC_REMOVED, symbol, f"{symbol} removed")

    def test_each_finding_names_the_library_it_came_from(self) -> None:
        merged = merge_results(
            [
                _result("liba.so", changes=[self._removal("shared_symbol")]),
                _result("libb.so", changes=[self._removal("shared_symbol")]),
            ],
            label="2 libraries",
        )
        assert sorted(c.library or "" for c in merged.changes) == [
            "liba.so",
            "libb.so",
        ]

    def test_the_same_symbol_from_two_libraries_stays_distinguishable(self) -> None:
        """The property the attribution exists for. Without it these two are
        identical objects and a reader sees one finding reported twice with no
        way to tell which library either belongs to."""
        merged = merge_results(
            [
                _result("liba.so", changes=[self._removal("dup")]),
                _result("libb.so", changes=[self._removal("dup")]),
            ],
            label="2 libraries",
        )
        assert len({(c.symbol, c.library) for c in merged.changes}) == 2

    def test_a_single_library_merge_leaves_findings_untouched(self) -> None:
        """ "One model, any cardinality": a one-member descriptor and the
        scalar path must produce the same findings, so nothing is stamped
        when there is nothing to disambiguate."""
        only = self._removal("f")
        merged = merge_results([_result("liba.so", changes=[only])], label="liba.so")
        assert merged.changes[0].library is None
        assert merged.changes[0] is only

    def test_the_inputs_are_not_mutated(self) -> None:
        """A merge is a projection: it must not change the results its caller
        still holds."""
        original = self._removal("f")
        inputs = [
            _result("liba.so", changes=[original]),
            _result("libb.so", changes=[self._removal("g")]),
        ]
        merge_results(inputs, label="2 libraries")
        assert original.library is None
        assert inputs[0].changes[0] is original

    def test_every_concatenated_finding_list_is_attributed(self) -> None:
        """Not just ``changes``. A suppressed or out-of-surface finding needs
        the same answer, and listing only the one field a test happened to
        check is how the next list gets missed."""
        merged = merge_results(
            [
                _result(
                    "liba.so",
                    changes=[self._removal("a")],
                    suppressed_changes=[self._removal("b")],
                    out_of_surface_changes=[self._removal("c")],
                ),
                _result("libb.so", changes=[self._removal("d")]),
            ],
            label="2 libraries",
        )
        for finding in (
            *merged.changes,
            *merged.suppressed_changes,
            *merged.out_of_surface_changes,
        ):
            assert finding.library, finding.symbol
