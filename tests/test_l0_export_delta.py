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

"""ADR-049 Phase 5 §6.3: the shared L0 hard-removal extraction used by both
``cli_helpers_compare.fold_l0_hard_removals`` (direct ``compare``) and
``cli_scan_baseline._run_baseline_compare`` (``scan --against``)."""

from __future__ import annotations

import itertools
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import DiffResult
from abicheck.errors import AbicheckError
from abicheck.l0_export_delta import (
    collect_l0_export_delta,
    elf_exports_cannot_lose_symbol,
)
from abicheck.model.change import Change
from abicheck.model.elf_facts import ElfSymbol, SymbolType


def test_resolve_failure_returns_empty_tuple(monkeypatch, tmp_path):
    """A path that can no longer be resolved (moved/missing binary) is
    swallowed -- this is a best-effort enrichment, never something that
    should raise out of a real compare/scan."""

    def _raise(*_a, **_kw):
        raise AbicheckError("no such file")

    monkeypatch.setattr("abicheck.workflows.input_resolution.resolve_input", _raise)
    result = collect_l0_export_delta(tmp_path / "old.so", tmp_path / "new.so", "c++")
    assert result == ()


def test_compare_failure_returns_empty_tuple(monkeypatch, tmp_path):
    """A resolve success followed by a compare_snapshots failure is just as
    much a "probe didn't pan out" case and must not escape."""
    monkeypatch.setattr(
        "abicheck.workflows.input_resolution.resolve_input", lambda *a, **kw: object()
    )

    def _raise(*_a, **_kw):
        raise AbicheckError("incompatible snapshots")

    monkeypatch.setattr("abicheck.workflows.compare_policy.compare_snapshots", _raise)
    result = collect_l0_export_delta(tmp_path / "old.so", tmp_path / "new.so", "c++")
    assert result == ()


def test_folds_elf_only_removal(monkeypatch, tmp_path):
    """The symbols-only re-probe finds a hard ELF-only removal (case97's exact
    shape) and returns exactly that fact."""
    monkeypatch.setattr(
        "abicheck.workflows.input_resolution.resolve_input", lambda *a, **kw: object()
    )
    removal = Change(
        kind=ChangeKind.FUNC_REMOVED_ELF_ONLY,
        symbol="_ZN3lib8extendedEv",
        description="ELF-only function removed",
    )
    unrelated = Change(
        kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="other", description=""
    )
    diff = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="lib.so",
        changes=[removal, unrelated],
        verdict=Verdict.BREAKING,
    )
    monkeypatch.setattr(
        "abicheck.workflows.compare_policy.compare_snapshots", lambda *a, **kw: diff
    )
    result = collect_l0_export_delta(tmp_path / "old.so", tmp_path / "new.so", "c++")
    assert result == (removal,)


def test_ignores_non_elf_only_findings(monkeypatch, tmp_path):
    """A breaking finding that isn't func_removed_elf_only is never returned --
    this probe restores exactly one specific fact, never a general advisory
    dump."""
    monkeypatch.setattr(
        "abicheck.workflows.input_resolution.resolve_input", lambda *a, **kw: object()
    )
    diff = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="lib.so",
        changes=[Change(kind=ChangeKind.FUNC_REMOVED, symbol="other", description="")],
        verdict=Verdict.BREAKING,
    )
    monkeypatch.setattr(
        "abicheck.workflows.compare_policy.compare_snapshots", lambda *a, **kw: diff
    )
    result = collect_l0_export_delta(tmp_path / "old.so", tmp_path / "new.so", "c++")
    assert result == ()


def test_no_breaking_findings_returns_empty_tuple(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "abicheck.workflows.input_resolution.resolve_input", lambda *a, **kw: object()
    )
    diff = DiffResult(
        old_version="1.0",
        new_version="1.0",
        library="lib.so",
        changes=[],
        verdict=Verdict.COMPATIBLE,
    )
    monkeypatch.setattr(
        "abicheck.workflows.compare_policy.compare_snapshots", lambda *a, **kw: diff
    )
    result = collect_l0_export_delta(tmp_path / "old.so", tmp_path / "new.so", "c++")
    assert result == ()


