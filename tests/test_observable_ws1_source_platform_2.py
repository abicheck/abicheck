"""ADR-093 WS-1 batch 6: real-build oracles for three ``observable_at`` declarations.

Kinds: ``sanitizer_runtime_required`` ({B}, ELF), ``rtti_mode_changed``
({B,BD}), ``undocumented_export_ratio_increased`` ({B,H}). Each test builds real
GNU ELF shared objects with g++, runs the public ``abicheck dump`` and
``abicheck compare`` CLI, and checks the emission against the declaration with
a negative control. The ASan build is gated on a probe that the host can build
a sanitized shared object (a toolchain capability), never on the OS.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from abicheck.model.change_catalog.assembled import REGISTRY
from abicheck.model.change_catalog.observability import Platform
from tests._abicheck_subprocess import abicheck_command
from tests._elf_capability import elf_capability, skip_reason


def _gxx() -> list[str]:
    prefix = elf_capability("gcc", "shared")
    if prefix is None:
        pytest.skip(skip_reason("gcc", "shared"))
    return prefix


def _asan_capability(prefix: list[str], tmp_path: Path) -> None:
    """Skip, naming the missing capability, when no sanitized shared object builds."""
    probe_src = tmp_path / "asan_probe.cpp"
    probe_src.write_text('extern "C" int probe(int x){return x;}\n', encoding="utf-8")
    probe = subprocess.run(
        [
            *prefix,
            "-shared",
            "-fPIC",
            "-fsanitize=address",
            str(probe_src),
            "-o",
            str(tmp_path / "libasan_probe.so"),
        ],
        capture_output=True,
    )
    if probe.returncode != 0:
        pytest.skip(
            "capability missing: a sanitized (-fsanitize=address) shared object"
        )


def _dump(so: Path, out: Path, *extra: str) -> Path:
    subprocess.run(
        abicheck_command("dump", str(so), "-o", str(out), *extra),
        check=True,
        capture_output=True,
    )
    return out


def _compare_stdout(old: Path, new: Path, *extra: str) -> str:
    return subprocess.run(
        abicheck_command("compare", str(old), str(new), *extra),
        capture_output=True,
        text=True,
    ).stdout


def _observes(kind: str, config: tuple[str, ...]) -> bool:
    """Whether some declared entry for *kind* that admits ELF observes *config*."""
    obs = REGISTRY.entries[kind].observable_at
    applicable = [
        o for o in obs if o.condition is None or o.condition.matches(Platform.ELF, None)
    ]
    return any(o.observes(config) for o in applicable)


_KEEP = 'extern "C" int keep(int x) { return x; }\n'


@pytest.mark.integration
def test_sanitizer_runtime_required_is_emitted_from_the_binary_alone(
    tmp_path: Path,
) -> None:
    # An ASan-instrumented shared object carries the sanitizer runtime's imports
    # and DT_NEEDED: the binary alone observes the kind.
    prefix = _gxx()
    _asan_capability(prefix, tmp_path)
    src = tmp_path / "lib.cpp"
    src.write_text(_KEEP, encoding="utf-8")
    plain = tmp_path / "libplain.so"
    asan = tmp_path / "libasan.so"
    subprocess.run(
        [*prefix, "-shared", "-fPIC", str(src), "-o", str(plain)], check=True
    )
    subprocess.run(
        [*prefix, "-shared", "-fPIC", "-fsanitize=address", str(src), "-o", str(asan)],
        check=True,
    )
    old = _dump(plain, tmp_path / "old.json")
    new = _dump(asan, tmp_path / "new.json")
    assert "sanitizer_runtime_required" in _compare_stdout(old, new)
    assert _observes("sanitizer_runtime_required", ("B",))


@pytest.mark.integration
def test_sanitizer_runtime_required_is_absent_without_sanitizer_instrumentation(
    tmp_path: Path,
) -> None:
    # Negative control: two uninstrumented builds of the same source.
    prefix = _gxx()
    src = tmp_path / "lib.cpp"
    src.write_text(_KEEP, encoding="utf-8")
    so = tmp_path / "libplain.so"
    subprocess.run([*prefix, "-shared", "-fPIC", str(src), "-o", str(so)], check=True)
    old = _dump(so, tmp_path / "old.json")
    new = _dump(so, tmp_path / "new.json")
    assert "sanitizer_runtime_required" not in _compare_stdout(old, new)


def _build_info(tmp_path: Path, name: str, flags: str, src: Path) -> Path:
    """A one-entry compile database recording *flags* for *src*."""
    db = tmp_path / f"cc_{name}.json"
    entry = (
        f'[{{"directory": "{tmp_path}", '
        f'"command": "g++ -std=c++17 {flags} -fPIC -c {src.name} -o x.o", '
        f'"file": "{src}"}}]'
    )
    db.write_text(entry, encoding="utf-8")
    return db


@pytest.mark.integration
def test_rtti_mode_changed_is_emitted_from_build_data(tmp_path: Path) -> None:
    # -frtti versus -fno-rtti is compile data: the compile database records it,
    # and the compare at build depth emits the kind. Declared {B,BD}.
    prefix = _gxx()
    src = tmp_path / "a.cpp"
    src.write_text(_KEEP, encoding="utf-8")
    dumps = {}
    for name, flags in (("on", "-frtti"), ("off", "-fno-rtti")):
        so = tmp_path / f"lib{name}.so"
        subprocess.run(
            [*prefix, "-shared", "-fPIC", flags, str(src), "-o", str(so)], check=True
        )
        dumps[name] = _dump(
            so,
            tmp_path / f"{name}.json",
            "--build-info",
            str(_build_info(tmp_path, name, flags, src)),
        )
    out = _compare_stdout(dumps["on"], dumps["off"], "--depth", "build")
    assert "rtti_mode_changed" in out, out
    assert _observes("rtti_mode_changed", ("B", "BD"))
    assert not _observes("rtti_mode_changed", ("B",))


@pytest.mark.integration
def test_rtti_mode_changed_is_absent_from_binary_only_dumps(tmp_path: Path) -> None:
    # Binary-only dumps (no compile database) cannot see the flag.
    prefix = _gxx()
    src = tmp_path / "a.cpp"
    src.write_text(_KEEP, encoding="utf-8")
    dumps = {}
    for name, flags in (("on", "-frtti"), ("off", "-fno-rtti")):
        so = tmp_path / f"lib{name}.so"
        subprocess.run(
            [*prefix, "-shared", "-fPIC", flags, str(src), "-o", str(so)], check=True
        )
        dumps[name] = _dump(so, tmp_path / f"{name}.json")
    assert "rtti_mode_changed" not in _compare_stdout(
        dumps["on"], dumps["off"], "--depth", "build"
    )


@pytest.mark.integration
def test_undocumented_export_ratio_increased_is_emitted_with_headers(
    tmp_path: Path,
) -> None:
    # An export added without a public-header declaration raises the
    # undocumented ratio. The header records are needed on the new side.
    prefix = _gxx()
    old_src = tmp_path / "old.cpp"
    new_src = tmp_path / "new.cpp"
    old_src.write_text(
        'extern "C" int a1(int x){return x;}\nextern "C" int a2(int x){return x;}\n',
        encoding="utf-8",
    )
    new_src.write_text(
        'extern "C" int a1(int x){return x;}\nextern "C" int a2(int x){return x;}\n'
        'extern "C" int a3(int x){return x;}\n',
        encoding="utf-8",
    )
    old_h = tmp_path / "old.h"
    new_h = tmp_path / "new.h"
    old_h.write_text("int a1(int);\nint a2(int);\n", encoding="utf-8")
    # The new public header declares a1 and a2 only: a3 is exported undocumented.
    new_h.write_text("int a1(int);\nint a2(int);\n", encoding="utf-8")
    so_old = tmp_path / "libold.so"
    so_new = tmp_path / "libnew.so"
    subprocess.run(
        [*prefix, "-shared", "-fPIC", str(old_src), "-o", str(so_old)], check=True
    )
    subprocess.run(
        [*prefix, "-shared", "-fPIC", str(new_src), "-o", str(so_new)], check=True
    )
    old = _dump(so_old, tmp_path / "old_h.json", "-H", str(old_h))
    new = _dump(so_new, tmp_path / "new_h.json", "-H", str(new_h))
    assert "undocumented_export_ratio_increased" in _compare_stdout(old, new)
    assert _observes("undocumented_export_ratio_increased", ("B", "H"))
    assert not _observes("undocumented_export_ratio_increased", ("B",))


@pytest.mark.integration
def test_undocumented_export_ratio_increased_is_absent_from_binary_only_dumps(
    tmp_path: Path,
) -> None:
    # Without headers the ratio is not computed, so the same pair emits nothing.
    prefix = _gxx()
    old_src = tmp_path / "old.cpp"
    new_src = tmp_path / "new.cpp"
    old_src.write_text(
        'extern "C" int a1(int x){return x;}\nextern "C" int a2(int x){return x;}\n',
        encoding="utf-8",
    )
    new_src.write_text(
        'extern "C" int a1(int x){return x;}\nextern "C" int a2(int x){return x;}\n'
        'extern "C" int a3(int x){return x;}\n',
        encoding="utf-8",
    )
    so_old = tmp_path / "libold.so"
    so_new = tmp_path / "libnew.so"
    subprocess.run(
        [*prefix, "-shared", "-fPIC", str(old_src), "-o", str(so_old)], check=True
    )
    subprocess.run(
        [*prefix, "-shared", "-fPIC", str(new_src), "-o", str(so_new)], check=True
    )
    old = _dump(so_old, tmp_path / "old.json")
    new = _dump(so_new, tmp_path / "new.json")
    assert "undocumented_export_ratio_increased" not in _compare_stdout(old, new)
