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

"""Per-bundle-member baseline header staging
(:mod:`abicheck.buildsource.bundle_member_snapshots`).

The invariant that matters here is the one the ``BUNDLE_CHECK_DEPTHS``
restriction exists to protect: **a header-derived change between the
baseline and the candidate must actually be detected, per member.** That is
exactly what a bundle check cannot do today, because its old-side operand is
a directory of raw binaries with no historical header evidence -- so both
sides end up parsed from the same current checkout and the change vanishes.

So the central test here is not an example: it *generates* member sets and
per-member removal sets, stages the old side through the real staging
function, runs the real public workflow (``abicheck compare`` over two
directories) against it, and checks the reported removals against an oracle
built from the generator's own bookkeeping -- never from any abicheck
detector, registry or helper.

It also states the differential-test rule AGENTS.md spells out: a test
claiming "the historical old side is what produced this finding" asserts
nothing unless the *other* configuration (both sides from the candidate,
which is today's bundle behavior) is shown to produce no finding on the same
inputs. Both configurations run inside the same test, on the same generated
input, and the staged bytes are asserted to differ from the candidate's --
so a staging implementation that silently copied the candidate, or a compare
that ignored the staged directory, fails here rather than passing vacuously.
"""

from __future__ import annotations

import gzip
import json
import random
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.buildsource.baseline_set import (
    BaselineArtifact,
    BaselineManifest,
    compute_snapshot_content_hash,
)
from abicheck.buildsource.bundle_member_snapshots import (
    MEMBER_SNAPSHOTS_DIRNAME,
    MemberSnapshot,
    MemberSnapshotResolution,
    MemberStagingError,
    resolve_member_snapshots,
    stage_bundle_baseline_headers,
    stage_member_snapshots,
)
from abicheck.elf_metadata import ElfMetadata
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json
from abicheck.storage.sectioned_document import (
    from_sectioned_document,
    is_sectioned_document,
)

# ── fixture builders (deliberately independent of the code under test) ────


def _mangled(name: str) -> str:
    """The test's own Itanium-ish mangling for a nullary function.

    Deliberately the test's, not abicheck's: the oracle in
    :class:`TestHeaderChangeIsDetectedPerMember` is expressed in these
    symbols, so deriving them from a production helper would let one
    implementation answer both sides of the comparison.
    """
    return f"_Z{len(name)}{name}v"


