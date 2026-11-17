"""The libstdc++ dual-ABI flip is detected per symbol, not by mass churn.

A flip of one or two functions used to read nothing: the detector wanted at
least five removed and five added exports. The oracle here is independent of
abicheck's detector and of its demangler:

* the expected count is the number of functions the source passes a
  ``std::string`` to (each is re-mangled by a flip, and nothing else exports);
* the expected direction comes from ``c++filt`` on the side whose names carry
  the ``__cxx11`` inline namespace.

Built with the real toolchain (``g++``) at one, two and eight functions, flipped
both ways; a library with no ``std::string`` must not be flagged.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._abicheck_subprocess import abicheck_command

pytestmark = pytest.mark.integration

_GXX = shutil.which("g++")
_FLIP = "glibcxx_dual_abi_flip_detected"
_CHANGED = "glibcxx_dual_abi_changed"


def _source(n_string: int, n_plain: int = 1) -> str:
    """A library exporting *n_string* functions that take a ``std::string`` (the
    flip re-mangles each) and *n_plain* that take no string (never re-mangled).
    The bodies use only ``size()``, which is inlined, so the library exports
    exactly these functions."""
    lines = ["#include <string>"]
    for i in range(n_string):
        lines.append(
            f"int with_string{i}(const std::string& s) {{ return (int)s.size() + {i}; }}"
        )
    for i in range(n_plain):
        lines.append(f"int plain{i}(int x) {{ return x + {i}; }}")
    return "\n".join(lines) + "\n"


def _build(work: Path, tag: str, n_string: int, abi: int, n_plain: int = 1) -> Path:
    src = work / f"{tag}_abi{abi}.cpp"
    src.write_text(_source(n_string, n_plain))
    so = work / f"lib{tag}_abi{abi}.so"
    subprocess.run(
        [
            _GXX,
            "-shared",
            "-fPIC",
            "-O2",
            f"-D_GLIBCXX_USE_CXX11_ABI={abi}",
            str(src),
            "-o",
            str(so),
        ],
        check=True,
    )
    return so


def _exported_demangled(so: Path) -> list[str]:
    """The oracle's view of the exports: ``nm`` then ``c++filt``, not abicheck."""
    nm = subprocess.run(
        ["nm", "-D", "--defined-only", str(so)],
        capture_output=True,
        text=True,
        check=True,
    )
    mangled = [line.split()[-1] for line in nm.stdout.splitlines() if line.strip()]
    filt = subprocess.run(
        ["c++filt"],
        input="\n".join(mangled) + "\n",
        capture_output=True,
        text=True,
        check=True,
    )
    return filt.stdout.splitlines()


def _compare(work: Path, old: Path, new: Path) -> list[dict]:
    old_json, new_json = work / f"{old.stem}.json", work / f"{new.stem}.json"
    for so, out in ((old, old_json), (new, new_json)):
        subprocess.run(
            abicheck_command("dump", str(so), "-o", str(out)),
            check=True,
            capture_output=True,
        )
    report = work / f"{old.stem}_vs_{new.stem}.json"
    subprocess.run(
        abicheck_command(
            "compare", str(old_json), str(new_json), "-o", f"json={report}"
        ),
        capture_output=True,
    )
    return json.loads(report.read_text())["changes"]


def _expected_direction(old_demangled: list[str]) -> str:
    """Which way the flip goes, read off the old side's ``c++filt`` names."""
    with_tag = any("__cxx11::" in name for name in old_demangled)
    return "CXX11 ABI → legacy ABI" if with_tag else "legacy ABI → CXX11 ABI"


@pytest.mark.skipif(_GXX is None, reason="g++ not on PATH")
@pytest.mark.parametrize("n", [1, 2, 8], ids=["one", "two", "many"])
@pytest.mark.parametrize("direction", ["legacy_to_cxx11", "cxx11_to_legacy"])
def test_a_flip_of_n_functions_is_detected_with_its_real_count(tmp_path, n, direction):
    old_abi, new_abi = (0, 1) if direction == "legacy_to_cxx11" else (1, 0)
    old = _build(tmp_path, f"n{n}", n, old_abi)
    new = _build(tmp_path, f"n{n}", n, new_abi)

    # Oracle: each string-taking function is removed under one spelling and added
    # under the other, so a flip churns 2n symbols; the plain function never does.
    old_names = _exported_demangled(old)
    assert sum("__cxx11::" in name for name in old_names) == (n if old_abi == 1 else 0)
    flips = [c for c in _compare(tmp_path, old, new) if c["kind"] == _FLIP]

    assert len(flips) == 1, flips
    description = flips[0]["description"]
    assert f"{n} of {2 * n} churned symbols" in description, description
    assert f"({_expected_direction(old_names)})" in description, description


@pytest.mark.skipif(_GXX is None, reason="g++ not on PATH")
@pytest.mark.parametrize(
    "abi_pair", [(0, 1), (1, 0)], ids=["legacy_to_cxx11", "cxx11_to_legacy"]
)
def test_a_library_without_std_string_is_not_flagged(tmp_path, abi_pair):
    """No ``std::string`` anywhere: nothing is re-mangled, so no flip is reported
    (and the toolchain flag alone does not change the exports)."""
    old_abi, new_abi = abi_pair
    old = _build(tmp_path, "nostr", 0, old_abi, n_plain=3)
    new = _build(tmp_path, "nostr", 0, new_abi, n_plain=3)
    kinds = {c["kind"] for c in _compare(tmp_path, old, new)}
    assert _FLIP not in kinds
    assert _CHANGED not in kinds
