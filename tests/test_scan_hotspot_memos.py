"""Invariants for the per-table/per-file memos added for large-library compares.

Every memo here must be *transparent*: a hit returns exactly what a fresh
computation would, hands the caller an object it may mutate without
affecting later callers, and misses as soon as its input changes. The
oracle in each test is a fresh, memo-free computation.
"""

from __future__ import annotations

import random
import shutil
import subprocess
import sys

import pytest

from abicheck import elf_metadata
from abicheck.elf_symbol_filter import (
    ALL_SYMBOL_TYPES,
    FUNCTION_SYMBOL_TYPES,
    VARIABLE_SYMBOL_TYPES,
    _exported_symbol_names,
    exported_symbol_names,
)
from abicheck.extract import cpp20_header_prep
from abicheck.extract.digest_memo import DigestMemo, content_digest
from abicheck.model.elf_facts import ElfMetadata, ElfSymbol, SymbolType
from abicheck.model.export_index import (
    RawExportEntry,
    build_raw_export_index_from_elf,
)


def _random_elf(rng: random.Random) -> ElfMetadata:
    types = list(SymbolType)
    return ElfMetadata(
        symbols=[
            ElfSymbol(
                name=rng.choice(
                    ["", "f", "g", "_ZNSt3foo", "__svml_x", "h__i", f"s{i}"]
                ),
                sym_type=rng.choice(types),
                is_default=rng.random() < 0.8,
                visibility=rng.choice(["default", "hidden"]),
            )
            for i in range(rng.randint(0, 40))
        ]
    )


def _fresh_index(meta: ElfMetadata) -> tuple[RawExportEntry, ...]:
    return tuple(
        RawExportEntry(
            name=s.name,
            is_default=s.is_default,
            sym_type=s.sym_type.name,
            visibility=s.visibility,
        )
        for s in meta.symbols
    )


@pytest.mark.parametrize("seed", range(30))
def test_export_index_memo_matches_fresh_and_tracks_list_changes(seed: int) -> None:
    rng = random.Random(seed)
    meta = _random_elf(rng)
    first = build_raw_export_index_from_elf(meta)
    assert first.entries == _fresh_index(meta)
    assert build_raw_export_index_from_elf(meta) is first  # memo hit
    meta.symbols.append(ElfSymbol(name="added"))  # length change -> rebuild
    assert build_raw_export_index_from_elf(meta).entries == _fresh_index(meta)
    meta.symbols = list(reversed(meta.symbols))  # new list object -> rebuild
    assert build_raw_export_index_from_elf(meta).entries == _fresh_index(meta)


@pytest.mark.parametrize("seed", range(30))
def test_exported_symbol_names_memo_is_transparent_and_copy_safe(seed: int) -> None:
    rng = random.Random(seed)
    meta = _random_elf(rng)
    for types in (FUNCTION_SYMBOL_TYPES, VARIABLE_SYMBOL_TYPES, ALL_SYMBOL_TYPES):
        for relevant in (False, True):
            for transitive in (False, True):
                oracle = _exported_symbol_names(
                    meta.symbols, types, relevant, transitive
                )
                kw = {
                    "abi_relevant_only": relevant,
                    "filter_transitive_runtime_symbols": transitive,
                }
                got = exported_symbol_names(meta, types, **kw)
                assert got == oracle
                got.add("caller-mutation")  # must not leak into the memo
                assert exported_symbol_names(meta, types, **kw) == oracle
    meta.symbols = [*meta.symbols, ElfSymbol(name="late_func")]
    assert "late_func" in exported_symbol_names(meta, FUNCTION_SYMBOL_TYPES)


def test_digest_memo_is_bounded_lru_and_keyed_by_content() -> None:
    memo: DigestMemo[int] = DigestMemo(max_entries=3)
    calls: list[bytes] = []

    def compute(b: bytes) -> int:
        calls.append(b)
        return len(b)

    for b in (b"a", b"bb", b"a", b"ccc", b"dddd"):
        assert memo.get_or_compute(content_digest(b), lambda b=b: compute(b)) == len(b)
    assert calls == [b"a", b"bb", b"ccc", b"dddd"]  # "a" hit once
    assert len(memo) == 3
    memo.get_or_compute(content_digest(b"bb"), lambda: compute(b"bb"))  # evicted as LRU
    assert calls[-1] == b"bb"
    assert content_digest(b"x") != content_digest(b"y")


@pytest.mark.parametrize("flag", [False, True])
def test_cpp20_preprocessing_memo_matches_fresh_and_sees_edits(tmp_path, flag) -> None:
    header = tmp_path / "h.hpp"
    bodies = [
        b"template <typename T> concept C = true;\n",
        b'// concept\nconst char* s = "requires";\n#if 0\nconsteval int f();\n#endif\n',
        b"struct concept {};\n#if __cplusplus < 202002L\nstruct requires {};\n#endif\n",
    ]
    for body in bodies:
        header.write_bytes(body)  # same path, new content: never a stale hit
        got = cpp20_header_prep._preprocessed_header_content(
            header, for_language_mode_decision=flag
        )
        assert got == cpp20_header_prep._prepare_content(body, flag)


