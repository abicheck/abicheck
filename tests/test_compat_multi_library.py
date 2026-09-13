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
from pathlib import Path

import pytest

from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change, DiffResult
from abicheck.compat.multi_library import (
    _FIELD_POLICY,
    _WORST_SCALES,
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
        known = {"first", "concat", "union", "sum", "worst", "all", "any", "drop"}
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


@pytest.mark.integration
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
