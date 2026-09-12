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

import os
from pathlib import Path
from typing import Any

import pytest
from _source_licence_fixtures import (
    PACKED_SOURCE,
    TEMPLATE_SOURCE,
    live as _live,
    snapshot_recording as _snapshot_recording,
    stored as _stored,
    with_build_evidence as _with_build_evidence,
    write_tree as _write_tree,
)

from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.pattern_facts import (
    find_pattern_facts,
)
from abicheck.buildsource.source_inputs import (
    WITHHELD_FOR_STORED_SNAPSHOT,
    SourceInputDisposition,
    SourceReadLicence,
    build_evidence_collected_live,
    extraction_read_source_inputs,
)
from abicheck.model import AbiSnapshot, ScopeOrigin
from abicheck.workflows.pattern_preprocessor_scan import (
    CHECK_PATTERN_ESCALATION,
    build_evidence_licence,
    compute_pattern_preprocessor_scan,
    snapshot_source_licence,
)

# ── Fixtures: a library whose declarations point at a real source tree ───────

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


# ── The fold's own contract, exhaustively ────────────────────────────────────


# ── Review-round findings: each is a way the two invariants above leak ────────


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


def test_the_verified_context_override_is_reachable_through_compare(
    tmp_path: Path,
) -> None:
    """The override must be usable by an actual comparison, not only by the
    workflow helper underneath it.

    It began life as a parameter on `compute_pattern_preprocessor_scan` alone,
    which `checker.compare()` called without — so the documented
    verified-context case was unreachable except by mutating the runtime flag,
    i.e. not a shipped capability at all (AGENTS.md "finish the workflow":
    wire the consumer before claiming a feature; Codex review).
    """
    from abicheck.checker import compare

    header = tmp_path / "pub.hpp"
    header.write_text(PACKED_SOURCE)
    old = _stored(_snapshot_recording([str(header)]), tmp_path, "o.json")
    new = _stored(_snapshot_recording([str(header)], version="2.0"), tmp_path, "n.json")

    # Without a licence, `compare` declines to re-read the stored sides.
    withheld = compare(old, new).pattern_preprocessor_scan
    assert withheld is not None
    assert withheld.pattern_escalation_evolution == {}

    verified = SourceReadLicence.verified_context("checkout pinned to snapshot sha")
    granted = compare(
        old, new, old_source_licence=verified, new_source_licence=verified
    ).pattern_preprocessor_scan
    assert granted is not None
    assert set(granted.pattern_escalation_evolution.values()) == {"persistent"}
    assert granted.coverage[CHECK_PATTERN_ESCALATION]["old"].established is True


