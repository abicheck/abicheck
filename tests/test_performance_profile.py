# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""``performance.profile``: one memory/speed setting, reachable from the
project config, the ``compare`` CLI and the typed API, that changes how a
run executes and never what it reports.

Oracle for precedence: CLI flag > request field > ``.abicheck.yml`` >
built-in ``balanced``, observed by which execution path actually ran (the
side-isolation fork spy), not by reading the setting back.
"""

from __future__ import annotations

import json
import sys

import pytest
from click.testing import CliRunner

from abicheck.buildsource.build_config import BuildConfig
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.performance import (
    DEFAULT_PERFORMANCE_PROFILE,
    ExecutionTuning,
    PerformanceProfile,
    current_performance_profile,
    parse_performance_profile,
    performance_profile_scope,
    tuning_for,
)
from abicheck.serialization import save_snapshot

linux_only = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="isolation forks; Linux only"
)


# ── the model ────────────────────────────────────────────────────────────────


def test_every_profile_has_a_complete_tuning():
    for profile in PerformanceProfile:
        assert isinstance(tuning_for(profile), ExecutionTuning)


def test_default_is_balanced_and_changes_nothing():
    assert DEFAULT_PERFORMANCE_PROFILE is PerformanceProfile.BALANCED
    t = tuning_for(PerformanceProfile.BALANCED)
    assert (t.sequential_sides, t.isolate_sides, t.sequential_members) == (
        False,
        False,
        False,
    )


def test_low_memory_never_runs_two_extractions_at_once():
    t = tuning_for(PerformanceProfile.LOW_MEMORY)
    assert t.sequential_sides and t.isolate_sides and t.sequential_members


@pytest.mark.parametrize(
    "spelling",
    ["low-memory", "LOW-MEMORY", " low-memory ", PerformanceProfile.LOW_MEMORY],
)
def test_parse_accepts_canonical_spellings(spelling):
    assert parse_performance_profile(spelling) is PerformanceProfile.LOW_MEMORY


@pytest.mark.parametrize("bad", ["", "low_memory", "fast", "process"])
def test_parse_rejects_unknown_values_naming_the_valid_ones(bad):
    with pytest.raises(ValueError, match="balanced, low-memory"):
        parse_performance_profile(bad)


def test_scope_nests_and_restores():
    assert current_performance_profile() is PerformanceProfile.BALANCED
    with performance_profile_scope(PerformanceProfile.LOW_MEMORY):
        assert current_performance_profile() is PerformanceProfile.LOW_MEMORY
        with performance_profile_scope(None):  # "nothing to say" keeps it
            assert current_performance_profile() is PerformanceProfile.LOW_MEMORY
        with performance_profile_scope(PerformanceProfile.BALANCED):
            assert current_performance_profile() is PerformanceProfile.BALANCED
        assert current_performance_profile() is PerformanceProfile.LOW_MEMORY
    assert current_performance_profile() is PerformanceProfile.BALANCED


# ── .abicheck.yml ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["balanced", "low-memory", "Low-Memory"])
def test_config_block_parses_and_round_trips(value):
    cfg = BuildConfig.from_dict({"performance": {"profile": value}})
    assert cfg.performance_profile == value.lower()
    again = BuildConfig.from_dict(cfg.to_dict())
    assert again.performance_profile == cfg.performance_profile


def test_config_without_the_block_leaves_it_unset():
    cfg = BuildConfig.from_dict({})
    assert cfg.performance_profile is None
    assert "performance" not in cfg.to_dict()


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"performance": {"profile": "fast"}}, "performance.profile"),
        ({"performance": {"profile": 3}}, "must be a string"),
        ({"performance": {"workers": 2}}, "unknown .abicheck.yml key"),
        ({"performance": "low-memory"}, "must be a mapping"),
    ],
)
def test_config_rejects_bad_blocks(raw, message):
    with pytest.raises(ValueError, match=message):
        BuildConfig.from_dict(raw)


# ── execution: the typed API and the CLI ────────────────────────────────────


def _snap(path, version, fns):
    snap = AbiSnapshot(library="libx.so", version=version)
    snap.functions = [
        Function(name=n, mangled=n, return_type="int", visibility=Visibility.PUBLIC)
        for n in fns
    ]
    save_snapshot(snap, path)
    return path


@pytest.fixture
def pair(tmp_path):
    return (
        _snap(tmp_path / "old.json", "1", ["f", "g"]),
        _snap(tmp_path / "new.json", "2", ["f"]),
    )


@pytest.fixture
def forks(monkeypatch):
    """Records each fork batch (its size) that side isolation performed."""
    import abicheck.workflows.side_isolation as iso

    seen: list[int] = []
    real = iso._run_children

    def spy(ctx, fns):
        seen.append(len(fns))
        return real(ctx, fns)

    monkeypatch.setattr(iso, "_run_children", spy)
    return seen


def _kinds(changes):
    return sorted((c.kind.value, c.symbol) for c in changes)


@linux_only
def test_typed_api_field_selects_isolation_with_identical_result(pair, forks):
    from abicheck.service import CompareRequest, InputSpec, run_compare_request

    old, new = pair
    base = CompareRequest(old=InputSpec(path=old), new=InputSpec(path=new))
    plain = run_compare_request(base)
    assert forks == []
    low = run_compare_request(
        CompareRequest(
            old=InputSpec(path=old),
            new=InputSpec(path=new),
            performance_profile=PerformanceProfile.LOW_MEMORY,
        )
    )
    assert forks == [1, 1]  # one side per child, one after the other
    assert low.diff.verdict == plain.diff.verdict
    assert _kinds(low.diff.changes) == _kinds(plain.diff.changes)
    assert ("func_removed", "g") in _kinds(plain.diff.changes)


@linux_only
def test_typed_api_field_overrides_the_ambient_profile(pair, forks):
    from abicheck.service import CompareRequest, InputSpec, run_compare_request

    old, new = pair
    with performance_profile_scope(PerformanceProfile.LOW_MEMORY):
        run_compare_request(
            CompareRequest(
                old=InputSpec(path=old),
                new=InputSpec(path=new),
                performance_profile="balanced",
            )
        )
        assert forks == []
        run_compare_request(
            CompareRequest(old=InputSpec(path=old), new=InputSpec(path=new))
        )
    assert forks == [1, 1]  # field unset: the ambient profile applied


def test_typed_api_rejects_an_unknown_profile(pair):
    from abicheck.errors import ValidationError
    from abicheck.service import CompareRequest, InputSpec, run_compare_request

    old, new = pair
    request = CompareRequest(
        old=InputSpec(path=old), new=InputSpec(path=new), performance_profile="fast"
    )
    assert any("performance profile" in e for e in request.validation_errors())
    with pytest.raises(ValidationError):
        run_compare_request(request)


def _cli(tmp_path, monkeypatch, pair, *extra, config=None):
    from abicheck.cli import main

    monkeypatch.chdir(tmp_path)
    if config is not None:
        (tmp_path / ".abicheck.yml").write_text(config)
    out = tmp_path / "r.json"
    result = CliRunner().invoke(
        main,
        ["compare", str(pair[0]), str(pair[1]), "-o", f"json={out}", *extra],
    )
    report = json.loads(out.read_text()) if out.exists() else None
    return result, report


@linux_only
@pytest.mark.parametrize(
    ("config", "flag", "expect_forks"),
    [
        (None, (), []),
        (None, ("--performance-profile", "low-memory"), [1, 1]),
        ("performance:\n  profile: low-memory\n", (), [1, 1]),
        (
            "performance:\n  profile: low-memory\n",
            ("--performance-profile", "balanced"),
            [],
        ),
        (
            "performance:\n  profile: balanced\n",
            ("--performance-profile", "low-memory"),
            [1, 1],
        ),
    ],
)
def test_cli_precedence_flag_then_config_then_default(
    tmp_path, monkeypatch, pair, forks, config, flag, expect_forks
):
    run_dir, base_dir = tmp_path / "run", tmp_path / "base"
    run_dir.mkdir()
    base_dir.mkdir()
    result, report = _cli(run_dir, monkeypatch, pair, *flag, config=config)
    assert forks == expect_forks
    baseline, base_report = _cli(base_dir, monkeypatch, pair)  # no config, no flag
    assert result.exit_code == baseline.exit_code
    assert sorted(c["kind"] for c in report["changes"]) == sorted(
        c["kind"] for c in base_report["changes"]
    )


def test_cli_rejects_an_unknown_flag_value(tmp_path, monkeypatch, pair):
    result, _ = _cli(tmp_path, monkeypatch, pair, "--performance-profile", "fast")
    assert result.exit_code != 0
    assert "low-memory" in result.output


def test_cli_invalid_config_value_is_a_usage_error(tmp_path, monkeypatch, pair):
    result, _ = _cli(
        tmp_path, monkeypatch, pair, config="performance:\n  profile: fast\n"
    )
    assert result.exit_code == 64
    assert "performance.profile" in result.output


def test_cli_scope_does_not_leak_past_the_command(tmp_path, monkeypatch, pair):
    _cli(tmp_path, monkeypatch, pair, "--performance-profile", "low-memory")
    assert current_performance_profile() is PerformanceProfile.BALANCED


# ── release fan-out ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("jobs", [0, -1])
def test_low_memory_compares_release_members_one_at_a_time(jobs):
    from abicheck.workflows.release_jobs import plan_release_workers

    with performance_profile_scope(PerformanceProfile.LOW_MEMORY):
        plan = plan_release_workers(jobs, depth="headers", header_roots=True)
    assert plan.initial_jobs == 1 and plan.pool_size == 1


def test_an_explicit_job_count_is_still_honored_under_low_memory():
    from abicheck.workflows.release_jobs import plan_release_workers

    with performance_profile_scope(PerformanceProfile.LOW_MEMORY):
        plan = plan_release_workers(3, depth="headers", header_roots=True)
    assert plan.initial_jobs == 3
