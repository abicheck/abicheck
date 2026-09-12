# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Bug-class regression tests for the two P1 defects in source-derived
enrichment of *stored* snapshots.

Registered classes (``tests/regressions/manifest.py``):

``evidence.stored_snapshot_rederivation``
    A path recorded in a snapshot is provenance, not a licence to re-read the
    current filesystem for a historical fact. The reported instance was
    ``workflows/pattern_preprocessor_scan.py`` rebuilding pattern/preprocessor
    roots from a stored snapshot's ``source_header``/compile units and letting
    ``buildsource/pattern_facts.py`` ``read_text()`` them, so a baseline
    dumped elsewhere was re-characterised against today's runner.

``coverage.discovery_derived_completeness``
    Sufficiency for an *absence* claim must be computed from the **expected**
    input set, never from what a discovery walk happened to find. The reported
    instance was ``files_scanned > 0 and files_skipped == 0`` over a walk that
    silently ``continue``d past a non-existent root, so missing inputs read as
    "fully covered".

These are deliberately not written as one repro each. The provenance class is
exercised by an **exhaustive small-domain enumeration** of every way a source
checkout can move out from under a stored snapshot (delete a file, edit it,
relocate the tree, substitute an unrelated file at the same path, replace the
whole root with a directory) crossed with both pattern kinds and both
evolution directions; the completeness class by an **exhaustive enumeration**
of every ``SourceInputDisposition`` and, for the fold, of all sixteen
(old_hit, new_hit, old_sufficient, new_sufficient) combinations checked
against a hand-written truth table -- an oracle written independently of the
implementation, not a second call into it.
"""

from __future__ import annotations

import itertools
import os
from pathlib import Path
from typing import Any

import pytest

from abicheck.buildsource.pattern_facts import (
    PatternFactsResult,
    find_pattern_facts,
    resolve_expected_source_inputs,
)
from abicheck.buildsource.preprocessor_facts import PreprocessorFactsResult
from abicheck.buildsource.preprocessor_probe_families import ProbeTallies
from abicheck.buildsource.source_inputs import (
    GAP_DISPOSITIONS,
    WITHHELD_FOR_STORED_SNAPSHOT,
    SourceInput,
    SourceInputDisposition,
    SourceInputSet,
    SourceReadLicence,
    extraction_read_source_inputs,
    resolve_source_inputs,
)
from abicheck.model import AbiSnapshot, Function, ScopeOrigin
from abicheck.serialization import load_snapshot, write_snapshot
from abicheck.workflows.pattern_preprocessor_scan import (
    CHECK_HEADER_LEAK,
    CHECK_MACRO_DIVERGENCE,
    CHECK_PATTERN_ESCALATION,
    Sufficiency,
    _fold_evolution,
    _header_leak_sufficiency,
    _macro_divergence_sufficiency,
    compute_pattern_preprocessor_scan,
    snapshot_source_licence,
)

# ── Fixtures: a library whose declarations point at a real source tree ───────

#: Two independent escalating constructs, so a test can assert on one kind
#: while varying the other -- a single-kind fixture cannot tell "the fold
#: refused this finding" apart from "the scan produced nothing at all".
PACKED_SOURCE = "#pragma pack(push, 1)\nstruct S { int a; };\n#pragma pack(pop)\n"
TEMPLATE_SOURCE = "template class Widget<int>;\n"


def _write_tree(root: Path, contents: dict[str, str]) -> list[str]:
    paths = []
    for name, text in contents.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        paths.append(str(target))
    return paths


def _snapshot_recording(headers: list[str], *, version: str = "1.0") -> AbiSnapshot:
    """A snapshot whose declarations record *headers* as their provenance."""
    return AbiSnapshot(
        library="libfoo.so",
        version=version,
        functions=[
            Function(
                name=f"f{i}",
                mangled=f"_Z1f{i}v",
                return_type="void",
                params=[],
                source_header=h,
                origin=ScopeOrigin.PUBLIC_HEADER,
            )
            for i, h in enumerate(headers)
        ],
    )


def _stored(snapshot: AbiSnapshot, tmp_path: Path, name: str) -> AbiSnapshot:
    """Round-trip *snapshot* through the real storage codec, as a baseline is."""
    out = tmp_path / name
    write_snapshot(snapshot, out)
    return load_snapshot(out)


def _live(snapshot: AbiSnapshot) -> AbiSnapshot:
    """Mark *snapshot* as produced by a live extraction in this run."""
    snapshot.live_source_evidence = True
    return snapshot


# ── Class 1: evidence.stored_snapshot_rederivation ───────────────────────────


class TestStoredSnapshotIsNeverReDerivedFromTodaysFilesystem:
    """A stored snapshot's recorded paths are provenance, never a read licence."""

    def test_storage_round_trip_never_grants_a_licence(self, tmp_path: Path) -> None:
        """The licence cannot survive serialization -- it is not a persisted
        field, so no on-disk content can make a loaded snapshot claim one."""
        live = _live(_snapshot_recording([str(tmp_path / "a.hpp")]))
        assert snapshot_source_licence(live).permitted is True

        stored = _stored(live, tmp_path, "snap.json")
        assert stored.live_source_evidence is False
        assert snapshot_source_licence(stored).permitted is False

    @pytest.mark.parametrize(
        "mutation",
        [
            "unchanged",
            "delete_file",
            "edit_file",
            "truncate_file",
            "substitute_unrelated_file",
            "relocate_tree",
            "replace_file_with_directory",
            "make_unreadable",
        ],
    )
    @pytest.mark.parametrize("seed_construct", [PACKED_SOURCE, TEMPLATE_SOURCE])
    def test_stored_snapshot_facts_are_invariant_under_source_mutation(
        self, tmp_path: Path, mutation: str, seed_construct: str
    ) -> None:
        """**The acceptance invariant.** Exhaustive over every way a checkout
        can move out from under a snapshot: an unchanged stored snapshot must
        neither gain nor lose a historical fact when the tree it names is
        mutated, deleted, relocated, or replaced.

        The oracle is the ``unchanged`` arm of this same enumeration, captured
        *before* any mutation -- not a recomputation through the code under
        test with the same inputs.
        """
        tree = tmp_path / "checkout"
        header = tree / "include" / "pub.hpp"
        _write_tree(tree, {"include/pub.hpp": seed_construct})

        old = _stored(
            _snapshot_recording([str(header)], version="1.0"), tmp_path, "old.json"
        )
        new = _stored(
            _snapshot_recording([str(header)], version="2.0"), tmp_path, "new.json"
        )
        before = compute_pattern_preprocessor_scan(old, new).to_dict()

        if mutation == "delete_file":
            header.unlink()
        elif mutation == "edit_file":
            header.write_text(
                seed_construct + "\n__attribute__((packed)) struct T{};\n"
            )
        elif mutation == "truncate_file":
            header.write_text("")
        elif mutation == "substitute_unrelated_file":
            header.write_text("int unrelated_symbol;\n")
        elif mutation == "relocate_tree":
            tree.rename(tmp_path / "moved")
        elif mutation == "replace_file_with_directory":
            header.unlink()
            header.mkdir()
        elif mutation == "make_unreadable":
            header.chmod(0o000)

        after = compute_pattern_preprocessor_scan(old, new).to_dict()
        assert after == before, f"stored facts changed under mutation {mutation!r}"

        if mutation == "make_unreadable":
            header.chmod(0o600)

    def test_unlicensed_side_touches_no_filesystem_api_at_all(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Stronger than "the answer is stable": the unlicensed path must not
        *reach* the filesystem, so a same-looking path on the current runner
        cannot influence the result even by existing.

        Asserted by executing the real code with the filesystem primitives
        booby-trapped, not by asserting on source text (the #705->#758 lesson
        in AGENTS.md: a defense must be executed, not read)."""
        tripped: list[str] = []

        def _trap(name: str) -> Any:
            def _fail(*_a: Any, **_kw: Any) -> Any:
                tripped.append(name)
                raise AssertionError(f"unlicensed scan touched {name}")

            return _fail

        header = tmp_path / "pub.hpp"
        header.write_text(PACKED_SOURCE)
        old = _stored(_snapshot_recording([str(header)]), tmp_path, "o.json")
        new = _stored(
            _snapshot_recording([str(header)], version="2.0"), tmp_path, "n.json"
        )

        monkeypatch.setattr(Path, "read_text", _trap("Path.read_text"))
        monkeypatch.setattr(Path, "is_file", _trap("Path.is_file"))
        monkeypatch.setattr(Path, "is_dir", _trap("Path.is_dir"))
        monkeypatch.setattr(Path, "exists", _trap("Path.exists"))
        monkeypatch.setattr(os, "walk", _trap("os.walk"))

        result = compute_pattern_preprocessor_scan(old, new)
        assert tripped == []
        assert result.pattern_escalation_evolution == {}
        assert result.coverage[CHECK_PATTERN_ESCALATION]["old"].established is False

    def test_live_extraction_still_reads_and_reports(self, tmp_path: Path) -> None:
        """The fix must not simply disable the feature: a side that really was
        extracted from today's tree keeps its facts."""
        header = tmp_path / "pub.hpp"
        header.write_text(PACKED_SOURCE)
        old = _live(_snapshot_recording([str(header)]))
        new = _live(_snapshot_recording([str(header)], version="2.0"))

        result = compute_pattern_preprocessor_scan(old, new)
        assert result.coverage[CHECK_PATTERN_ESCALATION]["old"].established is True
        assert set(result.pattern_escalation_evolution.values()) == {"persistent"}

    def test_explicit_verified_context_is_the_only_override(
        self, tmp_path: Path
    ) -> None:
        """A caller that has independently verified the recorded tree can
        supply a licence; nothing else re-enables reading."""
        header = tmp_path / "pub.hpp"
        header.write_text(PACKED_SOURCE)
        stored_old = _stored(_snapshot_recording([str(header)]), tmp_path, "o.json")
        stored_new = _stored(
            _snapshot_recording([str(header)], version="2.0"), tmp_path, "n.json"
        )

        withheld = compute_pattern_preprocessor_scan(stored_old, stored_new)
        assert withheld.pattern_escalation_evolution == {}

        verified = SourceReadLicence.verified_context("checkout pinned to snapshot sha")
        granted = compute_pattern_preprocessor_scan(
            stored_old,
            stored_new,
            old_source_licence=verified,
            new_source_licence=verified,
        )
        assert set(granted.pattern_escalation_evolution.values()) == {"persistent"}

    def test_default_licence_is_deny(self) -> None:
        """Deny-by-default: a snapshot that establishes nothing reads nothing."""
        assert WITHHELD_FOR_STORED_SNAPSHOT.permitted is False
        assert (
            snapshot_source_licence(AbiSnapshot(library="l", version="1")).permitted
            is False
        )

    def test_withheld_licence_stats_nothing_even_for_a_real_path(
        self, tmp_path: Path
    ) -> None:
        """At the primitive level: the roots are accounted for as
        ``not_licensed`` regardless of whether they exist."""
        real = tmp_path / "real.hpp"
        real.write_text(PACKED_SOURCE)
        gone = tmp_path / "gone.hpp"

        result = find_pattern_facts(
            [str(real), str(gone)], licence=WITHHELD_FOR_STORED_SNAPSHOT
        )
        assert result.facts == []
        assert result.sufficient is False
        assert {i.disposition for i in result.inputs.inputs} == {
            SourceInputDisposition.NOT_LICENSED
        }


# ── Class 2: coverage.discovery_derived_completeness ─────────────────────────


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


# ── The fold's own contract, exhaustively ────────────────────────────────────


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


# ── Review-round findings: each is a way the two invariants above leak ────────


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
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root bypasses directory permissions, so chmod 000 denies nothing",
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


class TestTypedPythonApiGetsTheSameLicenceAsTheCli:
    """`service.run_dump()` is a documented public entry point and does not go
    through `cached_run_dump`. Stamping the licence there left the typed API
    unstamped, so identical inputs produced `not_evaluated` through the Python
    API while working through the CLI (Codex review, P2)."""

    def test_run_dump_grants_the_licence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from abicheck import service, service_dump_native

        # Header-derived: the AST frontend really opened the files it attributes
        # declarations to. A DWARF-only extraction is *not* licensed -- see
        # TestLicenceRequiresThatSourceInputsWereActuallyRead below.
        produced = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
        assert produced.live_source_evidence is False

        monkeypatch.setattr(
            service_dump_native,
            "_run_dump_uncached",
            lambda *a, **kw: produced,
        )
        out = service.run_dump(Path("libfoo.so"), "elf")
        assert out.live_source_evidence is True
        assert snapshot_source_licence(out).permitted is True

    def test_licence_is_granted_by_the_shared_dump_not_by_one_front_end(self) -> None:
        """Structural: the grant lives in the dump operation every front end
        funnels through, so a front end cannot be added that silently skips it.
        `cached_run_dump` keeps its own grant only for the *cache-hit* path,
        which never calls `run_dump` at all."""
        from abicheck import service_dump_cache, service_dump_native
        from abicheck.buildsource import source_inputs

        # The grant is the contract owner's, applied once at the shared dump.
        assert service_dump_native.granting_live_source_licence is (
            source_inputs.granting_live_source_licence
        )
        cache_src = Path(service_dump_cache.__file__).read_text()
        # Exactly one grant in the cache module, and it is on the hit path.
        assert cache_src.count("live_source_evidence = True") == 1
        assert "cached.live_source_evidence = True" in cache_src


# ── Remaining branches of the two new primitives ─────────────────────────────


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


class TestDeclaredSourceHeadersCoversEveryDeclarationKind:
    """The pattern-scan roots come from *every* declaration's provenance, not
    only functions: a library whose public surface is types and constants must
    not silently contribute no roots."""

    def test_variables_records_and_enums_all_contribute_roots(
        self, tmp_path: Path
    ) -> None:
        from abicheck.model import EnumType, RecordType, Variable
        from abicheck.workflows.pattern_preprocessor_scan import (
            _declared_source_headers,
        )

        var_h = tmp_path / "var.h"
        rec_h = tmp_path / "rec.h"
        enum_h = tmp_path / "enum.h"
        for h in (var_h, rec_h, enum_h):
            h.write_text(PACKED_SOURCE)

        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            variables=[
                Variable(
                    name="v",
                    mangled="v",
                    type="int",
                    source_header=str(var_h),
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
            types=[
                RecordType(
                    name="S",
                    kind="struct",
                    size_bits=32,
                    source_header=str(rec_h),
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
            enums=[
                EnumType(
                    name="E",
                    source_header=str(enum_h),
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
        )
        assert _declared_source_headers(snap) == {
            str(var_h),
            str(rec_h),
            str(enum_h),
        }
        # And the public-only filter keeps all three, since each is public.
        assert _declared_source_headers(snap, public_only=True) == {
            str(var_h),
            str(rec_h),
            str(enum_h),
        }

        # End to end: those roots really are scanned for a live side.
        snap.live_source_evidence = True
        other = AbiSnapshot(library="libfoo.so", version="2.0")
        other.live_source_evidence = True
        result = compute_pattern_preprocessor_scan(snap, other)
        assert result.pattern_old["files_scanned"] == 3


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


class TestLicenceRequiresThatSourceInputsWereActuallyRead:
    """ "Extracted in this run" is not by itself evidence that the recorded
    source paths were read.

    A headerless DWARF dump derives every declaration's `source_header` from
    `DW_AT_decl_file` — a path on the *build* machine this run never opened and
    which may not exist here at all. Granting the licence unconditionally to
    any live extraction therefore reopened the original hole for downloaded or
    previously-built binaries: the scan would characterise whatever now occupies
    those paths (Codex review, P2). The licence now requires header-derived
    provenance, where the AST frontend genuinely opened the files it attributes
    declarations to.
    """

    @staticmethod
    def _snapshot(**kw: object) -> AbiSnapshot:
        snap = AbiSnapshot(library="libfoo.so", version="1.0")
        for k, v in kw.items():
            setattr(snap, k, v)
        return snap

    @pytest.mark.parametrize(
        "from_headers,inferred,expected",
        [
            (True, False, True),  # header-AST extraction: files really opened
            (True, True, False),  # from_headers was *guessed* on a legacy load
            (False, False, False),  # DWARF- or symbol-table-derived: never opened
            (False, True, False),
        ],
    )
    def test_only_established_header_provenance_reads_source_inputs(
        self, from_headers: bool, inferred: bool, expected: bool
    ) -> None:
        """Exhaustive over the four (from_headers, inferred) states, so neither
        can be dropped from the predicate without a failure."""
        from abicheck.buildsource.source_inputs import extraction_read_source_inputs

        snap = self._snapshot(from_headers=from_headers, from_headers_inferred=inferred)
        assert extraction_read_source_inputs(snap) is expected

    def test_a_dwarf_only_live_extraction_is_not_licensed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Through the real `run_dump` wrapper: a snapshot whose provenance is
        DWARF comes back unlicensed even though this run produced it."""
        from abicheck import service, service_dump_native

        dwarf_only = self._snapshot(from_headers=False)
        monkeypatch.setattr(
            service_dump_native, "_run_dump_uncached", lambda *a, **kw: dwarf_only
        )
        out = service.run_dump(tmp_path / "libfoo.so", "elf")
        assert out.live_source_evidence is False
        assert snapshot_source_licence(out).permitted is False

    def test_a_header_derived_live_extraction_is_licensed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from abicheck import service, service_dump_native

        header_derived = self._snapshot(from_headers=True)
        monkeypatch.setattr(
            service_dump_native, "_run_dump_uncached", lambda *a, **kw: header_derived
        )
        out = service.run_dump(tmp_path / "libfoo.so", "elf")
        assert out.live_source_evidence is True

    def test_a_dwarf_only_side_reports_not_evaluated_not_fabricated_facts(
        self, tmp_path: Path
    ) -> None:
        """The consequence. The path a DWARF snapshot records is occupied by an
        unrelated file here — exactly the downloaded-binary case — and the scan
        must decline rather than describe it."""
        decoy = tmp_path / "on_the_build_machine.h"
        decoy.write_text(PACKED_SOURCE)

        # DWARF provenance: recorded, never read (from_headers stays False).
        old = _snapshot_recording([str(decoy)])
        new = _snapshot_recording([str(decoy)], version="2.0")
        old.live_source_evidence = extraction_read_source_inputs(old)
        new.live_source_evidence = extraction_read_source_inputs(new)

        result = compute_pattern_preprocessor_scan(old, new)
        assert result.pattern_escalation_evolution == {}
        assert result.coverage[CHECK_PATTERN_ESCALATION]["old"].established is False

        # The very same paths, now with header-derived provenance, are read.
        for side in (old, new):
            side.from_headers = True
            side.live_source_evidence = extraction_read_source_inputs(side)
        licensed = compute_pattern_preprocessor_scan(old, new)
        assert set(licensed.pattern_escalation_evolution.values()) == {"persistent"}