class TestEveryFrontEndThatExtractsLiveGrantsTheLicence:
    """A front end that dumps for itself has to grant the licence for itself.

    `service.run_dump` covers everything that funnels through it, but the
    ABICC-compatible CLI calls `dumper.dump` directly and deliberately — to skip
    `run_dump`'s dependency-scope wrapper — so its genuinely live, header-derived
    snapshots came back unlicensed and its report withheld the source-derived
    facts as if they were a stored snapshot's (Codex review). Same parity class
    as the earlier `run_dump` finding, one front end over.
    """

    def test_the_helper_grants_only_for_header_derived_provenance(self) -> None:
        from abicheck.workflows.pattern_preprocessor_scan import (
            grant_live_source_licence,
        )

        header_derived = AbiSnapshot(library="l", version="1", from_headers=True)
        assert grant_live_source_licence(header_derived).live_source_evidence is True

        # The ABICC `abi-dumper`-style path can dump a descriptor with no
        # headers; that must still be denied.
        dwarf_only = AbiSnapshot(library="l", version="1")
        assert grant_live_source_licence(dwarf_only).live_source_evidence is False

    def test_the_abicc_front_end_grants_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Through the real `_snapshot_from_compat_input`, with only the dump
        itself stubbed — so removing the grant from that function fails this.

        Asserting the helper in isolation would not: the whole finding was that
        the call site never invoked it.
        """
        from abicheck.compat import cli as compat_cli
        from abicheck.compat.descriptor import CompatDescriptor

        so = tmp_path / "libfoo.so"
        so.write_bytes(b"\x7fELF")
        header = tmp_path / "pub.hpp"
        header.write_text(PACKED_SOURCE)

        produced = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
        assert produced.live_source_evidence is False
        monkeypatch.setattr(compat_cli, "dump", lambda *a, **kw: produced)

        snap, version = compat_cli._snapshot_from_compat_input(
            CompatDescriptor(version="1.0", headers=[header], libs=[so]),
            None,
            tmp_path / "desc.xml",
            headers_list_path=None,
            single_header=None,
            skip_headers_set=set(),
            quiet=True,
            gcc_path=None,
            gcc_prefix=None,
            gcc_options=None,
            sysroot=None,
            nostdinc=False,
            lang=None,
        )
        assert version == "1.0"
        assert snap.live_source_evidence is True

    def test_the_abicc_front_end_still_denies_a_headerless_dump(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The grant is conditional at this call site too: the ABICC
        `abi-dumper`-style path can dump a descriptor with no headers."""
        from abicheck.compat import cli as compat_cli
        from abicheck.compat.descriptor import CompatDescriptor

        so = tmp_path / "libfoo.so"
        so.write_bytes(b"\x7fELF")
        dwarf_only = AbiSnapshot(library="libfoo.so", version="1.0")
        monkeypatch.setattr(compat_cli, "dump", lambda *a, **kw: dwarf_only)

        snap, _ = compat_cli._snapshot_from_compat_input(
            CompatDescriptor(version="1.0", headers=[], libs=[so]),
            None,
            tmp_path / "desc.xml",
            headers_list_path=None,
            single_header=None,
            skip_headers_set=set(),
            quiet=True,
            gcc_path=None,
            gcc_prefix=None,
            gcc_options=None,
            sysroot=None,
            nostdinc=False,
            lang=None,
        )
        assert snap.live_source_evidence is False

    def test_an_unlicensed_live_comparison_would_withhold_its_own_facts(
        self, tmp_path: Path
    ) -> None:
        """Why it matters, stated as the observable difference: the same live,
        header-derived pair reports nothing when unstamped and `persistent` when
        stamped. This is what the ABICC report was doing before the grant."""
        from abicheck.checker import compare

        header = tmp_path / "pub.hpp"
        header.write_text(PACKED_SOURCE)

        def _pair(licensed: bool) -> dict[str, str]:
            old = _snapshot_recording([str(header)])
            new = _snapshot_recording([str(header)], version="2.0")
            for side in (old, new):
                side.from_headers = True
                side.live_source_evidence = licensed
            block = compare(old, new).pattern_preprocessor_scan
            assert block is not None
            return block.pattern_escalation_evolution

        assert _pair(licensed=False) == {}
        assert set(_pair(licensed=True).values()) == {"persistent"}


# ── Class 1, continued: the *second* evidence source ─────────────────────────


