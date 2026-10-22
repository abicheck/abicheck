# SPDX-License-Identifier: Apache-2.0
"""The finished tier of the whole-snapshot cache (``dump.cache.finished``).

Invariant: for any operand configuration, ``resolve_input`` served from a
warm finished entry returns a snapshot byte-identical (serialized) to the
uncached recompute -- raw extraction followed by ownership, the achieved
``--exclude-header`` stamp and the dependency re-scope -- and on that hit
none of those passes run.

The oracle is an independent run in reference mode (every disk-cache lookup
misses) with the finished tier switched off, i.e. the pre-tier code path.
The configurations share one cache root and differ in exactly one finishing
input each, so a finished key that ignored an input would serve one
configuration another's entry and fail the comparison.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from abicheck.model.execution_cache import REFERENCE_MODE_ENV_VAR
from abicheck.model.ownership_rules import OwnershipRequest
from abicheck.serialization import snapshot_to_dict
from abicheck.workflows import input_resolution
from abicheck.workflows.dump import dependency_scope
from abicheck.workflows.dump.cache import api as cache_api, store as cache_store

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="builds an ELF .so with gcc"),
]


def _build(tmp: Path) -> tuple[Path, Path, Path]:
    inc = tmp / "inc"
    sub = inc / "detail"
    sub.mkdir(parents=True)
    (inc / "priv.h").write_text("#pragma once\nstruct Priv { int a; long b; };\n")
    (inc / "extra.h").write_text(
        "#pragma once\nstruct Extra { char c; };\nint extra_fn(int);\n"
    )
    (sub / "inner.h").write_text("#pragma once\nstruct Inner { double d; };\n")
    (inc / "api.h").write_text(
        "#pragma once\n#include <time.h>\n#include <stdio.h>\n"
        '#include "priv.h"\n#include "extra.h"\n#include "detail/inner.h"\n'
        "void api_fn(struct Priv *p, struct tm *t, struct Extra *e,"
        " struct Inner *i);\n"
        "FILE *api_file(void);\n"
    )
    src = tmp / "api.c"
    src.write_text(
        '#include "api.h"\n'
        "void api_fn(struct Priv *p, struct tm *t, struct Extra *e,"
        " struct Inner *i) { (void)p; (void)t; (void)e; (void)i; }\n"
        "FILE *api_file(void) { return 0; }\n"
        "int extra_fn(int x) { return x; }\n"
    )
    so = tmp / "libapi.so"
    res = subprocess.run(
        ["gcc", "-g", "-I", str(inc), "-shared", "-fPIC", "-o", str(so), str(src)],
        capture_output=True,
    )
    if res.returncode != 0:
        pytest.skip(f"gcc failed: {res.stderr.decode()[:200]}")
    return so, inc, sub


def _configs(inc: Path, sub: Path) -> dict[str, dict[str, Any]]:
    """Each differs from ``base`` in one input the finishing passes read."""
    return {
        "base": {"headers": [inc]},
        "excl_priv": {"headers": [inc], "exclude_headers": ["priv.h"]},
        "excl_extra": {"headers": [inc], "exclude_headers": ["extra.h"]},
        "excl_both": {"headers": [inc], "exclude_headers": ["priv.h", "extra.h"]},
        "excl_nomatch": {"headers": [inc], "exclude_headers": ["nothing.h"]},
        "pub_dir": {"headers": [inc], "public_header_dirs": [sub]},
        "excl_pub": {
            "headers": [inc],
            "exclude_headers": ["inner.h"],
            "public_header_dirs": [sub],
        },
        "with_deps": {"headers": [inc], "include_dependencies": True},
        # Same raw key as `file_only` (the -H list filters to the same file):
        # only the finishing passes tell these apart.
        "file_only": {"headers": [inc / "api.h"]},
        "file_excl_priv": {"headers": [inc / "api.h"], "exclude_headers": ["priv.h"]},
        "file_excl_inner": {
            "headers": [inc / "api.h"],
            "exclude_headers": ["inner.h"],
        },
        "file_owned": {
            "headers": [inc / "api.h"],
            "ownership": OwnershipRequest(project_root=str(sub)),
        },
    }


def _resolve(so: Path, cfg: dict[str, Any]) -> Any:
    kw = dict(cfg)
    headers = kw.pop("headers")
    return input_resolution.resolve_input(
        so, headers, [], "1.0", "c", is_elf=True, **kw
    )


def _canon(snap: Any) -> str:
    return json.dumps(snapshot_to_dict(snap), sort_keys=True, default=str)


def _runtime(snap: Any) -> tuple[Any, ...]:
    """Fields a serialized entry does not carry, re-stamped on a hit."""
    return (snap.live_source_evidence, snap.from_headers, snap.dependency_scope)


@pytest.fixture
def built(tmp_path: Path) -> tuple[Path, Path, Path]:
    return _build(tmp_path)


def test_finished_hit_matches_uncached_recompute_across_configs(
    built: tuple[Path, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    so, inc, sub = built
    configs = _configs(inc, sub)

    # Oracle: reference mode (no disk lookups) and no finished tier at all.
    ref: dict[str, tuple[str, tuple[Any, ...]]] = {}
    with monkeypatch.context() as mp:
        mp.setattr(cache_store, "_CACHE_DIR", tmp_path / "ref-cache")
        mp.setenv(REFERENCE_MODE_ENV_VAR, "1")
        mp.setattr(cache_api, "take_pending", lambda: None)
        for name, cfg in configs.items():
            snap = _resolve(so, cfg)
            ref[name] = (_canon(snap), _runtime(snap))
    # The configurations really do finish differently, so a key ignoring the
    # input that separates them could not go unnoticed.
    assert len({c for c, _ in ref.values()}) >= 5

    monkeypatch.setattr(cache_store, "_CACHE_DIR", tmp_path / "shared-cache")
    for name, cfg in configs.items():  # cold: populates both tiers
        snap = _resolve(so, cfg)
        assert (_canon(snap), _runtime(snap)) == ref[name], f"cold {name}"
    finished_dir = tmp_path / "shared-cache" / "finished"
    assert len(list(finished_dir.glob("*.json.zst"))) == len(configs)

    # Warm, in reverse order: every finishing pass and the dump itself are
    # forbidden, so each result must come from its own finished entry.
    def _forbidden(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("finishing pass ran on a finished-tier hit")

    with monkeypatch.context() as mp:
        mp.setattr(
            dependency_scope, "scope_snapshot_excluding_dependencies", _forbidden
        )
        mp.setattr(input_resolution, "finish_snapshot", _forbidden)
        mp.setattr(input_resolution, "record_achieved_header_exclusions", _forbidden)
        mp.setattr(input_resolution, "run_dump", _forbidden)
        for name, cfg in reversed(list(configs.items())):
            snap = _resolve(so, cfg)
            assert (_canon(snap), _runtime(snap)) == ref[name], f"warm {name}"


def test_raw_hit_without_finished_entry_finishes_and_backfills(
    built: tuple[Path, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cache warmed before the finished tier existed (raw entry only) still
    returns the recompute, and stores the finished entry for the next run."""
    so, inc, sub = built
    cfg = _configs(inc, sub)["excl_priv"]
    monkeypatch.setattr(cache_store, "_CACHE_DIR", tmp_path / "c")
    with monkeypatch.context() as mp:
        mp.setattr(cache_api, "take_pending", lambda: None)
        expected = _canon(_resolve(so, cfg))  # raw entry only
    assert not (tmp_path / "c" / "finished").exists()
    calls: list[int] = []
    real = dependency_scope.scope_snapshot_excluding_dependencies

    def _counting(*a: Any, **k: Any) -> Any:
        calls.append(1)
        return real(*a, **k)

    with monkeypatch.context() as mp:
        mp.setattr(dependency_scope, "scope_snapshot_excluding_dependencies", _counting)
        mp.setattr(input_resolution, "run_dump", lambda *a, **k: 1 / 0)
        assert _canon(_resolve(so, cfg)) == expected
        assert calls, "raw hit must still finish"
        calls.clear()
        assert _canon(_resolve(so, cfg)) == expected
        assert not calls, "second run must be a finished-tier hit"