@pytest.mark.skipif(shutil.which("gcc") is None, reason="needs gcc")
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="ELF only")
def test_parse_elf_metadata_memo_returns_independent_equal_copies(tmp_path) -> None:
    src = tmp_path / "a.c"
    lib = tmp_path / "liba.so"

    def build(body: str) -> None:
        src.write_text(body)
        subprocess.run(
            ["gcc", "-shared", "-fPIC", "-o", str(lib), str(src)], check=True
        )

    build("int f(void){return 0;}\n")
    elf_metadata._PARSE_MEMO.clear()
    fresh = elf_metadata._parse(open(lib, "rb"), lib)
    a = elf_metadata.parse_elf_metadata(lib)
    b = elf_metadata.parse_elf_metadata(lib)
    assert a == b == fresh and a is not b and a.symbols[0] is not b.symbols[0]
    a.symbols.clear()  # a caller's mutation never reaches the next one
    assert elf_metadata.parse_elf_metadata(lib) == fresh
    build("int f(void){return 0;}\nint g(void){return 1;}\n")  # rebuilt in place
    names = {s.name for s in elf_metadata.parse_elf_metadata(lib).symbols}
    assert "g" in names


def test_compare_scopes_the_elf_memos_to_one_call() -> None:
    from abicheck.checker import compare
    from abicheck.elf_symbol_filter import _EXPORTED_NAMES_MEMO
    from abicheck.model import AbiSnapshot
    from abicheck.model.export_index import _ELF_INDEX_MEMO

    def snap(*names: str) -> AbiSnapshot:
        return AbiSnapshot(
            library="libx.so",
            version="1",
            elf=ElfMetadata(symbols=[ElfSymbol(name=n) for n in names]),
        )

    old, new = snap("f", "g"), snap("f")
    for s in (old, new):  # memos left over from before the compare
        exported_symbol_names(s.elf, FUNCTION_SYMBOL_TYPES)
        build_raw_export_index_from_elf(s.elf)
    old.elf.symbols[1].name = "renamed_in_place"  # same list, same length
    compare(old, new)
    for s in (old, new):
        assert _EXPORTED_NAMES_MEMO not in s.elf.__dict__
        assert _ELF_INDEX_MEMO not in s.elf.__dict__
    # The pre-compare memo can no longer serve the pre-edit spelling.
    assert "renamed_in_place" in exported_symbol_names(old.elf, FUNCTION_SYMBOL_TYPES)
    assert "renamed_in_place" in {
        e.name for e in build_raw_export_index_from_elf(old.elf).entries
    }


@pytest.mark.parametrize("seed", range(20))
def test_digest_memo_byte_budget_matches_an_lru_oracle(seed: int) -> None:
    rng = random.Random(seed)
    budget, max_entries = rng.randint(1, 60), rng.randint(1, 8)
    memo: DigestMemo[bytes] = DigestMemo(max_entries, max_bytes=budget, weigh=len)
    oracle: list[tuple[int, bytes]] = []  # LRU order, oldest first
    for _ in range(80):
        key = rng.randint(0, 10)
        value = b"x" * rng.randint(0, 30)
        known = [v for k, v in oracle if k == key]
        got = memo.get_or_compute(key, lambda value=value: value)
        if known:
            assert got == known[0]
            oracle = [e for e in oracle if e[0] != key] + [(key, known[0])]
            continue
        assert got == value
        if len(value) <= budget:
            oracle.append((key, value))
            while len(oracle) > max_entries or sum(len(v) for _, v in oracle) > budget:
                oracle.pop(0)
        assert memo.retained_bytes == sum(len(v) for _, v in oracle) <= budget
        assert len(memo) == len(oracle)


_CPP20_SNIPPETS = [
    "template <typename T> concept C = true;\n",
    "struct concept {};\n",
    "consteval int f() { return 1; }\n",
    "constinit int g = 0;\n",
    "template <typename T> requires C<T> void h(T);\n",
    "#if 0\nconsteval int dead();\n#endif\n",
    '// concept X = 1;\nconst char* s = "requires";\n',
    "#if __cplusplus < 202002L\nstruct requires {};\n#endif\n",
    "int plain(void);\n",
]


@pytest.mark.parametrize("seed", range(15))
def test_cpp20_scan_memos_match_a_cold_scan_across_overlapping_sets(
    tmp_path, seed
) -> None:
    from abicheck import dumper_ast_config_cpp20 as cpp20

    def clear() -> None:
        cpp20._SCAN_MEMO.clear()
        cpp20._SHADOW_MEMO.clear()
        cpp20._find_cpp20_requirements.cache_clear()

    rng = random.Random(seed)
    files = []
    for i in range(6):
        f = tmp_path / f"h{i}.hpp"
        f.write_text("".join(rng.sample(_CPP20_SNIPPETS, rng.randint(1, 4))))
        files.append(f)
    clear()
    for _ in range(12):  # warm memos accumulate across overlapping header sets
        subset = rng.sample(files, rng.randint(1, len(files)))
        flag = rng.random() < 0.5
        warm = cpp20._find_cpp20_requirements(subset, for_language_mode_decision=flag)
        clear()
        cold = cpp20._find_cpp20_requirements(subset, for_language_mode_decision=flag)
        assert warm == cold
        # Re-warm with a different set sharing files, so the next warm call
        # really is served partly from per-file entries another set created.
        cpp20._find_cpp20_requirements(
            rng.sample(files, 3), for_language_mode_decision=flag
        )
