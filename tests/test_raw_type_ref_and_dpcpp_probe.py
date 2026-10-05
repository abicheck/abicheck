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
from types import SimpleNamespace

import pytest

from abicheck import dumper_clang
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


@pytest.fixture
def probe(monkeypatch):
    """Run the probe against a scripted driver; returns the argv it saw."""
    import abicheck.deadline as deadline
    import abicheck.dumper_toolchain as toolchain

    seen: list[list[str]] = []
    counter = iter(range(10**6))
    # A fresh identity per call so the per-executable memo never answers.
    monkeypatch.setattr(toolchain, "_tool_identity", lambda b: f"id-{next(counter)}")

    def install(stderr: str | None = None, exc: BaseException | None = None):
        def fake(cmd, **_kw):
            seen.append(cmd)
            if exc is not None:
                raise exc
            return subprocess.CompletedProcess(cmd, 0, "", stderr)

        monkeypatch.setattr(deadline, "run_bounded", fake)

    return install, seen


def test_probe_reports_a_device_pass(probe):
    install, seen = probe
    install(
        stderr='"-cc1" "-triple" "spir64" "-fsycl-is-device"\n"-cc1" "-fsycl-is-host"'
    )
    assert dumper_clang._driver_host_only_is_single_pass("icpx", ("-O0",)) is False
    assert seen[0][:2] == ["icpx", "-O0"]
    assert {"-fsycl", "-fsycl-host-only", "-###"} <= set(seen[0])


def test_probe_reports_a_single_host_pass(probe):
    install, _ = probe
    install(stderr='"-cc1" "-triple" "x86_64" "-fsycl-is-host"')
    assert dumper_clang._driver_host_only_is_single_pass("icpx", ()) is True


@pytest.mark.parametrize(
    "exc", [OSError("no such file"), subprocess.TimeoutExpired("icpx", 30)]
)
def test_probe_that_cannot_run_keeps_the_host_only_request(probe, exc):
    install, _ = probe
    install(exc=exc)
    assert dumper_clang._driver_host_only_is_single_pass("icpx", ()) is True


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
