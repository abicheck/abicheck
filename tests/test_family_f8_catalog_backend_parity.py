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

"""Harness H8, catalog-wide: the clang header backend records the record
layout castxml records, for every buildable C/C++ case in ``catalog/``.

Bug class ``extraction.backend_fact_family_parity`` (family F8): the same
headers through the clang backend must carry the layout facts castxml reads
from its own Clang -- with no DWARF to fall back on. The single-header cell in
``test_family_f8_target_parity.py`` checks one hand-written header; this
module runs the same relation over the whole native catalog, whose cases were
written for other bugs and so reach shapes nobody wrote with the backends in
mind (empty tag types, C typedef'd structs, anonymous unions, virtual and
empty bases, defaulted template arguments, inline namespaces).

**Cell.** Each case's v1 and v2 libraries are built once (the shared CMake
tree, or a direct compile), copied with ``objcopy --strip-debug`` so the
layout can only come from the headers, and dumped through the typed API
(``abicheck.service.run_dump``) with ``ABICHECK_AST_FRONTEND=castxml`` and
``=clang``. Dumps are memoized per session, so the seeded mutant reuses the
parity run's builds and oracle.

**Oracle.** The castxml dump. For every *library-owned* record -- declared in
a header inside the case's own directory, not a class-template pattern --
both dumps must agree on: presence, ``size_bits``, ``alignment_bits``, the
ordered field names with their ``offset_bits`` and type identity, the base
offsets, and vtable presence. Type identity is compared normalized
(:func:`type_identity`): spelling noise that names the same type -- ``short
int`` vs ``short``, ``std::__cxx11::`` vs ``std::``, a type written
relative to the enclosing scope (``detail::Impl`` vs ``ns::detail::Impl``)
-- is equal; different types are not.

**Exceptions.** A divergence that is not a backend bug to fix here is a
:data:`BACKEND_EXCEPTIONS` row: the frontend whose fact differs from the
truth, the case, the record, the fact, the reason, and the known-gaps entry
tracking it. A row that no longer matches a divergence fails the cell
(stale), so the table can only shrink as the backends converge.

**Seeded mutant.** ``ABICHECK_CLANG_LAYOUT_TOOL=/bin/false`` replaces the
built-in record-layout pass with a companion tool that always fails, which
leaves every clang record unsized; the oracle must report it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.test_example_autodiscovery import (
    _ALL_CASES,
    CURRENT_PLATFORM,
    PLATFORMS,
    REQUIRES_FEATURE,
    _build_libs_for_case,
    _find_sources,
)

_KNOWN_GAPS = "docs/contribute/known-gaps.md"
_GAP_NAMING = (
    f"{_KNOWN_GAPS}#castxml-elides-defaulted-template-arguments-and-inline-"
    "namespaces-from-record-names-2026-11-05"
)
_GAP_IMPLICIT = (
    f"{_KNOWN_GAPS}#the-clang-header-backend-records-only-explicitly-"
    "instantiated-class-template-specializations-2026-11-05"
)


# -- Normalized type identity -------------------------------------------------
#
# Written against the spellings, not against abicheck's own canonicalizer, so
# the comparison does not share a blind spot with the code under test.

_INLINE_STD_NS = re.compile(r"\bstd::(?:__cxx11::|__1::)")
_TAG_KEYWORD = re.compile(r"\b(?:struct|class|union|enum)\s+")
_ANON = re.compile(r"\((?:anonymous|unnamed)[^()]*(?:\([^()]*\)[^()]*)*\)")
#: A run of integer type-specifier words, in any order (``long unsigned int``).
_INT_RUN = re.compile(r"\b(?:(?:signed|unsigned|short|long|int|char)\b\s*)+")


def _int_spelling(match: re.Match[str]) -> str:
    """One spelling per integer type: the specifier words of ``long unsigned
    int`` and ``unsigned long`` are the same multiset, so they name one type."""
    words = match.group(0).split()
    trail = " " if match.group(0)[-1].isspace() else ""
    if "char" in words:  # char, signed char and unsigned char are distinct
        sign = next((w for w in words if w in ("signed", "unsigned")), "")
        return f"{sign} char".strip() + trail
    size = (
        "short"
        if "short" in words
        else " ".join(["long"] * words.count("long")) or "int"
    )
    return ("unsigned " if "unsigned" in words else "") + size + trail


_TOKEN = re.compile(r"[A-Za-z_]\w*(?:::[A-Za-z_~]\w*)*|\S")


def _tokens(spelling: str) -> tuple[str, ...]:
    s = _ANON.sub("<anonymous>", spelling)
    s = _INLINE_STD_NS.sub("std::", s)
    s = _TAG_KEYWORD.sub("", s)
    s = _INT_RUN.sub(_int_spelling, s)
    tokens = [t.removeprefix("::") for t in _TOKEN.findall(s)]
    # East const: ``const T`` and ``T const`` name one type when T is a single
    # name (template arguments and declarators keep their order).
    if (
        len(tokens) >= 2
        and tokens[0] in ("const", "volatile")
        and tokens[1][0].isalpha()
    ):
        tokens = [tokens[1], tokens[0], *tokens[2:]]
    return tuple(tokens)


def _same_name(a: str, b: str) -> bool:
    """One qualified name written relative to a scope the other spells out:
    the shorter one's components are a suffix of the longer one's."""
    pa, pb = a.split("::"), b.split("::")
    short, long_ = (pa, pb) if len(pa) <= len(pb) else (pb, pa)
    return long_[len(long_) - len(short) :] == short


def type_identity_equal(a: str | None, b: str | None) -> bool:
    """Whether two field-type spellings name the same type."""
    if a is None or b is None:
        return a is b
    ta, tb = _tokens(a), _tokens(b)
    return len(ta) == len(tb) and all(
        _same_name(x, y) for x, y in zip(ta, tb, strict=True)
    )


# -- Record facts and the divergence oracle -----------------------------------


@dataclass(frozen=True)
class Divergence:
    side: str  # "v1" | "v2"
    record: str
    fact: str
    castxml: object
    clang: object

    def __str__(self) -> str:
        return f"{self.side} {self.record} {self.fact}: castxml={self.castxml!r} clang={self.clang!r}"


def _owned_records(snap, case_dir: Path) -> dict[str, object]:
    root = str(case_dir.resolve())
    out = {}
    for t in snap.declarations.types:
        header = t.source_header or ""
        if t.is_template_pattern or not str(Path(header).resolve()).startswith(root):
            continue
        out[t.qualified_name or t.name] = t
    return out


def _vtable_present(t) -> bool | None:
    fact = t.vtable_fact
    if fact is None or not fact.is_present:
        return None
    return bool(fact.value)


def _match_keys(ref: dict[str, int], got: dict[str, int]) -> dict[str, int | None]:
    """*got*'s value under each of *ref*'s keys, by type identity."""
    out: dict[str, int | None] = {}
    for key in ref:
        hit = next((v for k, v in got.items() if type_identity_equal(key, k)), None)
        out[key] = hit
    return out


def record_divergences(
    side: str, oracle, candidate, case_dir: Path
) -> list[Divergence]:
    """Every layout fact *candidate* (clang) records differently from *oracle*
    (castxml) for the library-owned records of one side of one case."""
    want, got = _owned_records(oracle, case_dir), _owned_records(candidate, case_dir)
    out: list[Divergence] = []
    for name in sorted(set(want) | set(got)):
        ref, rec = want.get(name), got.get(name)
        if ref is None or rec is None:
            out.append(
                Divergence(side, name, "presence", ref is not None, rec is not None)
            )
            continue

        def diff(fact: str, a: object, b: object, _name: str = name) -> None:
            if a != b:
                out.append(Divergence(side, _name, fact, a, b))

        diff("size_bits", ref.size_bits, rec.size_bits)
        diff("alignment_bits", ref.alignment_bits, rec.alignment_bits)
        diff("vtable", _vtable_present(ref), _vtable_present(rec))
        diff("fields", [f.name for f in ref.fields], [f.name for f in rec.fields])
        rec_fields = {f.name: f for f in rec.fields}
        for f in ref.fields:
            g = rec_fields.get(f.name)
            if g is None:
                continue
            diff(f"field_offset:{f.name}", f.offset_bits, g.offset_bits)
            if not type_identity_equal(f.type, g.type):
                out.append(
                    Divergence(side, name, f"field_type:{f.name}", f.type, g.type)
                )
        bases = _match_keys(ref.base_offsets, rec.base_offsets)
        extra = [
            k
            for k in rec.base_offsets
            if not any(type_identity_equal(k, r) for r in ref.base_offsets)
        ]
        if bases != dict(ref.base_offsets) or extra:
            out.append(
                Divergence(
                    side,
                    name,
                    "base_offsets",
                    dict(ref.base_offsets),
                    dict(rec.base_offsets),
                )
            )
    return out


# -- Declared exceptions -------------------------------------------------------


@dataclass(frozen=True)
class BackendException:
    """A divergence that is a known, tracked backend limitation, not a bug
    this harness lets regress. *frontend* is the backend whose fact is not
    the truth; *fact* is a :class:`Divergence` fact name."""

    frontend: str
    case: str
    record: str
    fact: str
    reason: str
    gap: str

    def matches(self, case: str, d: Divergence) -> bool:
        return (case, d.record, d.fact) == (self.case, self.record, self.fact)


_DEFAULT_ARGS = (
    "castxml names a class-template specialization with its defaulted template "
    "arguments elided (X<double>, X<>), clang spells every argument "
    "(X<double, Dist<double>>), so the same specialization has two names"
)
_IMPLICIT_SPECIALIZATION = (
    "castxml records an implicitly instantiated specialization the header only "
    "uses (a member's or base's type); the clang backend records a specialization "
    "only when the header instantiates or specializes it explicitly"
)
_INLINE_NS = (
    "castxml drops an inline namespace from a record's qualified name "
    "(crypto::Context), clang keeps it (crypto::v1::Context)"
)


_GAP_FOR_REASON = {
    _DEFAULT_ARGS: _GAP_NAMING,
    _INLINE_NS: _GAP_NAMING,
    _IMPLICIT_SPECIALIZATION: _GAP_IMPLICIT,
}


def _rows(
    frontend: str, case: str, facts: dict[str, str], reason: str
) -> tuple[BackendException, ...]:
    return tuple(
        BackendException(frontend, case, rec, fact, reason, _GAP_FOR_REASON[reason])
        for rec, fact in facts.items()
    )


BACKEND_EXCEPTIONS: tuple[BackendException, ...] = (
    *_rows(
        "castxml",
        "case77_detail_templated_base_changed",
        {
            "mylib::knn_descriptor<>": "presence",
            "mylib::knn_descriptor<mylib::task::classification>": "presence",
        },
        _DEFAULT_ARGS,
    ),
    *_rows(
        "castxml",
        "case87_default_template_arg_changed",
        {
            "mylib::descriptor<double>": "presence",
            "mylib::descriptor<float>": "presence",
            "mylib::descriptor<double, mylib::minkowski_distance<double>>": "presence",
            "mylib::descriptor<float, mylib::minkowski_distance<float>>": "presence",
            "mylib::descriptor<double, mylib::euclidean_distance<double>>": "presence",
            "mylib::descriptor<float, mylib::euclidean_distance<float>>": "presence",
        },
        _DEFAULT_ARGS,
    ),
    *_rows(
        "clang",
        "case87_default_template_arg_changed",
        {
            "mylib::minkowski_distance<double>": "presence",
            "mylib::minkowski_distance<float>": "presence",
            "mylib::euclidean_distance<double>": "presence",
            "mylib::euclidean_distance<float>": "presence",
        },
        _IMPLICIT_SPECIALIZATION,
    ),
    *_rows(
        "clang",
        "case109_flow_graph_policy_renames",
        {
            "mylib::flow::function_node<mylib::flow::queueing>": "presence",
            "mylib::flow::function_node<mylib::flow::buffering_policy>": "presence",
        },
        _IMPLICIT_SPECIALIZATION,
    ),
    *(
        row
        for case in (
            "case217_export_loss_weak_binding_demoted",
            "case218_export_loss_strong_binding_stays_breaking",
        )
        for row in _rows(
            "clang",
            case,
            {"ns::detail::P<int>": "presence", "ns::detail::P<float>": "presence"},
            _IMPLICIT_SPECIALIZATION,
        )
    ),
    *_rows(
        "castxml",
        "case71_inline_namespace_moved",
        {
            "crypto::Context": "presence",
            "crypto::v1::Context": "presence",
            "crypto::v2::Context": "presence",
        },
        _INLINE_NS,
    ),
)


def unexplained(
    case: str, found: list[Divergence]
) -> tuple[list[Divergence], list[BackendException]]:
    """(divergences no exception explains, this case's exceptions nothing matched)."""
    rows = [e for e in BACKEND_EXCEPTIONS if e.case == case]
    left = [d for d in found if not any(e.matches(case, d) for e in rows)]
    stale = [e for e in rows if not any(e.matches(case, d) for d in found)]
    return left, stale


# -- Session-memoized builds and dumps ------------------------------------------


@contextmanager
def _env(values: dict[str, str | None]) -> Iterator[None]:
    saved = {k: os.environ.get(k) for k in values}
    try:
        for k, v in values.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class CatalogDumps:
    """One build per case and one dump per (case, side, frontend, variant),
    for the whole session (per xdist worker)."""

    def __init__(self, root: Path, cmake_dir: Path | None) -> None:
        self._root = root
        self._cmake_dir = cmake_dir
        self._libs: dict[str, tuple[Path, Path, Path, Path]] = {}
        self._snaps: dict[tuple[str, str, str, str], object] = {}

    def case_dir(self, case: str) -> Path:
        import example_catalog  # on sys.path via test_example_autodiscovery

        return example_catalog.case_dir(case)

    def stripped(self, case: str) -> tuple[Path, Path, Path, Path]:
        """``(v1_lib, v2_lib, v1_header, v2_header)``, the libraries without
        debug info; skips a case with no source pair or no headers."""
        if case not in self._libs:
            case_dir = self.case_dir(case)
            v1_src, v2_src, v1_hdr, v2_hdr = _find_sources(case_dir)
            if not (v1_hdr and v2_hdr and v1_hdr.exists() and v2_hdr.exists()):
                pytest.skip(f"{case}: no v1/v2 headers")
            work = self._root / case
            work.mkdir(parents=True, exist_ok=True)
            libs = _build_libs_for_case(
                case, case_dir, work, self._cmake_dir, v1_src, v2_src
            )
            out = []
            for side, lib in zip(("v1", "v2"), libs, strict=True):
                copy = work / f"{side}-nodebug{lib.suffix}"
                shutil.copyfile(lib, copy)
                subprocess.run(["objcopy", "--strip-debug", str(copy)], check=True)
                out.append(copy)
            self._libs[case] = (out[0], out[1], v1_hdr, v2_hdr)
        return self._libs[case]

    def dump(self, case: str, side: str, frontend: str, variant: str = "real"):
        key = (case, side, frontend, variant)
        if key not in self._snaps:
            from abicheck.service import run_dump

            v1, v2, h1, h2 = self.stripped(case)
            lib, hdr = (v1, h1) if side == "v1" else (v2, h2)
            env: dict[str, str | None] = {
                "ABICHECK_AST_FRONTEND": frontend,
                "ABICHECK_CLANG_LAYOUT_TOOL": _VARIANTS[variant],
                "XDG_CACHE_HOME": str(
                    self._root / case / f"cache-{frontend}-{variant}"
                ),
            }
            with _env(env):
                self._snaps[key] = run_dump(lib, "elf", headers=[hdr], version=side)
        return self._snaps[key]


#: ``variant -> ABICHECK_CLANG_LAYOUT_TOOL``: unset is the built-in pass;
#: a companion tool that always fails disables it (the seeded mutant).
_VARIANTS: dict[str, str | None] = {
    "real": None,
    "no_clang_layout": shutil.which("false") or "/bin/false",
}


#: The harness dumps ELF libraries (``run_dump(..., "elf")``) stripped with
#: ``objcopy --strip-debug``, so it is a Linux harness: a Windows or macOS
#: runner can have all three tools on PATH and still no ELF case to build.
_needs_tools = pytest.mark.skipif(
    CURRENT_PLATFORM != "linux"
    or not all(shutil.which(t) for t in ("castxml", "clang++", "objcopy")),
    reason="needs Linux (ELF cases) with castxml, clang++ and objcopy",
)


@pytest.fixture(scope="session")
def catalog_dumps(
    tmp_path_factory: pytest.TempPathFactory, shared_cmake_build_dir: Path | None
) -> CatalogDumps:
    return CatalogDumps(
        tmp_path_factory.mktemp("catalog_backend_parity"), shared_cmake_build_dir
    )


def _case_divergences(dumps: CatalogDumps, case: str) -> tuple[list[Divergence], int]:
    """Divergences across both sides, and how many owned records castxml sized."""
    case_dir = dumps.case_dir(case)
    found: list[Divergence] = []
    sized = 0
    for side in ("v1", "v2"):
        oracle = dumps.dump(case, side, "castxml")
        owned = _owned_records(oracle, case_dir)
        sized += sum(1 for t in owned.values() if t.size_bits)
        found += record_divergences(
            side, oracle, dumps.dump(case, side, "clang"), case_dir
        )
    return found, sized


def _skip_unbuildable(case: str) -> None:
    if CURRENT_PLATFORM != "linux" or CURRENT_PLATFORM not in PLATFORMS.get(
        case, [CURRENT_PLATFORM]
    ):
        pytest.skip(f"{case}: not an ELF case on {CURRENT_PLATFORM}")
    feature = REQUIRES_FEATURE.get(case)
    if feature is not None:
        from tests.feature_probe import compiler_supports

        if not compiler_supports(feature):
            pytest.skip(f"{case}: compiler lacks {feature!r}")


@pytest.mark.integration
@_needs_tools
@pytest.mark.known_gap_canary
@pytest.mark.parametrize("case", [c for c, _ in _ALL_CASES])
def test_clang_record_layout_matches_castxml_across_the_catalog(
    case: str, catalog_dumps: CatalogDumps
) -> None:
    _skip_unbuildable(case)
    found, sized = _case_divergences(catalog_dumps, case)
    left, stale = unexplained(case, found)
    assert not left, "clang layout differs from castxml:\n" + "\n".join(map(str, left))
    assert not stale, (
        "stale BACKEND_EXCEPTIONS rows (the divergence is gone):\n"
        + "\n".join(f"{e.frontend} {e.record} {e.fact}" for e in stale)
    )
    if sized == 0:
        # Vacuity guard: a case whose headers own no record the oracle sized
        # proves nothing about layout parity; say so instead of passing.
        pytest.skip(f"{case}: castxml sized no library-owned record")


#: Cases the seeded mutant runs on: a C struct, C++ bases at nonzero offsets,
#: and an empty tag type -- one per shape the built-in pass now sizes.
_MUTANT_CASES = (
    "case07_struct_layout",
    "case37_base_class",
    "case140_empty_base_optimization_lost",
)


@pytest.mark.integration
@_needs_tools
@pytest.mark.parametrize("case", _MUTANT_CASES)
def test_seeded_mutant_disabled_clang_layout_pass_is_reported(
    case: str, catalog_dumps: CatalogDumps
) -> None:
    _skip_unbuildable(case)
    case_dir = catalog_dumps.case_dir(case)
    found: list[Divergence] = []
    for side in ("v1", "v2"):
        oracle = catalog_dumps.dump(case, side, "castxml")
        assert any(t.size_bits for t in _owned_records(oracle, case_dir).values()), (
            "vacuity guard: castxml sizes the case's records"
        )
        mutant = catalog_dumps.dump(case, side, "clang", variant="no_clang_layout")
        found += record_divergences(side, oracle, mutant, case_dir)
    assert any(d.fact == "size_bits" and d.clang is None for d in found), found


# -- Fast checks of the oracle itself -------------------------------------------


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("long unsigned int", "unsigned long"),
        ("short int", "short"),
        ("long long int", "long long"),
        ("unsigned", "unsigned int"),
        ("std::__cxx11::basic_string<char>", "std::basic_string<char>"),
        ("detail::Impl *", "ns::detail::Impl*"),
        ("const char *", "char const *"),
        ("struct S", "S"),
        ("union X::(anonymous at /a/b.h:3:5)", "X::(unnamed union at /c.h:9:1)"),
    ],
)
def test_type_identity_equates_spellings_of_one_type(a: str, b: str) -> None:
    assert type_identity_equal(a, b) and type_identity_equal(b, a)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("int", "long"),
        ("unsigned long", "long"),
        ("char", "signed char"),
        ("short", "unsigned short"),
        ("long", "long long"),
        ("ns1::X", "ns2::X"),
        ("int *", "int"),
        ("X<int>", "X<long>"),
        ("const char *", "char *"),
        ("int", None),
    ],
)
def test_type_identity_keeps_different_types_apart(a: str, b: str | None) -> None:
    assert not type_identity_equal(a, b) and not type_identity_equal(b, a)


