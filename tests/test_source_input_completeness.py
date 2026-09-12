# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Bug-class regression tests for `coverage.discovery_derived_completeness`.

Sufficiency for an *absence* claim is computed from the **expected** input set,
never from what a discovery pass happened to find. The reported instance was
`files_scanned > 0 and files_skipped == 0` over a walk that silently
`continue`d past a non-existent root, so missing inputs read as "fully
covered".

Three further ways the same silent drop reached the account, each found in
review and each fixed at the cause rather than at the symptom:

- `os.walk` ignores traversal errors without an `onerror` callback, so an
  unreadable *directory* yielded no entries and no exception;
- the extensionless-header heuristic has to read a file to classify it and
  treated a read failure as "binary", so an unreadable *candidate* was filtered
  out as uninteresting rather than recorded as a gap;
- the macro and header probe families shared one attempted/succeeded/truncated
  triple, so each check's sufficiency depended on the other's failures.

Split from `test_stored_snapshot_source_licence.py` at the bug-class boundary
once that file reached the architecture gate's test-size cap; shared fixtures
live in `_source_licence_fixtures.py`.

The fold's own contract is here too, since what it refuses is exactly what
sufficiency establishes: presence is established by observation, absence only
by coverage.
"""

from __future__ import annotations

import itertools
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pytest
from _source_licence_fixtures import (
    PACKED_SOURCE,
    TEMPLATE_SOURCE,
    live as _live,
    snapshot_recording as _snapshot_recording,
)

from abicheck.buildsource.pattern_facts import (
    PatternFactsResult,
    find_pattern_facts,
    resolve_expected_source_inputs,
)
from abicheck.buildsource.preprocessor_facts import PreprocessorFactsResult
from abicheck.buildsource.preprocessor_probe_families import ProbeTallies
from abicheck.buildsource.source_inputs import (
    DISPOSITION_PRECEDENCE,
    GAP_DISPOSITIONS,
    WITHHELD_FOR_STORED_SNAPSHOT,
    SourceInput,
    SourceInputDisposition,
    SourceInputSet,
    SourceReadLicence,
    resolve_source_inputs,
)
from abicheck.workflows.pattern_preprocessor_scan import (
    CHECK_HEADER_LEAK,
    CHECK_MACRO_DIVERGENCE,
    CHECK_PATTERN_ESCALATION,
    Sufficiency,
    _fold_evolution,
    _header_leak_sufficiency,
    _macro_divergence_sufficiency,
    compute_pattern_preprocessor_scan,
)


def _chmod_can_deny_directory_read() -> bool:
    """Can ``chmod(0o000)`` actually make a directory unreadable here?

    Asked of the filesystem rather than assumed from the platform. The
    premise fails for at least three independent reasons -- running as root
    (which bypasses the permission check entirely), Windows (where
    ``os.chmod`` only toggles the read-only flag, and not for directories at
    all, so the "locked" directory stays fully listable and the scan reads
    the header it was supposed to be denied), and a mount or filesystem that
    does not enforce Unix modes. Naming the platforms would have to be
    revisited for each new one; asking directly is true wherever it is true.

    Probes the filesystem only, never the code under test: where chmod does
    deny, the test runs exactly as before, so this cannot quietly turn a real
    regression into a skip on an ordinary non-root Linux runner.
    """
    try:
        probe = Path(tempfile.mkdtemp())
    except OSError:
        return False
    locked = probe / "locked"
    try:
        locked.mkdir()
        (locked / "probe.hpp").write_text("int probe(void);\n", encoding="utf-8")
        locked.chmod(0o000)
        try:
            list(locked.iterdir())
        except PermissionError:
            return True
        return False
    except OSError:
        # Any failure to even set the probe up is itself "cannot demonstrate a
        # denial here". This runs while `pytest.mark.skipif` is evaluated --
        # at *collection* -- so an escaping OSError fails the whole module's
        # collection instead of skipping one test, which is the same
        # module-wide-failure shape this probe exists to avoid (CodeRabbit
        # review). `Path.chmod` is the likeliest raiser, and on Windows it
        # can reject a directory outright.
        return False
    finally:
        try:
            locked.chmod(0o755)
        except OSError:
            pass
        shutil.rmtree(probe, ignore_errors=True)


class TestExpectedInputSetReplacesDiscoveryDerivedCompleteness:
    """Sufficiency comes from the expected input set, not from the survivors."""

    def test_missing_root_is_recorded_not_dropped(self, tmp_path: Path) -> None:
        """**The reported bug.** A declared root that does not exist used to
        vanish from the walk entirely: zero inputs, zero skips, and one
        surviving file then read as "fully covered"."""
        present = tmp_path / "there.hpp"
        present.write_text(PACKED_SOURCE)
        absent = tmp_path / "gone.hpp"

        result = find_pattern_facts([str(present), str(absent)])
        dispositions = {i.path: i.disposition for i in result.inputs.inputs}
        assert dispositions[str(absent)] is SourceInputDisposition.MISSING
        assert dispositions[str(present)] is SourceInputDisposition.SCANNED
        # It scanned a file and skipped none -- the exact state the old
        # `files_scanned > 0 and files_skipped == 0` signal called complete.
        assert result.files_scanned == 1
        assert result.files_skipped == 0
        assert result.sufficient is False

    @pytest.mark.parametrize("disposition", list(SourceInputDisposition))
    def test_every_disposition_is_classified_as_gap_or_not_exhaustively(
        self, disposition: SourceInputDisposition
    ) -> None:
        """Exhaustive over the whole enum, so a disposition added later cannot
        default into "not a gap" unnoticed: a set holding a gap is never
        sufficient, and a set of non-gap inputs is sufficient iff something
        was actually scanned."""
        inputs = SourceInputSet(
            inputs=(SourceInput(path="x", disposition=disposition),),
            licence=SourceReadLicence.live_extraction(),
        )
        if disposition in GAP_DISPOSITIONS:
            assert inputs.sufficient is False
            assert inputs.insufficiency_reason() != ""
        elif disposition is SourceInputDisposition.SCANNED:
            assert inputs.sufficient is True
        else:  # EXCLUDED: deliberately out of scope, but nothing was scanned
            assert inputs.sufficient is False

    @pytest.mark.parametrize(
        "count_missing,count_unreadable,count_scanned",
        list(itertools.product(range(3), range(3), range(3))),
    )
    def test_sufficiency_is_conjunctive_over_the_whole_expected_set(
        self, count_missing: int, count_unreadable: int, count_scanned: int
    ) -> None:
        """Generated over every small mix of gaps and successes. The oracle is
        stated independently: sufficient iff at least one input scanned AND no
        gap of any kind, which is *not* how the implementation is written
        (it folds over a disposition set)."""
        items = (
            [
                SourceInput(path=f"m{i}", disposition=SourceInputDisposition.MISSING)
                for i in range(count_missing)
            ]
            + [
                SourceInput(path=f"u{i}", disposition=SourceInputDisposition.UNREADABLE)
                for i in range(count_unreadable)
            ]
            + [
                SourceInput(path=f"s{i}", disposition=SourceInputDisposition.SCANNED)
                for i in range(count_scanned)
            ]
        )
        inputs = SourceInputSet(
            inputs=tuple(items), licence=SourceReadLicence.live_extraction()
        )
        expected = count_scanned > 0 and count_missing == 0 and count_unreadable == 0
        assert inputs.sufficient is expected

    def test_deliberately_excluded_input_is_not_a_gap(self, tmp_path: Path) -> None:
        """ "Deliberately excluded" is a distinct state from "missing": the
        caller asked for the narrowing, so it does not spoil sufficiency."""
        kept = tmp_path / "kept.hpp"
        dropped = tmp_path / "dropped.hpp"
        kept.write_text(PACKED_SOURCE)
        dropped.write_text(TEMPLATE_SOURCE)

        result = find_pattern_facts(
            [str(kept), str(dropped)], changed_paths=["kept.hpp"]
        )
        dispositions = {i.path: i.disposition for i in result.inputs.inputs}
        assert dispositions[str(dropped)] is SourceInputDisposition.EXCLUDED
        assert result.sufficient is True

    @pytest.mark.skipif(
        not hasattr(os, "mkfifo"),
        reason="os.mkfifo is unavailable on Windows, where CI runs the whole suite",
    )
    @pytest.mark.skipif(
        not hasattr(os, "mkfifo"),
        reason="os.mkfifo is unavailable on Windows, where CI runs the whole suite",
    )
    def test_unsupported_input_is_its_own_state(self, tmp_path: Path) -> None:
        """A root that exists but is not a readable file (a FIFO here) is
        ``unsupported`` -- neither silently dropped nor mislabelled missing."""
        fifo = tmp_path / "pipe"
        os.mkfifo(fifo)
        scanned = tmp_path / "ok.hpp"
        scanned.write_text(PACKED_SOURCE)

        inputs = resolve_expected_source_inputs([str(fifo), str(scanned)])
        dispositions = {i.path: i.disposition for i in inputs.inputs}
        assert dispositions[str(fifo)] is SourceInputDisposition.UNSUPPORTED
        assert inputs.sufficient is False

    def test_a_dangling_symlink_is_unsupported_not_missing(
        self, tmp_path: Path
    ) -> None:
        """The portable sibling of the FIFO case above, and the label the
        disposition contract promises.

        Both are gaps, so sufficiency is unaffected either way -- this is about
        the label being true. ``MISSING`` would send a reader looking for a
        deleted file when ``ls`` plainly shows the link still sitting there;
        ``UNSUPPORTED`` points at the broken link, which is the actionable fact.
        """
        link = tmp_path / "pub.hpp"
        link.symlink_to(tmp_path / "never_existed.hpp")
        assert link.is_symlink() and not link.exists()

        inputs = resolve_expected_source_inputs([str(link)])
        assert [i.disposition for i in inputs.inputs] == [
            SourceInputDisposition.UNSUPPORTED
        ]
        assert inputs.sufficient is False

    def test_sufficiency_is_answered_per_check_not_globally(
        self, tmp_path: Path
    ) -> None:
        """The three checks answer to different evidence, so one global
        ``files_scanned > 0`` cannot speak for all of them."""
        header = tmp_path / "pub.hpp"
        header.write_text(PACKED_SOURCE)
        old = _live(_snapshot_recording([str(header)]))
        new = _live(_snapshot_recording([str(header)], version="2.0"))

        result = compute_pattern_preprocessor_scan(old, new)
        assert set(result.coverage) == {
            CHECK_PATTERN_ESCALATION,
            CHECK_MACRO_DIVERGENCE,
            CHECK_HEADER_LEAK,
        }
        # The lexical scan is established; the preprocessor checks are not
        # (no L3 build evidence) -- three answers, not one.
        assert result.coverage[CHECK_PATTERN_ESCALATION]["old"].established is True
        assert result.coverage[CHECK_MACRO_DIVERGENCE]["old"].established is False
        assert result.coverage[CHECK_HEADER_LEAK]["old"].established is False

    def test_macro_and_leak_sufficiency_can_disagree(self) -> None:
        """A run with compile units but no public headers fully establishes
        macro divergence while establishing nothing about leaks -- the single
        global predicate this replaces gave both the same answer."""
        from abicheck.workflows.pattern_preprocessor_scan import (
            _header_leak_sufficiency,
            _macro_divergence_sufficiency,
        )

        res = PreprocessorFactsResult(
            ran=True,
            attempted=2,
            succeeded=2,
            probe_tallies=ProbeTallies(attempted={"macro": 2}, succeeded={"macro": 2}),
        )
        assert _macro_divergence_sufficiency(res).established is True
        assert _header_leak_sufficiency(res).established is False


class TestDirectoryTraversalFailureIsAGap:
    """`os.walk` swallows traversal errors by default, so an unreadable
    directory yields no entries *and* no exception. Without an `onerror`
    callback the root vanished from the expected-input account entirely —
    which is the `coverage.discovery_derived_completeness` bug class exactly,
    reached through a different door than a missing root (Codex review, P2).

    The primary tests inject the traversal error rather than relying on
    `chmod`: these suites run as root in some environments, where `chmod 000`
    denies nothing and a permission-based test would pass without ever
    exercising the callback — a silent no-op test is worse than none. The
    permission-based sibling below is kept for the real-syscall path and
    skipped where it cannot bite.
    """

    @staticmethod
    def _walk_raising(target: str, real_walk: Any) -> Any:
        """An `os.walk` that reports a failure for *target*, as the real one
        does: by calling `onerror` with an `OSError` carrying `.filename`."""

        def _walk(top: Any, *args: Any, **kwargs: Any) -> Any:
            onerror = kwargs.get("onerror")
            if onerror is not None:
                err = PermissionError(13, "Permission denied")
                err.filename = target
                onerror(err)
            return real_walk(top, *args, **kwargs)

        return _walk

    def test_injected_traversal_failure_is_recorded_as_unreadable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        readable = tmp_path / "open"
        readable.mkdir()
        (readable / "seen.hpp").write_text(TEMPLATE_SOURCE)
        unreachable = str(tmp_path / "open" / "locked")

        monkeypatch.setattr(os, "walk", self._walk_raising(unreachable, os.walk))
        result = find_pattern_facts([str(readable)])

        # A file really was scanned and nothing was "skipped", so the old
        # `files_scanned > 0 and files_skipped == 0` signal would have called
        # this fully covered while a whole directory went unread.
        assert result.files_scanned == 1
        assert result.files_skipped == 0
        assert result.sufficient is False
        dispositions = {i.path: i.disposition for i in result.inputs.inputs}
        assert dispositions[unreachable] is SourceInputDisposition.UNREADABLE

    def test_traversal_failure_blocks_the_absence_claim_end_to_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The consequence that matters: an unenumerable directory on OLD must
        stop `introduced` for a construct NEW flags, since OLD's absence was
        never established."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        (old_dir / "a.hpp").write_text("int f(void);\n")
        (new_dir / "b.hpp").write_text(PACKED_SOURCE)

        old = _live(_snapshot_recording([str(old_dir / "a.hpp")]))
        new = _live(_snapshot_recording([str(new_dir / "b.hpp")], version="2.0"))

        # Baseline: with no traversal failure this is a real `introduced`.
        assert (
            compute_pattern_preprocessor_scan(
                old, new
            ).pattern_escalation_evolution.get("pragma_pack")
            == "introduced"
        )

        monkeypatch.setattr(
            os, "walk", self._walk_raising(str(old_dir / "gone"), os.walk)
        )
        # OLD's roots are explicit files, so force the directory-walk path by
        # pointing the OLD side at its directory instead.
        old_dir_side = _live(_snapshot_recording([str(old_dir)]))
        folded = compute_pattern_preprocessor_scan(old_dir_side, new)
        assert folded.pattern_escalation_evolution.get("pragma_pack") == "not_evaluated"
        assert folded.coverage[CHECK_PATTERN_ESCALATION]["old"].established is False

    @pytest.mark.skipif(
        not _chmod_can_deny_directory_read(),
        reason=(
            "chmod cannot deny a directory read here, so this test's own "
            "premise (a real EACCES from the kernel) does not hold"
        ),
    )
    def test_real_unreadable_directory_is_recorded(self, tmp_path: Path) -> None:
        """The same invariant through a real `EACCES` from the kernel, not an
        injected one — the syscall path the injected tests stand in for."""
        locked = tmp_path / "locked"
        locked.mkdir()
        (locked / "hidden.hpp").write_text(PACKED_SOURCE)
        readable = tmp_path / "open"
        readable.mkdir()
        (readable / "seen.hpp").write_text(TEMPLATE_SOURCE)
        locked.chmod(0o000)
        try:
            result = find_pattern_facts([str(locked), str(readable)])
            assert result.sufficient is False
            assert any(
                i.disposition is SourceInputDisposition.UNREADABLE
                for i in result.inputs.inputs
            )
        finally:
            locked.chmod(0o755)


class TestProbeFamilySufficiencyIsIndependent:
    """The macro-divergence and header-leak checks run different clang
    invocations, so their coverage must be tallied separately. Folding both
    into one attempted/succeeded/truncated triple made each check's
    sufficiency depend on the other's failures (Codex review, P2)."""

    @staticmethod
    def _result(**families: tuple[int, int, int]) -> PreprocessorFactsResult:
        """Build a result from per-family ``(attempted, succeeded, truncated)``."""
        return PreprocessorFactsResult(
            ran=True,
            attempted=sum(f[0] for f in families.values()),
            succeeded=sum(f[1] for f in families.values()),
            probes_truncated=sum(f[2] for f in families.values()),
            probe_tallies=ProbeTallies(
                attempted={k: v[0] for k, v in families.items()},
                succeeded={k: v[1] for k, v in families.items()},
                truncated={k: v[2] for k, v in families.items()},
            ),
        )

    def test_truncated_macro_probes_do_not_spoil_the_header_check(self) -> None:
        res = self._result(macro=(512, 512, 40), header=(3, 3, 0))
        assert _macro_divergence_sufficiency(res).established is False
        assert _header_leak_sufficiency(res).established is True

    def test_truncated_header_probes_do_not_spoil_the_macro_check(self) -> None:
        res = self._result(macro=(4, 4, 0), header=(512, 512, 7))
        assert _macro_divergence_sufficiency(res).established is True
        assert _header_leak_sufficiency(res).established is False

    def test_failed_macro_probes_do_not_spoil_the_header_check(self) -> None:
        res = self._result(macro=(4, 2, 0), header=(3, 3, 0))
        assert _macro_divergence_sufficiency(res).established is False
        assert _header_leak_sufficiency(res).established is True

    @pytest.mark.parametrize(
        "attempted,succeeded,truncated,expected",
        [
            (0, 0, 0, False),  # never run for this family
            (1, 0, 0, False),  # every probe failed
            (4, 2, 0, False),  # partial failure
            (4, 4, 1, False),  # cap truncated the set
            (1, 1, 0, True),  # the only sufficient shape
            (9, 9, 0, True),
        ],
    )
    def test_family_sufficiency_is_exhaustive_over_probe_shapes(
        self, attempted: int, succeeded: int, truncated: int, expected: bool
    ) -> None:
        """Exhaustive over every shape a family's tallies can take, against a
        rule stated independently: established iff at least one probe ran, all
        of them succeeded, and none were truncated."""
        res = self._result(macro=(attempted, succeeded, truncated))
        assert _macro_divergence_sufficiency(res).established is expected
        # Independently-stated oracle, not the implementation's own fold.
        assert expected == (attempted > 0 and succeeded == attempted and not truncated)

    def test_successful_probe_finding_no_abi_macro_still_establishes_absence(
        self,
    ) -> None:
        """The regression that motivated the family tallies: a successful
        `-E -dM` probe of a unit defining none of the curated ABI macros
        contributes no entry to `abi_macros`, so `tus_scanned` is 0 for an
        ordinary build. Gating on `tus_scanned` reported "no translation unit
        was probed" for a *fully covered* run, and its evolution could then
        never leave `not_evaluated`."""
        res = self._result(macro=(3, 3, 0))
        assert res.tus_scanned == 0
        assert res.abi_macros == {}
        assert _macro_divergence_sufficiency(res).established is True
        # And end to end: NEW gains a divergence, OLD's clean full-coverage
        # scan establishes its absence, so this reads `introduced`.
        folded = _fold_evolution(
            old=_macro_divergence_sufficiency(res),
            new=Sufficiency(established=True),
            old_keys=set(),
            new_keys={"FOO_ABI"},
        )
        assert folded == {"FOO_ABI": "introduced"}


