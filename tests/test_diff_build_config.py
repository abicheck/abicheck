# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Unit tests for matrix-aware build-configuration diff detectors."""
from __future__ import annotations

from abicheck.checker_policy import ChangeKind
from abicheck.diff_build_config import (
    detect_api_depends_on_consumer_env,
    detect_behavioural_default_changed,
    detect_cxx_standard_floor_raised,
    diff_matrix,
)
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.probe_harness import MatrixSnapshot, ProbeResult


def _snap_fn(name: str) -> AbiSnapshot:
    return AbiSnapshot(
        library="lib", version="0",
        functions=[Function(
            name=name, mangled=f"_Z{name}", return_type="void",
            visibility=Visibility.PUBLIC,
        )],
    )


def _matrix(cfgs: dict[str, list[str]],
            cxx_stds: dict[str, int | None] | None = None,
            defaults: dict[str, str] | None = None,
            library: str = "lib",
            version: str = "1") -> MatrixSnapshot:
    results: list[ProbeResult] = []
    for cfg_id, fn_names in cfgs.items():
        if not fn_names:
            # Empty configuration — still register a probe result so the
            # configuration is visible to the matrix detectors.
            results.append(ProbeResult(
                configuration_id=cfg_id,
                probe_id="p_empty",
                snapshot=AbiSnapshot(library=library, version=version),
            ))
            continue
        for i, fn in enumerate(fn_names):
            results.append(ProbeResult(
                configuration_id=cfg_id,
                probe_id=f"p{i}",
                snapshot=_snap_fn(fn),
            ))
    return MatrixSnapshot(
        library=library, version=version, spec_name="test",
        cxx_stds=cxx_stds or {},
        defaults=defaults or {},
        results=results,
    )


# ---------------------------------------------------------------------------
# API_DEPENDS_ON_CONSUMER_ENV
# ---------------------------------------------------------------------------


class TestApiDependsOnConsumerEnv:
    def test_diverging_decl_fires(self) -> None:
        m = _matrix({
            "tbb": ["lib::sort"],
            "omp": ["lib::sort", "lib::omp_only"],
        })
        changes = detect_api_depends_on_consumer_env(m)
        names = {c.symbol for c in changes}
        assert "lib::omp_only" in names
        assert "lib::sort" not in names  # common to both

    def test_common_only_no_finding(self) -> None:
        m = _matrix({"a": ["x"], "b": ["x"]})
        assert detect_api_depends_on_consumer_env(m) == []

    def test_single_config_no_finding(self) -> None:
        m = _matrix({"a": ["x", "y"]})
        assert detect_api_depends_on_consumer_env(m) == []

    def test_finding_describes_present_and_absent(self) -> None:
        m = _matrix({
            "a": ["lib::f"],
            "b": [],
            "c": ["lib::f"],
        })
        changes = detect_api_depends_on_consumer_env(m)
        assert len(changes) == 1
        c = changes[0]
        assert c.kind == ChangeKind.API_DEPENDS_ON_CONSUMER_ENV
        assert "a" in c.description
        assert "c" in c.description
        assert "b" in c.description


# ---------------------------------------------------------------------------
# CXX_STANDARD_FLOOR_RAISED
# ---------------------------------------------------------------------------


class TestCxxStandardFloorRaised:
    def test_floor_raised_fires(self) -> None:
        old = _matrix({"a": []}, cxx_stds={"a": 17})
        new = _matrix({"a": []}, cxx_stds={"a": 20})
        changes = detect_cxx_standard_floor_raised(old, new)
        assert len(changes) == 1
        c = changes[0]
        assert c.kind == ChangeKind.CXX_STANDARD_FLOOR_RAISED
        assert "C++17" in c.old_value
        assert "C++20" in c.new_value

    def test_min_across_configs(self) -> None:
        old = _matrix({"a": [], "b": []}, cxx_stds={"a": 17, "b": 20})
        new = _matrix({"a": [], "b": []}, cxx_stds={"a": 20, "b": 23})
        changes = detect_cxx_standard_floor_raised(old, new)
        # Old floor = 17, new floor = 20 → raised.
        assert len(changes) == 1

    def test_unchanged_no_finding(self) -> None:
        old = _matrix({"a": []}, cxx_stds={"a": 20})
        new = _matrix({"a": []}, cxx_stds={"a": 20})
        assert detect_cxx_standard_floor_raised(old, new) == []

    def test_lowered_no_finding(self) -> None:
        old = _matrix({"a": []}, cxx_stds={"a": 20})
        new = _matrix({"a": []}, cxx_stds={"a": 17})
        assert detect_cxx_standard_floor_raised(old, new) == []


# ---------------------------------------------------------------------------
# BEHAVIOURAL_DEFAULT_CHANGED
# ---------------------------------------------------------------------------


class TestBehaviouralDefaultChanged:
    def test_value_changed(self) -> None:
        old = _matrix({"a": []}, defaults={"backend": "tbb"})
        new = _matrix({"a": []}, defaults={"backend": "omp"})
        changes = detect_behavioural_default_changed(old, new)
        assert len(changes) == 1
        c = changes[0]
        assert c.kind == ChangeKind.BEHAVIOURAL_DEFAULT_CHANGED
        assert c.symbol == "backend"

    def test_value_added(self) -> None:
        old = _matrix({"a": []})
        new = _matrix({"a": []}, defaults={"new_key": "v"})
        changes = detect_behavioural_default_changed(old, new)
        assert len(changes) == 1
        assert "added" in changes[0].description.lower()

    def test_value_removed(self) -> None:
        old = _matrix({"a": []}, defaults={"k": "v"})
        new = _matrix({"a": []})
        changes = detect_behavioural_default_changed(old, new)
        assert len(changes) == 1
        assert "removed" in changes[0].description.lower()

    def test_unchanged_no_finding(self) -> None:
        old = _matrix({"a": []}, defaults={"k": "v"})
        new = _matrix({"a": []}, defaults={"k": "v"})
        assert detect_behavioural_default_changed(old, new) == []


# ---------------------------------------------------------------------------
# diff_matrix combined
# ---------------------------------------------------------------------------


class TestDiffMatrix:
    def test_combines_findings_and_dedupes(self) -> None:
        old = _matrix(
            {"tbb": ["lib::sort"], "omp": ["lib::sort", "lib::omp_only"]},
            cxx_stds={"tbb": 17, "omp": 17},
            defaults={"backend": "tbb"},
        )
        new = _matrix(
            {"tbb": ["lib::sort"], "omp": ["lib::sort", "lib::omp_only"]},
            cxx_stds={"tbb": 20, "omp": 20},
            defaults={"backend": "omp"},
        )
        changes = diff_matrix(old, new)
        kinds = {c.kind for c in changes}
        assert ChangeKind.API_DEPENDS_ON_CONSUMER_ENV in kinds
        assert ChangeKind.CXX_STANDARD_FLOOR_RAISED in kinds
        assert ChangeKind.BEHAVIOURAL_DEFAULT_CHANGED in kinds
        # dedup: only one env-depends finding for lib::omp_only even
        # though both old and new have it.
        env_findings = [c for c in changes
                        if c.kind == ChangeKind.API_DEPENDS_ON_CONSUMER_ENV]
        env_symbols = {c.symbol for c in env_findings}
        assert "lib::omp_only" in env_symbols
        assert len([c for c in env_findings
                    if c.symbol == "lib::omp_only"]) == 1