def _header_snapshot(library: str, names: list[str], version: str) -> AbiSnapshot:
    """A header-derived snapshot declaring exactly *names*.

    ``from_headers=True`` is the fact the staging assessment reads, and the
    declarations are the header-level surface whose loss the invariant test
    expects to be reported.
    """
    return AbiSnapshot(
        library=library,
        version=version,
        functions=[
            Function(
                name=name,
                mangled=_mangled(name),
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
            for name in names
        ],
        from_headers=True,
    )


def _write_snapshot(path: Path, snapshot: AbiSnapshot) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(snapshot_to_json(snapshot), encoding="utf-8")
    return path


def _write_baseline_set(
    baseline_dir: Path, members: dict[str, AbiSnapshot]
) -> BaselineManifest:
    """Write a baseline-set directory the way ``actions/baseline`` does: one
    ``<member>.abicheck.json`` per library plus a ``manifest.json`` whose
    ``artifacts[]`` rows carry the snapshot path and its content digest."""
    baseline_dir.mkdir(parents=True, exist_ok=True)
    artifacts = []
    for member, snapshot in members.items():
        rel = f"{member}.abicheck.json"
        path = _write_snapshot(baseline_dir / rel, snapshot)
        raw = json.loads(path.read_text(encoding="utf-8"))
        # Mirror the *producer* (actions/baseline/build_manifest.py): the
        # recorded digest is taken over the unwrapped snapshot, not the
        # sectioned envelope it is written in.
        if is_sectioned_document(raw):
            raw = from_sectioned_document(raw)
        artifacts.append(
            {
                "library": member,
                "artifact": f"/build/{member}.so",
                "snapshot": rel,
                "sha256": compute_snapshot_content_hash(raw),
            }
        )
    manifest = {
        "manifest_version": 1,
        "profile": "linux-x86_64",
        "artifacts": artifacts,
    }
    (baseline_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return BaselineManifest(
        manifest_version=1,
        profile="linux-x86_64",
        artifacts=[BaselineArtifact.from_dict(a) for a in artifacts],
    )


def _compare_dirs(old_dir: Path, new_dir: Path) -> dict:
    """Run the real public workflow -- ``abicheck compare OLD NEW --format
    json`` over two directories -- and return the parsed release summary."""
    from abicheck.cli import main

    result = CliRunner().invoke(
        main, ["compare", str(old_dir), str(new_dir), "--format", "json"]
    )
    assert result.stdout, (
        f"no JSON on stdout (exit {result.exit_code}):\n{result.output}"
    )
    return json.loads(result.stdout)


def _reported_removed_symbols(summary: dict) -> dict[str, set[str]]:
    """Per-member set of removed symbols the rendered report actually carries.

    Reads the report's own published ``libraries[].findings[]`` rows rather
    than any abicheck helper, so the oracle comparison below is against the
    *user-facing* result. The member key is recovered from the library
    filename the way a reader would (``libfoo.abicheck.json`` -> ``libfoo``).
    """
    per_member: dict[str, set[str]] = {}
    for row in summary.get("libraries", []):
        symbols: set[str] = set()
        for finding in row.get("findings", []) or []:
            kind = str(finding.get("kind", ""))
            symbol = str(finding.get("symbol") or "")
            if "removed" in kind and symbol and not symbol.startswith("<"):
                symbols.add(symbol)
        member = str(row.get("library", "")).split(".abicheck.json")[0]
        per_member[member] = symbols
    return per_member


# ── the invariant: a header-derived change is detected, per member ────────


class TestHeaderChangeIsDetectedPerMember:
    """Generated member sets, generated per-member removals, one oracle.

    The oracle is the generator's own record of which declarations it left
    out of the candidate -- not a detector, not ``ChangeKind``, not the
    staging module. A vacuity guard asserts the generator actually removed
    something for at least one member in every case, so a generator that
    silently degenerated to "no change" cannot make the whole sweep pass.
    """

    @pytest.mark.parametrize("seed", [1, 2, 3, 5, 8, 13, 21])
    def test_generated_header_removals_are_reported_for_every_member(
        self, seed: int, tmp_path: Path
    ) -> None:
        rng = random.Random(seed)
        member_count = rng.randint(1, 4)
        members = [f"libmem{i}" for i in range(member_count)]

        baseline_snapshots: dict[str, AbiSnapshot] = {}
        expected_removed: dict[str, set[str]] = {}
        candidate_dir = tmp_path / "candidate"
        for member in members:
            declared = [f"{member}_fn{n}" for n in range(rng.randint(2, 5))]
            # Remove a non-empty subset for at least one member; others may
            # legitimately be unchanged.
            removable = rng.sample(declared, rng.randint(0, len(declared) - 1))
            baseline_snapshots[member] = _header_snapshot(member, declared, "1.0")
            expected_removed[member] = set(removable)
            kept = [d for d in declared if d not in removable]
            _write_snapshot(
                candidate_dir / f"{member}.abicheck.json",
                _header_snapshot(member, kept, "2.0"),
            )
        if not any(expected_removed.values()):
            # Force the case to be a real differential for this seed.
            member = members[0]
            declared = [f.name for f in baseline_snapshots[member].functions]
            expected_removed[member] = {declared[0]}
            _write_snapshot(
                candidate_dir / f"{member}.abicheck.json",
                _header_snapshot(member, declared[1:], "2.0"),
            )
        assert any(expected_removed.values()), "vacuous case: nothing was removed"

        baseline_dir = tmp_path / "baseline"
        manifest = _write_baseline_set(baseline_dir, baseline_snapshots)

        staged = stage_bundle_baseline_headers(
            baseline_dir, manifest, members, tmp_path / "staging"
        )
        assert staged.header_evidence_state == "complete", staged.problems

        # (a) Both configurations genuinely differ: the staged old side's
        #     bytes are not the candidate's for any member whose declared
        #     surface changed.
        for member, removed in expected_removed.items():
            if not removed:
                continue
            staged_bytes = (
                staged.snapshots_dir / f"{member}.abicheck.json"
            ).read_bytes()
            candidate_bytes = (candidate_dir / f"{member}.abicheck.json").read_bytes()
            assert staged_bytes != candidate_bytes

        # (b) The historical configuration: the staged baseline vs. the
        #     candidate detects exactly the generated removals.
        reported = _reported_removed_symbols(
            _compare_dirs(staged.snapshots_dir, candidate_dir)
        )
        for member, removed in expected_removed.items():
            expected_symbols = {_mangled(name) for name in removed}
            assert expected_symbols == reported.get(member, set()), (
                f"member {member}: expected removals {sorted(expected_symbols)}, "
                f"reported {sorted(reported.get(member, set()))}"
            )

        # (c) The configuration today's bundle check actually runs -- both
        #     sides from the candidate, because the old operand carries no
        #     header evidence -- sees nothing. Without this, (b) would pass
        #     just as well for a staging that copied the candidate.
        self_reported = _reported_removed_symbols(
            _compare_dirs(candidate_dir, candidate_dir)
        )
        for member in members:
            assert not self_reported.get(member, set()), (
                "comparing the candidate against itself reported removals -- "
                "the differential in (b) cannot be attributed to the staged "
                "historical old side"
            )


# ── the header-evidence assessment ───────────────────────────────────────


class TestHeaderEvidenceAssessment:
    """Exhaustive small-domain enumeration against an independent oracle.

    The oracle is stated directly from the documented rule ("complete iff
    every requested member resolved *and* every staged snapshot is
    header-derived"), deliberately not by calling any property of the object
    under test.
    """

    @pytest.mark.parametrize(
        "header_flags",
        [
            (),
            (True,),
            (False,),
            (True, False),
            (False, True),
            (True, True),
            (False, False),
        ],
    )
    @pytest.mark.parametrize("with_problem", [False, True])
    def test_state_matches_the_documented_rule(
        self, header_flags: tuple[bool, ...], with_problem: bool, tmp_path: Path
    ) -> None:
        baseline_dir = tmp_path / "baseline"
        snapshots = {}
        for i, from_headers in enumerate(header_flags):
            member = f"lib{i}"
            snapshot = _header_snapshot(member, [f"{member}_fn"], "1.0")
            if not from_headers:
                snapshot = AbiSnapshot(
                    library=member,
                    version="1.0",
                    functions=list(snapshot.functions),
                    from_headers=False,
                )
            snapshots[member] = snapshot
        manifest = _write_baseline_set(baseline_dir, snapshots)
        members = list(snapshots)
        if with_problem:
            members.append("libmissing")

        staged = stage_bundle_baseline_headers(
            baseline_dir, manifest, members, tmp_path / "staging"
        )

        expected_complete = (
            bool(header_flags) and all(header_flags) and not with_problem
        )
        assert staged.header_evidence_complete is expected_complete
        if expected_complete:
            assert staged.header_evidence_state == "complete"
        elif any(header_flags):
            assert staged.header_evidence_state == "partial"
        else:
            assert staged.header_evidence_state == "none"

    def test_unloadable_snapshot_is_not_header_evidence(self, tmp_path: Path) -> None:
        """A staged file this build cannot load answers "no proven header
        evidence" rather than raising -- the operand still stages."""
        baseline_dir = tmp_path / "baseline"
        manifest = _write_baseline_set(
            baseline_dir, {"libfoo": _header_snapshot("libfoo", ["a"], "1.0")}
        )
        (baseline_dir / "libfoo.abicheck.json").write_text("{}", encoding="utf-8")
        # Digest no longer matches, so resolve rejects it -- point the
        # staging at the truncated file directly to isolate the load path.
        staged = stage_member_snapshots(
            MemberSnapshotResolution(
                snapshots=(
                    MemberSnapshot(
                        member="libfoo", path=baseline_dir / "libfoo.abicheck.json"
                    ),
                ),
            ),
            tmp_path / "staging",
        )
        assert manifest.artifact_for("libfoo") is not None
        assert staged.members_without_header_evidence == ("libfoo",)
        assert (staged.snapshots_dir / "libfoo.abicheck.json").is_file()


# ── resolution: every rejection cause stays its own cause ─────────────────


class TestMemberSnapshotResolution:
    def test_resolves_every_declared_member(self, tmp_path: Path) -> None:
        baseline_dir = tmp_path / "baseline"
        manifest = _write_baseline_set(
            baseline_dir,
            {
                "liba": _header_snapshot("liba", ["a"], "1.0"),
                "libb": _header_snapshot("libb", ["b"], "1.0"),
            },
        )
        resolution = resolve_member_snapshots(baseline_dir, manifest, ["liba", "libb"])
        assert resolution.complete
        assert [s.member for s in resolution.snapshots] == ["liba", "libb"]

    def test_member_absent_from_manifest_lists_the_known_targets(
        self, tmp_path: Path
    ) -> None:
        baseline_dir = tmp_path / "baseline"
        manifest = _write_baseline_set(
            baseline_dir, {"liba": _header_snapshot("liba", ["a"], "1.0")}
        )
        resolution = resolve_member_snapshots(baseline_dir, manifest, ["libz"])
        assert not resolution.complete
        assert "not in this baseline-set's manifest" in resolution.problems["libz"]
        assert "liba" in resolution.problems["libz"]

    def test_escaping_snapshot_path_is_refused(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside.abicheck.json"
        _write_snapshot(outside, _header_snapshot("liba", ["a"], "1.0"))
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        manifest = BaselineManifest(
            manifest_version=1,
            artifacts=[
                BaselineArtifact(library="liba", snapshot="../outside.abicheck.json")
            ],
        )
        resolution = resolve_member_snapshots(baseline_dir, manifest, ["liba"])
        assert "outside the baseline-set directory" in resolution.problems["liba"]

    def test_digest_mismatch_is_its_own_cause(self, tmp_path: Path) -> None:
        baseline_dir = tmp_path / "baseline"
        manifest = _write_baseline_set(
            baseline_dir, {"liba": _header_snapshot("liba", ["a"], "1.0")}
        )
        # Rewrite the snapshot with different ABI content: the manifest's
        # recorded digest no longer matches.
        _write_snapshot(
            baseline_dir / "liba.abicheck.json",
            _header_snapshot("liba", ["a", "b"], "1.0"),
        )
        resolution = resolve_member_snapshots(baseline_dir, manifest, ["liba"])
        assert not resolution.complete
        assert resolution.problems["liba"]
        assert "not in this baseline-set's manifest" not in resolution.problems["liba"]

    def test_duplicate_manifest_rows_are_ambiguous(self, tmp_path: Path) -> None:
        baseline_dir = tmp_path / "baseline"
        _write_baseline_set(
            baseline_dir, {"liba": _header_snapshot("liba", ["a"], "1.0")}
        )
        manifest = BaselineManifest(
            manifest_version=1,
            artifacts=[
                BaselineArtifact(library="liba", snapshot="liba.abicheck.json"),
                BaselineArtifact(library="liba", snapshot="liba.abicheck.json"),
            ],
        )
        resolution = resolve_member_snapshots(baseline_dir, manifest, ["liba"])
        assert "ambiguous which one is authoritative" in resolution.problems["liba"]

    @pytest.mark.parametrize(
        "member",
        ["", ".", "..", "sub/lib", "sub\\lib", "/abs/lib", "lib\nfoo", "lib\x7f"],
    )
    def test_unsafe_member_names_never_reach_the_filesystem(
        self, member: str, tmp_path: Path
    ) -> None:
        baseline_dir = tmp_path / "baseline"
        manifest = _write_baseline_set(
            baseline_dir, {"liba": _header_snapshot("liba", ["a"], "1.0")}
        )
        resolution = resolve_member_snapshots(baseline_dir, manifest, [member])
        assert not resolution.complete
        assert resolution.snapshots == ()


# ── staging mechanics ────────────────────────────────────────────────────


class TestStaging:
    def test_staged_directory_is_a_clean_one_snapshot_per_member_tree(
        self, tmp_path: Path
    ) -> None:
        baseline_dir = tmp_path / "baseline"
        members = {
            "liba": _header_snapshot("liba", ["a"], "1.0"),
            "libb": _header_snapshot("libb", ["b"], "1.0"),
        }
        manifest = _write_baseline_set(baseline_dir, members)
        # The baseline-set root also holds manifest.json (and, for a real
        # bundle baseline, binaries/) -- the staged tree must hold neither,
        # or a directory compare would double-discover every member.
        (baseline_dir / "binaries").mkdir()
        (baseline_dir / "binaries" / "liba").write_bytes(b"\x7fELF stub")

        staged = stage_bundle_baseline_headers(
            baseline_dir, manifest, list(members), tmp_path / "staging"
        )
        assert staged.snapshots_dir.name == MEMBER_SNAPSHOTS_DIRNAME
        assert sorted(p.name for p in staged.snapshots_dir.iterdir()) == [
            "liba.abicheck.json",
            "libb.abicheck.json",
        ]

    def test_stale_staged_snapshot_is_swept(self, tmp_path: Path) -> None:
        baseline_dir = tmp_path / "baseline"
        manifest = _write_baseline_set(
            baseline_dir, {"liba": _header_snapshot("liba", ["a"], "1.0")}
        )
        staging = tmp_path / "staging"
        stale = staging / MEMBER_SNAPSHOTS_DIRNAME / "libgone.abicheck.json"
        stale.parent.mkdir(parents=True)
        stale.write_text("{}", encoding="utf-8")
        keep = staging / MEMBER_SNAPSHOTS_DIRNAME / "notes.txt"
        keep.write_text("unrelated", encoding="utf-8")

        staged = stage_bundle_baseline_headers(
            baseline_dir, manifest, ["liba"], staging
        )
        assert not stale.exists(), (
            "a member dropped since an earlier staging run would read as a "
            "removed library on the new side"
        )
        assert keep.exists(), "only files this staging writes may be swept"
        assert (staged.snapshots_dir / "liba.abicheck.json").is_file()

    def test_compression_suffix_is_preserved(self, tmp_path: Path) -> None:
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        raw = snapshot_to_json(_header_snapshot("liba", ["a"], "1.0"))
        gz = baseline_dir / "liba.abicheck.json.gz"
        gz.write_bytes(gzip.compress(raw.encode("utf-8")))
        manifest = BaselineManifest(
            manifest_version=1,
            artifacts=[
                BaselineArtifact(library="liba", snapshot="liba.abicheck.json.gz")
            ],
        )
        staged = stage_bundle_baseline_headers(
            baseline_dir, manifest, ["liba"], tmp_path / "staging"
        )
        assert (staged.snapshots_dir / "liba.abicheck.json.gz").is_file()
        # A gzip snapshot still answers the header-evidence question, so the
        # suffix is not merely cosmetic.
        assert staged.members_with_header_evidence == ("liba",)

    def test_two_members_colliding_on_one_filename_is_an_error(
        self, tmp_path: Path
    ) -> None:
        baseline_dir = tmp_path / "baseline"
        manifest = _write_baseline_set(
            baseline_dir, {"liba": _header_snapshot("liba", ["a"], "1.0")}
        )
        assert manifest.artifact_for("liba") is not None
        path = baseline_dir / "liba.abicheck.json"
        with pytest.raises(MemberStagingError, match="stage to"):
            stage_member_snapshots(
                MemberSnapshotResolution(
                    snapshots=(
                        MemberSnapshot(member="liba", path=path),
                        MemberSnapshot(member="liba", path=path),
                    )
                ),
                tmp_path / "staging",
            )


# ── the consumer: actions/resolve-baseline's own wiring ───────────────────


def _load_resolve_baseline_module():
    """Import ``actions/resolve-baseline/resolve_baseline.py`` in-process.

    In-process rather than through ``run.sh``: the shell wrapper runs the
    script with ``python3 -I``, which resolves ``abicheck`` from the
    *installed* distribution, so a subprocess test would exercise whatever
    build happens to be installed rather than this tree's staging module.
    """
    import importlib.util

    path = (
        Path(__file__).resolve().parents[1]
        / "actions"
        / "resolve-baseline"
        / "resolve_baseline.py"
    )
    spec = importlib.util.spec_from_file_location("_resolve_baseline_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestResolveBaselineActionWiring:
    """The staging is reachable from the Action, and its two outputs say the
    truth about what was staged -- a staging function nothing calls is not a
    shipped capability."""

    def _bundle_baseline(
        self, tmp_path: Path, *, from_headers: bool = True
    ) -> tuple[Path, list[str]]:
        members = ["liba", "libb"]
        snapshots: dict[str, AbiSnapshot] = {}
        for member in members:
            snapshot = _header_snapshot(member, [f"{member}_fn"], "1.0")
            if not from_headers:
                snapshot = AbiSnapshot(
                    library=member,
                    version="1.0",
                    functions=list(snapshot.functions),
                    from_headers=False,
                )
            snapshots[member] = snapshot
        baseline_dir = tmp_path / "baseline"
        written = _write_baseline_set(baseline_dir, snapshots)
        # A bundle-scoped baseline-set also stages each member's binary, and
        # resolve_bundle() requires one per member -- rewrite manifest.json
        # with the binary rows alongside the snapshot rows.
        binaries = baseline_dir / "binaries"
        binaries.mkdir()
        artifacts = []
        for member in members:
            (binaries / member).write_bytes(b"\x7fELF placeholder")
            artifact = written.artifact_for(member)
            assert artifact is not None
            artifacts.append(
                {
                    "library": member,
                    "artifact": artifact.artifact,
                    "snapshot": artifact.snapshot,
                    "sha256": artifact.sha256,
                    "binary": f"binaries/{member}",
                }
            )
        (baseline_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "manifest_version": 1,
                    "profile": "linux-x86_64",
                    "artifacts": artifacts,
                }
            ),
            encoding="utf-8",
        )
        return baseline_dir, members

    def _invoke(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        baseline_dir: Path,
        members: list[str],
        extra: list[str],
    ) -> tuple[int, dict[str, str]]:
        # The staged placeholder bytes are not a full ELF structure, and
        # resolve_bundle() deep-parses each one; stub that parse the way
        # test_baseline_set.py's own bundle tests do, so this test exercises
        # the staging wiring rather than an ELF fixture.
        monkeypatch.setattr(
            "abicheck.buildsource.baseline_set.parse_elf_metadata",
            lambda path: ElfMetadata(soname=path.name),
        )
        module = _load_resolve_baseline_module()
        code = module.main(
            [
                "--baseline-dir",
                str(baseline_dir),
                "--kind",
                "bundle",
                "--name",
                "core",
                "--members",
                json.dumps(members),
                "--profile",
                "linux-x86_64",
                "--required",
                "true",
                *extra,
            ]
        )
        out = capsys.readouterr().out
        outputs = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        return code, outputs

    def test_staging_is_reachable_and_reports_complete_evidence(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        baseline_dir, members = self._bundle_baseline(tmp_path)
        code, outputs = self._invoke(
            monkeypatch,
            capsys,
            baseline_dir,
            members,
            ["--stage-member-snapshots", str(tmp_path / "staging")],
        )
        assert code == 0, outputs
        assert outputs["outcome"] == "resolved"
        assert outputs["member-header-evidence"] == "complete"
        staged_dir = Path(outputs["member-snapshots-dir"])
        assert sorted(p.name for p in staged_dir.iterdir()) == [
            "liba.abicheck.json",
            "libb.abicheck.json",
        ]
        # The binaries operand is untouched: staging adds an old-side
        # evidence path, it does not replace the bundle-graph one.
        assert outputs["binaries-dir"] == str(baseline_dir / "binaries")

    def test_elf_only_baseline_reports_no_header_evidence_but_still_resolves(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        baseline_dir, members = self._bundle_baseline(tmp_path, from_headers=False)
        code, outputs = self._invoke(
            monkeypatch,
            capsys,
            baseline_dir,
            members,
            ["--stage-member-snapshots", str(tmp_path / "staging")],
        )
        assert code == 0, outputs
        assert outputs["member-header-evidence"] == "none"

    def test_without_the_flag_nothing_is_staged(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        baseline_dir, members = self._bundle_baseline(tmp_path)
        code, outputs = self._invoke(monkeypatch, capsys, baseline_dir, members, [])
        assert code == 0, outputs
        assert outputs["member-snapshots-dir"] == ""
        assert outputs["member-header-evidence"] == "none"
        assert not (tmp_path / "staging").exists()