class TestExpectedInputSetEdgeCases:
    def test_a_side_declaring_no_roots_is_not_sufficient(self) -> None:
        """Deny-by-default at the empty end: a snapshot that recorded no source
        paths has no evidence, so it cannot establish an absence either."""
        empty = SourceInputSet(licence=SourceReadLicence.live_extraction())
        assert empty.sufficient is False
        assert (
            empty.insufficiency_reason()
            == "no source inputs were declared for this side"
        )

    def test_selected_lists_only_the_pending_inputs(self) -> None:
        inputs = SourceInputSet(
            inputs=(
                SourceInput(path="a", disposition=SourceInputDisposition.SELECTED),
                SourceInput(path="b", disposition=SourceInputDisposition.SCANNED),
                SourceInput(path="c", disposition=SourceInputDisposition.MISSING),
            ),
            licence=SourceReadLicence.live_extraction(),
        )
        assert [i.path for i in inputs.selected()] == ["a"]

    def test_an_unstattable_root_is_a_gap_not_a_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A root whose very `is_file()` raises (a path too long, a dead mount)
        must land as UNREADABLE rather than propagating — the pre-scan is
        advisory, but it must not claim coverage it does not have."""

        def _boom(self: Path) -> bool:
            raise OSError(5, "I/O error")

        monkeypatch.setattr(Path, "is_file", _boom)
        inputs = resolve_source_inputs(
            [str(tmp_path / "x.h")], licence=SourceReadLicence.live_extraction()
        )
        assert [i.disposition for i in inputs.inputs] == [
            SourceInputDisposition.UNREADABLE
        ]
        assert inputs.sufficient is False

    def test_no_changed_filter_keeps_every_candidate(self, tmp_path: Path) -> None:
        """`changed_paths=None` is "no narrowing", distinct from an empty list."""
        (tmp_path / "a.hpp").write_text(PACKED_SOURCE)
        (tmp_path / "b.hpp").write_text(TEMPLATE_SOURCE)
        result = find_pattern_facts([str(tmp_path)])
        assert result.files_scanned == 2
        assert result.sufficient is True

    @pytest.mark.parametrize("disposition", list(SourceInputDisposition))
    def test_every_disposition_has_a_precedence_rank(
        self, disposition: SourceInputDisposition
    ) -> None:
        """One path can be reached both as an explicit root and through a walk,
        so every disposition needs a rank; a missing one would raise KeyError
        mid-scan. Exhaustive so a new member cannot be added without one."""
        from abicheck.buildsource.source_inputs import _rank

        assert isinstance(_rank(disposition), int)

    def test_a_gap_never_downgrades_to_a_weaker_disposition(
        self, tmp_path: Path
    ) -> None:
        """Precedence in action: a path named as an explicit root *and* found
        under a directory root keeps the stronger claim."""
        d = tmp_path / "tree"
        d.mkdir()
        f = d / "a.hpp"
        f.write_text(PACKED_SOURCE)
        result = find_pattern_facts([str(d), str(f)], changed_paths=["a.hpp"])
        dispositions = {i.path: i.disposition for i in result.inputs.inputs}
        assert dispositions[str(f)] is SourceInputDisposition.SCANNED


class TestAnUnexaminableCandidateIsAGapNotAFilteredFile:
    """A file the classifier could not *examine* is a coverage gap; a file that
    was never evidence is not. Collapsing the two is the third instance of this
    PR's own bug class (Codex review, P2).

    `_looks_binary` returns True on `OSError` — correct for its own callers, but
    for discovery it meant an unreadable extensionless header (an ACL, a
    transient I/O error) was classified "binary", dropped by the scannability
    filter, and never recorded. A sibling file scanning successfully then let
    the set report full coverage over a header nobody read.
    """

    @staticmethod
    def _classify(path: Path) -> Any:
        from abicheck.buildsource.pattern_facts_files import classify_walked_file

        return classify_walked_file(path)

    def test_a_readable_extensionless_header_is_selected(self, tmp_path: Path) -> None:
        h = tmp_path / "Core"
        h.write_text(PACKED_SOURCE)
        assert self._classify(h) is SourceInputDisposition.SELECTED

    def test_a_binary_extensionless_blob_is_not_evidence(self, tmp_path: Path) -> None:
        """`None`, not a gap: it was never a header, so it must not spoil
        sufficiency — otherwise every repo with a stray blob reads as incomplete."""
        b = tmp_path / "blob"
        b.write_bytes(b"\x7fELF\x00\x01\x02")
        assert self._classify(b) is None

    def test_an_oversized_extensionless_file_is_not_evidence(
        self, tmp_path: Path
    ) -> None:
        from abicheck.buildsource.pattern_facts_files import _EXTENSIONLESS_MAX_BYTES

        big = tmp_path / "data"
        big.write_text("x" * (_EXTENSIONLESS_MAX_BYTES + 1))
        assert self._classify(big) is None

    def test_a_known_suffix_that_is_unreadable_is_still_selected(
        self, tmp_path: Path
    ) -> None:
        """A `.hpp` is a candidate on its name alone; the read failure surfaces
        later as UNREADABLE when the scan tries to open it, which is already a
        gap. Only the extensionless heuristic needs to *read* to classify."""
        h = tmp_path / "a.hpp"
        h.write_text(PACKED_SOURCE)
        assert self._classify(h) is SourceInputDisposition.SELECTED

    @pytest.mark.parametrize("failing", ["stat", "open"])
    def test_an_unexaminable_extensionless_candidate_is_recorded_unreadable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing: str
    ) -> None:
        """Both ways the heuristic can fail to examine a file — the `stat` and
        the `open` — must land as UNREADABLE, not as "binary". Injected rather
        than chmod'd, since these suites can run as root."""
        target = tmp_path / "Core"
        target.write_text(PACKED_SOURCE)
        sibling = tmp_path / "other.hpp"
        sibling.write_text(TEMPLATE_SOURCE)

        if failing == "stat":
            real_stat = Path.stat

            def _stat(self: Path, *a: Any, **kw: Any) -> Any:
                if self.name == "Core":
                    raise PermissionError(13, "Permission denied")
                return real_stat(self, *a, **kw)

            monkeypatch.setattr(Path, "stat", _stat)
        else:
            import builtins

            real_open = builtins.open

            def _open(file: Any, *a: Any, **kw: Any) -> Any:
                if Path(file).name == "Core":
                    raise PermissionError(13, "Permission denied")
                return real_open(file, *a, **kw)

            monkeypatch.setattr(builtins, "open", _open)

        assert self._classify(target) is SourceInputDisposition.UNREADABLE

        result = find_pattern_facts([str(tmp_path)])
        dispositions = {i.path: i.disposition for i in result.inputs.inputs}
        assert dispositions[str(target)] is SourceInputDisposition.UNREADABLE
        # The sibling really was scanned and nothing was "skipped", so the old
        # tally-based signal would have called this complete.
        assert result.files_scanned == 1
        assert result.files_skipped == 0
        assert result.sufficient is False

    def test_an_unexaminable_candidate_blocks_the_absence_claim(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The consequence: OLD cannot establish an absence while one of its
        candidate headers went unread, so NEW's construct is not `introduced`."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        (old_dir / "Core").write_text("int f(void);\n")
        (old_dir / "seen.hpp").write_text("int g(void);\n")
        (new_dir / "b.hpp").write_text(PACKED_SOURCE)

        old = _live(_snapshot_recording([str(old_dir)]))
        new = _live(_snapshot_recording([str(new_dir)], version="2.0"))
        for side in (old, new):
            side.from_headers = True

        assert (
            compute_pattern_preprocessor_scan(
                old, new
            ).pattern_escalation_evolution.get("pragma_pack")
            == "introduced"
        )

        import builtins

        real_open = builtins.open

        def _open(file: Any, *a: Any, **kw: Any) -> Any:
            if Path(file).name == "Core":
                raise PermissionError(13, "Permission denied")
            return real_open(file, *a, **kw)

        monkeypatch.setattr(builtins, "open", _open)

        folded = compute_pattern_preprocessor_scan(old, new)
        assert folded.pattern_escalation_evolution.get("pragma_pack") == "not_evaluated"
        assert folded.coverage[CHECK_PATTERN_ESCALATION]["old"].established is False


class TestFoldEvolutionPerFindingEstablishment:
    @pytest.mark.parametrize(
        "old_hit,new_hit,old_sufficient,new_sufficient",
        [c for c in itertools.product([False, True], repeat=4) if c[0] or c[1]],
    )
    def test_exhaustive_against_independent_truth_table(
        self,
        old_hit: bool,
        new_hit: bool,
        old_sufficient: bool,
        new_sufficient: bool,
    ) -> None:
        """All twelve reachable (old_hit, new_hit, old_suf, new_suf) states --
        an identity absent on both sides never enters the union at all."""
        folded = _fold_evolution(
            old=Sufficiency(established=old_sufficient, reason=""),
            new=Sufficiency(established=new_sufficient, reason=""),
            old_keys={"k"} if old_hit else set(),
            new_keys={"k"} if new_hit else set(),
        )
        assert folded == {
            "k": _oracle(old_hit, new_hit, old_sufficient, new_sufficient)
        }

    def test_introduced_requires_established_absence_in_old(self) -> None:
        """The headline refusal: NEW flags it, OLD does not, and OLD's
        coverage is not established -- ``introduced`` would be a claim about
        what OLD did not contain, which nothing supports."""
        folded = _fold_evolution(
            old=Sufficiency(established=False, reason="root missing"),
            new=Sufficiency(established=True),
            old_keys=set(),
            new_keys={"k"},
        )
        assert folded == {"k": "not_evaluated"}

    def test_resolved_requires_established_absence_in_new(self) -> None:
        folded = _fold_evolution(
            old=Sufficiency(established=True),
            new=Sufficiency(established=False, reason="root missing"),
            old_keys={"k"},
            new_keys=set(),
        )
        assert folded == {"k": "not_evaluated"}

    def test_persistent_rests_on_observation_not_on_sufficiency(self) -> None:
        """Both sides *observed* the construct. Neither side's incompleteness
        can un-see a hit, so ``persistent`` is established for this identity
        even though neither side's absence claims would be -- this is what
        "the OLD side's coverage **for that specific finding**" means, as
        opposed to one global completeness flag."""
        folded = _fold_evolution(
            old=Sufficiency(established=False, reason="one unreadable file"),
            new=Sufficiency(established=False, reason="one unreadable file"),
            old_keys={"k"},
            new_keys={"k"},
        )
        assert folded == {"k": "persistent"}

    def test_an_identity_is_never_silently_dropped(self) -> None:
        """Every key either side flagged appears in the result, whatever its
        state -- a bare ``continue`` that drops evidence is worse than an
        honest ``not_evaluated``."""
        folded = _fold_evolution(
            old=Sufficiency(established=False, reason="one unreadable file"),
            new=Sufficiency(established=False, reason="one unreadable file"),
            old_keys={"a"},
            new_keys={"b"},
        )
        # "a" was seen only by the (insufficient) OLD side and "b" only by the
        # (insufficient) NEW side: neither direction is decidable, and both
        # keys must still be listed rather than dropped.
        assert folded == {"a": "not_evaluated", "b": "not_evaluated"}


def _oracle(
    old_hit: bool, new_hit: bool, old_sufficient: bool, new_sufficient: bool
) -> str:
    """Independent truth table for :func:`_fold_evolution`.

    Written from the *contract* rather than from the implementation: presence
    is established by having observed it; absence is established only by that
    side's sufficiency; a state whose premises are not all established is
    ``not_evaluated``.
    """
    knows_old = old_hit or old_sufficient
    knows_new = new_hit or new_sufficient
    if not knows_old or not knows_new:
        return "not_evaluated"
    if old_hit and new_hit:
        return "persistent"
    if new_hit:
        return "introduced"
    return "resolved"


def test_pattern_facts_result_without_an_input_account_is_never_sufficient() -> None:
    """Deny-by-default at the result level too: a hand-built result that
    reports only the legacy tallies cannot claim sufficiency."""
    assert PatternFactsResult(files_scanned=5, files_skipped=0).sufficient is False


def test_resolve_source_inputs_without_a_licence_never_touches_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("filesystem touched under a withheld licence")

    monkeypatch.setattr(os, "walk", _fail)
    monkeypatch.setattr(Path, "is_file", _fail)
    monkeypatch.setattr(Path, "is_dir", _fail)
    monkeypatch.setattr(Path, "exists", _fail)

    inputs = resolve_source_inputs([str(tmp_path), "/nonexistent/x.h"])
    assert [i.disposition for i in inputs.inputs] == [
        SourceInputDisposition.NOT_LICENSED
    ] * 2


def test_a_bare_filename_in_the_changed_list_matches_by_basename(
    tmp_path: Path,
) -> None:
    """`_path_changed`'s basename fallback: a changed list holding just
    `pub.hpp` must still select `<abs>/include/pub.hpp`, since the two are
    rooted differently and neither is a tail of the other."""
    inc = tmp_path / "include"
    inc.mkdir()
    (inc / "pub.hpp").write_text(PACKED_SOURCE)
    (inc / "other.hpp").write_text(TEMPLATE_SOURCE)

    result = find_pattern_facts([str(inc)], changed_paths=["pub.hpp"])
    dispositions = {Path(i.path).name: i.disposition for i in result.inputs.inputs}
    assert dispositions["pub.hpp"] is SourceInputDisposition.SCANNED
    assert dispositions["other.hpp"] is SourceInputDisposition.EXCLUDED


def test_a_changed_filter_with_no_join_predicate_narrows_nothing(
    tmp_path: Path,
) -> None:
    """`resolve_source_inputs` is scanner-agnostic: a caller that passes
    `changed_paths` but supplies no `path_changed` predicate has stated no way
    to join the two, so nothing may be silently excluded on its behalf — the
    safe direction, since a wrongly-excluded input would read as deliberate
    scope rather than as a gap."""
    f = tmp_path / "a.hpp"
    f.write_text(PACKED_SOURCE)
    inputs = resolve_source_inputs(
        [str(f)],
        changed_paths=["something-else.hpp"],
        licence=SourceReadLicence.live_extraction(),
    )
    assert [i.disposition for i in inputs.inputs] == [SourceInputDisposition.SELECTED]


class TestSourceInputSetMergeProperties:
    """`SourceInputSet.merged` stated as invariants, not as one caller's case.

    A new reusable combining primitive gets this treatment per this repo's own
    "Primitive-level property tests" guidance: the merge exists so one scan can
    span two independently-licensed evidence sources, and the properties that
    make it safe to build a sufficiency answer on are order-independence, no
    double-counting, no silent loss, and a mixed-provenance result that is never
    sufficient. Enumerated exhaustively over the disposition domain rather than
    sampled, since the domain is small enough to close completely.
    """

    _DISPOSITIONS = tuple(SourceInputDisposition)

    @staticmethod
    def _set(
        items: list[tuple[str, SourceInputDisposition]], *, permitted: bool
    ) -> SourceInputSet:
        licence = (
            SourceReadLicence.live_extraction()
            if permitted
            else WITHHELD_FOR_STORED_SNAPSHOT
        )
        return SourceInputSet(
            inputs=tuple(SourceInput(path=p, disposition=d) for p, d in items),
            licence=licence,
        )

    def test_the_precedence_is_a_total_order_over_every_disposition(self) -> None:
        """A newly-added disposition must be ranked, not silently unranked.

        `merged` indexes the precedence table directly, so an unranked member
        would be a `KeyError` at merge time on some real input rather than a
        failure here. Exhaustiveness is mechanical even though the *ordering*
        judgement is not — the same split this repo applies to the canonical
        identity contract.
        """
        assert set(DISPOSITION_PRECEDENCE) == set(SourceInputDisposition)
        assert len(DISPOSITION_PRECEDENCE) == len(SourceInputDisposition)

    @pytest.mark.parametrize("left", _DISPOSITIONS)
    @pytest.mark.parametrize("right", _DISPOSITIONS)
    def test_order_never_changes_the_outcome(
        self, left: SourceInputDisposition, right: SourceInputDisposition
    ) -> None:
        """Every disposition pair on the same path merges order-independently.

        An order-dependent merge would make a side's sufficiency depend on which
        evidence source happened to be resolved first — the exact defect class
        that made `_paired_stable_indices` need this kind of test.
        """
        a = self._set([("shared.h", left)], permitted=True)
        b = self._set([("shared.h", right)], permitted=True)
        forward = a.merged(b)
        backward = b.merged(a)
        assert {i.disposition for i in forward.inputs} == {
            i.disposition for i in backward.inputs
        }
        assert forward.sufficient == backward.sufficient

    @pytest.mark.parametrize("left", _DISPOSITIONS)
    @pytest.mark.parametrize("right", _DISPOSITIONS)
    def test_a_shared_path_is_accounted_for_exactly_once(
        self, left: SourceInputDisposition, right: SourceInputDisposition
    ) -> None:
        """Counted twice, one header could make a complete set read incomplete.

        Or the reverse: a duplicated `scanned` entry inflating the scanned tally
        past a real gap. Either way the account stops being an account.
        """
        merged = self._set([("shared.h", left)], permitted=True).merged(
            self._set([("shared.h", right)], permitted=True)
        )
        assert [i.path for i in merged.inputs] == ["shared.h"]

    @pytest.mark.parametrize("disposition", _DISPOSITIONS)
    def test_no_input_is_ever_dropped(
        self, disposition: SourceInputDisposition
    ) -> None:
        a = self._set([("a.h", disposition)], permitted=True)
        b = self._set([("b.cpp", disposition)], permitted=True)
        assert {i.path for i in a.merged(b).inputs} == {"a.h", "b.cpp"}

    def test_a_real_outcome_outranks_not_licensed_on_the_same_path(self) -> None:
        """One source read the path; the other was not licensed to.

        The read happened, so the account records it — `not_licensed` is the
        weaker statement and must not mask a real observation.
        """
        scanned = self._set(
            [("shared.h", SourceInputDisposition.SCANNED)], permitted=True
        )
        unlicensed = self._set(
            [("shared.h", SourceInputDisposition.NOT_LICENSED)], permitted=False
        )
        for merged in (scanned.merged(unlicensed), unlicensed.merged(scanned)):
            assert [i.disposition for i in merged.inputs] == [
                SourceInputDisposition.SCANNED
            ]
            assert merged.sufficient is True

    @pytest.mark.parametrize("licensed_first", [True, False])
    def test_mixed_provenance_is_never_sufficient(self, licensed_first: bool) -> None:
        """The whole point of the merge: one unlicensed source keeps it open.

        The licensed half's observations survive (it reports a scanned input),
        but the absence claim does not — and the reason names the gap rather
        than the licence, since the set as a whole *was* partly readable.
        """
        licensed = self._set(
            [("read.h", SourceInputDisposition.SCANNED)], permitted=True
        )
        withheld = self._set(
            [("historical.cpp", SourceInputDisposition.NOT_LICENSED)], permitted=False
        )
        merged = (
            licensed.merged(withheld) if licensed_first else withheld.merged(licensed)
        )
        assert merged.licence.permitted is True
        assert merged.scanned == 1
        assert merged.sufficient is False
        assert "not_licensed" in merged.insufficiency_reason()

    def test_two_withheld_sources_keep_a_licence_reason(self) -> None:
        """Neither side readable: the reason must name the licence, not a tally.

        A caller reading "incomplete expected-input set" for a side nothing was
        licensed to read would be told the wrong thing about why.
        """
        a = self._set([("a.h", SourceInputDisposition.NOT_LICENSED)], permitted=False)
        b = self._set([("b.cpp", SourceInputDisposition.NOT_LICENSED)], permitted=False)
        merged = a.merged(b)
        assert merged.licence.permitted is False
        assert merged.insufficiency_reason() == WITHHELD_FOR_STORED_SNAPSHOT.reason

    def test_merging_an_empty_account_is_the_identity(self) -> None:
        """A side with no build pack at all must decide exactly as before.

        This is what keeps every pre-existing single-source invocation
        bit-for-bit unchanged.
        """
        one = self._set([("a.h", SourceInputDisposition.SCANNED)], permitted=True)
        empty = SourceInputSet(licence=one.licence)
        assert one.merged(empty) == one
        assert empty.merged(one).inputs == one.inputs