def test_record_divergences_report_each_layout_fact() -> None:
    """Every fact the oracle compares is reported when it differs, and nothing
    is reported for a record that differs only in type spelling."""
    from abicheck.model import AbiSnapshot, RecordType, TypeField

    case_dir = Path("/catalog/caseX")
    hdr = str(case_dir / "v1.h")

    def snap(**over) -> AbiSnapshot:
        rec = dict(
            name="S",
            kind="struct",
            size_bits=64,
            alignment_bits=32,
            fields=[TypeField("a", "short int", 0), TypeField("b", "int", 32)],
            base_offsets={"ns::B": 0},
            source_header=hdr,
        )
        rec.update(over)
        return AbiSnapshot(library="l", version="1", types=[RecordType(**rec)])

    oracle = snap()
    assert (
        record_divergences(
            "v1",
            oracle,
            snap(
                fields=[TypeField("a", "short", 0), TypeField("b", "int", 32)],
                base_offsets={"B": 0},
            ),
            case_dir,
        )
        == []
    )
    mutants = {
        "size_bits": snap(size_bits=None),
        "alignment_bits": snap(alignment_bits=128),
        "fields": snap(fields=[TypeField("a", "short", 0)]),
        "field_offset:b": snap(
            fields=[TypeField("a", "short", 0), TypeField("b", "int", None)]
        ),
        "field_type:b": snap(
            fields=[TypeField("a", "short", 0), TypeField("b", "long", 32)]
        ),
        "base_offsets": snap(base_offsets={"B": 64}),
        "presence": AbiSnapshot(library="l", version="1", types=[]),
    }
    for fact, mutant in mutants.items():
        found = {d.fact for d in record_divergences("v1", oracle, mutant, case_dir)}
        assert fact in found, (fact, found)
    # A record declared outside the case directory is not library-owned.
    foreign = snap(source_header="/usr/include/x.h")
    assert (
        record_divergences(
            "v1", foreign, AbiSnapshot(library="l", version="1", types=[]), case_dir
        )
        == []
    )


