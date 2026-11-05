"""The header-order witness's second dump runs only when a comparison reads it.

A live ``compare`` resolves both sides with the witness deferred, then asks
``order_proof_needed``: only when the two recorded ``header_sequence`` values
differ without a proof on both sides does it resolve again with the witness on.
A declared order that is already canonical still proves itself for free, and a
standalone ``dump`` (whose snapshot may be stored as a baseline) is never
deferred.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from abicheck.extract import header_order_witness as witness
from abicheck.workflows.compare import resolve as resolve_mod
from abicheck.workflows.compare.order_proof import (
    order_proof_needed,
    should_resolve_with_order_witness,
)


def _contract(seq: list[str] | None, proof: list[str] | None) -> SimpleNamespace:
    fields = {} if seq is None else {"header_sequence": json.dumps(seq)}
    return SimpleNamespace(profile_fields=fields, header_canonical=proof)


def _snap(seq, proof=None, *, contract=True) -> SimpleNamespace:
    return SimpleNamespace(contract=_contract(seq, proof) if contract else None)


_AB, _BA = ["a.h", "b.h"], ["b.h", "a.h"]


@pytest.mark.parametrize(
    ("old", "new", "needed"),
    [
        (_snap(_BA), _snap(_BA), False),  # same declared order: nothing to prove
        (_snap(_AB, _AB), _snap(_BA), True),  # a permutation, one side unproven
        (_snap(_BA), _snap(_AB, _AB), True),
        (_snap(_BA, _AB), _snap(_AB, _AB), False),  # both already proven
        (_snap(_AB), _snap(["a.h", "b.h", "c.h"]), True),  # an insertion
        (_snap(None), _snap(None), False),  # no headers on either side
        (_snap(_BA, contract=False), _snap(_AB), False),  # no contract: today's rules
    ],
)
def test_the_proof_is_needed_only_where_the_comparison_reads_it(old, new, needed):
    assert order_proof_needed(old, new) is needed


def _dump_counting(calls: list[list[str]]):
    def dump(so_path, headers, extra=None, *, dump_manifest=None):
        calls.append([h.name for h in headers])
        return SimpleNamespace(
            from_headers=True,
            declarations=SimpleNamespace(
                functions=[],
                variables=[],
                types=[],
                enums=[],
                typedefs={},
                typedefs_qualified={},
                constants={},
            ),
            contract=_contract([h.name for h in headers], None),
        )

    return witness.header_order_proof(dump)


def _hdrs(tmp_path: Path, *names: str) -> list[Path]:
    for n in names:
        (tmp_path / n).write_text("// x\n")
    return [tmp_path / n for n in names]


def test_a_deferred_unsorted_order_is_dumped_once_and_left_unproven(tmp_path):
    calls: list[list[str]] = []
    with witness.deferred_order_witness():
        snap = _dump_counting(calls)(Path("lib.so"), _hdrs(tmp_path, "b.h", "a.h"))
    assert calls == [["b.h", "a.h"]]
    assert snap.contract.header_canonical is None


def test_a_deferred_sorted_order_still_proves_itself(tmp_path):
    calls: list[list[str]] = []
    with witness.deferred_order_witness():
        snap = _dump_counting(calls)(Path("lib.so"), _hdrs(tmp_path, "a.h", "b.h"))
    assert calls == [["a.h", "b.h"]]
    assert snap.contract.header_canonical == ["a.h", "b.h"]


def test_outside_the_scope_the_witness_runs_as_before(tmp_path):
    """The negative control: a standalone dump is never deferred."""
    calls: list[list[str]] = []
    assert witness.order_witness_deferred() is False
    snap = _dump_counting(calls)(Path("lib.so"), _hdrs(tmp_path, "b.h", "a.h"))
    assert calls == [["b.h", "a.h"], ["a.h", "b.h"]]
    assert snap.contract.header_canonical == ["a.h", "b.h"]


@pytest.mark.parametrize(
    ("pairs", "expected_rounds"),
    [
        ([(_snap(_BA), _snap(_BA))], [True]),  # one deferred round
        (
            [(_snap(_AB, _AB), _snap(_BA)), (_snap(_AB, _AB), _snap(_BA, _AB))],
            [True, False],
        ),
    ],
)
def test_compare_resolves_again_with_the_witness_only_when_needed(
    monkeypatch: pytest.MonkeyPatch, pairs, expected_rounds
):
    rounds: list[bool] = []
    results = iter(
        SimpleNamespace(old=o, new=n, old_fmt="elf", new_fmt="elf") for o, n in pairs
    )

    def once(request, *, notify=None, allow_parallel=True):
        rounds.append(witness.order_witness_deferred())
        return next(results)

    monkeypatch.setattr(resolve_mod, "_resolve_compare_request_once", once)
    pair = resolve_mod.resolve_compare_request(object())
    assert rounds == expected_rounds
    assert pair.new is pairs[-1][1]


def test_a_deferred_dump_never_shares_a_cache_entry_with_a_full_one(tmp_path):
    from _dump_cache_request import dump_request

    from abicheck.workflows.dump.cache import api

    so = tmp_path / "lib.so"
    so.write_bytes(b"\x7fELF" + b"\0" * 60)
    hdrs = _hdrs(tmp_path, "b.h", "a.h")
    req = dump_request(path=so, headers=hdrs, includes=[])
    full = api._cache_key_for(req)
    with witness.deferred_order_witness():
        deferred = api._cache_key_for(req)
    assert full and deferred and full != deferred


@pytest.mark.integration
def test_a_compare_with_one_unsorted_order_parses_each_side_once(
    tmp_path: Path,
) -> None:
    """End to end: ``-H part1.h -H part2.h -H part10.h`` on both sides is not
    canonical (``part10.h`` sorts before ``part2.h``), but both sides declare
    the same order, so no proof is read and each side parses once."""
    import shutil
    import subprocess
    import sys

    from tests._abicheck_subprocess import abicheck_command, abicheck_env

    if sys.platform != "linux" or not (shutil.which("g++") and shutil.which("clang++")):
        pytest.skip("needs g++ emitting ELF and clang++")
    names = ("part1.h", "part2.h", "part10.h")
    args: list[str] = []
    for side, extra in (("old", ""), ("new", " int added;")):
        inc = tmp_path / side / "include"
        inc.mkdir(parents=True)
        for i, name in enumerate(names):
            body = (
                f"struct S{i} {{ int a;{extra if i == 0 else ''} }}; int f{i}(S{i}*);"
            )
            (inc / name).write_text(f"#pragma once\n{body}\n")
        src = tmp_path / side / "a.cpp"
        src.write_text(
            "".join(f'#include "{n}"\n' for n in names)
            + "".join(f"int f{i}(S{i}*s){{return s->a;}}\n" for i in range(3))
        )
        so = tmp_path / side / "lib.so"
        subprocess.run(
            ["g++", "-shared", "-fPIC", f"-I{inc}", "-o", str(so), str(src)], check=True
        )
        args += [a for n in names for a in ("-H", f"{side}={inc / n}")]
    cache, report = tmp_path / "cache", tmp_path / "report.json"
    env = abicheck_env(
        {
            "XDG_CACHE_HOME": str(cache),
            "LOCALAPPDATA": str(cache),
            "ABICHECK_AST_FRONTEND": "clang",
        }
    )
    proc = subprocess.run(
        abicheck_command(
            "compare",
            str(tmp_path / "old/lib.so"),
            str(tmp_path / "new/lib.so"),
            *args,
            "--depth",
            "headers",
            "-o",
            f"json={report}",
        ),
        env=env,
        capture_output=True,
        text=True,
    )
    assert report.exists(), proc.stderr
    assert "added" in report.read_text()
    entries = [
        p
        for p in cache.rglob("clang/*.json")
        if not p.name.endswith(".projection.json")
    ]
    assert len(entries) == 2, sorted(p.name for p in entries)


@pytest.mark.parametrize(
    ("old_fmt", "new_fmt", "retry"),
    [
        ("elf", "elf", True),  # both live: re-dumping can prove the old side
        (None, "elf", False),  # the old side is a stored snapshot without a proof
        ("elf", None, True),  # the stored new side already carries its proof
    ],
)
def test_no_retry_when_the_unproven_side_is_a_stored_snapshot(old_fmt, new_fmt, retry):
    """A stored snapshot cannot gain a proof, and the reorder carve-out needs
    both sides proven, so re-resolving the live side would buy nothing."""
    pair = SimpleNamespace(
        old=_snap(_BA), new=_snap(_AB, _AB), old_fmt=old_fmt, new_fmt=new_fmt
    )
    assert should_resolve_with_order_witness(pair) is retry


@pytest.mark.parametrize("names", [(), ("a.h",)])
def test_a_dump_that_cannot_run_the_witness_keeps_the_standalone_key(tmp_path, names):
    from _dump_cache_request import dump_request

    from abicheck.workflows.dump.cache import api

    so = tmp_path / "lib.so"
    so.write_bytes(b"\x7fELF" + b"\0" * 60)
    req = dump_request(path=so, headers=_hdrs(tmp_path, *names) or None, includes=[])
    full = api._cache_key_for(req)
    with witness.deferred_order_witness():
        deferred = api._cache_key_for(req)
    assert full and full == deferred