class TestEachEvidenceSourceIsLicensedByItsOwnProvenance:
    """A side can hold live headers and a pre-captured build pack at once.

    The licence was originally snapshot-wide, answered from the header AST
    alone. A live header dump combined with a pre-captured `--build-info` pack
    therefore licensed re-reading the *pack's* recorded compile-unit paths from
    whatever occupies them on this runner — the same fabrication the licence
    exists to prevent, reached through the other evidence source (Codex review,
    P2). Both the lexical scan (compile-unit roots) and the preprocessor scan
    (`clang -E` over those units) read that source.

    Stated exhaustively over the small domain rather than as one repro: the two
    provenances are independent booleans, so all four combinations are
    enumerated, and each is checked for what it may read *and* for what it may
    conclude.
    """

    @staticmethod
    def _pair(tmp_path: Path, *, live_headers: bool, live_pack: bool):
        header = _write_tree(tmp_path / "inc", {"widget.h": TEMPLATE_SOURCE})[0]
        unit = _write_tree(tmp_path / "src", {"widget.cpp": PACKED_SOURCE})[0]
        pair = []
        for version in ("1.0", "2.0"):
            snap = _snapshot_recording([header], version=version)
            _with_build_evidence(snap, [unit], live_pack=live_pack)
            if live_headers:
                snap.from_headers = True
                snap.live_source_evidence = True
            pair.append(snap)
        return pair[0], pair[1], header, unit

    @pytest.mark.parametrize("live_headers", [True, False])
    @pytest.mark.parametrize("live_pack", [True, False])
    def test_a_source_is_read_only_under_its_own_licence(
        self, tmp_path: Path, live_headers: bool, live_pack: bool
    ) -> None:
        old, new, header, unit = self._pair(
            tmp_path, live_headers=live_headers, live_pack=live_pack
        )
        result = compute_pattern_preprocessor_scan(old, new)

        # `template class Widget<int>;` lives only in the header, `#pragma
        # pack` only in the compile unit, so each construct's presence is a
        # direct read-out of which source was actually opened.
        kinds = set(result.pattern_new.get("counts_by_kind", {}))
        assert ("explicit_template_instantiation" in kinds) is live_headers
        assert ("pragma_pack" in kinds) is live_pack

        accounted = {
            i["path"]: i["disposition"] for i in result.pattern_new["inputs"]["gaps"]
        }
        # Nothing is silently dropped: an unlicensed root stays in the account.
        assert (accounted.get(header) == "not_licensed") is not live_headers
        assert (accounted.get(unit) == "not_licensed") is not live_pack

    @pytest.mark.parametrize("live_headers", [True, False])
    @pytest.mark.parametrize("live_pack", [True, False])
    def test_only_a_fully_licensed_side_establishes_an_absence(
        self, tmp_path: Path, live_headers: bool, live_pack: bool
    ) -> None:
        """Mixed provenance is never "fully covered".

        The half that *was* licensed reports its observations, but an absence
        claim spans every expected input, so one unlicensed source keeps the
        side insufficient — which is what stops the fold asserting `introduced`
        or `resolved` off a partially-read side.
        """
        old, new, _, _ = self._pair(
            tmp_path, live_headers=live_headers, live_pack=live_pack
        )
        result = compute_pattern_preprocessor_scan(old, new)
        sides = result.coverage[CHECK_PATTERN_ESCALATION]
        established = live_headers and live_pack
        for side in ("old", "new"):
            assert sides[side].established is established
            assert bool(sides[side].reason) is not established

    def test_a_live_pack_licenses_a_headerless_side_build_reads(
        self, tmp_path: Path
    ) -> None:
        """The symmetric direction, which the first fix denied outright.

        A `--sources`-only collection (no `-H`, so no header licence) genuinely
        read its compile units in this run, and now says so per source instead
        of reporting every source-derived fact as not evaluated.
        """
        old, new, _, unit = self._pair(tmp_path, live_headers=False, live_pack=True)
        result = compute_pattern_preprocessor_scan(old, new)
        assert "pragma_pack" in result.pattern_new.get("counts_by_kind", {})
        assert build_evidence_licence(new).permitted is True
        assert snapshot_source_licence(new).permitted is False

    @pytest.mark.parametrize(
        "mutation",
        ["delete_file", "edit_file", "substitute_unrelated_file", "relocate_tree"],
    )
    def test_a_precaptured_packs_facts_are_invariant_under_source_mutation(
        self, tmp_path: Path, mutation: str
    ) -> None:
        """The acceptance invariant, for the second evidence source.

        The side's *headers* are a genuine live extraction of this run, so the
        side as a whole is licensed -- which is exactly the shape that used to
        let the pack's recorded compile units be re-read. Mutating the tree the
        pack names must not move a single fact, and the oracle is this same
        comparison captured before the mutation.
        """
        unit_dir = tmp_path / "checkout"
        unit = unit_dir / "widget.cpp"
        _write_tree(unit_dir, {"widget.cpp": PACKED_SOURCE})
        header = _write_tree(tmp_path / "inc", {"widget.h": TEMPLATE_SOURCE})[0]

        pair = []
        for version in ("1.0", "2.0"):
            snap = _snapshot_recording([header], version=version)
            _with_build_evidence(snap, [str(unit)], live_pack=False)
            snap.from_headers = True
            snap.live_source_evidence = True
            pair.append(snap)
        old, new = pair
        before = compute_pattern_preprocessor_scan(old, new).to_dict()

        if mutation == "delete_file":
            unit.unlink()
        elif mutation == "edit_file":
            unit.write_text(PACKED_SOURCE + "\ntemplate class Gadget<int>;\n")
        elif mutation == "substitute_unrelated_file":
            unit.write_text("int unrelated_symbol;\n")
        elif mutation == "relocate_tree":
            unit_dir.rename(tmp_path / "moved")

        after = compute_pattern_preprocessor_scan(old, new).to_dict()
        assert after == before, f"pre-captured facts changed under {mutation!r}"
        # And the header half still contributed, so this is not vacuous.
        assert (
            "explicit_template_instantiation"
            in after["pattern"]["new"]["counts_by_kind"]
        )

    def test_an_explicit_verified_context_still_covers_both_sources(
        self, tmp_path: Path
    ) -> None:
        """A caller asserting provenance is asserting it about the tree.

        The override is a statement that the recorded tree *is* the one on
        disk, which is not a claim about one evidence source's route to it — so
        it must not be narrowed by the pack's own unstamped flag.
        """
        old, new, _, _ = self._pair(tmp_path, live_headers=False, live_pack=False)
        override = SourceReadLicence.verified_context("the harness checked it out")
        result = compute_pattern_preprocessor_scan(
            old, new, old_source_licence=override, new_source_licence=override
        )
        kinds = set(result.pattern_new.get("counts_by_kind", {}))
        assert {"explicit_template_instantiation", "pragma_pack"} <= kinds
        assert result.coverage[CHECK_PATTERN_ESCALATION]["new"].established is True


