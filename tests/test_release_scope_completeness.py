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

"""ADR-065 S2: the incompleteness axis on the release fan-out.

The ADR's own acceptance contract, made executable:

* one candidate against a twelve-variant baseline yields one comparison and
  eleven ``out_of_scope`` members, zero removals (D9);
* a mixed matrix -- one clean pair, one ``unsupported`` selected member --
  reports the clean pair's findings *and* an incomplete scope: exit ``0``
  under ``warn``, ``1`` under ``block``, and ``4`` under either when the
  compared member is itself BREAKING (D6);
* a run with zero valid comparisons reports ``no comparison completed``
  under every policy (D7);
* adding an unrelated baseline member never changes a selected comparison,
  and input order never changes pairing (property tests over the record
  builder itself, per ``AGENTS.md``'s "primitive-level property tests");
* exit ``8`` needs D2's proof: a stored ``ProjectSnapshot`` NEW package;
* a stranded library whose dump failed is persisted as a degraded member
  and a stored/stored comparison skips it, saying so (D8).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from hypothesis import given, settings, strategies as st

from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.scope_acquisition import (
    AcquisitionState,
    InventoryCompleteness,
    ScopeAcquisitionRecord,
    SideInventory,
)
from abicheck.policy.exit_decision import ExitDecision, ExitReason
from abicheck.policy.outcome import OperationalStatus, RunOutcome, ScopeCompleteness
from abicheck.serialization import snapshot_to_json
from abicheck.workflows.release_scope import (
    DIRECT_PAIR_KEY,
    ReleaseInventoryEvidence,
    build_release_scope_record,
    release_inventory_evidence,
    unmatched_names,
)

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _fn(name: str) -> Function:
    return Function(
        name=name,
        mangled=f"_Z3{name}v",
        return_type="int",
        visibility=Visibility.PUBLIC,
    )


def _snap(
    library: str, funcs: tuple[str, ...] = ("foo",), version: str = "1.0"
) -> AbiSnapshot:
    return AbiSnapshot(
        library=library,
        version=version,
        functions=[_fn(f) for f in funcs],
        from_headers=True,
    )


def _write(dir_: Path, name: str, snap: AbiSnapshot) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / name).write_text(snapshot_to_json(snap), encoding="utf-8")


def _write_unreadable(dir_: Path, name: str, snap: AbiSnapshot) -> None:
    """A stored snapshot this build cannot read (schema far newer than this
    reader) -- the one `unsupported` trigger constructible without a foreign
    binary format."""
    dir_.mkdir(parents=True, exist_ok=True)
    doc = json.loads(snapshot_to_json(snap))
    doc["schema_version"] = 9999
    (dir_ / name).write_text(json.dumps(doc), encoding="utf-8")


def _invoke(*args: str) -> tuple[int, str]:
    from abicheck.cli import main

    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.output


def _invoke_json(*args: str) -> tuple[int, dict[str, object]]:
    """JSON from stdout alone: the fan-out's per-member stderr notices
    (``Unsupported: ...``) are interleaved into ``result.output``."""
    from abicheck.cli import main

    result = CliRunner().invoke(main, [*args, "--format", "json"])
    return result.exit_code, json.loads(result.stdout)


_LIVE = release_inventory_evidence(old_stored=False, new_stored=False)


# ---------------------------------------------------------------------------
# D9: one candidate against a twelve-variant baseline
# ---------------------------------------------------------------------------


class TestTwelveVariantBaseline:
    @pytest.fixture
    def dirs(self, tmp_path: Path) -> tuple[Path, Path]:
        old, new = tmp_path / "baseline", tmp_path / "candidate"
        for i in range(12):
            _write(old, f"libv{i}.json", _snap(f"libv{i}.so"))
        _write(new, "libv3.json", _snap("libv3.so"))
        return old, new

    @pytest.mark.parametrize("extra", [(), ("--fail-on-removed-library",)])
    def test_one_comparison_eleven_out_of_scope_zero_removals(
        self, dirs: tuple[Path, Path], extra: tuple[str, ...]
    ) -> None:
        old, new = dirs
        code, doc = _invoke_json("compare", str(old), str(new), *extra)
        assert code == 0
        assert [lib["library"] for lib in doc["libraries"]] == ["libv3.json"]
        scope = doc["comparison_scope"]
        assert scope["selection"] == "current_artifact"
        assert scope["completeness"] == "complete"
        assert scope["counts"]["available"] == 1
        assert scope["counts"]["out_of_scope"] == 11
        assert scope["proven_removed"] == []
        assert scope["unchecked"] == []
        assert doc["run_outcome"]["scope"] == "complete"
        assert doc["exit"]["removed_required_library_contribution"] == 0
        # `unmatched_old` keeps its key and now means exactly that.
        assert len(doc["unmatched_old"]) == 11
        assert "libv3.json" not in doc["unmatched_old"]

    def test_block_policy_is_a_no_op_on_a_complete_scope(
        self, dirs: tuple[Path, Path]
    ) -> None:
        old, new = dirs
        code, doc = _invoke_json(
            "compare", str(old), str(new), "--on-incomplete-scope", "block"
        )
        assert code == 0
        assert doc["exit"]["incomplete_scope_contribution"] == 0


# ---------------------------------------------------------------------------
# D6: mixed matrix under warn and block
# ---------------------------------------------------------------------------


class TestMixedMatrix:
    def _dirs(self, tmp_path: Path, *, breaking: bool) -> tuple[Path, Path]:
        old, new = tmp_path / "old", tmp_path / "new"
        _write(old, "liba.json", _snap("liba.so", ("foo", "bar")))
        _write(
            new, "liba.json", _snap("liba.so", ("foo",) if breaking else ("foo", "bar"))
        )
        _write(old, "libb.json", _snap("libb.so"))
        _write_unreadable(new, "libb.json", _snap("libb.so"))
        return old, new

    def test_warn_reports_clean_pair_and_incomplete_scope_exit_0(
        self, tmp_path: Path
    ) -> None:
        old, new = self._dirs(tmp_path, breaking=False)
        code, doc = _invoke_json("compare", str(old), str(new))
        assert code == 0
        verdicts = {lib["library"]: lib["verdict"] for lib in doc["libraries"]}
        assert verdicts == {"liba.json": "NO_CHANGE", "libb.json": "unsupported"}
        scope = doc["comparison_scope"]
        assert scope["completeness"] == "incomplete"
        assert scope["unchecked"] == ["libb.json"]
        assert scope["counts"]["unsupported"] == 1
        assert scope["policy"] == "warn"
        assert scope["incomplete_scope_exit_contribution"] == 0
        assert doc["run_outcome"]["scope"] == "incomplete"
        assert doc["run_outcome"]["gate"] == "none"
        assert doc["run_outcome"]["operational"] == "none"
        assert doc["exit"]["reasons"] == ["clean"]
        # Every view names the unchecked member.
        _, md = _invoke("compare", str(old), str(new), "--format", "markdown")
        assert "Comparison Scope" in md
        assert "libb.json" in md and "unsupported" in md
        assert "scope incompletely checked" in md

    def test_block_exits_1_naming_the_scope_axis(self, tmp_path: Path) -> None:
        old, new = self._dirs(tmp_path, breaking=False)
        code, doc = _invoke_json(
            "compare", str(old), str(new), "--on-incomplete-scope", "block"
        )
        assert code == 1
        assert doc["exit"]["code"] == 1
        assert doc["exit"]["reasons"] == ["incomplete_scope"]
        assert doc["exit"]["incomplete_scope_contribution"] == 1
        assert doc["comparison_scope"]["incomplete_scope_exit_contribution"] == 1
        assert doc["run_outcome"]["scope"] == "incomplete"

    @pytest.mark.parametrize("policy", ["warn", "block"])
    def test_a_breaking_compared_member_still_exits_4(
        self, tmp_path: Path, policy: str
    ) -> None:
        old, new = self._dirs(tmp_path, breaking=True)
        code, doc = _invoke_json(
            "compare", str(old), str(new), "--on-incomplete-scope", policy
        )
        assert code == 4
        assert doc["exit"]["reasons"] == ["compatibility_gate"]
        assert doc["run_outcome"]["gate"] == "abi_breaking"
        assert doc["run_outcome"]["scope"] == "incomplete"

    def test_unsupported_is_not_an_operational_error(self, tmp_path: Path) -> None:
        """Before S2 an unreadable member was an `ERROR` floored to exit 4."""
        old, new = self._dirs(tmp_path, breaking=False)
        _, doc = _invoke_json("compare", str(old), str(new))
        assert doc["exit"]["operational_error_contribution"] == 0
        assert doc["changed_libraries"] == []


# ---------------------------------------------------------------------------
# D7: zero completed comparisons is never success
# ---------------------------------------------------------------------------


class TestZeroPairRelease:
    @pytest.mark.parametrize("policy", ["warn", "block"])
    def test_disjoint_directories(self, tmp_path: Path, policy: str) -> None:
        old, new = tmp_path / "old", tmp_path / "new"
        _write(old, "liba.json", _snap("liba.so"))
        _write(new, "libb.json", _snap("libb.so"))
        code, doc = _invoke_json(
            "compare", str(old), str(new), "--on-incomplete-scope", policy
        )
        assert code == 1
        assert doc["libraries"] == []
        assert doc["run_outcome"]["operational"] == "no_comparison_completed"
        assert doc["run_outcome"]["compatibility"] is None
        assert doc["run_outcome"]["scope"] == "incomplete"
        assert doc["exit"]["no_comparison_completed_contribution"] == 1
        assert "no_comparison_completed" in doc["exit"]["reasons"]
        assert doc["comparison_scope"]["no_comparison_completed"] is True
        assert doc["comparison_scope"]["no_comparison_completed_exit_contribution"] == 1
        _, md = _invoke("compare", str(old), str(new), "--format", "markdown")
        assert "no comparison completed" in md

    def test_every_member_unsupported_is_also_no_comparison(
        self, tmp_path: Path
    ) -> None:
        old, new = tmp_path / "old", tmp_path / "new"
        _write(old, "liba.json", _snap("liba.so"))
        _write_unreadable(new, "liba.json", _snap("liba.so"))
        code, doc = _invoke_json("compare", str(old), str(new))
        assert code == 1
        assert doc["run_outcome"]["operational"] == "no_comparison_completed"

    def test_exit_function_folds_the_axis_like_coverage(self) -> None:
        from abicheck.cli_compare_release_helpers import _exit_compare_release

        with pytest.raises(SystemExit) as info:
            _exit_compare_release(
                "NO_CHANGE", False, [], no_comparison_completed_exit_contribution=1
            )
        assert info.value.code == 1
        with pytest.raises(SystemExit) as info:
            _exit_compare_release(
                "BREAKING", False, [], incomplete_scope_exit_contribution=1
            )
        assert info.value.code == 4
        with pytest.raises(SystemExit) as info:
            _exit_compare_release(
                "NO_CHANGE", False, [], 0, incomplete_scope_exit_contribution=1
            )
        assert info.value.code == 1


# ---------------------------------------------------------------------------
# D2: exit 8 needs a proven-complete NEW inventory
# ---------------------------------------------------------------------------


def _write_stored_package(root: Path, libraries: dict[str, AbiSnapshot]) -> None:
    from abicheck.bundle_facts import BundleFacts, capture_bundle_facts
    from abicheck.bundle_facts_store import write_bundle_facts_package
    from abicheck.project_snapshot_store import (
        DirectoryObjectStore,
        write_project_manifest,
    )

    facts = capture_bundle_facts(libraries, variant_fingerprint="gcc13")
    facts = BundleFacts(
        variant_fingerprint=facts.variant_fingerprint,
        per_library_snapshots=facts.per_library_snapshots,
        library_filenames={name: name for name in facts.per_library_snapshots},
    )
    store = DirectoryObjectStore(root)
    manifest = write_bundle_facts_package(facts, store=store, variant_id="default")
    write_project_manifest(root, manifest)


class TestProvenRemoval:
    def test_stored_new_package_proves_the_removal(self, tmp_path: Path) -> None:
        old = tmp_path / "old"
        _write(old, "liba.so.json", _snap("liba.so"))
        _write(old, "libb.so.json", _snap("libb.so"))
        _write(old, "libgone.so.json", _snap("libgone.so"))
        new = tmp_path / "new_pkg"
        _write_stored_package(
            new, {"liba.so": _snap("liba.so"), "libb.so": _snap("libb.so")}
        )

        code, doc = _invoke_json("compare", str(old), str(new), "-j", "1")
        scope = doc["comparison_scope"]
        assert scope["new_inventory"]["completeness"] == "proven"
        assert scope["old_inventory"]["completeness"] == "unproven"
        assert scope["proven_removed"] == ["libgone.so.json"]
        assert (
            scope["completeness"] == "complete"
        )  # a proven removal is not "unchecked"
        assert code == 0
        assert doc["verdict"] == "COMPATIBLE_WITH_RISK"

        code, doc = _invoke_json(
            "compare", str(old), str(new), "-j", "1", "--fail-on-removed-library"
        )
        assert code == 8
        assert doc["exit"]["reasons"] == ["removed_required_library"]

    def test_a_proven_one_member_release_is_not_narrowed_by_d9(
        self, tmp_path: Path
    ) -> None:
        """D9's one-candidate inference must never override a proof: a
        stored NEW package listing one library *is* a one-library release."""
        old = tmp_path / "old"
        _write(old, "liba.so.json", _snap("liba.so"))
        _write(old, "libgone.so.json", _snap("libgone.so"))
        new = tmp_path / "new_pkg"
        _write_stored_package(new, {"liba.so": _snap("liba.so")})
        code, doc = _invoke_json(
            "compare", str(old), str(new), "-j", "1", "--fail-on-removed-library"
        )
        assert code == 8
        assert doc["comparison_scope"]["selection"] == "all_expected"
        assert doc["comparison_scope"]["proven_removed"] == ["libgone.so.json"]


# ---------------------------------------------------------------------------
# The record builder's own contract (property tests)
# ---------------------------------------------------------------------------

_KEY = st.text(alphabet="abcdefgh", min_size=1, max_size=3)
_VERDICT = st.sampled_from(
    ["NO_CHANGE", "COMPATIBLE", "BREAKING", "ERROR", "not_comparable", "unsupported"]
)


def _maps(
    old_keys: list[str], new_keys: list[str]
) -> tuple[dict[str, Path], dict[str, Path]]:
    return (
        {k: Path(f"/old/{k}.so") for k in old_keys},
        {k: Path(f"/new/{k}.so") for k in new_keys},
    )


def _results(
    old_map: dict[str, Path], matched: list[str], verdicts: list[str]
) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for key, verdict in zip(matched, verdicts, strict=False):
        entry: dict[str, object] = {"library": old_map[key].name, "verdict": verdict}
        if verdict == "ERROR":
            entry["error"] = "boom"
        elif verdict in ("not_comparable", "unsupported"):
            entry["reason"] = "why"
        out.append(entry)
    return out


@st.composite
def _release_shapes(draw: st.DrawFn) -> tuple[list[str], list[str], list[str]]:
    old_keys = draw(st.lists(_KEY, unique=True, min_size=0, max_size=6))
    new_keys = draw(st.lists(_KEY, unique=True, min_size=0, max_size=6))
    matched = sorted(set(old_keys) & set(new_keys))
    verdicts = draw(st.lists(_VERDICT, min_size=len(matched), max_size=len(matched)))
    return old_keys, new_keys, verdicts


class TestRecordBuilderProperties:
    @settings(max_examples=150, deadline=None)
    @given(_release_shapes())
    def test_partition_is_exact_and_states_map_from_results(
        self, shape: tuple[list[str], list[str], list[str]]
    ) -> None:
        old_keys, new_keys, verdicts = shape
        old_map, new_map = _maps(old_keys, new_keys)
        matched = sorted(set(old_keys) & set(new_keys))
        record = build_release_scope_record(
            old_map, new_map, matched, _results(old_map, matched, verdicts), _LIVE
        )
        # Pairwise disjoint and summing to the expected set: every key
        # exactly once, counts totalling the union.
        assert sorted(m.member for m in record.members) == sorted(
            set(old_keys) | set(new_keys)
        )
        assert sum(record.counts().values()) == len(record.members)
        assert not (set(record.unchecked_members) & set(record.completed_members))
        by_key = {m.member: m for m in record.members}
        for key, verdict in zip(matched, verdicts, strict=False):
            expected = {
                "ERROR": AcquisitionState.FAILED,
                "not_comparable": AcquisitionState.FAILED,
                "unsupported": AcquisitionState.UNSUPPORTED,
            }.get(verdict, AcquisitionState.AVAILABLE)
            assert by_key[key].state is expected
            assert by_key[key].old_present and by_key[key].new_present
        # Unproven inventories on both sides: never a removal or addition.
        assert record.proven_removed_members == ()
        assert record.proven_added_members == ()
        # D7 falls out of the partition: nothing available <=> nothing completed.
        assert record.no_comparison_completed == (
            not any(m.state is AcquisitionState.AVAILABLE for m in record.members)
        )
        assert record.is_incomplete == (
            bool(record.unchecked_members) or record.no_comparison_completed
        )
        # The JSON codec is lossless.
        assert ScopeAcquisitionRecord.from_dict(record.to_dict()) == record

    @settings(max_examples=150, deadline=None)
    @given(_release_shapes(), st.randoms(use_true_random=False))
    def test_input_order_never_changes_the_record(
        self, shape: tuple[list[str], list[str], list[str]], rng: object
    ) -> None:
        old_keys, new_keys, verdicts = shape
        old_map, new_map = _maps(old_keys, new_keys)
        matched = sorted(set(old_keys) & set(new_keys))
        results = _results(old_map, matched, verdicts)
        reference = build_release_scope_record(
            old_map, new_map, matched, results, _LIVE
        )
        shuffled_old = list(old_map.items())
        shuffled_new = list(new_map.items())
        shuffled_results = list(results)
        shuffled_matched = list(matched)
        for seq in (shuffled_old, shuffled_new, shuffled_results, shuffled_matched):
            rng.shuffle(seq)  # type: ignore[attr-defined]
        again = build_release_scope_record(
            dict(shuffled_old),
            dict(shuffled_new),
            shuffled_matched,
            shuffled_results,
            _LIVE,
        )
        assert again == reference

    @settings(max_examples=150, deadline=None)
    @given(_release_shapes(), _KEY)
    def test_an_unrelated_baseline_member_never_changes_a_selected_comparison(
        self, shape: tuple[list[str], list[str], list[str]], extra: str
    ) -> None:
        old_keys, new_keys, verdicts = shape
        if extra in old_keys or extra in new_keys:
            return
        old_map, new_map = _maps(old_keys, new_keys)
        matched = sorted(set(old_keys) & set(new_keys))
        results = _results(old_map, matched, verdicts)
        before = build_release_scope_record(old_map, new_map, matched, results, _LIVE)
        bigger_old = {**old_map, extra: Path(f"/old/{extra}.so")}
        after = build_release_scope_record(bigger_old, new_map, matched, results, _LIVE)
        before_by_key = {m.member: m for m in before.members}
        after_by_key = {m.member: m for m in after.members}
        for key in matched:
            assert after_by_key[key] == before_by_key[key]
        assert after.completed_members == before.completed_members
        assert after.proven_removed_members == ()
        assert after.no_comparison_completed == before.no_comparison_completed
        # The extra member is unmatched (or, under D9's one-candidate rule,
        # out of scope) -- never anything that touches a compared member.
        assert after_by_key[extra].state in (
            AcquisitionState.NOT_SUPPLIED,
            AcquisitionState.OUT_OF_SCOPE,
        )
        assert f"{extra}.so" in unmatched_names(after, side="old")

    @settings(max_examples=100, deadline=None)
    @given(st.lists(_KEY, unique=True, min_size=2, max_size=8), _VERDICT)
    def test_d9_narrows_one_candidate_to_its_own_counterpart(
        self, old_keys: list[str], verdict: str
    ) -> None:
        candidate = old_keys[0]
        old_map, new_map = _maps(old_keys, [candidate])
        results = _results(old_map, [candidate], [verdict])
        record = build_release_scope_record(
            old_map, new_map, [candidate], results, _LIVE
        )
        assert record.selection == "current_artifact"
        others = [m for m in record.members if m.member != candidate]
        assert all(m.state is AcquisitionState.OUT_OF_SCOPE for m in others)
        assert len(others) == len(old_keys) - 1
        # Complete exactly when the one selected member itself compared.
        assert record.is_incomplete == (
            verdict in ("ERROR", "not_comparable", "unsupported")
        )
        # A proven-complete NEW inventory switches the inference off (D2 wins).
        proven = ReleaseInventoryEvidence(
            old=_LIVE.old,
            new=SideInventory(InventoryCompleteness.PROVEN, "test"),
        )
        strict = build_release_scope_record(
            old_map, new_map, [candidate], results, proven
        )
        assert strict.selection == "all_expected"
        assert sorted(m.member for m in strict.proven_removed_members) == sorted(
            old_keys[1:]
        )

    def test_direct_pair_is_one_member_scope(self) -> None:
        old_map = {DIRECT_PAIR_KEY: Path("/o/libx.so")}
        new_map = {DIRECT_PAIR_KEY: Path("/n/libx.so")}
        evidence = release_inventory_evidence(
            old_stored=False, new_stored=False, direct_pair=True
        )
        record = build_release_scope_record(
            old_map,
            new_map,
            [DIRECT_PAIR_KEY],
            [{"library": "libx.so", "verdict": "NO_CHANGE"}],
            evidence,
        )
        assert record.selection == "direct_pair"
        assert [m.state for m in record.members] == [AcquisitionState.AVAILABLE]
        assert not record.is_incomplete

    def test_duplicate_member_is_rejected(self) -> None:
        from abicheck.model.scope_acquisition import MemberAcquisition

        m = MemberAcquisition("k", AcquisitionState.AVAILABLE, True, True)
        with pytest.raises(ValueError, match="twice"):
            ScopeAcquisitionRecord((m, m), _LIVE.old, _LIVE.new, "all_expected")


# ---------------------------------------------------------------------------
# The axes themselves
# ---------------------------------------------------------------------------


class TestAxes:
    def test_run_outcome_scope_round_trips_and_backfills(self) -> None:
        outcome = RunOutcome(
            compatibility=None,
            assurance=None,
            gate=RunOutcome.__dataclass_fields__["gate"].type
            and __import__(
                "abicheck.policy.outcome", fromlist=["PolicyGateDecision"]
            ).PolicyGateDecision.NONE,
            operational=OperationalStatus.NO_COMPARISON_COMPLETED,
            scope=ScopeCompleteness.INCOMPLETE,
        )
        d = outcome.to_dict()
        assert d["scope"] == "incomplete"
        assert d["operational"] == "no_comparison_completed"
        assert RunOutcome.from_dict(d) == RunOutcome(
            compatibility=None,
            assurance=None,
            gate=outcome.gate,
            operational=OperationalStatus.NO_COMPARISON_COMPLETED,
            scope=ScopeCompleteness.INCOMPLETE,
        )
        legacy = {"gate": "none", "operational": "none"}
        restored = RunOutcome.from_dict(legacy)
        assert restored is not None and restored.scope is ScopeCompleteness.COMPLETE
        bogus = RunOutcome.from_dict({**legacy, "scope": "bogus"})
        assert bogus is not None and bogus.scope is ScopeCompleteness.COMPLETE

    def test_exit_decision_round_trips_the_new_axes(self) -> None:
        from abicheck.policy.exit_decision import resolve_exit_decision

        d = resolve_exit_decision(
            compatibility_contribution=0,
            contract_coverage_contribution=1,
            incomplete_scope_contribution=1,
            no_comparison_completed_contribution=1,
        )
        assert d.code == 1
        assert set(d.reasons) == {
            ExitReason.CONTRACT_COVERAGE,
            ExitReason.INCOMPLETE_SCOPE,
            ExitReason.NO_COMPARISON_COMPLETED,
        }
        assert ExitDecision.from_dict(d.to_dict()) == d
        legacy = {
            k: v
            for k, v in d.to_dict().items()
            if not k.startswith(("incomplete", "no_comparison"))
        }
        assert ExitDecision.from_dict(legacy).incomplete_scope_contribution == 0

    @pytest.mark.parametrize("severity", [False, True])
    @pytest.mark.parametrize("compat", [0, 2, 4])
    def test_release_resolver_folds_like_coverage(
        self, severity: bool, compat: int
    ) -> None:
        from abicheck.policy.exit_decision_precedence import (
            resolve_release_exit_decision,
        )

        d = resolve_release_exit_decision(
            not_comparable=False,
            severity_scheme_active=severity,
            verdict_or_severity_contribution=compat,
            incomplete_scope_contribution=1,
        )
        assert d.code == max(compat, 1)
        assert (ExitReason.INCOMPLETE_SCOPE in d.reasons) == (compat == 0)
        dominated = resolve_release_exit_decision(
            not_comparable=True,
            severity_scheme_active=severity,
            verdict_or_severity_contribution=compat,
            no_comparison_completed_contribution=1,
        )
        assert dominated.code == 16
        assert dominated.no_comparison_completed_contribution == 1

    def test_policy_validation(self) -> None:
        from abicheck.policy.scope_completeness import (
            incomplete_scope_exit_contribution,
            validate_incomplete_scope_policy,
        )

        assert validate_incomplete_scope_policy(None) == "warn"
        with pytest.raises(ValueError):
            validate_incomplete_scope_policy("maybe")
        assert incomplete_scope_exit_contribution(None, "block") == 0

    def test_release_outcome_reads_no_comparison_from_the_exit_block(self) -> None:
        from abicheck.policy.outcome_release import run_outcome_dict_for_release

        out = run_outcome_dict_for_release(
            None,
            {
                "compatibility_contribution": 0,
                "no_comparison_completed_contribution": 1,
            },
            scope=ScopeCompleteness.INCOMPLETE,
        )
        assert out["operational"] == "no_comparison_completed"
        assert out["scope"] == "incomplete"
        assert out["compatibility"] is None


# ---------------------------------------------------------------------------
# D8: degraded stranded capture
# ---------------------------------------------------------------------------


class TestDegradedStrandedCapture:
    def test_failed_stranded_dump_is_persisted_as_degraded(
        self, tmp_path: Path
    ) -> None:
        from abicheck.cli_compare_release_helpers import write_bundle_facts_out
        from abicheck.serialization import load_bundle_facts
        from abicheck.workflows.release_scope import StrandedLibraryResolution

        old_map = {"libx.so": tmp_path / "libx.so", "liby.so": tmp_path / "liby.so"}

        def resolve(path: Path) -> StrandedLibraryResolution | AbiSnapshot:
            if path.name == "libx.so":
                return StrandedLibraryResolution(
                    AbiSnapshot(library="libx.so", version=""), failure="ELF-only: boom"
                )
            return AbiSnapshot(
                library="liby.so", version=""
            )  # the pre-S2 bare contract

        out = tmp_path / "old.bundlefacts.json"
        write_bundle_facts_out(out, [], None, old_map, resolve_stranded_library=resolve)
        facts = load_bundle_facts(out)
        assert facts.degraded_members == {"libx.so": "ELF-only: boom"}
        assert set(facts.per_library_snapshots) == {"libx.so", "liby.so"}

    def test_stored_pair_skips_a_degraded_member_and_says_so(
        self, tmp_path: Path
    ) -> None:
        from abicheck.bundle_facts import capture_bundle_facts
        from abicheck.elf_metadata import ElfMetadata, ElfSymbol
        from abicheck.serialization import save_bundle_facts
        from abicheck.workflows.bundle_stored_pair_compare import (
            compare_stored_bundle_facts_pair,
        )

        def facts_path(name: str, *, degraded: bool) -> Path:
            snap = AbiSnapshot(
                library="libcore.so",
                version="1",
                elf=ElfMetadata(
                    soname="libcore.so",
                    symbols=[ElfSymbol(name="core_fn", visibility="default")],
                ),
                functions=[
                    Function(
                        name="core_fn",
                        mangled="core_fn",
                        return_type="int",
                        visibility=Visibility.PUBLIC,
                    )
                ],
            )
            facts = capture_bundle_facts(
                {"libcore.so": snap},
                degraded_members={"libcore.so": "ELF-only: boom"} if degraded else None,
            )
            path = tmp_path / name
            save_bundle_facts(facts, path)
            return path

        old = facts_path("old.json", degraded=True)
        new = facts_path("new.json", degraded=False)
        result = compare_stored_bundle_facts_pair(old, new)
        assert result.per_library == []
        assert any(
            "libcore.so" in msg and "degraded" in msg for msg in result.analysis_errors
        )


# ---------------------------------------------------------------------------
# PR comment
# ---------------------------------------------------------------------------


class TestPrComment:
    def _report(self, **scope: object) -> dict[str, object]:
        base_scope = {
            "completeness": "incomplete",
            "policy": "warn",
            "incomplete_scope_exit_contribution": 0,
            "no_comparison_completed": False,
            "no_comparison_completed_exit_contribution": 0,
            "members": [
                {
                    "member": "libb",
                    "name": "libb.so",
                    "state": "unsupported",
                    "old_present": True,
                    "new_present": True,
                    "reason": "newer schema",
                },
                {
                    "member": "liba",
                    "name": "liba.so",
                    "state": "available",
                    "old_present": True,
                    "new_present": True,
                    "reason": "",
                },
            ],
            "unchecked": ["libb.so"],
            "proven_removed": [],
            "proven_added": [],
        }
        base_scope.update(scope)
        return {
            "verdict": "NO_CHANGE",
            "old_dir": "/o",
            "new_dir": "/n",
            "libraries": [
                {
                    "library": "liba.so",
                    "verdict": "NO_CHANGE",
                    "breaking": 0,
                    "source_breaks": 0,
                    "compatible_additions": 0,
                }
            ],
            "unmatched_old": ["libgone.so"],
            "unmatched_new": [],
            "comparison_scope": base_scope,
        }

    def test_warn_headline_never_claims_the_whole_scope(self) -> None:
        from abicheck.pr_comment import build_model, render_comment, should_post

        model = build_model(self._report())
        assert model.removed_libraries == []  # unmatched, not removed (D2)
        assert model.unmatched_old == ["libgone.so"]
        assert model.scope_notice is not None and "libb.so" in model.scope_notice
        assert not model.scope_blocking
        assert should_post(model, "changes")
        body = render_comment(model, report_url=None, sha="abc1234")
        assert "scope incompletely checked" in body
        assert "Unmatched libraries" in body and "libgone.so" in body

    def test_block_and_no_comparison_headlines(self) -> None:
        from abicheck.pr_comment import build_model, render_comment

        blocked = build_model(
            self._report(incomplete_scope_exit_contribution=1, policy="block")
        )
        assert blocked.scope_blocking
        assert "Comparison scope incompletely checked" in render_comment(
            blocked, report_url=None, sha="abc1234"
        )
        nothing = build_model(
            self._report(
                no_comparison_completed=True,
                no_comparison_completed_exit_contribution=1,
            )
        )
        assert nothing.no_comparison_completed
        assert "No comparison completed" in render_comment(
            nothing, report_url=None, sha="abc1234"
        )

    def test_legacy_report_without_scope_keeps_old_reading(self) -> None:
        from abicheck.pr_comment import build_model

        report = self._report()
        del report["comparison_scope"]
        model = build_model(report)
        assert model.removed_libraries == ["libgone.so"]
        assert model.scope_notice is None
