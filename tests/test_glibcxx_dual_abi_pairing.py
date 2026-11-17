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
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.compare.glibcxx_dual_abi_pairs import pair_dual_abi_flips
from tests._abicheck_subprocess import abicheck_command
from tests._elf_capability import elf_capability

#: The oracle reads ELF (``nm -D``) and builds the flip with the libstdc++ dual
#: ABI, so it needs an ELF-writing g++ (the probe names the missing capability)
#: plus nm and c++filt. A host without them is a skip; a compiler that runs and
#: fails is a failure.
_ELF_GXX = elf_capability("gcc", "shared", std_headers=True)
_MISSING_TOOLS = [t for t in ("nm", "c++filt") if shutil.which(t) is None]
_ORACLE_SKIP = (
    "no ELF-capable g++ on this host"
    if _ELF_GXX is None
    else f"oracle tools not on PATH: {_MISSING_TOOLS}"
    if _MISSING_TOOLS
    else None
)
_NEEDS_ORACLE = pytest.mark.skipif(_ORACLE_SKIP is not None, reason=_ORACLE_SKIP or "")
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
            *_ELF_GXX,
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


@pytest.mark.integration
@_NEEDS_ORACLE
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


@pytest.mark.integration
@_NEEDS_ORACLE
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


# -- The pairing primitive's contract, without a compiler ----------------------
#
# ``pair_dual_abi_flips`` takes mangled names and their demangled spellings. The
# identities below are the demangled forms libstdc++ prints for one function
# taking ``std::string`` under each ABI.

_LEGACY = "with_string{i}(std::string const&)"
_CXX11 = (
    "with_string{i}(std::__cxx11::basic_string<char, std::char_traits<char>, "
    "std::allocator<char> > const&)"
)


def _flip_churn(
    n: int, *, legacy_removed: bool
) -> tuple[list[str], list[str], dict, dict]:
    """*n* functions re-mangled by a flip, removed under one spelling and added
    under the other, mangled names ``m<i>`` mapped to their demangled forms."""
    old_fmt, new_fmt = (_LEGACY, _CXX11) if legacy_removed else (_CXX11, _LEGACY)
    removed = [f"r{i}" for i in range(n)]
    added = [f"a{i}" for i in range(n)]
    rem_dm = {f"r{i}": old_fmt.format(i=i) for i in range(n)}
    add_dm = {f"a{i}": new_fmt.format(i=i) for i in range(n)}
    return removed, added, rem_dm, add_dm


@pytest.mark.parametrize("legacy_removed", [True, False], ids=["to_cxx11", "to_legacy"])
@pytest.mark.parametrize("n", [1, 2, 8])
def test_every_flipped_function_pairs_once_in_either_direction(n, legacy_removed):
    removed, added, rem_dm, add_dm = _flip_churn(n, legacy_removed=legacy_removed)
    p = pair_dual_abi_flips(removed, added, rem_dm, add_dm)
    assert p.paired == n
    assert p.rem_tagged_paired + p.add_tagged_paired == n
    assert p.un_removed == 0 and p.un_added == 0
    assert p.mass is False


@pytest.mark.parametrize("seed", range(20))
def test_pairing_is_invariant_under_input_order(seed):
    removed, added, rem_dm, add_dm = _flip_churn(8, legacy_removed=True)
    base = pair_dual_abi_flips(removed, added, rem_dm, add_dm)
    rng = random.Random(seed)
    shuffled_r, shuffled_a = removed[:], added[:]
    rng.shuffle(shuffled_r)
    rng.shuffle(shuffled_a)
    assert pair_dual_abi_flips(shuffled_r, shuffled_a, rem_dm, add_dm) == base