# ── elf_exports_cannot_lose_symbol: the fold's fast path ─────────────────────


def _elf_snap(*symbols: ElfSymbol) -> SimpleNamespace:
    return SimpleNamespace(elf=SimpleNamespace(symbols=list(symbols)))


def test_fast_path_declines_without_captured_elf_tables():
    """No table on either side is 'unknown', never 'nothing was lost'."""
    sym = ElfSymbol(name="f")
    assert not elf_exports_cannot_lose_symbol(SimpleNamespace(elf=None), _elf_snap(sym))
    assert not elf_exports_cannot_lose_symbol(_elf_snap(sym), SimpleNamespace(elf=None))
    assert not elf_exports_cannot_lose_symbol(_elf_snap(), _elf_snap(sym))
    assert not elf_exports_cannot_lose_symbol(object(), object())


@pytest.mark.parametrize(
    "changed",
    [
        {"name": "g"},
        {"sym_type": SymbolType.OBJECT},
        {"version": "V2"},
        {"is_default": False},
        {"visibility": "hidden"},
        {"origin_lib": "libdep.so"},
    ],
)
def test_fast_path_declines_when_any_identity_field_of_an_old_symbol_changes(changed):
    old = ElfSymbol(name="f", version="V1")
    new = ElfSymbol(**{**old.__dict__, **changed})
    assert not elf_exports_cannot_lose_symbol(_elf_snap(old), _elf_snap(new))
    assert elf_exports_cannot_lose_symbol(_elf_snap(old), _elf_snap(old, new))


def test_fast_path_ignores_size_and_alignment_and_additions():
    old = ElfSymbol(name="f", size=8, value_alignment=8)
    new = ElfSymbol(name="f", size=16, value_alignment=16)
    assert elf_exports_cannot_lose_symbol(
        _elf_snap(old), _elf_snap(new, ElfSymbol(name="extra"))
    )


_FUNCS = ("alpha", "beta", "gamma")


def _build_lib(tmp_path, names: tuple[str, ...], tag: str):
    src = tmp_path / f"{tag}.c"
    src.write_text(
        "".join(f"int {n}(void) {{ return 0; }}\n" for n in names)
        + "int keep(void) { return 1; }\n"
    )
    out = tmp_path / f"lib{tag}.so"
    subprocess.run(["gcc", "-shared", "-fPIC", "-o", str(out), str(src)], check=True)
    return out


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("gcc") is None, reason="needs gcc")
@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the fast path reads ELF tables; gcc emits PE/Mach-O elsewhere",
)
def test_fast_path_never_skips_a_removal_the_real_probe_finds(tmp_path):
    """Oracle: the real symbols-only probe. Over every (old, new) pair of
    subsets of a small export set, whenever the fast path says nothing can be
    lost, the probe must find no ``func_removed_elf_only`` -- and the fast
    path must say so for every pair where NEW is a superset of OLD, or it
    saves nothing."""
    from abicheck.workflows.input_resolution import resolve_input

    subsets = [
        c for r in range(len(_FUNCS) + 1) for c in itertools.combinations(_FUNCS, r)
    ]
    libs = {s: _build_lib(tmp_path, s, "l" + "".join(x[0] for x in s)) for s in subsets}
    snaps = {
        s: resolve_input(
            p, [], [], version="", lang="c", symbols_only=True, notify=lambda _m: None
        )
        for s, p in libs.items()
    }
    skipped = 0
    for old_set, new_set in itertools.product(subsets, repeat=2):
        fast = elf_exports_cannot_lose_symbol(snaps[old_set], snaps[new_set])
        if set(old_set) <= set(new_set):
            assert fast, (old_set, new_set)
        if fast:
            skipped += 1
            assert collect_l0_export_delta(libs[old_set], libs[new_set], "c") == ()
        else:
            assert collect_l0_export_delta(libs[old_set], libs[new_set], "c"), (
                old_set,
                new_set,
            )
    assert skipped >= len(subsets)  # vacuity guard