class TestOnlyAnInlineCollectionInThisRunIsLive:
    """`build_evidence_collected_live`'s rule, and the real embed step's use of it.

    The predicate is exhaustive over its own two-boolean domain; the embed test
    is what proves the production call site passes the right booleans, since a
    predicate asserted in isolation passes just as well when nothing calls it.
    """

    @pytest.mark.parametrize(
        "precaptured,collected_inline,expected",
        [
            (False, True, True),  # a pure inline collection: read now
            (True, True, False),  # merged with a pre-captured pack: fail closed
            (True, False, False),  # a loaded pack only
            (False, False, False),  # nothing collected at all
        ],
    )
    def test_the_rule(
        self, precaptured: bool, collected_inline: bool, expected: bool
    ) -> None:
        assert (
            build_evidence_collected_live(
                precaptured=precaptured, collected_inline=collected_inline
            )
            is expected
        )

    def test_a_loaded_pack_never_arrives_stamped(self, tmp_path: Path) -> None:
        """The flag is runtime-only: it cannot survive a round trip.

        Asserted through the real storage codec and the real pack codec, not by
        reading the field's `dataclasses.field` metadata — the property that
        matters is that a snapshot read back off disk grants nothing.
        """
        snap = _with_build_evidence(
            _snapshot_recording([]), ["/nonexistent/a.cpp"], live_pack=True
        )
        snap.from_headers = True
        snap.live_source_evidence = True
        reloaded = _stored(snap, tmp_path, "old.abi.json")
        assert reloaded.build_source is not None
        assert reloaded.build_source.live_source_evidence is False
        assert build_evidence_licence(reloaded).permitted is False

    def test_the_embed_step_grants_it_for_an_inline_collection_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Drives the real `embed_build_source`, stubbing only the collectors.

        Both routes produce the identical merged pack, so the only thing under
        test is which provenance the step attributes to it.
        """
        from abicheck.buildsource import embed as embed_mod, inline as inline_mod
        from abicheck.buildsource.pack import BuildSourcePack

        def _pack() -> BuildSourcePack:
            return BuildSourcePack(
                root=Path(""),
                build_evidence=BuildEvidence(
                    compile_units=[CompileUnit(id="cu0", source="/src/a.cpp")]
                ),
            )

        tree = tmp_path / "src"
        tree.mkdir()
        monkeypatch.setattr(inline_mod, "collect_inline_pack", lambda **kw: _pack())
        monkeypatch.setattr(embed_mod, "load_pack_or_raise", lambda p: _pack())

        inline_side = AbiSnapshot(library="libfoo.so", version="1.0")
        embed_mod.embed_build_source(inline_side, None, tree)
        assert inline_side.build_source is not None
        assert inline_side.build_source.live_source_evidence is True

        # A pack *directory* on the same argument: same facts, historical.
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        # A real pack directory: `is_pack_dir` validates the version marker,
        # not merely the manifest's presence.
        (pack_dir / "manifest.json").write_text('{"build_source_pack_version": 1}')
        pack_side = AbiSnapshot(library="libfoo.so", version="1.0")
        embed_mod.embed_build_source(pack_side, None, pack_dir)
        assert pack_side.build_source is not None
        assert pack_side.build_source.live_source_evidence is False


class TestEveryDumpExecutionBranchGrantsTheLicence:
    """The grant is at the join, not at one branch.

    `service.run_dump` was stamped first, then the ABICC front end — and the
    typed API's two *binary-less* dispatches (`execute_header_only_dump_request`
    and `execute_source_only_dump_request`, reached when `DumpRequest.input.path`
    is None) still came back unlicensed, because each is its own `return
    DumpResult(...)` that never passes through `run_dump` (Codex review, P2).
    Fixing execution branches one at a time is how that kept recurring, so the
    licence is granted at `execute_dump_request` — the one function all three
    branches return through — and this class pins every branch rather than the
    one that was reported.
    """

    @staticmethod
    def _resolved(branch: str) -> Any:
        """A `ResolvedDumpRequest` whose execution takes *branch*.

        Built through the real `resolve_dump_request`, so the dispatch decision
        (`side.path is None`, then `is_header_only_evidence`) is the production
        one rather than a hand-set flag.
        """
        from abicheck.api_types import DumpRequest, InputSpec
        from abicheck.service_dump_pipeline import resolve_dump_request

        if branch == "binary":
            spec = InputSpec(path=Path("libfoo.so"), version="1.0")
        elif branch == "header_only":
            spec = InputSpec(path=None, headers=(Path("pub.hpp"),), version="1.0")
        else:
            spec = InputSpec(path=None, sources=Path("src"), version="1.0")
        return resolve_dump_request(DumpRequest(input=spec))

    @pytest.mark.parametrize("branch", ["binary", "header_only", "source_only"])
    @pytest.mark.parametrize("from_headers", [True, False])
    def test_every_branch_is_stamped_conditionally(
        self, branch: str, from_headers: bool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each branch's own executor is stubbed; the wrapper under test is real.

        Stubbing at the branch executor (rather than at `execute_dump_request`
        itself) is what makes this a test of the join: the stub returns an
        unstamped snapshot, so only the production wrapper can license it. The
        `from_headers=False` arm keeps the grant conditional, so a DWARF-only
        dump through any branch is still denied.
        """
        import abicheck.service_dump_pipeline as pipeline

        produced = AbiSnapshot(
            library="libfoo.so", version="1.0", from_headers=from_headers
        )
        assert produced.live_source_evidence is False

        class _Outcome:
            snapshot = produced
            effective_depth = None
            resolved_execution_context = None
            effective_includes: list[Path] = []
            effective_compile_context = None

        if branch == "binary":
            monkeypatch.setattr(
                pipeline, "_resolve_side_snapshot_impl", lambda *a, **kw: _Outcome()
            )
            monkeypatch.setattr(
                pipeline, "enforce_requested_depth", lambda *a, **kw: None
            )
            monkeypatch.setattr(
                pipeline, "side_effective_compile_context", lambda *a, **kw: None
            )
        elif branch == "header_only":
            monkeypatch.setattr(
                pipeline,
                "execute_header_only_dump_request",
                lambda *a, **kw: _Outcome(),
            )
        else:
            monkeypatch.setattr(
                pipeline,
                "execute_source_only_dump_request",
                lambda *a, **kw: _Outcome(),
            )

        result = pipeline.execute_dump_request(self._resolved(branch))
        assert result.snapshot.live_source_evidence is from_headers
