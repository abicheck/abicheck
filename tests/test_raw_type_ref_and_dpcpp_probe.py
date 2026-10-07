# SPDX-License-Identifier: Apache-2.0
"""Unit cases for the raw ``DW_AT_type`` reader, the calling-convention
fallbacks it feeds, and the ``icpx`` host-pass probe.

The compiled-binary matrix in ``test_dwarf_subtree_index.py`` checks the raw
reader against pyelftools on real units; these pin the branches a real GCC
or clang unit never produces (``DW_FORM_ref_udata``, ``DW_FORM_ref_addr`` at
DWARF 2, big-endian, an undecodable form, a variable-length prefix) against
a hand-encoded oracle.
"""

from __future__ import annotations

import subprocess
import uuid
from types import SimpleNamespace

import pytest
from _strict_process import StrictProcessRunner, proc

from abicheck import dumper_clang
from abicheck.buildsource import dpcpp_jobs as dumper_dpcpp_jobs
from abicheck.extract import dwarf_subtree_index as dsi


def _uleb(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


class _Table:
    def __init__(self, specs: dict[int, list[tuple[str, str]]]) -> None:
        self._specs = specs

    def get_abbrev(self, code: int) -> dict[str, list[SimpleNamespace]]:
        return {
            "attr_spec": [SimpleNamespace(name=n, form=f) for n, f in self._specs[code]]
        }


def _index(
    data: bytes,
    specs: dict[int, list[tuple[str, str]]],
    *,
    version: int = 5,
    little_endian: bool = True,
    cu_offset: int = 0x100,
) -> dsi._CuIndex:
    return dsi._CuIndex(
        0,
        data,
        {},
        {},
        8,
        4,
        version,
        abbrev_table=_Table(specs),
        cu_offset=cu_offset,
        little_endian=little_endian,
    )


@pytest.mark.parametrize(
    ("form", "payload", "kwargs", "expected"),
    [
        ("DW_FORM_ref1", bytes([0x10]), {}, 0x110),
        ("DW_FORM_ref2", (0x1234).to_bytes(2, "little"), {}, 0x1334),
        ("DW_FORM_ref4", (0x10).to_bytes(4, "big"), {"little_endian": False}, 0x110),
        ("DW_FORM_ref8", (0x20).to_bytes(8, "little"), {}, 0x120),
        ("DW_FORM_ref_udata", _uleb(300), {}, 300 + 0x100),
        # Section-absolute: no unit offset; 4 bytes (offset size) at DWARF 3+ ...
        ("DW_FORM_ref_addr", (0x5000).to_bytes(4, "little"), {}, 0x5000),
        # ... and address-sized (8) at DWARF 2.
        ("DW_FORM_ref_addr", (0x6000).to_bytes(8, "little"), {"version": 2}, 0x6000),
    ],
)
def test_reference_forms_decode_like_resolve_die_ref(form, payload, kwargs, expected):
    # code 1: a name string and a uleb before the type, so both a variable-
    # length and a fixed step are skipped on the way.
    specs = {
        1: [
            ("DW_AT_name", "DW_FORM_string"),
            ("DW_AT_decl_line", "DW_FORM_udata"),
            ("DW_AT_type", form),
        ]
    }
    data = _uleb(1) + b"p\0" + _uleb(1000) + payload
    assert _index(data, specs, **kwargs).type_ref(0) == expected


def test_no_type_attribute_and_null_entry_read_as_none():
    idx = _index(_uleb(1) + b"x\0" + b"\0", {1: [("DW_AT_name", "DW_FORM_string")]})
    assert idx.type_ref(0) is None
    assert idx.type_ref(3) is None  # the null entry


def test_a_non_reference_form_is_left_to_the_ordinary_decode():
    idx = _index(_uleb(1) + bytes(8), {1: [("DW_AT_type", "DW_FORM_ref_sig8")]})
    assert idx.type_ref(0) is dsi.NOT_DECODABLE
    assert idx.type_ref(0) is dsi.NOT_DECODABLE  # cached plan, same answer


def test_parameters_of_an_unindexable_die_come_back_decoded():
    child = SimpleNamespace(tag="DW_TAG_formal_parameter")
    other = SimpleNamespace(tag="DW_TAG_variable")
    die = SimpleNamespace(iter_children=lambda: iter([child, other]))
    assert list(dsi.iter_formal_parameter_type_refs(die)) == [(None, child)]


# ── calling-convention fallbacks ───────────────────────────────────────────


class _BrokenCU:
    cu_offset = 0

    def get_DIE_from_refaddr(self, _offset: int):
        raise ValueError("dangling reference")


def test_param_trait_for_ref_tolerates_missing_and_dangling_types():
    from abicheck import dwarf_advanced as da

    cache = da._DwarfTypeCache()
    assert da._param_trait_for_ref(None, _BrokenCU(), cache) is None
    assert da._param_trait_for_ref(0x40, _BrokenCU(), cache) is None
    assert cache.param_trait == {0x40: None}  # memoized, not re-probed
    assert da._param_trait_for_ref(0x40, _BrokenCU(), None) is None


def test_traits_without_a_cache_or_a_type_fall_back_to_the_plain_path():
    from abicheck import dwarf_advanced as da

    untyped = SimpleNamespace(attributes={})
    assert da._param_trait(untyped, _BrokenCU(), None) is None
    assert da._return_facts(untyped, _BrokenCU(), None) == (None, None, False)
    cache = da._DwarfTypeCache()
    assert da._return_facts(untyped, _BrokenCU(), cache) == (None, None, False)
    assert cache.ret_facts == {}  # no reference: nothing to key on


def test_type_ref_offset_reads_forms_without_decoding_the_target():
    from abicheck import dwarf_advanced as da

    cu = SimpleNamespace(cu_offset=0x10)

    def die(form, value):
        return SimpleNamespace(
            attributes={"DW_AT_type": SimpleNamespace(form=form, value=value)}
        )

    assert da._type_ref_offset(die("DW_FORM_ref4", 5), cu) == 0x15
    assert da._type_ref_offset(die("DW_FORM_ref_addr", 5), cu) == 5
    assert da._type_ref_offset(die("DW_FORM_ref_sig8", b"\0" * 8), cu) is None
    assert da._type_ref_offset(SimpleNamespace(attributes={}), cu) is None


# ── icpx host-pass probe ───────────────────────────────────────────────────


#: What the probe asks the driver, after the caller's own flags.
_PROBE_TAIL = [
    "-fsycl",
    "-fsycl-host-only",
    "-fsyntax-only",
    "-x",
    "c++",
    "-",
    "-###",
]

_DEVICE_JOB = (
    ' "/opt/clang" "-cc1" "-triple" "spir64" "-fsycl-is-device"'
    ' "-fsycl-int-header=/tmp/icpx-1/agg-header.h"'
    ' "-fsycl-int-footer=/tmp/icpx-1/agg-footer.h" "-ast-dump=json" "agg.hpp"'
)
_HOST_JOB = (
    ' "/opt/clang" "-cc1" "-triple" "x86_64" "-fsycl-is-host"'
    ' "-include-internal-header" "/tmp/icpx-1/agg-header.h"'
    ' "-include-internal-footer" "/tmp/icpx-1/agg-footer.h" "-ast-dump=json" "agg.hpp"'
)


@pytest.fixture
def probe(monkeypatch):
    """Run the probe against a scripted driver; returns the argv it saw."""
    import abicheck.deadline as deadline

    seen: list[list[str]] = []
    # A process-unique identity per call, so the per-executable memo never
    # answers -- a per-test counter would repeat across tests and serve one
    # test the previous test's probe result.
    monkeypatch.setattr(
        dumper_dpcpp_jobs, "executable_revision", lambda b: f"id-{uuid.uuid4()}"
    )

    def install(stderr: str | None = None, exc: BaseException | None = None):
        def fake(cmd, **_kw):
            seen.append(cmd)
            if exc is not None:
                raise exc
            return subprocess.CompletedProcess(cmd, 0, "", stderr)

        monkeypatch.setattr(deadline, "run_bounded", fake)

    return install, seen


def test_probe_accepts_a_replayable_device_plus_host_plan(probe):
    install, seen = probe
    install(stderr="clang version x\n" + _DEVICE_JOB + "\n" + _HOST_JOB + "\n")
    assert dumper_clang._driver_host_only_is_single_pass("icpx", ("-O0",)) is True
    assert seen == [["icpx", "-O0", *_PROBE_TAIL]]


def test_probe_rejects_a_device_plan_it_cannot_replay(probe):
    install, _ = probe
    second_device = _DEVICE_JOB.replace("spir64", "spir64_gen")
    install(stderr="\n".join([_DEVICE_JOB, second_device, _HOST_JOB]))
    assert dumper_clang._driver_host_only_is_single_pass("icpx", ()) is False


def test_probe_reports_a_single_host_pass(probe):
    install, seen = probe
    install(stderr=_HOST_JOB)
    assert dumper_clang._driver_host_only_is_single_pass("icpx", ()) is True
    assert seen == [["icpx", *_PROBE_TAIL]]


@pytest.mark.parametrize(
    "exc", [OSError("no such file"), subprocess.TimeoutExpired("icpx", 30)]
)
def test_probe_that_cannot_run_keeps_the_host_only_request(probe, exc):
    install, seen = probe
    install(exc=exc)
    assert dumper_clang._driver_host_only_is_single_pass("icpx", ()) is True
    assert seen == [["icpx", *_PROBE_TAIL]]


def test_replay_strips_the_device_dump_and_relocates_the_integration_files(
    probe, tmp_path
):
    install, _ = probe
    install(stderr=_DEVICE_JOB + "\n" + _HOST_JOB)
    cmd = ["icpx", "-fsycl", "-fsycl-host-only", "-Xclang", "-ast-dump=json", "a"]
    device, host = dumper_dpcpp_jobs.sycl_host_replay_jobs(cmd, tmp_path)
    assert "-ast-dump=json" not in device
    assert f"-fsycl-int-header={tmp_path}/agg-header.h" in device
    assert "-ast-dump=json" in host
    assert f"{tmp_path}/agg-footer.h" in host
    assert not any("/tmp/icpx-1" in t for t in [*device, *host])


def test_no_replay_for_a_request_that_is_not_host_only(tmp_path):
    assert (
        dumper_dpcpp_jobs.sycl_host_replay_jobs(["clang", "-x", "c++"], tmp_path)
        is None
    )


def _is_device_job(argv: tuple[str, ...]) -> bool:
    return "-fsycl-is-device" in argv and "-ast-dump=json" not in argv


def _is_host_job(argv: tuple[str, ...]) -> bool:
    return "-fsycl-is-host" in argv and "-ast-dump=json" in argv


def _ast_run_script(monkeypatch, device_result, host_result=None):
    """Script every process call of a DPC++ AST run, in order: the driver
    plan (``-###``), the device job, then (when given) the host job."""
    import abicheck.deadline as deadline

    monkeypatch.setattr(
        dumper_dpcpp_jobs, "executable_revision", lambda b: f"id-{uuid.uuid4()}"
    )
    runner = StrictProcessRunner().expect(
        argv=["icpx", "-fsycl", "-fsycl-host-only", "a.hpp", "-###"],
        returns=proc(stderr=_DEVICE_JOB + "\n" + _HOST_JOB),
        label="driver plan",
    )
    runner.expect(
        argv_matches=_is_device_job, returns=device_result, label="device job"
    )
    if host_result is not None:
        runner.expect(argv_matches=_is_host_job, returns=host_result, label="host job")
    return runner.install(monkeypatch, deadline, "run_bounded")


def _run_ast(monkeypatch):
    from abicheck.dumper_clang_errors import run_clang_to_ast_file

    created: list[object] = []
    try:
        return run_clang_to_ast_file(
            ["icpx", "-fsycl", "-fsycl-host-only", "a.hpp"],
            timeout=5,
            on_created=created.append,
        )
    finally:
        for p in created:
            p.unlink()


def test_ast_run_executes_device_then_host_and_returns_the_host_result(monkeypatch):
    runner = _ast_run_script(
        monkeypatch, device_result=proc(), host_result=proc(stderr="")
    )
    _run_ast(monkeypatch)
    runner.assert_exhausted()
    assert "-fsycl-is-host" in runner.calls[-1].argv


def test_a_failed_device_pass_is_reported_without_running_the_host(monkeypatch):
    runner = _ast_run_script(
        monkeypatch, device_result=proc(returncode=1, stderr="device error")
    )
    result = _run_ast(monkeypatch)
    runner.assert_exhausted()
    assert (result.returncode, result.stderr) == (1, "device error")


def test_a_two_pass_driver_routes_the_host_request_to_the_selector(monkeypatch):
    monkeypatch.setattr(
        dumper_clang, "_driver_host_only_is_single_pass", lambda *_a: False
    )
    assert dumper_clang._resolve_dpcpp_acquisition("icpx", "host", None, ()) == (
        True,
        False,
    )
    monkeypatch.setattr(
        dumper_clang, "_driver_host_only_is_single_pass", lambda *_a: True
    )
    assert dumper_clang._resolve_dpcpp_acquisition("icpx", "host", None, ()) == (
        False,
        True,
    )


# ── memoized parameter trait, undecodable parameter, load-time strip ───────


def test_param_trait_computes_once_per_referenced_type(monkeypatch):
    from abicheck import dwarf_advanced as da

    calls: list[object] = []

    def trait(die, cu, cache=None):
        calls.append(die)
        return "nontrivial"

    monkeypatch.setattr(da, "_value_abi_trait_for_typed_die", trait)
    cu = SimpleNamespace(cu_offset=0x10)

    def param():
        return SimpleNamespace(
            attributes={"DW_AT_type": SimpleNamespace(form="DW_FORM_ref4", value=8)}
        )

    cache = da._DwarfTypeCache()
    assert da._param_trait(param(), cu, cache) == "nontrivial"
    assert da._param_trait(param(), cu, cache) == "nontrivial"
    assert len(calls) == 1
    assert cache.param_trait == {0x18: "nontrivial"}


def test_an_undecodable_parameter_is_built_and_parented(monkeypatch):
    built: list[object] = []

    class Child:
        def __init__(self, offset):
            self.offset = offset
            self.parent = None

        def set_parent(self, parent):
            self.parent = parent

    class CU:
        def _get_cached_DIE(self, offset):
            child = Child(offset)
            built.append(child)
            return child

    steps = {10: (12, "DW_TAG_formal_parameter"), 12: (14, "DW_TAG_formal_parameter")}
    index = SimpleNamespace(
        abbrev_table=object(),
        step_over=lambda off: steps.get(off),
        type_ref=lambda off: dsi.NOT_DECODABLE if off == 10 else 0x40,
    )
    monkeypatch.setattr(dsi, "_index_for", lambda _cu: index)
    die = SimpleNamespace(has_children=True, offset=8, size=2, cu=CU())
    got = list(dsi.iter_formal_parameter_type_refs(die))
    assert got == [(None, built[0]), (0x40, None)]
    assert built[0].offset == 10 and built[0].parent is die


def _raw_marker_snapshot():
    from abicheck.model import AbiSnapshot, RecordType

    raw = "W<(lambda at /checkout/a/foo.h:4:37)>"
    return AbiSnapshot(
        library="l",
        version="1",
        types=[RecordType(name=raw, kind="struct", qualified_name=raw, size_bits=8)],
    )


def test_load_strips_a_raw_location_before_renumbering():
    from abicheck.storage.snapshot_load_normalization import (
        normalize_and_renumber_closure_identities_on_load,
        normalize_anonymous_type_spellings_on_load,
    )

    snap = normalize_and_renumber_closure_identities_on_load(_raw_marker_snapshot())
    assert snap.declarations.types[0].qualified_name == "W<(lambda:foo.h#1)>"
    stripped = normalize_anonymous_type_spellings_on_load(_raw_marker_snapshot())
    assert stripped.declarations.types[0].qualified_name == "W<(lambda:foo.h:4:37)>"


def test_empty_qualified_name_has_no_segments():
    from abicheck.compare.qualified_name_normalization import segments

    assert segments("") == []


#: (path as the driver spells it, its directory spelled the same way). The
#: oracle is the spelling itself, not ``pathlib`` -- whose host-dependent
#: normalisation (``/tmp/x`` -> ``\tmp\x`` on Windows) is the bug.
_SPELLED_PARENTS = [
    ("/run/user/1000/icpx-1/agg-header.h", "/run/user/1000/icpx-1"),
    ("/var/folders/a b/T/icpx-9/agg-footer.h", "/var/folders/a b/T/icpx-9"),
    (
        "C:/Users/dev/AppData/Local/Temp/icpx-3/agg-header.h",
        "C:/Users/dev/AppData/Local/Temp/icpx-3",
    ),
    (
        "C:\\Users\\dev\\AppData\\Local\\Temp\\icpx-3\\agg-header.h",
        "C:\\Users\\dev\\AppData\\Local\\Temp\\icpx-3",
    ),
    ("D:\\a\\_temp/icpx-4/agg-header.h", "D:\\a\\_temp/icpx-4"),
    ("agg-header.h", ""),
]


@pytest.mark.parametrize(("path", "parent"), _SPELLED_PARENTS)
def test_spelled_parent_keeps_the_drivers_spelling(path: str, parent: str) -> None:
    assert dumper_dpcpp_jobs.spelled_parent(path) == parent
    # Re-joining with the same separator gives the input back.
    if parent:
        assert path.startswith(parent) and path[len(parent)] in "/\\"


_TEMP_DIR_SPELLINGS = [
    "/run/user/1000/icpx-1",
    "/var/folders/a b/T/icpx-9",
    "C:/Users/dev/AppData/Local/Temp/icpx-3",
    "C:\\Users\\dev\\AppData\\Local\\Temp\\icpx-3",
]


@pytest.mark.parametrize("temp_dir", _TEMP_DIR_SPELLINGS)
def test_replay_relocates_whatever_the_temp_dir_spelling_on_any_host(
    probe, tmp_path, temp_dir: str
) -> None:
    """Bug class: the relocation is a textual replace over the driver's own
    tokens, so the temp directory it searches for must be spelled as the
    driver spelled it. Derived through ``pathlib`` it matched on Linux and
    silently matched nothing on Windows (the integration files stayed in the
    driver's temp dir). Every spelling here runs on every host."""
    install, _ = probe
    sep = "\\" if "\\" in temp_dir else "/"
    header, footer = f"{temp_dir}{sep}agg-header.h", f"{temp_dir}{sep}agg-footer.h"
    device_job = (
        ' "/opt/clang" "-cc1" "-triple" "spir64" "-fsycl-is-device"'
        f' "-fsycl-int-header={header}" "-fsycl-int-footer={footer}"'
        ' "-ast-dump=json" "agg.hpp"'
    )
    host_job = (
        ' "/opt/clang" "-cc1" "-triple" "x86_64" "-fsycl-is-host"'
        f' "-include-internal-header" "{header}"'
        f' "-include-internal-footer" "{footer}" "-ast-dump=json" "agg.hpp"'
    )
    install(stderr=device_job + "\n" + host_job)
    cmd = ["icpx", "-fsycl", "-fsycl-host-only", "-Xclang", "-ast-dump=json", "a"]
    device, host = dumper_dpcpp_jobs.sycl_host_replay_jobs(cmd, tmp_path)
    assert f"-fsycl-int-header={tmp_path}{sep}agg-header.h" in device
    assert f"{tmp_path}{sep}agg-footer.h" in host
    assert not any(temp_dir in t for t in [*device, *host])