@pytest.mark.parametrize("seed", range(20))
def test_a_removal_pairs_at_most_one_addition(seed):
    """Each removed export pairs with at most one added export: the paired count
    is bounded by the smaller side, however the churn is subset."""
    rng = random.Random(seed)
    removed, added, rem_dm, add_dm = _flip_churn(8, legacy_removed=bool(seed % 2))
    keep_r = rng.sample(removed, rng.randint(0, len(removed)))
    keep_a = rng.sample(added, rng.randint(0, len(added)))
    p = pair_dual_abi_flips(keep_r, keep_a, rem_dm, add_dm)
    assert p.paired <= min(len(keep_r), len(keep_a))


def test_a_user_namespace_named_like_the_inline_namespace_is_not_a_flip():
    """Negative control: ``api::__cxx11::f`` moved to ``api::f`` is a namespace
    change, not a libstdc++ dual-ABI flip, and must not pair."""
    p = pair_dual_abi_flips(
        ["m1"],
        ["m2"],
        {"m1": "api::__cxx11::f(int)"},
        {"m2": "api::f(int)"},
    )
    assert p.paired == 0
    assert p.rem_tagged_paired == 0 and p.add_tagged_paired == 0


@pytest.mark.parametrize(
    ("n_removed", "n_added", "tagged", "expected_mass"),
    [
        (4, 5, 4, False),  # the five-per-side floor is not met
        (5, 5, 0, False),  # no marker in any unreadable name
        (5, 5, 2, False),  # 2 of 10 churned names: under the 30% boundary
        (5, 5, 3, True),  # 3 of 10 reaches the 30% boundary
        (5, 5, 10, True),  # every unreadable name tagged
    ],
)
def test_unreadable_fallback_boundaries(n_removed, n_added, tagged, expected_mass):
    """Unreadable names have no identity, so they never pair; only the raw-marker
    mass fallback applies, with its floor and 30% boundary."""
    total = n_removed + n_added
    removed = [
        f"_ZN{'7__cxx11' if i < tagged else '3foo'}{i}E" for i in range(n_removed)
    ]
    added = [
        f"_ZN{'7__cxx11' if n_removed + j < tagged else '3foo'}{j}E"
        for j in range(n_added)
    ]
    p = pair_dual_abi_flips(removed, added, {}, {})
    assert p.paired == 0
    assert p.un_removed + p.un_added == total
    assert p.mass is expected_mass


def test_a_user_namespace_move_with_a_std_string_argument_is_not_a_flip():
    """Negative control (review): ``api::__cxx11::f(std::list<int>)`` moved to
    ``api::f(std::__cxx11::list<int>)`` changes the namespace as well as the
    spelling, so it is not one flipped function."""
    p = pair_dual_abi_flips(
        ["m1"],
        ["m2"],
        {"m1": "api::__cxx11::f(std::list<int>)"},
        {"m2": "api::f(std::__cxx11::list<int>)"},
    )
    assert p.paired == 0


@pytest.mark.parametrize(
    ("spelling", "expected"),
    [
        # libstdc++'s own inline namespace and tag are the dual-ABI spellings.
        (
            "f(std::__cxx11::list<int, std::allocator<int> > const&)",
            "f(std::list<int, std::allocator<int> > const&)",
        ),
        ("f[abi:cxx11](int)", "f(int)"),
        (
            "f(std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > const&)",
            "f(std::string const&)",
        ),
        # A user namespace's __cxx11:: names a different function: kept.
        ("api::__cxx11::f(int)", "api::__cxx11::f(int)"),
        ("api::f(std::__cxx11::list<int> const&)", "api::f(std::list<int> const&)"),
        (
            "ns::__cxx11::g(std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >)",
            "ns::__cxx11::g(std::string)",
        ),
    ],
)
def test_normaliser_strips_only_the_std_inline_namespace(spelling, expected):
    from abicheck.compare.glibcxx_dual_abi import normalize_dual_abi_spelling

    assert normalize_dual_abi_spelling(spelling) == expected


def test_user_namespace_move_is_not_equal_to_the_std_spelling_after_normalising():
    from abicheck.compare.glibcxx_dual_abi import normalize_dual_abi_spelling

    assert normalize_dual_abi_spelling(
        "api::__cxx11::f(int)"
    ) != normalize_dual_abi_spelling("api::f(int)")
