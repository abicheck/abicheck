"""ADR-093 WS-1 batch 5: real-build oracles for ``observable_at`` declarations.

Each test builds real GNU ELF shared objects with g++, dumps them with the
``abicheck dump`` CLI (no headers, no ``-g``: the binary alone), and runs the
public ``abicheck compare`` workflow. The declared ``observable_at`` must
observe the configuration the oracle used, and a negative control must show
the kind is absent when its trigger is absent.

Gated on ``tests/_elf_capability`` (a GNU ELF producer), never on the OS.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from abicheck.model.change_catalog.assembled import REGISTRY
from abicheck.model.change_catalog.observability import Platform
from tests._abicheck_subprocess import abicheck_command
from tests._elf_capability import elf_capability, skip_reason


def _gxx():
    prefix = elf_capability("gcc", "shared")
    if prefix is None:
        pytest.skip(skip_reason("gcc", "shared"))
    return prefix


def _build_and_dump(
    tmp_path: Path, name: str, source: str, soname: str, prefix: list[str]
) -> Path:
    src = tmp_path / f"{name}.cpp"
    src.write_text(source, encoding="utf-8")
    so = tmp_path / f"lib{name}.so"
    subprocess.run(
        [*prefix, "-shared", "-fPIC", f"-Wl,-soname,{soname}", str(src), "-o", str(so)],
        check=True,
    )
    dump = tmp_path / f"{name}.json"
    subprocess.run(
        abicheck_command("dump", str(so), "-o", str(dump)),
        check=True,
        capture_output=True,
    )
    return dump


def _compare_stdout(old: Path, new: Path) -> str:
    result = subprocess.run(
        abicheck_command("compare", str(old), str(new)),
        capture_output=True,
        text=True,
    )
    return result.stdout


def _observes(kind: str, config: tuple[str, ...]) -> bool:
    """Whether some declared entry for *kind* that admits ELF observes *config*."""
    obs = REGISTRY.entries[kind].observable_at
    applicable = [
        o for o in obs if o.condition is None or o.condition.matches(Platform.ELF, None)
    ]
    return any(o.observes(config) for o in applicable)


_BINARY = ("B",)

_KEEP = 'extern "C" int keep(int x) { return x; }\n'
_ADD2 = 'extern "C" int add2(int x) { return x + 2; }\n'
_INTERNAL = 'extern "C" int _internal_helper(int x) { return x; }\n'


@pytest.mark.integration
def test_soname_bump_recommended_is_emitted_from_the_binary_alone(
    tmp_path: Path,
) -> None:
    # A removed export with the SONAME left unchanged: the binary's DT_SONAME
    # and export table carry both facts, so the binary alone observes the kind.
    prefix = _gxx()
    old = _build_and_dump(
        tmp_path,
        "old",
        _KEEP + 'extern "C" int gone(int x){return x+1;}\n',
        "libo.so.1",
        prefix,
    )
    new = _build_and_dump(tmp_path, "new", _KEEP, "libo.so.1", prefix)
    assert "soname_bump_recommended" in _compare_stdout(old, new)
    assert _observes("soname_bump_recommended", _BINARY)


@pytest.mark.integration
def test_soname_bump_recommended_is_absent_without_a_break(tmp_path: Path) -> None:
    # Negative control: the same SONAME with only an addition is not a break.
    prefix = _gxx()
    old = _build_and_dump(tmp_path, "old", _KEEP, "libo.so.1", prefix)
    new = _build_and_dump(tmp_path, "new", _KEEP + _ADD2, "libo.so.1", prefix)
    assert "soname_bump_recommended" not in _compare_stdout(old, new)


@pytest.mark.integration
def test_soname_bump_unnecessary_is_emitted_from_the_binary_alone(
    tmp_path: Path,
) -> None:
    # A changed SONAME with only an addition: DT_SONAME is a binary fact.
    prefix = _gxx()
    old = _build_and_dump(tmp_path, "old", _KEEP, "libo.so.1", prefix)
    new = _build_and_dump(tmp_path, "new", _KEEP + _ADD2, "libo.so.2", prefix)
    out = _compare_stdout(old, new)
    assert "soname_bump_unnecessary" in out, out
    assert "soname_bump_recommended" not in out, out
    assert _observes("soname_bump_unnecessary", _BINARY)


@pytest.mark.integration
def test_soname_bump_unnecessary_is_absent_when_the_soname_is_unchanged(
    tmp_path: Path,
) -> None:
    prefix = _gxx()
    old = _build_and_dump(tmp_path, "old", _KEEP, "libo.so.1", prefix)
    new = _build_and_dump(tmp_path, "new", _KEEP + _ADD2, "libo.so.1", prefix)
    assert "soname_bump_unnecessary" not in _compare_stdout(old, new)


@pytest.mark.integration
def test_visibility_leak_is_emitted_from_the_binary_alone(tmp_path: Path) -> None:
    # The old side exports an internal-looking symbol that no header declares:
    # the export table alone carries the fact.
    prefix = _gxx()
    old = _build_and_dump(tmp_path, "old", _KEEP + _INTERNAL, "libo.so", prefix)
    new = _build_and_dump(tmp_path, "new", _KEEP + _INTERNAL, "libo.so", prefix)
    assert "visibility_leak" in _compare_stdout(old, new)
    assert _observes("visibility_leak", _BINARY)


@pytest.mark.integration
def test_visibility_leak_is_absent_without_an_internal_export(tmp_path: Path) -> None:
    # Negative control: no internal-looking export on either side.
    prefix = _gxx()
    old = _build_and_dump(tmp_path, "old", _KEEP, "libo.so", prefix)
    new = _build_and_dump(tmp_path, "new", _KEEP + _ADD2, "libo.so", prefix)
    assert "visibility_leak" not in _compare_stdout(old, new)