def test_backend_exceptions_are_well_formed() -> None:
    """Every row names a catalog case, a backend, a reason, and a known-gaps
    heading that exists; no two rows claim the same divergence."""
    cases = {c for c, _ in _ALL_CASES}
    keys = [(e.case, e.record, e.fact) for e in BACKEND_EXCEPTIONS]
    assert len(keys) == len(set(keys))
    doc_root = Path(__file__).resolve().parents[1]
    for e in BACKEND_EXCEPTIONS:
        assert e.case in cases, e
        assert e.frontend in ("castxml", "clang"), e
        assert e.reason.strip(), e
        path, _, anchor = e.gap.partition("#")
        text = (doc_root / path).read_text(encoding="utf-8")
        slugs = {
            re.sub(r"[^a-z0-9 -]", "", line.lstrip("#").strip().lower()).replace(
                " ", "-"
            )
            for line in text.splitlines()
            if line.startswith("#")
        }
        assert anchor in slugs, e


def test_unexplained_separates_new_divergences_from_stale_rows() -> None:
    """A divergence a row covers is explained; one no row covers is reported;
    a row that matched no divergence is stale."""
    row = BACKEND_EXCEPTIONS[0]
    covered = Divergence("v1", row.record, row.fact, True, False)
    new = Divergence("v2", "Other", "size_bits", 64, None)
    left, stale = unexplained(row.case, [covered, new])
    assert left == [new]
    assert row not in stale
    left, stale = unexplained(row.case, [])
    assert left == [] and row in stale
    assert unexplained("case_with_no_rows", [new]) == ([new], [])
