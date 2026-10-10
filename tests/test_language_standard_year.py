# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""``model.language_standard.language_standard_year`` is the one owner that turns a
``-std=`` spelling into an orderable number.

Bug class: several private parsers of the same spelling (first-match vs
last-match flag scans, a draft table missing ``1z``/``0x``, a bare two-digit
suffix that ranks ``c++98`` above ``c++20``) answering the same question
differently. The oracle is an independent table of published editions:
every spelling of an edition -- ISO, GNU, draft, language-tagged -- must map
to that edition's year, and order must follow the year.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.build_context import _std_sort_key
from abicheck.extract.headers.toolchain import _cplusplus_macro_for_standard
from abicheck.model.language_standard import language_standard_year
from abicheck.probe_harness import _parse_cxx_std, parse_probe_spec

# edition year -> every spelling GCC/Clang accept for it.
_CXX_EDITIONS: dict[int, tuple[str, ...]] = {
    1998: ("98",),
    2003: ("03",),
    2011: ("11", "0x"),
    2014: ("14", "1y"),
    2017: ("17", "1z"),
    2020: ("20", "2a"),
    2023: ("23", "2b"),
    2026: ("26", "2c"),
}
_C_EDITIONS: dict[int, tuple[str, ...]] = {
    1989: ("89", "90"),
    1999: ("99", "9x"),
    2011: ("11", "1x"),
    2017: ("17", "18"),
    2023: ("23", "2x"),
}


def _cxx_spellings():
    for year, editions in _CXX_EDITIONS.items():
        for edition, dialect in itertools.product(editions, ("c++", "gnu++")):
            yield year, f"{dialect}{edition}"
            yield year, f"c++:{dialect}{edition}"
            yield year, f"{dialect}{edition}".upper()


def _c_spellings():
    for year, editions in _C_EDITIONS.items():
        for edition, dialect in itertools.product(editions, ("c", "gnu")):
            if edition in ("90", "18"):
                continue  # aliases whose year is not the edition's own name
            yield year, f"{dialect}{edition}"
            yield year, f"c:{dialect}{edition}"


@pytest.mark.parametrize(("year", "spelling"), list(_cxx_spellings()))
def test_every_cxx_spelling_maps_to_its_edition(year: int, spelling: str) -> None:
    assert language_standard_year(spelling) == year


@pytest.mark.parametrize(("year", "spelling"), list(_c_spellings()))
def test_every_c_spelling_maps_to_its_edition(year: int, spelling: str) -> None:
    assert language_standard_year(spelling) == year


@pytest.mark.parametrize(
    "spelling", ["", "c++latest", "c++23a", "c++", "gnu", "c++2", "cxx17", "iso"]
)
def test_unrecognized_spellings_are_none_not_a_guess(spelling: str) -> None:
    assert language_standard_year(spelling) is None


def test_sort_order_follows_publication_year_for_every_pair() -> None:
    spellings = list(_cxx_spellings())
    for (y1, s1), (y2, s2) in itertools.product(spellings, repeat=2):
        assert (_std_sort_key(s1) < _std_sort_key(s2)) == (y1 < y2), (s1, s2)


def test_pre_2000_editions_order_below_later_ones() -> None:
    """A bare two-digit suffix made ``c++98`` outrank ``c++20``."""
    stds = ["c++98", "c++20", "c++03", "c++11"]
    assert sorted(stds, key=_std_sort_key) == ["c++98", "c++03", "c++11", "c++20"]
    assert max(["c89", "c11"], key=_std_sort_key) == "c11"


@pytest.mark.parametrize(("year", "spelling"), list(_cxx_spellings()))
def test_probe_harness_reads_the_same_edition(year: int, spelling: str) -> None:
    if ":" in spelling:
        return  # a language tag is a stored-field spelling, never a flag
    assert _parse_cxx_std([f"-std={spelling}"]) == year % 100


def test_probe_harness_takes_the_last_std_flag_like_the_compiler() -> None:
    assert _parse_cxx_std(["-std=c++17", "-O2", "-std=c++20"]) == 20
    assert _parse_cxx_std(["-std=c++20", "-std=c++17"]) == 17
    assert _parse_cxx_std(["-std=c11"]) is None
    assert _parse_cxx_std(["-Wall"]) is None


def test_cplusplus_macro_agrees_across_spellings_of_one_edition() -> None:
    for editions in _CXX_EDITIONS.values():
        macros = {
            _cplusplus_macro_for_standard(f"{dialect}{edition}")
            for edition in editions
            for dialect in ("c++", "gnu++")
        }
        assert len(macros) == 1, (editions, macros)


@pytest.mark.parametrize(("year", "spelling"), list(_cxx_spellings()))
def test_a_probe_spec_accepts_every_spelling_it_can_read(
    year: int, spelling: str
) -> None:
    """The flag allowlist ran before the reader and rejected ``gnu++20``."""
    if ":" in spelling:
        return
    spec = parse_probe_spec(
        {
            "name": "t",
            "configurations": [
                {"id": "c", "compiler": "g++", "flags": [f"-std={spelling}"]}
            ],
            "probes": [{"name": "p", "body": "int main() {}"}],
        }
    )
    assert spec.configurations[0].cxx_std == year % 100


@pytest.mark.parametrize("flag", ["-std=c11", "-std=gnu11", "-std=foo", "-std="])
def test_a_probe_spec_still_rejects_non_cxx_std_flags(flag: str) -> None:
    with pytest.raises(ValueError, match="disallowed"):
        parse_probe_spec(
            {
                "name": "t",
                "configurations": [{"id": "c", "compiler": "g++", "flags": [flag]}],
                "probes": [{"name": "p", "body": "int main() {}"}],
            }
        )