def _build_mixed_lib(tmp_path, names: tuple[str, ...], tag: str):
    """Functions *and* data exports, so the view must classify both classes."""
    src = tmp_path / f"{tag}.c"
    src.write_text(
        "".join(f"int {n}(void) {{ return 0; }}\nint {n}_data = 1;\n" for n in names)
        + "int keep(void) { return 1; }\n"
    )
    out = tmp_path / f"lib{tag}.so"
    subprocess.run(["gcc", "-shared", "-fPIC", "-o", str(out), str(src)], check=True)
    return out


def _removed_symbols(changes) -> set[str]:
    return {c.symbol for c in changes}


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("gcc") is None, reason="needs gcc")
@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the in-memory view reads ELF tables; gcc emits PE/Mach-O elsewhere",
)
def test_in_memory_view_matches_the_rereading_probe_on_every_pair(
    tmp_path, monkeypatch
):
    """Oracle: the original path-based probe, which re-reads both binaries.

    Over every (old, new) pair of subsets of a small function+data export set,
    the view built from each already-resolved snapshot's own ELF table must
    report exactly the removals the re-reading probe reports -- and must not
    open either binary to do it."""
    from abicheck.l0_export_delta import collect_l0_export_delta_from_snapshots
    from abicheck.workflows.input_resolution import resolve_input

    subsets = [
        c for r in range(len(_FUNCS) + 1) for c in itertools.combinations(_FUNCS, r)
    ]
    libs = {
        s: _build_mixed_lib(tmp_path, s, "m" + "".join(x[0] for x in s))
        for s in subsets
    }
    snaps = {
        s: resolve_input(p, [], [], version="", lang="c", notify=lambda _m: None)
        for s, p in libs.items()
    }
    expected = {
        (o, n): _removed_symbols(collect_l0_export_delta(libs[o], libs[n], "c"))
        for o, n in itertools.product(subsets, repeat=2)
    }

    import abicheck.elf_metadata as elf_metadata
    import abicheck.extract.elf_symbol_classify as elf_symbols

    def _no_reread(*_a, **_kw):
        raise AssertionError("the in-memory view must not re-read the binary")

    monkeypatch.setattr(elf_symbols, "_pyelftools_exported_symbols", _no_reread)
    monkeypatch.setattr(elf_metadata, "parse_elf_metadata", _no_reread)
    nonempty = 0
    for (o, n), want in expected.items():
        got = _removed_symbols(
            collect_l0_export_delta_from_snapshots(snaps[o], snaps[n], "c")
        )
        assert got == want, (o, n)
        assert got == {f for f in o if f not in n}, (o, n)
        nonempty += bool(got)
    assert nonempty > 0  # vacuity guard: the sweep exercised real removals


def test_view_declines_without_a_captured_elf_table():
    """No table, no machine, or no path is 'cannot build a view' -- the caller
    then falls back to re-reading, never to 'nothing was removed'."""
    from abicheck.l0_export_delta import symbols_only_view
    from abicheck.model.elf_facts import ElfMetadata

    for snap in (
        SimpleNamespace(elf=None, source_path="/x.so"),
        SimpleNamespace(elf=ElfMetadata(), source_path="/x.so"),
        SimpleNamespace(
            elf=ElfMetadata(machine="EM_X86_64", symbols=[]), source_path="/x.so"
        ),
        SimpleNamespace(
            elf=ElfMetadata(machine="EM_X86_64", symbols=[ElfSymbol(name="f")]),
            source_path=None,
        ),
    ):
        assert symbols_only_view(snap, "c") is None
