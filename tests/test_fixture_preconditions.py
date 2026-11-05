"""Unit tests for the catalog binary fixture-precondition mechanism."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import fixture_preconditions as fp  # noqa: E402

CET = ["IBT", "SHSTK"]
CASE179_SPECS = [
    {"side": "v1", "property": "elf_gnu_property", "includes": CET},
    {"side": "v2", "property": "elf_gnu_property", "excludes": CET},
]


@pytest.fixture
def fake_reader(monkeypatch: pytest.MonkeyPatch):
    """Register a reader whose tokens per binary name the test controls."""
    tokens: dict[str, frozenset[str]] = {}
    monkeypatch.setitem(
        fp.PROPERTY_READERS, "elf_gnu_property", lambda p: tokens[p.name]
    )
    return tokens


@pytest.mark.parametrize(
    ("v1_tokens", "v2_tokens", "met"),
    [
        ({"IBT", "SHSTK"}, set(), True),
        ({"IBT", "SHSTK", "x86-64-baseline"}, {"x86-64-baseline"}, True),
        (set(), set(), False),  # non-CET crt: linker ANDed the bits away
        ({"IBT"}, set(), False),  # half the premise
        ({"IBT", "SHSTK"}, {"SHSTK"}, False),  # v2 still protected
        ({"IBT", "SHSTK"}, {"IBT", "SHSTK"}, False),
    ],
)
def test_met_and_unmet(fake_reader, v1_tokens, v2_tokens, met, tmp_path) -> None:
    fake_reader["v1.so"] = frozenset(v1_tokens)
    fake_reader["v2.so"] = frozenset(v2_tokens)
    entry = {"fixture_preconditions": CASE179_SPECS}
    reason = fp.unmet(entry, tmp_path / "v1.so", tmp_path / "v2.so")
    # Oracle: plain set algebra over the declared premise, independent of unmet().
    expected_met = set(CET) <= v1_tokens and not (set(CET) & v2_tokens)
    assert expected_met is met
    if met:
        assert reason is None
    else:
        assert reason is not None and reason.startswith(fp.SKIP_PREFIX)


def test_no_declaration_is_always_met(tmp_path) -> None:
    assert fp.unmet({}, None, None) is None


def test_unreadable_binary_skips_not_fails(tmp_path) -> None:
    bogus = tmp_path / "v1.so"
    bogus.write_bytes(b"not an elf")
    reason = fp.unmet({"fixture_preconditions": CASE179_SPECS[:1]}, bogus, None)
    assert reason is not None and reason.startswith(fp.SKIP_PREFIX)
    assert "unreadable" in reason


@pytest.mark.parametrize(
    "spec",
    [
        {"side": "v3", "property": "elf_gnu_property"},
        {"side": "v1", "property": "no_such_property"},
    ],
)
def test_unknown_side_or_property_skips(spec, tmp_path) -> None:
    reason = fp.unmet({"fixture_preconditions": [spec]}, tmp_path, tmp_path)
    assert reason is not None and "unknown side/property" in reason


def test_validator_routes_unmet_precondition_to_skip() -> None:
    import validate_examples as ve

    res = ve._handle_build_error("c", "COMPATIBLE", f"{fp.SKIP_PREFIX}: x")
    assert res.status == "SKIP"
    assert res.message.startswith("fixture precondition unmet")


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not found")
def test_real_elf_gnu_property_reader(tmp_path) -> None:
    if not sys.platform.startswith("linux"):
        pytest.skip("ELF/CET is Linux x86-64 only")
    src = tmp_path / "a.c"
    src.write_text("int f(int (*g)(void)) { return g(); }\n")
    built = {}
    for name, flags in {
        "on": ["-fcf-protection=full", "-Wl,-z,ibt", "-Wl,-z,shstk"],
        "off": ["-fcf-protection=none"],
    }.items():
        out = tmp_path / f"{name}.so"
        r = subprocess.run(
            ["gcc", "-shared", "-fPIC", *flags, str(src), "-o", str(out)],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode != 0 and "fcf-protection" in r.stderr:
            pytest.skip("compiler lacks -fcf-protection (non-x86-64)")
        assert r.returncode == 0, r.stderr
        built[name] = out
    assert {"IBT", "SHSTK"} <= fp.PROPERTY_READERS["elf_gnu_property"](built["on"])
    entry = {"fixture_preconditions": CASE179_SPECS}
    assert fp.unmet(entry, built["on"], built["off"]) is None
    assert fp.unmet(entry, built["off"], built["off"]) is not None
